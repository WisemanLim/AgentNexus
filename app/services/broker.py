"""외부 IdP 로그인 브로커 (FR-I00·I01, SCR-A10·J02) — 내장 IdP 가 Keycloak 처럼 외부 OIDC(Entra·Okta·Keycloak)로
로그인을 위임하고, 돌아온 id_token 을 JWKS 로 검증해 사용자·그룹→역할을 동기화한 뒤 원래 인가 요청(콘솔 PKCE)에 코드를 준다.

    콘솔 ─/idp/authorize?idp_hint─▶ 내장 IdP ─302─▶ 외부 IdP authorize (PKCE·state·nonce)
         ◀─302 code─ 내장 IdP ◀─/idp/broker/callback?code─ 외부 IdP   (토큰 교환·서명·nonce 검증은 서버 측)

분리 DNS: 브라우저용 주소는 issuer·discovery 그대로, 서버 측 호출(discovery·token·jwks)은 internal_url 로 바꿔 부른다.
오류는 원인 코드로 원래 redirect_uri 에 돌려준다(OAuth error 응답) — 콘솔이 원인별 문구를 보여 준다.
"""
from datetime import timedelta
from urllib.parse import urlencode

import jwt

from app.core import security
from app.core.auth import _external_verify, _map_roles, _upsert_external_user
from app.core.config import settings
from app.core.http import sync_client
from app.core.ids import token_urlsafe
from app.db import utcnow
from app.models import BrokerRequest, IdpConnection
from app.services import audit, secrets

TTL = timedelta(minutes=10)
ERRORS = {  # 원인 코드 → 사용자 안내 (콘솔 Login 이 같은 코드로 문구를 고른다)
    "access_denied": "IdP 에서 로그인을 거부하거나 취소했습니다.",
    "invalid_state": "로그인 요청이 만료되었거나 이미 사용되었습니다. 다시 시작하세요.",
    "idp_unavailable": "IdP 에 연결할 수 없습니다.",
    "token_invalid": "IdP 토큰 검증(서명·발급자·nonce)에 실패했습니다.",
    "user_disabled": "비활성화된 계정입니다.",
    "idp_disabled": "사용할 수 없는 IdP 입니다.",
}


class BrokerError(Exception):
    def __init__(self, code: str, detail: str | None = None):
        super().__init__(detail or ERRORS.get(code, code))
        self.code, self.detail = code, detail or ERRORS.get(code, code)


def callback_url() -> str:
    return settings.public_base_url.rstrip("/") + "/idp/broker/callback"


def srv(idp: IdpConnection, url: str) -> str:
    """서버 측 호출 주소 — issuer 접두사를 internal_url 로 (브라우저와 서버가 보는 주소가 다른 배포)."""
    if idp.internal_url and url.startswith(idp.issuer.rstrip("/")):
        return idp.internal_url.rstrip("/") + url[len(idp.issuer.rstrip("/")):]
    return url


def discover(issuer: str, internal_url: str | None = None) -> dict:
    base = (internal_url or issuer).rstrip("/")
    with sync_client(timeout=10) as http:
        res = http.get(base + "/.well-known/openid-configuration")
        res.raise_for_status()
        meta = res.json()
    if meta.get("issuer") != issuer:
        raise BrokerError("idp_unavailable", f"discovery issuer 불일치: {meta.get('issuer')} ≠ {issuer}")
    return meta


def _meta(idp: IdpConnection) -> dict:
    if idp.discovery and idp.discovery.get("authorization_endpoint") and idp.discovery.get("token_endpoint"):
        return idp.discovery
    try:
        meta = discover(idp.issuer, idp.internal_url)
    except BrokerError:
        raise
    except Exception as exc:  # noqa: BLE001 — 네트워크·형식 오류
        raise BrokerError("idp_unavailable", f"discovery 실패: {type(exc).__name__}") from exc
    idp.discovery = {k: meta.get(k) for k in ("authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint",
                                              "code_challenge_methods_supported")}
    idp.jwks_uri = idp.jwks_uri or meta.get("jwks_uri")
    return idp.discovery


