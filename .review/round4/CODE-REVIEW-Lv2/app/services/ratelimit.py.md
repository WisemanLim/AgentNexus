> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../../INDEX.md) · 3차 리뷰: [round3 ratelimit.py.md](../../../../round3/CODE-REVIEW-Lv2/app/services/ratelimit.py.md)

### 02. app/services/ratelimit.py (+ `app/api/gateway_admin.py:491-502` 예산 현황)
**파일 요약 / File Summary**
- 역할 / Role: 게이트웨이 한도(FR-G04) — 요청 버킷(rps)·분당 토큰·일/월 예산. 백엔드는 프로세스 메모리 또는 Valkey(redis). 호출 전 `check` 가 예상치를 **예약(reservation)** 하고, 호출 뒤 `settle`(정산)·`release`(반환)가 실제 값으로 맞춘다.
- 진입점 거리 / Distance: 1 — 모델 호출 준비(`app/services/llm.py:252`), MCP·A2A 게이트웨이, 발견 API 익명 한도(`app/api/catalog.py:517`), 푸시 jti(`app/services/a2a_gateway.py:885`), listen 보유자(`app/services/mcp_modern.py:64`).
- LOC: 484 (3차 403 → 484)
- 주요 export: `MemoryBackend`·`RedisBackend`·`backend`·`check`·`release`·`settle`·`record_tokens`·`spend`·`budget_buckets`(신규)·`HOLD_TTL_S`
- 의존(내부): `app.core.auth.Principal`, `app.core.config.settings`, `app.models.{RateLimit, UsageRecord}`, `app.services.notify`
- 외부 패키지: `redis`(Valkey 클라이언트, `uv.lock:1874` redis 8.1.0), SQLAlchemy
- 부수효과 / Side effects: Valkey 키 `anx:w:*`(분 창)·`anx:h:*`(보유자 ZSET)·`anx:a:*`(예약 금액 HASH)·`anx:c:*`(증감 카운터)·버킷 HASH, 프로세스 전역 `_SPEND_CACHE`·`_backend`, 알림(`notify`).
- 동시성 / Concurrency: 여러 워커·파드가 Valkey 를 공유. 메모리 백엔드는 `threading.Lock`. Lua 스크립트는 Valkey 안에서 원자적으로 돈다.
- 테스트 커버리지 흔적: `test/test_limit_precision.py:45`(C63-01 분 슬롯), `:64`(C63-02 출력 예약), `:86`(C63-03 진행 중 상한), `:104`(C63-04 Valkey 시계), `:122`(C63-05 미리 보기 예약 없음), `:133`(C63-06 예산 버킷).
- 4차 변경 요약 / Changes in D22: D22-③(Epic 63) — ① 토큰 예약에 **분 슬롯**을 기록해 반환·미사용 반환은 그 분 창에만(3차 L8) ② **출력 토큰도 예약**하되 자기 통과 판정은 입력까지만(3차 M9 — 59th "기록" 판정을 뒤집음) ③ 보유자·예약 만료를 **Valkey 시계(TIME)** 로(3차 L35) ④ `*`·per-subject·`any` 예산 현황을 실제 버킷별로(3차 L9).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/ratelimit.py lines=43-59
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
```
**분석 / Analysis (Level 2)**
- L43-47 `window_get`: 분 창 = `int(time.time() // 60)`. 저장된 슬롯이 지금 슬롯과 다르면 0(창이 바뀌었다).
- L49-59 `window_add`(D22-③): `slot` 을 주면 "그 분 창에만" 더한다 — 이미 지난 분이면 **아무것도 하지 않고 0** 을 돌려준다(L52-53). 예약 반환(음수 더하기)이 다음 분 창을 음수에서 시작하게 만들어 그 분 한도가 늘던 문제(3차 L8)를 막는다.
- 흔한 실수: 반환값 0 은 "창 합이 0" 과 "지난 창이라 건너뜀" 을 구분하지 못한다. 호출처가 반환값으로 판정하면 오판한다 — 아래 `check` 의 L404 가 그 경우다.

```python path=app/services/ratelimit.py lines=129-160
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
```
**분석 / Analysis (Level 2)**
- L130 `_LUA_NOW`(D22-③): Lua 안에서 `redis.call('TIME')` 으로 서버 시계를 읽는다. 전에는 워커가 `time.time()` 을 인자로 넘겼다 — 파드 시계가 수 초 다르면 보유자·예약 만료가 그만큼 앞당겨지거나 늦어졌다(3차 L35).
- 왜 안전한가: Lua 에서 `TIME` 같은 비결정 명령 뒤에 쓰기 명령을 부르는 것은 Redis 5 이전(스크립트 복제 모드)에는 거부됐다. 차트·compose 의 Valkey 는 8(`deploy/helm/agentnexus/values.yaml:106`, `compose/valkey.yml:5`) — 효과 복제(effects replication)만 쓰므로 허용된다. 외부 Valkey/Redis 를 쓸 때 **5 이상**이어야 한다는 전제가 문서에 없다(아래 INFRA 참고 수준, 등급 없음).
- L132-138 `_LUA_HOLD`: 만료 지난 보유자 제거 → 한도 확인 → `ZADD` 점수 = 서버 시각 + TTL. 만료가 "넣을 때 정해지는" 고정 만료(D20-④).
- L146-160 `_LUA_AMOUNT`: 예약 금액 HASH 값 `"금액:만료"` — 만료 지난 항목을 지우며 합을 구한다. 59th 형식과 같아 업그레이드 뒤에도 그대로 읽힌다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] Valkey 시계로 옮긴 것은 보유자·예약 금액뿐이다. 요청 버킷(`take`, L192 `time.time()`)과 분 창 키(`anx:w:{key}:{slot}`, L199·L203)는 여전히 워커 시계 — 파드 시계가 어긋나면 분 경계 근처 요청이 서로 다른 창으로 간다. D22-③ 범위 밖이라 등급만 낮음, 지적 목록에는 넣지 않는다.

