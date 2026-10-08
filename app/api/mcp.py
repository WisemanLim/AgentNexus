"""MCP 경로 API — 게이트웨이 MCP 엔드포인트(PRD 9.2 `POST /mcp/{server_id}`) + MCP 서버 관리 (SCR-F01~F06·H03·H14·J06).

요청 헤더: Mcp-Session-Id · MCP-Protocol-Version · X-AgentNexus-Agent · X-AgentNexus-Data-Class ·
X-AgentNexus-Approval(승인 id) · X-AgentNexus-Session/Run/Step.
"""
import asyncio
import json
import re
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import security
from app.core.auth import ROLE_PERMS, Principal, authenticate_token, bearer, require, role_perms
from app.core.config import settings
from app.core.context import current_trace_id
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.core.ids import new_id
from app.db import SessionLocal, get_db
from app.models import McpServer, McpSession, Secret, TokenExchangePolicy, ToolRule, VirtualServer
from app.services import audit, mcp_consent, mcp_modern, mcp_tasks, mcp_template, policy, registry, secrets
from app.services import mcp_gateway as gw
from app.services.mcp_gateway import RpcContext, RpcError

router = APIRouter(tags=["mcp"])


# ───────────────────────── 게이트웨이 MCP 엔드포인트 ─────────────────────────
def _mcp_auth(request: Request, server_id: str) -> Principal:
    token = bearer(request)
    prm = f"{settings.public_base_url.rstrip('/')}/.well-known/oauth-protected-resource/mcp/{server_id}"
    if not token:
        raise ApiError(401, "unauthorized", "인증이 필요합니다.", headers={
            "WWW-Authenticate": f'Bearer resource_metadata="{prm}"'})
    with SessionLocal() as db:
        p = authenticate_token(db, token)
    if not p.has("chat:use"):
        raise ApiError(403, "forbidden", "권한 부족: chat:use")
    return p


@router.get("/.well-known/oauth-protected-resource/mcp/{server_id}")
def gateway_prm(server_id: str):
    """RFC 9728 — 게이트웨이 MCP 엔드포인트 자체의 보호 자원 메타데이터 (외부 MCP 클라이언트용)."""
    return {"resource": f"{settings.public_base_url.rstrip('/')}/mcp/{server_id}",
            "authorization_servers": [security.issuer()], "bearer_methods_supported": ["header"],
            "scopes_supported": ["openid", "profile"]}


def _ctx(request: Request, p: Principal, params: dict) -> RpcContext:
    h = request.headers
    meta = (params or {}).get("_meta") or {}
    caller = policy.caller_context(None, p, h)  # 신고 헤더는 서버가 판정 (D17-①)
    if caller["data_class"] not in policy.DATA_CLASSES:  # 모르는 등급은 정책 조건에 맞지 않아 통제를 피한다 (D19-④)
        raise RpcError(gw.INVALID_PARAMS, f"X-AgentNexus-Data-Class 는 {policy.DATA_CLASSES} 중 하나여야 합니다.")
    return RpcContext(p, approval_id=h.get("x-agentnexus-approval") or meta.get("io.agentnexus/approval"), **caller)


async def dispatch(server_id: str, ctx: RpcContext, method: str, params: dict) -> dict:
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": await gw.list_tools(server_id, ctx)}
    if method == "tools/call":
        if not isinstance(params.get("name"), str):
            raise RpcError(gw.INVALID_PARAMS, "params.name 이 필요합니다.")
        return await gw.call_tool(server_id, ctx, params["name"], params.get("arguments") or {})
    if method in ("resources/list", "resources/templates/list", "resources/read", "prompts/list", "prompts/get"):
        return await gw.generic(server_id, ctx, method, params)
    if method == "logging/setLevel":
        return {}
    raise RpcError(gw.NOT_FOUND, f"Method not found: {method}")


@router.post("/mcp/{server_id}")
async def mcp_endpoint(server_id: str, request: Request):
    p = await run_in_threadpool(_mcp_auth, request, server_id)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(RpcError(gw.PARSE, "Parse error").body(None), status_code=400)
    if isinstance(body, list):
        return JSONResponse(RpcError(gw.INVALID_REQ, "JSON-RPC batch 는 지원하지 않습니다 (MCP 2025-06-18+).")
                            .body(None), status_code=400)
    if isinstance(body, dict) and "method" not in body and "id" in body and ("result" in body or "error" in body):
        # 중계한 서버→클라이언트 요청(sampling·elicitation)에 대한 하류 클라이언트의 응답
        sid = request.headers.get("mcp-session-id")
        if sid and await run_in_threadpool(gw.check_session, sid, server_id, p) \
                and await run_in_threadpool(mcp_consent.answer, sid, body):
            return Response(status_code=202)
        return JSONResponse(RpcError(gw.INVALID_REQ, "대기 중인 중계 요청이 없습니다.").body(body.get("id")),
                            status_code=404)
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
        return JSONResponse(RpcError(gw.INVALID_REQ, "Invalid Request").body(body.get("id") if isinstance(body, dict)
                                                                             else None), status_code=400)
    rid, method, params = body.get("id"), body["method"], body.get("params") or {}
    if "id" not in body:  # notification
        return Response(status_code=202)
    version = mcp_modern.requested_version(params)
    if (version is not None and method != "initialize") or request.headers.get("mcp-protocol-version") in mcp_modern.MODERN_VERSIONS:
        return await _modern(server_id, request, p, rid, method, params, version)
    headers = {"MCP-Protocol-Version": request.headers.get("mcp-protocol-version") or gw.MCP_PROTOCOL_VERSION}
    try:
        if method == "initialize":
            await run_in_threadpool(gw._load_targets, server_id, p)
            sid, result = await run_in_threadpool(gw.open_session, server_id, p, params)
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result},
                                headers={"Mcp-Session-Id": sid, "MCP-Protocol-Version": result["protocolVersion"]})
        sid = request.headers.get("mcp-session-id")
        if sid and not await run_in_threadpool(gw.check_session, sid, server_id, p):
            return JSONResponse(RpcError(gw.INVALID_REQ, "Session not found — 다시 initialize 하세요.").body(rid),
                                status_code=404)
        ctx = await run_in_threadpool(_ctx, request, p, params)  # 실행 소유·기본 등급 조회·감사 — 루프 밖
        caps = await run_in_threadpool(gw.session_capabilities, sid) if sid else {}
        token = mcp_modern.progress_token(params)
        if method in RELAY_METHODS and "text/event-stream" in request.headers.get("accept", "") \
                and (({"sampling", "elicitation"} & set(caps)) or token is not None):
            return _relay_stream(server_id, sid, caps, ctx, rid, method, params, headers, token)
        result = await dispatch(server_id, ctx, method, params)
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result}, headers=headers)
    except RpcError as exc:
        if exc.data.get("retry_after"):
            headers = {**(headers or {}), "Retry-After": str(exc.data["retry_after"])}
        return JSONResponse(exc.body(rid), status_code=exc.http_status, headers=headers)


