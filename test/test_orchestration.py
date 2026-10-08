"""Epic 8 — Agent App/MCP Host · Local Agent Orchestration (FR-A00~A02, FR-W02·W04·W05, FR-A05)."""
import json
import time
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.http import register_mount
from app.db import SessionLocal
from app.models import A2ATask, McpSession, Message, Run, RunStep, TokenExchange, UsageRecord
from app.services import a2a_proto as ap
from app.services import orchestrator as orch
from test.conftest import approve_agent

H_SSE = {"Accept": "text/event-stream"}


def wait_run(client, H, rid, until=("succeeded", "partial", "failed", "waiting_approval", "canceled"), timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        run = client.get(f"/api/v1/runs/{rid}", headers=H).json()
        if run["status"] in until:
            return run
        time.sleep(0.1)
    raise AssertionError(f"run {rid} timeout: {run['status']}")


def ask(client, H, profile, text, **kw):
    sid = client.post("/api/v1/sessions", json={"profile_id": profile}, headers=H).json()["id"]
    res = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": text, **kw}, headers=H)
    assert res.status_code == 200, res.text
    return sid, res.json()["run_id"]


def sse_events(text):
    out, ev = [], {}
    for line in text.splitlines():
        if line.startswith("event: "):
            ev = {"type": line[7:]}
        elif line.startswith("data: ") and ev:
            ev["data"] = json.loads(line[6:])
            out.append(ev)
            ev = {}
    return out


@pytest.fixture(scope="module", autouse=True)
def ready(client, H):
    for sid in ("jira", "confluence", "dynatrace", "internal-api"):
        client.post(f"/api/v1/mcp-servers/{sid}/refresh", headers=H["operator"])
    client.post("/api/v1/a2a/agents/observability-analyst/refresh", headers=H["operator"])


def test_templates_and_building_blocks(client, H):
    tpls = client.get("/api/v1/agents", params={"template": True}, headers=H["user"]).json()["items"]
    assert {t["id"] for t in tpls} >= {"tpl-qa", "tpl-issue-report", "tpl-incident"}
    blocks = client.get("/api/v1/agents/tools", headers=H["dev"]).json()
    tools = {t["qualified"]: t for t in blocks["tools"]}
    assert tools["confluence.create_page"]["suggested_node"] == "executor"
    assert tools["jira.search_issues"]["suggested_node"] == "retriever"
    assert "observability-analyst" in {a["id"] for a in blocks["remote_agents"]}
    cat = client.get("/v1/catalog", params={"type": "agent_profile"}, headers=H["user"]).json()
    assert cat["counts"]["agent_profile"] >= 3


def test_qa_template_sse_end_to_end(client, H):
    sid = client.post("/api/v1/sessions", json={"profile_id": "tpl-qa"}, headers=H["user"]).json()["id"]
    with client.stream("POST", f"/api/v1/sessions/{sid}/messages", json={"content": "장애 대응 런북 알려줘"},
                       headers={**H["user"], **H_SSE}) as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        events = sse_events(res.read().decode())
    types = [e["type"] for e in events]
    assert types[:2] == ["run", "run.started"] and "plan" in types and types[-1] == "run.completed"
    plan = next(e for e in events if e["type"] == "plan")["data"]
    assert plan["source"] == "template" and plan["fallback_reason"]  # mock 플래너 → 템플릿 폴백
    assert types.count("step.completed") == 2 and "delta" in types
    done = events[-1]["data"]
    assert done["status"] == "succeeded" and done["citations"][0]["tool"] == "confluence.search_pages"
    assert "[1]" in done["text"]  # 근거 인용
    run_id = events[0]["data"]["run_id"]
    with SessionLocal() as db:
        msgs = db.scalars(select(Message).where(Message.session_id == sid).order_by(Message.created_at)).all()
        assert [m.role for m in msgs] == ["user", "assistant"] and msgs[1].parts[0]["type"] == "citations"
        kinds = {(u.kind, u.agent) for u in db.scalars(select(UsageRecord).where(UsageRecord.run_id == run_id))}
        assert ("tool", "tpl-qa/retriever") in kinds and ("model", "tpl-qa/summarize") in kinds  # 게이트웨이 경유
        tx = db.scalars(select(TokenExchange).where(TokenExchange.audience == "mcp:confluence",
                                                    TokenExchange.subject_sub == "usr_user")
                        .order_by(TokenExchange.ts.desc())).first()
        assert tx.subject_kind == "delegation"  # 사용자 신원(위임 토큰) 전파
        host = [s for s in db.scalars(select(McpSession).where(McpSession.kind == "client"))
                if (s.client_info or {}).get("run") == run_id]
        assert host and host[0].closed_at is not None  # 실행 단위 MCP 클라이언트 세션 생성·종료 (FR-A00)


def test_issue_report_approval_pause_and_resume(client, H):
    _, rid = ask(client, H["user"], "tpl-issue-report", "이번 주 프로젝트 ORION 이슈 요약해서 보고서 써줘")
    run = wait_run(client, H["user"], rid)
    assert run["status"] == "waiting_approval"
    st = {s["name"]: s for s in run["steps"]}
    assert st["s1"]["status"] == st["s2"]["status"] == st["s3"]["status"] == "succeeded"
    assert st["s4"]["status"] == "waiting_approval" and st["s4"]["args"]["body"].startswith("요약")  # {{s3.text}} 렌더
    ap_id = st["s4"]["approval_id"]
    assert client.post(f"/api/v1/approvals/{ap_id}/approve", headers=H["user"]).json()["status"] == "approved"
    run = wait_run(client, H["user"], rid, until=("succeeded", "partial", "failed"))
    assert run["status"] == "succeeded" and "실행 완료 — confluence.create_page" in run["result"]
    page = next(s for s in run["steps"] if s["name"] == "s4")["output"]["raw"]
    assert page["created_by"] == "사용자" and page["url"].startswith("https://wiki.hangyeol.example/ORION/")
    ev = client.get(f"/api/v1/runs/{rid}/events", headers=H["user"]).json()["items"]
    types = [e["type"] for e in ev]
    assert types.index("run.waiting") < types.index("approval.resolved") < types.index("run.completed")


def test_rejected_approval_gives_partial_result(client, H):
    _, rid = ask(client, H["user"], "tpl-issue-report", "주간 보고")
    run = wait_run(client, H["user"], rid)
    ap_id = next(s for s in run["steps"] if s["name"] == "s4")["approval_id"]
    client.post(f"/api/v1/approvals/{ap_id}/reject", json={"note": "아직 공개 불가"}, headers=H["user"])
    run = wait_run(client, H["user"], rid, until=("succeeded", "partial", "failed"))
    assert run["status"] == "partial" and "일부 단계가 완료되지 않았습니다" in run["result"]
    assert next(s for s in run["steps"] if s["name"] == "s4")["status"] == "rejected"


def test_incident_delegation_parallel_with_remote_agent(client, H):
    _, rid = ask(client, H["user"], "tpl-incident", "orders-api 장애 원인 분석해줘")
    run = wait_run(client, H["user"], rid)
    assert run["status"] == "succeeded", run
    st = {s["name"]: s for s in run["steps"]}
    assert st["s2"]["node"] == "remote" and st["s2"]["output"]["state"] == "completed"
    # s1·s2 는 병렬(의존 없음), s3 가 합류
    assert st["s3"]["started_at"] >= max(st["s1"]["finished_at"], st["s2"]["finished_at"])
    assert "원격 에이전트 observability-analyst 위임 완료" in run["result"]
    with SessionLocal() as db:
        task = db.scalars(select(A2ATask).where(A2ATask.run_id == rid)).first()
        assert task.state == "completed" and task.agent_id == "observability-analyst"
        msg = db.scalars(select(Message).where(Message.run_id == rid, Message.role == "assistant")).first()
        assert any(p["type"] == "remote_task" and p["artifacts"] for p in msg.parts)  # SCR-C05 위임 카드


def _flaky_agent(state: dict) -> Starlette:
    async def rpc(request):
        state["calls"] += 1
        if state["calls"] <= state["fail_first"]:
            return JSONResponse({"error": "busy"}, status_code=503)
        body = await request.json()
        m = ap.message_from(body["params"]["message"])
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": {
            "kind": "task", "id": uuid.uuid4().hex, "contextId": "c", "artifacts": [],
            "status": {"state": "completed", "message": {"kind": "message", "messageId": "x", "role": "agent",
                                                         "parts": [{"kind": "text", "text": "flaky ok: " +
                                                                    m["parts"][0]["text"]}]}}}})

    async def card(_):
        return JSONResponse(ap.build_card(name="Flaky", description="일시 장애 에이전트", url="http://flaky-agent.test/",
                                          version="1", skills=[{"id": "f", "name": "F"}], schemes={}))
    return Starlette(routes=[Route("/.well-known/agent-card.json", card), Route("/", rpc, methods=["POST"])])


