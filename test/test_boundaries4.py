"""Epic 64 — 경계 하드닝 4 (D22-④): 직접 발급 Access Token 스코프 ⊆ 교환 정책(사용자 결정) · 푸시 JWKS 모르는 kid 갱신·
운영 https·발급자 없는 설정 표시 · Task artifacts 상한 · OpenAPI 시크릿 선점 거부·실패 정리 · Inspector 드라이런 서버 판정 등급 ·
익명 발견 API(SQL 이름 검색·IP 한도) · 소유자 없는 항목 · gRPC readiness 결과 공유."""
import hashlib
import json
import time
import uuid

import grpc
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.api import catalog
from app.builtin import internal_system
from app.core.config import settings
from app.core.http import register_mount
from app.db import SessionLocal
from app.models import A2AAgent, A2ATask, McpServer, Secret, TokenExchangePolicy
from app.services import a2a_gateway, a2a_grpc, governance, policy, registry, secrets
from test.test_a2a import AG
from test.test_catalog import server_json


# ── C64-01 Access Token — mcp:·a2a: 대상은 교환 정책의 허용 스코프 안만, 정책 없으면 거부 · 게이트웨이 audience 는 그대로 ──
def test_access_token_scopes_follow_exchange_policy(client, H):
    sid = f"e64-{uuid.uuid4().hex[:6]}"  # 정책 없는 등록 서버 — 다른 시험이 기존 서버 정책을 만들어 두는 순서와 무관하게
    with SessionLocal() as db:
        db.add(McpServer(id=sid, name=sid, transport="streamable-http", endpoint=f"https://{sid}.e64.example/mcp"))
        db.commit()
    base = {"name": "e64", "subject": "svc:e64", "ttl_seconds": 300}
    issue = lambda **kw: client.post("/api/v1/access-tokens", json={**base, **kw}, headers=H["operator"])  # noqa: E731
    no_pol = issue(audience=f"mcp:{sid}", scopes=[])
    assert no_pol.status_code == 400 and no_pol.json()["code"] == "no_exchange_policy"
    with SessionLocal() as db:
        db.add(TokenExchangePolicy(id=f"txp_e64_{uuid.uuid4().hex[:6]}", audience=f"mcp:{sid}", audience_type="mcp_server",
                                   allowed_scopes=["e64:read"], default_scopes=["e64:read"], ttl_seconds=300))
        db.commit()
    wide = issue(audience=f"mcp:{sid}", scopes=["e64:read", "e64:admin"])
    assert wide.status_code == 400 and wide.json()["code"] == "scope_not_allowed" and wide.json()["allowed"] == ["e64:read"]
    ok = [issue(audience=f"mcp:{sid}", scopes=["e64:read"]), issue(scopes=["gateway:invoke", "anything:else"])]
    assert [r.status_code for r in ok] == [201, 201], [r.text for r in ok]
    for r in ok:
        client.post(f"/api/v1/access-tokens/{r.json()['id']}/revoke", headers=H["operator"])
    with SessionLocal() as db:
        db.query(TokenExchangePolicy).filter(TokenExchangePolicy.audience == f"mcp:{sid}").delete()
        db.delete(db.get(McpServer, sid))
        db.commit()


