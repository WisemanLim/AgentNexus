"""인증·인가 — Principal, 역할→권한(RBAC), Bearer 토큰 해석 (FR-I01·I02·I04, SCR-L03).

허용 토큰: 내장 issuer JWT(user · workload · access_token · delegation), 외부 IdP JWT(OIDC 연합, JIT 프로비저닝),
PAT(`anx_pat_`), 게이트웨이 클라이언트 키(`anx_key_`). 대상(aud) 이 'agentnexus' 가 아닌 교환 토큰은 거부한다.
"""
import time
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.errors import forbidden, unauthorized
from app.core.http import sync_client
from app.db import get_db, utcnow
from app.models import AccessToken, IdpConnection, PersonalToken, User, Workload

_USER = {"chat:use", "catalog:read", "agents:read", "models:read", "data:declare"}
_DEV = _USER | {"agents:write", "mcp:read", "mcp:write", "a2a:read", "a2a:write", "registry:publish",
                "observe:read", "gateway:read"}
ROLE_PERMS: dict[str, set[str]] = {
    "user": _USER,
    "developer": _DEV,
    "operator": _DEV | {"catalog:write", "catalog:approve", "gateway:write", "models:write", "admin:read",
                        "admin:write", "approvals:decide", "cost:read", "tokens:issue", "identity:read",
                        "limits:write"},
    "security": {"chat:use", "data:declare", "catalog:read", "catalog:approve", "gateway:read", "policy:write", "approvals:decide",
                 "identity:read", "identity:write", "audit:read", "observe:read", "tokens:issue", "mcp:read",
                 "a2a:read", "agents:read", "models:read"},
    "finops": {"chat:use", "data:declare", "catalog:read", "cost:read", "cost:write", "limits:write", "observe:read", "gateway:read",
               "models:read"},
    "admin": {"*"},
}
# 토큰 스코프 → 권한 (FR-L03·I04). 스코프가 권한 이름 그대로면 그 권한만, 묶음 스코프는 아래로 펼친다.
SCOPE_GRANTS: dict[str, set[str]] = {
    "gateway:invoke": {"chat:use", "models:read", "catalog:read", "data:declare"},  # Unified Model API·MCP·A2A 게이트웨이 호출
}
SCOPED_KINDS = ("pat", "workload", "access_token")
ROLE_LABELS = {"admin": "플랫폼 관리자", "operator": "플랫폼 운영자", "developer": "에이전트 개발자",
               "security": "보안·IAM 담당", "finops": "FinOps 담당", "user": "최종 사용자"}


@dataclass
class Principal:
    sub: str
    kind: str  # user | workload | access_token | client_key | delegation
    name: str | None = None
    email: str | None = None
    roles: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    team: str | None = None
    scopes: list[str] = field(default_factory=list)
    token_id: str | None = None
    issuer: str | None = None
    actor: str | None = None  # 위임 체인(act.sub)
    user_id: str | None = None
    raw_token: str | None = field(default=None, repr=False)
    session_id: str | None = None  # 로그인 세션(refresh token id, 토큰 claim sid)

    def permissions(self) -> set[str]:
        perms: set[str] = set()
        for r in self.roles:
            perms |= role_perms(r)
        return perms

    def scope_perms(self) -> set[str] | None:
        """스코프로 제한되는 토큰이면 허용 권한 집합, 아니면 None. 워크로드·Access Token 은 스코프가 없으면 권한 없음
        (기본 거부), PAT 는 스코프를 비우면 사용자 권한 그대로."""
        if self.kind not in SCOPED_KINDS or (self.kind == "pat" and not self.scopes):
            return None
        return set().union(*(SCOPE_GRANTS.get(s, {s}) for s in self.scopes or []))

    def has(self, perm: str) -> bool:
        sp = self.scope_perms()
        if sp is not None and perm not in sp:  # 역할이 admin(*) 이어도 토큰 스코프 밖은 거부
            return False
        perms = self.permissions()
        return "*" in perms or perm in perms

    def snapshot(self) -> dict:
        return {"sub": self.sub, "kind": self.kind, "name": self.name, "email": self.email, "roles": self.roles,
                "groups": self.groups, "team": self.team, "user_id": self.user_id}


# ── 역할 권한 재정의 (SCR-L03 권한 행렬) — Setting rbac.overrides {role: {add: [...], remove: [...]}} · 5초 캐시 ──
_RBAC: list = [0.0, {}]


