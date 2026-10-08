"""Identity 서비스 — 토큰 발급(사용자·워크로드·Access Token·위임), RFC 8693 교환, PAT, 워크로드 신원."""
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import security
from app.core.auth import Principal
from app.core.config import settings
from app.core.context import current_trace_id
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.core.ids import new_id
from app.db import utcnow
from app.models import AccessToken, PersonalToken, RefreshToken, TokenExchange, TokenExchangePolicy, User, Workload
from app.services import audit

TOKEN_TYPE_ACCESS = "urn:ietf:params:oauth:token-type:access_token"
TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"
GRANT_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"

PUBLIC_CLIENTS = {"agentnexus-console", "nexus-cli"}


def user_claims(user: User) -> dict:
    return {"sub": user.sub, "anx_kind": "user", "name": user.name or user.username, "email": user.email,
            "preferred_username": user.username, "roles": list(user.roles or []), "groups": list(user.groups or []),
            "team": user.team}


def auth_policy(db: Session) -> dict:
    """세션·토큰·IP 정책 (SCR-L06 ⑤⑥) — 액세스 토큰 수명(분)·세션(갱신 토큰) 수명(시간)·로그인 허용 IP(CIDR)."""
    from app.models import Setting
    get = lambda k, d: (db.get(Setting, k).value if db.get(Setting, k) is not None else d)  # noqa: E731
    return {"access_token_ttl_min": int(get("auth.access_token_ttl_min", settings.access_token_ttl // 60)),
            "session_ttl_hours": int(get("auth.session_ttl_hours", 14 * 24)),
            "ip_allowlist": list(get("auth.ip_allowlist", []) or [])}


def ip_allowed(db: Session, ip: str | None) -> bool:
    import ipaddress
    nets = auth_policy(db)["ip_allowlist"]
    if not nets:
        return True
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(n, strict=False) for n in nets)


def issue_user_tokens(db: Session, user: User, client_id: str, scope: str = "openid profile",
                      nonce: str | None = None, with_refresh: bool = True, family: str | None = None) -> dict:
    pol = auth_policy(db)
    ttl = min(settings.access_token_ttl, pol["access_token_ttl_min"] * 60)
    rt_id = new_id("rt") if with_refresh else None
    access, _ = security.sign({**user_claims(user), "aud": security.API_AUDIENCE, "azp": client_id, "scope": scope,
                               **({"sid": rt_id} if rt_id else {})}, ttl)  # sid = 세션(refresh token) — 현재 세션 식별 (SCR-B06)
    out = {"access_token": access, "token_type": "Bearer", "expires_in": ttl, "scope": scope}
    if "openid" in scope.split():
        id_claims = {"sub": user.sub, "aud": client_id, "name": user.name or user.username, "email": user.email,
                     "preferred_username": user.username}
        if nonce:
            id_claims["nonce"] = nonce
        out["id_token"], _ = security.sign(id_claims, ttl, typ="JWT")
    if with_refresh:
        raw = security.opaque("anx_rt_")
        db.add(RefreshToken(id=rt_id, token_hash=security.token_hash(raw), user_id=user.id, client_id=client_id,
                            scope=scope, family_id=family or rt_id,
                            expires_at=utcnow() + timedelta(hours=pol["session_ttl_hours"])))
        out["refresh_token"] = raw
    user.last_login_at = utcnow()
    db.commit()
    return out


REUSE_GRACE_S = 10  # 같은 토큰으로 거의 동시에 온 갱신(탭 두 개 등)은 재사용으로 보지 않는다


def refresh(db: Session, raw: str, client_id: str) -> dict:
    row = db.scalars(select(RefreshToken).where(RefreshToken.token_hash == security.token_hash(raw))).first()
    if row is not None and row.revoked_at and row.revoked_at < utcnow() - timedelta(seconds=REUSE_GRACE_S):
        # 이미 회전·폐기된 토큰의 재제시 = 유출 신호 (OAuth 2.0 Security BCP) — 같은 계열의 살아 있는 토큰을 모두 폐기
        fam = row.family_id or row.id
        n = db.execute(update(RefreshToken).where((RefreshToken.family_id == fam) | (RefreshToken.id == fam),
                                                  RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow())).rowcount
        db.commit()
        audit.append("auth.refresh_reuse", actor=f"user:{row.user_id}", actor_kind="user", decision="deny",
                     data={"family": fam, "revoked": n, "client_id": client_id})
        raise ApiError(400, "invalid_grant", "refresh token 이 유효하지 않습니다.")
    if row is None or row.revoked_at or row.expires_at < utcnow() or row.client_id != client_id:
        raise ApiError(400, "invalid_grant", "refresh token 이 유효하지 않습니다.")
    # 회전 — 조건부 UPDATE 로 같은 refresh token 의 동시 요청 중 하나만 새 토큰을 받는다
    if db.execute(update(RefreshToken).where(RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None))
                  .values(revoked_at=utcnow())).rowcount != 1:
        raise ApiError(400, "invalid_grant", "refresh token 이 유효하지 않습니다.")
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise ApiError(400, "invalid_grant", "사용자가 비활성 상태입니다.")
    return issue_user_tokens(db, user, client_id, row.scope, family=row.family_id or row.id)


