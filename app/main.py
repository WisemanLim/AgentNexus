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

# 같은 이벤트 루프에서 lifespan 을 함께 돌릴 내장 ASGI 앱 (시험에서 데모 서버를 in-process 로 붙일 때)
EMBEDDED: list = []
# 기동 후 백그라운드 작업(async 함수) — 각 구성요소가 등록
BACKGROUND: list = []


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


async def _health_loop() -> None:
    from app.models import McpServer
    from app.services import mcp_gateway
    from app.services import scheduler
    while True:
        await asyncio.sleep(settings.mcp_health_interval_s)
        # 리스를 가진 워커만 — 워커 수만큼 업스트림 점검이 늘지 않게 (D18-②). 보유 워커가 죽으면 2 주기 뒤 다른 워커가 잇는다
        try:  # DB 장애(장애조치 중 등) 한 번에 루프가 끝나지 않게, 리스를 못 읽으면 이번 주기는 쉰다 (D19-④)
            if not await run_in_threadpool(scheduler.lease, "mcp_health", settings.mcp_health_interval_s * 2):
                continue

            def _active():
                with db.SessionLocal() as s:
                    return [m.id for m in s.scalars(select(McpServer).where(McpServer.status == "active"))]
            ids = await run_in_threadpool(_active)
        except Exception as exc:  # noqa: BLE001
            logs.event("warning", "gateway", f"MCP 헬스 점검 주기 건너뜀: {type(exc).__name__}")
            continue
        for sid in ids:
            with contextlib.suppress(Exception):
                await mcp_gateway.refresh_tools(sid)


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
        from app.services import scheduler  # 어떤 구성요소 조합이든 — 작업은 구성요소별로 고른다 (D21-②: gateway·registry 단독 배포)
        tasks.append(asyncio.create_task(scheduler.loop()))  # 경보·보존·실행 복구·승인 만료·레지스트리 (리스로 워커 1개만)
        tasks += [asyncio.create_task(fn()) for fn in BACKGROUND]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            from app.services import scheduler, slo
            with contextlib.suppress(Exception):  # 종료 전 남은 SLI 카운터 반영 (DB 가 이미 내려갔으면 버림)
                slo.flush(scheduler.WORKER)


_SECURITY_HEADERS = [(b"x-frame-options", b"DENY"), (b"content-security-policy", b"frame-ancestors 'none'"),
                     (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]


class SecurityHeadersMiddleware:
    """모든 응답에 프레임 삽입 금지(로그인 화면 클릭재킹)·MIME 추측 금지·리퍼러 없음 (D19-④) — 순수 ASGI 라 SSE 스트림을 감싸지 않는다.
    HSTS 는 TLS 를 끝내는 Ingress 가 붙인다."""

    def __init__(self, app):
        self.app = app

    @property
    def router(self):
        return self.app.router

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def _send(message):
            if message["type"] == "http.response.start":
                have = {k.lower() for k, _ in message.get("headers", [])}
                message = {**message, "headers": [*message.get("headers", []),
                                                  *((k, v) for k, v in _SECURITY_HEADERS if k not in have)]}
            await send(message)
        await self.app(scope, receive, _send)


logs.setup()
tracing.setup("agentnexus")
app = FastAPI(title="AgentNexus", version=APP_VERSION, lifespan=lifespan,
              description="MCP + AI Gateway + A2A 통합 에이전트 플랫폼 — 관리 API · Unified Model API · MCP/A2A 프록시")
errors.install(app)
app.add_middleware(tracing.TracingMiddleware, component="api")
app.add_middleware(SecurityHeadersMiddleware)

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
