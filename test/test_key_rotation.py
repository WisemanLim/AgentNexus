"""Epic 58 — 키 회전 (D21-③): 관리 명령(수동) — 서명 키(직전 키 유예·공개키 보관) · 암호화 키(재암호화·이전 키 제거) ·
권한·확인·env 모드 거절 · 다른 워커 캐시 갱신(generation) · CLI."""
import json
import time

import jwt
from sqlalchemy import select
import pytest

from app.core import keys, security
from app.core.config import settings
from app.db import SessionLocal
from app.models import Secret
from app.services import secrets
from test.conftest import login
from test.test_cli import cli_env, run  # noqa: F401 — 픽스처


def _rotate(client, H, kind: str, who: str = "admin", **body):
    return client.post("/api/v1/admin/keys/rotate", json={"kind": kind, "confirm": "ROTATE", **body}, headers=H[who])


# ── C58-01 서명 키 — 새 kid · 직전 키는 유예 동안 토큰 검증·JWKS 에 · 유예 뒤 토큰 거부 · 백업·보고서 서명은 보관 공개키로 계속 검증 ──
def test_signing_rotation(client, H):
    old_kid = keys.kid()
    old_token = login(client, "user")
    long_lived = jwt.encode({"sha256": "x", "iss": "agentnexus-backup"}, keys.signing_key(), algorithm="RS256",
                            headers={"kid": old_kid})
    res = _rotate(client, H, "signing")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["previous_kid"] == old_kid and out["kid"] != old_kid
    kids = [k["kid"] for k in keys.jwks()["keys"]]
    assert kids[0] == out["kid"] and old_kid in kids
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 200  # 유예 중
    new_token = login(client, "user")
    assert jwt.get_unverified_header(new_token)["kid"] == out["kid"]
    prev_file = keys._keys_dir() / keys.SIGNING_PREV
    saved = prev_file.read_text()
    prev_file.write_text(json.dumps([{**e, "until": time.time() - 1} for e in json.loads(saved)]))  # 유예 끝
    keys._bump()
    assert [k["kid"] for k in keys.jwks()["keys"]] == [out["kid"]]
    with pytest.raises(jwt.InvalidTokenError):
        security.verify_local(old_token)
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    assert keys.decode_long_lived(long_lived, algorithms=["RS256"])["iss"] == "agentnexus-backup"  # 보관 공개키
    prev_file.write_text(saved)  # 다른 시험의 세션 토큰(회전 전 키)이 계속 쓰이게 유예를 되돌린다
    keys._bump()


# ── C58-02 암호화 키 — 회전하면 저장 암호문을 새 키로 재암호화, 이전 키는 남았다가 drop_previous 로 제거 · 값은 그대로 ──
def test_encryption_rotation(client, H):
    with SessionLocal() as db:
        secrets.put(db, "e58/value", "e58-secret-value", kind="token")
        db.commit()
        before = db.scalars(select(Secret).where(Secret.name == "e58/value")).first().ciphertext
    res = _rotate(client, H, "encryption")
    assert res.status_code == 200 and res.json()["failed"] == 0 and res.json()["secrets"] >= 1, res.text
    assert client.get("/api/v1/admin/keys", headers=H["admin"]).json()["encryption"]["keys"] == 2
    with SessionLocal() as db:
        row = db.scalars(select(Secret).where(Secret.name == "e58/value")).first()
        assert row.ciphertext != before and secrets.get_plain(db, "e58/value") == "e58-secret-value"
    dropped = client.post("/api/v1/admin/keys/reencrypt", params={"drop_previous": "true"}, headers=H["admin"]).json()
    assert dropped["failed"] == 0 and dropped["dropped"] == 1
    assert client.get("/api/v1/admin/keys", headers=H["admin"]).json()["encryption"]["keys"] == 1
    with SessionLocal() as db:
        assert secrets.get_plain(db, "e58/value") == "e58-secret-value"


# ── C58-03 경계 — 관리자만 · ROTATE 확인 · env 로 주입된 키는 앱이 회전하지 않음(409) ──
def test_rotation_guards(client, H, monkeypatch):
    assert _rotate(client, H, "signing", who="operator").json()["code"] == "admin_only"
    assert client.post("/api/v1/admin/keys/rotate", json={"kind": "signing"}, headers=H["admin"]).json()["code"] == "confirm_required"
    monkeypatch.setattr(settings, "encryption_key", keys.Fernet.generate_key().decode())
    res = _rotate(client, H, "encryption")
    assert res.status_code == 409 and res.json()["code"] == "keys_env_managed"


# ── C58-04 다른 워커 — 회전은 generation 파일로 알린다(캐시를 직접 비우지 않아도 다음 호출이 새 키) ──
def test_other_worker_picks_up_rotation(monkeypatch):
    before = keys.kid()
    keys.signing_key()  # 이 워커 캐시를 채운다

    def other_worker_bump():  # 다른 워커의 회전 — 파일만 바뀌고 이 프로세스 캐시는 비우지 않는다
        time.sleep(0.01)
        (keys._keys_dir() / "generation").write_text(str(time.time_ns()))
    monkeypatch.setattr(keys, "_bump", other_worker_bump)
    out = keys.rotate_signing(3600)
    assert keys.kid() == out["kid"] != before and before in [k["kid"] for k in keys.jwks()["keys"]]


# ── C58-05 CLI — nexus keys status · rotate --yes ──
def test_cli_keys(tokens, cli_env):  # noqa: F811
    st = run("keys", "status", token=tokens["admin"])
    assert st.exit_code == 0 and '"kid"' in st.output
    rot = run("keys", "rotate", "--kind", "signing", "--yes", token=tokens["admin"])
    assert rot.exit_code == 0 and '"previous_kid"' in rot.output
