> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [api_Abstract.md](api_Abstract.md) · 1차 리뷰: 1차 리뷰 대상 아님(부록 A)

### 29. app/api/idp.py
**파일 요약 / File Summary**
- 역할 / Role: 내장 IdP(OIDC 발급자, `issuer = PUBLIC_BASE_URL/idp`) — discovery·JWKS, 로그인 폼(인가 코드 + PKCE), 외부 IdP 브로커 시작·콜백, 토큰 엔드포인트(authorization_code·refresh_token·client_credentials·device_code·token-exchange·password(local/test)), Device Authorization Grant, userinfo, refresh 폐기. 로그인 실패 한도·MFA·로그인 IP 정책.
- 진입점 거리 / Distance: 1 (`app/main.py` 가 `settings.builtin_idp` 일 때 `idp_api.router` 를 마운트 — 인증 없이 인터넷에 노출되는 엔드포인트가 대부분)
- LOC: 421 (현재 `wc -l`) · 1차 부록 A 415 → +6
- 주요 export: `router`, `LOGIN_MAX_FAILURES`·`LOGIN_WINDOW_S`·`LOCKED`(시험이 monkeypatch — `test/test_review_fixes.py:96`), 내부 `_check_redirect`·`_login`·`_mfa_ok`·`_issue_code`·`_device_poll`
- 의존(내부): `app.core.{keys,security,auth,config,errors,ids}`, `app.db`, `app.models.{AuthCode,BrokerRequest,DeviceCode,IdpConnection,RefreshToken,User}`, `app.services.{audit,broker,identity,ratelimit,totp,deploy}`
- 외부 패키지: fastapi(`Form`·`HTMLResponse`·`RedirectResponse`·`JSONResponse`), sqlalchemy, 표준 `html`·`urllib.parse`·`base64`·`secrets`·`json`
- 부수효과 / Side effects: DB 쓰기(인가 코드·디바이스 코드·refresh·사용자 해시 재작성), 감사(로그인 성공·실패·디바이스 승인 — 실패마다 해시 체인 1건), 실패 카운터(ratelimit 백엔드 — prod Valkey), 외부 IdP 리다이렉트
- 동시성 / Concurrency: 혼합. `token`·`device_code`·`revoke` 는 **`async def`** 인데 본문이 동기 DB·scrypt·(교환 시) 외부 JWKS HTTP 를 호출 → 이벤트 루프에서 실행. 나머지 폼 핸들러는 동기(스레드풀). 인가 코드 1회 사용은 조건부 UPDATE(L306-308), 디바이스 코드 사용 처리는 읽기-수정-쓰기(L374).
- 테스트 커버리지 흔적: `test/test_identity.py:34-44`(discovery·JWKS), `:45-82`(인가 코드 + PKCE + refresh 회전), `:83-100`(디바이스 흐름), `:114-163`(client_credentials·교환), `test/test_review_fixes.py:95-117`(로그인 실패 한도 — 브라우저·password grant), `:119-127`(loopback redirect 는 local/test 만), `:130-136`(디바이스 승인 MFA), `test/test_caller_trust.py:20-34`(교환은 워크로드 인증 필수), `test/test_policy_hardening.py:73-89`(로그인 재해싱), `test/test_sso.py:33-143`(브로커), `test/test_cli.py:35-60`(CLI 디바이스 로그인). 이벤트 루프 차단·IP 정책 우회(디바이스·SSO)·MFA 추측 횟수 시험 없음.
- 2차 변경 요약 / Changes since 1st review: 1차 라인 리뷰 원문은 없고(부록 A), 1차 위임 보안 리뷰 인용(`.review/delegated/security-review.md` SEC-03·05·06·11·12)과 1차 identity 보고서 인용(`idp.py:313-317` 무인증 교환) 대비 — (1) `_check_redirect` loopback 허용을 `app_env in (local,test)` 로 한정(L65, SEC-05 · Epic 39). (2) 사용자 이름별 실패 한도 `_login`(L98-117, SEC-03 · Epic 39) — 브라우저(L253-257)·password grant(L328-329)·디바이스(L395-396) 공통. (3) 로그인 성공 시 scrypt 재해싱(L112-114, Epic 43). (4) 디바이스 승인 MFA(L399-400, Epic 39). (5) 인가 코드 1회 사용 조건부 UPDATE(L306-308, Epic 39 "Lv2 경쟁"). (6) token-exchange grant 에 워크로드 인증 필수·`act`=인증된 워크로드(L318-325, Epic 40 D17-①). 1차 LOC 415 → 421 로 순증이 작은 것은 일부 수정이 1차 인벤토리 이전에 이미 들어갔기 때문으로 보인다(가능성).
- 스냅샷 주의 / Snapshot note: **이 파일은 리뷰 도중 21:41 에 수정되었다**(현재 디스크 451줄 — 동시 작업 Epic 51 · `.review/round2/VERIFY.md`, `test/test_review2_fixes.py:300-331` C51-12). 아래 라인 리뷰의 인용·라인 번호는 **스냅샷(21:03) 원문**이며, 인용 블록을 이어 붙인 파일의 SHA-1 이 `snapshot-start.sha` 의 `app/api/idp.py`(8776543e…)와 일치함을 확인했다. 스냅샷 이후 변경과 지적별 현재 상태는 맨 끝 "부록 — 스냅샷 이후 동시 변경" 참조. 다른 파일 교차 인용 중 `app/main.py`·`app/core/http.py`·`app/services/{mcp_gateway,a2a_gateway,policy,secrets}.py`·`app/api/{mcp,a2a}.py`·`deploy/helm/agentnexus/templates/api.yaml` 도 스냅샷 이후 수정되어 라인이 어긋날 수 있다.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/idp.py lines=1-26
"""내장 IdP (FR-I00·I01) — OIDC 발급자. 외부 IdP(Keycloak·Entra ID·Okta)가 없을 때의 기본 IdP 이자,
게이트웨이 STS(RFC 8693 토큰 교환)·워크로드 client_credentials·CLI Device Flow 를 제공한다.

issuer = {PUBLIC_BASE_URL}/idp
"""
import html
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import keys, security
from app.core.auth import authenticate_token, current_principal, Principal
from app.core.config import settings
from app.core.errors import ApiError
from app.core.ids import token_urlsafe
from app.db import get_db, utcnow
from app.models import AuthCode, BrokerRequest, DeviceCode, IdpConnection, RefreshToken, User
from app.services import audit, broker, identity

router = APIRouter(tags=["identity"])


```
**분석 / Analysis (Level 2)**
- L1-5: 이 라우터의 세 역할 — 콘솔 OIDC 발급자, 게이트웨이 STS(외부 클라이언트용 교환), CLI 디바이스 흐름.
- L6 `html`: 로그인 페이지를 템플릿 엔진 없이 문자열로 만들기 때문에 모든 동적 값은 `html.escape` 가 유일한 XSS 방어선이다(아래 L89-95 확인).
- L12 `update`: 인가 코드 1회 사용을 조건부 UPDATE 로(L306).
- L16 `authenticate_token`: token-exchange 의 subject_token 검증을 인증 의존성과 같은 함수로(토큰 종류 규칙 일관).

```python path=app/api/idp.py lines=27-60
def _metadata() -> dict:
    iss = security.issuer()
    return {
        "issuer": iss,
        "authorization_endpoint": f"{iss}/authorize",
        "token_endpoint": f"{iss}/token",
        "userinfo_endpoint": f"{iss}/userinfo",
        "jwks_uri": f"{iss}/jwks",
        "revocation_endpoint": f"{iss}/revoke",
        "device_authorization_endpoint": f"{iss}/device/code",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials",
                                  identity.GRANT_DEVICE, identity.GRANT_TOKEN_EXCHANGE]
                                 + (["password"] if settings.app_env in ("local", "test") else []),
        "code_challenge_methods_supported": ["S256"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
    }


@router.get("/idp/.well-known/openid-configuration")
@router.get("/idp/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/idp")
def discovery():
    return _metadata()


@router.get("/idp/jwks")
def jwks():
    return keys.jwks()