async def _modern(server_id: str, request: Request, p: Principal, rid, method: str, params: dict, version: str | None):
    """MCP 2026-07-28 (stateless) — 세션 없이 요청마다 판정, MRTR 로 서버 요청을 결과에 실어 보낸다 (D13-④)."""
    hdr = {"MCP-Protocol-Version": version or mcp_modern.MODERN_VERSIONS[0]}
    try:
        if version is None:
            raise mcp_modern.ModernError(mcp_modern.HEADER_MISMATCH, "Header mismatch: _meta 에 io.modelcontextprotocol/protocolVersion 이 없습니다.")
        mcp_modern.validate(request.headers, method, params, version)
        if method == "server/discover":
            await run_in_threadpool(gw._load_targets, server_id, p)
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": mcp_modern.discover()}, headers=hdr)
        if method == "subscriptions/listen":
            return await _listen(server_id, request, p, rid, params, hdr)
        if method in mcp_modern.REMOVED:
            raise mcp_modern.ModernError(gw.NOT_FOUND, f"Method not found: {method}", http_status=404)
        if method in mcp_tasks.METHODS:  # Tasks 확장 — 확장을 선언한 요청만, 주체·서버에 묶인 작업만
            if not mcp_tasks.wants(params):
                raise mcp_modern.ModernError(mcp_tasks.MISSING_CAPABILITY,
                                             f"Missing required client capability: {mcp_tasks.EXT}")
            await run_in_threadpool(gw._load_targets, server_id, p)
            out = await mcp_tasks.handle(server_id, p, method, params)
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": mcp_modern.complete(method, out)}, headers=hdr)
        if method == "tools/call":  # x-mcp-header ↔ Mcp-Param-* (호출 전, 일반 JSON 400)
            schema = await run_in_threadpool(gw.tool_schema, server_id, p, params.get("name"))
            mcp_modern.check_params(schema, params.get("arguments"), request.headers)
        ctx = await run_in_threadpool(_ctx, request, p, params)  # 실행 소유·기본 등급 조회·감사 — 루프 밖
        if method in mcp_modern.MRTR_METHODS:
            if method == "tools/call" and mcp_tasks.wants(params) and not params.get("requestState"):
                out = await mcp_tasks.start(server_id, ctx, params, dispatch)  # 오래 걸리면 CreateTaskResult
                if "__final__" in out:
                    return JSONResponse(_modern_final(rid, method, out), headers=hdr)
                out["_meta"] = {mcp_modern.K_SERVER: mcp_modern.SERVER_INFO}
                return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": out}, headers=hdr)
            token = mcp_modern.progress_token(params)
            if token is not None and "text/event-stream" in request.headers.get("accept", ""):
                return _modern_progress_stream(server_id, ctx, rid, method, params, token, hdr)
            out = await (mcp_modern.resume(server_id, ctx, method, params, rid) if params.get("requestState")
                         else mcp_modern.start(server_id, ctx, method, params, rid, dispatch))
            return JSONResponse(_modern_final(rid, method, out), headers=hdr)
        result = await dispatch(server_id, ctx, method, {k: v for k, v in params.items() if k != "_meta"})
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": mcp_modern.complete(method, result)}, headers=hdr)
    except mcp_modern.ModernError as exc:
        return JSONResponse(exc.body(rid), status_code=exc.http_status, headers=hdr)
    except RpcError as exc:  # modern: 모르는 메서드는 404 (Streamable HTTP 2026-07-28)
        status = 404 if exc.code == gw.NOT_FOUND and exc.http_status == 200 else exc.http_status
        return JSONResponse(exc.body(rid), status_code=status, headers=hdr)


