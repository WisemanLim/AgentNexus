"""Epic 14 — MCP 서버→클라이언트 요청 (FR-A00·A03·G09, SCR-C08): Host 동의(sampling·elicitation)·하류 중계·`/` 메뉴.

데모 서버 internal-orders 는 stateful·SSE 로 도구 실행 도중 게이트웨이에 sampling/createMessage·elicitation/create 를 보낸다.
흐름이 한 요청 안에서 오가므로 시나리오는 앱 이벤트 루프(TestClient.portal)에서 비동기로 돌린다.
"""
import asyncio
import json
import time

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.http import StreamingASGITransport
from app.db import SessionLocal
from app.models import Approval, AuditEvent, McpRelay, UsageRecord
from app.services import mcp_consent

MCP_H = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


@pytest.fixture(scope="module", autouse=True)
def refreshed(client, H):
    res = client.post("/api/v1/mcp-servers/internal-orders/refresh", headers=H["operator"]).json()
    assert res["health"] == "healthy", res


def run(client, fn):
    return client.portal.call(fn)


def http(client) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=StreamingASGITransport(client.app), base_url="http://testserver", timeout=60)


def call_body(tool: str, args: dict, rid: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": tool, "arguments": args}}


async def pending(ac: httpx.AsyncClient, hdr: dict, kind: str, since: float, timeout: float = 20) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        items = (await ac.get("/api/v1/approvals", params={"status": "pending"}, headers=hdr)).json()["items"]
        found = [a for a in items if a["kind"] == kind and a["server_id"] == "internal-orders"
                 and a["created_at"] >= since]
        if found:
            return found[0]
        await asyncio.sleep(0.1)
    raise AssertionError(f"{kind} 동의 요청이 생기지 않음")


def _iso_now() -> str:
    from app.db import utcnow
    return utcnow().isoformat()


def test_sampling_consent_edit_then_allow_routes_to_local_model(client, H):
    async def scenario():
        async with http(client) as ac:
            since = _iso_now()
            call = asyncio.create_task(ac.post("/mcp/internal-orders", json=call_body("classify_orders", {}),
                                               headers={**H["user"], **MCP_H}))
            ap = await pending(ac, H["user"], "sampling", since)
            detail = (await ac.get(f"/api/v1/approvals/{ap['id']}", headers=H["user"])).json()
            # 다른 사용자는 남의 동의 요청을 볼 수 없다
            other = await ac.get(f"/api/v1/approvals/{ap['id']}", headers=H["user2"])
            ok = await ac.post(f"/api/v1/approvals/{ap['id']}/consent", headers=H["user"], json={
                "decision": "allow", "messages": [{"role": "user", "text": "O-2403 만 분류: 동일 카드 7건"}]})
            res = (await call).json()
            return ap, detail, other.status_code, ok.status_code, res
    ap, detail, other, ok, res = run(client, scenario)
    assert detail["args"]["maxTokens"] == 256 and "systemPrompt" in detail["args"]
    assert detail["result"]["routing"]["model"] == "domain-local"  # 내부 데이터 → 로컬 모델 (게이트웨이가 결정, MSG-011)
    assert detail["result"]["routing"]["hints"] == ["small-fast"] and detail["reason"].startswith("MSG-016")
    assert other == 404 and ok == 200
    out = res["result"]["structuredContent"]
    assert res["result"]["isError"] is False and "O-2403 만 분류" in out["classification"]  # 편집한 프롬프트로 실행
    assert out["model"] == "domain-local"
    with SessionLocal() as db:
        row = db.get(Approval, ap["id"])
        assert row.status == "completed" and row.result["final"]["model"] == "domain-local"
        use = db.scalars(select(UsageRecord).where(UsageRecord.agent == "mcp:internal-orders/sampling")
                         .order_by(UsageRecord.ts.desc())).first()
        assert use.kind == "model" and use.user_sub == "usr_user"  # 사용자 비용·한도로 집계
        ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "mcp.sampling")
                        .order_by(AuditEvent.seq.desc())).first()
        assert ev.data["approval_id"] == ap["id"] and ev.data["edited"] is True


def test_sampling_deny_and_timeout_return_tool_error(client, H, monkeypatch):
    async def deny():
        async with http(client) as ac:
            since = _iso_now()
            call = asyncio.create_task(ac.post("/mcp/internal-orders", json=call_body("classify_orders", {}),
                                               headers={**H["user"], **MCP_H}))
            ap = await pending(ac, H["user"], "sampling", since)
            await ac.post(f"/api/v1/approvals/{ap['id']}/reject", headers=H["user"], json={"note": "불필요"})
            return (await call).json()
    res = run(client, deny)
    assert res["result"]["isError"] is True and "거부" in res["result"]["content"][0]["text"]

    monkeypatch.setattr(settings, "mcp_consent_timeout_s", 1.0)

    async def timeout():
        async with http(client) as ac:
            since = _iso_now()
            res = (await ac.post("/mcp/internal-orders", json=call_body("classify_orders", {}),
                                 headers={**H["user"], **MCP_H})).json()
            items = (await ac.get("/api/v1/approvals", headers=H["user"])).json()["items"]
            return res, [a for a in items if a["kind"] == "sampling" and a["created_at"] >= since]
    res, aps = run(client, timeout)
    assert res["result"]["isError"] is True and "대기 시간" in res["result"]["content"][0]["text"]
    assert aps and aps[0]["status"] == "expired"  # 60초(여기선 1초) 무응답 자동 거부