```
**분석 / Analysis (Level 2)**
- L27-46: OIDC discovery + RFC 8414 메타데이터. password grant 는 local/test 에서만 광고(L40) — 실제 처리 조건(L326)과 같은 식이라 광고·동작이 일치.
- L49-53: 세 경로 — OIDC(`/idp/.well-known/openid-configuration`), RFC 8414 경로 접미(`/idp/.well-known/oauth-authorization-server`), RFC 8414 표준 삽입 형식(`/.well-known/oauth-authorization-server/idp`, MCP 클라이언트가 쓰는 형식).
- L56-58 `jwks`: 공개키만(`app/core/keys.py:67-70`). 내장 MCP 서버·외부 리소스 서버가 이 키로 교환 토큰을 검증한다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L44 `client_secret_post`·`client_secret_basic` 을 광고하지만 authorization_code·refresh_token grant 는 클라이언트 시크릿을 전혀 확인하지 않는다(L299-313) — 시크릿은 워크로드(client_credentials·token-exchange)에만 쓰인다. 비공개(confidential) 클라이언트로 등록했다고 믿는 외부 RP 는 "코드를 가로채도 시크릿이 없으면 못 바꾼다" 고 가정하게 된다. 최소 수정: 클라이언트 등록 모델 도입 전까지 광고를 `["none"]`+워크로드 전용 설명으로 줄이거나, 공개 클라이언트 외에는 PKCE 를 필수화.

```python path=app/api/idp.py lines=61-69
def _check_redirect(redirect_uri: str) -> None:
    allowed = urlparse(settings.public_base_url)
    u = urlparse(redirect_uri)
    same = (u.scheme, u.netloc) == (allowed.scheme, allowed.netloc)
    dev = settings.app_env in ("local", "test") and u.hostname in ("localhost", "127.0.0.1") and u.scheme == "http"  # Vite 콘솔(:5173)
    if not (same or dev):
        raise ApiError(400, "invalid_request", "허용되지 않은 redirect_uri 입니다.")


