> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../INDEX.md) · 디렉터리 개요: [app_Abstract.md](app_Abstract.md) · 1차 리뷰: [main.py.md](../../../CODE-REVIEW-Lv2/app/main.py.md)

### 01. app/main.py
**파일 요약 / File Summary**
- 역할 / Role: FastAPI ASGI 앱 조립 지점. 구성요소(`COMPONENTS`)별 라우터 선택 마운트, lifespan 에서 백그라운드 작업(참조 MCP 도구 조회 재시도·MCP 헬스 루프·A2A 감시기·스케줄러) 기동, 콘솔 SPA·라이선스 정적 서빙, `/health`.
- 진입점 거리 / Distance: 0 (`uvicorn app.main:app` — `Dockerfile:55` CMD, Helm `deploy/helm/agentnexus/templates/api.yaml:43`)
- LOC: 203 (현재 `wc -l`) · 1차 195 → +8
- 주요 export: `app`(FastAPI), `lifespan`, `EMBEDDED`, `BACKGROUND`, `health`, (내부) `_refresh_builtin_mcp`·`_refresh_builtin_a2a`·`_health_loop`
- 의존(내부): `app.db`, `app.api.*`(16개 라우터 무조건 import), `app.core.{errors,logs,tracing,config}`, `app.services.{orchestrator,mcp_gateway,a2a_gateway,scheduler,slo,setup}`(일부 지연 import), `app.models`, `app.builtin.demo_idp`
- 외부 패키지: fastapi, starlette(`StaticFiles`·`FileResponse`·`run_in_threadpool`), sqlalchemy
- 부수효과 / Side effects: import 시점 `logs.setup()`·`tracing.setup()`(전역 TracerProvider), 라우터 등록, `gateway_admin_api.APPROVAL_HOOKS`·`a2a_api.PLATFORM` 전역 변이. 기동 시 DB 쓰기(프로세스 시작 이벤트·설정 코드). 주기마다 `settings` 테이블 리스 행 갱신(`scheduler.lease`, 2차 신규).
- 동시성 / Concurrency: asyncio — lifespan 이 `asyncio.create_task` 로 장기 루프 최대 5개 + `BACKGROUND`. 루프 안 DB 조회 일부는 이벤트 루프에서 동기 실행(L48-50·L65-66·L85-86), 리스 획득만 `run_in_threadpool`(L83).
- 테스트 커버리지 흔적: `test/test_health.py:6-7`(/health 200), `test/conftest.py:63-64`(EMBEDDED 로 데모 MCP 서버 in-process), `test/test_admin.py:17`(구성요소별 OpenAPI 경로), `test/test_multiworker.py:77-98`(리스 — **`a2a_gateway.watcher` 만** 검증, `_health_loop` 의 리스·예외 경로는 시험 없음)
- **스냅샷 이후 변경 고지**: 이 보고서의 라인 번호·인용은 스냅샷(21:03, sha1 `b961a1dc…`, 203 LOC) 기준이다. 리뷰 도중 21:21 에 디스크의 `app/main.py` 가 수정됐다(sha1 `90725cd6…`, 232 LOC). 확인한 차이는 **한 군데뿐**: 스냅샷 L123 과 L126 사이에 `_SECURITY_HEADERS`·`SecurityHeadersMiddleware`(현재 디스크 L126-151 — `X-Frame-Options: DENY`·CSP `frame-ancestors 'none'`·`nosniff`·`Referrer-Policy: no-referrer`, 이미 있는 헤더는 덮지 않음, HSTS 는 Ingress 담당이라는 주석, "D19-④")가 추가되고 `app.add_middleware(SecurityHeadersMiddleware)`(현재 L160)가 붙었다. 나머지 줄은 스냅샷과 동일(스냅샷 재구성 sha1 일치로 확인). 따라서 아래 L126-131 의 [위험:중간] 보안 헤더 지적은 **현재 디스크에서 헤더 부분이 수정 진행 중**이고, `TrustedHostMiddleware`(Host 헤더 검증) 부분은 여전히 없다 — 다음 차수에서 확인 필요.
- 2차 변경 요약 / Changes since 1st review: (1) `_health_loop` 에 `mcp_health` 리스 추가(L78·L81-84, Epic 45 D18-② — `test/impl/45th/scenario.md:9`). (2) `run_in_threadpool` import(L11). (3) 콘솔 번들의 서드파티 라이선스 사본 `/licenses` 마운트(L169-170, Epic 42 D17-③). (4) A2A 감시기 리스는 호출 대상 쪽(`app/services/a2a_gateway.py:883-886`)에 추가 — 이 파일의 호출부(L105-107)는 그대로.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/main.py lines=1-13
"""AgentNexus — FastAPI entrypoint. 구성요소(components)별 라우터를 선택 마운트한다 (FR-W01)."""
import asyncio
import contextlib
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app import db
```
**분석 / Analysis (Level 2)**
- L1: 이 파일이 FR-W01(구성요소 선택 기동)의 구현 위치임을 밝힌다.
- L2-5: `contextlib` 은 `suppress`·`asynccontextmanager`·`AsyncExitStack` 세 용도로 쓰인다(L54·L92·L97).
- L11 (2차 신규): `run_in_threadpool` — 동기 함수를 AnyIO 워커 스레드에서 실행하고 결과를 `await` 로 받는다. 이벤트 루프를 막지 않으려는 목적이지만 이 파일에서는 리스 호출(L83)에만 쓰고, 같은 루프 안의 다른 동기 DB 조회(L85-86)에는 쓰지 않는다(아래).
- L13: 모듈 자체를 import(`db.SessionLocal`) — 시험에서 `app.db` 속성을 바꿔치기해도 참조가 따라간다(함수를 직접 import 하면 바인딩이 고정됨).

```python path=app/main.py lines=14-33
from app.api import a2a as a2a_api
from app.api import admin as admin_api
from app.api import agents as agents_api
from app.api import audit as audit_api
from app.api import catalog as catalog_api
from app.api import chat as chat_api
from app.api import gateway_admin as gateway_admin_api
from app.api import identity as identity_api
from app.api import idp as idp_api
from app.api import knowledge as knowledge_api
from app.api import llm as llm_api
from app.api import setup as setup_api
from app.api import mcp as mcp_api
from app.api import observe as observe_api
from app.api import ops as ops_api
from app.api import system as system_api
from app.core import errors, logs, tracing
from app.core.errors import ApiError
from app.core.config import APP_VERSION, settings
from app.services import orchestrator
```
**분석 / Analysis (Level 2)**
- L14-29: 16개 라우터 모듈을 **구성요소 설정과 무관하게** 모두 import 한다. 등록(`include_router`)만 L133-160 에서 선택한다. 따라서 각 라우터 모듈의 import 시점 부수효과(모듈 전역 상태 생성, 데코레이터 등록 등)는 항상 실행된다.
- L30-33: 오류 처리기 설치(`errors`), JSON 로거(`logs`), OTel(`tracing`), 설정 싱글턴(`settings`), 오케스트레이터(이벤트 루프 주입 대상, L94).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L14-29 무조건 import(1차 INDEX §4.2 계층 위반 지적) — `COMPONENTS=gateway` 로 띄워도 `chat`·`knowledge`·`ops` 등 모든 모듈이 로드돼 기동 시간·메모리·import 부수효과가 따라온다. 최소 수정: 구성요소 블록(L134-160) 안으로 import 를 옮겨 지연 import. 순환 import 위험(`app/services/scheduler.py:46` 이 `app.api.observe` 를 import)은 L113·L121 의 지연 import 로 가려진 상태가 그대로다.

```python path=app/main.py lines=34-39

