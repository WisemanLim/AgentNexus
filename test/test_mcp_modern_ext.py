"""Epic 29 — MCP 2026-07-28 확장 (D14-②): 게이트웨이 → modern 업스트림 stateless 호출(server/discover 로 발견, 서버별 auto|legacy|2026-07-28)
· x-mcp-header(`Mcp-Param-*`) 검증 -32020 · subscriptions/listen(승인·subscriptionId 태그·목록 변경 알림·정상 종료·토큰 폐기 종료)
· progress 중계(modern 요청 단위 SSE · 레거시 SSE, 증가하지 않는 값 버림) · 레거시 회귀 없음.
"""
import asyncio
import base64
import json

import httpx
import pytest
from sqlalchemy import select

from app.core.http import StreamingASGITransport
from app.db import SessionLocal
from app.models import McpSession, ToolRule
from app.services import mcp_modern as mm

V = "2026-07-28"
LEGACY_H = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


@pytest.fixture(scope="module", autouse=True)
def refreshed(client, H):
    for s in ("jira", "dynatrace", "internal-orders"):
        r = client.post(f"/api/v1/mcp-servers/{s}/refresh", headers=H["operator"]).json()
        assert r["health"] == "healthy" and r["protocol"] == V, r


def run(client, fn):
    return client.portal.call(fn)


def http(client) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=StreamingASGITransport(client.app), base_url="http://testserver", timeout=60)


def hdr(auth: dict, method: str, name: str | None = None, extra: dict | None = None) -> dict:
    return {**auth, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": V, "Mcp-Method": method,
            **({"Mcp-Name": name} if name is not None else {}), **(extra or {})}


def body(method: str, params: dict | None = None, rid=1, caps: dict | None = None, progress=None) -> dict:
    p = dict(params or {})
    p["_meta"] = {mm.K_VERSION: V, mm.K_CAPS: caps or {}, mm.K_CLIENT: {"name": "ext-test", "version": "1"},
                  **({"progressToken": progress} if progress is not None else {})}
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": p}


async def sse_messages(res):
    buf = ""
    async for chunk in res.aiter_text():
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            data = "".join(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
            if data:
                yield json.loads(data)


def upstream_sessions(server_id: str) -> int:
    with SessionLocal() as db:
        return len(db.scalars(select(McpSession).where(McpSession.kind == "upstream", McpSession.server_id == server_id)).all())


# ── C29-01 modern 업스트림 발견·stateless 왕복 · 적용 전 시험 · 강제 legacy 회귀 ──
def test_modern_upstream_detected_and_stateless(client, H):
    srv = client.get("/api/v1/mcp-servers/jira", headers=H["dev"]).json()
    assert srv["protocol"] == "auto" and srv["upstream_protocol"] == V
    before = upstream_sessions("jira")
    init = client.post("/mcp/jira", json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "legacy", "version": "1"}}}, headers={**H["dev"], **LEGACY_H})
    sid = init.headers["mcp-session-id"]
    call = client.post("/mcp/jira", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "search_issues", "arguments": {"query": "결제"}}}, headers={**H["dev"], **LEGACY_H, "Mcp-Session-Id": sid}).json()
    res = call["result"]
    assert not res.get("isError") and "resultType" not in res  # 레거시 하류에는 modern 필드를 흘리지 않는다
    assert upstream_sessions("jira") == before  # modern 업스트림 = initialize·세션 없음
    cfg = client.post("/api/v1/mcp-servers/jira/test-config", json={}, headers=H["operator"]).json()
    proto = next(c for c in cfg["checks"] if c["key"] == "protocol")
    assert proto["ok"] and "2026-07-28" in proto["detail"]
    # 강제 legacy — initialize·업스트림 세션으로 되돌아간다 (레거시 경로 회귀)
    assert client.patch("/api/v1/mcp-servers/jira", json={"protocol": "legacy"}, headers=H["operator"]).json()["protocol"] == "legacy"
    try:
        again = client.post("/mcp/jira", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "search_issues", "arguments": {"query": "결제"}}}, headers={**H["dev"], **LEGACY_H, "Mcp-Session-Id": sid}).json()
        assert not again["result"].get("isError") and upstream_sessions("jira") == before + 1
        assert client.patch("/api/v1/mcp-servers/jira", json={"protocol": "nope"}, headers=H["operator"]).status_code == 422
    finally:
        client.patch("/api/v1/mcp-servers/jira", json={"protocol": "auto"}, headers=H["operator"])


