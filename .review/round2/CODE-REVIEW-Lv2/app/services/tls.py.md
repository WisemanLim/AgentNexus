> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: 1차 리뷰 대상 아님(부록 A)

### 19. app/services/tls.py
**파일 요약 / File Summary**
- 역할 / Role: 업스트림 mTLS(FR-G02) — 대상별 TLS 설정(`auth.tls`/공급자 `options.tls`: `ca_secret`·`client_cert_secret`·`verify`)과 게이트웨이 정책(`gateway.security.mtls.upstream` = disabled|optional|required)을 합쳐 httpx 용 `ssl.SSLContext`(`context_for`) 또는 gRPC 채널 자격 증명(`grpc_credentials`)을 만든다. 정책 값은 워커별 5초 캐시.
- 진입점 거리 / Distance: 3 (API → `app/services/{mcp_gateway,a2a_gateway,providers}.py` → 이 파일. 정책 캐시 무효화는 `app/api/gateway_admin.py:577` 에서 직접 — 2)
- LOC: 122 (현재 `wc -l`) · 1차 INDEX 부록 A 기준 94(`.review/CODE-REVIEW-Lv2/INDEX.md:341`) → +28
- 주요 export: `MtlsError`, `mode`, `cached_mode`, `invalidate`, `fast_none`, `context_for`, `client_kwargs`, `grpc_credentials`, `MODE_TTL_S`
- 의존(내부): `app.db.SessionLocal`, `app.services.guards`(`security_settings`), `app.services.secrets`(`require_plain`)
- 외부 패키지: 표준 `ssl`·`tempfile`·`hashlib`, `grpcio`(지연 import, `uv.lock:689`)
- 부수효과 / Side effects: DB 읽기(정책·시크릿 복호화), **개인키를 임시 파일로 디스크에 썼다가 삭제**(L57-64), 프로세스 전역 `_CACHE`·`_MODE`
- 동시성 / Concurrency: 동기 함수 — 비동기 호출부는 `run_in_threadpool` 로 부른다(`app/services/a2a_gateway.py:81-82`, `app/services/mcp_gateway.py:190`). `app/services/providers.py:400` 은 동기 `llm.prepare`(스레드풀) 안에서 직접 호출. 전역 dict 는 락 없이 갱신(중복 생성 정도의 무해한 경합)
- 테스트 커버리지 흔적: `test/test_mtls.py:136`(`test_mcp_server_requires_client_certificate`)·`:157`(`test_required_policy_fails_closed_for_plain_upstreams`)·`:180`(LLM 공급자)·`:190`(A2A 카드), `test/test_lv2_hardening.py:76`(`test_outbound_grpcs_private_ca` — 사설 CA 없으면 실패·있으면 성공)
- 2차 변경 요약 / Changes since 1st review: 1차 라인 리뷰 대상이 아니어서 인용 원문 비교는 불가. 의도 문서로 확인되는 변경 = L97-121 `grpc_credentials` 신규(Epic 49 D19-②, `test/impl/49th/scenario.md:8` "원격 grpcs 신뢰") — `app/services/a2a_gateway.py:80-81` 이 `grpcs://` 에 사용.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/tls.py lines=1-22
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
```
**분석 / Analysis (Level 2)**
- L1-9: 설정 형식과 정책 3단계. 설계 근거 — 표준 `ssl` 모듈의 `load_cert_chain` 은 **파일 경로만** 받으므로(메모리 PEM 적재 API 없음) 개인키를 잠깐 디스크에 써야 한다는 제약을 docstring 이 명시한다.
- L17: 이 모듈은 자기 세션을 연다 — 호출부(비동기 게이트웨이)가 세션 없이 부를 수 있게 하려는 선택.
- L20 `_CACHE`: 같은 (CA·인증서·verify) 조합의 SSLContext 재사용. SSLContext 는 여러 연결·스레드가 공유해도 안전하다.
- L21-22 `_MODE`: 정책 값·읽은 시각. 5초 TTL 로 업스트림 호출마다 DB 를 읽지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L3-4: docstring 이 `server_hostname` 설정을 안내하지만 코드 어디에도 구현이 없다(`grep server_hostname app` → 이 docstring 한 곳). 운영자가 IP 로 접속하면서 인증서 이름을 맞추려고 이 키를 넣으면 **조용히 무시**되고 호스트 이름 검증 실패로 끝난다. 최소 수정: docstring 에서 제거하거나, httpx 는 `extensions={"sni_hostname": ...}`, gRPC 는 `grpc.ssl_target_name_override` 채널 옵션으로 구현.

```python path=app/services/tls.py lines=25-48
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
```
**분석 / Analysis (Level 2)**
- L25-26 `MtlsError`: 도메인 예외. 호출부가 각자 프로토콜 오류로 바꾼다 — A2A `-32003`/403(`app/services/a2a_gateway.py:83-84`), MCP `FORBIDDEN`(`app/services/mcp_gateway.py:192-193`), LLM `ProviderError(403, retryable=False)`(`app/services/providers.py:401-402`) → `llm._target` 이 후보에서 제외(`app/services/llm.py:140-141`). 세 경로 모두 **fail closed**.
- L29-33 `mode`: 정책 값 읽기(`guards.security_settings` = 기본값 + 저장값 병합, `app/services/guards.py:79-81`), 기본 `optional`. 여러 스레드가 동시에 만료를 보면 각자 읽고 덮어쓴다 — 결과가 같으므로 무해.
- L36-38 `cached_mode`: DB 없이 신선한 캐시만. L41-42 `invalidate`: 설정 저장 직후 같은 워커 캐시 무효화(`app/api/gateway_admin.py:577`).
- L45-48 `fast_none`: TLS 설정이 없고, 캐시된 정책이 신선하며 required 가 아니면 True → 호출부가 스레드풀 왕복 없이 기본 연결(`app/services/a2a_gateway.py:77`, `app/services/mcp_gateway.py:187`). 캐시가 만료면 False → 느린 경로(`context_for`)로 정책을 다시 읽는다. 흔한 실수: `m is not None` 조건을 빼면 첫 호출(캐시 없음)에 required 정책을 건너뛴다 — 이 코드는 그 함정을 피했다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L22·L30: 다른 워커에서 정책을 `required` 로 올린 직후 최대 5초 동안 이 워커는 평문·인증서 없는 업스트림 호출을 허용한다(설계상 수용, 주석에 명시). 즉시성이 필요하면 설정 변경 시각을 Valkey 에 두고 비교.

```python path=app/services/tls.py lines=51-65
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
```
**분석 / Analysis (Level 2)**
- L52: `create_default_context` 기본값 = `CERT_REQUIRED` + `check_hostname=True` + 안전한 최소 프로토콜(TLS 1.2+). `cadata` 를 주면 **그 CA 만** 신뢰하고 시스템 저장소는 적재하지 않는다(사설 CA 고정 효과). 주지 않으면 시스템 저장소.
- L53-55: `verify=false` 면 호스트 이름 검사를 **먼저** 끄고 `CERT_NONE` — 순서를 바꾸면 `ValueError`(check_hostname 이 켜진 채 CERT_NONE 불가). 흔한 실수 지점을 올바르게 처리했다.
- L57: `mkstemp` 는 POSIX 에서 이미 0600 으로 만들고 이름 경쟁(TOCTOU)이 없는 안전한 임시 파일 생성 API 다. L59 `fchmod` 는 중복이지만 무해(의도 명시).
- L60-62: 같은 PEM 에 인증서 + 개인키(`load_cert_chain(certfile)` 은 keyfile 생략 시 같은 파일에서 키를 찾는다).
- L63-64: 성공·실패 모두 `finally` 에서 삭제.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L57-64 키 디스크 노출: 임시 디렉터리는 기본 `/tmp` — Helm 에서는 `emptyDir`(기본 매체 = 노드 디스크, `deploy/helm/agentnexus/templates/api.yaml:38,74`)이다. 쓰기와 `unlink` 사이에 프로세스가 SIGKILL·OOM 으로 죽으면 개인키 파일이 남고, 삭제돼도 노드 디스크 블록에 잔존할 수 있다. 최소 수정: `/tmp` emptyDir 에 `medium: Memory`, 또는 `tempfile.mkstemp(dir="/dev/shm")`.
- [위험:낮음] L62 암호화된 개인키(`ENCRYPTED PRIVATE KEY`)를 넣으면 `password` 인자가 없어 OpenSSL 기본 암호 입력(TTY 프롬프트)이 시도된다(Python `ssl` 문서의 동작). 컨테이너에서는 즉시 실패 → L88-89 가 "인증서 형식이 올바르지 않습니다" 로 보고(원인 오도), 터미널에서 띄운 개발 서버라면 스레드풀 스레드가 입력을 기다리며 멈출 수 있다(가능성). 최소 수정: `password=lambda: b""` 를 넘겨 즉시 실패시키고 "암호화된 키는 지원하지 않음" 메시지.

```python path=app/services/tls.py lines=68-94
def context_for(tls: dict | None, target: str, url: str) -> ssl.SSLContext | None:
    """대상 TLS 설정·게이트웨이 정책 → SSLContext(또는 None = httpx 기본 검증). 정책 위반이면 MtlsError."""
    tls = tls or {}
    with SessionLocal() as db:
        m = mode(db)
        has_cert = bool(tls.get("client_cert_secret"))
        if m == "required" and not (url.startswith("https://") and has_cert):
            raise MtlsError(f"mTLS 필수 정책: {target} 업스트림에 https + 클라이언트 인증서가 설정되지 않았습니다.")
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
```
**분석 / Analysis (Level 2)**
- L71: 세션은 정책·시크릿을 읽는 동안만 연다. `mode` 가 캐시 적중이고 TLS 설정이 없으면(L76) 실제 쿼리 없이 끝난다(SQLAlchemy 세션은 첫 쿼리 때 연결을 빌린다).
- L74-75 required: `https://` + 클라이언트 인증서 시크릿 **이름**이 있어야 통과(시크릿 실재 여부는 L80 에서). 평문 `http://` 업스트림은 여기서 fail closed(`test/test_mtls.py:157`).
- L76-77: 설정이 없거나 https 가 아니면 None → httpx 기본 검증(시스템 저장소, `trust_env` 의 `SSL_CERT_FILE` 반영).
- L79-80: CA 는 정책과 무관하게 적재, 클라이언트 인증서는 `disabled` 가 아닐 때만(docstring 의 "CA 검증 설정은 유지").
- L81-82: 시크릿 없음·복호화 실패를 예외 **타입 이름만** 담아 변환 — 시크릿 내용이 메시지로 새지 않는다(좋은 습관).
- L83: JSON `false` 만 검증 해제 — `"false"` 문자열 등은 검증 유지(fail safe).
- L84-90: PEM 내용 해시를 캐시 키로 — 같은 설정을 쓰는 대상들이 SSLContext 하나를 공유. 형식 오류는 `MtlsError`.
- L93-94 `client_kwargs`: httpx `verify=SSLContext` 인자 형태로.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L74 + L83: `required` 는 "https + 클라이언트 인증서" 만 요구하고 **서버 인증서 검증 해제(`verify:false`)는 막지 않는다.** 보안 담당이 required 로 올려도, 대상 소유 개발자가 `auth.tls.verify=false`(자체 서명 회피)를 두면 중간자(MITM)가 아무 인증서로 끼어들어 요청 본문·Bearer 토큰을 본다. 최소 수정: `m == "required"` 이면 `verify is False` 를 `MtlsError` 로.
- [위험:낮음] L76-77: `optional` 에서 TLS 설정이 있는데 URL 이 `http://` 면 설정이 **조용히 무시**된다(운영자는 mTLS 로 붙는다고 믿음). 최소 수정: 경고 로그 또는 `MtlsError`.
- [위험:낮음] L84-90: `_CACHE` 에 상한·제거가 없다 — 인증서를 교체할 때마다 새 항목이 생기고 **이전 개인키를 담은 SSLContext 가 프로세스 수명 동안 메모리에 남는다.** 최소 수정: 대상(target)별 1개만 유지하거나 LRU(예: 64개).

