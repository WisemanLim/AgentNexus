# 보안 리뷰 (security-review 폴백)

> **대상:** wise-AgentNexus (FastAPI + SQLAlchemy, Python 3.12) — AI Gateway 플랫폼
> **날짜:** 2026-10-07
> **방법:** 정적 읽기 (소스 코드 분석, 의존성 감사 제외 — 런타임 접근 불가)
> **도구:** Claude Code `/security-review` 가 이 세션에서 호출 불가 → `oh-my-claudecode:security-reviewer` 서브에이전트로 폴백 (wds-review §1). 아래는 그 결과 원문.

---

## 요약 -- 심각도별 건수

| 심각도 | 건수 |
|--------|------|
| Critical | 0 |
| High | 3 |
| Medium | 6 |
| Low | 3 |
| **합계** | **12** |

**전체 위험 수준: MEDIUM** -- Critical 은 없으나, High 3건(JWT 알고리즘 혼동, SSRF, 로그인 무차별 공격 미방어)이 합쳐지면 외부 인증 우회 또는 내부 네트워크 탐색으로 이어질 수 있다.

---

## 발견 사항

### SEC-01 | HIGH | JWT Algorithm Confusion — 외부 IdP 토큰 검증 시 공격자 제어 `alg` 사용

**위치:** `app/core/auth.py:140-148`
**카테고리:** A02 Cryptographic Failures / A07 Auth Failures
**확신도:** Confirmed

**근거:**
```python
def _external_verify(idp: IdpConnection, token: str) -> dict:
    header = jwt.get_unverified_header(token)
    for attempt in (False, True):
        for k in _external_keys(idp, refresh=attempt).get("keys", []):
            if header.get("kid") in (None, k.get("kid")):
                key = jwt.PyJWK(k).key
                return jwt.decode(token, key, algorithms=[header.get("alg", "RS256")],
                                  audience=idp.audience or idp.client_id, issuer=idp.issuer, leeway=30)
```

**공격 시나리오:** 공격자가 외부 IdP 경로로 JWT 를 제출할 때, `alg` 헤더를 임의로 설정할 수 있다. `algorithms` 파라미터가 검증되지 않은 `header["alg"]` 에서 직접 온다. PyJWT 2.x 의 키-알고리즘 타입 검사가 HS256-with-RSA-key 공격을 대부분 차단하지만: (1) `kid` 가 `None` 이면 JWKS 의 모든 키와 매칭 시도하고, (2) JWKS 에 여러 타입의 키(RSA + EC + symmetric)가 있으면 공격자가 의도하지 않은 키-알고리즘 조합을 선택할 수 있다. 내부 토큰 검증(`verify_local`)은 `algorithms=["RS256"]` 으로 하드코딩되어 있어 안전하나, 외부 IdP 경로만 취약하다.

**영향:** 외부 IdP 를 연동한 환경에서 인증 우회 가능성. JIT 프로비저닝(`_upsert_external_user`)으로 임의 역할의 사용자가 생성될 수 있다.

**권고 수정:**
```python
# BAD — 공격자 제어 alg
algorithms=[header.get("alg", "RS256")]

# GOOD — IdP 설정 또는 JWKS 키 타입에서 허용 알고리즘을 결정
_ALLOWED_ALGS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"}
alg = header.get("alg", "RS256")
if alg not in _ALLOWED_ALGS:
    raise jwt.InvalidTokenError(f"disallowed algorithm: {alg}")
return jwt.decode(token, key, algorithms=list(_ALLOWED_ALGS),
                  audience=idp.audience or idp.client_id, issuer=idp.issuer, leeway=30)
```

---

### SEC-02 | HIGH | SSRF — OpenAPI Fetch 엔드포인트에서 내부 네트워크 접근 가능

**위치:** `app/api/mcp.py:533-551`
**카테고리:** A10 SSRF
**확신도:** Confirmed

**근거:**
```python
class FetchSpecIn(BaseModel):
    url: str = Field(pattern=r"^https?://", max_length=2000)

@router.post("/api/v1/mcp-servers/openapi/fetch")
def fetch_openapi(body: FetchSpecIn, _: Principal = Depends(require("mcp:write"))):
    with sync_client(timeout=10) as http:
        res = http.get(body.url)
```

