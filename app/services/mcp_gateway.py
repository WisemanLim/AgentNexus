"""MCP Requests 프록시 (FR-G09·G00·G07·I02·I03, FR-A03 원격 경로, FR-S00).

게이트웨이는 클라이언트 MCP 세션을 직접 종료(terminate)하고, 업스트림 서버별 세션을 따로 관리한다.
- 대상: 단일 서버(/mcp/{id}) · 가상 서버(VirtualServer) · 전체 집계(/mcp/_all, 도구명 `{ns}.{tool}`)
- 전송: streamable-http(원격) · hosted-openapi(OpenAPI→도구, 게이트웨이 내부 호스팅) · stdio(허용 명령만)
- 인증: 업스트림이 OAuth 2.1 리소스 서버면 RFC 9728 PRM 으로 인가 서버를 발견하고, 내장 STS 면 RFC 8693 으로
  도구별 최소 스코프 토큰을 교환해 주입한다(사용자 신원 전파). 외부 AS 는 token-exchange 또는 서비스 계정.
- 세대: 업스트림이 MCP 2026-07-28 을 지원하면(server/discover 로 발견, 서버별 protocol=auto|legacy|2026-07-28)
  initialize·세션 없이 요청마다 `_meta`·헤더로 호출하고, InputRequiredResult 는 같은 중계·동의 경로로 답해 재시도한다(D14-②).
"""
import asyncio
import contextvars
import fnmatch
import json
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from opentelemetry.trace import SpanKind

from app.core import logs, metrics, security
from app.core.auth import Principal
from app.core.config import MCP_PROTOCOL_VERSION, MCP_SUPPORTED_VERSIONS, settings
from app.core.context import current_trace_id
from app.core.http import async_client
from app.core.tracing import span
from app.core.ids import new_id, token_urlsafe
from app.db import SessionLocal, utcnow
from app.models import (AgentProfile, McpServer, McpSession, RoutingDecision, TokenExchangePolicy, ToolRule,
                        UsageRecord, VirtualServer)
from app.services import audit, governance, guards, identity, mcp_modern, ratelimit, secrets

GATEWAY_INFO = {"name": "agentnexus-gateway", "title": "AgentNexus AI Gateway", "version": "0.1.0"}
ALL = "_all"
TOOLS_TTL_S = 300
SYSTEM = Principal(sub="system:gateway", kind="workload", name="gateway", roles=["admin"])

# JSON-RPC 오류 코드
PARSE, INVALID_REQ, NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603
APPROVAL_REQUIRED, UPSTREAM_ERROR, FORBIDDEN = -32001, -32002, -32003
RATE_LIMITED = -32005  # A2A 게이트웨이와 같은 코드, HTTP 429 + Retry-After


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: dict | None = None, http_status: int = 200):
        super().__init__(message)
        self.code, self.message, self.data, self.http_status = code, message, data or {}, http_status

    def body(self, rid) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.data:
            err["data"] = self.data
        return {"jsonrpc": "2.0", "id": rid, "error": err}


@dataclass
class ServerRef:
    """DB 행의 분리 스냅샷 (비동기 구간에서 세션 없이 사용)."""
    id: str
    name: str
    transport: str
    endpoint: str | None
    command: list
    auth: dict
    tools: list
    status: str
    server_json: dict = field(default_factory=dict)
    tools_refreshed_at: float | None = None
    protocol: str = "auto"  # auto | legacy | 2026-07-28
    upstream_protocol: str | None = None

    @classmethod
    def of(cls, s: McpServer) -> "ServerRef":
        return cls(s.id, s.name, s.transport, s.endpoint, list(s.command or []), dict(s.auth or {}),
                   list(s.tools or []), s.status, dict(s.server_json or {}),
                   s.tools_refreshed_at.timestamp() if s.tools_refreshed_at else None,
                   s.protocol or "auto", s.upstream_protocol)


# ───────────────────────── 업스트림 토큰 (MCP Authorization) ─────────────────────────
_TOKENS: dict[tuple, tuple[float, str]] = {}


def _cache_token(key: tuple, expires: float, token: str) -> None:
    if len(_TOKENS) >= 1024:  # 만료분 정리 — 한 번 왔다 다시 오지 않는 주체·대상 조합이 쌓이지 않게
        now = time.time()
        for k in [k for k, (exp, _) in _TOKENS.items() if exp <= now]:
            _TOKENS.pop(k, None)
    _TOKENS[key] = (expires, token)


def clear_caches() -> None:
    _TOKENS.clear()
    _UPSTREAM_SESSIONS.clear()
    _UPSTREAM_SEEN.clear()
    _close_hosted(list(_HOSTED))


def _required_scopes(srv: ServerRef, tool: str | None) -> list[str] | None:
    scopes = (srv.auth.get("tool_scopes") or {})
    if tool and tool in scopes:
        return [scopes[tool]]
    return None  # 교환 정책의 default_scopes


def ensure_exchange_policy(db: Session, srv: McpServer) -> None:
    """등록 시 대상 audience 교환 정책 기본값 — 허용 스코프 = PRM scopes_supported ∪ 도구 스코프, 기본 = 읽기."""
    aud = f"mcp:{srv.id}"
    if db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == aud)).first():
        return
    tool_scopes = sorted(set((srv.auth or {}).get("tool_scopes", {}).values()))
    allowed = sorted(set(tool_scopes) | set((srv.auth or {}).get("scopes_supported") or []))
    default = [s for s in allowed if s.endswith(":read")] or allowed[:1]
    if not allowed:
        allowed = default = ["mcp:invoke"]
    db.add(TokenExchangePolicy(id=new_id("txp"), audience=aud, audience_type="mcp_server", allowed_scopes=allowed,
                               default_scopes=default, ttl_seconds=300))


