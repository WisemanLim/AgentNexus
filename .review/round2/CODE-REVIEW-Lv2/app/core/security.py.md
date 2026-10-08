> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md) · 1차 리뷰: [security.py.md](../../../../CODE-REVIEW-Lv2/app/core/security.py.md)

### 07. app/core/security.py
**파일 요약 / File Summary**
- 역할 / Role: 내장 IdP 토큰 서명(RS256)·검증, 비밀번호·워크로드 시크릿 해시(scrypt, 파라미터 내장 형식 + 이전 형식 호환 + 재해싱 판정), 불투명 토큰 생성·SHA-256 해시, PKCE S256.
- 진입점 거리 / Distance: 2 (`app/api/idp.py:111-113` 로그인 → `check_password`·`needs_rehash`·`hash_password`, `app/core/auth.py:215-221` 인증 → `unverified_claims`·`verify_local`, `app/services/identity.py:57·65·134·144·167·199` 서명)
- LOC: 88 (현재 `wc -l`) · 1차 67 → +21
- 주요 export: `API_AUDIENCE`, `issuer`, `sign`, `verify_local`, `unverified_claims`, `SCRYPT`, `hash_password`, `check_password`, `needs_rehash`, `opaque`, `token_hash`, `pkce_s256` (내부 `_LEGACY`, `_parse`)
- 의존(내부): `app.core.keys`(서명 키·kid), `app.core.config`(`public_base_url`)
- 외부 패키지: pyjwt[crypto], 표준 `hashlib`·`hmac`·`secrets`·`base64`·`uuid`
- 부수효과 / Side effects: 없음(키는 `app/core/keys.py:49-52` 가 lru_cache 로 지연 로드)
- 동시성 / Concurrency: 순수 함수. 단 `hashlib.scrypt` 는 CPU·메모리(16 MiB/회) 집약 — 호출 위치가 `async def` 이면 이벤트 루프를 그만큼 막는다(`app/api/idp.py:288-333` — idp 보고서 참조).
- 테스트 커버리지 흔적: `test/test_policy_hardening.py:66-89`(`test_scrypt_upgrade_and_rehash` — 새 형식 접두·검증·이전 형식 검증·로그인 후 재해싱), `test/test_identity.py:34-44`(JWKS 로 토큰 검증), `test/test_identity.py:45-82`(PKCE), `test/test_review_fixes.py:95-117`(로그인 경로). 손상된 해시 문자열·비정상 파라미터 시험 없음.
- 2차 변경 요약 / Changes since 1st review: 1차 인용(1차 보고서 L70-84)과 비교 — (1) scrypt 파라미터를 `SCRYPT={"n":2**14,"r":8,"p":5}` 로 올림(1차 `p=1`) — L43-45, Epic 43 D17-④(`test/impl/43rd/scenario.md:9`). (2) 저장 형식에 파라미터 포함 `scrypt$n=…,r=…,p=…$salt$dk`(L52-53). (3) 형식 파서 `_parse` 추가 — 3조각(이전 형식)·4조각(새 형식) 모두 수용(L56-61), 1차의 `split("$", 2)` 언패킹 오류 지적을 조각 수 검사로 대체. (4) `needs_rehash` 추가(L73-75) — 로그인 성공 시 재해싱(`app/api/idp.py:112-114`). 서명·검증·불투명 토큰·PKCE(L14-39, L78-88)는 1차 원문과 같다(빈 줄 차이만).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/security.py lines=1-20
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
- L14 `API_AUDIENCE`: 플랫폼 API 가 받는 토큰의 `aud`. 교환 토큰(`aud=mcp:*`·`a2a:*`·`api:*`)은 `verify_local` 기본 audience 와 달라 플랫폼 API 에서 거부된다(`app/core/auth.py:219-223`) — "교환 토큰은 업스트림 전용" 경계의 근거.
- L17-18 `issuer()`: `PUBLIC_BASE_URL/idp`. 호출마다 설정을 읽으므로 시험에서 `settings.public_base_url` 를 바꾸면 즉시 반영된다. 흔한 실수: 끝 슬래시 차이로 issuer 문자열이 달라져 `iss` 비교가 깨지는 것 — `rstrip('/')` 로 정규화(같은 정규화를 `app/services/mcp_gateway.py:144-145` 도 사용).

