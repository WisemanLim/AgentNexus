"""A2A API 프록시 (FR-G08·G01, FR-R00~R06, FR-A04 게이트웨이 경유).

- Agent Card: `{url}/.well-known/agent-card.json` 조회 → 검증 → 해시 캐시 → 변경 감지(알림·카탈로그 갱신)
- 호출: 신뢰 등급·정책·한도 → 카드 securitySchemes 에 맞춘 인증 주입(bearer=RFC 8693 교환, apiKey=시크릿)
  → 클라이언트 버전(0.3/1.0)과 업스트림 버전이 다르면 변환 → SSE 중계 → Task·Artifacts 영속
- 푸시: 업스트림에는 게이트웨이 웹훅을 등록하고, 수신 시 Task 갱신 후 클라이언트 웹훅으로 중계
- 장기 Task: 감시기(watcher)가 터미널 상태까지 tasks/get 폴링 (Temporal 대체, PRD D3)
"""
import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from urllib.parse import urlparse
from dataclasses import dataclass, field

import httpx
from sqlalchemy import func, select
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
        if url.startswith("grpcs://"):  # gRPC 채널 자격 증명 — 같은 TLS 설정·mTLS 정책 (D19-②)
            return {"grpc_credentials": await run_in_threadpool(tls.grpc_credentials, tls_cfg, target, url)}
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


# ───────────────────────── 업스트림 호출 ─────────────────────────
_TOKENS: dict[tuple, tuple[float, str]] = {}


def _cache_token(key: tuple, expires: float, token: str) -> None:
    if len(_TOKENS) >= 1024:  # 만료분 정리 — 한 번 왔다 다시 오지 않는 주체·대상 조합이 쌓이지 않게
        now = time.time()
        for k in [k for k, (exp, _) in _TOKENS.items() if exp <= now]:
            _TOKENS.pop(k, None)
    _TOKENS[key] = (expires, token)


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
    key = ("x", p.sub, agent.id)  # 종류 접두 — client_credentials 키 ("cc", id) 와 섞이지 않게 (59th)
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


def drop_tokens(agent_id: str) -> int:
    """에이전트 인증 설정이 바뀌면 이 워커의 캐시 토큰(client_credentials·교환)을 버린다 (D21-⑤) — 다른 워커는 만료까지."""
    gone = [k for k in list(_TOKENS) if k == ("cc", agent_id) or (k[0] == "x" and k[2] == agent_id)]  # 교환 키 = ("x", sub, id) (59th)
    for k in gone:
        _TOKENS.pop(k, None)
    return len(gone)


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


async def _rest_rpc(url: str, op: str, params: dict, headers: dict, tls_kw: dict | None = None) -> dict:
    """HTTP+JSON 전용 원격 에이전트 호출 — 정준 작업 → REST 경로, google.rpc.Status 오류 → A2A 코드."""
    method, path, query, body = a2a_rest.rest_request(op, params)
    hdr = {"Content-Type": a2a_rest.MEDIA, "Accept": f"{a2a_rest.MEDIA}, application/json", "A2A-Version": ap.V10, **headers}
    try:
        async with async_client(timeout=settings.upstream_timeout, **(tls_kw or {})) as http:
            status, raw = await _fetch(http, method, f"{url.rstrip('/')}/{path}", params=query, headers=hdr,
                                       **({"json": body} if body is not None else {}))
    except httpx.HTTPError as exc:
        raise A2AError(-32603, f"원격 에이전트 연결 실패: {type(exc).__name__}", http_status=502) from exc
    if status in (401, 403):
        raise A2AError(-32003, f"원격 에이전트 인증 실패 ({status})", http_status=502)
    if status >= 400:
        try:
            err = json.loads(raw) if raw else None
        except ValueError:
            err = None
        code, msg = a2a_rest.code_from_status(status, err)
        raise A2AError(code, msg, http_status=200 if code in a2a_rest.A2A_ERRORS else 502)
    return _json_body(raw)


MAX_REMOTE_BYTES = 8 * 1024 * 1024  # 원격 응답·이벤트 상한 (D21-①) — 큰 응답으로 게이트웨이 메모리를 소모시키지 못하게
HISTORY_MAX = 200  # 저장하는 Task history 메시지 수 (최근 것부터)
ARTIFACTS_MAX, ARTIFACTS_MAX_BYTES = 50, 4 * 1024 * 1024  # 저장하는 Task artifacts — 개수·총량 (최근 것부터, D22-④)