# ── C29-02 x-mcp-header — 업스트림 미러링 · 하류 Mcp-Param 검증(누락·불일치·여분·Base64) · 중복 헤더 ──
def test_x_mcp_header_mirroring_and_validation(client, H):
    tools = client.post("/mcp/dynatrace", json=body("tools/list"), headers=hdr(H["dev"], "tools/list")).json()["result"]["tools"]
    qm = next(t for t in tools if t["name"] == "query_metrics")
    assert qm["inputSchema"]["properties"]["service"]["x-mcp-header"] == "Service"
    args = {"name": "query_metrics", "arguments": {"service": "payment-api"}}
    missing = client.post("/mcp/dynatrace", json=body("tools/call", args), headers=hdr(H["dev"], "tools/call", "query_metrics"))
    assert missing.status_code == 400 and missing.json()["error"]["code"] == -32020 and "Mcp-Param-Service" in missing.json()["error"]["message"]
    wrong = client.post("/mcp/dynatrace", json=body("tools/call", args),
                        headers=hdr(H["dev"], "tools/call", "query_metrics", {"Mcp-Param-Service": "other-api"}))
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == -32020
    ok = client.post("/mcp/dynatrace", json=body("tools/call", args),
                     headers=hdr(H["dev"], "tools/call", "query_metrics", {"Mcp-Param-Service": "payment-api"}))
    assert ok.status_code == 200 and ok.json()["result"]["structuredContent"]["service"] == "payment-api"  # 업스트림에도 미러링돼 통과
    extra = client.post("/mcp/dynatrace", json=body("tools/call", {"name": "query_metrics", "arguments": {}}),
                        headers=hdr(H["dev"], "tools/call", "query_metrics", {"Mcp-Param-Service": "payment-api"}))
    assert extra.status_code == 400 and extra.json()["error"]["code"] == -32020  # 인자 없는 헤더
    enc = "=?base64?" + base64.b64encode("결제 서비스".encode()).decode() + "?="
    b64 = client.post("/mcp/dynatrace", json=body("tools/call", {"name": "query_metrics", "arguments": {"service": "결제 서비스"}}),
                      headers=hdr(H["dev"], "tools/call", "query_metrics", {"Mcp-Param-Service": enc}))
    assert b64.status_code == 200 and b64.json()["result"]["structuredContent"]["service"] == "결제 서비스"
    dup = httpx.Headers(list(hdr(H["dev"], "tools/call", "query_metrics", {"Mcp-Param-Service": "payment-api"}).items())
                        + [("Mcp-Method", "tools/call")])
    assert client.post("/mcp/dynatrace", json=body("tools/call", args), headers=dup).json()["error"]["code"] == -32020
    # 레거시 하류는 헤더 없이도 같은 도구를 쓴다 (게이트웨이가 업스트림에 Mcp-Param-Service 를 대신 단다)
    init = client.post("/mcp/dynatrace", json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "legacy", "version": "1"}}}, headers={**H["dev"], **LEGACY_H})
    leg = client.post("/mcp/dynatrace", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": args},
                      headers={**H["dev"], **LEGACY_H, "Mcp-Session-Id": init.headers["mcp-session-id"]}).json()
    assert leg["result"]["structuredContent"]["service"] == "payment-api"


