"""TOTP (RFC 6238 · HMAC-SHA1 · 30초 · 6자리) — 첫 실행 관리자 MFA (SCR-A05), 표준 라이브러리만."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP, DIGITS = 30, 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, t: float | None = None) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((time.time() if t is None else t) // STEP)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS).zfill(DIGITS)


def verify(secret: str, code: str, window: int = 1) -> bool:
    """앞뒤 window 스텝(±30초)까지 허용 — 시계 오차."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return False
    now = time.time()
    return any(hmac.compare_digest(code_at(secret, now + i * STEP), code) for i in range(-window, window + 1))


def uri(secret: str, account: str, issuer: str = "AgentNexus") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"
