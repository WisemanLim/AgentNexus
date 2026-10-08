"""Epic 19 — 외부 IdP SSO 로그인 브로커 (FR-I00·I01, SCR-A10·J01·J02) — 데모 외부 IdP(/_demo/oidc)로 실제 OIDC 흐름."""
from urllib.parse import parse_qs, urlparse

import pytest

from app.core import security
from app.db import SessionLocal
from app.models import BrokerRequest, User

IDP = "idp-demo-partner"
CONSOLE = "http://testserver/login/callback"


def q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def console_authorize(client, hint: str | None = IDP, verifier: str = "v" * 48):
    params = {"response_type": "code", "client_id": "agentnexus-console", "redirect_uri": CONSOLE, "state": "st-1",
              "scope": "openid profile email", "code_challenge": security.pkce_s256(verifier),
              "code_challenge_method": "S256", **({"idp_hint": hint} if hint else {})}
    return client.get("/idp/authorize", params=params, follow_redirects=False)


def partner_login(client, ext_url: str, username: str = "partner-kim", action: str = "login"):
    """외부(데모) IdP 화면에서 로그인 → 내장 IdP 콜백 → 콘솔 redirect_uri (Location)."""
    form = {**q(ext_url), "username": username, "password": f"{username}-pass", "action": action}
    back = client.post("/_demo/oidc/authorize", data=form, follow_redirects=False)
    assert back.status_code == 302 and back.headers["location"].startswith("http://testserver/idp/broker/callback")
    return client.get(back.headers["location"], follow_redirects=False)


def test_console_sso_via_external_idp_maps_groups_to_roles(client):
    providers = client.get("/idp/providers").json()["items"]
    assert {"id": IDP, "name": "데모 파트너 IdP", "kind": "oidc"} in providers
    page = console_authorize(client, hint=None)
    assert f"/idp/broker/{IDP}/start" in page.text and "데모 파트너 IdP 계정으로 로그인" in page.text  # 로그인 화면 버튼
    r = console_authorize(client)
    ext = r.headers["location"]
    assert r.status_code == 302 and ext.startswith("http://testserver/_demo/oidc/authorize")
    eq = q(ext)
    assert eq["code_challenge_method"] == "S256" and eq["nonce"] and eq["redirect_uri"].endswith("/idp/broker/callback")
    done = partner_login(client, ext)
    loc = done.headers["location"]
    assert done.status_code == 302 and loc.startswith(CONSOLE) and q(loc)["state"] == "st-1"
    tok = client.post("/idp/token", data={"grant_type": "authorization_code", "code": q(loc)["code"],
                                          "redirect_uri": CONSOLE, "client_id": "agentnexus-console",
                                          "code_verifier": "v" * 48}).json()
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
    assert me["sub"] == f"ext:{IDP}:partner-kim" and me["roles"] == ["developer"] and me["groups"] == ["anx-developers"]
    assert me["email"] == "kim@partner.example"
    assert client.get("/api/v1/agents", headers={"Authorization": f"Bearer {tok['access_token']}"}).status_code == 200


def test_unmapped_groups_get_least_privilege_user_role(client):
    done = partner_login(client, console_authorize(client).headers["location"], username="partner-lee")
    code = q(done.headers["location"])["code"]
    tok = client.post("/idp/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CONSOLE,
                                          "client_id": "agentnexus-console", "code_verifier": "v" * 48}).json()
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
    assert me["roles"] == ["user"]


