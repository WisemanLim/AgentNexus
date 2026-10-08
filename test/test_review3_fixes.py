"""Epic 59 — 3차 품질 검토 확인 결함 수정 (D21-④): `.review/round3/VERIFY.md` 의 '수정' 항목.
IdP 신뢰 설정 · 스트림 끊김 정산·예약 고정 만료 · 번들 검증 키 · TOTP 정규화 · SSE 줄 상한 · 재암호화 범위·배치 · 미러 보류·소유 ·
Host 재개 · 스팬 집계 범위 · 설치 다이제스트 · 폴백 모델 없음 · 토큰 캐시 · 키 파일 권한 · 백업 서명 발급자 · 수신 상한 · 위험 동사."""
import asyncio
import gzip
import json
import os
import subprocess
import time
import types
import uuid
from pathlib import Path

import httpx
import jwt
import pytest
from sqlalchemy import event, select
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.api import identity as identity_api
from app.api import llm as llm_api
from app.api import observe
from app.cli import nexus
from app.core import keys, tracing
from app.core.auth import Principal
from app.core.config import settings
from app.core.http import register_mount
from app.db import SessionLocal, engine, utcnow
from app.models import A2ATask, CatalogVersion, RateLimit, RegistrySource, Run, RunStep, UsageRecord
from app.services import a2a_gateway, governance, llm, orchestrator, ratelimit, registry, review, totp
from app.services.a2a_gateway import A2AError
from test.conftest import approve_agent
from test.test_a2a import AG, call, msg, sse
from test.test_catalog import server_json
from test.test_cli_install import cli_env, run  # noqa: F401 — 픽스처
from test.test_review_fixes import _user


# ── C59-01 admin 매핑 IdP — 신뢰 설정(jwks_uri 등)은 admin 만, 이름·중지는 보안 담당도 · 좁힌 admin 토큰은 admin 부여 불가 ──
def test_admin_mapped_idp_trust_fields_admin_only(client, H):
    iss = f"https://idp-e59-{uuid.uuid4().hex[:6]}.example"
    idp = client.post("/api/v1/identity/idps", json={"name": "e59", "issuer": iss, "client_id": "x",
                                                    "role_mapping": {"AgentNexus-Admins": "admin"}}, headers=H["admin"])
    assert idp.status_code == 201, idp.text
    url = f"/api/v1/identity/idps/{idp.json()['id']}"
    for body in ({"jwks_uri": "https://attacker.e59.example/jwks.json"}, {"internal_url": "http://attacker.e59"},
                 {"groups_claim": "grp"}, {"audience": "x"}, {"enabled": True}, {"client_secret": "e59-not-real"}):
        res = client.patch(url, json=body, headers=H["security"])
        assert res.status_code == 403 and res.json()["code"] == "admin_grant_forbidden", (body, res.text)
    assert client.patch(url, json={"name": "e59 renamed"}, headers=H["security"]).status_code == 200
    assert client.patch(url, json={"enabled": False}, headers=H["security"]).status_code == 200  # 끄는 쪽은 권한을 넓히지 않음
    assert client.patch(url, json={"jwks_uri": "https://idp.e59.example/jwks"}, headers=H["admin"]).status_code == 200
    plain = client.post("/api/v1/identity/idps", json={"name": "e59b", "issuer": iss + "/b", "client_id": "x",
                                                      "role_mapping": {"devs": "developer"}}, headers=H["security"])
    assert client.patch(f"/api/v1/identity/idps/{plain.json()['id']}", json={"jwks_uri": "https://idp.e59.example/b"},
                        headers=H["security"]).status_code == 200  # admin 매핑이 없으면 그대로
    for i in (idp.json()["id"], plain.json()["id"]):
        client.delete(f"/api/v1/identity/idps/{i}", headers=H["admin"])
    narrowed = Principal(sub="usr_admin", kind="pat", roles=["admin"], scopes=["identity:write"])
    assert not identity_api._is_admin(narrowed) and identity_api._is_admin(Principal(sub="usr_admin", kind="user", roles=["admin"]))


