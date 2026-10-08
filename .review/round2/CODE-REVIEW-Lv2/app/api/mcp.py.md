> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [api_Abstract.md](api_Abstract.md) · 1차 리뷰: [mcp.py.md](../../../../CODE-REVIEW-Lv2/app/api/mcp.py.md)

### 25. app/api/mcp.py
**파일 요약 / File Summary**
- 역할 / Role: MCP 경로 API — (1) 게이트웨이 MCP 엔드포인트 `POST/GET/DELETE /mcp/{server_id}`(legacy 세션·SSE 중계·progress, modern 2026-07-28 stateless·MRTR·Tasks 확장·`subscriptions/listen`), 게이트웨이 PRM(RFC 9728), (2) MCP 서버 관리(목록·상세·설정 PATCH·적용 전 시험·갱신·Inspector·즉시 등록·OpenAPI 가져오기/생성·커스텀 템플릿), (3) 게이트웨이 현황(세션·도구 카탈로그·가상 서버), (4) MCP 인가 현황.
- 진입점 거리 / Distance: 1 — `app/main.py:247` `app.include_router(mcp_api.router)` (`gateway` 구성요소일 때만).
- LOC: 778 (현재 `wc -l`) · 1차 760 → +18 · `max_lines_per_file`(600) 초과 → **논리 블록 단위로 분석(모든 블록 커버)**. 40줄 초과 블록은 핵심 줄 원문 + `# … (Lx-y 생략 — 요약)` 표시.
- 주요 export: `router`, `dispatch`(Tasks·MRTR 가 실행 함수로 재사용), `server_out`, `select_operations`, `session_handle`, `RELAY_METHODS`, `SSE_HEADERS`
- 의존(내부): `app.core.{security,auth,config,context,errors,ids,http(지연)}`, `app.db`, `app.models.{McpServer,McpSession,TokenExchangePolicy,ToolRule,VirtualServer}`, `app.services.{audit,mcp_consent,mcp_modern,mcp_tasks,mcp_template,policy,registry,secrets,mcp_gateway,governance(지연)}`
- 외부 패키지: fastapi, pydantic, sqlalchemy, starlette(`run_in_threadpool`·`StreamingResponse`), pyyaml(지연 import)
- 부수효과 / Side effects: 업스트림 MCP HTTP 호출(도구·리소스·인증 발견·토큰 교환), 사용자 지정 URL GET(OpenAPI fetch), DB 쓰기(세션·서버 설정·시크릿·도구 규칙·가상 서버·교환 정책), 감사, 게이트웨이 캐시 초기화(`gw.clear_caches`), 전역 구독 카운터(`ratelimit.backend().counter_add`).
- 동시성 / Concurrency: async 핸들러 + `run_in_threadpool`(동기 인증·DB). 스트림 3종(legacy 중계 `_relay_stream`·modern progress `_modern_progress_stream`·listen)이 `asyncio.Queue` + 백그라운드 `asyncio.Task` 를 쓴다. 동기 `def` 관리 엔드포인트는 FastAPI 가 스레드풀에서 실행.
- 테스트 커버리지 흔적: `test/test_idor_matrix.py:157-166`(C48-03 남의 `Mcp-Session-Id` 로 요청·DELETE 불가), `test/test_review_fixes.py:246-265`(C39-12 남의 `secret_ref` 403 · `endpoint` 변경 403), `test/test_review_fixes.py:74-80`(fetch 메타데이터 차단), `test/test_ops.py:268-278`(OpenAPI 생성·operation 선택·위험도), `test/test_ops.py:434-437`(fetch·`file://` 422), `test/test_design_gaps.py:181-197`(Inspector 가상 주체 — 팀 `team-e24` 임의 지정 허용을 단언), `test/test_caller_trust.py:38`·`:72`·`:90`(caller_context: agent·데이터 등급·run), `test/test_mcp_modern_ext.py:181-188`·`test/test_multiworker.py:25-35`(listen 한도 429·전역 카운터). **시험 없음**: `auth.authorization_server`·`token_endpoint`·`resource_metadata` 변경, Inspector 의 `data_class`·`_all`/가상 서버 경로, fetch 응답 크기·YAML 별칭 폭탄.
- 2차 변경 요약 / Changes since 1st review: (1) `_ctx` 가 클라이언트 헤더를 그대로 쓰지 않고 `policy.caller_context` 로 서버 판정(L58-59 — agent 는 기계 주체만, 데이터 등급은 `data:declare`, run 은 본인 소유만; Epic 40 · D17-①, `test/impl/40th/scenario.md:1-12`). (2) `subscriptions/listen` 한도를 워커 로컬 `LISTENS` 대신 전역 카운터 `listen_acquire/release` 로(L231-238), 스트림 열기 전 실패 시 반납(`except BaseException`), 본문을 `_listen_open`(L241-272)으로 분리(Epic 45 · D18-②). (3) `patch_server`·`test_config` 에 시크릿 참조·전송처 검사 `secrets.check_refs`·`check_destination` 추가(L403-405·L429-436, Epic 39 C39-12, `test/impl/39th/scenario.md:28`). (4) `policy` import 추가(L26).

## 블록 리뷰 / Block Walk (Level 2)

```python path=app/api/mcp.py lines=1-31
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
from app.models import McpServer, McpSession, TokenExchangePolicy, ToolRule, VirtualServer
from app.services import audit, mcp_consent, mcp_modern, mcp_tasks, mcp_template, policy, registry, secrets
from app.services import mcp_gateway as gw
from app.services.mcp_gateway import RpcContext, RpcError

router = APIRouter(tags=["mcp"])

```
**분석 / Analysis (Level 2)**
- L1-5: 이 파일이 받는 요청 헤더 계약. `X-AgentNexus-*` 는 "호출자 신고값"이며, 2차부터는 L58 에서 서버가 판정한 값만 쓴다(신고 그대로 믿지 않는다는 점이 이 파일의 신뢰 경계 핵심).
- L16: `run_in_threadpool` — 동기 SQLAlchemy 세션·동기 인증을 AnyIO 워커 스레드로 보내 이벤트 루프를 막지 않게 한다. async 핸들러 안에서 이것 없이 `SessionLocal()` 을 쓰면 그 워커의 모든 SSE 가 멈춘다(흔한 실수).
- L26: 2차 신규 `policy` — `caller_context` 용.
- L27-28: 게이트웨이 서비스를 `gw` 별칭으로. `RpcError` 는 JSON-RPC 오류 코드·HTTP 상태를 함께 들고 다니는 예외(`exc.body(rid)` 로 JSON-RPC 응답 본문 생성).

```python path=app/api/mcp.py lines=32-60

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
    return RpcContext(p, approval_id=h.get("x-agentnexus-approval") or meta.get("io.agentnexus/approval"), **caller)
```
**분석 / Analysis (Level 2)**
- L34-44 `_mcp_auth`: Bearer 없으면 401 + `WWW-Authenticate: Bearer resource_metadata="…"`(RFC 9728 — MCP 클라이언트가 이 헤더로 인가 서버를 발견한다). 토큰이 있으면 `authenticate_token`(JWT·PAT·클라이언트 키 해석, `app/core/auth.py`) 후 `chat:use` 필요. 동기 함수라 호출부가 `run_in_threadpool` 로 감싼다(L80·L325).
- L47-52 `gateway_prm`: 인증 없이 공개 — 존재하지 않는 `server_id` 에도 같은 모양을 돌려주므로 서버 존재 여부가 새지 않는다.
- L55-59 `_ctx` (2차 변경): `policy.caller_context(None, p, h)`(`app/services/policy.py:56-73`)가 (a) `agent` 는 `MACHINE_KINDS`(workload·access_token·delegation)일 때만, (b) `data_class` 는 `declared_data_class` — 기본값보다 낮은 신고는 `data:declare` 권한 필요, 없으면 기본값으로 올리고 감사, (c) `run_id` 는 `Run.user_sub == p.sub` 일 때만, `step_id` 는 run 이 살아 있을 때만 남긴다. 승인 id 는 헤더 또는 `_meta["io.agentnexus/approval"]`. 승인 id 는 `governance._valid_approval`(`app/services/governance.py:105-113`)이 요청자·인자 해시·서버·도구·만료로 묶어 검증하므로 남의 승인 id 를 넣어도 쓰이지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L58 `caller_context` 가 `session_id` 는 여전히 헤더 원문(`app/services/policy.py:72`)으로 넘긴다(1차 지적 중 session 부분 미해결). 이 값은 `governance.create_approval(session_id=…)` 에 그대로 저장되고 `approval_context`(`app/services/governance.py:210`)가 `ChatSession.title` 을 읽어 승인 모달에 보여 준다 — 남의 세션 id 를 알면 그 제목이 내 승인 카드에 노출된다(세션 id 가 난수라 실제 악용은 어려움). `remembered`(`:188-195`)는 `requester_sub` 로 묶여 있어 "다시 묻지 않기" 우회는 없다. 최소 수정: `caller_context` 에서 `ChatSession.user_sub == p.sub` 일 때만 session_id 를 남긴다(run 과 같은 규칙).
- [위험:낮음] L58 `caller_context(None, …)` 는 run 헤더가 있으면 `SessionLocal()` 을 열고(`policy.py:60-67`), 데이터 등급 기본값 캐시(5초) 만료 시 DB 를 읽고, 하향 신고면 `audit.append` 를 동기 실행한다 — `_ctx` 는 async 경로(L116·L154·L243)에서 스레드풀 없이 호출되므로 그동안 이벤트 루프가 멈춘다. 최소 수정: `ctx = await run_in_threadpool(_ctx, request, p, params)`.
- [위험:낮음] L36·L39 `server_id` 경로 값을 검증 없이 `WWW-Authenticate` 헤더 문자열에 넣는다 — `%22` 로 따옴표가 들어가면 헤더 파라미터 구문이 깨지고, 디코드된 제어 문자는 h11 이 헤더 값으로 거부해 인증 없는 요청이 500/연결 오류로 끝날 수 있다(가능성). 최소 수정: `urllib.parse.quote(server_id, safe="")`.