```python path=app/core/security.py lines=21-41
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
- L21-25 `sign`: 표준 클레임(iss·iat·nbf·exp·jti) 뒤에 `**claims` 를 펼쳐 호출자가 덮어쓸 수 있게 했다(내부 호출 전용이라 의도된 유연성 — `app/services/audit.py:224-225` 는 아예 다른 iss 로 `jwt.encode` 를 직접 쓴다). `jti` 는 Access Token 행의 PK(`app/services/identity.py:146`)와 교환 이력의 `jti`(`app/services/identity.py:205`)로 쓰인다. `typ` 기본 `at+jwt`(RFC 9068 액세스 토큰), id_token 은 `typ="JWT"`(`app/services/identity.py:65`).
- L28-35 `verify_local`: 알고리즘 `RS256` 고정(헤더 alg 로 검증 방식을 고르지 못함 — 알고리즘 혼동 차단), `require` 로 exp·iat·sub·iss 존재 강제(PyJWT 는 `require` 에 없는 클레임은 "있으면 검증, 없으면 통과" 이므로 명시가 필요), audience·issuer 검증, 시계 오차 5초.
- L38-39 `unverified_claims`: 서명 검증 없는 디코드 — 호출처는 `app/core/auth.py:215` 한 곳(issuer 분기용)이고 이후 반드시 `verify_local`(L221) 또는 `_external_verify`(L256)를 거친다(재확인).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L28-35 헤더 `typ` 를 확인하지 않는다. id_token(`typ=JWT`, `aud=client_id`)은 보통 `aud` 가 달라 거부되지만, `client_id` 는 사전 등록이 없어 임의 문자열이 허용된다(`app/api/idp.py:245`, `:301`). 사용자가 `client_id=agentnexus` 로 인가 코드를 교환하면 id_token 의 `aud` 가 `agentnexus` 가 되어 `verify_local` 을 통과하고, `anx_kind` 가 없으므로 `app/core/auth.py:224` 기본값 `"user"` 로 사용자 토큰처럼 인증된다. 같은 사용자 신원이라 권한 상승은 아니지만 "id_token 은 API 호출에 쓰지 않는다"(OIDC) 경계가 무너진다. 최소 수정: `verify_local` 에 `jwt.get_unverified_header(token).get("typ") == "at+jwt"` 확인(기본) 또는 `auth.py:224` 의 `anx_kind` 기본값 제거.
- (1차 지적 후속) 서명 키 공유: 리포트 서명은 여전히 같은 키(`app/services/audit.py:225` `keys.signing_key()`)지만 검증이 `issuer="agentnexus-audit"`·`require=["iss","report_id","sha256"]`(`app/services/audit.py:237-238`)를 요구해 "사용자 토큰이 리포트 서명으로 통과" 는 막혔다. 반대 방향(리포트 서명을 Bearer 로 제시)은 iss 가 달라 `app/core/auth.py:251-254` 에서 401. 키 용도 분리는 남은 하드닝.

```python path=app/core/security.py lines=42-55
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


```
**분석 / Analysis (Level 2)**
- L43-45: OWASP Password Storage Cheat Sheet 의 scrypt 동급표(N=2^17·r8·p1 / 2^16·r8·p2 / 2^15·r8·p3 / **2^14·r8·p5** / 2^13·r8·p10)에서 메모리 16 MiB 조합을 골랐다. scrypt 메모리 ≈ `128·r·N` = 128×8×16384 = 16 MiB(p 는 메모리가 아니라 반복 횟수에 곱해짐) — 그래서 "메모리 폭증 없이 연산만 5배" 라는 주석이 맞다. OpenSSL 의 scrypt 는 p 개 블록을 순차 계산하므로 CPU 시간이 p 에 거의 비례한다(1차 p=1 대비 약 5배 — 절대값은 미측정).
- L46 `_LEGACY`: 1차 시점 파라미터. 저장 형식에 파라미터가 없던 해시를 검증하려면 고정값을 알고 있어야 한다.
- L49-53 `hash_password`: 16바이트 무작위 salt(`secrets` — CSPRNG), 32바이트 파생 키, `scrypt$n=16384,r=8,p=5$<salt>$<dk>`. 파라미터를 함께 저장하므로 다음 상향 때는 `SCRYPT` 만 바꾸면 이전 해시도 각자의 파라미터로 검증된다(1차 권고 (1) "형식에 파라미터 포함" 반영). `dict` 삽입 순서(n,r,p)가 형식 문자열 순서가 된다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L51 `hashlib.scrypt(..., maxmem=0)` 기본값은 OpenSSL 기본 한도 32 MiB 를 쓴다. 현재 조합은 약 16 MiB 라 통과하지만, 주석의 다음 단계(N=2^15·r=8 → `128·8·(32768+2)` ≈ 32 MiB + p 블록)로 올리면 한도를 넘어 `ValueError` 로 **모든 로그인·워크로드 인증이 500** 이 된다. `test/impl/39th/scenario.md:56`(SEC-10 판정)도 "maxmem 설계 필요" 를 남겼다. 최소 수정: 상향 시 `maxmem=256 * 1024 * 1024` 처럼 명시하고 시험으로 고정.
- [위험:낮음] p=5 는 같은 함수를 쓰는 **워크로드 시크릿 검증**(`app/services/identity.py:121-125`)에도 그대로 적용된다. 워크로드 시크릿은 `anx_ws_` + 256비트 난수(`app/services/identity.py:105`)라 느린 해시가 보안상 필요 없는데, token-exchange·client_credentials 요청마다 CPU 비용이 5배가 되었고 그 호출이 `async def token` 안에서 이벤트 루프를 막는다(`app/api/idp.py:288-325` — idp 보고서 [위험:중간]). 최소 수정: 고엔트로피 시크릿은 `token_hash`(SHA-256, L83-84)+`hmac.compare_digest` 로 분리하고, 비밀번호만 scrypt.

```python path=app/core/security.py lines=56-77
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


