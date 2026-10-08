> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 22. app/services/a2a_gateway.py
**파일 요약 / File Summary**
- 역할 / Role: A2A 프록시 핵심 — Agent Card 조회·검증·해시 캐시·ETag 재검증·변경 감지, 신뢰 등급·정책·한도 판정, 카드 보안 스킴에 맞춘 업스트림 인증(RFC 8693 교환·apiKey·OAuth2 client_credentials), 0.3↔1.0 변환·바인딩(JSON-RPC·HTTP+JSON·gRPC) 선택, SSE 중계, 입출력 가드, Task 영속, 푸시 수신(JWT 검증)·클라이언트 웹훅 중계, 장기 Task 감시기.
- 진입점 거리 / Distance: 1 (`app/main.py:61,100-101`) · 2 (`app/api/a2a.py` 경유)
- LOC: 819 — `max_lines_per_file`(600) 초과 → **논리 블록 단위로 분석**(모든 블록 커버, 원문 전체 인용)
- 주요 export: `A2AError`, `AgentRef`, `CallContext`, `proxy`, `fetch_card`, `apply_card`, `refresh_card`, `gateway_card`, `card_url_for`, `ensure_exchange_policy`, `load_agent`, `record`, `route_by_skill`, `receive_push`, `PushRejected`, `verify_push_jwt`, `poll_once`, `watcher`, `upstream_headers`, `tls_kwargs`, `RESERVED`
- 의존(내부): `app.core.{logs,metrics,auth,config,context,http,tracing,ids}`, `app.db`, `app.models`, `app.services.{a2a_proto,a2a_rest,a2a_grpc,audit,governance,guards,identity,ratelimit,secrets,tls,notify}`
- 외부 패키지: httpx, pyjwt(푸시 검증), starlette, opentelemetry
- 부수효과 / Side effects: 원격 에이전트·클라이언트 웹훅 HTTP, DB 쓰기(카드·Task·사용량·결정 로그·카드 점검 이력), 알림·감사, 전역 캐시 `_TOKENS`·`_JWKS_CACHE`
- 동시성 / Concurrency: asyncio + `run_in_threadpool`(동기 DB), 감시기 무한 루프(워커마다)
- 테스트 커버리지 흔적: `test/test_a2a.py`, `test/test_a2a_rest.py`, `test/test_a2a_grpc.py`, `test/test_a2a_v10.py`, `test/test_design_gaps.py`(푸시 JWT), `test/test_mtls.py`
- 참고: 리뷰 도중(14:00) 동시 작업으로 `_cache_token`(L235-240) 이 추가되었다 — 본 리뷰는 14:03 스냅샷 기준(INDEX §1.6).

## 블록 리뷰 / Block Walk (Level 2)

```python path=app/services/a2a_gateway.py lines=1-36
"""A2A API 프록시 (FR-G08·G01, FR-R00~R06, FR-A04 게이트웨이 경유).

- Agent Card: `{url}/.well-known/agent-card.json` 조회 → 검증 → 해시 캐시 → 변경 감지(알림·카탈로그 갱신)
- 호출: 신뢰 등급·정책·한도 → 카드 securitySchemes 에 맞춘 인증 주입(bearer=RFC 8693 교환, apiKey=시크릿)
  → 클라이언트 버전(0.3/1.0)과 업스트림 버전이 다르면 변환 → SSE 중계 → Task·Artifacts 영속
- 푸시: 업스트림에는 게이트웨이 웹훅을 등록하고, 수신 시 Task 갱신 후 클라이언트 웹훅으로 중계
- 장기 Task: 감시기(watcher)가 터미널 상태까지 tasks/get 폴링 (Temporal 대체, PRD D3)
"""
import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, StreamingResponse

from opentelemetry.trace import SpanKind

from app.core import logs, metrics
from app.core.auth import Principal
from app.core.config import settings
from app.core.context import current_trace_id
from app.core.http import async_client
from app.core.tracing import span
from app.core.ids import new_id, token_urlsafe
from app.db import SessionLocal, utcnow
from app.models import A2AAgent, A2ATask, CatalogItem, RoutingDecision, TokenExchangePolicy, UsageRecord, User
from app.services import a2a_proto as ap
from app.services import a2a_rest
from app.services import audit, governance, guards, identity, ratelimit, secrets
from app.services.notify import notify
```
**분석 / Analysis (Level 2)**
- L1-8: 설계 요약 — 카드 캐시·변경 감지, 정책·한도, 스킴별 인증 주입, 버전 변환, SSE 중계, Task 영속, 푸시 중계, 감시기(Temporal 대체 — 단순 폴링).
- L33-36: 프로토콜 코덱(`a2a_proto`)·REST 바인딩·서비스들.

```python path=app/services/a2a_gateway.py lines=38-65
RESERVED = {"platform", "push", "_skill"}


class A2AError(Exception):
    def __init__(self, code: int, message: str, data: dict | None = None, http_status: int = 200):
        super().__init__(message)
        self.code, self.message, self.data, self.http_status = code, message, data or {}, http_status

    def response(self, rid) -> JSONResponse:
        err = {"code": self.code, "message": self.message, **({"data": self.data} if self.data else {})}
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": err}, status_code=self.http_status)


@dataclass
class AgentRef:
    id: str
    name: str
    url: str
    card: dict
    trust_tier: str
    auth: dict
    status: str
    info: dict = field(default_factory=dict)

    @classmethod
    def of(cls, a: A2AAgent) -> "AgentRef":
        return cls(a.id, a.name, a.url, dict(a.card or {}), a.trust_tier, dict(a.auth or {}), a.status,
                   ap.card_info(a.card or {}) if a.card else {})
```
**분석 / Analysis (Level 2)**
- L38 `RESERVED`: 에이전트 id 로 쓸 수 없는 경로 세그먼트(`/a2a/platform`·`/a2a/push`·`/a2a/_skill` 과 충돌 방지).
- L41-48 `A2AError`: JSON-RPC 오류 + HTTP 상태, `response(rid)` 로 JSONResponse.
- L51-65 `AgentRef`: DB 행의 분리 스냅샷(카드·인증 dict 복사 + 카드 해석 결과 `info`).

