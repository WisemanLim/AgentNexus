"""Epic 22 — 채팅·오케스트레이션 편집기 깊이 (SCR-C01~C07 · D01~D09).

응답 메타·피드백·다시 생성·편집 후 재전송 · 대화 이력 검색·미리보기·내보내기·삭제 · 승인 맥락·세션 기억·만료 ·
부분 결과로 계속 · 드라이런·버전 실행·게시 전 점검 · 코드만 저장·게시본 비교·ADK 보기 · 노드 설정(타임아웃·429·합류·재계획) ·
실패 시뮬레이션 · Host 세션 재연결·종료.
"""
import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.http import register_mount
from app.db import SessionLocal, utcnow
from app.models import A2ATask, Approval, McpSession, Message, RateLimit, RunStep, ToolRule, UsageRecord
from app.services import scheduler
from test.test_orchestration import ask, wait_run

DONE = ("succeeded", "partial", "failed", "waiting_approval", "canceled")


def server_json(name: str, ns: str, tools: list[str]) -> dict:
    return {"$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json", "name": name,
            "description": "Epic 22 시험 서버", "version": "1.0.0",
            "remotes": [{"type": "streamable-http", "url": f"http://{ns}.test/mcp"}],
            "_meta": {"auth": {"type": "none"}, "tools": tools, "license": "MIT"}}


def _mock_mcp(delay_s: float = 0) -> httpx.MockTransport:
    tools = [{"name": "lookup", "description": "조회", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
             {"name": "quick", "description": "빠른 조회", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]

    async def handle(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}")
        if "id" not in body:
            return httpx.Response(202)
        m = body["method"]
        if m == "tools/call" and body["params"]["name"] == "lookup" and delay_s:
            await asyncio.sleep(delay_s)
        res = {"initialize": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "mock", "version": "1"}},
               "tools/list": {"tools": tools},
               "tools/call": {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"result": [{"id": "r1", "title": "결과"}]},
                              "isError": False}}.get(m)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": res})
    return httpx.MockTransport(handle)


@pytest.fixture(scope="module", autouse=True)
def ready(client, H):
    for sid in ("jira", "confluence", "dynatrace"):
        client.post(f"/api/v1/mcp-servers/{sid}/refresh", headers=H["operator"])
    register_mount("http://e22slow.test", async_transport=_mock_mcp(delay_s=2))
    item = client.post("/v0/publish", json=server_json("io.e22/slow", "e22slow", ["lookup", "quick"]), headers=H["dev"]).json()
    client.post(f"/v1/catalog/{item['item']['id']}/approve", json={"note": "e22"}, headers=H["operator"])
    reg = client.post(f"/v1/catalog/{item['item']['id']}/register", json={"namespace": "e22slow"}, headers=H["operator"])
    assert reg.status_code == 200 and reg.json()["instance"]["refresh"]["health"] == "healthy", reg.text


def profile(client, H, pid: str, yaml_text: str, publish: bool = True) -> dict:
    res = client.post("/api/v1/agents", json={"name": pid, "yaml": yaml_text, "id": pid}, headers=H["dev"])
    assert res.status_code == 201, res.text
    if publish:
        pub = client.post(f"/api/v1/agents/{pid}/publish", headers=H["dev"])
        assert pub.status_code == 200, pub.text
    return res.json()


