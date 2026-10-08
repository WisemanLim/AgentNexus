"""Epic 55 — 관측·카운터 성능 (D20-④): 개요 KPI·usage SQL 집계(이전 파이썬 집계와 같은 값) · listen 보유자 단위 슬롯 ·
도구 위험도 단어 단위 분류."""
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.api import observe
from app.db import SessionLocal, utcnow
from app.models import Run, RunEvent, Span, UsageRecord
from app.services import governance, ratelimit, review


def _overview_reference(db, since) -> tuple[list, list]:
    """D20 이전 구현 — 스팬마다 자식 조회 · 이벤트 전부 읽기 (비교 기준)."""
    over = []
    for sp in db.scalars(select(Span).where(Span.start_time >= since, Span.name.like("%tools/call%")).limit(500)):
        kids = [float(c.duration_ms) for c in db.scalars(select(Span).where(Span.parent_id == sp.id, Span.trace_id == sp.trace_id))]
        if kids:
            over.append(max(0.0, float(sp.duration_ms) - max(kids)))
    first, starts = [], {}
    for ev in db.scalars(select(RunEvent).where(RunEvent.ts >= since, RunEvent.type.in_(("run.started", "delta")))
                         .order_by(RunEvent.id)):
        if ev.type == "run.started":
            starts[ev.run_id] = ev.ts
        elif ev.run_id in starts:
            first.append((ev.ts - starts.pop(ev.run_id)).total_seconds() * 1000)
    return over, first


# ── C55-01 개요 KPI — 게이트웨이 추가 지연(자식 최대 집계)·첫 토큰(다음 시작 전 첫 delta)이 이전 구현과 같다 ──
def test_overview_extra_matches_reference(client):
    now = utcnow()
    since = now - timedelta(minutes=30)
    with SessionLocal() as db:
        for i in range(12):
            tid = uuid.uuid4().hex
            parent = Span(id=uuid.uuid4().hex[:16], trace_id=tid, name="POST /mcp/{server_id} tools/call", service="e55",
                          kind="server", start_time=now - timedelta(seconds=i), end_time=now, duration_ms=50 + i * 7)
            db.add(parent)
            for k in range(i % 3):  # 자식 0~2 개 (0 이면 표본 제외)
                db.add(Span(id=uuid.uuid4().hex[:16], trace_id=tid, parent_id=parent.id, name="mcp tools/call x", service="e55",
                            kind="client", start_time=now, end_time=now, duration_ms=10 + k * 13 + i))
        for r in range(6):
            run_id = f"run_e55_{uuid.uuid4().hex[:8]}"
            db.add(Run(id=run_id, user_sub="usr_user", profile_id="tpl-qa", status="succeeded", request="e55"))
            t0 = now - timedelta(minutes=10, seconds=r)
            events = [("run.started", 0), ("delta", 120 + r * 40), ("delta", 900)]
            if r % 2:  # 재시작 — 첫 시작 뒤 delta 없이 다시 시작 → 두 번째 시작 기준
                events = [("run.started", 0), ("run.started", 300), ("delta", 450 + r)]
            if r == 5:  # delta 없는 실행
                events = [("run.started", 0)]
            for typ, ms in events:
                db.add(RunEvent(run_id=run_id, ts=t0 + timedelta(milliseconds=ms), type=typ, data={}))
            db.flush()
        db.commit()
        got = observe._overview_extra(db, since)
        ref_over, ref_first = _overview_reference(db, since)
    assert got["gateway_overhead_p95_ms"] == observe._p95([int(x) for x in ref_over])
    assert got["first_token_p95_ms"] == observe._p95([int(x) for x in ref_first])
    assert ref_over and ref_first


