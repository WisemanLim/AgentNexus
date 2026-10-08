"""Epic 32 — MCP 2026-07-28 리소스 구독·Tasks 확장 (D15-①): listen `resourceSubscriptions` → notifications/resources/updated ·
`io.modelcontextprotocol/tasks`(SEP-2663) — 빨리 끝나면 일반 결과, 오래 걸리면 CreateTaskResult · tasks/get·update·cancel ·
input_required(inputRequests ↔ inputResponses) · Host 동의 경로 · listen `taskIds` → notifications/tasks · 남의 작업·만료·워커 중단.
"""
import asyncio
import time
from datetime import timedelta

import httpx
import pytest

from app.builtin import mcp_servers
from app.core.http import StreamingASGITransport
from app.db import SessionLocal, utcnow
from app.models import McpTask
from app.services import mcp_modern as mm
from app.services import mcp_tasks

V = "2026-07-28"
TASKS = {"extensions": {mcp_tasks.EXT: {}}}


@pytest.fixture(scope="module", autouse=True)
def refreshed(client, H):
    for s in ("jira", "dynatrace", "internal-orders"):
        assert client.post(f"/api/v1/mcp-servers/{s}/refresh", headers=H["operator"]).json()["protocol"] == V


def run(client, fn):
    return client.portal.call(fn)


def http(client) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=StreamingASGITransport(client.app), base_url="http://testserver", timeout=60)


def hdr(auth: dict, method: str, name: str | None = None) -> dict:
    return {**auth, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": V, "Mcp-Method": method,
            **({"Mcp-Name": name} if name is not None else {})}


def body(method: str, params: dict | None = None, rid=1, caps: dict | None = None) -> dict:
    p = dict(params or {})
    p["_meta"] = {mm.K_VERSION: V, mm.K_CAPS: caps or {}, mm.K_CLIENT: {"name": "tasks-test", "version": "1"}}
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": p}