async def upstream_token(srv: ServerRef, p: Principal, scopes: list[str] | None) -> dict[str, str]:
    """업스트림 요청 인증 헤더."""
    auth = srv.auth or {}
    kind = auth.get("type", "none")
    if kind in ("none", "", None):
        return {}
    if kind in ("bearer", "api_key"):
        def _secret():
            with SessionLocal() as db:
                return secrets.get_plain(db, auth.get("secret_ref"))
        value = auth.get("_candidate_secret") or await run_in_threadpool(_secret)  # 적용 전 시험(F03)은 저장 안 한 후보 값
        if not value:
            raise RpcError(UPSTREAM_ERROR, f"{srv.id}: 백엔드 자격 증명이 설정되지 않았습니다.")
        return {"Authorization": f"Bearer {value}"} if kind == "bearer" else {auth.get("header", "X-API-Key"): value}
    aud = f"mcp:{srv.id}"
    key = (p.sub, aud, tuple(sorted(scopes or [])))
    hit = _TOKENS.get(key)
    if hit and hit[0] - 30 > time.time():
        return {"Authorization": f"Bearer {hit[1]}"}
    as_url = auth.get("authorization_server") or security.issuer()
    if as_url.rstrip("/") == security.issuer():
        def _exchange():
            with SessionLocal() as db:
                return identity.exchange(db, p, aud, scopes, actor="gateway")
        try:
            tok = await run_in_threadpool(_exchange)
        except Exception as exc:  # noqa: BLE001 — ApiError(token_exchange_failed)
            raise RpcError(FORBIDDEN, getattr(exc, "detail", str(exc)), {"code": "token_exchange_failed"}) from exc
    else:
        tok = await _external_token(srv, p, scopes)
    _cache_token(key, time.time() + int(tok.get("expires_in", 300)), tok["access_token"])
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def _external_token(srv: ServerRef, p: Principal, scopes: list[str] | None) -> dict:
    auth = srv.auth
    endpoint = auth.get("token_endpoint")
    if not endpoint:
        raise RpcError(UPSTREAM_ERROR, f"{srv.id}: 외부 인가 서버 token_endpoint 미발견")
    scope = " ".join(scopes or auth.get("scopes_supported") or [])
    if auth.get("mode", "exchange") == "service":
        def _sec():
            with SessionLocal() as db:
                return secrets.get_plain(db, auth.get("secret_ref"))
        secret = await run_in_threadpool(_sec)
        form = {"grant_type": "client_credentials", "client_id": auth.get("client_id", ""),
                "client_secret": secret or "", "scope": scope, "resource": srv.endpoint or ""}
    else:
        form = {"grant_type": identity.GRANT_TOKEN_EXCHANGE, "subject_token": p.raw_token or "",
                "subject_token_type": identity.TOKEN_TYPE_ACCESS, "resource": srv.endpoint or "", "scope": scope,
                "client_id": auth.get("client_id", "agentnexus-gateway")}
    async with async_client(timeout=10) as http:
        res = await http.post(endpoint, data=form)
    if res.status_code >= 400:
        raise RpcError(FORBIDDEN, f"{srv.id}: 외부 인가 서버 토큰 발급 실패 ({res.status_code})",
                       {"code": "token_exchange_failed"})
    return res.json()


async def _tls(srv: ServerRef) -> dict:
    """업스트림 mTLS (FR-G02) — 서버 auth.tls + 게이트웨이 정책 → httpx 인자. 정책 위반은 호출 전에 거부."""
    from app.services import tls
    if tls.fast_none(srv.auth.get("tls")):
        return {}
    try:
        return tls.client_kwargs(await run_in_threadpool(tls.context_for, srv.auth.get("tls"), f"mcp:{srv.id}",
                                                         srv.endpoint or ""))
    except tls.MtlsError as exc:
        raise RpcError(FORBIDDEN, str(exc), {"code": "mtls_required", "server": srv.id}) from exc


def _pinned(srv: ServerRef, found: dict) -> dict:
    """401 재탐색 결과 — 등록된 인가 서버와 다르면 거부. 업스트림 응답 헤더 하나로 토큰 교환 대상(사용자 플랫폼 토큰을 받는 곳)을
    바꾸지 못하게 한다. 바꾸려면 관리자가 재탐색(`/api/v1/identity/mcp-auth/{id}/rediscover`)."""
    pinned = (srv.auth.get("authorization_server") or "").rstrip("/")
    new = (found.get("authorization_server") or "").rstrip("/")
    if pinned and new and new != pinned:
        raise RpcError(FORBIDDEN, f"{srv.id}: 업스트림이 등록과 다른 인가 서버를 요구합니다 — 관리자 재탐색이 필요합니다.",
                       {"code": "authorization_server_changed", "server": srv.id})
    return found


def _prm_url(endpoint: str) -> str:
    u = urlparse(endpoint)
    return f"{u.scheme}://{u.netloc}/.well-known/oauth-protected-resource{u.path}"


async def discover_auth(srv: ServerRef, challenge: str | None = None) -> dict:
    """RFC 9728 PRM → 인가 서버 → (외부면) RFC 8414 메타데이터. 결과는 server.auth 에 병합."""
    if not srv.endpoint:
        return {}
    url = None
    if challenge and "resource_metadata=" in challenge:
        url = challenge.split("resource_metadata=", 1)[1].split('"')[1] if '"' in challenge else None
    url = url or srv.auth.get("resource_metadata") or _prm_url(srv.endpoint)
    try:
        return await _discover(url, await _tls(srv))
    except (httpx.HTTPError, ValueError) as exc:
        raise RpcError(UPSTREAM_ERROR, f"{srv.id} 인증 메타데이터 조회 실패: {type(exc).__name__}",
                       {"server": srv.id}) from exc


async def _discover(url: str, tls_kw: dict | None = None) -> dict:
    out: dict = {}
    async with async_client(timeout=10, **(tls_kw or {})) as http:
        res = await http.get(url)
        if res.status_code != 200:
            return {}
        meta = res.json()
        out = {"type": "oauth2", "resource_metadata": url,
               "authorization_server": (meta.get("authorization_servers") or [security.issuer()])[0].rstrip("/"),
               "scopes_supported": meta.get("scopes_supported") or []}
        if out["authorization_server"] != security.issuer():
            as_meta = None
            for suffix in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
                r = await http.get(out["authorization_server"] + suffix)
                if r.status_code == 200:
                    as_meta = r.json()
                    break
            if as_meta:
                out["token_endpoint"] = as_meta.get("token_endpoint")
    return out


# ───────────────────────── 업스트림 JSON-RPC (Streamable HTTP) ─────────────────────────
_UPSTREAM_SESSIONS: dict[tuple[str, str], tuple[str | None, str]] = {}
_UPSTREAM_SEEN: dict[tuple[str, str], float] = {}  # 마지막 사용 시각 — 유휴 세션은 다시 초기화 (D19-②)
UPSTREAM_SESSION_IDLE_S = 1800.0
UPSTREAM_SESSION_MAX = 4096


def _parse_rpc(res: httpx.Response, rid) -> dict:
    ctype = res.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for block in res.text.split("\n\n"):
            data = "".join(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
            if not data:
                continue
            msg = json.loads(data)
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                return msg
        raise RpcError(UPSTREAM_ERROR, "업스트림 SSE 응답에 결과가 없습니다.")
    return res.json()


async def _post(srv: ServerRef, body: dict, headers: dict) -> httpx.Response:
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **headers}
    tls_kw = await _tls(srv)
    try:
        async with async_client(timeout=settings.upstream_timeout, **tls_kw) as http:
            return await http.post(srv.endpoint, json=body, headers=hdr)
    except httpx.HTTPError as exc:
        raise RpcError(UPSTREAM_ERROR, f"{srv.id} 연결 실패: {type(exc).__name__}", {"server": srv.id}) from exc


# 업스트림에 선언하는 클라이언트 능력 — 서버의 sampling·elicitation 요청은 mcp_consent 가 중계·동의로 처리 (SCR-C08)
UPSTREAM_CAPABILITIES = {"sampling": {}, "elicitation": {}}


