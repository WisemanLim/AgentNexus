"""데모 외부 IdP (local·test 전용) — 콘솔 SSO 브로커(FR-I00·I01)를 실제 OIDC 흐름으로 시연·시험하기 위한 작은 OIDC 공급자.

Authorization Code + PKCE(S256) · client_secret_post · RS256 id_token(nonce·groups) · JWKS. 서명 키는 프로세스 메모리에만 있고,
계정은 시드 계정과 같은 규칙(`<계정>-pass`)이다. APP_ENV=prod 에서는 라우터를 붙이지 않는다(main.py).
"""
import html
import time
from datetime import timedelta
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core import security
from app.core.config import settings
from app.core.ids import token_urlsafe
from app.db import utcnow

router = APIRouter(prefix="/_demo/oidc", include_in_schema=False)
CLIENT_ID, CLIENT_SECRET = "demo-partner", "demo-partner-secret"
# 데모 파트너 계정 — 그룹 클레임으로 역할이 매핑된다 (anx-developers → developer)
USERS = {"partner-kim": {"name": "김파트너", "email": "kim@partner.example", "groups": ["anx-developers"]},
         "partner-lee": {"name": "이파트너", "email": "lee@partner.example", "groups": []}}
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = token_urlsafe(8)
_CODES: dict[str, dict] = {}


def issuer() -> str:
    return settings.public_base_url.rstrip("/") + "/_demo/oidc"


@router.get("/.well-known/openid-configuration")
def discovery():
    i = issuer()
    return {"issuer": i, "authorization_endpoint": f"{i}/authorize", "token_endpoint": f"{i}/token",
            "jwks_uri": f"{i}/jwks", "response_types_supported": ["code"], "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"], "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"], "scopes_supported": ["openid", "profile", "email"]}


@router.get("/jwks")
def jwks():
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": _KID, "use": "sig", "alg": "RS256"}]}


_FORM = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>데모 파트너 IdP</title></head>
<body style="font:14px system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;background:#FFF8E1">
<main style="background:#fff;padding:28px;border-radius:10px;width:320px;box-shadow:0 2px 12px #0002">
<h1 style="font-size:18px;margin:0 0 4px">데모 파트너 IdP</h1><p style="color:#666;margin:0 0 16px">외부 조직 계정 (데모)</p>
<form method="post">{hidden}<label for="du">파트너 계정</label><br><input id="du" name="username" required style="width:100%;height:32px"><br>
<label for="dp">파트너 비밀번호</label><br><input id="dp" name="password" type="password" required style="width:100%;height:32px">
{error}<button name="action" value="login" type="submit" style="margin-top:14px;width:100%;height:34px">파트너 로그인</button></form>
<form method="post">{hidden}<button name="action" value="cancel" type="submit" style="margin-top:8px;width:100%;height:30px">취소</button></form>
</main></body></html>"""


def _form(params: dict, error: str = "") -> HTMLResponse:
    hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">' for k, v in params.items())
    err = f'<p role="alert" style="color:#C92A2A">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(_FORM.format(hidden=hidden, error=err))


def _redirect(uri: str, q: dict) -> RedirectResponse:
    return RedirectResponse(f"{uri}{'&' if '?' in uri else '?'}{urlencode(q)}", status_code=302)


@router.get("/authorize")
def authorize(client_id: str, redirect_uri: str, state: str = "", nonce: str = "", code_challenge: str = "",
              code_challenge_method: str = "S256", scope: str = "openid"):
    if client_id != CLIENT_ID or not code_challenge or code_challenge_method != "S256":
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    return _form({"client_id": client_id, "redirect_uri": redirect_uri, "state": state, "nonce": nonce,
                  "code_challenge": code_challenge, "scope": scope})


@router.post("/authorize")
def authorize_submit(client_id: str = Form(...), redirect_uri: str = Form(...), state: str = Form(""),
                     nonce: str = Form(""), code_challenge: str = Form(...), scope: str = Form("openid"),
                     action: str = Form("login"), username: str = Form(""), password: str = Form("")):
    if action == "cancel":
        return _redirect(redirect_uri, {"error": "access_denied", "state": state})
    user = USERS.get(username)
    if user is None or password != f"{username}-pass":
        return _form({"client_id": client_id, "redirect_uri": redirect_uri, "state": state, "nonce": nonce,
                      "code_challenge": code_challenge, "scope": scope}, "파트너 계정 또는 비밀번호가 올바르지 않습니다.")
    code = token_urlsafe(24)
    _CODES[code] = {"sub": username, "client_id": client_id, "redirect_uri": redirect_uri, "nonce": nonce,
                    "challenge": code_challenge, "exp": time.time() + 120}
    return _redirect(redirect_uri, {"code": code, "state": state})


@router.post("/token")
async def token(request: Request):
    f = dict(await request.form())
    c = _CODES.pop(f.get("code", ""), None)
    if f.get("client_id") != CLIENT_ID or f.get("client_secret") != CLIENT_SECRET:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    if c is None or c["exp"] < time.time() or c["redirect_uri"] != f.get("redirect_uri"):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if security.pkce_s256(f.get("code_verifier", "")) != c["challenge"]:
        return JSONResponse({"error": "invalid_grant", "error_description": "PKCE"}, status_code=400)
    u = USERS[c["sub"]]
    now = utcnow()
    claims = {"iss": issuer(), "sub": c["sub"], "aud": CLIENT_ID, "iat": int(now.timestamp()),
              "exp": int((now + timedelta(minutes=5)).timestamp()), "nonce": c["nonce"], "name": u["name"],
              "email": u["email"], "preferred_username": c["sub"], "groups": u["groups"]}
    id_token = jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": _KID})
    return {"access_token": token_urlsafe(24), "token_type": "Bearer", "expires_in": 300, "id_token": id_token}
