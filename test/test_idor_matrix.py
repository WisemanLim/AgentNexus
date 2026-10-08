"""Epic 48 — 객체 수준 인가 전수 점검 (D19-①): 일반 사용자가 닿는 경로 매개변수 엔드포인트를 두 사용자로 훑는다.

`user`(team-biz)가 만든 객체를 `user2`(team-agents)가 읽기·바꾸기·지우기 시도 → 403/404 여야 한다(존재 비노출 우선).
`test_every_user_route_is_classified` 는 새 라우트가 생기면 이 행렬(또는 공유 자원 목록)에 넣도록 강제한다.
"""
import uuid
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute

from app.core.auth import ROLE_PERMS
from app.db import SessionLocal, utcnow
from app.models import Approval, PersonalToken
from test.test_a2a import AG, call, msg
from test.test_orchestration import ask, wait_run

OWNER, OTHER = "user", "user2"
MCP_HDR = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


@pytest.fixture(scope="module")
def objs(client, H):
    sid, rid = ask(client, H[OWNER], "tpl-qa", "휴가 정책")
    wait_run(client, H[OWNER], rid)
    mid = client.get(f"/api/v1/sessions/{sid}", headers=H[OWNER]).json()["messages"][0]["id"]
    aid = client.post("/api/v1/chat/attachments", files={"file": ("e48.txt", b"e48 secret note")}, headers=H[OWNER]).json()["id"]
    tok = client.post("/api/v1/me/tokens", json={"name": "e48"}, headers=H[OWNER]).json()["id"]
    task = call(client, H[OWNER], "message/send", {"message": msg("orders-api 장애 분석")}).json()["result"]
    a2a_tid = f"{AG}:{task['id']}"
    with SessionLocal() as db:
        ap = Approval(id="apr_e48_" + uuid.uuid4().hex[:8], kind="tool_call", status="pending", requester_sub="usr_user",
                      server_id="jira", tool="add_comment", target="jira.add_comment", args={}, args_hash="h", risk="high",
                      approver="requester", reason="e48", expires_at=utcnow() + timedelta(minutes=10))
        db.add(ap)
        db.commit()
        apid = ap.id
    init = client.post("/mcp/jira", headers={**H[OWNER], **MCP_HDR}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "e48", "version": "1"}}})
    mcp_sid = init.headers.get("mcp-session-id")
    kb = client.post("/api/v1/knowledge-bases", json={"id": "e48-kb", "name": "E48 팀 전용", "team": "team-biz"}, headers=H["admin"])
    assert kb.status_code in (201, 409), kb.text
    return {"sid": sid, "rid": rid, "mid": mid, "aid": aid, "tok": tok, "a2a": a2a_tid, "ap": apid, "mcp": mcp_sid, "kb": "e48-kb"}


def cases(o: dict) -> list[tuple[str, str, dict | None]]:
    s, r, m = o["sid"], o["rid"], o["mid"]
    return [
        ("GET", f"/api/v1/sessions/{s}", None), ("PATCH", f"/api/v1/sessions/{s}", {"title": "e48"}),
        ("GET", f"/api/v1/sessions/{s}/export", None), ("GET", f"/api/v1/sessions/{s}/summary", None),
        ("POST", f"/api/v1/sessions/{s}/messages", {"content": "끼어들기"}),
        ("POST", f"/api/v1/sessions/{s}/messages/{m}/edit", {"content": "고쳐 쓰기"}),
        ("POST", f"/api/v1/sessions/{s}/messages/{m}/feedback", {"rating": "down"}),
        ("POST", f"/api/v1/sessions/{s}/messages/{m}/regenerate", {}),
        ("DELETE", f"/api/v1/sessions/{s}", None),
        ("GET", f"/api/v1/runs/{r}", None), ("GET", f"/api/v1/runs/{r}/events", None),
        ("POST", f"/api/v1/runs/{r}/cancel", {}), ("POST", f"/api/v1/runs/{r}/continue", {}),
        ("POST", f"/api/v1/runs/{r}/retry", {}), ("POST", f"/api/v1/runs/{r}/input", {"step": "x", "text": "y"}),
        ("GET", f"/api/v1/audit/replay/{s}", None), ("POST", f"/api/v1/audit/replay/{r}/rerun", {}),
        ("GET", f"/api/v1/chat/attachments/{o['aid']}", None),
        ("DELETE", f"/api/v1/me/tokens/{o['tok']}", None),
        ("GET", f"/api/v1/a2a/tasks/{o['a2a']}", None), ("GET", f"/api/v1/a2a/tasks/{o['a2a']}/artifacts/x", None),
        ("POST", f"/api/v1/a2a/tasks/{o['a2a']}/cancel", {}),
        ("GET", f"/api/v1/approvals/{o['ap']}", None), ("POST", f"/api/v1/approvals/{o['ap']}/approve", {}),
        ("POST", f"/api/v1/approvals/{o['ap']}/reject", {}), ("POST", f"/api/v1/approvals/{o['ap']}/extend", {}),
        ("POST", f"/api/v1/approvals/{o['ap']}/consent", {"decision": "deny"}),
        ("GET", f"/api/v1/knowledge-bases/{o['kb']}", None), ("POST", f"/api/v1/knowledge-bases/{o['kb']}/search", {"query": "x"}),
    ]