async def _post_rpc(srv: ServerRef, body: dict, headers: dict, rid, ctx) -> tuple[httpx.Response, dict | None]:
    """요청을 보내고 SSE 응답을 흘려 읽는다 — 도중의 서버→클라이언트 요청(sampling·elicitation·roots·ping)에 답한다."""
    from app.services import mcp_consent
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **headers}
    # 동의(·검토) 대기 포함 — 사용자가 시간 연장(최대 10회, D14-③)을 해도 끊기지 않게
    wait = settings.upstream_timeout + 2 * settings.mcp_consent_timeout_s * (1 + governance.MAX_EXTENSIONS) + 30
    tls_kw = await _tls(srv)
    try:
        async with async_client(timeout=httpx.Timeout(settings.upstream_timeout, read=wait), **tls_kw) as http:
            async with http.stream("POST", srv.endpoint, json=body, headers=hdr) as res:
                if res.status_code >= 400:
                    await res.aread()
                    return res, None
                if "text/event-stream" not in res.headers.get("content-type", ""):
                    await res.aread()
                    return res, (res.json() if res.content else {})
                buf = ""
                async for chunk in res.aiter_text():
                    buf += chunk.replace("\r\n", "\n")
                    while "\n\n" in buf:
                        block, buf = buf.split("\n\n", 1)
                        data = "".join(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
                        if not data:
                            continue
                        msg = json.loads(data)
                        if "method" in msg and "id" in msg:  # 서버 → 클라이언트 요청
                            reply = await mcp_consent.handle(srv.id, ctx, msg)
                            await _post(srv, reply, headers)
                        elif msg.get("method") == "notifications/progress" and getattr(ctx, "progress", None):
                            await ctx.progress(msg.get("params") or {})  # 하류 요청의 progressToken 으로 다시 태그해 중계
                        elif msg.get("id") == rid and ("result" in msg or "error" in msg):
                            return res, msg
                raise RpcError(UPSTREAM_ERROR, "업스트림 SSE 응답에 결과가 없습니다.")
    except httpx.HTTPError as exc:
        raise RpcError(UPSTREAM_ERROR, f"{srv.id} 연결 실패: {type(exc).__name__}", {"server": srv.id}) from exc


def _drop_upstream(key: tuple[str, str]):
    """업스트림 세션과 그 유휴 시각을 함께 지운다 (D19-④) — 지운 세션 값(없으면 None)."""
    _UPSTREAM_SEEN.pop(key, None)
    return _UPSTREAM_SESSIONS.pop(key, None)


async def _ensure_upstream_session(srv: ServerRef, p: Principal, auth_hdr: dict) -> tuple[str | None, str]:
    key = (srv.id, p.sub)
    now = time.monotonic()
    if key in _UPSTREAM_SESSIONS and now - _UPSTREAM_SEEN.get(key, now) < UPSTREAM_SESSION_IDLE_S:
        _UPSTREAM_SEEN[key] = now
        return _UPSTREAM_SESSIONS[key]
    _drop_upstream(key)
    if len(_UPSTREAM_SESSIONS) >= UPSTREAM_SESSION_MAX:  # 가장 오래 쓰지 않은 것부터
        for old_key in sorted(_UPSTREAM_SEEN, key=_UPSTREAM_SEEN.get)[:len(_UPSTREAM_SESSIONS) - UPSTREAM_SESSION_MAX + 1]:
            _drop_upstream(old_key)
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": UPSTREAM_CAPABILITIES,
                       "clientInfo": {"name": "agentnexus-gateway", "version": "0.1.0"}}}
    res = await _post(srv, init, auth_hdr)
    if res.status_code >= 400:
        raise RpcError(UPSTREAM_ERROR, f"{srv.id} initialize 실패 ({res.status_code})",
                       {"server": srv.id, "status": res.status_code}, http_status=res.status_code)
    msg = _parse_rpc(res, 0)
    version = (msg.get("result") or {}).get("protocolVersion", MCP_PROTOCOL_VERSION)
    sid = res.headers.get("mcp-session-id")
    hdr = {**auth_hdr, "MCP-Protocol-Version": version, **({"Mcp-Session-Id": sid} if sid else {})}
    await _post(srv, {"jsonrpc": "2.0", "method": "notifications/initialized"}, hdr)
    _UPSTREAM_SESSIONS[key] = (sid, version)
    _UPSTREAM_SEEN[key] = time.monotonic()

    def _store():
        with SessionLocal() as db:
            db.add(McpSession(id=new_id("ups"), kind="upstream", server_id=srv.id, principal_sub=p.sub,
                              upstream_session_id=sid, protocol_version=version))
            db.commit()
    await run_in_threadpool(_store)
    return sid, version


def _progress_meta(ctx) -> dict:
    """하류가 progress 를 원하면(ctx.progress) 업스트림 요청에도 progressToken 을 단다."""
    return {"progressToken": new_id("prg")} if getattr(ctx, "progress", None) else {}


def _modern_upstream(srv: ServerRef) -> bool:
    if srv.transport != "streamable-http":
        return False
    return srv.protocol == "2026-07-28" or (srv.protocol in ("auto", None, "")
                                             and srv.upstream_protocol in mcp_modern.MODERN_VERSIONS)


def _save_protocol(server_id: str, version: str) -> None:
    with SessionLocal() as db:
        s = db.get(McpServer, server_id)
        if s is not None:
            s.upstream_protocol = version
            db.commit()


async def probe_protocol(srv: ServerRef, p: Principal) -> str:
    """server/discover 로 업스트림 세대 판별 — 2026-07-28 을 지원하면 modern, 아니면(오류·미지원) 레거시."""
    if srv.transport != "streamable-http" or not srv.endpoint or srv.protocol == "legacy":
        return MCP_PROTOCOL_VERSION
    try:
        auth_hdr = await upstream_token(srv, p, None)
        res = await _post(srv, {"jsonrpc": "2.0", "id": 0, "method": "server/discover",
                                "params": {"_meta": mcp_modern.request_meta({})}},
                          {**auth_hdr, **mcp_modern.request_headers("server/discover", {})})
        if res.status_code == 200:
            found = set((_parse_rpc(res, 0).get("result") or {}).get("supportedVersions") or [])
            if found & set(mcp_modern.MODERN_VERSIONS):
                return mcp_modern.MODERN_VERSIONS[0]
    except (RpcError, ValueError):
        pass
    return MCP_PROTOCOL_VERSION


class _LegacyOnly(Exception):
    """modern 요청에 UnsupportedProtocolVersion(-32022) — 레거시로 내려간다."""


MRTR_ROUNDS = 8