```python path=app/services/a2a_gateway.py lines=68-128
# ───────────────────────── Agent Card ─────────────────────────
def card_url_for(url: str) -> str:
    base = url.rstrip("/")
    return base if base.endswith("agent-card.json") else base + "/.well-known/agent-card.json"


async def tls_kwargs(tls_cfg: dict | None, target: str, url: str) -> dict:
    """원격 에이전트 업스트림 mTLS (FR-G02) — auth.tls + 게이트웨이 정책."""
    from app.services import tls
    if tls.fast_none(tls_cfg):
        return {}
    try:
        return tls.client_kwargs(await run_in_threadpool(tls.context_for, tls_cfg, target, url))
    except tls.MtlsError as exc:
        raise A2AError(-32003, str(exc), {"code": "mtls_required"}, http_status=403) from exc


async def fetch_card(card_url: str, tls_cfg: dict | None = None) -> dict:
    tls_kw = await tls_kwargs(tls_cfg, "a2a:card", card_url)
    try:
        async with async_client(timeout=10, **tls_kw) as http:
            res = await http.get(card_url)
    except httpx.HTTPError as exc:
        raise A2AError(-32603, f"Agent Card 조회 실패: {type(exc).__name__}", http_status=502) from exc
    if res.status_code != 200:
        raise A2AError(-32603, f"Agent Card 조회 실패 ({res.status_code})", http_status=502)
    try:
        card = res.json()
    except ValueError as exc:
        raise A2AError(-32603, "Agent Card 가 JSON 이 아닙니다.", http_status=502) from exc
    errors = ap.validate_card(card)
    if errors:
        raise A2AError(-32602, "Agent Card 검증 실패", {"errors": errors}, http_status=422)
    return card


def apply_card(db: Session, agent: A2AAgent, card: dict) -> bool:
    h = ap.card_hash(card)
    changed = bool(agent.card_hash) and agent.card_hash != h
    info = ap.card_info(card)
    agent.card, agent.card_hash, agent.version = card, h, card.get("version")
    agent.skills = [{"id": s.get("id"), "name": s.get("name"), "tags": s.get("tags") or []} for s in info["skills"]]
    agent.last_fetched_at = utcnow()
    agent.card_changed = agent.card_changed or changed
    if not agent.auth.get("scheme"):
        scheme = next(iter(info["schemes"].values()), {"type": "none"})
        agent.auth = {**(agent.auth or {}), "scheme": {"http": "bearer", "oauth2": "bearer", "openIdConnect": "bearer"}
                      .get(scheme.get("type"), scheme.get("type", "none")),
                      **({"header": scheme.get("name", "X-API-Key")} if scheme.get("type") == "apiKey" else {})}
    item = db.get(CatalogItem, agent.catalog_item_id) if agent.catalog_item_id else None
    if item is not None:
        item.item_metadata, item.version, item.description = card, str(card.get("version")), card.get("description")
    return changed


def ensure_exchange_policy(db: Session, agent_id: str) -> None:
    aud = f"a2a:{agent_id}"
    if not db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == aud)).first():
        db.add(TokenExchangePolicy(id=new_id("txp"), audience=aud, audience_type="a2a_agent",
                                   allowed_scopes=["a2a:invoke"], default_scopes=["a2a:invoke"], ttl_seconds=600))

```
**분석 / Analysis (Level 2)**
- L69-71: 기준 URL 이면 `/.well-known/agent-card.json` 을 붙인다.
- L74-82 `tls_kwargs`: 에이전트별 mTLS(정책 위반은 403 `mtls_required`).
- L85-101 `fetch_card`: 카드 조회 → 200·JSON·스키마 검증(필수 필드·스킬·인터페이스 URL — `app/services/a2a_proto.py:303-316`).
- L104-120 `apply_card`: 해시 비교로 변경 감지(이전 해시가 있을 때만), 스킬 요약 저장, **인증 스킴이 비어 있으면 카드의 첫 보안 스킴으로 자동 설정**(L112-116), 연결된 카탈로그 항목 메타데이터 갱신.
- L123-127: `a2a:{id}` 교환 정책 기본값(`a2a:invoke`, TTL 600초).
**짚어볼 점 / Watch-outs**
- L112-116 원격 카드(신뢰하지 않는 입력)가 게이트웨이 인증 방식을 정한다 — 예: 카드가 `apiKey` 스킴과 헤더 이름 `Authorization` 을 선언하면 게이트웨이가 그 헤더로 API 키를 보낸다. 최초 등록 시에만 적용되고 운영자가 바꿀 수 있지만, 카드 변경 후 재등록 흐름에서 의도치 않은 스킴이 설정될 수 있다.
- L119 카드 변경이 카탈로그 메타데이터를 **즉시 덮어쓴다** — "변경 감지 → 재검토 필요" 알림(L209-215)과 달리 실제 데이터는 이미 바뀐 상태다(재검토 전 적용). 재검토 전까지 이전 카드로 동작하게 하려면 별도 대기 필드가 필요하다.

```python path=app/services/a2a_gateway.py lines=130-148
def _card_changes(old: dict, new: dict) -> list[str]:
    so = {s.get("id") for s in (old or {}).get("skills") or []}
    sn = {s.get("id") for s in (new or {}).get("skills") or []}
    out = [f"+ skill {x}" for x in sorted(sn - so)] + [f"- skill {x}" for x in sorted(so - sn)]
    if (old or {}).get("version") != new.get("version") and old:
        out.append(f"~ version {old.get('version')} → {new.get('version')}")
    if set((old or {}).get("securitySchemes") or {}) != set(new.get("securitySchemes") or {}) and old:
        out.append("~ securitySchemes")
    return out


def _record_check(agent_id: str, status: str, http_status: int | None = None, etag: str | None = None,
                  changes: list | None = None, error: str | None = None) -> None:
    from app.models import A2ACardCheck
    with SessionLocal() as db:
        db.add(A2ACardCheck(id=new_id("acc"), agent_id=agent_id, status=status, http_status=http_status, etag=etag,
                            changes=changes or [], error=error))
        db.commit()

```
**분석 / Analysis (Level 2)**
- L130-138 `_card_changes`: 스킬 추가·삭제, 버전·보안 스킴 변경 요약(사람이 읽는 diff).
- L141-147 `_record_check`: 카드 재검증 이력 행.

```python path=app/services/a2a_gateway.py lines=150-216
async def refresh_card(agent_id: str, actor: str = "system:gateway") -> dict:
    """카드 재검증 (SCR-G03 ⑥⑦) — ETag(If-None-Match)로 변경 없으면 304, 실패해도 이전 캐시를 유지하고 이력에 남긴다."""
    def _load():
        with SessionLocal() as db:
            a = db.get(A2AAgent, agent_id)
            if a is None:
                raise A2AError(-32001, f"원격 에이전트 없음: {agent_id}", http_status=404)
            return a.card_url, (a.auth or {}).get("tls"), a.etag, dict(a.card or {})
    card_url, tls_cfg, etag, old = await run_in_threadpool(_load)
    tls_kw = await tls_kwargs(tls_cfg, "a2a:card", card_url)
    try:
        async with async_client(timeout=10, **tls_kw) as http:
            res = await http.get(card_url, headers={"If-None-Match": etag} if etag and old else {})
    except httpx.HTTPError as exc:
        await run_in_threadpool(_record_check, agent_id, "error", None, etag, None, f"{type(exc).__name__}")
        raise A2AError(-32603, f"Agent Card 조회 실패: {type(exc).__name__} — 이전 캐시 유지", http_status=502) from exc
    max_age = None
    import re as _re
    if m := _re.search(r"max-age=(\d+)", res.headers.get("cache-control", "")):
        max_age = int(m.group(1))
    if res.status_code == 304:
        def _touch():
            with SessionLocal() as db:
                a = db.get(A2AAgent, agent_id)
                a.last_fetched_at = utcnow()
                if max_age:
                    a.cache_ttl_s = max_age
                db.commit()
        await run_in_threadpool(_touch)
        await run_in_threadpool(_record_check, agent_id, "not_modified", 304, etag)
        return {"agent_id": agent_id, "changed": False, "not_modified": True, "card": old, "info": ap.card_info(old)}
    try:
        if res.status_code != 200:
            raise A2AError(-32603, f"Agent Card 조회 실패 ({res.status_code}) — 이전 캐시 유지", http_status=502)
        card = res.json()
        errors = ap.validate_card(card)
        if errors:
            raise A2AError(-32602, "Agent Card 검증 실패 — 이전 캐시 유지", {"errors": errors}, http_status=422)
    except (A2AError, ValueError) as exc:
        msg = exc.message if isinstance(exc, A2AError) else "JSON 아님"
        await run_in_threadpool(_record_check, agent_id, "error", res.status_code, etag, None, msg)
        if isinstance(exc, A2AError):
            raise
        raise A2AError(-32603, "Agent Card 가 JSON 이 아닙니다 — 이전 캐시 유지", http_status=502) from exc
    new_etag = res.headers.get("etag")

    def _save():
        with SessionLocal() as db:
            a = db.get(A2AAgent, agent_id)
            changed = apply_card(db, a, card)
            a.etag = new_etag
            if max_age:
                a.cache_ttl_s = max_age
            ensure_exchange_policy(db, agent_id)
            db.commit()
            return changed
    changed = await run_in_threadpool(_save)
    await run_in_threadpool(_record_check, agent_id, "changed" if changed else "ok", 200, new_etag,
                            _card_changes(old, card) if changed else [])
    if changed:
        await run_in_threadpool(lambda: notify("a2a.card_changed", f"Agent Card 변경: {agent_id}",
                                               f"version {card.get('version')} — 재검토 필요", severity="warn",
                                               link=f"/console/a2a/{agent_id}", audience_role="security",
                                               dedupe_key=f"card:{agent_id}:{ap.card_hash(card)[:12]}"))
        await run_in_threadpool(lambda: audit.append("a2a.card.changed", actor=actor, target=agent_id,
                                                     data={"version": card.get("version")}))
    return {"agent_id": agent_id, "changed": changed, "card": card, "info": ap.card_info(card)}
```
**분석 / Analysis (Level 2)**
- L152-158: 에이전트 로드(카드 URL·TLS·ETag·이전 카드).
- L159-165: `If-None-Match` 조건부 GET — 네트워크 오류면 이력 기록 후 502(이전 캐시 유지).
- L166-169: `Cache-Control: max-age` 를 카드 캐시 TTL 로.
- L170-180: 304 면 조회 시각만 갱신.
- L181-193: 200·JSON·스키마 검증 실패면 이력 기록 후 오류(이전 캐시 유지 — 장애가 서비스 중단으로 번지지 않게).
- L194-208: 적용·ETag·TTL·교환 정책 저장 → 이력.
- L209-215: 변경이면 보안 담당 알림(중복 억제 키 = 카드 해시 앞 12자) + 감사.
**짚어볼 점 / Watch-outs**
- L167 `import re as _re` 를 함수 안에서 — 모듈 상단으로 옮기는 편이 관례적이다.
- L173 `a = db.get(...)` 가 `None`(그 사이 삭제)이면 `AttributeError`.
- L159-165 카드 URL 은 등록 시 사용자가 넣은 값 — egress 가드(링크 로컬만 차단)를 제외하면 내부망 URL 로 등록할 수 있다(등록은 승인 대상이지만 developer 가 dry_run 으로 임의 URL 조회 가능 — `app/api/a2a.py:448-462`).

