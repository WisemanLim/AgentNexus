> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [api_Abstract.md](api_Abstract.md)

### 33. app/api/a2a.py
**파일 요약 / File Summary**
- 역할 / Role: A2A 경로 API — 플랫폼 A2A 서버(`/.well-known/agent-card.json`·`/a2a/platform[/{profile}]`·REST), 게이트웨이 프록시(`/a2a/{id}`·REST·푸시 수신·스킬 라우팅·게이트웨이 카드), 원격 에이전트 관리(등록·검증·신뢰·인증 설정·시험·카드 재검증), Task 조회·취소·아티팩트, 카탈로그 등록 핸들러.
- 진입점 거리 / Distance: 1 (`app/main.py:13,138,141`)
- LOC: 719 — `max_lines_per_file`(600) 초과 → **논리 블록 단위로 분석**(모든 블록 커버, 원문 전체 인용)
- 주요 export: `router`, `PLATFORM`(실행기 교체점), `platform_card`, `agent_out`, `create_agent_row`, `card_review`, `framework_hint`, `task_out`
- 의존(내부): `app.core.{auth,config,errors,ids}`, `app.db`, `app.models`, `app.services.{a2a_proto,a2a_rest,audit,registry,secrets,a2a_gateway,a2a_server}`
- 외부 패키지: fastapi, pydantic, sqlalchemy, starlette
- 부수효과 / Side effects: 원격 에이전트 HTTP(프록시·카드), DB 쓰기(에이전트·Task·시크릿·카탈로그·카드 점검), 감사·알림
- 동시성 / Concurrency: async + `run_in_threadpool`, 플랫폼 서버 인스턴스 캐시 `_SERVERS`
- 테스트 커버리지 흔적: `test/test_a2a.py`, `test/test_a2a_rest.py`, `test/test_a2a_v10.py`, `test/test_a2a_grpc.py`, `test/test_design_gaps.py`

## 블록 리뷰 / Block Walk (Level 2)

```python path=app/api/a2a.py lines=1-33
"""A2A 경로 API (FR-G08·A04·A05, FR-R00~R06 · SCR-G01~G07·H15·D10).

게이트웨이: POST /a2a/{agent_id} (JSON-RPC 0.3/1.0) · GET /a2a/{agent_id}/.well-known/agent-card.json(게이트웨이 경유 카드)
· POST /a2a/_skill/{skill}(스킬 기반 라우팅) · POST /a2a/push/{token}(푸시 수신)
플랫폼 A2A 서버(FR-A05): GET /.well-known/agent-card.json · POST /a2a/platform[/{profile_id}]
"""
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, authenticate_token, bearer, require
from app.core.config import APP_VERSION, settings
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.core.ids import new_id, slugify
from app.db import SessionLocal, get_db, utcnow
from app.models import A2AAgent, A2ACardCheck, A2ATask, AgentProfile, CatalogItem, CatalogVersion, Run
from app.services import a2a_proto as ap
from app.services import a2a_rest
from app.services import audit, registry, secrets
from app.services.a2a_gateway import (RESERVED, A2AError, CallContext, apply_card, card_url_for,
                                      ensure_exchange_policy, fetch_card, gateway_card, load_agent, proxy,
                                      PushRejected, receive_push, refresh_card, route_by_skill)
from app.services.a2a_server import A2AServer, TaskContext

router = APIRouter(tags=["a2a"])
```
**분석 / Analysis (Level 2)**
- L1-6: 경로 요약 — 게이트웨이(프록시·카드·스킬·푸시)와 플랫폼 A2A 서버(FR-A05).
- L28-31: 게이트웨이 서비스에서 많은 심볼을 가져온다(변환·인증·카드·프록시·푸시).

```python path=app/api/a2a.py lines=36-61
def _auth(request: Request) -> Principal:
    token = bearer(request)
    if not token:
        raise ApiError(401, "unauthorized", "인증이 필요합니다.")
    with SessionLocal() as db:
        p = authenticate_token(db, token)
    if not p.has("chat:use"):
        raise ApiError(403, "forbidden", "권한 부족: chat:use")
    return p


def _ctx(request: Request, p: Principal) -> CallContext:
    h = request.headers
    return CallContext(p, agent=h.get("x-agentnexus-agent"), data_class=h.get("x-agentnexus-data-class", "internal"),
                       session_id=h.get("x-agentnexus-session"), run_id=h.get("x-agentnexus-run"),
                       step_id=h.get("x-agentnexus-step"))


async def _jsonrpc(request: Request) -> tuple:
    try:
        body = await request.json()
    except ValueError:
        raise A2AError(-32700, "Parse error", http_status=400) from None
    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        raise A2AError(-32600, "Invalid Request", http_status=400)
    return body.get("id"), body["method"], body.get("params") or {}
```
**분석 / Analysis (Level 2)**
- L36-44 `_auth`: Bearer 필수, 인증 후 `chat:use` 필요.
- L47-51 `_ctx`: 클라이언트 헤더 → CallContext(agent·data_class·session/run/step).
- L54-61 `_jsonrpc`: 본문 파싱·형식 검증.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 신뢰 경계] L47-51 `agent`·`data_class`·`session_id`·`run_id`·`step_id` 가 클라이언트 헤더 그대로 — `app/api/mcp.py:55-61` 과 같은 문제(agent 주체 정책 위조·데이터 등급 자기 신고).

