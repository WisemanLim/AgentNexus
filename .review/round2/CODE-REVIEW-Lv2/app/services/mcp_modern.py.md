> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [mcp_modern.py.md](../../../../CODE-REVIEW-Lv2/app/services/mcp_modern.py.md)

### 09. app/services/mcp_modern.py
**파일 요약 / File Summary**
- 역할 / Role: MCP 2026-07-28(stateless) 하류 수용 어댑터 — 요청별 버전·헤더/본문 일치 검증, `server/discover`·결과 `resultType`·캐시 힌트, MRTR(InputRequiredResult ↔ inputResponses/requestState) 상태 봉인(Fernet)·워커 간 공유(mcp_relays), progress 중계(같은 워커는 큐, 다른 워커는 공유 행), `subscriptions/listen` 알림 스트림(워커 전체 동시 구독 카운터·주체별 지문 캐시), 게이트웨이 → modern 업스트림 헤더·`_meta` 생성.
- 진입점 거리 / Distance: 2 (`app/api/mcp.py:104` `mcp_endpoint` → `_modern`(L130) → 이 모듈 / 업스트림 방향은 `app/services/mcp_gateway.py:383-384,410-411`)
- LOC: 500 (현재 디스크 `wc -l`) · 1차 리뷰 시점 415 → +85
- 주요 export: `MODERN_VERSIONS`·`SUPPORTED`·`MRTR_METHODS`·`REMOVED`·`K_SERVER`·`SERVER_INFO`, `ModernError`, `requested_version`, `validate`, `check_params`, `progress_token`, `ProgressRelay`, `request_headers`, `request_meta`, `strip`, `client_caps`, `discover`, `complete`, `seal`/`unseal`, `start`/`resume`, `detach`, `listen_filter`, `listen_events`, `sse`, `MAX_LISTENS`·`LISTENS`, (신규) `listen_acquire`/`listen_release`, `_answer`(외부 사용 — `app/services/mcp_tasks.py:229`), `_take_progress`·`_FP_CACHE`(시험에서 사용)
- 의존(내부): `app.core.{config,ids,keys}`, `app.db`(`SessionLocal`·`utcnow`), `app.models.McpRelay`, (지연 import) `app.services.{ratelimit,mcp_consent,mcp_gateway,mcp_tasks}`
- 외부 패키지: mcp(`mcp.shared.inbound` — 헤더 검증 규칙), cryptography(Fernet), starlette(`run_in_threadpool`), (간접) redis — `ratelimit.RedisBackend`
- 부수효과 / Side effects: DB 쓰기(mcp_relays — 최종 결과 upsert, progress 공유 행 삽입·삭제, 입력 응답 갱신), 카운터 증감(Valkey `anx:c:listen:{sub}` 또는 워커 메모리), 워커 전역 상태 `LISTENS`·`_RUNNING`·`_PROGRESS`·`_FP_CACHE`, 백그라운드 Task(`work`·`_relay_shared`)
- 동시성 / Concurrency: asyncio Task·Queue, DB 폴링(`_wait` 0.2초·`_relay_shared` 0.3초), 다중 워커 공유(DB 행·Valkey 카운터), 스트림 종료 경로의 동기 Valkey 호출(L500)
- 테스트 커버리지 흔적: `test/test_mcp_modern.py:42,60,85,106,127`(C27-05 requestState 변조·다른 주체·다른 인자), `test/test_mcp_modern_ext.py:125,162,192,224`(listen·상한·폐기 토큰·progress), `test/test_multiworker.py:14,26,45,100`(C45-01~05 카운터·전역 한도·워커 간 progress·끊김 반납), `test/test_perf_paths.py:81`(C47-03 지문 공유), `test/test_mcp_tasks.py:76,190`(리소스·작업 구독)
- 2차 변경 요약 / Changes since 1st review:
  - Epic 45(D18-②, `test/impl/45th/scenario.md` 범위 표): L49 상한 주석이 "워커 전체 합" 으로, L53-68 `_listen_key`·`listen_acquire`·`listen_release` 신설(ratelimit 백엔드 `counter_add`). L497-500 `listen_events` 의 `finally` 에서 **동기** 반납(45th result.md "회차 중 발견": `await run_in_threadpool` 반납이 anyio 취소 범위에서 다시 취소돼 빠졌던 결함 수정).
  - Epic 45: progress 워커 간 중계 — L276-277 상수, L280-288 `_sink(state_id, server_id)` 가 큐가 없으면 공유 행으로, L291-315 `_store_progress`·`_take_progress`·`_relay_shared` 신설, L351-371 `resume` 이 쌓인 progress 를 먼저 받고 다른 워커 호출은 공유 행을 이어 받도록 재작성(try/finally).
  - Epic 47(D18-④, `test/impl/47th/scenario.md` L9): L401-414 `_FP_CACHE`·`_digest_cached`(주체별 지문 0.8×폴링 주기 공유), L417-456 `_fingerprints` 가 fetch 클로저로 재구성.
  - 나머지(L75-273 검증·봉인·MRTR 대기, L374-394 필터)는 1차 인용 원문과 동일.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/mcp_modern.py lines=1-12
