"""Epic 39 — 품질 검토 반영 (D16-④): .review/ 위임·심층 리뷰에서 확인된 결함의 회귀 시험.

SEC-01 외부 IdP alg 허용 목록 · SEC-02/04 egress 가드(메타데이터·링크 로컬) · SEC-03 로그인 실패 한도 · SEC-05 loopback
redirect 는 local/test 만 · SEC-07 Access Token 주체 대리 금지 · 디바이스 승인 MFA · BUG-01/04/09 중계·작업 보존 ·
BUG-02 A2AServer 메모리 상한 · BUG-03/10 작업 생존 신호 · BUG-05 토큰 캐시 정리 · D-17 liveness.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import time
import types
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.api import idp as idp_api
from app.core import auth, http, keys, security
from app.core.config import settings
from app.db import SessionLocal, utcnow
from app.models import McpRelay, McpTask, User
from app.services import a2a_server, mcp_gateway, mcp_tasks, ratelimit, retention, totp


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# ── C39-01 외부 IdP — 헤더 alg 로 HS256·none 을 고르지 못한다 (알고리즘 혼동) ──
def test_external_idp_alg_allowlist(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="k1", alg="RS256")
    monkeypatch.setattr(auth, "_external_keys", lambda idp, refresh=False: {"keys": [jwk]})
    idp = types.SimpleNamespace(issuer="https://idp.example", audience="agentnexus", client_id="c")
    claims = {"sub": "u1", "iss": idp.issuer, "aud": "agentnexus", "exp": int(time.time()) + 60}
    assert auth._external_verify(idp, jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"}))["sub"] == "u1"
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    signing = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode()) + "." + b64(json.dumps(claims).encode())
    forged = signing + "." + b64(hmac.new(pem, signing.encode(), hashlib.sha256).digest())
    unsigned = b64(json.dumps({"alg": "none", "kid": "k1"}).encode()) + "." + b64(json.dumps(claims).encode()) + "."
    for token in (forged, unsigned):
        with pytest.raises(jwt.InvalidAlgorithmError):
            auth._external_verify(idp, token)
    with pytest.raises(jwt.InvalidTokenError):  # 키에 alg 가 있으면 헤더 alg 와 같아야 한다
        auth._external_verify(idp, jwt.encode(claims, key, algorithm="RS384", headers={"kid": "k1"}))


# ── C39-02 egress 가드 — 메타데이터·링크 로컬만 차단, 사설망 업스트림·시험 마운트는 그대로 ──
def test_egress_guard(client, H, monkeypatch):
    for url in ("http://169.254.169.254/latest/meta-data/", "http://[::ffff:169.254.169.254]/", "http://metadata.google.internal/x",
                "http://[fe80::1]/", "http://[fd00:ec2::254]/"):
        with pytest.raises(http.EgressBlocked), http.sync_client(timeout=2) as c:
            c.get(url)

    async def async_probe():
        async with http.async_client(timeout=2) as c:
            await c.get("http://169.254.169.254/")
    with pytest.raises(http.EgressBlocked):
        asyncio.run(async_probe())
    real = http.socket.getaddrinfo
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("169.254.169.254", 0))]
                        if host == "imds-alias.example" else real(host, *a, **k))
    http._RESOLVED.pop("imds-alias.example", None)
    assert http.blocked_url("http://imds-alias.example/") is True  # 이름이 메타데이터 주소로 해석돼도
    assert http.blocked_url("http://10.0.0.5:8080/api") is False and http.blocked_url("http://192.168.1.10/") is False
    http.register_mount("http://169.254.169.254", sync_transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    try:
        assert http.blocked_url("http://169.254.169.254/") is False  # 시험 마운트는 네트워크를 타지 않는다
    finally:
        http._SYNC_MOUNTS.pop("http://169.254.169.254", None)
    res = client.post("/api/v1/mcp-servers/openapi/fetch", json={"url": "http://169.254.169.254/latest/meta-data/"},
                      headers=H["dev"])
    assert res.status_code == 400 and res.json()["code"] == "fetch_failed" and "EgressBlocked" in res.json()["detail"]


def _user(username: str, password: str, mfa_secret: str | None = None) -> None:
    with SessionLocal() as db:
        if db.query(User).filter(User.username == username).first() is None:
            uid = "usr_" + uuid.uuid4().hex[:12]
            db.add(User(id=uid, sub=uid, issuer=security.issuer(), username=username, name=username, roles=["user"],
                        password_hash=security.hash_password(password), status="active", mfa_enabled=bool(mfa_secret),
                        mfa_secret=keys.fernet().encrypt(mfa_secret.encode()).decode() if mfa_secret else None))
            db.commit()


# ── C39-03 로그인 실패 한도 — 사용자 이름별, 한도에 걸리면 맞는 비밀번호도 거부 (브라우저·password grant) ──
def test_login_failure_lockout(client, monkeypatch):
    monkeypatch.setattr(idp_api, "LOGIN_MAX_FAILURES", 3)
    _user("lockout-probe", "Right-pass-39")
    params = {"response_type": "code", "client_id": "agentnexus-console", "redirect_uri": "http://testserver/console/callback",
              "state": "s", "scope": "openid", "code_challenge": security.pkce_s256("v" * 50), "code_challenge_method": "S256"}
    try:
        for _ in range(3):
            bad = client.post("/idp/authorize", data={**params, "username": "lockout-probe", "password": "wrong"},
                              follow_redirects=False)
            assert bad.status_code == 200 and "올바르지 않습니다" in bad.text
        locked = client.post("/idp/authorize", data={**params, "username": "Lockout-Probe ", "password": "Right-pass-39"},
                             follow_redirects=False)
        assert locked.status_code == 200 and "너무 많습니다" in locked.text
        grant = client.post("/idp/token", data={"grant_type": "password", "username": "lockout-probe",
                                                "password": "Right-pass-39", "client_id": "test"}).json()
        assert grant["error"] == "invalid_grant" and "너무 많습니다" in grant["error_description"]
    finally:
        ratelimit.backend().reset()
    ok = client.post("/idp/authorize", data={**params, "username": "lockout-probe", "password": "Right-pass-39"},
                     follow_redirects=False)
    assert ok.status_code == 302


# ── C39-04 loopback redirect_uri 는 local/test 에서만 (prod 콘솔은 같은 origin /console) ──
def test_loopback_redirect_only_local(client, monkeypatch):
    params = {"response_type": "code", "client_id": "agentnexus-console", "redirect_uri": "http://localhost:5173/login/callback",
              "state": "s", "code_challenge": security.pkce_s256("v" * 50), "code_challenge_method": "S256"}
    assert client.get("/idp/authorize", params=params).status_code == 200
    monkeypatch.setattr(settings, "app_env", "prod")
    assert client.get("/idp/authorize", params=params).status_code == 400
    same = {**params, "redirect_uri": settings.public_base_url.rstrip("/") + "/console/login/callback"}
    assert client.get("/idp/authorize", params=same).status_code == 200


# ── C39-05 디바이스 승인도 MFA 를 요구한다 ──
def test_device_approval_requires_mfa(client):
    secret = totp.new_secret()
    _user("mfa-device", "Mfa-pass-39", mfa_secret=secret)
    code = client.post("/idp/device/code", data={"client_id": "nexus-cli"}).json()["user_code"]
    form = {"user_code": code, "username": "mfa-device", "password": "Mfa-pass-39"}
    assert "MFA 코드가 필요" in client.post("/idp/device", data=form).text
    assert "승인했습니다" in client.post("/idp/device", data={**form, "otp": totp.code_at(secret)}).text


# ── C39-06 Access Token — 다른 사람의 sub 로 대리 발급 금지, 서비스 주체·자기 자신은 허용 ──
def test_access_token_subject_not_other_user(client, H):
    dev_sub = client.get("/api/v1/me", headers=H["dev"]).json()["sub"]
    own_sub = client.get("/api/v1/me", headers=H["security"]).json()["sub"]
    body = {"name": "e39", "audience": "agentnexus", "scopes": ["gateway:invoke"], "ttl_seconds": 300}
    res = client.post("/api/v1/access-tokens", json={**body, "subject": dev_sub}, headers=H["security"])
    assert res.status_code == 400 and res.json()["code"] == "subject_is_user"
    for subject in ("a2a:e39-service", own_sub):
        ok = client.post("/api/v1/access-tokens", json={**body, "subject": subject}, headers=H["security"])
        assert ok.status_code == 201, ok.text
        assert client.post(f"/api/v1/access-tokens/{ok.json()['id']}/revoke", headers=H["security"]).status_code == 200


# ── C39-07 보존 — 1일 지난 MCP 중계·작업 행 삭제, 최근 행 유지 ──
def test_retention_mcp_state(client):
    old, now = utcnow() - timedelta(days=2), utcnow()
    with SessionLocal() as db:
        for tag, ts in (("old", old), ("new", now)):
            db.add(McpRelay(id=f"rel-e39-{tag}", session_id=f"task-e39-{tag}", server_id="s", method="elicitation/create",
                            params={}, created_at=ts))
            db.add(McpTask(id=f"task-e39-{tag}", server_id="s", principal_sub="u", status="completed", ttl_ms=mcp_tasks.TTL_MS,
                           created_at=ts, updated_at=ts))
        db.commit()
        out = retention.run(db, "test")
        assert out["deleted"]["mcp_relays"] >= 1 and out["deleted"]["mcp_tasks"] >= 1
        assert db.get(McpRelay, "rel-e39-old") is None and db.get(McpTask, "task-e39-old") is None
        assert db.get(McpRelay, "rel-e39-new") is not None and db.get(McpTask, "task-e39-new") is not None
        db.delete(db.get(McpRelay, "rel-e39-new"))
        db.delete(db.get(McpTask, "task-e39-new"))
        db.commit()


# ── C39-08 작업 생존 신호 — 일시 DB 오류 뒤에도 계속, 감시 Task 는 강한 참조 · 즉시 끝난 작업은 중계 행도 함께 정리 ──
def test_task_heartbeat_survives_errors(client, monkeypatch):
    monkeypatch.setattr(mcp_tasks, "HEARTBEAT_S", 0.05)
    monkeypatch.setattr(mcp_tasks, "INLINE_S", 0.1)
    calls, real = [], mcp_tasks._beat

    def flaky(task_id):
        calls.append(task_id)
        if len(calls) == 1:
            raise RuntimeError("일시적 DB 오류")
        return real(task_id)
    monkeypatch.setattr(mcp_tasks, "_beat", flaky)
    ctx = types.SimpleNamespace(principal=types.SimpleNamespace(sub="e39-user"), relay=None)

    async def run(server_id, ctx, method, call):
        await asyncio.sleep(0.6)
        return {"content": [{"type": "text", "text": "done"}]}

    async def scenario():
        out = await mcp_tasks.start("srv-e39", ctx, {"name": "slow", "arguments": {}}, run)
        watchers = len(mcp_tasks._WATCHERS)
        await asyncio.sleep(1.0)
        return out, watchers
    out, watchers = client.portal.call(scenario)
    assert out.get("resultType") == "task" and watchers >= 1
    assert len(calls) >= 3  # 첫 신호 실패 뒤에도 감시가 이어졌다
    with SessionLocal() as db:
        row = db.get(McpTask, out["taskId"])
        assert row.status == "completed"
        db.delete(row)
        db.commit()


# ── C39-09 A2AServer 메모리 상한 — 종료 작업의 실행 문맥 해제, 상한을 넘으면 오래된 종료 작업부터 ──
def test_a2a_server_bounded():
    async def executor(ctx):
        return None
    server = a2a_server.A2AServer(lambda: {}, executor)
    server.MAX_TASKS = 3

    async def scenario():
        ids = []
        for i in range(6):
            task = await server._start({"kind": "message", "messageId": uuid.uuid4().hex, "role": "user",
                                        "parts": [{"kind": "text", "text": f"m{i}"}]}, {})
            ids.append(task["id"])
            await asyncio.sleep(0.01)
        return ids
    ids = asyncio.run(scenario())
    assert len(server.tasks) <= 3 and ids[-1] in server.tasks and ids[0] not in server.tasks
    assert not server.ctxs and not server.runners and not server.subscribers


# ── C39-10 업스트림 토큰 캐시 — 커지면 만료분을 정리 ──
def test_token_cache_prunes_expired():
    saved = dict(mcp_gateway._TOKENS)
    try:
        mcp_gateway._TOKENS.clear()
        for i in range(1100):
            mcp_gateway._TOKENS[("u", f"aud{i}", ())] = (time.time() - 1, "x")
        mcp_gateway._cache_token(("u", "fresh", ()), time.time() + 300, "y")
        assert list(mcp_gateway._TOKENS) == [("u", "fresh", ())]
    finally:
        mcp_gateway._TOKENS.clear()
        mcp_gateway._TOKENS.update(saved)


# ── C39-11 Helm — liveness 는 프로세스 생존만 (DB 장애에 api 재시작 금지), readiness 는 /health ──
def test_helm_liveness_tcp():
    text = Path(__file__).resolve().parents[1].joinpath("deploy/helm/agentnexus/templates/api.yaml").read_text()
    live = text.split("livenessProbe:", 1)[1].split("resources:", 1)[0]
    ready = text.split("readinessProbe:", 1)[1].split("livenessProbe:", 1)[0]
    assert "tcpSocket" in live and "httpGet" not in live and "path: /health" in ready


# ── C39-12 시크릿 참조 인가 — mcp:write·a2a:write 만으로 남의 시크릿(공급자 키 등)을 가리키지 못한다 ──
def test_secret_ref_requires_secret_manager(client, H):
    from app.services import registry
    foreign = {"auth": {"type": "bearer", "secret_ref": "provider/openai/api_key"}}
    for path in ("/api/v1/mcp-servers/jira", "/api/v1/mcp-servers/jira/test-config"):
        call = client.patch if path.endswith("jira") else client.post
        res = call(path, json=foreign, headers=H["dev"])
        assert res.status_code == 403 and res.json()["code"] == "secret_ref_forbidden", (path, res.text)
    tls = client.patch("/api/v1/mcp-servers/jira", json={"auth": {"tls": {"ca_secret": "tls/test-ca"}}}, headers=H["dev"])
    assert tls.status_code == 403
    a2a = client.patch("/api/v1/a2a/agents/observability-analyst", json={"auth": {"secret_ref": "provider/openai/api_key"}},
                       headers=H["dev"])
    assert a2a.status_code == 403 and a2a.json()["code"] == "secret_ref_forbidden"
    own = client.patch("/api/v1/mcp-servers/jira", json={"auth": {"secret_ref": "mcp/jira/backend"}}, headers=H["dev"])
    assert own.status_code == 403 and own.json()["code"] == "secret_destination_forbidden"  # 저장된 시크릿 다시 붙이기는 시크릿 관리 권한 (51st — 떼었다 붙이는 우회)
    own = client.patch("/api/v1/mcp-servers/jira", json={"secret": "e39-backend-token"}, headers=H["dev"])
    assert own.status_code == 200  # 새 값을 직접 넣는 설정은 그대로 허용 (자기 네임스페이스 mcp/jira/backend)
    # 자격 증명이 묶인 서버의 전송처 변경 — 새 값을 함께 넣지 않으면 시크릿 관리 권한 필요
    moved = client.patch("/api/v1/mcp-servers/jira", json={"endpoint": "https://attacker.example/mcp"}, headers=H["dev"])
    assert moved.status_code == 403 and moved.json()["code"] == "secret_destination_forbidden"
    probe = client.post("/api/v1/mcp-servers/jira/test-config", json={"endpoint": "https://attacker.example/mcp"}, headers=H["dev"])
    assert probe.status_code == 403 and probe.json()["code"] == "secret_destination_forbidden"
    with SessionLocal() as db:  # 원상 복구 — 시험 전 jira 에는 secret_ref 가 없었다
        from app.models import McpServer
        row = db.get(McpServer, "jira")
        row.auth = {k: v for k, v in (row.auth or {}).items() if k != "secret_ref"}
        db.commit()
    with SessionLocal() as db:  # 게시 메타데이터의 남의 시크릿 참조는 등록 때 떨어진다(자기 네임스페이스·비시크릿 값은 유지)
        from app.models import CatalogItem, McpServer
        meta = {"name": "io.e39/leak", "remotes": [{"type": "streamable-http", "url": "https://attacker.example/mcp"}],
                "_meta": {"auth": {"type": "bearer", "secret_ref": "provider/openai/api_key",
                                   "tls": {"ca_secret": "tls/test-ca", "client_cert_secret": "mcp/e39-leak/cert", "verify": True}}}}
        item = CatalogItem(id="cat_e39", type="mcp_server", name="io.e39/leak", status="approved", item_metadata=meta)
        db.add(item)
        db.flush()
        registry._register_mcp(db, item, "e39", {"namespace": "e39-leak"})
        auth = db.get(McpServer, "e39-leak").auth
        db.rollback()
    assert "secret_ref" not in auth and auth["tls"] == {"client_cert_secret": "mcp/e39-leak/cert", "verify": True}


# ── C39-13 인가 서버 고정 — 401 재탐색이 등록과 다른 AS 를 내밀면 거부 ──
def test_authorization_server_pinned():
    srv = types.SimpleNamespace(id="e39", auth={"authorization_server": "https://as.example"})
    with pytest.raises(mcp_gateway.RpcError) as exc:
        mcp_gateway._pinned(srv, {"authorization_server": "https://attacker.example"})
    assert exc.value.data["code"] == "authorization_server_changed"
    assert mcp_gateway._pinned(srv, {"authorization_server": "https://as.example/"})
    fresh = types.SimpleNamespace(id="e39", auth={})
    assert mcp_gateway._pinned(fresh, {"authorization_server": "https://as.example"})["authorization_server"]


# ── C39-14 nexus install 의 env 생성 — DB 접속 문자열 비밀번호 일치 · 서명 키는 자동 생성(비움) ──
def test_write_env_consistent(tmp_path):
    from app.cli import nexus
    ex, out = tmp_path / ".env.example", tmp_path / ".env.gen"
    ex.write_text("DB_PASSWORD=CHANGE_ME\nDATABASE_URL=postgresql+psycopg://app:CHANGE_ME@db:5432/app\n"
                  "SIGNING_KEY_PEM=CHANGE_ME\nENCRYPTION_KEY=CHANGE_ME\n")
    assert nexus.write_env(ex, out) == 3
    env = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert env["DATABASE_URL"] == f"postgresql+psycopg://app:{env['DB_PASSWORD']}@db:5432/app"
    assert env["SIGNING_KEY_PEM"] == "" and "CHANGE_ME" not in out.read_text()


# ── C39-15 CLI 파일 쓰기 — 서버 응답·번들 매니페스트의 경로가 대상 밖이면 중단 ──
def test_cli_rejects_path_traversal(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from app.cli import nexus
    monkeypatch.setattr(nexus, "_req", lambda *a, **k: {"files": {"server.py": "ok", "../escape.txt": "x"}})
    res = CliRunner().invoke(nexus.app, ["mcp", "scaffold", "e39", "--dir", str(tmp_path / "e39")],
                             env={"NEXUS_TOKEN": "t", "NEXUS_CONFIG_DIR": str(tmp_path / "cfg")})
    assert res.exit_code == 1 and "대상 디렉터리 밖" in res.output and not (tmp_path / "escape.txt").exists()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (tmp_path / "victim.txt").write_text("keep")
    (bundle / "manifest.json").write_text(json.dumps({"files": {"../victim.txt": "0" * 64}}))
    res = CliRunner().invoke(nexus.app, ["bundle", "import", str(bundle)],
                             env={"NEXUS_TOKEN": "t", "NEXUS_CONFIG_DIR": str(tmp_path / "cfg")})
    assert res.exit_code == 1 and "번들 밖" in res.output and (tmp_path / "victim.txt").read_text() == "keep"


# ── C39-16 정책 2인 검토 — 남의 초안을 고친 사람도 승인할 수 없다 ──
def test_policy_editor_cannot_approve(client, H):
    P = "/api/v1/gateway"
    base = {"name": "e39 편집자 승인 금지", "subject_type": "role", "subject": "user", "target_type": "tool",
            "target": "e39x.*", "effect": "deny", "priority": 31}
    d = client.post(f"{P}/policies/drafts", json={"policy": base}, headers=H["security"]).json()
    try:
        assert client.put(f"{P}/policies/drafts/{d['id']}", json={**base, "effect": "allow"}, headers=H["admin"]).status_code == 200
        assert client.post(f"{P}/policies/drafts/{d['id']}/test", headers=H["admin"]).json()["test_result"]["passed"] is True
        assert client.post(f"{P}/policies/drafts/{d['id']}/submit", json={"reason": "e39"}, headers=H["admin"]).status_code == 200
        res = client.post(f"{P}/policies/drafts/{d['id']}/approve", json={}, headers=H["admin"])
        assert res.status_code == 403 and res.json()["code"] == "self_approval"
    finally:
        client.post(f"{P}/policies/drafts/{d['id']}/reject", json={"note": "e39 정리"}, headers=H["admin"])


# ── C39-17 승인 1회 사용 — 동시 사용 중 하나만 성공 ──
def test_approval_consumed_once():
    from app.models import Approval
    from app.services import governance
    with SessionLocal() as db:
        ap = Approval(id="apr_e39", kind="tool_call", status="approved", requester_sub="u", server_id="s", tool="t",
                      target="s.t", args_hash="h", risk="high", reason="e39")
        db.add(ap)
        db.commit()
        first, second = governance._consume(db, ap), governance._consume(db, db.get(Approval, "apr_e39"))
        assert (first, second) == (True, False) and db.get(Approval, "apr_e39").status == "used"
        db.delete(db.get(Approval, "apr_e39"))
        db.commit()


# ── C39-18 푸시 — 토큰이 묶인 Task 만 갱신 (같은 에이전트의 다른 Task 위조 거부) ──
def test_push_bound_to_task(client):
    from app.models import A2ATask
    with SessionLocal() as db:
        db.add(A2ATask(id="observability-analyst:e39-push", remote_task_id="e39-push", agent_id="observability-analyst",
                       state="working", push_token="e39-tok", history=[], artifacts=[]))
        db.commit()
    try:
        forged = {"kind": "task", "id": "e39-victim", "status": {"state": "completed"}}
        assert client.post("/a2a/push/e39-tok", json=forged).status_code == 404
        with SessionLocal() as db:
            assert db.get(A2ATask, "observability-analyst:e39-victim") is None
        ok = {"kind": "task", "id": "e39-push", "status": {"state": "completed"}}
        assert client.post("/a2a/push/e39-tok", json=ok).status_code == 200
    finally:
        with SessionLocal() as db:
            db.delete(db.get(A2ATask, "observability-analyst:e39-push"))
            db.commit()


# ── C39-19 원격 Task 폴링 — 계속 실패하는 Task 가 앞자리를 독점하지 않고 돌아간다 ──
def test_watcher_rotates(client):
    from app.models import A2ATask
    from app.services import a2a_gateway
    ids = [f"observability-analyst:e39-w{i}" for i in range(3)]
    with SessionLocal() as db:  # 주체 없는 Task — 폴링 대상이지만 호출은 건너뛴다(실패와 같은 효과)
        db.query(A2ATask).filter(A2ATask.direction == "outbound", A2ATask.state.in_(("submitted", "working"))) \
            .update({"last_polled_at": utcnow()}, synchronize_session=False)
        for i, tid in enumerate(ids):
            db.add(A2ATask(id=tid, remote_task_id=tid.split(":")[1], agent_id="observability-analyst", direction="outbound",
                           state="working", history=[], artifacts=[], created_at=utcnow() - timedelta(hours=3 - i)))
        db.commit()
    try:
        client.portal.call(a2a_gateway.poll_once, 2)
        client.portal.call(a2a_gateway.poll_once, 2)
        with SessionLocal() as db:
            assert all(db.get(A2ATask, tid).last_polled_at is not None for tid in ids)
    finally:
        with SessionLocal() as db:
            for tid in ids:
                db.delete(db.get(A2ATask, tid))
            db.commit()


# ── C39-20 refresh token 회전 — 같은 토큰의 동시 요청 중 하나만 성공 (오래된 세션 상태로 경쟁을 재현) ──
def test_refresh_rotation_single_winner(client):
    from app.core.errors import ApiError
    from app.models import RefreshToken
    from app.services import identity
    raw = security.opaque("anx_rt_")
    with SessionLocal() as db:
        db.add(RefreshToken(id="rt_e39", token_hash=security.token_hash(raw), user_id="usr_dev", client_id="agentnexus-console",
                            scope="openid", expires_at=utcnow() + timedelta(hours=1)))
        db.commit()
    slow, fast = SessionLocal(), SessionLocal()
    try:
        slow.scalars(identity.select(RefreshToken).where(RefreshToken.id == "rt_e39")).first()  # 회전 전 상태를 들고 있는 요청
        assert identity.refresh(fast, raw, "agentnexus-console")["refresh_token"]
        with pytest.raises(ApiError) as exc:
            identity.refresh(slow, raw, "agentnexus-console")
        assert exc.value.code == "invalid_grant"
    finally:
        slow.close()
        fast.close()


# ── C39-21 도구 호출 — RpcError 밖의 예외도 사용량·지표에 error 로 남는다 ──
def test_tool_call_generic_error_recorded(monkeypatch):
    seen = []

    async def boom(*a, **k):
        raise RuntimeError("stdio 프로세스 종료")
    monkeypatch.setattr(mcp_gateway, "call_server", boom)
    monkeypatch.setattr(mcp_gateway, "_record_tool", lambda ctx, q, sid, ms, status, *a: seen.append(status))
    dec = types.SimpleNamespace(allow=True, code="allowed", risk="low", reason="", sanitize=False)
    ctx = types.SimpleNamespace(principal=types.SimpleNamespace(sub="u", kind="user"), agent=None)
    with pytest.raises(RuntimeError):
        asyncio.run(mcp_gateway._call_tool_inner(types.SimpleNamespace(id="e39"), ctx, "t", "e39.t", {}, dec, None, "call", "e39"))
    assert seen == ["error"]


# ── C39-22 서버→클라이언트 요청 처리 중 예외 — 업스트림에 -32603 으로 즉시 답한다 ──
def test_consent_handle_unexpected_error():
    from app.services import mcp_consent

    class Relay:
        session_id = "s"

        def supports(self, method):
            raise RuntimeError("relay 상태 손상")
    ctx = types.SimpleNamespace(relay=Relay(), principal=types.SimpleNamespace(sub="u", kind="user"))
    out = asyncio.run(mcp_consent.handle("e39", ctx, {"jsonrpc": "2.0", "id": 7, "method": "sampling/createMessage", "params": {}}))
    assert out["id"] == 7 and out["error"]["code"] == -32603


# ── C39-23 라우팅 A/B 가중치 검증 — 숫자 아님·음수·합 0 은 저장 거부 ──
def test_ab_weights_validated(client, H):
    base = {"name": "e39-ab", "match_model": "e39-*", "strategy": "ab"}
    for targets in ([{"model": "m1", "weight": "많이"}], [{"model": "m1", "weight": -1}], [{"model": "m1", "weight": 0}]):
        res = client.post("/api/v1/gateway/routing/rules", json={**base, "targets": targets}, headers=H["operator"])
        assert res.status_code == 422, (targets, res.text)
    ok = client.post("/api/v1/gateway/routing/rules", json={**base, "targets": [{"model": "m1", "weight": 3}, {"model": "m2"}]},
                     headers=H["operator"])
    assert ok.status_code == 201
    client.delete(f"/api/v1/gateway/routing/rules/{ok.json()['id']}", headers=H["operator"])


# ── C39-24 입력 마스킹 — 멀티모달 메시지의 이미지 파트를 지우지 않는다 ──
def test_masking_keeps_image_parts(monkeypatch):
    from app.services import guards, llm
    monkeypatch.setattr(guards, "scan_input", lambda db, text: types.SimpleNamespace(blocked=False, types=["email"], text="[MASKED]",
                                                                                     reason=None))
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
    body = {"messages": [{"role": "user", "content": [img, {"type": "text", "text": "a@b.com"}, {"type": "text", "text": " 확인"}]}]}
    with SessionLocal() as db:
        assert llm._guard_messages(db, body) == ["email"]
    assert body["messages"][0]["content"] == [img, {"type": "text", "text": "[MASKED]"}]


# ── C39-25 리포트 서명 검증 — 같은 키로 서명된 사용자 토큰은 리포트 서명이 아니다 ──
def test_report_signature_rejects_user_token(client, H, tokens):
    res = client.post("/api/v1/audit/reports/verify", json={"signature": tokens["user"]}, headers=H["security"]).json()
    assert res["valid"] is False


# ── C39-26 CLI `context add` — 현재 컨텍스트의 URL 도 바뀐다 ──
def test_cli_context_add_current(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from app.cli import nexus
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({"current": "dev", "url": "http://old:8800",
                                                     "contexts": {"dev": {"url": "http://old:8800"}}}))
    res = CliRunner().invoke(nexus.app, ["context", "add", "dev", "http://new:8800/"], env={"NEXUS_CONFIG_DIR": str(cfg_dir)})
    assert res.exit_code == 0, res.output
    cfg = json.loads((cfg_dir / "config.json").read_text())
    assert cfg["url"] == "http://new:8800" and cfg["contexts"]["dev"]["url"] == "http://new:8800"