async def _modern_call(srv: ServerRef, p: Principal, method: str, params: dict | None, tool: str | None, ctx) -> dict:
    """modern 업스트림 — 요청마다 `_meta`(버전·능력)·헤더(Mcp-Method·Mcp-Name·Mcp-Param-*), 세션 없음.

    InputRequiredResult 면 inputRequests 를 레거시 서버 요청과 같은 mcp_consent.handle(중계·동의)로 답해
    inputResponses·requestState 로 다시 부른다(MRTR). 동의 거부·만료는 도구 오류 결과로 돌려준다(레거시와 같은 결과 모양)."""
    from app.services import mcp_consent
    scopes = _required_scopes(srv, tool) if method == "tools/call" else None
    schema = next((t.get("inputSchema") for t in srv.tools if t.get("name") == tool), None) if tool else None
    caps = UPSTREAM_CAPABILITIES if ctx is not None else {}
    params, reauth = dict(params or {}), True
    for rnd in range(MRTR_ROUNDS):
        auth_hdr = await upstream_token(srv, p, scopes)
        rid = rnd + 1
        body = {"jsonrpc": "2.0", "id": rid, "method": method,
                "params": {**params, "_meta": mcp_modern.request_meta(caps, _progress_meta(ctx).get("progressToken"))}}
        res, msg = await _post_rpc(srv, body, {**auth_hdr, **mcp_modern.request_headers(method, params, schema)}, rid, ctx)
        if res.status_code == 401 and reauth:
            reauth = False
            found = _pinned(srv, await discover_auth(srv, res.headers.get("www-authenticate")))
            srv.auth.update({k: v for k, v in found.items() if k != "type"})
            _TOKENS.pop((p.sub, f"mcp:{srv.id}", tuple(sorted(scopes or []))), None)
            continue
        if res.status_code == 403:
            raise RpcError(FORBIDDEN, f"{srv.id}: 업스트림 권한 부족", {"server": srv.id,
                                                                  "www_authenticate": res.headers.get("www-authenticate")})
        if res.status_code >= 400:
            try:
                err = (res.json() or {}).get("error") or {}
            except ValueError:
                err = {}
            if err.get("code") == mcp_modern.UNSUPPORTED_VERSION:
                raise _LegacyOnly()
            raise RpcError(UPSTREAM_ERROR, f"{srv.id}: 업스트림 오류 {res.status_code}"
                           + (f" — {err['message']}" if err.get("message") else ""),
                           {"server": srv.id, "status": res.status_code, **({"upstream_code": err["code"]} if "code" in err else {})})
        if "error" in msg:
            e = msg["error"]
            raise RpcError(e.get("code", UPSTREAM_ERROR), e.get("message", "upstream error"), e.get("data"))
        result = msg.get("result") or {}
        if result.get("resultType") != "input_required":
            return mcp_modern.strip(result)
        responses = {}
        for key, req in (result.get("inputRequests") or {}).items():
            out = await mcp_consent.handle(srv.id, ctx, {"id": key, "method": (req or {}).get("method"),
                                                         "params": (req or {}).get("params") or {}})
            if "error" in out:
                e = out["error"]
                if method == "tools/call":
                    return {"content": [{"type": "text", "text": f"{srv.id}: {(req or {}).get('method')} 요청이 처리되지 않아 "
                                                                 f"도구를 실행하지 못했습니다 — {e.get('message')}"}], "isError": True}
                raise RpcError(e.get("code", UPSTREAM_ERROR), e.get("message", "input refused"))
            responses[key] = out["result"]
        params = {k: v for k, v in params.items() if k not in ("inputResponses", "requestState")}
        if responses:
            params["inputResponses"] = responses
        elif result.get("requestState") is not None:
            await asyncio.sleep(0.5)  # 업스트림이 아직 처리 중 — 상태만 돌려줬다
        if result.get("requestState") is not None:
            params["requestState"] = result["requestState"]
    raise RpcError(UPSTREAM_ERROR, f"{srv.id}: 입력 요청 왕복 한도({MRTR_ROUNDS}) 초과")


async def _http_call(srv: ServerRef, p: Principal, method: str, params: dict | None, tool: str | None,
                     ctx=None) -> dict:
    if _modern_upstream(srv):
        try:
            return await _modern_call(srv, p, method, params, tool, ctx)
        except _LegacyOnly:
            if srv.protocol == "2026-07-28":
                raise RpcError(UPSTREAM_ERROR, f"{srv.id}: 업스트림이 MCP 2026-07-28 을 지원하지 않습니다.",
                               {"server": srv.id, "code": "protocol_unsupported"}) from None
            srv.upstream_protocol = MCP_PROTOCOL_VERSION
            await run_in_threadpool(_save_protocol, srv.id, MCP_PROTOCOL_VERSION)
    scopes = _required_scopes(srv, tool) if method == "tools/call" else None
    if prog := _progress_meta(ctx):
        params = {**(params or {}), "_meta": {**((params or {}).get("_meta") or {}), **prog}}
    for attempt in range(2):
        auth_hdr = await upstream_token(srv, p, scopes)
        sid, version = await _ensure_upstream_session(srv, p, auth_hdr)
        hdr = {**auth_hdr, "MCP-Protocol-Version": version, **({"Mcp-Session-Id": sid} if sid else {})}
        rid = 1
        res, msg = await _post_rpc(srv, {"jsonrpc": "2.0", "id": rid, "method": method,
                                         **({"params": params} if params else {})}, hdr, rid, ctx)
        if res.status_code == 401 and attempt == 0:
            found = _pinned(srv, await discover_auth(srv, res.headers.get("www-authenticate")))
            srv.auth.update({k: v for k, v in found.items() if k != "type"})
            _TOKENS.pop((p.sub, f"mcp:{srv.id}", tuple(sorted(scopes or []))), None)
            continue
        if res.status_code == 404 and sid and attempt == 0:
            _drop_upstream((srv.id, p.sub))  # 업스트림 세션 만료 → 재초기화
            continue
        if res.status_code == 403:
            raise RpcError(FORBIDDEN, f"{srv.id}: 업스트림 권한 부족", {"server": srv.id,
                                                                  "www_authenticate": res.headers.get(
                                                                      "www-authenticate")})
        if res.status_code >= 400:
            raise RpcError(UPSTREAM_ERROR, f"{srv.id}: 업스트림 오류 {res.status_code}",
                           {"server": srv.id, "status": res.status_code})
        if "error" in msg:
            e = msg["error"]
            raise RpcError(e.get("code", UPSTREAM_ERROR), e.get("message", "upstream error"), e.get("data"))
        return msg.get("result") or {}
    raise RpcError(UPSTREAM_ERROR, f"{srv.id}: 업스트림 인증 실패")


# ───────────────────────── hosted(OpenAPI) · stdio 전송 ─────────────────────────
_HOSTED: dict[str, object] = {}
_HOSTED_CLIENTS: dict[str, object] = {}  # cache_key → httpx2.AsyncClient — 설정이 바뀌거나 캐시를 비우면 닫는다 (D19-②)


def _close_hosted(keys: list[str]) -> None:
    """hosted 클라이언트 연결 풀 정리 — 실행 중인 루프가 있으면 aclose 를 예약(동기 경로에서도 부를 수 있게)."""
    clients = [c for k in keys if (c := _HOSTED_CLIENTS.pop(k, None)) is not None]
    for k in keys:
        _HOSTED.pop(k, None)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for c in clients:
        task = loop.create_task(c.aclose())
        _CLOSING.add(task)
        task.add_done_callback(_CLOSING.discard)


_CLOSING: set = set()


def stdio_allowed(command: list[str]) -> bool:
    """MCP_STDIO_ALLOWED_COMMANDS — 쉼표 구분. 공백 없는 항목은 실행 파일만 비교(기존), 공백이 있는 항목은 명령+인자 **전체 일치**
    (예: `npx -y @modelcontextprotocol/server-filesystem@0.6.2 /data`). 인터프리터(python·node·npx·uvx…)를 실행 파일만으로 허용하면
    인자로 임의 코드를 돌릴 수 있으니 운영은 전체 명령으로 고정한다 (D19-②)."""
    import shlex
    for entry in (e.strip() for e in settings.mcp_stdio_allowed_commands.split(",")):
        parts = shlex.split(entry) if entry else []
        if (len(parts) == 1 and command[:1] == parts) or (len(parts) > 1 and list(command) == parts):
            return True
    return False


def _dump(obj) -> dict:
    return obj.model_dump(mode="json", by_alias=True, exclude_none=True)


_HOSTED_TOKEN: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("anx_hosted_obo", default=None)


