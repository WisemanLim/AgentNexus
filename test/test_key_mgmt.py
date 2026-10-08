"""Epic 62 — 키 관리 보강 (D22-②): generation 카운터(내용) · 회전 잠금 · env 2단계 회전(`*_NEXT`) · env 이전 키
공개키 PEM·검증 끝·폐기 kid · 회전 감사 선기록 · Helm rotationNonce."""
import os
import threading
from pathlib import Path

import jwt
import pytest
import yaml
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from sqlalchemy import select

from app.api import admin
from app.core import keys, security
from app.core.config import settings
from app.db import SessionLocal
from app.models import AuditEvent

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def envkeys(monkeypatch):
    """env 로 키를 준 것처럼 — 설정을 바꾸고 이 워커 캐시를 비운다(끝나면 되돌림)."""
    def apply(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(settings, k, v)
        keys._signing.cache_clear()
        keys._fernet.cache_clear()
    yield apply
    monkeypatch.undo()
    keys._signing.cache_clear()
    keys._fernet.cache_clear()


def _pair() -> tuple[str, str, str]:
    """(개인키 PEM, 공개키 PEM, kid)."""
    pem = keys._new_rsa_pem()
    pub = serialization.load_pem_private_key(pem, password=None).public_key()
    pub_pem = pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return pem.decode(), pub_pem, keys._kid_of(pub)


def _token(envkeys, pem: str) -> str:
    envkeys(signing_key_pem=pem)
    return security.sign({"sub": "usr_e62", "aud": security.API_AUDIENCE}, 600)[0]


# ── C62-01 generation — 파일 내용(카운터)으로: mtime 이 그대로여도(거친 mtime 파일시스템) 다른 워커가 새 세대를 본다 ──
def test_generation_is_content_counter():
    g0 = keys._generation()
    keys._bump()
    assert keys._generation() == g0 + 1
    gfile = keys._keys_dir() / "generation"
    ns = gfile.stat().st_mtime_ns
    gfile.write_text(str(g0 + 2))  # 다른 워커의 회전 — 같은 mtime 틱
    os.utime(gfile, ns=(ns, ns))
    assert keys._generation() == g0 + 2
    keys._bump()


# ── C62-02 회전 잠금 — 동시에 두 번 회전해도 새 키가 사라지지 않는다(k0→k1→k2, 직전 키 둘 다 유예) ──
def test_concurrent_rotation_keeps_every_key():
    before = keys.kid()
    gate, outs = threading.Barrier(2), []

    def go():
        gate.wait()
        outs.append(keys.rotate_signing(3600))
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    prev = {o["previous_kid"] for o in outs}
    assert len(prev) == 2 and before in prev and keys.kid() not in prev
    assert prev <= {keys._kid_of(p) for p in keys._active_previous()}


# ── C62-03 env 2단계 회전 — 다음 키를 먼저 배포하면 롤링 중 새·옛 파드가 서로의 토큰·암호문을 받는다 ──
def test_env_next_keys(envkeys):
    a, _, kid_a = _pair()
    b, _, kid_b = _pair()
    new_pod_token = _token(envkeys, b)  # 이미 B 로 올라간 파드가 발급
    envkeys(signing_key_pem=a, signing_key_pem_next=b, signing_key_pem_previous="")  # 아직 옛 파드: 현재 A + 다음 B
    assert keys.kid() == kid_a and [k["kid"] for k in keys.jwks()["keys"]][:1] == [kid_a]
    assert kid_b in [k["kid"] for k in keys.jwks()["keys"]]
    assert security.verify_local(new_pod_token)["sub"] == "usr_e62"
    ka, kb = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    envkeys(encryption_key=kb, encryption_key_next="")
    ct = keys.fernet().encrypt(b"e62-secret")  # 새 파드가 저장
    envkeys(encryption_key=ka, encryption_key_next=kb)
    assert keys.fernet().decrypt(ct) == b"e62-secret"  # 옛 파드가 읽음
    assert Fernet(ka.encode()).decrypt(keys.fernet().encrypt(b"x")) == b"x"  # 쓰기는 아직 현재 키(A)
    assert keys.encryption_key_count() == 1  # 다음 키는 수에 넣지 않는다 (65th — CR4-02)


# ── C62-04 env 이전 키 — 공개키 PEM 도 받음 · 검증 끝(ISO) 뒤에는 토큰 거부·백업 서명은 계속 · 폐기 kid 는 어디서도 거부 ──
def test_env_previous_public_until_and_revoked(envkeys):
    a, _, _ = _pair()
    c, c_pub, kid_c = _pair()
    old_token = _token(envkeys, c)
    envkeys(signing_key_pem=c)
    backup = jwt.encode({"sha256": "x", "iss": "agentnexus-backup"}, keys.signing_key(), algorithm="RS256", headers={"kid": kid_c})
    envkeys(signing_key_pem=a, signing_key_pem_previous=c_pub)  # 이전 키는 공개키만
    assert security.verify_local(old_token)["sub"] == "usr_e62" and kid_c in [k["kid"] for k in keys.jwks()["keys"]]
    envkeys(signing_key_previous_until="2000-01-01T00:00:00Z")
    with pytest.raises(jwt.InvalidTokenError):
        security.verify_local(old_token)
    assert kid_c not in [k["kid"] for k in keys.jwks()["keys"]]
    assert keys.decode_long_lived(backup, algorithms=["RS256"])["iss"] == "agentnexus-backup"  # 백업 서명은 계속
    envkeys(signing_revoked_kids=f"other,{kid_c}")
    with pytest.raises(jwt.InvalidTokenError):
        keys.decode_long_lived(backup, algorithms=["RS256"])


# ── C62-05 회전 감사 — 회전 직후 기록(재암호화가 실패해도 남음) · env 키는 전용 예외 ──
def test_rotation_audited_before_reencrypt(client, H, monkeypatch, envkeys):
    def boom(db):
        raise RuntimeError("e62 reencrypt failed")
    monkeypatch.setattr(admin, "reencrypt", boom)
    with SessionLocal() as db:
        before = db.query(AuditEvent).filter(AuditEvent.action == "keys.rotate").count()
    with pytest.raises(RuntimeError, match="e62"):
        client.post("/api/v1/admin/keys/rotate", json={"kind": "encryption", "confirm": "ROTATE"}, headers=H["admin"])
    with SessionLocal() as db:
        rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "keys.rotate").order_by(AuditEvent.seq)).all()
    assert len(rows) == before + 1 and rows[-1].target == "encryption"
    monkeypatch.undo()
    assert client.post("/api/v1/admin/keys/reencrypt", params={"drop_previous": "true"}, headers=H["admin"]).json()["failed"] == 0
    envkeys(encryption_key=Fernet.generate_key().decode())
    with pytest.raises(keys.EnvManaged):
        keys.rotate_encryption()


# ── C62-06 Helm — existingSecret 키 교체는 rotationNonce 로 재기동 (helm-check 가 ci/ha 에서 확인) ──
def test_helm_rotation_nonce():
    values = yaml.safe_load((ROOT / "deploy/helm/agentnexus/values.yaml").read_text())
    assert values["secrets"]["rotationNonce"] == "" and "signingKeyPemNext" in values["secrets"]
    for tpl in ("api.yaml", "demo.yaml"):
        assert "agentnexus/rotation-nonce" in (ROOT / "deploy/helm/agentnexus/templates" / tpl).read_text(), tpl
    assert "rotation-nonce" in (ROOT / "deploy/helm/check-rendered.py").read_text()