```python path=app/services/a2a_gateway.py lines=219-228
def gateway_card(agent: AgentRef, base_url: str | None = None) -> dict:
    """게이트웨이 경유용 카드 — 인터페이스 URL 을 (요청이 들어온) 게이트웨이로, 보안 스킴을 AgentNexus OIDC 로 치환."""
    url = f"{(base_url or settings.public_base_url).rstrip('/')}/a2a/{agent.id}"
    info = agent.info or ap.card_info(agent.card)
    out = ap.build_card(name=agent.card.get("name", agent.name), description=agent.card.get("description", ""),
                        url=url, version=str(agent.card.get("version", "1")), skills=agent.card.get("skills", []),
                        streaming=info.get("streaming", False), push=True, rest_url=f"{url}/rest",
                        grpc_url=settings.a2a_grpc_public_url or None, grpc_tenant=agent.id)
    out["provider"] = {"organization": "AgentNexus Gateway (proxy)", "url": settings.public_base_url}
    return out
```
**분석 / Analysis (Level 2)**
- L219-228 `gateway_card`: 게이트웨이 경유 카드 — 인터페이스 URL 을 게이트웨이 주소로, 보안 스킴을 AgentNexus OIDC(bearer)로 치환, gRPC 공개 주소·tenant(에이전트 id) 광고. 클라이언트가 원격 에이전트에 직접 접근하지 않게 하는 핵심.
**짚어볼 점 / Watch-outs**
- `base_url` 은 요청의 `request.base_url`(Host 헤더 기반 — `app/api/a2a.py:314`)에서 온다 — Host 헤더 조작으로 카드의 URL 을 바꿀 수 있다(캐시 중독 위험은 중간 캐시가 있을 때만). 공개 URL 설정(`PUBLIC_BASE_URL`)을 우선하는 편이 안전하다.

```python path=app/services/a2a_gateway.py lines=231-241
# ───────────────────────── 업스트림 호출 ─────────────────────────
_TOKENS: dict[tuple, tuple[float, str]] = {}


def _cache_token(key: tuple, expires: float, token: str) -> None:
    if len(_TOKENS) >= 1024:  # 만료분 정리 — 한 번 왔다 다시 오지 않는 주체·대상 조합이 쌓이지 않게
        now = time.time()
        for k in [k for k, (exp, _) in _TOKENS.items() if exp <= now]:
            _TOKENS.pop(k, None)
    _TOKENS[key] = (expires, token)

```
**분석 / Analysis (Level 2)**
- L232 `_TOKENS`: (주체, 에이전트) 또는 ("cc", 에이전트) → (만료, 토큰).
- L235-240 `_cache_token`: 1024 개 이상이면 만료분 정리 후 저장(`app/services/mcp_gateway.py:89-95` 와 같은 구현 — 중복 코드).
**짚어볼 점 / Watch-outs**
- 두 모듈에 같은 캐시 정리 함수가 복붙되어 있다 — 공용 유틸(`app/core`)로 묶으면 수정이 한 곳에서 끝난다. 만료 전 항목만 많으면 상한이 없는 점도 동일하다.

```python path=app/services/a2a_gateway.py lines=243-270
async def upstream_headers(agent: AgentRef, p: Principal) -> dict:
    scheme = agent.auth.get("scheme", "none")
    if scheme in ("none", None, ""):
        return {}
    if scheme == "apiKey":
        value = await run_in_threadpool(_secret, agent.auth.get("secret_ref"))
        if not value:
            raise A2AError(-32603, f"{agent.id}: API 키 미설정")
        return {agent.auth.get("header", "X-API-Key"): value}
    if scheme == "oauth2":  # OAuth2 client_credentials (SCR-G04 ②) — 게이트웨이가 발급·캐시(만료 30초 전 갱신)
        return {"Authorization": f"Bearer {await _client_credentials(agent)}"}
    if agent.auth.get("secret_ref") and not agent.auth.get("token_exchange", True):
        value = await run_in_threadpool(_secret, agent.auth["secret_ref"])
        return {"Authorization": f"Bearer {value}"}
    key = (p.sub, agent.id)
    hit = _TOKENS.get(key)
    if hit and hit[0] - 30 > time.time():
        return {"Authorization": f"Bearer {hit[1]}"}

    def _exchange():
        with SessionLocal() as db:
            return identity.exchange(db, p, f"a2a:{agent.id}", None, actor="gateway")
    try:
        tok = await run_in_threadpool(_exchange)
    except Exception as exc:  # noqa: BLE001 — token_exchange_failed
        raise A2AError(-32003, getattr(exc, "detail", str(exc)), {"code": "token_exchange_failed"}) from exc
    _cache_token(key, time.time() + tok["expires_in"], tok["access_token"])
    return {"Authorization": f"Bearer {tok['access_token']}"}
```
**분석 / Analysis (Level 2)**
- L244-246: 스킴 없음이면 헤더 없음.
- L247-251 apiKey: 시크릿 저장소 값 → 지정 헤더.
- L252-253 oauth2: 게이트웨이가 client_credentials 로 토큰 발급·캐시.
- L254-256: 시크릿이 있고 교환을 끈 경우 고정 Bearer.
- L257-270 기본: 사용자 → `a2a:{id}` audience 교환 토큰(사용자 신원 전파), 캐시.
**짚어볼 점 / Watch-outs**
- [위험:높음 · 시크릿 유출] L248·L255 `secret_ref` 는 에이전트 `auth` JSON 값이며, 관리 API 가 `a2a:write`(developer 포함)만으로 요청 본문을 병합한다(`app/api/a2a.py:529-536`). 소유권 확인이 없으므로 developer 가 **이미 승인된 다른 에이전트**의 `auth` 를 `{"scheme": "oauth2", "token_url": "https://attacker/", "client_id": "x", "secret_ref": "provider/openai/api_key"}` 로 바꾸면, 다음 호출에서 L273-294 가 그 시크릿을 `client_secret` 으로 공격자에게 POST 한다(운영 환경은 https 만 요구 — `app/api/a2a.py:533-535`, 공격자도 https 를 쓸 수 있다). `secret_ref` 는 `a2a/{agent_id}/…` 네임스페이스로 고정하고 auth 변경은 재승인해야 한다.
- L269 `tok["expires_in"]` — `identity.exchange` 가 항상 넣으므로(`app/services/identity.py:188-189`) 안전하다.

```python path=app/services/a2a_gateway.py lines=273-299
async def _client_credentials(agent: AgentRef) -> str:
    key = ("cc", agent.id)
    hit = _TOKENS.get(key)
    if hit and hit[0] - 30 > time.time():
        return hit[1]
    url, cid = agent.auth.get("token_url"), agent.auth.get("client_id")
    if not url or not cid:
        raise A2AError(-32603, f"{agent.id}: OAuth2 token_url·client_id 미설정", {"code": "auth_config"})
    form = {"grant_type": "client_credentials", "client_id": cid,
            "client_secret": await run_in_threadpool(_secret, agent.auth.get("secret_ref")) or ""}
    if agent.auth.get("scopes"):
        form["scope"] = " ".join(agent.auth["scopes"])
    try:
        async with async_client(timeout=10, **await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url)) as http:
            res = await http.post(url, data=form)
        tok = res.json() if res.status_code < 400 else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise A2AError(-32003, f"OAuth2 토큰 발급 실패: {type(exc).__name__}", {"code": "token_exchange_failed"}) from exc
    if not tok.get("access_token"):
        raise A2AError(-32003, f"OAuth2 토큰 발급 실패: HTTP {res.status_code}", {"code": "token_exchange_failed"})
    _cache_token(key, time.time() + int(tok.get("expires_in") or 300), tok["access_token"])
    return tok["access_token"]


def _secret(ref: str | None) -> str | None:
    with SessionLocal() as db:
        return secrets.get_plain(db, ref)
```
**분석 / Analysis (Level 2)**
- L273-294 `_client_credentials`: 캐시 적중이면 재사용, 아니면 `token_url` 로 client_credentials(시크릿 저장소의 client_secret, 선택 scope), mTLS 적용. 실패는 403 성격(-32003).
- L297-299 `_secret`: 스레드풀용 시크릿 조회.
**짚어볼 점 / Watch-outs**
- L286-287 위 L243-270 의 유출 싱크 — `token_url`·`secret_ref` 가 모두 사용자 변경 가능한 값이다.
- 에이전트 `auth` 를 바꿔도 `("cc", agent.id)` 캐시는 비워지지 않는다(`patch_agent` 가 캐시를 지우지 않음) — 설정 변경이 최대 토큰 만료 시각까지 반영되지 않는다.