def rbac_overrides() -> dict:
    if time.monotonic() - _RBAC[0] < 5:
        return _RBAC[1]
    try:
        from app.db import SessionLocal
        from app.models import Setting
        with SessionLocal() as db:
            row = db.get(Setting, "rbac.overrides")
            val = row.value if row and isinstance(row.value, dict) else {}
    except Exception:  # noqa: BLE001 — 읽지 못하면 기본 행렬
        val = _RBAC[1]
    _RBAC[0], _RBAC[1] = time.monotonic(), val
    return val


def reset_rbac_cache() -> None:
    _RBAC[0] = 0.0


def role_perms(role: str, overrides: dict | None = None) -> set[str]:
    base = set(ROLE_PERMS.get(role, set()))
    if "*" in base:  # 관리자는 재정의하지 않는다
        return base
    ov = (rbac_overrides() if overrides is None else overrides).get(role) or {}
    return (base | set(ov.get("add") or [])) - set(ov.get("remove") or [])


# ── 외부 IdP JWKS 캐시 ──
_JWKS: dict[str, tuple[float, dict]] = {}


def _external_keys(idp: IdpConnection, refresh: bool = False) -> dict:
    cached = _JWKS.get(idp.issuer)
    if cached and not refresh and time.time() - cached[0] < 600:
        return cached[1]
    jwks_uri = idp.jwks_uri

    def srv(url: str) -> str:  # 분리 DNS — 서버 측 호출은 internal_url 로 (broker.srv 와 같은 규칙)
        iss = idp.issuer.rstrip("/")
        return idp.internal_url.rstrip("/") + url[len(iss):] if idp.internal_url and url.startswith(iss) else url
    with sync_client(timeout=10) as http:
        if not jwks_uri:
            disc = http.get(srv(idp.issuer.rstrip("/") + "/.well-known/openid-configuration")).json()
            jwks_uri = disc["jwks_uri"]
        keyset = http.get(srv(jwks_uri)).json()
    _JWKS[idp.issuer] = (time.time(), keyset)
    return keyset


# 외부 IdP 서명은 비대칭만 — 토큰 헤더의 alg 가 HS*·none 으로 검증 방식을 고르지 못하게 (알고리즘 혼동 방지)
EXTERNAL_ALGS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA")


def _external_verify(idp: IdpConnection, token: str) -> dict:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "RS256")
    if alg not in EXTERNAL_ALGS:
        raise jwt.InvalidAlgorithmError(f"허용하지 않는 서명 알고리즘: {alg}")
    for attempt in (False, True):
        for k in _external_keys(idp, refresh=attempt).get("keys", []):
            if header.get("kid") in (None, k.get("kid")) and k.get("alg") in (None, alg):
                key = jwt.PyJWK(k).key
                return jwt.decode(token, key, algorithms=[alg],
                                  audience=idp.audience or idp.client_id, issuer=idp.issuer, leeway=30)
    raise jwt.InvalidTokenError("signing key not found")


def _map_roles(idp: IdpConnection, claims: dict) -> list[str]:
    values = claims.get(idp.groups_claim, []) or []
    values = values if isinstance(values, list) else [values]
    roles_v = claims.get(idp.roles_claim, []) or []
    values += roles_v if isinstance(roles_v, list) else [roles_v]
    roles = sorted({idp.role_mapping[v] for v in values if v in (idp.role_mapping or {})})
    return roles or ["user"]


def _upsert_external_user(db: Session, idp: IdpConnection, claims: dict) -> User:
    from app.core.ids import new_id
    sub = f"ext:{idp.id}:{claims['sub']}"
    user = db.scalars(select(User).where(User.sub == sub)).first()
    groups = claims.get(idp.groups_claim, []) or []
    if user is None:
        user = User(id=new_id("usr"), sub=sub, issuer=idp.issuer, roles=_map_roles(idp, claims))
        db.add(user)
    else:
        user.roles = _map_roles(idp, claims)
    user.email = claims.get("email")
    user.name = claims.get("name") or claims.get("preferred_username")
    user.groups = groups if isinstance(groups, list) else [groups]
    user.last_login_at = utcnow()
    roles_v = claims.get(idp.roles_claim, []) or []
    user.claims = {"idp": idp.id, "issuer": idp.issuer, idp.groups_claim: user.groups,  # 재동기화·클레임 보기 (SCR-J03)
                   idp.roles_claim: roles_v if isinstance(roles_v, list) else [roles_v], "keys": sorted(claims),
                   "at": utcnow().isoformat()}
    db.commit()
    return user


def _user_principal(user: User, token: str, kind: str = "user", actor: str | None = None,
                    token_id: str | None = None, session_id: str | None = None) -> Principal:
    return Principal(sub=user.sub, kind=kind, name=user.name or user.username, email=user.email,
                     roles=list(user.roles or ["user"]), groups=list(user.groups or []), team=user.team,
                     issuer=user.issuer, user_id=user.id, raw_token=token, actor=actor, token_id=token_id,
                     session_id=session_id)


