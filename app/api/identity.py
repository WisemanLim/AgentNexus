"""Identity & Authorization 관리 API (SCR-B06·I05·J01~J07·L03)."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core import security
from app.core.auth import ROLE_LABELS, ROLE_PERMS, Principal, _JWKS, current_principal, require
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.http import sync_client
from app.core.ids import new_id
from app.db import get_db, utcnow
from app.models import (AccessToken, BrokerRequest, IdpConnection, PersonalToken, RefreshToken, TokenExchange, TokenExchangePolicy, User,
                        Workload)
from app.services import audit, broker, identity, secrets

router = APIRouter(tags=["identity"])


# ───────── 내 계정 (SCR-B06) ─────────
@router.get("/api/v1/me")
def me(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    perms = sorted(p.permissions())
    u = db.get(User, p.user_id) if p.user_id else None
    own = {"issuer": u.issuer, "idp": (u.claims or {}).get("idp"), "mfa_enabled": u.mfa_enabled, "last_login_at": u.last_login_at,
           "claims": {k: v for k, v in (u.claims or {}).items() if k != "keys"}} if u else {}  # 내 IdP·그룹 클레임 (SCR-B06 ⑥)
    return {**p.snapshot(), "scopes": p.scopes, "actor": p.actor, "permissions": perms,
            "role_labels": {r: ROLE_LABELS.get(r, r) for r in p.roles}, **own}


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field("pat", pattern="^(pat|client_key)$")
    scopes: list[str] = []
    ttl_days: int = Field(90, ge=1, le=90)  # SCR-B06 — 최대 90일 (무기한 없음, D19-④)
    team: str | None = None
    allowed_models: list[str] | None = Field(None, max_length=50)  # 클라이언트 키 허용 모델 별칭 (SCR-H13 ④)


def _token_out(t: PersonalToken) -> dict:
    return {"id": t.id, "kind": t.kind, "name": t.name, "prefix": t.prefix, "scopes": t.scopes, "team": t.team,
            "allowed_models": t.allowed_models,
            "expires_at": t.expires_at, "revoked_at": t.revoked_at, "last_used_at": t.last_used_at,
            "created_at": t.created_at}


@router.get("/api/v1/me/tokens")
def my_tokens(p: Principal = Depends(current_principal), db: Session = Depends(get_db), kind: str | None = None):
    stmt = select(PersonalToken).where(PersonalToken.user_id == p.user_id).order_by(PersonalToken.created_at.desc())
    if kind:
        stmt = stmt.where(PersonalToken.kind == kind)
    return {"items": [_token_out(t) for t in db.scalars(stmt)]}


@router.post("/api/v1/me/tokens", status_code=201)
def create_my_token(body: TokenIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if p.user_id is None:
        raise bad_request("사용자 토큰으로만 발급할 수 있습니다.")
    if p.kind != "user":  # 토큰으로 토큰을 만들면 스코프·만료 제한을 벗어난다 — 로그인 세션(콘솔·CLI)에서만 (D19-④)
        raise forbidden("토큰으로는 새 토큰을 발급할 수 없습니다 — 콘솔·CLI 로그인 세션에서 발급하세요.", code="token_from_token")
    if body.kind == "client_key" and not p.has("gateway:read"):
        raise bad_request("클라이언트 키는 게이트웨이 권한이 필요합니다.")
    if body.allowed_models and body.kind != "client_key":
        raise bad_request("allowed_models 는 클라이언트 키에만 지정합니다.")
    if body.allowed_models:
        from app.models import Model, RoutingRule
        known = {m.id for m in db.scalars(select(Model))} | {r.match_model for r in db.scalars(select(RoutingRule))} | {"auto"}
        bad = [m for m in body.allowed_models if m not in known]
        if bad:
            raise bad_request(f"알 수 없는 모델 별칭: {', '.join(bad)} (MSG-004)", code="unknown_model")
    user = identity.get_user(db, p.user_id)
    team = body.team or user.team
    if team != user.team and not p.has("admin:write"):  # 팀은 KB·정책·한도의 경계 — 다른 팀 키는 운영자만 (D19-④)
        raise forbidden("다른 팀으로 발급하려면 운영 권한(admin:write)이 필요합니다.", code="team_forbidden")
    row, raw = identity.create_personal_token(db, user, body.name, body.kind, body.scopes, body.ttl_days, team)
    row.allowed_models = sorted(set(body.allowed_models)) if body.allowed_models else None
    db.commit()
    audit.append("token.personal.create", actor=p.sub, target=row.id, data={"kind": row.kind, "name": row.name})
    return {**_token_out(row), "token": raw}


@router.delete("/api/v1/me/tokens/{token_id}")
def revoke_my_token(token_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    row = db.get(PersonalToken, token_id)
    if row is None or row.user_id != p.user_id:
        raise not_found("token")
    row.revoked_at = utcnow()
    db.commit()
    audit.append("token.personal.revoke", actor=p.sub, target=row.id)
    return _token_out(row)


# ───────── 역할·권한 (SCR-L03) ─────────
@router.get("/api/v1/identity/roles")
def roles(_: Principal = Depends(require("identity:read"))):
    all_perms = sorted({x for v in ROLE_PERMS.values() for x in v if x != "*"})
    return {"roles": [{"id": r, "label": ROLE_LABELS.get(r, r),
                       "permissions": all_perms if "*" in perms else sorted(perms)} for r, perms in ROLE_PERMS.items()],
            "permissions": all_perms}


# ───────── 사용자·그룹·역할 (SCR-J03) ─────────
def _user_out(u: User) -> dict:
    return {"id": u.id, "sub": u.sub, "username": u.username, "email": u.email, "name": u.name, "roles": u.roles,
            "groups": u.groups, "team": u.team, "status": u.status, "issuer": u.issuer,
            "last_login_at": u.last_login_at, "created_at": u.created_at}


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=120, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=8)
    name: str | None = None
    email: str | None = None
    roles: list[str] = ["user"]
    team: str | None = None


class UserPatch(BaseModel):
    roles: list[str] | None = None
    team: str | None = None
    status: str | None = Field(None, pattern="^(active|disabled)$")
    name: str | None = None


def _is_admin(p: Principal) -> bool:
    """플랫폼 관리자 — admin 역할이면서 이 토큰 스코프에 admin:write 가 있을 때 (좁힌 admin PAT 로 admin 을 만들지 못하게, 59th)."""
    return "admin" in (p.roles or []) and p.has("admin:write")


def _check_roles(roles: list[str], p: Principal | None = None) -> None:
    bad = [r for r in roles if r not in ROLE_PERMS]
    if bad:
        raise bad_request(f"알 수 없는 역할: {', '.join(bad)}")
    if p is not None and "admin" in roles and not _is_admin(p):  # admin 부여는 admin 만 (D20-②)
        raise forbidden("admin 역할은 플랫폼 관리자만 부여할 수 있습니다.", code="admin_grant_forbidden")


@router.get("/api/v1/identity/users")
def list_users(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db), q: str | None = None,
               role: str | None = None, limit: int = Query(200, le=1000)):
    stmt = select(User).order_by(User.created_at).limit(limit)
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # 와일드카드는 글자 그대로 (65th)
        like = f"%{esc}%"
        stmt = stmt.where(or_(User.username.like(like, escape="\\"), User.email.like(like, escape="\\"),
                              User.name.like(like, escape="\\")))
    users = [u for u in db.scalars(stmt) if not role or role in (u.roles or [])]
    return {"items": [_user_out(u) for u in users]}


@router.post("/api/v1/identity/users", status_code=201)
def create_user(body: UserIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    _check_roles(body.roles, p)
    if db.scalars(select(User).where(User.username == body.username)).first():
        raise conflict("이미 존재하는 사용자 이름입니다.")
    uid = new_id("usr")
    user = User(id=uid, sub=uid, issuer=security.issuer(), username=body.username, name=body.name, email=body.email,
                password_hash=security.hash_password(body.password), roles=body.roles, team=body.team)
    db.add(user)
    db.commit()
    audit.append("identity.user.create", actor=p.sub, target=user.sub, data={"roles": body.roles})
    return _user_out(user)


@router.patch("/api/v1/identity/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, p: Principal = Depends(require("identity:write")),
               db: Session = Depends(get_db)):
    user = identity.get_user(db, user_id)
    changes = body.model_dump(exclude_none=True)
    if "admin" in (user.roles or []) and not _is_admin(p):  # 관리자 계정 변경도 관리자만 (D20-②)
        raise forbidden("플랫폼 관리자 계정은 관리자만 바꿀 수 있습니다.", code="admin_grant_forbidden")
    if "roles" in changes:
        _check_roles(changes["roles"], p)
    for k, v in changes.items():
        setattr(user, k, v)
    db.commit()
    audit.append("identity.user.update", actor=p.sub, target=user.sub, data=changes)
    return _user_out(user)


# ───────── IdP 연동 (SCR-J01·J02) ─────────
class IdpIn(BaseModel):
    name: str
    kind: str = Field("oidc", pattern="^(oidc|entra|okta|keycloak)$")
    issuer: str
    client_id: str | None = None
    client_secret: str | None = None
    audience: str | None = None
    jwks_uri: str | None = None
    groups_claim: str = "groups"
    roles_claim: str = "roles"
    role_mapping: dict[str, str] = {}
    enabled: bool = True
    login_enabled: bool = False  # 시험 로그인 성공 후 켠다 (SCR-J02 ④)
    internal_url: str | None = None
    scopes: str = "openid profile email"


def _idp_out(i: IdpConnection) -> dict:
    return {"id": i.id, "name": i.name, "kind": i.kind, "issuer": i.issuer, "client_id": i.client_id,
            "client_secret": "••••" if i.client_secret_ref else None, "audience": i.audience, "jwks_uri": i.jwks_uri,
            "groups_claim": i.groups_claim, "roles_claim": i.roles_claim, "role_mapping": i.role_mapping,
            "enabled": i.enabled, "login_enabled": i.login_enabled, "internal_url": i.internal_url, "scopes": i.scopes,
            "redirect_uri": broker.callback_url(), "last_test": i.last_test or {}, "status": i.status,
            "last_tested_at": i.last_tested_at, "created_at": i.created_at}


@router.get("/api/v1/identity/idps")
def list_idps(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    builtin = {"id": "builtin", "name": "AgentNexus 내장 IdP", "kind": "builtin", "issuer": security.issuer(),
               "enabled": True, "status": "ok"}
    return {"items": [builtin] + [_idp_out(i) for i in db.scalars(select(IdpConnection).order_by(IdpConnection.name))]}


@router.post("/api/v1/identity/idps", status_code=201)
def create_idp(body: IdpIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    for v in body.role_mapping.values():
        _check_roles([v], p)
    if db.scalars(select(IdpConnection).where(IdpConnection.issuer == body.issuer)).first():
        raise conflict("이미 연동된 issuer 입니다.")
    if body.issuer.rstrip("/") == security.issuer().rstrip("/"):  # 내장 IdP 와 같은 issuer — 내장 토큰과 섞인다 (D22-⑥)
        raise bad_request("내장 IdP 의 issuer 는 외부 IdP 로 연동할 수 없습니다.", code="issuer_reserved")
    idp = IdpConnection(id=new_id("idp"), **body.model_dump(exclude={"client_secret"}))
    if body.client_secret:
        idp.client_secret_ref = f"idp/{idp.id}/client_secret"
        secrets.put(db, idp.client_secret_ref, body.client_secret, kind="oauth_client")
    db.add(idp)
    db.commit()
    audit.append("identity.idp.create", actor=p.sub, target=idp.issuer, data={"kind": idp.kind})
    return _idp_out(idp)


@router.patch("/api/v1/identity/idps/{idp_id}")
def patch_idp(idp_id: str, body: dict, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    allowed = {"name", "client_id", "audience", "jwks_uri", "groups_claim", "roles_claim", "role_mapping", "enabled",
               "login_enabled", "internal_url", "scopes"}
    if body.get("login_enabled") and not idp.login_enabled and (idp.last_test or {}).get("status") != "ok":
        raise bad_request("콘솔 로그인에 노출하려면 먼저 시험 로그인에 성공해야 합니다 (SCR-J02 ④).", code="test_login_required")
    if isinstance(body.get("role_mapping"), dict):
        _check_roles(list(body["role_mapping"].values()), p)
    # admin 으로 매핑된 IdP 는 그 IdP 의 서명이 곧 admin 부여 — 신뢰 설정(jwks_uri·internal_url·audience·클레임 등)은 admin 만.
    # 이름 변경·연동/로그인 중지는 누구나(identity:write) — 끄는 쪽은 권한을 넓히지 않는다 (59th)
    if "admin" in (idp.role_mapping or {}).values() and not _is_admin(p) and any(
            k != "name" and not (k in ("enabled", "login_enabled") and v is False) for k, v in body.items()):
        raise forbidden("admin 역할로 매핑된 IdP 의 신뢰 설정은 플랫폼 관리자만 바꿀 수 있습니다.", code="admin_grant_forbidden")
    disabling = idp.enabled and body.get("enabled") is False
    for k, v in body.items():
        if k in allowed:
            setattr(idp, k, v)
    ended = _end_idp_sessions(db, idp)[1] if disabling else 0  # 연동 중지 = 그 IdP 사용자 세션 종료 (SCR-J01)
    if body.get("client_secret"):
        idp.client_secret_ref = idp.client_secret_ref or f"idp/{idp.id}/client_secret"
        secrets.put(db, idp.client_secret_ref, body["client_secret"], kind="oauth_client")
    _JWKS.pop(idp.issuer, None)
    db.commit()
    audit.append("identity.idp.update", actor=p.sub, target=idp.issuer, data={k: v for k, v in body.items()
                                                                              if k != "client_secret"} | ({"sessions_ended": ended} if disabling else {}))
    return _idp_out(idp) | ({"sessions_ended": ended} if disabling else {})


def _end_idp_sessions(db: Session, idp: IdpConnection) -> tuple[int, int]:
    """그 IdP 로 들어온 사용자(sub=ext:{id}:*)의 세션 종료 — tokens_not_before(액세스 토큰도 다음 요청부터 401) + refresh 폐기.
    연동 중지·삭제가 같은 기준을 쓴다(issuer 기준이면 내장 issuer 와 같은 IdP 삭제가 로컬 사용자 전원을 끊는다).
    하위 질의 UPDATE — 사용자가 많아도 IN 목록 상한에 걸리지 않는다. 반환 (사용자 수, 폐기한 refresh 수) (D22-⑥)."""
    now = utcnow()
    esc = idp.id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # idp_… 의 _ 는 LIKE 와일드카드
    mine = User.sub.like(f"ext:{esc}:%", escape="\\")
    users = db.execute(update(User).where(mine).values(tokens_not_before=now)
                       .execution_options(synchronize_session=False)).rowcount
    revoked = db.execute(update(RefreshToken).where(RefreshToken.user_id.in_(select(User.id).where(mine)),
                                                    RefreshToken.revoked_at.is_(None)).values(revoked_at=now)
                         .execution_options(synchronize_session=False)).rowcount
    # PAT·클라이언트 키는 tokens_not_before 를 보지 않는다 — 그 IdP 를 끊었으면 같이 폐기 (65th)
    db.execute(update(PersonalToken).where(PersonalToken.user_id.in_(select(User.id).where(mine)),
                                           PersonalToken.revoked_at.is_(None)).values(revoked_at=now)
               .execution_options(synchronize_session=False))
    return users, revoked


@router.delete("/api/v1/identity/idps/{idp_id}")
def delete_idp(idp_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    # 그 IdP 로 들어온 사용자의 세션 종료 — 발급한 토큰 무효화·갱신 토큰 폐기 (D20-②, 기준은 연동 중지와 같게 D22-⑥)
    users, ended = _end_idp_sessions(db, idp)
    db.delete(idp)
    db.commit()
    audit.append("identity.idp.delete", actor=p.sub, target=idp.issuer, data={"users_signed_out": users, "refresh_revoked": ended})
    return {"deleted": idp_id, "users_signed_out": users}


@router.post("/api/v1/identity/idps/{idp_id}/test")
def test_idp(idp_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    """OIDC 디스커버리 → JWKS 조회로 연결 시험 (SCR-J02 'Issuer·디스커버리')."""
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    result = _discover_check(idp.issuer, idp.internal_url)
    if result["discovery"]:
        idp.discovery = result["endpoints"]
        idp.jwks_uri = idp.jwks_uri or result["endpoints"].get("jwks_uri")
    idp.status = "ok" if result["discovery"] and result["jwks"] else "error"
    idp.last_tested_at = utcnow()
    db.commit()
    return result


def _discover_check(issuer: str, internal_url: str | None) -> dict:
    """디스커버리(issuer 일치) → JWKS 키 → PKCE S256 지원 여부 (SCR-J02 ②)."""
    out = {"issuer": issuer, "discovery": False, "jwks": False, "keys": 0, "pkce_s256": False, "endpoints": {}, "error": None}
    try:
        meta = broker.discover(issuer, internal_url)
        out["discovery"] = True
        out["endpoints"] = {k: meta.get(k) for k in ("authorization_endpoint", "token_endpoint", "jwks_uri",
                                                     "userinfo_endpoint", "code_challenge_methods_supported")}
        out["pkce_s256"] = "S256" in (meta.get("code_challenge_methods_supported") or [])
        tmp = IdpConnection(issuer=issuer, internal_url=internal_url)
        with sync_client(timeout=10) as http:
            ks = http.get(broker.srv(tmp, meta["jwks_uri"])).json()
        out["keys"] = len(ks.get("keys", []))
        out["jwks"] = out["keys"] > 0
    except Exception as exc:  # noqa: BLE001 — 네트워크·형식·issuer 불일치
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


class DiscoverIn(BaseModel):
    issuer: str
    internal_url: str | None = None


@router.post("/api/v1/identity/idps/discover")
def discover_idp(body: DiscoverIn, _: Principal = Depends(require("identity:write"))):
    """저장 전 디스커버리 시험 (SCR-J02 ②) — 연결을 만들지 않는다."""
    return {**_discover_check(body.issuer, body.internal_url), "redirect_uri": broker.callback_url()}


@router.post("/api/v1/identity/idps/{idp_id}/test-login")
def start_test_login(idp_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    """시험 로그인 (SCR-J02 ④) — 새 창에서 외부 IdP 로그인 → 클레임·역할 매핑 결과만 저장(로그인 세션은 만들지 않음)."""
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    try:
        url = broker.start(db, idp, {}, mode="test", actor=p.sub)
    except broker.BrokerError as exc:
        raise bad_request(exc.detail, code=exc.code) from exc
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(url).query)["state"][0]
    return {"url": url, "state": state}


@router.get("/api/v1/identity/idps/{idp_id}/test-login/{state}")
def test_login_result(idp_id: str, state: str, _: Principal = Depends(require("identity:write")),
                      db: Session = Depends(get_db)):
    req = db.get(BrokerRequest, state)
    if req is None or req.idp_id != idp_id or req.mode != "test":
        raise not_found("test login")
    return req.result or {"status": "pending"}


# ───────── 토큰 교환 정책·이력 (SCR-J04·J05) ─────────
class TxPolicyIn(BaseModel):
    audience: str = Field(pattern=r"^(mcp|a2a|model|api):[A-Za-z0-9._*-]+$")
    allowed_scopes: list[str]
    default_scopes: list[str] = []
    ttl_seconds: int = Field(300, ge=30, le=900)  # SCR-J04 — 교환 토큰은 최대 15분
    method: str = Field("rfc8693", pattern="^(rfc8693|obo)$")
    enabled: bool = True


def _txp_out(t: TokenExchangePolicy) -> dict:
    return {"id": t.id, "audience": t.audience, "audience_type": t.audience_type, "allowed_scopes": t.allowed_scopes,
            "default_scopes": t.default_scopes, "ttl_seconds": t.ttl_seconds, "method": t.method, "enabled": t.enabled,
            "updated_at": t.updated_at}


@router.get("/api/v1/identity/token-exchange/policies")
def list_tx_policies(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    return {"items": [_txp_out(t) for t in db.scalars(select(TokenExchangePolicy).order_by(TokenExchangePolicy.audience))]}


@router.put("/api/v1/identity/token-exchange/policies")
def upsert_tx_policy(body: TxPolicyIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    if not set(body.default_scopes) <= set(body.allowed_scopes):
        raise bad_request("default_scopes 는 allowed_scopes 의 부분집합이어야 합니다.")
    row = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == body.audience)).first()
    kind = {"mcp": "mcp_server", "a2a": "a2a_agent", "model": "model", "api": "api"}[body.audience.split(":")[0]]
    if row is None:
        row = TokenExchangePolicy(id=new_id("txp"), audience_type=kind, **body.model_dump())
        db.add(row)
    else:
        for k, v in body.model_dump().items():
            setattr(row, k, v)
    db.commit()
    audit.append("identity.token_exchange_policy.upsert", actor=p.sub, target=row.audience, data=body.model_dump())
    return _txp_out(row)


@router.get("/api/v1/identity/token-exchanges")
def list_exchanges(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db),
                   trace_id: str | None = None, subject: str | None = None, audience: str | None = None,
                   limit: int = Query(100, le=1000)):
    stmt = select(TokenExchange).order_by(TokenExchange.ts.desc()).limit(limit)
    if trace_id:
        stmt = stmt.where(TokenExchange.trace_id == trace_id)
    if subject:
        stmt = stmt.where(TokenExchange.subject_sub == subject)
    if audience:
        stmt = stmt.where(TokenExchange.audience == audience)
    return {"items": [{"id": t.id, "ts": t.ts, "subject": t.subject_sub, "subject_kind": t.subject_kind,
                       "actor": t.actor, "audience": t.audience, "scopes": t.scopes, "ttl_seconds": t.ttl_seconds,
                       "method": t.method, "decision": t.decision, "reason": t.reason, "trace_id": t.trace_id}
                      for t in db.scalars(stmt)]}


# ───────── 워크로드 아이덴티티 (SCR-J07) ─────────
class WorkloadIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    kind: str = Field("agent", pattern="^(agent|mcp_server|service)$")
    scopes: list[str] = []


def _wl_out(w: Workload) -> dict:
    return {"id": w.id, "name": w.name, "kind": w.kind, "client_id": w.client_id, "spiffe_id": w.spiffe_id,
            "scopes": w.scopes, "enabled": w.enabled, "created_at": w.created_at, "rotated_at": w.rotated_at}


@router.get("/api/v1/identity/workloads")
def list_workloads(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    return {"items": [_wl_out(w) for w in db.scalars(select(Workload).order_by(Workload.name))]}


@router.post("/api/v1/identity/workloads", status_code=201)
def create_workload(body: WorkloadIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    if db.scalars(select(Workload).where(Workload.name == body.name)).first():
        raise conflict("이미 존재하는 워크로드입니다.")
    wl, secret = identity.create_workload(db, body.name, body.kind, body.scopes)
    db.commit()
    audit.append("identity.workload.create", actor=p.sub, target=wl.client_id, data={"kind": wl.kind})
    return {**_wl_out(wl), "client_secret": secret}


@router.post("/api/v1/identity/workloads/{wl_id}/rotate")
def rotate_workload(wl_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    wl = db.get(Workload, wl_id)
    if wl is None:
        raise not_found("workload")
    secret = identity.rotate_workload(db, wl)
    db.commit()
    audit.append("identity.workload.rotate", actor=p.sub, target=wl.client_id)
    return {**_wl_out(wl), "client_secret": secret}


@router.patch("/api/v1/identity/workloads/{wl_id}")
def patch_workload(wl_id: str, body: dict, p: Principal = Depends(require("identity:write")),
                   db: Session = Depends(get_db)):
    wl = db.get(Workload, wl_id)
    if wl is None:
        raise not_found("workload")
    for k in ("enabled", "scopes"):
        if k in body:
            setattr(wl, k, body[k])
    db.commit()
    audit.append("identity.workload.update", actor=p.sub, target=wl.client_id, data=body)
    return _wl_out(wl)


# ───────── Access Tokens 발급·폐기 (FR-L03, SCR-I05) ─────────
class AccessTokenIn(BaseModel):
    name: str
    subject: str = Field(description="토큰 사용 주체 (예: a2a:observability-analyst, mcp:internal-api)")
    audience: str = security.API_AUDIENCE
    scopes: list[str] = ["gateway:invoke"]
    ttl_seconds: int = Field(3600, ge=300, le=3600)  # SCR-I05 — 5~60분


def _at_out(t: AccessToken) -> dict:
    now = utcnow()
    state = "revoked" if t.revoked_at else ("expired" if t.expires_at < now else "active")
    return {"id": t.id, "name": t.name, "subject": t.subject, "audience": t.audience, "scopes": t.scopes,
            "issued_by": t.issued_by, "expires_at": t.expires_at, "revoked_at": t.revoked_at,
            "last_used_at": t.last_used_at, "created_at": t.created_at, "state": state}


def _audience_ok(db: Session, aud: str) -> bool:
    """Access Token audience 허용 목록 (D20-②) — 게이트웨이 API 또는 등록된 MCP 서버·A2A 에이전트."""
    from app.models import A2AAgent, McpServer
    if aud == security.API_AUDIENCE:
        return True
    kind, _, ident = aud.partition(":")
    return bool(ident) and ((kind == "mcp" and db.get(McpServer, ident) is not None)
                            or (kind == "a2a" and db.get(A2AAgent, ident) is not None))


@router.get("/api/v1/access-tokens")
def list_access_tokens(_: Principal = Depends(require("tokens:issue")), db: Session = Depends(get_db),
                       subject: str | None = None):
    stmt = select(AccessToken).order_by(AccessToken.created_at.desc())
    if subject:
        stmt = stmt.where(AccessToken.subject == subject)
    return {"items": [_at_out(t) for t in db.scalars(stmt)]}


@router.post("/api/v1/access-tokens", status_code=201)
def issue_access_token(body: AccessTokenIn, p: Principal = Depends(require("tokens:issue")),
                       db: Session = Depends(get_db)):
    if body.subject != p.sub and db.scalars(select(User.id).where(User.sub == body.subject)).first():
        # 서비스 주체용 — 다른 사람의 sub 로 발급하면 그 사람의 세션·Task 를 열람하는 대리 토큰이 된다
        raise bad_request("다른 사용자의 sub 로는 Access Token 을 발급할 수 없습니다 — 서비스 주체(예: a2a:…, mcp:…)를 쓰세요.",
                          code="subject_is_user")
    if not _audience_ok(db, body.audience):
        raise bad_request("audience 는 게이트웨이 또는 등록된 대상(mcp:<서버 id>·a2a:<에이전트 id>)이어야 합니다.",
                          code="unknown_audience")
    if body.audience != security.API_AUDIENCE:  # 대상용 토큰 스코프는 그 대상 교환 정책의 허용 스코프 안 — 교환과 같은 규칙 (D22-④)
        from app.models import TokenExchangePolicy
        pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == body.audience,
                                                           TokenExchangePolicy.enabled.is_(True))).first()
        if pol is None:
            raise bad_request(f"{body.audience} 의 토큰 교환 정책이 없습니다 — 정책에서 허용 스코프를 먼저 정하세요.",
                              code="no_exchange_policy")
        if not body.scopes:  # 빈 스코프를 '제한 없음' 으로 읽는 대상도 있다 — 교환처럼 정책 기본 스코프로 (65th)
            body.scopes = list(pol.default_scopes or [])
        extra = [s for s in body.scopes if s not in (pol.allowed_scopes or [])]
        if extra:
            raise bad_request(f"{body.audience} 교환 정책이 허용하지 않는 스코프: {' '.join(extra)}", code="scope_not_allowed",
                              allowed=pol.allowed_scopes or [])
    row, token = identity.issue_access_token(db, body.name, body.subject, body.audience, body.scopes,
                                             body.ttl_seconds, issued_by=p.sub)
    db.commit()
    audit.append("token.access.issue", actor=p.sub, target=row.subject,
                 data={"jti": row.id, "aud": row.audience, "scopes": row.scopes, "ttl": body.ttl_seconds})
    return {**_at_out(row), "token": token}


@router.post("/api/v1/access-tokens/{token_id}/revoke")
def revoke_access_token(token_id: str, p: Principal = Depends(require("tokens:issue")), db: Session = Depends(get_db)):
    row = db.get(AccessToken, token_id)
    if row is None:
        raise not_found("access token")
    row.revoked_at = row.revoked_at or utcnow()
    db.commit()
    audit.append("token.access.revoke", actor=p.sub, target=row.subject, data={"jti": row.id})
    return _at_out(row)