def _cap_artifacts(arts: list) -> list:
    """원격 에이전트가 artifact-update 를 끝없이 보내도 행 하나가 커지지 않게 — 최근 것부터 개수·직렬화 총량 안에서만."""
    out, size = [], 0
    for a in reversed(arts[-ARTIFACTS_MAX:]):
        n = len(json.dumps(a, ensure_ascii=False, default=str))
        if size + n > ARTIFACTS_MAX_BYTES:  # 큰 항목 하나 때문에 목록 전체를 비우지 않게 — 그 항목만 건너뜀 (65th)
            continue
        size += n
        out.append(a)
    return out[::-1]


def _plain(res) -> None:
    """압축 응답 거부 — 상한은 받은 바이트로 세는데, 압축은 한 조각이 수십 MB 로 풀릴 수 있다 (요청은 identity, 59th)."""
    if res.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
        raise A2AError(-32603, "원격 응답이 압축되어 있습니다 (Accept-Encoding: identity).", http_status=502)


async def _fetch(http, method: str, url: str, **kw) -> tuple[int, bytes]:
    kw["headers"] = {"Accept-Encoding": "identity", **(kw.get("headers") or {})}
    async with http.stream(method, url, **kw) as res:
        _plain(res)
        buf = bytearray()
        async for chunk in res.aiter_bytes():
            buf += chunk
            if len(buf) > MAX_REMOTE_BYTES:
                raise A2AError(-32603, f"원격 응답이 상한({MAX_REMOTE_BYTES // 1048576} MiB)을 넘습니다.", http_status=502)
        return res.status_code, bytes(buf)


async def _capped_lines(res):
    """SSE 줄 — 줄바꿈 없이 이어지는 줄도 상한을 넘는 순간 끊는다 (aiter_lines 는 줄 끝까지 모은 뒤라 메모리를 다 쓴 다음, 59th)."""
    _plain(res)
    buf = bytearray()
    async for chunk in res.aiter_bytes():
        buf += chunk
        while (i := buf.find(b"\n")) >= 0:
            line, buf = bytes(buf[:i]), buf[i + 1:]
            yield line.rstrip(b"\r").decode("utf-8", "replace")
        if len(buf) > MAX_REMOTE_BYTES:
            raise A2AError(-32603, "원격 이벤트가 상한을 넘습니다.")
    if buf:
        yield bytes(buf).rstrip(b"\r").decode("utf-8", "replace")


def _json_body(raw: bytes) -> dict:
    try:
        out = json.loads(raw) if raw else {}
    except ValueError:
        raise A2AError(-32603, "원격 응답이 JSON 이 아닙니다.", http_status=502) from None
    if not isinstance(out, dict):
        raise A2AError(-32603, "원격 응답 형식 오류 (객체가 아님)", http_status=502)
    return out


