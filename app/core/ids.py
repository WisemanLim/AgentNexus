"""식별자·해시 유틸."""
import hashlib
import json
import re
import secrets
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s) or "item"


def token_urlsafe(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj) -> str:
    """해시·서명용 정규 JSON (키 정렬, 공백 없음, 비ASCII 유지)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