def start(db, idp: IdpConnection, params: dict, mode: str = "login", actor: str | None = None) -> str:
    """외부 IdP authorize URL — 이 요청 전용 PKCE·state·nonce 를 만든다."""
    if (not idp.enabled and mode != "test") or (mode == "login" and not idp.login_enabled):  # 시험 로그인은 비활성 초안도 (SCR-J02 ④)
        raise BrokerError("idp_disabled")
    if not idp.client_id:
        raise BrokerError("idp_unavailable", "client_id 가 설정되지 않았습니다.")
    meta = _meta(idp)
    req = BrokerRequest(state=token_urlsafe(24), idp_id=idp.id, mode=mode, params=params, nonce=token_urlsafe(16),
                        code_verifier=token_urlsafe(48), created_by=actor, expires_at=utcnow() + TTL)
    db.add(req)
    db.commit()
    q = {"response_type": "code", "client_id": idp.client_id, "redirect_uri": callback_url(), "scope": idp.scopes,
         "state": req.state, "nonce": req.nonce, "code_challenge": security.pkce_s256(req.code_verifier),
         "code_challenge_method": "S256"}
    sep = "&" if "?" in meta["authorization_endpoint"] else "?"
    return f"{meta['authorization_endpoint']}{sep}{urlencode(q)}"


def _exchange(db, idp: IdpConnection, code: str, verifier: str) -> dict:
    meta = _meta(idp)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": callback_url(), "client_id": idp.client_id,
            "code_verifier": verifier}
    if idp.client_secret_ref:
        form["client_secret"] = secrets.require_plain(db, idp.client_secret_ref)
    try:
        with sync_client(timeout=15) as http:
            res = http.post(srv(idp, meta["token_endpoint"]), data=form)
    except Exception as exc:  # noqa: BLE001
        raise BrokerError("idp_unavailable", f"토큰 교환 실패: {type(exc).__name__}") from exc
    if res.status_code >= 400:
        err = (res.json() if res.headers.get("content-type", "").startswith("application/json") else {}) or {}
        raise BrokerError("token_invalid", f"토큰 교환 거부: {err.get('error') or res.status_code}")
    return res.json()


def complete(db, state: str, code: str | None, error: str | None) -> tuple[BrokerRequest, dict]:
    """콜백 처리 → (요청, 결과). 결과: login 모드는 {user}, test 모드는 {claims, roles, groups}."""
    req = db.get(BrokerRequest, state or "")
    if req is None or req.used or req.expires_at < utcnow():
        raise BrokerError("invalid_state")
    req.used = True
    db.commit()
    idp = db.get(IdpConnection, req.idp_id)
    if idp is None or (not idp.enabled and req.mode != "test"):
        raise BrokerError("idp_disabled")
    if error:
        raise BrokerError("access_denied" if error in ("access_denied", "login_required", "consent_required") else
                          "idp_unavailable", f"IdP 응답: {error}")
    tokens = _exchange(db, idp, code or "", req.code_verifier)
    try:
        claims = _external_verify(idp, tokens.get("id_token") or "")
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise BrokerError("token_invalid", f"id_token 검증 실패: {exc}") from exc
    if claims.get("nonce") != req.nonce:
        raise BrokerError("token_invalid", "nonce 불일치 (재전송 의심)")
    groups = claims.get(idp.groups_claim) or []
    roles = _map_roles(idp, claims)
    if req.mode == "test":
        result = {"status": "ok", "sub": claims.get("sub"), "email": claims.get("email"),
                  "name": claims.get("name") or claims.get("preferred_username"),
                  "groups": groups if isinstance(groups, list) else [groups], "roles": roles,
                  "claims": sorted(claims), "tested_at": utcnow().isoformat()}
        req.result = result
        idp.last_test, idp.status, idp.last_tested_at = result, "ok", utcnow()
        db.commit()
        audit.append("identity.idp.test_login", actor=req.created_by or "unknown", target=idp.issuer,
                     data={"roles": roles, "groups": result["groups"]})
        return req, result
    user = _upsert_external_user(db, idp, claims)
    if user.status != "active":
        raise BrokerError("user_disabled")
    audit.append("auth.login", actor=user.sub, actor_kind="user", decision="allow",
                 data={"client_id": req.params.get("client_id"), "idp": idp.id, "roles": user.roles})
    return req, {"user": user}


def fail_test(db, state: str, err: BrokerError) -> None:
    req = db.get(BrokerRequest, state or "")
    if req is not None and req.mode == "test":
        req.result = {"status": "error", "code": err.code, "detail": err.detail, "tested_at": utcnow().isoformat()}
        idp = db.get(IdpConnection, req.idp_id)
        if idp is not None:
            idp.last_test, idp.status, idp.last_tested_at = req.result, "error", utcnow()
        db.commit()