# ── C22-01 응답 메타 · 피드백 · 다시 생성 · 편집 후 재전송 · 이력 검색·미리보기·내보내기·삭제 (C03·C07) ──
def test_response_meta_feedback_branches_and_history(client, H):
    sid, rid = ask(client, H["user"], "tpl-qa", "장애 대응 런북 알려줘")
    run = wait_run(client, H["user"], rid)
    assert run["status"] == "succeeded"
    meta = run["meta"]
    assert meta["tokens_out"] > 0 and meta["first_token_ms"] is not None and meta["local_only"] in (True, False)
    assert {n["step"] for n in meta["nodes"]} == {s["name"] for s in run["steps"]}
    summ = next(n for n in meta["nodes"] if n["node"] == "summarize")
    assert summ["tokens"] > 0 and summ["latency_ms"] is not None  # 노드별 비용·지연 (step_id)
    assert meta["sources"]["retrieved"] >= meta["sources"]["cited"] >= 1
    sess = client.get(f"/api/v1/sessions/{sid}", headers=H["user"]).json()
    ans = next(m for m in sess["messages"] if m["role"] == "assistant")
    up = client.post(f"/api/v1/sessions/{sid}/messages/{ans['id']}/feedback", json={"rating": "up"}, headers=H["user"]).json()
    assert up["feedback"]["rating"] == "up"
    assert client.post(f"/api/v1/sessions/{sid}/messages/{ans['id']}/feedback", json={},
                       headers=H["user"]).json()["feedback"] is None  # 다시 누르면 취소
    assert client.post(f"/api/v1/sessions/{sid}/messages/{ans['id']}/feedback", json={"rating": "down"},
                       headers=H["user2"]).status_code == 404  # 남의 세션
    regen = client.post(f"/api/v1/sessions/{sid}/messages/{ans['id']}/regenerate", headers=H["user"]).json()
    assert wait_run(client, H["user"], regen["run_id"])["status"] == "succeeded"
    first_user = next(m for m in sess["messages"] if m["role"] == "user")
    edit = client.post(f"/api/v1/sessions/{sid}/messages/{first_user['id']}/edit", json={"content": "결제 장애 런북 알려줘"},
                       headers=H["user"]).json()
    assert wait_run(client, H["user"], edit["run_id"])["request"] == "결제 장애 런북 알려줘"
    msgs = client.get(f"/api/v1/sessions/{sid}", headers=H["user"]).json()["messages"]
    branches = [m for m in msgs if m["branch_of"] == first_user["id"]]
    assert {p["type"] for m in branches for p in m["parts"]} >= {"regenerate", "edited"}
    # 이력 — 본문 검색·필터·미리보기·내보내기·삭제(내용 제거)
    hit = client.get("/api/v1/sessions", params={"q": "결제 장애 런북"}, headers=H["user"]).json()["items"]
    assert sid in [s["id"] for s in hit] and next(s for s in hit if s["id"] == sid)["messages"] >= 6
    assert sid not in [s["id"] for s in client.get("/api/v1/sessions", params={"source": "api"}, headers=H["user"]).json()["items"]]
    assert sid in [s["id"] for s in client.get("/api/v1/sessions", params={"profile_id": "tpl-qa", "days": 1},
                                               headers=H["user"]).json()["items"]]
    summary = client.get(f"/api/v1/sessions/{sid}/summary", headers=H["user"]).json()
    assert summary["profile_name"] and summary["messages"] >= 6 and summary["traces"] >= 1 and "last_answer" in summary
    md = client.get(f"/api/v1/sessions/{sid}/export", headers=H["user"])
    assert md.headers["content-type"].startswith("text/markdown") and "결제 장애 런북" in md.text
    gone = client.delete(f"/api/v1/sessions/{sid}", headers=H["user"]).json()
    assert gone["messages"] >= 6
    with SessionLocal() as db:
        assert all(m.content == "" for m in db.scalars(select(Message).where(Message.session_id == sid)))
    assert client.get(f"/api/v1/sessions/{sid}", headers=H["user"]).status_code == 404