**공격 시나리오:** `mcp:write` 권한을 가진 사용자(developer 이상)가 `url=http://169.254.169.254/latest/meta-data/iam/security-credentials/` (AWS 메타데이터) 또는 `url=http://10.0.0.1:5432/` (내부 PostgreSQL) 을 요청한다. URL 검증이 `^https?://` 패턴만 적용되고, 사설 IP / 클라우드 메타데이터 주소 차단이 없다. `sync_client` 에도 SSRF 보호 로직이 없다 (`app/core/http.py` 확인 완료).

동일한 SSRF 패턴이 다음 위치에도 존재한다:
- `app/services/a2a_gateway.py:85-101` — Agent Card fetch (`fetch_card`)
- `app/services/a2a_gateway.py:706` — Push JWKS fetch (`_push_jwks`)
- `app/core/auth.py:131-136` — 외부 IdP JWKS/Discovery fetch
- `app/api/admin.py:87-93` — Connector 헬스 프로브 (`_probe`)

**영향:** 내부 서비스 포트 스캔, 클라우드 인스턴스 메타데이터에서 IAM 자격 증명 탈취, 내부 API 호출.

**권고 수정:**
```python
# GOOD — 해석된 IP 를 사설/메타데이터 대역과 대조
import ipaddress, socket

_BLOCKED = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # AWS/GCP 메타데이터
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fd00::/8"),
]

def validate_url(url: str) -> None:
    host = urlparse(url).hostname
    for addr in socket.getaddrinfo(host, None):
        ip = ipaddress.ip_address(addr[4][0])
        if any(ip in net for net in _BLOCKED):
            raise bad_request(f"내부 네트워크 주소는 허용되지 않습니다: {host}")
```

---

### SEC-03 | HIGH | 로그인 Brute Force 방어 미비 — 무차별 비밀번호 시도 차단 없음

**위치:** `app/api/idp.py:98-103, 229-260`
**카테고리:** A07 Auth Failures
**확신도:** Confirmed

**근거:**
```python
def _login(db: Session, username: str, password: str) -> User | None:
    user = db.scalars(select(User).where(User.username == username)).first()
    if user and user.status == "active" and security.check_password(password, user.password_hash):
        return user
    return None
```

`POST /idp/authorize` (로그인 폼)와 `POST /idp/device` (CLI 디바이스 로그인) 모두 동일한 `_login` 함수를 사용하며, 실패 횟수 추적, 계정 잠금, 점진적 지연, CAPTCHA 가 없다. 감사 로그(`audit.append("auth.login", ..., decision="deny")`)는 남기지만 이는 탐지용이지 방어가 아니다.

**공격 시나리오:** 공격자가 `POST /idp/authorize` 에 사전 기반 비밀번호를 초당 수백 건 시도한다. IP 정책(`ip_allowed`)은 성공 후에만 적용되고, 실패 시도는 제한 없이 허용된다.

**영향:** 약한 비밀번호를 가진 관리자 계정이 탈취될 수 있다.

**권고 수정:**
```python
# GOOD — IP 기반 실패 횟수 추적 + 점진적 잠금
from collections import defaultdict
import time

_FAIL_COUNTS: dict[str, list[float]] = defaultdict(list)
MAX_ATTEMPTS, WINDOW_S, LOCKOUT_S = 5, 300, 600

def _check_rate(ip: str) -> None:
    now = time.time()
    attempts = [t for t in _FAIL_COUNTS[ip] if now - t < WINDOW_S]
    _FAIL_COUNTS[ip] = attempts
    if len(attempts) >= MAX_ATTEMPTS:
        raise ApiError(429, "too_many_attempts",
                       f"로그인 시도가 너무 많습니다. {LOCKOUT_S}초 후 다시 시도하세요.")

def _record_fail(ip: str) -> None:
    _FAIL_COUNTS[ip].append(time.time())
```

---

### SEC-04 | MEDIUM | SSRF — 사용자 입력 Push Notification URL 로 서버측 요청 발생

**위치:** `app/services/a2a_gateway.py:764-766`
**카테고리:** A10 SSRF
**확신도:** Confirmed

**근거:**
```python
if client and client.get("url"):
    with contextlib.suppress(Exception):
        async with async_client(timeout=5) as http:
            await http.post(client["url"], json=canon, headers={
                "X-A2A-Notification-Token": client.get("token", "")})
```