"""MCP 2026-07-28 (modern, stateless) 수용 — 게이트웨이 `/mcp/{id}` 를 dual-era 서버로 (D13-④, Epic 27).

- 요청마다 `_meta` 의 `io.modelcontextprotocol/protocolVersion`·`clientCapabilities` 로 판정, 세션·initialize 없음.
- `MCP-Protocol-Version`·`Mcp-Method`·`Mcp-Name` 헤더가 본문과 다르거나 빠지면 400 HeaderMismatch(-32020).
- `server/discover` · 모든 결과에 `resultType` · `_meta.io.modelcontextprotocol/serverInfo` · 목록·읽기 결과에 `ttlMs`·`cacheScope`.
- MRTR(SEP-2322): 업스트림(2025-11-25)이 sampling·elicitation 을 요청하면 `InputRequiredResult` 로 돌려주고, 클라이언트가
  `inputResponses`+`requestState` 로 재시도하면 같은 업스트림 호출에 응답을 넘겨 최종 결과를 돌려준다(응답·결과는 mcp_relays 로 워커 간 공유).
  requestState 는 Fernet(AEAD) — 호출 주체·메서드·인자 요약·만료(10분)를 담아 다른 주체·다른 요청의 재사용을 거부한다.
- 확장(D14-②, Epic 29): `subscriptions/listen`(승인 알림 → 목록 변경 알림, 모두 subscriptionId 태그) · `x-mcp-header` 의
  `Mcp-Param-*` 검증(-32020, SDK `mcp.shared.inbound` 규칙 그대로) · `_meta.progressToken` 이 있으면 요청 단위 SSE 로
  progress 중계 · 게이트웨이 → modern 업스트림 호출용 헤더·`_meta` 생성.
"""
```
**분석 / Analysis (Level 2)**
- L1-11: 이 모듈이 구현하는 사양 요소 목록. 보안 설계의 핵심은 L8 — requestState 를 Fernet 으로 봉인하고 주체·메서드·인자 요약·만료에 묶어 "다른 주체·다른 요청" 의 재사용을 막는다. 여기서 말하지 않는 것(같은 주체의 같은 요청 재전송)은 막지 않는다는 점을 함께 기억해 둔다(L203-223 분석).
- L8 "AEAD": Fernet 은 AES-128-CBC + HMAC-SHA256(encrypt-then-MAC)이라 엄밀한 AEAD 는 아니지만 "인증된 암호화" 라는 성질(변조 탐지·기밀성)은 같다.
- Epic 45 의 워커 간 progress 공유·Valkey 카운터, Epic 47 의 지문 캐시는 docstring 에 반영돼 있지 않다 — 새로 읽는 사람은 L49·L280·L401 주석을 봐야 한다.

```python path=app/services/mcp_modern.py lines=13-26
import asyncio
import base64
import hashlib
import json
import time