```python path=app/api/a2a.py lines=64-109
# ───────────────────────── 플랫폼 A2A 서버 (FR-A05) ─────────────────────────
# 실행기: (TaskContext, profile_id, Principal) -> None. 오케스트레이터(Epic 8)가 교체 등록한다.
PlatformExecutor = Callable[[TaskContext, str, Principal], Awaitable[None]]


async def _echo_executor(ctx: TaskContext, profile_id: str, p: Principal) -> None:
    text = " ".join(x.get("text", "") for x in ctx.message.get("parts", []) if x.get("kind") == "text")
    await ctx.artifact("echo", [{"kind": "text", "text": text}])
    await ctx.update("completed", f"[{profile_id}] {text}")


PLATFORM: dict = {"executor": _echo_executor}
_SERVERS: dict[str, A2AServer] = {}


def _exposed(db: Session) -> list[AgentProfile]:
    return list(db.scalars(select(AgentProfile).where(AgentProfile.a2a_exposed.is_(True),
                                                      AgentProfile.status == "published").order_by(AgentProfile.name)))


def platform_card(profile_id: str | None = None, base_url: str | None = None) -> dict:
    base = (base_url or settings.public_base_url).rstrip("/")
    with SessionLocal() as db:
        profiles = _exposed(db)
        if profile_id:
            profiles = [pr for pr in profiles if pr.id == profile_id]
    skills = []
    for pr in profiles:  # 승인된 Agent Card 편집본(D10)이 있으면 그 스킬·설명, 없으면 프로파일 1개 = 스킬 1개
        own = (pr.a2a_card or {}).get("skills")
        skills += [{"id": sk.get("id"), "name": sk.get("name") or sk.get("id"), "description": sk.get("description") or pr.name,
                    "examples": sk.get("examples") or [], "tags": sk.get("tags") or ["agentnexus"]} for sk in own] if own else \
            [{"id": pr.id, "name": pr.name, "description": pr.description or pr.name, "tags": ["agentnexus", "orchestration"]}]
    skills = skills or [
        {"id": "none", "name": "(노출된 프로파일 없음)", "description": "콘솔에서 프로파일을 A2A 로 노출하세요.", "tags": []}]
    url = f"{base}/a2a/platform" + (f"/{profile_id}" if profile_id else "")
    own_card = (profiles[0].a2a_card or {}) if profile_id and profiles else {}
    caps = own_card.get("capabilities") or {}
    card = ap.build_card(name=own_card.get("name") or ("AgentNexus" + (f" — {profiles[0].name}" if profile_id and profiles else "")),
                         description=own_card.get("description") or
                         "AgentNexus 로컬 오케스트레이션(Planner→Retriever·Summarize·Executor)을 A2A 로 노출",
                         url=url, version=own_card.get("version") or APP_VERSION, skills=skills,
                         streaming=caps.get("streaming", True), push=caps.get("pushNotifications", True),
                         provider={"organization": "AgentNexus", "url": base}, rest_url=f"{url}/rest",
                         grpc_url=settings.a2a_grpc_public_url or None, grpc_tenant="platform" + (f"/{profile_id}" if profile_id else ""))
    return card

```
**분석 / Analysis (Level 2)**
- L66-75: 플랫폼 실행기 타입·기본 echo 실행기(오케스트레이터가 L141 에서 교체), 서버 인스턴스 캐시.
- L79-81 `_exposed`: A2A 노출·게시 프로파일.
- L84-108 `platform_card`: 노출 프로파일들의 스킬 → 이중 카드(기본 설명 또는 편집본).
**짚어볼 점 / Watch-outs**
- L98·L107 `url`·tenant 를 `request.base_url`(Host 헤더)로 — Host 조작 시 카드 URL 이 바뀐다(`app/services/a2a_gateway.py:219-228` 와 동일 지적).

```python path=app/api/a2a.py lines=111-171
async def _persist(task: dict, event: dict, profile_id: str, sub: str | None) -> None:
    def _do():
        with SessionLocal() as db:
            row = db.get(A2ATask, f"platform:{task['id']}")
            if row is None:
                row = A2ATask(id=f"platform:{task['id']}", remote_task_id=task["id"], agent_id=f"platform:{profile_id}",
                              direction="inbound", context_id=task.get("contextId"), principal_sub=sub, history=[],
                              artifacts=[])
                db.add(row)
            row.state, row.history, row.artifacts = task["status"]["state"], task.get("history") or [], \
                task.get("artifacts") or []
            row.run_id = (task.get("metadata") or {}).get("anx_run_id") or row.run_id
            row.status_message = ap.text_of({"kind": "task", "status": task["status"]})[:2000] or row.status_message
            row.updated_at = utcnow()
            db.commit()
    await run_in_threadpool(_do)


_RUN_TO_TASK = {"succeeded": "completed", "partial": "completed", "failed": "failed", "canceled": "canceled"}


def _load_platform(tid: str) -> dict | None:
    """영속 Task → A2A Task. 실행기가 다른 워커·재시작 전에 있었어도, 실행(run)이 끝났으면 그 결과로 Task 를 완결한다."""
    with SessionLocal() as db:
        row = db.get(A2ATask, f"platform:{tid}")
        if row is None:
            return None
        if row.state not in ap.TERMINAL and row.run_id and (r := db.get(Run, row.run_id)) is not None \
                and r.status in _RUN_TO_TASK:
            row.state = _RUN_TO_TASK[r.status]
            row.status_message = f"실행 {r.status} (run {r.id})"
            if row.state == "completed" and not any(a.get("name") == "answer.md" for a in row.artifacts or []):
                row.artifacts = [*(row.artifacts or []), {"artifactId": uuid.uuid4().hex, "name": "answer.md",
                                                           "parts": [{"kind": "text", "text": r.result or ""}],
                                                           "description": "AgentNexus 오케스트레이션 답변"}]
            row.updated_at = utcnow()
            db.commit()
        status = {"state": row.state, "timestamp": (row.updated_at or row.created_at).isoformat()}
        if row.status_message:
            status["message"] = {"kind": "message", "messageId": f"{tid}-status", "role": "agent", "taskId": tid,
                                 "contextId": row.context_id or tid, "parts": [{"kind": "text", "text": row.status_message}]}
        return {"kind": "task", "id": tid, "contextId": row.context_id or tid, "status": status,
                "artifacts": row.artifacts or [], "history": row.history or [],
                **({"metadata": {"anx_run_id": row.run_id}} if row.run_id else {})}


def _cancel_platform(tid: str) -> dict | None:
    from app.services import orchestrator
    with SessionLocal() as db:
        row = db.get(A2ATask, f"platform:{tid}")
        if row is None:
            return None
        run_id = row.run_id
    if run_id:
        orchestrator.cancel(run_id)
    with SessionLocal() as db:
        row = db.get(A2ATask, f"platform:{tid}")
        row.state, row.status_message, row.updated_at = "canceled", "다른 인스턴스에서 취소", utcnow()
        db.commit()
    return _load_platform(tid)

```
**분석 / Analysis (Level 2)**
- L111-126 `_persist`: 플랫폼 수신 Task 를 `platform:{id}` 행으로 영속(방향 inbound).
- L132-155 `_load_platform`: 영속 Task → A2A Task, 실행(run)이 끝났으면 그 결과로 Task 완결(다른 워커·재시작 후에도).
- L157-170 `_cancel_platform`: 실행 취소 후 Task canceled.
**짚어볼 점 / Watch-outs**
- L142 `answer.md` 아티팩트 중복 추가 방지 검사 — 재조회 멱등성 배려.
- L122 `anx_run_id` 메타데이터로 실행과 Task 를 잇는다 — 이 메타는 실행기가 설정(신뢰 범위 내부).