def test_automatic_retry_and_user_retry(client, H):
    state = {"calls": 0, "fail_first": 1}
    register_mount("http://flaky-agent.test", async_transport=httpx.ASGITransport(app=_flaky_agent(state)))
    client.post("/api/v1/a2a/agents", json={"url": "http://flaky-agent.test", "id": "flaky",
                                            "auth": {"scheme": "none"}}, headers=H["dev"])
    approve_agent(client, H, "flaky")
    client.put("/api/v1/gateway/governance/agents/flaky/trust", json={"trust_tier": "trusted"}, headers=H["security"])
    rest = FastAPI()
    flags = {"down": True}

    @rest.get("/kb", operation_id="kb.search")
    def kb(q: str = ""):
        if flags["down"]:
            raise RuntimeError("kb down")
        return [{"id": "KB-1", "title": "복구 절차", "excerpt": "재시작 후 확인"}]
    register_mount("http://kb.test", async_transport=httpx.ASGITransport(app=rest, raise_app_exceptions=False))
    client.post("/api/v1/mcp-servers/openapi", json={"namespace": "kb", "title": "KB", "openapi": rest.openapi(),
                                                     "base_url": "http://kb.test"}, headers=H["operator"])
    yaml_text = """
planner: {model: auto, mode: template}
workers:
  retriever: {tools: [kb.kb_search]}
  remote: {agents: [flaky]}
  summarize: {model: domain-local}
retry: {max_attempts: 2, backoff_ms: 50}
default_plan:
  - {id: r1, agent: remote, remote_agent: flaky, args: {message: "{{request}}"}}
  - {id: k1, agent: retriever, tool: kb.kb_search, args: {q: "{{request}}"}}
  - {id: s1, agent: summarize, depends_on: [r1, k1]}
"""
    prof = client.post("/api/v1/agents", json={"name": "재시도 시험", "yaml": yaml_text, "id": "retry-test"},
                       headers=H["dev"])
    assert prof.status_code == 201, prof.text
    client.post("/api/v1/agents/retry-test/publish", headers=H["dev"])
    _, rid = ask(client, H["user"], "retry-test", "서버 복구")
    run = wait_run(client, H["user"], rid)
    st = {s["name"]: s for s in run["steps"]}
    assert st["r1"]["status"] == "succeeded" and st["r1"]["attempts"] == 2  # 일시 장애 자동 재시도
    assert st["k1"]["status"] == "failed" and st["s1"]["status"] == "succeeded"  # 부분 결과로 합류
    assert run["status"] == "partial" and "kb.kb_search" in run["result"]
    flags["down"] = False
    assert client.post(f"/api/v1/runs/{rid}/retry", headers=H["user"]).json()["status"] == "retrying"
    run = wait_run(client, H["user"], rid, until=("succeeded", "failed"))
    st = {s["name"]: s for s in run["steps"]}
    assert run["status"] == "succeeded" and st["k1"]["status"] == "succeeded"
    assert "복구 절차" in st["s1"]["output"]["text"]  # 하위 의존 요약도 재실행


