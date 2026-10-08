"""Epic 60 — 남은 기록 항목 (D21-⑤, 2차 검토 VERIFY·INDEX 의 '기록' 중 결함·하드닝): 신뢰 어휘 · 발견 API 페이지 ·
mTLS 필수의 verify:false · 에이전트 토큰 캐시 · OpenAPI 생성 시크릿 덮어쓰기 · CLI 파일 권한 · OTLP 상한·형식 · A2A 사용량 상태 ·
푸시 토큰 암호화 · 실행기 오류 원문."""
import asyncio
import json
import os
import types

import httpx
import pytest

from app.builtin import internal_system
from app.core.http import register_mount
from app.db import SessionLocal
from app.models import A2ATask, CatalogItem, Secret, UsageRecord
from app.services import a2a_gateway, governance, registry, secrets, tls
from test.test_a2a import AG, call, msg


# ── C60-01 신뢰 어휘 — 승인 신뢰(official·partner)면 readOnlyHint 로 위험을 낮춘다 (전에는 "trusted" 와 비교해 늘 거짓) ──
def test_trust_vocabulary(monkeypatch):
    tool = {"name": "update_record", "annotations": {"readOnlyHint": True}}
    for trust, want in (("official", "low"), ("partner", "low"), ("community", "high"), (None, "high")):
        item = types.SimpleNamespace(item_metadata={"_anx_review": {"trust": trust}} if trust else {})
        trusted = bool(item) and registry.trust_of(item) in ("official", "partner", "trusted")
        assert governance.classify_risk(tool, trusted) == want, trust
    src = open(governance.__file__, encoding="utf-8").read()
    assert 'registry.trust_of(item) in ("official", "partner", "trusted")' in src and 'trust_of(item) == "trusted"' not in src


# ── C60-02 발견 API — 거름 조건이 없으면 SQL 페이지(전체를 읽지 않음) · 페이지·커서 그대로 ──
def test_v0_servers_paged_in_sql(client, monkeypatch):
    seen = []
    real = registry.search
    monkeypatch.setattr(registry, "search", lambda db, **kw: seen.append(kw) or real(db, **kw))
    first = client.get("/v0/servers", params={"limit": 2}).json()
    assert len(first["servers"]) <= 2 and seen[-1]["limit"] == 2 and seen[-1]["offset"] == 0
    if first["metadata"]["nextCursor"]:
        nxt = client.get("/v0/servers", params={"limit": 2, "cursor": first["metadata"]["nextCursor"]}).json()
        assert {s["server"]["name"] for s in nxt["servers"]}.isdisjoint({s["server"]["name"] for s in first["servers"]})
    with SessionLocal() as db:
        page, total = real(db, type_="mcp_server", statuses=registry.DISCOVERABLE, limit=1, offset=0)
        assert len(page) <= 1 and total == db.query(CatalogItem).filter(CatalogItem.type == "mcp_server",
                                                                        CatalogItem.status.in_(registry.DISCOVERABLE)).count()


# ── C60-03 mTLS 필수 정책 — 서버 인증서 검증을 끈 업스트림은 거부 ──
def test_mtls_required_rejects_verify_false(client, monkeypatch):
    monkeypatch.setattr(tls, "mode", lambda db: "required")
    with pytest.raises(tls.MtlsError, match="verify"):
        tls.context_for({"client_cert_secret": "x", "verify": False}, "mcp:e60", "https://up.e60.example")


# ── C60-04 에이전트 인증을 바꾸면 이 워커의 캐시 토큰을 버린다 ──
def test_agent_token_cache_dropped_on_auth_change(client, H):
    a2a_gateway._TOKENS[("cc", AG)] = (9e12, "old-cc")
    a2a_gateway._TOKENS[("x", "usr_user", AG)] = (9e12, "old-x")  # 교환 토큰 키 = ("x", sub, 에이전트 id) (59th — 전에는 합성 키로 시험)
    a2a_gateway._TOKENS[("x", "usr_user", "other")] = (9e12, "keep")
    assert client.patch(f"/api/v1/a2a/agents/{AG}", json={"auth": {"scopes": ["read"]}}, headers=H["operator"]).status_code == 200
    assert ("cc", AG) not in a2a_gateway._TOKENS and ("x", "usr_user", AG) not in a2a_gateway._TOKENS
    assert a2a_gateway._TOKENS.pop(("x", "usr_user", "other"))[1] == "keep"


