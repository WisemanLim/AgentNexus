"""실행 메타 (SCR-C03 ⑦⑧ · C06 ⑥⑦ · D08 ⑤⑥) — 토큰·비용·첫 토큰·노드별 지연/비용·폴백·한도 초과·응답 소독·근거 사용.

사용 기록(UsageRecord.step_id)·라우팅 결정(trace_id)·실행 이벤트에서 계산한다(저장하지 않음).
"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Model, RoutingDecision, Run, RunEvent, RunStep, UsageRecord


def _ms(a, b) -> int | None:
    return int((b - a).total_seconds() * 1000) if a and b else None


def run_meta(db: Session, r: Run, steps: list[RunStep] | None = None) -> dict:
    steps = steps if steps is not None else list(db.scalars(select(RunStep).where(RunStep.run_id == r.id)
                                                            .order_by(RunStep.seq)))
    usage = list(db.scalars(select(UsageRecord).where(UsageRecord.run_id == r.id)))
    cost = sum((u.cost or Decimal("0")) for u in usage)
    by_step: dict[str, dict] = {}
    for u in usage:
        if not u.step_id:
            continue
        d = by_step.setdefault(u.step_id, {"cost": Decimal("0"), "tokens": 0, "calls": 0, "models": set()})
        d["cost"] += u.cost or Decimal("0")
        d["tokens"] += (u.tokens_in or 0) + (u.tokens_out or 0)
        d["calls"] += 1
        if u.kind == "model":
            d["models"].add(u.target)
    models = sorted({u.target for u in usage if u.kind == "model"})
    cats = {m.id: m.category for m in db.scalars(select(Model).where(Model.id.in_(models)))} if models else {}
    events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == r.id, RunEvent.type.in_(
        ("run.started", "delta", "step.rate_limited", "summarize.regenerate", "step.dry_run", "run.accepted_partial")))
        .order_by(RunEvent.id)))
    started = next((e.ts for e in events if e.type == "run.started"), None)
    first = next((e.ts for e in events if e.type == "delta"), None)
    fallbacks = [{"requested": d.requested, "chosen": d.chosen, "reason": (d.reason or "")[:200]}
                 for d in db.scalars(select(RoutingDecision).where(RoutingDecision.trace_id == r.trace_id,
                                                                  RoutingDecision.fallback_used.is_(True)))] \
        if r.trace_id else []
    retrieved, cited, sanitized = 0, set(), 0
    for s in steps:
        out = s.output if isinstance(s.output, dict) else {}
        if s.node in ("retriever", "remote") and s.status == "succeeded":
            retrieved += len(out.get("sources") or [])
        if s.node == "summarize":
            cited |= set(out.get("cited") or [])
        sanitized += int(out.get("sanitized") or 0)
    nodes = [{"step": s.name, "node": s.node, "tool": s.tool, "agent": s.agent_id, "status": s.status,
              "attempts": s.attempts, "latency_ms": _ms(s.started_at, s.finished_at),
              "cost": float(by_step.get(s.id, {}).get("cost", 0)), "tokens": by_step.get(s.id, {}).get("tokens", 0),
              "models": sorted(by_step.get(s.id, {}).get("models", set())), "error": s.error,
              "dry_run": bool(isinstance(s.output, dict) and s.output.get("dry_run"))} for s in steps]
    return {"tokens_in": sum(u.tokens_in or 0 for u in usage), "tokens_out": sum(u.tokens_out or 0 for u in usage),
            "cost": float(cost), "models": models,
            "local_only": bool(models) and all(cats.get(m) in ("local", "domain", "mock") for m in models),
            "latency_ms": _ms(r.started_at or r.created_at, r.finished_at), "first_token_ms": _ms(started, first),
            "fallbacks": fallbacks,
            "rate_limited": [e.data for e in events if e.type == "step.rate_limited"],
            "regenerated": [e.data for e in events if e.type == "summarize.regenerate"],
            "dry_run_calls": [e.data for e in events if e.type == "step.dry_run"],
            "accepted_partial": any(e.type == "run.accepted_partial" for e in events),
            "sources": {"retrieved": retrieved, "cited": len(cited), "unused": max(0, retrieved - len(cited))},
            "sanitized": sanitized, "nodes": nodes}
