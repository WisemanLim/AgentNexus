"""Epic 36 — A2A HTTP+JSON(REST) 바인딩 (D16-①): 게이트웨이·플랫폼 수신 REST(같은 정책·감사·Task 영속) ·
google.rpc.Status 오류(ErrorInfo reason·domain) · SSE 는 StreamResponse 그대로 · 카드 HTTP+JSON 인터페이스 ·
HTTP+JSON 전용 원격 에이전트 호출(JSON-RPC 0.3/1.0 클라이언트 ↔ REST 업스트림 변환) · JSONRPC 회귀 없음.
"""
import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app.core.http import register_mount
from app.db import SessionLocal
from app.models import A2ATask, AuditEvent
from app.services import a2a_rest
from test.conftest import approve_agent
from test.test_a2a import AG, call, msg

REST = f"/a2a/{AG}/rest"


@pytest.fixture(scope="module", autouse=True)
def ready(client, H):
    assert client.post(f"/api/v1/a2a/agents/{AG}/refresh", headers=H["operator"]).status_code == 200


def body(text: str) -> dict:
    return {"message": {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text}]}}


def info(err: dict) -> dict:
    return next(d for d in err["error"]["details"] if d["@type"].endswith("ErrorInfo"))


# ── C36-01 게이트웨이 REST — send·get·cancel·list 정규 경로 · 오류는 google.rpc.Status · 인증 ──
def test_gateway_rest_unary(client, H):
    sent = client.post(f"{REST}/message:send", json=body("orders-api 장애 분석"), headers={**H["user"], "Content-Type": a2a_rest.MEDIA})
    assert sent.status_code == 200 and sent.headers["content-type"].startswith(a2a_rest.MEDIA) and "jsonrpc" not in sent.json()
    task = sent.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    got = client.get(f"{REST}/tasks/{task['id']}", params={"historyLength": 1}, headers=H["user"]).json()
    assert got["id"] == task["id"] and len(got.get("history") or []) <= 1
    done = client.post(f"{REST}/tasks/{task['id']}:cancel", json={}, headers=H["user"])
    assert done.status_code == 400 and info(done.json())["reason"] == "TASK_NOT_CANCELABLE" and info(done.json())["domain"] == "a2a-protocol.org"
    missing = client.get(f"{REST}/tasks/no-such-task", headers=H["user"])
    assert missing.status_code == 404 and missing.json()["error"]["status"] == "NOT_FOUND" and info(missing.json())["reason"] == "TASK_NOT_FOUND"
    unsupported = client.delete(f"{REST}/tasks/{task['id']}/pushNotificationConfigs/x", headers=H["user"])
    assert unsupported.status_code == 400 and info(unsupported.json())["reason"] == "UNSUPPORTED_OPERATION"
    bad = client.post(f"{REST}/message:send", json={"nope": 1}, headers=H["user"])
    assert bad.status_code == 400 and bad.json()["error"]["status"] == "INVALID_ARGUMENT"
    unknown = client.get(f"{REST}/nope", headers=H["user"])
    assert unknown.status_code == 404
    anon = client.post(f"{REST}/message:send", json=body("x"))
    assert anon.status_code == 401 and anon.json()["error"]["status"] == "UNAUTHENTICATED"
    with SessionLocal() as db:
        row = db.get(A2ATask, f"{AG}:{task['id']}")
        assert row is not None and any((e.get("protocol") or {}).get("client_binding") == "HTTP+JSON" for e in row.events or [])
        assert db.scalars(select(AuditEvent).where(AuditEvent.action == "a2a.send", AuditEvent.target == AG)
                          .order_by(AuditEvent.seq.desc())).first() is not None  # 같은 감사 경로


# ── C36-02 REST 스트림 — data 는 StreamResponse 그대로(JSON-RPC 봉투 없음) · 첫 이벤트 Task · 마지막 완료 ──
def test_gateway_rest_stream(client, H):
    with client.stream("POST", f"{REST}/message:stream", json=body("orders-api 장애 분석"), headers=H["user"]) as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[5:].strip()) for line in res.iter_lines() if line.startswith("data:")]
    assert events and all("jsonrpc" not in e for e in events)
    assert set(events[0]) & {"task", "statusUpdate", "message"}
    last = events[-1]
    state = (last.get("statusUpdate") or {}).get("status", {}).get("state") or (last.get("task") or {}).get("status", {}).get("state")
    assert state == "TASK_STATE_COMPLETED"


# ── C36-03 정책 동일 — 차단 등급 에이전트는 REST 로도 403 PERMISSION_DENIED (게이트웨이 사유 domain agentnexus) ──
def test_gateway_rest_policy_parity(client, H):
    client.put(f"/api/v1/gateway/governance/agents/{AG}/trust", json={"trust_tier": "blocked"}, headers=H["security"])
    try:
        r = client.post(f"{REST}/message:send", json=body("x"), headers=H["user"])
        rpc = call(client, H["user"], "SendMessage", {"message": msg("x", v="1.0")}).json()
    finally:
        client.put(f"/api/v1/gateway/governance/agents/{AG}/trust", json={"trust_tier": "trusted"}, headers=H["security"])
    assert r.status_code == 403 and r.json()["error"]["status"] == "PERMISSION_DENIED"
    assert info(r.json())["domain"] == "agentnexus" and info(r.json())["reason"] == str(rpc["error"]["data"]["code"]).upper()


