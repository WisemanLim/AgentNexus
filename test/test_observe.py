"""Epic 9 — Observability & Audit (FR-O00~O05, FR-G05, COMPLIANCE testing_additions)."""
import json
import time
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core import logs, tracing
from app.core.ids import new_id
from app.db import SessionLocal, utcnow
from app.models import AuditEvent, LogEntry, Model, Run, Span, UsageRecord


def wait_run(client, H, rid, until=("succeeded", "partial", "failed", "waiting_approval"), timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/v1/runs/{rid}", headers=H).json()
        if r["status"] in until:
            return r
        time.sleep(0.1)
    raise AssertionError("timeout")


def ask(client, H, profile, text):
    sid = client.post("/api/v1/sessions", json={"profile_id": profile}, headers=H).json()["id"]
    rid = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": text}, headers=H).json()["run_id"]
    return sid, rid


@pytest.fixture(scope="module", autouse=True)
def ready(client, H):
    for sid in ("jira", "confluence", "dynatrace"):
        client.post(f"/api/v1/mcp-servers/{sid}/refresh", headers=H["operator"])
    client.post("/api/v1/a2a/agents/observability-analyst/refresh", headers=H["operator"])


def _components(spans):
    return {(s["attributes"] or {}).get("anx.component") for s in spans} - {None}


def test_single_trace_across_host_gateway_mcp_a2a(client, H):
    _, rid = ask(client, H["user"], "tpl-incident", "orders-api 장애 원인 분석")
    run = wait_run(client, H["user"], rid)
    assert run["status"] == "succeeded" and run["trace_id"]
    time.sleep(0.3)
    tr = client.get(f"/api/v1/traces/{run['trace_id']}", headers=H["operator"]).json()
    names = [s["name"] for s in tr["spans"]]
    assert any(n.startswith("invoke_agent tpl-incident") for n in names)
    assert {"retriever s1", "remote s2", "summarize s3"} <= set(names)
    assert "mcp tools/call dynatrace.list_problems" in names and "a2a SendMessage observability-analyst" in names  # D15-② 오케스트레이터 위임은 A2A 1.0
    assert any(n.startswith("chat domain-local") for n in names)
    assert {"api", "mcp-servers", "a2a-demo"} <= _components(tr["spans"])  # 단일 trace_id 전 구간 전파
    chat = next(s for s in tr["spans"] if s["name"].startswith("chat domain-local"))
    assert chat["attributes"]["gen_ai.operation.name"] == "chat" and chat["attributes"]["gen_ai.usage.input_tokens"] > 0
    assert tr["gen_ai_spans"] >= 4 and tr["tokens"] > 0
    listed = client.get("/api/v1/traces", params={"q": "invoke_agent"}, headers=H["operator"]).json()["items"]
    assert any(t["trace_id"] == run["trace_id"] for t in listed)
    assert client.get("/api/v1/traces", headers=H["user"]).status_code == 403


def test_approval_resume_stays_in_same_trace(client, H):
    _, rid = ask(client, H["user"], "tpl-issue-report", "주간 보고 작성")
    run = wait_run(client, H["user"], rid)
    ap_id = next(s for s in run["steps"] if s["status"] == "waiting_approval")["approval_id"]
    client.post(f"/api/v1/approvals/{ap_id}/approve", headers=H["user"])
    run = wait_run(client, H["user"], rid, until=("succeeded", "failed", "partial"))
    time.sleep(0.3)
    names = [s["name"] for s in client.get(f"/api/v1/traces/{run['trace_id']}", headers=H["operator"]).json()["spans"]]
    assert "invoke_agent tpl-issue-report (resume)" in names and "executor s4" in names
    assert "mcp tools/call confluence.create_page" in names


def _otlp_request(trace_hex: str):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans
    from opentelemetry.proto.trace.v1.trace_pb2 import Span as PSpan
    now = time.time_ns()
    sp = PSpan(trace_id=bytes.fromhex(trace_hex), span_id=bytes.fromhex("00f067aa0ba902b7"), name="external work",
               kind=PSpan.SPAN_KIND_SERVER, start_time_unix_nano=now - 5_000_000, end_time_unix_nano=now,
               attributes=[KeyValue(key="anx.component", value=AnyValue(string_value="external"))])
    return ExportTraceServiceRequest(resource_spans=[ResourceSpans(
        resource=Resource(attributes=[KeyValue(key="service.name", value=AnyValue(string_value="partner-svc"))]),
        scope_spans=[ScopeSpans(spans=[sp])])])


def test_otlp_receiver_protobuf_and_json(client, H):
    trace_hex = "4bf92f3577b34da6a3ce929d0e0e4736"
    body = _otlp_request(trace_hex).SerializeToString()
    hdr = {"Content-Type": "application/x-protobuf"}
    assert client.post("/v1/traces", content=body, headers={**hdr, "Authorization": "Bearer wrong"}).status_code == 401
    ok = client.post("/v1/traces", content=body, headers={**hdr, "Authorization": f"Bearer {tracing.ingest_token()}"})
    assert ok.status_code == 200 and ok.headers["content-type"] == "application/x-protobuf"
    tr = client.get(f"/api/v1/traces/{trace_hex}", headers=H["operator"]).json()
    assert tr["spans"][0]["service"] == "partner-svc" and tr["spans"][0]["kind"] == "server"
    doc = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "json-svc"}}]},
                              "scopeSpans": [{"spans": [{"traceId": "5" * 32, "spanId": "6" * 16, "name": "json span",
                                                         "kind": 3, "startTimeUnixNano": str(time.time_ns() - 1000),
                                                         "endTimeUnixNano": str(time.time_ns()),
                                                         "status": {"code": 2}}]}]}]}
    res = client.post("/v1/traces", json=doc, headers={"Authorization": f"Bearer {tracing.ingest_token()}"})
    assert res.status_code == 200
    j = client.get(f"/api/v1/traces/{'5' * 32}", headers=H["operator"]).json()["spans"][0]
    assert j["service"] == "json-svc" and j["status"] == "error" and j["kind"] == "client"
    pipe = client.get("/api/v1/observe/pipeline", headers=H["operator"]).json()
    assert pipe["receiver"]["ingested"] >= 2 and pipe["exporters"]["builtin_store"]["exported"] > 0