```python path=app/api/a2a.py lines=173-229
def _platform_server(profile_id: str | None) -> A2AServer:
    key = profile_id or "_default"
    if key in _SERVERS:
        return _SERVERS[key]
    principals: dict[str, Principal] = {}

    async def authenticate(request: Request):
        token = bearer(request)
        if not token:
            return None
        try:
            p = await run_in_threadpool(_principal_of, token)
        except ApiError:
            return None
        principals[token] = p
        return {"principal": p}

    async def executor(ctx: TaskContext) -> None:
        pid = profile_id or (ctx.message.get("metadata") or {}).get("skill")
        if pid and not profile_id:  # 스킬 id → 그 스킬을 선언한 프로파일 (D10 ⑤ 스킬 ↔ 그래프 진입점)
            with SessionLocal() as db:
                pid = next((pr.id for pr in _exposed(db) if pr.id == pid or any(
                    sk.get("id") == pid for sk in (pr.a2a_card or {}).get("skills") or [])), pid)
        if not pid:
            with SessionLocal() as db:
                exposed = _exposed(db)
            if not exposed:
                await ctx.update("rejected", "A2A 로 노출된 에이전트 프로파일이 없습니다.")
                return
            pid = exposed[0].id
        await PLATFORM["executor"](ctx, pid, ctx.auth["principal"])

    async def on_change(task: dict, event: dict) -> None:
        sub = None
        for c in server.ctxs.values():
            if c.task is task:
                sub = c.auth["principal"].sub
        await _persist(task, event, profile_id or "default", sub)

    async def loader(tid: str) -> dict | None:
        return await run_in_threadpool(_load_platform, tid)

    async def remote_cancel(tid: str) -> dict | None:
        return await run_in_threadpool(_cancel_platform, tid)

    server = A2AServer(lambda: platform_card(profile_id), executor, authenticate=authenticate, on_change=on_change,
                       blocking_timeout=60, loader=loader, remote_cancel=remote_cancel)
    _SERVERS[key] = server
    return server


def _principal_of(token: str) -> Principal:
    with SessionLocal() as db:
        p = authenticate_token(db, token)
    if not p.has("chat:use"):
        raise ApiError(403, "forbidden", "권한 부족")
    return p
```
**분석 / Analysis (Level 2)**
- L173-221 `_platform_server`: 프로파일별 A2AServer 캐시 — 인증 콜백(토큰 → 주체), 실행기(스킬 id → 프로파일 → `PLATFORM["executor"]`), 상태 변경 시 영속, 로더·원격 취소.
- L224-229 `_principal_of`: 토큰 → 주체, `chat:use` 필요.
**짚어볼 점 / Watch-outs**
- L177·L187 `principals` dict 에 토큰→주체를 담지만 정리(pop)가 없다 — 플랫폼 서버 인스턴스가 프로세스 수명만큼 사는데(L220 캐시), 토큰마다 항목이 쌓인다(메모리 증가). 토큰을 키로 쓰므로 민감 값이 메모리에 남는다.
- L207-210 `on_change` 가 `server.ctxs` 를 순회해 주체를 찾는다 — O(n) 이지만 Task 수가 적다는 가정.

```python path=app/api/a2a.py lines=232-275
@router.get("/.well-known/agent-card.json")
def platform_well_known(request: Request):
    return platform_card(base_url=str(request.base_url))


@router.post("/a2a/platform")
async def platform_rpc(request: Request):
    return await _platform_server(None).handle(request)


@router.get("/a2a/platform/{profile_id}/.well-known/agent-card.json")
def platform_profile_card(profile_id: str, request: Request):
    with SessionLocal() as db:
        if profile_id not in {pr.id for pr in _exposed(db)}:
            raise not_found("exposed profile")
    return platform_card(profile_id, base_url=str(request.base_url))


_REST_METHODS = ["GET", "POST", "DELETE"]


@router.api_route("/a2a/platform/rest/{path:path}", methods=_REST_METHODS)
async def platform_rest(path: str, request: Request):
    """플랫폼 A2A 서버 HTTP+JSON 바인딩 (D16-①) — JSON-RPC 와 같은 실행기."""
    return await _platform_server(None).rest(request)


@router.api_route("/a2a/platform/{profile_id}/rest/{path:path}", methods=_REST_METHODS)
async def platform_profile_rest(profile_id: str, path: str, request: Request):
    with SessionLocal() as db:
        if profile_id not in {pr.id for pr in _exposed(db)}:
            return a2a_rest.error_response(ap.TASK_NOT_FOUND, "노출되지 않은 프로파일", http_status=404)
    return await _platform_server(profile_id).rest(request)


@router.post("/a2a/platform/{profile_id}")
async def platform_profile_rpc(profile_id: str, request: Request):
    with SessionLocal() as db:
        if profile_id not in {pr.id for pr in _exposed(db)}:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": ap.TASK_NOT_FOUND,
                                                                         "message": "노출되지 않은 프로파일"}},
                                status_code=404)
    return await _platform_server(profile_id).handle(request)

```
**분석 / Analysis (Level 2)**
- L232-247 플랫폼 well-known 카드·프로파일 카드(노출 확인).
- L250-274 플랫폼 RPC·REST(JSON-RPC·HTTP+JSON 같은 실행기).
**짚어볼 점 / Watch-outs**
- L245·L262·L270 노출 프로파일 확인을 매 요청 DB 조회 — 캐시 없음(수용 범위).