# ── C29-03 subscriptions/listen — 승인(부분집합)·subscriptionId · 도구 규칙 변경 → tools/list_changed · 정상 종료 ──
def test_listen_ack_and_tools_list_changed(client, H, monkeypatch):
    monkeypatch.setattr(mm, "LISTEN_POLL_S", 0.2)
    req = body("subscriptions/listen", {"notifications": {"toolsListChanged": True, "promptsListChanged": False,
                                                           "resourceSubscriptions": ["confluence://page/1"]}}, rid="sub-1")

    def toggle(allowed: bool):
        with SessionLocal() as db:
            db.scalars(select(ToolRule).where(ToolRule.server_id == "jira", ToolRule.tool == "get_issue")).first().allowed = allowed
            db.commit()

    async def scenario():
        seen = []
        async with http(client) as ac:
            async with ac.stream("POST", "/mcp/jira", json=req, headers=hdr(H["dev"], "subscriptions/listen")) as res:
                assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
                async for msg in sse_messages(res):
                    seen.append(msg)
                    if msg.get("method") == "notifications/subscriptions/acknowledged":
                        await asyncio.sleep(0.5)
                        await asyncio.to_thread(toggle, False)
                    elif msg.get("method") == "notifications/tools/list_changed":
                        break
            no_sse = await ac.post("/mcp/jira", json=req, headers={**hdr(H["dev"], "subscriptions/listen"), "Accept": "application/json"})
            bad = await ac.post("/mcp/jira", json=body("subscriptions/listen", {}), headers=hdr(H["dev"], "subscriptions/listen"))
            return seen, no_sse.status_code, bad.json()
    try:
        seen, no_sse, bad = run(client, scenario)
    finally:
        toggle(True)
    ack, changed = seen[0], seen[-1]
    assert ack["params"]["_meta"][mm.K_SUB] == "sub-1" and ack["params"]["notifications"] == {"toolsListChanged": True}
    assert changed["method"] == "notifications/tools/list_changed" and changed["params"]["_meta"][mm.K_SUB] == "sub-1"
    assert no_sse == 406 and bad["error"]["code"] == -32602
    disc = client.post("/mcp/jira", json=body("server/discover"), headers=hdr(H["dev"], "server/discover")).json()["result"]
    assert disc["capabilities"]["tools"]["listChanged"] is True and disc["capabilities"]["resources"]["subscribe"] is True  # D15-① 리소스 구독


def test_listen_graceful_close_cap_and_revoked_token(client, H, monkeypatch):
    monkeypatch.setattr(mm, "LISTEN_POLL_S", 0.2)
    monkeypatch.setattr(mm, "LISTEN_MAX_S", 0.5)
    tok = client.post("/api/v1/access-tokens", json={"name": "listen-e29", "subject": "svc:listen-e29", "scopes": ["gateway:invoke"],
                                                     "ttl_seconds": 600}, headers=H["operator"]).json()
    at = {"Authorization": f"Bearer {tok['token']}"}
    flt = {"notifications": {"toolsListChanged": True}}

    async def scenario():
        async with http(client) as ac:
            async with ac.stream("POST", "/mcp/jira", json=body("subscriptions/listen", flt, rid=41), headers=hdr(H["dev"], "subscriptions/listen")) as res:
                graceful = [m async for m in sse_messages(res)]
            monkeypatch.setattr(mm, "LISTEN_MAX_S", 60)
            revoked = []
            async with ac.stream("POST", "/mcp/jira", json=body("subscriptions/listen", flt, rid=42), headers=hdr(at, "subscriptions/listen")) as res:
                async for msg in sse_messages(res):
                    revoked.append(msg)
                    if msg.get("method") == "notifications/subscriptions/acknowledged":
                        await ac.post(f"/api/v1/access-tokens/{tok['id']}/revoke", headers=H["operator"])
            monkeypatch.setattr(mm, "MAX_LISTENS", 0)
            capped = await ac.post("/mcp/jira", json=body("subscriptions/listen", flt, rid=43), headers=hdr(H["dev"], "subscriptions/listen"))
            return graceful, revoked, capped
    graceful, revoked, capped = run(client, scenario)
    assert graceful[0]["method"] == "notifications/subscriptions/acknowledged"
    assert graceful[-1] == {"jsonrpc": "2.0", "id": 41, "result": {"resultType": "complete", "_meta": {mm.K_SUB: 41}}}
    assert revoked[-1]["id"] == 42 and "폐기" in revoked[-1]["error"]["message"]
    assert capped.status_code == 429 and mm.LISTENS.get("usr_dev", 0) == 0