```python path=app/services/tls.py lines=97-121
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
```
**분석 / Analysis (Level 2)**
- L97-99 (신규, Epic 49): gRPC 는 파일이 아니라 **바이트**로 자격 증명을 받으므로 임시 파일이 필요 없다 — 같은 PEM 덩어리를 정규식으로 개인키·인증서 체인으로 나눈다.
- L100-102: 지연 import(gRPC 를 쓰지 않는 배포의 import 비용 회피). `re` 를 함수 안에서 import 하는 것은 1차 INDEX §4.4 가 지적한 "함수 내 import 중복" 계열(스타일).
- L104-108: required 정책 — `grpcs://` + 인증서 이름 필수. `grpc://`(평문)는 여기서 거부된다. 호출 조건: `app/services/a2a_gateway.py:80` 이 `grpcs://` 일 때만 이 함수를, 그 밖에는 `context_for` 를 부른다(평문 `grpc://` + required 는 `context_for` L74 에서 거부).
- L109-113: HTTP 판과 같은 시크릿 읽기·오류 변환.
- L115-120: `-----BEGIN ... PRIVATE KEY-----`(PKCS#8·RSA·EC 모두) 첫 블록과 모든 인증서 블록. 둘 중 하나라도 없으면 거부.
- L121: `root_certificates=None` 이면 gRPC 가 **자체 번들 루트(roots.pem)** 를 쓴다 — OS 신뢰 저장소를 쓰는 HTTP 경로(L52)와 신뢰 기반이 다르다는 점이 흔한 혼동 지점(사내 루트를 OS 에만 넣으면 gRPC 는 실패).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L97-121: HTTP 판과 달리 `verify`(검증 해제)·`server_hostname` 을 지원하지 않는다 — `verify:false` 를 둔 grpcs 대상은 조용히 검증을 계속해 실패한다(안전한 방향이지만 설정 의미 불일치). 최소 수정: 지원하지 않는 키가 있으면 `MtlsError` 로 명시.
- [위험:낮음] L104-121: 캐시가 없어 gRPC 호출마다 DB 세션·시크릿 복호화(Fernet)·자격 증명 생성이 반복된다(`app/services/a2a_gateway.py:81` 이 호출마다 실행). 최소 수정: `context_for` 처럼 내용 해시 키 캐시.
- [위험:낮음] L116: `ENCRYPTED PRIVATE KEY` 도 정규식에 맞아 통과하지만 gRPC 는 암호화 키를 쓰지 못해 핸드셰이크 단계에서 불투명한 오류로 실패한다. 최소 수정: `ENCRYPTED` 포함 시 즉시 `MtlsError`.

## 파일 평가 / File Assessment
- 강점: (1) 세 업스트림 종류(MCP·A2A·LLM)와 HTTP·gRPC 가 한 정책·한 설정 형식을 공유하고, 정책 위반·시크릿 오류·형식 오류가 모두 호출 전 fail closed. (2) 임시 파일을 `mkstemp`+`finally unlink` 로 다루고 오류 메시지에 시크릿 내용 대신 예외 타입만 담는다. (3) `fast_none` 의 `m is not None` 조건으로 첫 호출에서 required 를 건너뛰는 함정을 피하면서 대부분의 호출을 DB·스레드풀 없이 처리.
- 위험: (1) [위험:낮음] required 정책이 `verify:false` 를 허용 (L74·L83). (2) [위험:낮음] 개인키 임시 파일이 노드 디스크(emptyDir)에 기록 (L57-64, `deploy/helm/agentnexus/templates/api.yaml:74`). (3) [위험:낮음] SSLContext 캐시 무제한·옛 키 잔류, gRPC 자격 증명 무캐시 (L84-90·L104-121), 미구현 `server_hostname` 문서 (L3-4).
- 개선 권고(영향순): (1) required 에서 `verify:false` 거부. (2) `/tmp` 를 메모리 매체로(또는 `/dev/shm`), 암호화 키는 즉시 거부. (3) 대상별 캐시(1개 유지)로 바꾸고 gRPC 도 캐시, `server_hostname` 은 구현하거나 문서에서 삭제.
- 파일 위험 등급 / Risk grade: 낮음 — 미해결 지적이 모두 [위험:낮음]이며 정책 위반은 일관되게 fail closed.
- 재읽기 권고 레벨: 2 — `ssl`·gRPC 신뢰 저장소 차이와 임시 파일 처리 근거를 이해해야 수정이 안전하다.

## 1차 리뷰 대비 / vs 1st Review
1차 라인 리뷰 대상이 아니므로(부록 A, `.review/CODE-REVIEW-Lv2/INDEX.md:341`) INDEX·위임 보고서 중 관련 항목만 비교한다.

| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| INDEX §4.3 #3: gRPC 원격 주소 평문·가드 미적용 | 부분 해결 | `grpcs://` 에 사설 CA·클라이언트 인증서·required 정책 적용(`app/services/tls.py:97-121`, `app/services/a2a_gateway.py:80-81`, `test/test_lv2_hardening.py:76`). 그러나 gRPC 대상 주소에는 egress 가드가 여전히 없음(`app/services/a2a_grpc.py:57-61` 이 `secure_channel`/`insecure_channel` 을 직접 생성) |
| (위임 보고서) 이 파일 관련 지적 없음 | — | `.review/delegated/{code-review,security-review}.md` 에 tls 항목 없음(`grep`) |
