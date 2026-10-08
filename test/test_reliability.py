"""Epic 18 — 오케스트레이션 신뢰성·확장 (FR-A01·A02·W02·R02~R04·A05·O02·O03·G01·S00, NFR-S1)."""
import importlib.util
import io
import json
import time
import uuid
import zipfile
import zlib
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.api import a2a as a2a_api
from app.core.config import settings
from app.core.http import register_mount
from app.db import SessionLocal, utcnow
from app.models import A2ATask, AlertRule, Notification, Run, RunEvent, RunStep, Setting
from app.services import a2a_proto as ap
from app.services import graph as G
from app.services import mcp_template, scheduler
from app.services import orchestrator as orch

from test.test_orchestration import ask, wait_run
from test.conftest import approve_agent

UNTIL = ("succeeded", "partial", "failed", "waiting_approval", "waiting_input", "canceled")


@pytest.fixture(scope="module", autouse=True)
def ready(client, H):
    for sid in ("jira", "confluence", "dynatrace"):
        client.post(f"/api/v1/mcp-servers/{sid}/refresh", headers=H["operator"])
    client.post("/api/v1/a2a/agents/observability-analyst/refresh", headers=H["operator"])


def events(rid: str, type_: str) -> list[dict]:
    with SessionLocal() as db:
        return [e.data for e in db.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == type_))]


def profile(client, H, pid: str, yaml_text: str) -> None:
    res = client.post("/api/v1/agents", json={"name": pid, "yaml": yaml_text, "id": pid}, headers=H["dev"])
    assert res.status_code == 201, res.text
    assert client.post(f"/api/v1/agents/{pid}/publish", headers=H["dev"]).status_code == 200


# ── C18-01 A2A input-required → 같은 Task·contextId 로 재개 ──
def test_remote_input_required_then_resume_same_task(client, H):
    _, rid = ask(client, H["user"], "tpl-incident", "장애 원인 분석해줘")  # 서비스 미지정 → 원격 에이전트가 되묻는다
    run = wait_run(client, H["user"], rid, until=UNTIL)
    assert run["status"] == "waiting_input", run
    req = events(rid, "input.required")[0]
    assert req["step"] == "s2" and "어떤 서비스" in req["question"] and req["task_id"] and req["context_id"]
    assert client.post(f"/api/v1/runs/{rid}/input", json={"step": "s2", "text": "orders-api"},
                       headers=H["user2"]).status_code == 404  # 다른 사용자는 답할 수 없다
    ok = client.post(f"/api/v1/runs/{rid}/input", json={"step": "s2", "text": "orders-api"}, headers=H["user"])
    assert ok.status_code == 200 and ok.json()["resumed"] is True
    run = wait_run(client, H["user"], rid, until=("succeeded", "partial", "failed"))
    assert run["status"] == "succeeded", run
    s2 = next(s for s in run["steps"] if s["name"] == "s2")
    assert s2["output"]["task_id"] == req["task_id"] and s2["output"]["context_id"] == req["context_id"]  # 같은 Task
    assert s2["output"]["turns"] == 2 and "orders-api" in s2["output"]["text"]
    again = client.post(f"/api/v1/runs/{rid}/input", json={"step": "s2", "text": "x"}, headers=H["user"])
    assert again.status_code == 400 and again.json()["code"] == "not_waiting_input"


def _slow_agent(state: dict) -> Starlette:
    async def rpc(request: Request):
        body = await request.json()
        tid = "slow-1"
        if body["method"] in ("message/send", "SendMessage"):
            state["sent"] += 1
            st = "working"
        else:
            state["polls"] += 1
            st = "completed" if state["polls"] >= 2 else "working"
        task = {"kind": "task", "id": tid, "contextId": "ctx-slow", "artifacts": [], "status": {"state": st, "message": {
            "kind": "message", "messageId": uuid.uuid4().hex, "role": "agent",
            "parts": [{"kind": "text", "text": f"분석 {st}"}]}}}
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": task})

    async def card(_):
        return JSONResponse(ap.build_card(name="Slow", description="오래 걸리는 에이전트", url="http://slow-agent.test/",
                                          version="1", skills=[{"id": "s", "name": "S"}], schemes={}))
    return Starlette(routes=[Route("/.well-known/agent-card.json", card), Route("/", rpc, methods=["POST"])])


