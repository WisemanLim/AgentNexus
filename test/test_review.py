"""Epic 21 — 등록 → 심사 → 사용 (SCR-E01~E07 · G02/G03 · D10 · F03 · H07/H08).

등록 승인 대기열 점검·도구 위험도·신뢰 등급·허용목록, 버전 diff·고정·롤백, 원격 A2A 등록 요청, 플랫폼 Agent Card 게시 심사,
정책 2인 검토(초안 → 회귀 시험 → 게시 요청 → 작성자 외 승인), MCP 설정 적용 전 시험, 즉시 사용 위저드, 소스 네임스페이스·예약 동기화.
"""
import json
from datetime import timedelta

import httpx

from app.core.http import register_mount
from app.db import SessionLocal, utcnow
from app.models import AuditEvent, CatalogVersion, GatewayPolicy, McpServer, RateLimit, ToolRule
from app.services import a2a_proto as ap
from app.services import scheduler

P = "/api/v1/gateway"


def server_json(name: str, version: str = "1.0.0", tools=("notes.search",), url="https://mcp.e21.test/mcp", auth=None) -> dict:
    return {"$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
            "name": name, "description": "Epic 21 시험 서버", "version": version,
            "remotes": [{"type": "streamable-http", "url": url}],
            "_meta": {"auth": auth or {"type": "oauth2"}, "tools": list(tools), "license": "MIT"}}


def _mock_mcp(tools: list[dict]) -> httpx.MockTransport:
    """stateless JSON MCP (initialize · tools/list · tools/call)."""
    def handle(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}")
        if "id" not in body:
            return httpx.Response(202)
        res = {"initialize": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "mock", "version": "1"}},
               "tools/list": {"tools": tools},
               "tools/call": {"content": [{"type": "text", "text": "ok"}], "isError": False}}.get(body["method"])
        if res is None:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "x"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": res})
    return httpx.MockTransport(handle)


def _pending_row(client, H, item_id: str) -> dict:
    rows = client.get("/api/v1/registry/approvals", headers=H["operator"]).json()["items"]
    return next(r for r in rows if r["item"]["id"] == item_id)


# ── E04·E05·E07 — 점검·위험도·신뢰·허용목록 → diff·재승인·고정·롤백·이력 ──
def test_review_queue_risks_trust_allowlist_and_versions(client, H):
    doc = server_json("io.e21/notes", tools=("notes.search", "notes.delete_note"))
    item = client.post("/v0/publish", json=doc, headers=H["dev"]).json()["item"]["id"]
    row = _pending_row(client, H, item)
    rv = row["review"]
    assert rv["approvable"] and {c["key"] for c in rv["checks"]} >= {"schema", "signature", "provenance"}
    assert {t["name"]: t["risk"] for t in rv["tools"]} == {"notes.search": "low", "notes.delete_note": "high"}
    assert rv["previous_version"] is None and 0 < (utcnow().timestamp() - 0) and rv["expires_at"]
    counts = client.get("/api/v1/registry/approvals", headers=H["operator"]).json()["counts"]
    assert counts["pending"] >= 1
    ok = client.post(f"/v1/catalog/{item}/approve", json={"note": "검토 완료", "trust": "partner",
                                                         "tool_risks": {"notes.delete_note": "high"},
                                                         "allowlist_group": "anx-developers"}, headers=H["operator"]).json()
    assert ok["status"] == "approved" and any("허용목록" in e for e in ok["effects"])
    with SessionLocal() as db:
        pol = db.query(GatewayPolicy).filter(GatewayPolicy.name == "allow:io.e21/notes:anx-developers").one()
        assert pol.subject_type == "group" and pol.target == "notes.*" and pol.effect == "allow"
    assert client.get(f"/v1/catalog/{item}", headers=H["dev"]).json()["trust"] == "partner"
    # 새 버전에 고위험 도구 추가 → diff 에서 재승인 필요 표시
    client.post("/v0/publish", json=server_json("io.e21/notes", "1.1.0", ("notes.search", "notes.delete_note",
                                                                         "notes.purge_all")), headers=H["dev"])
    rv2 = _pending_row(client, H, item)["review"]
    assert rv2["previous_version"] == "1.0.0" and rv2["diff"]["reapproval"] and rv2["diff"]["risky"] >= 1
    client.post(f"/v1/catalog/{item}/approve", json={"version": "1.1.0", "note": "purge 는 고위험"}, headers=H["operator"])
    d = client.get(f"/v1/catalog/{item}/diff", params={"from": "1.0.0", "to": "1.1.0"}, headers=H["dev"]).json()
    assert {"op": "+", "path": "tools", "value": "notes.purge_all", "risk": "high"} in d["changes"]
    # 고정 · 롤백(확인 필수) · 이력
    assert client.put(f"/v1/catalog/{item}/pin", json={"version": "1.0.0"}, headers=H["operator"]).json()["pinned_version"] == "1.0.0"
    assert client.post(f"/v1/catalog/{item}/rollback", json={"version": "1.0.0"},
                       headers=H["operator"]).json()["code"] == "confirm_required"
    rb = client.post(f"/v1/catalog/{item}/rollback", json={"version": "1.0.0", "confirm": True}, headers=H["operator"])
    assert rb.status_code == 200 and rb.json()["version"] == "1.0.0"
    acts = [e["action"] for e in client.get(f"/v1/catalog/{item}/history", headers=H["dev"]).json()["items"]]
    assert {"registry.publish", "registry.approve", "registry.pin", "registry.rollback", "registry.review"} <= set(acts)
    assert client.get(f"/v1/catalog/{item}/usage", headers=H["dev"]).json()["items"] == []