```python path=app/api/mcp.py lines=61-76

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
```
**분석 / Analysis (Level 2)**
- L62-75: 메서드 라우터. 정책·승인·소독·감사는 모두 `gw.call_tool`/`gw.generic` 안에서 일어나고(`governance.check_tool` — `app/services/governance.py:116-175`), 이 함수는 분기만 한다. legacy·modern·Tasks·MRTR·Inspector 가 모두 이 함수를 쓰므로, 여기서 우회 분기가 생기면 모든 경로가 함께 뚫린다는 점이 설계상 장점이자 단일 실패점.
- L73-74 `logging/setLevel` 은 무시하고 성공 — 업스트림 로그 수준을 하류가 바꾸지 못하게 하는 의도적 선택.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L70 `arguments` 가 dict 가 아니어도(`"abc"`, `[1]`) 그대로 넘긴다(1차 지적 미해결). `governance.check_tool` 의 `jsonschema.validate(args or {}, sch)`(`governance.py:139-141`)는 스키마가 있는 도구만 거르므로, 스키마 없는 도구는 비객체 인자가 업스트림까지 간다. 최소 수정: `if not isinstance(params.get("arguments", {}), dict): raise RpcError(gw.INVALID_PARAMS, …)`.

```python path=app/api/mcp.py lines=77-128

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
    # … (L105-111 생략 — legacy: Protocol-Version 응답 헤더 준비, initialize 면 _load_targets 로 대상 확인 후 open_session → Mcp-Session-Id 응답)
        sid = request.headers.get("mcp-session-id")
        if sid and not await run_in_threadpool(gw.check_session, sid, server_id, p):
            return JSONResponse(RpcError(gw.INVALID_REQ, "Session not found — 다시 initialize 하세요.").body(rid),
                                status_code=404)
        ctx = _ctx(request, p, params)
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
```
**분석 / Analysis (Level 2)**
- L80: **본문을 읽기 전에 인증** — 인증 없는 대용량 본문이 메모리에 쌓이지 않는 올바른 순서(`app/api/a2a.py:284` 푸시 수신과 대조).
- L85-87: JSON-RPC 배열(batch)은 MCP 2025-06-18 부터 제거 — 400.
- L88-95: `method` 없이 `result`/`error` 만 있으면 "게이트웨이가 중계한 서버→클라이언트 요청에 대한 하류 응답". `check_session`(`app/services/mcp_gateway.py:1072-1079`)이 세션의 `server_id`·`principal_sub`·종료 여부까지 확인한 뒤에만 `mcp_consent.answer` 로 대기 행에 연결한다 — 남의 세션 id 로 남의 sampling 에 답을 끼워 넣지 못한다(Epic 48 C48-03, `test/test_idor_matrix.py:157-166`).
- L99-101: 알림(id 없음)은 처리하지 않고 202 — 사양상 알림에는 응답 본문이 없다.
- L102-104: 세대 판정 — `_meta["io.modelcontextprotocol/protocolVersion"]` 가 있고 initialize 가 아니거나, `MCP-Protocol-Version` 헤더가 modern 이면 stateless 경로.
- L112-115 (legacy): 세션 id 가 있으면 반드시 주체·서버에 묶인 세션이어야 하고(아니면 404), **없으면 세션 없이 진행**한다.
- L117-121: 세션 능력(`_capabilities`, initialize 때 클라이언트가 선언)에 sampling/elicitation 이 있거나 progressToken 이 있고 SSE 를 수락하면 중계 스트림.
- L124-127: `RpcError` 를 HTTP 상태(예: 429 → `Retry-After`)로 변환.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L99 `params = body.get("params") or {}` 의 타입을 확인하지 않는다 — `"params": [1]` 또는 `"_meta": "x"` 를 보내면 L102 `mcp_modern.requested_version`(`app/services/mcp_modern.py:85-88`)의 `.get` 에서 `AttributeError` → 일반 예외 처리기가 없어(`app/core/errors.py:55-75`, main.py 2차 보고서) text/plain 500. 인증된 사용자만 가능하지만 JSON-RPC 계약(-32600)이 깨진다. 최소 수정: `if not isinstance(params, dict) or not isinstance(params.get("_meta", {}), dict): return INVALID_REQ 400`.
- [위험:낮음] L112-115 legacy 경로에서 세션 id 가 선택 사항이다(1차 지적 미해결·설계) — 세션 바인딩은 중계 응답·능력 조회에만 쓰이고 도구 호출 자체의 인가는 매 요청 주체로 판정하므로 보안 결함은 아니다. 다만 MCP 사양의 "세션 필수 서버는 400" 과 다르다는 점을 문서화해 둘 것.

```python path=app/api/mcp.py lines=129-176

async def _modern(server_id: str, request: Request, p: Principal, rid, method: str, params: dict, version: str | None):
    """MCP 2026-07-28 (stateless) — 세션 없이 요청마다 판정, MRTR 로 서버 요청을 결과에 실어 보낸다 (D13-④)."""
    hdr = {"MCP-Protocol-Version": version or mcp_modern.MODERN_VERSIONS[0]}
    try:
        if version is None:
            raise mcp_modern.ModernError(mcp_modern.HEADER_MISMATCH, "Header mismatch: _meta 에 io.modelcontextprotocol/protocolVersion 이 없습니다.")
        mcp_modern.validate(request.headers, method, params, version)
    # … (L137-143 생략 — server/discover(대상 확인 후 discover 결과) · subscriptions/listen → _listen · REMOVED 메서드 404)
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
        ctx = _ctx(request, p, params)
        if method in mcp_modern.MRTR_METHODS:
            if method == "tools/call" and mcp_tasks.wants(params) and not params.get("requestState"):
                out = await mcp_tasks.start(server_id, ctx, params, dispatch)  # 오래 걸리면 CreateTaskResult
                if "__final__" in out:
                    return JSONResponse(_modern_final(rid, method, out), headers=hdr)
                out["_meta"] = {mcp_modern.K_SERVER: mcp_modern.SERVER_INFO}
                return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": out}, headers=hdr)
    # … (L162-167 생략 — progressToken+SSE 면 _modern_progress_stream, 아니면 requestState 유무로 mcp_modern.resume/start 후 _modern_final)
        result = await dispatch(server_id, ctx, method, {k: v for k, v in params.items() if k != "_meta"})
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": mcp_modern.complete(method, result)}, headers=hdr)
    except mcp_modern.ModernError as exc:
        return JSONResponse(exc.body(rid), status_code=exc.http_status, headers=hdr)
    except RpcError as exc:  # modern: 모르는 메서드는 404 (Streamable HTTP 2026-07-28)
        status = 404 if exc.code == gw.NOT_FOUND and exc.http_status == 200 else exc.http_status
        return JSONResponse(exc.body(rid), status_code=status, headers=hdr)
```
**분석 / Analysis (Level 2)**
- L134-136: modern 은 본문 `_meta` 의 버전이 필수이고, `validate` 가 헤더(`MCP-Protocol-Version`·`Mcp-Method` 등)와 본문 일치를 확인한다(헤더-본문 불일치 = 프록시 혼동 공격 방지).
- L144-150 Tasks: 확장을 선언한 요청만, `mcp_tasks.handle(server_id, p, …)` 가 작업을 **주체·서버**로 찾는다(존재 은닉 -32602 통일 — `mcp_tasks.py` 2차 보고서). `_load_targets` 로 서버 활성 여부를 먼저 확인.
- L151-153: `x-mcp-header` 스키마가 있는 도구는 `Mcp-Param-*` 헤더와 인자를 대조 — 호출 전 일반 JSON 400.
- L155-161: MRTR(다중 왕복) 메서드 중 Tasks 를 선언한 `tools/call` 은 `mcp_tasks.start` 가 `INLINE_S` 안에 끝나면 `__final__`, 아니면 `CreateTaskResult`.
- L168: modern 은 `_meta` 를 떼고 dispatch — 업스트림에 하류 `_meta` 가 섞이지 않는다.
- L172-174: legacy 의 "모르는 메서드" 는 HTTP 200 + JSON-RPC 오류지만 modern Streamable HTTP 는 404 로 바꾼다.
- 흔한 실수: `except` 순서 — `ModernError` 와 `RpcError` 는 서로 상속 관계가 아니어서 순서 무관하지만, 여기서 잡지 않은 예외(DB 오류 등)는 500 text/plain 으로 나간다.

