"""토큰 서명·검증, 비밀번호·토큰 해시."""
import base64
import hashlib
import hmac
import secrets
import time
import uuid

import jwt

from app.core import keys
from app.core.config import settings

API_AUDIENCE = "agentnexus"


def issuer() -> str:
    return f"{settings.public_base_url.rstrip('/')}/idp"


def sign(claims: dict, ttl: int, typ: str = "at+jwt") -> tuple[str, dict]:
    now = int(time.time())
    body = {"iss": issuer(), "iat": now, "nbf": now, "exp": now + ttl, "jti": uuid.uuid4().hex, **claims}
    token = jwt.encode(body, keys.signing_key(), algorithm="RS256", headers={"kid": keys.kid(), "typ": typ})
    return token, body


def verify_local(token: str, audience: str | list[str] | None = API_AUDIENCE) -> dict:
    """내장 issuer 가 서명한 토큰 검증 (aud 필수) — 키는 kid 로(회전 직후 유예 중인 직전 키 포함, D21-③)."""
    cands = keys.verification_keys(jwt.get_unverified_header(token).get("kid"))
    if not cands:
        raise jwt.InvalidTokenError("알 수 없는 서명 키(kid)")
    pub = cands[0]
    opts = {"require": ["exp", "iat", "sub", "iss"]}
    if audience is None:
        opts["verify_aud"] = False
    return jwt.decode(token, pub, algorithms=["RS256"], audience=audience, issuer=issuer(), options=opts,
                      leeway=5)


def unverified_claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


# ── 비밀번호 (scrypt) ──
# OWASP Password Storage 의 scrypt 동급 조합 중 메모리 16MiB 인 N=2^14·r=8·p=5 (D17-④) — 동시 로그인 시 메모리 폭증 없이 연산 강도만
# 올린다. 형식 `scrypt$n=…,r=…,p=…$salt$dk`, 이전 형식 `scrypt$salt$dk`(N=2^14·r=8·p=1)도 검증하고 로그인 성공 시 재해싱한다.
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 5}
_LEGACY = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    params = ",".join(f"{k}={v}" for k, v in SCRYPT.items())
    return f"scrypt${params}$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def _parse(stored: str) -> tuple[dict, bytes, bytes] | None:
    parts = stored.split("$")
    if parts[0] != "scrypt" or len(parts) not in (3, 4):
        return None
    params = _LEGACY if len(parts) == 3 else {k: int(v) for k, v in (kv.split("=", 1) for kv in parts[1].split(","))}
    return params, base64.b64decode(parts[-2]), base64.b64decode(parts[-1])


def check_password(password: str, stored: str | None) -> bool:
    parsed = _parse(stored) if stored else None
    if parsed is None:
        return False
    params, salt, expected = parsed
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, n=params["n"], r=params["r"], p=params["p"])
    return hmac.compare_digest(dk, expected)


def needs_rehash(stored: str | None) -> bool:
    parsed = _parse(stored) if stored else None
    return parsed is None or parsed[0] != SCRYPT


# ── 불투명 토큰 (PAT·클라이언트 키·리프레시) ──
def opaque(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pkce_s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