from mcp.shared import inbound
from starlette.concurrency import run_in_threadpool

from app.core.config import APP_VERSION, MCP_SUPPORTED_VERSIONS
from app.core.ids import new_id
from app.db import SessionLocal, utcnow
from app.models import McpRelay
```
**분석 / Analysis (Level 2)**
- L19 `mcp.shared.inbound`: 헤더 검증 규칙을 SDK 구현 그대로 재사용 — 게이트웨이와 다른 중개자(SDK 기반 서버·프록시)가 같은 요청을 다르게 판정하지 않게 하는 선택.
- L20 `run_in_threadpool`: 동기 SQLAlchemy 세션(`SessionLocal`)을 이벤트 루프 밖 스레드에서 돌리기 위해. 이 모듈의 DB 함수(`_poll`·`_answer`·`_store_*`·`_take_progress`)는 모두 이 래퍼로만 호출된다(흔한 실수: async 함수 안에서 `SessionLocal()` 을 직접 열어 루프를 막는 것 — 이 파일은 지킨다).
- `ratelimit`·`mcp_consent`·`mcp_gateway`·`mcp_tasks` 는 함수 안에서 지연 import — 순환 import(`mcp_gateway` 가 이 모듈을 import) 회피.

```python path=app/services/mcp_modern.py lines=27-52
MODERN_VERSIONS = ("2026-07-28",)
SUPPORTED = MODERN_VERSIONS + MCP_SUPPORTED_VERSIONS
K_VERSION = "io.modelcontextprotocol/protocolVersion"
K_CAPS = "io.modelcontextprotocol/clientCapabilities"
K_CLIENT = "io.modelcontextprotocol/clientInfo"
K_SERVER = "io.modelcontextprotocol/serverInfo"
K_SUB = "io.modelcontextprotocol/subscriptionId"
HEADER_MISMATCH, UNSUPPORTED_VERSION = -32020, -32022
NAMED = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri",
         "tasks/get": "taskId", "tasks/update": "taskId", "tasks/cancel": "taskId"}  # Tasks 확장: Mcp-Name = taskId (라우팅)
MRTR_METHODS = {"tools/call", "prompts/get", "resources/read"}
CACHEABLE = {"tools/list", "prompts/list", "resources/list", "resources/read", "resources/templates/list"}
REMOVED = {"ping", "logging/setLevel", "resources/subscribe", "resources/unsubscribe", "initialize"}
SERVER_INFO = {"name": "AgentNexus MCP Gateway", "version": APP_VERSION}
CLIENT_INFO = {"name": "agentnexus-gateway", "version": APP_VERSION}  # 게이트웨이가 modern 업스트림을 부를 때
# subscriptions/listen — 필터 키 → 알림 메서드. resourceSubscriptions 는 지원하지 않아(resources.subscribe=false) 승인에서 뺀다
LISTEN_KINDS = {"toolsListChanged": "notifications/tools/list_changed",
                "promptsListChanged": "notifications/prompts/list_changed",
                "resourcesListChanged": "notifications/resources/list_changed"}
LISTEN_POLL_S = 5.0    # 목록 지문 비교 주기 — 정책(도구 규칙)·업스트림 변경을 같이 잡는다
LISTEN_PING_S = 15.0   # 프록시 유휴 종료 방지 SSE 주석
LISTEN_MAX_S = 900.0   # 이후 정상 종료(resultType complete) → 클라이언트가 다시 listen
MAX_LISTENS = 5        # 주체별 동시 구독 — 워커 전체 합(ratelimit 백엔드 카운터, prod 는 Valkey 공유 · D18-②)
LISTENS: dict[str, int] = {}  # 이 워커의 구독 수 (상태 확인용)