```python path=app/api/mcp.py lines=177-184
def _modern_final(rid, method: str, out: dict) -> dict:
    if "__final__" not in out:
        return {"jsonrpc": "2.0", "id": rid, "result": out}
    fin = out["__final__"] or {}
    if "error" in fin:
        return {"jsonrpc": "2.0", "id": rid, "error": fin["error"]}
    return {"jsonrpc": "2.0", "id": rid, "result": mcp_modern.complete(method, fin.get("result") or {})}

```
**분석 / Analysis (Level 2)**
- L177-183: MRTR/Tasks 가 돌려준 내부 봉투 `__final__` 을 JSON-RPC `result`/`error` 로 푼다. `__final__` 이 없으면 중간 결과(`InputRequiredResult` 등)를 그대로 `result` 로.

```python path=app/api/mcp.py lines=185-223

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
```
**분석 / Analysis (Level 2)**
- L186 `X-Accel-Buffering: no`: nginx 가 SSE 를 버퍼링하지 않게 하는 응답 헤더(없으면 이벤트가 몰려서 도착).
- L192-195: 업스트림 작업 `waiter` 를 **응답 객체를 돌려주기 전에** Task 로 시작한다 — 스트림이 열리는 동안에도 작업이 진행된다.
- L201-217: 진행(progress) 큐와 작업 완료를 `asyncio.wait(FIRST_COMPLETED)` 로 경주. progress 가 먼저면 단조 증가 값만 전달(`ProgressRelay.note`), 작업이 먼저면 대기 중 getter 를 취소하고 큐에 남은 것을 비운 뒤 최종 메시지. `waiter.result()` 가 예외를 다시 던지므로 L212-215 에서 JSON-RPC 오류로 바꾼다.
- L218-221: 클라이언트가 끊으면(제너레이터 `aclose`) 미완료 작업 취소 + 이 요청의 큐만 분리.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L193·L305(가능성) 백그라운드 작업 참조가 제너레이터 클로저에만 있다. 응답 시작 전에 클라이언트가 끊겨 Starlette 가 `events()` 를 한 번도 반복하지 않으면 `finally` 가 실행되지 않아 (a) 작업이 취소되지 않고 끝까지 실행되며 (b) `mcp_modern.detach` 가 호출되지 않아 progress 큐가 `_PROGRESS` 에 남고, (c) 응답 객체가 버려진 뒤 asyncio 가 Task 를 약참조만 해 실행 중 GC 될 수 있다("Task was destroyed but it is pending"). 1차의 getter 경주 지적은 누수 없음으로 판정했으나 이 "시작 전 단절" 경로는 다루지 않았다. 최소 수정: 모듈 전역 `set` 에 Task 를 넣고 `add_done_callback(discard)`, `detach` 는 waiter 의 done 콜백에서도 호출.

```python path=app/api/mcp.py lines=224-239

async def _listen(server_id: str, request: Request, p: Principal, rid, params: dict, hdr: dict):
    """subscriptions/listen — 승인(acknowledged) 후 목록 변경 알림을 흘린다. 스트림을 닫으면 구독 취소."""
    if "text/event-stream" not in request.headers.get("accept", ""):
        return Response(status_code=406)  # listen 응답은 언제나 알림 스트림
    honored, uris, task_ids = mcp_modern.listen_filter(params)
    await run_in_threadpool(gw._load_targets, server_id, p)
    if not await run_in_threadpool(mcp_modern.listen_acquire, p.sub):  # 워커 전체 합 (D18-②)
        raise RpcError(gw.RATE_LIMITED, f"동시 구독 한도({mcp_modern.MAX_LISTENS}) 초과", {"limit": mcp_modern.MAX_LISTENS},
                       http_status=429)
    try:
        return await _listen_open(server_id, p, rid, params, hdr, request, honored, uris, task_ids)
    except BaseException:  # 스트림을 열기 전에 실패하면 확보한 구독을 돌려준다(열린 뒤에는 listen_events 가 돌려준다)
        await run_in_threadpool(mcp_modern.listen_release, p.sub)
        raise
```
**분석 / Analysis (Level 2)**
- L227-228: listen 응답은 항상 알림 스트림 — SSE 를 수락하지 않으면 406.
- L231-233 (2차 변경): 주체별 동시 구독 한도를 **워커 전체 합**으로. `listen_acquire`(`app/services/mcp_modern.py:57-63`)는 공유 카운터를 +1 하고 한도를 넘으면 즉시 -1 로 되돌리는 "낙관적 증가 후 보정" — 조건부 증가가 없는 백엔드에서도 원자적인 패턴. TTL 은 `LISTEN_MAX_S + 60`(900+60초)이라 워커가 죽어도 카운트가 풀린다. 1차의 워커 로컬 `LISTENS.get` 비교는 워커 수만큼 한도가 늘어났다.
- L234-238: `except BaseException` 은 `asyncio.CancelledError`(BaseException 하위)까지 잡아 반납한 뒤 다시 던진다 — 클라이언트가 구독 승인 중(L245-250 의 업스트림 읽기 동안) 끊겨도 카운트가 새지 않는다. `except Exception` 으로 썼다면 취소 시 누수(흔한 실수).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L235(가능성) `_listen_open` 이 `StreamingResponse` 를 **정상 반환한 뒤** 첫 반복 전에 연결이 끊기면, 반납 책임이 있는 `listen_events` 제너레이터의 `finally`(`app/services/mcp_modern.py:500`)가 실행되지 않아 카운트가 TTL(최대 960초)까지 남는다 — 주체가 짧은 시간에 반복 단절하면 스스로 429 에 걸린다. 최소 수정: StreamingResponse 의 `background=BackgroundTask(...)` 대신, 반환 직전 제너레이터를 감싼 래퍼에서 `try/finally` 로 반납하거나 L193 과 같은 Task 등록 방식으로 보장.

```python path=app/api/mcp.py lines=240-273

async def _listen_open(server_id: str, p: Principal, rid, params: dict, hdr: dict, request: Request, honored: dict,
                       uris: list, task_ids: list):
    ctx = _ctx(request, p, params)
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
    return StreamingResponse(mcp_modern.listen_events(server_id, ctx, rid, honored, alive),
                             media_type="text/event-stream", headers={**hdr, **SSE_HEADERS})
```
**분석 / Analysis (Level 2)**
- L245-252: 구독할 리소스 URI 마다 실제 `resources/read` 를 게이트웨이 경로(정책·권한 적용)로 한 번 호출해 "지금 읽을 수 있는 것만" 승인 — 권한 없는 리소스의 변경 알림으로 존재·변경 시점이 새지 않게 하는 설계. 비용은 URI 수(상한 20, `listen_filter`)만큼 업스트림 호출이며 순차 실행이라 최악 20×타임아웃.
- L253-254: Tasks 구독은 `mcp_tasks.owned` 로 이 주체·서버의 작업만 — 남의 taskId 는 조용히 빠진다(존재 비노출).
- L256-267 `alive`: 원본 토큰을 주기적으로 재인증(`listen_events` 가 호출) — 토큰 폐기·만료 시 구독 종료(`test/test_mcp_modern_ext.py:176-186`). `raw_token` 이 없는 주체(내부 위임)는 항상 살아 있음으로 본다.
- L268-270: 감사 기록은 스레드풀(동기 해시 체인 쓰기).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L243 `_ctx` 동기 호출(위 L58 Watch-out 과 동일). L245-250 의 `except RpcError: continue` 는 의도된 필터지만, 업스트림이 `httpx` 예외 등 `RpcError` 가 아닌 오류를 던지면 L236 으로 올라가 구독 전체가 실패한다 — 한 URI 장애가 구독 전체를 막는다(낮음).