# ── C59-02 스트림 끊김 — 받은 만큼 사용 기록(cancelled)·예약 정산 · 예산 예약은 예약마다 고정 만료(활동 중에도 풀림) ──
def test_stream_disconnect_settles_and_holds_expire(client, monkeypatch):
    lid = f"rl_e59_{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        db.add(RateLimit(id=lid, name="e59", enabled=True, target_type="model", target="*", scope_type="user", scope="*",
                         tokens_per_min=1_000_000))
        db.commit()
    p = Principal(sub=f"usr_e59_{uuid.uuid4().hex[:6]}", kind="user", roles=["user"])
    ctx = llm.CallContext(p, "internal")
    body = {"model": "auto", "stream": True, "messages": [{"role": "user", "content": "스트림 끊김 시험 " * 20}]}
    try:
        with SessionLocal() as db:
            plan = llm.prepare(db, ctx, body)
        assert any(h[0] == "tok" for h in plan.holds)

        async def scenario():
            agen = llm_api._sse(ctx, plan, body, time.perf_counter())
            first = await agen.__anext__()
            await agen.aclose()  # 클라이언트 끊김
            return first
        assert asyncio.run(scenario()).startswith("data: ")  # asyncio.run 이 기본 실행기를 기다린다 = 기록 완료
        with SessionLocal() as db:
            recs = db.scalars(select(UsageRecord).where(UsageRecord.user_sub == p.sub)).all()
        assert [r.status for r in recs] == ["cancelled"] and recs[0].tokens_in > 0 and plan.holds == []
    finally:
        with SessionLocal() as db:
            db.delete(db.get(RateLimit, lid))
            db.query(UsageRecord).filter(UsageRecord.user_sub == p.sub).delete()
            db.commit()
    b = ratelimit.MemoryBackend()
    t = [1_000_000.0]
    monkeypatch.setattr(ratelimit.time, "time", lambda: t[0])
    assert b.amount_hold("usd", "leaked", 500, 300) == 500  # 정산 없이 워커가 죽은 예약
    for _ in range(5):  # 같은 범위의 요청이 계속 와도(활동) 만료가 늘지 않는다
        t[0] += 59
        assert b.amount_hold("usd", "", 0, 300) == 500
    t[0] += 6
    assert b.amount_hold("usd", "live", 7, 300) == 7
    b.amount_release("usd", "live")
    assert b.amount_hold("usd", "", 0, 300) == 0


# ── C59-03 번들 반입 — 검증 키는 번들 밖에서(--cosign-key), 번들 안 키·번들 안을 가리키는 키는 거부 ──
def test_bundle_import_needs_out_of_band_key(cli_env, monkeypatch):  # noqa: F811
    b = cli_env / "swapped"
    b.mkdir()
    (b / "api.tar").write_bytes(b"attacker image")
    (b / "manifest.json").write_text(json.dumps({"format": "agentnexus-airgap/1", "files": {"api.tar": nexus._sha256(b / "api.tar")}}))
    (b / "cosign.pub").write_text("attacker-pub")  # 공격자가 자기 키로 함께 바꿔 넣음
    (b / "manifest.json.sig").write_text("signed-by-attacker")
    calls = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        if cmd[:2] == ["cosign", "verify-blob"]:
            return subprocess.CompletedProcess(cmd, 0 if Path(cmd[3]).read_text() == "attacker-pub" else 1)
        return subprocess.CompletedProcess(cmd, 0, stdout="Loaded image: api:1.0\n")
    monkeypatch.setattr(nexus.subprocess, "run", fake_run)
    monkeypatch.setattr(nexus.shutil, "which", lambda name: "/usr/bin/cosign")
    inside = run("bundle", "import", str(b), "--no-restore-config")
    assert inside.exit_code == 1 and "--cosign-key" in inside.output
    via = run("bundle", "import", str(b), "--no-restore-config", "--cosign-key", str(b / "cosign.pub"))
    assert via.exit_code == 1 and not any(c[:2] == ["docker", "load"] for c in calls)  # 번들 안을 가리켜도 거부
    (cli_env / "ops.pub").write_text("operator-pub")
    real = run("bundle", "import", str(b), "--no-restore-config", "--cosign-key", str(cli_env / "ops.pub"))
    assert real.exit_code == 1 and "매니페스트 서명" in real.output and not any(c[:2] == ["docker", "load"] for c in calls)