```python path=app/services/ratelimit.py lines=198-213
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
```
**분석 / Analysis (Level 2)**
- L202-205(D22-③): Redis 백엔드도 메모리와 같은 규칙 — 지난 슬롯이면 Valkey 왕복 없이 0.
- L207-212: 더하기는 언제나 **지금** 슬롯 키에(`now_slot`). `slot` 은 "지난 창이면 건너뛰기" 판정에만 쓰인다.
- L213: 연결 실패 시 5초간 워커 메모리로(`_guard`) — `slot` 도 그대로 넘긴다.

```python path=app/services/ratelimit.py lines=350-365
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
```
```python path=app/api/gateway_admin.py lines=491-502
@router.get(f"{P}/limits/status")
def limits_status(p: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """예산 한도별 현재 사용액 (FinOps)."""
    out = []
    for lim in db.scalars(select(RateLimit).where(RateLimit.enabled.is_(True), RateLimit.budget_usd.is_not(None))):
        # `*`·per-subject·`any` 도 실제 버킷별로 — 전에는 지정 값 한도만 보였다 (D22-③)
        for kind, sv in ratelimit.budget_buckets(db, lim):
            used = ratelimit.spend(db, kind, sv, lim.period, use_cache=False)
            out.append({"limit_id": lim.id, "name": lim.name, "scope_type": lim.scope_type, "scope": sv,
                        "bucket": f"{kind}:{sv}", "period": lim.period, "used_usd": float(used), "budget_usd": float(lim.budget_usd),
                        "pct": round(float(used / lim.budget_usd * 100), 1) if lim.budget_usd else None})
    return {"items": out}
```
**분석 / Analysis (Level 2)**
- `budget_buckets`(D22-③): `*`·per-subject 한도는 "이 기간 사용이 있는 버킷" 상위 20개를 SQL `GROUP BY` 로 뽑는다(L361-364). `any` 는 팀 버킷 + "팀 없는 사용자" 버킷(L358 `coalesce(team, '') == ''`) — `_budget_scope`(L375-379)의 "팀 있으면 팀, 없으면 사용자" 와 같은 규칙이다.
- `limits_status`: 버킷마다 `spend(..., use_cache=False)` — 화면은 캐시 없이 정확한 값. 한도 N 개 × 버킷 ≤20 개 쿼리(N+1)지만 FinOps 화면 한 번이라 감당할 만하다.
- 응답의 `scope` 가 한도의 `scope` 값(예: `*`)에서 **버킷 값**(예: `team-a`)으로 의미가 바뀌었다 — 콘솔이 `scope` 로 한도를 식별하던 곳이 있으면 확인 필요(`bucket` 필드가 새로 생김).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L3** 지정 값 한도는 `[(scope_type, scope)]` 그대로 돌려준다(L353-354). 그런데 `user` 한도의 적용 판정은 `lim.scope in ("*", p.sub, p.name)`(L299) — **사용자 이름**으로 만든 한도도 그 사용자에게 걸린다(버킷 값은 sub). 현황은 `spend(db, "user", "<이름>")` → `UsageRecord.user_sub == "<이름>"` 이라 늘 0% 로 보인다. 실제로는 예산이 차서 429 가 나는데 화면은 0% — FinOps 담당이 원인을 찾지 못한다. 최소 수정: 이름 한도는 저장 때 sub 로 바꾸거나, 현황에서 이름 → sub 조회.