```python path=app/api/mcp.py lines=274-316

RELAY_METHODS = {"tools/call", "resources/read", "prompts/get"}


def _relay_stream(server_id: str, sid: str, caps: dict, ctx: RpcContext, rid, method: str, params: dict,
                  headers: dict, token=None) -> StreamingResponse:
    # … (L280-284 생략 — docstring: 서버→클라이언트 요청·progress 를 같은 SSE 로 중계, 클라이언트는 같은 세션으로 응답 POST)
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
```
**분석 / Analysis (Level 2)**
- L275: 서버→클라이언트 요청이 생길 수 있는 메서드만 중계 대상.
- L286: `mcp_consent.Relay` 를 컨텍스트에 꽂으면 업스트림이 보낸 sampling·elicitation 요청이 하류 클라이언트로 같은 스트림에 실려 나가고, 응답은 L88-93 경로로 돌아온다(`mcp_relays` 테이블 공유로 다중 워커 대응 — `mcp_consent.py` 2차 보고서). progress 가 없는 세션 없음(`sid=None`) 요청도 여기 올 수 있는데, 이때 `caps` 가 비어 중계 대신 Host 동의 카드로 간다.
- L295-305: 작업을 Task 로 돌리고 결과·오류·종료(None)를 큐로 전달. `finally` 의 `queue.put(None)` 은 무제한 큐라 취소 중에도 막히지 않는다.
- L307-313: 제너레이터 종료(클라이언트 단절) 시 작업 취소.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L301-302 처리되지 않은 예외의 `str(exc)` 를 그대로 클라이언트에 보낸다 — DB 오류 메시지(테이블·컬럼명), 내부 호스트명·URL 이 담길 수 있다(정보 노출). 최소 수정: 클라이언트에는 `type(exc).__name__` + trace_id 만, 상세는 `logs.event` 로.
- [위험:낮음] L305 백그라운드 Task 참조 문제는 L193 Watch-out 과 동일(가능성).

```python path=app/api/mcp.py lines=317-331
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


```
**분석 / Analysis (Level 2)**
- L317-320: 독립 SSE(GET) 미제공 — 사양상 405 허용, `Allow` 헤더로 가능한 메서드 고지.
- L323-329: 세션 종료. `close_session`(`app/services/mcp_gateway.py:1088-1097`)은 `principal_sub == p.sub` 만 확인하고 **`server_id` 는 대조하지 않는다** — 다른 서버 경로로 내 세션을 닫을 수 있을 뿐 남의 세션에는 영향 없음(`test/test_idor_matrix.py:162`). 정합성 차원에서 `check_session` 과 같은 조건으로 맞추면 좋다.

```python path=app/api/mcp.py lines=332-357
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
```
**분석 / Analysis (Level 2)**
- L333-341: 목록용 요약. `auth` 전체 대신 `type`·`authorization_server` 만.
- L342-354: 상세 — 도구 + 규칙(위험도·승인·인자 스키마·소독), 리소스·프롬프트·server.json, `auth` 는 `secret_ref` 만 제거(시크릿 **값**은 원래 별도 저장소라 응답에 없다), 교환 정책. 규칙 조회를 서버당 1쿼리로 묶어 N+1 을 피했다.
- 흔한 실수: `rules[t["name"]]` 를 7번 반복 조회 — 동작은 맞지만 지역 변수(`r = rules.get(t.get("name"))`)로 바꾸면 읽기 쉽다(스타일).

```python path=app/api/mcp.py lines=358-386
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
```
**분석 / Analysis (Level 2)**
- L358-360: 목록(`mcp:read` — developer·security). 조직 공유 카탈로그라 객체 인가 없음이 의도(`test/test_idor_matrix.py` 의 사용자 경로 분류 밖 — `mcp:read` 는 user 역할에 없다).
- L363-376: 서버 코드 골격(템플릿)을 **문자열로만** 돌려준다(디스크에 쓰지 않음). `include == ["none"]` 은 "선택 없음" 을 쿼리로 표현하기 위한 특수값 — 빈 목록은 쿼리 문자열로 보낼 수 없어서다. 이름 형식 검증은 `mcp_template.render` 의 `ValueError` 로.
- L379-384: 라우트 선언 순서 — `/templates/custom` 이 `/{server_id}` 보다 **먼저** 선언되어야 `templates` 가 server_id 로 잡히지 않는다(FastAPI 는 선언 순서대로 매칭; 흔한 실수 지점).

```python path=app/api/mcp.py lines=387-425
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
    secrets.check_destination(p, {**(s.auth or {}), **(body.auth or {})},
                              body.endpoint not in (None, s.endpoint), bool(body.secret))
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

```
**분석 / Analysis (Level 2)**
- L391 `repr=False`: pydantic 모델을 로그·예외에 찍을 때 시크릿이 나오지 않게(값 검증 오류 응답에는 여전히 입력값이 들어갈 수 있으므로 `RequestValidationError` 처리기가 `input` 을 지우는지 확인 필요 — `app/core/errors.py`).
- L403 (2차 신규) `check_refs`: `body.auth` 의 `secret_ref`·`tls.ca_secret`·`tls.client_cert_secret` 가 자기 네임스페이스(`mcp/{id}/`) 밖이면 `gateway:write` 필요(`app/services/secrets.py:17-28`). 접두사 끝의 `/` 덕분에 `mcp/jira/` 가 `mcp/jira-x/…` 를 허용하지 않는다.
- L404-405 (2차 신규) `check_destination`: **endpoint 가 바뀌고** 새 시크릿을 함께 넣지 않았으며 병합 후 auth 에 `secret_ref`(또는 client cert)가 있으면 `gateway:write` 필요(`secrets.py:31-38`).
- L410-411: `auth` 는 얕은 병합(키 단위 덮어쓰기) — 중첩 `tls` 는 통째로 교체된다.
- L414-417: 시크릿 값은 Fernet 암호화 저장(`secrets.put` 은 flush 만, 커밋은 L418) 후 참조만 auth 에.
- L419 `gw.clear_caches()`: 토큰 캐시·업스트림 세션·hosted 서버를 **모두** 비운다(이 서버만이 아니라 전체 — 설정 변경 1건에 전 서버 재교환·재초기화 비용).
**짚어볼 점 / Watch-outs**
- [위험:높음] L403-405·L410-411 **전송처 검사가 `endpoint` 만 본다 — `auth.authorization_server`·`token_endpoint`·`resource_metadata`·`mode` 변경은 `mcp:write`(developer 역할, `app/core/auth.py:22`)만으로 통과**. 이 값들은 게이트웨이가 자격 증명·사용자 토큰을 POST 하는 곳이다: `upstream_token`(`app/services/mcp_gateway.py:144-152`)은 `authorization_server` 가 플랫폼 issuer 가 아니면 `_external_token` 으로 가고, 그 함수(`:159-181`)는 `auth.token_endpoint` 로 (a) `mode == "service"` 면 `client_secret = secret_ref 의 평문`(`:165-171`), (b) 기본 `mode="exchange"` 면 **호출 사용자의 원본 플랫폼 토큰** `subject_token = p.raw_token`(`:173-175`)을 보낸다. 시나리오: developer 가 기존 서버 `jira` 에 `PATCH {"auth": {"type": "oauth2", "authorization_server": "https://evil.example", "token_endpoint": "https://evil.example/t"}}` → `check_refs`(참조 없음)·`check_destination`(endpoint 불변 → `changed=False`) 모두 통과 → L419 캐시 비움 → 다음 tools/call 부터 그 서버를 쓰는 **모든 사용자의 액세스 토큰**이 evil 로 전송(aud=agentnexus 인 원본 토큰이라 플랫폼 API 를 그 사용자로 호출 가능). `refresh_tools` 는 `authorization_server` 가 이미 있으면 재발견하지 않으므로(`mcp_gateway.py:705-708`) 값이 유지되고, `mode="service"` 를 추가하면 운영자가 넣어 둔 `mcp/jira/backend` 시크릿이 헬스 루프(60초) 안에 자동 유출된다. 설계 의도와도 어긋난다 — `_pinned`(`mcp_gateway.py:196-205`)는 "토큰 교환 대상(사용자 플랫폼 토큰을 받는 곳)은 관리자 재탐색으로만 바꾼다" 고 명시한다. 1차 핫스팟 #1·#2 의 뿌리(사용자 병합 가능한 auth)가 이 키들로 남아 있다. 최소 수정: `check_destination` 의 `changed` 를 `endpoint` 또는 `{authorization_server, token_endpoint, resource_metadata, mode, type}` 중 하나라도 바뀌면 True 로 하고, `type == "oauth2"` 이면 `secret_ref` 유무와 무관하게 `gateway:write` 요구(사용자 토큰이 걸린 전송처). 근본 수정은 1차 권고대로 `ServerPatch.auth` 를 허용 키 pydantic 모델로 제한하고 외부 AS 는 운영자 허용 목록만. 시험 공백: `test/test_review_fixes.py:261-265` 는 `endpoint` 변경만 검사한다 — `authorization_server`·`token_endpoint`·`resource_metadata` 변경 403 시험 필요.
- [위험:낮음] L390 `auth: dict | None` 은 여전히 임의 키를 받는다(1차 지적 미해결). 예: 내부용 키 `_candidate_secret`(`mcp_gateway.py:135`·`:761`)을 영속 auth 에 넣으면 저장된 시크릿 대신 그 값을 쓰고 `server_out` 응답(L351)에 평문으로 보인다 — 자기 값이라 유출은 아니지만 내부 계약이 외부 입력으로 열려 있다는 신호.
- [위험:낮음] L396-423 객체 수준 인가 없음(1차 지적 미해결) — `s.owner` 가 있는데도 쓰지 않아 `mcp:write` 면 모든 서버를 수정한다. 특히 L408-409 `status` 로 운영자가 장애 대응으로 `disabled` 한 서버를 developer 가 즉시 `active` 로 되돌릴 수 있다(킬 스위치 무력화). 최소 수정: `status` 변경은 `gateway:write`, 그 외 변경은 `owner == p.sub` 또는 `gateway:write`.