def test_llm_planner_and_fallback(client, H):
    plan = {"steps": [{"id": "a", "agent": "retriever", "tool": "jira.search_issues", "args": {"query": "결제"}},
                      {"id": "b", "agent": "summarize", "depends_on": ["a"]}]}
    client.post("/api/v1/providers", json={"id": "mock-planner", "name": "Planner mock", "kind": "mock",
                                           "options": {"replies": {"planner-v1": plan,
                                                                   "planner-bad": {"steps": [{"id": "x",
                                                                                              "agent": "executor",
                                                                                              "tool": "jira.add_comment"}]}}}},
                headers=H["operator"])
    for mid in ("planner-v1", "planner-bad"):
        client.post("/api/v1/models", json={"id": mid, "provider_id": "mock-planner", "upstream_model": mid,
                                            "category": "local"}, headers=H["operator"])
    for pid, model in (("llm-plan", "planner-v1"), ("llm-bad", "planner-bad")):
        g = {"planner": {"model": model, "mode": "llm"},
             "workers": {"retriever": {"tools": ["jira.*"]}, "summarize": {"model": "domain-local"}},
             "default_plan": [{"id": "d1", "agent": "retriever", "tool": "jira.search_issues"},
                              {"id": "d2", "agent": "summarize", "depends_on": ["d1"]}]}
        client.post("/api/v1/agents", json={"name": pid, "graph": g, "id": pid}, headers=H["dev"])
    ok = client.post("/api/v1/agents/llm-plan/test", json={"request": "결제 이슈"}, headers=H["dev"]).json()
    assert [s["name"] for s in ok["run"]["steps"]] == ["a", "b"] and ok["status"] == "succeeded"
    ev = client.get(f"/api/v1/runs/{ok['run_id']}/events", headers=H["dev"]).json()["items"]
    assert next(e for e in ev if e["type"] == "plan")["data"]["source"] == "llm"
    bad = client.post("/api/v1/agents/llm-bad/test", json={"request": "x"}, headers=H["dev"]).json()
    assert [s["name"] for s in bad["run"]["steps"]] == ["d1", "d2"]  # 검증 실패 → 템플릿 폴백
    ev = client.get(f"/api/v1/runs/{bad['run_id']}/events", headers=H["dev"]).json()["items"]
    assert "workers.executor" in next(e for e in ev if e["type"] == "plan")["data"]["fallback_reason"]


