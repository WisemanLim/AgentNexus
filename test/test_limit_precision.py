"""Epic 63 — 한도 예약 정밀화 (D22-③): 토큰 예약은 예약한 분 슬롯에만 반환 · 출력 토큰 예약(자기 통과에는 입력만) ·
미사용 반환 · 주체별 진행 중 요청 상한 · 보유자·예약 만료는 Valkey 시계 · 실행하지 않는 계획은 예약을 남기지 않음 ·
`*`·`any` 예산 현황 버킷."""
import time
import uuid
from decimal import Decimal

import pytest

from app.core.auth import Principal
from app.core.config import settings
from app.core.errors import ApiError
from app.db import SessionLocal, utcnow
from app.models import RateLimit, UsageRecord
from app.services import llm, ratelimit


@pytest.fixture
def limit():
    made = []

    def add(**kw):
        lid = f"rl_e63_{uuid.uuid4().hex[:6]}"
        with SessionLocal() as db:
            db.add(RateLimit(id=lid, name=kw.pop("name", "e63"), enabled=True, target_type="model", target="*", **kw))
            db.commit()
        made.append(lid)
        return lid
    ratelimit.backend().reset()
    ratelimit.clear_cache()
    yield add
    with SessionLocal() as db:
        for lid in made:
            db.delete(db.get(RateLimit, lid))
        db.query(UsageRecord).filter(UsageRecord.target == "e63-model").delete()
        db.commit()
    ratelimit.backend().reset()


def _p(sub="usr_e63", team="team-e63") -> Principal:
    return Principal(sub=sub, kind="user", roles=["user"], team=team)


