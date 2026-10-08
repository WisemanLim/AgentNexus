"""Epic 27 — MCP 2026-07-28 수용 (D13-④): 게이트웨이 `/mcp/{id}` dual-era — stateless 요청(_meta 버전·능력), server/discover,
헤더 검증(-32020), 지원 안 하는 버전(-32022), resultType·serverInfo·캐시 힌트, MRTR(InputRequiredResult ↔ 레거시 업스트림 중계),
requestState 무결성(다른 주체·다른 요청·변조 거부), 레거시(initialize·세션) 회귀 없음.
"""
import base64

import httpx
import pytest

from app.core.http import StreamingASGITransport
from app.services import mcp_modern as mm

V = "2026-07-28"


@pytest.fixture(scope="module", autouse=True)
def refreshed(client, H):
    for s in ("jira", "internal-orders"):
        assert client.post(f"/api/v1/mcp-servers/{s}/refresh", headers=H["operator"]).json()["health"] == "healthy"


def run(client, fn):
    return client.portal.call(fn)


def http(client) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=StreamingASGITransport(client.app), base_url="http://testserver", timeout=60)


def hdr(auth: dict, method: str, name: str | None = None, version: str = V) -> dict:
    return {**auth, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": version, "Mcp-Method": method,
            **({"Mcp-Name": name} if name is not None else {})}


def body(method: str, params: dict | None = None, rid=1, caps: dict | None = None, version: str = V) -> dict:
    p = dict(params or {})
    p["_meta"] = {mm.K_VERSION: version, mm.K_CAPS: caps or {}, mm.K_CLIENT: {"name": "modern-test", "version": "1"}}
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": p}


# ── C27-01 server/discover · stateless 목록·호출 · resultType·serverInfo·캐시 힌트 · 세션 없음 ──
def test_discover_and_stateless_calls(client, H):
    d = client.post("/mcp/jira", json=body("server/discover"), headers=hdr(H["dev"], "server/discover"))
    assert d.status_code == 200 and "mcp-session-id" not in d.headers and d.headers["mcp-protocol-version"] == V
    r = d.json()["result"]
    assert r["resultType"] == "complete" and r["supportedVersions"][0] == V and "2025-11-25" in r["supportedVersions"]
    assert r["_meta"][mm.K_SERVER]["name"] == "AgentNexus MCP Gateway" and r["ttlMs"] > 0
    lst = client.post("/mcp/jira", json=body("tools/list", rid=2), headers=hdr(H["dev"], "tools/list")).json()["result"]
    assert lst["resultType"] == "complete" and lst["cacheScope"] == "private" and lst["ttlMs"] == 60_000
    assert any(t["name"] == "search_issues" for t in lst["tools"])
    call = client.post("/mcp/jira", json=body("tools/call", {"name": "search_issues", "arguments": {"query": "결제"}}, rid=3),
                       headers=hdr(H["dev"], "tools/call", "search_issues")).json()
    assert call["result"]["resultType"] == "complete" and not call["result"].get("isError")
    denied = client.post("/mcp/jira", json=body("tools/call", {"name": "nope_tool", "arguments": {}}, rid=4),
                         headers=hdr(H["dev"], "tools/call", "nope_tool")).json()
    assert "error" in denied or denied["result"].get("isError")  # 정책(허용목록)은 modern 에서도 같다


# ── C27-02 헤더 검증 -32020 · Base64 Mcp-Name · 지원 안 하는 버전 -32022 · 제거된 메서드 404 ──
def test_header_validation_versions_and_removed_methods(client, H):
    b = body("tools/call", {"name": "search_issues", "arguments": {"query": "x"}})
    no_method = client.post("/mcp/jira", json=b, headers={k: v for k, v in hdr(H["dev"], "tools/call", "search_issues").items() if k != "Mcp-Method"})
    assert no_method.status_code == 400 and no_method.json()["error"]["code"] == -32020
    wrong_name = client.post("/mcp/jira", json=b, headers=hdr(H["dev"], "tools/call", "get_issue"))
    assert wrong_name.status_code == 400 and wrong_name.json()["error"]["code"] == -32020
    enc = "=?base64?" + base64.b64encode(b"search_issues").decode() + "?="
    assert client.post("/mcp/jira", json=b, headers=hdr(H["dev"], "tools/call", enc)).status_code == 200
    mism = client.post("/mcp/jira", json=b, headers=hdr(H["dev"], "tools/call", "search_issues", version="2025-11-25"))
    assert mism.status_code == 400 and mism.json()["error"]["code"] == -32020
    old = client.post("/mcp/jira", json=body("tools/list", version="2099-01-01"), headers=hdr(H["dev"], "tools/list", version="2099-01-01"))
    assert old.status_code == 400 and old.json()["error"]["code"] == -32022
    assert V in old.json()["error"]["data"]["supported"] and old.json()["error"]["data"]["requested"] == "2099-01-01"
    no_meta = client.post("/mcp/jira", json={"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}}, headers=hdr(H["dev"], "tools/list"))
    assert no_meta.status_code == 400 and no_meta.json()["error"]["code"] == -32020
    ping = client.post("/mcp/jira", json=body("ping"), headers=hdr(H["dev"], "ping"))
    assert ping.status_code == 404 and ping.json()["error"]["code"] == -32601
    # 레거시 회귀 없음 — initialize·세션은 그대로
    init = client.post("/mcp/jira", json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "legacy", "version": "1"}}},
        headers={**H["dev"], "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"})
    assert init.status_code == 200 and init.headers.get("mcp-session-id")


