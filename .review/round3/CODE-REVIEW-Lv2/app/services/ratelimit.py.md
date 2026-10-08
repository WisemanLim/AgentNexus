> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 ratelimit.py.md](../../../../round2/CODE-REVIEW-Lv2/app/services/ratelimit.py.md)

### 04. app/services/ratelimit.py
**파일 요약 / File Summary**
- 역할 / Role: 레이트 리밋·토큰 한도·예산(FR-G04). 백엔드는 메모리(워커 로컬) 또는 Valkey(워커·파드 공유). 11차에 두 갈래가 바뀌었다 — D20-① **예약(reservation)**: 호출 전 예상 토큰·비용을 한도에 미리 더해 동시 요청이 함께 넘지 못하게, 끝나면 `settle`/`release` 로 정산. D20-④ **보유자 단위 슬롯(holder slot)**: MCP listen 동시 구독 수를 증감 카운터 대신 "보유자 집합 + 고정 만료" 로.
- 진입점 거리 / Distance: 2 — `/v1/chat/completions` → `app/services/llm.py:241` `ratelimit.check` · `app/services/llm.py:432-445` `settle`/`release`. listen 은 `app/api/mcp.py:233` → `app/services/mcp_modern.py:57-75`. TOTP·푸시 jti 1회 확인도 이 백엔드의 `counter_add` 를 쓴다(`app/api/idp.py:149`, `app/services/a2a_gateway.py:843`).
- LOC: 403 (2차 스냅샷 301 → +102)
- 주요 export: `MemoryBackend`·`RedisBackend`(`take`·`window_*`·`counter_add`·`holder_*`), `backend()`, `LimitResult`(+`holds`), `check`·`release`·`settle`·`record_tokens`·`spend`·`HOLD_TTL_S`
- 의존(내부): `app.core.auth.Principal`, `app.models.RateLimit`·`UsageRecord`, `app.services.notify`
- 외부 패키지: redis-py(Valkey), SQLAlchemy
- 부수효과 / Side effects: 프로세스 전역 백엔드·`_SPEND_CACHE`, Valkey 키 `anx:w:*`(분 창)·`anx:c:*`(증감 카운터)·`anx:h:*`(보유자 ZSET), 알림 발송.
- 동시성 / Concurrency: 메모리 백엔드는 `threading.Lock`, Valkey 는 Lua·`MULTI` 파이프라인으로 원자적. `check` 는 스레드풀(동기 DB)에서 불린다.
- 테스트 커버리지 흔적: `test/test_model_gateway2.py:74-103`(C52-02 동시 예약), `:105-115`(C52-03 팀 없는 `any`), `:117-130`(C52-04 클라이언트 키 예산), `test/test_observe_perf2.py:106-124`(C55-03 보유자 만료).
- 3차 변경 요약: D20-① `LimitResult.holds`(L233)·`check` 의 토큰 창 선가산(L326-335)·예산 진행 중 예약 `usdhold`(L336-352)·`release`/`settle`(L372-391)·`record_tokens(reserved)`(L394-403)·`_budget_scope`(L300-304)·클라이언트 키 범위(L251-254, L280-281). D20-④ `holder_*`(L64-83, L107-113, L176-190).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/ratelimit.py lines=55-83
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
```
**분석 / Analysis (Level 2)**
- L55-62 `counter_add`(메모리): 증감 카운터. **변경할 때마다 만료를 `now + ttl_s` 로 다시 잡는다(미끄러지는 TTL, sliding TTL)**. 0 아래로는 내려가지 않는다.
- L64-74 `holder_acquire`(D20-④): 키마다 `{보유자 id: 만료 epoch}`. 먼저 만료된 보유자를 걸러내고(L69), 한도 안이면 이 보유자를 **넣을 때 정한 만료**로 추가한다(L72). 만료가 미끄러지지 않으므로 반납 없이 죽은 워커의 몫은 활동이 계속되어도 정해진 시간 뒤 풀린다 — 2차 M31(증감 카운터 TTL 연장으로 listen 429 고착)의 정확한 해법이다.
- L76-83: 반납은 그 보유자만 제거, 개수는 만료되지 않은 것만 센다.
- 흔한 실수: "증감 카운터 + TTL" 은 단순해 보이지만, 감소가 빠지면(프로세스 사망·취소) 그 몫을 되돌릴 방법이 TTL 뿐이고, TTL 이 변경마다 연장되면 **바쁜 키일수록 영원히** 남는다. 개수 한도는 보유자 집합(ZSET)으로, 양(量) 한도는 "예약 건마다 독립 만료" 로 만드는 것이 정석이다.

```python path=app/services/ratelimit.py lines=107-113
_LUA_HOLD = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""
```
**분석 / Analysis (Level 2)**
- Valkey 쪽 보유자 슬롯: 점수(score) = 만료 epoch 인 ZSET. ① 만료 지난 멤버 제거 ② 개수 ≥ 한도면 0 ③ 추가 ④ 키 자체 TTL 은 넉넉히(만료 + 60초) — 한 스크립트라 원자적이다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] ARGV 의 `now` 는 **호출한 워커의 시계**(L178)다. 파드 간 시계가 어긋나면 한 워커가 넣은 만료를 다른 워커가 일찍(또는 늦게) 지운다. NTP 가 있는 클러스터에선 수 초 이내라 실해는 작다. Valkey `TIME` 명령을 스크립트 안에서 쓰면 시계가 하나가 된다.

```python path=app/services/ratelimit.py lines=163-190
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
            now = time.time()
            return int(self._hold(keys=[f"anx:h:{key}"], args=[now, now + ttl_s, limit, member, ttl_s + 60])) == 1
        return self._guard(remote, lambda: self._local.holder_acquire(key, member, limit, ttl_s))

    def holder_release(self, key: str, member: str) -> None:
        self._guard(lambda: self._r.zrem(f"anx:h:{key}", member), lambda: self._local.holder_release(key, member))

    def holder_count(self, key: str) -> int:
        def remote():
            k = f"anx:h:{key}"
            self._r.zremrangebyscore(k, "-inf", time.time())
            return int(self._r.zcard(k))
        return self._guard(remote, lambda: self._local.holder_count(key))
