> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../INDEX.md) · 디렉터리 개요: [app_Abstract.md](app_Abstract.md)

### 01. app/main.py
**파일 요약 / File Summary**
- 역할 / Role: FastAPI ASGI 앱 조립 지점. 구성요소(`COMPONENTS`)별 라우터 선택 마운트, lifespan 에서 백그라운드 작업(참조 MCP 도구 조회 재시도·헬스 루프·A2A 감시기·스케줄러) 기동, 콘솔 SPA 정적 서빙, `/health`.
- 진입점 거리 / Distance: 0 (`uvicorn app.main:app` — Dockerfile `CMD`, docker-compose `api`)
- LOC: 195
- 주요 export: `app`(FastAPI), `lifespan`, `EMBEDDED`, `BACKGROUND`, `health`
- 의존(내부): `app.db`, `app.api.*`(16개 라우터), `app.core.{errors,logs,tracing,config}`, `app.services.{orchestrator,mcp_gateway,a2a_gateway,scheduler,slo,setup}`, `app.models`, `app.builtin.demo_idp`
- 외부 패키지: fastapi, starlette(StaticFiles·FileResponse), sqlalchemy
- 부수효과 / Side effects: 모듈 import 시점에 `logs.setup()`·`tracing.setup()`(전역 TracerProvider 설정), 라우터 등록, `gateway_admin_api.APPROVAL_HOOKS`·`a2a_api.PLATFORM` 전역 변이, 기동 시 DB 쓰기(프로세스 시작 이벤트·설정 코드)
- 동시성 / Concurrency: asyncio — lifespan 에서 `asyncio.create_task` 로 장기 루프 4~5개, 각 루프 안에서 동기 DB 세션 사용
- 테스트 커버리지 흔적: `test/conftest.py`(TestClient 로 lifespan 구동), `test/test_health.py`, `test/test_admin.py`(구성요소 선택)

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/main.py lines=1-12
"""AgentNexus — FastAPI entrypoint. 구성요소(components)별 라우터를 선택 마운트한다 (FR-W01)."""
import asyncio
import contextlib
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app import db
```
**분석 / Analysis (Level 2)**
- L1: 모듈 docstring — 이 파일이 FR-W01(구성요소 선택 기동)의 구현 위치임을 명시. 운영자가 `COMPONENTS=gateway,identity` 처럼 일부만 켤 수 있는 설계의 출발점이다.
- L2-5: 표준 라이브러리. `contextlib` 은 `suppress`·`asynccontextmanager`·`AsyncExitStack` 3가지 용도로 쓰인다(아래 L53·L86·L91).
- L7-10: FastAPI 본체·응답 클래스·정적 파일 마운트, SQLAlchemy 2.x 스타일 `select()` 를 가져온다. `select` 는 L48·L65·L80 에서 ORM 엔티티 조회에 쓰인다.
- L12: `from app import db` — 모듈 자체를 가져와 `db.SessionLocal`·`db.ping` 으로 접근. 시험에서 `app.db.engine` 을 바꿔치기해도 참조가 유지되는 장점이 있다(함수를 직접 import 하면 바인딩이 고정됨).

```python path=app/main.py lines=13-32
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
- L13-28: API 라우터 16개를 모두 import 한다. 구성요소가 꺼져 있어도 import 는 일어나므로(라우터 등록만 생략), 각 라우터 모듈의 import 시점 부수효과(예: `app/api/a2a.py:705` 의 `@registry.register_handler`)는 항상 실행된다.
- L29-31: `errors`(예외 핸들러 설치), `logs`(JSON 로거), `tracing`(OTel), `ApiError`(콘솔 미빌드 503), `settings`(환경 설정 싱글턴).
- L32: `orchestrator` 는 lifespan 에서 이벤트 루프 참조를 주입받는다(L88).
**짚어볼 점 / Watch-outs**
- 순환 import 위험: `app.services.scheduler` 가 `app.api.observe`·`app.api.ops` 를 import 한다(`app/services/scheduler.py:46,116`). 여기서는 지연 import(L107·L115)라 문제가 드러나지 않지만, 최상위 import 로 바꾸면 순환이 생긴다.

