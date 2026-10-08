"""Epic 52 — 모델 게이트웨이 정확성 (D20-①): 라우팅 규칙 timeout_ms(시도마다) · 동시 요청 예약 · 팀 없는 주체의 `any` 예산 · 클라이언트 키 예산."""
import asyncio
import time
import uuid
from decimal import Decimal

import pytest

from app.core.auth import Principal
from app.core.errors import ApiError
from app.db import SessionLocal, utcnow
from app.models import RateLimit, UsageRecord
from app.services import llm, ratelimit

OK = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
      "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class _Delayed:
    def __init__(self, delay: float):
        self.delay = delay

    async def chat(self, req, upstream):
        await asyncio.sleep(self.delay)
        return dict(OK)


def _t(name: str, delay: float) -> llm.Target:
    return llm.Target(f"e52-{name}-{uuid.uuid4().hex[:4]}", name, "mock", "mock", _Delayed(delay), Decimal(0), Decimal(0))


@pytest.fixture
def limits():
    made = []

    def add(**kw):
        lim = RateLimit(id=f"rl_e52_{uuid.uuid4().hex[:6]}", name=kw.pop("name", "e52"), enabled=True, target_type="model",
                        target="*", **kw)
        with SessionLocal() as db:
            db.add(lim)
            db.commit()
        made.append(lim.id)
        return lim.id
    ratelimit.backend().reset()
    ratelimit.clear_cache()
    yield add
    with SessionLocal() as db:
        for lid in made:
            db.delete(db.get(RateLimit, lid))
        db.query(UsageRecord).filter(UsageRecord.target == "e52-model").delete()
        db.commit()
    ratelimit.backend().reset()
    ratelimit.clear_cache()


# ── C52-01 라우팅 규칙 timeout_ms — 시도마다 적용(느린 후보 → 다음 후보), 전체는 시간 × 시도 수로 묶인다 ──
def test_rule_timeout_per_attempt_and_deadline():
    body = {"messages": [{"role": "user", "content": "ping"}]}
    plan = llm.Plan("auto", "internal", [_t("slow", 1.0), _t("fast", 0)], 0, "e52", timeout_s=0.2)
    res, tgt, attempts = asyncio.run(llm.execute(plan, body))
    assert tgt.upstream == "fast" and attempts[0]["status"] == "error" and attempts[0]["upstream_status"] == 504
    slow2 = llm.Plan("auto", "internal", [_t("slow", 1.0), _t("slow", 1.0)], 1, "e52", timeout_s=0.2)
    t0 = time.monotonic()
    with pytest.raises(ApiError) as exc:
        asyncio.run(llm.execute(slow2, body))  # 후보 2 × (재시도 1 + 1) = 4 시도, 각각 0.2s 에서 끊는다
    took = time.monotonic() - t0
    assert exc.value.status == 502 and [a["upstream_status"] for a in exc.value.extra["attempts"]] == [504] * 4
    assert took < 1.5, took  # 규칙이 없으면 느린 후보 둘에 각 1s × 2
    no_rule = llm.Plan("auto", "internal", [_t("slow", 0.3)], 0, None)
    assert asyncio.run(llm.execute(no_rule, body))[1].upstream == "slow"  # 규칙 시간 없음 = 공급자 기본 시간


# ── C52-02 예약 — 진행 중 요청의 예상 토큰·비용을 함께 세어 동시 요청이 한도를 넘지 못한다 · 정산·반환 ──
def test_concurrent_reservations(limits):
    p = Principal(sub="usr_e52", kind="user", roles=["user"], team="team-e52")
    limits(scope_type="user", scope="*", tokens_per_min=100)
    with SessionLocal() as db:
        first = ratelimit.check(db, p, "model", "fast-commercial", None, 60)
        second = ratelimit.check(db, p, "model", "fast-commercial", None, 60)  # 첫 요청이 아직 끝나지 않음
    assert first.allowed and not second.allowed and "토큰 한도" in second.reason
    ratelimit.settle(first.holds, 90)  # 예상보다 30 더 씀
    with SessionLocal() as db:
        assert not ratelimit.check(db, p, "model", "fast-commercial", None, 20).allowed  # 90 + 20 > 100
        third = ratelimit.check(db, p, "model", "fast-commercial", None, 10)
    assert third.allowed
    ratelimit.release(third.holds)

    limits(scope_type="team", scope="*", budget_usd=Decimal("1.0"), period="day")
    with SessionLocal() as db:
        a = ratelimit.check(db, p, "model", "x", None, 0, Decimal("0.6"))
        b = ratelimit.check(db, p, "model", "x", None, 0, Decimal("0.6"))  # 0 + 0.6(진행 중) + 0.6 > 1.0
        assert a.allowed and not b.allowed and "예약" in b.reason
        ratelimit.release(a.holds)  # 실패 — 예약 반환
        c = ratelimit.check(db, p, "model", "x", None, 0, Decimal("0.6"))
        assert c.allowed
        db.add(UsageRecord(id=f"use_e52_{uuid.uuid4().hex[:8]}", ts=utcnow(), user_sub=p.sub, team=p.team, kind="model",
                           target="e52-model", cost=Decimal("0.6")))
        db.commit()
        ratelimit.settle(c.holds, 0)  # 성공 — 예약 반환, 실제 비용은 사용 기록으로 (이 워커 spend 캐시도 비움)
        assert not ratelimit.check(db, p, "model", "x", None, 0, Decimal("0.5")).allowed  # 0.6 + 0.5 > 1.0
        assert ratelimit.check(db, p, "model", "x", None, 0, Decimal("0.3")).allowed


# ── C52-03 `any` 예산 — 팀 없는 주체는 그 사용자 사용액으로 (전에는 빈 팀 = 항상 0) ──
def test_any_budget_applies_without_team(limits):
    limits(scope_type="any", scope="*", budget_usd=Decimal("0.01"), period="day")
    lone = Principal(sub="usr_e52_lone", kind="user", roles=["user"], team=None)
    with SessionLocal() as db:
        db.add(UsageRecord(id=f"use_e52_{uuid.uuid4().hex[:8]}", ts=utcnow(), user_sub=lone.sub, team=None, kind="model",
                           target="e52-model", cost=Decimal("0.02")))
        db.commit()
        r = ratelimit.check(db, lone, "model", "fast-commercial")
    assert not r.allowed and "예산 초과" in r.reason


# ── C52-04 클라이언트 키 예산 — 키별 사용액(사용 기록 client_key) · finalize 가 키 id 를 남긴다 ──
def test_client_key_budget(limits):
    limits(scope_type="client_key", scope="*", budget_usd=Decimal("0.01"), period="day")
    key = Principal(sub="usr_operator", kind="client_key", roles=["user"], team="team-platform", token_id="pat_e52key")
    other = Principal(sub="usr_operator", kind="client_key", roles=["user"], team="team-platform", token_id="pat_e52other")
    with SessionLocal() as db:
        plan = llm.Plan("fast-commercial", "public", [_t("fast", 0)], 0, None)
        plan.targets[0].price_in = Decimal("10000")  # 1M 토큰당 — 1 토큰 0.01 USD
        rec = llm.finalize(db, llm.CallContext(key, "public"), plan, plan.targets[0], {"prompt_tokens": 2, "completion_tokens": 0}, 1.0, [])
        assert rec.client_key == "pat_e52key" and rec.cost == Decimal("0.02")
        rec.target = "e52-model"
        db.commit()
        ratelimit.clear_cache()
        assert not ratelimit.check(db, key, "model", "fast-commercial").allowed
        assert ratelimit.check(db, other, "model", "fast-commercial").allowed  # 다른 키는 따로