```python path=app/api/a2a.py lines=277-348
# ───────────────────────── 게이트웨이 A2A 프록시 (FR-G08) ─────────────────────────
@router.post("/a2a/push/{token}")
async def push_webhook(token: str, request: Request):
    raw = await request.body()
    try:
        body = json.loads(raw)
    except ValueError:
        return Response(status_code=400)
    try:
        ok = await receive_push(token, body, request.headers.get("x-a2a-notification-token"),
                                request.headers.get("authorization"), raw)
    except PushRejected as exc:  # 서명 검증 실패 (SCR-G07 ⑧)
        return JSONResponse({"error": "push_signature_invalid", "message": str(exc)}, status_code=401)
    return Response(status_code=200 if ok else 404)


@router.post("/a2a/_skill/{skill}")
async def skill_rpc(skill: str, request: Request):
    p = await run_in_threadpool(_auth, request)
    rid = None
    try:
        rid, method, params = await _jsonrpc(request)
        agent_id = await run_in_threadpool(route_by_skill, skill, p)
        return await proxy(agent_id, _ctx(request, p), rid, method, params)
    except A2AError as exc:
        return exc.response(rid)


@router.get("/a2a/{agent_id}/.well-known/agent-card.json")
async def proxied_card(agent_id: str, request: Request):
    try:
        agent = await run_in_threadpool(load_agent, agent_id)
        if not agent.card:
            await refresh_card(agent_id)
            agent = await run_in_threadpool(load_agent, agent_id)
    except A2AError as exc:
        raise ApiError(exc.http_status if exc.http_status >= 400 else 502, "agent_card_unavailable", exc.message) from exc
    return gateway_card(agent, str(request.base_url))


@router.api_route("/a2a/{agent_id}/rest/{path:path}", methods=_REST_METHODS)
async def a2a_proxy_rest(agent_id: str, path: str, request: Request):
    """게이트웨이 A2A 프록시 HTTP+JSON 바인딩 (D16-①) — REST → 1.0 JSON-RPC 와 같은 정책·가드·감사·변환 → REST 응답."""
    try:
        p = await run_in_threadpool(_auth, request)
        raw = await request.body() if request.method not in ("GET", "DELETE") else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            raise a2a_rest.RestError(-32700, "Parse error", http_status=400) from None
        method, params = a2a_rest.to_rpc(request.method, path, request.query_params, body)
        ctx = _ctx(request, p)
        # a2a-grpc 서비스가 위임한 요청은 기록상 GRPC (값은 라벨일 뿐 권한과 무관)
        ctx.binding = "GRPC" if request.headers.get("x-agentnexus-binding") == "GRPC" else "HTTP+JSON"
        return a2a_rest.from_rpc_response(await proxy(agent_id, ctx, "rest", method, params))
    except (a2a_rest.RestError, A2AError) as exc:
        return a2a_rest.error_response(exc.code, exc.message, exc.data, exc.http_status,
                                       headers={"Retry-After": str(exc.data["retry_after"])} if (exc.data or {}).get("retry_after") else None)
    except ApiError as exc:
        return a2a_rest.error_response(-32603, exc.detail or exc.code, {"code": exc.code}, exc.status, headers=exc.headers or None)


@router.post("/a2a/{agent_id}")
async def a2a_proxy(agent_id: str, request: Request):
    p = await run_in_threadpool(_auth, request)
    rid = None
    try:
        rid, method, params = await _jsonrpc(request)
        return await proxy(agent_id, _ctx(request, p), rid, method, params)
    except A2AError as exc:
        return exc.response(rid)

```
**분석 / Analysis (Level 2)**
- L278-290 `push_webhook`: 원격 → 게이트웨이 웹훅 — 서명 검증 실패는 401, 성공/미발견은 200/404.
- L293-302 스킬 라우팅: 스킬 → 에이전트 → 프록시.
- L305-314 게이트웨이 경유 카드.
- L317-336 게이트웨이 프록시 REST: 본문 파싱 → `to_rpc` → 프록시 → REST 응답. **binding 라벨**은 `X-AgentNexus-Binding` 헤더로(권한 무관 기록용, L329-330).
- L339-347 게이트웨이 프록시 JSON-RPC.
**짚어볼 점 / Watch-outs**
- L279-290 푸시 수신은 인증 미들웨어가 아니라 URL 의 무작위 토큰 + (설정 시) 서명으로만 인증 — 토큰 노출 시 Task 조작 가능(상세 `app/services/a2a_gateway.py:748-776` 리뷰).
- L322 `request.body()` 를 읽어 파싱 — 크기 제한은 ASGI 서버·프록시에 의존.