async def _rpc(url: str, method: str, params: dict, headers: dict, version: str, tls_kw: dict | None = None,
               binding: str = "JSONRPC", op: str | None = None) -> dict:
    if binding == "HTTP+JSON":
        return await _rest_rpc(url, op or ap.op_of(method), params, headers, tls_kw)
    if binding == "GRPC":
        from app.services import a2a_grpc
        return await a2a_grpc.call(url, op or ap.op_of(method), params, headers, (tls_kw or {}).get("grpc_credentials"))
    hdr = {"Content-Type": "application/json", "A2A-Version": version, **headers}
    try:
        async with async_client(timeout=settings.upstream_timeout, **(tls_kw or {})) as http:
            status, raw = await _fetch(http, "POST", url, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method,
                                                               "params": params}, headers=hdr)
    except httpx.HTTPError as exc:
        raise A2AError(-32603, f"원격 에이전트 연결 실패: {type(exc).__name__}", http_status=502) from exc
    if status in (401, 403):
        raise A2AError(-32003, f"원격 에이전트 인증 실패 ({status})", http_status=502)
    if status >= 400:
        raise A2AError(-32603, f"원격 에이전트 오류 ({status})", http_status=502)
    body = _json_body(raw)
    if "error" in body:
        e = body["error"]
        raise A2AError(e.get("code", -32603), e.get("message", "remote error"), e.get("data"))
    return body.get("result") or {}


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
            row.history = (obj.get("history") or row.history or [])[-HISTORY_MAX:]
            row.artifacts = _cap_artifacts(obj.get("artifacts") or row.artifacts or [])
            row.status_message = ap.text_of({"kind": "task", "status": obj["status"]})[:2000] or row.status_message
        elif kind == "status-update":
            row.state = obj["status"]["state"]
            text = ap.text_of({"kind": "task", "status": obj["status"]})
            if text:
                row.status_message = text[:2000]
                row.history = [*(row.history or []), obj["status"]["message"]][-HISTORY_MAX:]
        elif kind == "artifact-update":
            arts = [a for a in (row.artifacts or []) if a.get("artifactId") != obj["artifact"].get("artifactId")]
            row.artifacts = _cap_artifacts([*arts, obj["artifact"]])
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
    if tid := _task_id_of(op, params):  # 게이트웨이를 거친 Task 는 만든 주체만 (D18-①) — 남의 Task 는 없는 것처럼
        await run_in_threadpool(_check_task_owner, agent_id, tid, ctx.principal.sub)
    up_v, url, binding = _upstream_version(agent, client_v)
    up_method = ap.method_for(op, up_v)
    up_params = ap.params_to(op, params, up_v)
    headers = await upstream_headers(agent, ctx.principal)
    push_token = None
    if op == "push_set":
        tid, client_cfg = ap.push_config_of(params)
        if reason := await run_in_threadpool(push_url_problem, client_cfg.get("url")):  # 이름 해석 — 이벤트 루프 밖 (D19-④)
            raise A2AError(-32602, reason, {"code": "push_url_rejected"}, http_status=400)
        push_token = token_urlsafe(18)
        base = await run_in_threadpool(push_callback_base, url)
        gw_cfg = {"url": f"{base}/a2a/push/{push_token}", "token": push_token}
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
    except BaseException:  # A2AError 밖의 실패(gRPC·형식 오류 등)도 사용량에는 error 로 (D21-⑤)
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
    if op == "list" and isinstance(result, dict) and isinstance(result.get("tasks"), list):  # 원격 목록 중 내 Task 만
        mine = await run_in_threadpool(_owned_ids, agent_id, [t.get("id") for t in result["tasks"] if isinstance(t, dict)],
                                       ctx.principal.sub)
        result = {**result, "tasks": [t for t in result["tasks"] if isinstance(t, dict) and t.get("id") in mine]}
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})


def _owned_ids(agent_id: str, ids: list, sub: str) -> set:
    with SessionLocal() as db:
        rows = db.scalars(select(A2ATask).where(A2ATask.id.in_([_tid(agent_id, i) for i in ids if i]),
                                                A2ATask.principal_sub == sub))
        return {r.remote_task_id for r in rows}


def _task_id_of(op: str, params: dict) -> str | None:
    if op in ("get", "cancel", "resubscribe"):
        return params.get("id")
    if op == "push_get":
        return params.get("taskId") or params.get("id")
    if op == "push_set":
        return ap.push_config_of(params)[0]
    if op in ("send", "stream"):
        return (params.get("message") or {}).get("taskId")
    return None


def _check_task_owner(agent_id: str, tid: str, sub: str) -> None:
    with SessionLocal() as db:
        row = db.get(A2ATask, _tid(agent_id, tid))
        if row is not None and row.principal_sub and row.principal_sub != sub:
            raise A2AError(ap.TASK_NOT_FOUND, f"Task not found: {tid}")


def push_callback_base(agent_url: str) -> str:
    """원격 에이전트에 넘길 푸시 콜백 기준 주소 (D19-④) — 사설망 에이전트(클러스터·compose 안)는 내부 GATEWAY_URL,
    그 밖(외부)은 PUBLIC_BASE_URL. 내부 주소를 외부에 주면 닿지도 못하고 내부 호스트 이름만 드러난다."""
    from app.core.http import internal_url
    probe = f"http://{urlparse(agent_url).netloc}/"  # grpc(s):// 바인딩도 호스트로 판정
    return (settings.gateway_url if internal_url(probe) else settings.public_base_url).rstrip("/")


def push_url_problem(url: str | None) -> str | None:
    """클라이언트 푸시 URL 검사 — http(s) 만, 운영(local·test 밖)에서는 사설·루프백·링크 로컬 수신처 거부 (D17-④)."""
    from app.core.http import internal_url
    if not url or not str(url).startswith(("http://", "https://")):
        return "pushNotificationConfig.url 은 http(s) URL 이어야 합니다."
    if settings.app_env not in ("local", "test") and internal_url(str(url)):
        return "푸시 URL 은 공인 수신처여야 합니다 — 사설·루프백·링크 로컬 주소는 받지 않습니다."
    return None