def test_signature_policy_blocks_approval_and_expiry(client, H):
    item = client.post("/v0/publish", json=server_json("io.e21/unsigned"), headers=H["dev"]).json()["item"]["id"]
    client.put("/api/v1/registry/trust", json={"keys": {}, "require_signature": True}, headers=H["security"])
    try:
        assert _pending_row(client, H, item)["review"]["approvable"] is False
        res = client.post(f"/v1/catalog/{item}/approve", json={"note": "x"}, headers=H["operator"])
        assert res.status_code == 400 and res.json()["code"] == "checks_failed" and "MSG-007" in res.json()["detail"]
    finally:
        client.put("/api/v1/registry/trust", json={"keys": {}, "require_signature": False}, headers=H["security"])
    # 72시간 넘은 대기 → 만료(자동 반려)
    with SessionLocal() as db:
        v = db.query(CatalogVersion).filter(CatalogVersion.item_id == item, CatalogVersion.status == "pending").one()
        v.created_at = utcnow() - timedelta(hours=73)
        db.commit()
    expired = client.get("/api/v1/registry/approvals", params={"status": "expired"}, headers=H["operator"]).json()
    assert any(r["item"]["id"] == item and r["decided_by"] == "system:expire" for r in expired["items"])
    assert client.get(f"/v1/catalog/{item}", headers=H["operator"]).json()["status"] == "rejected"


def test_registry_validate_inline_and_draft(client, H):
    bad = client.post("/api/v1/registry/validate", json={"text": '{\n  "name": "io.e21/x",\n  "version": \n}'}, headers=H["dev"]).json()
    assert bad["valid"] is False and bad["inline"][0]["line"] == 4
    doc = server_json("io.e21/ok", tools=("ok.search", "ok.delete_all"),
                      auth={"type": "oauth2", "scopes": ["ok:read"], "tool_scopes": {"delete_all": "ok:admin"}})
    v = client.post("/api/v1/registry/validate", json=doc, headers=H["dev"]).json()
    assert not v["valid"] and any("ok:admin" in e for e in v["errors"])  # 선언되지 않은 도구 스코프
    assert {r["tool"]: r["risk"] for r in v["risks"]}["ok.delete_all"] == "high"
    assert {s["scope"]: s["kind"] for s in v["scopes"]}["ok:admin"] == "admin" and v["signature"]["present"] is False
    assert client.put("/api/v1/registry/draft", json={"text": "{}"}, headers=H["dev"]).json()["saved_at"]
    assert client.get("/api/v1/registry/draft", headers=H["dev"]).json()["text"] == "{}"
    assert client.get("/api/v1/registry/draft", headers=H["operator"]).json()["text"] is None  # 사용자별


