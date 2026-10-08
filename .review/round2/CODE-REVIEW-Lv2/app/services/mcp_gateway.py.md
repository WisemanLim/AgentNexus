> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [mcp_gateway.py.md](../../../../CODE-REVIEW-Lv2/app/services/mcp_gateway.py.md)

### 08. app/services/mcp_gateway.py
**파일 요약 / File Summary**
- 역할 / Role: MCP 프록시 핵심. 클라이언트 MCP 세션 종료(terminate)·업스트림 세션 별도 관리, 대상 해석(단일·가상·`_all` 집계), 전송별 호출(streamable-http legacy / modern 2026-07-28 MRTR, hosted-openapi, stdio), 업스트림 인증 주입(bearer·api_key 시크릿, RFC 8693 교환, 외부 AS), RFC 9728/8414 발견, 도구 목록 갱신·헬스, 거버넌스 판정·한도·승인·소독·사용량 기록, 클라이언트 세션 수명.
- 진입점 거리 / Distance: 1 (`app/main.py:44,77` 지연 import — 기동 갱신·헬스 루프) · 2 (`app/api/mcp.py:27` → `/mcp/{id}`·관리 API, `app/api/catalog.py:19`, `app/api/ops.py:248,579`, `app/api/chat.py:597`)
- LOC: 1096 — `max_lines_per_file`(600) 초과 → **논리 블록 단위로 분석(모든 블록 커버)** · 1차 리뷰 시점 1041(스냅샷 1031) → +55
- **스냅샷 이후 소스 변경 주의 / Post-snapshot edit**: 리뷰 도중 21:30:59 에 Epic 51(D19-④) 수정이 이 파일에 적용되어 현재 디스크는 1110줄(sha1 `deb4d2d…`)이다. 이 보고서의 `mcp_gateway.py` 라인·인용은 모두 **21:03 스냅샷**(sha1 `658f455…`, `scratchpad/snapshot-start.sha`) 기준 — 현재 파일에 수정 스크립트(`scratchpad/e51-patch-mcp.py`)를 역적용해 같은 해시를 재현하고 인용 대조함. 스냅샷→현재 라인 대응: L1-320 동일 · L321-328 +6 · L332-552 +5 · L553-798 +12 · L799-809 +13 · L810-1096 +14 (L329-331·L799·L807-809 는 수정 구간). 반영된 내용: (1) `_drop_upstream`(현재 L321-325) — 세션과 `_UPSTREAM_SEEN` 을 함께 제거(아래 L320-355 블록의 "`_UPSTREAM_SEEN` 미정리" 지적·delegated CR2-04 → C51-05). (2) `_guard_hosted`(현재 L560-564, 훅 등록 L594) — hosted-openapi 요청마다 egress 가드(아래 L555-589 블록의 "L574 생성 시 1회" 지적·delegated SEC2-01 → C51-01). 파일 밖: `app/services/secrets.py` 가 21:32:38 에 `DESTINATION_KEYS`·`destination_changed`·외부 AS 교환(`external_obo`) 판정을 추가(현재 `:31-51`)했으나 21:43 확인 시점에 `app/api/mcp.py`(최종 수정 17:48)는 아직 이를 부르지 않았다 — 아래 L124-182 [위험:높음] 2건은 스냅샷 기준 미해결로 두고, 수정 완료 여부는 재확인 대상이다.
- 주요 export: `RpcError`, `ServerRef`, `RpcContext`, `upstream_token`, `discover_auth`, `call_server`, `refresh_tools`, `test_config`, `list_tools`, `call_tool`, `generic`, `tool_schema`, `load_targets`·`_load_targets`, `stdio_allowed`, `open_session`·`check_session`·`close_session`·`session_capabilities`, `clear_caches`, `ensure_exchange_policy`, 오류 코드 상수
- 의존(내부): `app.core.{logs,metrics,security,auth,config,context,http,tracing,ids}`, `app.db`, `app.models`, `app.services.{audit,governance,guards,identity,mcp_modern,ratelimit,secrets}`, (지연) `app.services.{mcp_consent,tls,review}`
- 외부 패키지: httpx, httpx2(fastmcp 전이 의존), fastmcp 4.0.11(`uv.lock:574-575` — Client·StdioTransport·FastMCP.from_openapi), mcp.types, starlette(`run_in_threadpool`), opentelemetry, sqlalchemy
- 부수효과 / Side effects: 업스트림 HTTP·하위 프로세스(stdio), DB 읽기·쓰기(세션·사용량·결정 로그·도구 규칙·서버 상태·교환 정책), 프로세스 전역 캐시 `_TOKENS`·`_UPSTREAM_SESSIONS`·`_UPSTREAM_SEEN`·`_HOSTED`·`_HOSTED_CLIENTS`·`_CLOSING`
- 동시성 / Concurrency: asyncio + 동기 DB 는 `run_in_threadpool`(일관). 전역 dict 캐시는 락 없음·워커 로컬. 헬스 루프는 리스 보유 워커만(`app/main.py:83`).
- 테스트 커버리지 흔적: `test/test_mcp.py:37,75,104,118,135,149,173,185,203,221,237,277`; `test/test_review_fixes.py:224-235`(토큰 캐시 정리), `:246-283`(시크릿 참조·전송처), `:285-293`(AS 고정), `:423-436`(C39-21 일반 예외 상태); `test/test_lv2_hardening.py:114-121`(stdio 허용 목록), `:124-146`(hosted 클라이언트 닫기), `:148-175`(업스트림 세션 유휴 재초기화); `test/test_review.py:262-268`·`test/test_mcp_modern_ext.py:78`(test-config); `test/test_design_gaps.py:90`(hosted OBO); `test/test_mtls.py`.
- 2차 변경 요약 / Changes since 1st review: (1) `_pinned`(L196-204) — 401 재탐색이 등록과 다른 AS 를 내밀면 거부, 두 401 경로(L419·L485)에 적용(Epic 39). (2) `_call_tool_inner` 가 `except Exception` 으로 모든 실패를 `error` 로 기록(L976-978, Epic 39). (3) 업스트림 세션 캐시 유휴 1800초·상한 4096·LRU 정리(`_UPSTREAM_SEEN`, L250-253·L321-331, Epic 49). (4) hosted 클라이언트 보관·닫기(`_HOSTED_CLIENTS`·`_close_hosted`·`_CLOSING`, L507-526·L584-586, Epic 49) + `clear_caches` 가 닫기까지(L97-101). (5) `stdio_allowed`(L529-538) — 공백 있는 항목은 명령+인자 전체 일치(Epic 49). (6) 파일 밖 — API 계층 `secrets.check_refs`·`check_destination`(`app/api/mcp.py:403-405,429-436`), `policy.caller_context`(`app/api/mcp.py:58`, Epic 40), 헬스 루프 리스(`app/main.py:83`, Epic 45).

## 블록 리뷰 / Block Walk (Level 2)