```
**분석 / Analysis (Level 2)**
- L56-61 `_parse`: `$` 로 나눠 3조각(`scrypt$salt$dk` — 이전 형식)이면 `_LEGACY`, 4조각이면 `n=…,r=…,p=…` 를 dict 로. base64 salt·dk 에는 `$` 가 없으므로(표준 알파벳 + `/`·`+`·`=`) 조각 수로 형식을 구분할 수 있다. salt·dk 는 `parts[-2]`·`parts[-1]` 로 형식과 무관하게 같은 위치.
- L64-70 `check_password`: 저장된 파라미터로 다시 계산 → `hmac.compare_digest` 상수 시간 비교. 길이가 다른 바이트열이면 예외 없이 `False`. 사용자 입력 비밀번호는 파라미터를 바꿀 수 없다(파라미터는 DB 저장값에서만 온다) — 공격자 제어 파라미터 DoS 는 DB 쓰기 권한이 전제.
- L73-75 `needs_rehash`: 파싱 실패 또는 현재 `SCRYPT` 와 다르면 True. dict 동등 비교라 키 순서와 무관. 호출처는 로그인 성공 직후 한 곳(`app/api/idp.py:112-114`)이라 "검증 성공한 평문" 이 있을 때만 재해싱 — 맞는 설계(평문 없이 재해싱 불가).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L60-61 형식은 맞지만 내용이 손상된 저장값에서 예외가 그대로 전파된다: `n=abc` → `int()` `ValueError`, `p` 누락 → L69 `KeyError`, base64 손상 → `binascii.Error`. `_login`(`app/api/idp.py:111`)·`authenticate_workload`(`app/services/identity.py:123`)는 잡지 않으므로 그 사용자/워크로드의 인증 요청이 500. 1차 "`split` 언패킹 오류 → 500" 지적의 범위가 줄었을 뿐 남아 있다. 최소 수정: `_parse` 전체를 `try/except (ValueError, KeyError, binascii.Error): return None`.
- [위험:낮음] L60 파라미터 값의 범위 검사가 없다. DB 의 `password_hash` 를 쓸 수 있는 주체(관리 API 버그·백업 복원·SQL 콘솔)가 `p=100000` 같은 값을 넣으면 그 계정 로그인 시도마다 수 분의 CPU 를 쓰게 할 수 있다(메모리는 `maxmem` 32 MiB 로 막히지만 p·r 의 시간 비용은 막히지 않음). 위협 전제가 강해 낮음. 최소 수정: 허용 집합(`{SCRYPT, _LEGACY}`) 또는 상한(`n ≤ 2^17, r == 8, p ≤ 16`) 밖이면 `None`.
- L66-67 사용자가 없거나 해시가 없으면 scrypt 를 계산하지 않고 바로 False — 응답 시간 차로 사용자 이름 존재를 추정할 수 있다(호출부 `app/api/idp.py:110-111` 가 user 없음일 때 아예 호출하지 않음). p=5 로 그 시간 차가 1차보다 약 5배 커졌다(idp 보고서에서 [위험:낮음]으로 다룸).

```python path=app/core/security.py lines=78-88
# ── 불투명 토큰 (PAT·클라이언트 키·리프레시) ──
def opaque(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pkce_s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
```
**분석 / Analysis (Level 2)**
- L79-80 `opaque`: 접두(`anx_pat_`·`anx_key_`·`anx_rt_`·`anx_ws_`) + 32바이트(256비트) urlsafe 난수. 접두는 시크릿 스캐너가 유출을 잡게 하고 `app/core/auth.py:198` 의 토큰 종류 분기에도 쓰인다.
- L83-84 `token_hash`: 솔트 없는 SHA-256 — 고엔트로피 난수라 사전·무차별 공격이 성립하지 않아 빠른 해시로 충분(비밀번호와 다름). DB 등호 조회(`app/core/auth.py:199`, `app/services/identity.py:81`)라 원문 비교 타이밍 문제도 없다.
- L87-88 `pkce_s256`: RFC 7636 `BASE64URL(SHA256(ASCII(verifier)))` 패딩 제거. 비교는 호출부(`app/api/idp.py:304`)에서 `!=`(비상수 시간)지만 공격자가 알아낼 값(challenge)은 이미 공개 정보라 문제 없다.
**짚어볼 점 / Watch-outs**
- L87-88 verifier 길이(RFC 7636: 43~128자)·문자 집합 검사가 없다 — 짧은 verifier 를 쓰는 클라이언트 실수를 서버가 잡지 못한다(콘솔은 충분한 길이 사용, 영향 낮음).

## 파일 평가 / File Assessment
- 강점: (1) 검증 알고리즘 고정·필수 클레임·audience/issuer 강제. (2) 해시 형식에 파라미터를 넣고 이전 형식을 함께 검증 + 로그인 시 재해싱 — 앞으로의 강도 상향 경로가 열렸다(OWASP 동급 N=2^14·r8·p5). (3) 상수 시간 비교, 고엔트로피 토큰의 해시 저장, 위험 함수의 명시적 이름(`unverified_claims`).
- 위험: (1) [위험:낮음] p=5 가 워크로드 시크릿 검증에도 적용되어 비동기 토큰 엔드포인트의 CPU·루프 차단 비용 증가(`app/core/security.py:51`·`:69` ↔ `app/api/idp.py:288-325`). (2) [위험:낮음] 손상·비정상 저장값에서 예외 전파·파라미터 상한 없음(`app/core/security.py:60-61`·`:69`). (3) [위험:낮음] `typ` 미확인으로 id_token 이 액세스 토큰처럼 통과할 수 있는 경로(`app/core/security.py:28-35` + `app/core/auth.py:224`), 다음 상향 시 `maxmem` 함정(`:51`).
- 개선 권고(영향순): (1) 워크로드 시크릿을 SHA-256+상수 시간 비교로 분리(또는 최소한 해당 호출을 스레드풀로). (2) `_parse` 예외 처리 + 파라미터 허용 범위. (3) `verify_local` 에서 `typ=at+jwt` 확인, 강도 상향 시 `maxmem` 명시와 손상 해시 시험 추가.
- 파일 위험 등급 / Risk grade: 낮음 — 미해결 항목이 모두 [위험:낮음]이고, 해시 강도·형식 문제는 해결됐다(가장 큰 파급은 호출부 `app/api/idp.py` 의 비동기 실행 위치).
- 재읽기 권고 레벨: 3 — 짧은 순수 함수 모음. 강도 상향 시 maxmem·호출 위치(async 여부)만 다시 확인하면 된다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 1차 L89 [위험:낮음 · 하드닝] N=2^14·p=1 은 권장치 미달, 파라미터가 저장 형식에 없어 상향 불가 | 해결 — OWASP 동급 조합(2^14·r8·p5)으로 상향, 형식에 파라미터 포함, 이전 형식 검증 + 로그인 시 재해싱 | `app/core/security.py:43-53`, `:56-61`, `:73-75`, `app/api/idp.py:112-114`, `test/test_policy_hardening.py:73-89` |
| 1차 L90 `split("$", 2)` — 손상 값이면 `ValueError`(500), `len(parts)==3` 형식 검사 권고 | 부분 해결 — 조각 수 검사(3·4)는 추가, 조각 내용 손상(`int`·`KeyError`·base64) 예외는 여전히 전파 | `app/core/security.py:58-61`, `:69` |
| 1차 L91 워크로드 시크릿 검증에도 scrypt — 고빈도 인증 비용 | 미해결(악화) — 같은 함수, p=5 로 비용 약 5배, 이제 token-exchange 요청마다 호출 | `app/services/identity.py:121-125`, `app/api/idp.py:321` |
| 1차 L68 같은 서명 키를 리포트 서명에 공유 · `verify_report_signature` 가 iss·typ 미확인 | 부분 해결 — 검증에 `iss=agentnexus-audit`·필수 클레임 요구(사용자 토큰 통과 차단), 키 공유는 유지 | `app/services/audit.py:224-225`, `:236-238` |
| 1차 L67 `unverified_claims` 를 분기 외 용도로 쓰면 인증 우회 — 호출처 1곳 | 해결(유지) — 호출처 여전히 `app/core/auth.py:215` 한 곳, 뒤에 검증 필수 | `app/core/auth.py:215-223`, `:255-258` |
| 1차 위임 보안 리뷰 SEC-10(scrypt 비용 파라미터) | 해결 — 위 1행과 동일 근거(N=2^17 이 아니라 OWASP 동급표의 p 증가 조합) | `app/core/security.py:43-45` |