```python path=app/api/a2a.py lines=350-441
# ───────────────────────── 원격 에이전트 관리 (SCR-G01~G04) ─────────────────────────
_FRAMEWORKS = (("google-adk", "ADK"), ("adk", "ADK"), ("langgraph", "LangGraph"), ("crewai", "CrewAI"), ("autogen", "AutoGen"),
               ("semantic-kernel", "Semantic Kernel"), ("beeai", "BeeAI"), ("pydantic-ai", "PydanticAI"), ("a2a-sdk", "a2a-sdk"),
               ("agentnexus", "AgentNexus"))


def framework_hint(card: dict | None) -> str | None:
    """Agent Card 의 provider·확장 URI·문서 주소에서 프레임워크 추정 (SCR-G01 — 표준 필드가 없어 힌트, 운영자 지정이 우선)."""
    if not card:
        return None
    hay = json.dumps({k: card.get(k) for k in ("provider", "documentationUrl", "capabilities", "url", "name")},
                     ensure_ascii=False).lower()
    return next((label for key, label in _FRAMEWORKS if key in hay), None)


def agent_out(a: A2AAgent, detail: bool = False) -> dict:
    info = ap.card_info(a.card) if a.card else {}
    out = {"id": a.id, "name": a.name, "url": a.url, "card_url": a.card_url, "version": a.version,
           "trust_tier": a.trust_tier, "status": a.status, "skills": a.skills, "card_changed": a.card_changed,
           "last_fetched_at": a.last_fetched_at, "auth": {k: v for k, v in (a.auth or {}).items() if k != "secret_ref"},
           "credential_configured": bool((a.auth or {}).get("secret_ref")), "gateway_path": f"/a2a/{a.id}",
           "protocol_versions": info.get("protocol_versions", []), "bindings": info.get("bindings", []),
           "streaming": info.get("streaming"),
           "push": info.get("push"), "catalog_item_id": a.catalog_item_id, "owner_team": a.owner_team,
           "framework": a.framework or framework_hint(a.card), "framework_source": "operator" if a.framework else
           ("card" if framework_hint(a.card) else None), "push_verify": bool(((a.auth or {}).get("push_verify") or {}).get("jwks_url")),
           "cache": {"etag": a.etag, "ttl_s": a.cache_ttl_s, "last_fetched_at": a.last_fetched_at,
                     "next_check_at": (a.last_fetched_at + timedelta(seconds=a.cache_ttl_s or 3600))
                     if a.last_fetched_at else None},
           "index": sorted({t for sk in a.skills or [] for t in (sk.get("tags") or [])} | {sk.get("id") for sk in a.skills or []}
                           | set((a.card or {}).get("securitySchemes") or {}))}
    if detail:
        out["card"], out["card_hash"] = a.card, a.card_hash
    return out


class AgentIn(BaseModel):
    url: str = Field(description="에이전트 기준 URL 또는 agent-card.json URL")
    id: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    trust_tier: str = Field("untrusted", pattern="^(verified|trusted|untrusted|blocked)$")
    auth: dict = {}
    secret: str | None = Field(None, repr=False)
    owner_team: str | None = None
    dry_run: bool = False  # 조회·검증만 (SCR-G02 ②~⑥) — 등록하지 않음


def create_agent_row(db: Session, card: dict, card_url: str, actor: str, *, agent_id: str | None = None,
                     trust_tier: str = "untrusted", auth: dict | None = None, catalog_item: CatalogItem | None = None
                     ) -> A2AAgent:
    info = ap.card_info(card)
    aid = agent_id or slugify(card.get("name", "agent"))[:60]
    if aid in RESERVED:
        raise bad_request(f"예약된 id 입니다: {aid}")
    if db.get(A2AAgent, aid):
        raise conflict(f"이미 등록된 에이전트 id: {aid}")
    if catalog_item is None:
        catalog_item = registry.find_item(db, "a2a_agent", aid)
    if catalog_item is None:
        catalog_item = CatalogItem(id=new_id("cat"), type="a2a_agent", name=aid, display_name=card.get("name"),
                                   description=card.get("description"), source="manual", status="active",
                                   version=str(card.get("version")), item_metadata=card, owner=actor, ref_id=aid,
                                   tags=sorted({t for s in info["skills"] for t in (s.get("tags") or [])})[:20])
        db.add(catalog_item)
        db.flush()
    a = A2AAgent(id=aid, catalog_item_id=catalog_item.id, name=card.get("name", aid), url=info["url"],
                 card_url=card_url, trust_tier=trust_tier, auth=auth or {})
    db.add(a)
    apply_card(db, a, card)
    catalog_item.status, catalog_item.ref_id = "active", aid
    ensure_exchange_policy(db, aid)
    return a


def card_review(card: dict, card_url: str, fetch_ms: float | None = None) -> dict:
    """등록 전 카드 점검 (SCR-G02 ⑤⑥) — 스키마·필수·url 도메인·TLS·capabilities·skills·securitySchemes·서명."""
    from urllib.parse import urlparse
    info = ap.card_info(card)
    errs = ap.validate_card(card)
    host = lambda u: (urlparse(u).hostname or "")  # noqa: E731
    items = [{"key": "schema", "ok": not errs, "detail": "; ".join(errs) or "A2A Agent Card 스키마 ✓"},
             {"key": "url_domain", "ok": host(info["url"]) == host(card_url) or not info["url"],
              "detail": f"카드 url {info['url'] or '없음'} · 조회 {card_url}"},
             {"key": "tls", "ok": card_url.startswith("https://"), "detail": "HTTPS" if card_url.startswith("https://") else "HTTP — 운영은 HTTPS 권장", "warn": True},
             {"key": "capabilities", "ok": True, "detail": f"streaming {'✓' if info['streaming'] else '✗'} · push {'✓' if info['push'] else '✗'}"},
             {"key": "skills", "ok": bool(info["skills"]), "detail": f"{len(info['skills'])}개"},
             {"key": "security_schemes", "ok": True, "detail": ", ".join(card.get("securitySchemes") or {}) or "선언 없음"},
             {"key": "signature", "ok": bool(card.get("signatures")), "warn": True,
              "detail": "JWS 서명 ✓" if card.get("signatures") else "서명 없음 — partner 이상 등급은 사유 필요"}]
    suggest = "partner" if card.get("signatures") and card_url.startswith("https://") else "community"
    return {"checks": items, "ok": all(i["ok"] or i.get("warn") for i in items), "suggested_trust": suggest,
            "fetch_ms": fetch_ms, "card": card}

```
**분석 / Analysis (Level 2)**
- L351-362 프레임워크 힌트(카드에서 추정).
- L365-383 `agent_out`: 에이전트 표현 — `auth` 에서 `secret_ref` 제거, 자격 설정 여부만.
- L386-420 `AgentIn`·`create_agent_row`: 등록 — 예약 id 거부, 중복 거부, 카탈로그 항목 생성, 카드 적용, 교환 정책.
- L423-440 `card_review`: 등록 전 카드 점검(스키마·도메인·TLS·능력·스킬·보안·서명) → 권장 신뢰 등급.
**짚어볼 점 / Watch-outs**
- L369·L382 `secret_ref` 제거하고 노출 — 시크릿 값 숨김(좋음).
- L430-431 `url_domain` 검사는 카드 url 호스트 = 조회 url 호스트 확인(스푸핑 일부 방어).

