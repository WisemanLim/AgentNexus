"""서명 키(RS256, JWKS)·암호화 키(Fernet) — env 우선, 없으면 data_dir/keys 에 생성·보존. 회전은 아래 rotate_* (D21-③).

여러 워커가 동시에 생성하지 않도록 O_EXCL 로 만들고, 이미 있으면 읽는다(entrypoint 시드 단계에서 선생성).
"""
import base64
import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings


def _keys_dir() -> Path:
    p = Path(settings.data_dir) / "keys"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_or_create(path: Path, factory) -> bytes:
    if path.exists():
        return path.read_bytes()
    data = factory()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return data


def _new_rsa_pem() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _configured(value: str) -> str:
    """빈 값·CHANGE_ME placeholder 는 미설정으로 본다 (→ data_dir/keys 자동 생성)."""
    v = value.strip()
    return "" if v in ("", "CHANGE_ME") else v


# ── 회전 (D21-③) — 파일 모드(data_dir/keys)는 관리 명령으로 회전, env 모드는 *_PREVIOUS env 로 운영자가 회전.
# 워커·파드마다 캐시하므로 회전은 keys/generation 파일을 바꿔 알린다(호출마다 stat 1회 — 바뀌면 다시 읽음).
SIGNING_PREV = "signing.prev.json"       # 이전 서명 키들 [{pem, until}] — 각자 유예(until) 동안 JWKS·토큰 검증에 남김
SIGNING_ARCHIVE = "signing.archive.pem"  # 회전한 공개키 전부 — 백업·감사 보고서 서명은 오래 검증해야 한다
FERNET_PREV = "secrets.prev.key"         # 이전 암호화 키들(줄 단위) — 재암호화가 끝나면 비운다


class EnvManaged(RuntimeError):
    """키가 env(Secret)로 주입돼 앱이 회전하지 않는다 — 운영자가 Secret·`*_PREVIOUS`·`*_NEXT` 로 회전."""