```
**분석 / Analysis (Level 2)**
- L163-174 Valkey `counter_add`: `INCRBY` + `EXPIRE` 를 한 파이프라인(기본 `transaction=True` → MULTI/EXEC)으로. 메모리판과 같은 **미끄러지는 TTL** 이다(L168).
- L176-190 Valkey 보유자: 위 Lua 호출, 반납 `ZREM`, 개수는 만료 정리 후 `ZCARD`.
- `_guard`(L129-139): Valkey 연결 오류면 5초 동안 이 워커의 메모리 백엔드로 버틴다 — 그동안 모든 한도(TOTP 1회·푸시 jti 1회 포함)가 **워커 단위**로 느슨해진다(설계 문서화됨, L117).

```python path=app/services/ratelimit.py lines=224-233
@dataclass
class LimitResult:
    allowed: bool = True
    reason: str | None = None
    retry_after: int = 0
    fallback_model: str | None = None
    limit_id: str | None = None
    budget: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)  # on_exceed=warn 로 넘긴 한도
    holds: list[tuple[str, str, int]] = field(default_factory=list)  # (kind tok|usd, 키, 예약량) — finalize 에서 정산 (D20-①)
```
**분석 / Analysis (Level 2)**
- `holds`: `(종류 tok|usd, 키, 예약량)` 목록. `check` 가 만들고, 호출 결과에 따라 `llm.finalize` 가 `settle`(성공) 또는 `release`(실패)로 정확히 한 번 정산해야 한다 — **이 "정확히 한 번" 이 깨지면 예약이 남는다**(아래 L336-352).

```python path=app/services/ratelimit.py lines=236-255
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
```
**분석 / Analysis (Level 2)**
- `_scope_value`: 이 한도가 주체에 적용되면 버킷 구분값, 아니면 None. L251-254(D20-①): 클라이언트 키 범위 — 주체가 클라이언트 키일 때만, 값은 키 id(`token_id`).
- `any` 는 `per-subject` 면 주체 sub, 아니면 `*`(전체 공용 버킷) — **요청·토큰 버킷 기준**이다. 예산은 아래 `_budget_scope` 가 다시 정한다.

```python path=app/services/ratelimit.py lines=274-304
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


def clear_cache() -> None:
    _SPEND_CACHE.clear()


HOLD_TTL_S = 300  # 예약은 finalize 가 정산 — 워커가 죽어 정산이 없으면 마지막 변경 뒤 이 시간에 풀린다


