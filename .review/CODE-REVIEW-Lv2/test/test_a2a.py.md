> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../INDEX.md) · 디렉터리 개요: [test_Abstract.md](test_Abstract.md)

### 40. test/test_a2a.py
**파일 요약 / File Summary**
- 역할 / Role: Epic 7 A2A 경로 통합 시험 — 카드 조회·검증·게이트웨이 카드, 0.3/1.0 send·stream, input-required 재개, 비블로킹·감시기, 취소·푸시 중계, 신뢰 등급 차단, 스킬 라우팅, 등록·검증·프로토콜 변환, 카드 변경 감지, Task 격리·아티팩트, 플랫폼 A2A 서버, 공식 a2a-sdk 상호운용.
- 진입점 거리 / Distance: 테스트(진입점 근접 — `/a2a/*`)
- LOC: 306
- 주요 export(테스트 함수): 16개
- 의존(내부): `app.core.http`, `app.db`, `app.models`, `app.services.{a2a_gateway,a2a_proto,a2a_server}`, `test.conftest.approve_agent`
- 외부 패키지: pytest, httpx, starlette, a2a-sdk(상호운용 시험)
- 부수효과 / Side effects: 공유 DB(에이전트 신뢰 등급 변경·프로파일 추가), transport 마운트
- 동시성 / Concurrency: 동기 TestClient + async 시험(감시기·SDK)
- 테스트 커버리지 흔적: 자신이 `app/api/a2a.py`·`app/services/a2a_gateway.py`·`a2a_proto` 를 검증

## 라인 리뷰 / Line Walk (Level 2)

```python path=test/test_a2a.py lines=1-45
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

```
**분석 / Analysis (Level 2)**
- L24-28 `msg`: 0.3/1.0 메시지 생성 헬퍼. L31-32 `call`: JSON-RPC 호출. L35-36 `sse`: SSE 응답 파싱.
- L39-44 `ready`(module·autouse): dynatrace 갱신 + 데모 에이전트 카드 조회.

```python path=test/test_a2a.py lines=47-75
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

```
**분석 / Analysis (Level 2)**
- L47-57: 카드 조회·검증(0.3·1.0 인터페이스), 게이트웨이 카드 URL 치환, 카탈로그 노출.
- L59-74 `test_send_v03_completes_via_gateway_with_workload_identity`: 0.3 send 완료, 보고서·데이터 아티팩트, **원격 에이전트가 게이트웨이 경유로 MCP·모델 호출**(`via: gateway`), Task 영속, 교환 토큰 2종(사용자→에이전트, 워크로드→MCP), 사용량 기록.
**짚어볼 점 / Watch-outs**
- L66-74 는 PRD 핵심 시나리오(원격 에이전트가 **자기 워크로드 신원**으로 게이트웨이를 다시 경유)를 엔드투엔드로 검증 — 교환 토큰 쌍 `("usr_user", "a2a:…")`·`("workload:a2a-demo", "mcp:dynatrace")` 를 DB 에서 확인. 강력한 통합 시험.

```python path=test/test_a2a.py lines=77-124
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
```
**분석 / Analysis (Level 2)**
- L77-82: 1.0 형식(`TASK_STATE_COMPLETED`·kind 없는 파트).
- L84-90: input-required → 같은 taskId 로 재개(multi-turn).
- L93-104: 스트리밍 이벤트(task→artifact-update→status-update), 0.3·1.0 양쪽.
- L106-124 `test_nonblocking_and_watcher`(async): 비블로킹 send 후 Task 를 working 으로 되돌려 **감시기(poll_once)가 회수**하는지 폴링 확인.
**짚어볼 점 / Watch-outs**
- L111-114 Task 상태를 직접 DB 로 조작해 "진행 중 유실" 상황을 만든다 — 감시기 복구 경로를 결정적으로 시험하는 영리한 방법.
- L117-123 최대 10초 폴링 — 느린 CI 에서 불안정(flaky) 가능성. 실제로는 in-process 라 빠르게 완료.

