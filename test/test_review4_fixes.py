"""Epic 65 — 4차 품질 검토 확인 결함 수정 (D22-⑤): `.review/round4/VERIFY.md` 의 '수정' 항목.
출력 예약 상한·max_tokens 검증 · 대량 작업 세션 상한 해제 · 2단계 회전 중 재암호화 거부·세대 확인 · IdP 정리 시 PAT 폐기 ·
현재 키 폐기 거부 · 실패·취소 경로 예약 반환 · 클라이언트 키별 진행 중 상한 · 익명 전체 한도·버킷 상한 · JWKS 강제 갱신 공유 ·
등록 경로 push_verify·운영 https · artifacts 큰 항목 · OpenAPI 정리 · 서명된 백업만 복원 · 샘플링 실패 기록 · readiness 대기 없음 ·
사용자 검색 와일드카드 · 빈 스코프 기본값 · RSA 아닌 이전 키."""
import asyncio
import json
import time
import types
import uuid
from datetime import timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import event, select
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.api import admin, catalog
from app.builtin import internal_system
from app.cli import nexus
from app.core import keys
from app.core.auth import Principal
from app.core.config import settings
from app.core.errors import ApiError
from app.core.http import register_mount
from app.db import SessionLocal, engine, utcnow
from app.models import A2ATask, McpServer, PersonalToken, RateLimit, RoutingDecision, Secret, TokenExchangePolicy, User
from app.services import a2a_gateway, a2a_grpc, llm, mcp_consent, ratelimit, registry, retention
from test.test_a2a import AG
from test.test_cli_install import cli_env, run  # noqa: F401 — 픽스처
from test.test_key_mgmt import envkeys  # noqa: F401 — 픽스처

PG = engine.dialect.name == "postgresql"


@pytest.fixture
def limit():
    made = []

    def add(**kw):
        lid = f"rl_e65_{uuid.uuid4().hex[:6]}"
        with SessionLocal() as db:
            db.add(RateLimit(id=lid, name="e65", enabled=True, target_type="model", target="*", **kw))
            db.commit()
        made.append(lid)
        return lid
    ratelimit.backend().reset()
    yield add
    with SessionLocal() as db:
        for lid in made:
            db.delete(db.get(RateLimit, lid))
        db.commit()
    ratelimit.backend().reset()


# ── C65-01 출력 예약 — 남은 여유분까지만(버킷을 한도 넘게 잠그지 않음) · max_tokens 는 1 이상 정수 · 모델 컨텍스트로 자름 ──
def test_output_reservation_bounded_and_validated(client, H, limit):
    lid = limit(scope_type="user", scope="*", tokens_per_min=1000)
    p = Principal(sub="usr_e65", kind="user", roles=["user"])
    with SessionLocal() as db:
        first = ratelimit.check(db, p, "model", "m", None, 100, output_tokens=5000)
    assert first.allowed and first.holds[0][2] == 1000 and ratelimit.backend().window_get(f"tok:{lid}:usr_e65") == 1000
    ratelimit.settle(first.holds, 150)
    assert ratelimit.backend().window_get(f"tok:{lid}:usr_e65") == 150
    for bad in (-5, 0, "abc", True, 1.5):
        res = client.post("/v1/chat/completions", json={"model": "auto", "max_tokens": bad,
                                                        "messages": [{"role": "user", "content": "x"}]}, headers=H["user"])
        assert res.status_code == 400, (bad, res.text)
    with SessionLocal() as db:
        plan = llm.prepare(db, llm.CallContext(p, "internal"), {"model": "auto", "max_tokens": 10 ** 9,
                                                               "messages": [{"role": "user", "content": "x"}]})
        from app.models import Model
        cw = db.get(Model, plan.targets[0].model_id).context_window
        assert cw and plan.holds[0][2] <= cw + 10
        llm.discard(plan)