# ── C22-02 승인 맥락 · '다시 묻지 않기'(중위험만) · Executor 노드 승인 정책 (C04·D05) ──
def test_approval_context_remember_and_node_policy(client, H):
    with SessionLocal() as db:
        r = db.scalars(select(ToolRule).where(ToolRule.server_id == "jira", ToolRule.tool == "add_comment")).first()
        saved = (r.risk, r.approval_required)
        r.risk, r.approval_required = "medium", False
        db.commit()
    try:
        profile(client, H, "e22-comment", """
planner: {mode: template, max_steps: 2}
workers:
  executor: {tools: [jira.add_comment], approval: {approver: requester, ttl_s: 120, medium: [jira.add_comment]}}
default_plan:
  - {id: s1, agent: executor, tool: jira.add_comment, args: {key: ORI-1440, body: "{{request}}"}}
""")
        sid, rid = ask(client, H["user"], "e22-comment", "확인했습니다")
        run = wait_run(client, H["user"], rid)
        assert run["status"] == "waiting_approval"  # 중위험이지만 노드 정책으로 사람 승인
        ap = client.get(f"/api/v1/approvals/{run['approvals'][0]['id']}", headers=H["user"]).json()
        ctx = ap["context"]
        assert ctx["source"]["profile"] == "e22-comment" and ctx["source"]["step"] == "1/1" and ctx["source"]["trace_id"]
        assert ctx["identity"]["audience"] == "mcp:jira" and "OBO" in ctx["identity"]["method"]
        assert ctx["remember_allowed"] is True and 0 < ctx["expires_in_s"] <= 120
        assert {c["key"] for c in ctx["checks"]} == {"allowlist", "schema", "approval_required", "policy"}
        ok = client.post(f"/api/v1/approvals/{ap['id']}/approve", json={"note": "ok", "remember": True}, headers=H["user"])
        assert ok.status_code == 200
        done = ("succeeded", "partial", "failed")  # 승인 직후 재개 전까지는 아직 waiting_approval (D19-③ 경합 제거)
        assert wait_run(client, H["user"], rid, until=done)["status"] == "succeeded"
        rid2 = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "한 번 더"}, headers=H["user"]).json()["run_id"]
        assert wait_run(client, H["user"], rid2)["status"] == "succeeded"  # 같은 세션 — 다시 묻지 않음
        with SessionLocal() as db:
            assert db.scalars(select(Approval).where(Approval.run_id == rid2)).first() is None
        _, rid3 = ask(client, H["user"], "e22-comment", "새 세션")
        assert wait_run(client, H["user"], rid3)["status"] == "waiting_approval"  # 다른 세션이면 다시 묻는다
    finally:
        with SessionLocal() as db:
            r = db.scalars(select(ToolRule).where(ToolRule.server_id == "jira", ToolRule.tool == "add_comment")).first()
            r.risk, r.approval_required = saved
            db.commit()


# ── C22-03 고위험은 기억 불가 · 승인 만료 → 자동 거부 → 부분 결과 (C04 ⑥⑦ · ST-06) ──
def test_high_risk_no_remember_and_expiry(client, H):
    profile(client, H, "e22-page", """
planner: {mode: template, max_steps: 3}
workers:
  retriever: {tools: [jira.search_issues]}
  executor: {tools: [confluence.create_page], approval: {ttl_s: 60}}
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues, args: {query: "{{request}}"}}
  - {id: s2, agent: executor, tool: confluence.create_page, depends_on: [s1], args: {space: ORION, title: e22, body: "{{s1.text}}"}}
""")
    _, rid = ask(client, H["user"], "e22-page", "결제 지연")
    run = wait_run(client, H["user"], rid)
    assert run["status"] == "waiting_approval"
    apid = run["approvals"][0]["id"]
    bad = client.post(f"/api/v1/approvals/{apid}/approve", json={"remember": True}, headers=H["user"])
    assert bad.status_code == 400 and bad.json()["code"] == "remember_not_allowed"
    with SessionLocal() as db:
        db.get(Approval, apid).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert scheduler.approval_jobs() >= 1
    done = wait_run(client, H["user"], rid, until=("succeeded", "partial", "failed"))
    assert done["status"] == "partial"
    s2 = next(s for s in done["steps"] if s["name"] == "s2")
    assert s2["status"] == "rejected" and "만료" in s2["error"]