# ── C59-04 TOTP — 공백을 넣은 같은 코드("123 456")도 재사용 거부 ──
def test_totp_reuse_with_space_rejected(client):
    secret = totp.new_secret()
    _user("e59-mfa", "Mfa-pass-59", mfa_secret=secret)
    code = totp.code_at(secret)
    form = {"grant_type": "password", "username": "e59-mfa", "password": "Mfa-pass-59", "client_id": "test", "otp": code}
    assert client.post("/idp/token", data=form).status_code == 200
    again = client.post("/idp/token", data={**form, "otp": f"{code[:3]} {code[3:]}"})
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


# ── C59-05 원격 스트림 — 줄바꿈 없는 큰 줄은 상한에서 끊고 오류 이벤트 · 압축 응답 거부(identity 요청) ──
def test_remote_sse_line_cap_and_no_compression(client, H, monkeypatch):
    monkeypatch.setattr(a2a_gateway, "MAX_REMOTE_BYTES", 4096)
    seen = []
    card = {"name": "E59 stream", "description": "큰 줄", "version": "1", "capabilities": {"streaming": True},
            "skills": [{"id": "s", "name": "S"}], "securitySchemes": {},
            "supportedInterfaces": [{"url": "http://e59-sse.test/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]}

    async def rpc(req):
        seen.append(req.headers.get("accept-encoding"))
        return Response("data: " + "x" * 50_000, media_type="text/event-stream")  # 줄바꿈 없이 계속
    register_mount("http://e59-sse.test", async_transport=httpx.ASGITransport(app=Starlette(routes=[
        Route("/.well-known/agent-card.json", lambda _: JSONResponse(card)), Route("/", rpc, methods=["POST"])])))
    assert client.post("/api/v1/a2a/agents", json={"url": "http://e59-sse.test", "id": "e59-sse", "auth": {"scheme": "none"}},
                       headers=H["dev"]).status_code == 201
    approve_agent(client, H, "e59-sse")
    client.put("/api/v1/gateway/governance/agents/e59-sse/trust", json={"trust_tier": "trusted"}, headers=H["security"])
    events = sse(call(client, H["user"], "message/stream", {"message": msg("x")}, path="/a2a/e59-sse").text)
    assert events[-1]["error"]["message"] == "원격 이벤트가 상한을 넘습니다." and seen and set(seen) == {"identity"}
    bomb = gzip.compress(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"kind": "task", "id": "t", "pad": "0" * 100_000}}).encode())
    register_mount("http://e59-gz.test", async_transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=bomb, headers={"content-encoding": "gzip", "content-type": "application/json"})))
    with pytest.raises(A2AError) as exc:
        asyncio.run(a2a_gateway._rpc("http://e59-gz.test/", "message/send", {}, {}, "0.3"))
    assert "압축" in exc.value.message