**공격 시나리오:** 인증된 사용자가 `push_set` 으로 `pushNotificationConfig.url` 을 `http://169.254.169.254/...` 로 설정하면, Task 상태 변경 시 게이트웨이가 해당 내부 주소로 POST 요청을 보낸다. `contextlib.suppress(Exception)` 으로 오류가 묵살되므로 공격 흔적도 남지 않는다.

**영향:** 내부 서비스에 임의 JSON 데이터를 POST 할 수 있다. 클라우드 메타데이터 접근은 GET 이 아닌 POST 이므로 제한적이나, 내부 웹훅 트리거 등에 악용 가능하다.

**권고 수정:** SEC-02 와 동일한 URL 검증 함수를 `push_set` 파라미터 검증 시 적용한다.

---

### SEC-05 | MEDIUM | Open Redirect — 프로덕션에서도 localhost redirect_uri 허용

**위치:** `app/api/idp.py:61-67`
**카테고리:** A01 Broken Access Control
**확신도:** Confirmed

**근거:**
```python
def _check_redirect(redirect_uri: str) -> None:
    allowed = urlparse(settings.public_base_url)
    u = urlparse(redirect_uri)
    same = (u.scheme, u.netloc) == (allowed.scheme, allowed.netloc)
    dev = u.hostname in ("localhost", "127.0.0.1") and u.scheme == "http"
    if not (same or dev):
        raise ApiError(400, "invalid_request", "허용되지 않은 redirect_uri 입니다.")
```

`dev` 조건이 `settings.app_env` 를 검사하지 않으므로 프로덕션에서도 `redirect_uri=http://localhost:9999/steal` 이 허용된다. 또한 `client_id` 와 `redirect_uri` 의 사전등록 매핑이 없어 어떤 `client_id` 든 아무 localhost 경로로 리디렉션할 수 있다.

**공격 시나리오:** 피해자 브라우저에서 공격자가 로컬 포트(예: Electron 앱, 브라우저 확장)를 열어 두고, 조작된 인가 URL(`redirect_uri=http://localhost:9999/callback`)을 클릭하게 만들면 인가 코드를 가로챌 수 있다.

**영향:** PKCE 가 공개 클라이언트에만 강제되므로, `client_id` 가 `PUBLIC_CLIENTS` 가 아닌 값이면 PKCE 없이 인가 코드를 탈취해 토큰을 발급받을 수 있다.

**권고 수정:**
```python
# GOOD — 프로덕션에서 localhost 차단 + client_id 별 redirect_uri 등록
dev = u.hostname in ("localhost", "127.0.0.1") and u.scheme == "http" \
      and settings.app_env in ("local", "test")
```

---

### SEC-06 | MEDIUM | CSRF — OAuth 로그인 폼에 CSRF 토큰 없음

**위치:** `app/api/idp.py:229-260`
**카테고리:** A01 Broken Access Control
**확신도:** Plausible

**근거:** `POST /idp/authorize` 로그인 폼은 HTML `<form method="post">` 으로 렌더링되며, CSRF 토큰(`<input type="hidden" name="csrf_token">`)이 없다. `SameSite` 쿠키 정책도 설정되지 않는다(세션 쿠키 자체를 사용하지 않으나, 브라우저 기본 쿠키가 있다면 전송됨).

**공격 시나리오:** Login CSRF — 공격자가 자신의 자격 증명으로 피해자 브라우저에서 로그인하게 만들어, 피해자가 공격자 계정으로 활동하게 한다. 피해자의 채팅 이력·비밀 등이 공격자 계정에 쌓인다.

**영향:** 제한적이나, Login CSRF 는 OWASP 에서 유효한 공격 벡터로 인정된다.

**권고 수정:** 로그인 폼 렌더링 시 서버에서 일회용 CSRF 토큰을 생성하고, `POST` 에서 검증한다.

---

### SEC-07 | MEDIUM | Access Token 발급 시 subject 임의 지정 가능 — IDOR

**위치:** `app/services/identity.py:119-129` (호출부는 `app/api/identity.py` 의 Access Token 생성 엔드포인트)
**카테고리:** A01 Broken Access Control
**확신도:** Plausible

**근거:**
```python
def issue_access_token(db, name, subject, audience, scopes, ttl, issued_by):
    token, claims = security.sign({"sub": subject, "anx_kind": "access_token",
                                   "aud": audience, "scope": " ".join(scopes)}, ttl)
```

