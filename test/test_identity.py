"""Epic 2 — Identity & Authorization (FR-I00~I04, L03)."""
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import security
from app.core.http import register_mount
from app.db import SessionLocal, utcnow
from app.models import DeviceCode
from test.conftest import login


def test_unauthenticated_is_401(client):
    for path in ("/api/v1/me", "/api/v1/audit/verify", "/api/v1/identity/users"):
        res = client.get(path)
        assert res.status_code == 401, path
        assert res.headers.get("www-authenticate", "").startswith("Bearer")


def test_role_based_access(client, H):
    me = client.get("/api/v1/me", headers=H["user"]).json()
    assert me["roles"] == ["user"] and me["team"] == "team-biz"
    assert client.get("/api/v1/audit/verify", headers=H["user"]).status_code == 403
    assert client.get("/api/v1/audit/verify", headers=H["security"]).status_code == 200
    assert client.get("/api/v1/identity/users", headers=H["dev"]).status_code == 403
    assert client.get("/api/v1/identity/users", headers=H["admin"]).status_code == 200


def test_discovery_and_jwks_verify_tokens(client, tokens):
    meta = client.get("/idp/.well-known/openid-configuration").json()
    assert meta["issuer"] == security.issuer()
    assert "urn:ietf:params:oauth:grant-type:token-exchange" in meta["grant_types_supported"]
    keyset = client.get("/idp/jwks").json()
    header = jwt.get_unverified_header(tokens["dev"])
    k = next(k for k in keyset["keys"] if k["kid"] == header["kid"])
    claims = jwt.decode(tokens["dev"], jwt.PyJWK(k).key, algorithms=["RS256"], audience="agentnexus")
    assert claims["preferred_username"] == "dev" and claims["iss"] == meta["issuer"]


def test_authorization_code_with_pkce(client):
    verifier = "v" * 50
    challenge = security.pkce_s256(verifier)
    redirect = "http://testserver/console/callback"
    params = {"response_type": "code", "client_id": "agentnexus-console", "redirect_uri": redirect, "state": "s1",
              "scope": "openid profile", "code_challenge": challenge, "code_challenge_method": "S256", "nonce": "n1"}
    page = client.get("/idp/authorize", params=params)
    assert page.status_code == 200 and "AgentNexus" in page.text
    # PKCE 없는 공개 클라이언트는 거부
    assert client.get("/idp/authorize", params={**params, "code_challenge": ""}).status_code == 400
    # 허용되지 않은 redirect_uri
    assert client.get("/idp/authorize", params={**params, "redirect_uri": "https://evil.example/cb"}).status_code == 400
    bad = client.post("/idp/authorize", data={**params, "username": "dev", "password": "wrong"},
                      follow_redirects=False)
    assert bad.status_code == 200 and "올바르지 않습니다" in bad.text
    res = client.post("/idp/authorize", data={**params, "username": "dev", "password": "dev-pass"},
                      follow_redirects=False)
    assert res.status_code == 302
    q = parse_qs(urlparse(res.headers["location"]).query)
    assert q["state"] == ["s1"]
    form = {"grant_type": "authorization_code", "code": q["code"][0], "client_id": "agentnexus-console",
            "redirect_uri": redirect}
    assert client.post("/idp/token", data={**form, "code_verifier": "x" * 50}).json()["error"] == "invalid_grant"
    tok = client.post("/idp/token", data={**form, "code_verifier": verifier}).json()
    assert tok["token_type"] == "Bearer" and tok["id_token"] and tok["refresh_token"]
    id_claims = security.verify_local(tok["id_token"], audience="agentnexus-console")
    assert id_claims["nonce"] == "n1"
    # code 재사용 거부
    assert client.post("/idp/token", data={**form, "code_verifier": verifier}).json()["error"] == "invalid_grant"
    # refresh 회전: 새 토큰 발급 후 이전 refresh 재사용 거부
    r1 = client.post("/idp/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                         "client_id": "agentnexus-console"})
    assert r1.status_code == 200 and r1.json()["refresh_token"] != tok["refresh_token"]
    r2 = client.post("/idp/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                         "client_id": "agentnexus-console"})
    assert r2.json()["error"] == "invalid_grant"


def test_device_flow(client):
    dc = client.post("/idp/device/code", data={"client_id": "nexus-cli"}).json()
    assert dc["verification_uri"].endswith("/idp/device") and "-" in dc["user_code"]
    poll = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": dc["device_code"],
            "client_id": "nexus-cli"}
    assert client.post("/idp/token", data=poll).json()["error"] == "authorization_pending"
    assert client.post("/idp/token", data=poll).json()["error"] == "slow_down"
    page = client.post("/idp/device", data={"user_code": dc["user_code"], "username": "dev", "password": "dev-pass"})
    assert "승인했습니다" in page.text
    with SessionLocal() as db:
        row = db.get(DeviceCode, dc["device_code"])
        row.last_poll_at = utcnow() - timedelta(seconds=30)
        db.commit()
    tok = client.post("/idp/token", data=poll).json()
    assert "access_token" in tok
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok['access_token']}"}).json()["sub"] == "usr_dev"