def test_graph_validation_versions_and_rules(client, H):
    bad_yaml = "planner: {model: auto, mode: llm}\nworkers:\n  retriever: {tools: [jira.search_issues]}\n" \
               "default_plan:\n  - {id: s1, agent: retriever, tool: confluence.create_page}\n"
    res = client.post("/api/v1/agents", json={"name": "bad", "yaml": bad_yaml}, headers=H["dev"])
    assert res.status_code == 422
    err = res.json()["errors"][0]
    assert err["path"] == "default_plan[0].tool" and err["line"] == 5
    syntax = client.post("/api/v1/agents", json={"name": "bad2", "yaml": "planner: [unclosed"}, headers=H["dev"])
    assert syntax.status_code == 422 and syntax.json()["errors"][0]["line"] is not None
    clone = client.post("/api/v1/agents", json={"name": "나의 Q&A", "from_template": "tpl-qa", "id": "my-qa"},
                        headers=H["dev"]).json()
    assert clone["status"] == "draft" and clone["tools"] == ["confluence.get_page", "confluence.search_pages",
                                                             "internal-api.orders_search"]
    assert client.put("/api/v1/agents/tpl-qa/graph", json={"graph": {}}, headers=H["dev"]).status_code == 400
    assert client.post("/api/v1/agents/my-qa/a2a", json={"exposed": True}, headers=H["dev"]).status_code == 400
    v1 = client.post("/api/v1/agents/my-qa/publish", json={"note": "first"}, headers=H["dev"]).json()
    g = v1["graph"]
    g["planner"]["max_steps"] = 3
    client.put("/api/v1/agents/my-qa/graph", json={"graph": g}, headers=H["dev"])
    v2 = client.post("/api/v1/agents/my-qa/publish", headers=H["dev"]).json()
    assert (v1["version"], v2["version"]) == (1, 2)
    rb = client.post("/api/v1/agents/my-qa/rollback", json={"version": 1}, headers=H["dev"]).json()
    assert rb["version"] == 3 and rb["graph"]["planner"]["max_steps"] == 4
    val = client.post("/api/v1/agents/my-qa/validate", json={"yaml": rb["yaml"]}, headers=H["dev"]).json()
    assert val["valid"] and val["canvas"]["edges"]
    assert client.post("/api/v1/agents", json={"name": "x", "graph": {}}, headers=H["user"]).status_code == 403