# ── C22-04 드라이런·버전 실행·노드 결과·이전 실행·게시 전 점검 (D08) ──
def test_dry_run_versions_and_prepublish_checks(client, H):
    pr = profile(client, H, "e22-dry", """
planner: {mode: template, max_steps: 3}
workers:
  retriever: {tools: [jira.search_issues]}
  executor: {tools: [confluence.create_page]}
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues, args: {query: "{{request}}"}}
  - {id: s2, agent: executor, tool: confluence.create_page, depends_on: [s1], args: {space: ORION, title: dry, body: x}}
""")
    res = client.post("/api/v1/agents/e22-dry/test", json={"request": "결제"}, headers=H["dev"]).json()
    assert res["status"] == "succeeded" and res["run"]["dry_run"] is True
    s2 = next(s for s in res["run"]["steps"] if s["name"] == "s2")
    assert s2["output"]["dry_run"] is True and s2["output"]["would_call"] == "confluence.create_page"
    meta = res["run"]["meta"]
    assert meta["dry_run_calls"][0]["tool"] == "confluence.create_page"
    with SessionLocal() as db:  # 쓰기 0건 — 승인·도구 호출 기록 없음
        assert db.scalars(select(Approval).where(Approval.run_id == res["run_id"])).first() is None
        assert db.scalars(select(UsageRecord).where(UsageRecord.run_id == res["run_id"],
                                                    UsageRecord.target == "confluence.create_page")).first() is None
    # 초안 수정 후 게시 버전 v1 로 실행
    g = pr["graph"]
    g["default_plan"].append({"id": "s3", "agent": "retriever", "tool": "jira.search_issues", "args": {"query": "x"}})
    assert client.put("/api/v1/agents/e22-dry/graph", json={"graph": g}, headers=H["dev"]).status_code == 200
    v1 = client.post("/api/v1/agents/e22-dry/test", json={"request": "결제", "version": 1}, headers=H["dev"]).json()
    assert v1["run"]["profile_version"] == 1 and len(v1["run"]["steps"]) == 2
    assert client.post("/api/v1/agents/e22-dry/test", json={"request": "x", "version": 9}, headers=H["dev"]).status_code == 404
    client.post("/api/v1/agents/e22-dry/test", json={"request": "결제"}, headers=H["dev"])
    runs = client.get("/api/v1/agents/e22-dry/test-runs", headers=H["dev"]).json()["items"]
    assert len(runs) == 3 and {r["version"] for r in runs} >= {1} and runs[0]["nodes"]
    checks = client.get("/api/v1/agents/e22-dry/checks", headers=H["dev"]).json()
    keys = {c["key"]: c for c in checks["items"]}
    assert keys["validation"]["ok"] and keys["tests"]["ok"] and keys["approval"]["ok"] and checks["publishable"]
    hl = client.get("/api/v1/agents/e22-dry/health", headers=H["dev"]).json()
    assert hl["ok"] and {i["id"] for i in hl["items"]} >= {"jira", "confluence"}


# ── C22-05 코드만 저장·게시 차단·되돌리기 · 게시본 비교 · ADK 보기 · 검증(쓰기 도구·노드 설정) · 목록·복제·보관 (D01·D07) ──
def test_code_only_diff_adk_validation_and_list(client, H):
    pr = profile(client, H, "e22-code", """
planner: {mode: template, max_steps: 2}
workers: {retriever: {tools: [jira.search_issues]}, summarize: {model: domain-local}}
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues}
  - {id: s2, agent: summarize, depends_on: [s1]}
""")
    broken = pr["yaml"].rstrip("\n") + "\n- {id: s3, agent: summarize, depends_on: [ghost]}\n"
    bad = client.put("/api/v1/agents/e22-code/graph", json={"yaml": broken}, headers=H["dev"])
    assert bad.status_code == 422
    co = client.put("/api/v1/agents/e22-code/graph", json={"yaml": broken, "allow_invalid": True}, headers=H["dev"]).json()
    assert co["saved"] == "code_only" and co["sync"] is False and co["errors"][0]["line"]
    assert client.post("/api/v1/agents/e22-code/publish", headers=H["dev"]).json()["code"] == "code_only_draft"
    d = client.get("/api/v1/agents/e22-code/diff", headers=H["dev"]).json()
    assert d["base_version"] == 1 and {"op": "+", "path": "default_plan.s3", "value": "summarize · summarize"} in d["changes"]
    assert client.get("/api/v1/agents/e22-code/code", headers=H["dev"]).json()["code"] == broken
    adk = client.get("/api/v1/agents/e22-code/code", params={"format": "adk"}, headers=H["dev"]).json()
    assert "root_agent = " in adk["code"] and "McpToolset" in adk["code"] and adk["editable"] is False
    assert client.post("/api/v1/agents/e22-code/code/revert", headers=H["dev"]).json()["sync"] is True
    assert client.post("/api/v1/agents/e22-code/publish", headers=H["dev"]).status_code == 200
    # 검증 — Retriever 쓰기 도구(정확 이름)는 오류, 와일드카드는 경고 · 노드 설정 범위
    v = client.post("/api/v1/agents/e22-code/validate", json={"yaml": """
planner: {mode: template, max_steps: 2, max_depth: 1, replan: {triggers: [nope]}}
workers: {retriever: {tools: [jira.transition_issue, "confluence.*"], top_k: 99}, summarize: {citation_format: bad}}
fanout: {join: required}
default_plan:
  - {id: s1, agent: retriever, tool: jira.transition_issue}
  - {id: s2, agent: summarize, depends_on: [s1]}
"""}, headers=H["dev"]).json()
    paths = {e["path"] for e in v["errors"]}
    assert {"planner.max_depth", "planner.replan.triggers", "workers.retriever.top_k", "workers.summarize.citation_format",
            "fanout.required"} <= paths
    v2 = client.post("/api/v1/agents/e22-code/validate", json={"yaml": """
planner: {mode: template, max_steps: 2}
workers: {retriever: {tools: [jira.transition_issue, "confluence.*"]}}
default_plan:
  - {id: s1, agent: retriever, tool: jira.transition_issue}
"""}, headers=H["dev"]).json()
    assert any("Retriever" in e["message"] and "쓰기" in e["message"] for e in v2["errors"])
    assert any("와일드카드" in w["message"] for w in v2["warnings"])
    # 목록 필터·복제·보관·복원
    assert "e22-code" in [p["id"] for p in client.get("/api/v1/agents", params={"status": "published", "q": "e22-code"},
                                                      headers=H["dev"]).json()["items"]]
    cl = client.post("/api/v1/agents/e22-code/clone", json={"name": "e22-code 복제"}, headers=H["dev"]).json()
    assert cl["status"] == "draft" and cl["graph"]["default_plan"][0]["id"] == "s1"
    client.delete(f"/api/v1/agents/{cl['id']}", headers=H["dev"])
    assert cl["id"] in [p["id"] for p in client.get("/api/v1/agents", params={"status": "archived"}, headers=H["dev"]).json()["items"]]
    assert client.post(f"/api/v1/agents/{cl['id']}/restore", headers=H["dev"]).json()["status"] == "draft"