```
**분석 / Analysis (Level 2)**
- L62-64: `PUBLIC_BASE_URL` 과 scheme+netloc(호스트:포트) 동일 여부. `netloc` 비교라 `https://evil@host`(userinfo 포함)·`https://host.evil.com` 은 거부된다. 흔한 실수인 `startswith(public_base_url)`(→ `https://host.evil.com` 통과) 를 피했다.
- L65: loopback 허용을 local/test 로 한정(SEC-05 해결, `test/test_review_fixes.py:119-127`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L64 같은 origin 이면 **아무 경로**나 허용(SEC-12 미해결, 39th 판정 "PKCE 로 충분" — `test/impl/39th/scenario.md:58`). 그런데 PKCE 는 `PUBLIC_CLIENTS` 에만 필수(L152)이고 `client_id` 는 등록 없이 아무 문자열이므로, 공격자는 `client_id=x`(비공개 취급·PKCE 불필요·시크릿 확인 없음)로 같은 origin 의 다른 경로를 redirect_uri 로 지정할 수 있다. 같은 origin 에 쿼리스트링을 외부로 흘리는 페이지(오픈 리다이렉트, 서드파티 스크립트가 `location.href` 를 보내는 페이지 등)가 하나라도 생기면 인가 코드 → 토큰 탈취가 된다. 현재 그런 경로가 있는지는 미확인(가능성). 최소 수정: 공개 클라이언트가 아니어도 PKCE 필수(OAuth 2.1 권고) 또는 redirect_uri 를 `/console/` 등 허용 경로 접두로 제한.
- [위험:낮음] L65 의 안전성은 `settings.app_env` 기본값이 `"local"`(`app/core/config.py:17`)이라는 점에 기대고 있다 — 운영에서 `APP_ENV` 를 빠뜨리면 loopback redirect 와 password grant(L326)가 함께 켜진다(SEC-11 미해결, 기동 경고 없음).

```python path=app/api/idp.py lines=70-97
_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentNexus 로그인</title><style>
:root{{--primary:#3F51E5;--navy:#1F3864;--muted:#868E96;--danger:#C92A2A;--bg:#F5F6FA;--card:#fff;--text:#1F2937}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111827;--card:#1F2937;--text:#E5E7EB;--navy:#C7D2FE}}}}
body{{margin:0;font:14px Pretendard,'Noto Sans KR',system-ui,sans-serif;background:var(--bg);color:var(--text);display:grid;place-items:center;min-height:100vh}}
.card{{background:var(--card);padding:32px;border-radius:12px;width:min(360px,calc(100vw - 32px));box-shadow:0 4px 24px #0001}}
h1{{font-size:20px;color:var(--navy);margin:0 0 4px}}p{{color:var(--muted);margin:0 0 20px}}
label{{display:block;font-size:12px;margin:12px 0 4px}}input{{width:100%;box-sizing:border-box;height:36px;padding:0 10px;border:1px solid #CED4DA;border-radius:6px;background:transparent;color:inherit}}
input:focus{{outline:2px solid var(--primary);outline-offset:1px}}button{{margin-top:20px;width:100%;height:36px;border:0;border-radius:6px;background:var(--primary);color:#fff;font-weight:600;cursor:pointer}}
.err{{color:var(--danger);font-size:12px;margin-top:8px}}
a.idp{{display:block;box-sizing:border-box;height:36px;line-height:34px;margin-top:8px;border:1px solid #CED4DA;border-radius:6px;text-align:center;color:inherit;text-decoration:none;font-weight:600}}
a.idp:hover{{border-color:var(--primary)}}.or{{margin:16px 0 0;text-align:center;font-size:12px;color:var(--muted)}}</style></head><body><main class="card">
<h1>AgentNexus</h1><p>{subtitle}</p><form method="post">{hidden}
{extra}<label for="u">사용자 이름 / Username</label><input id="u" name="username" autocomplete="username" value="{username}" required autofocus>
<label for="p">비밀번호 / Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<label for="o">MFA 코드 (등록한 경우)</label><input id="o" name="otp" inputmode="numeric" autocomplete="one-time-code" maxlength="6">
{error}<button type="submit">{button}</button></form></main></body></html>"""


def _page(subtitle: str, hidden: dict, button: str = "로그인", error: str = "", extra: str = "",
          username: str = "") -> HTMLResponse:
    fields = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v or "")}">'
                     for k, v in hidden.items())
    err = f'<div class="err" role="alert">{html.escape(error)}</div>' if error else ""
    return HTMLResponse(_PAGE.format(subtitle=html.escape(subtitle), hidden=fields, button=button, error=err,
                                     extra=extra, username=html.escape(username)))


```
**분석 / Analysis (Level 2)**
- L70-86 `_PAGE`: `str.format` 템플릿 — CSS 의 중괄호를 `{{ }}` 로 이스케이프. `<form method="post">` 에 `action` 이 없어 현재 URL(쿼리 포함)로 POST 된다. 다크 모드·접근성 레이블 포함.
- L89-95 `_page`: hidden 필드의 이름·값, 오류, 부제, 사용자 이름을 `html.escape`(기본 `quote=True` — `"`·`'` 도 이스케이프)로 처리 → 속성 값 탈출 XSS 차단. `extra` 와 `button` 은 이스케이프하지 않는다 — 호출부가 만든 HTML(L181-183 버튼 목록은 각 값을 escape, L381-382 디바이스 코드 입력은 escape)이거나 상수라 안전하지만, 새 호출부가 사용자 값을 `extra` 에 넣으면 바로 XSS 가 된다(규칙 주석 필요).
**짚어볼 점 / Watch-outs**
- 스냅샷(21:03) 시점에는 이 페이지에 프레임 삽입 방지 헤더가 없었다(클릭재킹 — round2 `app/main.py` 보고서 [위험:중간] 과 같은 결론). 스냅샷 이후 동시 작업으로 전역 보안 헤더가 추가되었다(`app/main.py` `_SECURITY_HEADERS` — 21:21 수정, `test/test_review2_fixes.py:44-50` 이 `/idp/authorize` 포함 확인). 2차 기준선 밖 변경이라 여기서는 집계하지 않는다.
- L82 로그인 폼에 CSRF 토큰이 없다(SEC-06 미해결 — 의도적 미수정). 완화 근거를 코드로 확인: 콘솔 콜백이 `state` 를 sessionStorage 의 보류 값과 비교해 다르면 거부한다(`web/src/lib/auth.tsx:75-77`). 다만 서버는 POST 에서 PKCE 를 다시 요구하지 않는다(L244-249 참조) — 콘솔 외 클라이언트(CLI·서드파티 RP)는 각자 `state` 를 확인해야만 login CSRF 가 막힌다.

```python path=app/api/idp.py lines=98-119
# 무차별 대입 방지 — 사용자 이름별 실패 횟수(15분 고정 창, 워커 공유는 Valkey 백엔드일 때). 한도에 걸리면 비밀번호를 확인하지 않는다.
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_S = 900
LOCKED = "locked"


def _login(db: Session, username: str, password: str) -> User | str | None:
    """성공 → User · 실패 → None · 실패 한도 초과 → LOCKED."""
    from app.services import ratelimit
    key = f"login-fail:{username.strip().lower()}"
    if ratelimit.backend().window_get(key, LOGIN_WINDOW_S) >= LOGIN_MAX_FAILURES:
        return LOCKED
    user = db.scalars(select(User).where(User.username == username)).first()
    if user and user.status == "active" and security.check_password(password, user.password_hash):
        if security.needs_rehash(user.password_hash):  # 이전 강도 해시 → 현재 파라미터로 (D17-④)
            user.password_hash = security.hash_password(password)
            db.commit()
        return user
    ratelimit.backend().window_add(key, 1, LOGIN_WINDOW_S)
    return None


```
**분석 / Analysis (Level 2)**
- L98-101: 15분 고정 창(`slot = time // 900` — `app/services/ratelimit.py:40-52`, Valkey 는 `anx:w:{key}:{slot}` + TTL — `:116-128`)에 실패 10회.
- L107 키 정규화 `strip().lower()` — `Admin `·`admin` 이 같은 카운터를 쓰므로 대소문자·공백 변형으로 한도를 피할 수 없다(`test/test_review_fixes.py:107` 이 `"Lockout-Probe "` 로 확인). 조회(L110)는 정확 일치라 변형은 항상 실패로 집계 — 우회 대신 잠금 쪽으로 기운다(안전).
- L108-109: 한도에 걸리면 비밀번호를 **계산하지 않고** `LOCKED` — 잠금 중 scrypt 비용도 없다(DoS 완화).
- L111: 존재·활성·비밀번호 검증. L112-114: 이전 형식 해시면 현재 파라미터로 재해싱 후 커밋(평문이 있는 유일한 시점).
- L116: 실패 시에만 카운트(성공 시 초기화는 없음 — 9회 실패 후 성공해도 창이 끝날 때까지 9 유지).
**짚어볼 점 / Watch-outs**
- [위험:중간] L104-117 (SEC-03 부분 해결) 사용자 이름 **단독** 키의 고정 창이라 두 방향 모두 약하다. (a) **표적 잠금 DoS**: 인증 없는 공격자가 15분마다 `admin` 으로 틀린 비밀번호 10회만 보내면 관리자는 맞는 비밀번호로도 계속 `LOCKED`(L108-109, 브라우저·디바이스·password grant 공통) — 관리자 콘솔 로그인을 상시 차단. (b) **비밀번호 분사(spraying)**: IP·전역 한도가 없어 사용자 수 × 10회/15분까지 시도 가능. (c) **비원자 확인-증가**: L108 조회와 L116 증가 사이에 scrypt(p=5) 시간이 있어, 같은 사용자 이름으로 동시 요청 N 개를 보내면 모두 한도 전 값을 읽고 통과 — 창당 10 + 동시성(스레드풀 40 × 워커 수)만큼 추측. (d) 고정 창 경계 직전·직후로 최대 20회 연속. (e) 기본 백엔드가 memory(`app/core/config.py:42`)면 워커별 카운트이고 `_windows` dict 는 퇴출이 없어(`app/services/ratelimit.py:46-52`) 무작위 사용자 이름 요청마다 항목이 영구히 쌓인다(prod 는 `.env.prod:28` `RATE_LIMIT_BACKEND=redis` — 키 TTL 있음). 최소 수정: 먼저 `window_add` 로 원자 증가 후 반환값으로 판정(실패 확정 전 선차감), 키를 (사용자 이름) + (클라이언트 IP) 두 축으로, 사용자 이름 축은 잠금 대신 점진 지연(또는 CAPTCHA)으로 바꿔 표적 잠금을 무력화.
- [위험:낮음] L110-111 사용자가 없거나 비활성이면 scrypt 를 건너뛴다 → 응답 시간 차로 사용자 이름 열거. p=5 로 차이가 커졌다(`app/core/security.py:45`). 최소 수정: 없는 사용자에도 고정 더미 해시로 `check_password` 1회.

```python path=app/api/idp.py lines=120-146
def _mfa_ok(user: User, otp: str) -> bool:
    """MFA 를 등록한 사용자는 TOTP 6자리가 맞아야 한다 (SCR-A05 ③)."""
    if not user.mfa_enabled:
        return True
    from app.services import totp
    try:
        secret = keys.fernet().decrypt((user.mfa_secret or "").encode()).decode()
    except Exception:  # noqa: BLE001 — 키 교체 등으로 복호화 불가 → 거부
        return False
    return totp.verify(secret, otp)


def _providers(db: Session) -> list[IdpConnection]:
    from app.services import deploy
    if deploy.airgap(db):  # 에어갭 — 외부 IdP 선택지 숨김 (ST-09)
        return []
    return list(db.scalars(select(IdpConnection).where(IdpConnection.enabled.is_(True),
                                                       IdpConnection.login_enabled.is_(True))
                           .order_by(IdpConnection.name)))


@router.get("/idp/providers")
def providers(db: Session = Depends(get_db)):
    """로그인 화면용 외부 IdP 목록 (SCR-A10) — 이름·종류만, 인증 불필요."""
    return {"items": [{"id": i.id, "name": i.name, "kind": i.kind} for i in _providers(db)]}


```
**분석 / Analysis (Level 2)**
- L120-129 `_mfa_ok`: MFA 미등록이면 통과, 등록이면 Fernet 으로 TOTP 비밀 복호화(실패 시 거부 — fail-closed, 조용한 삼킴이지만 결과가 거부라 안전 쪽) → `totp.verify`(앞뒤 1스텝 ±30초 허용, 상수 시간 비교 — `app/services/totp.py:25-31`).
- L132-138 `_providers`: 에어갭이면 외부 IdP 숨김, 활성 + 로그인 노출 연동만.
- L141-144: 로그인 화면용 목록 — id·이름·종류만(issuer·client_id 는 비노출).
**짚어볼 점 / Watch-outs**
- [위험:중간] L120-129 MFA 실패가 **어디서도 실패 한도에 집계되지 않는다** — `_login` 은 비밀번호 실패에만 `window_add`(L116)하고, MFA 실패는 호출부가 오류 페이지만 돌려준다(L268-272, L330-331, L399-400). 비밀번호를 이미 아는 공격자(MFA 가 막아야 하는 바로 그 상황)는 OTP 를 무제한 추측할 수 있다: 허용 창 3스텝 → 시도당 성공 확률 3/10^6, 기대 시도 약 33만 회. 각 시도에 scrypt(p=5)가 들지만 스레드풀·워커·코어 수만큼 병렬이라 시간 단위로 뚫릴 수 있다(정확한 처리량은 미측정 — 가능성). 또 `totp.verify` 는 사용한 스텝을 기록하지 않아 같은 코드를 창 안에서 재사용(재전송 공격)할 수 있다. 최소 수정: MFA 실패도 같은 키(또는 더 엄격한 `mfa-fail:{user.id}` 5회/15분)에 `window_add`, 마지막 성공 스텝을 사용자 행에 저장해 재사용 거부.

```python path=app/api/idp.py lines=147-165
def _authorize_params(response_type: str, client_id: str, redirect_uri: str, state: str, scope: str,
                      code_challenge: str, code_challenge_method: str, nonce: str) -> dict:
    if response_type != "code":
        raise ApiError(400, "unsupported_response_type", "response_type=code 만 지원합니다.")
    _check_redirect(redirect_uri)
    if client_id in identity.PUBLIC_CLIENTS and not code_challenge:
        raise ApiError(400, "invalid_request", "공개 클라이언트는 PKCE(code_challenge) 가 필요합니다.")
    return {"client_id": client_id, "redirect_uri": redirect_uri, "state": state, "scope": scope,
            "code_challenge": code_challenge, "code_challenge_method": code_challenge_method, "nonce": nonce}


def _error_redirect(params: dict, err: broker.BrokerError) -> RedirectResponse:
    """OAuth 오류 응답 — 원래 redirect_uri 로 원인 코드를 돌려준다 (콘솔이 원인별 문구 표시)."""
    uri = params["redirect_uri"]
    sep = "&" if "?" in uri else "?"
    q = {"error": err.code, "error_description": err.detail, "state": params.get("state", "")}
    return RedirectResponse(f"{uri}{sep}{urlencode(q)}", status_code=302)


```
**분석 / Analysis (Level 2)**
- L147-155 `_authorize_params`: 인가 요청 공통 검증 — `response_type=code`, redirect 검증, 공개 클라이언트 PKCE 필수. GET 폼(L170)·브로커 시작(L191)이 쓴다.
- L158-163 `_error_redirect`: 오류를 원래 redirect_uri 로 돌려줌(RFC 6749 §4.1.2.1). `urlencode` 로 값 인코딩. `params` 는 이미 검증된 redirect_uri(L151) 또는 DB 에 저장된 브로커 요청 값이라 오픈 리다이렉트가 아니다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 이 검증은 **POST 로그인(L244-249)에서 다시 실행되지 않는다** — POST 는 `_check_redirect` 만 호출한다. 그래서 공개 클라이언트(`agentnexus-console`)도 `code_challenge` 없이 POST 하면 PKCE 없는 코드가 발급되고, 토큰 교환(L304)은 `row.code_challenge` 가 없으면 PKCE 검증을 건너뛴다. 공격 가치는 낮지만(피해자가 직접 비밀번호를 입력해야 함, 콘솔은 `state` 확인) "공개 클라이언트는 항상 PKCE" 불변식이 서버에서 깨진다. 최소 수정: L249 를 `_authorize_params("code", client_id, ...)` 호출로 교체.

```python path=app/api/idp.py lines=166-201
@router.get("/idp/authorize", response_class=HTMLResponse)
def authorize_form(response_type: str, client_id: str, redirect_uri: str, state: str = "", scope: str = "openid",
                   code_challenge: str = "", code_challenge_method: str = "S256", nonce: str = "", idp_hint: str = "",
                   db: Session = Depends(get_db)):
    params = _authorize_params(response_type, client_id, redirect_uri, state, scope, code_challenge,
                               code_challenge_method, nonce)
    if idp_hint:  # 콘솔의 IdP 버튼 — 바로 외부 IdP 로
        idp = db.get(IdpConnection, idp_hint)
        try:
            if idp is None or idp not in _providers(db):
                raise broker.BrokerError("idp_disabled")
            return RedirectResponse(broker.start(db, idp, params), status_code=302)
        except broker.BrokerError as exc:
            return _error_redirect(params, exc)
    q = urlencode({"response_type": "code", **params})
    buttons = "".join(f'<a class="idp" href="/idp/broker/{html.escape(i.id)}/start?{html.escape(q)}">'
                      f'{html.escape(i.name)} 계정으로 로그인</a>' for i in _providers(db))
    extra = f'{buttons}<div class="or">또는 AgentNexus 계정</div>' if buttons else ""
    return _page("콘솔에 로그인 (OIDC SSO)", params, extra=extra)


@router.get("/idp/broker/{idp_id}/start")
def broker_start(idp_id: str, client_id: str, redirect_uri: str, response_type: str = "code", state: str = "",
                 scope: str = "openid", code_challenge: str = "", code_challenge_method: str = "S256", nonce: str = "",
                 db: Session = Depends(get_db)):
    params = _authorize_params(response_type, client_id, redirect_uri, state, scope, code_challenge,
                               code_challenge_method, nonce)
    idp = db.get(IdpConnection, idp_id)
    try:
        if idp is None or idp not in _providers(db):  # 비활성·로그인 미노출·에어갭
            raise broker.BrokerError("idp_disabled")
        return RedirectResponse(broker.start(db, idp, params), status_code=302)
    except broker.BrokerError as exc:
        return _error_redirect(params, exc)


```
**분석 / Analysis (Level 2)**
- L166-184 `authorize_form`: 검증된 파라미터를 hidden 필드로 다시 싣는 로그인 폼. `idp_hint` 면 곧바로 외부 IdP 로(`broker.start` 가 state·PKCE·nonce 를 `BrokerRequest` 에 저장). `idp not in _providers(db)` 는 같은 세션 identity map 의 같은 객체 비교라 동작한다.
- L180-183: 외부 IdP 버튼의 href 에 원래 인가 파라미터를 붙인다 — 값은 `urlencode` 후 `html.escape`(속성 탈출 방지). 이름도 escape.
- L187-199 `broker_start`: 같은 검증을 거친 뒤 브로커 시작. 비활성·로그인 미노출·에어갭은 `idp_disabled` 오류 리다이렉트.

```python path=app/api/idp.py lines=202-231
_RESULT = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>IdP 시험 로그인</title></head>
<body style="font:14px system-ui,sans-serif;display:grid;place-items:center;min-height:90vh"><main role="status">
<h1 style="font-size:18px">{title}</h1><p>{detail}</p><p>이 창을 닫고 콘솔로 돌아가세요.</p></main>
<script>try{{window.opener&&window.opener.postMessage({{type:"anx-idp-test",state:{state}}},location.origin)}}catch(e){{}}</script>
</body></html>"""


@router.get("/idp/broker/callback")
def broker_callback(state: str = "", code: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    """외부 IdP → 내장 IdP 콜백 — 검증 후 원래 인가 요청(콘솔)에 코드 발급, 시험 모드는 결과만 저장."""
    import json as _json
    try:
        req, result = broker.complete(db, state, code, error)
    except broker.BrokerError as exc:
        req = db.get(BrokerRequest, state or "")
        audit.append("auth.login", actor=f"broker:{req.idp_id if req else '?'}", actor_kind="user", decision="deny",
                     data={"code": exc.code, "detail": exc.detail[:200]})
        if req is not None and req.mode == "test":
            broker.fail_test(db, state, exc)
            return HTMLResponse(_RESULT.format(title="시험 로그인 실패", detail=html.escape(exc.detail),
                                               state=_json.dumps(state)), status_code=400)
        if req is not None and req.params.get("redirect_uri"):
            return _error_redirect(req.params, exc)
        return HTMLResponse(_RESULT.format(title="로그인 실패", detail=html.escape(exc.detail), state='""'), status_code=400)
    if req.mode == "test":
        detail = f"역할 {', '.join(result['roles'])} · 그룹 {', '.join(result['groups']) or '없음'}"
        return HTMLResponse(_RESULT.format(title="시험 로그인 성공", detail=html.escape(detail), state=_json.dumps(state)))
    return _issue_code(db, result["user"], req.params)


```
**분석 / Analysis (Level 2)**
- L202-206 `_RESULT`: 시험 로그인 결과 창 — `postMessage` 대상 origin 을 `location.origin` 으로 고정(다른 origin 의 opener 에게 상태를 흘리지 않음).
- L209-229 `broker_callback`: `broker.complete` 가 state 1회 사용·코드 교환·ID 토큰 검증을 맡는다. 실패는 감사 + (시험 모드면 결과 저장 후 결과 창, 로그인이면 원래 redirect_uri 로 오류, 둘 다 아니면 일반 실패 창). 성공 + 시험 모드면 매핑 결과만, 로그인이면 내장 IdP 코드 발급.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L205 + L222·L228 `state` 를 `json.dumps` 로 **`<script>` 안에** 넣는다 — `json.dumps` 는 `</script>` 나 `<!--` 를 이스케이프하지 않는다. 지금은 두 분기 모두 `state` 가 DB 의 `BrokerRequest` 기본키와 일치해야만 도달하므로(L216-219 `db.get` 성공 + `mode == "test"`, L226 은 `broker.complete` 성공) 공격자가 임의 문자열을 넣을 수 없어 안전하다. 분기 조건이 바뀌면 바로 반사형 XSS 가 되는 구조. 최소 수정: `_json.dumps(state).replace("<", "\\u003c")`.
- L229 브로커 경유(SSO) 로그인은 **IP 허용 목록을 확인하지 않는다**(L262-267 은 비밀번호 폼에만) — 아래 L244-282 [위험:중간] 에 묶음.

```python path=app/api/idp.py lines=232-243
def _issue_code(db: Session, user: User, params: dict) -> RedirectResponse:
    code = token_urlsafe(24)
    db.add(AuthCode(code=code, client_id=params["client_id"], redirect_uri=params["redirect_uri"], user_id=user.id,
                    code_challenge=params.get("code_challenge") or None,
                    code_challenge_method=params.get("code_challenge_method") or None, scope=params.get("scope"),
                    nonce=params.get("nonce") or None, expires_at=utcnow() + timedelta(minutes=2)))
    db.commit()
    uri = params["redirect_uri"]
    sep = "&" if "?" in uri else "?"
    return RedirectResponse(f"{uri}{sep}{urlencode({'code': code, 'state': params.get('state', '')})}", status_code=302)


```
**분석 / Analysis (Level 2)**
- L232-241: 192비트 무작위 코드(`token_urlsafe(24)`), 클라이언트·redirect_uri·PKCE·nonce 에 묶고 2분 만료. 브로커 경유 로그인의 마지막 단계 — 원래 콘솔 요청의 PKCE 를 그대로 이어받아 콘솔이 자기 verifier 로 교환한다.
- L238 커밋 후 리다이렉트 — 리다이렉트 응답이 나간 뒤 코드가 DB 에 없을 수 없다(순서 맞음).

```python path=app/api/idp.py lines=244-282
@router.post("/idp/authorize")
def authorize_submit(request: Request, db: Session = Depends(get_db), client_id: str = Form(...), redirect_uri: str = Form(...),
                     username: str = Form(...), password: str = Form(...), otp: str = Form(""), state: str = Form(""),
                     scope: str = Form("openid"), code_challenge: str = Form(""),
                     code_challenge_method: str = Form("S256"), nonce: str = Form("")):
    _check_redirect(redirect_uri)
    user = _login(db, username, password)
    hidden = {"client_id": client_id, "redirect_uri": redirect_uri, "state": state, "scope": scope,
              "code_challenge": code_challenge, "code_challenge_method": code_challenge_method, "nonce": nonce}
    if user == LOCKED:
        audit.append("auth.login", actor=username, actor_kind="user", decision="deny", data={"client_id": client_id,
                                                                                            "reason": "too_many_failures"})
        return _page("콘솔에 로그인 (OIDC SSO)", hidden, error="로그인 실패가 너무 많습니다 — 15분 뒤 다시 시도하세요.",
                     username=username)
    if user is None:
        audit.append("auth.login", actor=username, actor_kind="user", decision="deny", data={"client_id": client_id})
        return _page("콘솔에 로그인 (OIDC SSO)", hidden, error="사용자 이름 또는 비밀번호가 올바르지 않습니다.",
                     username=username)
    ip = request.client.host if request.client else None
    if not identity.ip_allowed(db, ip):  # 로그인 허용 IP (SCR-L06 ⑥)
        audit.append("auth.login", actor=user.sub, actor_kind="user", decision="deny", data={"client_id": client_id,
                                                                                            "reason": "ip_policy", "ip": ip})
        return _page("콘솔에 로그인 (OIDC SSO)", hidden, error="허용되지 않은 접속 위치입니다 (IP 정책) — 관리자에게 문의하세요.",
                     username=username)
    if not _mfa_ok(user, otp):
        audit.append("auth.login", actor=user.sub, actor_kind="user", decision="deny", data={"client_id": client_id,
                                                                                            "reason": "mfa"})
        return _page("콘솔에 로그인 (OIDC SSO)", hidden, error="MFA 코드가 필요하거나 올바르지 않습니다 (인증 앱의 6자리).",
                     username=username)
    code = token_urlsafe(24)
    db.add(AuthCode(code=code, client_id=client_id, redirect_uri=redirect_uri, user_id=user.id,
                    code_challenge=code_challenge or None, code_challenge_method=code_challenge_method or None,
                    scope=scope, nonce=nonce or None, expires_at=utcnow() + timedelta(minutes=2)))
    db.commit()
    audit.append("auth.login", actor=user.sub, actor_kind="user", decision="allow", data={"client_id": client_id})
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}", status_code=302)


```
**분석 / Analysis (Level 2)**
- L244-249: 동기 핸들러(스레드풀) — scrypt 가 이벤트 루프를 막지 않는다. redirect 를 먼저 검증해 오픈 리다이렉트 차단.
- L253-261: 잠금·실패는 같은 폼을 오류와 함께 다시 그린다(입력한 사용자 이름 유지, escape). 실패마다 감사.
- L262-267: 비밀번호 확인 **뒤** IP 정책(SCR-L06 ⑥) — 실패 시 감사에 IP 기록.
- L268-272: MFA. L273-280: 코드 발급·감사·리다이렉트.
**짚어볼 점 / Watch-outs**
- [위험:중간] L262-267 로그인 IP 허용 목록이 **이 경로에만** 있다(`grep -rn ip_allowed app/` → `app/api/idp.py:263` 한 곳). 디바이스 승인(L386-405), 외부 IdP 브로커 로그인(L229 → `_issue_code`), password grant(L326-333, local/test)는 확인하지 않는다. 시나리오: 허용 CIDR 밖에서 탈취한 계정 정보를 가진 공격자가 `POST /idp/device/code`(인증 없음)로 코드를 만들고 `POST /idp/device` 에 계정 정보를 넣어 승인 → `/idp/token` device_code 폴링으로 액세스·refresh 토큰을 받는다 — 정책(SCR-L06 ⑥)이 그대로 우회된다. SSO 사용자는 어디서든 로그인된다. 최소 수정: IP 검사를 `_login` 성공 직후 공통 함수로 옮겨 디바이스 승인·password grant 에 적용하고, `_issue_code` 호출 전(브로커 콜백)에도 적용(`request.client.host` 를 넘기도록 시그니처 확장). 시험 공백: `test/test_ops.py:115-157` 는 브라우저 폼만 확인.
- [위험:낮음] L254·L259 실패 감사의 `actor` 에 사용자가 보낸 `username` 원문을 넣는다. `Form(...)` 에 길이 제한이 없고 `AuditEvent.actor` 는 `String(255)`(`app/models.py:770`) — PostgreSQL 은 초과 시 `value too long` 오류로 이 요청이 500(SQLite 는 길이를 강제하지 않음). 또 인증 없는 실패마다 해시 체인 머리 잠금(`app/services/audit.py:56-57` `_head(db, lock=True)`)으로 감사 쓰기가 직렬화된다 — 실패 폭주 시 다른 감사 이벤트 지연. 최소 수정: `username: str = Form(..., max_length=120)`(모델 `User.username` 길이), 실패 감사는 사용자 이름 해시·샘플링 검토.
- [위험:낮음] L262 `request.client.host` 는 uvicorn `--proxy-headers` + `--forwarded-allow-ips` 설정에 따라 `X-Forwarded-For` 값이 된다(`deploy/helm/agentnexus/templates/api.yaml:43`, 기본값 `"*"` — `deploy/helm/agentnexus/values.yaml:29`, 스냅샷 이후 21:20 에 설정화). 기본값이면 Ingress 를 거치지 않는 경로에서 XFF 위조로 IP 정책을 속일 수 있다(가능성 — 네트워크 구성 의존).
- L262-272 판정 순서상 IP 정책·MFA 오류 문구가 "비밀번호는 맞음" 을 알려 준다 — 실패 한도에 걸리지 않는 비밀번호 확인 오라클(아주 낮음, 비밀번호 추측은 이미 L116 에서 집계).

```python path=app/api/idp.py lines=283-297
def _oauth_error(code: str, desc: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code, "error_description": desc}, status_code=status,
                        headers={"Cache-Control": "no-store"})


@router.post("/idp/token")
async def token(request: Request, db: Session = Depends(get_db)):
    form = dict(await request.form())
    grant = form.get("grant_type", "")
    client_id = form.get("client_id", "")
    client_secret = form.get("client_secret", "")
    if not client_id and request.headers.get("authorization", "").lower().startswith("basic "):
        import base64
        raw = base64.b64decode(request.headers["authorization"][6:]).decode()
        client_id, _, client_secret = raw.partition(":")
```
**분석 / Analysis (Level 2)**
- L283-285 `_oauth_error`: RFC 6749 §5.2 형식 + `no-store`.
- L288-290: **`async def`** — `await request.form()`(Starlette 비동기 폼 파싱)을 쓰기 위해서다. FastAPI 는 `async def` 경로 함수를 이벤트 루프에서 직접 실행하고, 동기 의존성 `get_db` 만 스레드풀에서 만든다. 따라서 이후 본문의 동기 호출(SQLAlchemy 쿼리, scrypt, 외부 HTTP)은 모두 **이벤트 루프 스레드를 막는다**.
- L290 `dict(...)`: 같은 키가 여러 번 오면 마지막 값만 남는다(OAuth 는 파라미터 중복을 오류로 보라고 하지만 실무상 무해).
- L294-297: `client_id` 가 폼에 없을 때만 HTTP Basic(`client_secret_basic`) 해석.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L296 Basic 값이 base64·UTF-8 로 디코드되지 않으면 `binascii.Error`·`UnicodeDecodeError` 가 `try`(L298) **밖**에서 나 500 이 된다. 또 RFC 6749 §2.3.1 은 Basic 의 id·secret 을 먼저 form-urlencode 하도록 하는데 `unquote` 가 없다 — 현재 워크로드 시크릿은 urlsafe 문자만 써서(`app/core/security.py:79-80`) 실제 문제는 없다. 최소 수정: L294-297 을 `try` 안으로 옮기고 실패 시 `invalid_client` 401.

```python path=app/api/idp.py lines=298-317
    try:
        if grant == "authorization_code":
            row = db.get(AuthCode, form.get("code", ""))
            if row is None or row.used or row.expires_at < utcnow() or row.client_id != client_id \
                    or row.redirect_uri != form.get("redirect_uri"):
                return _oauth_error("invalid_grant", "code 가 유효하지 않습니다.")
            if row.code_challenge and security.pkce_s256(form.get("code_verifier", "")) != row.code_challenge:
                return _oauth_error("invalid_grant", "PKCE 검증 실패")
            if db.execute(update(AuthCode).where(AuthCode.code == row.code, AuthCode.used.is_(False))
                          .values(used=True)).rowcount != 1:  # 1회용 — 동시 교환 중 하나만
                return _oauth_error("invalid_grant", "code 가 유효하지 않습니다.")
            user = db.get(User, row.user_id)
            return JSONResponse(identity.issue_user_tokens(db, user, client_id, row.scope, row.nonce),
                                headers={"Cache-Control": "no-store"})
        if grant == "refresh_token":
            return JSONResponse(identity.refresh(db, form.get("refresh_token", ""), client_id))
        if grant == "client_credentials":
            return JSONResponse(identity.issue_workload_token(db, client_id, client_secret, form.get("scope")))
        if grant == identity.GRANT_DEVICE:
            return _device_poll(db, form.get("device_code", ""), client_id)
```
**분석 / Analysis (Level 2)**
- L299-311 authorization_code: 코드·만료·클라이언트·redirect_uri 일치(RFC 6749 §4.1.3) → PKCE S256 → **조건부 UPDATE**(`used = false` 일 때만 true)로 1회 사용 보장 → 토큰. UPDATE 와 토큰 INSERT 는 `issue_user_tokens` 의 커밋 한 번으로 함께 확정(`app/services/identity.py:73`). 동시 교환은 PostgreSQL 행 잠금 재평가·SQLite 쓰기 직렬화로 하나만 성공(services/identity 보고서와 같은 원리).
- L312-313 refresh: 회전·재사용 탐지는 서비스(`app/services/identity.py:80-100`). L314-315 client_credentials. L316-317 device_code 폴링.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L313·L315·L317(→L376)·L325 성공 응답에 `Cache-Control: no-store` 가 없다 — RFC 6749 §5.1 은 토큰 응답에 필수로 요구한다(L311 authorization_code 만 붙임). 중간 프록시·브라우저 캐시에 refresh token 이 남을 수 있다. 최소 수정: 성공 응답 생성을 `_oauth_ok(body)` 하나로 모아 헤더 고정.
- L309-310 코드 교환 시 사용자 활성 여부를 다시 보지 않는다 — 로그인 후 2분 안에 비활성화된 사용자도 토큰을 받는다(이후 API 호출은 `app/core/auth.py:227` 에서 401 이라 실효 낮음). 사용자가 삭제됐으면 `None` 으로 500.

```python path=app/api/idp.py lines=318-339
        if grant == identity.GRANT_TOKEN_EXCHANGE:
            # confidential 클라이언트(워크로드) 인증 필수 — 사용자 토큰만으로 업스트림 audience 토큰을 받아 게이트웨이를 우회하지
            # 못하게, act 는 신고값이 아니라 인증된 워크로드 (D17-①)
            wl = identity.authenticate_workload(db, client_id, client_secret)
            subject = authenticate_token(db, form.get("subject_token", ""))
            audience = form.get("audience") or form.get("resource") or ""
            scopes = form.get("scope", "").split() or None
            return JSONResponse(identity.exchange(db, subject, audience, scopes, actor=f"workload:{wl.name}"))
        if grant == "password" and settings.app_env in ("local", "test"):
            user = _login(db, form.get("username", ""), form.get("password", ""))
            if user == LOCKED:
                return _oauth_error("invalid_grant", "로그인 실패가 너무 많습니다 — 15분 뒤 다시 시도하세요.")
            if user is None or not _mfa_ok(user, form.get("otp", "")):
                return _oauth_error("invalid_grant", "자격 증명(또는 MFA 코드)이 올바르지 않습니다.")
            return JSONResponse(identity.issue_user_tokens(db, user, client_id or "dev",
                                                           form.get("scope", "openid profile")))
    except ApiError as exc:
        return _oauth_error(exc.code if exc.code.startswith("invalid") else "invalid_request", exc.detail,
                            401 if exc.status == 401 else 400)
    return _oauth_error("unsupported_grant_type", f"지원하지 않는 grant_type: {grant}")


```
**분석 / Analysis (Level 2)**
- L318-325 token-exchange: **워크로드 인증을 먼저**(L321, 실패 401 `invalid_client`) — 사용자가 자기 토큰만으로 `aud=mcp:*` 토큰을 받아 게이트웨이 승인·정책을 우회하던 1차 [중간] 경로를 닫았다(`test/test_caller_trust.py:20-34`). 그다음 subject_token 을 일반 인증 규칙으로 검증(L322 — 교환 토큰·폐기 PAT 등은 여기서 거부), `audience`(없으면 RFC 8707 `resource`), `act = workload:{인증된 이름}`.
- L326-333 password grant: local/test 에서만(SEC-11), 실패 한도·MFA 공통.
- L334-336: 서비스 `ApiError` 를 OAuth 오류 코드로 변환 — `invalid_*` 는 그대로, 나머지(예: 교환 거부 `token_exchange_failed`)는 `invalid_request`. 401 은 유지.
**짚어볼 점 / Watch-outs**
- [위험:중간] L288-337 **비동기 핸들러 안의 동기 CPU·I/O** — client_credentials(L315 → `authenticate_workload` → scrypt), token-exchange(L321 scrypt + L322 subject 가 외부 IdP 토큰이면 JWKS 동기 HTTP 최대 10초×2 — `app/core/auth.py:131-135`), password grant(L327 scrypt)가 모두 이벤트 루프 스레드에서 돈다. 이번 차수에 scrypt 가 p=5(약 5배, `app/core/security.py:45`)가 됐고 token-exchange 도 매번 scrypt 를 하게 됐다. 시나리오: 인증 없는 공격자가 예측 가능한 `client_id`(예: 시드 워크로드 `wl-a2a-demo` — `app/seed.py:186-195`, 규칙 `wl-{name}` — `app/services/identity.py:106`)에 틀린 시크릿으로 client_credentials 를 초당 수십 건 보내면, 요청마다 scrypt 시간만큼 그 워커의 이벤트 루프가 멈춰 같은 워커의 모든 요청(게이트웨이 스트리밍·MCP SSE·헬스 체크 포함)이 지연된다. `/idp/token` 에는 요청 한도가 없다. 확신: 코드 경로 확인됨, 지연 크기는 미측정(가능성). 최소 수정: 본문을 `def _token_sync(form, headers, db)` 로 빼고 `await run_in_threadpool(...)` (또는 폼을 `Form(...)` 매개변수로 받는 동기 `def` 로 전환), 실패한 클라이언트 인증에 `client_id` 별 한도, 워크로드 시크릿은 SHA-256 검증(`app/core/security.py` 보고서). 시험 공백: `/idp/token` 부하·지연 시험 없음(Epic 47 성능 시험 범위 밖).
- [위험:낮음] L326-333 password grant 는 IP 허용 목록을 보지 않는다(위 L262 [위험:중간] 에 묶음). `client_id or "dev"` 로 클라이언트 없이도 발급.
- L325 교환 결과 응답에도 `no-store` 없음(L313 Watch-out 과 같음).

```python path=app/api/idp.py lines=340-378
# ── Device Authorization Grant (RFC 8628) — nexus login --device ──
@router.post("/idp/device/code")
async def device_code(request: Request, db: Session = Depends(get_db)):
    form = dict(await request.form())
    import secrets as _s
    alphabet = "BCDFGHJKLMNPQRSTVWXZ23456789"
    user_code = "".join(_s.choice(alphabet) for _ in range(4)) + "-" + "".join(_s.choice(alphabet) for _ in range(4))
    dc = DeviceCode(device_code=token_urlsafe(32), user_code=user_code, client_id=form.get("client_id", "nexus-cli"),
                    scope=form.get("scope", "openid profile"), expires_at=utcnow() + timedelta(minutes=10))
    db.add(dc)
    db.commit()
    uri = f"{security.issuer()}/device"
    return {"device_code": dc.device_code, "user_code": user_code, "verification_uri": uri,
            "verification_uri_complete": f"{uri}?user_code={user_code}", "expires_in": 600, "interval": dc.interval}


def _device_poll(db: Session, code: str, client_id: str) -> JSONResponse:
    dc = db.get(DeviceCode, code)
    if dc is None or dc.client_id != client_id:
        return _oauth_error("invalid_grant", "device_code 가 유효하지 않습니다.")
    if dc.expires_at < utcnow():
        return _oauth_error("expired_token", "만료되었습니다.")
    now = utcnow()
    if dc.last_poll_at and (now - dc.last_poll_at).total_seconds() < dc.interval - 1:
        dc.last_poll_at = now
        db.commit()
        return _oauth_error("slow_down", "폴링 간격을 늘리세요.")
    dc.last_poll_at = now
    if dc.status == "denied":
        db.commit()
        return _oauth_error("access_denied", "거부되었습니다.")
    if dc.status != "approved":
        db.commit()
        return _oauth_error("authorization_pending", "브라우저에서 코드를 확인하세요.")
    dc.status = "used"
    user = db.get(User, dc.user_id)
    return JSONResponse(identity.issue_user_tokens(db, user, client_id, dc.scope))


```
**분석 / Analysis (Level 2)**
- L341-353 `device_code`: RFC 8628 §3.2 — `device_code`(256비트 비밀, CLI 만 앎)와 `user_code`(사람이 입력, 모음·혼동 문자 뺀 28자 알파벳 8자리 ≈ 3.8×10^11). `secrets.choice` 로 CSPRNG. 10분 만료, 폴링 간격 `interval`(기본 5초 — `app/models.py:187`).
- L356-376 `_device_poll`: 클라이언트 일치 → 만료 → 간격 위반이면 `slow_down`(§3.5) → 거부·대기 → 승인이면 `used` 로 바꾸고 토큰 발급(커밋은 `issue_user_tokens` 안).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L374 `dc.status = "used"` 는 읽기-수정-쓰기 — 인가 코드(L306)와 달리 조건부 UPDATE 가 아니다. 같은 `device_code` 로 두 워커에 동시에 폴링이 오면 둘 다 `approved` 를 읽고 각자 토큰 세트를 받는다(계열 2개). `device_code` 는 비밀이라 공격 조건은 "코드 유출" 이고, 정상 CLI 는 순차 폴링이라 영향 낮음. 최소 수정: `update(DeviceCode).where(device_code==code, status=="approved").values(status="used")` rowcount 확인.
- [위험:낮음] L341-350 인증 없는 무제한 생성 + 정리 작업 없음(`oauth_device_codes` 삭제 경로 0건) → 요청 폭주로 테이블 증가. `async def` 라 커밋도 이벤트 루프에서. 최소 수정: IP 한도 + 보존 정리(만료 후 1일).
- L375-376 승인 후 폴링까지 최대 10분 사이 비활성화된 사용자도 토큰을 받는다(L309 와 같음, 실효 낮음).

```python path=app/api/idp.py lines=379-407
@router.get("/idp/device", response_class=HTMLResponse)
def device_form(user_code: str = ""):
    extra = (f'<label for="c">디바이스 코드 / Device code</label><input id="c" name="user_code" '
             f'value="{html.escape(user_code)}" required>')
    return _page("CLI 로그인 — 터미널에 표시된 코드를 확인하세요", {}, button="승인", extra=extra)


@router.post("/idp/device", response_class=HTMLResponse)
def device_submit(db: Session = Depends(get_db), user_code: str = Form(...), username: str = Form(...),
                  password: str = Form(...), otp: str = Form("")):
    dc = db.scalars(select(DeviceCode).where(DeviceCode.user_code == user_code.strip().upper())).first()
    user = _login(db, username, password)
    extra = (f'<label for="c">디바이스 코드 / Device code</label><input id="c" name="user_code" '
             f'value="{html.escape(user_code)}" required>')
    if dc is None or dc.expires_at < utcnow() or dc.status != "pending":
        return _page("CLI 로그인", {}, button="승인", error="코드가 유효하지 않거나 만료되었습니다.", extra=extra)
    if user == LOCKED:
        return _page("CLI 로그인", {}, button="승인", error="로그인 실패가 너무 많습니다 — 15분 뒤 다시 시도하세요.", extra=extra)
    if user is None:
        return _page("CLI 로그인", {}, button="승인", error="사용자 이름 또는 비밀번호가 올바르지 않습니다.", extra=extra)
    if not _mfa_ok(user, otp):  # 브라우저 로그인과 같은 MFA 요구 (SCR-A05 ③)
        return _page("CLI 로그인", {}, button="승인", error="MFA 코드가 필요하거나 올바르지 않습니다 (인증 앱의 6자리).", extra=extra)
    dc.status, dc.user_id = "approved", user.id
    db.commit()
    audit.append("auth.device_approved", actor=user.sub, data={"client_id": dc.client_id})
    return HTMLResponse("<!doctype html><meta charset=utf-8><title>승인됨</title>"
                        "<p style='font:16px system-ui;text-align:center;margin-top:20vh'>승인했습니다. 터미널로 돌아가세요.</p>")


```
**분석 / Analysis (Level 2)**
- L379-383 `device_form`: `verification_uri_complete` 의 `user_code` 를 미리 채운 폼(escape).
- L386-405 `device_submit`: 코드 조회(대문자·공백 정규화) → 로그인(실패 한도 공통) → 코드 상태 → 잠금·실패 → MFA(Epic 39 추가) → 승인·감사.
**짚어볼 점 / Watch-outs**
- (위 L262 [위험:중간] 의 한 경로) IP 허용 목록 확인이 없다.
- [위험:낮음] L393-400 실패(코드 무효·비밀번호·MFA·잠금)는 감사에 남지 않는다 — 브라우저 폼(L253-272)은 모두 남긴다. 디바이스 흐름을 이용한 추측은 실패 한도 카운터에만 흔적이 남는다. 최소 수정: 같은 `auth.login` deny 감사(`data.flow="device"`).
- L389-390 코드가 무효여도 `_login` 을 먼저 실행 — 실패 한도는 소모되지만 비밀번호 추측 결과(성공·실패)가 응답 문구로 드러나지 않게 코드 무효 문구가 먼저 나온다(L393-394). 다만 유효 코드 + 맞는 비밀번호 + MFA 미등록이면 공격자가 **자기** 디바이스 코드를 피해자 계정으로 승인하는 것이므로, 이 순서가 피싱(RFC 8628 §5.4 원격 피싱)을 막지는 않는다 — 승인 화면에 `client_id`·요청 위치를 보여 주는 것이 표준 완화책.

```python path=app/api/idp.py lines=408-421
@router.get("/idp/userinfo")
def userinfo(p: Principal = Depends(current_principal)):
    return {"sub": p.sub, "name": p.name, "email": p.email, "roles": p.roles, "groups": p.groups, "team": p.team}


@router.post("/idp/revoke")
async def revoke(request: Request, db: Session = Depends(get_db)):
    form = dict(await request.form())
    raw = form.get("token", "")
    row = db.scalars(select(RefreshToken).where(RefreshToken.token_hash == security.token_hash(raw))).first()
    if row and not row.revoked_at:
        row.revoked_at = utcnow()
        db.commit()
    return JSONResponse({}, status_code=200)  # RFC 7009: 존재 여부와 무관하게 200
```
**분석 / Analysis (Level 2)**
- L408-410 `userinfo`: OIDC UserInfo — 인증된 주체의 표시 정보. 워크로드·Access Token 주체도 호출 가능(클레임 대부분 None).
- L413-421 `revoke`: RFC 7009 — refresh token 만 지원, 존재 여부와 무관하게 200(토큰 존재 오라클 방지). 공개 클라이언트용이라 클라이언트 인증이 없다(RFC 7009 §2.1 허용 — 토큰을 아는 자만 폐기 가능).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L418-420 로그아웃 폐기가 회전 폐기와 같은 `revoked_at` 만 쓴다 → 로그아웃한 토큰이 10초 뒤 다시 오면 `auth.refresh_reuse`(유출 신호) 감사가 남는다(`app/services/identity.py:82-90` — services/identity 보고서 [위험:낮음]). 또 폐기는 그 세션의 액세스 토큰을 끊지 않는다(최대 60분 유효). 로그아웃 자체의 감사도 없다.
- L414 `async def` + 동기 DB(짧은 쿼리 — 영향 작음, 패턴 일관성 문제).

## 파일 평가 / File Assessment
- 강점: (1) 표준 흐름(인가 코드 + PKCE S256, refresh 회전, RFC 8628 디바이스, RFC 8693 교환, RFC 7009 폐기)을 작은 코드로 정확히 구현 — 인가 코드 1회 사용 조건부 UPDATE, redirect_uri 의 netloc 비교, 모든 HTML 동적 값 escape. (2) 이번 차수에 실패 한도(정규화 키·잠금 중 비밀번호 미계산)·디바이스 MFA·loopback 운영 차단·로그인 시 재해싱·token-exchange 워크로드 인증(`act`=인증된 워크로드)이 들어갔고 각각 회귀 시험이 있다. (3) 오류 응답이 존재 여부를 흘리지 않도록 같은 문구(`invalid_grant`, revoke 200).
- 위험: (1) [위험:중간] `async def token` 안의 동기 scrypt(p=5)·DB·JWKS HTTP — 인증 없는 이벤트 루프 차단 DoS(`app/api/idp.py:288-337`). (2) [위험:중간] 로그인 IP 정책이 비밀번호 폼에만 — 디바이스·SSO·password grant 우회(`:262-267` vs `:386-405`·`:229`·`:326-333`). (3) [위험:중간] MFA 실패 무제한·재사용 허용(`:120-129`·`:268-272`·`:399-400`), [위험:중간] 사용자 이름 단독 고정 창 실패 한도 — 표적 잠금 DoS·분사·비원자(`:104-117`).
- 개선 권고(영향순): (1) `/idp/token` 본문을 스레드풀로 + 클라이언트 인증 실패 한도 + 워크로드 시크릿 SHA-256 검증. (2) IP 정책·MFA 실패 집계를 `_login` 다음 공통 단계로 묶어 모든 로그인 경로(브라우저·디바이스·password·브로커 콜백)에 적용, TOTP 스텝 재사용 거부. (3) 실패 한도를 원자 증가 + (사용자 이름, IP) 이원화 + 점진 지연으로, POST 로그인에도 `_authorize_params`(PKCE) 적용, 토큰 응답 `no-store` 통일, Basic 디코드 예외 처리, 디바이스 코드 조건부 UPDATE·정리 작업, 입력 길이 제한.
- 파일 위험 등급 / Risk grade: 높음 — 인터넷에 노출되는 인증 경계 핵심 모듈에 [위험:중간] 4건(가용성 1·정책 우회 1·MFA 1·무차별 대입 방어 설계 1).
- 재읽기 권고 레벨: 2 — 모든 줄이 인증 경계. FastAPI 의 async/sync 실행 위치와 OAuth RFC 조항을 옆에 두고 읽을 것.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 1차 위임 보안 리뷰 SEC-03 [HIGH] 로그인 무차별 대입 방어 없음(`idp.py:98-103, 229-260`) | 부분 해결 — 사용자 이름별 10회/15분 잠금(브라우저·디바이스·password grant). IP 축 없음·표적 잠금 DoS·비원자 확인-증가·MFA 실패 미집계는 [위험:중간] 2건으로 남김 | `app/api/idp.py:98-117`, `:253-257`, `:328-329`, `:395-396`, `test/test_review_fixes.py:95-117` |
| SEC-05 [MEDIUM] 운영에서도 localhost redirect_uri 허용 | 해결 — local/test 에서만. 단 `app_env` 기본값 `local` 에 기댐 | `app/api/idp.py:65`, `app/core/config.py:17`, `test/test_review_fixes.py:119-127` |
| SEC-06 [MEDIUM] 로그인 폼 CSRF 토큰 없음 | 미해결(의도적 미수정 — 39th 판정). 콘솔의 `state` 확인으로 완화됨을 확인, 서버는 POST 에서 PKCE 를 다시 요구하지 않음 | `app/api/idp.py:244-249`, `web/src/lib/auth.tsx:75-77`, `test/impl/39th/scenario.md:53` |
| SEC-11 [LOW] password grant 비운영 환경 활성화 — 오설정 시 노출, 기동 경고 권고 | 미해결 — 게이트는 그대로 유효하나 `app_env` 기본 `local`, 기동 경고 없음 | `app/api/idp.py:40`, `:326`, `app/core/config.py:17` |
| SEC-12 [LOW] client_id 별 redirect_uri 사전 등록 없음 | 미해결(의도적 — 39th 판정). 비공개 client_id 는 PKCE·시크릿 없이 코드 교환 가능 | `app/api/idp.py:61-67`, `:152`, `:299-311`, `test/impl/39th/scenario.md:58` |
| 1차 `app/services/identity.py.md` L261 [위험:중간] `/idp/token` token-exchange grant 가 클라이언트 인증 없이 임의 subject_token 으로 교환(`idp.py:313-317`) | 해결(이 경로) — 워크로드 `client_id`+`client_secret` 필수, `act`=인증된 워크로드. 워크로드별 교환 허용 목록은 없음(services/identity 보고서 잔여 [위험:낮음]) | `app/api/idp.py:318-325`, `test/test_caller_trust.py:20-34` |
| 1차 INDEX §4.6-3 "authorization code 1회 교환 읽기→쓰기" (39th Lv2 경쟁) | 해결 — 조건부 UPDATE rowcount | `app/api/idp.py:306-308` |
| (39th 추가) 디바이스 승인이 MFA 미확인 | 해결 | `app/api/idp.py:399-400`, `test/test_review_fixes.py:130-136` |
| 1차 위임 보안 리뷰 checked-OK "PKCE(공개 클라이언트) `code_challenge` 필수" | 부분 해결 — GET 폼·브로커 시작은 강제, POST 로그인은 미강제 | `app/api/idp.py:152-153`, `:249` |
| 1차 위임 보안 리뷰 checked-OK "MFA(TOTP) 6자리 필수" | 부분 해결 — 검증은 있으나 실패 횟수 제한·스텝 재사용 방지가 없음([위험:중간]) | `app/api/idp.py:120-129`, `app/services/totp.py:25-31` |
