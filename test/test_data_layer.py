"""Epic 61 — 데이터 계층 견고성 (D22-①): PostgreSQL 문장·잠금 대기 상한(세션 옵션) · 마이그레이션은 상한 없이 ·
SQLite busy_timeout · 개요 KPI 인덱스(migration 0021) · 최근 표본 정렬."""
import pytest
from alembic import command
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import OperationalError

from app import db as dbmod
from app.api import observe
from app.core.config import settings
from app.db import SessionLocal, engine, utcnow
from test.conftest import _alembic_cfg

PG = engine.dialect.name == "postgresql"


# ── C61-01 세션 상한 — PostgreSQL 은 문장 2분·잠금 15초·idle-in-transaction 끔(기본), SQLite 는 잠금 대기 30초 ──
def test_session_limits_applied():
    with engine.connect() as c:
        if PG:
            got = [c.execute(text(f"SHOW {k}")).scalar() for k in
                   ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")]
            assert got == ["2min", "15s", "0"], got
        else:
            assert c.execute(text("PRAGMA busy_timeout")).scalar() == 30000


# ── C61-02 상한이 실제로 걸린다 — env 값으로 만든 엔진에서 넘는 문장은 취소 (PostgreSQL) ──
@pytest.mark.skipif(not PG, reason="PostgreSQL 세션 옵션")
def test_statement_timeout_cancels(monkeypatch):
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 300)
    eng = dbmod._make_engine()
    try:
        with eng.connect() as c:
            assert c.execute(text("SHOW statement_timeout")).scalar() == "300ms"
            with pytest.raises(OperationalError, match="statement timeout"):
                c.execute(text("SELECT pg_sleep(2)"))
    finally:
        eng.dispose()


# ── C61-03 마이그레이션은 앱 상한 없이 — 큰 테이블 인덱스·다른 파드 잠금 대기 (PostgreSQL 은 SET LOCAL 0) ──
def test_migrations_run_without_session_limits():
    seen = []
    listen = lambda conn, cur, stmt, *a: seen.append(stmt)  # noqa: E731
    cfg = _alembic_cfg()
    event.listen(engine, "before_cursor_execute", listen)
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")  # 이미 head — 상한 해제·잠금만 실행
            conn.commit()
    finally:
        event.remove(engine, "before_cursor_execute", listen)
    if PG:
        assert "SET LOCAL statement_timeout = 0" in seen and "SET LOCAL lock_timeout = 0" in seen
        assert seen.index("SET LOCAL lock_timeout = 0") < next(i for i, s in enumerate(seen) if "pg_advisory_xact_lock" in s)


# ── C61-04 인덱스 (migration 0021) — 자식 스팬 집계·기간 안 실행 시작 이벤트 ──
def test_overview_indexes():
    ins = inspect(engine)
    idx = {i["name"]: i["column_names"] for t in ("spans", "run_events") for i in ins.get_indexes(t)}
    assert idx["ix_spans_parent_trace"] == ["parent_id", "trace_id"]
    assert idx["ix_run_events_type_ts"] == ["type", "ts"]


# ── C61-05 개요 표본 — 최근 500 (순서 없는 표본은 p95 가 실행마다 흔들림) ──
def test_overview_sample_ordered():
    seen = []
    listen = lambda conn, cur, stmt, *a: seen.append(stmt)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listen)
    try:
        with SessionLocal() as db:
            observe._overview_extra(db, utcnow())
    finally:
        event.remove(engine, "before_cursor_execute", listen)
    sample = next(s for s in seen if "max(spans_1.duration_ms)" in s)
    assert "ORDER BY spans.start_time DESC" in sample and "LIMIT" in sample.upper()