```python path=app/main.py lines=33-38

# 같은 이벤트 루프에서 lifespan 을 함께 돌릴 내장 ASGI 앱 (시험에서 데모 서버를 in-process 로 붙일 때)
EMBEDDED: list = []
# 기동 후 백그라운드 작업(async 함수) — 각 구성요소가 등록
BACKGROUND: list = []

```
**분석 / Analysis (Level 2)**
- L35 `EMBEDDED`: 같은 이벤트 루프에서 lifespan 을 함께 돌릴 내장 ASGI 앱 목록. `test/conftest.py:64` 가 `mcp_servers.app` 을 넣어 in-process 로 데모 MCP 서버를 붙인다.
- L37 `BACKGROUND`: 다른 구성요소가 등록하는 백그라운드 코루틴 함수 목록(L109 에서 실행).
**짚어볼 점 / Watch-outs**
- 모듈 전역 가변 리스트 — 시험 간 공유 상태가 된다. 시험이 append 만 하고 정리하지 않으면 다음 시험 세션에서 누적될 수 있다(현재 세션 단위 fixture 라 실제 피해는 없음).

```python path=app/main.py lines=40-56
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
- L40-41: 기동 직후 참조 MCP 서버(별도 컨테이너)가 아직 안 떠 있을 수 있으므로 도구 목록 조회를 재시도한다(FR-P02 '전체 기동').
- L42-43: 함수 안 지연 import — 모듈 로드 순서·순환 의존을 피하려는 의도.
- L44-45: `loop.time()` 은 단조 시계(monotonic) 기반이라 시스템 시각 변경에 영향받지 않는다. 마감 = 지금 + `MCP_REFRESH_ON_START_S`(기본 90초, `app/core/config.py:73`).
- L47-49: 동기 세션을 `with` 로 열고 닫는다(자원 누수 없음). `builtin=True`·`active` 서버 중 `tools` 가 비어 있는 것만 대상. 결과를 id 리스트로 만들어 세션 밖으로 꺼내는 패턴은 detached 객체 접근 오류를 피한다.
- L52-54: 서버마다 `mcp_gateway.refresh_tools` 호출(`app/services/mcp_gateway.py:638-687`), 예외는 무시.
- L55: 3초 간격 재시도. L56: 같은 마감 시각을 넘겨 A2A 카드 조회도 이어서 수행.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L47-49 는 async 함수 안의 동기 DB I/O — 이벤트 루프를 잠깐 막는다. 기동 시점 1회성이라 영향은 작지만, 같은 패턴이 L64·L79 의 상시 루프에도 있다.
- [위험:낮음] L53 `contextlib.suppress(Exception)` 은 조용한 예외 삼킴. `refresh_tools` 는 RpcError 를 결과로 바꿔 주지만 그 밖의 예외(DB 오류 등)는 로그 없이 사라진다. 최소 `logs.event("warning", ...)` 권장.

```python path=app/main.py lines=59-71
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
- L59: 위 루프와 같은 마감(`deadline`)을 공유 — 두 단계 합계가 90초를 넘지 않는다.
- L65: `status == "active"` 이고 카드(`card`)가 빈 에이전트만 대상. `builtin` 조건이 없으므로 사용자가 등록한 원격 에이전트도 포함된다.
- L69-70: `a2a_gateway.refresh_card` 는 카드 조회·검증·해시 저장(`app/services/a2a_gateway.py:150-216`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L69 도 조용한 예외 삼킴(L53 과 동일).

```python path=app/main.py lines=74-83
async def _health_loop() -> None:
    from app.models import McpServer
    from app.services import mcp_gateway
    while True:
        await asyncio.sleep(settings.mcp_health_interval_s)
        with db.SessionLocal() as s:
            ids = [m.id for m in s.scalars(select(McpServer).where(McpServer.status == "active"))]
        for sid in ids:
            with contextlib.suppress(Exception):
                await mcp_gateway.refresh_tools(sid)
```
**분석 / Analysis (Level 2)**
- L77-83: 무한 루프 헬스 점검 — `MCP_HEALTH_INTERVAL_S`(기본 60초)마다 활성 서버 전부에 `refresh_tools`. 도구 목록 갱신과 헬스 판정을 같은 호출로 한다.
**짚어볼 점 / Watch-outs**
- [위험:중간] 다중 워커(`UVICORN_WORKERS=2`, Dockerfile runtime)·다중 파드에서 모든 워커가 이 루프를 독립 실행한다. 스케줄러는 리스(`app/services/scheduler.py:24-42`)로 1개만 실행하지만 이 루프와 `a2a_gateway.watcher` 는 리스가 없어 업스트림 호출이 워커 수만큼 배가된다.
- [위험:낮음] 서버를 순차 호출하므로 서버 하나가 타임아웃(`UPSTREAM_TIMEOUT` 30초)이면 다음 서버 점검이 밀린다. `asyncio.gather` + 개별 타임아웃 고려.
- L82 조용한 예외 삼킴.

```python path=app/main.py lines=86-117
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
- L86-87: FastAPI lifespan 계약 — `yield` 이전은 기동, 이후(finally)는 종료 처리. `@asynccontextmanager` 로 만든다.
- L88: 오케스트레이터에 현재 이벤트 루프를 전역으로 저장. 승인 API(동기 엔드포인트 → 스레드풀)에서 실행 재개를 이 루프에 예약하기 위함(`run_coroutine_threadsafe` 류).
- L89-90: 워커 기동 이벤트를 동기(`sync=True`)로 DB 에 남겨 재시작 횟수 집계(SCR-B05).
- L91-93: `AsyncExitStack` 으로 내장 앱들의 lifespan 을 함께 진입 — 여러 컨텍스트를 한 번에 정리하는 표준 패턴.
- L95-101: 구성요소·설정값에 따라 백그라운드 작업 생성. 반환된 Task 객체를 `tasks` 리스트에 보관하므로 GC 로 인한 조기 소멸 위험이 없다(좋은 습관 — `app/services/mcp_tasks.py:177-179` 의 `_WATCHERS` 도 같은 이유로 강한 참조를 둔다).
- L102-105: 첫 실행이 끝나지 않았으면 일회용 설정 코드를 생성해 로그에 남긴다.
- L106-108: 스케줄러 루프(경보·보존·실행 복구, 내부에서 리스로 1개 워커만 수행).
- L110-117: 종료 시 작업 취소 후 SLI 카운터 마지막 반영.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L113-114 `t.cancel()` 만 하고 `await asyncio.gather(*tasks, return_exceptions=True)` 로 기다리지 않는다. 취소는 다음 루프 틱에 전달되므로, L117 `slo.flush` 와 DB 엔진 정리가 아직 실행 중인 작업과 겹칠 수 있고 "Task was destroyed but it is pending" 경고가 날 수 있다.
- [위험:낮음] L104 `contextlib.suppress(Exception)` 이 `ensure_code` 실패(예: DB 미준비)를 조용히 삼키면 운영자는 설정 코드를 받지 못한 이유를 알 수 없다.
- L89·L116 도 조용한 예외 삼킴 — 의도(로그 실패가 기동을 막지 않게)는 타당하지만 stderr 경고 1줄은 남기는 편이 운영에 낫다.

```python path=app/main.py lines=120-125
logs.setup()
tracing.setup("agentnexus")
app = FastAPI(title="AgentNexus", version=APP_VERSION, lifespan=lifespan,
              description="MCP + AI Gateway + A2A 통합 에이전트 플랫폼 — 관리 API · Unified Model API · MCP/A2A 프록시")
errors.install(app)
app.add_middleware(tracing.TracingMiddleware, component="api")
```
**분석 / Analysis (Level 2)**
- L120-121: import 시점 전역 설정. `tracing.setup` 은 `_configured` 플래그로 1회만 실행(`app/core/tracing.py:118-123`).
- L122-123: `lifespan=` 인자로 위 함수 연결. `version=APP_VERSION` 은 OpenAPI 문서에 노출.
- L124: `errors.install` — ApiError·HTTPException·검증 오류를 RFC 7807 / OpenAI 형식으로 변환(`app/core/errors.py:55-75`).
- L125: 순수 ASGI 미들웨어로 트레이싱(`app/core/tracing.py:176-226`). `add_middleware` 는 마지막에 추가한 것이 가장 바깥이다.
**짚어볼 점 / Watch-outs**
- 일반 `Exception` 처리기는 설치되지 않는다 — 처리되지 않은 예외는 Starlette 기본 500(text/plain)으로 나가 problem+json·JSON-RPC 계약이 깨진다(`app/api/mcp.py:126-129` 처럼 RpcError 만 잡는 엔드포인트에서 드러남).

```python path=app/main.py lines=127-158
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
- L127: `system` 라우터는 항상 등록.
- L128-132: identity 구성요소 — 내장 IdP(`/idp/*`)는 `builtin_idp` 일 때만.
- L133-138: gateway 구성요소 — 관리 API·OpenAI 호환 모델 API·MCP·A2A 프록시.
- L139-144: host 구성요소 — 오케스트레이터 승인 훅(`APPROVAL_HOOKS.append`)과 플랫폼 A2A 실행기 교체(`a2a_api.PLATFORM["executor"]`). 전역 레지스트리에 함수 주입 = 간단한 의존성 역전.
- L156-158: 데모 외부 IdP 는 `local`/`test` 환경 + `seed_demo` 일 때만 붙인다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L140 은 모듈 import 마다 append — 같은 프로세스에서 `app.main` 을 재로드(시험 `importlib.reload`)하면 훅이 중복 등록된다.
- L156 의 `app_env` 기본값은 `"local"`(`app/core/config.py:17`) — 운영 배포에서 `APP_ENV` 를 빠뜨리면 데모 IdP 가 붙는 fail-open 구조다(`config.py` 리뷰 참조).

```python path=app/main.py lines=160-183
if settings.enabled("console"):
    # 웹 콘솔 SPA (React + Vite, web/dist) — 해시 자산은 /_app, 화면 경로는 index.html 폴백(클라이언트 라우팅)
    app.mount("/_app", StaticFiles(directory=Path(settings.console_dist) / "_app", check_dir=False), name="console-assets")

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
- L162: 해시된 정적 자산은 `/_app` 으로 마운트. `check_dir=False` 로 빌드 전에도 기동 가능.
- L164-168: SPA index.html 폴백 — 존재하지 않으면 503 `console_not_built`. `Cache-Control: no-cache` 로 배포 후 즉시 새 번들을 받게 한다(해시 자산은 길게 캐시 가능).
- L170-172: 클라이언트 라우팅 경로 4개(`/console`·`/chat`·`/login`·`/setup`)와 그 하위 경로를 모두 index.html 로. `{path:path}` 는 슬래시를 포함한 나머지 경로를 받는 Starlette 변환기.
- L174-183: favicon·루트 리다이렉트.
**짚어볼 점 / Watch-outs**
- `_console` 은 `path` 인자를 사용하지 않고 항상 같은 index.html 을 반환하므로 경로 조작(path traversal) 위험은 없다. 정적 자산 경로 검증은 Starlette `StaticFiles` 에 위임된다.
- `/setup` 은 인증 없이 SPA 를 내려준다 — 실제 보호는 설정 API(`app/api/setup.py`)의 일회용 코드 검증에 의존한다.

```python path=app/main.py lines=186-195
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
- L186-195: 헬스 체크 — DB `SELECT 1`(`app/db.py:54-57`) 성공 시 200, 실패 시 503 `degraded`. 예외 타입 이름만 노출해 연결 문자열 등 시크릿 유출을 피한다(L191 주석의 의도).
- docker-compose `healthcheck`(`docker-compose.yml`)가 이 경로를 호출해 의존 서비스(`mcp-servers`·`a2a-demo`·`a2a-grpc`) 기동 순서를 맞춘다.
**짚어볼 점 / Watch-outs**
- 동기 `def` 엔드포인트라 스레드풀에서 실행된다(이벤트 루프 차단 없음). 단, DB 가 응답하지 않으면 `pool_pre_ping`·SQLite `timeout=30`(`app/db.py:46`) 만큼 헬스 응답이 지연되어 오케스트레이터 타임아웃(compose 3초)과 엇갈릴 수 있다.
- 인증 없는 엔드포인트가 `env`(app_env)·DB 엔진 종류를 노출한다 — 정보 노출 수준은 낮지만 공개 인터넷 노출 시 고려.

## 파일 평가 / File Assessment
- 강점: (1) 구성요소 플래그로 라우터·백그라운드 작업을 한 곳에서 선택 — 독립 실행 요구(FR-W01)가 읽기 쉽게 드러난다. (2) 백그라운드 Task 참조를 리스트로 보관하고 종료 시 취소한다. (3) 헬스 응답이 시크릿을 노출하지 않는다.
- 위험: (1) 헬스 루프·A2A 감시기가 워커마다 중복 실행(리스 없음). (2) 백그라운드 루프의 조용한 예외 삼킴 — 장애 원인 추적이 어렵다. (3) 일반 예외 처리기 부재로 비정형 500 이 클라이언트 계약을 깬다.
- 개선 권고(영향순): (1) `_health_loop`·`a2a_gateway.watcher` 에 `scheduler.lease` 적용. (2) `suppress(Exception)` 을 `try/except Exception: logs.event("warning", ...)` 로 교체. (3) 종료 시 `await asyncio.gather(*tasks, return_exceptions=True)`.
- 재읽기 권고 레벨: 3 — 구조가 단순해 블록 단위 설계 의도(구성요소·lifespan)만 다시 보면 충분하다.