# ── C18-02 원격 Task working → tasks/get 으로 완료까지 따라감 ──
def test_remote_working_task_is_polled_until_done(client, H):
    state = {"sent": 0, "polls": 0}
    register_mount("http://slow-agent.test", async_transport=httpx.ASGITransport(app=_slow_agent(state)))
    assert client.post("/api/v1/a2a/agents", json={"url": "http://slow-agent.test", "id": "slow-agent",
                                                   "auth": {"scheme": "none"}}, headers=H["dev"]).status_code == 201
    approve_agent(client, H, "slow-agent")
    client.put("/api/v1/gateway/governance/agents/slow-agent/trust", json={"trust_tier": "trusted"}, headers=H["security"])
    profile(client, H, "slow-delegate", """
planner: {mode: template, max_steps: 2}
workers: {remote: {agents: [slow-agent]}, summarize: {model: domain-local}}
default_plan:
  - {id: s1, agent: remote, remote_agent: slow-agent, args: {message: "{{request}}"}}
  - {id: s2, agent: summarize, depends_on: [s1]}
""")
    res = client.post("/api/v1/agents/slow-delegate/test", json={"request": "느린 분석"}, headers=H["dev"]).json()
    assert res["status"] == "succeeded", res
    assert state["sent"] == 1 and state["polls"] >= 2  # 다시 보내지 않고 tasks/get 으로 진행을 따라감
    s1 = next(s for s in res["run"]["steps"] if s["name"] == "s1")
    assert s1["output"]["state"] == "completed" and "분석 completed" in s1["output"]["text"]


# ── C18-03 조건 분기 ──
def test_conditional_branch_skips_and_takes_alternative(client, H):
    profile(client, H, "branch-test", """
planner: {mode: template, max_steps: 4}
workers:
  retriever: {tools: [jira.search_issues, confluence.search_pages]}
  summarize: {model: domain-local}
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues, args: {query: "없는키워드zzqx"}}
  - {id: s2, agent: retriever, tool: confluence.search_pages, args: {query: "런북"}, depends_on: [s1], when: "s1.count == 0"}
  - {id: s3, agent: summarize, depends_on: [s1], when: "s1.count > 0"}
  - {id: s4, agent: summarize, depends_on: [s2], when: "request contains '런북'"}
""")
    res = client.post("/api/v1/agents/branch-test/test", json={"request": "런북 찾아줘"}, headers=H["dev"]).json()
    st = {s["name"]: s for s in res["run"]["steps"]}
    n = st["s1"]["output"]["count"]
    assert st["s1"]["status"] == "succeeded" and isinstance(n, int)
    if n == 0:  # 검색 결과 없음 → 대체 분기(s2→s4)
        assert st["s2"]["status"] == "succeeded" and st["s3"]["error"] == "조건 불충족: s1.count > 0"
        assert st["s4"]["status"] == "succeeded"
    else:  # 결과 있음 → s3, 대체 분기는 선택되지 않음(실패 아님)
        assert st["s3"]["status"] == "succeeded" and st["s2"]["error"] == "조건 불충족: s1.count == 0"
        assert st["s4"]["status"] == "skipped" and st["s4"]["error"].startswith("분기 미선택")
    assert res["status"] == "succeeded"  # 조건·분기로 건너뛴 단계는 실패로 집계하지 않음
    bad = client.post("/api/v1/agents/branch-test/validate", json={"yaml": """
planner: {mode: template}
workers: {retriever: {tools: [jira.search_issues]}, summarize: {}}
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues, when: "s2.count > 0"}
  - {id: s2, agent: summarize, when: "s1.count >>> 0"}
"""}, headers=H["dev"]).json()
    paths = {e["path"] for e in bad["errors"]}
    assert "default_plan[0].when" in paths and "default_plan[1].when" in paths


def test_condition_evaluator_unit():
    outs = {"s1": {"count": 3, "text": "결제 장애 3건", "state": "completed"}}
    assert G.eval_condition("s1.count >= 3", "", outs) and not G.eval_condition("s1.count < 3", "", outs)
    assert G.eval_condition("s1.text contains '장애'", "", outs) and G.eval_condition("s1.state == 'completed'", "", outs)
    assert G.eval_condition("request not contains '배포'", "결제 문의", outs) and G.eval_condition("s1 exists", "", outs)
    assert not G.eval_condition("s9.count > 0", "", outs)  # 없는 출력은 거짓
    with pytest.raises(ValueError):
        G.parse_condition("s1.count")