```python path=test/test_a2a.py lines=127-177
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

```
**분석 / Analysis (Level 2)**
- L127-155 `test_cancel_and_push_relay`: input-required Task 취소(재취소 불가 -32002), tasks/get 확인, **푸시 설정 → 원격 → 게이트웨이 웹훅 → 클라이언트 웹훅 중계**(클라이언트 토큰 확인), 미지 토큰 404.
- L158-166: 신뢰 등급 blocked → 403(agent_blocked), 없는 에이전트 404, 미인증 401.
- L168-177: 스킬 라우팅(결정 로그), 없는 스킬 404.
**짚어볼 점 / Watch-outs**
- L137-151 푸시 중계를 MockTransport 로 받아 **클라이언트 웹훅 토큰이 전달되는지** 확인 — 중계 경로 검증. 단 `app/services/a2a_gateway.py:769-770` 의 "본문 task id 로 갱신"(무결성 약점)은 이 시험이 잡지 않는다(정상 흐름만 검증).
- L158-162 신뢰 등급을 blocked 로 바꾸고 L162 에서 trusted 로 되돌린다 — 부분 복원(이후 시험이 trusted 를 전제, L306 에서 최종 확인).

```python path=test/test_a2a.py lines=179-256
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

```
**분석 / Analysis (Level 2)**
- L179-221 `test_register_from_url_validation_and_protocol_translation`: 잘못된 카드 422, 신뢰 등급 지정 권한 403, 레거시(0.3) 에이전트 등록 → pending(승인 전 호출 불가) → 승인 후 미신뢰 거부 → 신뢰 상향(보안 담당) → **1.0 클라이언트 ↔ 0.3 에이전트 변환**(게이트웨이가 message/send 로 변환).
- L224-241: 카드 변경 감지(버전 2.0)·알림·확인.
- L244-256: Task 격리(user2 는 본인 것만, 교차 404), 아티팩트 markdown, 카드 캐시.
**짚어볼 점 / Watch-outs**
- L204-220 은 등록 승인(E05)·신뢰 등급 직무 분리·프로토콜 변환을 한 시험에 연결 — 여러 통제를 한 흐름으로 검증(현실적).
- L247-249 **크로스테넌트 Task 격리**(user2 가 user 의 Task 를 404)를 명시 검증 — 중요한 보안 커버리지.

```python path=test/test_a2a.py lines=258-306
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
```
**분석 / Analysis (Level 2)**
- L258-275 플랫폼 A2A 서버: 노출 프로파일 카드, 미인증 401, 완료·아티팩트, 수신 Task 영속(inbound).
- L278-296 `test_a2a_sdk_client_interop`(async): **공식 a2a-sdk(1.x)** 클라이언트가 게이트웨이 경유 카드로 원격 에이전트 호출(NFR-INT1 상호운용).
- L299-307: 미지 메서드·잘못된 본문·빈 서버.
**짚어볼 점 / Watch-outs**
- L278-296 공식 SDK 상호운용 시험은 자체 코덱(`a2a_proto`)이 사양과 맞는지 **외부 구현으로 교차 검증** — 매우 가치 있는 시험(자체 변환 로직의 사양 적합성 보증).
- L305-306 최종적으로 에이전트 신뢰 등급이 trusted 임을 확인 — 모듈 내 상태 변경(L162·L215)의 최종 상태를 고정.

## 파일 평가 / File Assessment
- 강점: (1) PRD 핵심 시나리오(원격 에이전트 워크로드 신원 재경유)·크로스테넌트 Task 격리·프로토콜 변환을 엔드투엔드로. (2) 공식 a2a-sdk 로 코덱 사양 적합성 교차 검증. (3) 감시기 복구를 DB 조작으로 결정적 시험.
- 위험: (1) 폴링 기반 시험(감시기·푸시)의 flaky 가능성. (2) 신뢰 등급 등 공유 상태를 모듈 내에서 바꿔 시험 순서에 의존. (3) 푸시 본문 task id 무결성 약점은 커버되지 않음(정상 흐름만).
- 개선 권고(영향순): (1) 폴링에 더 넉넉한 타임아웃·명시적 완료 신호. (2) 모듈 간 상태를 fixture 로 복원. (3) 다른 사용자 Task 를 푸시로 조작 시도하는 음성 시험 추가.
- 재읽기 권고 레벨: 3.