# ── C27-03 MRTR elicitation — 레거시 업스트림의 elicitation → InputRequiredResult → inputResponses 재시도 → 최종 결과 ──
def test_mrtr_elicitation_bridge(client, H):
    caps = {"elicitation": {}}
    args = {"name": "request_return", "arguments": {"order_id": "O-2404"}}

    async def scenario():
        async with http(client) as ac:
            first = (await ac.post("/mcp/internal-orders", json=body("tools/call", args, rid=10, caps=caps),
                                   headers=hdr(H["user"], "tools/call", "request_return"))).json()["result"]
            key, req = next(iter(first["inputRequests"].items()))
            again = (await ac.post("/mcp/internal-orders", json=body("tools/call", {
                **args, "inputResponses": {key: {"action": "accept", "content": {"reason": "파손", "quantity": 2}}},
                "requestState": first["requestState"]}, rid=11, caps=caps), headers=hdr(H["user"], "tools/call", "request_return"))).json()
            return first, req, again
    first, req, again = run(client, scenario)
    assert first["resultType"] == "input_required" and req["method"] == "elicitation/create"
    assert set(req["params"]["requestedSchema"]["properties"]) == {"reason", "quantity"}
    res = again["result"]
    assert res["resultType"] == "complete" and res["structuredContent"]["quantity"] == 2


# ── C27-04 MRTR sampling — 클라이언트가 직접 모델 호출 결과를 돌려준다 ──
def test_mrtr_sampling_bridge(client, H):
    caps = {"sampling": {}}
    args = {"name": "classify_orders", "arguments": {}}

    async def scenario():
        async with http(client) as ac:
            first = (await ac.post("/mcp/internal-orders", json=body("tools/call", args, rid=20, caps=caps),
                                   headers=hdr(H["dev"], "tools/call", "classify_orders"))).json()["result"]
            key, req = next(iter(first["inputRequests"].items()))
            done = (await ac.post("/mcp/internal-orders", json=body("tools/call", {**args, "requestState": first["requestState"],
                    "inputResponses": {key: {"role": "assistant", "content": {"type": "text", "text": "클라이언트 분류"},
                                             "model": "client-model", "stopReason": "endTurn"}}}, rid=21, caps=caps),
                                   headers=hdr(H["dev"], "tools/call", "classify_orders"))).json()
            return req, done
    req, done = run(client, scenario)
    assert req["method"] == "sampling/createMessage"
    out = done["result"]["structuredContent"]
    assert out["classification"] == "클라이언트 분류" and out["model"] == "client-model"


# ── C27-05 requestState 무결성 — 변조·다른 주체·다른 인자 거부 (-32602) ──
def test_request_state_integrity(client, H):
    caps = {"elicitation": {}}
    args = {"name": "request_return", "arguments": {"order_id": "O-2401"}}

    async def scenario():
        async with http(client) as ac:
            first = (await ac.post("/mcp/internal-orders", json=body("tools/call", args, rid=30, caps=caps),
                                   headers=hdr(H["user"], "tools/call", "request_return"))).json()["result"]
            st = first["requestState"]
            tamper = await ac.post("/mcp/internal-orders", json=body("tools/call", {**args, "requestState": st[:-4] + "AAAA"}, rid=31, caps=caps),
                                   headers=hdr(H["user"], "tools/call", "request_return"))
            other = await ac.post("/mcp/internal-orders", json=body("tools/call", {**args, "requestState": st}, rid=32, caps=caps),
                                  headers=hdr(H["user2"], "tools/call", "request_return"))
            changed = await ac.post("/mcp/internal-orders", json=body("tools/call", {"name": "request_return", "arguments": {"order_id": "O-9999"},
                                    "requestState": st}, rid=33, caps=caps), headers=hdr(H["user"], "tools/call", "request_return"))
            key = next(iter(first["inputRequests"]))
            await ac.post("/mcp/internal-orders", json=body("tools/call", {**args, "requestState": st,
                          "inputResponses": {key: {"action": "decline"}}}, rid=34, caps=caps), headers=hdr(H["user"], "tools/call", "request_return"))
            return tamper.json(), other.json(), changed.json()
    tamper, other, changed = run(client, scenario)
    for r in (tamper, other, changed):
        assert r["error"]["code"] == -32602, r