async def _inject_obo(request) -> None:
    """hosted-openapi OBO — 이 호출의 교환 토큰을 업스트림 요청에 주입 (서버 객체는 공유, 토큰은 호출별)."""
    tok = _HOSTED_TOKEN.get()
    if tok:
        request.headers["Authorization"] = f"Bearer {tok}"


async def _guard_hosted(request) -> None:
    """hosted-openapi 클라이언트(httpx2 — 팩토리 밖)도 요청마다 egress 가드 — 생성 때 1회 검사 뒤 이름이 메타데이터 주소로 바뀌어도 (D19-④)."""
    from app.core.http import blocked_url
    if await run_in_threadpool(blocked_url, str(request.url)):
        raise RpcError(UPSTREAM_ERROR, f"메타데이터·링크 로컬 주소로는 요청할 수 없습니다: {request.url.host}")


async def _hosted_server(srv: ServerRef):
    import httpx2
    from fastmcp import FastMCP
    cache_key = f"{srv.id}:{json.dumps(srv.server_json.get('_meta', {}).get('hosted', {}), sort_keys=True)}"
    if cache_key in _HOSTED:
        return _HOSTED[cache_key]
    hosted = (srv.server_json.get("_meta") or {}).get("hosted") or {}
    headers = {}
    if hosted.get("secret_ref"):
        def _sec():
            with SessionLocal() as db:
                return secrets.get_plain(db, hosted["secret_ref"])
        value = await run_in_threadpool(_sec)
        headers[hosted.get("auth_header", "Authorization")] = (f"Bearer {value}" if hosted.get("auth_header", "Authorization")
                                                               == "Authorization" else value)
    from app.core.http import _ASYNC_MOUNTS, blocked_url
    base = hosted["base_url"].rstrip("/")
    origin = "://".join(urlparse(base)[:2])
    mount = _ASYNC_MOUNTS.get(origin)
    if mount is None and await run_in_threadpool(blocked_url, base):  # 팩토리 밖 클라이언트 — 생성 전에 egress 가드
        raise RpcError(UPSTREAM_ERROR, f"{srv.id}: 메타데이터·링크 로컬 주소는 base_url 로 쓸 수 없습니다.", {"server": srv.id})
    transport = httpx2.ASGITransport(app=mount.app) if mount is not None and hasattr(mount, "app") else None
    obo = hosted.get("identity") == "obo"
    if obo:
        headers.pop(hosted.get("auth_header", "Authorization"), None)
    client = httpx2.AsyncClient(base_url=base, headers=headers, timeout=settings.upstream_timeout,
                                **({"transport": transport} if transport else {}),
                                event_hooks={"request": [_guard_hosted, *([_inject_obo] if obo else [])]})
    server = FastMCP.from_openapi(hosted["openapi"], client=client, name=srv.id)
    _close_hosted([k for k in list(_HOSTED) if k.startswith(f"{srv.id}:") and k != cache_key])  # 이전 설정의 클라이언트
    _HOSTED[cache_key] = server
    _HOSTED_CLIENTS[cache_key] = client
    return server


def _client_handlers(srv: ServerRef, ctx) -> dict:
    """stdio·hosted 서버의 sampling·elicitation 도 같은 중계·동의 경로로 (동의 채널이 없으면 능력을 선언하지 않는다)."""
    if ctx is None:
        return {}
    import mcp.types as mt

    from app.services import mcp_consent

    async def _ask(method: str, params) -> dict:
        out = await mcp_consent.handle(srv.id, ctx, {"id": "local", "method": method,
                                                     "params": params.model_dump(mode="json", by_alias=True,
                                                                                 exclude_none=True)})
        if "error" in out:
            raise RuntimeError(out["error"]["message"])
        return out["result"]

    async def sampling(_messages, params, _context):
        return mt.CreateMessageResult(**await _ask("sampling/createMessage", params))

    async def elicitation(_message, _response_type, params, _context):
        return mt.ElicitResult(**await _ask("elicitation/create", params))
    return {"sampling_handler": sampling, "elicitation_handler": elicitation}


async def _client_call(srv: ServerRef, method: str, params: dict | None, ctx=None) -> dict:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    if srv.transport == "hosted-openapi":
        target = await _hosted_server(srv)
    else:
        if not srv.command or not stdio_allowed(srv.command):
            raise RpcError(FORBIDDEN, f"stdio 명령이 허용 목록에 없습니다: {srv.command[:1]}",
                           {"code": "stdio_not_allowed"})
        target = StdioTransport(command=srv.command[0], args=srv.command[1:])
    params = params or {}
    async with Client(target, **_client_handlers(srv, ctx)) as c:
        if method == "tools/list":
            return {"tools": [_dump(t) for t in (await c.list_tools_mcp()).tools]}
        if method == "tools/call":
            return _dump(await c.call_tool_mcp(params["name"], params.get("arguments") or {}))
        if method == "resources/list":
            return {"resources": [_dump(r) for r in (await c.list_resources_mcp()).resources]}
        if method == "resources/templates/list":
            return {"resourceTemplates": [_dump(r) for r in (await c.list_resource_templates_mcp()).resourceTemplates]}
        if method == "resources/read":
            return _dump(await c.read_resource_mcp(params["uri"]))
        if method == "prompts/list":
            return {"prompts": [_dump(x) for x in (await c.list_prompts_mcp()).prompts]}
        if method == "prompts/get":
            return _dump(await c.get_prompt_mcp(params["name"], params.get("arguments") or {}))
        if method == "ping":
            await c.ping()
            return {}
    raise RpcError(NOT_FOUND, f"지원하지 않는 메서드: {method}")


async def call_server(srv: ServerRef, p: Principal, method: str, params: dict | None = None, ctx=None) -> dict:
    """ctx(RpcContext) 가 있으면 서버의 sampling·elicitation 요청을 중계·동의로 처리, 없으면(시스템 호출) 거절."""
    tool = (params or {}).get("name") if method == "tools/call" else None
    hosted = (srv.server_json.get("_meta") or {}).get("hosted") or {}
    if srv.transport == "hosted-openapi" and hosted.get("identity") == "obo" and method == "tools/call":
        def _ex():
            with SessionLocal() as db:
                return identity.exchange(db, p, f"api:{srv.id}", hosted.get("scopes") or None, actor="gateway")
        try:
            tok = await run_in_threadpool(_ex)
        except Exception as exc:  # noqa: BLE001 — 교환 실패 = 호출하지 않음 (MSG-019)
            raise RpcError(-32003, getattr(exc, "detail", str(exc)), {"code": "token_exchange_failed"}, http_status=403) from exc
        reset = _HOSTED_TOKEN.set(tok["access_token"])
        try:
            return await _client_call(srv, method, params, ctx)
        finally:
            _HOSTED_TOKEN.reset(reset)
    if srv.transport in ("hosted-openapi", "stdio"):
        return await _client_call(srv, method, params, ctx)
    if not srv.endpoint:
        raise RpcError(UPSTREAM_ERROR, f"{srv.id}: endpoint 미설정")
    return await _http_call(srv, p, method, params, tool, ctx)