```
**분석 / Analysis (Level 2)**
- L27-28: modern 1개 + legacy 지원 버전(`app/core/config.py` 의 `MCP_SUPPORTED_VERSIONS`). `discover()` 가 이 목록을 그대로 광고한다.
- L29-33: 사양의 `_meta` 네임스페이스 키. 문자열 오타가 곧 상호운용 결함이 되므로 상수로 모아 둔 것.
- L35-36 `NAMED`: `Mcp-Name` 헤더와 대조할 본문 필드. Tasks 확장 메서드는 taskId 로 라우팅.
- L37-39: MRTR 대상·사설 캐시 대상·modern 에서 제거된 메서드(`ping`·`initialize` 등은 404).
- L46-48: 폴링 5초·핑 15초·최대 15분 — 15분 뒤 정상 종료하고 클라이언트가 다시 listen 한다는 설계(L48 주석). 이 "재접속이 정상 경로" 라는 점이 L60 카운터 TTL 분석에서 중요하다.
- L49 `MAX_LISTENS`: 1차의 "워커 단위" 지적을 받아 ratelimit 백엔드 카운터로 전역화(L57-68).
- L50 `LISTENS`: 이제 상태 확인용 워커 로컬 수치일 뿐 한도 판정에는 쓰이지 않는다. 앱 코드에서 읽는 곳은 없고 시험만 본다(`test/test_mcp_modern_ext.py:188`, `test/test_multiworker.py:35`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L42 주석은 "resourceSubscriptions 는 지원하지 않아(resources.subscribe=false) 승인에서 뺀다" 고 하지만, 실제로는 L181 이 `"subscribe": True` 를 광고하고 L378-390·`app/api/mcp.py:245-252` 가 읽을 수 있는 URI 를 승인한다. 낡은 주석이 반대 사실을 말해 유지보수자가 오판할 수 있다 — 주석을 현재 동작으로 고친다.
- [위험:낮음] L49 "prod 는 Valkey 공유" 는 설정 의존이다. `rate_limit_backend` 기본값이 `"memory"`(`app/core/config.py:42`)라 Valkey 를 켜지 않은 배포는 다시 워커 단위 한도(5×워커 수)가 되고, Valkey 장애 시 5초 동안 워커 메모리로 내려간다(`app/services/ratelimit.py:96-103`). 운영 문서·헬스 점검에 "listen 한도는 redis 백엔드 전제" 를 명시한다.

```python path=app/services/mcp_modern.py lines=53-74
def _listen_key(sub: str) -> str:
    return f"listen:{sub}"


def listen_acquire(sub: str) -> bool:
    """구독 1개 확보 — 전역 카운터를 올리고 한도를 넘으면 되돌린다. TTL 은 최대 구독 시간 + 여유(보유 워커가 죽어도 풀림)."""
    from app.services import ratelimit
    if ratelimit.backend().counter_add(_listen_key(sub), 1, int(LISTEN_MAX_S) + 60) > MAX_LISTENS:
        listen_release(sub)
        return False
    return True


def listen_release(sub: str) -> None:
    from app.services import ratelimit
    ratelimit.backend().counter_add(_listen_key(sub), -1, int(LISTEN_MAX_S) + 60)
STATE_TTL_S = 600
WAIT_S = 25.0
_RUNNING: dict[str, asyncio.Task] = {}
_PROGRESS: dict[str, asyncio.Queue] = {}  # state_id → 지금 이 호출을 기다리는 요청의 progress 큐 (워커 로컬)