```python path=app/api/a2a.py lines=443-507
@router.get("/api/v1/a2a/agents")
def list_agents(_: Principal = Depends(require("a2a:read")), db: Session = Depends(get_db)):
    return {"items": [agent_out(a) for a in db.scalars(select(A2AAgent).order_by(A2AAgent.id))]}


@router.post("/api/v1/a2a/agents", status_code=201)
async def register_agent(body: AgentIn, p: Principal = Depends(require("a2a:write"))):
    """원격 에이전트 등록 (SCR-G02) — Agent Card 조회(게이트웨이 egress)·검증 → 승인 권한이 없으면 등록 요청(pending,
    E05 승인 후 활성), 있으면 바로 활성. dry_run 이면 조회·점검 결과만."""
    import time as _t
    cu = card_url_for(body.url)
    t0 = _t.perf_counter()
    try:
        card = await fetch_card(cu, (body.auth or {}).get("tls"))
    except A2AError as exc:
        raise ApiError(exc.http_status if exc.http_status >= 400 else 502, "agent_card_invalid", exc.message,
                       extra=exc.data) from exc
    rv = card_review(card, cu, round((_t.perf_counter() - t0) * 1000, 1))
    if body.dry_run:
        return rv
    if body.trust_tier in ("verified", "trusted") and not p.has("policy:write"):
        raise ApiError(403, "forbidden", "신뢰 등급 지정은 보안 담당 권한이 필요합니다.")
    review_needed = not p.has("catalog:approve")

    def _do():
        with SessionLocal() as db:
            auth = dict(body.auth)
            a = create_agent_row(db, card, cu, p.sub, agent_id=body.id, trust_tier=body.trust_tier, auth=auth)
            a.owner_team = body.owner_team or p.team
            if review_needed:  # 등록 요청 — 승인(E05) 전에는 호출 불가 (MSG-008)
                a.status = "pending"
                item = db.get(CatalogItem, a.catalog_item_id)
                item.status = "pending"
                db.add(CatalogVersion(id=new_id("cver"), item_id=item.id, version=str(card.get("version") or "0"),
                                      item_metadata=card, status="pending", created_by=p.sub))
            if body.secret:
                ref = f"a2a/{a.id}/credential"
                secrets.put(db, ref, body.secret, kind="token")
                a.auth = {**a.auth, "secret_ref": ref}
            db.commit()
            if review_needed:  # 커밋 후 — 알림은 별도 세션이라 열린 쓰기 트랜잭션 중이면 SQLite 가 잠긴다
                from app.services.notify import notify
                notify("registry.pending", f"원격 에이전트 등록 요청: {a.id}", f"{card.get('name')} — {p.sub}", severity="warn",
                       link="/console/registry/approvals", audience_role="operator", dedupe_key=f"a2a.pending:{a.id}")
            audit.append("a2a.agent.register", actor=p.sub, target=a.id, data={"url": a.url, "trust": a.trust_tier,
                                                                               "status": a.status})
            return {**agent_out(a, detail=True), "review": {k: rv[k] for k in ("checks", "ok", "suggested_trust")}}
    return await run_in_threadpool(_do)


@router.get("/api/v1/a2a/agents/{agent_id}/checks")
def card_checks(agent_id: str, _: Principal = Depends(require("a2a:read")), db: Session = Depends(get_db)):
    """Agent Card 재검증 이력 (SCR-G03 ⑦)."""
    rows = db.scalars(select(A2ACardCheck).where(A2ACardCheck.agent_id == agent_id)
                      .order_by(A2ACardCheck.checked_at.desc()).limit(50))
    return {"items": [{"id": r.id, "status": r.status, "http_status": r.http_status, "etag": r.etag, "changes": r.changes,
                       "error": r.error, "checked_at": r.checked_at} for r in rows]}


@router.get("/api/v1/a2a/agents/{agent_id}")
def get_agent(agent_id: str, _: Principal = Depends(require("a2a:read")), db: Session = Depends(get_db)):
    a = db.get(A2AAgent, agent_id)
    if a is None:
        raise not_found("a2a agent")
    return agent_out(a, detail=True)
```
**분석 / Analysis (Level 2)**
- L443-445 목록(`a2a:read`).
- L448-490 `register_agent`(`a2a:write`): 카드 조회·검증 → dry_run 이면 점검만 → 신뢰 등급 지정은 `policy:write` 필요(L463-464) → 승인 권한 없으면 pending(E05 승인 후 활성) → 시크릿 저장 → 알림·감사.
- L493-507 카드 점검 이력·상세.
**짚어볼 점 / Watch-outs**
- [위험:중간] L448-462 `dry_run` 은 `a2a:write`(developer) 만으로 **임의 URL 의 카드를 게이트웨이가 조회**하게 한다 — egress 가드(링크 로컬·메타데이터)만 막으므로 내부 서비스 조회(블라인드 SSRF)가 가능하다. 응답은 카드 형식으로 파싱·반환되므로 일부 내부 응답이 노출될 수 있다.
- L463-464 신뢰 등급 상향은 `policy:write`(보안 담당) — 직무 분리 적용(좋음).
- L472-477 pending 등록은 카탈로그 버전·알림으로 승인 흐름에 연결.

```python path=app/api/a2a.py lines=510-556
class AgentPatch(BaseModel):
    status: str | None = Field(None, pattern="^(active|disabled)$")
    framework: str | None = Field(None, max_length=40)
    auth: dict | None = None
    secret: str | None = Field(None, repr=False)
    acknowledge_card_change: bool = False


@router.patch("/api/v1/a2a/agents/{agent_id}")
def patch_agent(agent_id: str, body: AgentPatch, p: Principal = Depends(require("a2a:write")),
                db: Session = Depends(get_db)):
    """원격 에이전트 인증 설정 (SCR-G04) — scheme(bearer 교환·apiKey·none)·시크릿·카드 변경 확인."""
    a = db.get(A2AAgent, agent_id)
    if a is None:
        raise not_found("a2a agent")
    if body.status:
        a.status = body.status
    if body.framework is not None:
        a.framework = body.framework.strip() or None
    if body.auth is not None:
        pv = (body.auth.get("push_verify") or {}) if isinstance(body.auth.get("push_verify"), dict) else None
        if pv is not None and pv.get("jwks_url") and not str(pv["jwks_url"]).startswith(("https://", "http://")):
            raise bad_request("push_verify.jwks_url 은 http(s) URL (MSG-004).", code="invalid_jwks_url")
        if body.auth.get("scheme") == "oauth2" and not str(body.auth.get("token_url") or (a.auth or {}).get("token_url") or "").startswith("https://") \
                and settings.app_env == "prod":
            raise bad_request("OAuth2 token_url 은 https 여야 합니다 (MSG-004).", code="invalid_token_url")
        a.auth = {**(a.auth or {}), **body.auth}
    if body.secret:
        ref = f"a2a/{a.id}/credential"
        secrets.put(db, ref, body.secret, kind="token")
        a.auth = {**(a.auth or {}), "secret_ref": ref}
    if body.acknowledge_card_change:
        a.card_changed = False
    db.commit()
    audit.append("a2a.agent.update", actor=p.sub, target=a.id, data={"status": body.status, "auth": body.auth,
                                                                    "secret": bool(body.secret)})
    return agent_out(a, detail=True)


@router.post("/api/v1/a2a/agents/{agent_id}/refresh")
async def refresh_agent(agent_id: str, p: Principal = Depends(require("a2a:write"))):
    try:
        res = await refresh_card(agent_id, p.sub)
    except A2AError as exc:
        raise ApiError(exc.http_status if exc.http_status >= 400 else 502, "agent_card_invalid", exc.message,
                       extra=exc.data) from exc
    return {"agent_id": agent_id, "changed": res["changed"], "info": res["info"]}
```
**분석 / Analysis (Level 2)**
- L510-546 `AgentPatch`·`patch_agent`(`a2a:write`): 상태·프레임워크·auth(병합)·시크릿·카드 변경 확인. push_verify jwks_url·OAuth token_url 형식 검증(prod 는 https).
- L549-556 카드 재검증.
**짚어볼 점 / Watch-outs**
- [위험:높음 · 시크릿 유출] L529-536·L537-540: `body.auth` 를 **그대로 병합**한다 — `a2a:write`(developer) 만으로 이미 승인된 에이전트의 `auth.secret_ref`·`token_url` 을 바꿔 게이트웨이가 저장된 시크릿을 외부로 전송하게 만들 수 있다(상세 `app/services/a2a_gateway.py:243-294` 리뷰). `secret_ref` 입력 금지·소유권/재승인 필요. L533-535 의 token_url https 검증은 prod 에서만이고 https 를 쓰는 외부 주소는 막지 못한다.
- L530-532 push_verify.jwks_url 은 http(s) 만 확인 — 내부 주소는 허용된다.