# 같은 이벤트 루프에서 lifespan 을 함께 돌릴 내장 ASGI 앱 (시험에서 데모 서버를 in-process 로 붙일 때)
EMBEDDED: list = []
# 기동 후 백그라운드 작업(async 함수) — 각 구성요소가 등록
BACKGROUND: list = []

```
**분석 / Analysis (Level 2)**
- L36 `EMBEDDED`: 같은 이벤트 루프에서 lifespan 을 함께 돌릴 내장 ASGI 앱. `test/conftest.py:63-64` 가 데모 MCP 서버 앱을 넣는다.
- L38 `BACKGROUND`: 외부에서 등록하는 백그라운드 코루틴 **함수** 목록(L115 에서 `fn()` 으로 호출해 Task 생성). 리스트에 코루틴 객체가 아니라 함수를 넣어야 한다는 점이 흔한 실수 지점이다(코루틴 객체는 한 번만 await 가능).
- 모듈 전역 가변 리스트 — 시험 간 공유 상태(세션 범위 fixture 라 현재 피해 없음).

```python path=app/main.py lines=41-57
async def _refresh_builtin_mcp() -> None:
    """참조 MCP 서버가 뜰 때까지 도구 조회를 재시도해 기본 규칙을 적용한다 (FR-P02 '전체 기동')."""
    from app.models import McpServer
    from app.services import mcp_gateway
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.mcp_refresh_on_start_s
    while loop.time() < deadline:
        with db.SessionLocal() as s:
            pending = [m.id for m in s.scalars(select(McpServer).where(McpServer.builtin.is_(True),
                                                                       McpServer.status == "active")) if not m.tools]
        if not pending:
            break
        for sid in pending:
            with contextlib.suppress(Exception):
                await mcp_gateway.refresh_tools(sid)
        await asyncio.sleep(3)
    await _refresh_builtin_a2a(deadline)
