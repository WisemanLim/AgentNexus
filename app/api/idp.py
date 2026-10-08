"""내장 IdP (FR-I00·I01) — OIDC 발급자. 외부 IdP(Keycloak·Entra ID·Okta)가 없을 때의 기본 IdP 이자,
게이트웨이 STS(RFC 8693 토큰 교환)·워크로드 client_credentials·CLI Device Flow 를 제공한다.

issuer = {PUBLIC_BASE_URL}/idp
"""
import html
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import keys, security
from app.core.auth import authenticate_token, current_principal, Principal
from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import token_urlsafe
from app.db import get_db, utcnow
from app.models import AuthCode, BrokerRequest, DeviceCode, IdpConnection, RefreshToken, User
from app.services import audit, broker, identity

router = APIRouter(tags=["identity"])


def _metadata() -> dict:
    iss = security.issuer()
    return {
        "issuer": iss,
        "authorization_endpoint": f"{iss}/authorize",
        "token_endpoint": f"{iss}/token",
        "userinfo_endpoint": f"{iss}/userinfo",
        "jwks_uri": f"{iss}/jwks",
        "revocation_endpoint": f"{iss}/revoke",
        "device_authorization_endpoint": f"{iss}/device/code",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials",
                                  identity.GRANT_DEVICE, identity.GRANT_TOKEN_EXCHANGE]
                                 + (["password"] if settings.app_env in ("local", "test") else []),
        "code_challenge_methods_supported": ["S256"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
    }


@router.get("/idp/.well-known/openid-configuration")
@router.get("/idp/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/idp")
def discovery():
    return _metadata()


@router.get("/idp/jwks")
def jwks():
    return keys.jwks()


def _check_redirect(redirect_uri: str) -> None:
    allowed = urlparse(settings.public_base_url)
    u = urlparse(redirect_uri)
    same = (u.scheme, u.netloc) == (allowed.scheme, allowed.netloc)
    dev = settings.app_env in ("local", "test") and u.hostname in ("localhost", "127.0.0.1") and u.scheme == "http"  # Vite 콘솔(:5173)
    if not (same or dev):
        raise ApiError(400, "invalid_request", "허용되지 않은 redirect_uri 입니다.")