```python path=app/services/a2a_gateway.py lines=302-315
def _upstream_version(agent: AgentRef, client_version: str) -> tuple[str, str, str]:
    """(업스트림 버전, URL, 바인딩) — JSONRPC 를 우선, 카드에 HTTP+JSON(·GRPC)만 있으면 그 바인딩으로 (D16)."""
    info = agent.info
    if client_version == ap.V10 and info.get("url_v10"):
        return ap.V10, info["url_v10"], "JSONRPC"
    if client_version == ap.V03 and info.get("url_v03"):
        return ap.V03, info["url_v03"], "JSONRPC"
    if info.get("url_v10"):
        return ap.V10, info["url_v10"], "JSONRPC"
    if not info.get("url_v03") and info.get("url_rest"):
        return ap.V10, info["url_rest"], "HTTP+JSON"
    if not info.get("url_v03") and info.get("url_grpc"):
        return ap.V10, info["url_grpc"], "GRPC"
    return ap.V03, info.get("url_v03") or agent.url, "JSONRPC"
```
**분석 / Analysis (Level 2)**
- L302-315 `_upstream_version`: (업스트림 버전, URL, 바인딩) 선택 — 클라이언트와 같은 버전의 JSON-RPC 인터페이스 우선, 1.0 JSON-RPC, 0.3 이 없으면 HTTP+JSON, 그다음 gRPC, 마지막으로 0.3/기본 URL.

```python path=app/services/a2a_gateway.py lines=318-362
async def _rest_rpc(url: str, op: str, params: dict, headers: dict, tls_kw: dict | None = None) -> dict:
    """HTTP+JSON 전용 원격 에이전트 호출 — 정준 작업 → REST 경로, google.rpc.Status 오류 → A2A 코드."""
    method, path, query, body = a2a_rest.rest_request(op, params)
    hdr = {"Content-Type": a2a_rest.MEDIA, "Accept": f"{a2a_rest.MEDIA}, application/json", "A2A-Version": ap.V10, **headers}
    try:
        async with async_client(timeout=settings.upstream_timeout, **(tls_kw or {})) as http:
            res = await http.request(method, f"{url.rstrip('/')}/{path}", params=query, headers=hdr,
                                     **({"json": body} if body is not None else {}))
    except httpx.HTTPError as exc:
        raise A2AError(-32603, f"원격 에이전트 연결 실패: {type(exc).__name__}", http_status=502) from exc
    if res.status_code in (401, 403):
        raise A2AError(-32003, f"원격 에이전트 인증 실패 ({res.status_code})", http_status=502)
    if res.status_code >= 400:
        try:
            err = res.json()
        except ValueError:
            err = None
        code, msg = a2a_rest.code_from_status(res.status_code, err)
        raise A2AError(code, msg, http_status=200 if code in a2a_rest.A2A_ERRORS else 502)
    return res.json() if res.content else {}


async def _rpc(url: str, method: str, params: dict, headers: dict, version: str, tls_kw: dict | None = None,
               binding: str = "JSONRPC", op: str | None = None) -> dict:
    if binding == "HTTP+JSON":
        return await _rest_rpc(url, op or ap.op_of(method), params, headers, tls_kw)
    if binding == "GRPC":
        from app.services import a2a_grpc
        return await a2a_grpc.call(url, op or ap.op_of(method), params, headers)
    hdr = {"Content-Type": "application/json", "A2A-Version": version, **headers}
    try:
        async with async_client(timeout=settings.upstream_timeout, **(tls_kw or {})) as http:
            res = await http.post(url, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method,
                                             "params": params}, headers=hdr)
    except httpx.HTTPError as exc:
        raise A2AError(-32603, f"원격 에이전트 연결 실패: {type(exc).__name__}", http_status=502) from exc
    if res.status_code in (401, 403):
        raise A2AError(-32003, f"원격 에이전트 인증 실패 ({res.status_code})", http_status=502)
    if res.status_code >= 400:
        raise A2AError(-32603, f"원격 에이전트 오류 ({res.status_code})", http_status=502)
    body = res.json()
    if "error" in body:
        e = body["error"]
        raise A2AError(e.get("code", -32603), e.get("message", "remote error"), e.get("data"))
    return body.get("result") or {}
```
**분석 / Analysis (Level 2)**
- L318-337 `_rest_rpc`: 정준 작업 → REST 경로·쿼리·본문(`a2a_rest.rest_request`) → 401/403 은 인증 실패, 4xx/5xx 는 google.rpc.Status → A2A 코드 변환.
- L340-362 `_rpc`: 바인딩별 분기(HTTP+JSON·gRPC·JSON-RPC). JSON-RPC 는 무작위 id 로 POST, 오류 객체는 A2AError 로.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 버그] L337·L358 `res.json()` 이 200 인데 JSON 이 아니면 `ValueError` 가 A2AError 로 감싸지지 않고 전파된다 — 호출부(`proxy`)는 A2AError 만 처리하므로 클라이언트는 500(비 JSON-RPC 본문)을 받는다. `fetch_card`(L94-97)처럼 감싸야 한다.
- L342-358 원격 응답 크기 제한이 없다 — 거대한 응답이 메모리에 그대로 적재된다.