# ── C64-02 푸시 JWKS — 원격이 키를 회전해 모르는 kid 면 한 번 다시 받음(30초에 1회) · 운영은 https 만 · 발급자 없는 설정 표시 ──
def test_push_jwks_refresh_https_and_issuer_flag(client, H, monkeypatch):
    iss = "https://agent.e64.example"
    k1, k2 = (rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))

    def jwk(k, kid):
        d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(k.public_key()))
        return {**d, "kid": kid, "alg": "RS256", "use": "sig"}
    served = {"keys": [jwk(k1, "k1")]}
    fetches = []
    register_mount("http://jwks-e64", sync_transport=httpx.MockTransport(lambda r: fetches.append(1) or httpx.Response(200, json=served)))
    a2a_gateway._JWKS_CACHE.pop("http://jwks-e64/jwks", None)
    from app.services import ratelimit
    ratelimit.backend().holder_release(a2a_gateway._forced_key("http://jwks-e64/jwks"), "refresh")
    cfg = {"jwks_url": "http://jwks-e64/jwks", "issuer": iss}
    body = b'{"kind":"task"}'

    def tok(k, kid):
        claims = {"iss": iss, "iat": int(time.time()), "jti": uuid.uuid4().hex, "request_body_sha256": hashlib.sha256(body).hexdigest()}
        return "Bearer " + jwt.encode(claims, k, algorithm="RS256", headers={"kid": kid})
    assert a2a_gateway.verify_push_jwt(cfg, tok(k1, "k1"), body)["iss"] == iss and len(fetches) == 1
    served["keys"].append(jwk(k2, "k2"))  # 원격 에이전트가 새 키 추가 — 캐시(5분)에는 아직 없음
    assert a2a_gateway.verify_push_jwt(cfg, tok(k2, "k2"), body)["iss"] == iss and len(fetches) == 2
    with pytest.raises(a2a_gateway.PushRejected, match="kid"):
        a2a_gateway.verify_push_jwt(cfg, tok(k2, "k3-forged"), body)  # 30초 안 — 다시 받지 않음
    assert len(fetches) == 2
    monkeypatch.setattr(settings, "app_env", "prod")
    res = client.patch(f"/api/v1/a2a/agents/{AG}", json={"auth": {"push_verify": {"jwks_url": "http://jwks-e64/jwks", "issuer": iss}}},
                       headers=H["operator"])
    assert res.status_code == 400 and res.json()["code"] == "invalid_jwks_url"
    monkeypatch.setattr(settings, "app_env", "test")
    with SessionLocal() as db:
        a = db.get(A2AAgent, AG)
        old = dict(a.auth or {})
        a.auth = {**old, "push_verify": {"jwks_url": "https://agent.e64.example/jwks"}}  # D21 이전 설정
        db.commit()
    try:
        got = client.get(f"/api/v1/a2a/agents/{AG}", headers=H["operator"]).json()
        assert got["push_verify"] is True and got["push_verify_issuer"] is False
    finally:
        with SessionLocal() as db:
            db.get(A2AAgent, AG).auth = old
            db.commit()


# ── C64-03 Task artifacts — 최근 50 개·직렬화 4 MiB 안에서만 ──
def test_artifacts_capped(monkeypatch):
    rid = f"e64-art-{uuid.uuid4().hex[:6]}"
    arts = [{"artifactId": f"a{i}", "parts": [{"kind": "text", "text": str(i)}]} for i in range(80)]
    a2a_gateway.record(AG, {"kind": "task", "id": rid, "contextId": "c", "status": {"state": "working"}, "artifacts": arts})
    monkeypatch.setattr(a2a_gateway, "ARTIFACTS_MAX_BYTES", 3000)
    big = {"artifactId": "big", "parts": [{"kind": "text", "text": "x" * 2500}]}
    a2a_gateway.record(AG, {"kind": "artifact-update", "taskId": rid, "artifact": big})
    with SessionLocal() as db:
        row = db.get(A2ATask, f"{AG}:{rid}")
        ids = [a["artifactId"] for a in row.artifacts]
        db.delete(row)
        db.commit()
    assert ids[-1] == "big" and len(ids) < 50 and "a0" not in ids


# ── C64-04 OpenAPI 서버 생성 — 같은 이름 시크릿이 이미 있으면 409(덮지 않음) · 게시가 실패하면 쓴 시크릿을 지움 ──
def test_openapi_secret_preexisting_and_cleanup(client, H, monkeypatch):
    ns = f"e64-{uuid.uuid4().hex[:6]}"
    register_mount("http://e64-int", async_transport=httpx.ASGITransport(app=internal_system.app))
    doc = {"namespace": ns, "title": "E64", "openapi": internal_system.app.openapi(), "base_url": "http://e64-int",
           "secret": "e64-new-value"}
    with SessionLocal() as db:
        secrets.put(db, f"mcp/{ns}/backend", "e64-someone-else", kind="token")
        db.commit()
    res = client.post("/api/v1/mcp-servers/openapi", json=doc, headers=H["operator"])
    assert res.status_code == 409 and res.json()["code"] == "secret_exists"
    with SessionLocal() as db:
        assert secrets.get_plain(db, f"mcp/{ns}/backend") == "e64-someone-else"
        db.query(Secret).filter(Secret.name == f"mcp/{ns}/backend").delete()
        db.commit()

    def boom(*a, **k):
        raise RuntimeError("e64 publish failed")
    monkeypatch.setattr(registry, "publish", boom)
    with pytest.raises(RuntimeError, match="e64"):
        client.post("/api/v1/mcp-servers/openapi", json=doc, headers=H["operator"])
    with SessionLocal() as db:
        assert db.scalars(select(Secret).where(Secret.name == f"mcp/{ns}/backend")).first() is None