# ── 워크로드 (FR-I04) ──
def create_workload(db: Session, name: str, kind: str, scopes: list[str]) -> tuple[Workload, str]:
    secret = security.opaque("anx_ws_")
    wl = Workload(id=new_id("wl"), name=name, kind=kind, client_id=f"wl-{name}", secret_hash=security.hash_password(secret),
                  scopes=scopes, spiffe_id=f"spiffe://agentnexus/{kind}/{name}")
    db.add(wl)
    db.flush()
    return wl, secret


def rotate_workload(db: Session, wl: Workload) -> str:
    secret = security.opaque("anx_ws_")
    wl.secret_hash = security.hash_password(secret)
    wl.rotated_at = utcnow()
    db.flush()
    return secret


def authenticate_workload(db: Session, client_id: str, client_secret: str) -> Workload:
    wl = db.scalars(select(Workload).where(Workload.client_id == client_id)).first()
    if wl is None or not wl.enabled or not security.check_password(client_secret, wl.secret_hash):
        raise ApiError(401, "invalid_client", "클라이언트 인증 실패")
    return wl


def issue_workload_token(db: Session, client_id: str, client_secret: str, scope: str | None) -> dict:
    wl = authenticate_workload(db, client_id, client_secret)
    allowed = set(wl.scopes or [])
    requested = set(scope.split()) if scope else allowed
    granted = sorted(requested & allowed)
    ttl = settings.access_token_ttl
    token, _ = security.sign({"sub": f"workload:{wl.name}", "anx_kind": "workload", "client_id": wl.client_id,
                              "aud": security.API_AUDIENCE, "scope": " ".join(granted)}, ttl)
    return {"access_token": token, "token_type": "Bearer", "expires_in": ttl, "scope": " ".join(granted)}


# ── Access Tokens (FR-L03) ──
def issue_access_token(db: Session, name: str, subject: str, audience: str, scopes: list[str], ttl: int,
                       issued_by: str) -> tuple[AccessToken, str]:
    if ttl <= 0 or ttl > 86400:
        raise bad_request("ttl 은 1초 ~ 24시간 범위여야 합니다.")
    token, claims = security.sign({"sub": subject, "anx_kind": "access_token", "aud": audience,
                                   "scope": " ".join(scopes)}, ttl)
    row = AccessToken(id=claims["jti"], name=name, subject=subject, audience=audience, scopes=scopes,
                      issued_by=issued_by, expires_at=utcnow() + timedelta(seconds=ttl))
    db.add(row)
    db.flush()
    return row, token