# ── C65-02 대량 작업 — 보존 정리·감사 머리 잠금은 세션 상한 없이 (PostgreSQL SET LOCAL 0) ──
def test_bulk_jobs_unbounded():
    seen = []
    listen = lambda conn, cur, stmt, *a: seen.append(stmt)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listen)
    try:
        with SessionLocal() as db:
            retention.run(db, "test:e65")
    finally:
        event.remove(engine, "before_cursor_execute", listen)
    if PG:
        assert seen.count("SET LOCAL statement_timeout = 0") >= 2 and "SET LOCAL lock_timeout = 0" in seen  # 정리 + 감사 머리
        assert seen.index("SET LOCAL statement_timeout = 0") < next(i for i, s in enumerate(seen) if s.startswith("DELETE FROM spans"))
    else:
        assert not any("SET LOCAL" in s for s in seen)


# ── C65-03 재암호화 — 다음 키가 남은 1단계 파드는 거부 · 응답에 현재 키 지문 · 재암호화 중 회전이 끼면 이전 키 제거 거부 ──
def test_reencrypt_guards(client, H, envkeys, monkeypatch):  # noqa: F811
    res = client.post("/api/v1/admin/keys/reencrypt", headers=H["admin"]).json()
    assert res["failed"] == 0 and len(res["key_fingerprint"]) == 12 and res["key_fingerprint"] == keys.encryption_fingerprint()
    real = admin.reencrypt

    def racing(db):
        out = real(db)
        keys._bump()  # 다른 관리자의 회전
        return out
    monkeypatch.setattr(admin, "reencrypt", racing)
    raced = client.post("/api/v1/admin/keys/reencrypt", params={"drop_previous": "true"}, headers=H["admin"])
    assert raced.status_code == 409 and raced.json()["code"] == "keys_changed"
    envkeys(encryption_key_next=Fernet.generate_key().decode())
    staged = client.post("/api/v1/admin/keys/reencrypt", headers=H["admin"])
    assert staged.status_code == 409 and staged.json()["code"] == "rotation_in_progress"


# ── C65-04 IdP 연동 중지·삭제 — 그 사용자의 PAT·클라이언트 키도 폐기 (tokens_not_before 를 보지 않으므로) ──
def test_idp_removal_revokes_pats(client, H):
    iss = f"https://idp-e65-{uuid.uuid4().hex[:6]}.example"
    iid = client.post("/api/v1/identity/idps", json={"name": "e65", "issuer": iss, "client_id": "x"}, headers=H["security"]).json()["id"]
    uid = f"usr_e65_{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        db.add(User(id=uid, sub=f"ext:{iid}:{uid}", issuer=iss, username=None, roles=["user"], status="active"))
        db.flush()  # 관계 없는 외래 키 — 사용자 먼저 (PostgreSQL)
        for kind in ("pat", "client_key"):
            db.add(PersonalToken(id=f"pat_{kind}_{uid}"[:40], user_id=uid, kind=kind, name=kind, token_hash=uuid.uuid4().hex, prefix="anx_e65",
                                 scopes=[], expires_at=utcnow() + timedelta(days=30)))
        db.commit()
    assert client.delete(f"/api/v1/identity/idps/{iid}", headers=H["security"]).status_code == 200
    with SessionLocal() as db:
        toks = db.scalars(select(PersonalToken).where(PersonalToken.user_id == uid)).all()
        assert len(toks) == 2 and all(t.revoked_at is not None for t in toks)
        for t in toks:
            db.delete(t)
        db.delete(db.get(User, uid))
        db.commit()


# ── C65-05 현재 서명 키를 폐기 목록에 넣으면 조용히 넘기지 않고 멈춘다 (먼저 회전) · RSA 아닌 이전 키는 읽을 때 거부 ──
def test_revoked_current_key_and_non_rsa(envkeys):  # noqa: F811
    current = keys.kid()
    envkeys(signing_revoked_kids=current)
    with pytest.raises(RuntimeError, match="회전"):
        keys.signing_key()
    ec_pub = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    envkeys(signing_revoked_kids="", signing_key_pem_previous=ec_pub)
    with pytest.raises(ValueError, match="RSA"):
        keys.jwks()