# ───────────────────────── 서버 조회·도구 갱신 ─────────────────────────
def load_targets(db: Session, server_id: str, p: Principal) -> tuple[str, list[ServerRef], dict | None]:
    """('single'|'virtual'|'all', 서버들, 가상 멤버 도구 제한)."""
    if server_id == ALL:
        rows = db.scalars(select(McpServer).where(McpServer.status == "active").order_by(McpServer.id)).all()
        return "all", [ServerRef.of(s) for s in rows], None
    srv = db.get(McpServer, server_id)
    if srv is not None:
        if srv.status != "active":
            raise RpcError(FORBIDDEN, f"MCP 서버 {server_id} 비활성", {"code": "server_inactive"}, http_status=403)
        return "single", [ServerRef.of(srv)], None
    vs = db.get(VirtualServer, server_id)
    if vs is not None:
        refs, limits = [], {}
        for m in vs.members or []:
            s = db.get(McpServer, m.get("server_id"))
            if s is not None and s.status == "active":
                refs.append(ServerRef.of(s))
                limits[s.id] = m.get("tools")
        return "virtual", refs, limits
    raise RpcError(NOT_FOUND, f"MCP 서버를 찾을 수 없습니다: {server_id}", http_status=404)


async def refresh_tools(server_id: str, p: Principal = SYSTEM) -> dict:
    """도구 목록·인증 발견·헬스 갱신 + 기본 도구 규칙(정책 기본값) 적용 — 즉시 사용(FR-W04)."""
    def _load():
        with SessionLocal() as db:
            s = db.get(McpServer, server_id)
            if s is None:
                raise RpcError(NOT_FOUND, f"server {server_id}")
            return ServerRef.of(s)
    srv = await run_in_threadpool(_load)
    t0 = time.perf_counter()
    found, error, tools, error_code = {}, None, [], None
    try:
        if srv.transport == "streamable-http" and srv.auth.get("type") == "oauth2":
            if not srv.auth.get("authorization_server"):
                found = await discover_auth(srv)
                srv.auth.update({k: v for k, v in found.items() if k != "type"})

            def _pol():
                with SessionLocal() as db:
                    s = db.get(McpServer, server_id)
                    s.auth = {**(s.auth or {}), **{k: v for k, v in found.items() if k != "type"}}
                    if (s.auth.get("authorization_server") or security.issuer()) == security.issuer():
                        ensure_exchange_policy(db, s)
                    db.commit()
            await run_in_threadpool(_pol)
        _drop_upstream((srv.id, p.sub))
        if srv.transport == "streamable-http":
            srv.upstream_protocol = await probe_protocol(srv, p)
        tools = (await call_server(srv, p, "tools/list")).get("tools", [])
    except RpcError as exc:
        error, error_code = exc.message, (exc.data or {}).get("code")
    ms = round((time.perf_counter() - t0) * 1000, 1)

    def _save():
        with SessionLocal() as db:
            s = db.get(McpServer, server_id)
            s.health_checked_at = utcnow()
            if srv.upstream_protocol:
                s.upstream_protocol = srv.upstream_protocol
            if error:
                s.health, s.health_detail = "unhealthy", error[:500]
            else:
                s.tools, s.tools_refreshed_at, s.health, s.health_detail = tools, utcnow(), "healthy", f"{ms}ms"
                governance.default_rules(db, s.id, tools)
            db.commit()
    await run_in_threadpool(_save)
    return {"server_id": server_id, "health": "unhealthy" if error else "healthy", "error": error,
            "error_code": error_code,
            "tools": [t.get("name") for t in tools], "latency_ms": ms, "auth": {k: v for k, v in found.items()},
            "protocol": srv.upstream_protocol}


async def test_config(server_id: str, p: Principal, candidate: dict) -> dict:
    """설정 적용 전 시험 (SCR-F03) — 후보 엔드포인트·인증으로 PRM/AS 발견 · tools/list · 스코프 최소화 점검. 저장하지 않는다."""
    from app.services.review import scope_kind, tool_risk

    def _load():
        with SessionLocal() as db:
            s = db.get(McpServer, server_id)
            if s is None:
                raise RpcError(NOT_FOUND, f"server {server_id}")
            return ServerRef.of(s)
    srv = await run_in_threadpool(_load)
    before = {t.get("name") for t in srv.tools}
    if candidate.get("endpoint"):
        srv.endpoint = candidate["endpoint"]
    srv.auth = {**srv.auth, **(candidate.get("auth") or {})}
    if candidate.get("secret"):
        srv.auth["_candidate_secret"] = candidate["secret"]
    checks: list[dict] = []

    def add(key, ok, detail, warn=False):
        checks.append({"key": key, "ok": bool(ok), "level": "ok" if ok else ("warn" if warn else "fail"), "detail": detail})
    t0 = time.perf_counter()
    kind = srv.auth.get("type", "none")
    if srv.transport == "streamable-http" and kind == "oauth2":
        try:
            found = await discover_auth(srv)
        except RpcError as exc:
            found = {}
            add("prm", False, exc.message, warn=bool(srv.auth.get("authorization_server")))
        if found:
            add("prm", True, f"보호 리소스 메타데이터(RFC 9728) {found.get('resource_metadata')}")
            srv.auth.update({k: v for k, v in found.items() if k != "type"})
        elif not any(c["key"] == "prm" for c in checks):
            add("prm", False, "보호 리소스 메타데이터 없음 — 인가 서버를 직접 지정해야 합니다",
                warn=bool(srv.auth.get("authorization_server")))
        as_url = (srv.auth.get("authorization_server") or security.issuer()).rstrip("/")
        if as_url == security.issuer():
            add("authorization_server", True, f"플랫폼 IdP {as_url} — 토큰 교환(RFC 8693)으로 위임")
        else:
            add("authorization_server", bool(srv.auth.get("token_endpoint")),
                f"외부 인가 서버 {as_url} · token_endpoint {srv.auth.get('token_endpoint') or '없음 (RFC 8414 메타데이터 조회 실패)'}")
    elif kind in ("bearer", "api_key"):
        add("credential", bool(srv.auth.get("_candidate_secret") or srv.auth.get("secret_ref")),
            "백엔드 자격 증명 " + ("(후보 값으로 시험)" if srv.auth.get("_candidate_secret") else
                                   "저장된 값" if srv.auth.get("secret_ref") else "없음"))
    if candidate.get("protocol"):
        srv.protocol = candidate["protocol"]
    if srv.transport == "streamable-http":
        srv.upstream_protocol = await probe_protocol(srv, p)
        modern = _modern_upstream(srv)
        add("protocol", modern or srv.protocol != "2026-07-28",
            "MCP 2026-07-28 — 세션 없이 요청마다 호출 (server/discover)" if modern else
            f"MCP {srv.upstream_protocol} — initialize·세션" + (" (업스트림이 2026-07-28 미지원)" if srv.protocol == "2026-07-28" else ""))
    key = (srv.id, p.sub)
    saved_seen = _UPSTREAM_SEEN.get(key)
    saved = _drop_upstream(key)
    tools: list[dict] = []
    try:
        tools = (await call_server(srv, p, "tools/list")).get("tools", [])
        add("tools_list", True, f"tools/list {len(tools)}개 · {round((time.perf_counter() - t0) * 1000)}ms")
    except RpcError as exc:
        add("tools_list", False, f"tools/list 실패 — {exc.message}")
    finally:
        _drop_upstream(key)  # 후보 설정 세션은 버리고 기존 세션 복원
        if saved:
            _UPSTREAM_SESSIONS[key] = saved
            _UPSTREAM_SEEN[key] = saved_seen or time.monotonic()
    needed = set((srv.auth.get("tool_scopes") or {}).values())
    requested = set(srv.auth.get("scopes") or [])
    extra = sorted(requested - needed) if needed and requested else []
    broad = sorted(s for s in (requested or needed) if scope_kind(s) == "admin")
    add("scopes", not extra and not broad,
        "최소 스코프 ✓" if not extra and not broad else
        "; ".join(x for x in (f"도구가 쓰지 않는 스코프 {extra}" if extra else "",
                              f"관리 스코프 {broad} — 꼭 필요한지 확인" if broad else "") if x), warn=True)
    names = [t.get("name") for t in tools]
    return {"server_id": server_id, "ok": not any(c["level"] == "fail" for c in checks), "checks": checks,
            "tools": [{"name": n, "risk": tool_risk(n, (t.get("annotations") or {}))} for n, t in zip(names, tools)],
            "added": sorted(set(names) - before) if tools else [], "removed": sorted(before - set(names)) if tools else [],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), "applied": False}