# ── C60-05 OpenAPI 서버 생성 — 이미 있는 namespace 면 시크릿을 쓰기 전에 거부 (다른 서버 시크릿 보존) ──
def test_openapi_create_does_not_overwrite_secret(client, H):
    with SessionLocal() as db:
        secrets.put(db, "mcp/jira/backend", "jira-original", kind="token")
        db.commit()
    register_mount("http://e60-int", async_transport=httpx.ASGITransport(app=internal_system.app))
    res = client.post("/api/v1/mcp-servers/openapi", json={"namespace": "jira", "title": "Jira 덮기", "openapi": internal_system.app.openapi(),
                                                          "base_url": "http://e60-int", "secret": "attacker-value"}, headers=H["operator"])
    assert res.status_code == 409 and res.json()["code"] == "namespace_taken", res.text
    with SessionLocal() as db:
        assert secrets.get_plain(db, "mcp/jira/backend") == "jira-original"
        db.delete(db.query(Secret).filter(Secret.name == "mcp/jira/backend").one())
        db.commit()


# ── C60-06 CLI — 토큰·시크릿 파일은 처음부터 0600 (기존 파일 권한도) ──
def test_cli_private_files(tmp_path):
    from app.cli import nexus
    f = tmp_path / "cfg.json"
    f.write_text("{}")
    os.chmod(f, 0o644)
    nexus._write_private(f, json.dumps({"access_token": "t"}))
    assert oct(f.stat().st_mode)[-3:] == "600" and json.loads(f.read_text())["access_token"] == "t"
    g = tmp_path / "new.env"
    nexus._write_private(g, "A=1\n")
    assert oct(g.stat().st_mode)[-3:] == "600"


# ── C60-07 OTLP 수신 — 상한 초과 413 · 형식 오류 400 (전에는 500) ──
def test_otlp_limits(client, monkeypatch):
    from app.api import observe
    from app.core import tracing
    hdr = {"Authorization": f"Bearer {tracing.ingest_token()}"}
    assert client.post("/v1/traces", content=b"{not json", headers={**hdr, "Content-Type": "application/json"}).status_code == 400
    assert client.post("/v1/traces", content=b"\xff\x00garbage", headers={**hdr, "Content-Type": "application/x-protobuf"}).status_code == 400
    monkeypatch.setattr(observe, "OTLP_MAX_BYTES", 10)
    assert client.post("/v1/traces", content=b"{}" * 20, headers={**hdr, "Content-Type": "application/json"}).status_code == 413


# ── C60-08 A2A 사용량 — A2AError 밖의 실패도 error 로 기록 ──
def test_a2a_usage_status_on_unexpected_error(client, H, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("grpc channel exploded")
    monkeypatch.setattr(a2a_gateway, "_rpc", boom)
    with SessionLocal() as db:
        before = db.query(UsageRecord).filter(UsageRecord.target == AG, UsageRecord.status == "error").count()
    with pytest.raises(RuntimeError):
        call(client, H["user"], "message/send", {"message": msg("x")})
    with SessionLocal() as db:
        assert db.query(UsageRecord).filter(UsageRecord.target == AG, UsageRecord.status == "error").count() == before + 1


# ── C60-09 푸시 — 클라이언트 웹훅 토큰은 암호화 저장, 전달할 때만 복호화 (이전 평문 행도 동작) ──
def test_push_client_token_encrypted(client, H):
    received = []
    register_mount("http://e60-hook.test", async_transport=httpx.MockTransport(
        lambda r: received.append(r.headers.get("x-a2a-notification-token")) or httpx.Response(200)))
    t = call(client, H["user"], "message/send", {"message": msg("분석")}).json()["result"]
    call(client, H["user"], "tasks/pushNotificationConfig/set", {"taskId": t["id"], "pushNotificationConfig": {
        "url": "http://e60-hook.test/h", "token": "client-secret-e60"}})
    with SessionLocal() as db:
        cfg = db.get(A2ATask, f"{AG}:{t['id']}").push_config
    assert "token" not in cfg and cfg["token_enc"] and "client-secret-e60" not in json.dumps(cfg)
    asyncio.run(a2a_gateway._relay_push(cfg, {"kind": "task", "id": t["id"]}, AG, t["id"]))
    asyncio.run(a2a_gateway._relay_push({"url": "http://e60-hook.test/h", "token": "legacy-plain"}, {"kind": "task"}, AG, "x"))
    assert received == ["client-secret-e60", "legacy-plain"]


# ── C60-10 플랫폼 실행기 오류 — 원격 클라이언트에는 원문 대신 참조 id ──
def test_executor_error_text_not_exposed():
    from app.services import a2a_proto as ap
    from app.services.a2a_server import A2AServer

    async def bad_exec(ctx):
        raise ValueError("SELECT * FROM secrets WHERE name='internal/path'")

    async def scenario():
        srv = A2AServer(lambda: {}, bad_exec)
        task = await srv._start({"kind": "message", "messageId": "m1", "role": "user", "parts": [{"kind": "text", "text": "x"}]}, {})
        for _ in range(50):
            if task["status"]["state"] in ap.TERMINAL:
                break
            await asyncio.sleep(0.02)
        return task
    task = asyncio.run(scenario())
    text = ap.text_of({"kind": "task", "status": task["status"]})
    assert task["status"]["state"] == "failed" and "SELECT" not in text and "internal/path" not in text and "참조" in text