# ── C65-06 실패·취소 경로 — 비스트림 예상 밖 오류·한도 검사 오류에도 예약·진행 중 슬롯 반환 ──
def test_unexpected_failures_release_holds(client, H, limit, monkeypatch):
    lid = limit(scope_type="user", scope="*", tokens_per_min=1_000_000)
    me = client.get("/api/v1/me", headers=H["user"]).json()["sub"]

    async def boom(plan, body):
        raise RuntimeError("e65 upstream exploded")
    monkeypatch.setattr(llm, "execute", boom)
    with pytest.raises(RuntimeError, match="e65"):
        client.post("/v1/chat/completions", json={"model": "auto", "messages": [{"role": "user", "content": "x"}]}, headers=H["user"])
    assert ratelimit.backend().window_get(f"tok:{lid}:{me}") == 0 and ratelimit.backend().holder_count(f"inflight:{me}") == 0

    def broken_check(*a, **k):
        raise RuntimeError("e65 limits db down")
    monkeypatch.setattr(ratelimit, "check", broken_check)
    p = Principal(sub="usr_e65_chk", kind="user", roles=["user"])
    with SessionLocal() as db, pytest.raises(RuntimeError, match="limits"):
        llm.prepare(db, llm.CallContext(p, "internal"), {"model": "auto", "messages": [{"role": "user", "content": "x"}]})
    assert ratelimit.backend().holder_count("inflight:usr_e65_chk") == 0


# ── C65-07 진행 중 상한 — 클라이언트 키는 키마다 (한 사람의 여러 연동이 상한 하나를 나눠 쓰지 않게) ──
def test_inflight_key_per_client_key():
    p = Principal(sub="usr_e65_ck", kind="client_key", roles=["user"], token_id="pat_e65_key")
    with SessionLocal() as db:
        plan = llm.prepare(db, llm.CallContext(p, "internal"), {"model": "auto", "messages": [{"role": "user", "content": "x"}]})
    assert plan.inflight[0] == "inflight:key:pat_e65_key"
    llm.discard(plan)


# ── C65-08 익명 발견 API — 전체 버킷(IP 를 속여도 총량) · 메모리 버킷 수 상한 ──
def test_anonymous_total_bucket_and_memory_cap(client, monkeypatch):
    ratelimit.backend().reset()
    monkeypatch.setattr(catalog, "V0_ANON_BURST", 100)
    monkeypatch.setattr(catalog, "V0_ANON_TOTAL_RPS", 0.001)
    monkeypatch.setattr(catalog, "V0_ANON_TOTAL_BURST", 2)
    assert [client.get("/v0/servers").status_code for _ in range(3)] == [200, 200, 429]
    ratelimit.backend().reset()
    b = ratelimit.MemoryBackend()
    monkeypatch.setattr(ratelimit.MemoryBackend, "MAX_BUCKETS", 10)
    for i in range(25):
        b.take(f"rl:v0anon:10.0.0.{i}", 1.0, 5)
    assert len(b._buckets) <= 10


# ── C65-09 JWKS 강제 갱신 — URL 마다 30초 한 번, 워커 공유(보유자 고정 만료 — 계속 보내도 늘지 않음) ──
def test_forced_jwks_refresh_shared():
    url = f"https://jwks.e65-{uuid.uuid4().hex[:6]}.example/jwks"
    assert a2a_gateway._forced_ok(url) is True
    assert [a2a_gateway._forced_ok(url) for _ in range(3)] == [False] * 3
    ratelimit.backend().holder_release(a2a_gateway._forced_key(url), "refresh")


# ── C65-10 push_verify — 등록에도 같은 검사(발급자·권한) · 운영은 사용 시점에도 https ──
def test_push_verify_checked_on_register_and_use(client, H, monkeypatch):
    body = {"url": "http://e65-agent.test", "id": f"e65-{uuid.uuid4().hex[:6]}", "auth": {"scheme": "none",
            "push_verify": {"jwks_url": "https://jwks.e65.example/jwks"}}}
    res = client.post("/api/v1/a2a/agents", json=body, headers=H["operator"])
    assert res.status_code == 400 and res.json()["code"] == "push_verify_issuer_required"
    dev = client.post("/api/v1/a2a/agents", json={**body, "auth": {**body["auth"], "push_verify": {
        "jwks_url": "https://jwks.e65.example/jwks", "issuer": "https://e65.example"}}}, headers=H["dev"])
    assert dev.status_code == 403 and dev.json()["code"] == "push_verify_forbidden"
    monkeypatch.setattr(settings, "app_env", "prod")
    with pytest.raises(a2a_gateway.PushRejected, match="https"):
        a2a_gateway.verify_push_jwt({"jwks_url": "http://old.e65/jwks", "issuer": "https://e65.example"}, "Bearer x.y.z", b"{}")