def _save_push(agent_id: str, tid: str, token: str, client_cfg: dict, ctx: CallContext) -> None:
    with SessionLocal() as db:
        row = db.get(A2ATask, _tid(agent_id, tid))
        if row is None:  # 게이트웨이가 중계한 Task 만 — 아무 id 로 행을 만들면 감시기가 영원히 폴링한다 (D19-④)
            raise A2AError(ap.TASK_NOT_FOUND, f"Task not found: {tid}")
        row.push_token = token
        row.push_config = {"url": client_cfg["url"]} if client_cfg.get("url") else {}
        if client_cfg.get("token"):  # 클라이언트 웹훅 토큰은 암호화 저장 (D21-⑤) — 전에는 평문
            from app.core import keys
            row.push_config["token_enc"] = keys.fernet().encrypt(str(client_cfg["token"]).encode()).decode()
        db.commit()


def _stream(agent: AgentRef, ctx: CallContext, rid, url: str, method: str, params: dict, headers: dict,
            up_v: str, client_v: str, binding: str = "JSONRPC", op: str = "stream") -> StreamingResponse:
    async def upstream_events(http, grpc_credentials=None):
        """업스트림 스트림 → (JSON-RPC 형태 메시지) — REST 는 data 가 StreamResponse 그대로, gRPC 는 서버 스트림."""
        if binding == "GRPC":
            from app.services import a2a_grpc
            async for ev in a2a_grpc.stream(url, op, params, headers, grpc_credentials):
                yield ev
            return
        if binding == "HTTP+JSON":
            m, path, query, body = a2a_rest.rest_request(op, params)
            hdr = {"Content-Type": a2a_rest.MEDIA, "Accept": "text/event-stream", "Accept-Encoding": "identity", "A2A-Version": up_v,
                   **headers}
            req = http.stream(m, f"{url.rstrip('/')}/{path}", params=query, headers=hdr, **({"json": body} if body is not None else {}))
        else:
            hdr = {"Content-Type": "application/json", "Accept": "text/event-stream", "Accept-Encoding": "identity", "A2A-Version": up_v,
                   **headers}
            req = http.stream("POST", url, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method,
                                                 "params": params}, headers=hdr)
        async with req as res:
            if res.status_code >= 400:
                raise A2AError(-32603, f"원격 에이전트 오류 ({res.status_code})")
            async for line in _capped_lines(res):
                if not line.startswith("data:"):
                    continue
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:  # 오류 이벤트로 알리고 끝낸다 (전에는 스트림이 말없이 끊김)
                    raise A2AError(-32603, "원격 이벤트가 JSON 이 아닙니다.") from None
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
            creds = tls_kw.pop("grpc_credentials", None)
            async with async_client(timeout=settings.upstream_timeout, **tls_kw) as http:
                async for msg in upstream_events(http, creds):
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


# ───────────────────────── 푸시 수신 · 감시기 ─────────────────────────
class PushRejected(Exception):
    """푸시 서명 검증 실패 (SCR-G07 ⑧) — 401."""


_JWKS_CACHE: dict[str, tuple[float, dict]] = {}


def _forced_key(url: str) -> str:
    return f"jwks-forced:{hashlib.sha256(url.encode()).hexdigest()[:16]}"


def _forced_ok(url: str) -> bool:
    """모르는 kid 로 강제 갱신 — URL 마다 30초에 한 번, 워커·파드 전체 공유(Valkey 보유자, 고정 만료 — 계속 보내도 늘지 않음)
    — 위조 kid 로 JWKS 를 계속 부르지 못하게 (65th)."""
    from app.services import ratelimit
    return ratelimit.backend().holder_acquire(_forced_key(url), "refresh", 1, 30)


def _push_jwks(url: str, refresh: bool = False) -> dict:
    hit = _JWKS_CACHE.get(url)
    if hit and hit[0] > time.time() and not (refresh and _forced_ok(url)):
        return hit[1]
    from app.core.http import sync_client
    with sync_client(timeout=5) as http:
        doc = http.get(url).raise_for_status().json()
    _JWKS_CACHE[url] = (time.time() + 300, doc)
    return doc