# ── C18-04 재계획 (대체 계획) ──
def test_replan_with_fallback_plan_recovers(client, H):
    profile(client, H, "replan-test", """
planner:
  mode: template
  max_steps: 3
  replan:
    max_rounds: 1
    fallback_plan:
      - {id: alt, agent: retriever, tool: confluence.search_pages, args: {query: "런북"}}
      - {id: sum, agent: summarize, depends_on: [alt]}
workers:
  retriever: {tools: ["jira.*", confluence.search_pages]}
  summarize: {model: domain-local}
retry: {max_attempts: 1}
default_plan:
  - {id: s1, agent: retriever, tool: jira.no_such_tool, args: {}}
  - {id: s2, agent: summarize, depends_on: [s1]}
""")
    res = client.post("/api/v1/agents/replan-test/test", json={"request": "런북 요약"}, headers=H["dev"]).json()
    st = {s["name"]: s["status"] for s in res["run"]["steps"]}
    assert st == {"s1": "replanned", "s2": "replanned", "r1_alt": "succeeded", "r1_sum": "succeeded"}, st
    assert res["status"] == "succeeded"
    rev = events(res["run_id"], "plan.revised")
    assert len(rev) == 1 and rev[0]["round"] == 1 and rev[0]["source"] == "fallback" and rev[0]["previous"] == ["s1", "s2"]
    assert "재계획으로 대체한 단계" in res["run"]["result"]


# ── C18-05 RAG ──
def test_embeddings_endpoint_and_data_class_routing(client, H):
    res = client.post("/v1/embeddings", json={"input": ["주문 API 지연", "결제 인증서"]},
                      headers={**H["user"], "X-AgentNexus-Data-Class": "confidential"})
    assert res.status_code == 200 and res.headers["x-agentnexus-model"] == "embed-local"
    data = res.json()["data"]
    assert len(data) == 2 and len(data[0]["embedding"]) == 256
    assert client.post("/v1/embeddings", json={"model": "fast-commercial", "input": "x"},
                       headers=H["user"]).status_code == 404  # 임베딩 미지원 모델


def test_knowledge_base_search_and_rag_retriever(client, H):
    hits = client.post("/api/v1/knowledge-bases/ops-runbooks/search", json={"query": "orders-api p95 지연 경보 Dynatrace 문제",
                                                                             "top_k": 3}, headers=H["user"]).json()["items"]
    assert hits[0]["title"] == "주문 API 지연 대응 런북" and hits[0]["score"] >= hits[1]["score"] >= hits[2]["score"]
    kb = client.post("/api/v1/knowledge-bases", json={"id": "team-x-kb", "name": "팀 X", "team": "team-x"},
                     headers=H["admin"])
    assert kb.status_code == 201
    doc = client.post("/api/v1/knowledge-bases/team-x-kb/documents", json={
        "title": "온콜 교대", "text": "온콜 교대는 매주 월요일 10시. 인수인계는 장애 티켓 링크로 한다."}, headers=H["admin"])
    assert doc.status_code == 201 and doc.json()["chunks"] == 1
    assert client.post("/api/v1/knowledge-bases/team-x-kb/search", json={"query": "온콜"},
                       headers=H["user"]).status_code == 404  # 다른 팀 범위는 보이지 않음
    _, rid = ask(client, H["user"], "demo-rag", "결제 인증서 갱신 절차 알려줘")
    run = wait_run(client, H["user"], rid)
    assert run["status"] == "succeeded", run
    st = {s["name"]: s for s in run["steps"]}
    assert st["s1"]["tool"] == "rag:ops-runbooks" and st["s2"]["status"] == "skipped"  # '이슈' 없음 → Jira 생략
    assert run["citations"][0]["title"] == "결제 샌드박스 인증서 갱신 절차"


# ── C18-06 첨부 (PDF·docx·xlsx·이미지) ──
def _docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


def _xlsx() -> bytes:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"><si><t>서비스</t></si><si><t>orders-api</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData><row><c t="s"><v>0</v></c>'
                   '<c><v>42</v></c></row><row><c t="s"><v>1</v></c><c><v>7</v></c></row></sheetData></worksheet>')
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    stream = zlib.compress(f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("latin-1"))
    return (b"%PDF-1.4\n1 0 obj << /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream
            + b"\nendstream\nendobj\ntrailer << >>\n%%EOF\n")