# ── C59-06 재암호화 — A2A 웹훅 토큰도 새 키로(이전 키 제거 뒤에도 중계 가능) ──
def test_reencrypt_covers_push_tokens(client, H):
    tid = f"{AG}:e59-push-{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        db.add(A2ATask(id=tid, remote_task_id=tid.split(":")[1], agent_id=AG, state="working", history=[], artifacts=[],
                       push_config={"url": "http://e59-hook.test/h", "token_enc": keys.fernet().encrypt(b"client-e59").decode()}))
        db.commit()
    rot = client.post("/api/v1/admin/keys/rotate", json={"kind": "encryption", "confirm": "ROTATE"}, headers=H["admin"])
    assert rot.status_code == 200 and rot.json()["failed"] == 0 and rot.json()["push_tokens"] >= 1, rot.text
    dropped = client.post("/api/v1/admin/keys/reencrypt", params={"drop_previous": "true"}, headers=H["admin"]).json()
    assert dropped["failed"] == 0 and dropped["dropped"] == 1
    with SessionLocal() as db:
        cfg = db.get(A2ATask, tid).push_config
        assert keys.fernet().decrypt(cfg["token_enc"].encode()) == b"client-e59"  # 이전 키 없이 풀린다
        db.delete(db.get(A2ATask, tid))
        db.commit()


# ── C59-07 미러 자동 승인 — oauth2 인데 인가 서버가 플랫폼 issuer 로 고정되지 않은 항목은 보류 ──
def test_mirror_holds_unpinned_oauth():
    from app.core.security import issuer
    with SessionLocal() as db:
        unpinned = server_json(name=f"io.e59/m-{uuid.uuid4().hex[:6]}")  # _meta.auth = {type: oauth2} — 등록 때 업스트림이 인가 서버를 정함
        pinned = server_json(name=f"io.e59/m-{uuid.uuid4().hex[:6]}", _meta={"auth": {"type": "oauth2", "authorization_server": issuer()}})
        noauth = server_json(name=f"io.e59/m-{uuid.uuid4().hex[:6]}", _meta={"license": "MIT"})
        vers = [registry.publish(db, "mcp_server", d["name"], "1.0.0", d, "system:mirror", source="mirror", auto_approve=True)[1]
                for d in (unpinned, pinned, noauth)]
        db.commit()
        assert [db.get(CatalogVersion, v.id).status for v in vers] == ["pending", "approved", "approved"]


# ── C59-08 미러 동기화 — 같은 이름의 수동 게시 항목에 버전을 얹지 않는다 ──
def test_mirror_sync_skips_items_owned_elsewhere():
    name = f"io.e59/own-{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        registry.publish(db, "mcp_server", name, "1.0.0", server_json(name=name, _meta={"license": "MIT"}), "usr_dev", source="manual")
        src = RegistrySource(id=f"src_e59_{uuid.uuid4().hex[:6]}", name=f"e59-{uuid.uuid4().hex[:4]}", kind="official",
                             url="https://registry.e59.test", auto_approve=True, enabled=True)
        db.add(src)
        db.commit()
        counts = registry.import_entries(db, src, [server_json(name=name, version="9.9.9", _meta={"license": "MIT"})], "system:sync")
        item = registry.find_item(db, "mcp_server", name)
        assert counts["invalid"] == 1 and counts["errors"][0]["error"] == "name_owned_elsewhere"
        assert item.version == "1.0.0" and db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id,
                                                                                 CatalogVersion.version == "9.9.9")).first() is None
        db.delete(src)
        db.commit()


# ── C59-09 승인 만료·결정 — 실행 재개는 Host 프로세스만 (게이트웨이 단독이면 Host 복구 작업이 이어받음) ──
def test_resume_only_in_host(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator, "schedule", lambda rid, resume=False: calls.append((rid, resume)))
    out = []
    for comps in ("gateway", "all"):
        monkeypatch.setattr(settings, "components", comps)
        rid, ap_id = f"run_e59_{uuid.uuid4().hex[:8]}", f"ap_e59_{uuid.uuid4().hex[:8]}"
        with SessionLocal() as db:
            db.add(Run(id=rid, user_sub="usr_user", profile_id="tpl-qa", status="waiting_approval", request="e59"))
            db.add(RunStep(id=f"st_{uuid.uuid4().hex[:8]}", run_id=rid, seq=1, node="executor", name="s1", status="waiting_approval",
                           approval_id=ap_id))
            db.commit()
        orchestrator.on_approval(types.SimpleNamespace(id=ap_id, run_id=rid, status="expired", decided_by="system:expire"))
        out.append(list(calls))
        with SessionLocal() as db:
            assert db.scalars(select(RunStep).where(RunStep.run_id == rid)).first().status == "rejected"
            db.delete(db.get(Run, rid))
            db.commit()
    assert out[0] == [] and out[1] == [(rid, True)]