def _modern_final(rid, method: str, out: dict) -> dict:
    if "__final__" not in out:
        return {"jsonrpc": "2.0", "id": rid, "result": out}
    fin = out["__final__"] or {}
    if "error" in fin:
        return {"jsonrpc": "2.0", "id": rid, "error": fin["error"]}
    return {"jsonrpc": "2.0", "id": rid, "result": mcp_modern.complete(method, fin.get("result") or {})}


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _modern_progress_stream(server_id: str, ctx: RpcContext, rid, method: str, params: dict, token,
                            hdr: dict) -> StreamingResponse:
    """`_meta.progressToken` + SSE 수락 → 요청 단위 SSE: notifications/progress … → 결과(최종 또는 InputRequiredResult)."""
    queue: asyncio.Queue = asyncio.Queue()
    waiter = asyncio.create_task(mcp_modern.resume(server_id, ctx, method, params, rid, progress=queue)
                                 if params.get("requestState") else
                                 mcp_modern.start(server_id, ctx, method, params, rid, dispatch, progress=queue))

    relay = mcp_modern.ProgressRelay(token)

    async def events():
        try:
            while True:
                getter = asyncio.ensure_future(queue.get())
                await asyncio.wait({waiter, getter}, return_when=asyncio.FIRST_COMPLETED)
                if getter.done():
                    if note := relay.note(getter.result()):
                        yield mcp_modern.sse(note)
                    continue
                getter.cancel()
                while not queue.empty():
                    if note := relay.note(queue.get_nowait()):
                        yield mcp_modern.sse(note)
                try:
                    msg = _modern_final(rid, method, waiter.result())
                except (mcp_modern.ModernError, RpcError) as exc:
                    msg = exc.body(rid)
                yield mcp_modern.sse(msg)
                return
        finally:
            if not waiter.done():
                waiter.cancel()
            mcp_modern.detach(ctx.state_id, queue)
    return StreamingResponse(events(), media_type="text/event-stream", headers={**hdr, **SSE_HEADERS})


async def _listen(server_id: str, request: Request, p: Principal, rid, params: dict, hdr: dict):
    """subscriptions/listen — 승인(acknowledged) 후 목록 변경 알림을 흘린다. 스트림을 닫으면 구독 취소."""
    if "text/event-stream" not in request.headers.get("accept", ""):
        return Response(status_code=406)  # listen 응답은 언제나 알림 스트림
    honored, uris, task_ids = mcp_modern.listen_filter(params)
    await run_in_threadpool(gw._load_targets, server_id, p)
    holder = await run_in_threadpool(mcp_modern.listen_acquire, p.sub)  # 워커 전체 합 (D18-② · 보유자 단위 D20-④)
    if holder is None:
        raise RpcError(gw.RATE_LIMITED, f"동시 구독 한도({mcp_modern.MAX_LISTENS}) 초과", {"limit": mcp_modern.MAX_LISTENS},
                       http_status=429)
    try:
        return await _listen_open(server_id, p, rid, params, hdr, request, honored, uris, task_ids, holder)
    except BaseException:  # 스트림을 열기 전에 실패하면 확보한 구독을 돌려준다(열린 뒤에는 listen_events 가 돌려준다)
        mcp_modern.listen_release(p.sub, holder)  # 동기 — 취소 중에도 반납
        raise


async def _listen_open(server_id: str, p: Principal, rid, params: dict, hdr: dict, request: Request, honored: dict,
                       uris: list, task_ids: list, holder: str | None = None):
    ctx = await run_in_threadpool(_ctx, request, p, params)  # 실행 소유·기본 등급 조회·감사 — 루프 밖
    readable = []
    for uri in uris:  # 지금 읽을 수 있는 리소스만 승인 (권한·존재 확인)
        try:
            await gw.generic(server_id, ctx, "resources/read", {"uri": uri})
            readable.append(uri)
        except RpcError:
            continue
    if readable:
        honored["resourceSubscriptions"] = readable
    if task_ids and (mine := await run_in_threadpool(mcp_tasks.owned, server_id, p.sub, task_ids)):
        honored["taskIds"] = mine  # 이 주체의 작업만 — 남의 taskId 는 존재도 알리지 않는다

    def _check():
        with SessionLocal() as db:
            authenticate_token(db, p.raw_token)

    async def alive() -> bool:
        if not p.raw_token:
            return True
        try:
            await run_in_threadpool(_check)
            return True
        except ApiError:
            return False
    await run_in_threadpool(lambda: audit.append("mcp.subscribe", actor=p.sub, actor_kind=p.kind, target=server_id,
                                                 data={"subscription_id": rid, "notifications": sorted(honored),
                                                       "resources": len(readable)}))
    return StreamingResponse(mcp_modern.listen_events(server_id, ctx, rid, honored, alive, holder),
                             media_type="text/event-stream", headers={**hdr, **SSE_HEADERS})


RELAY_METHODS = {"tools/call", "resources/read", "prompts/get"}


def _relay_stream(server_id: str, sid: str, caps: dict, ctx: RpcContext, rid, method: str, params: dict,
                  headers: dict, token=None) -> StreamingResponse:
    """하류 클라이언트가 sampling·elicitation 능력을 선언하거나 progressToken 을 주고 SSE 로 호출 →
    업스트림의 서버 요청·progress 를 같은 스트림으로 중계.

    스트림: (0개 이상의 서버→클라이언트 요청·progress 알림) → 원래 요청의 결과. 클라이언트는 같은 세션으로 응답을 POST 한다.
    """
    queue: asyncio.Queue = asyncio.Queue()
    ctx.relay = mcp_consent.Relay(sid, server_id, caps, queue)
    if token is not None:
        relay = mcp_modern.ProgressRelay(token)

        async def _progress(p: dict) -> None:
            if note := relay.note(p):
                await queue.put(note)
        ctx.progress = _progress

    async def work():
        try:
            result = await dispatch(server_id, ctx, method, params)
            await queue.put({"jsonrpc": "2.0", "id": rid, "result": result})
        except RpcError as exc:
            await queue.put(exc.body(rid))
        except Exception as exc:  # noqa: BLE001 — 스트림 안에서 JSON-RPC 오류로 알림
            await queue.put(RpcError(gw.INTERNAL, f"{type(exc).__name__}: {exc}").body(rid))
        finally:
            await queue.put(None)
    task = asyncio.create_task(work())

    async def events():
        try:
            while (msg := await queue.get()) is not None:
                yield f"event: message\ndata: {json.dumps(msg, ensure_ascii=False, default=str)}\n\n"
        finally:
            if not task.done():
                task.cancel()
    return StreamingResponse(events(), media_type="text/event-stream", headers={**headers, **SSE_HEADERS})