def test_binary_attachments_extract_and_ground_answer(client, H):
    up = lambda name, data: client.post("/api/v1/chat/attachments", files={"file": (name, data)}, headers=H["user"])  # noqa: E731
    d = up("회의록.docx", _docx("배포는 금요일 18시로 확정"))
    x = up("지표.xlsx", _xlsx())
    p = up("report.pdf", _pdf("Latency p95 820ms on orders-api"))
    i = up("diagram.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    assert [r.status_code for r in (d, x, p, i)] == [201] * 4
    assert d.json()["kind"] == "document" and d.json()["text_chars"] > 5 and i.json()["kind"] == "image"
    assert up("run.exe", b"MZ").json()["code"] == "invalid_attachment"
    old = settings.attachment_max_mb
    settings.attachment_max_mb = 0
    try:
        assert up("big.txt", b"x" * 10).status_code == 400
    finally:
        settings.attachment_max_mb = old
    assert client.get(f"/api/v1/chat/attachments/{d.json()['id']}", headers=H["user2"]).status_code == 404
    _, rid = ask(client, H["user"], "tpl-qa", "첨부 요약", attachments=[
        {"attachment_id": d.json()["id"]}, {"attachment_id": x.json()["id"]}, {"attachment_id": p.json()["id"]}])
    run = wait_run(client, H["user"], rid)
    titles = {c["title"] for c in run["citations"]}
    assert {"첨부:회의록.docx", "첨부:지표.xlsx", "첨부:report.pdf"} <= titles
    assert "금요일 18시" in run["result"] or "회의록" in run["result"]
    other = client.post("/api/v1/sessions", json={"profile_id": "tpl-qa"}, headers=H["user2"]).json()["id"]
    assert client.post(f"/api/v1/sessions/{other}/messages", json={"content": "x", "attachments": [
        {"attachment_id": d.json()["id"]}]}, headers=H["user2"]).status_code == 404  # 남의 첨부


# ── C18-07 품질 라우팅 ──
def test_quality_routing_strategy(client, H):
    rule = client.post("/api/v1/gateway/routing/rules", json={
        "name": "q-test", "match_model": "q-test", "strategy": "quality",
        "targets": [{"model": "fast-commercial"}, {"model": "smart-commercial"}], "fallbacks": [], "retries": 0,
        "priority": 1}, headers=H["operator"])
    assert rule.status_code == 201, rule.text
    body = {"model": "q-test", "messages": [{"role": "user", "content": "hi"}]}
    hdr = {**H["user"], "X-AgentNexus-Data-Class": "public"}
    try:
        assert client.post("/v1/chat/completions", json=body, headers=hdr).headers["x-agentnexus-model"] == "smart-commercial"
        client.patch("/api/v1/models/fast-commercial", json={"quality": 0.95}, headers=H["operator"])
        assert client.post("/v1/chat/completions", json=body, headers=hdr).headers["x-agentnexus-model"] == "fast-commercial"
        assert client.patch("/api/v1/models/fast-commercial", json={"quality": 2}, headers=H["operator"]).status_code == 400
    finally:
        client.patch("/api/v1/models/fast-commercial", json={"quality": 0.7}, headers=H["operator"])
        client.delete(f"/api/v1/gateway/routing/rules/{rule.json()['id']}", headers=H["operator"])


# ── C18-08 플랫폼 A2A Task 영속 (다른 복제·재시작) ──
def test_platform_task_survives_lost_memory(client, H):
    send = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {
        "kind": "message", "messageId": uuid.uuid4().hex, "role": "user", "parts": [{"kind": "text", "text": "휴가 정책"}]}}}
    task = client.post("/a2a/platform/tpl-qa", json=send, headers=H["user"]).json()["result"]
    assert task["status"]["state"] == "completed"
    for srv in a2a_api._SERVERS.values():  # 다른 복제가 받았거나 재시작된 상황
        srv.tasks.clear()
        srv.ctxs.clear()
    got = client.post("/a2a/platform/tpl-qa", json={"jsonrpc": "2.0", "id": 2, "method": "tasks/get",
                                                     "params": {"id": task["id"]}}, headers=H["user"]).json()["result"]
    assert got["status"]["state"] == "completed" and any(a["name"] == "answer.md" for a in got["artifacts"])
    # 실행기가 끝을 기록하기 전에 사라진 Task — 실행(run) 결과로 완결
    with SessionLocal() as db:
        row = db.get(A2ATask, f"platform:{task['id']}")
        run_id = row.run_id
        assert run_id
        row.state, row.artifacts = "working", []
        db.commit()
    got = client.post("/a2a/platform/tpl-qa", json={"jsonrpc": "2.0", "id": 3, "method": "tasks/get",
                                                     "params": {"id": task["id"]}}, headers=H["user"]).json()["result"]
    assert got["status"]["state"] == "completed" and got["artifacts"][0]["name"] == "answer.md"
    sse = client.post("/a2a/platform/tpl-qa", json={"jsonrpc": "2.0", "id": 4, "method": "tasks/resubscribe",
                                                     "params": {"id": task["id"]}}, headers=H["user"])
    first = json.loads(next(line[6:] for line in sse.text.splitlines() if line.startswith("data: ")))
    assert first["result"]["status"]["state"] == "completed"


