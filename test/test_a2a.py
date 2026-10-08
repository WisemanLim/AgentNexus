"""Epic 7 — A2A 경로 (FR-G08·G01·A04·A05, FR-R00~R06, FR-L03·I04)."""
import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.http import register_mount
from app.db import SessionLocal
from app.models import A2AAgent, A2ATask, AgentProfile, Notification, RoutingDecision, TokenExchange, UsageRecord
from app.services import a2a_gateway
from app.services import a2a_proto as ap
from app.services.a2a_server import A2AServer
from test.conftest import approve_agent

AG = "observability-analyst"


def msg(text, data=None, task_id=None, v="0.3"):
    m = {"kind": "message", "messageId": uuid.uuid4().hex, "role": "user",
         "parts": [{"kind": "text", "text": text}] + ([{"kind": "data", "data": data}] if data else []),
         **({"taskId": task_id} if task_id else {})}
    return m if v == "0.3" else ap.message_to_v10(m)


def call(client, headers, method, params, path=f"/a2a/{AG}"):
    return client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=headers)


def sse(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.fixture(scope="module", autouse=True)
def ready(client, H):
    client.post("/api/v1/mcp-servers/dynatrace/refresh", headers=H["operator"])
    res = client.post(f"/api/v1/a2a/agents/{AG}/refresh", headers=H["operator"])
    assert res.status_code == 200, res.text
    return res.json()


def test_card_fetch_validate_and_gateway_card(client, H, ready):
    info = ready["info"]
    assert info["protocol_versions"] == ["0.3", "1.0"] and info["streaming"] and info["push"]
    assert info["skills"][0]["id"] == "incident-analysis"
    gcard = client.get(f"/a2a/{AG}/.well-known/agent-card.json").json()
    assert gcard["url"] == f"http://testserver/a2a/{AG}"
    assert {i["protocolVersion"] for i in gcard["supportedInterfaces"]} == {"1.0", "0.3.0"}
    assert gcard["securitySchemes"]["bearer"]["scheme"] == "bearer"
    cat = client.get("/v1/catalog", params={"type": "a2a_agent", "q": "incident"}, headers=H["user"]).json()
    assert any(i["name"] == AG for i in cat["items"])


def test_send_v03_completes_via_gateway_with_workload_identity(client, H):
    res = call(client, H["user"], "message/send", {"message": msg("orders-api 장애 원인 분석해줘")}).json()
    task = res["result"]
    assert task["kind"] == "task" and task["status"]["state"] == "completed"
    report = next(a for a in task["artifacts"] if a["name"] == "incident-report.md")["parts"][0]["text"]
    data = next(a for a in task["artifacts"] if a["name"] == "incident-data")["parts"][0]["data"]
    assert "인덱스" in report and data["via"] == "gateway"  # 원격 에이전트가 게이트웨이 경유로 MCP·모델 호출
    with SessionLocal() as db:
        row = db.get(A2ATask, f"{AG}:{task['id']}")
        assert row.state == "completed" and row.principal_sub == "usr_user" and len(row.artifacts) == 2
        tx = {(t.subject_sub, t.audience) for t in db.scalars(select(TokenExchange).where(
            TokenExchange.decision == "issued"))}
        assert ("usr_user", f"a2a:{AG}") in tx  # 사용자 → 원격 에이전트 교환 토큰
        assert ("workload:a2a-demo", "mcp:dynatrace") in tx  # 원격 에이전트 자체 신원으로 MCP 호출
        kinds = {(u.user_sub, u.kind) for u in db.scalars(select(UsageRecord))}
        assert ("usr_user", "remote_agent") in kinds and ("workload:a2a-demo", "model") in kinds


def test_send_v10_format(client, H):
    res = call(client, H["user"], "SendMessage", {"message": msg("payment-gateway 분석", v="1.0")}).json()["result"]
    task = res["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "kind" not in task["artifacts"][0]["parts"][0] and "인증서" in task["artifacts"][0]["parts"][0]["text"]


def test_input_required_then_resume(client, H):
    first = call(client, H["user"], "message/send", {"message": msg("장애 분석 부탁해")}).json()["result"]
    assert first["status"]["state"] == "input-required" and "어떤 서비스" in ap.text_of(first)
    done = call(client, H["user"], "message/send",
                {"message": msg("settlement-batch", task_id=first["id"])}).json()["result"]
    assert done["id"] == first["id"] and done["status"]["state"] == "completed"
    assert "중복" in ap.text_of(done)


def test_streaming_events(client, H):
    raw = call(client, H["user"], "message/stream", {"message": msg("orders-api 스트리밍 분석")}).text
    events = [e["result"] for e in sse(raw)]
    kinds = [e.get("kind") for e in events]
    assert kinds[0] == "task" and "artifact-update" in kinds and kinds[-1] == "status-update"
    assert events[-1]["status"]["state"] == "completed" and events[-1]["final"] is True
    with SessionLocal() as db:
        assert db.get(A2ATask, f"{AG}:{events[0]['id']}").state == "completed"
    raw10 = call(client, H["user"], "SendStreamingMessage", {"message": msg("orders-api", v="1.0")}).text
    ev10 = [e["result"] for e in sse(raw10)]
    assert "task" in ev10[0] and ev10[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


@pytest.mark.anyio
async def test_nonblocking_and_watcher(client, H):
    res = call(client, H["user"], "message/send", {"message": msg("orders-api"), "configuration": {"blocking": False}})
    task = res.json()["result"]
    assert task["status"]["state"] in ("submitted", "working", "completed")
    with SessionLocal() as db:
        row = db.get(A2ATask, f"{AG}:{task['id']}")
        row.state = "working"  # 푸시·스트림 없이 진행 중으로 남은 Task 를 감시기가 회수하는지 확인
        db.commit()
    import asyncio
    state = None
    for _ in range(50):  # 감시기 주기 폴링 (최대 10s)
        assert await a2a_gateway.poll_once() >= 0
        with SessionLocal() as db:
            state = db.get(A2ATask, f"{AG}:{task['id']}").state
        if state == "completed":
            break
        await asyncio.sleep(0.2)
    assert state == "completed"


def test_cancel_and_push_relay(client, H):
    waiting = call(client, H["user"], "message/send", {"message": msg("분석")}).json()["result"]
    assert waiting["status"]["state"] == "input-required"
    canceled = call(client, H["user"], "tasks/cancel", {"id": waiting["id"]}).json()["result"]
    assert canceled["status"]["state"] == "canceled"
    again = call(client, H["user"], "tasks/cancel", {"id": waiting["id"]}).json()
    assert again["error"]["code"] == ap.TASK_NOT_CANCELABLE
    got = call(client, H["user"], "tasks/get", {"id": waiting["id"]}).json()["result"]
    assert got["status"]["state"] == "canceled"

    received = []
    register_mount("http://client-hook.test", async_transport=httpx.MockTransport(
        lambda r: received.append((json.loads(r.content), r.headers.get("x-a2a-notification-token")))
        or httpx.Response(200)))
    t2 = call(client, H["user"], "message/send", {"message": msg("분석")}).json()["result"]
    cfg = call(client, H["user"], "tasks/pushNotificationConfig/set", {"taskId": t2["id"], "pushNotificationConfig": {
        "url": "http://client-hook.test/hook", "token": "client-secret-token"}}).json()["result"]
    assert cfg["pushNotificationConfig"]["url"] == "http://client-hook.test/hook"  # 클라이언트에는 원래 설정을 돌려줌
    call(client, H["user"], "message/send", {"message": msg("orders-api", task_id=t2["id"])})
    import time
    for _ in range(30):  # 푸시는 비동기 전달 — 원격 → 게이트웨이 웹훅 → 클라이언트 웹훅
        if any(b.get("status", {}).get("state") == "completed" for b, _ in received):
            break
        time.sleep(0.1)
    assert any(b.get("status", {}).get("state") == "completed" and tok == "client-secret-token" for b, tok in received)
    with SessionLocal() as db:
        row = db.get(A2ATask, f"{AG}:{t2['id']}")
        assert row.push_token and row.push_config["url"] == "http://client-hook.test/hook"
    assert client.post("/a2a/push/unknown-token", json={}).status_code == 404


def test_trust_tier_blocking_and_unknown(client, H):
    client.put(f"/api/v1/gateway/governance/agents/{AG}/trust", json={"trust_tier": "blocked"}, headers=H["security"])
    res = call(client, H["user"], "message/send", {"message": msg("orders-api")})
    assert res.status_code == 403 and res.json()["error"]["data"]["code"] == "agent_blocked"
    client.put(f"/api/v1/gateway/governance/agents/{AG}/trust", json={"trust_tier": "trusted"}, headers=H["security"])
    nf = call(client, H["user"], "message/send", {"message": msg("x")}, path="/a2a/nope")
    assert nf.status_code == 404 and nf.json()["error"]["code"] == -32001
    assert call(client, {}, "message/send", {"message": msg("x")}).status_code == 401


def test_skill_based_routing(client, H):
    res = call(client, H["user"], "message/send", {"message": msg("orders-api")}, path="/a2a/_skill/incident-analysis")
    assert res.json()["result"]["status"]["state"] == "completed"
    with SessionLocal() as db:
        rd = db.scalars(select(RoutingDecision).where(RoutingDecision.kind == "a2a")
                        .order_by(RoutingDecision.ts.desc())).first()
        assert rd.chosen == AG and rd.requested == "skill:incident-analysis"
    assert call(client, H["user"], "message/send", {"message": msg("x")},
                path="/a2a/_skill/no-such-skill").status_code == 404


def _mock_agent(card: dict, seen: list) -> Starlette:
    async def rpc(request: Request):
        body = await request.json()
        seen.append(body["method"])
        m = ap.message_from(body["params"]["message"])
        task = {"kind": "task", "id": "t-legacy", "contextId": "c1", "status": {"state": "completed", "message": {
            "kind": "message", "messageId": "r1", "role": "agent", "parts": [{"kind": "text", "text": "legacy ok: "
                                                                               + m["parts"][0]["text"]}]}},
                "artifacts": []}
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": task})

    async def card_ep(_):
        return JSONResponse(card)
    return Starlette(routes=[Route("/.well-known/agent-card.json", card_ep), Route("/", rpc, methods=["POST"])])


def test_register_from_url_validation_and_protocol_translation(client, H):
    legacy_card = {"protocolVersion": "0.3.0", "name": "Legacy Agent", "description": "0.3 전용", "url": "http://legacy.test/",
                   "version": "0.9", "capabilities": {"streaming": False},
                   "skills": [{"id": "legacy", "name": "레거시", "description": "d"}], "securitySchemes": {}}
    seen: list = []
    register_mount("http://legacy.test", async_transport=httpx.ASGITransport(app=_mock_agent(legacy_card, seen)))
    register_mount("http://badcard.test", async_transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"name": "x"})))
    bad = client.post("/api/v1/a2a/agents", json={"url": "http://badcard.test"}, headers=H["dev"])
    assert bad.status_code == 422 and bad.json()["code"] == "agent_card_invalid" and bad.json()["errors"]
    assert client.post("/api/v1/a2a/agents", json={"url": "http://legacy.test", "trust_tier": "trusted"},
                       headers=H["dev"]).status_code == 403  # 신뢰 등급 지정은 보안 담당
    reg = client.post("/api/v1/a2a/agents", json={"url": "http://legacy.test", "id": "legacy",
                                                 "auth": {"scheme": "none"}}, headers=H["dev"])
    assert reg.status_code == 201 and reg.json()["protocol_versions"] == ["0.3"] and reg.json()["status"] == "pending"
    pending = call(client, H["user"], "SendMessage", {"message": msg("hi", v="1.0")}, path="/a2a/legacy")
    assert pending.json()["error"]["data"]["code"] == "agent_inactive"  # 승인 전 호출 불가 (MSG-008)
    approve_agent(client, H, "legacy")
    untrusted = call(client, H["user"], "SendMessage", {"message": msg("hi", v="1.0")}, path="/a2a/legacy")
    assert untrusted.json()["error"]["data"]["code"] == "agent_untrusted"  # 미신뢰 기본 거부
    client.put("/api/v1/gateway/governance/agents/legacy/trust", json={"trust_tier": "trusted"},
               headers=H["security"])  # 신뢰 등급은 보안 담당이 지정 (직무 분리)
    # 1.0 클라이언트 → 0.3 전용 에이전트: 게이트웨이가 변환
    out = call(client, H["user"], "SendMessage", {"message": msg("hello", v="1.0")}, path="/a2a/legacy").json()
    assert seen[-1] == "message/send"
    assert out["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert out["result"]["task"]["status"]["message"]["parts"][0]["text"] == "legacy ok: hello"


def test_card_change_detection(client, H):
    state = {"v": "1.0"}

    def card(_r):
        return httpx.Response(200, json=ap.build_card(name="Changing", description="d", url="http://changing.test/",
                                                      version=state["v"], skills=[{"id": "s", "name": "S"}]))
    register_mount("http://changing.test", async_transport=httpx.MockTransport(card))
    client.post("/api/v1/a2a/agents", json={"url": "http://changing.test", "id": "changing"}, headers=H["dev"])
    approve_agent(client, H, "changing")
    assert client.post("/api/v1/a2a/agents/changing/refresh", headers=H["dev"]).json()["changed"] is False
    state["v"] = "2.0"
    assert client.post("/api/v1/a2a/agents/changing/refresh", headers=H["dev"]).json()["changed"] is True
    detail = client.get("/api/v1/a2a/agents/changing", headers=H["dev"]).json()
    assert detail["card_changed"] and detail["version"] == "2.0"
    with SessionLocal() as db:
        assert db.scalars(select(Notification).where(Notification.kind == "a2a.card_changed")).first()
    ack = client.patch("/api/v1/a2a/agents/changing", json={"acknowledge_card_change": True}, headers=H["dev"])
    assert ack.json()["card_changed"] is False


def test_task_api_isolation_and_artifacts(client, H):
    task = call(client, H["user"], "message/send", {"message": msg("orders-api")}).json()["result"]
    tid = f"{AG}:{task['id']}"
    assert any(t["id"] == tid for t in client.get("/api/v1/a2a/tasks", headers=H["user"]).json()["items"])
    assert all(t["principal"] == "usr_user2" for t in client.get("/api/v1/a2a/tasks", headers=H["user2"]).json()["items"])
    assert client.get(f"/api/v1/a2a/tasks/{tid}", headers=H["user2"]).status_code == 404  # 팀 간 누출 차단
    detail = client.get(f"/api/v1/a2a/tasks/{tid}", headers=H["user"]).json()
    art = next(a for a in detail["artifacts"] if a["name"] == "incident-report.md")
    md = client.get(f"/api/v1/a2a/tasks/{tid}/artifacts/{art['artifactId']}", headers=H["user"])
    assert md.headers["content-type"].startswith("text/markdown") and md.text.startswith("# 장애 분석 보고서")
    cards = client.get("/api/v1/gateway/a2a/cards", headers=H["operator"]).json()["items"]
    assert any(c["id"] == AG and c["card_hash"] for c in cards)


def test_platform_a2a_server_exposes_profiles(client, H):
    with SessionLocal() as db:
        if db.get(AgentProfile, "qa-bot") is None:
            db.add(AgentProfile(id="qa-bot", name="사내 Q&A (A2A)", description="사내 문서 질의응답", status="published",
                                a2a_exposed=True))
            db.commit()
    card = client.get("/.well-known/agent-card.json").json()
    assert any(s["id"] == "qa-bot" for s in card["skills"]) and card["url"].endswith("/a2a/platform")
    assert client.get("/a2a/platform/qa-bot/.well-known/agent-card.json").json()["url"].endswith("/a2a/platform/qa-bot")
    assert client.post("/a2a/platform/qa-bot", json={"jsonrpc": "2.0", "id": 1, "method": "message/send",
                                                     "params": {"message": msg("x")}}).status_code == 401
    res = client.post("/a2a/platform/qa-bot", json={"jsonrpc": "2.0", "id": 1, "method": "message/send",
                                                    "params": {"message": msg("연차 규정 알려줘")}},
                      headers=H["user"]).json()["result"]
    assert res["status"]["state"] == "completed" and res["artifacts"]
    with SessionLocal() as db:
        row = db.get(A2ATask, f"platform:{res['id']}")
        assert row.direction == "inbound" and row.agent_id == "platform:qa-bot" and row.principal_sub == "usr_user"


@pytest.mark.anyio
async def test_a2a_sdk_client_interop(client, tokens):
    """NFR-INT1 — 공식 a2a-sdk(1.x) 클라이언트가 게이트웨이 경유 카드로 원격 에이전트를 호출."""
    from a2a.client import ClientConfig, create_client
    from a2a.types import Message, Part, Role, SendMessageRequest
    from app.main import app
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                             headers={"Authorization": f"Bearer {tokens['user']}"})
    sdk = await create_client(f"http://testserver/a2a/{AG}", client_config=ClientConfig(httpx_client=http,
                                                                                         streaming=False),
                              resolver_http_kwargs={"headers": {"Authorization": f"Bearer {tokens['user']}"}})
    req = SendMessageRequest(message=Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER,
                                             parts=[Part(text="orders-api 장애")]))
    final = None
    async for ev in sdk.send_message(req):
        final = ev
    assert final is not None and final.task.status.state == 3  # TASK_STATE_COMPLETED
    assert any("인덱스" in p.text for a in final.task.artifacts for p in a.parts)
    await http.aclose()


def test_unknown_methods_and_bad_requests(client, H):
    assert call(client, H["user"], "no/such", {}).json()["error"]["code"] == -32601
    assert client.post(f"/a2a/{AG}", content=b"{bad", headers={**H["user"], "Content-Type": "application/json"}
                       ).status_code == 400
    srv = A2AServer(lambda: {}, lambda ctx: None)
    assert srv.card() == {}
    with SessionLocal() as db:
        assert db.get(A2AAgent, AG).trust_tier == "trusted"