```python path=app/services/ratelimit.py lines=382-446
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
            now_used = backend().window_add(key, reserve, slot=slot) if reserve else backend().window_get(key)
            if now_used - output_tokens > lim.tokens_per_min:
                if reserve:
                    backend().window_add(key, -reserve, slot=slot)
                exceeded = (f"토큰 한도 초과 ({lim.name}: {now_used - reserve}/{lim.tokens_per_min} tokens/min)",
                            60 - int(time.time()) % 60)
            elif reserve:
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
```
**분석 / Analysis (Level 2)**
- L389: 예상 비용을 마이크로달러 정수로 — 부동소수점 합산 오차를 피한다.
- L402-411 토큰 한도(D22-③): 예약량 = 입력 추정 + **출력 추정**(L403). `window_add` 로 먼저 더한 뒤(L404) `now_used - output_tokens > 한도` 면 초과(L405) — 즉 **이 요청의 출력 추정은 자기 통과 판정에서 뺀다**. 다른 요청이 볼 때는 출력 추정까지 포함된 합이다. 의도는 docstring(L384-386): 작은 `tokens_per_min` 에서 출력 추정 때문에 혼자 거절되지 않게.
- L411: 예약을 `("tok", 키, 예약량, 분 슬롯)` 으로 기록 — 반환·정산이 그 슬롯을 본다.
- L412-429 예산: 예약 단위 금액(`amount_hold`) — 만료가 넣을 때 고정(59th). 예산은 **자기 예약까지 포함**해 판정한다(L427 `used + in_flight`) — 토큰과 규칙이 다르다.
- L430-445: 한도 초과면 그 한도의 예약을 돌려주고(L431), warn·fallback·거절로 갈린다. 거절이면 앞서 쌓은 다른 한도의 예약도 모두 돌려준다(L443).
**짚어볼 점 / Watch-outs**
- [위험:중간] **M1** 출력 추정은 클라이언트가 보낸 `max_tokens` 그대로다(`app/services/llm.py:244` — 상한·형식·부호 확인 없음, [llm.py.md](llm.py.md)). 토큰 한도는 자기 출력 추정을 빼고 판정하므로 **아무리 큰 `max_tokens` 도 자기 요청은 통과**하고, 그 예약은 같은 버킷의 다른 모든 요청에 보인다. 시나리오: `any` + `*` 한도(전체 공용 버킷 `tok:<id>:*` — `_scope_value` L297) `tokens_per_min=200000` → `chat:use` 사용자 한 명이 `max_tokens: 64000`(모델이 받아들이는 값) 요청을 4개 동시에 연다(진행 중 상한 32 안) → 각 요청은 자기 입력만으로 통과 → 창에 256k 예약 → 그 분이 끝날 때까지 **모든 사용자의 모델 호출이 429**. 실제로 쓴 토큰은 생성 속도만큼(수천)뿐이고, 매 분 반복할 수 있다. 음수 `max_tokens` 면 예약량이 음수라(L403) 창이 줄어 그 요청이 도는 동안 같은 버킷이 한도를 우회하고, 문자열이면 `int()` 가 `ValueError` → 500. 최소 수정: ① `prepare` 에서 `max_tokens` 를 정수 ≥1 로 검증(아니면 400)하고 `min(값, 모델 context_window)` 로 자르기 ② `check` 에서 출력 예약은 **남은 여유분까지만**(`min(output_tokens, max(0, 한도 − (now_used − reserve)))`) — 혼자 거절되지 않는다는 의도는 지키면서 남의 몫을 미리 잠그지 못하게.
- [위험:낮음] **L2** L403 에서 슬롯을 계산한 뒤 L404 에서 그 슬롯으로 **더한다**. 두 줄 사이에 분이 바뀌면 `window_add` 가 "지난 창" 으로 보고 아무것도 더하지 않고 0 을 돌려준다(L52-53) → `now_used = 0` → 통과, 그런데 예약은 기록되지 않았다(L411 은 기록했다고 믿음). 정산 때 덜 쓴 만큼 반환은 건너뛰고(L466-467), 더 쓴 만큼만 지금 창에 더해져 이 요청 사용량 대부분이 빠진다. 창은 마이크로초라 빈도는 낮지만, "반환에만 쓰라고 만든 슬롯 인자" 를 예약에 쓴 것이 원인이다. 최소 수정: 예약 더하기는 `slot` 없이 호출하고, `window_add` 가 실제로 쓴 슬롯을 함께 돌려주게.