```python path=app/services/a2a_gateway.py lines=365-426
# ───────────────────────── Task 영속 ─────────────────────────
@dataclass
class CallContext:
    principal: Principal
    agent: str | None = None  # 호출 에이전트 프로파일
    data_class: str = "internal"
    session_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    binding: str = "JSONRPC"  # 하류 바인딩 — JSONRPC | HTTP+JSON | GRPC (D16)


def _tid(agent_id: str, remote_id: str) -> str:
    return f"{agent_id}:{remote_id}"


def record(agent_id: str, obj: dict, ctx: CallContext | None = None, push_token: str | None = None,
           source: str = "send", protocol: tuple | None = None) -> None:
    """정준형 Task 또는 이벤트를 A2ATask 로 반영 — 수신 경로(send·sse·push·poll)와 상태 전이를 이벤트로 남긴다."""
    kind = obj.get("kind")
    remote = obj.get("id") if kind == "task" else obj.get("taskId")
    if not remote:
        return
    with SessionLocal() as db:
        row = db.get(A2ATask, _tid(agent_id, remote))
        if row is None:
            row = A2ATask(id=_tid(agent_id, remote), remote_task_id=remote, agent_id=agent_id, direction="outbound",
                          context_id=obj.get("contextId"), principal_sub=ctx.principal.sub if ctx else None,
                          session_id=ctx.session_id if ctx else None, run_id=ctx.run_id if ctx else None,
                          trace_id=current_trace_id(), history=[], artifacts=[])
            db.add(row)
        before = row.state  # 새 행은 None — 첫 상태도 전이로 기록
        if kind == "task":
            row.state = obj["status"]["state"]
            row.history = obj.get("history") or row.history
            row.artifacts = obj.get("artifacts") or row.artifacts
            row.status_message = ap.text_of({"kind": "task", "status": obj["status"]})[:2000] or row.status_message
        elif kind == "status-update":
            row.state = obj["status"]["state"]
            text = ap.text_of({"kind": "task", "status": obj["status"]})
            if text:
                row.status_message = text[:2000]
                row.history = [*(row.history or []), obj["status"]["message"]]
        elif kind == "artifact-update":
            arts = [a for a in (row.artifacts or []) if a.get("artifactId") != obj["artifact"].get("artifactId")]
            row.artifacts = [*arts, obj["artifact"]]
        if push_token:
            row.push_token = push_token
        ev = {"at": utcnow().isoformat(), "source": source, "kind": kind}
        if protocol:  # (하류 클라이언트, 업스트림) A2A 버전[·바인딩] — 다르면 게이트웨이가 변환했다
            ev["protocol"] = {"client": protocol[0], "upstream": protocol[1]}
            for key, b in zip(("client_binding", "upstream_binding"), protocol[2:4]):
                if b and b != "JSONRPC":
                    ev["protocol"][key] = b
        if kind in ("task", "status-update"):
            ev["state"] = row.state
            ev["transition"] = row.state != before
        if kind == "artifact-update":
            ev["artifact"] = obj["artifact"].get("name") or obj["artifact"].get("artifactId")
        row.events = [*(row.events or []), ev][-100:]
        row.updated_at = utcnow()
        db.commit()
```
**분석 / Analysis (Level 2)**
- L366-374 `CallContext`: 주체·호출 에이전트·데이터 등급·세션/실행/단계·하류 바인딩.
- L377-378 `_tid`: 게이트웨이 Task id = `{에이전트}:{원격 Task id}`.
- L381-426 `record`: 정준형 Task/이벤트 → A2ATask 행 반영 — 없으면 생성(주체·세션·실행·trace), task 면 상태·이력·아티팩트·메시지, status-update 면 상태·이력 추가, artifact-update 면 같은 id 교체. 수신 경로·버전 변환 여부·전이 여부를 이벤트로(최근 100개, 재할당 방식이라 JSON 변경 추적 문제 없음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L398·L403 `obj["status"]["state"]` — 원격이 보낸 형식이 어긋나면 `KeyError` 가 스레드풀에서 전파된다(500).
- `record` 는 이력(`history`)을 계속 덧붙인다(L407) — 상한이 없어 장기 multi-turn Task 의 행이 커진다(events 는 100개 상한).

```python path=app/services/a2a_gateway.py lines=429-461
def _usage(ctx: CallContext, agent_id: str, ms: int, status: str) -> None:
    with SessionLocal() as db:
        db.add(UsageRecord(id=new_id("use"), trace_id=current_trace_id(), session_id=ctx.session_id, run_id=ctx.run_id,
                           step_id=ctx.step_id, user_sub=ctx.principal.sub, team=ctx.principal.team, agent=ctx.agent,
                           kind="remote_agent",
                           target=agent_id, provider=agent_id, latency_ms=ms, status=status))
        db.commit()


# ───────────────────────── 프록시 ─────────────────────────
def load_agent(agent_id: str) -> AgentRef:
    with SessionLocal() as db:
        a = db.get(A2AAgent, agent_id)
        if a is None:
            raise A2AError(-32001, f"원격 에이전트를 찾을 수 없습니다: {agent_id}", http_status=404)
        return AgentRef.of(a)


async def _check(agent: AgentRef, ctx: CallContext) -> None:
    def _do():
        with SessionLocal() as db:
            a = db.get(A2AAgent, agent.id)
            dec = governance.check_agent(db, ctx.principal, a, caller_agent=ctx.agent, data_class=ctx.data_class)
            lim = ratelimit.check(db, ctx.principal, "agent", agent.id, ctx.agent) if dec.allow else None
            return dec, lim
    dec, lim = await run_in_threadpool(_do)
    if not dec.allow:
        await run_in_threadpool(lambda: audit.append("gateway.a2a.deny", actor=ctx.principal.sub, target=agent.id,
                                                     decision=dec.code, data={"reason": dec.reason}))
        raise A2AError(-32003, dec.reason, {"code": dec.code}, http_status=403)
    if lim is not None and not lim.allowed:
        raise A2AError(-32005, lim.reason or "rate limited", {"code": "rate_limited", "retry_after": lim.retry_after},
                       http_status=429)
```
**분석 / Analysis (Level 2)**
- L429-435 `_usage`: 원격 에이전트 호출 사용량 행.
- L439-444 `load_agent`: 없으면 404(-32001).
- L447-461 `_check`: 신뢰 등급·정책(`governance.check_agent`) + 허용이면 에이전트 한도 차감. 거부는 감사 후 403, 한도 초과는 429.
**짚어볼 점 / Watch-outs**
- L451 `governance.check_agent(..., caller_agent=ctx.agent)` 의 `ctx.agent` 는 클라이언트 헤더 `X-AgentNexus-Agent`(`app/api/a2a.py:47-51`) — 에이전트 주체 정책이 위조 가능하다(`app/services/policy.py:71-72`).

```python path=app/services/a2a_gateway.py lines=464-477
async def proxy(agent_id: str, ctx: CallContext, rid, method: str, params: dict):
    """클라이언트 JSON-RPC → 업스트림. 반환: JSONResponse | StreamingResponse."""
    with span(f"a2a {method} {agent_id}", SpanKind.CLIENT, **{"gen_ai.operation.name": "invoke_agent",
                                                              "gen_ai.agent.name": agent_id, "a2a.method": method,
                                                              "anx.agent": ctx.agent}):
        try:
            res = await _proxy(agent_id, ctx, rid, method, params)
            metrics.A2A_CALLS.labels(agent_id, ap.op_of(method) or "unknown", "ok").inc()
            return res
        except A2AError as exc:
            metrics.A2A_CALLS.labels(agent_id, ap.op_of(method) or "unknown", "error").inc()
            logs.event("warning", "gateway", f"A2A 호출 실패/거부: {agent_id} {method}", code=exc.code,
                       detail=exc.message)
            raise
```
**분석 / Analysis (Level 2)**
- L464-477 `proxy`: CLIENT 스팬(GenAI `invoke_agent`) 안에서 실제 처리, 성공·실패 메트릭, 실패는 이벤트 로그 후 재발생.
**짚어볼 점 / Watch-outs**
- 스트리밍(`stream`)은 `StreamingResponse` 를 반환하는 순간 "ok" 로 집계된다 — 실제 스트림 실패는 이 메트릭에 반영되지 않는다.

```python path=app/services/a2a_gateway.py lines=480-544
# ── 입출력 가드 (FR-G02·G07, NFR-SEC1·PRV1) ──
def _each_part(obj, fn) -> None:
    """정준형·업스트림형 어디서든 'parts' 목록의 각 파트에 fn 적용 (Task·Message·Artifact·이벤트 공통)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "parts" and isinstance(v, list):
                for i, part in enumerate(v):
                    if isinstance(part, dict):
                        v[i] = fn(part)
            else:
                _each_part(v, fn)
    elif isinstance(obj, list):
        for v in obj:
            _each_part(v, fn)


def _guard_out(agent_id: str, ctx: CallContext, msg: dict) -> None:
    """원격(외부) 에이전트로 나가는 메시지 — 인젝션 차단·PII/시크릿 마스킹(pii_input 설정)."""
    findings: list = []

    def fix(part: dict) -> dict:
        if isinstance(part.get("text"), str):
            r = guards.scan_input(db, part["text"])
            findings.extend(r.findings)
            if r.blocked:
                raise A2AError(-32003, r.reason or "입력 가드 차단", {"code": "input_blocked", "findings": r.types},
                               http_status=403)
            return {**part, "text": r.text}
        if part.get("data") is not None and guards.security_settings(db)["pii_input"] != "allow":
            masked, f = guards.mask_pii(json.dumps(part["data"], ensure_ascii=False))
            if f:
                findings.extend(f)
                return {**part, "data": json.loads(masked)}
        return part
    with SessionLocal() as db:
        try:
            _each_part(msg, fix)
        finally:
            if findings:
                audit.append("gateway.a2a.guard", actor=ctx.principal.sub, target=agent_id, data={
                    "direction": "outbound", "findings": sorted({f"{f.kind}:{f.type}" for f in findings})})


def _sanitize_in(agent_id: str, ctx: CallContext, obj: dict) -> dict:
    """원격 에이전트 응답(간접 인젝션 경로) — 지시문 제거·시크릿·PII 마스킹·소독 규칙. 도구 응답과 같은 규칙."""
    findings: list = []

    def fix(part: dict) -> dict:
        if isinstance(part.get("text"), str):
            r = guards.sanitize_tool_text(db, part["text"])
            findings.extend(r.findings)
            return {**part, "text": r.text}
        if part.get("data") is not None:
            clean, f, blocked = guards.sanitize_structured(db, part["data"])
            findings.extend(f)
            return {"kind": "text", "text": guards._BLOCKED} if blocked else {**part, "data": clean}
        return part
    with SessionLocal() as db:
        if not guards.security_settings(db).get("tool_response_sanitize", True):
            return obj
        _each_part(obj, fix)
        if findings:
            audit.append("gateway.a2a.guard", actor=ctx.principal.sub, target=agent_id, data={
                "direction": "inbound", "findings": sorted({f"{f.kind}:{f.type}" for f in findings})})
    return obj
```
**분석 / Analysis (Level 2)**
- L481-493 `_each_part`: 어떤 중첩 구조든 `parts` 목록의 각 파트에 함수 적용(제자리 교체).
- L496-520 `_guard_out`: 원격(외부)으로 나가는 메시지 — 텍스트는 입력 가드(인젝션 차단·PII 마스킹), data 파트는 설정에 따라 PII 마스킹. 발견은 감사.
- L523-544 `_sanitize_in`: 원격 응답(간접 프롬프트 인젝션 경로) — 텍스트 소독·구조화 데이터 소독(차단이면 텍스트 대체).
**짚어볼 점 / Watch-outs**
- L500-513 중첩 함수 `fix` 가 바깥 `with SessionLocal() as db` 의 `db` 를 클로저로 참조한다 — `fix` 가 `with` 블록 안에서만 호출되므로 동작하지만, 정의가 `db` 보다 위에 있어 읽기 어렵다(late binding 에 의존).
- 파일 파트(`kind: file`, URI·base64)는 가드 대상이 아니다 — 첨부 내용의 PII·인젝션은 검사되지 않는다.

```python path=app/services/a2a_gateway.py lines=547-600
async def _proxy(agent_id: str, ctx: CallContext, rid, method: str, params: dict):
    op = ap.op_of(method)
    if op is None:
        raise A2AError(-32601, f"Method not found: {method}")
    client_v = ap.version_of(method)
    agent = await run_in_threadpool(load_agent, agent_id)
    if not agent.card:
        await refresh_card(agent_id)
        agent = await run_in_threadpool(load_agent, agent_id)
    await _check(agent, ctx)
    up_v, url, binding = _upstream_version(agent, client_v)
    up_method = ap.method_for(op, up_v)
    up_params = ap.params_to(op, params, up_v)
    headers = await upstream_headers(agent, ctx.principal)
    push_token = None
    if op == "push_set":
        tid, client_cfg = ap.push_config_of(params)
        push_token = token_urlsafe(18)
        gw_cfg = {"url": f"{settings.gateway_url.rstrip('/')}/a2a/push/{push_token}", "token": push_token}
        up_params = ap.params_to("push_set", {"taskId": tid, "pushNotificationConfig": gw_cfg}, up_v)
        await run_in_threadpool(_save_push, agent_id, tid, push_token, client_cfg, ctx)
    if op in ("send", "stream") and (msg := up_params.get("message")) is not None:
        if not msg.get("messageId"):
            msg["messageId"] = uuid.uuid4().hex
        await run_in_threadpool(_guard_out, agent_id, ctx, msg)
    if op in ("stream", "resubscribe"):
        return _stream(agent, ctx, rid, url, up_method, up_params, headers, up_v, client_v, binding, op)
    t0 = time.perf_counter()
    status = "ok"
    try:
        result = await _rpc(url, up_method, up_params, headers, up_v,
                            await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url), binding, op)
    except A2AError:
        status = "error"
        raise
    finally:
        if op in ("send", "cancel"):
            await run_in_threadpool(_usage, ctx, agent_id, int((time.perf_counter() - t0) * 1000), status)
    if op in ("send", "get", "cancel"):
        canon = await run_in_threadpool(_sanitize_in, agent_id, ctx, ap.result_from(result))
        await run_in_threadpool(record, agent_id, canon, ctx, None, "poll" if op == "get" else op,
                                (client_v, up_v, ctx.binding, binding))
        if op in ("send", "cancel"):
            await run_in_threadpool(lambda: audit.append(f"a2a.{op}", actor=ctx.principal.sub, target=agent_id,
                                                         data={"task": canon.get("id"), "state": (canon.get("status")
                                                                                                  or {}).get("state"),
                                                               "agent": ctx.agent}))
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": ap.result_to(canon, client_v, op)})
    if op == "push_set":
        tid, client_cfg = ap.push_config_of(params)
        out = {"taskId": tid, "pushNotificationConfig": client_cfg} if client_v == ap.V03 else {"taskId": tid,
                                                                                                  **client_cfg}
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": out})
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})
```
**분석 / Analysis (Level 2)**
- L548-556: 메서드 → 정준 작업, 에이전트 로드(카드 없으면 조회), 판정.
- L557-560: 업스트림 버전·URL·바인딩 선택, 메서드·params 변환, 인증 헤더.
- L561-567 push_set: 업스트림에는 **게이트웨이 웹훅**(`/a2a/push/{무작위 토큰}`)을 등록하고, 클라이언트 웹훅 설정은 Task 행에 저장(중계용).
- L568-571: 나가는 메시지에 messageId 보장 + 출력 방향 가드.
- L572-573: 스트림 작업은 SSE 응답으로.
- L574-600: 단건 호출 — send·cancel 은 사용량 기록(`finally`), 결과를 소독·영속·감사 후 클라이언트 버전으로 변환해 반환.
**짚어볼 점 / Watch-outs**
- [위험:중간 · SSRF] L563 `ap.push_config_of(params)` 의 클라이언트 웹훅 URL 은 **검증 없이 저장**되고(`app/services/a2a_proto.py:246-250`), 원격 에이전트가 푸시를 보낼 때 게이트웨이가 그 URL 로 POST 한다(L771-775). egress 가드는 링크 로컬·메타데이터만 막으므로 `chat:use` 권한의 아무 사용자나 게이트웨이(backend 내부망에 연결된 유일한 서비스 — `docker-compose.yml` networks)를 통해 내부 서비스(`http://valkey:6379`, `http://db:5432`, `http://mcp-servers:8100/...`)로 요청을 보내게 할 수 있다. 웹훅 URL 은 https + 공인 주소만 허용해야 한다.
- L582-584 `status` 는 `A2AError` 일 때만 error — `ValueError`(L358) 같은 예외는 `ok` 로 사용량 기록된다(`mcp_gateway` 와 같은 패턴).

```python path=app/services/a2a_gateway.py lines=603-612
def _save_push(agent_id: str, tid: str, token: str, client_cfg: dict, ctx: CallContext) -> None:
    with SessionLocal() as db:
        row = db.get(A2ATask, _tid(agent_id, tid))
        if row is None:
            row = A2ATask(id=_tid(agent_id, tid), remote_task_id=tid, agent_id=agent_id, principal_sub=ctx.principal.sub,
                          history=[], artifacts=[])
            db.add(row)
        row.push_token = token
        row.push_config = {k: client_cfg[k] for k in ("url", "token") if client_cfg.get(k)}
        db.commit()
```
**분석 / Analysis (Level 2)**
- L603-612 `_save_push`: Task 행(없으면 생성)에 게이트웨이 푸시 토큰과 클라이언트 웹훅 `{url, token}` 저장.
**짚어볼 점 / Watch-outs**
- L611 클라이언트 웹훅 토큰을 평문 JSON 으로 저장(`app/models.py:393` 참조).
- 같은 Task 에 push_set 을 다시 하면 이전 게이트웨이 토큰이 덮여 업스트림에 등록된 옛 URL 의 푸시는 404 가 된다(업스트림 설정은 갱신되므로 정상 흐름에서는 문제없다).

```python path=app/services/a2a_gateway.py lines=615-677
def _stream(agent: AgentRef, ctx: CallContext, rid, url: str, method: str, params: dict, headers: dict,
            up_v: str, client_v: str, binding: str = "JSONRPC", op: str = "stream") -> StreamingResponse:
    async def upstream_events(http):
        """업스트림 스트림 → (JSON-RPC 형태 메시지) — REST 는 data 가 StreamResponse 그대로, gRPC 는 서버 스트림."""
        if binding == "GRPC":
            from app.services import a2a_grpc
            async for ev in a2a_grpc.stream(url, op, params, headers):
                yield ev
            return
        if binding == "HTTP+JSON":
            m, path, query, body = a2a_rest.rest_request(op, params)
            hdr = {"Content-Type": a2a_rest.MEDIA, "Accept": "text/event-stream", "A2A-Version": up_v, **headers}
            req = http.stream(m, f"{url.rstrip('/')}/{path}", params=query, headers=hdr, **({"json": body} if body is not None else {}))
        else:
            hdr = {"Content-Type": "application/json", "Accept": "text/event-stream", "A2A-Version": up_v, **headers}
            req = http.stream("POST", url, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method,
                                                 "params": params}, headers=hdr)
        async with req as res:
            if res.status_code >= 400:
                raise A2AError(-32603, f"원격 에이전트 오류 ({res.status_code})")
            async for line in res.aiter_lines():
                if not line.startswith("data:"):
                    continue
                msg = json.loads(line[5:].strip())
                if binding == "HTTP+JSON":
                    if isinstance(msg.get("error"), dict) and "status" in msg["error"]:
                        code, text = a2a_rest.code_from_status(int(msg["error"].get("code") or 500), msg)
                        yield {"error": {"code": code, "message": text}}
                    else:
                        yield {"result": msg}
                else:
                    yield msg

    async def gen():
        t0 = time.perf_counter()
        status = "ok"
        try:
            tls_kw = await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url)
            async with async_client(timeout=settings.upstream_timeout, **tls_kw) as http:
                async for msg in upstream_events(http):
                    if "error" in msg:
                        yield _sse(rid, error=msg["error"])
                        status = "error"
                        break
                    canon = await run_in_threadpool(_sanitize_in, agent.id, ctx,
                                                    ap.event_from(msg.get("result") or {}))
                    await run_in_threadpool(record, agent.id, canon, ctx, None, "sse",
                                            (client_v, up_v, ctx.binding, binding))
                    yield _sse(rid, ap.event_to(canon, client_v))
        except A2AError as exc:
            status = "error"
            yield _sse(rid, error={"code": exc.code, "message": exc.message})
        except httpx.HTTPError as exc:
            status = "error"
            yield _sse(rid, error={"code": -32603, "message": f"연결 실패: {type(exc).__name__}"})
        finally:
            await run_in_threadpool(_usage, ctx, agent.id, int((time.perf_counter() - t0) * 1000), status)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _sse(rid, result=None, error=None) -> str:
    body = {"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
```
**분석 / Analysis (Level 2)**
- L617-646 `upstream_events`: 바인딩별 업스트림 스트림 — gRPC 서버 스트림, REST(SSE `data:` 에 StreamResponse 객체), JSON-RPC(SSE `data:` 에 JSON-RPC 메시지). REST 의 google.rpc.Status 오류는 A2A 오류로 변환.
- L648-671 `gen`: 이벤트마다 소독 → 영속 → 클라이언트 버전 변환 → SSE. 오류면 SSE 오류 이벤트 후 종료, `finally` 에서 사용량.
- L675-677 `_sse`: JSON-RPC 응답 SSE 포맷.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 버그] L638 `json.loads(...)` 실패(`ValueError`)는 L664-669 의 `except A2AError`·`except httpx.HTTPError` 에 걸리지 않는다 — 스트림이 예외로 끊기고 클라이언트는 오류 이벤트 없이 연결 종료를 본다. 또 `status` 가 `"ok"` 로 남아 L670-671 사용량이 성공으로 기록된다.
- L635 SSE 의 `data:` 가 여러 줄로 나뉜 이벤트(사양 허용)는 처리하지 못한다(줄 단위 파싱).
- L653 `async with async_client(timeout=...)` 의 읽기 타임아웃은 이벤트 간격 기준 — 원격이 30초 넘게 아무것도 보내지 않으면 끊긴다(장기 작업은 하트비트 필요).