# ── C22-06 노드 타임아웃 · 지수 백오프 · 도구 429 대기 후 재시도 · 최대 동시 · 합류 N 중 M · 필수 노드 (D06) ──
def test_timeouts_429_and_join_rules(client, H):
    profile(client, H, "e22-join", """
planner: {mode: template, max_steps: 4}
workers: {retriever: {tools: ["e22slow.*", jira.search_issues]}, summarize: {model: domain-local}}
fanout: {parallel: true, max_concurrency: 2, join: n_of_m, n: 1, node_timeout_s: 1}
retry: {max_attempts: 2, backoff_ms: 50, backoff: exponential}
default_plan:
  - {id: slow, agent: retriever, tool: e22slow.lookup}
  - {id: fast, agent: retriever, tool: jira.search_issues, args: {query: "{{request}}"}}
  - {id: sum, agent: summarize, depends_on: [slow, fast]}
""")
    res = client.post("/api/v1/agents/e22-join/test", json={"request": "결제"}, headers=H["dev"]).json()
    st = {s["name"]: s for s in res["run"]["steps"]}
    assert st["slow"]["status"] == "failed" and "노드 타임아웃" in st["slow"]["error"] and st["slow"]["attempts"] == 2
    assert st["sum"]["status"] == "succeeded" and res["status"] == "partial"  # N 중 1 — 합류 성립
    # 필수 노드 실패 → 전체 실패
    profile(client, H, "e22-req", """
planner: {mode: template, max_steps: 4}
workers: {retriever: {tools: ["e22slow.*", jira.search_issues]}, summarize: {model: domain-local}}
fanout: {join: required, required: [slow], node_timeout_s: 1}
retry: {max_attempts: 1}
default_plan:
  - {id: slow, agent: retriever, tool: e22slow.lookup}
  - {id: fast, agent: retriever, tool: jira.search_issues}
  - {id: sum, agent: summarize, depends_on: [slow, fast]}
""")
    res = client.post("/api/v1/agents/e22-req/test", json={"request": "결제"}, headers=H["dev"]).json()
    st = {s["name"]: s for s in res["run"]["steps"]}
    assert res["status"] == "failed" and st["sum"]["status"] == "skipped" and "필수 노드 실패" in st["sum"]["error"]
    sim = client.post("/api/v1/agents/e22-req/simulate", json={"timeout": ["slow"]}, headers=H["dev"]).json()
    assert sim["status"] == "failed" and "필수 노드" in sim["message"]
    sim2 = client.post("/api/v1/agents/e22-join/simulate", json={"failed": ["fast"]}, headers=H["dev"]).json()
    assert sim2["status"] == "partial" and "MSG-014" in sim2["message"]
    # 도구 429 — 재시도 가능 시각까지 기다렸다 성공
    with SessionLocal() as db:
        db.add(RateLimit(id="rl_e22", name="e22 quick", scope_type="user", scope="*", target_type="tool",
                         target="e22slow.quick", rps=0.5, burst=1))
        db.commit()
    try:
        profile(client, H, "e22-429", """
planner: {mode: template, max_steps: 3}
workers: {retriever: {tools: ["e22slow.*"]}}
fanout: {parallel: false}
default_plan:
  - {id: a, agent: retriever, tool: e22slow.quick}
  - {id: b, agent: retriever, tool: e22slow.quick}
""")
        res = client.post("/api/v1/agents/e22-429/test", json={"request": "x", "timeout_s": 30}, headers=H["dev"]).json()
        assert res["status"] == "succeeded", res["run"]["steps"]
        rl = res["run"]["meta"]["rate_limited"]
        assert rl and rl[0]["action"] == "wait" and rl[0]["retry_after"] <= 10
    finally:
        with SessionLocal() as db:
            db.delete(db.get(RateLimit, "rl_e22"))
            db.commit()