```python path=app/api/mcp.py lines=426-449
@router.post("/api/v1/mcp-servers/{server_id}/test-config")
async def test_config(server_id: str, body: ServerPatch, p: Principal = Depends(require("mcp:write"))):
    """적용 전 시험 (SCR-F03) — 후보 엔드포인트·인증으로 연결·발견·tools/list·스코프 점검. 저장하지 않는다."""
    secrets.check_refs(p, body.auth, f"mcp/{server_id}/")

    def _current():
        with SessionLocal() as db:
            s = db.get(McpServer, server_id)
            return (s.auth or {}, s.endpoint) if s else ({}, None)
    cur_auth, cur_endpoint = await run_in_threadpool(_current)
    secrets.check_destination(p, {**cur_auth, **(body.auth or {})}, body.endpoint not in (None, cur_endpoint), bool(body.secret))
    try:
        return await gw.test_config(server_id, p, body.model_dump(exclude_none=True))
    except RpcError as exc:
        if exc.code == gw.NOT_FOUND:
            raise not_found("mcp server") from exc
        raise bad_request(exc.message) from exc


@router.post("/api/v1/mcp-servers/{server_id}/refresh")
async def refresh_server(server_id: str, p: Principal = Depends(require("mcp:write"))):
    return await gw.refresh_tools(server_id, p)


```
**분석 / Analysis (Level 2)**
- L429-436 (2차 신규): 저장하지 않는 시험에도 같은 시크릿 참조·전송처 검사 — "저장 안 하니 괜찮다" 는 착각을 막은 점이 좋다(후보 설정으로 실제 업스트림 호출이 일어나므로). 현재 auth 를 읽는 DB 조회는 스레드풀로.
- L438: `gw.test_config`(`app/services/mcp_gateway.py:744-829`)가 후보 endpoint·auth·secret 을 메모리 사본에 병합해 PRM/AS 발견 → protocol probe → tools/list → 스코프 점검 후 결과만 돌려준다. 시험 중 만든 업스트림 세션은 버리고 기존 세션을 복원(`:796-805`).
- L445-447: 도구 목록·헬스 즉시 갱신.
**짚어볼 점 / Watch-outs**
- [위험:높음] L436 위 L404-405 와 같은 결함(같은 근본 원인 — 등급은 위 항목에 합산). 저장 없이 한 번에 일어난다: 후보 `auth = {"type":"oauth2","mode":"service","resource_metadata":"https://evil/prm"}` 로 보내면 `discover_auth` 가 후보 `resource_metadata` 를 따라가(`mcp_gateway.py:219`) evil 의 PRM·AS 메타데이터로 `authorization_server`·`token_endpoint` 를 덮어쓰고(`:766-771`), `probe_protocol`/`tools/list` 의 `upstream_token` 이 **현재 저장된 `secret_ref`**(예: bearer 용으로 운영자가 넣은 값)를 evil 의 토큰 엔드포인트로 POST 한다.
- [위험:낮음] L426-442 적용 전 시험은 감사 기록이 없다 — 저장된 자격 증명으로 업스트림을 호출하는 행위인데 누가 어떤 후보로 시험했는지 남지 않는다. 최소 수정: `audit.append("mcp.server.test_config", actor=p.sub, target=server_id, data={"endpoint": body.endpoint, "auth_keys": sorted(body.auth or {})})`.

```python path=app/api/mcp.py lines=450-481
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
```
**분석 / Analysis (Level 2)**
- L457 `params: dict = {}`: pydantic 은 가변 기본값을 인스턴스마다 복사하므로 파이썬 함수 기본 인자 함정(공유 dict)은 없다.
- L463-473 `_test_principal`: 역할을 권한 집합 포함 관계(`role_perms(r) <= mine`)로 상한 — 역할 이름이 아니라 **권한**으로 비교하므로 RBAC 재정의에도 맞게 동작한다. 가상 주체 `sub=test:{실제 sub}`, `kind="test"`, `actor=실제 호출자`.
- L476-479 `_write_tool`: 규칙상 위험도가 low 가 아니거나 승인 필요면 "쓰기·위험 도구" 로 보고 드라이런 대상.
**짚어볼 점 / Watch-outs**
- [위험:중간] L452·L473 **가상 주체의 `team` 은 상한이 없다**, L476-479 **드라이런 판정이 `(server_id, tool)` 원문으로 규칙을 찾는다**. (a) 팀 기반 정책(`subject_type == "team"` — `app/services/policy.py:123-124`)은 `p.team` 으로 매칭하므로, developer(`mcp:read`)가 `test_principal.team` 에 다른 팀(예: `team-finance`)을 넣으면 그 팀에만 허용된 **읽기·저위험 도구가 실제로 호출**되어 결과가 돌아온다(드라이런은 쓰기·위험 도구만). `test/test_design_gaps.py:183-186` 이 임의 팀 `team-e24` 로 실제 호출(`dry_run is False`)되는 것을 단언하고 있어 동작이 고정돼 있다. (b) server_id 에 집계 `_all` 이나 가상 서버 id 를 쓰면 도구 이름이 `jira.transition_issue` 처럼 접두된 형태라 `ToolRule(server_id="_all", tool="jira.transition_issue")` 가 없어 `_write_tool` 이 False → 드라이런 없이 `dispatch` 가 집계 경로로 실제 서버 도구를 호출한다. 기본 규칙은 high 만 승인 필요(`app/services/governance.py:68-70`)이므로 **medium 위험 쓰기 도구는 가상 주체(임의 팀)로 실제 실행**된다 — 문서화된 "쓰기·위험 도구는 호출하지 않는 드라이런" 보장 위반. 최소 수정: `team` 은 관리자가 아니면 `p.team` 만 허용(또는 다른 팀이면 모든 호출을 드라이런), `_write_tool` 은 `gw._resolve_tool`(`app/services/mcp_gateway.py:904-910`)로 실제 (서버, 도구)를 해석한 뒤 판정하고, 해석 실패·규칙 없음은 드라이런(fail closed). 시험 공백: `_all`/가상 서버 경로 Inspector 시험 없음.