```
**분석 / Analysis (Level 2)**
- L41-44: 기동 직후 참조 MCP 서버(별도 컨테이너)가 아직 안 떴을 수 있어 도구 조회를 재시도. 함수 안 지연 import 로 모듈 로드 순서·순환 의존을 피한다.
- L45-47: `loop.time()` 은 단조 시계(monotonic) — 시스템 시각 변경에 영향받지 않는다. 마감 = 지금 + `MCP_REFRESH_ON_START_S`(기본 90초, `app/core/config.py:75`).
- L48-50: 세션을 `with` 로 짧게 열고 **id 만** 리스트로 꺼낸다 — 세션을 닫은 뒤 ORM 객체 속성을 읽다가 나는 detached 오류를 피하는 패턴.
- L53-55: 서버마다 `mcp_gateway.refresh_tools`(`app/services/mcp_gateway.py:693-740`). 호출 대상은 DB 접근을 `run_in_threadpool` 로 분리하고(L695-701·L725-737) `RpcError` 만 잡아 헬스 결과로 바꾼다(L721-722). 그 외 예외(DB 오류 등)는 여기 `suppress` 가 삼킨다.
- L56-57: 3초 간격 재시도, 같은 마감으로 A2A 카드 조회까지 이어간다.
- 이 작업은 리스가 없다 — 워커·파드마다 기동 시 1회씩 실행된다(최대 90초로 상한이 있어 영향은 작다).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L48-50(그리고 L65-66·L85-86) async 함수 안의 동기 DB I/O — 이벤트 루프 전체가 그동안 멈춘다. 2차에서 PostgreSQL 연결에 `connect_timeout=5`·`pool_timeout=10`(`app/db.py:49-51`)이 생겨, DB 장애조치 구간에서는 이 한 줄이 **최대 약 15초** 이벤트 루프를 막을 수 있다(그 워커의 모든 async 요청·SSE 정지). 최소 수정: `await run_in_threadpool(_pending_ids)` 로 감싼다(호출 대상 `refresh_tools` 가 이미 그렇게 한다).
- [위험:낮음] L54(그리고 L70·L82·L88) `contextlib.suppress(Exception)` — 조용한 예외 삼킴(1차 §4.1 패턴 6). 원인 로그가 전혀 남지 않는다. 최소 수정: `try/except Exception as exc: log.warning("...: %s", type(exc).__name__)`. 참고로 `suppress(Exception)` 은 `asyncio.CancelledError`(3.8+ 부터 `BaseException`)를 잡지 않으므로 종료 시 취소는 정상 전파된다.

```python path=app/main.py lines=60-72
async def _refresh_builtin_a2a(deadline: float) -> None:
    from app.models import A2AAgent
    from app.services import a2a_gateway
    loop = asyncio.get_running_loop()
    while loop.time() < deadline:
        with db.SessionLocal() as s:
            pending = [a.id for a in s.scalars(select(A2AAgent).where(A2AAgent.status == "active")) if not a.card]
        if not pending:
            return
        for aid in pending:
            with contextlib.suppress(Exception):
                await a2a_gateway.refresh_card(aid)
        await asyncio.sleep(3)
```
**분석 / Analysis (Level 2)**
- L60: 위 루프와 같은 `deadline` 공유 — 두 단계 합계가 `MCP_REFRESH_ON_START_S` 를 넘지 않는다.
- L66: `builtin` 조건이 없어 사용자가 등록한 원격 에이전트 중 카드가 빈 것도 대상이다(기동 시 외부 호출 발생 — `refresh_card` 의 egress 검사는 호출 대상 책임, `app/services/a2a_gateway.py:152`).
- L70-71: 예외 삼킴은 L54 와 같은 패턴(위 Watch-out 에 포함).

```python path=app/main.py lines=75-89
async def _health_loop() -> None:
    from app.models import McpServer
    from app.services import mcp_gateway
    from app.services import scheduler
    while True:
        await asyncio.sleep(settings.mcp_health_interval_s)
        # 리스를 가진 워커만 — 워커 수만큼 업스트림 점검이 늘지 않게 (D18-②). 보유 워커가 죽으면 2 주기 뒤 다른 워커가 잇는다
        with contextlib.suppress(Exception):
            if not await run_in_threadpool(scheduler.lease, "mcp_health", settings.mcp_health_interval_s * 2):
                continue
        with db.SessionLocal() as s:
            ids = [m.id for m in s.scalars(select(McpServer).where(McpServer.status == "active"))]
        for sid in ids:
            with contextlib.suppress(Exception):
                await mcp_gateway.refresh_tools(sid)