`tokens:issue` 권한을 가진 operator/security 역할이 `subject` 를 임의의 사용자 `sub` 로 지정할 수 있다. 결과 토큰은 `roles=["user"]` 로 하드코딩되지만, `principal.sub` 가 다른 사용자의 값이 되므로 해당 사용자의 세션·Task·채팅 이력 등에 접근할 수 있다.

**공격 시나리오:** operator 역할 사용자가 `subject="admin-sub-123"`, `audience="agentnexus"` 로 Access Token 을 발급한 뒤, 그 토큰으로 `/api/v1/a2a/tasks`(`principal_sub` 기반 필터)나 채팅 세션 목록에 접근하면 관리자의 데이터를 열람할 수 있다.

**영향:** 교차 사용자 데이터 접근 (IDOR). `roles=["user"]` 로 권한은 제한되나 데이터 열람이 가능하다.

**권고 수정:** Access Token 의 `subject` 를 호출자의 `sub` 로 고정하거나, 명시적 위임 관계 검증을 추가한다.

---

### SEC-08 | MEDIUM | Search 엔드포인트 LIKE 와일드카드 주입

**위치:** `app/api/admin.py:459` (`search` 함수), `app/api/chat.py:73-74`, `app/api/identity.py:133` 등 다수
**카테고리:** A03 Injection
**확신도:** Confirmed

**근거:**
```python
like = f"%{q}%"
# ...
stmt.where(User.username.like(like), User.name.like(like), User.email.like(like))
```

사용자 입력 `q` 가 이스케이프 없이 LIKE 패턴에 삽입된다. SQLAlchemy 의 파라미터화 쿼리로 SQL injection 은 불가하나, `%` 와 `_` 와일드카드는 패턴의 일부로 해석된다.

**공격 시나리오:** 공격자가 `q=%` 를 검색하면 모든 레코드가 반환된다. `q=a_min` 은 `admin` 뿐 아니라 의도하지 않은 패턴과 매칭될 수 있다. 정보 노출 범위는 해당 사용자의 기존 권한 내로 제한된다.

**영향:** 제한적 정보 노출. 권한 검사는 별도로 적용되므로 권한 우회는 아니다.

**권고 수정:**
```python
# GOOD — LIKE 특수문자 이스케이프
import re
escaped = re.sub(r"([%_\\])", r"\\\1", q)
like = f"%{escaped}%"
```

---

### SEC-09 | MEDIUM | 외부 IdP JWKS/Discovery URL 에 SSRF 보호 없음

**위치:** `app/core/auth.py:122-137`
**카테고리:** A10 SSRF
**확신도:** Confirmed

**근거:**
```python
def _external_keys(idp: IdpConnection, refresh: bool = False) -> dict:
    def srv(url: str) -> str:
        return idp.internal_url.rstrip("/") + url[len(iss):] if idp.internal_url ... else url
    with sync_client(timeout=10) as http:
        if not jwks_uri:
            disc = http.get(srv(idp.issuer.rstrip("/") + "/.well-known/openid-configuration")).json()
            jwks_uri = disc["jwks_uri"]
        keyset = http.get(srv(jwks_uri)).json()
```

`idp.internal_url` 은 관리자가 설정하는 값으로, 사설 네트워크 주소를 지정할 수 있다. 또한 Discovery 응답의 `jwks_uri` 를 그대로 fetch 하므로, 악의적 IdP 가 `jwks_uri` 를 내부 주소로 반환하면 SSRF 가 발생한다.

**영향:** 관리자 권한 필요(IdpConnection 생성). 그러나 `idp.internal_url` 은 의도적으로 내부 주소를 허용하는 설계이므로, `jwks_uri` 가 외부 응답에서 오는 경우만 SSRF 위험이 있다. Discovery 에서 반환된 `jwks_uri` 에 대한 도메인 검증이 없다.

**권고 수정:** Discovery 응답의 `jwks_uri` 가 `idp.issuer` 와 같은 도메인/origin 인지 검증한다.

---

### SEC-10 | LOW | Password Hashing — scrypt 비용 파라미터가 OWASP 권장값 미만

**위치:** `app/core/security.py:45`
**카테고리:** A02 Cryptographic Failures
**확신도:** Confirmed

**근거:**
```python
dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
```

`n=2^14` (16384)은 RFC 7914 의 최소값이지만 OWASP 는 `n=2^17` (131072) 이상을 권장한다. 현재 값은 GPU 기반 공격에 대한 저항력이 8배 낮다.