# ── C22-07 재계획 조건(0건) · Planner 폴백 모델·분해 프롬프트 · 배정 대상 (D03) ──
def test_replan_on_empty_and_planner_fallback(client, H):
    profile(client, H, "e22-empty", """
planner:
  mode: template
  max_steps: 3
  replan: {max_rounds: 1, triggers: [empty], fallback_plan: [{id: alt, agent: retriever, tool: confluence.search_pages, args: {query: "런북"}}]}
workers: {retriever: {tools: [jira.search_issues, confluence.search_pages]}}
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues, args: {query: x, status: Nope}}
""")
    res = client.post("/api/v1/agents/e22-empty/test", json={"request": "런북"}, headers=H["dev"]).json()
    st = {s["name"]: s for s in res["run"]["steps"]}
    assert st["s1"]["status"] == "replanned" and st["r1_alt"]["status"] == "succeeded" and res["status"] == "succeeded"
    profile(client, H, "e22-llm", """
planner: {mode: llm, model: no-such-model, fallback_model: domain-local, max_steps: 2, prompt: "요청 {request} 를 분해. 도구 {tools}",
          assign: [retriever]}
workers: {retriever: {tools: [jira.search_issues]}}
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues}
""")
    res = client.post("/api/v1/agents/e22-llm/test", json={"request": "결제"}, headers=H["dev"]).json()
    ev = client.get(f"/api/v1/runs/{res['run_id']}/events", headers=H["dev"]).json()["items"]
    plan = next(e for e in ev if e["type"] == "plan")["data"]
    assert plan["fallback_model"] == "domain-local" and res["status"] in ("succeeded", "partial")


# ── C22-08 부분 결과로 계속 (C06 ④) ──
def test_continue_with_partial_results(client, H):
    profile(client, H, "e22-fail", """
planner: {mode: template, max_steps: 3}
workers: {retriever: {tools: ["e22slow.*", jira.search_issues]}, summarize: {model: domain-local}}
on_failure: fail
retry: {max_attempts: 1}
default_plan:
  - {id: slow, agent: retriever, tool: e22slow.lookup, timeout_s: 1}
  - {id: fast, agent: retriever, tool: jira.search_issues}
  - {id: sum, agent: summarize, depends_on: [fast]}
""")
    sid, rid = ask(client, H["user"], "e22-fail", "결제")
    run = wait_run(client, H["user"], rid, until=("succeeded", "partial", "failed"))
    assert run["status"] == "failed"
    assert client.post(f"/api/v1/runs/{rid}/continue", headers=H["user2"]).status_code == 404
    assert client.post(f"/api/v1/runs/{rid}/continue", headers=H["user"]).json()["result"] == "resumed"
    run = wait_run(client, H["user"], rid, until=("succeeded", "partial", "failed"))
    assert run["status"] == "partial" and next(s for s in run["steps"] if s["name"] == "sum")["status"] == "succeeded"
    assert client.post(f"/api/v1/runs/{rid}/continue", headers=H["user"]).json()["result"] == "accepted"
    assert client.get(f"/api/v1/runs/{rid}", headers=H["user"]).json()["meta"]["accepted_partial"] is True