```
**분석 / Analysis (Level 2)**
- L79-80: 무한 루프 — `MCP_HEALTH_INTERVAL_S`(기본 60초)마다 활성 서버 전체 `refresh_tools`(도구 목록 갱신 + 헬스 판정을 한 호출로).
- L81-84 (2차 신규): `scheduler.lease("mcp_health", ttl=2×주기)`(`app/services/scheduler.py:24-43`) — `settings` 테이블의 `lease.mcp_health` 행을 "보유자가 나이거나 TTL 만료" 조건부 UPDATE 로 잡는다(원자적, SQLite·PG 공통). 리스를 못 잡으면 `continue` 로 이번 주기를 건너뛴다. 보유 워커는 매 주기 갱신하므로 계속 보유하고, 죽으면 TTL(2주기) 뒤 다른 워커가 잡는다.
- 파이썬 문법 메모: `with` 블록 안의 `continue` 는 컨텍스트 관리자의 `__exit__` 를 정상 호출한 뒤 바깥 `while` 의 다음 반복으로 간다.
- L83: 리스 함수는 동기 DB UPDATE 라 `run_in_threadpool` 로 감쌌다(좋음). 그러나 L85-86 의 조회는 감싸지 않았다(일관성 없음).
**짚어볼 점 / Watch-outs**
- [위험:중간] L82-86 **DB 오류 한 번에 헬스 루프가 영구 종료 + 리스 예외 시 fail-open**. (a) L82 `suppress(Exception)` 이 L83 리스 호출 예외(DB 장애조치 중 `OperationalError` 등)를 삼키면 `continue` 가 실행되지 않고 L85 로 그대로 내려간다 — 리스 없이 점검을 시도하는 fail-open. (b) L85-86 조회는 아무 예외 처리도 없어 같은 DB 오류가 `_health_loop` 밖으로 전파되고 Task 가 끝난다. 이 Task 는 `tasks` 리스트(L104)에만 있고 누구도 `await` 하지 않으므로(L119-120 은 cancel 만) 예외는 "Task exception was never retrieved" 로 GC 시점에야 찍히고, **그 워커의 헬스 루프는 프로세스 재시작까지 멈춘다**. 모든 워커가 같은 DB 를 보므로 장애조치(Epic 46 — 새 연결이 `connect_timeout` 5초로 실패하는 구간, `test/impl/46th/result.md:7`) 한 번에 모든 워커의 루프가 동시에 죽을 수 있다. 결과: `McpServer.health` 가 마지막 값(예: "healthy")에 고정되고 도구 목록 갱신이 끊긴다 — 리스 행도 갱신이 멈추지만 다른 워커도 루프가 없으니 아무도 잇지 않는다. 대조: `scheduler.loop`(`app/services/scheduler.py:136-141`)와 `a2a_gateway.watcher`(`app/services/a2a_gateway.py:881-886`)는 주기마다 예외를 잡아 루프를 유지한다. 최소 수정:
  ```python
  while True:
      await asyncio.sleep(settings.mcp_health_interval_s)
      try:
          if not await run_in_threadpool(scheduler.lease, "mcp_health", settings.mcp_health_interval_s * 2):
              continue
          ids = await run_in_threadpool(_active_server_ids)
          for sid in ids:
              await mcp_gateway.refresh_tools(sid)   # 서버별 try/except 로 계속 진행
      except Exception as exc:  # noqa: BLE001 — 다음 주기에 재시도
          log.warning("mcp health tick failed: %s", type(exc).__name__)
  ```
  시험 공백: `test/conftest.py:25` 가 `MCP_HEALTH_INTERVAL_S=0` 으로 이 루프를 아예 끄고, `test/test_multiworker.py:77-98` 는 watcher 만 다룬다 — `_health_loop` 에 대해 "lease 가 예외를 던지면 점검하지 않음", "조회가 1회 실패해도 다음 주기 계속" 시험이 필요하다.
- [위험:낮음] L87-89 서버를 **순차** 점검 — 한 서버가 타임아웃(`UPSTREAM_TIMEOUT` 기본 30초, `app/core/config.py:46`)이면 나머지가 밀린다. 또 한 주기 = `sleep(주기)` + Σ(서버별 소요)이므로 느린 서버가 많으면 리스 TTL(2×주기, 기본 120초)보다 주기가 길어져 다른 워커가 리스를 가져가고 두 워커가 동시에 점검하게 된다(정합성 문제는 아니고 업스트림 부하 배가). 최소 수정: `asyncio.gather` + 서버별 `asyncio.wait_for`, 또는 TTL 을 실제 소요 기반으로.

```python path=app/main.py lines=92-123
@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    orchestrator.LOOP = asyncio.get_running_loop()  # 승인 훅(스레드)에서 실행 재개를 이 루프로 예약
    with contextlib.suppress(Exception):  # 재시작 횟수(SCR-B05) — 워커마다 1건
        logs.event("info", "system", f"프로세스 시작 (pid {os.getpid()})", sync=True, version=APP_VERSION)
    async with contextlib.AsyncExitStack() as stack:
        for sub in EMBEDDED:
            await stack.enter_async_context(sub.router.lifespan_context(sub))
        tasks = []
        if settings.enabled("gateway") and settings.mcp_refresh_on_start_s > 0:
            tasks.append(asyncio.create_task(_refresh_builtin_mcp()))
        if settings.enabled("gateway") and settings.mcp_health_interval_s > 0:
            tasks.append(asyncio.create_task(_health_loop()))
        if settings.enabled("gateway") and settings.a2a_watch_interval_s > 0:
            from app.services import a2a_gateway
            tasks.append(asyncio.create_task(a2a_gateway.watcher(settings.a2a_watch_interval_s)))
        if settings.enabled("admin") and settings.enabled("identity"):
            from app.services import setup as setup_svc
            with contextlib.suppress(Exception), db.SessionLocal() as s0:  # 첫 실행 미완료면 일회용 설정 코드를 로그에
                setup_svc.ensure_code(s0)
        if settings.enabled("host") or settings.enabled("observe"):
            from app.services import scheduler
            tasks.append(asyncio.create_task(scheduler.loop()))  # 경보·보존·실행 복구 (리스로 워커 1개만)
        tasks += [asyncio.create_task(fn()) for fn in BACKGROUND]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            from app.services import scheduler, slo
            with contextlib.suppress(Exception):  # 종료 전 남은 SLI 카운터 반영 (DB 가 이미 내려갔으면 버림)
                slo.flush(scheduler.WORKER)