@router.get("/mcp/{server_id}")
def mcp_get_stream(server_id: str):
    """독립 SSE 스트림(GET)은 제공하지 않는다(사양상 405 허용) — 서버 요청은 POST 응답 스트림으로 중계한다."""
    return Response(status_code=405, headers={"Allow": "POST, DELETE"})


@router.delete("/mcp/{server_id}")
async def mcp_close(server_id: str, request: Request):
    p = await run_in_threadpool(_mcp_auth, request, server_id)
    sid = request.headers.get("mcp-session-id")
    if not sid or not await run_in_threadpool(gw.close_session, sid, p):
        return Response(status_code=404)
    return Response(status_code=200)


# ───────────────────────── MCP 서버 관리 (SCR-F01~F06) ─────────────────────────
def server_out(s: McpServer, detail: bool = False, db: Session | None = None) -> dict:
    out = {"id": s.id, "name": s.name, "transport": s.transport, "endpoint": s.endpoint, "status": s.status,
           "health": s.health, "health_detail": s.health_detail, "health_checked_at": s.health_checked_at,
           "tools_count": len(s.tools or []), "builtin": s.builtin, "catalog_item_id": s.catalog_item_id,
           "gateway_path": f"/mcp/{s.id}", "auth_type": (s.auth or {}).get("type", "none"),
           "authorization_server": (s.auth or {}).get("authorization_server"), "owner": s.owner,
           "sampling_policy": s.sampling_policy or "ask", "version": (s.server_json or {}).get("version"),
           "tools_refreshed_at": s.tools_refreshed_at, "protocol": s.protocol or "auto",
           "upstream_protocol": s.upstream_protocol}
    if detail and db is not None:
        rules = {r.tool: r for r in db.scalars(select(ToolRule).where(ToolRule.server_id == s.id))}
        out["tools"] = [{**t, "rule": ({"allowed": rules[t["name"]].allowed, "risk": rules[t["name"]].risk,
                                        "approval_required": rules[t["name"]].approval_required,
                                        "approver": rules[t["name"]].approver, "args_schema": rules[t["name"]].args_schema,
                                        "sanitize": rules[t["name"]].sanitize,
                                        "description": rules[t["name"]].description} if t.get("name") in rules else None)}
                        for t in s.tools or []]
        out["resources"], out["prompts"], out["server_json"] = s.resources, s.prompts, s.server_json
        out["auth"] = {k: v for k, v in (s.auth or {}).items() if k != "secret_ref"}
        pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == f"mcp:{s.id}")).first()
        out["token_exchange"] = {"allowed_scopes": pol.allowed_scopes, "default_scopes": pol.default_scopes,
                                 "ttl_seconds": pol.ttl_seconds} if pol else None
    return out


@router.get("/api/v1/mcp-servers")
def list_servers(_: Principal = Depends(require("mcp:read")), db: Session = Depends(get_db)):
    return {"items": [server_out(s) for s in db.scalars(select(McpServer).order_by(McpServer.id))]}


@router.get("/api/v1/mcp-servers/templates/custom")
def custom_template(name: str, title: str | None = None, description: str | None = None,
                    public_url: str | None = None, include: list[str] | None = Query(None),
                    _: Principal = Depends(require("mcp:write"))):
    if include == ["none"]:  # 선택 항목 없이 (SCR-F05 ②) — include 를 비우면 전부
        include = []
    if include and any(x not in mcp_template.OPTIONAL for x in include):
        raise bad_request(f"include 는 {mcp_template.OPTIONAL} 중에서 (없음 = none)")
    try:
        files = mcp_template.render(name, title, description, public_url, include=include)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    return {"name": name, "files": files, "next": ["nexus mcp scaffold", "uv sync && uv run uvicorn server:app",
                                                    "nexus registry publish server.json"]}


@router.get("/api/v1/mcp-servers/{server_id}")
def get_server(server_id: str, _: Principal = Depends(require("mcp:read")), db: Session = Depends(get_db)):
    s = db.get(McpServer, server_id)
    if s is None:
        raise not_found("mcp server")
    return server_out(s, detail=True, db=db)


class ServerPatch(BaseModel):
    endpoint: str | None = None
    status: str | None = Field(None, pattern="^(active|disabled)$")
    auth: dict | None = None
    secret: str | None = Field(None, repr=False, description="bearer/api_key/서비스 계정 시크릿 (암호화 저장)")
    protocol: str | None = Field(None, pattern="^(auto|legacy|2026-07-28)$",
                                 description="업스트림 세대 — auto(server/discover 로 발견) · legacy(initialize·세션) · 2026-07-28")