# ── G02 — 원격 A2A 등록 요청 → 승인(신뢰 등급) 후 활성 ──
def test_a2a_registration_request_then_approval(client, H):
    card = ap.build_card(name="E21 Partner", description="파트너 분석", url="http://e21-agent.test/", version="1.0.0",
                         skills=[{"id": "analyze", "name": "Analyze", "tags": ["sre"]}])
    register_mount("http://e21-agent.test", async_transport=httpx.MockTransport(lambda r: httpx.Response(200, json=card)))
    dry = client.post("/api/v1/a2a/agents", json={"url": "http://e21-agent.test", "dry_run": True}, headers=H["dev"]).json()
    assert dry["ok"] and {c["key"] for c in dry["checks"]} >= {"schema", "url_domain", "tls", "skills", "signature"}
    assert client.get("/api/v1/a2a/agents/e21-partner", headers=H["dev"]).status_code == 404  # dry-run 은 저장 안 함
    reg = client.post("/api/v1/a2a/agents", json={"url": "http://e21-agent.test", "id": "e21-partner"}, headers=H["dev"]).json()
    assert reg["status"] == "pending" and reg["review"]["checks"]
    row = _pending_row(client, H, reg["catalog_item_id"])
    assert row["item"]["type"] == "a2a_agent" and [t["name"] for t in row["review"]["tools"]] == ["analyze"]
    no_note = client.post(f"/v1/catalog/{reg['catalog_item_id']}/approve", json={"trust": "partner"}, headers=H["operator"])
    assert no_note.json()["code"] == "reason_required"  # 서명 없는 카드에 partner 이상은 사유 필요
    ok = client.post(f"/v1/catalog/{reg['catalog_item_id']}/approve", json={"trust": "partner", "note": "계약 확인"},
                     headers=H["operator"]).json()
    assert any("활성" in e for e in ok["effects"])
    a = client.get("/api/v1/a2a/agents/e21-partner", headers=H["dev"]).json()
    assert a["status"] == "active" and a["trust_tier"] == "trusted" and a["cache"]["ttl_s"]
    checks = client.get("/api/v1/a2a/agents/e21-partner/checks", headers=H["dev"]).json()["items"]
    assert isinstance(checks, list)


# ── D10 — 플랫폼 Agent Card 편집 → 게시 요청 → 승인 후 공개 ──
def test_platform_agent_card_editor_and_review(client, H):
    assert client.post("/api/v1/agents", json={"name": "e21-card", "id": "e21-card", "yaml": """
planner: {mode: template, max_steps: 2}
workers: {summarize: {model: domain-local}}
default_plan:
  - {id: s1, agent: summarize, args: {message: "{{request}}"}}
"""}, headers=H["dev"]).status_code == 201
    assert client.post("/api/v1/agents/e21-card/publish", headers=H["dev"]).status_code == 200
    g = client.get("/api/v1/agents/e21-card/a2a-card", headers=H["dev"]).json()
    assert g["status"] == "draft" and not g["errors"] and g["url"].endswith("/a2a/platform/e21-card")
    bad = client.put("/api/v1/agents/e21-card/a2a-card", json={**g["draft"], "version": "1.0"}, headers=H["dev"]).json()
    assert any("SemVer" in e for e in bad["errors"])
    skills = [{"id": "triage", "name": "Triage", "description": "운영 이슈 분류", "tags": ["ops"]}]
    put = client.put("/api/v1/agents/e21-card/a2a-card", json={**g["draft"], "version": "1.2.0", "skills": skills},
                     headers=H["dev"]).json()
    assert not put["errors"] and put["preview"]["skills"][0]["id"] == "triage"
    assert client.post("/api/v1/agents/e21-card/a2a", json={"exposed": True},
                       headers=H["dev"]).json()["code"] == "review_required"  # 직접 노출은 승인 경로로만
    req = client.post("/api/v1/agents/e21-card/a2a-card/publish", headers=H["dev"]).json()
    assert req["requested"] is True
    assert client.get("/a2a/platform/e21-card/.well-known/agent-card.json").status_code == 404  # 승인 전 비공개
    rows = client.get("/api/v1/registry/approvals", headers=H["operator"]).json()["items"]
    row = next(r for r in rows if r["item"]["type"] == "agent_profile" and r["item"]["name"] == "e21-card")
    assert row["review"]["kind"] == "a2a_card" and row["review"]["approvable"]
    ok = client.post(f"/v1/catalog/{row['item']['id']}/approve", json={"version": "1.2.0", "note": "게시 승인"},
                     headers=H["operator"]).json()
    assert any("A2A 게시" in e for e in ok["effects"])
    card = client.get("/a2a/platform/e21-card/.well-known/agent-card.json").json()
    assert card["version"] == "1.2.0" and [s["id"] for s in card["skills"]] == ["triage"]