```
**분석 / Analysis (Level 2)**
- L92-93: FastAPI lifespan 계약 — `yield` 이전이 기동, `finally` 가 종료. 워커 프로세스마다 1회 실행된다(uvicorn `--workers N` 이면 N 번).
- L94: 현재 이벤트 루프를 오케스트레이터 전역(`app/services/orchestrator.py:33`)에 저장 — 동기 승인 엔드포인트(스레드풀)에서 `run_coroutine_threadsafe` 류로 실행 재개를 이 루프에 예약하기 위함.
- L95-96: 워커 기동 이벤트를 동기(`sync=True`) 기록(SCR-B05 재시작 횟수). 실패는 삼킨다(기동 차단 방지 의도).
- L97-99: `AsyncExitStack` 으로 내장 앱 lifespan 을 함께 진입 — `yield` 이후 `async with` 를 빠져나갈 때 역순으로 정리된다(즉 L118-123 `finally` 가 먼저, 내장 앱 종료가 나중).
- L100-107: 구성요소·설정값에 따라 Task 생성. Task 참조를 리스트에 보관해 GC 조기 소멸을 막는다(`asyncio.create_task` 결과를 버리면 실행 중에도 수거될 수 있음 — 흔한 실수).
- L108-111: 첫 실행 미완료면 일회용 설정 코드를 생성해 로그로(`app/services/setup.py:87-97`).
- L112-114: 스케줄러 루프 — 경보 평가·SLO·실행 복구·백업·승인 만료·레지스트리 작업·보존 파기를 작업별 리스로 1개 워커만 수행(`app/services/scheduler.py:99-131`).
- L118-123: 종료 시 Task 취소 후 이 워커의 SLI 카운터를 마지막으로 DB 반영(`app/services/slo.py:60`).
**짚어볼 점 / Watch-outs**
- [위험:중간] L112 스케줄러가 `host` 또는 `observe` 구성요소일 때만 기동된다. 그런데 배포 프로파일 `gateway-only` = (`gateway`,`identity`), `registry-only` = (`registry`,`identity`)(`app/services/deploy.py:21-27`, CLI `app/cli/nexus.py:153`)이다. 이 프로파일로 **단독** 배포하면 같은 DB 를 쓰는 다른 프로세스가 없는 한 `scheduler.tick`(`app/services/scheduler.py:99-131`)의 **보존 파기(spans·logs·usage·run_events·McpRelay·McpTask, `app/services/retention.py:24-34`)·도구 승인 만료(`approval_jobs`, `scheduler.py:80-96`)·레지스트리 심사 만료/동기화**가 영영 돌지 않는다. 게이트웨이는 계속 UsageRecord·Span·LogEntry·McpTask 를 쓰므로 테이블이 무한히 커지고, 보존 기간(PII 보관 정책) 준수도 깨진다. 확신: 코드 경로는 확인됨, 실제 영향은 배포 형태(같은 DB 에 host/observe 프로세스가 함께 있는지)에 의존. 최소 수정: 조건을 `gateway or host or observe or registry or admin`(사실상 항상)으로 넓히거나, 작업별로 필요한 구성요소를 `tick` 안에서 판정. 시험 공백: 구성요소별 스케줄러 기동 시험 없음.
- [위험:낮음] L119-120 `t.cancel()` 만 하고 `await asyncio.gather(*tasks, return_exceptions=True)` 로 기다리지 않는다(1차 지적 그대로). 취소는 다음 루프 틱에 전달되므로 L122-123 `slo.flush` 가 아직 실행 중인 작업과 겹칠 수 있고, 이미 예외로 끝난 Task(위 L82-86 시나리오)의 예외는 끝내 회수되지 않는다. 최소 수정: cancel 후 `await asyncio.gather(*tasks, return_exceptions=True)` 하고 `CancelledError` 외 예외는 로그.
- [위험:낮음] L108-111 `ensure_code` 실패를 조용히 삼켜 운영자가 설정 코드를 못 받은 이유를 알 수 없다(1차 지적 그대로). 또 이 블록은 리스 없이 **워커마다** 실행된다 — 기존 코드가 만료된 상태에서 2 워커가 동시에 기동하면 둘 다 `new_code`(`app/services/setup.py:78-84`)로 행을 덮어써 로그에 서로 다른 코드 2개가 찍히고 먼저 쓴 쪽은 무효가 된다(가능성 — `_put` 의 upsert 방식은 추가 확인 필요). 최소 수정: `scheduler.lease("setup_code", 60)` 보유 워커만 실행, 실패는 `log.warning`.
- L95-96·L122-123 의 `suppress` 는 의도(로그·플러시 실패가 기동·종료를 막지 않게)는 타당하나 stderr 경고 1줄은 남기는 편이 운영에 낫다.

```python path=app/main.py lines=126-131
logs.setup()
tracing.setup("agentnexus")
app = FastAPI(title="AgentNexus", version=APP_VERSION, lifespan=lifespan,
              description="MCP + AI Gateway + A2A 통합 에이전트 플랫폼 — 관리 API · Unified Model API · MCP/A2A 프록시")