# ── C29-04 progress — modern 요청 단위 SSE (MRTR 두 번 왕복 모두 각 요청 토큰으로) ──
def test_modern_progress_relay_with_mrtr(client, H):
    caps = {"sampling": {}}
    args = {"name": "classify_orders", "arguments": {}}

    async def scenario():
        async with http(client) as ac:
            first = []
            async with ac.stream("POST", "/mcp/internal-orders", json=body("tools/call", args, rid=50, caps=caps, progress="p1"),
                                 headers=hdr(H["dev"], "tools/call", "classify_orders")) as res:
                ctype = res.headers["content-type"]
                first = [m async for m in sse_messages(res)]
            result = first[-1]["result"]
            key = next(iter(result["inputRequests"]))
            retry = {**args, "requestState": result["requestState"], "inputResponses": {key: {
                "role": "assistant", "content": {"type": "text", "text": "진행률 분류"}, "model": "client-model", "stopReason": "endTurn"}}}
            async with ac.stream("POST", "/mcp/internal-orders", json=body("tools/call", retry, rid=51, caps=caps, progress="p2"),
                                 headers=hdr(H["dev"], "tools/call", "classify_orders")) as res:
                second = [m async for m in sse_messages(res)]
            return ctype, first, second
    ctype, first, second = run(client, scenario)
    assert ctype.startswith("text/event-stream")
    p1 = [m["params"] for m in first if m.get("method") == "notifications/progress"]
    assert p1 and all(p["progressToken"] == "p1" for p in p1) and p1[0]["progress"] == 1 and p1[0]["total"] == 3
    assert first[-1]["id"] == 50 and first[-1]["result"]["resultType"] == "input_required"
    p2 = [m["params"] for m in second if m.get("method") == "notifications/progress"]
    values = [p["progress"] for p in p2]
    assert all(p["progressToken"] == "p2" for p in p2) and values == sorted(set(values)) and values[-1] == 3
    assert p2[-1]["message"] == "완료"
    assert second[-1]["id"] == 51 and second[-1]["result"]["structuredContent"]["classification"] == "진행률 분류"


# ── C29-05 progress — 레거시 SSE 중계(서버 요청 중계와 같은 스트림) · 증가하지 않는 값 버림 · modern 업스트림 MRTR 다리 ──
def test_legacy_progress_relay_through_modern_upstream(client, H):
    async def scenario():
        async with http(client) as ac:
            init = await ac.post("/mcp/internal-orders", headers={**H["dev"], **LEGACY_H}, json={
                "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {"sampling": {}}, "clientInfo": {"name": "legacy", "version": "1"}}})
            h = {**H["dev"], **LEGACY_H, "Mcp-Session-Id": init.headers["mcp-session-id"]}
            before = upstream_sessions("internal-orders")
            seen = []
            async with ac.stream("POST", "/mcp/internal-orders", headers=h, json={
                    "jsonrpc": "2.0", "id": 60, "method": "tools/call",
                    "params": {"name": "classify_orders", "arguments": {}, "_meta": {"progressToken": "lp"}}}) as res:
                async for msg in sse_messages(res):
                    seen.append(msg)
                    if msg.get("method") == "sampling/createMessage":
                        await ac.post("/mcp/internal-orders", headers=h, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
                            "role": "assistant", "content": {"type": "text", "text": "레거시 분류"}, "model": "legacy-model",
                            "stopReason": "endTurn"}})
            return seen, before, upstream_sessions("internal-orders")
    seen, before, after = run(client, scenario)
    kinds = [m.get("method") or "result" for m in seen]
    assert kinds.index("notifications/progress") < kinds.index("sampling/createMessage") < kinds.index("result")
    values = [m["params"]["progress"] for m in seen if m.get("method") == "notifications/progress"]
    assert values == [1, 2, 3]  # 업스트림이 재시도 때 다시 보낸 1 은 버린다
    assert seen[-1]["result"]["structuredContent"]["classification"] == "레거시 분류"
    assert after == before  # 업스트림은 modern(세션 없음) — MRTR 로 sampling 을 받아 하류 SSE 로 중계