def _generation() -> int:
    """회전 세대 = keys/generation 의 카운터(내용). mtime 은 파일시스템에 따라 같은 틱의 두 회전을 구분하지 못한다 (D22-②)."""
    try:
        return int((_keys_dir() / "generation").read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def _bump() -> None:
    _write_atomic(_keys_dir() / "generation", str(_generation() + 1).encode())
    _signing.cache_clear()
    _fernet.cache_clear()


@contextmanager
def _rotation_lock():
    """회전·이전 키 제거는 한 번에 하나 — 동시에 돌면 둘 다 같은 현재 키를 직전 키로 옮겨 새 키 하나가 사라진다 (D22-②)."""
    fd = os.open(_keys_dir() / "rotate.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _append_private(path: Path, data: bytes) -> None:
    """키 파일에 덧붙이기 — 처음부터 0600 (umask 로 만든 뒤 chmod 하는 틈 없이, 이전 버전이 만든 파일도 0600 으로)."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "ab") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(data)


def env_managed() -> dict:
    return {"signing": bool(_configured(settings.signing_key_pem)), "encryption": bool(_configured(settings.encryption_key))}


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _kid_of(pub) -> str:
    der = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


def _pems(blob: bytes) -> list[bytes]:
    marker = b"-----END"
    out, cur = [], b""
    for line in blob.splitlines(keepends=True):
        cur += line
        if line.startswith(marker):
            out.append(cur)
            cur = b""
    return [x for x in out if x.strip()]


def _prev_entries() -> list[dict]:
    f = _keys_dir() / SIGNING_PREV
    return json.loads(f.read_text()) if f.exists() else []


def _public(blob: bytes):
    """PEM → 공개키 — env 이전·다음 키는 공개키 PEM 도 받는다(검증만 하므로 개인키를 둘 필요 없음, D22-②).
    서명은 RS256 만 — 다른 종류(EC 등)는 JWKS·검증에서 나중에 터지지 않게 읽을 때 거부 (65th)."""
    pub = serialization.load_pem_private_key(blob, password=None).public_key() if b"PRIVATE KEY" in blob \
        else serialization.load_pem_public_key(blob)
    if not isinstance(pub, rsa.RSAPublicKey):
        raise ValueError(f"서명 키는 RSA 여야 합니다 (받은 키: {type(pub).__name__})")
    return pub


def _env_until() -> float | None:
    v = settings.signing_key_previous_until.strip()
    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() if v else None


@lru_cache(maxsize=2)
def _signing(gen: int):
    """(현재 개인키, 이전 [(공개키, 토큰 검증 끝 epoch | None)], 보관 공개키, 다음 공개키) — 폐기 kid 는 모두에서 뺀다."""
    pem = _configured(settings.signing_key_pem).encode() or _read_or_create(_keys_dir() / "signing.pem", _new_rsa_pem)
    current = serialization.load_pem_private_key(pem, password=None)
    revoked = {k.strip() for k in settings.signing_revoked_kids.split(",") if k.strip()}
    if _kid_of(current.public_key()) in revoked:  # 현재 키 폐기는 회전 없이는 뜻이 없다 — 조용히 넘기지 않고 멈춘다 (65th)
        raise RuntimeError("현재 서명 키가 SIGNING_REVOKED_KIDS 에 있습니다 — 먼저 새 키로 회전한 뒤 폐기하세요 (SECURITY.md 키 회전)")
    keep = lambda pub: _kid_of(pub) not in revoked  # noqa: E731
    until = _env_until()
    env_prev = [_public(b) for b in _pems(_configured(settings.signing_key_pem_previous).encode())]
    previous = [(pub, until) for pub in env_prev]
    d = _keys_dir()
    for e in _prev_entries():
        previous.append((serialization.load_pem_public_key(e["pub"].encode()), float(e["until"])))
    archive = [serialization.load_pem_public_key(b) for b in _pems((d / SIGNING_ARCHIVE).read_bytes())] \
        if (d / SIGNING_ARCHIVE).exists() else []
    archive += env_prev  # env 이전 키도 토큰 유예 뒤 백업·보고서 서명 검증에는 남는다(폐기 kid 로만 끊음)
    nxt = [_public(b) for b in _pems(_configured(settings.signing_key_pem_next).encode())]
    return (current, [(p, u) for p, u in previous if keep(p)], [p for p in archive if keep(p)], [p for p in nxt if keep(p)])


def signing_key():
    return _signing(_generation())[0]


def kid() -> str:
    return _kid_of(signing_key().public_key())


def _active_previous() -> list:
    now = time.time()
    return [pub for pub, until in _signing(_generation())[1] if until is None or until > now]


def verification_keys(kid_: str | None, long_lived: bool = False) -> list:
    """서명 검증용 공개키 후보 — 현재 키·유예 중인 직전 키·다음 키(토큰), long_lived 면 회전한 공개키 보관본까지
    (백업·보고서 서명). 폐기 kid 는 없다. kid 가 있으면 그 키만(없으면 빈 목록 = 모르는 키)."""
    current, _, archive, nxt = _signing(_generation())
    cands = [current.public_key(), *_active_previous(), *nxt, *(archive if long_lived else [])]
    return [k for k in cands if _kid_of(k) == kid_] if kid_ else cands


def decode_long_lived(token: str, **kw) -> dict:
    """백업·감사 보고서처럼 오래 남는 서명 검증 — 회전 전 공개키(보관본)까지 kid 로 찾는다 (D21-③)."""
    import jwt
    cands = verification_keys(jwt.get_unverified_header(token).get("kid"), long_lived=True)
    if not cands:
        raise jwt.InvalidTokenError("알 수 없는 서명 키(kid)")
    return jwt.decode(token, cands[0], **kw)


def jwks() -> dict:
    out = []
    for pub in [signing_key().public_key(), *_active_previous(), *_signing(_generation())[3]]:  # 다음 키도 미리 공개 (D22-②)
        nums = pub.public_numbers()
        out.append({"kty": "RSA", "use": "sig", "alg": "RS256", "kid": _kid_of(pub), "n": _b64u(nums.n), "e": _b64u(nums.e)})
    return {"keys": out}


@lru_cache(maxsize=2)
def _fernet(gen: int) -> MultiFernet:
    if _configured(settings.encryption_key):
        primary = _configured(settings.encryption_key).encode()
    else:
        primary = _read_or_create(_keys_dir() / "secrets.key", Fernet.generate_key).strip()
    older = [k.strip().encode() for k in _configured(settings.encryption_key_previous).split(",") if k.strip()]
    older += [k.strip().encode() for k in _configured(settings.encryption_key_next).split(",") if k.strip()]  # 다음 키 — 복호화만 (D22-②)
    f = _keys_dir() / FERNET_PREV
    if f.exists():
        older += [ln.strip() for ln in f.read_bytes().splitlines() if ln.strip()]
    return MultiFernet([Fernet(primary), *[Fernet(k) for k in older]])  # 암호화는 첫 키, 복호화는 모두


def encryption_fingerprint() -> str:
    """현재(쓰기) 암호화 키 지문 — 재암호화가 어느 키로 썼는지 확인용 (키 자체는 노출하지 않음, 65th)."""
    primary = _configured(settings.encryption_key) or (_keys_dir() / "secrets.key").read_bytes().strip().decode()
    return hashlib.sha256(primary.encode()).hexdigest()[:12]


def encryption_key_count() -> int:
    """현재 + 이전(env·파일) 암호화 키 수 — 1 이면 재암호화가 끝나 이전 키가 없다."""
    f = _keys_dir() / FERNET_PREV
    files = len([ln for ln in f.read_bytes().splitlines() if ln.strip()]) if f.exists() else 0
    # 다음 키(*_NEXT)는 아직 아무것도 암호화하지 않았다 — 재암호화 판단용 수에서 뺀다 (65th)
    return 1 + files + len([k for k in _configured(settings.encryption_key_previous).split(",") if k.strip()])


def fernet() -> MultiFernet:
    return _fernet(_generation())


def rotate_signing(grace_s: int) -> dict:
    """새 서명 키 — 직전 키는 grace_s 동안 JWKS·토큰 검증에 남고, 공개키는 보관본에 영구히(백업·보고서 서명 검증)."""
    if env_managed()["signing"]:
        raise EnvManaged("signing")
    d = _keys_dir()
    with _rotation_lock():
        old_pem = (d / "signing.pem").read_bytes()
        old_pub = serialization.load_pem_private_key(old_pem, password=None).public_key()
        pub_pem = old_pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        _append_private(d / SIGNING_ARCHIVE, pub_pem)
        now = time.time()  # 유예 중인 이전 키는 모두 남긴다 — 유예 안에 두 번 회전해도 그 사이 토큰이 끊기지 않게 (공개키만 보관)
        keep = [e for e in _prev_entries() if float(e["until"]) > now] + [{"pub": pub_pem.decode(), "until": now + grace_s}]
        _write_atomic(d / SIGNING_PREV, json.dumps(keep).encode())
        _write_atomic(d / "signing.pem", _new_rsa_pem())
        _bump()
        return {"previous_kid": _kid_of(old_pub), "kid": kid(), "previous_valid_until": time.time() + grace_s}


def rotate_encryption() -> dict:
    """새 암호화 키 — 이전 키는 복호화용으로 남는다(재암호화 뒤 drop_previous_encryption)."""
    if env_managed()["encryption"]:
        raise EnvManaged("encryption")
    d = _keys_dir()
    with _rotation_lock():
        old = (d / "secrets.key").read_bytes().strip()
        _append_private(d / FERNET_PREV, old + b"\n")
        _write_atomic(d / "secrets.key", Fernet.generate_key())
        _bump()
        return {"previous_keys": len([ln for ln in (d / FERNET_PREV).read_bytes().splitlines() if ln.strip()])}


def drop_previous_encryption() -> int:
    f = _keys_dir() / FERNET_PREV
    with _rotation_lock():
        n = len([ln for ln in f.read_bytes().splitlines() if ln.strip()]) if f.exists() else 0
        if f.exists():
            f.unlink()
        _bump()
        return n


def ensure_keys() -> None:
    """entrypoint 에서 호출 — 워커 기동 전 키 파일 확정."""
    signing_key()
    fernet()