def test_personal_access_token_lifecycle(client, H):
    created = client.post("/api/v1/me/tokens", json={"name": "ci", "ttl_days": 7}, headers=H["dev"])
    assert created.status_code == 201
    raw = created.json()["token"]
    assert raw.startswith("anx_pat_") and created.json()["prefix"] == raw[:12]
    listed = client.get("/api/v1/me/tokens", headers=H["dev"]).json()["items"]
    assert all("token" not in t for t in listed)  # 평문은 발급 시 1회만
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {raw}"}).json()
    assert me["kind"] == "pat" and me["sub"] == "usr_dev"
    client.delete(f"/api/v1/me/tokens/{created.json()['id']}", headers=H["dev"])
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_workload_client_credentials(client, H):
    res = client.post("/api/v1/identity/workloads", json={"name": "obs-agent", "kind": "agent",
                                                         "scopes": ["gateway:invoke", "mcp:dynatrace"]},
                      headers=H["security"])
    assert res.status_code == 201
    wl = res.json()
    assert wl["spiffe_id"] == "spiffe://agentnexus/agent/obs-agent"
    bad = client.post("/idp/token", data={"grant_type": "client_credentials", "client_id": wl["client_id"],
                                          "client_secret": "nope"})
    assert bad.status_code == 401 and bad.json()["error"] == "invalid_client"
    tok = client.post("/idp/token", data={"grant_type": "client_credentials", "client_id": wl["client_id"],
                                          "client_secret": wl["client_secret"], "scope": "gateway:invoke admin:*"})
    assert tok.json()["scope"] == "gateway:invoke"  # 허용 범위 밖 스코프는 부여되지 않음
    hdr = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    me = client.get("/api/v1/me", headers=hdr).json()
    assert me["kind"] == "workload" and me["sub"] == "workload:obs-agent"
    client.patch(f"/api/v1/identity/workloads/{wl['id']}", json={"enabled": False}, headers=H["security"])
    assert client.get("/api/v1/me", headers=hdr).status_code == 401


def test_token_exchange_rfc8693(client, H, tokens):
    pol = client.put("/api/v1/identity/token-exchange/policies",
                     json={"audience": "mcp:jira-test", "allowed_scopes": ["issues:read", "issues:write"],
                           "default_scopes": ["issues:read"], "ttl_seconds": 300}, headers=H["security"])
    assert pol.status_code == 200
    wl = client.post("/api/v1/identity/workloads", json={"name": "tx-client", "kind": "service", "scopes": []},
                     headers=H["security"]).json()
    form = {"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": tokens["user"],
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token", "audience": "mcp:jira-test",
            "client_id": wl["client_id"], "client_secret": wl["client_secret"]}
    # 워크로드 인증 없이(신고한 client_id 만) 교환 — 거부 (D17-①)
    anon = client.post("/idp/token", data={**form, "client_id": "gateway", "client_secret": "", "scope": "issues:read"})
    assert anon.status_code == 401 and anon.json()["error"] == "invalid_client"
    ok = client.post("/idp/token", data={**form, "scope": "issues:read"}).json()
    assert ok["issued_token_type"] == "urn:ietf:params:oauth:token-type:access_token" and ok["expires_in"] == 300
    claims = security.verify_local(ok["access_token"], audience="mcp:jira-test")
    assert claims["sub"] == "usr_user" and claims["scope"] == "issues:read" and claims["act"] == {"sub": "workload:tx-client"}
    assert claims["exp"] - claims["iat"] == 300
    # 교환 토큰은 audience 가 달라 관리 API 에서 거부
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {ok['access_token']}"}).status_code == 401
    denied = client.post("/idp/token", data={**form, "scope": "issues:admin"})
    assert denied.status_code == 400 and "issues:admin" in denied.json()["error_description"]
    no_policy = client.post("/idp/token", data={**form, "audience": "mcp:unknown"})
    assert no_policy.status_code == 400
    hist = client.get("/api/v1/identity/token-exchanges", params={"subject": "usr_user"}, headers=H["security"]).json()
    decisions = {(h["audience"], h["decision"]) for h in hist["items"]}
    assert ("mcp:jira-test", "issued") in decisions and ("mcp:jira-test", "denied") in decisions
    assert ("mcp:unknown", "denied") in decisions