# ── PAT·클라이언트 키 ──
def create_personal_token(db: Session, user: User, name: str, kind: str = "pat", scopes: list[str] | None = None,
                          ttl_days: int | None = 90, team: str | None = None) -> tuple[PersonalToken, str]:
    raw = security.opaque("anx_pat_" if kind == "pat" else "anx_key_")
    row = PersonalToken(id=new_id("pat"), user_id=user.id, kind=kind, name=name, token_hash=security.token_hash(raw),
                        prefix=raw[:12], scopes=scopes or [], team=team,
                        expires_at=utcnow() + timedelta(days=ttl_days) if ttl_days else None)
    db.add(row)
    db.flush()
    return row, raw


# ── 위임 토큰 (Host 가 승인 재개 등 사용자 부재 시 사용자 신원으로 게이트웨이 호출) ──
def delegation_token(snapshot: dict, actor: str, ttl: int = 600) -> str:
    token, _ = security.sign({"sub": snapshot["sub"], "anx_kind": "delegation", "aud": security.API_AUDIENCE,
                              "act": {"sub": actor}, "name": snapshot.get("name"), "team": snapshot.get("team")}, ttl)
    return token


# ── RFC 8693 토큰 교환 (FR-I02) ──
def exchange(db: Session, subject: Principal, audience: str, scopes: list[str] | None, actor: str = "gateway",
             method_hint: str | None = None) -> dict:
    """사용자(또는 워크로드) 토큰 → 대상(audience)별 최소 스코프 토큰."""
    trace_id = current_trace_id()
    pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == audience)).first()

    def _deny(reason: str) -> ApiError:
        db.add(TokenExchange(id=new_id("tx"), subject_sub=subject.sub, subject_kind=subject.kind, actor=actor,
                             audience=audience, scopes=scopes or [], ttl_seconds=0, method=method_hint or "rfc8693",
                             decision="denied", reason=reason, trace_id=trace_id))
        db.commit()
        audit.append("token.exchange", actor=subject.sub, actor_kind=subject.kind, target=audience, decision="deny",
                     data={"reason": reason, "scopes": scopes or [], "actor": actor})
        return forbidden(f"{audience}용 토큰을 발급하지 못했습니다(스코프 {' '.join(scopes or []) or '-'}): {reason}",
                         code="token_exchange_failed")

    if pol is None or not pol.enabled:
        raise _deny("교환 정책 없음")
    requested = list(scopes) if scopes else list(pol.default_scopes or pol.allowed_scopes or [])
    granted = [s for s in requested if s in (pol.allowed_scopes or [])]
    denied = [s for s in requested if s not in (pol.allowed_scopes or [])]
    if scopes and denied:
        raise _deny(f"허용되지 않은 스코프: {' '.join(denied)}")
    if not granted:
        raise _deny("부여 가능한 스코프 없음")
    ttl = pol.ttl_seconds
    token, claims = security.sign({"sub": subject.sub, "anx_kind": "exchanged", "aud": audience,
                                   "scope": " ".join(granted), "act": {"sub": actor}, "name": subject.name,
                                   "email": subject.email, "team": subject.team, "roles": subject.roles,
                                   "anx_trace": trace_id}, ttl)
    db.add(TokenExchange(id=new_id("tx"), subject_sub=subject.sub, subject_kind=subject.kind, actor=actor,
                         audience=audience, scopes=granted, ttl_seconds=ttl, method=pol.method, decision="issued",
                         trace_id=trace_id, jti=claims["jti"]))
    db.commit()
    audit.append("token.exchange", actor=subject.sub, actor_kind=subject.kind, target=audience, decision="issued",
                 data={"scopes": granted, "ttl": ttl, "method": pol.method, "actor": actor, "jti": claims["jti"]})
    return {"access_token": token, "issued_token_type": TOKEN_TYPE_ACCESS, "token_type": "Bearer",
            "expires_in": ttl, "scope": " ".join(granted)}


def get_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise not_found("user")
    return user