```python path=app/services/mcp_gateway.py lines=1-47
"""MCP Requests 프록시 (FR-G09·G00·G07·I02·I03, FR-A03 원격 경로, FR-S00).

게이트웨이는 클라이언트 MCP 세션을 직접 종료(terminate)하고, 업스트림 서버별 세션을 따로 관리한다.
- 대상: 단일 서버(/mcp/{id}) · 가상 서버(VirtualServer) · 전체 집계(/mcp/_all, 도구명 `{ns}.{tool}`)
- 전송: streamable-http(원격) · hosted-openapi(OpenAPI→도구, 게이트웨이 내부 호스팅) · stdio(허용 명령만)
- 인증: 업스트림이 OAuth 2.1 리소스 서버면 RFC 9728 PRM 으로 인가 서버를 발견하고, 내장 STS 면 RFC 8693 으로
  도구별 최소 스코프 토큰을 교환해 주입한다(사용자 신원 전파). 외부 AS 는 token-exchange 또는 서비스 계정.
- 세대: 업스트림이 MCP 2026-07-28 을 지원하면(server/discover 로 발견, 서버별 protocol=auto|legacy|2026-07-28)
  initialize·세션 없이 요청마다 `_meta`·헤더로 호출하고, InputRequiredResult 는 같은 중계·동의 경로로 답해 재시도한다(D14-②).
"""
# … (L11-36 생략 — asyncio·contextvars·fnmatch·json·time·dataclasses·urlparse, httpx·sqlalchemy·starlette·opentelemetry, app.core·app.db·app.models·app.services import)

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
- L1-10: 설계 요약 — 게이트웨이가 세션을 직접 종료(중계자가 세션을 투명 전달하지 않음), 내장 STS 면 도구별 최소 스코프 교환 토큰, 외부 AS 는 교환 또는 서비스 계정.
- L22(생략 구간) `run_in_threadpool`: 동기 SQLAlchemy 를 이벤트 루프 밖 스레드에서 — 이 파일 전체의 기본 패턴.
- L38-46: 서버 정보, 집계 id, 도구 캐시 TTL 300초, 시스템 호출 주체 `SYSTEM`, JSON-RPC 표준·확장 오류 코드.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L41 `SYSTEM` 은 roles=["admin"] 이다. 헬스 루프·`_tools_of` 가 이 주체로 내장 STS 교환을 하면(`refresh_tools` 기본값 L693 → L381 `upstream_token`) `identity.exchange` 가 `roles` 를 교환 토큰 클레임에 그대로 싣는다(`app/services/identity.py:199-201`). 업스트림이 `roles` 클레임으로 권한을 판단하면 헬스 점검 호출이 admin 으로 보인다. 최소 수정: SYSTEM 은 roles 를 비우거나 교환 시 `roles` 클레임을 audience 정책으로 제한. 또 `kind="workload"` 라 `has()` 가 스코프 없이는 거부한다(1차 지적, 수용).

```python path=app/services/mcp_gateway.py lines=48-84

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
- L49-58 `RpcError`: JSON-RPC 오류 + HTTP 상태(기본 200 — JSON-RPC 관례).
- L61-82 `ServerRef`: ORM 행의 분리 스냅샷. 리스트·dict 를 복사해 세션 밖에서 detached 객체 문제와 공유 가변 상태를 피한다. `tools_refreshed_at` 은 tz-aware(`UTCDateTime`, `app/db.py:17-34`)라 `.timestamp()` 가 로컬 시간대 영향을 받지 않는다.
- 흔한 실수: 얕은 복사 — `auth["tls"]` 같은 중첩 dict 는 원본과 공유. 이 파일은 최상위 키만 바꾸므로 현재는 안전(1차 지적, 수용).