def test_access_tokens_issue_and_revoke(client, H):
    assert client.post("/api/v1/access-tokens", json={"name": "x", "subject": "a2a:t"},
                       headers=H["user"]).status_code == 403
    res = client.post("/api/v1/access-tokens", json={"name": "obs", "subject": "a2a:observability-analyst",
                                                     "scopes": ["gateway:invoke"], "ttl_seconds": 600},
                      headers=H["operator"])
    assert res.status_code == 201
    at = res.json()
    claims = security.verify_local(at["token"])
    assert claims["exp"] - claims["iat"] == 600 and claims["sub"] == "a2a:observability-analyst"
    hdr = {"Authorization": f"Bearer {at['token']}"}
    assert client.get("/api/v1/me", headers=hdr).json()["kind"] == "access_token"
    client.post(f"/api/v1/access-tokens/{at['id']}/revoke", headers=H["operator"])
    assert client.get("/api/v1/me", headers=hdr).status_code == 401
    items = client.get("/api/v1/access-tokens", headers=H["operator"]).json()["items"]
    assert next(i for i in items if i["id"] == at["id"])["state"] == "revoked"


@pytest.fixture(scope="module")
def external_idp():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    iss = "https://idp.example.test"
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": "ext-1", "use": "sig", "alg": "RS256"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": iss, "jwks_uri": f"{iss}/keys",
                                             "authorization_endpoint": f"{iss}/auth", "token_endpoint": f"{iss}/token"})
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    register_mount(iss, sync_transport=httpx.MockTransport(handler))

    def make(sub="ext-user-1", aud="anx-client", groups=("grp-sec",)):
        now = int(time.time())
        return jwt.encode({"iss": iss, "sub": sub, "aud": aud, "iat": now, "exp": now + 300, "email": "kim@corp.test",
                           "name": "김보안", "groups": list(groups)}, key, algorithm="RS256", headers={"kid": "ext-1"})
    return iss, make


def test_external_oidc_federation(client, H, external_idp):
    iss, make = external_idp
    res = client.post("/api/v1/identity/idps", json={"name": "Corp Entra", "kind": "entra", "issuer": iss,
                                                    "client_id": "anx-client", "client_secret": "s3cr3t-value-123",
                                                    "role_mapping": {"grp-sec": "security"}},
                      headers=H["security"])
    assert res.status_code == 201 and res.json()["client_secret"] == "••••"
    test = client.post(f"/api/v1/identity/idps/{res.json()['id']}/test", headers=H["security"]).json()
    assert test["discovery"] and test["jwks"] and test["keys"] == 1
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {make()}"})
    assert me.status_code == 200
    body = me.json()
    assert body["roles"] == ["security"] and body["sub"].startswith("ext:") and body["email"] == "kim@corp.test"
    # 감사 권한까지 연결 (그룹 → 역할 매핑)
    assert client.get("/api/v1/audit/verify", headers={"Authorization": f"Bearer {make()}"}).status_code == 200
    # 매핑되지 않은 그룹은 user 역할
    other = client.get("/api/v1/me", headers={"Authorization": f"Bearer {make(sub='u2', groups=('x',))}"}).json()
    assert other["roles"] == ["user"]
    # 잘못된 audience → 401
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {make(aud='other')}"}).status_code == 401


def test_user_management_and_disable(client, H):
    res = client.post("/api/v1/identity/users", json={"username": "kim", "password": "kim-pass-1234",
                                                     "roles": ["developer"], "team": "team-agents"},
                      headers=H["security"])
    assert res.status_code == 201
    assert client.post("/api/v1/identity/users", json={"username": "lee", "password": "lee-pass-1234",
                                                      "roles": ["root"]}, headers=H["security"]).status_code == 400
    tok = login(client, "kim", "kim-pass-1234")
    hdr = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/me", headers=hdr).json()["roles"] == ["developer"]
    client.patch(f"/api/v1/identity/users/{res.json()['id']}", json={"roles": ["finops"]}, headers=H["security"])
    assert client.get("/api/v1/me", headers=hdr).json()["roles"] == ["finops"]  # 역할 변경 즉시 반영
    client.patch(f"/api/v1/identity/users/{res.json()['id']}", json={"status": "disabled"}, headers=H["security"])
    assert client.get("/api/v1/me", headers=hdr).status_code == 401


def test_roles_matrix_and_audit_trail(client, H):
    roles = client.get("/api/v1/identity/roles", headers=H["security"]).json()
    ids = {r["id"] for r in roles["roles"]}
    assert {"admin", "operator", "developer", "security", "finops", "user"} <= ids
    events = client.get("/api/v1/audit/events", params={"action": "token.exchange"}, headers=H["security"]).json()
    assert events["items"], "토큰 교환이 감사 로그에 남아야 함"
    assert client.get("/api/v1/audit/verify", headers=H["security"]).json()["valid"] is True