def test_metrics_endpoint_auth_and_content(client, H, monkeypatch):
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers=H["user"]).status_code == 401  # observe:read 필요
    res = client.get("/metrics", headers=H["operator"])
    assert res.status_code == 200
    text = res.text
    for name in ("anx_http_requests_total", "anx_llm_tokens_total", "anx_mcp_tool_calls_total",
                 "anx_approvals_pending", "anx_mcp_server_healthy", "anx_runs_total"):
        assert name in text, name
    from app.core.config import settings
    monkeypatch.setattr(settings, "metrics_token", "scrape-token-123")
    assert client.get("/metrics", headers={"Authorization": "Bearer scrape-token-123"}).status_code == 200


def test_structured_logs_masked_and_searchable(client, H):
    client.post("/mcp/jira", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "nope_tool", "arguments": {}}},
                headers={**H["user"], "Accept": "application/json, text/event-stream"})
    logs.event("warning", "test", "고객 연락처 010-1234-5678 확인 필요", email="kim@corp.test")
    logs.flush()
    found = client.get("/api/v1/logs", params={"component": "gateway", "level": "WARNING", "q": "도구 호출 거부"},
                       headers=H["operator"]).json()["items"]
    assert found and found[0]["data"]["code"] == "unregistered_tool"
    with SessionLocal() as db:
        row = db.scalars(select(LogEntry).where(LogEntry.component == "test").order_by(LogEntry.id.desc())).first()
        assert "010-1234-5678" not in row.message and "[MASKED:phone]" in row.message
        assert row.data["email"] == "[MASKED:email]"


