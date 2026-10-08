"""Epic 53 — 신원·레지스트리 경계 (D20-②): Access Token audience 허용 목록 · admin 부여는 admin 만 · IdP 삭제 → 세션 종료 ·
새 버전 게시 소유자 · 미러 자동 승인 보류(외부 인가) · push_verify 변경 통제 · TOTP 재사용 거부."""
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core import security
from app.db import SessionLocal, utcnow
from app.models import CatalogItem, CatalogVersion, IdpConnection, RefreshToken, User
from app.services import registry, totp
from test.test_a2a import AG
from test.test_catalog import server_json
from test.test_review_fixes import _user


# ── C53-01 Access Token audience — 게이트웨이·등록된 MCP 서버·A2A 에이전트만 ──
def test_access_token_audience_allowlist(client, H):
    client.post("/api/v1/mcp-servers/jira/refresh", headers=H["operator"])  # 대상 교환 정책(D22-④ 발급 조건) — 시험 순서와 무관하게
    base = {"name": "e53", "subject": "a2a:e53-svc", "scopes": ["gateway:invoke"], "ttl_seconds": 300}
    ids = []
    for aud, code in ((security.API_AUDIENCE, 201), ("mcp:jira", 201), (f"a2a:{AG}", 201), ("mcp:no-such-server", 400),
                      ("model:fast-commercial", 400), ("https://evil.example", 400)):
        scopes = base["scopes"] if aud == security.API_AUDIENCE else []  # 대상용은 교환 정책 허용 스코프 안 (D22-④ — C64-01)
        res = client.post("/api/v1/access-tokens", json={**base, "audience": aud, "scopes": scopes}, headers=H["operator"])
        assert res.status_code == code, (aud, res.text)
        if code == 400:
            assert res.json()["code"] == "unknown_audience"
        else:
            ids.append(res.json()["id"])
    for tid in ids:
        client.post(f"/api/v1/access-tokens/{tid}/revoke", headers=H["operator"])


# ── C53-02 admin 부여 — 보안 담당(identity:write)은 admin 을 주거나 매핑하거나 관리자 계정을 바꾸지 못한다 ──
def test_admin_grant_admin_only(client, H):
    name = f"e53-{uuid.uuid4().hex[:6]}"
    body = {"username": name, "password": "E53-pass-word!", "name": name, "roles": ["admin"]}
    sec = client.post("/api/v1/identity/users", json=body, headers=H["security"])
    assert sec.status_code == 403 and sec.json()["code"] == "admin_grant_forbidden", sec.text
    dev = client.post("/api/v1/identity/users", json={**body, "roles": ["developer"]}, headers=H["security"])
    assert dev.status_code == 201, dev.text
    up = client.patch(f"/api/v1/identity/users/{dev.json()['id']}", json={"roles": ["admin"]}, headers=H["security"])
    assert up.status_code == 403 and up.json()["code"] == "admin_grant_forbidden"
    with SessionLocal() as db:
        admin_id = db.scalars(select(User.id).where(User.username == "admin")).first()
    assert client.patch(f"/api/v1/identity/users/{admin_id}", json={"roles": ["user"]}, headers=H["security"]).status_code == 403
    mapping = client.post("/api/v1/identity/idps", json={"name": "e53", "issuer": f"https://idp-{name}.example", "client_id": "x",
                                                        "role_mapping": {"platform-admins": "admin"}}, headers=H["security"])
    assert mapping.status_code == 403 and mapping.json()["code"] == "admin_grant_forbidden", mapping.text
    ok = client.patch(f"/api/v1/identity/users/{dev.json()['id']}", json={"roles": ["admin"]}, headers=H["admin"])
    assert ok.status_code == 200  # 관리자는 부여
    client.patch(f"/api/v1/identity/users/{dev.json()['id']}", json={"roles": ["user"], "status": "disabled"}, headers=H["admin"])