def test_instant_use_within_60s(client, H):
    t0 = time.perf_counter()
    rest = FastAPI()

    @rest.get("/faq", operation_id="faq.search")
    def faq(q: str = ""):
        return [{"id": "FAQ-7", "title": "비밀번호 재설정", "excerpt": "포털 > 계정 > 재설정"}]
    register_mount("http://faq.test", async_transport=httpx.ASGITransport(app=rest))
    reg = client.post("/api/v1/mcp-servers/openapi", json={"namespace": "faq", "title": "FAQ", "openapi": rest.openapi(),
                                                           "base_url": "http://faq.test"}, headers=H["operator"])
    assert reg.json()["refresh"]["health"] == "healthy"
    tools = {t["qualified"] for t in client.get("/api/v1/agents/tools", headers=H["dev"]).json()["tools"]}
    assert "faq.faq_search" in tools  # 등록 즉시 오케스트레이션 재료로 노출
    g = {"planner": {"model": "auto", "mode": "template"},
         "workers": {"retriever": {"tools": ["faq.faq_search"]}, "summarize": {"model": "domain-local"}},
         "default_plan": [{"id": "s1", "agent": "retriever", "tool": "faq.faq_search", "args": {"q": "{{request}}"}},
                          {"id": "s2", "agent": "summarize", "depends_on": ["s1"]}]}
    client.post("/api/v1/agents", json={"name": "FAQ 봇", "graph": g, "id": "faq-bot"}, headers=H["dev"])
    res = client.post("/api/v1/agents/faq-bot/test", json={"request": "비밀번호 재설정 방법"}, headers=H["dev"]).json()
    assert res["status"] == "succeeded" and "비밀번호 재설정" in res["run"]["result"]
    assert time.perf_counter() - t0 < 60  # 카탈로그 선택 → 등록 → 사용 ≤ 60s (FR-W04, NFR-P1)


def test_platform_a2a_runs_orchestration(client, H):
    res = client.post("/a2a/platform/tpl-qa", json={"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {
        "message": {"kind": "message", "messageId": uuid.uuid4().hex, "role": "user",
                    "parts": [{"kind": "text", "text": "휴가 정책"}]}}}, headers=H["user"]).json()["result"]
    assert res["status"]["state"] == "completed"
    answer = next(a for a in res["artifacts"] if a["name"] == "answer.md")["parts"][0]["text"]
    assert "[1]" in answer
    with SessionLocal() as db:
        assert db.get(A2ATask, f"platform:{res['id']}").state == "completed"


def test_run_isolation_and_data_class_routing(client, H):
    _, rid_internal = ask(client, H["user"], "tpl-qa", "런북")
    g = client.get("/api/v1/agents/my-qa", headers=H["dev"]).json()["graph"]
    g["workers"]["summarize"]["model"] = "auto"
    client.put("/api/v1/agents/my-qa/graph", json={"graph": g}, headers=H["dev"])
    client.post("/api/v1/agents/my-qa/publish", headers=H["dev"])
    _, rid_public = ask(client, H["user"], "my-qa", "공개 FAQ", data_class="public")
    _, rid_conf = ask(client, H["user"], "my-qa", "기밀 문서", data_class="confidential")
    for rid in (rid_internal, rid_public, rid_conf):
        wait_run(client, H["user"], rid)
    with SessionLocal() as db:
        def model_of(rid):
            return db.scalars(select(UsageRecord).where(UsageRecord.run_id == rid, UsageRecord.kind == "model")).first().target
        assert model_of(rid_public) == "fast-commercial"  # auto → 비용 우선
        assert model_of(rid_conf) == "domain-local"  # 기밀 → 로컬 모델 재라우팅
    assert client.get(f"/api/v1/runs/{rid_internal}", headers=H["user2"]).status_code == 404
    assert all(s["id"] for s in client.get("/api/v1/sessions", headers=H["user2"]).json()["items"]) is True


def test_attachment_used_as_source(client, H):
    _, rid = ask(client, H["user"], "tpl-qa", "첨부 내용 요약",
                 attachments=[{"name": "notes.txt", "text": "회의 결론: 배포는 금요일 18시"}])
    run = wait_run(client, H["user"], rid)
    assert "첨부 notes.txt" in run["result"] and any(c["title"] == "첨부:notes.txt" for c in run["citations"])


def test_cancel_and_errors(client, H):
    _, rid = ask(client, H["user"], "tpl-issue-report", "보고서")
    wait_run(client, H["user"], rid)
    assert client.post(f"/api/v1/runs/{rid}/cancel", headers=H["user"]).json()["status"] == "canceled"
    assert client.post(f"/api/v1/runs/{rid}/cancel", headers=H["user"]).status_code == 400
    sid = client.post("/api/v1/sessions", json={}, headers=H["user"]).json()["id"]
    assert client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "x"},
                       headers=H["user"]).json()["code"] == "profile_required"
    with SessionLocal() as db:
        assert db.get(Run, rid).status == "canceled"
        assert {s.status for s in db.scalars(select(RunStep).where(RunStep.run_id == rid))} <= {"succeeded", "skipped"}
    assert orch.render("{{request}}/{{s1.text}}", {"request": "a", "outputs": {"s1": {"text": "b"}}}) == "a/b"