# ── C36-04 플랫폼 REST · 카드 HTTP+JSON 인터페이스(게이트웨이 경유 카드·플랫폼 카드) ──
def test_platform_rest_and_cards(client, H):
    res = client.post("/a2a/platform/tpl-qa/rest/message:send", json=body("휴가 정책"), headers=H["user"]).json()
    assert res["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    got = client.get(f"/a2a/platform/tpl-qa/rest/tasks/{res['task']['id']}", headers=H["user"]).json()
    assert got["id"] == res["task"]["id"]
    assert client.get("/a2a/platform/nope-profile/rest/tasks/x", headers=H["user"]).status_code == 404
    pcard = client.get("/.well-known/agent-card.json").json()
    gcard = client.get(f"/a2a/{AG}/.well-known/agent-card.json").json()
    for card, suffix in ((pcard, "/a2a/platform/rest"), (gcard, f"/a2a/{AG}/rest")):
        rest = [i for i in card["supportedInterfaces"] if i["protocolBinding"] == "HTTP+JSON"]
        assert rest and rest[0]["url"].endswith(suffix) and rest[0]["protocolVersion"] == "1.0"


# ── C36-05 HTTP+JSON 전용 원격 에이전트 — JSON-RPC 0.3·1.0 클라이언트를 게이트웨이가 REST 로 변환 ──
def _rest_agent(seen: list) -> Starlette:
    tasks: dict = {}

    def task_of(tid: str, text: str, state="TASK_STATE_COMPLETED") -> dict:
        return {"id": tid, "contextId": "ctx-r", "status": {"state": state, "message": {
            "messageId": uuid.uuid4().hex, "role": "ROLE_AGENT", "parts": [{"text": f"rest ok: {text}"}]}}}

    async def send(request: Request):
        b = await request.json()
        seen.append(("POST", "message:send", request.headers.get("a2a-version"), request.headers.get("content-type")))
        tid = uuid.uuid4().hex
        tasks[tid] = task_of(tid, b["message"]["parts"][0]["text"])
        return JSONResponse({"task": tasks[tid]}, media_type=a2a_rest.MEDIA)

    async def get(request: Request):
        tid = request.path_params["tid"]
        seen.append(("GET", "tasks/{id}", None, None))
        if tid not in tasks:
            return JSONResponse({"error": {"code": 404, "status": "NOT_FOUND", "message": "없음", "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "TASK_NOT_FOUND", "domain": "a2a-protocol.org"}]}},
                status_code=404)
        return JSONResponse(tasks[tid])

    async def stream(request: Request):
        b = await request.json()
        seen.append(("POST", "message:stream", None, None))
        tid = uuid.uuid4().hex

        async def gen():
            yield f"data: {json.dumps({'task': task_of(tid, b['message']['parts'][0]['text'], 'TASK_STATE_WORKING')})}\n\n"
            yield f"data: {json.dumps({'statusUpdate': {'taskId': tid, 'contextId': 'ctx-r', 'status': {'state': 'TASK_STATE_COMPLETED'}}})}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    async def card(_):
        return JSONResponse({"name": "REST only", "description": "HTTP+JSON 전용", "version": "1", "capabilities": {"streaming": True},
                             "skills": [{"id": "rest", "name": "REST"}], "securitySchemes": {},
                             "supportedInterfaces": [{"url": "http://rest-agent.test/api", "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}]})
    return Starlette(routes=[Route("/.well-known/agent-card.json", card), Route("/api/message:send", send, methods=["POST"]),
                             Route("/api/message:stream", stream, methods=["POST"]), Route("/api/tasks/{tid}", get)])


def test_outbound_rest_only_agent(client, H):
    seen: list = []
    register_mount("http://rest-agent.test", async_transport=httpx.ASGITransport(app=_rest_agent(seen)))
    reg = client.post("/api/v1/a2a/agents", json={"url": "http://rest-agent.test", "id": "rest-only", "auth": {"scheme": "none"}}, headers=H["dev"])
    assert reg.status_code == 201, reg.text
    approve_agent(client, H, "rest-only")
    client.put("/api/v1/gateway/governance/agents/rest-only/trust", json={"trust_tier": "trusted"}, headers=H["security"])
    v03 = call(client, H["user"], "message/send", {"message": msg("hello")}, path="/a2a/rest-only").json()["result"]
    assert v03["kind"] == "task" and v03["status"]["state"] == "completed" and v03["status"]["message"]["parts"][0]["text"] == "rest ok: hello"
    assert seen[0] == ("POST", "message:send", "1.0", a2a_rest.MEDIA)
    got = call(client, H["user"], "tasks/get", {"id": v03["id"]}, path="/a2a/rest-only").json()["result"]
    assert got["id"] == v03["id"] and seen[-1][1] == "tasks/{id}"
    missing = call(client, H["user"], "GetTask", {"id": "nope"}, path="/a2a/rest-only").json()
    assert missing["error"]["code"] == -32001  # REST 404 TASK_NOT_FOUND → A2A 코드
    with client.stream("POST", "/a2a/rest-only", json={"jsonrpc": "2.0", "id": 5, "method": "SendStreamingMessage",
                                                       "params": {"message": msg("s", v="1.0")}}, headers=H["user"]) as res:
        evs = [json.loads(line[5:].strip()) for line in res.iter_lines() if line.startswith("data:")]
    assert evs[-1]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED" and ("POST", "message:stream", None, None) in seen
    with SessionLocal() as db:
        row = db.get(A2ATask, f"rest-only:{v03['id']}")
        assert any((e.get("protocol") or {}).get("upstream_binding") == "HTTP+JSON" for e in row.events or [])
