"""Epic 43 — 정책 하드닝 (D17-④): readOnlyHint 는 trusted 서버만 · 클라이언트 푸시 URL 사설망 거부(prod) ·
scrypt 강도 상향 + 로그인 시 재해싱 · refresh token 재사용 탐지(계열 폐기).
"""
import base64
import hashlib
import secrets
import uuid
from datetime import timedelta

import httpx
import pytest

from app.core import security
from app.core.config import settings
from app.core.errors import ApiError
from app.core.http import register_mount
from app.db import SessionLocal, utcnow
from app.models import AuditEvent, CatalogItem, McpServer, RefreshToken, User
from app.services import governance, identity
from app.services.a2a_gateway import push_url_problem
from test.test_a2a import call, msg

READ_ONLY_WRITE = {"name": "update_record", "annotations": {"readOnlyHint": True}}


# ── C43-01 readOnlyHint — 위험을 낮추는 힌트는 trusted 서버만, destructiveHint 는 언제나 ──
def test_readonly_hint_trusted_only():
    assert governance.classify_risk(READ_ONLY_WRITE, trusted=True) == "low"
    assert governance.classify_risk(READ_ONLY_WRITE, trusted=False) == "high"  # 이름 규칙으로 판정
    assert governance.classify_risk({"name": "lookup", "annotations": {"destructiveHint": True}}, trusted=False) == "high"
    with SessionLocal() as db:
        for trust, expect in (("trusted", "low"), ("untrusted", "high")):
            sid = f"e43-{trust}"
            item = CatalogItem(id=f"cat_{sid}", type="mcp_server", name=f"io.e43/{trust}", status="approved",
                               item_metadata={"_anx_review": {"trust": trust}})
            db.add(item)
            db.add(McpServer(id=sid, catalog_item_id=item.id, name=sid, transport="streamable-http",
                             endpoint="http://e43.test/mcp", server_json={}, auth={}))
            db.flush()
            rule = governance.default_rules(db, sid, [READ_ONLY_WRITE])[0]
            assert (rule.risk, rule.approval_required) == (expect, expect == "high"), trust
        db.rollback()


# ── C43-02 클라이언트 푸시 URL — http(s) 만, prod 에서 사설·루프백·링크 로컬 거부 (시험 마운트는 통과) ──
def test_push_url_guard(client, H, monkeypatch):
    assert push_url_problem("http://10.0.0.5/hook") is None  # test 환경은 사설망 허용
    assert "http(s)" in push_url_problem("ftp://hooks.example/x")
    monkeypatch.setattr(settings, "app_env", "prod")
    for url in ("http://10.0.0.5/hook", "http://127.0.0.1:9/hook", "http://[::1]/h", "http://169.254.169.254/x",
                "http://localhost/hook", "http://192.168.1.2/x"):
        assert push_url_problem(url), url
    assert push_url_problem("https://93.184.216.34/hook") is None
    register_mount("http://e43-hook.test", async_transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert push_url_problem("http://e43-hook.test/hook") is None
    t = call(client, H["user"], "message/send", {"message": msg("분석")}).json()["result"]
    res = call(client, H["user"], "tasks/pushNotificationConfig/set",
               {"taskId": t["id"], "pushNotificationConfig": {"url": "http://10.1.2.3/hook"}}).json()
    assert res["error"]["code"] == -32602 and res["error"]["data"]["code"] == "push_url_rejected"
    mine = call(client, H["user"], "message/send", {"message": msg("휴가 정책")}, path="/a2a/platform").json()["result"]
    plat = call(client, H["user"], "tasks/pushNotificationConfig/set",  # 내 Task 라도 사설 수신처는 거부 (44th 소유권 검사 뒤)
                {"taskId": mine["id"], "pushNotificationConfig": {"url": "http://127.0.0.1/hook"}}, path="/a2a/platform").json()
    assert plat["error"]["code"] == -32602


# ── C43-03 scrypt — 새 형식에 파라미터 기록 · 이전 형식도 검증 · 로그인 성공 시 재해싱 ──
def _legacy_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def test_scrypt_upgrade_and_rehash(client):
    h = security.hash_password("Strong-pass-43")
    assert h.startswith("scrypt$n=16384,r=8,p=5$") and security.check_password("Strong-pass-43", h)
    assert not security.check_password("wrong", h) and not security.needs_rehash(h)
    legacy = _legacy_hash("Legacy-pass-43")
    assert security.check_password("Legacy-pass-43", legacy) and security.needs_rehash(legacy)
    uid = "usr_e43_" + uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add(User(id=uid, sub=uid, issuer=security.issuer(), username=uid, name=uid, roles=["user"], status="active",
                    password_hash=legacy))
        db.commit()
    tok = client.post("/idp/token", data={"grant_type": "password", "username": uid, "password": "Legacy-pass-43",
                                          "client_id": "test"})
    assert tok.status_code == 200, tok.text
    with SessionLocal() as db:
        upgraded = db.get(User, uid).password_hash
        assert upgraded.startswith("scrypt$n=16384,r=8,p=5$") and security.check_password("Legacy-pass-43", upgraded)


# ── C43-04 refresh 재사용 — 회전된 토큰 재제시 → 같은 계열 전부 폐기 + 감사 · 거의 동시(유예) 재제시는 계열 유지 ──
def test_refresh_reuse_revokes_family():
    with SessionLocal() as db:
        user = db.get(User, "usr_dev")
        first = identity.issue_user_tokens(db, user, "agentnexus-console")
        second = identity.refresh(db, first["refresh_token"], "agentnexus-console")
        with pytest.raises(ApiError):  # 방금 회전된 토큰(유예 안) — 거부만, 계열은 유지
            identity.refresh(db, first["refresh_token"], "agentnexus-console")
        third = identity.refresh(db, second["refresh_token"], "agentnexus-console")
        live = db.query(RefreshToken).filter(RefreshToken.token_hash == security.token_hash(third["refresh_token"])).first()
        assert live.revoked_at is None and live.family_id
        fam = live.family_id
        old = db.query(RefreshToken).filter(RefreshToken.token_hash == security.token_hash(first["refresh_token"])).first()
        old.revoked_at = utcnow() - timedelta(seconds=60)
        db.commit()
        with pytest.raises(ApiError) as exc:  # 유예 밖 재사용 = 유출 신호
            identity.refresh(db, first["refresh_token"], "agentnexus-console")
        assert exc.value.code == "invalid_grant"
        db.expire_all()
        assert db.query(RefreshToken).filter(RefreshToken.family_id == fam, RefreshToken.revoked_at.is_(None)).count() == 0
        ev = db.query(AuditEvent).filter(AuditEvent.action == "auth.refresh_reuse").order_by(AuditEvent.seq.desc()).first()
        assert ev.data["family"] == fam and ev.data["revoked"] >= 1
        with pytest.raises(ApiError):
            identity.refresh(db, third["refresh_token"], "agentnexus-console")