errors.install(app)
app.add_middleware(tracing.TracingMiddleware, component="api")
```
**분석 / Analysis (Level 2)**
- L126-127: import 시점 전역 설정. `tracing.setup` 은 `_configured` 플래그로 1회만(`app/core/tracing.py:115-123`).
- L128-129: `lifespan=` 으로 위 함수 연결, `version` 은 OpenAPI 문서에 노출.
- L130: `errors.install` — `ApiError`·`StarletteHTTPException`·`RequestValidationError` 3종 처리기(`app/core/errors.py:55-75`).
- L131: 미들웨어는 이 하나뿐이다(`add_middleware` 는 나중에 추가한 것이 가장 바깥).
**짚어볼 점 / Watch-outs**
- [위험:중간] (스냅샷 기준 — 헤더 부분은 리뷰 도중 디스크에서 수정 진행, 파일 요약의 "스냅샷 이후 변경 고지" 참조) 보안 응답 헤더·호스트 검증 미들웨어가 전혀 없다. 스냅샷 시점 저장소 전체 검색(`app/`·`deploy/`)에서 `X-Frame-Options`·`Content-Security-Policy`·`Strict-Transport-Security`·`X-Content-Type-Options`·`TrustedHostMiddleware` 가 0건이고, Helm Ingress 어노테이션도 기본 비어 있다(`deploy/helm/agentnexus/values.yaml:129-132`). 시나리오: 공격자 페이지가 `<iframe src="https://<agentnexus>/idp/authorize?...">` 로 내장 IdP 로그인 폼(`app/api/idp.py:70-94`, `:166`)을 삽입해 클릭재킹. 또 Host 헤더를 검증하지 않아 플랫폼 Agent Card 의 `url` 이 요청 Host 로 만들어진다(`app/api/a2a.py:236-238`·`:251` `base_url=str(request.base_url)`) — Host 를 키로 쓰지 않는 캐시가 앞에 있으면 카드 오염 가능(가능성). 2차 위임 보안 리뷰 SEC2-05(`.review/round2/delegated/security-review.md:146-176`)와 같은 결론을 코드로 재확인. 최소 수정: `@app.middleware("http")` 로 `X-Frame-Options: DENY`(또는 CSP `frame-ancestors 'none'`)·`nosniff`·`Referrer-Policy`·https 일 때 HSTS, 그리고 `TrustedHostMiddleware(allowed_hosts=[urlparse(settings.public_base_url).hostname, ...])`.
- [위험:낮음] 일반 `Exception` 처리기가 없다(1차 지적 그대로, `app/core/errors.py:55-75`) — 처리되지 않은 예외는 Starlette 기본 500(text/plain)으로 나가 problem+json·OpenAI·JSON-RPC 응답 계약이 깨진다. 예: `app/api/observe.py:80` 의 `KeyError`(OTLP 본문 결함). 최소 수정: `@app.exception_handler(Exception)` 로 problem+json 500 + `trace_id` 포함.
- CORS 미들웨어가 없는 것은 콘솔이 같은 출처(SPA 를 이 앱이 서빙, L166-191)라 안전한 기본값이다(교차 출처 브라우저는 응답을 읽지 못함).

```python path=app/main.py lines=133-164
app.include_router(system_api.router)
if settings.enabled("identity"):
    if settings.builtin_idp:
        app.include_router(idp_api.router)
    app.include_router(identity_api.router)
    app.include_router(ops_api.identity_router)
if settings.enabled("gateway"):
    app.include_router(gateway_admin_api.router)
    app.include_router(ops_api.gateway_router)
    app.include_router(llm_api.router)
    app.include_router(mcp_api.router)
    app.include_router(a2a_api.router)
if settings.enabled("host"):
    gateway_admin_api.APPROVAL_HOOKS.append(orchestrator.on_approval)
    a2a_api.PLATFORM["executor"] = orchestrator.a2a_executor
    app.include_router(chat_api.router)
    app.include_router(agents_api.router)
    app.include_router(knowledge_api.router)
if settings.enabled("registry"):
    app.include_router(catalog_api.router)
if settings.enabled("observe"):
    app.include_router(audit_api.router)
    app.include_router(observe_api.router)
    app.include_router(ops_api.observe_router)
if settings.enabled("admin"):
    app.include_router(admin_api.router)
    app.include_router(ops_api.admin_router)
    app.include_router(setup_api.router)

if settings.enabled("identity") and settings.builtin_idp and settings.seed_demo and settings.app_env in ("local", "test"):
    from app.builtin import demo_idp  # 데모 외부 IdP — SSO 브로커 시연·시험용, prod 에는 붙이지 않는다
    app.include_router(demo_idp.router)