@router.patch("/api/v1/mcp-servers/{server_id}")
def patch_server(server_id: str, body: ServerPatch, p: Principal = Depends(require("mcp:write")),
                 db: Session = Depends(get_db)):
    """SaaS 참조 서버 설정 (SCR-F03) — 엔드포인트·인증 방식(oauth2 위임 / service / bearer / api_key)·시크릿."""
    s = db.get(McpServer, server_id)
    if s is None:
        raise not_found("mcp server")
    secrets.check_refs(p, body.auth, f"mcp/{s.id}/")
    secrets.check_destination(p, {**(s.auth or {}), **(body.auth or {})}, body.endpoint not in (None, s.endpoint)
                              or secrets.destination_changed(s.auth, body.auth), bool(body.secret))
    if body.endpoint is not None:
        s.endpoint = body.endpoint
    if body.status is not None:
        s.status = body.status
    if body.auth is not None:
        s.auth = {**(s.auth or {}), **body.auth}
    if body.protocol is not None:
        s.protocol = body.protocol
    if body.secret:
        ref = f"mcp/{s.id}/backend"
        secrets.put(db, ref, body.secret, kind="token", description=f"{s.id} 백엔드 자격 증명")
        s.auth = {**(s.auth or {}), "secret_ref": ref}
    db.commit()
    gw.clear_caches()
    audit.append("mcp.server.update", actor=p.sub, target=s.id,
                 data={"endpoint": body.endpoint, "status": body.status, "auth": body.auth, "secret": bool(body.secret),
                       "protocol": body.protocol})
    return server_out(s, detail=True, db=db)


@router.post("/api/v1/mcp-servers/{server_id}/test-config")
async def test_config(server_id: str, body: ServerPatch, p: Principal = Depends(require("mcp:write"))):
    """적용 전 시험 (SCR-F03) — 후보 엔드포인트·인증으로 연결·발견·tools/list·스코프 점검. 저장하지 않는다."""
    secrets.check_refs(p, body.auth, f"mcp/{server_id}/")

    def _current():
        with SessionLocal() as db:
            s = db.get(McpServer, server_id)
            return (s.auth or {}, s.endpoint) if s else ({}, None)
    cur_auth, cur_endpoint = await run_in_threadpool(_current)
    secrets.check_destination(p, {**cur_auth, **(body.auth or {})}, body.endpoint not in (None, cur_endpoint)
                              or secrets.destination_changed(cur_auth, body.auth), bool(body.secret))
    try:
        return await gw.test_config(server_id, p, body.model_dump(exclude_none=True))
    except RpcError as exc:
        if exc.code == gw.NOT_FOUND:
            raise not_found("mcp server") from exc
        raise bad_request(exc.message) from exc


@router.post("/api/v1/mcp-servers/{server_id}/refresh")
async def refresh_server(server_id: str, p: Principal = Depends(require("mcp:write"))):
    return await gw.refresh_tools(server_id, p)


class TestPrincipalIn(BaseModel):
    roles: list[str] = Field(default_factory=lambda: ["user"], min_length=1, max_length=6)
    team: str | None = Field(None, max_length=120)


class InspectIn(BaseModel):
    method: str = "tools/list"
    params: dict = {}
    data_class: str = "internal"
    approval_id: str | None = None
    test_principal: TestPrincipalIn | None = Field(None, description="시험 전용 가상 사용자 (SCR-F06 ⑤) — 역할·팀만, 실제 사용자 대리 없음")


def _test_principal(p: Principal, t: TestPrincipalIn) -> Principal:
    """가상 시험 주체 — 호출자보다 넓은 역할은 줄 수 없다(관리자 제외). 감사 actor 는 실제 호출자."""
    unknown = [r for r in t.roles if r not in ROLE_PERMS]
    if unknown:
        raise bad_request(f"알 수 없는 역할: {', '.join(unknown)}", code="unknown_role")
    if "*" not in p.permissions():
        mine = p.permissions()
        wider = [r for r in t.roles if not role_perms(r) <= mine]
        if wider:
            raise ApiError(403, "test_roles_exceed", f"내 권한보다 넓은 역할로 시험할 수 없습니다: {', '.join(wider)}")
    return Principal(sub=f"test:{p.sub}", kind="test", name="시험 사용자", roles=list(t.roles), team=t.team, actor=p.sub)


def _write_tool(server_id: str, tool: str) -> bool:
    with SessionLocal() as db:
        r = db.scalars(select(ToolRule).where(ToolRule.server_id == server_id, ToolRule.tool == tool)).first()
        return bool(r and (r.risk != "low" or r.approval_required))


@router.post("/api/v1/mcp-servers/{server_id}/inspect")
async def inspect(server_id: str, body: InspectIn, p: Principal = Depends(require("mcp:read"))):
    """MCP 도구 시험 (SCR-F06 Inspector) — 게이트웨이 경로 그대로(정책·승인·소독 적용) 호출.
    test_principal 이면 가상 시험 주체로 판정·호출하고, 쓰기·위험 도구는 호출하지 않는 드라이런(정책 판정만)."""
    if body.test_principal is not None:
        tp = _test_principal(p, body.test_principal)
        tool = (body.params or {}).get("name")
        dry = body.method == "tools/call" and await run_in_threadpool(_write_tool, server_id, tool or "")

        def _audit(extra: dict):
            audit.append("mcp.inspect.test_principal", actor=p.sub, target=f"{server_id}.{tool or body.method}",
                         data={"roles": tp.roles, "team": tp.team, "method": body.method, **extra})
        if dry:
            # 드라이런도 실제 호출과 같은 서버 판정 등급으로 — 클라이언트 신고 원문을 그대로 쓰지 않는다 (D22-④)
            dry_dc = await run_in_threadpool(policy.declared_data_class, None, tp, body.data_class)
            if dry_dc not in policy.DATA_CLASSES:
                raise bad_request(f"data_class 는 {policy.DATA_CLASSES} 중 하나여야 합니다.", code="invalid_data_class")

            def _judge():
                from app.services import governance
                with SessionLocal() as db:
                    d = governance.check_tool(db, tp, server_id, tool, (body.params or {}).get("arguments") or {},
                                              data_class=dry_dc, create_pending=False)
                    return {"allow": d.allow, "code": d.code, "reason": d.reason, "risk": d.risk}
            dec = await run_in_threadpool(_judge)
            await run_in_threadpool(_audit, {"dry_run": True, "decision": dec["code"]})
            return {"request": {"method": body.method, "params": body.params}, "test_principal": {"sub": tp.sub, "roles": tp.roles, "team": tp.team},
                    "dry_run": True, "decision": dec, "result": None, "error": None, "latency_ms": 0.0, "trace_id": current_trace_id()}
        await run_in_threadpool(_audit, {"dry_run": False})
        p = tp
    dc = await run_in_threadpool(policy.declared_data_class, None, p, body.data_class)  # 신고 등급은 서버 판정 (D19-④)
    if dc not in policy.DATA_CLASSES:
        raise bad_request(f"data_class 는 {policy.DATA_CLASSES} 중 하나여야 합니다.", code="invalid_data_class")
    ctx = RpcContext(p, data_class=dc, approval_id=body.approval_id)
    t0 = time.perf_counter()
    try:
        result, error = await dispatch(server_id, ctx, body.method, body.params), None
    except RpcError as exc:
        result, error = None, {"code": exc.code, "message": exc.message, "data": exc.data}
    return {"request": {"method": body.method, "params": body.params}, "result": result, "error": error,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "trace_id": current_trace_id(),
            **({"test_principal": {"sub": p.sub, "roles": p.roles, "team": p.team}, "dry_run": False} if p.kind == "test" else {})}