@pytest.mark.parametrize("case", ["cancel", "replay", "disabled_user", "disabled_idp"])
def test_sso_error_causes_are_returned_to_console(client, case):
    ext = console_authorize(client).headers["location"]
    if case == "cancel":
        done = partner_login(client, ext, action="cancel")
        assert q(done.headers["location"])["error"] == "access_denied"
        return
    if case == "replay":
        form = {**q(ext), "username": "partner-kim", "password": "partner-kim-pass", "action": "login"}
        cb = client.post("/_demo/oidc/authorize", data=form, follow_redirects=False).headers["location"]
        assert client.get(cb, follow_redirects=False).status_code == 302
        again = client.get(cb, follow_redirects=False)  # 같은 state 재사용 → 원래 요청(콘솔)에 원인 코드로 돌려준다
        assert again.status_code == 302 and q(again.headers["location"])["error"] == "invalid_state"
        unknown = client.get("/idp/broker/callback", params={"state": "nope", "code": "x"}, follow_redirects=False)
        assert unknown.status_code == 400 and "만료되었거나 이미 사용" in unknown.text  # 원래 요청을 모르면 오류 화면
        return
    if case == "disabled_user":
        with SessionLocal() as db:
            u = db.query(User).filter(User.sub == f"ext:{IDP}:partner-kim").first()
            u.status = "disabled"
            db.commit()
        try:
            done = partner_login(client, ext)
            assert q(done.headers["location"])["error"] == "user_disabled"
        finally:
            with SessionLocal() as db:
                db.query(User).filter(User.sub == f"ext:{IDP}:partner-kim").first().status = "active"
                db.commit()
        return
    with SessionLocal() as db:  # disabled_idp
        from app.models import IdpConnection
        db.get(IdpConnection, IDP).enabled = False
        db.commit()
    try:
        r = console_authorize(client)
        assert r.status_code == 302 and q(r.headers["location"])["error"] == "idp_disabled"
        assert all(p["id"] != IDP for p in client.get("/idp/providers").json()["items"])
    finally:
        with SessionLocal() as db:
            db.get(IdpConnection, IDP).enabled = True
            db.commit()


def test_idp_wizard_discover_save_test_login_then_enable(client, H):
    iss = "http://testserver/_demo/oidc"
    disc = client.post("/api/v1/identity/idps/discover", json={"issuer": iss}, headers=H["security"]).json()
    assert disc["discovery"] and disc["jwks"] and disc["pkce_s256"] and disc["redirect_uri"].endswith("/idp/broker/callback")
    bad = client.post("/api/v1/identity/idps/discover", json={"issuer": "http://testserver/nope"}, headers=H["security"]).json()
    assert bad["discovery"] is False and bad["error"]
    # 같은 데모 IdP 를 다른 issuer 별칭으로 등록할 수 없으니(issuer 유일), 기존 연동을 시험 로그인 대상으로 쓴다
    with SessionLocal() as db:
        from app.models import IdpConnection
        db.get(IdpConnection, IDP).last_test = {}
        db.commit()
    start = client.post(f"/api/v1/identity/idps/{IDP}/test-login", headers=H["security"]).json()
    assert start["url"].startswith(iss + "/authorize")
    assert client.get(f"/api/v1/identity/idps/{IDP}/test-login/{start['state']}", headers=H["security"]).json() == {"status": "pending"}
    form = {**q(start["url"]), "username": "partner-kim", "password": "partner-kim-pass", "action": "login"}
    cb = client.post("/_demo/oidc/authorize", data=form, follow_redirects=False).headers["location"]
    page = client.get(cb, follow_redirects=False)
    assert page.status_code == 200 and "시험 로그인 성공" in page.text  # 로그인 세션은 만들지 않음
    res = client.get(f"/api/v1/identity/idps/{IDP}/test-login/{start['state']}", headers=H["security"]).json()
    assert res["status"] == "ok" and res["roles"] == ["developer"] and "groups" in res["claims"]
    idp = next(i for i in client.get("/api/v1/identity/idps", headers=H["security"]).json()["items"] if i["id"] == IDP)
    assert idp["last_test"]["status"] == "ok" and idp["client_secret"] == "••••"
    with SessionLocal() as db:
        assert db.get(BrokerRequest, start["state"]).used is True


def test_login_enable_requires_successful_test(client, H):
    created = client.post("/api/v1/identity/idps", json={
        "name": "초안 IdP", "issuer": "https://login.example.test/tenant", "client_id": "c1", "client_secret": "s"},
        headers=H["security"]).json()
    assert created["login_enabled"] is False and created["status"] == "untested"
    nope = client.patch(f"/api/v1/identity/idps/{created['id']}", json={"login_enabled": True}, headers=H["security"])
    assert nope.status_code == 400 and nope.json()["code"] == "test_login_required"
    off = client.patch(f"/api/v1/identity/idps/{created['id']}", json={"enabled": False, "name": "초안 IdP (중지)"},
                       headers=H["security"]).json()
    assert off["enabled"] is False and off["name"] == "초안 IdP (중지)"  # J01 수정·비활성
    assert client.delete(f"/api/v1/identity/idps/{created['id']}", headers=H["security"]).status_code == 200