# ── C59-10 개요 KPI — 자식 스팬 집계에 시간 조건(보존 기간 전체를 GROUP BY 하지 않음) ──
def test_overview_child_spans_bounded():
    seen = []
    listen = lambda conn, cur, stmt, *a: seen.append(stmt)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listen)
    try:
        with SessionLocal() as db:
            observe._overview_extra(db, utcnow())
    finally:
        event.remove(engine, "before_cursor_execute", listen)
    agg = [s for s in seen if "max(spans_1.duration_ms)" in s]  # 65th: GROUP BY 대신 부모마다 상관 하위 질의(인덱스)
    assert agg and "GROUP BY" not in agg[0] and "spans_1.parent_id = spans.id" in agg[0]


# ── C59-11 설치 — 서명 검증은 받은(pull) 다이제스트로, 기동은 빌드·pull 없이 그 이미지로 · 레지스트리 다이제스트 없으면 중단 ──
def test_install_verifies_pulled_digests(cli_env, monkeypatch):  # noqa: F811
    Path("install.yaml").write_text("mode: compose\nairgap: false\nprofile: full\n")
    Path(".env.example").write_text("APP_ENV=prod\nAPI_PORT=8800\n")
    Path("cosign.pub").write_text("pub")
    monkeypatch.setattr(nexus, "_healthy", lambda port: True)
    digests = {"reg.e59/api:1": "reg.e59/api@sha256:aaa", "local-build:prod": ""}
    calls = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        if "config" in cmd and "--images" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=" ".join(imgs))
        if cmd[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=digests[cmd[-1]])
        return subprocess.CompletedProcess(cmd, 0, stdout="")
    monkeypatch.setattr(nexus.subprocess, "run", fake_run)
    imgs = ["reg.e59/api:1"]
    ok = run("install", "compose", "--cosign-key", "cosign.pub", "--wait", "1")
    assert ok.exit_code == 0, ok.output
    assert ["cosign", "verify", "--key", "cosign.pub", "reg.e59/api@sha256:aaa"] in calls
    up = next(c for c in calls if "up" in c)
    assert up[-4:] == ["-d", "--no-build", "--pull", "never"] and calls.index(up) > calls.index(next(c for c in calls if c[-1] == "pull"))
    calls.clear()
    imgs = ["reg.e59/api:1", "local-build:prod"]  # 같은 태그의 로컬 빌드 — 레지스트리 다이제스트 없음
    bad = run("install", "compose", "--cosign-key", "cosign.pub", "--wait", "1")
    assert bad.exit_code == 1 and "local-build:prod" in bad.output and not any("up" in c for c in calls)


# ── C59-12 한도 폴백 — 폴백 모델이 없거나 꺼져 있으면 원래 모델로 한도 없이 가지 않고 429 ──
def test_limit_fallback_missing_model_is_429(client, H):
    lid = f"rl_e59_{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        db.add(RateLimit(id=lid, name="e59-fb", enabled=True, target_type="model", target="*", scope_type="user", scope="*",
                         tokens_per_min=1, on_exceed="fallback", fallback_model="e59-no-such-model"))
        db.commit()
    ratelimit.backend().reset()
    try:
        res = client.post("/v1/chat/completions", json={"model": "auto", "messages": [{"role": "user", "content": "한도 폴백 시험"}]},
                          headers=H["user"])
        assert res.status_code == 429 and "e59-no-such-model" in res.text, res.text
    finally:
        with SessionLocal() as db:
            db.delete(db.get(RateLimit, lid))
            db.commit()
        ratelimit.backend().reset()