```python path=app/api/mcp.py lines=482-517
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
            def _judge():
                from app.services import governance
                with SessionLocal() as db:
                    d = governance.check_tool(db, tp, server_id, tool, (body.params or {}).get("arguments") or {},
                                              data_class=body.data_class, create_pending=False)
                    return {"allow": d.allow, "code": d.code, "reason": d.reason, "risk": d.risk}
            dec = await run_in_threadpool(_judge)
            await run_in_threadpool(_audit, {"dry_run": True, "decision": dec["code"]})
            return {"request": {"method": body.method, "params": body.params}, "test_principal": {"sub": tp.sub, "roles": tp.roles, "team": tp.team},
                    "dry_run": True, "decision": dec, "result": None, "error": None, "latency_ms": 0.0, "trace_id": current_trace_id()}
        await run_in_threadpool(_audit, {"dry_run": False})
        p = tp
    ctx = RpcContext(p, data_class=body.data_class, approval_id=body.approval_id)
    t0 = time.perf_counter()
    try:
        result, error = await dispatch(server_id, ctx, body.method, body.params), None
    except RpcError as exc:
        result, error = None, {"code": exc.code, "message": exc.message, "data": exc.data}
    return {"request": {"method": body.method, "params": body.params}, "result": result, "error": error,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "trace_id": current_trace_id(),
            **({"test_principal": {"sub": p.sub, "roles": p.roles, "team": p.team}, "dry_run": False} if p.kind == "test" else {})}

```
**분석 / Analysis (Level 2)**
- L486-506: 가상 주체 경로 — 드라이런이면 `create_pending=False` 로 승인 대기 행을 만들지 않고 판정만, 감사 actor 는 실제 호출자(`test/test_design_gaps.py:195-197`). 아니면 감사 후 `p = tp` 로 바꿔 실제 호출.
- L507: Inspector 는 콘솔 기능이라 헤더가 아니라 본문 필드로 데이터 등급·승인 id 를 받는다.
- L509-512: `RpcError` 를 결과 객체로 감싸 200 으로 — Inspector UI 가 오류도 결과처럼 보여 주기 위함.
**짚어볼 점 / Watch-outs**
- [위험:중간] L458·L507 **`data_class` 를 서버 판정 없이 그대로 쓴다** — Epic 40(D17-①)이 MCP·A2A·모델 API 의 모든 진입점을 `policy.caller_context`/`declared_data_class` 로 바꿨는데(`test/impl/40th/scenario.md:10-11`) 이 경로가 빠졌다. 결과: (a) 관리자가 `gateway.default_data_class` 를 `confidential` 로 올려도 Inspector 실제 호출은 기본 `"internal"`(L458)로 판정되어 confidential 전용 거부 정책(`policy.evaluate` 의 data_class 조건)이 적용되지 않는다, (b) RBAC 에서 `data:declare` 를 회수한 developer 도 `"public"` 을 신고할 수 있고 감사 `gateway.data_class.raise` 도 남지 않는다. 최소 수정: `ctx = RpcContext(p, data_class=policy.declared_data_class(None, p, body.data_class), …)`(스레드풀에서), `InspectIn.data_class` 기본값을 `None` 으로. 드라이런 L498-499 도 같은 값으로.

```python path=app/api/mcp.py lines=518-545
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
```
**분석 / Analysis (Level 2)**
- L523-534: 게시 → (대기면) 자기 승인 → 등록 → 도구 조회를 한 세션에서. `registry.register`(`app/services/registry.py:261-272`)가 내부에서 커밋한다. 도구 조회는 업스트림 I/O 라 세션 밖(async)에서.
- L538: `require("catalog:approve", "mcp:write")` — 두 권한 **모두** 필요(operator 이상). 자기 승인 흐름이므로 승인 권한을 요구하는 것이 맞다.
- 호출 대상: `registry._register_mcp`(`app/services/registry.py:280-316`)가 게시 메타데이터의 `_meta.auth` 에서 남의 `secret_ref`·tls 시크릿만 떼고 나머지(`authorization_server`·`token_endpoint`·`mode`)는 그대로 복사한다 — L403 Watch-out 과 같은 키 문제가 게시 경로에도 있다(CROSS-FILE, registry 보고서에서 판정).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L538 vs L397 권한 비대칭(1차 지적 미해결) — 새 등록은 operator 이상인데, 등록된 서버의 엔드포인트·인증·상태 변경은 developer(`mcp:write`)도 가능해 "등록 심사" 의 의미가 PATCH 로 사후 무력화된다(L403 [높음] 의 배경).

```python path=app/api/mcp.py lines=546-571
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
```
**분석 / Analysis (Level 2)**
- L547: `http`/`https` 스킴만(`file://` 은 422 — `test/test_ops.py:437`).
- L551: 동기 `def` — httpx 동기 클라이언트를 쓰므로 FastAPI 스레드풀에서 실행(이벤트 루프 비차단).
- L557-558: `sync_client` 의 `event_hooks` 가 요청마다 egress 가드(링크 로컬·IMDS 차단, `app/core/http.py:34-38`·`:123-125`). httpx 기본은 리다이렉트를 따르지 않는다(`follow_redirects=False`) — 3xx 는 본문이 비어 L567 에서 거부된다.
- L559-560: 예외 **타입 이름만** 응답(메시지 비노출).
- L564: `yaml.safe_load` — 임의 파이썬 객체 생성이 없는 안전한 로더(JSON 은 YAML 의 부분집합이라 JSON 도 처리).
**짚어볼 점 / Watch-outs**
- [위험:중간] L557-569 **SSRF(1차·위임 SEC-02 부분 해결) + 자원 고갈**. (a) egress 가드는 설계상 사설망을 막지 않는다(`app/core/http.py:34-35` "사내 사설망 업스트림은 제품 기능") — developer 가 `http://127.0.0.1:<포트>/…`·`http://db:5432`·`http://valkey:6379` 를 조회해 상태 코드(`HTTP 404`)·예외 타입(`ConnectError`/`ReadTimeout`)으로 내부 포트·경로를 스캔할 수 있고, L566 은 `YAMLError` 의 `str(exc)` 를 그대로 응답하는데 PyYAML 오류 문자열에는 실패 위치 줄의 원문 일부(snippet)가 들어가 내부 응답 일부가 새어 나갈 수 있다(가능성). (b) L561 크기 검사는 `http.get` 이 **본문 전체를 메모리에 받은 뒤**다 — httpx `timeout=10` 은 읽기 간격 제한이지 총 시간 제한이 아니어서, 끝없이 흘리는 서버를 가리키면 워커 메모리가 계속 증가한다. (c) `yaml.safe_load` 는 앵커·별칭(`&a`·`*a`)을 공유 참조로 만들고, L569 반환값을 FastAPI `jsonable_encoder` 가 직렬화하면서 참조마다 복사해 펼친다 — 수 KB 의 "billion laughs" YAML(10단계×10배 별칭)에 `openapi`·`paths` 키만 넣으면 직렬화가 지수적으로 커져 워커 CPU·메모리 고갈(가능성 — 라이브러리 동작 기반, 실행 미검증). 최소 수정: `with http.stream("GET", url) as r:` 로 `iter_bytes` 누적 2MB 초과 시 중단, 전체 시간 제한(`anyio.fail_after`/`httpx.Timeout(10, read=…)` + 총 시간 체크), YAML 은 별칭 금지 로더(`yaml.SafeLoader` 상속 후 `compose_node` 에서 `AliasEvent` 거부) 또는 JSON 우선, 오류 응답은 `type(exc).__name__` 만, 그리고 사설·루프백 차단 옵션(`settings`)과 운영 egress NetworkPolicy(`http.py` 2차 보고서 권고). 시험 공백: 크기·별칭·루프백 시험 없음.

```python path=app/api/mcp.py lines=572-586
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

```
**분석 / Analysis (Level 2)**
- L573: namespace 는 서버 id·시크릿 이름(`mcp/{ns}/backend`)·교환 대상(`api:{ns}`)에 쓰이므로 소문자·숫자·하이픈만(경로·이름 주입 방지).
- L577 `base_url`: 형식 검증 없음 — 호출 시점에 `_hosted_server` 가 egress 검사를 한다(1차 확인 — `app/services/mcp_gateway.py` hosted 경로; 위임 보안 SEC2-01 은 hosted 클라이언트의 가드 우회를 별도 지적).
- L582-584: `service`(서비스 자격) vs `obo`(사용자 토큰을 `api:{ns}` 대상으로 교환해 주입 — 내부 API 가 사용자 신원을 받음).