def verify_push_jwt(cfg: dict, authorization: str | None, raw: bytes) -> dict:
    """원격 에이전트가 서명한 푸시 JWT 검증 — JWKS 서명·발급자(필수)·발급 시각(±5분)·본문 해시(필수)·jti 1회 (D21-①)."""
    import hashlib

    import jwt as pyjwt
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PushRejected("서명 토큰 없음")
    tok = authorization.split(" ", 1)[1]
    if not cfg.get("issuer"):  # D21 이전 설정 — 에이전트 목록에 push_verify_issuer=false 로 보인다
        raise PushRejected("push_verify.issuer 미설정 — 발급자를 확인할 수 없다 (에이전트 auth.push_verify.issuer 지정)")
    if settings.app_env == "prod" and not str(cfg.get("jwks_url", "")).startswith("https://"):  # 13차 전 저장된 http 설정도 (65th)
        raise PushRejected("운영에서는 https JWKS 만 — push_verify.jwks_url 을 https 로 바꾸세요")
    try:
        kid = pyjwt.get_unverified_header(tok).get("kid")
        jwk = None
        for refresh in (False, True):  # 모르는 kid — 원격 에이전트가 키를 회전했을 수 있어 한 번 다시 받는다(30초에 1회, D22-④)
            keys = _push_jwks(cfg["jwks_url"], refresh).get("keys") or []
            jwk = next((k for k in keys if not kid or k.get("kid") == kid), None)
            if jwk is not None:
                break
        if jwk is None:
            raise PushRejected("서명 키(kid)를 JWKS 에서 찾지 못함")
        key = pyjwt.PyJWK(jwk).key
        claims = pyjwt.decode(tok, key, algorithms=["RS256", "ES256", "EdDSA"], options={"verify_aud": False,
                              "require": ["iss", "iat", "jti"]}, issuer=cfg["issuer"])
    except PushRejected:
        raise
    except Exception as exc:  # noqa: BLE001 — 서명·형식 오류는 모두 거부
        raise PushRejected(f"서명 검증 실패: {type(exc).__name__}") from exc
    if abs(time.time() - float(claims.get("iat") or 0)) > 300:
        raise PushRejected("발급 시각이 5분 범위를 벗어남 (재전송 의심)")
    want = claims.get("request_body_sha256")
    if not want or want != hashlib.sha256(raw).hexdigest():  # 해시가 없으면 서명한 토큰을 다른 본문에 다시 쓸 수 있다
        raise PushRejected("본문 해시 없음·불일치")
    from app.services import ratelimit  # 같은 jti 는 한 번만 (iat 허용 창 ±5분 + 여유) — 재전송 거부, 워커 공유
    if ratelimit.backend().counter_add(f"push-jti:{cfg['issuer']}:{claims['jti']}", 1, 900) > 1:
        raise PushRejected("이미 쓴 jti (재전송)")
    return claims


async def receive_push(token: str, body: dict, header_token: str | None, authorization: str | None = None,
                       raw: bytes = b"") -> bool:
    def _find():
        with SessionLocal() as db:
            row = db.scalars(select(A2ATask).where(A2ATask.push_token == token)).first()
            if row is None:
                return None
            agent = db.get(A2AAgent, row.agent_id)
            return (row.agent_id, row.remote_task_id, dict(row.push_config or {}),
                    dict(((agent.auth or {}) if agent else {}).get("push_verify") or {}), row.principal_sub,
                    bool(agent and agent.status == "active" and agent.trust_tier != "blocked"))
    found = await run_in_threadpool(_find)
    if found is None or (header_token and header_token != token):
        return False
    agent_id, remote_id, client, verify, sub, usable = found
    if verify.get("jwks_url"):
        try:
            await run_in_threadpool(verify_push_jwt, verify, authorization, raw)
        except PushRejected as exc:
            reason = str(exc)
            await run_in_threadpool(lambda: audit.append("a2a.push.reject", actor=f"agent:{agent_id}", target=agent_id,
                                                         decision="deny", data={"reason": reason}))
            raise
    canon = ap.result_from(body) if "status" in body or "task" in body else ap.event_from(body)
    pushed = canon.get("id") if canon.get("kind") == "task" else canon.get("taskId")
    if pushed != remote_id:  # 푸시 토큰은 Task 하나에 묶인다 — 같은 에이전트의 다른 Task(남의 것 포함)를 고치지 못하게
        await run_in_threadpool(lambda: audit.append("a2a.push.reject", actor=f"agent:{agent_id}", target=agent_id,
                                                     decision="deny", data={"reason": "task_mismatch"}))
        return False
    if not usable:  # 차단·비활성 에이전트의 푸시는 받지 않는다 — 호출 경로와 같은 거버넌스 (D19-④)
        await run_in_threadpool(lambda: audit.append("a2a.push.reject", actor=f"agent:{agent_id}", target=agent_id,
                                                     decision="deny", data={"reason": "agent_inactive"}))
        return False
    ctx = CallContext(principal=Principal(sub=sub or f"agent:{agent_id}", kind="delegation", roles=[]))
    canon = await run_in_threadpool(_sanitize_in, agent_id, ctx, canon)  # 푸시도 응답과 같은 소독 (D19-④)
    await run_in_threadpool(record, agent_id, canon, None, None, "push")
    if client and client.get("url"):  # 클라이언트 웹훅 중계 — 백그라운드로(원격 푸시 요청을 붙잡지 않게), 일시 장애는 재시도 (D19-②)
        task = asyncio.create_task(_relay_push(client, canon, agent_id, remote_id))
        _PUSH_RELAYS.add(task)
        task.add_done_callback(_PUSH_RELAYS.discard)
    return True