```python path=app/api/a2a.py lines=559-605
class TryIn(BaseModel):
    text: str
    data: dict | None = None
    files: list[dict] = Field(default_factory=list, max_length=10,
                              description="[{name, mimeType, bytes(base64)} | {name, mimeType, uri}] — file Part (SCR-G07 ②)")
    task_id: str | None = None
    context_id: str | None = None
    version: str = Field("1.0", pattern=r"^(0\.3|1\.0)$")  # D15-② 1.0 기본
    blocking: bool = True


def _file_parts(files: list[dict]) -> list[dict]:
    import base64
    out, total = [], 0
    for f in files:
        name, mime = str(f.get("name") or "file")[:255], str(f.get("mimeType") or "application/octet-stream")[:120]
        if f.get("bytes"):
            try:
                total += len(base64.b64decode(f["bytes"], validate=True))
            except ValueError as exc:
                raise bad_request(f"{name}: bytes 는 base64 (MSG-004)", code="invalid_file") from exc
            out.append({"kind": "file", "file": {"name": name, "mimeType": mime, "bytes": f["bytes"]}})
        elif str(f.get("uri") or "").startswith(("https://", "http://")):
            out.append({"kind": "file", "file": {"name": name, "mimeType": mime, "uri": f["uri"]}})
        else:
            raise bad_request(f"{name}: bytes 또는 http(s) uri 가 필요합니다 (MSG-004)", code="invalid_file")
    if total > settings.attachment_max_mb * 1024 * 1024:
        raise bad_request(f"파일 합계가 {settings.attachment_max_mb}MB 를 넘습니다.", code="file_too_large")
    return out


@router.post("/api/v1/a2a/agents/{agent_id}/try")
async def try_agent(agent_id: str, body: TryIn, request: Request, p: Principal = Depends(require("a2a:read"))):
    """A2A 시험 콘솔 (SCR-G07) — 게이트웨이 경로 그대로 message/send."""
    parts = [{"kind": "text", "text": body.text}] + ([{"kind": "data", "data": body.data}] if body.data else []) + _file_parts(body.files)
    msg = {"kind": "message", "messageId": uuid.uuid4().hex, "role": "user", "parts": parts,
           **({"taskId": body.task_id} if body.task_id else {}), **({"contextId": body.context_id} if body.context_id
                                                                     else {})}
    method = ap.method_for("send", body.version)
    params = {"message": msg if body.version == ap.V03 else ap.message_to_v10(msg),
              "configuration": {"blocking": body.blocking} if body.version == ap.V03 else
              {"returnImmediately": not body.blocking}}
    try:
        res = await proxy(agent_id, _ctx(request, p), "try", method, params)
    except A2AError as exc:
        return {"error": {"code": exc.code, "message": exc.message, "data": exc.data}}
    return json.loads(res.body)
```
**분석 / Analysis (Level 2)**
- L559-587 `TryIn`·`_file_parts`: 시험 콘솔 입력 — 파일 파트(base64 검증·합계 크기 제한), uri 는 http(s)만.
- L590-605 `try_agent`(`a2a:read`): 게이트웨이 경로 그대로 message/send.
**짚어볼 점 / Watch-outs**
- L577 `base64.b64decode(..., validate=True)` 로 형식 검증(좋음). L585 합계 크기 제한.
- L581-582 파일 uri 는 http(s)만 허용하지만 원격 에이전트가 그 uri 를 가져오는지는 원격 책임.

```python path=app/api/a2a.py lines=608-667
# ───────────────────────── Task (SCR-G05·G06) ─────────────────────────
def task_out(t: A2ATask, detail: bool = False) -> dict:
    out = {"id": t.id, "remote_task_id": t.remote_task_id, "agent_id": t.agent_id, "direction": t.direction,
           "state": t.state, "status_message": t.status_message, "context_id": t.context_id,
           "session_id": t.session_id, "run_id": t.run_id, "principal": t.principal_sub,
           "artifacts_count": len(t.artifacts or []), "push": bool(t.push_token), "trace_id": t.trace_id,
           "created_at": t.created_at, "updated_at": t.updated_at}
    if detail:
        out["history"], out["artifacts"] = t.history, t.artifacts
        out["events"] = t.events or []
        out["transitions"] = [{"state": "submitted", "at": t.created_at}] + [
            {"state": e["state"], "at": e["at"], "source": e.get("source")} for e in (t.events or []) if e.get("transition")]
    return out


def _task_visible(p: Principal, t: A2ATask) -> bool:
    return p.has("a2a:write") or p.has("gateway:read") or t.principal_sub == p.sub


@router.get("/api/v1/a2a/tasks")
def list_tasks(p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db),
               agent_id: str | None = None, state: str | None = None, run_id: str | None = None,
               limit: int = Query(100, le=500)):
    stmt = select(A2ATask).order_by(A2ATask.updated_at.desc()).limit(limit)
    if not (p.has("a2a:write") or p.has("gateway:read")):
        stmt = stmt.where(A2ATask.principal_sub == p.sub)  # 팀 간 누출 방지
    if agent_id:
        stmt = stmt.where(A2ATask.agent_id == agent_id)
    if state:
        stmt = stmt.where(A2ATask.state == state)
    if run_id:
        stmt = stmt.where(A2ATask.run_id == run_id)
    rows = list(db.scalars(stmt))
    agents = {a.id: a for a in db.scalars(select(A2AAgent).where(A2AAgent.id.in_({t.agent_id for t in rows})))}
    out = []
    for t in rows:  # 위임 카드 헤더(SCR-C05 ①)·이벤트 로그(④) — 채팅 사용자는 에이전트 API 권한이 없어 함께 준다
        a = agents.get(t.agent_id)
        out.append(task_out(t) | {"agent": {"name": a.name, "trust_tier": a.trust_tier, "version": a.version,
                                            "skills": [s.get("name") or s.get("id") for s in a.skills or []][:5]} if a else None,
                                  "events": [{"state": (h.get("status") or {}).get("state") or h.get("kind"),
                                              "text": " ".join(x.get("text", "") for x in (h.get("parts") or [])
                                                               if x.get("kind") == "text")[:160]}
                                             for h in (t.history or [])[-8:]] if run_id else None})
    return {"items": out}


@router.get("/api/v1/a2a/tasks/{task_id:path}/artifacts/{artifact_id}")
def get_artifact(task_id: str, artifact_id: str, p: Principal = Depends(require("chat:use")),
                 db: Session = Depends(get_db)):
    t = db.get(A2ATask, task_id)
    if t is None or not _task_visible(p, t):
        raise not_found("task")
    art = next((a for a in t.artifacts or [] if a.get("artifactId") == artifact_id), None)
    if art is None:
        raise not_found("artifact")
    texts = [x.get("text", "") for x in art.get("parts", []) if x.get("kind") == "text"]
    if texts and len(texts) == len(art.get("parts", [])):
        media = "text/markdown" if (art.get("name") or "").endswith(".md") else "text/plain"
        return PlainTextResponse("\n".join(texts), media_type=media + "; charset=utf-8")
    return art
```
**분석 / Analysis (Level 2)**
- L609-620 `task_out`: Task 표현(push_config 는 노출 안 함 — `push` 불리언만).
- L623-624 `_task_visible`: 쓰기·게이트웨이 권한 또는 본인 Task.
- L627-651 목록(`chat:use`): 권한 없으면 본인 Task 만(팀 간 누출 방지), 에이전트 정보·이벤트 병합.
- L654-667 아티팩트 조회(가시성 확인).
**짚어볼 점 / Watch-outs**
- L613 `push` 불리언만 노출하고 `push_config`(url·token) 는 응답에 없다 — 클라이언트 웹훅 비밀 보호(좋음).
- L632-633 본인 Task 필터로 크로스테넌트 차단(`test/test_a2a.py:244-249` 검증).