```python path=app/services/a2a_gateway.py lines=680-697
# ───────────────────────── 스킬 기반 라우팅 (FR-G01) ─────────────────────────
TIER_RANK = {"verified": 0, "trusted": 1, "untrusted": 2, "blocked": 9}


def route_by_skill(skill: str, p: Principal) -> str:
    with SessionLocal() as db:
        rows = db.scalars(select(A2AAgent).where(A2AAgent.status == "active")).all()
        cands = [a for a in rows if a.trust_tier != "blocked" and any(
            s.get("id") == skill or skill in (s.get("tags") or []) for s in a.skills or [])]
        cands.sort(key=lambda a: (TIER_RANK.get(a.trust_tier, 5), a.id))
        chosen = cands[0].id if cands else None
        db.add(RoutingDecision(id=new_id("rd"), trace_id=current_trace_id(), kind="a2a", subject=p.sub,
                               requested=f"skill:{skill}", chosen=chosen, candidates=[a.id for a in cands],
                               reason="스킬 기반 라우팅 (신뢰 등급 우선)", status="ok" if chosen else "no_match"))
        db.commit()
    if chosen is None:
        raise A2AError(-32001, f"스킬 '{skill}' 을 가진 원격 에이전트가 없습니다.", http_status=404)
    return chosen
```
**분석 / Analysis (Level 2)**
- L680-697 `route_by_skill`: 활성·비차단 에이전트 중 스킬 id 또는 태그가 맞는 것 → 신뢰 등급 순 → id 순으로 첫 번째 선택, 결정 로그 기록, 없으면 404.
**짚어볼 점 / Watch-outs**
- L686-688 모든 활성 에이전트를 메모리로 읽어 Python 에서 필터 — 에이전트 수가 적다는 가정(수용).
- `untrusted` 에이전트도 후보가 된다(뒤순위) — 실제 호출은 `_check` 에서 미신뢰 기본 거부(`app/services/governance.py:311-315`)로 막힌다. 라우팅 단계에서 거르면 불필요한 결정 로그가 줄어든다.

