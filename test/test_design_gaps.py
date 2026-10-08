"""Epic 24 — 남은 설계 요소 (D13-①): H02 라우팅 규칙 버전·게시·되돌리기 · H13 키별 허용 모델 · F04 hosted OpenAPI OBO ·
B05 구성요소 헬스·재시작·마지막 오류 · G01 framework · G07 파일 Part · 푸시 JWT 서명 검증 · F06 시험 전용 가상 사용자.
"""
import base64
import hashlib
import json
import time
import uuid

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.builtin import internal_system
from app.core.http import register_mount
from app.db import SessionLocal
from app.models import A2AAgent, A2ATask, AuditEvent, LogEntry, TokenExchangePolicy
from app.services import mcp_gateway as gw
from test.test_a2a import AG

PUB = {"X-AgentNexus-Data-Class": "public"}


def _chat(client, headers, model):
    return client.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "e24"}]},
                       headers={**headers, **PUB})


# ── C24-01 라우팅 규칙 게시·버전 이력·게시 안 된 변경·되돌리기 (H02 ⑤) ──
def test_routing_rule_publish_versions_and_rollback(client, H):
    rule = {"name": "e24 rule", "match_model": "e24-alias", "strategy": "priority", "targets": [{"model": "fast-commercial"}],
            "fallbacks": ["domain-local"], "retries": 1, "timeout_ms": 30000, "priority": 900, "enabled": True}
    r = client.post("/api/v1/gateway/routing/rules", json=rule, headers=H["operator"]).json()
    rid = r["id"]
    assert r["version"] == 0
    assert client.post(f"/api/v1/gateway/routing/rules/{rid}/publish", json={"reason": ""}, headers=H["operator"]).status_code == 422
    v1 = client.post(f"/api/v1/gateway/routing/rules/{rid}/publish", json={"reason": "초기 게시"}, headers=H["operator"]).json()
    assert v1["version"] == 1 and v1["unpublished_changes"] is False
    assert client.post(f"/api/v1/gateway/routing/rules/{rid}/publish", json={"reason": "다시"},
                       headers=H["operator"]).json()["code"] == "no_changes"
    client.put(f"/api/v1/gateway/routing/rules/{rid}", json={**rule, "targets": [{"model": "smart-commercial"}]}, headers=H["operator"])
    listed = next(x for x in client.get("/api/v1/gateway/routing/rules", headers=H["operator"]).json()["items"] if x["id"] == rid)
    assert listed["unpublished_changes"] is True
    vs = client.get(f"/api/v1/gateway/routing/rules/{rid}/versions", headers=H["operator"]).json()
    assert vs["published_version"] == 1 and vs["pending"][0]["field"] == "targets"
    client.post(f"/api/v1/gateway/routing/rules/{rid}/publish", json={"reason": "고성능 모델로"}, headers=H["operator"])
    rb = client.post(f"/api/v1/gateway/routing/rules/{rid}/rollback", json={"version": 1, "reason": "품질 저하"},
                     headers=H["operator"]).json()
    assert rb["version"] == 3 and rb["restored_from"] == 1 and rb["targets"] == [{"model": "fast-commercial"}]
    hist = client.get(f"/api/v1/gateway/routing/rules/{rid}/versions", headers=H["operator"]).json()["items"]
    assert [h["version"] for h in hist] == [3, 2, 1] and hist[0]["reason"].startswith("v1 로 되돌림")
    assert client.post(f"/api/v1/gateway/routing/rules/{rid}/publish", json={"reason": "x"}, headers=H["dev"]).status_code == 403
    client.delete(f"/api/v1/gateway/routing/rules/{rid}", headers=H["operator"])


# ── C24-02 클라이언트 키 허용 모델 별칭 (H13 ④) ──
def test_client_key_allowed_models(client, H):
    assert client.post("/api/v1/me/tokens", json={"name": "e24-bad", "kind": "client_key", "allowed_models": ["nope-model"]},
                       headers=H["operator"]).json()["code"] == "unknown_model"
    assert client.post("/api/v1/me/tokens", json={"name": "e24-pat", "allowed_models": ["domain-local"]},
                       headers=H["operator"]).status_code == 400
    key = client.post("/api/v1/me/tokens", json={"name": "e24-key", "kind": "client_key", "allowed_models": ["domain-local"]},
                      headers=H["operator"]).json()
    kh = {"Authorization": f"Bearer {key['token']}"}
    assert key["allowed_models"] == ["domain-local"]
    assert _chat(client, kh, "domain-local").status_code == 200
    denied = _chat(client, kh, "fast-commercial")
    assert denied.status_code == 403 and "model_not_allowed" in denied.text
    row = next(k for k in client.get("/api/v1/gateway/client-keys", headers=H["operator"]).json()["items"] if k["id"] == key["id"])
    assert row["allowed_models"] == ["domain-local"]
    assert client.patch(f"/api/v1/gateway/client-keys/{key['id']}", json={"allowed_models": []},
                        headers=H["operator"]).json()["allowed_models"] is None
    assert _chat(client, kh, "fast-commercial").status_code == 200  # 제한 해제
    assert client.patch(f"/api/v1/gateway/client-keys/{key['id']}", json={"allowed_models": ["x"]},
                        headers=H["dev"]).status_code == 403


