> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md)

### 16. app/core/security.py
**파일 요약 / File Summary**
- 역할 / Role: 내장 IdP 토큰 서명(RS256)·검증, 비밀번호 해시(scrypt), 불투명 토큰 생성·해시, PKCE S256.
- 진입점 거리 / Distance: 2 (`app/core/auth.py:14`, `app/services/identity.py:7`)
- LOC: 67
- 주요 export: `API_AUDIENCE`, `issuer`, `sign`, `verify_local`, `unverified_claims`, `hash_password`, `check_password`, `opaque`, `token_hash`, `pkce_s256`
- 의존(내부): `app.core.keys`, `app.core.config`
- 외부 패키지: pyjwt[crypto], 표준 `hashlib`·`hmac`·`secrets`
- 부수효과 / Side effects: 없음(키는 `keys` 가 lazy 로드)
- 동시성 / Concurrency: none(순수 함수)
- 테스트 커버리지 흔적: `test/test_identity.py`, `test/test_hardening.py`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/security.py lines=1-18
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
```
**분석 / Analysis (Level 2)**
- L14 `API_AUDIENCE = "agentnexus"`: 게이트웨이·관리 API 가 받는 토큰의 audience. 교환 토큰(`mcp:*`·`a2a:*`)은 이 값이 아니라 여기서 거부된다(`auth.py:219-223`).
- L17-18 `issuer()`: `PUBLIC_BASE_URL/idp` — 외부에서 보이는 주소 기준. 분리 DNS 환경에서 내부 서비스는 다른 주소로 JWKS 를 조회한다(`settings.idp_internal_url`).

```python path=app/core/security.py lines=21-39
def sign(claims: dict, ttl: int, typ: str = "at+jwt") -> tuple[str, dict]:
    now = int(time.time())
    body = {"iss": issuer(), "iat": now, "nbf": now, "exp": now + ttl, "jti": uuid.uuid4().hex, **claims}
    token = jwt.encode(body, keys.signing_key(), algorithm="RS256", headers={"kid": keys.kid(), "typ": typ})
    return token, body


def verify_local(token: str, audience: str | list[str] | None = API_AUDIENCE) -> dict:
    """내장 issuer 가 서명한 토큰 검증 (aud 필수)."""
    pub = keys.signing_key().public_key()
    opts = {"require": ["exp", "iat", "sub", "iss"]}
    if audience is None:
        opts["verify_aud"] = False
    return jwt.decode(token, pub, algorithms=["RS256"], audience=audience, issuer=issuer(), options=opts,
                      leeway=5)


def unverified_claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})
```
**분석 / Analysis (Level 2)**
- L21-25 `sign`: 표준 클레임(iss·iat·nbf·exp·jti) + 호출자 클레임. 헤더에 `kid`(JWKS 키 선택)·`typ`(`at+jwt` = RFC 9068 액세스 토큰). `**claims` 가 뒤에 와서 호출자가 `iss`·`exp` 를 덮어쓸 수 있다 — 내부 호출만 있으므로 의도된 유연성.
- L28-35 `verify_local`: 알고리즘 RS256 **고정**(알고리즘 혼동 공격 방지), audience·issuer 검증, 필수 클레임 4개, 시계 오차 5초. `audience=None` 이면 aud 검증 생략(내부 용도).
- L38-39 `unverified_claims`: 서명 검증 없이 디코드 — issuer 분기용(`auth.py:215`). 이름이 위험성을 드러내는 좋은 명명.
**짚어볼 점 / Watch-outs**
- L38-39 를 분기 외 용도(권한 판단)로 쓰면 바로 인증 우회가 된다 — 호출처는 `app/core/auth.py:215` 한 곳뿐이며 이후 반드시 검증 경로를 거친다(확인됨).
- 같은 서명 키가 감사 리포트 서명에도 쓰인다(`app/services/audit.py:225`) — 키 용도 분리 원칙(액세스 토큰 vs 문서 서명)에 어긋난다. `audit.verify_report_signature` 는 iss·typ 를 확인하지 않아 사용자 액세스 토큰도 "유효한 리포트 서명" 으로 통과한다(내용 해시 미제공 시).

```python path=app/core/security.py lines=42-54
# ── 비밀번호 (scrypt) ──
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def check_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, dk_b64 = stored.split("$", 2)
    dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(dk, base64.b64decode(dk_b64))
```
**분석 / Analysis (Level 2)**
- L43-46 `hash_password`: 16바이트 무작위 salt + scrypt(N=2^14, r=8, p=1, 32바이트) → `scrypt$salt$dk` 형식(알고리즘 식별자를 저장해 향후 교체 가능).
- L49-54 `check_password`: 형식 확인 → 같은 파라미터로 재계산 → `hmac.compare_digest` 상수 시간 비교(타이밍 공격 방지).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 하드닝] N=2^14 는 오래된 권장치다. OWASP Password Storage Cheat Sheet 는 scrypt 최소 N=2^17(r=8, p=1)을 권한다. 파라미터가 저장 형식에 없으므로 상향 시 기존 해시를 검증할 수 없다 — `scrypt$N$r$p$salt$dk` 처럼 파라미터를 함께 저장하도록 바꾸는 것이 먼저다.
- L52 `split("$", 2)` — 저장 값이 손상돼 `$` 가 2개 미만이면 `ValueError`(500). 형식 검사를 `len(parts) == 3` 으로.
- 워크로드 시크릿 검증에도 scrypt 가 쓰인다(`app/services/identity.py:107`) — client_credentials 요청마다 ~수십 ms CPU. 고빈도 서비스 계정 인증에서는 비용이 크다.

```python path=app/core/security.py lines=57-67
# ── 불투명 토큰 (PAT·클라이언트 키·리프레시) ──
def opaque(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pkce_s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
```
**분석 / Analysis (Level 2)**
- L58-59 `opaque`: 접두(`anx_pat_`·`anx_rt_`·`anx_ws_`) + 256비트 무작위 urlsafe — 접두로 비밀 스캐너가 유출을 탐지할 수 있다(좋은 관행).
- L62-63 `token_hash`: SHA-256 — 고엔트로피 무작위 토큰이라 솔트 없는 빠른 해시로 충분하다(비밀번호와 다름).
- L66-67 `pkce_s256`: RFC 7636 — `BASE64URL(SHA256(verifier))`, 패딩 제거.

## 파일 평가 / File Assessment
- 강점: (1) 검증 알고리즘 고정·필수 클레임·audience 강제. (2) 상수 시간 비교, 토큰 접두·해시 저장. (3) 위험 함수의 명시적 이름.
- 위험: (1) [낮음] scrypt 파라미터가 권장치 미달이며 저장 형식에 파라미터가 없다. (2) [낮음] 서명 키를 문서 서명과 공유. (3) 손상된 해시 문자열 처리.
- 개선 권고(영향순): (1) 해시 형식에 파라미터 포함 + N 상향(재해시 on login). (2) 리포트 서명은 별도 키 또는 `typ`·`iss` 검증. (3) 형식 검사 보강.
- 재읽기 권고 레벨: 3.