```python path=app/services/ratelimit.py lines=449-472
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
```
**분석 / Analysis (Level 2)**
- L449-456 `release`(D22-③): 토큰 예약은 예약한 슬롯에만 반환 — 지난 분이면 그 창은 이미 끝났으니 할 일이 없다.
- L459-472 `settle`: 실제 > 예약이면 차이를 **지금** 창에(L464-465 — 늦게 끝난 긴 요청의 초과분도 지금 분 한도에 반영), 실제 < 예약이면 차이를 **예약 슬롯**에 반환(L466-467). 예산 예약은 반환하고(실제 비용은 사용 기록 → `spend`), 이 워커의 `spend` 캐시를 비운다(L470-472).
- C63-01(`test/test_limit_precision.py:45-61`)이 같은 분 반환·다음 분 무시·초과분 지금 창을 모두 확인한다.

## 파일 평가 / File Assessment
- 강점: (1) 분 슬롯 반환으로 다음 분 한도가 부풀지 않는다(`app/services/ratelimit.py:49-59`, `:449-467`). (2) 만료를 Valkey 서버 시계로 — 파드 시계 차이에 무관(`:130-160`). (3) `any` 예산 현황이 집행 규칙과 같은 버킷을 보여 준다(`:350-365`).
- 위험: (1) [위험:중간] M1 — 자기 출력 추정을 빼는 판정 + 검증 없는 `max_tokens` = 한 사용자가 공용 토큰 창을 잠근다(`:402-411` ↔ `app/services/llm.py:244`). (2) [위험:낮음] L2 — 예약 더하기에 슬롯 인자(`:403-404`). (3) [위험:낮음] L3 — 이름 지정 사용자 한도 현황 0%(`:353-354` ↔ `:299`).
- 개선 권고(영향순): (1) 출력 예약을 남은 여유분으로 제한 + `max_tokens` 검증·상한. (2) 예약 더하기 슬롯 제거. (3) 사용자 한도 범위 값 정규화.
- 파일 위험 등급 / Risk grade: **중간** — [위험:중간] 1(M1).
- 재읽기 권고 레벨: 2 — 예약·정산·반환이 세 함수와 두 백엔드에 걸쳐 있어 줄 단위로 읽어야 산수가 맞는지 보인다.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| H2 예산 예약 TTL 미끄러짐(59th 수정) | 유지 — 예약 단위 고정 만료 | `app/services/ratelimit.py:91-100`, `:146-160` |
| M9 출력 토큰 미예약 | 해결 · 새 결함(M1) | `:403-405` |
| L8 지난 분 창 반환 | 해결 · 예약 쪽 경계(L2) | `:49-59`, `:449-467` |
| L9 `any` 예산 버킷 의미·현황 | 현황 해결(의미는 52nd 결정 유지) | `:350-365`, `app/api/gateway_admin.py:496-501` |
| L35 보유자 만료 워커 시계 · 한 사람이 팀 예산 잠금 | 해결(Valkey TIME · 진행 중 상한 `app/services/llm.py:246-251`) | `:129-160` |
