"""Rate limits · 예산 (FR-G04) — 사용자·에이전트·팀·클라이언트 키 × 모델·도구·에이전트 단위
요청(토큰 버킷)·토큰(분당)·비용(일/월) 한도. 초과 시 429 또는 저비용 모델 폴백. 백엔드: memory | Valkey(redis).
"""
import fnmatch
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.config import settings
from app.models import RateLimit, UsageRecord
from app.services.notify import notify


# ── 백엔드 ──
class MemoryBackend:
    def __init__(self):
        self._buckets: dict[str, tuple[float, float]] = {}
        self._windows: dict[str, tuple[int, int]] = {}
        self._counters: dict[str, tuple[int, float]] = {}
        self._holders: dict[str, dict[str, float]] = {}  # 보유자 단위 슬롯 (D20-④)
        self._amounts: dict[str, dict[str, tuple[int, float]]] = {}  # 예약 단위 금액 (59th)
        self._lock = threading.Lock()

    MAX_BUCKETS = 50_000  # 키가 요청마다 달라도(위조 IP 등) 메모리가 끝없이 늘지 않게 — 넘으면 오래된 것부터 (65th)

    def take(self, key: str, rate: float, burst: int, cost: float = 1.0) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            if key not in self._buckets and len(self._buckets) >= self.MAX_BUCKETS:
                for k in list(self._buckets)[: self.MAX_BUCKETS // 10]:
                    del self._buckets[k]
            tokens, ts = self._buckets.get(key, (float(burst), now))
            tokens = min(float(burst), tokens + (now - ts) * rate)
            if tokens >= cost:
                self._buckets[key] = (tokens - cost, now)
                return True, 0.0
            self._buckets[key] = (tokens, now)
            return False, (cost - tokens) / rate if rate > 0 else 60.0

    def window_get(self, key: str, window_s: int = 60) -> int:
        slot = int(time.time() // window_s)
        with self._lock:
            s, n = self._windows.get(key, (slot, 0))
            return n if s == slot else 0

    def window_add(self, key: str, n: int, window_s: int = 60, slot: int | None = None) -> int:
        """분 창에 더하기 — slot 을 주면 그 창에만(지난 창이면 아무것도 안 함: 예약 반환이 다음 분을 음수로 만들지 않게, D22-③)."""
        now_slot = int(time.time() // window_s)
        if slot is not None and slot < now_slot:
            return 0
        slot = now_slot
        with self._lock:
            s, cur = self._windows.get(key, (slot, 0))
            cur = cur + n if s == slot else n
            self._windows[key] = (slot, cur)
            return cur

    def counter_add(self, key: str, delta: int, ttl_s: int) -> int:
        """동시 사용 수 같은 증감 카운터 — 0 아래로 내려가지 않고, 마지막 변경 뒤 ttl_s 가 지나면 0 (보유 워커가 죽어도 풀린다)."""
        now = time.monotonic()
        with self._lock:
            cur, exp = self._counters.get(key, (0, 0.0))
            cur = max((cur if exp > now else 0) + delta, 0)
            self._counters[key] = (cur, now + ttl_s)
            return cur

    def holder_acquire(self, key: str, member: str, limit: int, ttl_s: int) -> bool:
        """보유자 단위 슬롯 — 만료가 지난 보유자를 먼저 지우고 한도 안이면 이 보유자를 넣는다. 만료는 넣을 때 정해져
        (미끄러지지 않음) 보유 워커가 finally 없이 죽어도 그 몫이 정해진 시간 뒤 풀린다 (D20-④)."""
        now = time.time()
        with self._lock:
            held = {m: e for m, e in self._holders.get(key, {}).items() if e > now}
            ok = len(held) < limit
            if ok:
                held[member] = now + ttl_s
            self._holders[key] = held
            return ok

    def holder_release(self, key: str, member: str) -> None:
        with self._lock:
            self._holders.get(key, {}).pop(member, None)

    def holder_count(self, key: str) -> int:
        now = time.time()
        with self._lock:
            return sum(1 for e in self._holders.get(key, {}).values() if e > now)

    def amount_hold(self, key: str, member: str, amount: int, ttl_s: int) -> int:
        """예약 단위 금액 — 예약마다 만료가 넣을 때 정해진다(다른 요청이 와도 늘지 않음). 반환 = 살아 있는 예약 합(이번 것 포함).
        amount 0 은 합만 읽는다. 정산 없이 워커가 죽은 예약은 ttl_s 뒤 반드시 빠진다 (59th — 증감 카운터는 활동 중 계속 연장)."""
        now = time.time()
        with self._lock:
            live = {m: (a, e) for m, (a, e) in self._amounts.get(key, {}).items() if e > now}
            if amount > 0:
                live[member] = (amount, now + ttl_s)
            self._amounts[key] = live
            return sum(a for a, _ in live.values())

    def amount_release(self, key: str, member: str) -> None:
        with self._lock:
            self._amounts.get(key, {}).pop(member, None)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._windows.clear()
            self._counters.clear()
            self._holders.clear()
            self._amounts.clear()


_LUA_BUCKET = """
local b = redis.call('HMGET', KEYS[1], 't', 'ts')
local rate, burst, cost, now = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
local t = tonumber(b[1]) or burst
local ts = tonumber(b[2]) or now
t = math.min(burst, t + (now - ts) * rate)
local ok = 0
if t >= cost then t = t - cost; ok = 1 end
redis.call('HSET', KEYS[1], 't', t, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(burst / math.max(rate, 0.001)) + 5)
return {ok, tostring(t)}
"""


# 보유자·예약 만료는 Valkey 시계(TIME)로 — 워커·파드 시계 차이가 만료를 앞당기거나 늦추지 않게 (D22-③)
_LUA_NOW = "local t = redis.call('TIME'); local now = tonumber(t[1]) + tonumber(t[2]) / 1000000\n"

_LUA_HOLD = _LUA_NOW + """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[1]), ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1
"""

_LUA_HOLD_COUNT = _LUA_NOW + """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
return redis.call('ZCARD', KEYS[1])
"""

# 예약 단위 금액 — HASH member → "금액:만료". 만료 지난 예약을 지우고 합을 돌려준다 (59th)
_LUA_AMOUNT = _LUA_NOW + """
local s = 0
local all = redis.call('HGETALL', KEYS[1])
for i = 1, #all, 2 do
  local sep = string.find(all[i + 1], ':', 1, true)
  local a, e = tonumber(string.sub(all[i + 1], 1, sep - 1)), tonumber(string.sub(all[i + 1], sep + 1))
  if e <= now then redis.call('HDEL', KEYS[1], all[i]) else s = s + a end
end
if tonumber(ARGV[2]) > 0 then
  redis.call('HSET', KEYS[1], ARGV[1], ARGV[2] .. ':' .. (now + tonumber(ARGV[3])))
  s = s + tonumber(ARGV[2])
end
redis.call('EXPIRE', KEYS[1], ARGV[4])
return s
"""


class RedisBackend:
    """Valkey 공유 버킷. 연결이 끊기면 5초 동안 이 프로세스의 메모리 버킷으로 버틴다(NFR-A1 — 한도는 워커 단위로 느슨해짐, 차단은 하지 않음)."""
    DEGRADE_S = 5.0

    def __init__(self, url: str):
        import redis
        self._r = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
        self._bucket = self._r.register_script(_LUA_BUCKET)
        self._hold = self._r.register_script(_LUA_HOLD)
        self._hold_count = self._r.register_script(_LUA_HOLD_COUNT)
        self._amount = self._r.register_script(_LUA_AMOUNT)
        self._local = MemoryBackend()
        self._down_until = 0.0
        self.degraded_events = 0

    def _guard(self, fn, local):
        import redis
        if time.monotonic() < self._down_until:
            return local()
        try:
            return fn()
        except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
            self._down_until, self.degraded_events = time.monotonic() + self.DEGRADE_S, self.degraded_events + 1
            from app.core import logs
            logs.event("warning", "gateway", f"Valkey 연결 실패 — {self.DEGRADE_S:.0f}초 동안 워커 로컬 한도로 동작", error=type(exc).__name__)
            return local()

    def take(self, key: str, rate: float, burst: int, cost: float = 1.0) -> tuple[bool, float]:
        def remote():
            ok, left = self._bucket(keys=[f"anx:{key}"], args=[rate, burst, cost, time.time()])
            if int(ok) == 1:
                return True, 0.0
            return False, (cost - float(left)) / rate if rate > 0 else 60.0
        return self._guard(remote, lambda: self._local.take(key, rate, burst, cost))

    def window_get(self, key: str, window_s: int = 60) -> int:
        slot = int(time.time() // window_s)
        return self._guard(lambda: int(self._r.get(f"anx:w:{key}:{slot}") or 0), lambda: self._local.window_get(key, window_s))

    def window_add(self, key: str, n: int, window_s: int = 60, slot: int | None = None) -> int:
        now_slot = int(time.time() // window_s)
        if slot is not None and slot < now_slot:  # 지난 창 — 반환하지 않는다 (D22-③)
            return 0

        def remote():
            k = f"anx:w:{key}:{now_slot}"
            pipe = self._r.pipeline()
            pipe.incrby(k, n)
            pipe.expire(k, window_s + 5)
            return int(pipe.execute()[0])
        return self._guard(remote, lambda: self._local.window_add(key, n, window_s, slot))

    def counter_add(self, key: str, delta: int, ttl_s: int) -> int:
        def remote():
            k = f"anx:c:{key}"
            pipe = self._r.pipeline()
            pipe.incrby(k, delta)
            pipe.expire(k, ttl_s)
            val = int(pipe.execute()[0])
            if val < 0:
                self._r.set(k, 0, ex=ttl_s)
                return 0
            return val
        return self._guard(remote, lambda: self._local.counter_add(key, delta, ttl_s))

    def holder_acquire(self, key: str, member: str, limit: int, ttl_s: int) -> bool:
        def remote():
            return int(self._hold(keys=[f"anx:h:{key}"], args=[ttl_s, limit, member, ttl_s + 60])) == 1
        return self._guard(remote, lambda: self._local.holder_acquire(key, member, limit, ttl_s))

    def holder_release(self, key: str, member: str) -> None:
        self._guard(lambda: self._r.zrem(f"anx:h:{key}", member), lambda: self._local.holder_release(key, member))

    def holder_count(self, key: str) -> int:
        def remote():
            return int(self._hold_count(keys=[f"anx:h:{key}"]))
        return self._guard(remote, lambda: self._local.holder_count(key))

    def amount_hold(self, key: str, member: str, amount: int, ttl_s: int) -> int:
        def remote():
            return int(self._amount(keys=[f"anx:a:{key}"], args=[member or "-", amount, ttl_s, ttl_s + 60]))
        return self._guard(remote, lambda: self._local.amount_hold(key, member, amount, ttl_s))

    def amount_release(self, key: str, member: str) -> None:
        self._guard(lambda: self._r.hdel(f"anx:a:{key}", member), lambda: self._local.amount_release(key, member))

    def ping(self) -> bool:
        return bool(self._r.ping())


_backend = None
_backend_lock = threading.Lock()


def backend():
    global _backend
    with _backend_lock:
        if _backend is None:
            if settings.rate_limit_backend == "redis" and settings.valkey_url:
                _backend = RedisBackend(settings.valkey_url)
            else:
                _backend = MemoryBackend()
        return _backend


def backend_status() -> dict:
    b = backend()
    if isinstance(b, RedisBackend):
        degraded = time.monotonic() < b._down_until
        try:
            return {"backend": "valkey", "ok": b.ping(), "degraded": degraded, "degraded_events": b.degraded_events}
        except Exception as exc:  # noqa: BLE001
            return {"backend": "valkey", "ok": False, "error": type(exc).__name__, "degraded": True,
                    "degraded_events": b.degraded_events}
    return {"backend": "memory", "ok": True}


# ── 한도 평가 ──
@dataclass
class LimitResult:
    allowed: bool = True
    reason: str | None = None
    retry_after: int = 0
    fallback_model: str | None = None
    limit_id: str | None = None
    budget: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)  # on_exceed=warn 로 넘긴 한도
    holds: list[tuple] = field(default_factory=list)  # (tok, 키, 토큰 수, 분 슬롯) · (usd, 키, 예약 id) — finalize 에서 정산 (D20-①)


def _scope_value(lim: RateLimit, p: Principal, agent: str | None) -> str | None:
    """이 한도가 주체에 적용되면 버킷 키 구분값, 아니면 None."""
    match lim.scope_type:
        case "any":
            return p.sub if lim.scope == "per-subject" else "*"
        case "user":
            return p.sub if lim.scope in ("*", p.sub, p.name) else None
        case "team":
            if not p.team:
                return None
            return p.team if lim.scope in ("*", p.team) else None
        case "agent":
            if agent is None:
                return None
            return agent if lim.scope in ("*", agent) else None
        case "client_key":
            if p.kind != "client_key":
                return None
            return p.token_id if lim.scope in ("*", p.token_id) else None
    return None


def _target_match(lim: RateLimit, kind: str, target: str) -> bool:
    if lim.target_type not in ("any", kind):
        return False
    return fnmatch.fnmatchcase(target, lim.target or "*")


def _period_start(period: str) -> datetime:
    now = datetime.now(timezone.utc)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


_SPEND_CACHE: dict[tuple, tuple[float, Decimal]] = {}


def spend(db: Session, scope_type: str, scope_value: str, period: str, use_cache: bool = True) -> Decimal:
    key = (scope_type, scope_value, period)
    hit = _SPEND_CACHE.get(key)
    if use_cache and hit and time.monotonic() - hit[0] < 5:
        return hit[1]
    stmt = select(func.coalesce(func.sum(UsageRecord.cost), 0)).where(UsageRecord.ts >= _period_start(period))
    if scope_type == "client_key":
        stmt = stmt.where(UsageRecord.client_key == scope_value)
    elif scope_type == "team":
        stmt = stmt.where(UsageRecord.team == scope_value)
    elif scope_type == "user":
        stmt = stmt.where(UsageRecord.user_sub == scope_value)
    elif scope_type == "agent":
        stmt = stmt.where(UsageRecord.agent == scope_value)
    total = Decimal(str(db.scalar(stmt) or 0))
    _SPEND_CACHE[key] = (time.monotonic(), total)
    return total


def budget_buckets(db: Session, lim: RateLimit, top: int = 20) -> list[tuple[str, str]]:
    """예산 한도가 실제로 적용되는 버킷 [(종류, 값)] — 지정 값이면 그 하나, `*`·per-subject 는 이 기간 사용이 있는 버킷(상위 top).
    `any` 는 팀이 있으면 팀, 없으면 사용자 (`_budget_scope` 와 같은 규칙, D22-③)."""
    if lim.scope not in ("*", "per-subject"):
        return [(lim.scope_type, lim.scope)]
    since, total = _period_start(lim.period), func.sum(UsageRecord.cost)
    cols = {"team": [("team", UsageRecord.team, None)], "user": [("user", UsageRecord.user_sub, None)],
            "agent": [("agent", UsageRecord.agent, None)], "client_key": [("client_key", UsageRecord.client_key, None)],
            "any": [("team", UsageRecord.team, None), ("user", UsageRecord.user_sub, func.coalesce(UsageRecord.team, "") == "")]}
    out = []
    for kind, col, cond in cols.get(lim.scope_type, []):
        stmt = select(col, total).where(UsageRecord.ts >= since, col.is_not(None), col != "")
        if cond is not None:
            stmt = stmt.where(cond)
        out += [(kind, v, s) for v, s in db.execute(stmt.group_by(col).order_by(total.desc()).limit(top))]
    return [(k, v) for k, v, _ in sorted(out, key=lambda r: r[2] or 0, reverse=True)[:top]]


def clear_cache() -> None:
    _SPEND_CACHE.clear()


HOLD_TTL_S = 300  # 예약은 finalize 가 정산 — 워커가 죽어 정산이 없으면 예약한 때부터 이 시간에 풀린다 (예약마다 고정, 59th)


def _budget_scope(lim: RateLimit, p: Principal, sv: str) -> tuple[str, str]:
    """예산 집계 범위 — `any` 는 주체의 팀(팀이 없으면 그 사용자) (D20-①: 팀 없는 주체도 예산 안)."""
    if lim.scope_type != "any":
        return lim.scope_type, sv
    return ("team", p.team) if p.team else ("user", p.sub)


def check(db: Session, p: Principal, kind: str, target: str, agent: str | None = None,
          estimated_tokens: int = 0, estimated_cost: Decimal | None = None, output_tokens: int = 0) -> LimitResult:
    """호출 전 검사. 요청 버킷은 이 시점에 차감된다. 토큰·예산은 예상치를 예약해 동시 요청이 한도를 함께 넘지 못하게 한다 —
    예약은 `settle` 이 실제 사용량으로 정산(실패면 반환)한다 (D20-①). 출력 토큰 예상(`output_tokens`)도 예약하지만
    이 요청의 통과 여부는 입력까지만으로 판단한다 — 출력 추정 때문에 혼자서 거절되지 않고, 다른 요청에는 보인다 (D22-③)."""
    limits = db.scalars(select(RateLimit).where(RateLimit.enabled.is_(True))).all()
    result = LimitResult()
    est_micro = int((estimated_cost or Decimal(0)) * 1_000_000)
    for lim in limits:
        sv = _scope_value(lim, p, agent)
        if sv is None or not _target_match(lim, kind, target):
            continue
        exceeded = None
        if lim.rps:
            rate = float(lim.rps)
            burst = lim.burst or max(1, math.ceil(rate))
            ok, wait = backend().take(f"rl:{lim.id}:{sv}", rate, burst)
            if not ok:
                exceeded = (f"요청 한도 초과 ({lim.name}: {rate:g} rps)", max(1, math.ceil(wait)))
        held: list[tuple] = []
        if exceeded is None and lim.tokens_per_min:
            key, slot, reserve = f"tok:{lim.id}:{sv}", int(time.time() // 60), estimated_tokens + output_tokens
            # 더하기는 지금 창에 — 슬롯을 계산한 뒤 분이 바뀌어도 예약이 빠지지 않게(반환은 그 슬롯에만이라 보수적으로 남음, 65th)
            now_used = backend().window_add(key, reserve) if reserve else backend().window_get(key)
            if now_used - output_tokens > lim.tokens_per_min:
                if reserve:
                    backend().window_add(key, -reserve, slot=slot)
                exceeded = (f"토큰 한도 초과 ({lim.name}: {now_used - reserve}/{lim.tokens_per_min} tokens/min)",
                            60 - int(time.time()) % 60)
            elif reserve:
                over = max(0, now_used - lim.tokens_per_min)  # 출력 예약은 남은 여유분까지만 — 큰 max_tokens 하나가 버킷을 잠그지 않게 (65th)
                if over:
                    backend().window_add(key, -over, slot=slot)
                    reserve -= over
                held.append(("tok", key, reserve, slot))  # 예약한 분 슬롯 — 반환·정산은 그 창에만 (D22-③)
        if exceeded is None and lim.budget_usd is not None and lim.scope_type in ("team", "user", "agent", "any", "client_key"):
            st, val = _budget_scope(lim, p, sv)
            used = spend(db, st, val, lim.period)
            hkey = f"usdhold:{lim.id}:{st}:{val}"
            member = uuid.uuid4().hex if est_micro else ""
            in_flight = backend().amount_hold(hkey, member, est_micro, HOLD_TTL_S)  # 다른 진행 중 요청의 예약 + 이 요청
            if est_micro:
                held.append(("usd", hkey, member))
            pct = float(used / lim.budget_usd * 100) if lim.budget_usd else 100.0
            result.budget.append({"limit_id": lim.id, "name": lim.name, "used_usd": float(used),
                                  "budget_usd": float(lim.budget_usd), "pct": round(pct, 1)})
            if pct >= lim.alert_pct:
                notify("budget.threshold", f"예산 {pct:.0f}% 사용: {lim.name}", f"{st}={val} {used}/{lim.budget_usd} USD",
                       severity="danger" if pct >= 100 else "warn", link="/console/observe/budgets",
                       audience_role="finops", dedupe_key=f"budget:{lim.id}:{val}:{int(pct // 10)}")
            if used >= lim.budget_usd or (est_micro and used + Decimal(in_flight) / 1_000_000 > lim.budget_usd):
                exceeded = (f"예산 초과 ({lim.name}: {used}/{lim.budget_usd} USD/{lim.period}"
                            + (f" · 진행 중 예약 {Decimal(in_flight) / 1_000_000}" if est_micro else "") + ")", 3600)
        if exceeded:
            release(held)
            if lim.on_exceed == "warn":  # 경고만 (SCR-H09·K07) — 차단하지 않고 알림·기록
                result.warnings.append({"limit_id": lim.id, "name": lim.name, "reason": exceeded[0]})
                notify("limit.warn", f"한도 경고: {lim.name}", exceeded[0], severity="warn", link="/console/gateway/limits",
                       audience_role="finops" if lim.budget_usd is not None else "operator",
                       dedupe_key=f"limit.warn:{lim.id}:{sv}:{int(time.time() // 600)}")
                continue
            if lim.on_exceed == "fallback" and lim.fallback_model and kind == "model" and target != lim.fallback_model:
                result.fallback_model = lim.fallback_model
                result.reason = exceeded[0] + f" → 폴백 {lim.fallback_model}"
                result.limit_id = lim.id
                continue
            release(result.holds)
            return LimitResult(False, exceeded[0], exceeded[1], None, lim.id, result.budget)
        result.holds.extend(held)
    return result


def release(holds: list[tuple]) -> None:
    """예약 반환 — 호출이 실패했거나 한도 검사에서 거절됐을 때. 토큰 = (tok, 키, 토큰 수, 분 슬롯), 예산 = (usd, 키, 예약 id).
    토큰 예약은 예약한 분에만 반환 — 다음 분으로 넘어갔으면 그 창은 이미 끝났다 (D22-③)."""
    for h in holds:
        if h[0] == "tok":
            backend().window_add(h[1], -h[2], slot=h[3])
        else:
            backend().amount_release(h[1], h[2])


def settle(holds: list[tuple], tokens: int) -> None:
    """실제 사용량으로 정산 — 토큰 창은 더 쓴 만큼 지금 창에 더하고, 덜 쓴 만큼은 예약한 분 창에 돌려준다(그 분이 지났으면 그대로,
    D22-③). 예산 예약은 반환한다(실제 비용은 사용 기록이 spend 에 반영). 이 워커의 spend 캐시는 비운다."""
    for h in holds:
        kind, key = h[0], h[1]
        if kind == "tok" and tokens > h[2]:
            backend().window_add(key, tokens - h[2])
        elif kind == "tok" and tokens < h[2]:
            backend().window_add(key, tokens - h[2], slot=h[3])
        elif kind == "usd":
            backend().amount_release(key, h[2])
            parts = key.split(":", 3)  # usdhold:<lim>:<scope_type>:<value>
            for period in ("day", "month"):
                _SPEND_CACHE.pop((parts[2], parts[3], period), None)


def record_tokens(db: Session, p: Principal, kind: str, target: str, tokens: int, agent: str | None = None,
                  reserved: set[str] | None = None) -> None:
    """실제 토큰 기록 — 호출 전에 예약한 창(`reserved`)은 `settle` 이 정산하므로 건너뛴다."""
    if tokens <= 0:
        return
    for lim in db.scalars(select(RateLimit).where(RateLimit.enabled.is_(True), RateLimit.tokens_per_min.is_not(None))):
        sv = _scope_value(lim, p, agent)
        key = f"tok:{lim.id}:{sv}"
        if sv is not None and _target_match(lim, kind, target) and key not in (reserved or set()):
            backend().window_add(key, tokens)