def test_summary_dashboard(client, H):
    s = client.get("/api/v1/observe/summary", params={"window_minutes": 60}, headers=H["operator"]).json()
    assert s["calls"] > 0 and "model" in s["by_kind"] and "tool" in s["by_kind"]
    assert s["by_kind"]["model"]["p95_latency_ms"] is not None and s["mcp_servers"]["healthy"] >= 3
    assert s["cost_usd"] >= 0 and isinstance(s["cost_by_team"], list)


def test_usage_cost_accuracy_and_scope(client, H):
    t0 = utcnow()
    for model, dc in (("fast-commercial", "public"), ("smart-commercial", "public"), ("domain-local", "internal")):
        for _ in range(3):
            client.post("/v1/chat/completions", json={"model": model, "messages": [
                {"role": "user", "content": "비용 집계 정확도 측정용 요청 " * 5}]},
                headers={**H["user"], "X-AgentNexus-Data-Class": dc})
    allr = client.get("/api/v1/usage", params={"group_by": "model", "since": t0.isoformat()},
                      headers=H["finops"]).json()
    assert allr["scope"] == "all" and {i["key"] for i in allr["items"]} == {"fast-commercial", "smart-commercial",
                                                                            "domain-local"}
    with SessionLocal() as db:
        prices = {m.id: (m.price_in, m.price_out) for m in db.scalars(select(Model))}
        recs = db.scalars(select(UsageRecord).where(UsageRecord.kind == "model", UsageRecord.ts >= t0)).all()
        expected = sum((Decimal(r.tokens_in) * prices[r.target][0] + Decimal(r.tokens_out) * prices[r.target][1])
                       / Decimal(1_000_000) for r in recs if r.target in prices)
    got = Decimal(str(allr["total_cost_usd"]))
    assert expected > 0 and abs(got - expected) / expected <= Decimal("0.02")  # NFR-O1 비용 집계 ±2%
    mine = client.get("/api/v1/usage", params={"group_by": "user"}, headers=H["user2"]).json()
    assert mine["scope"] == "self" and {i["key"] for i in mine["items"]} <= {"usr_user2"}  # 팀 간 누출 차단
    team = client.get("/api/v1/usage", params={"group_by": "team", "team": "team-biz"}, headers=H["finops"]).json()
    assert {i["key"] for i in team["items"]} <= {"team-biz"}
    by_day = client.get("/api/v1/usage", params={"group_by": "day"}, headers=H["finops"]).json()
    assert by_day["items"][0]["key"].count("-") == 2


def test_alert_rules(client, H):
    client.post("/api/v1/observe/alerts", json={"name": "호출 발생", "metric": "qps", "op": ">=", "threshold": 0,
                                                "window_s": 3600}, headers=H["operator"])
    fired = client.post("/api/v1/observe/alerts/evaluate", headers=H["operator"]).json()["fired"]
    assert any(f["name"] == "호출 발생" for f in fired)


def test_session_replay_and_rerun(client, H):
    from app.services import mcp_gateway
    mcp_gateway.clear_caches()  # 교환 토큰 캐시를 비워 이 실행의 trace 안에서 RFC 8693 교환이 일어나게 한다
    sid, rid = ask(client, H["user"], "tpl-qa", "런북 요약")
    wait_run(client, H["user"], rid)
    rp = client.get(f"/api/v1/audit/replay/{sid}", headers=H["user"]).json()
    kinds = [t["kind"] for t in rp["timeline"]]
    for k in ("message.user", "step.retriever", "call.tool", "call.model", "token.exchange", "message.assistant"):
        assert k in kinds, k
    assert kinds.index("message.user") < kinds.index("message.assistant")
    assert client.get(f"/api/v1/audit/replay/{sid}", headers=H["user2"]).status_code == 404
    assert client.get(f"/api/v1/audit/replay/{sid}", headers=H["security"]).status_code == 200  # 감사 담당
    again = client.post(f"/api/v1/audit/replay/{rid}/rerun", headers=H["user"]).json()
    rerun = wait_run(client, H["user"], again["run_id"])
    assert rerun["status"] == "succeeded" and rerun["test"] is True