# ── C22-09 Host 세션 KPI·재연결·종료 (D09) · 채팅 프로파일 준비 상태 (C01) · Artifact → 첨부 (C05) ──
def test_host_sessions_readiness_and_artifact_attach(client, H):
    _, rid = ask(client, H["user"], "tpl-qa", "장애 대응 런북")
    wait_run(client, H["user"], rid)
    hs = client.get("/api/v1/host/sessions", headers=H["operator"]).json()
    assert {"active", "servers", "reconnecting", "consents"} <= set(hs["kpi"])
    row = next(i for i in hs["items"] if i["run_id"] == rid)
    assert row["state"] == "closed" and row["events"][0]["type"] == "opened" and row["transport"] == "HTTP·GW"
    with SessionLocal() as db:  # 열린 업스트림 세션 하나를 만들어 재연결·종료
        db.add(McpSession(id="e22-up", kind="upstream", server_id="jira", principal_sub="usr_user", protocol_version="2025-11-25"))
        db.commit()
    rc = client.post("/api/v1/host/sessions/e22-up/reconnect", headers=H["operator"]).json()
    assert rc["reconnects"] == 1 and rc["state"] == "reconnecting"
    assert client.delete("/api/v1/host/sessions/e22-up", headers=H["operator"]).json() == {"closed": "e22-up"}
    assert client.delete("/api/v1/host/sessions/e22-up", headers=H["operator"]).json()["code"] == "session_closed"
    assert client.post("/api/v1/host/sessions/e22-up/reconnect", headers=H["user"]).status_code == 403
    profile(client, H, "e22-ghost", """
planner: {mode: template, max_steps: 1}
workers: {retriever: {tools: [ghost.search]}}
default_plan:
  - {id: s1, agent: retriever, tool: ghost.search}
""")
    cards = {c["id"]: c for c in client.get("/api/v1/chat/profiles", headers=H["user"]).json()["items"]}
    assert cards["tpl-qa"]["ready"] and cards["tpl-qa"]["sample"]
    assert cards["e22-ghost"]["ready"] is False and cards["e22-ghost"]["missing"] == ["MCP ghost"]
    with SessionLocal() as db:
        db.add(A2ATask(id="observability-analyst:e22t", agent_id="observability-analyst", remote_task_id="e22t",
                       direction="outbound", principal_sub="usr_user", state="completed",
                       artifacts=[{"artifactId": "a1", "name": "report", "parts": [{"kind": "text", "text": "# 보고서\n지연 원인"}]}]))
        db.commit()
    att = client.post("/api/v1/chat/attachments/from-artifact", json={"task_id": "observability-analyst:e22t", "artifact_id": "a1"},
                      headers=H["user"])
    assert att.status_code == 201 and att.json()["name"] == "report.md"
    assert client.post("/api/v1/chat/attachments/from-artifact", json={"task_id": "observability-analyst:e22t", "artifact_id": "a1"},
                       headers=H["user2"]).status_code == 404
    files = client.get("/api/v1/chat/files", headers=H["user"]).json()["items"]
    assert any(f["kind"] == "attachment" and f["name"] == "report.md" for f in files)


# ── C22-10 재개 예약이 유실된 대기 실행 복구 (prod 리허설에서 발견 — 이벤트 루프 없는 프로세스의 승인 만료) ──
def test_orphaned_wait_is_recovered(client, H):
    from app.models import Run
    from app.services import orchestrator as orch
    _, rid = ask(client, H["user"], "e22-page", "결제 지연")
    assert wait_run(client, H["user"], rid)["status"] == "waiting_approval"
    with SessionLocal() as db:  # 승인 결과는 반영됐지만(거부) 재개 예약이 사라진 상태를 만든다
        for st in db.scalars(select(RunStep).where(RunStep.run_id == rid, RunStep.status == "waiting_approval")):
            st.status, st.error = "rejected", "사람 승인 만료 (system)"
        db.commit()
        assert db.get(Run, rid).status == "waiting_approval"
    assert client.portal.call(orch.recover_stale) >= 1
    assert wait_run(client, H["user"], rid, until=("succeeded", "partial", "failed"))["status"] == "partial"
