"""Epic 47 — 성능 정리 (D18-④): 관측 요약 SQL 집계(이전 파이썬 집계와 같은 값) · 경보 평가 요약 공유 · listen 지문 공유 · 저장소 위생."""
import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.api import observe
from app.db import SessionLocal, utcnow
from app.models import AlertRule, RoutingDecision, UsageRecord
from app.services import mcp_modern


def _reference(db, minutes: int) -> dict:
    """D18 이전 구현 — 행을 모두 읽어 파이썬으로 집계 (비교 기준)."""
    since = utcnow() - timedelta(minutes=minutes)
    usage = db.query(UsageRecord).filter(UsageRecord.ts >= since).all()
    by_kind: dict = {}
    for u in usage:
        k = by_kind.setdefault(u.kind, {"calls": 0, "errors": 0, "latency": [], "tokens": 0, "cost": Decimal(0)})
        k["calls"] += 1
        k["errors"] += 1 if u.status not in ("ok",) else 0
        k["latency"].append(u.latency_ms)
        k["tokens"] += u.tokens_in + u.tokens_out
        k["cost"] += u.cost
    kinds = {k: {"calls": v["calls"], "errors": v["errors"], "error_rate": round(v["errors"] / v["calls"], 4),
                 "p95_latency_ms": observe._p95(v["latency"]), "tokens": v["tokens"], "cost_usd": float(v["cost"])}
             for k, v in by_kind.items()}
    models: dict = {}
    teams: dict = {}
    for u in usage:
        if u.kind == "model":
            models[u.target] = models.get(u.target, Decimal(0)) + u.cost
        teams[u.team or "-"] = teams.get(u.team or "-", Decimal(0)) + u.cost
    decisions = db.query(RoutingDecision).filter(RoutingDecision.ts >= since).all()
    return {"by_kind": kinds, "calls": len(usage), "cost_usd": float(sum((u.cost for u in usage), Decimal(0))),
            "fallback_rate": round(sum(1 for d in decisions if d.fallback_used) / len(decisions), 4) if decisions else 0.0,
            "top_models": sorted([{"model": m, "cost_usd": float(c)} for m, c in models.items()], key=lambda x: (-x["cost_usd"], x["model"]))[:5],
            "cost_by_team": sorted([{"team": t, "cost_usd": float(c)} for t, c in teams.items()], key=lambda x: (-x["cost_usd"], x["team"]))}


# ── C47-01 SQL 집계 = 이전 파이썬 집계 (종류별 호출·오류·p95·토큰·비용 · 상위 모델 · 팀 · 폴백률) ──
def test_summary_sql_matches_reference(client):
    now = utcnow()
    with SessionLocal() as db:
        for i in range(37):
            db.add(UsageRecord(id=f"use_e47_{uuid.uuid4().hex[:10]}", ts=now - timedelta(seconds=i), kind=("model", "tool", "remote_agent")[i % 3],
                               target=f"e47-m{i % 4}", team=(None, "team-a", "team-b")[i % 3], latency_ms=(i * 37) % 251,
                               tokens_in=i, tokens_out=2 * i, cost=Decimal(str(round(0.0013 * (i % 7), 6))),
                               status="ok" if i % 5 else "error"))
        db.commit()
        got, ref = observe.summary_data(db, 60), _reference(db, 60)
    for key in ("by_kind", "calls", "fallback_rate", "top_models", "cost_by_team"):
        assert got[key] == ref[key], key
    assert abs(got["cost_usd"] - ref["cost_usd"]) < 1e-9


# ── C47-02 경보 평가 — 같은 창의 규칙들은 요약을 한 번만 ──
def test_alert_rules_share_summary(client, monkeypatch):
    calls = []
    real = observe.summary_data
    monkeypatch.setattr(observe, "summary_data", lambda db, m: calls.append(m) or real(db, m))
    with SessionLocal() as db:
        ids = []
        for i in range(3):
            r = AlertRule(id=f"alr_e47_{i}", name=f"e47-{i}", metric="error_rate", op=">", threshold=2.0, window_s=300,
                          severity="info", enabled=True)
            db.add(r)
            ids.append(r.id)
        db.commit()
        try:
            observe.evaluate(db)
        finally:
            for rid in ids:
                db.delete(db.get(AlertRule, rid))
            db.commit()
    assert calls.count(5) == 1


# ── C47-03 listen 지문 — 같은 주체의 구독은 폴링 주기 안에서 한 번만 조회, 다른 주체는 따로 ──
def test_fingerprint_shared_per_principal(client, monkeypatch):
    monkeypatch.setattr(mcp_modern, "LISTEN_POLL_S", 5.0)
    mcp_modern._FP_CACHE.clear()
    fetched = []

    async def fetch():
        fetched.append(1)
        return [{"name": "t"}]

    async def scenario():
        a = await mcp_modern._digest_cached(("jira", "u1", "toolsListChanged"), fetch)
        b = await mcp_modern._digest_cached(("jira", "u1", "toolsListChanged"), fetch)
        c = await mcp_modern._digest_cached(("jira", "u2", "toolsListChanged"), fetch)
        return a, b, c
    a, b, c = asyncio.run(scenario())
    assert a == b == c and len(fetched) == 2


# ── C47-04 저장소 위생 — .wds/ 는 무시, .docs/ 는 커밋 대상 ──
def test_gitignore_wds_only():
    lines = [ln.strip() for ln in (Path(__file__).resolve().parents[1] / ".gitignore").read_text().splitlines()]
    assert ".wds/" in lines and not any(ln.startswith(".docs") for ln in lines)