```
**분석 / Analysis (Level 2)**
- L133: `system` 라우터는 항상. L134-138 identity — 내장 IdP(`/idp/*`)는 `BUILTIN_IDP` 일 때만. L139-144 gateway — 관리·OpenAI 호환 모델 API·MCP·A2A 프록시. L145-150 host — 승인 훅(`app/api/gateway_admin.py:794`)·플랫폼 A2A 실행기(`app/api/a2a.py:72`) 주입(전역 레지스트리를 통한 간단한 의존성 역전). L151-160 registry·observe·admin.
- L162-164: 데모 외부 IdP 는 `local`/`test` + `seed_demo` 일 때만.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L146 모듈 import 마다 `append` — 같은 프로세스에서 `app.main` 을 재로드(`importlib.reload`)하면 훅이 중복 등록되어 승인 1건에 실행 재개가 2번 예약된다(1차 지적 그대로). 최소 수정: `if orchestrator.on_approval not in APPROVAL_HOOKS:`.
- L162 의 `app_env` 기본값 `"local"`(`app/core/config.py:17`)은 fail-open — `APP_ENV` 누락 시 데모 IdP 가 붙는다. 근본 원인은 설정 기본값이므로 등급은 `config.py` 보고서에서 매긴다(2차에서 같은 판정이 6곳 이상으로 늘었음).

```python path=app/main.py lines=166-191
if settings.enabled("console"):
    # 웹 콘솔 SPA (React + Vite, web/dist) — 해시 자산은 /_app, 화면 경로는 index.html 폴백(클라이언트 라우팅)
    app.mount("/_app", StaticFiles(directory=Path(settings.console_dist) / "_app", check_dir=False), name="console-assets")
    # 번들에 포함한 서드파티 라이선스 사본(폰트 OFL 등) — 배포물과 함께 고지 (D17-③)
    app.mount("/licenses", StaticFiles(directory=Path(settings.console_dist) / "licenses", check_dir=False), name="console-licenses")

    def _console(path: str = ""):
        index = Path(settings.console_dist) / "index.html"
        if not index.exists():
            raise ApiError(503, "console_not_built", "콘솔 번들이 없습니다 — make web-build (prod 이미지는 빌드 시 포함)")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    for _prefix in ("/console", "/chat", "/login", "/setup"):
        app.add_api_route(_prefix, _console, methods=["GET"], include_in_schema=False)
        app.add_api_route(f"{_prefix}/{{path:path}}", _console, methods=["GET"], include_in_schema=False)

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        icon = Path(settings.console_dist) / "favicon.svg"
        if not icon.exists():
            raise ApiError(404, "not_found", "favicon")
        return FileResponse(icon, media_type="image/svg+xml")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/console")
```
**분석 / Analysis (Level 2)**
- L168: 해시 자산 `/_app` 마운트, `check_dir=False` 로 빌드 전에도 기동 가능(요청 시 404).
- L170 (2차 신규): `/licenses` — 번들된 서드파티 라이선스 사본(예: Pretendard OFL, `test/test_supply_chain.py:24` 가 원본 파일 존재 확인) 공개 서빙. 인증 없음은 의도(고지 의무). 경로 정규화·`..` 차단은 Starlette `StaticFiles` 가 담당.
- L172-176: SPA 폴백 — `path` 인자를 쓰지 않고 항상 같은 index.html 을 반환하므로 경로 조작 위험 없음. `Cache-Control: no-cache` 로 배포 직후 새 번들을 받게 한다.
- L178-180: 클라이언트 라우팅 경로 4개와 하위 경로(`{path:path}` 는 슬래시 포함 나머지 경로를 받는 Starlette 변환기).
- `/setup` 은 SPA 만 인증 없이 내려주고 실제 보호는 설정 API 의 일회용 코드 검증(`app/api/setup.py`)에 있다.

```python path=app/main.py lines=194-203
@app.get("/health", tags=["system"])
def health():
    try:
        db.ping()
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001 — 헬스 응답에 원인 노출(시크릿 제외)
        db_status = f"error: {type(exc).__name__}"
    body = {"status": "ok" if db_status == "ok" else "degraded",
            "env": settings.app_env, "db": {"engine": settings.db_engine, "status": db_status}}
    return JSONResponse(body, status_code=200 if db_status == "ok" else 503)
```
**분석 / Analysis (Level 2)**
- L194-203: DB `SELECT 1`(`app/db.py:59-62`) 성공 200, 실패 503 `degraded`. 예외 **타입 이름만** 노출해 연결 문자열 유출을 피한다.
- 동기 `def` 라 스레드풀에서 실행(이벤트 루프 비차단). Helm 은 이 경로를 readiness 에만 쓰고 liveness 는 TCP 로 분리했다(`deploy/helm/agentnexus/templates/api.yaml:52-61` — DB 장애 때 파드를 재시작시키지 않으려는 올바른 설계).
- 2차: PostgreSQL `connect_timeout=5`(`app/db.py:49`)로 DB 무응답 시 지연에 상한이 생겼다. 다만 풀 고갈 시 `pool_timeout=10`(`app/db.py:51`)까지 더해질 수 있어 probe 타임아웃 3초(`api.yaml:55`, `docker-compose.yml:22`)보다 길다 — probe 는 타임아웃으로 실패(원하는 결과)하지만 응답 대기 스레드는 그동안 점유된다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L202 `db.engine` 표시가 실제 엔진이 아니라 설정 문자열 `settings.db_engine`(기본 `"sqlite"`, `app/core/config.py:18`)이다. 이 값은 표시 외 어디에도 쓰이지 않아(`app/api/system.py:19` 포함) `DATABASE_URL` 만 PostgreSQL 로 바꾸고 `DB_ENGINE` 을 빠뜨리면 헬스가 "sqlite" 라고 거짓 보고한다. 또 인증 없는 엔드포인트가 `env` 를 노출(1차 지적 그대로, 정보 노출 수준 낮음). 최소 수정: `db.engine.dialect.name` 으로 표시.

## 파일 평가 / File Assessment
- 강점: (1) 구성요소 플래그로 라우터·백그라운드 작업을 한 곳에서 선택 — FR-W01 이 읽기 쉽게 드러난다. (2) 2차에서 MCP 헬스 루프·A2A 감시기에 원자적 리스(조건부 UPDATE)가 붙어 다중 워커 중복 점검이 해소됐다. (3) 헬스 응답이 시크릿을 노출하지 않고, Helm 이 readiness/liveness 를 올바르게 분리했다.
- 위험: (1) [위험:중간] `_health_loop` 가 DB 오류 1회에 영구 종료, 리스 예외 시 fail-open — `app/main.py:82-86`. (2) [위험:중간] `gateway-only`·`registry-only` 단독 배포에서 스케줄러(보존·승인 만료) 미기동 — `app/main.py:112`. (3) [위험:중간] 보안 헤더·TrustedHost 부재 — `app/main.py:126-131`.
- 개선 권고(영향순): (1) `_health_loop` 본문을 주기 단위 try/except 로 감싸고 리스 예외는 건너뛰기, DB 조회는 `run_in_threadpool`. (2) 스케줄러 기동 조건을 모든 구성요소로 넓히기. (3) 보안 헤더 미들웨어 + `TrustedHostMiddleware`. (4) 종료 시 `gather(..., return_exceptions=True)`, `suppress` 를 로그 남기는 except 로.
- 파일 위험 등급 / Risk grade: 중간 — 미해결 [위험:중간] 3건(높음 없음), 조립 지점이라 보안 경계 핵심 모듈 기준은 적용하지 않음.
- 재읽기 권고 레벨: 2 — 백그라운드 루프의 예외·취소 경로는 줄 단위로 다시 볼 가치가 있다(구조 자체는 단순).

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| L13-32 순환 import 위험(scheduler→api.observe), 지연 import 로 가려짐 | 미해결 | `app/services/scheduler.py:46` 여전히 `from app.api.observe import evaluate`; 이 파일 L113·L121 지연 import 유지 |
| L35-37 `EMBEDDED`·`BACKGROUND` 전역 가변 리스트 | 미해결(실피해 없음) | L36·L38 |
| [낮음] L47-49 async 안 동기 DB I/O | 미해결(영향 증가) | L48-50·L65-66·L85-86 — 2차 `connect_timeout=5`·`pool_timeout=10`(`app/db.py:49-51`)으로 최악 차단 시간 증가 |
| [낮음] L53·L69·L82 조용한 예외 삼킴 | 미해결 | L54·L70·L82·L88 |
| [중간] 헬스 루프 리스 없음 → 워커 수만큼 중복 | 해결(새 결함 동반) | L81-84 `scheduler.lease("mcp_health", 2×주기)` — 단 예외 경로 결함은 본 보고서 L82-86 [위험:중간] |
| [중간] `a2a_gateway.watcher` 리스 없음 | 해결 | `app/services/a2a_gateway.py:883-886` (시험 `test/test_multiworker.py:77-98`) |
| [낮음] 서버 순차 점검 | 미해결 | L87-89 |
| [낮음] `t.cancel()` 후 await 없음 | 미해결 | L119-120 |
| [낮음] `ensure_code` 실패 삼킴 | 미해결 | L110 |
| 일반 `Exception` 처리기 부재 | 미해결 | `app/core/errors.py:55-75` (3종만) |
| [낮음] `APPROVAL_HOOKS.append` 재로드 시 중복 | 미해결 | L146 |
| `app_env` 기본 local → 데모 IdP fail-open | 미해결 | L162 + `app/core/config.py:17` |
| 헬스 지연이 probe 3초와 엇갈림 | 부분 해결 | `app/db.py:49` connect_timeout 5초로 상한 생김, `app/db.py:51` pool_timeout 10초 추가로 3초 초과는 여전 |
| 인증 없는 `/health` 의 env 노출 | 미해결 | L202 |
| (1차 INDEX §4.2) 모든 라우터 무조건 import | 미해결 | L14-29 |