# ── H07·H08 — 정책 2인 검토 · 회귀 시험 · 비상 경로 · diff · 롤백 ──
def test_policy_two_person_review_with_regression_tests(client, H):
    base = {"name": "e21 user 쓰기 금지", "subject_type": "role", "subject": "user", "target_type": "tool",
            "target": "e21x.*", "effect": "deny", "priority": 30}
    direct = client.post(f"{P}/policies", json=base, headers=H["security"])
    assert direct.status_code == 409 and direct.json()["code"] == "review_required"
    tc = client.post(f"{P}/policy-tests", json={"name": "user 는 e21x 쓰기 거부", "expect": "deny",
                                                "input": {"kind": "tool", "target": "e21x.write", "sub": "t", "roles": ["user"]}},
                     headers=H["security"]).json()
    try:
        d = client.post(f"{P}/policies/drafts", json={"policy": {**base, "effect": "allow"}}, headers=H["security"]).json()
        assert d["status"] == "draft" and d["policy_id"] is None and "package agentnexus.gateway" in d["rego"]
        t = client.post(f"{P}/policies/drafts/{d['id']}/test", headers=H["security"]).json()["test_result"]
        assert t["passed"] is False and "replay" in t
        assert client.post(f"{P}/policies/drafts/{d['id']}/submit", json={"reason": "x"},
                           headers=H["security"]).json()["code"] == "tests_required"
        client.put(f"{P}/policies/drafts/{d['id']}", json=base, headers=H["security"])
        t = client.post(f"{P}/policies/drafts/{d['id']}/test", headers=H["security"]).json()["test_result"]
        assert t["passed"] is True and t["cases"][0]["got"] == "deny"
        assert client.post(f"{P}/policies/drafts/{d['id']}/submit", json={"reason": " "},
                           headers=H["security"]).json()["code"] == "reason_required"
        sub = client.post(f"{P}/policies/drafts/{d['id']}/submit", json={"reason": "쓰기 도구 차단"}, headers=H["security"])
        assert sub.json()["status"] == "pending"
        selfie = client.post(f"{P}/policies/drafts/{d['id']}/approve", json={}, headers=H["security"])
        assert selfie.status_code == 403 and selfie.json()["code"] == "self_approval"
        ok = client.post(f"{P}/policies/drafts/{d['id']}/approve", json={"note": "확인"}, headers=H["admin"]).json()
        pid = ok["policy_id"]
        assert ok["status"] == "approved" and pid
        listed = client.get(f"{P}/policies", headers=H["security"]).json()
        row = next(x for x in listed["items"] if x["id"] == pid)
        assert row["version"] == 1 and row["decisions_24h"] == {"allow": 0, "deny": 0} and not row["pending_review"]
        assert listed["engine_status"]["engine"] == "builtin" and listed["engine_status"]["synced"]
        # 수정 초안 → 게시 → v2 · diff · 롤백(확인·사유)
        d2 = client.post(f"{P}/policies/drafts", json={"policy_id": pid, "policy": {**base, "priority": 31}},
                         headers=H["security"]).json()
        assert d2["diff"] == [{"field": "priority", "from": 30, "to": 31}] and d2["base_version"] == 1
        client.post(f"{P}/policies/drafts/{d2['id']}/test", headers=H["security"])
        client.post(f"{P}/policies/drafts/{d2['id']}/submit", json={"reason": "우선순위 조정"}, headers=H["security"])
        assert client.post(f"{P}/policies/drafts/{d2['id']}/approve", json={}, headers=H["admin"]).json()["status"] == "approved"
        diff = client.get(f"{P}/policies/{pid}/diff", params={"from": 1}, headers=H["security"]).json()
        assert diff["changes"] == [{"field": "priority", "from": 30, "to": 31}]
        assert client.post(f"{P}/policies/{pid}/rollback", json={"version": 1, "reason": "x"},
                           headers=H["security"]).json()["code"] == "confirm_required"
        assert client.post(f"{P}/policies/{pid}/rollback", json={"version": 1, "confirm": True},
                           headers=H["security"]).json()["code"] == "reason_required"
        rb = client.post(f"{P}/policies/{pid}/rollback", json={"version": 1, "confirm": True, "reason": "회귀"},
                         headers=H["security"]).json()
        assert rb["version"] == 3 and rb["priority"] == 30
        # 초안 작성 후 정책이 바뀌면(관리자 비상 경로) 승인 거부 — 다시 시험
        d3 = client.post(f"{P}/policies/drafts", json={"policy_id": pid, "policy": {**base, "priority": 40}},
                         headers=H["security"]).json()
        client.post(f"{P}/policies/drafts/{d3['id']}/test", headers=H["security"])
        client.post(f"{P}/policies/drafts/{d3['id']}/submit", json={"reason": "r"}, headers=H["security"])
        bg = client.put(f"{P}/policies/{pid}", json={**base, "priority": 35}, headers=H["admin"])
        assert bg.status_code == 200 and bg.json()["version"] == 4
        stale = client.post(f"{P}/policies/drafts/{d3['id']}/approve", json={}, headers=H["admin"])
        assert stale.status_code == 409 and stale.json()["code"] == "stale_draft"
        with SessionLocal() as db:
            ev = db.query(AuditEvent).filter(AuditEvent.action == "policy.update", AuditEvent.target == pid).all()
            assert any((e.data or {}).get("break_glass") for e in ev) and any((e.data or {}).get("draft") for e in ev)
        rego = client.post(f"{P}/policies/rego", json={"policy": {**base, "conditions": {"data_class": ["internal"]}}},
                           headers=H["security"]).json()["rego"]
        assert "deny if" in rego and 'input.data_class in {"internal"}' in rego and '"user" in input.subject.roles' in rego
        # 삭제도 초안·승인으로
        dd = client.post(f"{P}/policies/drafts", json={"policy_id": pid, "delete": True}, headers=H["security"]).json()
        assert dd["delete"] is True and dd["base_version"] == 4
        blocked = client.post(f"{P}/policies/drafts/{dd['id']}/test", headers=H["security"]).json()["test_result"]
        assert blocked["passed"] is False and blocked["cases"][0]["got"] == "allow"  # 지우면 user 가 허용됨 → 케이스가 막는다
        assert client.post(f"{P}/policies/drafts/{dd['id']}/submit", json={"reason": "정리"},
                           headers=H["security"]).json()["code"] == "tests_required"
        client.delete(f"{P}/policy-tests/{tc['id']}", headers=H["security"])  # 케이스를 내린 뒤에야 게시 가능
        assert client.post(f"{P}/policies/drafts/{dd['id']}/test", headers=H["security"]).json()["test_result"]["passed"]
        assert client.post(f"{P}/policies/drafts/{dd['id']}/submit", json={"reason": "정리"},
                           headers=H["security"]).json()["status"] == "pending"
        res = client.post(f"{P}/policies/drafts/{dd['id']}/approve", json={}, headers=H["admin"])
        assert res.status_code == 200, res.text
        with SessionLocal() as db:
            assert db.get(GatewayPolicy, pid) is None
    finally:
        client.delete(f"{P}/policy-tests/{tc['id']}", headers=H["security"])


