"""Epic 35 — SLO·오류 예산 (D15-④): 데이터 경로 분류 · 워커별 분 단위 카운터 누적 · 30일 이동 창 SLI·예산 잔량 ·
다중 창 소진율(page 14.4 / ticket 6) · 상태 변화 알림·회복 · API(목록·수정 사유·감사·검증) · 실제 요청 반영.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditEvent, Notification, SliMinute, Slo
from app.services import slo

FUTURE = datetime(2031, 3, 1, 12, 0, tzinfo=timezone.utc)  # 다른 시험의 실제 트래픽과 섞이지 않는 시점


def row(db, at: datetime, cls: str, total: int, errors: int = 0, slow: dict | None = None, worker: str = "e35"):
    m = at.replace(second=0, microsecond=0)
    db.add(SliMinute(id=f"{m:%Y%m%dT%H%M}:{cls}:{worker}", minute=m, day=f"{m:%Y-%m-%d}", cls=cls, worker=worker, total=total,
                     errors=errors, **{f"gt{b}": (slow or {}).get(b, 0) for b in slo.BOUNDS}))


# ── C35-01 분류·누적 — 데이터 경로만 · 같은 워커·분은 한 행에 더하고 워커가 다르면 행이 따로 ──
def test_classify_and_flush_accumulates():
    assert slo.classify("/mcp/jira") == "mcp" and slo.classify("/a2a/x") == "a2a" and slo.classify("/v1/chat/completions") == "model"
    assert slo.classify("/api/v1/me") is None and slo.classify("/console/") is None and slo.classify("/v1/traces") is None
    slo.flush("e35-w0")  # 앞선 요청 카운터 비우기
    slo.observe("/mcp/jira", 200, 0.05)
    slo.observe("/mcp/jira", 503, 0.3)
    slo.observe("/api/v1/me", 500, 9)  # 관리 API — 세지 않음
    assert slo.flush("e35-w1") >= 1
    slo.observe("/mcp/jira", 200, 2.5)
    slo.flush("e35-w1")
    slo.observe("/mcp/jira", 200, 0.01)
    slo.flush("e35-w2")
    with SessionLocal() as db:
        mine = db.scalars(select(SliMinute).where(SliMinute.worker == "e35-w1", SliMinute.cls == "mcp")).all()
        other = db.scalars(select(SliMinute).where(SliMinute.worker == "e35-w2", SliMinute.cls == "mcp")).all()
    assert sum(r.total for r in mine) == 3 and sum(r.errors for r in mine) == 1
    assert sum(r.gt250 for r in mine) == 2 and sum(r.gt2000 for r in mine) == 1 and sum(r.gt5000 for r in mine) == 0
    assert sum(r.total for r in other) == 1


# ── C35-02 계산 — SLI·예산 잔량·소진율·page / 지연 SLO 는 기준 초과 수 ──
def test_report_budget_and_burn():
    with SessionLocal() as db:
        db.merge(Slo(id="e35-avail", name="e35 가용성", kind="availability", scope="a2a", target=0.999, window_days=30, enabled=False,
                     updated_at=FUTURE))
        db.merge(Slo(id="e35-lat", name="e35 지연", kind="latency", scope="a2a", target=0.99, threshold_ms=500, window_days=30,
                     enabled=False, updated_at=FUTURE))
        row(db, FUTURE - timedelta(days=10), "a2a", 99_000, 50, {500: 400})
        row(db, FUTURE - timedelta(minutes=40), "a2a", 900, 15, {500: 300})
        row(db, FUTURE - timedelta(minutes=2), "a2a", 100, 5, {500: 10})
        row(db, FUTURE - timedelta(minutes=2), "mcp", 5_000, 4_000)  # 다른 분류는 범위 밖
        db.commit()
        avail = slo.report(db, db.get(Slo, "e35-avail"), FUTURE)
        lat = slo.report(db, db.get(Slo, "e35-lat"), FUTURE)
    assert avail["total"] == 100_000 and avail["bad"] == 70 and avail["sli"] == 0.9993
    assert avail["budget"]["allowed"] == 100.0 and avail["budget"]["remaining"] == 0.3
    assert avail["burn"]["1h"] == {"total": 1000, "bad": 20, "burn_rate": 20.0} and avail["burn"]["5m"]["burn_rate"] == 50.0
    assert avail["state"] == "page"
    assert len(avail["series"]) == 30 and avail["series"][-1]["total"] == 1000 and avail["series"][-11]["bad"] == 50
    assert lat["bad"] == 710 and lat["budget"]["remaining"] == 0.29 and lat["burn"]["1h"]["burn_rate"] == 31.0
    assert lat["burn"]["5m"]["burn_rate"] == 10.0 and lat["state"] == "ticket"  # 5분 10 < 14.4 → page 아님, 6h·30m ≥ 6 → ticket


# ── C35-03 경보 — 상태 바뀔 때만 알림(page danger) · 같은 상태 재평가는 조용 · 회복 알림 ──
def test_evaluate_notifies_on_state_change():
    with SessionLocal() as db:
        s = db.get(Slo, "e35-avail")
        s.enabled, s.alert_state = True, None
        db.commit()
        first = slo.evaluate(db, FUTURE)
        again = slo.evaluate(db, FUTURE)
        later = slo.evaluate(db, FUTURE + timedelta(days=2))  # 짧은 창 비고 예산 잔량 30% → ok
        notes = db.scalars(select(Notification).where(Notification.kind == "slo", Notification.title.like("SLO e35 가용성%"))
                           .order_by(Notification.ts)).all()
        db.get(Slo, "e35-avail").enabled = False
        db.commit()
    assert {"id": "e35-avail", "from": "ok", "to": "page"} in first and not [c for c in again if c["id"] == "e35-avail"]
    assert {"id": "e35-avail", "from": "page", "to": "ok"} in later
    assert [n.severity for n in notes][-2:] == ["danger", "info"] and "14.4" in notes[-2].body


# ── C35-04 API — 기본 SLO 2종 · 수정은 admin·사유·감사 · 검증 ──
def test_slo_api(client, H):
    out = client.get("/api/v1/observe/slos", headers=H["operator"]).json()
    ids = {s["id"]: s for s in out["items"]}
    assert ids["gateway-availability"]["target"] == 0.9995 and ids["gateway-latency"]["threshold_ms"] == 2000
    assert out["alerting"]["page_burn"] == 14.4 and 2000 in out["thresholds"]
    assert client.patch("/api/v1/observe/slos/gateway-latency", json={"target": 0.995, "reason": "x"}, headers=H["dev"]).status_code == 403
    assert client.patch("/api/v1/observe/slos/gateway-latency", json={"target": 0.995}, headers=H["admin"]).status_code == 422  # 사유 필수
    bad = client.patch("/api/v1/observe/slos/gateway-availability", json={"threshold_ms": 500, "reason": "x"}, headers=H["admin"])
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_threshold"
    assert client.patch("/api/v1/observe/slos/gateway-latency", json={"window_days": 3, "reason": "x"}, headers=H["admin"]).status_code == 422
    ok = client.patch("/api/v1/observe/slos/gateway-latency", json={"threshold_ms": 1000, "reason": "E35 시험"}, headers=H["admin"])
    assert ok.status_code == 200 and ok.json()["threshold_ms"] == 1000
    client.patch("/api/v1/observe/slos/gateway-latency", json={"threshold_ms": 2000, "reason": "되돌림"}, headers=H["admin"])
    with SessionLocal() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "observe.slo.update").order_by(AuditEvent.seq.desc())).first()
        assert ev.data["reason"] == "되돌림" and ev.data["before"]["threshold_ms"] == 1000


# ── C35-05 실제 요청 반영 — 데이터 경로 요청이 기본 SLO 에 바로 잡힌다 ──
def test_real_traffic_counted(client, H):
    before = {s["id"]: s for s in client.get("/api/v1/observe/slos", headers=H["operator"]).json()["items"]}["gateway-availability"]["total"]
    for _ in range(3):
        assert client.get("/v1/models", headers=H["dev"]).status_code == 200
    after = {s["id"]: s for s in client.get("/api/v1/observe/slos", headers=H["operator"]).json()["items"]}["gateway-availability"]
    assert after["total"] >= before + 3 and after["state"] in ("ok", "ticket", "page", "exhausted")