_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentNexus 로그인</title><style>
:root{{--primary:#3F51E5;--navy:#1F3864;--muted:#868E96;--danger:#C92A2A;--bg:#F5F6FA;--card:#fff;--text:#1F2937}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111827;--card:#1F2937;--text:#E5E7EB;--navy:#C7D2FE}}}}
body{{margin:0;font:14px Pretendard,'Noto Sans KR',system-ui,sans-serif;background:var(--bg);color:var(--text);display:grid;place-items:center;min-height:100vh}}
.card{{background:var(--card);padding:32px;border-radius:12px;width:min(360px,calc(100vw - 32px));box-shadow:0 4px 24px #0001}}
h1{{font-size:20px;color:var(--navy);margin:0 0 4px}}p{{color:var(--muted);margin:0 0 20px}}
label{{display:block;font-size:12px;margin:12px 0 4px}}input{{width:100%;box-sizing:border-box;height:36px;padding:0 10px;border:1px solid #CED4DA;border-radius:6px;background:transparent;color:inherit}}
input:focus{{outline:2px solid var(--primary);outline-offset:1px}}button{{margin-top:20px;width:100%;height:36px;border:0;border-radius:6px;background:var(--primary);color:#fff;font-weight:600;cursor:pointer}}
.err{{color:var(--danger);font-size:12px;margin-top:8px}}
a.idp{{display:block;box-sizing:border-box;height:36px;line-height:34px;margin-top:8px;border:1px solid #CED4DA;border-radius:6px;text-align:center;color:inherit;text-decoration:none;font-weight:600}}
a.idp:hover{{border-color:var(--primary)}}.or{{margin:16px 0 0;text-align:center;font-size:12px;color:var(--muted)}}</style></head><body><main class="card">
<h1>AgentNexus</h1><p>{subtitle}</p><form method="post">{hidden}
{extra}<label for="u">사용자 이름 / Username</label><input id="u" name="username" autocomplete="username" value="{username}" required autofocus>
<label for="p">비밀번호 / Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<label for="o">MFA 코드 (등록한 경우)</label><input id="o" name="otp" inputmode="numeric" autocomplete="one-time-code" maxlength="6">
{error}<button type="submit">{button}</button></form></main></body></html>"""


def _page(subtitle: str, hidden: dict, button: str = "로그인", error: str = "", extra: str = "",
          username: str = "") -> HTMLResponse:
    fields = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v or "")}">'
                     for k, v in hidden.items())
    err = f'<div class="err" role="alert">{html.escape(error)}</div>' if error else ""
    return HTMLResponse(_PAGE.format(subtitle=html.escape(subtitle), hidden=fields, button=button, error=err,
                                     extra=extra, username=html.escape(username)))


# 무차별 대입 방지 — 사용자 이름별 실패 횟수(15분 고정 창, 워커 공유는 Valkey 백엔드일 때). 한도에 걸리면 비밀번호를 확인하지 않는다.
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_S = 900
LOCKED = "locked"


def _login(db: Session, username: str, password: str) -> User | str | None:
    """성공 → User · 실패 → None · 실패 한도 초과 → LOCKED."""
    from app.services import ratelimit
    key = f"login-fail:{username.strip().lower()}"
    if ratelimit.backend().window_get(key, LOGIN_WINDOW_S) >= LOGIN_MAX_FAILURES:
        return LOCKED
    user = db.scalars(select(User).where(User.username == username)).first()
    if user and user.status == "active" and security.check_password(password, user.password_hash):
        if security.needs_rehash(user.password_hash):  # 이전 강도 해시 → 현재 파라미터로 (D17-④)
            user.password_hash = security.hash_password(password)
            db.commit()
        return user
    _fail(username)
    return None


def _fail(username: str) -> None:
    """로그인 실패 1회 — 비밀번호든 MFA 든 같은 창에 센다 (D19-④: 비밀번호를 아는 공격자의 MFA 무제한 대입 방지)."""
    from app.services import ratelimit
    ratelimit.backend().window_add(f"login-fail:{username.strip().lower()}", 1, LOGIN_WINDOW_S)


def _ip_ok(db: Session, request: Request, user: User, client_id: str | None) -> bool:
    """로그인 허용 IP (SCR-L06 ⑥) — 비밀번호 폼·디바이스 승인·외부 IdP 콜백 모두 (D19-④)."""
    ip = request.client.host if request.client else None
    if identity.ip_allowed(db, ip):
        return True
    audit.append("auth.login", actor=user.sub, actor_kind="user", decision="deny",
                 data={"client_id": client_id, "reason": "ip_policy", "ip": ip})
    return False


def _mfa_ok(user: User, otp: str) -> bool:
    """MFA 를 등록한 사용자는 TOTP 6자리가 맞아야 한다 (SCR-A05 ③)."""
    if not user.mfa_enabled:
        return True
    from app.services import totp
    try:
        secret = keys.fernet().decrypt((user.mfa_secret or "").encode()).decode()
    except Exception:  # noqa: BLE001 — 키 교체 등으로 복호화 불가 → 거부
        return False
    if not totp.verify(secret, otp):
        return False
    from app.services import ratelimit  # 같은 코드는 한 번만 (유효 창 ±1 단계 = 90초) — 엿본 코드 재사용 거부 (D20-②)
    code = (otp or "").strip().replace(" ", "")  # verify 와 같은 정규화 — "123 456" 으로 같은 코드를 다시 쓰지 못하게 (59th)
    return ratelimit.backend().counter_add(f"totp-used:{user.id}:{code}", 1, 120) == 1


def _providers(db: Session) -> list[IdpConnection]:
    from app.services import deploy
    if deploy.airgap(db):  # 에어갭 — 외부 IdP 선택지 숨김 (ST-09)
        return []
    return list(db.scalars(select(IdpConnection).where(IdpConnection.enabled.is_(True),
                                                       IdpConnection.login_enabled.is_(True))
                           .order_by(IdpConnection.name)))


@router.get("/idp/providers")
def providers(db: Session = Depends(get_db)):
    """로그인 화면용 외부 IdP 목록 (SCR-A10) — 이름·종류만, 인증 불필요."""
    return {"items": [{"id": i.id, "name": i.name, "kind": i.kind} for i in _providers(db)]}


def _authorize_params(response_type: str, client_id: str, redirect_uri: str, state: str, scope: str,
                      code_challenge: str, code_challenge_method: str, nonce: str) -> dict:
    if response_type != "code":
        raise ApiError(400, "unsupported_response_type", "response_type=code 만 지원합니다.")
    _check_redirect(redirect_uri)
    if client_id in identity.PUBLIC_CLIENTS and not code_challenge:
        raise ApiError(400, "invalid_request", "공개 클라이언트는 PKCE(code_challenge) 가 필요합니다.")
    return {"client_id": client_id, "redirect_uri": redirect_uri, "state": state, "scope": scope,
            "code_challenge": code_challenge, "code_challenge_method": code_challenge_method, "nonce": nonce}


def _error_redirect(params: dict, err: broker.BrokerError) -> RedirectResponse:
    """OAuth 오류 응답 — 원래 redirect_uri 로 원인 코드를 돌려준다 (콘솔이 원인별 문구 표시)."""
    uri = params["redirect_uri"]
    sep = "&" if "?" in uri else "?"
    q = {"error": err.code, "error_description": err.detail, "state": params.get("state", "")}
    return RedirectResponse(f"{uri}{sep}{urlencode(q)}", status_code=302)


@router.get("/idp/authorize", response_class=HTMLResponse)
def authorize_form(response_type: str, client_id: str, redirect_uri: str, state: str = "", scope: str = "openid",
                   code_challenge: str = "", code_challenge_method: str = "S256", nonce: str = "", idp_hint: str = "",
                   db: Session = Depends(get_db)):
    params = _authorize_params(response_type, client_id, redirect_uri, state, scope, code_challenge,
                               code_challenge_method, nonce)
    if idp_hint:  # 콘솔의 IdP 버튼 — 바로 외부 IdP 로
        idp = db.get(IdpConnection, idp_hint)
        try:
            if idp is None or idp not in _providers(db):
                raise broker.BrokerError("idp_disabled")
            return RedirectResponse(broker.start(db, idp, params), status_code=302)
        except broker.BrokerError as exc:
            return _error_redirect(params, exc)
    q = urlencode({"response_type": "code", **params})
    buttons = "".join(f'<a class="idp" href="/idp/broker/{html.escape(i.id)}/start?{html.escape(q)}">'
                      f'{html.escape(i.name)} 계정으로 로그인</a>' for i in _providers(db))
    extra = f'{buttons}<div class="or">또는 AgentNexus 계정</div>' if buttons else ""
    return _page("콘솔에 로그인 (OIDC SSO)", params, extra=extra)


@router.get("/idp/broker/{idp_id}/start")
def broker_start(idp_id: str, client_id: str, redirect_uri: str, response_type: str = "code", state: str = "",
                 scope: str = "openid", code_challenge: str = "", code_challenge_method: str = "S256", nonce: str = "",
                 db: Session = Depends(get_db)):
    params = _authorize_params(response_type, client_id, redirect_uri, state, scope, code_challenge,
                               code_challenge_method, nonce)
    idp = db.get(IdpConnection, idp_id)
    try:
        if idp is None or idp not in _providers(db):  # 비활성·로그인 미노출·에어갭
            raise broker.BrokerError("idp_disabled")
        return RedirectResponse(broker.start(db, idp, params), status_code=302)
    except broker.BrokerError as exc:
        return _error_redirect(params, exc)


_RESULT = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>IdP 시험 로그인</title></head>
<body style="font:14px system-ui,sans-serif;display:grid;place-items:center;min-height:90vh"><main role="status">
<h1 style="font-size:18px">{title}</h1><p>{detail}</p><p>이 창을 닫고 콘솔로 돌아가세요.</p></main>
<script>try{{window.opener&&window.opener.postMessage({{type:"anx-idp-test",state:{state}}},location.origin)}}catch(e){{}}</script>
</body></html>"""


@router.get("/idp/broker/callback")
def broker_callback(request: Request, state: str = "", code: str | None = None, error: str | None = None,
                    db: Session = Depends(get_db)):
    """외부 IdP → 내장 IdP 콜백 — 검증 후 원래 인가 요청(콘솔)에 코드 발급, 시험 모드는 결과만 저장."""
    import json as _json
    try:
        req, result = broker.complete(db, state, code, error)
    except broker.BrokerError as exc:
        req = db.get(BrokerRequest, state or "")
        audit.append("auth.login", actor=f"broker:{req.idp_id if req else '?'}", actor_kind="user", decision="deny",
                     data={"code": exc.code, "detail": exc.detail[:200]})
        if req is not None and req.mode == "test":
            broker.fail_test(db, state, exc)
            return HTMLResponse(_RESULT.format(title="시험 로그인 실패", detail=html.escape(exc.detail),
                                               state=_json.dumps(state)), status_code=400)
        if req is not None and req.params.get("redirect_uri"):
            return _error_redirect(req.params, exc)
        return HTMLResponse(_RESULT.format(title="로그인 실패", detail=html.escape(exc.detail), state='""'), status_code=400)
    if req.mode == "test":
        detail = f"역할 {', '.join(result['roles'])} · 그룹 {', '.join(result['groups']) or '없음'}"
        return HTMLResponse(_RESULT.format(title="시험 로그인 성공", detail=html.escape(detail), state=_json.dumps(state)))
    if not _ip_ok(db, request, result["user"], req.params.get("client_id")):
        return HTMLResponse(_RESULT.format(title="로그인 실패", detail="허용되지 않은 접속 위치입니다 (IP 정책) — 관리자에게 문의하세요.",
                                           state='""'), status_code=403)
    return _issue_code(db, result["user"], req.params)


def _issue_code(db: Session, user: User, params: dict) -> RedirectResponse:
    code = token_urlsafe(24)
    db.add(AuthCode(code=code, client_id=params["client_id"], redirect_uri=params["redirect_uri"], user_id=user.id,
                    code_challenge=params.get("code_challenge") or None,
                    code_challenge_method=params.get("code_challenge_method") or None, scope=params.get("scope"),
                    nonce=params.get("nonce") or None, expires_at=utcnow() + timedelta(minutes=2)))
    db.commit()
    uri = params["redirect_uri"]
    sep = "&" if "?" in uri else "?"
    return RedirectResponse(f"{uri}{sep}{urlencode({'code': code, 'state': params.get('state', '')})}", status_code=302)


@router.post("/idp/authorize")
def authorize_submit(request: Request, db: Session = Depends(get_db), client_id: str = Form(...), redirect_uri: str = Form(...),
                     username: str = Form(...), password: str = Form(...), otp: str = Form(""), state: str = Form(""),
                     scope: str = Form("openid"), code_challenge: str = Form(""),
                     code_challenge_method: str = Form("S256"), nonce: str = Form("")):
    _check_redirect(redirect_uri)
    user = _login(db, username, password)
    hidden = {"client_id": client_id, "redirect_uri": redirect_uri, "state": state, "scope": scope,
              "code_challenge": code_challenge, "code_challenge_method": code_challenge_method, "nonce": nonce}
    if user == LOCKED:
        audit.append("auth.login", actor=username, actor_kind="user", decision="deny", data={"client_id": client_id,
                                                                                            "reason": "too_many_failures"})
        return _page("콘솔에 로그인 (OIDC SSO)", hidden, error="로그인 실패가 너무 많습니다 — 15분 뒤 다시 시도하세요.",
                     username=username)
    if user is None:
        audit.append("auth.login", actor=username, actor_kind="user", decision="deny", data={"client_id": client_id})
        return _page("콘솔에 로그인 (OIDC SSO)", hidden, error="사용자 이름 또는 비밀번호가 올바르지 않습니다.",
                     username=username)
    if not _ip_ok(db, request, user, client_id):
        return _page("콘솔에 로그인 (OIDC SSO)", hidden, error="허용되지 않은 접속 위치입니다 (IP 정책) — 관리자에게 문의하세요.",
                     username=username)
    if not _mfa_ok(user, otp):
        _fail(username)
        audit.append("auth.login", actor=user.sub, actor_kind="user", decision="deny", data={"client_id": client_id,
                                                                                            "reason": "mfa"})
        return _page("콘솔에 로그인 (OIDC SSO)", hidden, error="MFA 코드가 필요하거나 올바르지 않습니다 (인증 앱의 6자리).",
                     username=username)
    code = token_urlsafe(24)
    db.add(AuthCode(code=code, client_id=client_id, redirect_uri=redirect_uri, user_id=user.id,
                    code_challenge=code_challenge or None, code_challenge_method=code_challenge_method or None,
                    scope=scope, nonce=nonce or None, expires_at=utcnow() + timedelta(minutes=2)))
    db.commit()
    audit.append("auth.login", actor=user.sub, actor_kind="user", decision="allow", data={"client_id": client_id})
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}", status_code=302)


def _oauth_error(code: str, desc: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code, "error_description": desc}, status_code=status,
                        headers={"Cache-Control": "no-store"})


@router.post("/idp/token")
async def token(request: Request, db: Session = Depends(get_db)):
    form = dict(await request.form())
    grant = form.get("grant_type", "")
    client_id = form.get("client_id", "")
    client_secret = form.get("client_secret", "")
    if not client_id and request.headers.get("authorization", "").lower().startswith("basic "):
        import base64
        raw = base64.b64decode(request.headers["authorization"][6:]).decode()
        client_id, _, client_secret = raw.partition(":")
    # scrypt(워크로드 시크릿)·DB·JWKS 조회는 동기 — 인증 없는 요청이 이벤트 루프를 막지 않게 스레드풀에서 (D19-④)
    return await run_in_threadpool(_grant, db, form, grant, client_id, client_secret)


def _grant(db: Session, form: dict, grant: str, client_id: str, client_secret: str) -> JSONResponse:
    try:
        if grant == "authorization_code":
            row = db.get(AuthCode, form.get("code", ""))
            if row is None or row.used or row.expires_at < utcnow() or row.client_id != client_id \
                    or row.redirect_uri != form.get("redirect_uri"):
                return _oauth_error("invalid_grant", "code 가 유효하지 않습니다.")
            if row.code_challenge and security.pkce_s256(form.get("code_verifier", "")) != row.code_challenge:
                return _oauth_error("invalid_grant", "PKCE 검증 실패")
            if db.execute(update(AuthCode).where(AuthCode.code == row.code, AuthCode.used.is_(False))
                          .values(used=True)).rowcount != 1:  # 1회용 — 동시 교환 중 하나만
                return _oauth_error("invalid_grant", "code 가 유효하지 않습니다.")
            user = db.get(User, row.user_id)
            return JSONResponse(identity.issue_user_tokens(db, user, client_id, row.scope, row.nonce),
                                headers={"Cache-Control": "no-store"})
        if grant == "refresh_token":
            return JSONResponse(identity.refresh(db, form.get("refresh_token", ""), client_id))
        if grant == "client_credentials":
            return JSONResponse(identity.issue_workload_token(db, client_id, client_secret, form.get("scope")))
        if grant == identity.GRANT_DEVICE:
            return _device_poll(db, form.get("device_code", ""), client_id)
        if grant == identity.GRANT_TOKEN_EXCHANGE:
            # confidential 클라이언트(워크로드) 인증 필수 — 사용자 토큰만으로 업스트림 audience 토큰을 받아 게이트웨이를 우회하지
            # 못하게, act 는 신고값이 아니라 인증된 워크로드 (D17-①)
            wl = identity.authenticate_workload(db, client_id, client_secret)
            subject = authenticate_token(db, form.get("subject_token", ""))
            audience = form.get("audience") or form.get("resource") or ""
            scopes = form.get("scope", "").split() or None
            return JSONResponse(identity.exchange(db, subject, audience, scopes, actor=f"workload:{wl.name}"))
        if grant == "password" and settings.app_env in ("local", "test"):
            user = _login(db, form.get("username", ""), form.get("password", ""))
            if user == LOCKED:
                return _oauth_error("invalid_grant", "로그인 실패가 너무 많습니다 — 15분 뒤 다시 시도하세요.")
            if user is not None and not _mfa_ok(user, form.get("otp", "")):
                _fail(form.get("username", ""))
                user = None
            if user is None:
                return _oauth_error("invalid_grant", "자격 증명(또는 MFA 코드)이 올바르지 않습니다.")
            return JSONResponse(identity.issue_user_tokens(db, user, client_id or "dev",
                                                           form.get("scope", "openid profile")))
    except ApiError as exc:
        return _oauth_error(exc.code if exc.code.startswith("invalid") else "invalid_request", exc.detail,
                            401 if exc.status == 401 else 400)
    return _oauth_error("unsupported_grant_type", f"지원하지 않는 grant_type: {grant}")


# ── Device Authorization Grant (RFC 8628) — nexus login --device ──
@router.post("/idp/device/code")
async def device_code(request: Request, db: Session = Depends(get_db)):
    form = dict(await request.form())
    import secrets as _s
    alphabet = "BCDFGHJKLMNPQRSTVWXZ23456789"
    user_code = "".join(_s.choice(alphabet) for _ in range(4)) + "-" + "".join(_s.choice(alphabet) for _ in range(4))
    dc = DeviceCode(device_code=token_urlsafe(32), user_code=user_code, client_id=form.get("client_id", "nexus-cli"),
                    scope=form.get("scope", "openid profile"), expires_at=utcnow() + timedelta(minutes=10))
    db.add(dc)
    db.commit()
    uri = f"{security.issuer()}/device"
    return {"device_code": dc.device_code, "user_code": user_code, "verification_uri": uri,
            "verification_uri_complete": f"{uri}?user_code={user_code}", "expires_in": 600, "interval": dc.interval}


def _device_poll(db: Session, code: str, client_id: str) -> JSONResponse:
    dc = db.get(DeviceCode, code)
    if dc is None or dc.client_id != client_id:
        return _oauth_error("invalid_grant", "device_code 가 유효하지 않습니다.")
    if dc.expires_at < utcnow():
        return _oauth_error("expired_token", "만료되었습니다.")
    now = utcnow()
    if dc.last_poll_at and (now - dc.last_poll_at).total_seconds() < dc.interval - 1:
        dc.last_poll_at = now
        db.commit()
        return _oauth_error("slow_down", "폴링 간격을 늘리세요.")
    dc.last_poll_at = now
    if dc.status == "denied":
        db.commit()
        return _oauth_error("access_denied", "거부되었습니다.")
    if dc.status != "approved":
        db.commit()
        return _oauth_error("authorization_pending", "브라우저에서 코드를 확인하세요.")
    dc.status = "used"
    user = db.get(User, dc.user_id)
    return JSONResponse(identity.issue_user_tokens(db, user, client_id, dc.scope))


@router.get("/idp/device", response_class=HTMLResponse)
def device_form(user_code: str = ""):
    extra = (f'<label for="c">디바이스 코드 / Device code</label><input id="c" name="user_code" '
             f'value="{html.escape(user_code)}" required>')
    return _page("CLI 로그인 — 터미널에 표시된 코드를 확인하세요", {}, button="승인", extra=extra)


@router.post("/idp/device", response_class=HTMLResponse)
def device_submit(request: Request, db: Session = Depends(get_db), user_code: str = Form(...), username: str = Form(...),
                  password: str = Form(...), otp: str = Form("")):
    dc = db.scalars(select(DeviceCode).where(DeviceCode.user_code == user_code.strip().upper())).first()
    user = _login(db, username, password)
    extra = (f'<label for="c">디바이스 코드 / Device code</label><input id="c" name="user_code" '
             f'value="{html.escape(user_code)}" required>')
    if dc is None or dc.expires_at < utcnow() or dc.status != "pending":
        return _page("CLI 로그인", {}, button="승인", error="코드가 유효하지 않거나 만료되었습니다.", extra=extra)
    if user == LOCKED:
        return _page("CLI 로그인", {}, button="승인", error="로그인 실패가 너무 많습니다 — 15분 뒤 다시 시도하세요.", extra=extra)
    if user is None:
        return _page("CLI 로그인", {}, button="승인", error="사용자 이름 또는 비밀번호가 올바르지 않습니다.", extra=extra)
    if not _ip_ok(db, request, user, dc.client_id):
        return _page("CLI 로그인", {}, button="승인", error="허용되지 않은 접속 위치입니다 (IP 정책) — 관리자에게 문의하세요.", extra=extra)
    if not _mfa_ok(user, otp):  # 브라우저 로그인과 같은 MFA 요구 (SCR-A05 ③)
        _fail(username)
        return _page("CLI 로그인", {}, button="승인", error="MFA 코드가 필요하거나 올바르지 않습니다 (인증 앱의 6자리).", extra=extra)
    dc.status, dc.user_id = "approved", user.id
    db.commit()
    audit.append("auth.device_approved", actor=user.sub, data={"client_id": dc.client_id})
    return HTMLResponse("<!doctype html><meta charset=utf-8><title>승인됨</title>"
                        "<p style='font:16px system-ui;text-align:center;margin-top:20vh'>승인했습니다. 터미널로 돌아가세요.</p>")


@router.get("/idp/userinfo")
def userinfo(p: Principal = Depends(current_principal)):
    return {"sub": p.sub, "name": p.name, "email": p.email, "roles": p.roles, "groups": p.groups, "team": p.team}


@router.post("/idp/revoke")
async def revoke(request: Request, db: Session = Depends(get_db)):
    form = dict(await request.form())
    raw = form.get("token", "")
    row = db.scalars(select(RefreshToken).where(RefreshToken.token_hash == security.token_hash(raw))).first()
    if row and not row.revoked_at:
        row.revoked_at = utcnow()
        db.commit()
    return JSONResponse({}, status_code=200)  # RFC 7009: 존재 여부와 무관하게 200