async def sse_messages(res):
    buf = ""
    async for chunk in res.aiter_text():
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            data = "".join(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
            if data:
                import json
                yield json.loads(data)


async def task_call(ac, auth, server, method, tid, extra=None, caps=None, rid=9):
    return (await ac.post(f"/mcp/{server}", json=body(method, {"taskId": tid, **(extra or {})}, rid=rid, caps=caps or TASKS),
                          headers=hdr(auth, method, tid))).json()


async def until_status(ac, auth, server, tid, want: str, timeout: float = 20) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = (await task_call(ac, auth, server, "tasks/get", tid))["result"]
        if r["status"] == want:
            return r
        await asyncio.sleep(0.2)
    raise AssertionError(f"작업 {tid} 이 {want} 가 되지 않음 (마지막 {r['status']})")


# ── C32-01 리소스 구독 — 읽을 수 있는 URI 만 승인 · 내용이 바뀌면 resources/updated(uri·subscriptionId) · discover 능력 ──
def test_resource_subscription_updates(client, H, monkeypatch):
    monkeypatch.setattr(mm, "LISTEN_POLL_S", 0.2)
    monkeypatch.setattr(mcp_servers, "LIVE_INTERVAL_S", 0.4)
    live = "dynatrace://live/payment-api"
    req = body("subscriptions/listen", {"notifications": {"resourceSubscriptions": [live, "dynatrace://nope/x"]}}, rid="rs-1")

    async def scenario():
        seen = []
        async with http(client) as ac:
            async with ac.stream("POST", "/mcp/dynatrace", json=req, headers=hdr(H["dev"], "subscriptions/listen")) as res:
                async for msg in sse_messages(res):
                    seen.append(msg)
                    if msg.get("method") == "notifications/resources/updated":
                        break
            bad = await ac.post("/mcp/dynatrace", json=body("subscriptions/listen", {"notifications": {"resourceSubscriptions": "x"}}),
                                headers=hdr(H["dev"], "subscriptions/listen"))
            return seen, bad.json()
    seen, bad = run(client, scenario)
    assert seen[0]["params"]["notifications"] == {"resourceSubscriptions": [live]}  # 없는 리소스는 승인하지 않는다
    upd = seen[-1]
    assert upd["params"]["uri"] == live and upd["params"]["_meta"][mm.K_SUB] == "rs-1"
    assert bad["error"]["code"] == -32602
    caps = client.post("/mcp/dynatrace", json=body("server/discover"), headers=hdr(H["dev"], "server/discover")).json()["result"]["capabilities"]
    assert caps["resources"]["subscribe"] is True and mcp_tasks.EXT in caps["extensions"]


# ── C32-02 서버가 정한다 — 빨리 끝나는 호출은 확장을 선언해도 일반 결과 · 작업 행을 남기지 않는다 ──
def test_fast_call_returns_inline(client, H):
    with SessionLocal() as db:
        before = db.query(McpTask).count()
    r = client.post("/mcp/jira", json=body("tools/call", {"name": "search_issues", "arguments": {"query": "결제"}}, caps=TASKS),
                    headers=hdr(H["dev"], "tools/call", "search_issues")).json()["result"]
    assert r["resultType"] == "complete" and not r.get("isError") and "taskId" not in r
    with SessionLocal() as db:
        assert db.query(McpTask).count() == before


# ── C32-03 입력이 필요한 호출 → 작업 · input_required(inputRequests) → tasks/update → completed(result) · 권한·헤더·능력 ──
def test_task_input_required_update_complete(client, H):
    caps = {"elicitation": {}, **TASKS}
    args = {"name": "request_return", "arguments": {"order_id": "O-2404"}}

    async def scenario():
        async with http(client) as ac:
            created = (await ac.post("/mcp/internal-orders", json=body("tools/call", args, rid=1, caps=caps),
                                     headers=hdr(H["user"], "tools/call", "request_return"))).json()["result"]
            tid = created["taskId"]
            got = await until_status(ac, H["user"], "internal-orders", tid, "input_required")
            other = await task_call(ac, H["user2"], "internal-orders", "tasks/get", tid)
            nocap = await task_call(ac, H["user"], "internal-orders", "tasks/get", tid, caps={"elicitation": {}})
            mism = (await ac.post("/mcp/internal-orders", json=body("tasks/get", {"taskId": tid}, caps=TASKS),
                                  headers=hdr(H["user"], "tasks/get", "task_other"))).json()
            key = next(iter(got["inputRequests"]))
            ack = await task_call(ac, H["user"], "internal-orders", "tasks/update", tid,
                                  {"inputResponses": {key: {"action": "accept", "content": {"reason": "파손", "quantity": 2}}, "stray": {}}})
            done = await until_status(ac, H["user"], "internal-orders", tid, "completed")
            return created, got, other, nocap, mism, ack, done
    created, got, other, nocap, mism, ack, done = run(client, scenario)
    assert created["resultType"] == "task" and created["taskId"].startswith("task_") and len(created["taskId"]) > 30
    assert created["ttlMs"] == mcp_tasks.TTL_MS and created["pollIntervalMs"] == mcp_tasks.POLL_MS and created["createdAt"]
    req = next(iter(got["inputRequests"].values()))
    assert req["method"] == "elicitation/create" and set(req["params"]["requestedSchema"]["properties"]) == {"reason", "quantity"}
    assert other["error"]["code"] == -32602 and "not found" in other["error"]["message"]  # 남의 작업은 존재도 알리지 않는다
    assert nocap["error"]["code"] == mcp_tasks.MISSING_CAPABILITY
    assert mism["error"]["code"] == -32020  # Mcp-Name = taskId
    assert ack["result"]["resultType"] == "complete"
    res = done["result"]
    assert res["resultType"] == "complete" and res["structuredContent"]["quantity"] == 2 and done["resultType"] == "complete"


# ── C32-04 취소 — 협조적 취소 → cancelled · 끝난 작업 재취소는 무시 ──
def test_task_cancel(client, H):
    caps = {"elicitation": {}, **TASKS}

    async def scenario():
        async with http(client) as ac:
            created = (await ac.post("/mcp/internal-orders", json=body("tools/call", {"name": "request_return", "arguments": {"order_id": "O-2401"}},
                                                                       caps=caps), headers=hdr(H["user"], "tools/call", "request_return"))).json()["result"]
            tid = created["taskId"]
            ack = await task_call(ac, H["user"], "internal-orders", "tasks/cancel", tid)
            done = await until_status(ac, H["user"], "internal-orders", tid, "cancelled")
            again = await task_call(ac, H["user"], "internal-orders", "tasks/cancel", tid)
            return ack, done, again
    ack, done, again = run(client, scenario)
    assert ack["result"]["resultType"] == "complete" and done["statusMessage"]
    assert again["result"]["resultType"] == "complete"


# ── C32-05 Host 동의 경로 — 클라이언트가 elicitation 능력이 없으면 채팅 동의 카드로 묻고 작업은 working 유지 → 응답 후 완료 ──
def test_task_with_host_consent(client, H):
    async def scenario():
        async with http(client) as ac:
            since = utcnow().isoformat()
            created = (await ac.post("/mcp/internal-orders", json=body("tools/call", {"name": "request_return", "arguments": {"order_id": "O-2404"}},
                                                                       caps=TASKS), headers=hdr(H["user"], "tools/call", "request_return"))).json()["result"]
            tid = created["taskId"]
            ap = None
            for _ in range(100):
                items = (await ac.get("/api/v1/approvals", params={"status": "pending"}, headers=H["user"])).json()["items"]
                ap = next((a for a in items if a["kind"] == "elicitation" and a["created_at"] >= since), None)
                if ap:
                    break
                await asyncio.sleep(0.1)
            mid = (await task_call(ac, H["user"], "internal-orders", "tasks/get", tid))["result"]
            await ac.post(f"/api/v1/approvals/{ap['id']}/consent", headers=H["user"],
                          json={"decision": "accept", "content": {"reason": "오배송", "quantity": 1}})
            done = await until_status(ac, H["user"], "internal-orders", tid, "completed")
            return created, mid, done
    created, mid, done = run(client, scenario)
    assert created["resultType"] == "task" and mid["status"] == "working"
    assert done["result"]["structuredContent"]["reason"] == "오배송"


# ── C32-06 listen taskIds — 내 작업만 승인 · 상태가 바뀌면 notifications/tasks(DetailedTask·subscriptionId) ──
def test_listen_task_status(client, H, monkeypatch):
    monkeypatch.setattr(mm, "LISTEN_POLL_S", 0.2)
    caps = {"elicitation": {}, **TASKS}

    async def scenario():
        seen = []
        async with http(client) as ac:
            created = (await ac.post("/mcp/internal-orders", json=body("tools/call", {"name": "request_return", "arguments": {"order_id": "O-2402"}},
                                                                       caps=caps), headers=hdr(H["user"], "tools/call", "request_return"))).json()["result"]
            tid = created["taskId"]
            got = await until_status(ac, H["user"], "internal-orders", tid, "input_required")
            key = next(iter(got["inputRequests"]))
            req = body("subscriptions/listen", {"notifications": {"taskIds": [tid, "task_someone_else"]}}, rid="ts-1", caps=caps)
            async with ac.stream("POST", "/mcp/internal-orders", json=req, headers=hdr(H["user"], "subscriptions/listen")) as res:
                async for msg in sse_messages(res):
                    seen.append(msg)
                    if msg.get("method") == "notifications/subscriptions/acknowledged":
                        await task_call(ac, H["user"], "internal-orders", "tasks/update", tid,
                                        {"inputResponses": {key: {"action": "decline"}}})
                    elif msg.get("method") == "notifications/tasks" and msg["params"]["status"] in mcp_tasks.TERMINAL:
                        break
            return tid, seen
    tid, seen = run(client, scenario)
    assert seen[0]["params"]["notifications"] == {"taskIds": [tid]}
    note = seen[-1]["params"]
    assert note["taskId"] == tid and note["status"] == "completed" and note["_meta"][mm.K_SUB] == "ts-1"
    assert note["result"]["structuredContent"]["status"] == "declined"


# ── C32-07 만료·워커 중단 — TTL 지난 작업은 -32602 · 생존 신호가 끊긴 working 은 failed 로 보인다 ──
def test_task_expiry_and_stale_worker(client, H):
    now = utcnow()
    with SessionLocal() as db:
        db.add(McpTask(id="task_e32_expired", server_id="jira", principal_sub="usr_dev", tool="search_issues", status="completed",
                       ttl_ms=1000, created_at=now - timedelta(minutes=5), updated_at=now - timedelta(minutes=5)))
        db.add(McpTask(id="task_e32_stale", server_id="jira", principal_sub="usr_dev", tool="search_issues", status="working",
                       ttl_ms=mcp_tasks.TTL_MS, created_at=now, updated_at=now, heartbeat_at=now - timedelta(minutes=2)))
        db.commit()

    async def scenario():
        async with http(client) as ac:
            return (await task_call(ac, H["dev"], "jira", "tasks/get", "task_e32_expired"),
                    await task_call(ac, H["dev"], "jira", "tasks/get", "task_e32_stale"))
    expired, stale = run(client, scenario)
    assert expired["error"]["code"] == -32602
    assert stale["result"]["status"] == "failed" and stale["result"]["error"]["code"] == -32603
    with SessionLocal() as db:
        assert db.get(McpTask, "task_e32_expired") is None  # 만료 작업은 지운다