# ── C65-11 artifacts — 상한보다 큰 최신 항목 하나 때문에 목록이 비지 않음(그 항목만 건너뜀) ──
def test_oversized_artifact_skipped(monkeypatch):
    monkeypatch.setattr(a2a_gateway, "ARTIFACTS_MAX_BYTES", 2000)
    rid = f"e65-art-{uuid.uuid4().hex[:6]}"
    arts = [{"artifactId": f"a{i}", "parts": [{"kind": "text", "text": "s"}]} for i in range(5)]
    arts.append({"artifactId": "huge", "parts": [{"kind": "text", "text": "x" * 5000}]})
    a2a_gateway.record(AG, {"kind": "task", "id": rid, "contextId": "c", "status": {"state": "working"}, "artifacts": arts})
    with SessionLocal() as db:
        row = db.get(A2ATask, f"{AG}:{rid}")
        ids = [a["artifactId"] for a in row.artifacts]
        db.delete(row)
        db.commit()
    assert ids == ["a0", "a1", "a2", "a3", "a4"]


# ── C65-12 OpenAPI 서버 생성 실패 — 이번에 만든 OBO 교환 정책도 정리 · 서버가 등록된 뒤 실패면 그 서버의 시크릿은 남긴다 ──
def test_openapi_failure_cleans_only_own_rows(client, H, monkeypatch):
    ns, ns2 = f"e65-{uuid.uuid4().hex[:6]}", f"e65-{uuid.uuid4().hex[:6]}"
    register_mount("http://e65-int", async_transport=httpx.ASGITransport(app=internal_system.app))
    doc = {"title": "E65", "openapi": internal_system.app.openapi(), "base_url": "http://e65-int"}

    def boom(*a, **k):
        raise RuntimeError("e65 publish failed")
    monkeypatch.setattr(registry, "publish", boom)
    with pytest.raises(RuntimeError, match="e65"):
        client.post("/api/v1/mcp-servers/openapi", json={**doc, "namespace": ns, "identity": "obo"}, headers=H["operator"])
    with SessionLocal() as db:
        assert db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == f"api:{ns}")).first() is None

    def registered_then_fail(db, *a, **k):  # 등록까지 끝난 뒤(예: 감사 기록) 실패
        db.add(McpServer(id=ns2, name=ns2, transport="hosted-openapi"))
        db.commit()
        raise RuntimeError("e65 failed after register")
    monkeypatch.setattr(registry, "publish", registered_then_fail)
    with pytest.raises(RuntimeError, match="after register"):
        client.post("/api/v1/mcp-servers/openapi", json={**doc, "namespace": ns2, "secret": "e65-backend"}, headers=H["operator"])
    with SessionLocal() as db:
        from app.services import secrets
        assert secrets.get_plain(db, f"mcp/{ns2}/backend") == "e65-backend"  # 등록된 서버가 쓰는 시크릿
        db.query(Secret).filter(Secret.name == f"mcp/{ns2}/backend").delete()
        db.delete(db.get(McpServer, ns2))
        db.commit()


# ── C65-13 번들 반입 — 서명된 매니페스트에 없는 config-backup.json 은 --restore-config 여도 복원하지 않음 ──
def test_restore_only_manifest_backup(cli_env, monkeypatch):  # noqa: F811
    b = cli_env / "b65"
    b.mkdir()
    (b / "notes.txt").write_text("x")
    (b / "config-backup.json").write_text(json.dumps({"bundle": {}, "signature": "old"}))  # 매니페스트 밖 — 끼워 넣은 옛 백업
    (b / "manifest.json").write_text(json.dumps({"format": "agentnexus-airgap/1", "files": {"notes.txt": nexus._sha256(b / "notes.txt")}}))
    calls = []
    monkeypatch.setattr(nexus, "_req", lambda *a, **k: calls.append(a) or {"restored": {}})
    res = run("bundle", "import", str(b), "--allow-unsigned", "--restore-config")
    assert res.exit_code == 0, res.output
    assert not any("restore" in str(c) for c in calls)