# ── C53-03 IdP 삭제 — 그 IdP 로 들어온 사용자의 세션 종료(토큰 무효·갱신 토큰 폐기) ──
def test_idp_delete_signs_out_users(client, H):
    iss = f"https://idp-e53-{uuid.uuid4().hex[:6]}.example"
    idp = client.post("/api/v1/identity/idps", json={"name": "e53-del", "issuer": iss, "client_id": "x"}, headers=H["security"])
    assert idp.status_code == 201, idp.text
    uid = "usr_e53_" + uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add(User(id=uid, sub=f"ext:{idp.json()['id']}:{uid}", issuer=iss, username=None, roles=["user"], status="active"))  # 실제 외부 사용자 sub 형식 (66th)
        db.add(RefreshToken(id="rt_" + uid, user_id=uid, client_id="agentnexus-console", token_hash=uuid.uuid4().hex, scope="openid",
                            expires_at=utcnow() + timedelta(days=1)))
        db.commit()
    res = client.delete(f"/api/v1/identity/idps/{idp.json()['id']}", headers=H["security"])
    assert res.status_code == 200 and res.json()["users_signed_out"] == 1
    with SessionLocal() as db:
        assert db.get(User, uid).tokens_not_before is not None and db.get(RefreshToken, "rt_" + uid).revoked_at is not None
        assert db.get(IdpConnection, idp.json()["id"]) is None


# ── C53-04 새 버전 게시 — 항목 소유자(또는 승인 권한자)만, 미러 항목은 수동 게시 불가 ──
def test_publish_new_version_owner_only(client, H):
    name = f"io.e53/own-{uuid.uuid4().hex[:6]}"
    v1 = client.post("/v0/publish", json=server_json(name=name), headers=H["dev"])
    assert v1.status_code == 201, v1.text
    with SessionLocal() as db:
        db.get(CatalogItem, v1.json()["item"]["id"]).owner = "usr_other_team"
        db.commit()
    v2 = client.post("/v0/publish", json=server_json(name=name, version="1.0.1"), headers=H["dev"])
    assert v2.status_code == 403 and v2.json()["code"] == "not_item_owner", v2.text
    assert client.post("/v0/publish", json=server_json(name=name, version="1.0.1"), headers=H["operator"]).status_code == 201


# ── C53-05 미러 자동 승인 — 외부 인가 서버로 사용자 토큰이 가는 항목은 보류(pending) ──
def test_mirror_auto_approve_holds_external_auth():
    with SessionLocal() as db:
        plain = server_json(name=f"io.e53/mirror-{uuid.uuid4().hex[:6]}", _meta={"license": "MIT"})  # 인가 없음 (59th: 고정 안 된 oauth2 도 보류)
        ext = server_json(name=f"io.e53/mirror-{uuid.uuid4().hex[:6]}",
                          _meta={"auth": {"type": "oauth2", "authorization_server": "https://login.partner.example"}})
        i1, v1, _ = registry.publish(db, "mcp_server", plain["name"], "1.0.0", plain, "system:mirror", source="mirror", auto_approve=True)
        i2, v2, _ = registry.publish(db, "mcp_server", ext["name"], "1.0.0", ext, "system:mirror", source="mirror", auto_approve=True)
        db.commit()
        assert db.get(CatalogVersion, v1.id).status == "approved" and db.get(CatalogVersion, v2.id).status == "pending"


# ── C53-06 push_verify — 푸시 서명 검증 설정 변경은 시크릿 관리 권한 ──
def test_push_verify_change_needs_secret_manage(client, H):
    pv = {"auth": {"push_verify": {"jwks_url": "https://agent.e53.example/jwks.json", "issuer": "https://agent.e53.example"}}}
    dev = client.patch(f"/api/v1/a2a/agents/{AG}", json=pv, headers=H["dev"])
    assert dev.status_code == 403 and dev.json()["code"] == "push_verify_forbidden", dev.text
    assert client.patch(f"/api/v1/a2a/agents/{AG}", json=pv, headers=H["operator"]).status_code == 200
    assert client.patch(f"/api/v1/a2a/agents/{AG}", json={"auth": {"push_verify": None}}, headers=H["operator"]).status_code == 200


# ── C53-07 TOTP — 같은 코드는 한 번만 (엿본 코드 재사용 거부) ──
def test_totp_code_single_use(client):
    secret = totp.new_secret()
    _user("e53-mfa", "Mfa-pass-53", mfa_secret=secret)
    form = {"grant_type": "password", "username": "e53-mfa", "password": "Mfa-pass-53", "client_id": "test",
            "otp": totp.code_at(secret)}
    assert client.post("/idp/token", data=form).status_code == 200
    again = client.post("/idp/token", data=form)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
