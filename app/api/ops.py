"""운영·관측 화면 깊이 API (Epic 23 · SCR-B02~B07·H01~H15·I02~I05·J03·J04·J06·K01~K10·L02~L06·F01·F02).

화면별 빈 기능을 기존 도메인 API 옆에 모았다 — 세션 강제 종료·재동기화, 권한 행렬·시뮬레이터, 우회 점검, 인자 스키마 시험,
승인 위임, 시크릿 사용처·유출 점검, 장애 주입 라우팅 시험, 로그 마스킹 해제(사유·30분), A2A 관측, 비용 CSV·드릴다운,
설정 이력·되돌리기, 업그레이드 계획·SBOM 비교, 격리 복구 시험, 알림 수신 설정 등.
"""
import csv
import io
import json
import re
from datetime import timedelta
from decimal import Decimal

import jsonschema
from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import auth as A
from app.core.auth import Principal, current_principal, require
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.core.ids import new_id
from app.db import get_db, utcnow
from app.models import (A2AAgent, A2ATask, Approval, AuditEvent, Connector, IdpConnection, LogEntry, McpServer,
                        McpSession, Message, Provider, RateLimit, RefreshToken, Run, Secret, Setting, SettingHistory, Span,
                        TokenExchangePolicy, ToolRule, UsageRecord, User)
from app.services import audit, guards, policy, ratelimit
from app.services import secrets as S
from app.services.notify import notify

identity_router = APIRouter(tags=["identity"])
gateway_router = APIRouter(tags=["gateway"])
observe_router = APIRouter(tags=["observe"])
admin_router = APIRouter(tags=["admin"])


def _setting(db: Session, key: str, default=None):
    row = db.get(Setting, key)
    return row.value if row is not None else default


def put_setting(db: Session, key: str, value, actor: str) -> None:
    """설정 저장 + 이력 (SCR-L06 되돌리기)."""
    row = db.get(Setting, key)
    db.add(SettingHistory(id=new_id("sh"), key=key, value=row.value if row is not None else None, changed_by=actor))
    if row is None:
        db.add(Setting(key=key, value=value, updated_by=actor))
    else:
        row.value, row.updated_by = value, actor


def _p95(values: list) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return float(v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))])


# ═══════════════════════ 신원 — 세션·재동기화·권한 행렬 (SCR-J03·B06·L03·J04·J06) ═══════════════════════
def _sessions(db: Session, user_id: str) -> list[dict]:
    rows = db.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None),
                                                 RefreshToken.expires_at > utcnow()).order_by(RefreshToken.created_at.desc()))
    return [{"id": r.id, "client_id": r.client_id, "scope": r.scope, "created_at": r.created_at, "expires_at": r.expires_at}
            for r in rows]


@identity_router.get("/api/v1/identity/users/{user_id}/detail")
def user_detail(user_id: str, _: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise not_found("user")
    return {"id": u.id, "sub": u.sub, "username": u.username, "name": u.name, "email": u.email, "roles": u.roles,
            "groups": u.groups, "team": u.team, "status": u.status, "issuer": u.issuer, "mfa_enabled": u.mfa_enabled,
            "external": u.sub.startswith("ext:"), "claims": u.claims or {}, "last_login_at": u.last_login_at,
            "tokens_not_before": u.tokens_not_before, "sessions": _sessions(db, u.id),
            "permissions": sorted(A.Principal(sub=u.sub, kind="user", roles=list(u.roles or [])).permissions())}


class ReasonIn(BaseModel):
    reason: str = Field("", max_length=500)


@identity_router.post("/api/v1/identity/users/{user_id}/sessions/revoke")
def revoke_user_sessions(user_id: str, body: ReasonIn, p: Principal = Depends(require("identity:write")),
                         db: Session = Depends(get_db)):
    """세션 강제 종료 (SCR-J03 ⑥, MSG-002) — 갱신 토큰 폐기 + 그 전에 발급된 액세스 토큰 거부. 사유 필수(MSG-003)."""
    if not body.reason.strip():
        raise bad_request("강제 종료 사유를 입력하세요 (MSG-003).", code="reason_required")
    u = db.get(User, user_id)
    if u is None:
        raise not_found("user")
    n = 0
    for r in db.scalars(select(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None))):
        r.revoked_at, n = utcnow(), n + 1
    u.tokens_not_before = utcnow()
    db.commit()
    audit.append("identity.session.revoke", actor=p.sub, target=u.sub, data={"refresh_tokens": n, "reason": body.reason})
    return {"revoked": n, "tokens_not_before": u.tokens_not_before}