def test_sampling_review_before_send(client, H):
    async def scenario():
        async with http(client) as ac:
            since = _iso_now()
            call = asyncio.create_task(ac.post("/mcp/internal-orders", json=call_body("classify_orders", {}),
                                               headers={**H["user"], **MCP_H}))
            ap = await pending(ac, H["user"], "sampling", since)
            await ac.post(f"/api/v1/approvals/{ap['id']}/consent", headers=H["user"],
                          json={"decision": "allow", "review": True})
            for _ in range(100):
                d = (await ac.get(f"/api/v1/approvals/{ap['id']}", headers=H["user"])).json()
                if d["status"] == "review":
                    break
                await asyncio.sleep(0.1)
            sent = await ac.post(f"/api/v1/approvals/{ap['id']}/consent", headers=H["user"],
                                 json={"decision": "send", "text": "O-2403: 이상 — 검토자가 수정"})
            return d, sent.status_code, (await call).json()
    d, sent, res = run(client, scenario)
    assert d["status"] == "review" and d["result"]["draft"]["text"].startswith("[domain-7b]")
    assert sent == 200 and res["result"]["structuredContent"]["classification"] == "O-2403: 이상 — 검토자가 수정"


def test_elicitation_accept_validates_schema_and_decline(client, H):
    async def scenario():
        async with http(client) as ac:
            since = _iso_now()
            call = asyncio.create_task(ac.post("/mcp/internal-orders", json=call_body("request_return",
                                                                                     {"order_id": "O-2404"}),
                                               headers={**H["user"], **MCP_H}))
            ap = await pending(ac, H["user"], "elicitation", since)
            generic = await ac.post(f"/api/v1/approvals/{ap['id']}/approve", headers=H["user"], json={})
            bad = await ac.post(f"/api/v1/approvals/{ap['id']}/consent", headers=H["user"],
                                json={"decision": "accept", "content": {"reason": "파손", "quantity": "두 개"}})
            ok = await ac.post(f"/api/v1/approvals/{ap['id']}/consent", headers=H["user"],
                               json={"decision": "accept", "content": {"reason": "파손", "quantity": 2}})
            accepted = (await call).json()
            since = _iso_now()
            call = asyncio.create_task(ac.post("/mcp/internal-orders", json=call_body("request_return",
                                                                                     {"order_id": "O-2401"}),
                                               headers={**H["user"], **MCP_H}))
            ap2 = await pending(ac, H["user"], "elicitation", since)
            await ac.post(f"/api/v1/approvals/{ap2['id']}/consent", headers=H["user"], json={"decision": "decline"})
            declined = (await call).json()
            return ap, generic, bad, ok.status_code, accepted, declined
    ap, generic, bad, ok, accepted, declined = run(client, scenario)
    schema = ap["args"]["requestedSchema"]
    assert set(schema["properties"]) == {"reason", "quantity"} and schema["properties"]["reason"]["enum"]
    assert generic.status_code == 400 and generic.json()["code"] == "consent_required"
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_elicitation"
    assert ok == 200
    assert accepted["result"]["structuredContent"] == {"order_id": "O-2404", "status": "requested", "reason": "파손",
                                                       "quantity": 2, "by": "사용자"}
    assert declined["result"]["structuredContent"]["status"] == "declined"


def test_downstream_client_with_sampling_capability_gets_relayed_request(client, H):
    async def scenario():
        async with http(client) as ac:
            init = await ac.post("/mcp/internal-orders", headers={**H["dev"], **MCP_H}, json={
                "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {"sampling": {}},
                    "clientInfo": {"name": "external-client", "version": "1"}}})
            sid = init.headers["mcp-session-id"]
            hdr = {**H["dev"], **MCP_H, "Mcp-Session-Id": sid}
            seen, final, answered = [], None, None
            async with ac.stream("POST", "/mcp/internal-orders", json=call_body("classify_orders", {}, rid=7),
                                 headers=hdr) as res:
                assert res.headers["content-type"].startswith("text/event-stream")
                buf = ""
                async for chunk in res.aiter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        block, buf = buf.split("\n\n", 1)
                        msg = json.loads(block.split("data: ", 1)[1])
                        seen.append(msg)
                        if msg.get("method") == "sampling/createMessage":
                            answered = await ac.post("/mcp/internal-orders", headers=hdr, json={
                                "jsonrpc": "2.0", "id": msg["id"], "result": {
                                    "role": "assistant", "content": {"type": "text", "text": "하류 클라이언트가 분류함"},
                                    "model": "client-local-model", "stopReason": "endTurn"}})
                        elif msg.get("id") == 7:
                            final = msg
            stray = await ac.post("/mcp/internal-orders", headers=hdr, json={"jsonrpc": "2.0", "id": "nope", "result": {}})
            return seen, final, answered.status_code, stray.status_code
    seen, final, answered, stray = run(client, scenario)
    assert seen[0]["method"] == "sampling/createMessage" and seen[0]["params"]["maxTokens"] == 256
    assert answered == 202 and stray == 404
    out = final["result"]["structuredContent"]
    assert out == {"date": "2026-09-30", "count": 4, "classification": "하류 클라이언트가 분류함",
                   "model": "client-local-model"}
    with SessionLocal() as db:
        relay = db.get(McpRelay, seen[0]["id"])
        assert relay.response["result"]["model"] == "client-local-model" and relay.method == "sampling/createMessage"
        # 중계 경로는 Host 동의를 만들지 않는다
        assert not db.scalars(select(Approval).where(Approval.requester_sub == "usr_dev",
                                                     Approval.kind == "sampling")).first()