def test_signed_audit_report(client, H):
    rep = client.post("/api/v1/audit/reports", json={"actions": ["token.", "approval."]}, headers=H["security"]).json()
    assert rep["count"] > 0 and rep["chain"]["valid"] is True
    dl = client.get(f"/api/v1/audit/reports/{rep['id']}/download", headers=H["security"])
    assert dl.headers["x-agentnexus-sha256"] == rep["sha256"]
    lines = dl.text.splitlines()
    assert all(json.loads(x)["action"].startswith(("token.", "approval.")) for x in lines)
    ok = client.post("/api/v1/audit/reports/verify", json={"signature": rep["signature"], "content": dl.text},
                     headers=H["security"]).json()
    assert ok["valid"] is True and ok["claims"]["count"] == rep["count"]
    bad = client.post("/api/v1/audit/reports/verify", json={"signature": rep["signature"], "content": dl.text + "x"},
                      headers=H["security"]).json()
    assert bad["valid"] is False and bad["reason"] == "content_hash_mismatch"
    forged = client.post("/api/v1/audit/reports/verify", json={"signature": rep["signature"][:-4] + "AAAA"},
                         headers=H["security"]).json()
    assert forged["valid"] is False
    assert client.post("/api/v1/audit/reports", json={}, headers=H["user"]).status_code == 403


def test_retention_archive_and_purge_proof(client, H):
    """COMPLIANCE: 감사로그 보관기간 경과 삭제 증명 + 텔레메트리 보관/삭제 (CC6.5/A.8.10)."""
    old = utcnow() - timedelta(days=60)
    with SessionLocal() as db:
        db.add(Span(id="0123456789abcdef", trace_id="a" * 32, name="old", service="x", kind="internal",
                    start_time=old, end_time=old, duration_ms=1))
        db.add(LogEntry(ts=old, level="INFO", logger="t", message="old"))
        db.add(UsageRecord(id=new_id("use"), ts=utcnow() - timedelta(days=500), kind="model", target="old"))
        db.commit()
        before_events = db.query(AuditEvent).count()
    assert client.post("/api/v1/audit/retention/run", json={}, headers=H["dev"]).status_code == 403
    res = client.post("/api/v1/audit/retention/run", json={"overrides": {"audit_days": 0}},
                      headers=H["admin"]).json()
    assert res["deleted"]["spans"] >= 1 and res["deleted"]["log_entries"] >= 1 and res["deleted"]["usage_records"] >= 1
    arc = res["archive"]
    assert arc and arc["count"] >= before_events - 5
    verify = client.get("/api/v1/audit/verify", headers=H["security"]).json()
    assert verify["valid"] is True and verify["anchor_seq"] == arc["to_seq"], (verify, arc)  # 앵커 이후 체인 유효
    v = client.post(f"/api/v1/audit/archives/{arc['archive_id']}/verify", headers=H["security"]).json()
    assert v["valid"] is True and v["file_present"] is True
    purged = client.post(f"/api/v1/audit/archives/{arc['archive_id']}/purge", headers=H["admin"]).json()
    assert purged["file_sha256"] == arc["file_sha256"]
    after = client.post(f"/api/v1/audit/archives/{arc['archive_id']}/verify", headers=H["security"]).json()
    assert after["file_present"] is False and after["purged_at"] and after["last_hash"] == arc["last_hash"]
    events = client.get("/api/v1/audit/events", params={"action": "audit.archive"}, headers=H["security"]).json()
    actions = {e["action"] for e in events["items"]}
    assert {"audit.archive", "audit.archive.purge"} <= actions  # 아카이브·파기 자체가 체인에 기록됨
    assert client.get("/api/v1/audit/verify", headers=H["security"]).json()["valid"] is True
    with SessionLocal() as db:
        assert db.get(Run, "nope") is None