**영향:** 데이터베이스 유출 시 비밀번호 크래킹 시간이 단축된다.

**권고 수정:** `n=2**17` 로 상향하고, 기존 해시에는 점진적 재해싱(로그인 성공 시 업그레이드) 적용.

---

### SEC-11 | LOW | Password Grant 비생산 환경 활성화 — app_env 오설정 시 노출

**위치:** `app/api/idp.py:301`
**카테고리:** A05 Security Misconfiguration
**확신도:** Confirmed

**근거:**
```python
if grant == "password" and settings.app_env in ("local", "test"):
```

`APP_ENV` 가 `local` 또는 `test` 이면 OAuth2 Password Grant 가 활성화된다. `.env.local` 에 `APP_ENV=local` 이 설정되어 있고, 프로덕션(`.env.prod`)에는 `APP_ENV=prod` 로 되어 있어 정상이나, 환경 변수 오설정 시 프로덕션에서 Password Grant 가 노출될 수 있다.

**영향:** 프로덕션에서 Password Grant 가 노출되면 MFA 우회 경로가 될 수 있다 (OTP 가 옵션 파라미터이므로).

**권고 수정:** Password Grant 활성화 시 시작 로그에 경고를 출력한다.

---

### SEC-12 | LOW | Redirect URI — client_id 별 redirect_uri 사전 등록 없음

**위치:** `app/api/idp.py:61-67, 132-140`
**카테고리:** A07 Auth Failures
**확신도:** Confirmed

**근거:** `_check_redirect` 는 같은 origin 또는 localhost 인지만 검사하고, `client_id` 에 대해 사전 등록된 `redirect_uri` 와 정확히 일치하는지 검증하지 않는다. OAuth 2.1 (RFC 9126) 은 정확한 redirect_uri 매칭을 요구한다.

**영향:** 같은 origin 내에서 Open Redirect 취약점이 있는 경로가 있으면, 인가 코드를 해당 경로로 보내 탈취할 수 있다.

**권고 수정:** `client_id` 별 `redirect_uri` 를 DB 에 사전 등록하고, 정확 일치(`==`)로 검증한다.

---

## 확인했으나 문제 없음 (checked-OK list)

| 항목 | 결과 |
|------|------|
| SQL Injection (SQLAlchemy ORM) | OK — 전체 코드가 SQLAlchemy 파라미터화 쿼리 사용. `f"..."` 로 SQL 을 조립하는 패턴 없음 |
| 내장 JWT 서명/검증 (verify_local) | OK — `algorithms=["RS256"]` 하드코딩, `aud`/`iss`/`exp`/`sub` 필수 검증 |
| PKCE (공개 클라이언트) | OK — `PUBLIC_CLIENTS` 에 대해 `code_challenge` 필수 적용 |
| Token Exchange scope escalation | OK — `TokenExchangePolicy.allowed_scopes` 와 교차 후 허용 스코프만 부여 |
| Refresh Token rotation | OK — `refresh()` 에서 `row.revoked_at = utcnow()` 로 즉시 폐기 후 재발급 |
| 비밀번호 타이밍 공격 | OK — `hmac.compare_digest()` 사용 (`security.py:54`) |
| Secrets 평문 노출 | OK — API 응답에 `masked()` 적용, `secrets.py` 에서 Fernet 암호화 |
| Opaque Token 해시 | OK — `token_hash` 는 SHA-256 단방향 해시 (`security.py:62-63`) |
| Helm securityContext | OK — `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, NetworkPolicy 적용 확인 |
| MFA (TOTP) | OK — `mfa_enabled` 사용자에게 6자리 TOTP 필수 검증 |
| 세션 강제 종료 (`tokens_not_before`) | OK — `iat < tokens_not_before` 이면 거부 (auth.py:222-224) |
| 승인 직무 분리 | OK — `self_approval` 검사로 정책 작성자 자기 승인 차단 (gateway_admin.py:314-315) |
| 도구 기본 거부 (unregistered_tool) | OK — `ToolRule` 없는 도구는 `list_tools` 에서 노출 안 되고 호출도 거부 |
| A2A 응답 소독 (`_sanitize_in`) | OK — 원격 에이전트 응답에 대해 PII 마스킹 + 지시문 제거 적용 |
| stdio 명령 허용 목록 | OK — `mcp_stdio_allowed_commands` 설정에 없는 명령은 실행 거부 (`mcp_gateway.py:554-556`) |