```python path=app/services/a2a_gateway.py lines=700-745
# ───────────────────────── 푸시 수신 · 감시기 ─────────────────────────
class PushRejected(Exception):
    """푸시 서명 검증 실패 (SCR-G07 ⑧) — 401."""


_JWKS_CACHE: dict[str, tuple[float, dict]] = {}


def _push_jwks(url: str) -> dict:
    hit = _JWKS_CACHE.get(url)
    if hit and hit[0] > time.time():
        return hit[1]
    from app.core.http import sync_client
    with sync_client(timeout=5) as http:
        doc = http.get(url).raise_for_status().json()
    _JWKS_CACHE[url] = (time.time() + 300, doc)
    return doc


def verify_push_jwt(cfg: dict, authorization: str | None, raw: bytes) -> dict:
    """원격 에이전트가 서명한 푸시 JWT 검증 — JWKS 서명·발급자·발급 시각(±5분)·본문 해시(request_body_sha256, 있으면)."""
    import hashlib

    import jwt as pyjwt
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PushRejected("서명 토큰 없음")
    tok = authorization.split(" ", 1)[1]
    try:
        kid = pyjwt.get_unverified_header(tok).get("kid")
        keys = _push_jwks(cfg["jwks_url"]).get("keys") or []
        jwk = next((k for k in keys if not kid or k.get("kid") == kid), None)
        if jwk is None:
            raise PushRejected("서명 키(kid)를 JWKS 에서 찾지 못함")
        key = pyjwt.PyJWK(jwk).key
        claims = pyjwt.decode(tok, key, algorithms=["RS256", "ES256", "EdDSA"], options={"verify_aud": False},
                              issuer=cfg.get("issuer") or None)
    except PushRejected:
        raise
    except Exception as exc:  # noqa: BLE001 — 서명·형식 오류는 모두 거부
        raise PushRejected(f"서명 검증 실패: {type(exc).__name__}") from exc
    if abs(time.time() - float(claims.get("iat") or 0)) > 300:
        raise PushRejected("발급 시각이 5분 범위를 벗어남 (재전송 의심)")
    want = claims.get("request_body_sha256")
    if want and want != hashlib.sha256(raw).hexdigest():
        raise PushRejected("본문 해시 불일치")
    return claims
```
**분석 / Analysis (Level 2)**
- L700-702 `PushRejected`: 서명 검증 실패(401).
- L705-716 `_push_jwks`: 에이전트별 푸시 서명 JWKS(운영자 설정 URL) 5분 캐시.
- L719-745 `verify_push_jwt`: Bearer JWT → kid 로 JWKS 키 선택 → **비대칭 알고리즘 허용 목록 고정**(RS256·ES256·EdDSA — L734), 발급자(설정 시), 발급 시각 ±5분, 본문 SHA-256 클레임이 있으면 일치 확인.
**짚어볼 점 / Watch-outs**
- [위험:낮음] `jti` 재사용 확인이 없다 — 5분 창 안에서 같은 서명 푸시를 재전송(replay)할 수 있다. 본문 해시가 있으면 같은 내용만 재전송되므로 영향은 "같은 상태 갱신 반복" 수준.
- L734 `issuer=cfg.get("issuer") or None` — 발급자를 설정하지 않으면 어떤 발급자든 통과(키가 맞으면). `verify_aud=False` 도 같은 맥락.
- L713 동기 HTTP(`sync_client`) — `receive_push` 가 `run_in_threadpool` 로 호출하므로 루프를 막지 않는다.

