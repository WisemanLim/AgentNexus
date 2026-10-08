> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: 1차 리뷰 대상 아님(부록 A)

### 20. app/services/ratelimit.py
**파일 요약 / File Summary**
- 역할 / Role: 게이트웨이 한도·예산(FR-G04) — 요청 토큰 버킷(rps·burst), 분당 토큰 창, 일/월 비용 예산(초과 시 block·fallback·warn), 그리고 범용 백엔드 프리미티브(고정 창 카운터 `window_*` — 로그인 실패 한도, 증감 카운터 `counter_add` — MCP listen 동시 구독 한도). 백엔드는 프로세스 메모리 또는 Valkey(redis 프로토콜), Valkey 장애 시 5초 단위로 워커 로컬 메모리로 강등.
- 진입점 거리 / Distance: 2 (`app/api/idp.py:108,116` 로그인 실패 창, `app/api/ops.py:335-344` 미리보기, `app/api/gateway_admin.py:450,475,498`) · 서비스 경유 `app/services/llm.py:222,393`, `app/services/mcp_gateway.py:940`, `app/services/a2a_gateway.py:454`, `app/services/mcp_modern.py:60,68`
- LOC: 301 (현재 `wc -l`) · 1차 INDEX 부록 A 기준 277(`.review/CODE-REVIEW-Lv2/INDEX.md:334`) → +24
- 주요 export: `MemoryBackend`, `RedisBackend`, `backend`, `backend_status`, `LimitResult`, `check`, `record_tokens`, `spend`, `clear_cache`, `_scope_value`·`_target_match`(밑줄이지만 `app/api/ops.py:335-336` 가 직접 사용)
- 의존(내부): `app.core.auth.Principal`, `app.core.config.settings`, `app.models.{RateLimit,UsageRecord}`, `app.services.notify.notify`, (지연) `app.core.logs`
- 외부 패키지: redis(>=5.0, `pyproject.toml:22` — 지연 import), sqlalchemy
- 부수효과 / Side effects: Valkey 키 쓰기(`anx:*`, 모두 EXPIRE 부여), 프로세스 메모리 dict, DB 읽기(한도·사용액 합계), 알림 생성(`notify` — 별도 세션·DB 쓰기·Slack 웹훅 스레드)
- 동시성 / Concurrency: 메모리 백엔드는 `threading.Lock`(스레드풀 호출 전제), Valkey 는 Lua 스크립트·MULTI/EXEC 파이프라인으로 원자적, 백엔드 싱글턴은 락으로 1회 생성. 다중 워커(`UVICORN_WORKERS=2`, `.env.prod:22`)는 Valkey 로만 공유
- 테스트 커버리지 흔적: `test/test_nfr.py:8-24`(`test_rate_limiter_survives_valkey_outage`), `test/test_multiworker.py:14-24`(`test_counter_semantics`)·`:26-41`(`test_listen_limit_is_global`), `test/test_llm.py:170-185`(`test_rate_limit_429_and_budget_fallback`), `test/test_gateway_policy.py:107`(`test_budget_alert_block_and_fallback`)
- 2차 변경 요약 / Changes since 1st review: 1차 라인 리뷰 대상이 아니어서 인용 원문 비교는 불가. 의도 문서로 확인되는 변경 = 증감 카운터 `counter_add`(메모리 L54-61, Valkey L130-141)와 `reset` 의 카운터 정리(L67) — Epic 45 D18-② "listen 한도를 워커 전체 합으로"(`test/impl/45th/scenario.md:7`). 로그인 실패 한도(Epic 39 SEC-03, `test/impl/39th/scenario.md:20`)는 기존 `window_*` 를 재사용.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/ratelimit.py lines=1-18
"""Rate limits · 예산 (FR-G04) — 사용자·에이전트·팀·클라이언트 키 × 모델·도구·에이전트 단위
요청(토큰 버킷)·토큰(분당)·비용(일/월) 한도. 초과 시 429 또는 저비용 모델 폴백. 백엔드: memory | Valkey(redis).
"""
import fnmatch
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.config import settings
from app.models import RateLimit, UsageRecord
from app.services.notify import notify
```
**분석 / Analysis (Level 2)**
- L1-2: 세 종류 한도(요청·토큰·비용)와 초과 동작. docstring 에 없는 두 용도(로그인 실패 창, listen 카운터)도 이 모듈의 백엔드를 쓴다 — 이 파일을 바꿀 때 영향 범위가 "게이트웨이 한도" 보다 넓다는 점을 기억해야 한다.
- L6 `threading`: 메모리 백엔드는 스레드풀(동기 엔드포인트) 동시 호출을 전제로 락을 건다.
- L10 `Decimal`: 비용 합계를 부동소수 오차 없이 비교.

```python path=app/services/ratelimit.py lines=21-52
# ── 백엔드 ──
class MemoryBackend:
    def __init__(self):
        self._buckets: dict[str, tuple[float, float]] = {}
        self._windows: dict[str, tuple[int, int]] = {}
        self._counters: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str, rate: float, burst: int, cost: float = 1.0) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
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

    def window_add(self, key: str, n: int, window_s: int = 60) -> int:
        slot = int(time.time() // window_s)
        with self._lock:
            s, cur = self._windows.get(key, (slot, 0))
            cur = cur + n if s == slot else n
            self._windows[key] = (slot, cur)
            return cur
```
**분석 / Analysis (Level 2)**
- L24-26: 세 저장소 — 버킷(남은 토큰·마지막 시각), 고정 창(슬롯 번호·누적), 카운터(값·만료 시각).
- L29-38 `take`(토큰 버킷): 새 키는 가득 찬 버킷(`burst`)으로 시작, 경과 시간 × rate 만큼 채우되 burst 상한. 충분하면 차감·허용, 아니면 현재 토큰을 저장하고 **다시 차기까지 기다릴 초**(`(cost - tokens) / rate`)를 돌려준다(→ `Retry-After`). `time.monotonic()` 이라 시계 조정에 영향받지 않는다.
- L40-52 `window_*`(고정 창): 슬롯 = `time.time() // window_s`(에포크 기준 정렬). 슬롯이 바뀌면 0 부터.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L24-25: 메모리 dict 는 **제거가 없다.** 로그인 실패 창 키는 `login-fail:{입력한 사용자 이름}`(`app/api/idp.py:107`)이라 **인증 전** 공격자가 무작위 이름으로 키를 무한히 만들 수 있다. 메모리 백엔드(코드 기본값 `rate_limit_backend="memory"`, `app/core/config.py:42`)와 Valkey 강등 구간(L99·L106)에서 메모리가 계속 는다. compose·Helm 은 기본 Valkey(`.env.prod:28`, `deploy/helm/agentnexus/values.yaml:51`)라 영향은 강등 구간·미설정 배포로 한정. 최소 수정: 슬롯이 지난 창 항목을 주기적으로(또는 크기 상한 초과 시) 정리.
- [위험:낮음] L40-52: 고정 창이라 창 경계 직전·직후에 한도의 2배까지 몰아 쓸 수 있다(로그인 10회/15분 → 경계에서 20회). 최소 수정: 두 슬롯 가중 합(sliding window counter).

```python path=app/services/ratelimit.py lines=54-67
    def counter_add(self, key: str, delta: int, ttl_s: int) -> int:
        """동시 사용 수 같은 증감 카운터 — 0 아래로 내려가지 않고, 마지막 변경 뒤 ttl_s 가 지나면 0 (보유 워커가 죽어도 풀린다)."""
        now = time.monotonic()
        with self._lock:
            cur, exp = self._counters.get(key, (0, 0.0))
            cur = max((cur if exp > now else 0) + delta, 0)
            self._counters[key] = (cur, now + ttl_s)
            return cur

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._windows.clear()
            self._counters.clear()
```
**분석 / Analysis (Level 2)**
- L54-61 `counter_add` (Epic 45 신규): 만료됐으면 0 에서, 아니면 현재 값에서 delta 를 더하고 0 하한, **매 변경마다 만료를 now+ttl 로 연장**. 호출처: `listen_acquire`(+1, 한도 초과 시 즉시 -1)·`listen_release`(-1) — `app/services/mcp_modern.py:57-68`, TTL = `LISTEN_MAX_S`(900초)+60(`app/services/mcp_modern.py:48`).
- L63-67 `reset`: 시험용 전체 초기화.
**짚어볼 점 / Watch-outs**
- [위험:중간] L59-60(메모리)·L134-135(Valkey) 공통: docstring 은 "마지막 변경 뒤 ttl_s 가 지나면 0 (보유 워커가 죽어도 풀린다)" 라고 하지만, 만료가 **키 전체 기준으로 매 변경마다 연장**되므로 같은 주체의 다른 변경이 계속되는 한 죽은 워커가 쥐고 있던 몫은 **영원히 풀리지 않는다.** 특히 거절된 확보 시도도 +1/-1 두 번의 변경으로 TTL 을 연장한다(`app/services/mcp_modern.py:60-61`). 시나리오: 사용자의 listen 5개를 보유한 워커가 OOM·SIGKILL 로 죽으면(정상 종료가 아니라 반납 경로가 실행되지 않음) 카운터는 5 로 남고, MCP 클라이언트가 재연결을 16분(960초) 안에 반복 시도하는 한 매 시도가 TTL 을 갱신해 그 사용자는 **listen 이 계속 429** 다. 일부(예: 2개)만 샌 경우에도 그 사용자의 실효 한도가 3 으로 줄어든 채 유지된다. 최소 수정: 보유자별 리스(예: Valkey ZSET 에 `listen-id → 만료시각` 점수로 넣고 확보 시 `ZREMRANGEBYSCORE` 로 만료분 제거 후 `ZCARD` 비교, 보유 중 주기적 갱신), 또는 최소한 거절 경로에서 TTL 을 연장하지 않는 `INCR`-조건부 Lua.

```python path=app/services/ratelimit.py lines=70-81
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
```
**분석 / Analysis (Level 2)**
- 초점 항목(원자성): 메모리 판 `take` 와 같은 토큰 버킷을 **Lua 스크립트 한 번**으로 실행한다. Valkey 는 스크립트를 단일 스레드에서 끊김 없이 실행하므로 여러 워커·파드의 동시 차감이 경쟁 없이 직렬화된다(HMGET→계산→HSET 사이에 끼어들 수 없음).
- L79 `EXPIRE`: 버킷이 가득 찰 때까지의 시간 + 5초 — 쉬는 키는 사라지므로 키 개수(cardinality)가 "최근 활동한 (한도 × 주체)" 로 묶인다.
- L80 `tostring(t)`: Lua 숫자를 그대로 반환하면 Redis 프로토콜이 **정수로 잘라** 소수 토큰이 사라진다 — 문자열로 넘겨 L113 에서 `float` 로 복원하는 세부가 정확하다(흔한 실수 지점).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L72 `now` 가 **호출 측 시계**(`time.time()`, L110)다. 파드 간 시계가 어긋나면 늦은 시계의 파드가 `now - ts < 0` 을 만들어 토큰을 깎고(과잉 차단), 빠른 시계의 파드는 더 많이 채운다(과소 차단). 최소 수정: 스크립트 안에서 `redis.call('TIME')` 사용.

```python path=app/services/ratelimit.py lines=84-114
class RedisBackend:
    """Valkey 공유 버킷. 연결이 끊기면 5초 동안 이 프로세스의 메모리 버킷으로 버틴다(NFR-A1 — 한도는 워커 단위로 느슨해짐, 차단은 하지 않음)."""
    DEGRADE_S = 5.0

    def __init__(self, url: str):
        import redis
        self._r = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
        self._bucket = self._r.register_script(_LUA_BUCKET)
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
```
**분석 / Analysis (Level 2)**
- L85 설계 결정(초점 항목 — Valkey 장애 대체): **fail open(차단하지 않음) + 워커 로컬 한도**. 한도 저장소 장애가 게이트웨이 전체 장애로 번지지 않게 가용성(NFR-A1)을 우선했다.
- L90: 0.5초 소켓·연결 타임아웃 — 장애 시 요청이 오래 붙잡히지 않게.
- L91 `register_script`: 첫 호출은 `EVALSHA`, 서버에 스크립트가 없으면(재시작·장애조치) redis-py 가 `NOSCRIPT` 를 받아 다시 적재한다.
- L96-106 `_guard`: 강등 창(5초) 동안은 원격을 시도하지 않고 로컬, 연결 오류면 강등 시작 + 경고 로그 + 로컬. 강등 창이 지나면 다음 호출이 원격을 다시 시도한다(`test/test_nfr.py:21-22`).
- L108-114 `take`: 키 접두 `anx:`, 결과를 메모리 판과 같은 형태로.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L102: 연결·타임아웃 오류만 강등한다. Valkey 장애조치 직후 복제본에 쓰면 오는 `ReadOnlyError`, 메모리 한도의 `OOM command not allowed` 등은 `ResponseError` 계열이라 **그대로 전파**되어 한도 검사를 거치는 모든 게이트웨이 호출이 500 이 된다(가능성 — 배포 토폴로지 의존). 최소 수정: `redis.RedisError` 전체를 강등 대상으로.
- [위험:낮음] L85·L98-99: 강등 구간에는 한도가 **워커 수 × 파드 수** 배로 느슨해진다(의도). 같은 백엔드를 쓰는 로그인 실패 한도(`app/api/idp.py:108-116`)도 같이 느슨해지고, 강등 중 로컬에 쌓인 카운트는 복구 뒤 Valkey 와 합쳐지지 않는다 — listen 카운터는 로컬에서 +1, 복구 뒤 Valkey 에서 -1 이 되어 양쪽 값이 어긋난다(Valkey 쪽은 0 하한으로 한도가 일시 느슨, 로컬 쪽은 다음 강등 때 과잉 차단).
- `_down_until`·`degraded_events` 는 락 없이 갱신 — 여러 스레드가 동시에 강등을 시작해도 결과가 같아 무해.

```python path=app/services/ratelimit.py lines=116-144
    def window_get(self, key: str, window_s: int = 60) -> int:
        slot = int(time.time() // window_s)
        return self._guard(lambda: int(self._r.get(f"anx:w:{key}:{slot}") or 0), lambda: self._local.window_get(key, window_s))

    def window_add(self, key: str, n: int, window_s: int = 60) -> int:
        def remote():
            slot = int(time.time() // window_s)
            k = f"anx:w:{key}:{slot}"
            pipe = self._r.pipeline()
            pipe.incrby(k, n)
            pipe.expire(k, window_s + 5)
            return int(pipe.execute()[0])
        return self._guard(remote, lambda: self._local.window_add(key, n, window_s))

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

    def ping(self) -> bool:
        return bool(self._r.ping())
```
**분석 / Analysis (Level 2)**
- L116-118: 창 키에 슬롯 번호를 넣어(`anx:w:{key}:{slot}`) 창마다 별도 키 — 이전 창 키는 EXPIRE 로 사라진다(키 증가 없음).
- L120-128: redis-py `pipeline()` 의 기본값은 `transaction=True`(MULTI/EXEC) — `INCRBY` 와 `EXPIRE` 가 원자적으로 함께 적용되어 "증가만 되고 만료가 없는 키" 가 남지 않는다.
- L130-141 `counter_add`(Epic 45): 같은 패턴 + 음수면 0 으로 되돌림.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L137-139: 0 하한 보정이 파이프라인 **밖의 별도 명령**이다. 반납(-1)이 -1 을 본 직후 다른 워커의 확보(+1)가 0 을 만들고, 그 뒤 `SET 0` 이 실행되면 확보 1건이 지워져 실제 6개가 동시에 열릴 수 있다(경합 창은 좁음). 최소 수정: Lua 로 `INCRBY` + 하한 + `EXPIRE` 를 한 번에.
- 초점 항목(Epic 45 listen 카운터): 전역 합산 자체는 맞게 구현됐다(`test/test_multiworker.py:26-41`). 남은 문제는 위 TTL 연장 [위험:중간].

```python path=app/services/ratelimit.py lines=147-171
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
```
**분석 / Analysis (Level 2)**
- L147-159: 프로세스 싱글턴 — 락으로 두 스레드가 동시에 백엔드를 두 개 만드는 경합을 막는다(이중 확인 없이 매 호출 락 — 비용은 작다). 설정은 최초 1회만 읽으므로 백엔드 전환은 재시작이 필요.
- L155: `redis` 인데 URL 이 비면 조용히 메모리 — 다중 워커에서 한도가 워커별이 되는 설정 실수를 알려 주지 않는다(아래 `backend_status` 가 `memory` 로 보여 줄 뿐).
- L162-171 `backend_status`: 헬스·관리 화면용(`app/api/admin.py:107`, `app/api/observe.py:244`). 핑 실패는 `degraded: True` 로.

```python path=app/services/ratelimit.py lines=174-211
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
```
**분석 / Analysis (Level 2)**
- L175-183 `LimitResult`: 허용 여부·사유·재시도 초·폴백 모델·걸린 한도·예산 현황·경고.
- L186-205 `_scope_value`: 한도가 이 주체에 적용되는지 + **버킷 구분값**. `any` 는 `per-subject` 면 주체별, 아니면 전체 공유 버킷(`"*"`). user 는 `*`·sub·이름, team 은 팀이 있을 때, agent 는 신고된 agent 가 있을 때(사람 주체는 None — `app/services/policy.py:70`), client_key 는 토큰 id.
- L208-211 `_target_match`: 대상 종류(any 또는 같은 종류) + glob.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L192: user 한도도 **표시 이름**으로 매치한다(`app/services/policy.py:118` 과 같은 문제) — 이름으로 건 한도는 외부 IdP 표시 이름 변경으로 회피된다(가능성). 최소 수정: sub(와 username)만.
- [위험:낮음] L194-196: 팀 없는 주체(워크로드·Access Token — `app/core/auth.py:238,246` 은 `team` 을 채우지 않음)는 모든 team 한도에서 빠진다(의도일 수 있으나 문서화 필요).

```python path=app/services/ratelimit.py lines=214-242
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
    if scope_type == "team":
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
```
**분석 / Analysis (Level 2)**
- L214-218: 기간 시작 = **UTC** 자정/1일 — 한국 사용자에게는 예산이 매일 09:00(KST)에 초기화된다(화면 표기 시 주의).
- L221-238 `spend`: 기간 내 비용 합계(SQL `SUM`, 없으면 0), 워커별 5초 캐시. `Decimal(str(...))` 로 드라이버가 float 를 줘도 이진 오차 없이 변환. 관리 화면은 캐시를 끄고 읽는다(`app/api/gateway_admin.py:498`).
- L230-235: scope_type 이 셋 중 무엇도 아니면 **필터 없이 전체 합계**가 된다(호출부가 team·user·agent 만 넘기므로 현재는 안전).
- L241-242 `clear_cache`: 한도 저장 직후 이 워커의 사용액 캐시 비우기(`app/api/gateway_admin.py:475`) — 다른 워커는 최대 5초 뒤.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L227·L236-237: 예산은 **호출 전 검사·호출 후 기록**(기록은 `app/services/llm.py:386-392`)이고 합계는 워커별 5초 캐시라, 동시 요청·다중 워커에서 예산을 넘겨 쓸 수 있다(소프트 한도). 하드 한도가 필요하면 Valkey 에 예약 차감(reserve) 방식.

```python path=app/services/ratelimit.py lines=245-278
def check(db: Session, p: Principal, kind: str, target: str, agent: str | None = None,
          estimated_tokens: int = 0) -> LimitResult:
    """호출 전 검사. 요청 버킷은 이 시점에 차감된다."""
    limits = db.scalars(select(RateLimit).where(RateLimit.enabled.is_(True))).all()
    result = LimitResult()
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
        if exceeded is None and lim.tokens_per_min:
            used = backend().window_get(f"tok:{lim.id}:{sv}")
            if used + estimated_tokens > lim.tokens_per_min:
                exceeded = (f"토큰 한도 초과 ({lim.name}: {used}/{lim.tokens_per_min} tokens/min)",
                            60 - int(time.time()) % 60)
        if exceeded is None and lim.budget_usd is not None and lim.scope_type in ("team", "user", "agent", "any"):
            st = lim.scope_type if lim.scope_type != "any" else "team"
            val = sv if lim.scope_type != "any" else (p.team or "")
            used = spend(db, st, val, lim.period)
            pct = float(used / lim.budget_usd * 100) if lim.budget_usd else 100.0
            result.budget.append({"limit_id": lim.id, "name": lim.name, "used_usd": float(used),
                                  "budget_usd": float(lim.budget_usd), "pct": round(pct, 1)})
            if pct >= lim.alert_pct:
                notify("budget.threshold", f"예산 {pct:.0f}% 사용: {lim.name}", f"{st}={val} {used}/{lim.budget_usd} USD",
                       severity="danger" if pct >= 100 else "warn", link="/console/observe/budgets",
                       audience_role="finops", dedupe_key=f"budget:{lim.id}:{val}:{int(pct // 10)}")
            if used >= lim.budget_usd:
                exceeded = (f"예산 초과 ({lim.name}: {used}/{lim.budget_usd} USD/{lim.period})", 3600)
```
**분석 / Analysis (Level 2)**
- L248: 요청마다 활성 한도 전체를 읽는다(한도 수가 적다는 전제).
- L255-260 요청 한도: `rps` 는 `Numeric(10,3)`(`app/models.py:712`) → `float` 변환. burst 미지정이면 `ceil(rps)`(최소 1). `Retry-After` 는 최소 1초.
- L261-265 토큰 한도: 현재 분 창 사용량 + **추정** 입력 토큰(`app/services/llm.py:221`) 으로 사전 판정. 실제 사용량은 호출 뒤 `record_tokens`(L295)로 더한다.
- L266-278 예산: `any` 는 **호출자 팀의 사용액**으로 판정(L267-268), 사용률이 알림 임계(`alert_pct`) 이상이면 10% 단위 dedupe 키로 알림, 100% 이상이면 초과(재시도 3600초).
**짚어볼 점 / Watch-outs**
- [위험:중간] L266-269: `any` 범위 예산은 호출자 **팀** 사용액으로 계산하는데, 팀이 없는 주체는 `spend(db, "team", "")` → `UsageRecord.team == ""` 가 되어 NULL 로 저장된 사용 기록(`app/services/llm.py:387` 이 `team=ctx.principal.team`)과 **하나도 매치되지 않아 사용액 0** → 예산이 영원히 초과되지 않는다. 시나리오: FinOps 가 "전체 월 500달러" 를 `scope_type=any` 로 걸어도, 팀이 없는 워크로드·Access Token 주체(`app/core/auth.py:238,246`)나 팀 미지정 사용자는 무제한으로 호출한다. 또 `client_key` 범위 예산은 L266 목록에서 빠져 **조용히 무시**되는데 생성 API 는 이를 받아 준다(`app/api/gateway_admin.py:424,431` — `scope_type` 에 client_key, `budget_usd` 허용). 최소 수정: `any`+`*` 는 필터 없는 전체 합계, `any`+`per-subject` 는 user 합계로 계산하고, client_key 예산은 생성 시 400 또는 `UsageRecord` 에 토큰 id 를 기록해 지원.
- [위험:낮음] L255-260: 앞의 한도에서 토큰을 **이미 차감**한 뒤 뒤의 한도가 막으면(L291) 거절된 요청이 앞 버킷을 소모한다. 미리보기(sampling 동의 카드)도 `llm.prepare` 를 통해 이 함수를 불러 차감한다(`app/services/mcp_consent.py:108` 과 `:186` — 1차 `mcp_consent.py.md:161` 지적, 이 파일에 차감 없는 판정 API 가 여전히 없음).
- [위험:낮음] L273-276: 임계 이상이면 **요청마다** `notify` 가 별도 세션을 열어 dedupe 조회를 한다(`app/services/notify.py:13-19`) — 예산 80% 이후 모든 호출에 DB 왕복이 하나씩 붙고, 조회-삽입이 원자적이지 않아 동시 요청에서 알림이 중복될 수 있다. 최소 수정: 워커 로컬 dedupe 캐시를 앞에 두기.

```python path=app/services/ratelimit.py lines=279-292
        if exceeded:
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
            return LimitResult(False, exceeded[0], exceeded[1], None, lim.id, result.budget)
    return result
```
**분석 / Analysis (Level 2)**
- L280-285 warn: 차단하지 않고 경고 목록·알림(10분 dedupe).
- L286-290 fallback: 모델 호출이고 이미 폴백 모델이 아니면 폴백 모델을 지정하고 **다음 한도 평가를 계속**한다. 폴백 한도가 여러 개면 마지막 것이 이긴다.
- L291: block — 즉시 429 정보 반환(예산 현황 포함).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L286-290: 폴백을 정한 뒤에도 남은 한도는 **원래 대상**(`target`) 기준으로 평가되고, 폴백 모델 자신의 한도는 어디에서도 검사되지 않는다(호출부 `app/services/llm.py:226-232` 는 다시 `check` 하지 않음). 더 큰 문제로 그 폴백 모델은 정책·데이터 등급·에어갭 검사도 건너뛴다 — `llm.py` 보고서의 [위험:높음] 참조. 최소 수정: 폴백 모델로 `check` 를 한 번 더(차감 포함) 수행하고, 정책 승인은 호출부에서.

```python path=app/services/ratelimit.py lines=295-301
def record_tokens(db: Session, p: Principal, kind: str, target: str, tokens: int, agent: str | None = None) -> None:
    if tokens <= 0:
        return
    for lim in db.scalars(select(RateLimit).where(RateLimit.enabled.is_(True), RateLimit.tokens_per_min.is_not(None))):
        sv = _scope_value(lim, p, agent)
        if sv is not None and _target_match(lim, kind, target):
            backend().window_add(f"tok:{lim.id}:{sv}", tokens)
```
**분석 / Analysis (Level 2)**
- L295-301: 호출 뒤 실제 토큰(입력+출력)을 해당 분 창에 더한다(`app/services/llm.py:393`). 키 구분값 계산은 `check` 와 같은 함수라 두 지점이 같은 버킷을 가리킨다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 스트리밍을 클라이언트가 중간에 끊으면 `finalize` 가 불리지 않아(`app/api/llm.py:93-120` — 그 파일 범위) 이 함수도 호출되지 않는다 → 토큰 한도·예산 기록에서 빠진다(교차 이슈, `llm.py` 보고서 참조).

## 파일 평가 / File Assessment
- 강점: (1) Valkey 토큰 버킷을 Lua 한 번으로 원자 처리하고 `tostring` 으로 소수 토큰을 보존, 모든 키에 EXPIRE 를 붙여 키 증가를 억제. (2) Valkey 장애 시 0.5초 타임아웃 + 5초 강등 창으로 게이트웨이 가용성을 지키면서 로컬 한도는 유지(`test/test_nfr.py:8-24`). (3) 한도·토큰·예산·warn/fallback/block 을 한 루프에서 일관 판정하고 예산 현황을 응답에 실어 FinOps 화면과 공유.
- 위험: (1) [위험:중간] 증감 카운터 TTL 이 매 변경마다 연장되어 죽은 워커의 listen 몫이 활성 사용자에게서 풀리지 않음 (L59-60·L134-135). (2) [위험:중간] `any` 예산이 팀 없는 주체에게 적용되지 않음·client_key 예산 무시 (L266-269). (3) [위험:낮음] `ResponseError` 계열 미강등, 호출 측 시계 사용, 0 하한 비원자, 메모리 dict 무제거, 폴백 모델 한도 미검사 (L102·L110·L137-139·L24-25·L286-290).
- 개선 권고(영향순): (1) listen 은 보유자별 리스(ZSET+만료 점수)로 재설계. (2) `any` 예산을 전체/주체 합계로, client_key 예산은 생성 시 거부. (3) `redis.RedisError` 전체 강등·`redis.call('TIME')`·하한 Lua·메모리 창 정리, 차감 없는 `peek` API 를 만들어 미리보기에서 사용.
- 파일 위험 등급 / Risk grade: 중간 — 미해결 [위험:중간] 2건(가용성·FinOps 통제), 보안 판정 자체는 아님.
- 재읽기 권고 레벨: 2 — 원자성 근거(Lua·MULTI/EXEC)와 강등 의미를 정확히 알아야 수정이 안전하다.

## 1차 리뷰 대비 / vs 1st Review
1차 라인 리뷰 대상이 아니므로(부록 A) 1차 INDEX·다른 1차 보고서·위임 보고서 중 이 파일 관련 항목만 비교한다.

| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 위임 `code-review.md` BUG-06: listen 동시 구독 한도가 워커 단위 — 2워커에서 2배 | 해결 | 전역 증감 카운터 `app/services/ratelimit.py:54-61`·`:130-141`, 사용처 `app/services/mcp_modern.py:57-68`, 시험 `test/test_multiworker.py:26-41`. 단 새 결함: TTL 연장으로 누수분이 풀리지 않음(`:59-60`·`:134-135`) |
| 위임 `security-review.md` SEC-03: 로그인 무차별 대입 방어 없음 | 부분 해결 | 이 모듈의 고정 창으로 10회/15분(`app/api/idp.py:98-117` → `:40-52`·`:116-128`). 조회(`window_get`)와 증가(`window_add`)가 분리돼 동시 시도가 한도를 넘을 수 있고(`app/api/idp.py:108`·`:116`), 창 경계 2배·강등 시 워커별 |
| 1차 `app/services/mcp_consent.py.md:161`: `check` 가 버킷을 차감 — 미리보기와 실제 생성이 각각 차감 | 미해결 | `:247`·`:258` 차감 방식 그대로, 차감 없는 판정 API 없음(`app/services/mcp_consent.py:108`·`:186`) |
| 1차 `app/services/llm.py.md` L221: 한도는 첫 후보 기준 — 폴백 모델의 한도 미검사 | 미해결 | `:286-290` 폴백 지정 후 재검사 없음(`app/services/llm.py:222`·`:226-232`) |
| INDEX §4.1 #2 워커 로컬 캐시 상한 없음 | 미해결(이 파일 해당분) | `_SPEND_CACHE`(`:221`)·메모리 백엔드 dict(`:24-26`) 제거 없음 |