# ── C59-13 에이전트 인증 변경 — 실제 교환 토큰 캐시(("x", sub, id))도 버린다 ──
def test_exchange_token_cache_dropped(client, H, monkeypatch):
    monkeypatch.setattr(a2a_gateway.identity, "exchange", lambda db, p, aud, scopes, actor=None: {"access_token": "old-x", "expires_in": 600})
    p = Principal(sub="usr_user", kind="user", roles=["user"])
    agent = types.SimpleNamespace(id=AG, auth={"scheme": "openIdConnect"})
    a2a_gateway._TOKENS.pop(("x", "usr_user", AG), None)  # 앞 시험이 채운 실제 토큰
    assert asyncio.run(a2a_gateway.upstream_headers(agent, p)) == {"Authorization": "Bearer old-x"}
    assert ("x", "usr_user", AG) in a2a_gateway._TOKENS
    assert client.patch(f"/api/v1/a2a/agents/{AG}", json={"auth": {"scopes": ["read"]}}, headers=H["operator"]).status_code == 200
    assert ("x", "usr_user", AG) not in a2a_gateway._TOKENS


# ── C59-14 키 파일 — 회전이 덧붙이는 이전 암호화 키·공개키 보관본도 처음부터 0600 (이전 버전이 만든 0644 도) ──
def test_rotation_files_private(client, H):
    d = keys._keys_dir()
    archive = d / keys.SIGNING_ARCHIVE
    if archive.exists():
        os.chmod(archive, 0o644)
    old = os.umask(0o022)
    try:
        assert client.post("/api/v1/admin/keys/rotate", json={"kind": "encryption", "confirm": "ROTATE"}, headers=H["admin"]).status_code == 200
        assert oct((d / keys.FERNET_PREV).stat().st_mode)[-3:] == "600"
        client.post("/api/v1/admin/keys/reencrypt", params={"drop_previous": "true"}, headers=H["admin"])
        keys._append_private(archive, b"")
        assert oct(archive.stat().st_mode)[-3:] == "600"
    finally:
        os.umask(old)


# ── C59-15 백업 서명 — 같은 키로 서명했어도 발급자가 agentnexus-backup 가 아니면 거부 ──
def test_backup_signature_requires_backup_issuer(client, H):
    from app.core.ids import canonical_json, sha256_hex
    bundle = {"format": "agentnexus-backup/1", "tables": {}}
    digest = sha256_hex(canonical_json(bundle))
    other = jwt.encode({"sha256": digest, "iss": "https://elsewhere.e59"}, keys.signing_key(), algorithm="RS256", headers={"kid": keys.kid()})
    res = client.post("/api/v1/admin/restore/test", json={"bundle": bundle, "signature": other}, headers=H["admin"]).json()
    assert res["ok"] is False and res["stage"] == "signature" and "iss" in res["detail"].lower()


# ── C59-16 수신 상한 — 인증 없는 푸시 수신도 상한(다 읽기 전에 413) · OTLP 구조 오류 400 ──
def test_inbound_caps(client, monkeypatch):
    monkeypatch.setattr(a2a_gateway, "MAX_REMOTE_BYTES", 100)
    assert client.post("/a2a/push/e59-unknown", content=b"{" + b" " * 500 + b"}",
                       headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/a2a/push/e59-unknown", content=b"{}", headers={"Content-Type": "application/json"}).status_code == 404
    hdr = {"Authorization": f"Bearer {tracing.ingest_token()}", "Content-Type": "application/json"}
    assert client.post("/v1/traces", content=json.dumps({"resourceSpans": "not-a-list"}), headers=hdr).status_code == 400


# ── C59-17 도구 위험도 — 빠졌던 파괴·쓰기 동사 ──
def test_missing_risk_verbs():
    for name in ("overwriteFile", "eraseDisk", "shutdownServer", "flush_db", "upsertRecord", "saveFile", "submitOrder", "clearCache"):
        assert governance.classify_risk({"name": name}, trusted=False) == "high", name
    assert [review.tool_risk(n) for n in ("overwriteFile", "saveFile", "listPosts")] == ["high", "medium", "low"]