```python path=app/services/mcp_gateway.py lines=85-123
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
```
**분석 / Analysis (Level 2)**
- L86 `_TOKENS`: (주체, audience, 스코프) → (만료, 토큰). 키에 AS·엔드포인트가 없다 — 같은 audience 면 설정이 바뀌어도 만료까지 재사용.
- L89-94 `_cache_token`: 1024 개 이상이면 만료분만 정리(시험 `test/test_review_fixes.py:224-235`).
- L97-101 `clear_caches`(2차 변경): 업스트림 세션·유휴 시각까지 비우고 hosted 클라이언트를 닫는다. 호출처 `app/api/mcp.py:419`(PATCH), `app/api/catalog.py:442`.
- L104-108: 도구별 필요 스코프. L111-122: 내장 STS 교환 정책 기본값(허용 = PRM 스코프 ∪ 도구 스코프, 기본 = `:read`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L90-93 상한이 아니라 정리 트리거다 — 살아 있는 항목이 1024 개를 넘으면 저장마다 전체 순회하고 계속 커진다(1차·delegated BUG-05 부분 해결). 최소 수정: `OrderedDict` LRU 로 하드 상한.
- [위험:낮음] L97-101 `clear_caches` 는 PATCH 를 받은 **그 워커만** 비운다(교차 워커 무효화 없음 — `grep clear_caches` 결과 호출처 2곳뿐). 다중 워커(Epic 45 기본 2워커)에서 다른 워커는 기존 교환 토큰·업스트림 세션을 만료·404 까지 계속 쓴다. 업스트림 세션은 404 로 스스로 회복되지만, 엔드포인트가 바뀐 경우 다른 워커의 캐시된 토큰이 새 엔드포인트로 갈 수 있다(최대 TTL 300초). 최소 수정: 서버 행에 설정 버전(`updated_at`)을 두고 캐시 키에 포함.

```python path=app/services/mcp_gateway.py lines=124-182

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
```
**분석 / Analysis (Level 2)**
- L127-138 bearer/api_key: `secret_ref` 평문을 `Authorization` 또는 지정 헤더로. 적용 전 시험은 저장하지 않은 후보 값(`_candidate_secret`).
- L139-143: 캐시 적중이면 만료 30초 전까지 재사용.
- L144-152: AS 가 플랫폼 자신이면 RFC 8693 교환(사용자 신원 + 도구 최소 스코프). 실패는 403 성격 RpcError(삼킴 아님).
- L153-156: 외부 AS 면 `_external_token`.
- L159-181 `_external_token`: service 모드는 client_credentials(서버 시크릿), exchange 모드(기본)는 **사용자 원본 토큰**을 `subject_token` 으로 외부 AS 에 POST.
- 시크릿 참조 인가는 API 계층이 맡는다 — `secrets.check_refs`(자기 네임스페이스 `mcp/{id}/` 밖 참조는 `gateway:write` 필요, `app/services/secrets.py:17-28`)와 `check_destination`(`:31-38`)이 `app/api/mcp.py:403-405`(PATCH)·`:429-436`(test-config)에서 호출된다(2차 해결 부분). 등록 경로도 남의 참조를 떨어뜨린다(`app/services/registry.py:298-303`).
**짚어볼 점 / Watch-outs**
- [위험:높음] 자격 증명 **전송처** 통제가 이 파일의 실제 전송처와 맞지 않는다. `check_destination` 은 "`endpoint` 가 바뀌었고 `secret_ref`·`tls.client_cert_secret` 이 있으면" 만 시크릿 관리 권한을 요구한다(`app/services/secrets.py:36`, 호출 시 `changed` 는 endpoint 비교만 — `app/api/mcp.py:405,436`). 그러나 이 블록에서 자격 증명이 나가는 곳은 그 밖에도 (a) `auth.token_endpoint`(L161·L177 — service 모드면 서버에 저장된 시크릿, exchange 모드면 사용자 원본 토큰), (b) `auth.authorization_server`(L144-145 — 외부/내장 분기 결정), (c) oauth2 서버의 `endpoint`(교환 토큰이 실린다 — 이 경우 `secret_ref` 가 없어 통제가 아예 걸리지 않는다)가 있다. `auth` 는 요청 본문 병합(`app/api/mcp.py:410-411`)이라 `mcp:write`(developer 역할 포함, `app/core/auth.py:21`) 보유자가 시크릿 관리 권한 없이 이 값들을 바꿀 수 있다. 결과: 저장된 서비스 자격·호출 사용자들의 플랫폼 토큰·교환 토큰이 등록 때와 다른 곳으로 향할 수 있다(감사 `mcp.server.update` 는 남는다). 최소 수정: `changed` 판정을 `endpoint`·`authorization_server`·`token_endpoint`·`resource_metadata`·`mode`·`type` 변경 전체로 넓히고, oauth2 서버(자격 증명이 사용자 토큰)도 대상에 포함 — 또는 이 키들을 `gateway:write` 전용 필드로 분리. 시험 공백: `test/test_review_fixes.py:246-283` 은 `secret_ref` 와 `endpoint` 만 검증.
- [위험:높음] L173 `subject_token=p.raw_token` — 외부 AS 에 사용자가 게이트웨이에 제시한 원본 토큰(웹 세션 액세스 토큰, PAT 는 PAT 문자열 자체 — `app/core/auth.py:207`)을 보낸다(1차 지적, 부분 해결). 2차에서 추가된 `_pinned`(L196-204)는 **등록된 AS 가 있을 때** 업스트림 401 응답으로 바꾸는 것만 막는다. 운영자 허용 목록이 없고 원본 토큰을 그대로 보내는 설계는 그대로다. 최소 수정: 외부 AS 는 운영자 등록 목록만, 원본 토큰 대신 audience 를 그 외부 AS 로 한정한 교환 토큰을 subject_token 으로.
- [위험:낮음] L180-181·L155 외부 AS 응답이 JSON 이 아니거나(`ValueError`) `access_token` 이 없거나(`KeyError`) `expires_in` 이 숫자가 아니면(`ValueError`) RpcError 로 감싸지 않아 호출 경로가 500 이 된다(`refresh_tools` 는 RpcError 만 잡는다, L722). 1차 지적 미해결.
- [위험:높음] L131-138 `check_destination` 우회 — **2단계 PATCH**. 판정은 "병합 후 `auth` 에 `secret_ref` 가 있는데 같은 요청에서 endpoint 가 바뀌는가" 만 본다(`app/services/secrets.py:36`, `app/api/mcp.py:404-405`). 그래서 `mcp:write` 보유자가 ① `{"endpoint": "https://attacker/mcp", "auth": {"secret_ref": null}}` 로 보내면 병합 결과에 `secret_ref` 가 없어 통과하고(`foreign_refs` 도 `None` 은 건너뜀 — `secrets.py:20-21`), ② 이어서 `{"auth": {"secret_ref": "mcp/{id}/backend"}}` 를 보내면 자기 네임스페이스라 `check_refs` 통과, endpoint 는 그대로라 `changed=False` 로 `check_destination` 통과한다. 저장된 시크릿 행 `mcp/{id}/backend` 는 ①에서 지워지지 않으므로, 다음 `refresh`·도구 호출에서 L134-138 이 운영자가 넣어 둔 백엔드 자격 증명을 공격자 엔드포인트로 보낸다(L275·L293). `"secret_ref": ""` 도 같다. 스냅샷 이후 추가된 `destination_changed`(현재 `app/services/secrets.py:31-37`)도 "같은 요청 안의 변경" 만 보므로 이 순서는 막지 못한다(코드 읽기로 확인, 실행 안 함 — 시험 `test/test_review_fixes.py:247-283` 은 1단계만 확인). 최소 수정: 저장된 값과 **다른 `secret_ref` 를 새로 붙이는 변경**도 전송처 변경과 같이 `gateway:write` 를 요구하거나, 시크릿 행에 등록 당시 전송처(endpoint·token_endpoint 지문)를 기록해 사용 시점(L131-138·L165-169)에 비교.
- [위험:낮음] L135 `_candidate_secret` 은 test-config 전용 의도이지만 `auth` 병합에 키 필터가 없어 PATCH 로 저장될 수 있다(`app/api/mcp.py:410-411`). 그러면 평문 자격 증명이 서버 행·감사 데이터(`app/api/mcp.py:420-422` 가 `body.auth` 를 기록)·조회 응답(`app/api/mcp.py:351` 은 `secret_ref` 만 제외)에 남는다. 최소 수정: 병합 시 `_` 접두 키 거부.

```python path=app/services/mcp_gateway.py lines=183-248

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
```
**분석 / Analysis (Level 2)**
- L184-193 `_tls`: 서버 `auth.tls` + 게이트웨이 mTLS 정책 → httpx 인자. 정책 위반은 호출 전 403(fail closed). 인증서 로드는 파일 I/O 라 스레드풀.
- L196-204 `_pinned`(2차 신규): 401 재탐색 결과의 AS 가 등록된 AS 와 다르면 403 `authorization_server_changed`. 시험 `test/test_review_fixes.py:285-293`. 대소문자·끝 슬래시 차이는 거부 쪽으로 처리(fail closed).
- L212-224 `discover_auth`: 401 의 `WWW-Authenticate` 에서 `resource_metadata="…"` 를 꺼내거나, 저장값, 아니면 RFC 9728 규칙 경로. 네트워크·JSON 오류는 RpcError.
- L227-246 `_discover`: PRM → `authorization_servers[0]`(없으면 플랫폼 issuer) → 외부 AS 면 RFC 8414/OIDC 메타데이터의 `token_endpoint`.
- 흔한 실수: "고정(pinning) 검사가 있으니 안전" 으로 보는 것. `_pinned` 는 `discover_auth` 가 **끝난 뒤** 결과에만 적용된다(L419·L485 `_pinned(srv, await discover_auth(...))`).
**짚어볼 점 / Watch-outs**
- [위험:중간] L217-219·L230·L240 업스트림 응답 헤더(401 challenge)와 PRM 본문이 정한 URL 을 그대로 GET 한다(1차 SSRF 지적 미해결). `_pinned` 검사 전에 두 번의 GET 이 이미 일어나고, egress 가드는 링크 로컬·메타데이터만 막는다(`app/core/http.py:34-38` — 사설망 허용은 의도된 제품 정책). 응답 본문이 호출자에게 돌아가지 않아 블라인드 성격이지만 내부망 HTTP 서비스에 대한 요청 발판이 된다. 또 RFC 9728 §3.3 이 요구하는 PRM `resource` 값과 엔드포인트의 일치 확인이 없다. 최소 수정: challenge 의 PRM URL 은 엔드포인트와 같은 origin 일 때만 사용, PRM `resource` 일치 확인, 고정된 AS 가 있으면 AS 메타데이터 조회 생략, 발견 URL 에 `internal_url` 수준(`app/core/http.py:102-120`) 검사 옵션.
- [위험:중간] L201 고정은 **등록된 AS 가 있을 때만** 동작한다. 최초 발견이 실패해 AS 가 비어 있는 oauth2 서버(L705-708 이 결과를 저장하지 못한 경우)에서는 업스트림이 401 로 제시한 AS 를 그대로 받아들이고(시험 `test/test_review_fixes.py:292-293` 이 이 동작을 확인), 다음 시도에서 위 L173 경로(원본 토큰 → 외부 AS)로 이어진다. 최소 수정: AS 가 비어 있으면 401 재탐색으로 외부 AS 를 채택하지 말고 플랫폼 issuer 또는 실패로 고정.
- [위험:낮음] L198 docstring 이 안내하는 재탐색 API(`app/api/ops.py:244-249`)는 `refresh_tools` 를 부르는데, `refresh_tools` 는 AS 가 이미 있으면 발견을 건너뛴다(L706). 즉 안내된 경로로는 고정된 AS 를 바꿀 수 없고 `auth.authorization_server` 를 비우는 PATCH 가 필요하다(기능 불일치). 또 그 API 권한은 "관리자" 가 아니라 `mcp:write` 다.
- [위험:낮음] L218 challenge 에 다른 따옴표 파라미터(예: `realm="…"`)가 있고 `resource_metadata` 값만 따옴표 없이 오면 `split('"')[1]` 이 IndexError — try 밖이라 RpcError 로 감싸지지 않고 원 호출이 500 이 된다. RFC 상 quoted-string 이 정상이지만 관대한 파서(정규식 `resource_metadata="?([^",\s]+)`)가 안전.
- [위험:낮음] L233-235 PRM 이 JSON 객체가 아니거나 `authorization_servers[0]` 이 문자열이 아니면 AttributeError(포착 대상 아님). L240 RFC 8414 는 경로가 있는 issuer 의 well-known 을 host 와 path 사이에 넣는데 여기서는 뒤에 붙인다(경로 있는 외부 AS 에서 발견 실패, 기능).

```python path=app/services/mcp_gateway.py lines=249-319
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
```
**분석 / Analysis (Level 2)**
- L250-253(2차 변경): 업스트림 세션 캐시 + 마지막 사용 시각, 유휴 30분, 상한 4096(delegated BUG-08 해결).
- L256-267 `_parse_rpc`: SSE 면 `\n\n` 블록의 `data:` 줄을 합쳐 요청 id 의 결과/오류를 찾는다.
- L270-277 `_post`: 공통 헤더 + mTLS, 연결 오류 메시지에 예외 타입만(내부 주소 비노출). `async_client` 가 egress 가드 훅을 단다(`app/core/http.py:173-175`). httpx 는 기본적으로 리다이렉트를 따라가지 않는다.
- L284-318 `_post_rpc`: 스트리밍으로 읽으며 도중의 서버→클라이언트 요청에 `mcp_consent.handle` 로 답하고 같은 헤더로 POST, progress 는 하류로 중계, 결과가 오면 반환. 읽기 타임아웃 = 30 + 2×60×11 + 30 = 1380초(기본값) — 동의 연장 10회를 견디도록 계산.
- L310: 2차에서 `mcp_consent.handle` 이 모든 예외를 -32603 응답으로 바꾸므로(`app/services/mcp_consent.py:62-65`), 동의 처리 실패가 업스트림 무응답으로 번지지 않는다(1차 L294 지적 해결).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L263·L308·L299 `json.loads`/`res.json()` 의 `ValueError` 는 RpcError 로 감싸지지 않는다 — 고장 난 업스트림 응답 하나로 원 호출이 500(1차 지적 미해결). `except (httpx.HTTPError, ValueError)` 로 넓히면 된다.
- [위험:낮음] L300-304 `buf` 는 `\n\n` 이 올 때까지 무한히 커진다(1차 지적 미해결) — 상한(예: 1MiB) 초과 시 RpcError.
- L292 읽기 타임아웃 최대 23분 — 응답 없는 업스트림이 워커 연결을 오래 점유(1차, 설계상 수용).

```python path=app/services/mcp_gateway.py lines=320-355

async def _ensure_upstream_session(srv: ServerRef, p: Principal, auth_hdr: dict) -> tuple[str | None, str]:
    key = (srv.id, p.sub)
    now = time.monotonic()
    if key in _UPSTREAM_SESSIONS and now - _UPSTREAM_SEEN.get(key, now) < UPSTREAM_SESSION_IDLE_S:
        _UPSTREAM_SEEN[key] = now
        return _UPSTREAM_SESSIONS[key]
    _UPSTREAM_SESSIONS.pop(key, None)
    if len(_UPSTREAM_SESSIONS) >= UPSTREAM_SESSION_MAX:  # 가장 오래 쓰지 않은 것부터
        for old_key in sorted(_UPSTREAM_SEEN, key=_UPSTREAM_SEEN.get)[:len(_UPSTREAM_SESSIONS) - UPSTREAM_SESSION_MAX + 1]:
            _UPSTREAM_SESSIONS.pop(old_key, None)
            _UPSTREAM_SEEN.pop(old_key, None)
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
```
**분석 / Analysis (Level 2)**
- L322-326(2차 변경): (서버, 주체) 키가 있고 유휴 30분 이내면 재사용 + 사용 시각 갱신. 시험 `test/test_lv2_hardening.py:148-175`.
- L327-331: 유휴·없음이면 버리고, 상한이면 가장 오래 쓰지 않은 것부터 정리(정렬 O(n log n), 4096 규모라 비용 작음).
- L332-353: initialize → `notifications/initialized` → 캐시 + DB 기록(`McpSession kind=upstream`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L323-345 확인-후-설정 사이에 `await` 가 여러 번 있어 같은 사용자의 동시 첫 호출 둘이 모두 initialize 한다 — 업스트림에 고아 세션(1차 지적 미해결). 키별 `asyncio.Lock` 으로 single-flight.
- [위험:낮음] L340 initialize 가 JSON-RPC 오류를 돌려줘도 기본 버전으로 진행(1차 미해결). 유휴 만료(L327)로 버린 업스트림 세션을 DELETE 로 종료하지 않고, `McpSession kind=upstream` 행은 재초기화마다 늘지만 보존 정리 대상이 아니다(`app/services/retention.py:29-34`).
- L490·L718·L799·L807 의 `_UPSTREAM_SESSIONS.pop` 은 `_UPSTREAM_SEEN` 을 지우지 않는다(delegated CR2-04 — 스냅샷 이후 C51-05 `_drop_upstream` 으로 수정, 현재 L321-325; `app/api/ops.py:580-581` 재시작 경로도 같은 pop) — 정리 대상 키에 세션 없는 키가 섞여 상한을 약간 넘길 수 있다(영향 미미).

```python path=app/services/mcp_gateway.py lines=356-393
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
- L356-358: 하류가 progress 를 원하면 업스트림 요청에도 progressToken.
- L361-365: streamable-http 이고 서버 설정이 `2026-07-28` 이거나 `auto` + 발견 버전이 modern 이면 modern 호출.
- L368-373: 발견한 세대를 DB 에 저장. L376-391 `probe_protocol`: `server/discover` 로 판별, 실패·미지원이면 레거시.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L389-390 `except (RpcError, ValueError): pass` — "판별 실패 = 레거시" 라는 명시적 폴백이지만 원인 로그가 없다(1차 지적 미해결, 1차 INDEX §4.1 패턴 6). 토큰 교환 실패(RpcError)까지 같은 곳에서 사라져 운영자는 "왜 레거시인지" 를 알 수 없다. 최소 수정: `logs.event("info", …, error=…)` 한 줄.

```python path=app/services/mcp_gateway.py lines=394-462
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
        # … (L426-438 생략 — 4xx 본문의 error.code 가 UNSUPPORTED_VERSION 이면 _LegacyOnly, 아니면 UPSTREAM_ERROR; msg 의 JSON-RPC error 는 RpcError 로)
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
- L401-416: 요청마다 `_meta`(버전·능력)·헤더를 붙여 세션 없이 호출. ctx 가 없으면(시스템 호출) 능력을 선언하지 않아 업스트림이 입력을 요구하지 않게 한다.
- L417-422(2차 변경): 401 이면 1회 재발견 → `_pinned` 로 AS 변경 거부 → 토큰 캐시 삭제 후 재시도.
- L439-459: `input_required` 면 각 `inputRequests` 를 `mcp_consent.handle`(중계·동의)로 답해 `inputResponses`+`requestState` 로 재호출(MRTR). 거부는 도구 오류 결과(`isError: true`)로 — 레거시와 같은 모양.
- 소유권: MRTR 입력 응답은 게이트웨이가 업스트림에 직접 답하는 구조라 하류가 끼어들 지점은 `ctx.relay`(상태 id·작업 id 바인딩, `app/services/mcp_modern.py:330`·`app/services/mcp_tasks.py:150`) 뿐이다.
**짚어볼 점 / Watch-outs**
- L420 `srv.auth.update(...)` 는 스냅샷만 바꾼다(DB 미저장) — 매 호출 401 → 재발견이 반복될 수 있다(1차 지적 미해결, 낮음). 재발견 GET 자체의 문제는 L212-246 참조.
- L456-459 `requestState` 만 오면 0.5초 쉬고 재시도 — 최대 8회(1차, 수용).

```python path=app/services/mcp_gateway.py lines=463-505
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
            _UPSTREAM_SESSIONS.pop((srv.id, p.sub), None)  # 업스트림 세션 만료 → 재초기화
            continue
        # … (L492-501 생략 — 403 → FORBIDDEN(www_authenticate 포함), 그 밖의 4xx·5xx → UPSTREAM_ERROR, JSON-RPC error → RpcError)
        return msg.get("result") or {}
    raise RpcError(UPSTREAM_ERROR, f"{srv.id}: 업스트림 인증 실패")
```
**분석 / Analysis (Level 2)**
- L465-473: modern 우선 → `_LegacyOnly` 면 강제 modern 설정일 때만 오류, 아니면 레거시로 내려가 DB 에 기록.
- L477-502: 최대 2회 — 토큰 → 업스트림 세션 → `_post_rpc`. 401 이면 재발견(2차: `_pinned` 적용) 후 재시도, 404 는 세션 재초기화.
**짚어볼 점 / Watch-outs**
- L503 두 번 다 401/404 면 같은 "업스트림 인증 실패" — 404(세션 만료 2회)와 구분되지 않는다(1차 지적 미해결, 낮음).

```python path=app/services/mcp_gateway.py lines=506-540
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
```
**분석 / Analysis (Level 2)**
- L507-526(2차 신규): hosted 서버·클라이언트 캐시와 닫기. 실행 중인 루프가 있으면 `aclose` 를 Task 로 예약하고 강한 참조(`_CLOSING`)로 GC 를 막는다. 동기 경로(루프 없음)에서는 참조만 버린다. 시험 `test/test_lv2_hardening.py:124-146`(delegated BUG-11 해결).
- L529-538 `stdio_allowed`(2차 신규): 쉼표 구분 항목을 `shlex.split` — 토큰 1개면 실행 파일만 비교(기존 호환), 2개 이상이면 `list(command) == parts` 정확 일치. 기본값 `""`(`app/core/config.py:73`)이면 전부 거부(fail closed). 시험 `test/test_lv2_hardening.py:114-121`.
- 우회 점검(전체 일치 항목): 리스트 정확 비교라 인자 추가·순서 변경·다른 경로 인자는 거부된다. 실행 파일 이름도 문자열 정확 비교라 `./npx`·절대경로 변형은 거부 쪽(fail closed). 명령은 리스트로 `StdioTransport(command, args)` 에 넘겨 셸을 거치지 않는다 — 셸 메타문자는 인자 문자열일 뿐이다(셸 미사용은 fastmcp/mcp 의 프로세스 실행 방식에 의존 — 저장소에 라이브러리 소스가 없어 추가 확인 필요).
**짚어볼 점 / Watch-outs**
- [위험:중간] L536 토큰 1개 항목은 여전히 **실행 파일만** 비교한다(1차 지적 부분 해결). 인터프리터·패키지 실행기(`uvx`·`npx`·`python`·`docker` 등)를 이 형식으로 허용하면 인자 전체가 서버 설정을 따른다. 그런데 stdio 명령은 카탈로그 server.json 의 `packages[0].runtimeHint`·`identifier` 로 만들어진다(`app/services/registry.py:292-296` — 게시자가 정하는 값, `grep` 결과 `McpServer.command` 를 쓰는 곳은 여기뿐). 또 등록 경로는 `[hint, identifier]`(docker 는 5개) 만 만들고 버전·추가 인자를 넣지 않으므로, docstring 예시 같은 전체 명령 항목(`-y …@0.6.2 /data`)과는 **일치할 수 없다** — 안전한 형식을 쓰려 해도 등록된 서버가 매칭되지 않아 운영자가 단일 토큰 형식으로 돌아가기 쉽다(시험도 `uvx` 단일 항목으로 인자 자유를 확인, `test/test_lv2_hardening.py:116-118`). 최소 수정: 인터프리터·패키지 실행기의 단일 토큰 항목은 기동 시 거부(또는 경고), 등록 경로가 버전·인자를 포함한 명령을 만들게 하고 전체 일치만 허용. 기본값이 빈 목록이라 기본 구성에서는 노출 없음.
- [위험:낮음] L534 쉼표로 먼저 자른 뒤 `shlex` — 인자 안의 쉼표(따옴표 안 포함)가 항목을 쪼갠다(fail closed, 기능). L535 따옴표 짝이 안 맞으면 `ValueError` 가 RpcError 가 아니어서 `refresh_tools`(L722)를 지나 500.

```python path=app/services/mcp_gateway.py lines=541-589
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
    _close_hosted([k for k in list(_HOSTED) if k.startswith(f"{srv.id}:") and k != cache_key])  # 이전 설정의 클라이언트
    _HOSTED[cache_key] = server
    _HOSTED_CLIENTS[cache_key] = client
    return server
```
**분석 / Analysis (Level 2)**
- L545-552: OBO 토큰을 ContextVar 로 호출별 전달 — 서버 객체는 공유하되 토큰은 요청마다(asyncio Task 마다 컨텍스트 분리).
- L555-587 `_hosted_server`: hosted 설정 JSON 을 캐시 키로 FastMCP 서버 생성. 서비스 모드는 시크릿 헤더, OBO 모드는 헤더 대신 이벤트 훅. L570-575: 이 httpx2 클라이언트는 egress 팩토리를 거치지 않으므로 **생성 전에** `blocked_url(base)` 로 링크 로컬·메타데이터를 거부(Epic 39). L584(2차): 같은 서버의 이전 설정 클라이언트를 닫는다. 서버 id 는 `[a-z0-9-]` 라 `f"{srv.id}:"` 접두 비교가 다른 서버와 겹치지 않는다(`app/api/mcp.py:573` 패턴).
- `hosted.secret_ref` 는 등록 API 가 항상 `mcp/{namespace}/backend` 로 만든다(`app/api/mcp.py:631-638`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L558-560 캐시 키에 시크릿 값이 없다 — 시크릿을 회전해도 이미 만든 클라이언트 헤더에 옛 값이 남는다(1차 지적 미해결). 회전 API 는 `clear_caches` 를 부르지 않는다.
- [위험:낮음] L559-587 동시 첫 호출 둘이면 클라이언트 둘이 만들어지고 L585-586 대입에서 먼저 만든 것은 `_HOSTED_CLIENTS` 에서 빠져 닫히지 않는다(1차 지적 부분 해결 — 설정 변경 시 닫기는 해결). 또 재시작 API 는 `gw._HOSTED` 만 직접 pop 하고 `_HOSTED_CLIENTS` 는 건드리지 않는다(`app/api/ops.py:582-583`) — 같은 설정이면 다음 호출의 L586 대입이 옛 클라이언트를 닫지 않고 덮어써 연결 풀이 샌다. 최소 수정: 재시작도 `_close_hosted([...])` 를 쓰게.
- [위험:낮음] L574 egress 검사는 생성 시 1회 — 이후 DNS 응답이 바뀌면(재바인딩) 막지 못한다(1차 지적 미해결, `app/core/http.py` 의 해석 캐시와 같은 한계). L580-582 의 `httpx2.AsyncClient` 는 팩토리(`app/core/http.py:173-175`) 밖이라 요청 단위 가드 훅이 없다 — 2차 위임 보안 검토는 이를 SEC2-01(HIGH, OpenAPI 경로·`servers` 가 base_url 밖을 가리킬 가능성 포함)로 보고했다. 여기서 낮음으로 두는 근거: base_url 은 생성 시 검사되고 경로는 상대 URL 로 base_url 에 합쳐지는 것이 httpx 동작이며(라이브러리가 절대 URL 을 만드는지는 추가 확인 필요), 스펙 제출은 `catalog:approve`+`mcp:write`(`app/api/mcp.py:614`) 운영자 권한이다. 스냅샷 이후 C51-01 이 `_guard_hosted` 로 요청마다 검사하도록 수정됨(현재 L560-564·L594).

```python path=app/services/mcp_gateway.py lines=590-645
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
        # … (L630-642 생략 — resources/list·templates/list·read, prompts/list·get, ping 을 fastmcp Client 메서드로 호출 후 dict 덤프)
    raise RpcError(NOT_FOUND, f"지원하지 않는 메서드: {method}")
```
**분석 / Analysis (Level 2)**
- L590-611 `_client_handlers`: stdio·hosted 서버의 sampling·elicitation 콜백을 같은 동의 경로로. ctx 가 없으면 능력을 선언하지 않는다.
- L614-643 `_client_call`: hosted 면 FastMCP 서버 객체, stdio 면 허용 목록 통과 시 `StdioTransport`. `async with` 로 연결·하위 프로세스 정리. 서버 요청 처리 중 예외는 2차부터 `mcp_consent.handle` 이 오류 응답으로 바꾼다.
**짚어볼 점 / Watch-outs**
- L603 거부를 `RuntimeError` 로 — 사용자 거부 코드 -1 이 업스트림에 그대로 전달되는지는 fastmcp 동작에 의존(1차 지적 미해결, 낮음).
- [위험:낮음] L623 stdio 는 호출마다 하위 프로세스를 새로 띄우고 동시 실행 상한이 없다 — 동시 `tools/call` 이 많으면 프로세스가 그만큼 늘어난다(1차 성능 지적 미해결). 환경 변수 전달 범위는 `env` 를 지정하지 않아 라이브러리 기본값을 따른다(추가 확인 필요).
- L629 `params["name"]` KeyError 가능 — API 계층이 검증(`app/api/mcp.py:68-69`, 1차 지적 미해결·수용).

```python path=app/services/mcp_gateway.py lines=646-669
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
- L646-667 `call_server`: 전송별 분기. hosted OBO 의 `tools/call` 은 `api:{id}` audience 로 교환 후 ContextVar 설정 → 호출 → `finally` 에서 reset(토큰이 다른 요청으로 새지 않게 하는 핵심). 교환 실패는 호출하지 않는다(fail closed, MSG-019). 시험 `test/test_design_gaps.py:90`.
**짚어볼 점 / Watch-outs**
- L650 OBO 주입은 `tools/call` 만 — 같은 hosted 서버의 `resources/read`·`prompts/get` 은 자격 증명 없이 나간다(L579 에서 서비스 헤더도 제거). 기능상 실패 쪽(fail closed)이지만 의도 확인 필요.

```python path=app/services/mcp_gateway.py lines=670-692
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
- L671-690: `_all` 이면 활성 서버 전부, 서버 id 면 활성 확인, 가상 서버면 활성 멤버 + 멤버별 도구 제한, 없으면 404.
**짚어볼 점 / Watch-outs**
- L671 인자 `p` 를 쓰지 않는다 — 서버 단위 접근 제어가 없고 도구 규칙·정책 단계에서만 거른다(1차 지적 미해결). 이 공백은 도구 규칙이 없는 `resources/*`·`prompts/*`(L1009-1047)에서 직접 드러난다.

```python path=app/services/mcp_gateway.py lines=693-744
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
    # … (L739-742 생략 — server_id·health·error·error_code·도구 이름·지연·발견 auth·protocol 반환)
```
**분석 / Analysis (Level 2)**
- L693-742: (oauth2 면) AS 가 비어 있을 때만 발견 → 결과를 DB `auth` 에 병합·내장 STS 면 교환 정책 기본값 → 이 주체의 업스트림 세션 초기화 → 세대 판별 → `tools/list` → 헬스·지연 저장, 성공이면 도구 저장 + 기본 도구 규칙.
- 호출처: 헬스 루프(`app/main.py:89`, 2차부터 리스 보유 워커만 `:83`), 기동 갱신(`:55`), `_tools_of`(L829), 관리 API(`app/api/mcp.py:447`, `app/api/ops.py:249`, `app/api/catalog.py:252,306`).
- 최초 발견 결과가 AS 를 정한다(TOFU — Trust On First Use). 재탐색 API 로는 바꿀 수 없다(L198 Watch-out).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L736 `governance.default_rules` 는 규칙이 없으면 INSERT 한다(`app/services/governance.py:66-72`)는데 `ToolRule` 은 `(server_id, tool)` 유니크다(`app/models.py:309`). TTL 만료 순간 여러 요청·워커가 동시에 갱신하면(L829 — single-flight 없음) 새 도구에 대해 두 INSERT 가 경합해 한쪽이 IntegrityError — RpcError 가 아니어서 `_tools_of` 를 거친 `tools/list`·`tools/call` 이 500 이 된다. 최소 수정: INSERT … ON CONFLICT DO NOTHING 또는 갱신 single-flight(리스).
- L712·L728 `s` 가 None(그 사이 삭제)이면 AttributeError(1차 지적 미해결).
- 외부 AS exchange 모드 서버를 `SYSTEM`(raw_token 없음)으로 갱신하면 L173 이 빈 subject_token 을 보내 실패할 가능성이 높다 — 헬스 점검이 그런 서버를 늘 unhealthy 로 기록할 수 있다(가능성, 추가 확인 필요).

```python path=app/services/mcp_gateway.py lines=745-824
async def test_config(server_id: str, p: Principal, candidate: dict) -> dict:
    """설정 적용 전 시험 (SCR-F03) — 후보 엔드포인트·인증으로 PRM/AS 발견 · tools/list · 스코프 최소화 점검. 저장하지 않는다."""
    from app.services.review import scope_kind, tool_risk
    # … (L748-755 생략 — 서버 행을 ServerRef 로 로드, 없으면 NOT_FOUND)
    before = {t.get("name") for t in srv.tools}
    if candidate.get("endpoint"):
        srv.endpoint = candidate["endpoint"]
    srv.auth = {**srv.auth, **(candidate.get("auth") or {})}
    if candidate.get("secret"):
        srv.auth["_candidate_secret"] = candidate["secret"]
    checks: list[dict] = []
    # … (L763-797 생략 — add() 점검표 헬퍼, oauth2 면 discover_auth·AS 판정, bearer/api_key 면 자격 증명 유무, protocol 후보 적용·probe_protocol)
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
    # … (L810-822 생략 — 도구 스코프 대비 요청 스코프 초과·관리 스코프 경고, 결과 dict(checks·tools·added·removed·elapsed_ms·applied=False))
```
**분석 / Analysis (Level 2)**
- L745-822: 적용 전 시험 — 후보 엔드포인트·인증을 스냅샷에만 병합해 발견·세대 판별·`tools/list`·스코프 점검. DB 저장 없음. 기존 업스트림 세션을 잠시 빼두었다 복원(L798-809).
- 인가: API 계층이 호출 전에 `check_refs`·`check_destination` 을 적용(`app/api/mcp.py:429,436`, 시험 `test/test_review_fixes.py:263-265`).
**짚어볼 점 / Watch-outs**
- [위험:높음과 동일 근원] L759 후보 `auth` 병합은 PATCH 와 같은 키 범위를 받으므로 L124-182 의 전송처 통제 공백이 저장 없이도 그대로 적용된다(1차 [높음] 부분 해결 — 남의 `secret_ref`·엔드포인트 변경만 막힘). 별도 집계하지 않고 L124-182 의 [위험:높음] 에 포함.
- L798-809 같은 사용자의 실제 호출과 시험이 동시에 일어나면 공유 세션 캐시를 빼고 넣는 사이에 경쟁(1차 지적 미해결, 낮음).

```python path=app/services/mcp_gateway.py lines=825-861
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
- L825-840 `_tools_of`: DB 기준 TTL 300초가 신선하면 그대로, 아니면 `refresh_tools`(SYSTEM). 헬스 루프가 한 워커에서 DB 를 갱신하면 다른 워커도 신선한 값으로 본다(다중 워커 일관). 장애 시 마지막 목록.
- L843-859 `_visible`: 기본 거부 — 규칙 없거나 차단된 도구는 노출하지 않는다. 가상 서버 제한·에이전트 프로파일 허용 패턴. `agent` 는 이제 기계·위임 주체가 신고한 값만(`app/services/policy.py:70`) — 좁히는 방향이라 생략해도 권한이 넓어지지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L826-829 TTL 만료 순간의 모든 요청이 각자 갱신한다(single-flight 없음 — 1차 지적 미해결). 위 L736 유니크 경합의 원인이기도 하다.

```python path=app/services/mcp_gateway.py lines=862-902
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
- L863-874 `RpcContext`: 호출 맥락. 2차부터 `agent`·`data_class`·`run_id`·`step_id` 는 `policy.caller_context` 가 서버에서 판정해 채운다(`app/api/mcp.py:55-59` → `app/services/policy.py:56-73`) — agent 는 기계·위임 주체만, run 은 그 주체 소유일 때만, 데이터 등급은 `declared_data_class`. `session_id` 는 여전히 헤더 값이지만 그 쓰임(승인 기억 `app/services/governance.py:188-193`)은 `requester_sub` 로 묶여 있다.
- L877-890 `list_tools`: 대상별 도구 수집, 집계 모드는 일부 장애를 건너뛰고 이름을 `{서버}.{도구}` 로. L893-900: 스레드풀용 DB 래퍼.

```python path=app/services/mcp_gateway.py lines=903-957
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
    # … (L952-955 생략 — GenAI 시맨틱 속성 CLIENT 스팬 안에서 _call_tool_inner 호출)
```
**분석 / Analysis (Level 2)**
- L903-910 `_resolve_tool`: 단일이면 그대로, 집계·가상이면 `서버.도구` 분해(서버 id 에 점이 없어 모호성 없음).
- L913-922 `tool_schema`: modern `Mcp-Param-*` 헤더 검증용 — 동기 함수, `app/api/mcp.py:152` 가 스레드풀로 부른다.
- L925-951 `call_tool`: 가상 서버 도구 제한 → `governance.check_tool`(허용 목록·프로파일·정책·인자 스키마·승인, 승인 소비는 2차에서 원자화 `app/services/governance.py:180-181`) → 허용된 호출만 도구 한도 차감 → 초과면 429.
**짚어볼 점 / Watch-outs**
- L930-931 `_tools_of` 반환값을 쓰지 않는다 — 실제 스키마 검증은 `governance.check_tool` 이 DB 의 `srv.tools` 를 다시 읽어서 한다(`app/services/governance.py:140`). 효과는 "DB 갱신" 뿐(1차 지적 미해결, 주석 정정 권장).

```python path=app/services/mcp_gateway.py lines=958-1008
async def _call_tool_inner(srv, ctx, tool, qualified, arguments, dec, ap_info, mode, server_id) -> dict:
    if not dec.allow:
        if dec.code == "approval_required":
            raise RpcError(APPROVAL_REQUIRED, dec.reason, {"code": dec.code, "risk": dec.risk, "tool": qualified,
                                                           **(ap_info or {})})
        # … (L963-969 생략 — 거부 지표·이벤트 로그·감사 gateway.tool.deny 후 FORBIDDEN)
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
    # … (L997-1006 생략 — UsageRecord(kind=tool) + 집계·가상 모드면 RoutingDecision, 한 트랜잭션 커밋)
```
**분석 / Analysis (Level 2)**
- L959-969: 승인 필요면 -32001 + approval_id, 그 밖의 거부는 지표·로그·감사 후 -32003.
- L970-982(2차 변경): `except Exception` 으로 stdio·hosted 일반 예외도 `error` 로 기록한 뒤 다시 던진다 — 1차 [낮음] 해결, 시험 `test/test_review_fixes.py:423-436`. `finally` 로 실패해도 사용량·지표 기록.
- L984-993: 결과 소독(지시문 제거·PII 마스킹), 고위험이거나 발견이 있으면 감사.
- L996-1006 `_record_tool`: 사용량 행 + 집계·가상 모드면 라우팅 결정 로그.
**짚어볼 점 / Watch-outs**
- L976-978 `except Exception` 은 `asyncio.CancelledError`(BaseException)를 잡지 않으므로 취소 시 `status` 가 "ok" 로 남은 채 `finally` 가 기록한다 — 작업 취소(`app/services/mcp_tasks.py:224`)·스트림 끊김 때 사용량이 성공으로 남는다(낮음). `except BaseException` 으로 상태만 바꾸고 다시 던지면 된다.

```python path=app/services/mcp_gateway.py lines=1009-1049
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
            # … (L1023-1027 생략 — 항목에 io.agentnexus/server 메타, 집계 모드 프롬프트는 이름 접두)
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
- L1012-1028: 목록 메서드는 서버별 결과를 합치고 서버 메타를 붙인다.
- L1029-1031: 집계 `prompts/get` 은 이름으로 서버 선택.
- L1032-1047: 그 밖(`resources/read`·`ping` 등)은 대상 서버를 순서대로 시도해 첫 성공 반환, `resources/read` 텍스트는 소독.
**짚어볼 점 / Watch-outs**
- [위험:중간] L1009-1047 리소스·프롬프트 경로에는 `call_tool` 의 통제(도구 규칙 기본 거부·정책 평가·승인·한도·사용량 기록·거부 감사)가 하나도 없다. 서버 단위 접근 제어도 없으므로(L671 `p` 미사용), `chat:use` 를 가진 모든 주체가 활성 서버(또는 `_all`)의 리소스·프롬프트를 읽을 수 있다. bearer·api_key·service 모드 서버는 업스트림에 공용 서비스 자격으로 나가므로(L130-138), 업스트림 쪽 사용자별 인가도 기대할 수 없다. 1차·2차 시험(`test/test_mcp.py:173`)은 정상 흐름만 본다. 최소 수정: `policy.evaluate(kind="resource"|"prompt", target="<서버>:<uri|name>")` + 서버별 허용 규칙(기본 거부 또는 운영자 명시 허용) + 사용량·감사 기록.
- [위험:낮음] L1033-1046 집계(`_all`) `resources/read` 는 URI 를 성공할 때까지 **모든 서버에 차례로** 보낸다 — 한 서버의 리소스 URI 가 다른 서버들에 전달된다(1차 지적 미해결). URI 스킴·서버 메타로 대상을 고르는 편이 맞다.

```python path=app/services/mcp_gateway.py lines=1050-1096
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
    # … (L1064-1068 생략 — initialize 결과: protocolVersion·capabilities(listChanged False)·serverInfo·instructions)
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
- L1051-1052 `negotiate`: 지원 버전이면 그대로, 아니면 기본.
- L1055-1069 `open_session`: 192비트 세션 id, 클라이언트 능력·이벤트 기록.
- L1072-1079 `check_session`: 클라이언트용·미종료·같은 서버·**같은 주체** 확인 — 하류 중계 응답(`app/api/mcp.py:91-92`)의 소유권 1단계이기도 하다. 시험 `test/test_mcp.py:75`.
- L1082-1085 능력 조회, L1088-1096 `close_session`: 주체 확인 후 종료, 최근 이벤트 20개 유지(새 리스트 대입 — JSON 변경 추적 문제 없음).
**짚어볼 점 / Watch-outs**
- L1077-1078 세션 확인마다 쓰기·커밋 1회(1차 지적 미해결, 낮음). L1091 `close_session` 은 `server_id` 를 확인하지 않는다(1차 지적 미해결, 영향 낮음). `McpSession` 은 보존 정리 대상이 아니다(`app/services/retention.py:29-34`).

## 파일 평가 / File Assessment
- 강점: (1) 기본 거부 도구 노출·도구별 최소 스코프 교환·승인 원자 소비·결과 소독이 한 경로에 모여 있고, 2차에서 실패 상태 기록·동의 예외 처리가 보강됐다. (2) `ServerRef` 스냅샷 + `run_in_threadpool` 로 세션 경계와 이벤트 루프를 일관되게 보호. (3) 캐시 수명 관리 개선 — 업스트림 세션 유휴·상한, hosted 클라이언트 닫기, AS 고정, stdio 전체 명령 일치.
- 위험: (1) [위험:높음] 자격 증명 전송처 통제가 `endpoint`+`secret_ref` 만 봐 `authorization_server`·`token_endpoint`·oauth2 엔드포인트 변경이 통제 밖 — `app/services/mcp_gateway.py:139-181` + `app/api/mcp.py:403-411` + `app/services/secrets.py:31-38`; 같은 판정이 "같은 요청 안의 endpoint 변경" 만 봐 `secret_ref` 를 비운 뒤 endpoint 를 바꾸고 다시 붙이는 2단계 PATCH 로 우회 — `mcp_gateway.py:131-138` + `app/api/mcp.py:403-405`. (2) [위험:높음] 외부 AS 에 사용자 원본 토큰 전송(허용 목록 없음) — `:173`; AS 고정은 등록값이 있을 때만 — `:201`. (3) [위험:중간] 업스트림 지정 URL 발견 요청(고정 검사 전) — `:217-240`; 리소스·프롬프트 경로 통제 부재 — `:1009-1047`; stdio 단일 토큰 항목 — `:536`.
- 개선 권고(영향순): (1) 전송처 판정 키를 넓히고 oauth2 서버 포함, 저장값과 다른 `secret_ref` 재부착도 전송처 변경으로 취급(또는 시크릿 행에 등록 전송처를 묶어 사용 시 비교), 외부 AS 는 운영자 허용 목록 + 원본 토큰 대신 audience 한정 교환 토큰. (2) 발견 URL 은 엔드포인트 origin·PRM `resource` 일치 확인, AS 가 비어 있으면 401 로 외부 AS 채택 금지. (3) `generic` 에 정책·규칙·감사, stdio 단일 토큰 항목 금지, 갱신 single-flight·`ON CONFLICT`, 캐시 키에 설정 버전.
- 파일 위험 등급 / Risk grade: 높음 — 미해결 [위험:높음] 3건(1차 핫스팟 #1·#2 부분 해결 + 2단계 PATCH 우회 신규, 스냅샷 기준 — 21:32 이후 `secrets.py` 수정은 API 미연결)이 남은 신뢰 경계 핵심 모듈.
- 재읽기 권고 레벨: 2 — 사용자→게이트웨이→업스트림 신뢰 경계가 교차한다. 인증 블록(L124-248)은 API 계층(`app/api/mcp.py:396-442`)·`app/services/secrets.py` 와 함께 읽어야 한다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| L41 `SYSTEM` admin+workload, 스코프 없음 | 미해결(수용) | `mcp_gateway.py:41` (+ 교환 토큰 roles 전파 신규 관찰 `app/services/identity.py:199-201`) |
| ServerRef 얕은 복사 | 미해결(수용) | `mcp_gateway.py:78-82` |
| L92 `_cache_token` 정리 트리거일 뿐 상한 아님 (delegated BUG-05) | 부분 해결 | 만료분 정리 `mcp_gateway.py:89-94`, 하드 상한 없음 |
| L133·L702-706 사용자 병합 `auth.secret_ref`·`endpoint` 로 시크릿 외부 전송 [높음] (핫스팟 #1) | 부분 해결 | 남의 참조 차단 `app/services/secrets.py:17-28`·`app/api/mcp.py:403,429`, 엔드포인트 변경 통제 `secrets.py:31-38`·`mcp.py:404-405,436`; `token_endpoint`·`authorization_server`·oauth2 엔드포인트는 통제 밖 `mcp_gateway.py:144-145,161,177`; `secret_ref` 비우기→endpoint 변경→재부착 2단계 우회 `mcp_gateway.py:131-138`·`secrets.py:20-21,36` |
| L172 외부 AS 에 원본 토큰 [높음] (핫스팟 #2) | 부분 해결 | AS 고정 `mcp_gateway.py:196-204,419,485` · `test/test_review_fixes.py:285-293`; 원본 토큰 전송 `:173` 그대로, 미고정 시 수용 `:201` |
| L180 `res.json()` ValueError 미포장 | 미해결 | `mcp_gateway.py:181` |
| L206·L218·L228 업스트림 지정 URL 조회 [중간] SSRF | 미해결 | `mcp_gateway.py:217-219,230,240` · `app/core/http.py:34-38` |
| L206 따옴표 없는 resource_metadata 무시 | 미해결(+IndexError 신규) | `mcp_gateway.py:218` |
| L248 `json.loads` ValueError | 미해결 | `mcp_gateway.py:263,308` |
| L285-289 SSE `buf` 무한 | 미해결 | `mcp_gateway.py:300-304` |
| L274 읽기 타임아웃 ~22분 | 미해결(설계) | `mcp_gateway.py:289` |
| L294 `mcp_consent.handle` 비 ConsentError 예외 → 업스트림 무응답 | 해결 | `app/services/mcp_consent.py:62-65` · `test/test_review_fixes.py:438-449` |
| L307-309 업스트림 세션 확인-후-설정 경쟁 | 미해결 | `mcp_gateway.py:323-345` |
| L318 initialize 오류 시 기본 버전 | 미해결 | `mcp_gateway.py:340` |
| (delegated BUG-08) `_UPSTREAM_SESSIONS` 무한 증가 | 해결 | `mcp_gateway.py:250-253,322-331` · `test/test_lv2_hardening.py:148-175` |
| L366-367 probe 조용한 삼킴 | 미해결 | `mcp_gateway.py:389-390` |
| L397 `srv.auth.update` 메모리만 | 미해결 | `mcp_gateway.py:420,486` |
| L433-434 requestState 0.5초 폴링 | 미해결(수용) | `mcp_gateway.py:456-457` |
| L462-465 401 재발견이 토큰 유출로 이어짐 | 부분 해결 | `_pinned` `mcp_gateway.py:485`; 미고정 시 `:201` |
| L480 401/404 같은 메시지 | 미해결 | `mcp_gateway.py:503` |
| L504-506 hosted 시크릿 회전 미반영 | 미해결 | `mcp_gateway.py:558-569` |
| L526 hosted 클라이언트 미닫힘·동시 생성 (delegated BUG-11) | 부분 해결 | 닫기 `mcp_gateway.py:511-526,584` · `test/test_lv2_hardening.py:124-146`; 동시 생성 누수 `:585-586` |
| L520 `blocked_url` 생성 시 1회(재바인딩) | 미해결 | `mcp_gateway.py:574` |
| L547 거부를 RuntimeError 로 | 미해결 | `mcp_gateway.py:602-603` |
| L565 stdio 실행 파일 이름만 [중간] | 부분 해결 | 전체 명령 모드 `mcp_gateway.py:529-538`; 단일 토큰 모드 유지 `:536` · `test/test_lv2_hardening.py:116-118` |
| L570 호출마다 하위 프로세스 | 미해결 | `mcp_gateway.py:623-625` |
| L574 `params["name"]` KeyError | 미해결(수용) | `mcp_gateway.py:629` |
| `load_targets` 의 `p` 미사용 — 서버 단위 접근 제어 없음 | 미해결 | `mcp_gateway.py:671` |
| `refresh_tools` 동시 삭제 시 AttributeError | 미해결 | `mcp_gateway.py:712,728` |
| L663 SYSTEM 세션만 비움 | 미해결(수용) | `mcp_gateway.py:718` |
| 헬스 루프가 워커마다 업스트림 점검 | 해결 | `app/main.py:83` 리스 |
| L743-754 test_config 세션 빼고 넣기 경쟁 | 미해결 | `mcp_gateway.py:798-809` |
| L774-775 도구 갱신 thundering herd | 미해결 | `mcp_gateway.py:826-829` |
| L809-816 `RpcContext` 헤더 값 신뢰 [중간] | 부분 해결 | `app/services/policy.py:56-73` · `app/api/mcp.py:58` · `test/test_caller_trust.py:37-105`; `session_id` 는 헤더 그대로(요청자 바인딩 용도) |
| L875-876 `_tools_of` 반환 미사용 | 미해결 | `mcp_gateway.py:930-931` |
| L916-923 일반 예외 시 status ok | 해결 | `mcp_gateway.py:976-978` · `test/test_review_fixes.py:423-436` |
| L932 `sanitize=False` 규칙이면 소독 꺼짐 | 미해결(설계) | `mcp_gateway.py:986` |
| L978-990 집계 resources/read 를 모든 서버에 | 미해결 | `mcp_gateway.py:1033-1046` |
| L1022-1023 세션 확인마다 쓰기 | 미해결 | `mcp_gateway.py:1077-1078` |
| L1036 `close_session` server_id 미확인 | 미해결 | `mcp_gateway.py:1091` |