```python path=app/api/mcp.py lines=587-612
def select_operations(doc: dict, ops: list[dict]) -> tuple[dict, dict[str, str]]:
    """노출할 operation 만 남기고 이름을 바꾼다. (새 문서, {도구 이름: 위험도})."""
    import copy
    want = {o.get("operationId"): o for o in ops if o.get("operationId")}
    out, risks = copy.deepcopy(doc), {}
    for path, item in list((out.get("paths") or {}).items()):
        for method, op in list(item.items()):
            if not isinstance(op, dict) or not op.get("operationId"):
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
```
**분석 / Analysis (Level 2)**
- L591: `deepcopy` — 원본 요청 본문을 변형하지 않는다. `list(... .items())` 로 감싼 이유는 순회 중 `del` 하면 `RuntimeError: dictionary changed size during iteration` 이 나기 때문(흔한 실수 방지).
- L597-599: 선택 목록(`want`)이 있으면 목록에 없거나 `include: false` 인 operation 삭제.
- L600-603: 운영자 지정 도구 이름(정규식 검증) — MCP 도구 이름 규칙.
- L605: FastMCP 가 operationId 의 `.` 을 `_` 로 바꾼 이름으로 도구를 만든다는 가정으로 위험도 키를 맞춘다.
**짚어볼 점 / Watch-outs**
- [위험:중간] L594-595 **선택 목록이 허용 목록(allowlist)인데 `operationId` 가 없는 operation 은 삭제하지 않고 `continue` 로 남긴다** — 운영자가 `orders.get` 하나만 고르더라도 operationId 없는 `DELETE /orders/{id}` 같은 operation 은 그대로 `spec` 에 남아 hosted 서버(`FastMCP.from_openapi`, `app/services/mcp_gateway.py:595`)가 도구로 노출할 수 있다(FastMCP 의 operationId 없는 operation 도구화 여부는 가능성 — 추가 확인 필요). 콘솔 선택 UI 는 operationId 로 항목을 식별하므로 운영자는 이 operation 을 보지 못하고, L639-640 의 `_meta.tools` 도 operationId 있는 것만 세어 카탈로그에 표시되지 않는다. 기본 규칙은 high 만 승인(`app/services/governance.py:68-70`)이라 medium 으로 분류되면 바로 호출 가능. 최소 수정: `if want and (not isinstance(op, dict) or not op.get("operationId")): del item[method]; continue` (선택이 있으면 식별 불가 operation 은 제외), 경로 수준 비-operation 키(`parameters`·`summary`)는 보존.
- [위험:낮음] L605 FastMCP 명명 규칙을 `.replace(".", "_")` 하나로 가정한다 — 실제 도구 이름이 다르면 L667-669 에서 운영자가 지정한 `high`(승인 필요)가 **조용히 적용되지 않는다**(`inst["risks"]` 는 지정값을 그대로 보여 줘 적용된 것처럼 보임). 최소 수정: `_risk` 에서 매칭되지 않은 키를 모아 응답 `risks_unmatched` 로 경고하거나 400.

```python path=app/api/mcp.py lines=613-676
@router.post("/api/v1/mcp-servers/openapi", status_code=201)
async def create_from_openapi(body: OpenApiIn, p: Principal = Depends(require("catalog:approve", "mcp:write"))):
    """Internal API MCP Server 생성 (FR-S02, SCR-F04) — OpenAPI 의 각 operation 을 MCP 도구로 노출(FastMCP)."""
    if not str(body.openapi.get("openapi", "")).startswith("3"):
        raise bad_request("OpenAPI 3.x 문서가 필요합니다.")
    spec, risks = select_operations(body.openapi, body.operations) if body.operations else (body.openapi, {})
    hosted = {"openapi": spec, "base_url": body.base_url, "auth_header": body.auth_header, "identity": body.identity}
    if body.identity == "obo":  # 사용자 위임 — 내부 API 는 사용자 신원(sub·팀·역할)을 받는다. 서비스 시크릿은 쓰지 않는다
        hosted["scopes"] = body.scopes
    # … (L623-631 생략 — _pol: api:{namespace} TokenExchangePolicy 가 없으면 생성·커밋, 스레드풀 실행)
    if body.secret and body.identity == "service":
        def _sec():
            with SessionLocal() as db:
                secrets.put(db, f"mcp/{body.namespace}/backend", body.secret, kind="token")
                db.commit()
        await run_in_threadpool(_sec)
        hosted["secret_ref"] = f"mcp/{body.namespace}/backend"
    # … (L639-651 생략 — 노출 operationId 목록 ops, server.json doc(hosted-openapi 패키지·_meta.hosted) 구성, _mark(transport=hosted-openapi·endpoint=None) 정의)
    t0 = time.perf_counter()

    def _do():
        with SessionLocal() as db:
            item, _, _ = registry.publish(db, "mcp_server", doc["name"], doc["version"], doc, p.sub, source="manual")
            if item.status == "pending":
                registry.approve(db, item, None, p.sub, "OpenAPI 서버 생성")
            return registry.register(db, item, p.sub, {"namespace": body.namespace})
    inst = await run_in_threadpool(_do)
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

```
**분석 / Analysis (Level 2)**
- L616-618: OpenAPI 3.x 만, operation 선택은 `select_operations`.
- L620-631: OBO 면 `api:{ns}` 교환 정책을 만들어 사용자 토큰을 내부 API 용으로 교환(서비스 시크릿 미사용).
- L632-638: service 면 시크릿 저장 후 `hosted.secret_ref`.
- L654-663: 게시→자기 승인→등록→hosted 표시→도구 조회. 등록 핸들러(`registry._register_mcp`)는 같은 namespace 를 **다른 카탈로그 항목**이 쓰면 409(`app/services/registry.py:307-308`).
- L664-672: 운영자 지정 위험도로 기본 규칙 덮어쓰기(high = 승인 필요). 도구 조회(L663)가 기본 규칙을 먼저 만든 뒤 덮어쓴다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L632-638·L623-631 **등록(L660, 409 가능) 전에 부수효과를 커밋**한다 — namespace 가 다른 서버 것이라 L660 이 409 로 실패해도 그 서버의 `mcp/{ns}/backend` 시크릿은 이미 새 값으로 덮어써졌고(`secrets.put` 은 upsert — `app/services/secrets.py:41-55`) `api:{ns}` 교환 정책도 생성된 채 남는다. operator 권한(시크릿 관리 권한도 보유)이라 권한 상승은 아니나 실수 한 번에 운영 중 서버 자격 증명이 바뀐다. 최소 수정: 시크릿·정책 생성을 `_do` 안으로 옮겨 등록과 한 트랜잭션으로.
- [위험:낮음] L605·L667-669 위험도 키 불일치 시 조용한 미적용(위 select_operations 항목과 동일).

```python path=app/api/mcp.py lines=677-695
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

```
**분석 / Analysis (Level 2)**
- L681-683: `.limit()` 뒤에 `.where()` 를 붙여도 SQLAlchemy 는 SQL 을 올바른 순서(WHERE → ORDER BY → LIMIT)로 만든다(메서드 호출 순서와 SQL 절 순서는 무관).
- L684: 세션 id 는 자격처럼 쓰이므로 원문 대신 앞 8자 + SHA-256 핸들(24 hex = 96비트). 앞 8자 노출 후에도 남는 엔트로피(`token_urlsafe(24)` 192비트 중 약 144비트)는 추측 불가 수준.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L680 `limit: int = 200` 상·하한이 없다 — 큰 값이면 세션 전체를 메모리로, 음수면 PostgreSQL 이 `LIMIT must not be negative` 로 500(SQLite 는 -1 = 무제한). 최소 수정: `limit: int = Query(200, ge=1, le=1000)`(`app/api/a2a.py:638` 처럼).

```python path=app/api/mcp.py lines=696-713
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

```
**분석 / Analysis (Level 2)**
- L700: 규칙 전체를 `(서버, 도구)` 사전으로 한 번에 — 도구마다 쿼리하지 않는다(N+1 회피). 검색은 파이썬 부분 문자열(LIKE 와일드카드 주입 SEC2-06 류 문제 없음).
- 도구 수가 수천 단위가 되면 전체 로드·전체 응답이 부담(페이지네이션 없음) — 현재 규모에서는 수용.