```python path=app/services/a2a_gateway.py lines=748-776
async def receive_push(token: str, body: dict, header_token: str | None, authorization: str | None = None,
                       raw: bytes = b"") -> bool:
    def _find():
        with SessionLocal() as db:
            row = db.scalars(select(A2ATask).where(A2ATask.push_token == token)).first()
            if row is None:
                return None
            agent = db.get(A2AAgent, row.agent_id)
            return row.agent_id, dict(row.push_config or {}), dict(((agent.auth or {}) if agent else {}).get("push_verify") or {})
    found = await run_in_threadpool(_find)
    if found is None or (header_token and header_token != token):
        return False
    agent_id, client, verify = found
    if verify.get("jwks_url"):
        try:
            await run_in_threadpool(verify_push_jwt, verify, authorization, raw)
        except PushRejected as exc:
            reason = str(exc)
            await run_in_threadpool(lambda: audit.append("a2a.push.reject", actor=f"agent:{agent_id}", target=agent_id,
                                                         decision="deny", data={"reason": reason}))
            raise
    canon = ap.result_from(body) if "status" in body or "task" in body else ap.event_from(body)
    await run_in_threadpool(record, agent_id, canon, None, None, "push")
    if client and client.get("url"):
        with contextlib.suppress(Exception):
            async with async_client(timeout=5) as http:
                await http.post(client["url"], json=canon, headers={
                    "X-A2A-Notification-Token": client.get("token", "")})
    return True
```
**분석 / Analysis (Level 2)**
- L750-757: 푸시 토큰으로 Task·에이전트 푸시 검증 설정 조회.
- L758-759: 토큰 불일치·없음이면 404.
- L760-767: 검증 설정(JWKS)이 있으면 서명 검증, 실패는 감사 후 401.
- L769-770: 본문 → 정준형 → 영속.
- L771-775: 클라이언트 웹훅이 있으면 중계(`X-A2A-Notification-Token` 은 클라이언트가 준 토큰).
**짚어볼 점 / Watch-outs**
- [위험:중간 · 무결성] L769-770 푸시 토큰 X 로 찾은 Task 가 아니라 **본문의 task id** 로 행을 갱신한다(`record(agent_id, canon)` → `_tid(agent_id, canon.id)`). 원격 에이전트(또는 토큰을 아는 자)가 같은 에이전트의 **다른 Task**(다른 사용자의 것 포함)를 갱신·생성할 수 있다. `canon` 의 id 가 `row.remote_task_id` 와 같은지 확인해야 한다.
- [위험:중간 · SSRF] L771-775 클라이언트 웹훅 중계(위 L561-567 참조). `contextlib.suppress(Exception)` 은 **조용한 예외 삼킴** — 중계 실패가 로그·감사·메트릭 어디에도 남지 않는다.
- 검증 설정이 없으면 URL 의 무작위 토큰(144비트)만으로 인증된다 — 토큰이 노출되면(로그·프록시) 누구나 Task 상태를 바꿀 수 있다.

```python path=app/services/a2a_gateway.py lines=779-819
def _principal_for(sub: str | None) -> Principal | None:
    if not sub:
        return None
    if sub.startswith("workload:"):
        return Principal(sub=sub, kind="workload", roles=["user"])
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.sub == sub)).first()
        if u is None:
            return None
        return Principal(sub=u.sub, kind="delegation", name=u.name or u.username, email=u.email,
                         roles=list(u.roles or ["user"]), team=u.team, user_id=u.id, actor="gateway-watcher")


async def poll_once(limit: int = 20) -> int:
    """비터미널 outbound Task 를 tasks/get 으로 갱신 (푸시 미사용·유실 대비)."""
    def _due():
        with SessionLocal() as db:
            rows = db.scalars(select(A2ATask).where(A2ATask.direction == "outbound",
                                                    A2ATask.state.in_(("submitted", "working")))
                              .order_by(A2ATask.updated_at).limit(limit)).all()
            return [(r.agent_id, r.remote_task_id, r.principal_sub) for r in rows]
    n = 0
    for agent_id, remote, sub in await run_in_threadpool(_due):
        p = await run_in_threadpool(_principal_for, sub)
        if p is None:
            continue
        with contextlib.suppress(A2AError, httpx.HTTPError):
            agent = await run_in_threadpool(load_agent, agent_id)
            up_v, url, binding = _upstream_version(agent, ap.V10)
            result = await _rpc(url, ap.method_for("get", up_v), {"id": remote}, await upstream_headers(agent, p),
                                up_v, await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url), binding, "get")
            await run_in_threadpool(record, agent_id, ap.result_from(result), None, None, "poll")
            n += 1
    return n


async def watcher(interval: float = 5.0) -> None:
    while True:
        await asyncio.sleep(interval)
        with contextlib.suppress(Exception):
            await poll_once()
```
**분석 / Analysis (Level 2)**
- L779-789 `_principal_for`: 감시기가 사용자 대신 원격을 조회할 주체 — 워크로드 sub 면 워크로드, 사용자면 DB 정보로 `delegation` 주체(행위자 `gateway-watcher`).
- L792-812 `poll_once`: submitted·working outbound Task 를 오래된 순(`updated_at`) 20개 — 각자 `tasks/get` 으로 갱신.
- L815-819 `watcher`: 주기마다 `poll_once`, 모든 예외 무시.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 버그(기아)] L796-797 은 **가장 오래된 20개**만 고르고, 실패(L805 `suppress(A2AError, httpx.HTTPError)`)하거나 주체가 없으면(L802-803 `continue`) `updated_at` 이 바뀌지 않는다. 영구 실패하는 Task 가 20개 이상 쌓이면(원격 에이전트 삭제·인증 오류·네트워크 차단) **매 주기 같은 20개만 재시도**하고 나머지 Task 는 영원히 폴링되지 않는다. 실패 시 `last_polled_at`(모델에 이미 있음 — `app/models.py:396`)을 갱신하고 그것으로 정렬해야 한다.
- [위험:낮음] L805·L818 조용한 예외 삼킴 — 감시기 실패가 관측되지 않는다. 또 L805 가 잡지 않는 예외(`KeyError` 등, L398 참조)는 L818 까지 올라가 **그 주기의 나머지 Task 처리를 중단**한다.
- L815-819 감시기는 `app/main.py:99-101` 에서 **워커마다** 실행된다(리스 없음) — 다중 워커·파드에서 같은 Task 를 중복 폴링한다.
- L784-789 사용자 부재 중에도 사용자 신원으로 토큰을 교환해 원격을 호출한다(위임 설계) — 사용자가 비활성화되면 L785 조회로 막히지만 역할 변경은 즉시 반영된다(조회 시점 기준).

## 파일 평가 / File Assessment
- 강점: (1) 카드 재검증(ETag·max-age·실패 시 이전 캐시 유지·이력)과 변경 알림. (2) 게이트웨이 경유 카드로 원격 직접 접근 차단, 입출력 가드·소독을 모든 바인딩에 적용. (3) 푸시 JWT 검증의 알고리즘 고정·시각·본문 해시.
- 위험: (1) [높음] 사용자 병합 가능한 `auth`(`secret_ref`·`token_url`)로 임의 시크릿 외부 전송(L243-294). (2) [중간] 클라이언트 웹훅 URL SSRF + 조용한 실패(L561-567, L771-775), 푸시 본문 task id 미검증(L769-770). (3) [중간] 감시기 기아(L792-812)·워커 중복 실행, [낮음] 비 JSON 응답 처리 누락(L337·L358·L638).
- 개선 권고(영향순): (1) `secret_ref`·`token_url` 변경을 보안 권한·재승인으로 제한, 네임스페이스 고정. (2) 웹훅 URL 검증(https·공인 주소)·중계 실패 감사, 푸시 id 일치 검사. (3) 감시기에 리스 + `last_polled_at` 기반 순환, `ValueError` 처리.
- 재읽기 권고 레벨: 2 — 외부 에이전트·클라이언트 웹훅·사용자 시크릿이 교차하는 신뢰 경계다.