# ── C63-01 분 슬롯 — 다음 분으로 넘어간 예약 반환·미사용 반환은 새 창을 음수로 만들지 않는다 ──
def test_release_only_into_reserved_slot(monkeypatch):
    b = ratelimit.MemoryBackend()
    monkeypatch.setattr(ratelimit, "backend", lambda: b)
    t = [6_000_000.0]
    monkeypatch.setattr(ratelimit.time, "time", lambda: t[0])
    slot = int(t[0] // 60)
    b.window_add("tok:e63", 60, slot=slot)
    ratelimit.settle([("tok", "tok:e63", 60, slot)], 20)  # 같은 분 — 40 반환
    assert b.window_get("tok:e63") == 20
    b.window_add("tok:e63", 50, slot=slot)
    t[0] += 60  # 다음 분
    ratelimit.release([("tok", "tok:e63", 50, slot)])
    ratelimit.settle([("tok", "tok:e63", 70, slot)], 10)
    assert b.window_get("tok:e63") == 0  # 전에는 -50·-60 에서 시작해 이 분 한도가 그만큼 늘었다
    ratelimit.settle([("tok", "tok:e63", 10, slot)], 35)  # 더 쓴 만큼은 지금 창에
    assert b.window_get("tok:e63") == 25


# ── C63-02 출력 토큰 예약 — 자기 통과는 입력까지만, 다른 요청에는 예약 전체가 보임 · 정산 때 안 쓴 만큼 반환 ──
def test_output_tokens_reserved(limit):
    lid = limit(scope_type="user", scope="*", tokens_per_min=1000)
    p = _p()
    with SessionLocal() as db:
        first = ratelimit.check(db, p, "model", "e63-model", None, 100, output_tokens=950)
        second = ratelimit.check(db, p, "model", "e63-model", None, 100)
    assert first.allowed and not second.allowed  # 첫 요청의 출력 예약 950 이 보인다
    ratelimit.settle(first.holds, 300)  # 실제 300 — 750 반환
    with SessionLocal() as db:
        assert ratelimit.check(db, p, "model", "e63-model", None, 100).allowed
    assert ratelimit.backend().window_get(f"tok:{lid}:{p.sub}") == 400
    body = {"model": "auto", "messages": [{"role": "user", "content": "출력 예약 시험"}]}
    with SessionLocal() as db:
        plan = llm.prepare(db, llm.CallContext(p, "internal"), body)
        est = plan.holds[0][2]
        llm.discard(plan)
        plan = llm.prepare(db, llm.CallContext(p, "internal"), {**body, "max_tokens": 40})
        assert plan.holds[0][2] == est - llm.OUTPUT_TOKENS_DEFAULT + 40
        llm.discard(plan)


# ── C63-03 주체별 진행 중 요청 상한 — 넘으면 429, 끝나거나(정산) 버리면 반납 ──
def test_inflight_cap_per_subject(monkeypatch, limit):
    monkeypatch.setattr(settings, "model_max_inflight_per_subject", 2)
    p, body = _p(sub=f"usr_e63_{uuid.uuid4().hex[:6]}"), {"model": "auto", "messages": [{"role": "user", "content": "x"}]}
    ctx = llm.CallContext(p, "internal")
    with SessionLocal() as db:
        a, b = llm.prepare(db, ctx, body), llm.prepare(db, ctx, body)
        with pytest.raises(ApiError) as exc:
            llm.prepare(db, ctx, body)
        assert exc.value.status == 429 and "동시 요청" in exc.value.detail
        assert ratelimit.backend().holder_count(f"inflight:{p.sub}") == 2
        llm.discard(a)
        c = llm.prepare(db, ctx, body)
        llm.finalize(db, ctx, c, None, None, 1.0, [], "error")  # 실패로 끝난 호출도 반납
        llm.discard(b)
    assert ratelimit.backend().holder_count(f"inflight:{p.sub}") == 0


# ── C63-04 Valkey 시계 — 보유자·예약 만료는 서버 TIME (워커 시계가 틀어져도 그대로) ──
def test_holder_expiry_uses_valkey_time(monkeypatch):
    try:
        b = ratelimit.RedisBackend(settings.valkey_url)
        b._r.ping()
    except Exception:  # noqa: BLE001 — Valkey 없는 시험 환경(PG 회귀 컨테이너 등)
        pytest.skip("Valkey 없음")
    k = f"e63:{uuid.uuid4().hex[:6]}"
    assert b.holder_acquire(k, "m1", 1, 2) and not b.holder_acquire(k, "m2", 1, 2)
    real = time.time
    monkeypatch.setattr(ratelimit.time, "time", lambda: real() + 10_000)  # 워커 시계만 크게 앞섬
    assert b.holder_count(k) == 1 and b.amount_hold(k + ":a", "x", 5, 2) == 5
    monkeypatch.setattr(ratelimit.time, "time", real)
    time.sleep(2.3)
    assert b.holder_count(k) == 0 and b.amount_hold(k + ":a", "", 0, 2) == 0
    b._r.delete(f"anx:h:{k}", f"anx:a:{k}:a")


# ── C63-05 실행하지 않는 계획 — 라우팅 시험은 토큰 예약·진행 중 슬롯을 남기지 않는다 ──
def test_dry_runs_leave_no_holds(client, H, limit):
    lid = limit(scope_type="user", scope="*", tokens_per_min=1_000_000)
    for _ in range(3):
        res = client.post("/api/v1/gateway/routing/test", json={"model": "auto", "fail": []}, headers=H["operator"])
        assert res.status_code == 200 and res.json()["ok"], res.text
    me = client.get("/api/v1/me", headers=H["operator"]).json()["sub"]
    assert ratelimit.backend().window_get(f"tok:{lid}:{me}") == 0
    assert ratelimit.backend().holder_count(f"inflight:{me}") == 0


# ── C63-06 예산 현황 — `*`·`any` 한도도 실제 버킷별(팀, 팀 없는 주체는 사용자) ──
def test_budget_status_buckets(client, H, limit):
    lid = limit(scope_type="any", scope="*", budget_usd=Decimal("100"), period="month", name="e63-any")
    with SessionLocal() as db:
        for team, sub in (("team-e63", "usr_e63a"), (None, "usr_e63_solo")):
            db.add(UsageRecord(id=f"use_e63_{uuid.uuid4().hex[:8]}", ts=utcnow(), user_sub=sub, team=team, kind="model",
                               target="e63-model", cost=Decimal("1.5")))
        db.commit()
    items = [i for i in client.get("/api/v1/gateway/limits/status", headers=H["finops"]).json()["items"] if i["limit_id"] == lid]
    buckets = {i["bucket"] for i in items}
    assert {"team:team-e63", "user:usr_e63_solo"} <= buckets and "user:usr_e63a" not in buckets  # 팀 있는 주체는 팀 버킷