def _budget_scope(lim: RateLimit, p: Principal, sv: str) -> tuple[str, str]:
    """예산 집계 범위 — `any` 는 주체의 팀(팀이 없으면 그 사용자) (D20-①: 팀 없는 주체도 예산 안)."""
    if lim.scope_type != "any":
        return lim.scope_type, sv
    return ("team", p.team) if p.team else ("user", p.sub)
```
**분석 / Analysis (Level 2)**
- L274-290 `spend`: 기간(일·월) 사용액 합. 5초 캐시(워커 로컬). L280-281(D20-①): `usage_records.client_key`(migration 0020) 로 키별 합.
- L297 `HOLD_TTL_S = 300`: 정산 없는 예산 예약이 풀리는 시간 — 주석은 "마지막 변경 뒤 이 시간" 이라고 정확히 적었다.
- L300-304 `_budget_scope`(D20-①): `any` 예산은 **주체의 팀, 팀이 없으면 그 사용자** 사용액으로 판정한다. 2차 M32("팀 없는 주체는 항상 0")를 이렇게 해결했다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L9 — `any` 예산의 의미가 버킷과 다르다.** 요청·토큰 버킷은 `any`+`*` 를 "전체 공용", `any`+`per-subject` 를 "주체별" 로 다루는데(L239-240), 예산은 둘 다 "주체의 팀(없으면 사용자)" 으로 모인다(L302-304). 그래서 ① `any`·`*`·월 1,000 USD 를 "플랫폼 전체 상한" 으로 만든 운영자는 실제로 **팀마다 1,000 USD** 를 허용하게 되고, ② `any`·`per-subject` 를 "사람마다" 로 만든 운영자는 **팀원끼리 한 예산을 나눠 쓰게** 된다. 현황 화면도 `*`·`per-subject` 예산은 표시하지 않는다(`app/api/gateway_admin.py:496-498`). 2차 M32 의 최소 수정안은 "`any`+`*` 는 전체 합계" 였다. 최소 수정: `any`+`*` → 전체 합(`spend` 의 필터 없음 분기), `any`+`per-subject` → 사용자 합, 콘솔 설명에 집계 범위 표시.

```python path=app/services/ratelimit.py lines=307-369
def check(db: Session, p: Principal, kind: str, target: str, agent: str | None = None,
          estimated_tokens: int = 0, estimated_cost: Decimal | None = None) -> LimitResult:
    """호출 전 검사. 요청 버킷은 이 시점에 차감된다. 토큰·예산은 예상치를 예약해 동시 요청이 한도를 함께 넘지 못하게 한다 —
    예약은 `settle` 이 실제 사용량으로 정산(실패면 반환)한다 (D20-①)."""
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
        held: list[tuple[str, str, int]] = []
        if exceeded is None and lim.tokens_per_min:
            key = f"tok:{lim.id}:{sv}"
            now_used = backend().window_add(key, estimated_tokens) if estimated_tokens else backend().window_get(key)
            if now_used > lim.tokens_per_min:
                if estimated_tokens:
                    backend().window_add(key, -estimated_tokens)
                exceeded = (f"토큰 한도 초과 ({lim.name}: {now_used - estimated_tokens}/{lim.tokens_per_min} tokens/min)",
                            60 - int(time.time()) % 60)
            elif estimated_tokens:
                held.append(("tok", key, estimated_tokens))
        if exceeded is None and lim.budget_usd is not None and lim.scope_type in ("team", "user", "agent", "any", "client_key"):
            st, val = _budget_scope(lim, p, sv)
            used = spend(db, st, val, lim.period)
            hkey = f"usdhold:{lim.id}:{st}:{val}"
            in_flight = backend().counter_add(hkey, est_micro, HOLD_TTL_S)  # 다른 진행 중 요청의 예약 + 이 요청
            if est_micro:
                held.append(("usd", hkey, est_micro))
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
```
**분석 / Analysis (Level 2)**
- 한도마다: 요청 버킷 차감(L319-324) → 토큰 창 **선가산**(L326-335) → 예산 진행 중 예약(L336-352) → 초과면 이번 한도의 예약을 되돌리고(L354) `warn`/`fallback`/거절.
- L328: 예상 토큰이 있으면 창에 먼저 더하고 결과(합계)로 판정, 넘으면 L331 에서 되돌린다 — "확인 후 증가" 사이 경합(2차 §4.1-7 의 비원자 확인-증가)을 없앤 것.
- L340: 예산은 `counter_add(hkey, est_micro)` 결과가 "다른 진행 중 요청의 예약 + 이 요청" 이다. L350: 이미 쓴 금액(`used`) + 진행 중 예약이 예산을 넘으면 거절.
- L366-367: 다른 한도에서 쌓아 둔 예약(`result.holds`)도 거절 시 모두 반환한다 — 누수 방지.
**짚어볼 점 / Watch-outs**
- [위험:높음] **H2(INDEX §5) — 예산 예약 누수가 활동 중에는 풀리지 않는다.** L340 의 `counter_add` 는 예상 비용이 0 인 요청이라도 **매번** 호출되어(L340 은 `est_micro` 와 무관하게 실행) 키 `usdhold:<한도>:<범위>:<값>` 의 TTL 을 300초로 다시 연장한다(L61, L168). 그런데 예약을 정산하는 `llm.finalize` 는 스트리밍 응답에서 클라이언트가 끊으면 실행되지 않는다(`app/api/llm.py:91-120` — `_final` 이 `try` 블록 **뒤**에 있고 `finally` 가 아니다, [llm.py.md](llm.py.md)). 실패 시나리오: 팀 예산 100 USD/일, 채팅 UI 의 "생성 중지" 또는 네트워크 단절로 스트림이 끊길 때마다 그 요청의 예상 비용(입력 토큰 + `max_tokens` 출력 × 단가)이 `usdhold` 에 남는다 → 팀의 다른 요청이 계속 들어오는 한 TTL 이 계속 연장되어 누적 → 어느 순간 `used + 진행 중 예약 > 예산` 으로 **팀 전원의 모델 호출이 429("예산 초과 … 진행 중 예약 …")**. 풀리는 조건은 그 팀 예산 키에 300초 동안 요청이 하나도 없는 것뿐이다. README 의 "정산 없이 워커가 죽으면 예산 예약은 300초 뒤 풀림"(`README.md:545`)은 바쁜 팀에서는 성립하지 않는다. 같은 결함 패턴을 D20-④ 가 listen 에서는 고쳤다(L64-74). 최소 수정: ① 예산 예약을 보유자 ZSET 처럼 **예약 건마다 독립 만료**로(멤버 = 요청 id, 점수 = 만료, 값은 별도 HASH 또는 멤버에 금액 인코딩 → 합은 Lua 로), ② 호출처의 정산을 `finally` 에서 동기로(아래 llm.py.md).
- [위험:낮음] **L8 — 분 창 경계를 넘는 반환.** L331·L376 의 `window_add(key, -n)` 과 L386 의 추가분은 **반환·정산 시점의 분 창**에 적용된다(L48·L155 가 매번 현재 슬롯을 계산). 예약 후 응답이 다음 분으로 넘어가 실패(`release`)하면 새 분 창이 `-n` 에서 시작해(L51 `cur = n if s != slot` → 음수, Valkey 는 새 키에 `INCRBY -n`) 그 분에는 한도보다 `n` 만큼 더 쓸 수 있다. 의도적으로 실패를 일으키면(느린 모델 + 규칙 시간 초과 → 504) 대략 한도의 2배까지 늘릴 수 있다. 최소 수정: 예약 키에 슬롯을 함께 기록(`("tok", key, n, slot)`)하고 반환은 그 슬롯 키로만, 슬롯이 지났으면 반환 생략.
- [위험:낮음] 예약은 예상치 기반이라, 팀의 한 사람이 큰 `max_tokens` 로 동시 요청을 여러 개 열어 두면 진행 중 예약만으로 팀 예산을 막을 수 있다(예약 방식의 본질적 성질). 주체별 동시 요청 상한 또는 예약 상한(예: 예산의 10%)을 두는 것을 검토.

```python path=app/services/ratelimit.py lines=372-403
def release(holds: list[tuple[str, str, int]]) -> None:
    """예약 반환 — 호출이 실패했거나 한도 검사에서 거절됐을 때."""
    for kind, key, n in holds:
        if kind == "tok":
            backend().window_add(key, -n)
        else:
            backend().counter_add(key, -n, HOLD_TTL_S)