# 사람마다 다른 객체 경로 — 이 행렬로 검사한다 (실제 경로 템플릿)
OWNED = {
    "/api/v1/sessions/{sid}", "/api/v1/sessions/{sid}/export", "/api/v1/sessions/{sid}/summary", "/api/v1/sessions/{sid}/messages",
    "/api/v1/sessions/{sid}/messages/{mid}/edit", "/api/v1/sessions/{sid}/messages/{mid}/feedback",
    "/api/v1/sessions/{sid}/messages/{mid}/regenerate", "/api/v1/runs/{rid}", "/api/v1/runs/{rid}/events",
    "/api/v1/runs/{rid}/cancel", "/api/v1/runs/{rid}/continue", "/api/v1/runs/{rid}/retry", "/api/v1/runs/{rid}/input",
    "/api/v1/audit/replay/{session_id}", "/api/v1/audit/replay/{run_id}/rerun", "/api/v1/chat/attachments/{aid}",
    "/api/v1/me/tokens/{token_id}", "/api/v1/a2a/tasks/{task_id:path}", "/api/v1/a2a/tasks/{task_id:path}/artifacts/{artifact_id}",
    "/api/v1/a2a/tasks/{task_id:path}/cancel", "/api/v1/approvals/{approval_id}", "/api/v1/approvals/{approval_id}/approve",
    "/api/v1/approvals/{approval_id}/reject", "/api/v1/approvals/{approval_id}/extend", "/api/v1/approvals/{approval_id}/consent",
    "/mcp/{server_id}", "/api/v1/knowledge-bases/{kid}", "/api/v1/knowledge-bases/{kid}/search",
}
# 공유 자원·다른 시험이 맡는 경로 — 이유
SHARED = {
    "/.well-known/oauth-protected-resource/mcp/{server_id}": "공개 메타데이터",
    "/a2a/_skill/{skill}": "스킬 라우팅 — 게이트웨이 Task 소유권은 test_task_ownership",
    "/a2a/platform/rest/{path:path}": "test_task_ownership(플랫폼 REST)", "/a2a/platform/{profile_id}": "test_task_ownership",
    "/a2a/platform/{profile_id}/.well-known/agent-card.json": "공개 카드",
    "/a2a/platform/{profile_id}/rest/{path:path}": "test_task_ownership", "/a2a/push/{token}": "푸시 토큰 결합(test_review_fixes)",
    "/a2a/{agent_id}": "test_task_ownership(게이트웨이 경유)", "/a2a/{agent_id}/.well-known/agent-card.json": "공개 카드",
    "/a2a/{agent_id}/rest/{path:path}": "test_task_ownership",
    "/api/v1/agents/{pid}": "프로파일은 조직 공유 카탈로그(읽기)", "/api/v1/agents/{pid}/a2a-card": "공유 프로파일",
    "/api/v1/agents/{pid}/checks": "공유 프로파일", "/api/v1/agents/{pid}/code": "공유 프로파일", "/api/v1/agents/{pid}/diff": "공유 프로파일",
    "/api/v1/agents/{pid}/health": "공유 프로파일", "/api/v1/agents/{pid}/simulate": "공유 프로파일(쓰기 없음)",
    "/api/v1/agents/{pid}/test-runs": "공유 프로파일", "/api/v1/agents/{pid}/validate": "공유 프로파일(쓰기 없음)",
    "/api/v1/agents/{pid}/versions": "공유 프로파일",
    "/api/v1/models/{model_id:path}": "권한 검사(models:write·cost:write) 본문 — test_llm",
    "/api/v1/setup/{step}/skip": "설정 토큰 — test_setup", "/idp/broker/{idp_id}/start": "로그인 시작(공개)",
    "/v0/servers/{server_id:path}": "MCP 레지스트리(공개 발견)",
    "/v1/catalog/{item_id}": "조직 카탈로그", "/v1/catalog/{item_id}/diff": "조직 카탈로그", "/v1/catalog/{item_id}/exposure": "조직 카탈로그",
    "/v1/catalog/{item_id}/history": "조직 카탈로그", "/v1/catalog/{item_id}/usage": "조직 카탈로그", "/v1/catalog/{item_id}/versions": "조직 카탈로그",
    "/chat/{path:path}": "SPA", "/console/{path:path}": "SPA", "/login/{path:path}": "SPA", "/setup/{path:path}": "SPA",
}