```python path=app/api/a2a.py lines=670-719
@router.get("/api/v1/a2a/tasks/{task_id:path}")
def get_task(task_id: str, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    t = db.get(A2ATask, task_id)
    if t is None or not _task_visible(p, t):
        raise not_found("task")
    return task_out(t, detail=True)


@router.post("/api/v1/a2a/tasks/{task_id:path}/cancel")
async def cancel_task(task_id: str, request: Request, p: Principal = Depends(require("chat:use"))):
    def _load():
        with SessionLocal() as db:
            t = db.get(A2ATask, task_id)
            if t is None or not _task_visible(p, t) or t.direction != "outbound":
                raise not_found("task")
            return t.agent_id, t.remote_task_id
    agent_id, remote = await run_in_threadpool(_load)
    try:
        res = await proxy(agent_id, _ctx(request, p), "cancel", "tasks/cancel", {"id": remote})
    except A2AError as exc:
        raise ApiError(400, "cancel_failed", exc.message) from exc
    return json.loads(res.body)


@router.get("/api/v1/gateway/a2a/cards", tags=["gateway"])
def card_cache(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """A2A 프록시·Agent Card 캐시 (SCR-H15)."""
    return {"items": [{"id": a.id, "name": a.name, "version": a.version, "card_hash": a.card_hash,
                       "last_fetched_at": a.last_fetched_at, "card_changed": a.card_changed,
                       "trust_tier": a.trust_tier, "upstream": a.url, "gateway_card": f"/a2a/{a.id}/.well-known/agent-card.json",
                       "protocol_versions": ap.card_info(a.card).get("protocol_versions") if a.card else []}
                      for a in db.scalars(select(A2AAgent).order_by(A2AAgent.id))]}


# ───────────────────────── 카탈로그 등록 핸들러 ─────────────────────────
@registry.register_handler("a2a_agent")
def _register_a2a(db: Session, item: CatalogItem, actor: str, options: dict) -> dict:
    card = item.item_metadata or {}
    errors = ap.validate_card(card)
    if errors:
        raise ApiError(422, "invalid_agent_card", "Agent Card 검증 실패", extra={"errors": errors})
    existing = db.get(A2AAgent, options.get("id") or item.name)
    if existing is None:
        a = create_agent_row(db, card, options.get("card_url") or card_url_for(ap.card_info(card)["url"]), actor,
                             agent_id=options.get("id") or slugify(item.name)[:60],
                             trust_tier=options.get("trust_tier", "untrusted"), catalog_item=item)
    else:
        a = existing
    db.flush()
    return {"id": a.id, "type": "a2a_agent", "gateway_path": f"/a2a/{a.id}"}
```
**분석 / Analysis (Level 2)**
- L670-675 Task 상세(가시성). L678-691 Task 취소(outbound 만, 프록시 경유). L694-701 카드 캐시(`gateway:read`).
- L705-719 카탈로그 등록 핸들러(`registry.register_handler("a2a_agent")`) — import 시 등록.
**짚어볼 점 / Watch-outs**
- L654·L670·L678 `task_id:path` 변환 — Task id 에 `:`(`{agent}:{remote}`)가 있어 path 변환이 필요하다. 경로 매칭이 다른 라우트(`/artifacts/...`)와 겹치지 않게 순서·구체성에 의존.
- L705-719 모듈 import 부수효과(핸들러 등록) — `app/main.py` 가 이 모듈을 import 하면 항상 실행된다.

## 파일 평가 / File Assessment
- 강점: (1) Task·시크릿 참조·push_config 를 응답에서 가림, 크로스테넌트 Task 격리. (2) 신뢰 등급 상향에 직무 분리(policy:write), 등록 승인 흐름(pending). (3) 파일 입력 base64·크기 검증.
- 위험: (1) [높음] `patch_agent` 가 `auth`(secret_ref·token_url)를 `a2a:write` 만으로 병합(시크릿 유출). (2) [중간] 클라이언트 헤더(agent·data_class·run_id) 신뢰, dry_run 등록의 내부 SSRF·푸시 토큰 단독 인증. (3) [낮음] 플랫폼 서버 `principals` 누수.
- 개선 권고(영향순): (1) `auth` 스키마 제한·`secret_ref` 금지·객체 인가/재승인. (2) agent/data_class 를 주체에서 도출, dry_run 에 사설 대역 차단, 푸시에 서명 기본 요구. (3) `principals` 캐시 정리.
- 재읽기 권고 레벨: 2.