def test_orchestrated_run_pauses_for_consent_cards(client, H):
    sid = client.post("/api/v1/sessions", json={"profile_id": "demo-orders"}, headers=H["user"]).json()["id"]
    rid = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "오늘 주문 이상 건 분류"},
                      headers=H["user"]).json()["run_id"]

    def wait_event(kind: str, timeout: float = 30) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            evs = client.get(f"/api/v1/runs/{rid}/events", headers=H["user"]).json()["items"]
            hit = [e for e in evs if e["type"] == kind]
            if hit:
                return hit[0]["data"]
            time.sleep(0.1)
        raise AssertionError(f"{kind} 이벤트 없음: {[e['type'] for e in evs]}")
    s_ev = wait_event("sampling.required")
    assert s_ev["server"] == "internal-orders" and s_ev["routing"]["model"] == "domain-local" and s_ev["step_id"]
    client.post(f"/api/v1/approvals/{s_ev['approval_id']}/consent", json={"decision": "allow"}, headers=H["user"])
    e_ev = wait_event("elicitation.required")
    assert e_ev["params"]["message"].startswith("O-2404")
    client.post(f"/api/v1/approvals/{e_ev['approval_id']}/consent", headers=H["user"],
                json={"decision": "accept", "content": {"reason": "오배송", "quantity": 1}})
    for _ in range(200):
        run_ = client.get(f"/api/v1/runs/{rid}", headers=H["user"]).json()
        if run_["status"] in ("succeeded", "partial", "failed"):
            break
        time.sleep(0.1)
    assert run_["status"] == "succeeded", run_
    types = [e["type"] for e in client.get(f"/api/v1/runs/{rid}/events", headers=H["user"]).json()["items"]]
    assert types.index("sampling.required") < types.index("sampling.resolved") < types.index("elicitation.required")
    steps = {s["name"]: s for s in run_["steps"]}
    assert "오배송" in json.dumps(steps["s2"]["output"], ensure_ascii=False)


def test_slash_menu_resources_and_prompts(client, H):
    ctx = client.get("/api/v1/chat/mcp/context", params={"profile_id": "demo-orders"}, headers=H["user"]).json()
    assert ctx["servers"] == ["internal-orders"]
    assert {r["uri"] for r in ctx["resources"]} >= {"orders://daily/2026-09-30", "orders://policy/returns"}
    assert [p["name"] for p in ctx["prompts"]] == ["anomaly_triage"] and ctx["prompts"][0]["arguments"][0]["name"] == "date"
    res = client.post("/api/v1/chat/mcp/resource", headers=H["user"], json={
        "profile_id": "demo-orders", "server": "internal-orders", "uri": "orders://daily/2026-09-30"}).json()
    assert "O-2403" in res["text"] and res["mime_type"] == "text/plain"
    pr = client.post("/api/v1/chat/mcp/prompt", headers=H["user"], json={
        "profile_id": "demo-orders", "server": "internal-orders", "name": "anomaly_triage",
        "arguments": {"date": "2026-10-01"}}).json()
    assert pr["text"].startswith("2026-10-01 주문 중 이상 건")
    wrong = client.post("/api/v1/chat/mcp/resource", headers=H["user"], json={
        "profile_id": "demo-orders", "server": "jira", "uri": "x://y"})
    assert wrong.status_code == 400 and wrong.json()["code"] == "server_not_in_profile"
    qa = client.get("/api/v1/chat/mcp/context", params={"profile_id": "tpl-qa"}, headers=H["user"]).json()
    assert qa["servers"] == ["confluence", "internal-api"]  # 프로파일이 쓰는 서버만


def test_server_requests_without_consent_channel():
    async def go():
        out = [await mcp_consent.handle("x", None, {"id": 1, "method": "ping"}),
               await mcp_consent.handle("x", None, {"id": 2, "method": "roots/list"}),
               await mcp_consent.handle("x", None, {"id": 3, "method": "sampling/createMessage", "params": {}}),
               await mcp_consent.handle("x", None, {"id": 4, "method": "nope/x"})]
        return out
    ping, roots, sampling, unknown = asyncio.run(go())
    assert ping["result"] == {} and roots["result"] == {"roots": []}
    assert sampling["error"]["code"] == -1 and unknown["error"]["code"] == -32601