@identity_router.post("/api/v1/identity/users/{user_id}/resync")
def resync_user(user_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    """재동기화 (SCR-J03 ⑦) — 마지막 IdP 로그인 클레임과 현재 그룹→역할 매핑으로 역할을 다시 계산한다."""
    u = db.get(User, user_id)
    if u is None:
        raise not_found("user")
    claims = u.claims or {}
    idp = db.get(IdpConnection, claims.get("idp")) if claims.get("idp") else None
    if idp is None:
        raise bad_request("외부 IdP 사용자만 재동기화할 수 있습니다 (로컬 계정은 역할을 직접 편집).", code="not_external")
    before = list(u.roles or [])
    u.roles = A._map_roles(idp, claims)
    db.commit()
    audit.append("identity.user.resync", actor=p.sub, target=u.sub, data={"before": before, "after": u.roles, "idp": idp.id})
    return {"before": before, "after": u.roles, "idp": idp.name}


@identity_router.get("/api/v1/me/sessions")
def my_sessions(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """내 세션 (SCR-B06 ⑤) — 토큰의 sid 가 현재 세션, sid 없는 토큰(PAT 등)이면 가장 최근 세션."""
    items = _sessions(db, p.user_id or "")
    cur = _current_sid(p, items)
    return {"items": [{**s, "current": s["id"] == cur} for s in items]}


def _current_sid(p: Principal, items: list[dict]) -> str | None:
    if p.session_id and any(s["id"] == p.session_id for s in items):
        return p.session_id
    return items[0]["id"] if items else None


@identity_router.post("/api/v1/me/sessions/revoke-others")
def revoke_other_sessions(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    items = _sessions(db, p.user_id or "")
    cur = _current_sid(p, items)
    n = 0
    for s in (x for x in items if x["id"] != cur):
        r = db.get(RefreshToken, s["id"])
        r.revoked_at, n = utcnow(), n + 1
    db.commit()
    audit.append("identity.session.revoke_others", actor=p.sub, target=p.sub, data={"revoked": n})
    return {"revoked": n}


def _all_perms() -> list[str]:
    perms = {x for v in A.ROLE_PERMS.values() for x in v if x != "*"} | {x for v in A.SCOPE_GRANTS.values() for x in v}
    return sorted(perms)


def _mappings(db: Session) -> list[dict]:
    return [{"idp_id": i.id, "idp": i.name, "value": k, "role": v}
            for i in db.scalars(select(IdpConnection)) for k, v in (i.role_mapping or {}).items()]


@identity_router.get("/api/v1/identity/rbac")
def rbac(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    """권한 행렬 (SCR-L03) — 역할별 기본·재정의·실효 권한과 IdP 그룹→역할 매핑."""
    ov = _setting(db, "rbac.overrides", {}) or {}
    perms = _all_perms()
    return {"permissions": perms, "roles": [
        {"id": r, "label": A.ROLE_LABELS.get(r, r), "admin": "*" in base, "base": perms if "*" in base else sorted(base),
         "effective": perms if "*" in base else sorted(A.role_perms(r, ov)), "overrides": ov.get(r) or {}}
        for r, base in A.ROLE_PERMS.items()], "mappings": _mappings(db)}


class RbacIn(BaseModel):
    overrides: dict[str, dict]
    reason: str = ""


@identity_router.put("/api/v1/identity/rbac")
def put_rbac(body: RbacIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    if not body.reason.strip():
        raise bad_request("권한 변경 사유를 입력하세요 (MSG-003).", code="reason_required")
    known = set(_all_perms())
    clean = {}
    for role, ov in body.overrides.items():
        if role not in A.ROLE_PERMS:
            raise bad_request(f"알 수 없는 역할: {role}")
        if "*" in A.ROLE_PERMS[role]:
            raise bad_request("관리자 역할은 재정의할 수 없습니다.")
        add, rem = sorted(set(ov.get("add") or [])), sorted(set(ov.get("remove") or []))
        bad = [x for x in add + rem if x not in known]
        if bad:
            raise bad_request(f"알 수 없는 권한: {bad}")
        if add or rem:
            clean[role] = {"add": add, "remove": rem}
    put_setting(db, "rbac.overrides", clean, p.sub)
    db.commit()
    A.reset_rbac_cache()
    audit.append("identity.rbac.update", actor=p.sub, data={"overrides": clean, "reason": body.reason})
    return rbac(p, db)


class SimIn(BaseModel):
    roles: list[str] = []
    groups: list[str] = []
    idp_id: str | None = None
    perm: str | None = None


@identity_router.post("/api/v1/identity/rbac/simulate")
def rbac_simulate(body: SimIn, _: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    """권한 시뮬레이터 (SCR-L03 ④) — 그룹(IdP 매핑)·역할 → 실효 권한, 특정 권한 허용 여부."""
    roles = set(body.roles)
    via = []
    idps = [db.get(IdpConnection, body.idp_id)] if body.idp_id else list(db.scalars(select(IdpConnection)))
    for idp in [i for i in idps if i]:
        for g in body.groups:
            r = (idp.role_mapping or {}).get(g)
            if r:
                roles.add(r)
                via.append({"group": g, "idp": idp.name, "role": r})
    roles = roles or {"user"}
    row = db.get(Setting, "rbac.overrides")  # 워커 캐시(5초) 대신 저장된 행렬 그대로 — 방금 저장한 변경도 바로 시뮬레이션
    fresh = row.value if row and isinstance(row.value, dict) else {}
    perms = set().union(*(A.role_perms(r, fresh) for r in roles))
    return {"roles": sorted(roles), "via": via, "permissions": _all_perms() if "*" in perms else sorted(perms),
            "allowed": ("*" in perms or body.perm in perms) if body.perm else None}


class ExchangeSimIn(BaseModel):
    audience: str
    scopes: list[str] = []


@identity_router.post("/api/v1/identity/token-exchange/simulate")
def exchange_simulate(body: ExchangeSimIn, _: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    """교환 시뮬레이터 (SCR-J04 ⑤) — 토큰을 발급하지 않고 정책 판정만 (허용·거부 스코프·수명·MSG-019 사유)."""
    pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == body.audience)).first()
    if pol is None or not pol.enabled:
        return {"ok": False, "reason": "교환 정책 없음 (MSG-019)", "granted": [], "denied": body.scopes}
    requested = body.scopes or list(pol.default_scopes or pol.allowed_scopes or [])
    granted = [s for s in requested if s in (pol.allowed_scopes or [])]
    denied = [s for s in requested if s not in (pol.allowed_scopes or [])]
    ok = bool(granted) and not (body.scopes and denied)
    return {"ok": ok, "granted": granted, "denied": denied, "ttl_seconds": pol.ttl_seconds, "method": pol.method,
            "reason": None if ok else (f"허용되지 않은 스코프: {' '.join(denied)} (MSG-019)" if denied else "부여 가능한 스코프 없음 (MSG-019)")}


@identity_router.post("/api/v1/identity/mcp-auth/{server_id}/rediscover")
async def mcp_auth_rediscover(server_id: str, p: Principal = Depends(require("mcp:write"))):
    """MCP Authorization 재발견 (SCR-J06) — PRM·인가 서버 메타데이터·스코프 점검표."""
    from app.db import SessionLocal
    from app.services import mcp_gateway as gw
    res = await gw.refresh_tools(server_id, p)

    def _check():
        with SessionLocal() as db:
            s = db.get(McpServer, server_id)
            if s is None:
                raise not_found("mcp server")
            a = s.auth or {}
            pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == f"mcp:{server_id}")).first()
            need = set((a.get("tool_scopes") or {}).values())
            allowed = set(pol.allowed_scopes or []) if pol else set()
            items = [{"key": "prm", "ok": bool(a.get("resource_metadata")) or a.get("type") != "oauth2",
                      "detail": a.get("resource_metadata") or ("인증 없음" if a.get("type") != "oauth2" else "PRM 없음")},
                     {"key": "authorization_server", "ok": bool(a.get("authorization_server")) or a.get("type") != "oauth2",
                      "detail": a.get("authorization_server") or "—"},
                     {"key": "scopes_supported", "ok": True, "detail": ", ".join(a.get("scopes_supported") or []) or "선언 없음"},
                     {"key": "exchange_policy", "ok": pol is not None, "detail": f"허용 {sorted(allowed)}" if pol else "교환 정책 없음"},
                     {"key": "tool_scopes", "ok": need <= allowed if pol else not need,
                      "detail": "도구 스코프가 모두 허용됨" if need <= allowed else f"허용 밖: {sorted(need - allowed)}"}]
            return items
    return {"refresh": res, "checks": await run_in_threadpool(_check)}


# ═══════════════════════ 게이트웨이 — 우회 점검·한도·거버넌스·승인 위임·시크릿·보안 (SCR-H01~H15) ═══════════════════════
@gateway_router.get("/api/v1/gateway/bypass-check")
def bypass_status(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """우회 점검 현황 (화면 진입 시 — 감사 기록 없음)."""
    return _bypass(db)


@gateway_router.post("/api/v1/gateway/bypass-check")
def bypass_check(p: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """우회 점검 '지금 점검' (SCR-H01·B05) — 단일 진입 보장 항목과 최근 차단·401/429/5xx. 감사 `gateway.bypass_check`."""
    out = _bypass(db)
    audit.append("gateway.bypass_check", actor=p.sub, data={"ok": out["ok"]})
    return out


def _bypass(db: Session) -> dict:
    from app.core import metrics, tracing
    since = utcnow() - timedelta(hours=1)
    otel_ok = not tracing.STATS.get("db_failed")
    providers = list(db.scalars(select(Provider).where(Provider.enabled.is_(True))))
    keyless = [pv.id for pv in providers if pv.kind not in ("mock", "vllm", "ollama", "openai_compat") and not pv.secret_ref]
    servers = list(db.scalars(select(McpServer).where(McpServer.status == "active")))
    stdio = [s.id for s in servers if s.transport == "stdio"]
    direct = [s.id for s in servers if s.transport == "streamable-http" and s.endpoint and s.endpoint.startswith("https://")
              and (s.auth or {}).get("type") in (None, "none")]
    usage = list(db.scalars(select(UsageRecord).where(UsageRecord.ts >= since)))
    denies = db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.ts >= utcnow() - timedelta(hours=24),
                                                                           AuditEvent.decision == "deny")) or 0
    items = [
        {"key": "provider_keys", "ok": not keyless, "detail": "상용 공급자 키는 게이트웨이 시크릿 저장소에만" if not keyless
         else f"키 참조 없는 상용 공급자: {keyless}"},
        {"key": "mcp_via_gateway", "ok": True, "detail": f"MCP 서버 {len(servers)}개 — 에이전트는 /mcp/{{id}} 로만 호출 (Host 세션이 게이트웨이 경유)"},
        {"key": "mcp_identity", "ok": not direct, "detail": "원격 MCP 는 사용자 위임·서비스 자격으로만" if not direct
         else f"인증 없이 외부로 나가는 서버: {direct}"},
        {"key": "stdio", "ok": True, "detail": f"stdio 서버 {len(stdio)}개 — 허용 명령만 실행" if stdio else "stdio 서버 없음"},
        {"key": "a2a_via_gateway", "ok": True, "detail": f"원격 에이전트 {db.scalar(select(func.count()).select_from(A2AAgent))}개 — /a2a/{{id}} 위임만"},
        {"key": "network", "ok": True, "detail": "compose: backend 내부 네트워크 · Helm: NetworkPolicy(egress 는 gateway 만)"},
        {"key": "otel", "ok": otel_ok, "detail": "관측 파이프라인 " + ("정상 — 게이트웨이 스팬 내보내기" if otel_ok
                                                                    else f"내보내기 실패 {tracing.STATS.get('db_failed')}건")},
    ]
    http = metrics.status_totals()
    return {"checked_at": utcnow(), "ok": all(i["ok"] for i in items), "items": items,
            "counts": {"blocked_24h": denies, "rate_limited_1h": sum(1 for u in usage if u.status == "rate_limited"),
                       "errors_1h": sum(1 for u in usage if u.status not in ("ok", "rate_limited", "denied")),
                       "denied_1h": sum(1 for u in usage if u.status == "denied")},
            # 프로세스 시작 이후 HTTP 응답 수(워커 합산, Prometheus 카운터) — 401 은 사용 기록에 남지 않는다
            "http_since_start": {"401": http.get("401", 0), "403": http.get("403", 0), "429": http.get("429", 0),
                                 "5xx": sum(n for k, n in http.items() if k.startswith("5"))}}


class LimitPreviewIn(BaseModel):
    kind: str = Field("model", pattern="^(model|tool|agent)$")
    target: str
    subject: dict = {}


@gateway_router.post("/api/v1/gateway/limits/preview")
def limits_preview(body: LimitPreviewIn, _: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """429 미리보기 (SCR-H09 ④) — 버킷을 쓰지 않고 적용 한도·현재 사용량·차단 여부."""
    pr = A.Principal(sub=body.subject.get("sub", "sim"), kind="user", roles=body.subject.get("roles", ["user"]),
                     team=body.subject.get("team"))
    out = []
    for lim in db.scalars(select(RateLimit).where(RateLimit.enabled.is_(True))):
        sv = ratelimit._scope_value(lim, pr, body.subject.get("agent"))
        if sv is None or not ratelimit._target_match(lim, body.kind, body.target):
            continue
        row = {"id": lim.id, "name": lim.name, "on_exceed": lim.on_exceed, "rps": float(lim.rps) if lim.rps else None,
               "tokens_per_min": lim.tokens_per_min, "budget_usd": float(lim.budget_usd) if lim.budget_usd is not None else None}
        if lim.tokens_per_min:
            row["tokens_used"] = ratelimit.backend().window_get(f"tok:{lim.id}:{sv}")
        if lim.budget_usd is not None:
            st = lim.scope_type if lim.scope_type != "any" else "team"
            used = ratelimit.spend(db, st, sv if lim.scope_type != "any" else (pr.team or ""), lim.period)
            row["spend_usd"] = float(used)
            row["exceeded"] = used >= lim.budget_usd
        if lim.tokens_per_min and row.get("tokens_used", 0) >= lim.tokens_per_min:
            row["exceeded"] = True
        row.setdefault("exceeded", False)
        row["would_block"] = row["exceeded"] and lim.on_exceed == "block"
        row["retry_after"] = (3600 if lim.budget_usd is not None and row.get("spend_usd", 0) >= float(lim.budget_usd) else 60) \
            if row["would_block"] else None  # 런타임과 같은 Retry-After (예산 3600초 · 분당 토큰 60초)
        row["fallback_model"] = lim.fallback_model if lim.on_exceed == "fallback" else None
        out.append(row)
    return {"limits": out, "would_block": any(r.get("would_block") for r in out)}


@gateway_router.get("/api/v1/gateway/governance/blocks")
def recent_blocks(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db),
                  limit: int = Query(50, le=500)):
    """최근 차단 (SCR-H10 ②) — 감사 로그의 거부 결정."""
    rows = db.scalars(select(AuditEvent).where(AuditEvent.decision == "deny").order_by(AuditEvent.seq.desc()).limit(limit))
    return {"items": [{"ts": e.ts, "action": e.action, "actor": e.actor, "target": e.target,
                       "reason": (e.data or {}).get("reason") or (e.data or {}).get("code"), "trace_id": e.trace_id} for e in rows]}


class ArgsTestIn(BaseModel):
    server_id: str
    tool: str
    args: dict = {}


@gateway_router.post("/api/v1/gateway/governance/tools/args-test")
def args_test(body: ArgsTestIn, _: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """인자 시험 (SCR-H11 ④) — 도구 inputSchema + 운영 args_schema 로 검증, 모드(strict/warn)별 결과."""
    srv = db.get(McpServer, body.server_id)
    rule = db.scalars(select(ToolRule).where(ToolRule.server_id == body.server_id, ToolRule.tool == body.tool)).first()
    if srv is None or rule is None:
        raise not_found("tool rule")
    schema = next((t.get("inputSchema") for t in (srv.tools or []) if t.get("name") == body.tool), None)
    errors = []
    for label, sch in (("inputSchema", schema), ("args_schema", rule.args_schema)):
        if sch:
            v = jsonschema.Draft202012Validator(sch)
            errors += [{"schema": label, "path": "/".join(str(x) for x in e.path) or "(root)", "message": e.message}
                       for e in v.iter_errors(body.args or {})]
    blocked = any(e["schema"] == "inputSchema" for e in errors) or (bool(errors) and rule.args_mode != "warn")
    return {"ok": not errors, "blocked": blocked, "mode": rule.args_mode, "errors": errors}


@gateway_router.post("/api/v1/gateway/governance/sanitize-rules/preview")
def sanitize_preview(text: str = Body(..., embed=True), _: Principal = Depends(require("gateway:read")),
                     db: Session = Depends(get_db)):
    """소독 미리보기 (SCR-H11 ⑥) — 도구 응답에 규칙을 적용한 결과."""
    res = guards.sanitize_tool_text(db, text)
    return {"text": res.text, "findings": [f.as_dict() for f in res.findings], "blocked": res.blocked}


@gateway_router.get("/api/v1/gateway/governance/sanitize-events")
def sanitize_events(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db),
                    limit: int = Query(50, le=500)):
    rows = db.scalars(select(AuditEvent).where(AuditEvent.action.in_(("gateway.tool.call", "gateway.a2a.guard",
                                                                       "gateway.tool.args_warn")))
                      .order_by(AuditEvent.seq.desc()).limit(limit * 3))
    out = [{"ts": e.ts, "action": e.action, "target": e.target, "actor": e.actor,
            "findings": (e.data or {}).get("sanitized") or (e.data or {}).get("findings") or ([{"type": "args"}] if e.action.endswith("args_warn") else []),
            "trace_id": e.trace_id} for e in rows]
    return {"items": [x for x in out if x["findings"]][:limit]}


class DelegateIn(BaseModel):
    to: str
    note: str = ""


@gateway_router.post("/api/v1/approvals/{approval_id}/delegate")
def delegate_approval(approval_id: str, body: DelegateIn, p: Principal = Depends(require("approvals:decide")),
                      db: Session = Depends(get_db)):
    """승인 위임 (SCR-H12 ⑤) — 다른 승인자에게 넘긴다(요청자 본인에게는 불가)."""
    ap = db.get(Approval, approval_id)
    if ap is None:
        raise not_found("approval")
    if ap.status != "pending":
        raise conflict(f"{ap.status} 상태 승인은 위임할 수 없습니다.", code="approval_closed")
    target = db.scalars(select(User).where(or_(User.sub == body.to, User.username == body.to))).first()
    if target is None or target.status != "active":
        raise not_found("delegate user")
    if target.sub == ap.requester_sub:
        raise bad_request("요청자 본인에게는 위임할 수 없습니다 (직무 분리).", code="delegate_forbidden")
    ap.delegated_to = target.sub
    db.commit()
    audit.append("approval.delegate", actor=p.sub, target=ap.target, data={"approval_id": ap.id, "to": target.sub,
                                                                           "note": body.note})
    notify("approval.pending", f"승인 위임: {ap.target}", f"{p.sub} → {target.username or target.sub}: {body.note}",
           severity="warn", link="/console/gateway/approvals", dedupe_key=f"approval.delegate:{ap.id}")
    return {"id": ap.id, "delegated_to": ap.delegated_to}


@gateway_router.delete("/api/v1/gateway/mcp/sessions/{sid}")
def close_mcp_session(sid: str, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    """MCP 프록시 세션 강제 종료 (SCR-H14 ③, MSG-002) — 목록의 해시 핸들(sid)로 찾는다."""
    from app.api.mcp import session_handle
    s = next((x for x in db.scalars(select(McpSession).where(McpSession.closed_at.is_(None))) if session_handle(x.id) == sid), None)
    if s is None:
        raise not_found("mcp session")
    if s.closed_at:
        raise bad_request("이미 종료된 세션입니다.", code="session_closed")
    s.closed_at = utcnow()
    s.events = (s.events or [])[-19:] + [{"type": "closed", "at": utcnow().isoformat(), "by": p.sub}]
    db.commit()
    audit.append("gateway.mcp.session.close", actor=p.sub, target=sid, data={"server": s.server_id})  # 핸들만 기록
    return {"closed": sid}


@gateway_router.get("/api/v1/gateway/a2a/stats")
def a2a_stats(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db), hours: int = Query(24, le=720)):
    """A2A 프록시 호출 통계 (SCR-H15 ③·K05) — 에이전트별 호출·오류·p95."""
    since = utcnow() - timedelta(hours=hours)
    rows: dict[str, dict] = {}
    for u in db.scalars(select(UsageRecord).where(UsageRecord.kind == "remote_agent", UsageRecord.ts >= since)):
        r = rows.setdefault(u.target, {"agent": u.target, "calls": 0, "errors": 0, "lat": []})
        r["calls"] += 1
        r["errors"] += 0 if u.status == "ok" else 1
        r["lat"].append(u.latency_ms)
    return {"hours": hours, "items": [{"agent": r["agent"], "calls": r["calls"], "errors": r["errors"],
                                       "p95_latency_ms": _p95(r["lat"])} for r in rows.values()]}


class AuthTestIn(BaseModel):
    token: str | None = Field(None, repr=False)


@gateway_router.post("/api/v1/gateway/security/auth-test")
def auth_test(body: AuthTestIn, _: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """미인증·잘못된 토큰 시험 (SCR-H05 ③) — 게이트웨이가 401 을 돌려주는지 (토큰은 저장·기록하지 않는다)."""
    if not body.token:
        return {"status": 401, "ok": True, "reason": "토큰 없음 → 401 (WWW-Authenticate: Bearer)"}
    try:
        pr = A.authenticate_token(db, body.token)
    except ApiError as exc:
        return {"status": exc.status, "ok": exc.status == 401, "reason": exc.detail}
    return {"status": 200, "ok": True, "reason": "유효한 토큰", "principal": {"sub": pr.sub, "kind": pr.kind, "roles": pr.roles}}


@gateway_router.get("/api/v1/gateway/security/detections")
def detections(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db), limit: int = Query(50, le=500)):
    """탐지 이벤트 (SCR-H05 ⑤) — 입력 가드·출력 소독·A2A 가드·샘플링 거부."""
    rows = db.scalars(select(AuditEvent).where(or_(AuditEvent.action.in_(("gateway.a2a.guard", "gateway.model.deny",
                                                                             "mcp.sampling.deny")),
                                                   AuditEvent.action.like("gateway.%.guard")))
                      .order_by(AuditEvent.seq.desc()).limit(limit))
    return {"items": [{"ts": e.ts, "action": e.action, "actor": e.actor, "target": e.target, "decision": e.decision,
                       "data": {k: v for k, v in (e.data or {}).items() if k in ("reason", "findings", "types", "code")},
                       "trace_id": e.trace_id} for e in rows]}


def _secret_refs(db: Session, name: str) -> list[dict]:
    out = []
    for pv in db.scalars(select(Provider).where(Provider.secret_ref == name)):
        out.append({"kind": "provider", "id": pv.id, "name": pv.name})
    for s in db.scalars(select(McpServer)):
        refs = [(s.auth or {}).get("secret_ref"), (((s.server_json or {}).get("_meta") or {}).get("hosted") or {}).get("secret_ref"),
                ((s.auth or {}).get("tls") or {}).get("ca_secret"), ((s.auth or {}).get("tls") or {}).get("client_cert_secret")]
        if name in refs:
            out.append({"kind": "mcp_server", "id": s.id, "name": s.name})
    for a in db.scalars(select(A2AAgent)):
        if name in ((a.auth or {}).get("secret_ref"), ((a.auth or {}).get("tls") or {}).get("ca_secret"),
                    ((a.auth or {}).get("tls") or {}).get("client_cert_secret")):
            out.append({"kind": "a2a_agent", "id": a.id, "name": a.name})
    for i in db.scalars(select(IdpConnection).where(IdpConnection.client_secret_ref == name)):
        out.append({"kind": "idp", "id": i.id, "name": i.name})
    for c in db.scalars(select(Connector)):
        if name in json.dumps(c.config or {}):
            out.append({"kind": "connector", "id": c.id, "name": c.name})
    return out


@gateway_router.get("/api/v1/gateway/secrets/{secret_id}/usage")
def secret_usage(secret_id: str, _: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """사용처·교체 D-day (SCR-H06 ③④)."""
    sec = db.get(Secret, secret_id) or db.scalars(select(Secret).where(Secret.name == secret_id)).first()
    if sec is None:
        raise not_found("secret")
    days = int(_setting(db, "secrets.rotation_days", 90) or 90)
    base = sec.rotated_at or sec.created_at
    due = base + timedelta(days=days)
    return {"id": sec.id, "name": sec.name, "used_by": _secret_refs(db, sec.name), "rotation_days": days,
            "rotation_due_at": due, "days_left": (due - utcnow()).days, "expires_at": sec.expires_at}


@gateway_router.post("/api/v1/gateway/secrets/{secret_id}/leak-check")
def secret_leak_check(secret_id: str, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    """유출 점검 (SCR-H06 ⑤) — 로그·스팬·감사·대화에 평문이 남았는지 검색 (값은 응답·기록하지 않는다)."""
    sec = db.get(Secret, secret_id) or db.scalars(select(Secret).where(Secret.name == secret_id)).first()
    if sec is None:
        raise not_found("secret")
    value = S.get_plain(db, sec.name) or ""
    if len(value) < 8:
        raise bad_request("8자 미만 값은 검색하지 않습니다(오탐).")
    like = f"%{value}%"
    hits = {"logs": db.scalar(select(func.count()).select_from(LogEntry).where(LogEntry.message.like(like))) or 0,
            "spans": sum(1 for (a,) in db.execute(select(Span.attributes).limit(20000)) if value in json.dumps(a or {})),
            "audit": sum(1 for (d,) in db.execute(select(AuditEvent.data).limit(20000)) if value in json.dumps(d or {})),
            "messages": db.scalar(select(func.count()).select_from(Message).where(Message.content.like(like))) or 0}
    total = sum(hits.values())
    audit.append("secret.leak_check", actor=p.sub, target=sec.name, decision="deny" if total else "allow", data=hits)
    if total:
        notify("security.leak", f"시크릿 평문 발견: {sec.name}", f"{hits}", severity="danger",
               link="/console/gateway/secrets", audience_role="security", dedupe_key=f"leak:{sec.id}")
    return {"name": sec.name, "hits": hits, "leaked": bool(total), "checked_at": utcnow()}


class RoutingTestIn(BaseModel):
    model: str = "auto"
    data_class: str = Field("internal", pattern="^(public|internal|confidential)$")
    fail: list[str] = []


@gateway_router.post("/api/v1/gateway/routing/test")
def routing_test(body: RoutingTestIn, p: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """장애 주입 시험 (SCR-H02 ⑥) — 가정한 대상 장애로 어떤 모델이 선택되는지 (호출하지 않음)."""
    from app.services import llm
    try:
        plan = llm.prepare(db, llm.CallContext(p, body.data_class, "routing-test"),
                           {"model": body.model, "messages": [{"role": "user", "content": "routing test"}]})
    except ApiError as exc:
        return {"ok": False, "reason": exc.detail, "candidates": [], "chosen": None}
    llm.discard(plan)  # 호출하지 않는 시험 — 한도 예약을 남기지 않는다 (D22-③)
    ids = [t.model_id for t in plan.targets]
    alive = [m for m in ids if m not in set(body.fail)]
    return {"ok": bool(alive), "rule": plan.rule, "candidates": ids, "failed": [m for m in ids if m in body.fail],
            "chosen": alive[0] if alive else None, "fallback_used": bool(alive) and alive[0] != (ids[0] if ids else None),
            "notes": plan.notes, "reason": None if alive else "모든 후보 장애 — 요청 실패 (MSG-015)"}


# ═══════════════════════ MCP 서버 운영 (SCR-F01·F02) ═══════════════════════
@gateway_router.post("/api/v1/mcp-servers/{server_id}/restart")
async def restart_server(server_id: str, p: Principal = Depends(require("mcp:write"))):
    """재시작 (SCR-F01 ⑤) — 게이트웨이 쪽 업스트림 세션·캐시를 비우고 initialize·tools/list 부터 다시."""
    from app.services import mcp_gateway as gw
    for k in [k for k in gw._UPSTREAM_SESSIONS if k[0] == server_id]:
        gw._UPSTREAM_SESSIONS.pop(k, None)
    for k in [k for k in gw._HOSTED if k.startswith(f"{server_id}:")]:
        gw._HOSTED.pop(k, None)
    res = await gw.refresh_tools(server_id, p)
    await run_in_threadpool(lambda: audit.append("mcp.server.restart", actor=p.sub, target=server_id,
                                                 data={"health": res.get("health")}))
    return res


class SamplingIn(BaseModel):
    policy: str = Field(pattern="^(ask|deny)$")


@gateway_router.put("/api/v1/mcp-servers/{server_id}/sampling")
def set_sampling(server_id: str, body: SamplingIn, p: Principal = Depends(require("mcp:write")), db: Session = Depends(get_db)):
    """샘플링 정책 (SCR-F02 ⑤) — ask(사용자 동의) | deny(서버의 모델 호출 요청 거부)."""
    s = db.get(McpServer, server_id)
    if s is None:
        raise not_found("mcp server")
    s.sampling_policy = body.policy
    db.commit()
    audit.append("mcp.server.sampling", actor=p.sub, target=server_id, data={"policy": body.policy})
    return {"server_id": server_id, "sampling_policy": s.sampling_policy}


@gateway_router.get("/api/v1/mcp-servers/{server_id}/logs")
def server_logs(server_id: str, _: Principal = Depends(require("mcp:read")), db: Session = Depends(get_db),
                limit: int = Query(100, le=500)):
    """서버 로그·헬스 이력 (SCR-F02 ⑦)."""
    logs = db.scalars(select(LogEntry).where(LogEntry.message.like(f"%{server_id}%")).order_by(LogEntry.id.desc()).limit(limit))
    events = db.scalars(select(AuditEvent).where(AuditEvent.target.like(f"{server_id}%")).order_by(AuditEvent.seq.desc()).limit(limit))
    return {"logs": [{"ts": e.ts, "level": e.level, "message": e.message, "trace_id": e.trace_id} for e in logs],
            "events": [{"ts": e.ts, "action": e.action, "actor": e.actor, "target": e.target, "decision": e.decision} for e in events]}


# ═══════════════════════ 관측 (SCR-K01·K02·K04·K05·K06·K09·K10) ═══════════════════════
@observe_router.get("/api/v1/observe/instrumentation")
def instrumentation(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db), hours: int = Query(1, le=168)):
    """계측 현황 (SCR-K01 ②) — 구성요소별 스팬·trace 수, 실행 중 trace 가 붙은 비율."""
    since = utcnow() - timedelta(hours=hours)
    comps = [{"service": s, "spans": n, "traces": t} for s, n, t in db.execute(
        select(Span.service, func.count(), func.count(func.distinct(Span.trace_id))).where(Span.start_time >= since)
        .group_by(Span.service))]
    runs = db.scalar(select(func.count()).select_from(Run).where(Run.created_at >= since)) or 0
    traced = db.scalar(select(func.count()).select_from(Run).where(Run.created_at >= since, Run.trace_id.is_not(None))) or 0
    return {"hours": hours, "components": comps, "runs": runs, "traced_runs": traced,
            "coverage_pct": round(traced / runs * 100, 1) if runs else None}


class UnmaskIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    reason: str = ""


def _secrets_only(text: str) -> str:
    for name, pat in guards.SECRET_PATTERNS:
        text = pat.sub(f"[MASKED:{name}]", text)
    return text


@observe_router.post("/api/v1/logs/unmask")
def unmask_logs(body: UnmaskIn, p: Principal = Depends(require("audit:read")), db: Session = Depends(get_db)):
    """마스킹 해제 (SCR-K04 ⑥) — 사유를 남기면 30분 동안 원문(PII)을 본다. 시크릿은 계속 가린다. 모든 해제는 감사 기록."""
    from app.core.keys import fernet
    grant = _setting(db, f"unmask.{p.sub}") or {}
    active = grant.get("until") and grant["until"] > utcnow().isoformat()
    if not active and len(body.reason.strip()) < 4:
        raise bad_request("마스킹 해제 사유를 입력하세요 (4자 이상, MSG-003).", code="reason_required")
    if not active:
        grant = {"until": (utcnow() + timedelta(minutes=30)).isoformat(), "reason": body.reason.strip()}
        put_setting(db, f"unmask.{p.sub}", grant, p.sub)
    out = []
    for e in db.scalars(select(LogEntry).where(LogEntry.id.in_(body.ids))):
        raw = None
        if e.raw_enc:
            try:
                d = json.loads(fernet().decrypt(e.raw_enc.encode()))
                raw = {"message": _secrets_only(d.get("message") or ""), "data": json.loads(_secrets_only(json.dumps(d.get("data") or {}, ensure_ascii=False)))}
            except Exception:  # noqa: BLE001
                raw = None
        out.append({"id": e.id, "masked": e.message, "raw": raw})
    db.commit()
    audit.append("observe.log.unmask", actor=p.sub, data={"ids": body.ids, "reason": grant.get("reason"), "until": grant["until"]})
    return {"until": grant["until"], "reason": grant.get("reason"), "items": out}


@observe_router.get("/api/v1/observe/a2a")
def observe_a2a(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db), hours: int = Query(24, le=720)):
    """A2A 관측 (SCR-K05) — Task 상태 분포·에이전트별 호출·p95·시간대별 추이."""
    since = utcnow() - timedelta(hours=hours)
    states = {s: n for s, n in db.execute(select(A2ATask.state, func.count()).where(A2ATask.created_at >= since)
                                          .group_by(A2ATask.state))}
    usage = list(db.scalars(select(UsageRecord).where(UsageRecord.kind == "remote_agent", UsageRecord.ts >= since)))
    series: dict[str, int] = {}
    for u in usage:
        k = u.ts.strftime("%Y-%m-%dT%H:00")
        series[k] = series.get(k, 0) + 1
    return {"hours": hours, "states": states, "calls": len(usage), "errors": sum(1 for u in usage if u.status != "ok"),
            "p95_latency_ms": _p95([u.latency_ms for u in usage]), "series": [{"t": k, "calls": v} for k, v in sorted(series.items())],
            "agents": a2a_stats(_, db, hours)["items"]}


@observe_router.get("/api/v1/usage/export")
def usage_export(p: Principal = Depends(require("cost:read")), db: Session = Depends(get_db), days: int = Query(30, le=366),
                 team: str | None = None):
    """비용 CSV 내보내기 (SCR-K06 ⑥)."""
    stmt = select(UsageRecord).where(UsageRecord.ts >= utcnow() - timedelta(days=days)).order_by(UsageRecord.ts)
    if team:
        stmt = stmt.where(UsageRecord.team == team)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ts", "team", "user", "kind", "target", "provider", "agent", "tokens_in", "tokens_out", "cost_usd",
                "latency_ms", "status", "trace_id"])
    n = 0
    for u in db.scalars(stmt):
        w.writerow([u.ts.isoformat(), u.team or "", u.user_sub or "", u.kind, u.target, u.provider or "", u.agent or "",
                    u.tokens_in, u.tokens_out, f"{u.cost:.6f}", u.latency_ms, u.status, u.trace_id or ""])
        n += 1
    audit.append("observe.usage.export", actor=p.sub, data={"rows": n, "days": days, "team": team})
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="agentnexus-usage-{days}d.csv"'})


@observe_router.get("/api/v1/usage/drilldown")
def usage_drilldown(_: Principal = Depends(require("cost:read")), db: Session = Depends(get_db), days: int = Query(30, le=366),
                    team: str | None = None, user: str | None = None):
    """드릴다운 (SCR-K06 ⑤) — 팀 → 사용자 → 모델·도구."""
    stmt = select(UsageRecord).where(UsageRecord.ts >= utcnow() - timedelta(days=days))
    level = "team"
    if team:
        stmt, level = stmt.where(UsageRecord.team == team), "user"
    if user:
        stmt, level = stmt.where(UsageRecord.user_sub == user), "target"
    rows: dict[str, dict] = {}
    for u in db.scalars(stmt):
        key = {"team": u.team or "-", "user": u.user_sub or "-", "target": f"{u.kind}:{u.target}"}[level]
        r = rows.setdefault(key, {"key": key, "calls": 0, "cost": Decimal(0), "tokens": 0})
        r["calls"] += 1
        r["cost"] += u.cost or Decimal(0)
        r["tokens"] += (u.tokens_in or 0) + (u.tokens_out or 0)
    items = sorted(({"key": r["key"], "calls": r["calls"], "tokens": r["tokens"], "cost_usd": float(r["cost"])}
                    for r in rows.values()), key=lambda x: -x["cost_usd"])
    return {"level": level, "team": team, "user": user, "items": items}


# ═══════════════════════ 운영·설정 (SCR-L02·L04·L05·L06 · B02·B04·B05) ═══════════════════════
@admin_router.get("/api/v1/admin/settings/history")
def settings_history(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db), key: str | None = None,
                     limit: int = Query(50, le=500)):
    stmt = select(SettingHistory).order_by(SettingHistory.ts.desc()).limit(limit)
    if key:
        stmt = stmt.where(SettingHistory.key == key)
    return {"items": [{"id": h.id, "key": h.key, "value": h.value, "changed_by": h.changed_by, "ts": h.ts}
                      for h in db.scalars(stmt) if not h.key.startswith(("unmask.", "lease.", "notify.prefs."))]}


@admin_router.post("/api/v1/admin/settings/revert")
def settings_revert(history_id: str = Body(..., embed=True), p: Principal = Depends(require("admin:write")),
                    db: Session = Depends(get_db)):
    """되돌리기 (SCR-L06 ⑨) — 그 변경 직전 값으로."""
    h = db.get(SettingHistory, history_id)
    if h is None:
        raise not_found("setting history")
    if h.value is None:
        row = db.get(Setting, h.key)
        if row is not None:
            db.add(SettingHistory(id=new_id("sh"), key=h.key, value=row.value, changed_by=p.sub))
            db.delete(row)
    else:
        put_setting(db, h.key, h.value, p.sub)
    db.commit()
    guards.reset_custom_cache()
    policy.reset_default_data_class()  # 기본 데이터 등급 5초 캐시
    A.reset_rbac_cache()
    audit.append("admin.settings.revert", actor=p.sub, target=h.key, data={"history_id": h.id})
    return {"key": h.key, "value": h.value}


@admin_router.post("/api/v1/admin/settings/pii-test")
def pii_test(text: str = Body(..., embed=True), rules: list[dict] | None = Body(None, embed=True),
             _: Principal = Depends(require("admin:read"))):
    """PII 규칙 시험 (SCR-L06 ③) — 저장 전 규칙(rules)이나 현재 규칙으로 마스킹 결과."""
    extra = None
    if rules is not None:
        try:
            extra = [(f"custom:{r.get('name') or 'rule'}", re.compile(r["pattern"])) for r in rules
                     if r.get("pattern") and r.get("enabled", True)]
        except (re.error, KeyError) as exc:
            raise bad_request(f"정규식 오류: {exc}") from exc
    out, findings = guards.mask_pii(text, include_secrets=True, extra=extra)
    return {"text": out, "findings": [f.as_dict() for f in findings]}


@admin_router.patch("/api/v1/admin/connectors/{cid}")
def patch_connector(cid: str, body: dict, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """커넥터 편집 (SCR-L02 ③)."""
    c = db.get(Connector, cid)
    if c is None:
        raise not_found("connector")
    if "name" in body:
        c.name = str(body["name"])[:120]
    if "config" in body:
        if not isinstance(body["config"], dict):
            raise bad_request("config 는 객체")
        c.config = {**(c.config or {}), **body["config"]}
    if body.get("secret"):  # 자격 증명 교체 — 값은 시크릿 저장소에만 (MSG-018)
        c.secret_ref = c.secret_ref or f"connector/{c.id}"
        S.put(db, c.secret_ref, str(body["secret"]), kind="token")
    db.commit()
    audit.append("admin.connector.update", actor=p.sub, target=cid,
                 data={k: v for k, v in body.items() if k not in ("config", "secret")} | {"secret_rotated": bool(body.get("secret"))})
    return {"id": c.id, "name": c.name, "kind": c.kind, "config": c.config, "status": c.status}


def _sbom_components(db: Session) -> dict[str, str]:
    from app.services import registry
    return {c["name"]: c.get("version") or "" for c in (registry.sbom(db).get("components") or [])}


@admin_router.get("/api/v1/admin/updates/plan")
def update_plan(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    """업그레이드 계획 (SCR-L04) — 현재·대상 버전, 대기 마이그레이션, 기준 대비 SBOM 변경, 서명 검증 준비, 단계."""
    import os

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.core.config import APP_VERSION, settings
    from app.db import engine
    with engine.connect() as conn:
        cur = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    script = ScriptDirectory.from_config(Config(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")))
    head = script.get_current_head()
    pending = [r.revision for r in script.iterate_revisions(head, cur)] if cur != head else []
    now = _sbom_components(db)
    base = _setting(db, "sbom.baseline") or {}
    prev = base.get("components") or {}
    diff = [{"op": "+", "name": k, "to": v} for k, v in now.items() if k not in prev] + \
        [{"op": "-", "name": k, "from": v} for k, v in prev.items() if k not in now] + \
        [{"op": "~", "name": k, "from": prev[k], "to": v} for k, v in now.items() if k in prev and prev[k] != v]
    target = os.environ.get("ANX_TARGET_VERSION") or APP_VERSION
    cosign = bool(os.environ.get("COSIGN_PUBLIC_KEY") or getattr(settings, "cosign_public_key", ""))
    return {"current_version": APP_VERSION, "target_version": target, "migration": cur, "head": head,
            "pending_migrations": pending, "sbom_baseline_at": base.get("at"), "sbom_diff": diff,
            "signature": {"configured": cosign, "detail": "cosign 공개키로 이미지 서명 검증" if cosign
                          else "cosign 공개키 미설정 — nexus install compose 가 서명 게이트에서 중단합니다"},
            "steps": ["백업(관리 › 백업·복구)", "이미지 서명 검증(cosign)", "마이그레이션(make db-migrate)", "롤링 재시작",
                      "헬스·스모크 확인", "SBOM 기준 갱신"]}


@admin_router.post("/api/v1/admin/updates/sbom-baseline")
def sbom_baseline(p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    comps = _sbom_components(db)
    put_setting(db, "sbom.baseline", {"at": utcnow().isoformat(), "components": comps}, p.sub)
    db.commit()
    audit.append("admin.sbom.baseline", actor=p.sub, data={"components": len(comps)})
    return {"components": len(comps)}


class RestoreTestIn(BaseModel):
    bundle: dict
    signature: str


@admin_router.post("/api/v1/admin/restore/test")
def restore_test(body: RestoreTestIn, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """격리 복구 시험 (SCR-L05 ⑤) — 운영 DB 를 건드리지 않고 임시 DB 에 복원해 서명·행 수·무결성을 확인한다."""
    import jwt as _jwt
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as _S

    from app.api import admin as adm
    from app.core import keys
    from app.core.ids import canonical_json, sha256_hex
    from app.models import Base
    digest = sha256_hex(canonical_json(body.bundle))
    try:
        claims = keys.decode_long_lived(body.signature, algorithms=["RS256"], issuer="agentnexus-backup",  # 회전 전 키로 서명한 백업도 (D21-③)
                                        options={"require": ["iss", "sha256"]})  # 백업 서명만 (59th)
    except _jwt.PyJWTError as exc:
        return {"ok": False, "stage": "signature", "detail": f"서명 검증 실패: {exc}"}
    if claims.get("sha256") != digest:
        return {"ok": False, "stage": "signature", "detail": "내용이 서명과 일치하지 않습니다(변조)."}
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    models = {m.__tablename__: m for m in adm.BACKUP_TABLES + [Secret]}
    counts, errors = {}, []
    with _S(eng) as tmp:
        for tname, rows in body.bundle.get("tables", {}).items():
            model = models.get(tname)
            if model is None:
                errors.append(f"알 수 없는 테이블 {tname}")
                continue
            try:
                for r in rows:
                    tmp.merge(adm._from_row(model, r))
                tmp.flush()
                counts[tname] = tmp.scalar(select(func.count()).select_from(model))
            except Exception as exc:  # noqa: BLE001
                tmp.rollback()
                errors.append(f"{tname}: {type(exc).__name__}")
        tmp.rollback()
    eng.dispose()
    expected = {k: len(v) for k, v in body.bundle.get("tables", {}).items()}
    mism = [k for k, v in expected.items() if counts.get(k) != v]
    ok = not errors and not mism
    audit.append("admin.restore.test", actor=p.sub, data={"ok": ok, "tables": counts, "sha256": digest})
    return {"ok": ok, "stage": "complete", "sha256": digest, "tables": counts, "expected": expected,
            "mismatch": mism, "errors": errors, "created_at": body.bundle.get("created_at"), "version": body.bundle.get("version")}


NOTIFY_CATEGORIES = {"approval": ("approval.",), "registry": ("registry.",), "budget": ("budget.", "limit."),
                     "security": ("security.", "audit."), "system": ("alert", "system.", "a2a.", "mcp.")}


@admin_router.get("/api/v1/notifications/preferences")
def get_notify_prefs(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """알림 수신 설정 (SCR-B04 ④) — 분류별 끄기·채널(앱·메일·Slack)."""
    prefs = _setting(db, f"notify.prefs.{p.sub}") or {}
    return {"categories": list(NOTIFY_CATEGORIES), "muted": prefs.get("muted", []),
            "channels": {"inapp": True, "email": False, "slack": False, **(prefs.get("channels") or {})}}


class PrefsIn(BaseModel):
    muted: list[str] = []
    channels: dict[str, bool] = {}


@admin_router.put("/api/v1/notifications/preferences")
def put_notify_prefs(body: PrefsIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    bad = [c for c in body.muted if c not in NOTIFY_CATEGORIES]
    if bad:
        raise bad_request(f"알 수 없는 분류: {bad}")
    key = f"notify.prefs.{p.sub}"[:80]
    row = db.get(Setting, key)
    val = {"muted": body.muted, "channels": {k: bool(v) for k, v in body.channels.items() if k in ("inapp", "email", "slack")}}
    if row is None:
        db.add(Setting(key=key, value=val, updated_by=p.sub))
    else:
        row.value = val
    db.commit()
    return get_notify_prefs(p, db)


def category_of(kind: str) -> str:
    return next((c for c, pre in NOTIFY_CATEGORIES.items() if kind.startswith(pre)), "system")


_COMP_LOGGERS = {"api": ("api", "system"), "gateway": ("gateway", "mcp", "a2a", "llm"), "registry": ("registry", "catalog"),
                 "host": ("host", "orchestrator", "chat"), "observe": ("observe", "audit", "tracing"),
                 "identity": ("identity", "idp", "auth"), "admin": ("admin", "setup", "scheduler")}


@admin_router.get("/api/v1/system/components")
def components(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    """구성요소 표 (SCR-B05 ①) — 버전·워커(리스 보유자)·상태와 운영 명령(재시작은 배포 도구로)."""
    import os

    from app.core.config import APP_VERSION, settings
    from app.core import tracing
    leases = [s for s in db.scalars(select(Setting).where(Setting.key.like("lease.%")))]
    workers = sorted({(s.value or {}).get("owner") for s in leases if (s.value or {}).get("owner")})
    replicas = int(os.environ.get("UVICORN_WORKERS", os.environ.get("WEB_CONCURRENCY", "1")))
    since1, since24 = utcnow() - timedelta(hours=1), utcnow() - timedelta(hours=24)
    starts = db.scalar(select(func.count()).select_from(LogEntry).where(LogEntry.component == "system", LogEntry.ts >= since24,
                                                                        LogEntry.message.like("프로세스 시작%"))) or 0
    errs = {}
    for comp, n in db.execute(select(LogEntry.component, func.count()).where(LogEntry.ts >= since1, LogEntry.level.in_(("ERROR", "CRITICAL")))
                              .group_by(LogEntry.component)):
        errs[comp] = n
    unhealthy = [s.id for s in db.scalars(select(McpServer).where(McpServer.status == "active", McpServer.health == "unhealthy"))]
    rows = []
    for name in ("api", "gateway", "registry", "host", "observe", "identity", "admin"):
        on = settings.enabled(name) if name != "api" else True
        comps = _COMP_LOGGERS[name]
        last = db.scalars(select(LogEntry).where(LogEntry.component.in_(comps), LogEntry.level.in_(("ERROR", "CRITICAL")))
                          .order_by(LogEntry.ts.desc())).first()
        n_err = sum(errs.get(c, 0) for c in comps)
        detail = []
        if name == "gateway" and unhealthy:
            detail.append(f"MCP 서버 헬스 실패 {len(unhealthy)}: {', '.join(unhealthy[:3])}")
        if name == "observe" and tracing.STATS.get("db_failed"):
            detail.append(f"스팬 저장 실패 {tracing.STATS['db_failed']}건")
        health = "disabled" if not on else "degraded" if (n_err or detail) else "healthy"
        rows.append({"component": name, "enabled": on, "version": APP_VERSION, "replicas": replicas if on else 0,
                     "health": health, "errors_1h": n_err, "detail": detail,
                     "restarts_24h": max(0, round(starts / max(replicas, 1)) - 1) if on else 0,  # 워커마다 시작 1건 — 첫 기동 제외
                     "last_error": {"ts": last.ts, "message": last.message[:300], "trace_id": last.trace_id} if last else None,
                     "restart": {"compose": f"docker compose restart {'api' if name != 'gateway' else 'api'}",
                                 "helm": f"kubectl rollout restart deploy/agentnexus-{name if name in ('gateway', 'registry') else 'api'}"}})
    return {"items": rows, "workers": workers, "profile": _setting(db, "deploy.profile", {"profile": "full"})}


# ═══════════════════════ 예약 백업 (SCR-L05 ②③) ═══════════════════════
def _backup_dir():
    from pathlib import Path

    from app.core.config import settings
    p = Path(settings.data_dir) / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_scheduled_backup(force: bool = False) -> str | None:
    """backup.schedule {interval: off|daily|weekly, keep} 에 따라 서명 번들을 파일로 남기고 오래된 것은 지운다."""
    from app.db import SessionLocal
    with SessionLocal() as db:
        cfg = _setting(db, "backup.schedule", {}) or {}
        interval = cfg.get("interval", "off")
        if interval == "off" and not force:
            return None
        files = sorted(_backup_dir().glob("backup-*.json"))
        if files and not force:
            age = (utcnow().timestamp() - files[-1].stat().st_mtime) / 3600
            if age < (24 if interval == "daily" else 24 * 7):
                return None
        name = write_backup(db, "system:schedule")
    keep = int(cfg.get("keep") or 7)
    for old in sorted(_backup_dir().glob("backup-*.json"))[:-keep]:
        old.unlink(missing_ok=True)
    return name


def write_backup(db: Session, actor: str, suffix: str = "", include_secrets: bool = False) -> str:
    """서명 번들을 데이터 볼륨 backups/ 에 쓴다 — 예약 백업·복원 직전 스냅샷 (SCR-L05)."""
    from app.api.admin import make_backup
    out = make_backup(db, actor, include_secrets)
    db.commit()
    path = _backup_dir() / f"backup-{utcnow():%Y%m%dT%H%M%S}{suffix}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return path.name


@admin_router.get("/api/v1/admin/backups")
def list_backups(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    files = sorted(_backup_dir().glob("backup-*.json"), reverse=True)
    t = db.scalars(select(AuditEvent).where(AuditEvent.action == "admin.restore.test").order_by(AuditEvent.seq.desc())).first()
    return {"schedule": _setting(db, "backup.schedule", {"interval": "off", "keep": 7}),
            "items": [{"name": f.name, "size": f.stat().st_size, "created_at": f.stat().st_mtime,
                       "pre_restore": f.name.endswith("-prerestore.json")} for f in files],
            "last_restore_test": {"ok": (t.data or {}).get("ok"), "at": t.ts, "by": t.actor} if t else None}


@admin_router.post("/api/v1/admin/backups/run")
def run_backup_now(p: Principal = Depends(require("admin:write"))):
    name = run_scheduled_backup(force=True)
    audit.append("admin.backup.scheduled", actor=p.sub, data={"file": name})
    return {"name": name}


@admin_router.get("/api/v1/admin/backups/{name}")
def get_backup(name: str, _: Principal = Depends(require("admin:write"))):
    if not re.fullmatch(r"backup-\d{8}T\d{6}(-prerestore)?\.json", name):
        raise bad_request("백업 이름 형식 오류")
    f = _backup_dir() / name
    if not f.exists():
        raise not_found("backup")
    return json.loads(f.read_text(encoding="utf-8"))
