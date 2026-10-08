"""보존 정책 (FR-O02 보존, COMPLIANCE: 텔레메트리 보관/삭제 검증 CC6.5/A.8.10).

Setting 'retention' = {audit_days, spans_days, logs_days, usage_days, run_events_days}. 감사 로그는 삭제하지 않고
아카이브(해시 체인 증명) 후 파기 단계로 넘기며, 텔레메트리는 기간 경과분을 삭제하고 건수를 감사에 남긴다.
MCP 중계(mcp_relays)·Tasks(mcp_tasks) 는 수명이 짧은 실행 상태라 정책과 무관하게 1일 경과분을 지운다(작업 TTL 1시간).
"""
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import unbounded, utcnow
from app.models import LogEntry, McpRelay, McpTask, RunEvent, Setting, Span, UsageRecord
from app.services import audit

DEFAULTS = {"audit_days": 365, "spans_days": 14, "logs_days": 30, "usage_days": 400, "run_events_days": 90}
STATE_DAYS = 1


def policy(db: Session) -> dict:
    row = db.get(Setting, "retention")
    return {**DEFAULTS, **(row.value if row and isinstance(row.value, dict) else {})}


def run(db: Session, actor: str, overrides: dict | None = None, now=None) -> dict:
    now = now or utcnow()
    pol = {**policy(db), **(overrides or {})}
    unbounded(db)  # 밀린 정리가 상한(2분)에 걸려 통째로 롤백·반복되지 않게 (65th)
    deleted = {}
    for key, model, col in (("spans_days", Span, Span.start_time), ("logs_days", LogEntry, LogEntry.ts),
                            ("usage_days", UsageRecord, UsageRecord.ts), ("run_events_days", RunEvent, RunEvent.ts)):
        cutoff = now - timedelta(days=pol[key])
        deleted[model.__tablename__] = db.execute(delete(model).where(col < cutoff)).rowcount
    for model in (McpRelay, McpTask):
        deleted[model.__tablename__] = db.execute(delete(model).where(model.created_at < now - timedelta(days=STATE_DAYS))).rowcount
    db.commit()
    archive = audit.archive_before(now - timedelta(days=pol["audit_days"]), actor)
    audit.append("retention.run", actor=actor, data={"policy": pol, "deleted": deleted,
                                                     "archive": archive["archive_id"] if archive else None})
    return {"policy": pol, "deleted": deleted, "archive": archive}