# ── C18-09 끊긴 실행 복구 ──
def test_stale_running_run_is_recovered(client, H):
    _, rid = ask(client, H["user"], "tpl-qa", "런북 알려줘")
    wait_run(client, H["user"], rid)
    with SessionLocal() as db:  # 실행 중 워커가 죽은 상황을 만든다: running + 오래된 생존 신호 + 진행 중 단계
        r = db.get(Run, rid)
        r.status, r.heartbeat_at, r.result = "running", utcnow() - timedelta(seconds=settings.run_stale_s + 30), None
        last = db.scalars(select(RunStep).where(RunStep.run_id == rid).order_by(RunStep.seq.desc())).first()
        last.status, last.output = "running", None
        db.commit()
    n = client.portal.call(orch.recover_stale)  # 앱 이벤트 루프에서 (운영은 예약 작업이 같은 루프에서 호출)
    assert n >= 1
    run = wait_run(client, H["user"], rid, until=("succeeded", "partial", "failed"))
    assert run["status"] == "succeeded" and run["result"]
    assert events(rid, "run.recovered")[0]["steps_reset"] == 1
    assert client.portal.call(orch.recover_stale) == 0  # 신선한 생존 신호는 건드리지 않는다


# ── C18-10 예약 작업 리스·경보·보존 ──
def test_scheduler_lease_and_jobs(client, H):
    assert scheduler.lease("t18", 30, owner="w1") and not scheduler.lease("t18", 30, owner="w2")
    assert scheduler.lease("t18", 30, owner="w1")  # 보유자는 갱신
    with SessionLocal() as db:
        db.get(Setting, "lease.t18").updated_at = utcnow() - timedelta(seconds=60)
        db.commit()
    assert scheduler.lease("t18", 30, owner="w2")  # 만료 후 다른 워커가 인수
    with SessionLocal() as db:
        db.add(AlertRule(id="al-18", name="항상 발생", metric="qps", op=">=", threshold=0, window_s=300, severity="info"))
        db.commit()
    fired = scheduler.evaluate_alerts()["fired"]
    assert any(f["id"] == "al-18" for f in fired)
    with SessionLocal() as db:
        assert db.scalars(select(Notification).where(Notification.title == "알림: 항상 발생")).first() is not None
        db.delete(db.get(AlertRule, "al-18"))
        db.commit()
    out = scheduler.run_retention()
    assert "deleted" in out
    client.portal.call(scheduler.tick, time.monotonic() + 10**6, [0.0])  # 리스 획득 → 경보·복구·보존 한 주기
    assert {"alerts", "recovery", "retention"} <= set(scheduler.LAST)


# ── C18-11 Custom MCP 서버 기동 시 자동 등록 ──
@pytest.mark.anyio
async def test_template_self_registers_on_startup(client, H, tmp_path):
    files = mcp_template.render("auto-reg", description="자동 등록 시험")
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    compile(files["server.py"], "server.py", "exec")
    spec = importlib.util.spec_from_file_location("anx_register_t", tmp_path / "anx_register.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    token = H["dev"]["Authorization"].split()[1]
    transport = httpx.ASGITransport(app=client.app)
    path = str(tmp_path / "server.json")
    assert await mod.register("http://testserver", token, path, transport=transport) == "published"
    assert await mod.register("http://testserver", token, path, transport=transport) == "published"  # 멱등
    doc = json.loads((tmp_path / "server.json").read_text())
    doc["description"] = "내용 변경, 버전 그대로"
    (tmp_path / "server.json").write_text(json.dumps(doc))
    assert await mod.register("http://testserver", token, path, transport=transport) == "conflict"
    assert await mod.register_on_startup() is None  # 환경 변수 없으면 아무것도 하지 않음
    pending = client.get("/api/v1/registry/approvals", headers=H["operator"]).json()["items"]
    assert any(r["item"]["name"] == "io.agentnexus.custom/auto-reg" for r in pending)