def _walk(routes):
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):
            yield from _walk(r.original_router.routes)
        elif hasattr(r, "routes"):
            yield from _walk(r.routes)


def _perms(dep, acc):
    for cell in getattr(dep.call, "__closure__", None) or ():
        v = cell.cell_contents
        if isinstance(v, tuple) and v and all(isinstance(x, str) and ":" in x for x in v):
            acc.update(v)
    for d in dep.dependencies:
        _perms(d, acc)
    return acc


# ── C48-01 분류 강제 — 일반 사용자가 닿는(권한 없음 또는 user 역할 권한만) 경로 매개변수 라우트는 OWNED·SHARED 중 하나 ──
def test_every_user_route_is_classified(client):
    user = ROLE_PERMS["user"]
    reach = {r.path for r in _walk(client.app.routes) if "{" in r.path and _perms(r.dependant, set()) <= user}
    assert reach - OWNED - set(SHARED) == set(), "새 라우트를 IDOR 행렬(OWNED) 또는 SHARED(이유)에 넣으세요"


# ── C48-02 남의 객체 — 읽기·쓰기·삭제 모두 403/404 ──
def test_other_user_cannot_touch(client, H, objs):
    leaks = []
    for method, path, body in cases(objs):
        res = client.request(method, path, headers=H[OTHER], **({"json": body} if body is not None else {}))
        if res.status_code not in (403, 404):
            leaks.append((method, path, res.status_code, res.text[:120]))
    assert leaks == [], leaks


# ── C48-02b 행렬이 헛돌지 않게 — 주인은 같은 읽기 경로에서 200 (객체·경로가 실제로 있다) ──
def test_owner_reads_ok(client, H, objs):
    bad = []
    for method, path, _ in cases(objs):
        if method != "GET" or path.endswith("/artifacts/x") or path.endswith("/events"):
            continue
        res = client.get(path, headers=H[OWNER])
        if res.status_code != 200:
            bad.append((path, res.status_code))
    assert bad == [], bad


# ── C48-03 MCP 세션 — 남의 Mcp-Session-Id 로 GET(스트림)·DELETE·요청 불가 ──
def test_other_user_cannot_use_mcp_session(client, H, objs):
    assert objs["mcp"], "레거시 initialize 가 세션 id 를 돌려줘야 한다"
    hdr = {**H[OTHER], **MCP_HDR, "Mcp-Session-Id": objs["mcp"]}
    res = client.post("/mcp/jira", headers=hdr, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert res.status_code in (403, 404), res.text[:200]
    assert client.delete("/mcp/jira", headers=hdr).status_code in (403, 404)
    mine = client.post("/mcp/jira", headers={**H[OWNER], **MCP_HDR, "Mcp-Session-Id": objs["mcp"]},
                       json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
    assert mine.status_code == 200 and "tools" in mine.text


# ── C48-04 주인 객체는 손대지 않은 채 — 세션·토큰·승인 그대로 ──
def test_owner_objects_intact(client, H, objs):
    test_other_user_cannot_touch(client, H, objs)  # 시도한 뒤
    assert client.get(f"/api/v1/sessions/{objs['sid']}", headers=H[OWNER]).status_code == 200
    with SessionLocal() as db:
        assert db.get(PersonalToken, objs["tok"]).revoked_at is None
        assert db.get(Approval, objs["ap"]).status == "pending"