_PUSH_RELAYS: set = set()  # 루프는 Task 를 약하게만 참조 — 끝날 때까지 붙잡아 둔다


async def _relay_push(client: dict, canon: dict, agent_id: str, remote_id: str) -> None:
    from app.core.http import post_with_retry
    if reason := await run_in_threadpool(push_url_problem, client["url"]):  # 등록 뒤 이름이 사설 주소로 바뀌었을 수 있다 — 전달 직전 다시 (D19-④)
        logs.event("warning", "gateway", f"클라이언트 푸시 중계 거부: {reason}", agent=agent_id, task=remote_id)
        return
    token = client.get("token", "")  # 이전 행은 평문
    if client.get("token_enc"):
        from app.core import keys
        token = await run_in_threadpool(lambda: keys.fernet().decrypt(client["token_enc"].encode()).decode())
    if not await post_with_retry(client["url"], json=canon, headers={"X-A2A-Notification-Token": token}):
        logs.event("warning", "gateway", "클라이언트 푸시 중계 실패(재시도 후)", agent=agent_id, task=remote_id)


def _principal_for(sub: str | None) -> Principal | None:
    if not sub:
        return None
    if sub.startswith("workload:"):
        return Principal(sub=sub, kind="workload", roles=["user"])
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.sub == sub)).first()
        if u is None or u.status != "active":  # 비활성 사용자 명의로는 위임 토큰을 만들지 않는다 (D19-④)
            return None
        return Principal(sub=u.sub, kind="delegation", name=u.name or u.username, email=u.email,
                         roles=list(u.roles or ["user"]), team=u.team, user_id=u.id, actor="gateway-watcher")


async def poll_once(limit: int = 20) -> int:
    """비터미널 outbound Task 를 tasks/get 으로 갱신 (푸시 미사용·유실 대비)."""
    def _due():
        with SessionLocal() as db:
            rows = db.scalars(select(A2ATask).where(A2ATask.direction == "outbound",
                                                    A2ATask.state.in_(("submitted", "working")))
                              .order_by(func.coalesce(A2ATask.last_polled_at, A2ATask.created_at)).limit(limit)).all()
            for r in rows:  # 시도 시각을 먼저 남겨 계속 실패하는 Task 가 앞자리를 차지하지 않게(돌아가며 폴링)
                r.last_polled_at = utcnow()
            db.commit()
            return [(r.agent_id, r.remote_task_id, r.principal_sub) for r in rows]
    n = 0
    for agent_id, remote, sub in await run_in_threadpool(_due):
        p = await run_in_threadpool(_principal_for, sub)
        if p is None:
            continue
        with contextlib.suppress(A2AError, httpx.HTTPError):
            agent = await run_in_threadpool(load_agent, agent_id)
            if agent.status != "active" or agent.trust_tier == "blocked":  # 차단·비활성 에이전트는 폴링하지 않는다 (D19-④)
                continue
            up_v, url, binding = _upstream_version(agent, ap.V10)
            result = await _rpc(url, ap.method_for("get", up_v), {"id": remote}, await upstream_headers(agent, p),
                                up_v, await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url), binding, "get")
            canon = await run_in_threadpool(_sanitize_in, agent_id, CallContext(principal=p), ap.result_from(result))
            await run_in_threadpool(record, agent_id, canon, None, None, "poll")
            n += 1
    return n


async def watcher(interval: float = 5.0) -> None:
    from app.services import scheduler
    while True:
        await asyncio.sleep(interval)
        with contextlib.suppress(Exception):
            # 리스를 가진 워커만 폴링 — 같은 Task 를 워커마다 조회하지 않게 (D18-②)
            if await run_in_threadpool(scheduler.lease, "a2a_watch", interval * 3):
                await poll_once()