# ── C24-03 hosted OpenAPI OBO — 호출 사용자 토큰을 api:{ns} 로 교환해 주입, 서비스 시크릿 미사용 (F04 ③) ──
def test_hosted_openapi_obo_propagates_user_identity(client, H):
    seen: list[dict] = []

    async def capture(scope, receive, send):
        if scope["type"] == "http":
            seen.append({k.decode(): v.decode() for k, v in scope["headers"]})
        await internal_system.app(scope, receive, send)
    register_mount("http://internal-obo", async_transport=httpx.ASGITransport(app=capture))
    gw.clear_caches()
    res = client.post("/api/v1/mcp-servers/openapi", json={
        "namespace": "orders-obo", "title": "Orders OBO", "openapi": internal_system.app.openapi(), "base_url": "http://internal-obo",
        "identity": "obo", "scopes": ["orders:read"], "secret": "svc-secret-should-not-be-used"}, headers=H["operator"])
    assert res.status_code == 201, res.text
    with SessionLocal() as db:
        pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == "api:orders-obo")).one()
        assert pol.allowed_scopes == ["orders:read"]
    seen.clear()
    out = client.post("/api/v1/mcp-servers/orders-obo/inspect", json={
        "method": "tools/call", "params": {"name": "orders_get", "arguments": {"order_id": "O-20260928-002"}}},
        headers=H["dev"]).json()
    assert out["error"] is None, out
    auth = next(h["authorization"] for h in seen if "authorization" in h)
    claims = jwt.decode(auth.split(" ", 1)[1], options={"verify_signature": False})
    assert claims["aud"] == "api:orders-obo" and claims["sub"] == "usr_dev" and claims["scope"] == "orders:read"
    assert all("svc-secret" not in json.dumps(h) for h in seen)
    with SessionLocal() as db:
        db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == "api:orders-obo")).one().enabled = False
        db.commit()
    bad = client.post("/api/v1/mcp-servers/orders-obo/inspect", json={
        "method": "tools/call", "params": {"name": "orders_get", "arguments": {"order_id": "O-20260928-002"}}},
        headers=H["dev"]).json()
    assert bad["error"]["data"]["code"] == "token_exchange_failed"


# ── C24-04 구성요소 헬스·오류·재시작·마지막 오류 (B05 ②) ──
def test_component_health_restarts_and_last_error(client, H):
    with SessionLocal() as db:
        db.add(LogEntry(level="ERROR", logger="agentnexus.mcp", message="e24 업스트림 연결 실패", component="mcp"))
        db.commit()
    items = {r["component"]: r for r in client.get("/api/v1/system/components", headers=H["admin"]).json()["items"]}
    gwc = items["gateway"]
    assert gwc["health"] == "degraded" and gwc["errors_1h"] >= 1 and gwc["last_error"]["message"].startswith("e24")
    assert {"health", "restarts_24h", "last_error", "detail"} <= set(items["api"])
    with SessionLocal() as db:
        assert db.scalars(select(LogEntry).where(LogEntry.component == "system", LogEntry.message.like("프로세스 시작%"))).first()


# ── C24-05 framework(운영자 지정·카드 힌트) · 시험 콘솔 파일 Part (G01·G07 ②) ──
def test_a2a_framework_and_file_parts(client, H):
    from app.api.a2a import framework_hint
    assert framework_hint({"provider": {"organization": "x", "url": "https://google.github.io/adk-docs"}}) == "ADK"
    assert framework_hint({"name": "plain"}) is None
    client.patch(f"/api/v1/a2a/agents/{AG}", json={"framework": "LangGraph"}, headers=H["operator"])
    a = client.get(f"/api/v1/a2a/agents/{AG}", headers=H["dev"]).json()
    assert a["framework"] == "LangGraph" and a["framework_source"] == "operator"
    client.patch(f"/api/v1/a2a/agents/{AG}", json={"framework": ""}, headers=H["operator"])
    b64 = base64.b64encode("주문 로그\nO-1 실패".encode()).decode()
    ok = client.post(f"/api/v1/a2a/agents/{AG}/try", json={"text": "orders-api 로그 분석", "files": [
        {"name": "log.txt", "mimeType": "text/plain", "bytes": b64}]}, headers=H["dev"]).json()
    assert "error" not in ok, ok
    assert client.post(f"/api/v1/a2a/agents/{AG}/try", json={"text": "x", "files": [{"name": "a", "bytes": "%%%"}]},
                       headers=H["dev"]).json()["code"] == "invalid_file"
    assert client.post(f"/api/v1/a2a/agents/{AG}/try", json={"text": "x", "files": [{"name": "a", "uri": "file:///etc"}]},
                       headers=H["dev"]).json()["code"] == "invalid_file"