async def _tools_of(srv: ServerRef) -> list[dict]:
    fresh = srv.tools_refreshed_at and time.time() - srv.tools_refreshed_at < TOOLS_TTL_S
    if srv.tools and fresh:
        return srv.tools
    res = await refresh_tools(srv.id)
    if res["error"] and srv.tools:
        return srv.tools  # 장애 시 마지막 목록
    if res["error"]:
        code = res.get("error_code")
        raise RpcError(FORBIDDEN if code == "mtls_required" else UPSTREAM_ERROR, res["error"],
                       {"server": srv.id, **({"code": code} if code else {})})

    def _load():
        with SessionLocal() as db:
            return list(db.get(McpServer, srv.id).tools or [])
    return await run_in_threadpool(_load)


def _visible(db: Session, srv_id: str, tools: list[dict], agent: str | None, limit: list | None) -> list[dict]:
    rules = {r.tool: r for r in db.scalars(select(ToolRule).where(ToolRule.server_id == srv_id))}
    profile = db.get(AgentProfile, agent.split("/")[0]) if agent else None
    out = []
    for t in tools:
        r = rules.get(t.get("name"))
        if r is None or not r.allowed:
            continue  # 기본 거부: 규칙 없는 도구는 노출하지 않는다
        if limit and t["name"] not in limit:
            continue
        q = f"{srv_id}.{t['name']}"
        if profile is not None and not any(fnmatch.fnmatchcase(q, pat) for pat in profile.tools or []):
            continue
        meta = {**(t.get("_meta") or {}), "io.agentnexus/risk": r.risk, "io.agentnexus/approval": r.approval_required,
                "io.agentnexus/server": srv_id}
        out.append({**t, "_meta": meta})
    return out


# ───────────────────────── 메서드 처리 ─────────────────────────
@dataclass
class RpcContext:
    principal: Principal
    agent: str | None = None
    data_class: str = "internal"
    approval_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    relay: object | None = None  # mcp_consent.Relay — 하류 클라이언트로 서버 요청 중계
    progress: object | None = None  # async (업스트림 progress params) → 하류 notifications/progress
    state_id: str | None = None  # modern MRTR 상태 id (progress 큐 연결)


async def list_tools(server_id: str, ctx: RpcContext) -> list[dict]:
    mode, refs, limits = await run_in_threadpool(_load_targets, server_id, ctx.principal)
    out = []
    for srv in refs:
        try:
            tools = await _tools_of(srv)
        except RpcError:
            if mode == "single":
                raise
            continue  # 집계 중 일부 장애는 제외
        visible = await run_in_threadpool(_visible_db, srv.id, tools, ctx.agent, (limits or {}).get(srv.id))
        for t in visible:
            out.append({**t, "name": f"{srv.id}.{t['name']}"} if mode != "single" else t)
    return out


def _load_targets(server_id: str, p: Principal):
    with SessionLocal() as db:
        return load_targets(db, server_id, p)


def _visible_db(srv_id, tools, agent, limit):
    with SessionLocal() as db:
        return _visible(db, srv_id, tools, agent, limit)


def _resolve_tool(mode: str, refs: list[ServerRef], name: str) -> tuple[ServerRef, str]:
    if mode == "single":
        return refs[0], name
    ns, _, tool = name.partition(".")
    for s in refs:
        if s.id == ns and tool:
            return s, tool
    raise RpcError(INVALID_PARAMS, f"알 수 없는 도구: {name} (집계 도구명은 '<서버>.<도구>')")


def tool_schema(server_id: str, p: Principal, name) -> dict | None:
    """게이트웨이가 노출하는 도구의 inputSchema (x-mcp-header 검증용) — 모르는 도구는 None (호출 단계에서 거부)."""
    mode, refs, _ = _load_targets(server_id, p)
    if not isinstance(name, str):
        return None
    try:
        srv, tool = _resolve_tool(mode, refs, name)
    except RpcError:
        return None
    return next((t.get("inputSchema") for t in srv.tools if t.get("name") == tool), None)


async def call_tool(server_id: str, ctx: RpcContext, name: str, arguments: dict) -> dict:
    mode, refs, limits = await run_in_threadpool(_load_targets, server_id, ctx.principal)
    srv, tool = _resolve_tool(mode, refs, name)
    if limits and limits.get(srv.id) and tool not in limits[srv.id]:
        raise RpcError(FORBIDDEN, f"가상 서버 {server_id} 에 포함되지 않은 도구: {name}", {"code": "tool_not_in_virtual"})
    if not srv.tools:
        await _tools_of(srv)  # 인자 스키마 검증용

    def _check():
        with SessionLocal() as db:
            d = governance.check_tool(db, ctx.principal, srv.id, tool, arguments, agent=ctx.agent,
                                      data_class=ctx.data_class, approval_id=ctx.approval_id,
                                      session_id=ctx.session_id, run_id=ctx.run_id, step_id=ctx.step_id)
            ap = d.approval
            # FR-G04 도구 단위 한도 — 허용된 호출만 버킷 차감 (target = "<서버>.<도구>", 패턴 예: "jira.*")
            lim = ratelimit.check(db, ctx.principal, "tool", f"{srv.id}.{tool}", ctx.agent) if d.allow else None
            return d, ({"approval_id": ap.id, "approver": ap.approver, "expires_at": ap.expires_at.isoformat()}
                       if ap is not None and d.code == "approval_required" else None), lim
    dec, ap_info, lim = await run_in_threadpool(_check)
    qualified = f"{srv.id}.{tool}"
    if lim is not None and not lim.allowed:
        metrics.POLICY_DENIES.labels("tool", "rate_limited").inc()
        await run_in_threadpool(lambda: audit.append(
            "gateway.tool.limit", actor=ctx.principal.sub, actor_kind=ctx.principal.kind, target=qualified,
            decision="rate_limited", data={"reason": lim.reason, "limit_id": lim.limit_id, "agent": ctx.agent}))
        raise RpcError(RATE_LIMITED, lim.reason or "한도 초과",
                       {"code": "rate_limited", "retry_after": lim.retry_after, "limit_id": lim.limit_id}, http_status=429)
    with span(f"mcp tools/call {qualified}", SpanKind.CLIENT, **{
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": qualified, "mcp.method.name": "tools/call",
            "mcp.server": srv.id, "anx.decision": dec.code, "anx.risk": dec.risk, "anx.agent": ctx.agent}):
        return await _call_tool_inner(srv, ctx, tool, qualified, arguments, dec, ap_info, mode, server_id)