def settle(holds: list[tuple[str, str, int]], tokens: int) -> None:
    """실제 사용량으로 정산 — 토큰 창은 예상보다 많이 쓴 만큼만 더하고(적게 썼으면 그대로 — 같은 분 창 안의 보수적 계산),
    예산 예약은 반환한다(실제 비용은 사용 기록이 spend 에 반영). 이 워커의 spend 캐시는 비운다."""
    for kind, key, n in holds:
        if kind == "tok" and tokens > n:
            backend().window_add(key, tokens - n)
        elif kind == "usd":
            backend().counter_add(key, -n, HOLD_TTL_S)
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
```
**분석 / Analysis (Level 2)**
- `release`: 실패·거절 시 예약 전부 반환.
- `settle`: 성공 시 — 토큰은 **더 쓴 만큼만** 추가(덜 썼으면 그대로 — 같은 분 안의 보수적 계산, 52nd 결과 문서의 의도), 예산 예약은 반환하고 이 워커의 spend 캐시를 비운다(실제 비용은 사용 기록이 `spend` 로 반영).
- `record_tokens(reserved)`: 예약으로 이미 셈한 창은 건너뛴다 — 이중 계산 방지.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L388-391 `settle` 이 spend 캐시를 비우는 시점이 호출처에서 **사용 기록 커밋 전**이다(`app/services/llm.py:434` → `:450`). 그 사이 같은 워커의 다른 요청이 `spend` 를 다시 읽으면 이 호출 비용이 빠진 합계가 5초 캐시된다. 상세는 [llm.py.md](llm.py.md) L10.

## 파일 평가 / File Assessment
- 강점: (1) 토큰 창 선가산·되돌림으로 "확인 후 증가" 경합을 제거했고, 거절 시 이미 쌓은 예약까지 반환한다(`app/services/ratelimit.py:326-335`, `:354`, `:366`). (2) listen 슬롯을 보유자 ZSET + 고정 만료로 바꿔 2차 M31 을 정확히 해결했다(`app/services/ratelimit.py:64-74`, `:107-113`, 시험 `test/test_observe_perf2.py:106-124`). (3) 클라이언트 키 범위·팀 없는 `any` 예산을 사용 기록 컬럼과 함께 일관되게 추가(`app/services/ratelimit.py:251-254`, `:280-281`, `:300-304`).
- 위험: (1) [위험:높음] 예산 예약이 미끄러지는 TTL 카운터에 있어, 정산이 빠진 예약(스트림 단절)이 바쁜 팀에서 영구히 쌓인다(`app/services/ratelimit.py:340`, `:55-62`, `:163-174` + `app/api/llm.py:91-120`). (2) [위험:낮음] 분 창 경계를 넘는 반환이 다음 분 한도를 늘린다(`app/services/ratelimit.py:331`, `:376`). (3) [위험:낮음] `any` 예산 집계 범위가 버킷 의미·현황 화면과 다르다(`app/services/ratelimit.py:300-304`).
- 개선 권고(영향순): (1) 예산 예약을 예약 건별 독립 만료 구조로(보유자 ZSET 재사용) + 호출처 정산을 `finally` 로. (2) 반환은 예약한 슬롯에만. (3) `any` 예산 의미를 정하고 문서·현황 화면에 반영.
- 파일 위험 등급 / Risk grade: 높음 — [위험:높음] 1건(H2 의 근본 원인 쪽). 같은 파일 안에 올바른 구조(보유자 ZSET)가 이미 있어 고치기는 쉽다.
- 재읽기 권고 레벨: 2 — 예약의 생애(check → finalize 의 settle/release)가 두 파일에 걸쳐 있어 호출처([llm.py.md](llm.py.md))와 같이 줄 단위로 읽어야 한다.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 (2차 INDEX §5) | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| M31 증감 카운터 TTL 연장 → listen 429 고착 (`mcp_modern.py:60`·`:68`, `ratelimit.py:59-60`·`:134-135`) | 해결(listen) · 패턴 재사용(예산) | 보유자 ZSET `app/services/ratelimit.py:64-74`, `:107-113`, `:176-190`. 같은 미끄러지는 TTL 카운터를 D20-① 예산 예약이 새로 쓴다 `:340` (H2) |
| M32 `any` 예산이 팀 없는 주체에 0, `client_key` 예산 무시 (`ratelimit.py:266-269`) | 부분 해결 | 팀 없는 주체 → 사용자 합 `:300-304`, 클라이언트 키 예산 `:251-254`·`:280-281`. `any`+`*` 전체 합계는 미반영(L9) |
| M35 한도·예산 예약 없는 사전 검사 (`llm.py:221-225`) | 해결(진행 중 경합) · 잔여 | 선가산·진행 중 예약 `:326-352`. 정산 누락 경로(H2)·출력 토큰 미예약(M9, [llm.py.md](llm.py.md)) 남음 |