class ServerCreate(BaseModel):
    server_json: dict
    namespace: str | None = None


async def _publish_register(p: Principal, doc: dict, namespace: str | None, source: str = "manual") -> dict:
    def _do():
        with SessionLocal() as db:
            item, _, _ = registry.publish(db, "mcp_server", str(doc.get("name", "")), str(doc.get("version", "")),
                                          doc, p.sub, source=source)
            if item.status == "pending":
                registry.approve(db, item, None, p.sub, "운영자 직접 등록")
            inst = registry.register(db, item, p.sub, {"namespace": namespace} if namespace else {})
            return inst
    inst = await run_in_threadpool(_do)
    inst["refresh"] = await gw.refresh_tools(inst["id"], p)
    return inst


@router.post("/api/v1/mcp-servers", status_code=201)
async def create_server(body: ServerCreate, p: Principal = Depends(require("catalog:approve", "mcp:write"))):
    """운영자 즉시 등록 — 게시 → 승인 → 등록 → 도구 조회·기본 규칙 (FR-W04 ≤ 60s)."""
    t0 = time.perf_counter()
    inst = await _publish_register(p, body.server_json, body.namespace)
    inst["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return inst


class FetchSpecIn(BaseModel):
    url: str = Field(pattern=r"^https?://", max_length=2000)


@router.post("/api/v1/mcp-servers/openapi/fetch")
def fetch_openapi(body: FetchSpecIn, _: Principal = Depends(require("mcp:write"))):
    """OpenAPI 문서 URL 가져오기 (SCR-F04 ① — 브라우저 CORS 회피). 게이트웨이 egress, 2MB 제한, JSON·YAML."""
    import yaml

    from app.core.http import sync_client
    try:
        with sync_client(timeout=10) as http:
            res = http.get(body.url)
    except Exception as exc:  # noqa: BLE001
        raise bad_request(f"가져오기 실패: {type(exc).__name__}", code="fetch_failed") from exc
    if res.status_code >= 400 or len(res.content) > 2_000_000:
        raise bad_request(f"가져오기 실패: HTTP {res.status_code}" if res.status_code >= 400 else "2MB 초과", code="fetch_failed")
    try:
        doc = yaml.safe_load(res.text)
    except yaml.YAMLError as exc:
        raise bad_request(f"JSON·YAML 이 아닙니다: {exc}", code="invalid_openapi") from exc
    if not isinstance(doc, dict) or not (doc.get("openapi") or doc.get("swagger")) or not isinstance(doc.get("paths"), dict):
        raise bad_request("OpenAPI 문서가 아닙니다 (openapi·paths 필요, MSG-004).", code="invalid_openapi")
    return {"openapi": doc, "url": body.url}


class OpenApiIn(BaseModel):
    namespace: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    title: str
    description: str = "Internal REST → MCP tools"
    openapi: dict = Field(description="OpenAPI 3 문서")
    base_url: str
    auth_header: str = "Authorization"
    secret: str | None = Field(None, repr=False)
    version: str = "0.1.0"
    operations: list[dict] = Field(default_factory=list, description="[{operationId, include, name, risk}] — 비우면 전부 (SCR-F04 ②)")
    identity: str = Field("service", pattern="^(service|obo)$",
                          description="service = 서비스 자격(시크릿) · obo = 호출 사용자 토큰을 api:{namespace} 용으로 교환해 주입 (SCR-F04 ③)")
    scopes: list[str] = Field(default_factory=lambda: ["api:call"], max_length=20)


def select_operations(doc: dict, ops: list[dict]) -> tuple[dict, dict[str, str]]:
    """노출할 operation 만 남기고 이름을 바꾼다. (새 문서, {도구 이름: 위험도})."""
    import copy
    want = {o.get("operationId"): o for o in ops if o.get("operationId")}
    out, risks = copy.deepcopy(doc), {}
    for path, item in list((out.get("paths") or {}).items()):
        for method, op in list(item.items()):
            if not isinstance(op, dict):
                continue
            if not op.get("operationId"):
                if want:  # 선택 목록이 있으면 식별할 수 없는 operation 은 도구로 노출하지 않는다 (D19-④)
                    del item[method]
                continue
            cfg = want.get(op["operationId"])
            if want and (cfg is None or cfg.get("include") is False):
                del item[method]
                continue
            if cfg and cfg.get("name"):
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", cfg["name"]):
                    raise bad_request(f"도구 이름 형식 오류: {cfg['name']}")
                op["operationId"] = cfg["name"]
            if cfg and cfg.get("risk") in ("low", "medium", "high"):
                risks[op["operationId"].replace(".", "_")] = cfg["risk"]
        if not item:
            del out["paths"][path]
    if not out.get("paths"):
        raise bad_request("노출할 operation 이 없습니다.")
    return out, risks


@router.post("/api/v1/mcp-servers/openapi", status_code=201)
async def create_from_openapi(body: OpenApiIn, p: Principal = Depends(require("catalog:approve", "mcp:write"))):
    """Internal API MCP Server 생성 (FR-S02, SCR-F04) — OpenAPI 의 각 operation 을 MCP 도구로 노출(FastMCP)."""
    if not str(body.openapi.get("openapi", "")).startswith("3"):
        raise bad_request("OpenAPI 3.x 문서가 필요합니다.")
    spec, risks = select_operations(body.openapi, body.operations) if body.operations else (body.openapi, {})
    ref = f"mcp/{body.namespace}/backend"

    def _free():  # 쓰기 전에 — 남의 namespace 의 교환 정책·시크릿을 만들거나 덮지 않게 (D21-⑤·D22-④)
        with SessionLocal() as db:
            if db.get(McpServer, body.namespace) is not None:
                raise conflict(f"namespace '{body.namespace}' 는 이미 사용 중입니다.", code="namespace_taken")
            if body.secret and body.identity == "service" and db.scalars(select(Secret).where(Secret.name == ref)).first():
                raise conflict(f"시크릿 '{ref}' 가 이미 있습니다 — 다른 namespace 를 쓰거나 시크릿을 먼저 정리하세요.",
                               code="secret_exists")
    await run_in_threadpool(_free)
    hosted = {"openapi": spec, "base_url": body.base_url, "auth_header": body.auth_header, "identity": body.identity}
    made = {"policy": False, "secret": False}  # 이번 요청이 만든 것만 실패 때 지운다 (65th)
    if body.identity == "obo":  # 사용자 위임 — 내부 API 는 사용자 신원(sub·팀·역할)을 받는다. 서비스 시크릿은 쓰지 않는다
        hosted["scopes"] = body.scopes

        def _pol():
            with SessionLocal() as db:
                aud = f"api:{body.namespace}"
                pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == aud)).first()
                if pol is None:
                    db.add(TokenExchangePolicy(id=new_id("txp"), audience=aud, audience_type="api", allowed_scopes=body.scopes,
                                               default_scopes=body.scopes, ttl_seconds=300, method="rfc8693"))
                    db.commit()
                    made["policy"] = True
        await run_in_threadpool(_pol)
    if body.secret and body.identity == "service":
        def _sec():
            from sqlalchemy.exc import IntegrityError
            with SessionLocal() as db:
                if db.scalars(select(Secret).where(Secret.name == ref)).first():  # 덮지 않는다 — 없을 때만 새로 (65th)
                    raise conflict(f"시크릿 '{ref}' 가 이미 있습니다.", code="secret_exists")
                try:
                    secrets.put(db, ref, body.secret, kind="token")
                    db.commit()
                except IntegrityError:  # 같은 namespace 동시 생성 — 진 쪽
                    db.rollback()
                    raise conflict(f"시크릿 '{ref}' 가 이미 있습니다.", code="secret_exists") from None
                made["secret"] = True
        await run_in_threadpool(_sec)
        hosted["secret_ref"] = ref
    ops = [op.get("operationId") for path in spec.get("paths", {}).values() for op in path.values()
           if isinstance(op, dict) and op.get("operationId")]
    doc = {"name": f"io.agentnexus.internal/{body.namespace}", "title": body.title[:100],
           "description": body.description[:100], "version": body.version,
           "packages": [{"registryType": "oci", "identifier": "agentnexus/hosted-openapi",
                         "transport": {"type": "streamable-http"}}],
           "_meta": {"hosted": hosted, "tools": ops, "license": "internal"}}

    def _mark():
        with SessionLocal() as db:
            s = db.get(McpServer, body.namespace)
            s.transport, s.endpoint = "hosted-openapi", None
            db.commit()
    t0 = time.perf_counter()

    def _do():
        with SessionLocal() as db:
            item, _, _ = registry.publish(db, "mcp_server", doc["name"], doc["version"], doc, p.sub, source="manual")
            if item.status == "pending":
                registry.approve(db, item, None, p.sub, "OpenAPI 서버 생성")
            return registry.register(db, item, p.sub, {"namespace": body.namespace})
    try:
        inst = await run_in_threadpool(_do)
    except Exception:
        def _drop():  # 게시·등록 실패 — 이번에 만든 시크릿·교환 정책만, 서버가 등록됐으면 건드리지 않음 (D22-④·65th)
            with SessionLocal() as db:
                if db.get(McpServer, body.namespace) is not None:
                    return
                if made["secret"]:
                    db.query(Secret).filter(Secret.name == ref).delete()
                if made["policy"]:
                    db.query(TokenExchangePolicy).filter(TokenExchangePolicy.audience == f"api:{body.namespace}").delete()
                db.commit()
        await run_in_threadpool(_drop)
        raise
    await run_in_threadpool(_mark)
    inst["transport"] = "hosted-openapi"
    inst["refresh"] = await gw.refresh_tools(body.namespace, p)
    if risks:  # 운영자가 정한 위험도 (고위험 = 사람 승인)
        def _risk():
            with SessionLocal() as db:
                for r in db.scalars(select(ToolRule).where(ToolRule.server_id == body.namespace)):
                    if r.tool in risks:
                        r.risk, r.approval_required = risks[r.tool], risks[r.tool] == "high"
                db.commit()
        await run_in_threadpool(_risk)
        inst["risks"] = risks
    inst["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return inst


# ───────────────────────── 게이트웨이 MCP 현황 (SCR-H03·H14) ─────────────────────────
@router.get("/api/v1/gateway/mcp/sessions", tags=["gateway"])
def mcp_sessions(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db),
                 kind: str | None = None, limit: int = 200):
    stmt = select(McpSession).order_by(McpSession.last_used_at.desc()).limit(limit)
    if kind:
        stmt = stmt.where(McpSession.kind == kind)
    return {"items": [{"id": s.id[:8] + "…", "sid": session_handle(s.id), "kind": s.kind, "server_id": s.server_id, "principal": s.principal_sub,
                       "protocol_version": s.protocol_version, "client": (s.client_info or {}).get("name"),
                       "upstream_session": bool(s.upstream_session_id), "created_at": s.created_at,
                       "last_used_at": s.last_used_at, "closed_at": s.closed_at} for s in db.scalars(stmt)]}