async def _call_tool_inner(srv, ctx, tool, qualified, arguments, dec, ap_info, mode, server_id) -> dict:
    if not dec.allow:
        if dec.code == "approval_required":
            raise RpcError(APPROVAL_REQUIRED, dec.reason, {"code": dec.code, "risk": dec.risk, "tool": qualified,
                                                           **(ap_info or {})})
        metrics.POLICY_DENIES.labels("tool", dec.code).inc()
        logs.event("warning", "gateway", f"도구 호출 거부: {qualified}", code=dec.code, reason=dec.reason,
                   agent=ctx.agent)
        await run_in_threadpool(lambda: audit.append(
            "gateway.tool.deny", actor=ctx.principal.sub, actor_kind=ctx.principal.kind, target=qualified,
            decision=dec.code, data={"reason": dec.reason, "agent": ctx.agent}))
        raise RpcError(FORBIDDEN, dec.reason, {"code": dec.code, "tool": qualified, "risk": dec.risk})
    t0 = time.perf_counter()
    status = "ok"
    try:
        result = await call_server(srv, ctx.principal, "tools/call", {"name": tool, "arguments": arguments}, ctx)
        if result.get("isError"):
            status = "tool_error"
    except Exception:  # RpcError 외(stdio·hosted 의 일반 예외)도 실패로 기록 — 사용량·지표가 ok 로 남지 않게
        status = "error"
        raise
    finally:
        ms = int((time.perf_counter() - t0) * 1000)
        metrics.TOOL_CALLS.labels(srv.id, tool, status).inc()
        await run_in_threadpool(_record_tool, ctx, qualified, srv.id, ms, status, dec, mode, server_id)

    def _sanitize():
        with SessionLocal() as db:
            return guards.sanitize_tool_result(db, result) if dec.sanitize else (result, [])
    clean, findings = await run_in_threadpool(_sanitize)
    if dec.risk == "high" or findings:
        await run_in_threadpool(lambda: audit.append(
            "gateway.tool.call", actor=ctx.principal.sub, actor_kind=ctx.principal.kind, target=qualified,
            decision="allow", data={"risk": dec.risk, "approval": dec.approval.id if dec.approval else None,
                                    "sanitized": [f.as_dict() for f in findings], "agent": ctx.agent}))
    return clean


def _record_tool(ctx: RpcContext, qualified: str, srv_id: str, ms: int, status: str, dec, mode: str, requested: str):
    with SessionLocal() as db:
        db.add(UsageRecord(id=new_id("use"), trace_id=current_trace_id(), session_id=ctx.session_id,
                           run_id=ctx.run_id, step_id=ctx.step_id, user_sub=ctx.principal.sub, team=ctx.principal.team, agent=ctx.agent,
                           kind="tool", target=qualified, provider=srv_id, latency_ms=ms, status=status,
                           policy_decisions=[{"code": dec.code, "risk": dec.risk, "policy": dec.policy.get("policy_id")}]))
        if mode != "single":
            db.add(RoutingDecision(id=new_id("rd"), trace_id=current_trace_id(), kind="mcp", subject=ctx.principal.sub,
                                   requested=f"{requested}:{qualified}", chosen=srv_id, candidates=[srv_id],
                                   reason=f"{mode} 서버 네임스페이스 라우팅", status=status))
        db.commit()


async def generic(server_id: str, ctx: RpcContext, method: str, params: dict | None) -> dict:
    """resources/* · prompts/* · ping — 집계 대상이면 서버별로 합치거나 순서대로 시도."""
    mode, refs, _ = await run_in_threadpool(_load_targets, server_id, ctx.principal)
    if method in ("resources/list", "resources/templates/list", "prompts/list"):
        key = {"resources/list": "resources", "resources/templates/list": "resourceTemplates",
               "prompts/list": "prompts"}[method]
        items = []
        for srv in refs:
            try:
                res = await call_server(srv, ctx.principal, method, params)
            except RpcError:
                if mode == "single":
                    raise
                continue
            for it in res.get(key, []):
                it = {**it, "_meta": {**(it.get("_meta") or {}), "io.agentnexus/server": srv.id}}
                if mode != "single" and key == "prompts":
                    it["name"] = f"{srv.id}.{it['name']}"
                items.append(it)
        return {key: items}
    if method == "prompts/get" and mode != "single":
        srv, name = _resolve_tool(mode, refs, (params or {}).get("name", ""))
        return await call_server(srv, ctx.principal, method, {**(params or {}), "name": name}, ctx)
    last: RpcError | None = None
    for srv in refs:
        try:
            res = await call_server(srv, ctx.principal, method, params, ctx)
            if method == "resources/read":
                def _san():
                    with SessionLocal() as db:
                        for c in res.get("contents", []):
                            if "text" in c:
                                c["text"] = guards.sanitize_tool_text(db, c["text"]).text
                        return res
                res = await run_in_threadpool(_san)
            return res
        except RpcError as exc:
            last = exc
    raise last or RpcError(NOT_FOUND, "대상 서버 없음")


# ───────────────────────── 클라이언트 세션 ─────────────────────────
def negotiate(requested: str | None) -> str:
    return requested if requested in MCP_SUPPORTED_VERSIONS else MCP_PROTOCOL_VERSION


def open_session(server_id: str, p: Principal, params: dict) -> tuple[str, dict]:
    version = negotiate(params.get("protocolVersion"))
    sid = token_urlsafe(24)
    with SessionLocal() as db:
        db.add(McpSession(id=sid, kind="client", server_id=server_id, principal_sub=p.sub, protocol_version=version,
                          client_info={**(params.get("clientInfo") or {}),
                                       "_capabilities": params.get("capabilities") or {}},
                          events=[{"type": "opened", "at": utcnow().isoformat(), "protocol": version}]))
        db.commit()
    result = {"protocolVersion": version,
              "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False},
                               "prompts": {"listChanged": False}, "logging": {}},
              "serverInfo": {**GATEWAY_INFO, "name": f"agentnexus-gateway/{server_id}"},
              "instructions": "AgentNexus AI Gateway — 정책·승인·감사가 적용된 MCP 프록시입니다."}
    return sid, result


def check_session(sid: str, server_id: str, p: Principal) -> bool:
    with SessionLocal() as db:
        s = db.get(McpSession, sid)
        if s is None or s.kind != "client" or s.closed_at or s.server_id != server_id or s.principal_sub != p.sub:
            return False
        s.last_used_at = utcnow()
        db.commit()
        return True


def session_capabilities(sid: str) -> dict:
    with SessionLocal() as db:
        s = db.get(McpSession, sid)
        return ((s.client_info or {}).get("_capabilities") or {}) if s else {}


def close_session(sid: str, p: Principal) -> bool:
    with SessionLocal() as db:
        s = db.get(McpSession, sid)
        if s is None or s.principal_sub != p.sub:
            return False
        s.closed_at = utcnow()
        s.events = (s.events or [])[-19:] + [{"type": "closed", "at": utcnow().isoformat(), "by": "client"}]
        db.commit()
        return True