# ── F03 — 적용 전 시험 (저장하지 않음) ──
def test_mcp_config_test_before_apply(client, H):
    register_mount("http://f03-down.test", async_transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    client.post("/api/v1/mcp-servers/dynatrace/refresh", headers=H["operator"])  # 등록 시와 같이 교환 정책 기본값
    with SessionLocal() as db:
        before = db.get(McpServer, "dynatrace").endpoint
    bad = client.post("/api/v1/mcp-servers/dynatrace/test-config", json={"endpoint": "http://f03-down.test/mcp"},
                      headers=H["operator"]).json()
    assert bad["ok"] is False and bad["applied"] is False
    assert next(c for c in bad["checks"] if c["key"] == "tools_list")["level"] == "fail"
    with SessionLocal() as db:
        assert db.get(McpServer, "dynatrace").endpoint == before  # 시험은 저장하지 않는다
    good = client.post("/api/v1/mcp-servers/dynatrace/test-config",
                       json={"auth": {"scopes": ["problems:read", "admin:all"]}}, headers=H["operator"]).json()
    keys = {c["key"]: c for c in good["checks"]}
    assert good["ok"] and keys["tools_list"]["ok"] and keys["prm"]["ok"] and keys["authorization_server"]["ok"]
    assert keys["scopes"]["level"] == "warn" and "admin:all" in keys["scopes"]["detail"]
    assert {t["name"] for t in good["tools"]} >= {"list_problems", "get_problem"}
    assert client.post("/api/v1/mcp-servers/nope/test-config", json={}, headers=H["operator"]).status_code == 404


# ── E03 — 즉시 사용 위저드: ① 등록 ② 인증·시험 교환 ③ 정책 기본값(Rego) ④ 노출 확인 ──
def test_instant_use_wizard(client, H):
    register_mount("http://e03-mcp.test", async_transport=_mock_mcp([
        {"name": "list_tasks", "description": "목록", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
        {"name": "create_task", "description": "생성", "inputSchema": {"type": "object"}},
        {"name": "delete_task", "description": "삭제", "inputSchema": {"type": "object"}}]))
    doc = server_json("io.e21/tasks", tools=("list_tasks", "create_task", "delete_task"), url="http://e03-mcp.test/mcp",
                      auth={"type": "none", "tool_scopes": {"list_tasks": "tasks:read", "create_task": "tasks:write"}})
    item = client.post("/v0/publish", json=doc, headers=H["dev"]).json()["item"]["id"]
    client.post(f"/v1/catalog/{item}/approve", json={"note": "e03"}, headers=H["operator"])
    assert client.post(f"/v1/catalog/{item}/instant-use/auth", json={},
                       headers=H["operator"]).json()["code"] == "not_registered"
    reg = client.post(f"/v1/catalog/{item}/register", headers=H["operator"]).json()
    assert reg["instance"]["id"] == "tasks" and reg["instance"]["refresh"]["health"] == "healthy"
    # ② 최소 스코프·짧은 수명 → 시험 교환 / 서버가 선언하지 않은 스코프는 MSG-019
    a = client.post(f"/v1/catalog/{item}/instant-use/auth", json={"scopes": ["tasks:read"], "ttl_seconds": 300},
                    headers=H["operator"]).json()
    assert a["ok"] and a["audience"] == "mcp:tasks" and a["scopes"] == ["tasks:read"] and a["expires_in"] == 300
    bad = client.post(f"/v1/catalog/{item}/instant-use/auth", json={"scopes": ["tasks:admin"]}, headers=H["operator"])
    assert bad.status_code == 403 and bad.json()["code"] == "token_exchange_failed"
    # ③ 미리보기(dry-run) 는 저장하지 않는다
    body = {"subject_type": "group", "subject": "anx-developers", "data_class_routing": {"internal": "domain-local"},
            "calls_per_min": 60}
    dry = client.post(f"/v1/catalog/{item}/instant-use/policy", json={**body, "dry_run": True}, headers=H["operator"]).json()
    plan = {t["tool"]: t for t in dry["tools"]}
    assert plan["list_tasks"]["allowed"] and not plan["create_task"]["allowed"] and plan["delete_task"]["approval_required"]
    assert dry["summary"] == {"read_allowed": 1, "write_blocked": 1, "approval": 1, "policy": "tool-allow/tasks", "version": 1}
    assert 'input.kind == "tool"' in dry["rego"] and '"delete_task"' in dry["rego"]
    with SessionLocal() as db:
        assert db.query(GatewayPolicy).filter(GatewayPolicy.name == "tool-allow/tasks").first() is None
    applied = client.post(f"/v1/catalog/{item}/instant-use/policy",
                          json={**body, "tools": {"create_task": {"allowed": True}}, "profile_id": "tpl-qa"},
                          headers=H["operator"]).json()
    assert applied["policy_id"] and applied["attached_profile"] == "tpl-qa"
    with SessionLocal() as db:
        rules = {r.tool: r for r in db.query(ToolRule).filter(ToolRule.server_id == "tasks")}
        assert rules["create_task"].allowed and rules["delete_task"].approval_required and rules["list_tasks"].allowed
        assert float(db.query(RateLimit).filter(RateLimit.name == "instant:tasks").one().rps) == 1.0
        assert "tasks.*" in db.get(__import__("app.models", fromlist=["AgentProfile"]).AgentProfile, "tpl-qa").tools
    again = client.post(f"/v1/catalog/{item}/instant-use/policy", json=body, headers=H["operator"]).json()
    assert again["summary"]["version"] == 2  # 다시 적용하면 정책 버전 증가
    # ④ 60초 노출 확인
    exp = client.get(f"/v1/catalog/{item}/exposure", headers=H["operator"]).json()
    assert exp["exposed"] and exp["within_slo"] and exp["elapsed_s"] is not None
    assert next(c for c in exp["checks"] if c["key"] == "host_tools")["ok"]
    with SessionLocal() as db:  # 다른 시험에 영향 없도록 템플릿 원복
        from app.models import AgentProfile
        pr = db.get(AgentProfile, "tpl-qa")
        pr.tools = [t for t in pr.tools if t != "tasks.*"]
        db.commit()


# ── E01·E06 — 검색 필터·점수·일치 힌트 · 소스 네임스페이스·부분 실패·예약 동기화 ──
def test_catalog_search_filters_and_sources(client, H):
    res = client.get("/v1/catalog", params={"q": "jira tag:builtin auth:oauth2"}, headers=H["operator"]).json()
    assert res["items"] and all("builtin" in i["tags"] and i["auth_scheme"] == "oauth2" for i in res["items"])
    assert "jira" in res["items"][0]["name"] and res["counts"]["mcp_server"] >= 1
    hint = client.get("/v1/catalog", params={"q": "transition_issue"}, headers=H["operator"]).json()["items"]
    assert any((i["match"] or "").startswith("transition_issue") for i in hint)
    partner = client.get("/v1/catalog", params={"trust": "partner"}, headers=H["operator"]).json()["items"]
    assert "io.e21/notes" in [i["name"] for i in partner]
    assert isinstance(client.get("/v1/catalog/exposures", headers=H["operator"]).json()["items"], list)
    src = client.post("/api/v1/registry/sources", json={"name": "e21-file", "kind": "file", "namespaces": ["io.e21ok*"]},
                      headers=H["operator"]).json()
    bad = server_json("io.e21ok/bad")
    bad.pop("remotes")
    counts = client.post(f"/api/v1/registry/sources/{src['id']}/import",
                         json=[server_json("io.e21ok/a"), bad, server_json("io.other/b")], headers=H["operator"]).json()
    assert (counts["imported"], counts["invalid"], counts["filtered"]) == (1, 1, 1)
    s = next(x for x in client.get("/api/v1/registry/sources", headers=H["operator"]).json()["items"] if x["id"] == src["id"])
    assert s["last_status"].startswith("partial") and s["last_result"]["total"] == 3 and s["namespaces"] == ["io.e21ok*"]
    assert client.patch(f"/api/v1/registry/sources/{src['id']}", json={"sync_interval_s": 60},
                        headers=H["operator"]).status_code == 400
    register_mount("https://e21.mirror.test", sync_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "servers": [{"server": server_json("io.e21sync/x")}], "metadata": {}})))
    off = client.post("/api/v1/registry/sources", json={"name": "e21-official", "kind": "official", "url": "https://e21.mirror.test",
                                                         "sync_interval_s": 300}, headers=H["operator"]).json()
    assert off["sync_interval_s"] == 300 and off["next_sync_at"] is None
    out = scheduler.registry_jobs()
    assert "e21-official" in out["synced"]
    s2 = next(x for x in client.get("/api/v1/registry/sources", headers=H["operator"]).json()["items"] if x["id"] == off["id"])
    assert s2["item_count"] == 1 and s2["next_sync_at"]
    assert "e21-official" not in scheduler.registry_jobs()["synced"]  # 주기 전에는 다시 하지 않는다