# ── C64-05 Inspector 드라이런 — 실제 호출과 같은 서버 판정 등급으로 (클라이언트 신고 원문 아님) ──
def test_inspector_dry_run_uses_server_data_class(client, H, monkeypatch):
    client.post("/api/v1/mcp-servers/jira/refresh", headers=H["operator"])  # 도구 규칙(위험도) — 쓰기 도구면 드라이런
    seen = []
    real_check = governance.check_tool

    def spy(db, p, server_id, tool, args, **kw):
        seen.append(kw.get("data_class"))
        return real_check(db, p, server_id, tool, args, **kw)
    monkeypatch.setattr(governance, "check_tool", spy)
    monkeypatch.setattr(policy, "declared_data_class", lambda db, p, declared: "confidential")
    body = {"method": "tools/call", "params": {"name": "transition_issue", "arguments": {"key": "E64-1", "to": "Done"}},
            "data_class": "public", "test_principal": {"roles": ["user"], "team": "team-e64"}}
    res = client.post("/api/v1/mcp-servers/jira/inspect", json=body, headers=H["operator"])
    assert res.status_code == 200 and res.json()["dry_run"] is True, res.text
    assert seen == ["confidential"]


# ── C64-06 익명 발견 API — 이름 부분 일치는 SQL(전체를 읽지 않음) · IP 별 한도, 인증 호출은 한도 밖 ──
def test_v0_anonymous_search_sql_and_limit(client, H, monkeypatch):
    seen = []
    real = registry.search
    monkeypatch.setattr(registry, "search", lambda db, **kw: seen.append(kw) or real(db, **kw))
    names = [s["server"]["name"] for s in client.get("/v0/servers", params={"search": "JIRA"}).json()["servers"]]
    assert names and all("jira" in n.lower() for n in names)
    assert seen[-1]["name_contains"] == "JIRA" and seen[-1].get("q") is None
    assert client.get("/v0/servers", params={"search": "%"}).json()["servers"] == [] or \
        all("%" in s["server"]["name"] for s in client.get("/v0/servers", params={"search": "%"}).json()["servers"])
    monkeypatch.setattr(catalog, "V0_ANON_RPS", 0.001)
    monkeypatch.setattr(catalog, "V0_ANON_BURST", 2)
    from app.services import ratelimit
    ratelimit.backend().reset()
    codes = [client.get("/v0/servers").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert client.get("/v0/servers", headers=H["user"]).status_code == 200  # 인증 호출
    ratelimit.backend().reset()


# ── C64-07 소유자 없는 항목 — 새 버전은 승인 권한자만 ──
def test_ownerless_item_new_version_approver_only(client, H):
    name = f"io.e64/ownerless-{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        item, _, _ = registry.publish(db, "mcp_server", name, "1.0.0", server_json(name=name, _meta={"license": "MIT"}), "system:seed")
        item.owner = None
        db.commit()
    dev = client.post("/v0/publish", json=server_json(name=name, version="1.0.1", _meta={"license": "MIT"}), headers=H["dev"])
    assert dev.status_code == 403 and dev.json()["code"] == "not_item_owner"
    op = client.post("/v0/publish", json=server_json(name=name, version="1.0.2", _meta={"license": "MIT"}), headers=H["operator"])
    assert op.status_code == 201, op.text


# ── C64-08 gRPC readiness — 위임 대상 /health 결과를 2초 공유 (인증 없는 프로브가 DB 로 증폭되지 않게) ──
def test_grpc_readiness_cached(client, monkeypatch):
    calls = []
    register_mount("http://e64-gw", async_transport=httpx.ASGITransport(
        Starlette(routes=[Route("/health", lambda _: calls.append(1) or JSONResponse({"status": "ok"}))])))

    async def run() -> list:
        server = await a2a_grpc.serve("127.0.0.1:0", gateway_url="http://e64-gw")
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}") as ch:
                check = ch.unary_unary(f"/{a2a_grpc.HEALTH_SERVICE}/Check")
                out = [await check(a2a_grpc.health_request("readiness"), timeout=5) for _ in range(5)]
                monkeypatch.setattr(a2a_grpc, "READINESS_CACHE_S", 0.0)  # 공유 시간이 지나면 다시 확인
                out.append(await check(a2a_grpc.health_request("readiness"), timeout=5))
                return out
        finally:
            await server.stop(0)
    out = client.portal.call(run)
    assert out == [a2a_grpc._STATUS[True]] * 6 and len(calls) == 2