# ── C24-06 푸시 JWT 서명 검증 — 서명·발급 시각·본문 해시, 실패는 401·감사 (G07 ⑧) ──
def test_push_notification_jwt_verification(client, H):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="e24", alg="RS256", use="sig")
    register_mount("http://jwks-e24", sync_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"keys": [jwk]})))
    with SessionLocal() as db:
        ag = db.get(A2AAgent, AG)
        old = dict(ag.auth or {})
        db.add(A2ATask(id=f"{AG}:e24-push", remote_task_id="e24-push", agent_id=AG, state="working", push_token="e24-tok",
                       history=[], artifacts=[]))
        db.commit()
    client.patch(f"/api/v1/a2a/agents/{AG}", json={"auth": {"push_verify": {"jwks_url": "http://jwks-e24/jwks", "issuer": "https://agent.e24.example"}}},
                 headers=H["operator"])
    body = json.dumps({"kind": "task", "id": "e24-push", "status": {"state": "completed"}}).encode()

    def sign(k, **claims):
        return jwt.encode({"iat": int(time.time()), "iss": "https://agent.e24.example", "jti": uuid.uuid4().hex,
                           "request_body_sha256": hashlib.sha256(body).hexdigest(), **claims}, k,
                          algorithm="RS256", headers={"kid": "e24"})
    post = lambda h: client.post("/a2a/push/e24-tok", content=body, headers={"Content-Type": "application/json", **h})  # noqa: E731
    try:
        assert post({}).status_code == 401
        assert post({"Authorization": f"Bearer {sign(other)}"}).status_code == 401
        assert post({"Authorization": f"Bearer {sign(key, iat=int(time.time()) - 900)}"}).status_code == 401
        assert post({"Authorization": f"Bearer {sign(key, request_body_sha256='0' * 64)}"}).status_code == 401
        assert post({"Authorization": f"Bearer {sign(key)}"}).status_code == 200
        with SessionLocal() as db:
            assert db.get(A2ATask, f"{AG}:e24-push").state == "completed"
            assert db.scalars(select(AuditEvent).where(AuditEvent.action == "a2a.push.reject")).first() is not None
    finally:
        with SessionLocal() as db:
            db.get(A2AAgent, AG).auth = old
            db.commit()


# ── C24-07 Inspector 시험 전용 가상 사용자 — 역할·팀만, 내 권한 이하, 쓰기·위험 도구는 드라이런 (F06 ⑤) ──
def test_inspector_virtual_test_principal(client, H):
    client.post("/api/v1/mcp-servers/jira/refresh", headers=H["operator"])
    read = client.post("/api/v1/mcp-servers/jira/inspect", json={
        "method": "tools/call", "params": {"name": "search_issues", "arguments": {"query": "결제"}},
        "test_principal": {"roles": ["user"], "team": "team-e24"}}, headers=H["dev"]).json()
    assert read["error"] is None and read["dry_run"] is False and read["test_principal"]["sub"] == "test:usr_dev"
    write = client.post("/api/v1/mcp-servers/jira/inspect", json={
        "method": "tools/call", "params": {"name": "transition_issue", "arguments": {"key": "ORI-1440", "to": "Done"}},
        "test_principal": {"roles": ["user"]}}, headers=H["dev"]).json()
    assert write["dry_run"] is True and write["result"] is None and write["decision"]["code"] in ("approval_required", "allowed", "policy_denied")
    assert client.post("/api/v1/mcp-servers/jira/inspect", json={"method": "tools/list", "test_principal": {"roles": ["admin"]}},
                       headers=H["dev"]).json()["code"] == "test_roles_exceed"
    assert client.post("/api/v1/mcp-servers/jira/inspect", json={"method": "tools/list", "test_principal": {"roles": ["ghost"]}},
                       headers=H["dev"]).json()["code"] == "unknown_role"
    with SessionLocal() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "mcp.inspect.test_principal").order_by(AuditEvent.seq.desc())).first()
        assert ev.actor == "usr_dev" and ev.data["dry_run"] is True