def session_handle(sid: str) -> str:
    """세션 ID 는 자격처럼 쓰이므로 목록에는 해시 핸들만 — 강제 종료(SCR-H14 ③)가 이 핸들로 찾는다."""
    import hashlib
    return hashlib.sha256(sid.encode()).hexdigest()[:24]


@router.get("/api/v1/gateway/mcp/tools", tags=["gateway"])
def mcp_tool_catalog(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db),
                     q: str | None = None):
    """도구 카탈로그·Discover 검색 (SCR-H14 ④) — 이름·설명."""
    rules = {(r.server_id, r.tool): r for r in db.scalars(select(ToolRule))}
    items = []
    for s in db.scalars(select(McpServer).order_by(McpServer.id)):
        for t in s.tools or []:
            if q and q.lower() not in f"{s.id}.{t.get('name')} {t.get('description') or ''}".lower():
                continue
            r = rules.get((s.id, t.get("name")))
            items.append({"qualified": f"{s.id}.{t.get('name')}", "server_id": s.id, "tool": t.get("name"),
                          "description": t.get("description"), "registered": r is not None,
                          "allowed": bool(r and r.allowed), "risk": r.risk if r else None,
                          "approval_required": bool(r and r.approval_required)})
    return {"aggregate_endpoint": "/mcp/_all", "items": items}


class VirtualIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    description: str | None = None
    members: list[dict] = Field(min_length=1, description="[{server_id, tools: [..] | null}]")
    collision_policy: str = Field("prefix", pattern="^(prefix|reject)$",
                                  description="prefix = server.tool 로 구분(기본) · reject = 같은 도구 이름이 있으면 생성 거부 (SCR-H03 ③)")


@router.get("/api/v1/gateway/virtual-servers", tags=["gateway"])
def list_virtual(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    return {"items": [{"id": v.id, "name": v.name, "description": v.description, "members": v.members,
                       "gateway_path": f"/mcp/{v.id}"} for v in db.scalars(select(VirtualServer))]}


@router.post("/api/v1/gateway/virtual-servers", status_code=201, tags=["gateway"])
def create_virtual(body: VirtualIn, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    if body.id == gw.ALL or db.get(McpServer, body.id) or db.get(VirtualServer, body.id):
        raise conflict("id 가 기존 MCP 서버·가상 서버와 겹칩니다.")
    seen: dict[str, str] = {}
    clashes = []
    for m in body.members:
        srv = db.get(McpServer, m.get("server_id"))
        if srv is None:
            raise bad_request(f"알 수 없는 server_id: {m.get('server_id')}")
        for t in (srv.tools or []):
            name = t.get("name")
            if m.get("tools") is not None and name not in m["tools"]:
                continue
            if name in seen and seen[name] != srv.id:
                clashes.append(f"{name} ({seen[name]}·{srv.id})")
            seen.setdefault(name, srv.id)
    if clashes and body.collision_policy == "reject":
        raise conflict(f"도구 이름 충돌 {len(clashes)}건: {', '.join(clashes[:5])} (MSG-004)", code="tool_collision")
    v = VirtualServer(**body.model_dump(exclude={"collision_policy"}))
    db.add(v)
    db.commit()
    audit.append("gateway.virtual_server.create", actor=p.sub, target=v.id, data=body.model_dump())
    return {"id": v.id, "gateway_path": f"/mcp/{v.id}", "members": v.members, "collisions": clashes}


@router.delete("/api/v1/gateway/virtual-servers/{vid}", tags=["gateway"])
def delete_virtual(vid: str, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    v = db.get(VirtualServer, vid)
    if v is None:
        raise not_found("virtual server")
    db.delete(v)
    db.commit()
    audit.append("gateway.virtual_server.delete", actor=p.sub, target=vid)
    return {"deleted": vid}


# ───────────────────────── MCP Authorization 현황 (SCR-J06) ─────────────────────────
@router.get("/api/v1/identity/mcp-auth", tags=["identity"])
def mcp_auth_status(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    out = []
    for s in db.scalars(select(McpServer).order_by(McpServer.id)):
        a = s.auth or {}
        pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == f"mcp:{s.id}")).first()
        out.append({"server_id": s.id, "auth_type": a.get("type", "none"), "resource_metadata": a.get("resource_metadata"),
                    "authorization_server": a.get("authorization_server"),
                    "scopes_supported": a.get("scopes_supported", []), "tool_scopes": a.get("tool_scopes", {}),
                    "mode": a.get("mode", "exchange" if a.get("type") == "oauth2" else None),
                    "token_exchange_policy": bool(pol), "credential_configured": bool(a.get("secret_ref"))})
    return {"items": out}