def authenticate_token(db: Session, token: str) -> Principal:
    if token.startswith(("anx_pat_", "anx_key_")):
        row = db.scalars(select(PersonalToken).where(PersonalToken.token_hash == security.token_hash(token))).first()
        if row is None or row.revoked_at or (row.expires_at and row.expires_at < utcnow()):
            raise unauthorized("토큰이 유효하지 않거나 폐기되었습니다.")
        user = db.get(User, row.user_id)
        if user is None or user.status != "active":
            raise unauthorized("사용자가 비활성 상태입니다.")
        row.last_used_at = utcnow()
        db.commit()
        p = _user_principal(user, token, kind="pat" if row.kind == "pat" else "client_key", token_id=row.id)
        p.scopes = list(row.scopes or [])
        if row.kind == "client_key":
            p.roles = ["user"]
            p.team = row.team or user.team
        return p

    try:
        claims = security.unverified_claims(token)
    except jwt.PyJWTError as exc:
        raise unauthorized("토큰 형식이 올바르지 않습니다.") from exc

    if claims.get("iss") == security.issuer():
        try:
            claims = security.verify_local(token)
        except jwt.PyJWTError as exc:
            raise unauthorized(f"토큰 검증 실패: {exc}") from exc
        kind = claims.get("anx_kind", "user")
        if kind in ("user", "delegation"):
            user = db.scalars(select(User).where(User.sub == claims["sub"])).first()
            if user is None or user.status != "active":
                raise unauthorized("사용자가 비활성 상태입니다.")
            nbf = user.tokens_not_before
            if nbf is not None and int(claims.get("iat") or 0) < int(nbf.timestamp()):  # 세션 강제 종료 (SCR-J03·B06) — iat 는 초 단위
                raise unauthorized("세션이 종료되었습니다 — 다시 로그인하세요.")
            act = (claims.get("act") or {}).get("sub")
            return _user_principal(user, token, kind=kind, actor=act, token_id=claims.get("jti"), session_id=claims.get("sid"))
        if kind == "workload":
            wl = db.scalars(select(Workload).where(Workload.client_id == claims.get("client_id"))).first()
            if wl is None or not wl.enabled:
                raise unauthorized("워크로드가 비활성 상태입니다.")
            return Principal(sub=claims["sub"], kind="workload", name=wl.name, roles=["user"],
                             scopes=claims.get("scope", "").split(), raw_token=token, token_id=claims.get("jti"))
        if kind == "access_token":
            row = db.get(AccessToken, claims.get("jti"))
            if row is None or row.revoked_at or row.expires_at < utcnow():
                raise unauthorized("Access Token 이 폐기되었거나 만료되었습니다.")
            row.last_used_at = utcnow()
            db.commit()
            return Principal(sub=claims["sub"], kind="access_token", name=row.name, roles=["user"],
                             scopes=list(row.scopes or []), raw_token=token, token_id=row.id,
                             actor=row.issued_by)
        raise unauthorized("지원하지 않는 토큰 유형입니다.")

    idp = db.scalars(select(IdpConnection).where(IdpConnection.issuer == claims.get("iss"),
                                                 IdpConnection.enabled.is_(True))).first()
    if idp is None:
        raise unauthorized("신뢰하지 않는 발급자(issuer)입니다.")
    try:
        verified = _external_verify(idp, token)
    except Exception as exc:  # noqa: BLE001 — 네트워크·서명 오류 모두 401
        raise unauthorized(f"외부 IdP 토큰 검증 실패: {exc}") from exc
    user = _upsert_external_user(db, idp, verified)
    if user.status != "active":
        raise unauthorized("사용자가 비활성 상태입니다.")
    return _user_principal(user, token)


def bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.headers.get("x-api-key") or None


def optional_principal(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    if getattr(request.state, "principal", None) is not None:
        return request.state.principal
    token = bearer(request)
    if not token:
        return None
    p = authenticate_token(db, token)
    request.state.principal = p
    return p


def current_principal(p: Principal | None = Depends(optional_principal)) -> Principal:
    if p is None:
        raise unauthorized()
    return p


def require(*perms: str):
    def _dep(p: Principal = Depends(current_principal)) -> Principal:
        missing = [x for x in perms if not p.has(x)]
        if missing:
            raise forbidden(f"권한 부족: {', '.join(missing)}", code="forbidden", required=missing)
        return p
    return _dep