# ── C55-02 usage — 모든 그룹 기준에서 SQL 집계 = 이전 파이썬 집계 (day 는 UTC 날짜) ──
def test_usage_sql_matches_reference(client, H):
    now = utcnow()
    with SessionLocal() as db:
        for i in range(40):
            db.add(UsageRecord(id=f"use_e55_{uuid.uuid4().hex[:10]}", ts=now - timedelta(hours=i * 7), kind=("model", "tool", "remote_agent")[i % 3],
                               target=f"e55-t{i % 4}", team=(None, "team-a", "", "team-b")[i % 4], agent=(None, "ag1")[i % 2],
                               user_sub=("usr_user", "usr_dev")[i % 2], provider=("mock", None)[i % 2], tokens_in=i, tokens_out=2 * i,
                               cost=Decimal(str(round(0.0007 * (i % 9), 6))), latency_ms=(i * 31) % 211,
                               status=("ok", "error", None)[i % 3] if i % 5 else "ok"))
        db.commit()
    for group in ("model", "tool", "agent", "team", "user", "kind", "provider", "day"):
        got = client.get("/api/v1/usage", params={"group_by": group}, headers=H["finops"]).json()
        with SessionLocal() as db:
            ref = _usage_reference(db, group)
        assert {r["key"]: r for r in got["items"]} == {r["key"]: r for r in ref["items"]}, group
        assert abs(got["total_cost_usd"] - ref["total_cost_usd"]) < 1e-9, group


def _usage_reference(db, group_by: str) -> dict:
    stmt = select(UsageRecord).where(UsageRecord.ts >= utcnow() - timedelta(days=30))
    if group_by == "model":
        stmt = stmt.where(UsageRecord.kind == "model")
    elif group_by == "tool":
        stmt = stmt.where(UsageRecord.kind == "tool")
    rows: dict = {}
    for u in db.scalars(stmt):
        key = u.ts.date().isoformat() if group_by == "day" else (getattr(u, observe.GROUPS[group_by].key) or "-")
        r = rows.setdefault(key, {"key": key, "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": Decimal(0), "latency": 0, "errors": 0})
        r["calls"] += 1
        r["tokens_in"] += u.tokens_in
        r["tokens_out"] += u.tokens_out
        r["cost"] += u.cost
        r["latency"] += u.latency_ms
        r["errors"] += 0 if u.status == "ok" else 1
    items = [{**{k: v for k, v in r.items() if k not in ("cost", "latency")}, "cost_usd": float(r["cost"]),
              "avg_latency_ms": round(r["latency"] / r["calls"], 1)} for r in rows.values()]
    return {"items": items, "total_cost_usd": float(sum((r["cost"] for r in rows.values()), Decimal(0)))}


# ── C55-03 listen 슬롯 — 보유자 단위 만료(넣을 때 정해짐): 반납 없이 죽은 워커 몫은 주체가 계속 구독해도 풀린다 ──
def test_holder_slots_expire_while_active(monkeypatch):
    b = ratelimit.MemoryBackend()
    t = [1_000_000.0]
    monkeypatch.setattr(ratelimit.time, "time", lambda: t[0])
    for i in range(5):
        assert b.holder_acquire("listen:u", f"dead:{i}", 5, 960)  # 죽은 워커가 쥔 5개
    t[0] += 500
    assert not b.holder_acquire("listen:u", "live:1", 5, 960)  # 계속 시도(활동)해도
    t[0] += 461  # 첫 확보 + 960초 지남
    assert b.holder_acquire("listen:u", "live:1", 5, 960) and b.holder_count("listen:u") == 1
    c = ratelimit.MemoryBackend()  # 비교: 증감 카운터는 변경마다 TTL 이 늘어 활동 중에는 풀리지 않는다
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t[0])
    c.counter_add("k", 5, 960)
    for _ in range(4):
        t[0] += 500
        c.counter_add("k", 0, 960)
    assert c.counter_add("k", 0, 960) == 5


# ── C55-04 도구 위험도 — 단어 단위(camelCase·약어) · 빠졌던 동사 · 부분 문자열 오탐 없음 ──
def test_tool_risk_words():
    hi = governance.classify_risk
    for name in ("createIssue", "deleteRepository", "dropTable", "revokeToken", "grantAccess", "transferFunds", "refund_order",
                 "HTTPPost", "orders.cancel", "truncate-logs"):
        assert hi({"name": name}, trusted=False) == "high", name
    for name in ("getUserProfile", "listAddresses", "settings_get", "search_issues", "lookup", "readme"):
        assert hi({"name": name}, trusted=False) == "low", name
    assert [review.tool_risk(n) for n in ("deleteRepo", "grantAccess", "addComment", "settings_get", "listAddresses", "adminConsole")] == \
        ["high", "high", "medium", "low", "low", "high"]
