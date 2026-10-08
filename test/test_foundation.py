"""Epic 1 — 기반: 마이그레이션, 감사 해시 체인, 공통 오류, OpenAPI 계약."""
import json
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db import Base, SessionLocal
from app.services import audit


def test_migration_creates_all_tables():
    insp = inspect(SessionLocal().get_bind())
    tables = set(insp.get_table_names())
    expected = set(Base.metadata.tables)
    assert expected <= tables, f"missing: {expected - tables}"


def test_migration_roundtrip(tmp_root):
    url = f"sqlite:///{tmp_root}/roundtrip.db"
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    # env.py 는 app.db.engine 을 쓰므로 별도 엔진으로 연결을 주입한다.
    eng = create_engine(url)
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    assert "audit_events" in inspect(eng).get_table_names()


def _restorable(row) -> dict:
    """raw SQL 재삽입용 — PG 는 JSON 컬럼을 dict 로 돌려주므로 문자열로 되돌린다."""
    vals = dict(row)
    if not isinstance(vals["data"], str):
        vals["data"] = json.dumps(vals["data"])
    return vals


def test_audit_chain_detects_tampering():
    first = audit.append("test.event", actor="alice", data={"n": 1})
    audit.append("test.event", actor="alice", data={"n": 2, "한글": "값"})
    third = audit.append("test.event", actor="bob", data={"n": 3})
    with SessionLocal() as db:
        assert audit.verify(db)["valid"] is True

        # 내용 변조 → content_tampered
        db.execute(text("UPDATE audit_events SET data = :d WHERE seq = :s"), {"d": json.dumps({"n": 99}), "s": first.seq})
        db.commit()
        res = audit.verify(db)
        assert res["valid"] is False and res["reason"] == "content_tampered" and res["broken_at"] == first.seq
        db.execute(text("UPDATE audit_events SET data = :d WHERE seq = :s"), {"d": json.dumps({"n": 1}), "s": first.seq})
        db.commit()
        assert audit.verify(db)["valid"] is True

        # 꼬리 절단 → tail_truncated
        row = db.execute(text("SELECT * FROM audit_events WHERE seq = :s"), {"s": third.seq}).mappings().one()
        db.execute(text("DELETE FROM audit_events WHERE seq = :s"), {"s": third.seq})
        db.commit()
        assert audit.verify(db)["reason"] == "tail_truncated"
        db.execute(text("INSERT INTO audit_events (seq, event_id, ts, actor, actor_kind, action, target, decision, "
                        "trace_id, data, prev_hash, hash) VALUES (:seq, :event_id, :ts, :actor, :actor_kind, :action, "
                        ":target, :decision, :trace_id, :data, :prev_hash, :hash)"), _restorable(row))
        db.commit()
        assert audit.verify(db)["valid"] is True


def test_audit_api_lists_and_verifies(client, H):
    audit.append("test.api", actor="carol")
    res = client.get("/api/v1/audit/events", params={"action": "test.api"}, headers=H["security"])
    assert res.status_code == 200 and res.json()["items"][0]["actor"] == "carol"
    assert client.get("/api/v1/audit/verify", headers=H["security"]).json()["valid"] is True


def test_problem_json_errors(client):
    res = client.get("/api/v1/does-not-exist")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "not_found"


def test_system_info(client):
    body = client.get("/api/v1/system/info").json()
    assert body["protocols"]["mcp"] == "2025-11-25" and body["protocols"]["a2a"] == "1.0"  # D15-② A2A 1.0 기본


def test_concurrent_migrate_and_seed_serialized():
    """Helm 다중 파드 동시 기동 — advisory lock 으로 마이그레이션·시드가 직렬화되어 둘 다 성공 (PostgreSQL 전용)."""
    import os
    import subprocess
    import sys

    import pytest
    if not os.environ["DATABASE_URL"].startswith("postgresql"):
        pytest.skip("advisory lock 은 PostgreSQL 전용 (SQLite 는 단일 프로세스)")
    root = os.path.join(os.path.dirname(__file__), "..")
    cmd = [sys.executable, "-c", "from alembic.config import main; main(['upgrade', 'head']);"
           "from app import seed; seed.run()"]
    procs = [subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) for _ in range(3)]
    outs = [p.communicate(timeout=120)[0].decode() for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