```
**분석 / Analysis (Level 2)**
- L57-63 `listen_acquire`: "먼저 올리고(INCRBY) 넘었으면 되돌린다" 패턴. 증가 자체가 원자적이라 1차의 TOCTOU(확인과 증가가 분리돼 동시 요청이 상한을 넘김)는 사라졌다. 대가로 동시 요청이 몰리면 둘 다 6·7 을 보고 둘 다 거절될 수 있다(보수적 오거절 — 안전 쪽).
- L60·L68 TTL `int(LISTEN_MAX_S) + 60` = 960초. `counter_add` 는 호출할 때마다 키 전체의 만료를 다시 건다 — Valkey 는 `INCRBY` 뒤 `EXPIRE k ttl`(`app/services/ratelimit.py:130-141`, transaction 파이프라인), 메모리 백엔드는 `now + ttl_s`(`app/services/ratelimit.py:54-61`). 즉 **슬라이딩 TTL** 이다.
- L66-68 `listen_release`: -1. 백엔드가 0 아래로는 내리지 않는다(`ratelimit.py:58-59`, Valkey 는 `val < 0` 이면 0 으로 되돌림).
- L68 뒤에 빈 줄 없이 L69 상수가 이어진다 — 기능 문제는 아니지만 PEP 8(E305) 위반이라 읽을 때 `listen_release` 본문으로 오해하기 쉽다.
- L69-72: MRTR 상태 수명 10분, 한 번의 롱폴링 25초, 워커 로컬 실행 중 작업·progress 큐.
**짚어볼 점 / Watch-outs**
- [위험:중간] L60·L68 슬라이딩 TTL + 구독 단위 식별 없음 → 새어 나간 카운트가 활동이 있는 한 영원히 풀리지 않는다. docstring(L58)의 "보유 워커가 죽어도 풀림" 은 **그 주체의 키가 960초 동안 아무 변화도 없을 때만** 참이다. 시나리오: 사용자 A 가 구독 3개를 연 워커가 OOM·SIGKILL 로 죽는다(finally 미실행) → 카운터 3 이 남는다 → A 의 클라이언트는 설계대로(L48) 다시 listen 하고, 거절된 시도까지 포함해 acquire·release 가 매번 `EXPIRE 960` 을 갱신 → 3 은 사라지지 않아 A 의 실효 한도가 2 로 굳는다. 5개가 모두 새면 매 시도가 429 이면서 TTL 을 갱신해 **영구 잠김**(클라이언트가 16분 이상 쉬어야 풀림). Valkey 순간 장애도 같은 결과를 만든다: acquire 는 Valkey 에서, release 는 장애 창(5초) 안이라 워커 메모리 카운터로 가면(`ratelimit.py:96-103,141`) Valkey 쪽 +1 이 남는다. 또 `app/api/mcp.py:235` 에서 응답을 돌려준 뒤 제너레이터가 한 번도 시작되지 않으면 이 파일의 L497-500 이 실행되지 않는다(해당 보고서 B9). 최소 수정: 구독마다 고유 id 를 두고 Valkey ZSET(`ZADD key expiry sub_id` / `ZREM` / 판정 전 `ZREMRANGEBYSCORE key -inf now` + `ZCARD`)으로 세고, `listen_events` 루프가 폴링마다 자기 멤버의 만료를 갱신(heartbeat)한다. 확신: 확인됨(코드 경로) — 장애 재현은 미실행. 시험 공백: `test_counter_semantics`(`test/test_multiworker.py:14`)는 TTL 경과 후 0 만 보고, 활동 중 갱신으로 새는 경우는 없다.

```python path=app/services/mcp_modern.py lines=75-84
class ModernError(Exception):
    def __init__(self, code: int, message: str, http_status: int = 400, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.http_status, self.data = code, message, http_status, data

    def body(self, rid) -> dict:
        err = {"code": self.code, "message": self.message, **({"data": self.data} if self.data else {})}
        return {"jsonrpc": "2.0", "id": rid, "error": err}


```
**분석 / Analysis (Level 2)**
- L75-82 `ModernError`: JSON-RPC 오류 + HTTP 상태(기본 400). modern 사양은 헤더 불일치·미지원 버전을 HTTP 4xx 로도 표현하므로 `RpcError`(기본 200)와 따로 둔다. `app/api/mcp.py:170-171` 이 `http_status` 를 그대로 응답 상태로 쓴다.

```python path=app/services/mcp_modern.py lines=85-100
def requested_version(params: dict) -> str | None:
    meta = (params or {}).get("_meta") or {}
    v = meta.get(K_VERSION)
    return v if isinstance(v, str) else None


def _header_value(raw: str | None) -> str | None:
    """`=?base64?…?=` 표기를 풀어 비교한다 (Streamable HTTP Value Encoding)."""
    if raw is not None and raw.startswith("=?base64?") and raw.endswith("?="):
        try:
            return base64.b64decode(raw[9:-2]).decode()
        except ValueError:
            return None
    return raw


```
**분석 / Analysis (Level 2)**
- L85-88 `requested_version`: `_meta` 의 버전 키(문자열만). `params` 가 dict 가 아니면 `.get` 에서 `AttributeError` — 호출부(`app/api/mcp.py:99,102`)가 dict 여부를 확인하지 않는다(해당 보고서 B4).
- L91-98 `_header_value`: 비 ASCII 값을 헤더로 보내는 `=?base64?…?=` 표기를 풀어 비교. `binascii.Error`·`UnicodeDecodeError` 는 모두 `ValueError` 하위라 L96 에서 잡혀 `None`(=불일치). `b64decode` 는 기본 `validate=False` 라 알파벳 밖 문자를 조용히 버리지만, 결과가 본문 값과 다르면 어차피 L116 에서 불일치 처리된다.

```python path=app/services/mcp_modern.py lines=101-119
def validate(headers, method: str, params: dict, version: str) -> None:
    """버전 지원 여부 → 헤더·본문 일치 (서버 검증 규칙)."""
    if version not in MODERN_VERSIONS:
        raise ModernError(UNSUPPORTED_VERSION, "Unsupported protocol version", data={"supported": list(SUPPORTED), "requested": version})
    dup = inbound.find_duplicated_routing_header(headers.items())
    if dup is not None:  # 첫 값·끝 값을 읽는 중개자가 서로 다르게 판단하지 않도록
        raise ModernError(HEADER_MISMATCH, f"Header mismatch: {dup} 헤더가 여러 번 있습니다.")
    hv = headers.get("mcp-protocol-version")
    if hv is None or hv != version:
        raise ModernError(HEADER_MISMATCH, f"Header mismatch: MCP-Protocol-Version '{hv}' ≠ body '{version}'")
    if headers.get("mcp-method") != method:
        raise ModernError(HEADER_MISMATCH, f"Header mismatch: Mcp-Method '{headers.get('mcp-method')}' ≠ body '{method}'")
    if method in NAMED:
        want = (params or {}).get(NAMED[method])
        got = _header_value(headers.get("mcp-name"))
        if got is None or got != want:
            raise ModernError(HEADER_MISMATCH, f"Header mismatch: Mcp-Name '{got}' ≠ body '{want}'")
```
**분석 / Analysis (Level 2)**
- L103-104: 지원하지 않는 버전 → -32022 + 지원 목록(클라이언트가 내려갈 버전을 고를 수 있게).
- L105-107: 라우팅 헤더 **중복** 거부. Starlette `Headers.items()` 는 같은 이름의 헤더를 모두 돌려주므로 SDK 함수가 중복을 볼 수 있다(`headers.get` 은 첫 값만). 앞 값·끝 값을 읽는 중개자가 서로 다르게 라우팅하는 헤더 스머글링 방지.
- L108-117: `MCP-Protocol-Version`·`Mcp-Method`·`Mcp-Name` 이 본문과 같아야 한다 — WAF·프록시가 헤더만 보고 정책을 적용해도 본문과 어긋나지 않게 하는 사양 요구. `want` 가 문자열이 아니면(정수 등) 헤더 문자열과 같을 수 없어 거부된다.
- 오류 메시지에 헤더 원문을 그대로 넣는다(L110·L112·L117). JSON 응답 본문이라 HTML 주입 위험은 없다.

```python path=app/services/mcp_modern.py lines=120-133
def check_params(schema: dict | None, arguments, headers) -> None:
    """tools/call 의 `Mcp-Param-*` ↔ 인자 일치 (x-mcp-header 가 붙은 속성) — 어긋나면 400 HeaderMismatch."""
    if not isinstance(schema, dict):
        return
    rej = inbound.validate_mcp_param_headers(schema, arguments if isinstance(arguments, dict) else {}, headers)
    if rej is not None:
        raise ModernError(HEADER_MISMATCH, f"Header mismatch: {rej.message}")


def progress_token(params