# ── C65-14 샘플링 실행 실패 — 라우팅 결정·실패로 기록하고 예약 반환 (전에는 버리기만) ──
def test_sampling_failure_recorded(monkeypatch):
    async def boom(plan, body):
        raise ApiError(502, "upstream_error", "e65 provider down")
    monkeypatch.setattr(llm, "execute", boom)
    sub = f"usr_e65_s_{uuid.uuid4().hex[:6]}"
    ctx = types.SimpleNamespace(principal=Principal(sub=sub, kind="user", roles=["user"]), data_class="internal",
                                session_id=None, run_id=None)
    with pytest.raises(mcp_consent.ConsentError):
        asyncio.run(mcp_consent._generate("jira", ctx, {"messages": [{"role": "user", "content": {"type": "text", "text": "x"}}],
                                                        "maxTokens": 20}, None))
    with SessionLocal() as db:
        rd = db.scalars(select(RoutingDecision).where(RoutingDecision.subject == sub)).all()
    assert [r.status for r in rd] == ["error"] and ratelimit.backend().holder_count(f"inflight:{sub}") == 0


# ── C65-15 gRPC readiness — 다른 요청이 확인 중이면 줄 서지 않고 직전 값 ──
def test_readiness_does_not_queue(client):
    async def slow(_):
        await asyncio.sleep(1.0)
        return JSONResponse({"status": "ok"})
    register_mount("http://e65-gw", async_transport=httpx.ASGITransport(Starlette(routes=[Route("/health", slow)])))

    async def run_() -> float:
        handler = a2a_grpc.health_handler("http://e65-gw")
        check = handler.service(types.SimpleNamespace(method=f"/{a2a_grpc.HEALTH_SERVICE}/Check")).unary_unary
        first = asyncio.create_task(check(a2a_grpc.health_request("readiness"), None))
        await asyncio.sleep(0.1)
        t0 = time.perf_counter()
        await check(a2a_grpc.health_request("readiness"), None)
        took = time.perf_counter() - t0
        await first
        return took
    assert client.portal.call(run_) < 0.3


# ── C65-16 사용자 검색 — LIKE 와일드카드는 글자 그대로 ──
def test_user_search_escapes_wildcards(client, H):
    items = client.get("/api/v1/identity/users", params={"q": "_"}, headers=H["admin"]).json()["items"]
    assert all("_" in " ".join(str(u.get(k) or "") for k in ("username", "email", "name")) for u in items)
    assert "admin" not in [u.get("username") for u in items]


# ── C65-17 대상용 Access Token — 빈 스코프면 교환 정책 기본 스코프 ──
def test_empty_scopes_use_policy_defaults(client, H):
    sid = f"e65-{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        db.add(McpServer(id=sid, name=sid, transport="streamable-http", endpoint=f"https://{sid}.e65.example/mcp"))
        db.add(TokenExchangePolicy(id=f"txp_e65_{uuid.uuid4().hex[:6]}", audience=f"mcp:{sid}", audience_type="mcp_server",
                                   allowed_scopes=["e65:read", "e65:write"], default_scopes=["e65:read"], ttl_seconds=300))
        db.commit()
    res = client.post("/api/v1/access-tokens", json={"name": "e65", "subject": "svc:e65", "audience": f"mcp:{sid}", "scopes": [],
                                                     "ttl_seconds": 300}, headers=H["operator"])
    assert res.status_code == 201 and res.json()["scopes"] == ["e65:read"], res.text
    client.post(f"/api/v1/access-tokens/{res.json()['id']}/revoke", headers=H["operator"])
    with SessionLocal() as db:
        db.query(TokenExchangePolicy).filter(TokenExchangePolicy.audience == f"mcp:{sid}").delete()
        db.delete(db.get(McpServer, sid))
        db.commit()
