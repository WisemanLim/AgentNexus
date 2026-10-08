"""업스트림 mTLS (FR-G02) — MCP 서버·원격 A2A 에이전트·LLM 공급자별 TLS 설정 → ssl.SSLContext.

대상 설정(`auth.tls` 또는 공급자 `options.tls`): {"ca_secret": 이름, "client_cert_secret": 이름, "verify": true,
"server_hostname": 선택}. 인증서·키 PEM 은 시크릿 저장소(kind=certificate)에만 두고, 클라이언트 키는 컨텍스트에 적재하는 순간만
권한 0600 임시 파일로 쓴 뒤 바로 지운다(ssl 모듈은 메모리 적재를 지원하지 않음).
게이트웨이 정책 `gateway.security.mtls.upstream`:
- disabled  — 클라이언트 인증서를 내지 않는다(CA 검증 설정은 유지)
- optional  — 설정된 대상만 mTLS (기본)
- required  — https + 클라이언트 인증서가 없는 업스트림 호출은 거부(fail closed)
"""
import hashlib
import os
import ssl
import tempfile
import time

from app.db import SessionLocal
from app.services import guards, secrets

_CACHE: dict[str, ssl.SSLContext] = {}
_MODE: dict = {"value": None, "at": 0.0}
MODE_TTL_S = 5.0  # 정책 변경은 같은 워커에선 즉시(invalidate), 다른 워커엔 최대 5초 뒤 반영 — 업스트림 호출마다 DB 를 읽지 않는다


class MtlsError(Exception):
    pass


def mode(db) -> str:
    if _MODE["value"] is None or time.monotonic() - _MODE["at"] > MODE_TTL_S:
        _MODE.update(value=(guards.security_settings(db).get("mtls") or {}).get("upstream", "optional"),
                     at=time.monotonic())
    return _MODE["value"]


def cached_mode() -> str | None:
    """신선한 캐시 값(없거나 만료면 None) — 설정 없는 대상의 빠른 경로 판단용 (DB·스레드풀 없이)."""
    return _MODE["value"] if _MODE["value"] is not None and time.monotonic() - _MODE["at"] <= MODE_TTL_S else None


def invalidate() -> None:
    _MODE.update(value=None, at=0.0)


def fast_none(tls_cfg: dict | None) -> bool:
    """TLS 설정이 없고 정책이 required 가 아니면 조회 없이 기본 연결."""
    m = cached_mode()
    return not tls_cfg and m is not None and m != "required"


def _build(ca_pem: str | None, cert_pem: str | None, verify: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cadata=ca_pem) if ca_pem else ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if cert_pem:
        fd, path = tempfile.mkstemp(prefix="anx-mtls-", suffix=".pem")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(cert_pem)
            ctx.load_cert_chain(path)  # 인증서 + 개인키 (같은 PEM)
        finally:
            os.unlink(path)
    return ctx


def context_for(tls: dict | None, target: str, url: str) -> ssl.SSLContext | None:
    """대상 TLS 설정·게이트웨이 정책 → SSLContext(또는 None = httpx 기본 검증). 정책 위반이면 MtlsError."""
    tls = tls or {}
    with SessionLocal() as db:
        m = mode(db)
        has_cert = bool(tls.get("client_cert_secret"))
        if m == "required" and not (url.startswith("https://") and has_cert):
            raise MtlsError(f"mTLS 필수 정책: {target} 업스트림에 https + 클라이언트 인증서가 설정되지 않았습니다.")
        if m == "required" and tls.get("verify") is False:  # 서버 검증을 끄면 상호 인증의 한쪽이 빠진다 (D21-⑤)
            raise MtlsError(f"mTLS 필수 정책: {target} 의 서버 인증서 검증(verify)을 끌 수 없습니다.")
        if not tls or not url.startswith("https://"):
            return None
        try:
            ca = secrets.require_plain(db, tls["ca_secret"]) if tls.get("ca_secret") else None
            cert = secrets.require_plain(db, tls["client_cert_secret"]) if has_cert and m != "disabled" else None
        except Exception as exc:  # noqa: BLE001 — 시크릿 없음·복호화 실패
            raise MtlsError(f"{target}: TLS 시크릿을 읽을 수 없습니다 ({type(exc).__name__}).") from exc
    verify = tls.get("verify", True) is not False
    key = hashlib.sha256(f"{ca}|{cert}|{verify}".encode()).hexdigest()
    if key not in _CACHE:
        try:
            _CACHE[key] = _build(ca, cert, verify)
        except (ssl.SSLError, ValueError, OSError) as exc:
            raise MtlsError(f"{target}: TLS 인증서 형식이 올바르지 않습니다 ({exc}).") from exc
    return _CACHE[key]


def client_kwargs(ctx: ssl.SSLContext | None) -> dict:
    return {"verify": ctx} if ctx is not None else {}


def grpc_credentials(tls: dict | None, target: str, url: str):
    """원격 gRPC(`grpcs://`) 채널 자격 증명 — 같은 대상 TLS 설정(사설 CA·클라이언트 인증서)과 게이트웨이 mTLS 정책 (D19-②).
    클라이언트 인증서 시크릿은 인증서 + 개인키 PEM 한 덩어리(HTTP 와 같은 형식)."""
    import re

    import grpc
    tls = tls or {}
    with SessionLocal() as db:
        m = mode(db)
        has_cert = bool(tls.get("client_cert_secret"))
        if m == "required" and not (url.startswith("grpcs://") and has_cert):
            raise MtlsError(f"mTLS 필수 정책: {target} 업스트림에 grpcs + 클라이언트 인증서가 설정되지 않았습니다.")
        try:
            ca = secrets.require_plain(db, tls["ca_secret"]) if tls.get("ca_secret") else None
            pem = secrets.require_plain(db, tls["client_cert_secret"]) if has_cert and m != "disabled" else None
        except Exception as exc:  # noqa: BLE001
            raise MtlsError(f"{target}: TLS 시크릿을 읽을 수 없습니다 ({type(exc).__name__}).") from exc
    key = chain = None
    if pem:
        key_m = re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", pem, re.S)
        certs = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
        if not key_m or not certs:
            raise MtlsError(f"{target}: 클라이언트 인증서 PEM 에 인증서·개인키가 모두 있어야 합니다.")
        key, chain = key_m.group(0).encode(), "\n".join(certs).encode()
    return grpc.ssl_channel_credentials(root_certificates=ca.encode() if ca else None, private_key=key, certificate_chain=chain)