```python path=app/api/mcp.py lines=714-728
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

```
**분석 / Analysis (Level 2)**
- L715: 가상 서버 id 는 `/mcp/{id}` 경로가 되므로 MCP 서버 id 와 같은 형식.
- L718 `members: list[dict]`: 구성원 형식은 문서 문자열로만 약속.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L718 구성원 dict 를 검증하지 않고 저장한다(L748). `tools` 에 목록 대신 문자열(`"add_comment"`)을 넣으면 L741 의 `name not in m["tools"]` 가 **부분 문자열 검사**가 되어 `add`·`comment` 같은 이름의 도구까지 통과한다(`load_targets` 의 제한 적용 방식에 따라 노출 범위가 의도와 달라질 수 있음 — 가능성). 최소 수정: `class Member(BaseModel): server_id: str; tools: list[str] | None = None` 로 형식 고정.

```python path=app/api/mcp.py lines=729-765
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


```
**분석 / Analysis (Level 2)**
- L731-732: 예약어 `_all`(집계)·기존 서버·가상 서버 id 와 겹치면 409 — 같은 `/mcp/{id}` 경로 공간을 공유하기 때문.
- L733-747: 도구 이름 충돌 검사. `prefix` 정책이면 충돌을 기록만 하고 생성(호출 시 `서버.도구` 로 구분), `reject` 면 409.
- L748: `model_dump(exclude=…)` 로 모델 필드명을 ORM 생성자 인자로 그대로 — 필드명이 컬럼명과 일치해야 하는 암묵 계약.
- 흔한 실수: L731 의 존재 확인과 L749-750 삽입 사이 경쟁(TOCTOU) — 동시 생성 시 PK 충돌은 DB 가 막아 `IntegrityError` 500 으로 끝난다(409 가 아님). 운영자 기능이라 영향 낮음.

```python path=app/api/mcp.py lines=766-778
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
```
**분석 / Analysis (Level 2)**
- L767-778: 보안·IAM 담당(`identity:read`)용 서버별 인가 현황 — 시크릿 값·참조 이름 대신 "설정됨" 불리언만(L777). 이 화면이 `authorization_server` 를 보여 주므로, L403 [높음] 의 변조는 사후에 여기서 눈으로 발견할 수 있다(탐지 수단은 있으나 예방은 없음).
- 서버마다 교환 정책 1쿼리(N+1) — 서버 수가 작아 수용.

## 파일 평가 / File Assessment
- 강점: (1) 2차에서 호출자 신고 헤더를 `policy.caller_context` 한 곳으로 서버 판정(agent·데이터 등급·run)하고, 세션·중계 응답·Tasks·listen 을 모두 주체·서버에 묶어 Epic 48 IDOR 행렬을 통과한다(`test/test_idor_matrix.py:157-166`). (2) 스트림 3종이 `finally` 로 작업 취소·큐 분리·구독 반납을 하고, listen 한도가 워커 전역 카운터 + `except BaseException` 반납으로 바뀌어 다중 워커에서 정확해졌다. (3) 시크릿 값·세션 id 를 응답에서 가리고(`secret_ref` 제거·해시 핸들), 저장하지 않는 `test_config` 에도 시크릿 참조·전송처 검사를 적용했다.
- 위험: (1) [위험:높음] 전송처 검사가 `endpoint` 만 봐 `authorization_server`·`token_endpoint`·`resource_metadata`·`mode` 변경으로 저장 시크릿·사용자 원본 토큰이 외부로 — `app/api/mcp.py:403-405`·`:436` + `app/services/mcp_gateway.py:144-181`. (2) [위험:중간] Inspector 가상 주체의 임의 팀·`_all`/가상 서버 경로 드라이런 우회 — `:450-453`·`:476-479`; 같은 엔드포인트의 `data_class` 서버 판정 누락 — `:458`·`:507`. (3) [위험:중간] `fetch_openapi` 사설망 SSRF·무제한 다운로드·YAML 별칭 폭탄 — `:557-569`; OpenAPI 선택 목록이 operationId 없는 operation 을 남김 — `:594-595`.
- 개선 권고(영향순): (1) `check_destination` 의 "전송처" 를 endpoint + 인가 서버 계열 키 전체로 넓히고 oauth2 는 `gateway:write` 필수, 시험 추가 → 근본적으로 `ServerPatch.auth` 허용 키 모델화·외부 AS 허용 목록. (2) Inspector: 팀은 호출자 팀만, 드라이런 판정은 해석된 실제 (서버, 도구)로 fail-closed, `data_class` 는 `declared_data_class`. (3) fetch: 스트리밍 크기 제한·총 시간 제한·별칭 금지 로더·오류 문자열 비노출, select_operations 는 선택 시 식별 불가 operation 제거. (4) PATCH `status` 는 `gateway:write`, 객체 수준(소유자) 인가, `test_config` 감사.
- 파일 위험 등급 / Risk grade: 높음 — 미해결 [위험:높음] 1건(사용자 토큰·저장 시크릿 외부 전송) + 게이트웨이 진입 경계 모듈에 [위험:중간] 4건.
- 재읽기 권고 레벨: 2 — 관리 API 의 auth 병합이 서비스 계층 토큰 전송 경로와 맞물리는 지점은 줄 단위로 함께 읽어야 결함이 보인다(이 파일만 읽으면 "검사가 있다" 로 보인다).

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| [중간] L58·L60-61 `_ctx` 가 agent·data_class·session/run/step 헤더를 그대로 신뢰 | 부분 해결 | `app/api/mcp.py:58` → `app/services/policy.py:56-73`(agent 기계 주체만·data:declare·run 소유) · `test/test_caller_trust.py:38`·`:72`·`:90`. 남은 것: `session_id` 원문(`policy.py:72`), Inspector `data_class`(`mcp.py:458`·`:507`) |
| L72 `arguments` 비-dict 통과 | 미해결 | `app/api/mcp.py:70` |
| L114-117 legacy 세션 선택적 | 미해결(설계) | `app/api/mcp.py:112-115` |
| [낮음] L205 getter 경주(누수 없음 판정) | 미해결(영향 없음) | `app/api/mcp.py:202-208` — 단 "응답 시작 전 단절" 경로는 2차 신규 지적(L193) |
| L238-243 listen URI 마다 업스트림 읽기 비용 | 미해결(설계) | `app/api/mcp.py:245-250` |
| (1차 코드) listen 한도가 워커 로컬 `LISTENS` | 해결 | `app/api/mcp.py:231-238` `listen_acquire/release` 전역 카운터 · `test/test_multiworker.py:25-35` |
| L344 `auth` 에서 `secret_ref` 만 제거 | 미해결(수용) | `app/api/mcp.py:351` |
| [높음] L400-407 `patch_server` 가 임의 `secret_ref`·`endpoint` 병합(핫스팟 #1) | 부분 해결 | 남의 참조 403 `app/api/mcp.py:403` + endpoint 변경 403 `:404-405`(`test/test_review_fixes.py:246-265`). **미해결**: `authorization_server`·`token_endpoint`·`resource_metadata`·`mode` 변경으로 같은 유출(본 보고서 [위험:높음]) |
| L380-386 `ServerPatch.auth` 임의 키 | 미해결 | `app/api/mcp.py:390` |
| L389-413 객체 수준 인가 없음 | 미해결 | `app/api/mcp.py:396-423`(`s.owner` 미사용) |
| [높음] L416-420 `test_config` 후보 auth 로 시크릿 유출 | 부분 해결 | `app/api/mcp.py:429`·`:436` 검사 추가, 인가 서버 계열 키 우회는 동일하게 남음 |
| L445-455 가상 시험 주체 역할 상한 | 해결 유지(새 결함 동반) | `app/api/mcp.py:463-473` — 팀 무제한·`_all` 드라이런 우회는 2차 신규 [위험:중간] |
| L520 등록(두 권한) vs 수정(`mcp:write`) 비대칭 | 미해결 | `app/api/mcp.py:538` vs `:397` |
| [중간] L538-540 `fetch_openapi` 내부 서비스 SSRF(위임 SEC-02) | 부분 해결 | 링크 로컬·IMDS 차단 `app/core/http.py:36-38`·`:123-125`(`test/test_review_fixes.py:74-80`) · 사설·루프백은 설계상 허용 유지, 응답 크기·YAML 별칭은 2차 신규 |
| L601 `hosted.base_url` 호출 시 egress 검사 | 해당 없음(1차 확인 유지) | `app/api/mcp.py:619` |
| L666-675 세션 해시 핸들 · L713 예약어 거부 | 유지 | `app/api/mcp.py:684`·`:690-693`·`:731` |
