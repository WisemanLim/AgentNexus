# 보안 리뷰 (2차, security-review 폴백)

> **대상:** wise-AgentNexus (FastAPI + SQLAlchemy 2, Python 3.12) — MCP Gateway + AI Gateway + A2A Gateway
> **날짜:** 2026-10-07
> **방법:** 정적 읽기 (소스 코드 분석, 런타임·네트워크 접근 없음). Claude Code `/security-review` 가 이 세션에서 호출 불가하여 `oh-my-claudecode:security-reviewer` 서브에이전트로 폴백.
> **범위:** 1차 리뷰(SEC-01..12) 이후 변경된 52개 파일 + 그 사이 추가된 수정(egress guard, EXTERNAL_ALGS, login lockout, refresh family reuse detection, scrypt 강화, A2A task ownership, gRPC limits, stdio full-command matching 등)
>
> 아래는 서브에이전트 결과 원문이다. 검증·처리 결과는 `../VERIFY.md` 에 따로 적는다(원문은 고치지 않음).

---

## 요약 -- 심각도별 건수

| 심각도 | 건수 |
|--------|------|
| Critical | 0 |
| High | 1 |
| Medium | 4 |
| Low | 3 |
| **합계** | **8** |

**전체 위험 수준: MEDIUM** -- Critical 없음. 1차 리뷰의 High 3건은 모두 수정됨. 새로 발견한 High 1건(hosted-openapi httpx2 클라이언트 egress guard 미적용)과 Medium 4건이 남아 있으나 모두 인증된 관리자 경로이거나 제한적 영향.

---

## 발견 사항

### SEC2-01 | HIGH | hosted-openapi httpx2 클라이언트가 egress guard 를 우회한다

**위치:** `app/services/mcp_gateway.py:555-587`
**카테고리:** A10 SSRF
**확신도:** Confirmed
**악용 가능성:** 원격, 인증 필요 (mcp:write — developer 역할 이상)

**근거:**
`_hosted_server()` 는 `httpx2.AsyncClient` 를 직접 생성한다. 이 클라이언트에는 `app/core/http.py` 의 `_inject_async` / `_guard_async` event hook 이 걸려 있지 않다. `blocked_url()` 검사가 생성 시점(574행)에 `base_url` 에 대해서만 수행되지만, 이후 OpenAPI 스펙이 정의하는 개별 경로(path)가 base_url 과 다른 서버를 가리키는 경우(`servers[].url` 상대/절대 경로) 나, httpx2 redirect following 이 활성화된 경우 요청이 egress guard 없이 임의 대상에 도달할 수 있다.

```python
# mcp_gateway.py:574-582
if mount is None and await run_in_threadpool(blocked_url, base):  # base_url만 검사
    raise RpcError(...)
client = httpx2.AsyncClient(base_url=base, headers=headers, ...)
# → event_hooks 에 egress guard 없음; OBO 일 때만 _inject_obo
```

또한 OBO 모드가 아닌(`hosted.get("identity") != "obo"`) hosted-openapi 서버는 `event_hooks` 자체가 빈 dict 이므로, 요청 시점의 IP 검사가 전혀 없다.

**실패 시나리오:**
1. operator 가 hosted-openapi MCP 서버를 등록하면서 `base_url=https://legitimate-api.com` (egress 통과), `openapi` 스펙의 `servers[0].url` 또는 개별 경로에 `http://169.254.169.254/latest/meta-data/` 를 포함시킨다.
2. FastMCP `from_openapi` 가 해당 경로를 도구로 노출하고, 사용자가 해당 도구를 호출하면 httpx2 클라이언트가 metadata endpoint 에 직접 요청한다.

**영향 범위:** 클라우드 IMDS 자격 증명 탈취, 내부 서비스 접근. 단, MCP 서버 등록에는 mcp:write(developer 이상) 권한이 필요하고, OpenAPI 스펙이 관리자가 제출하는 값이므로 악용에는 특권 계정 탈취가 선행되어야 한다.

**권고:**
```python
# httpx2 클라이언트에도 egress guard hook 적용
from app.core.http import _guard_async
client = httpx2.AsyncClient(
    base_url=base, headers=headers, timeout=settings.upstream_timeout,
    **({"transport": transport} if transport else {}),
    event_hooks={"request": [_guard_async] + ([_inject_obo] if obo else [])})
```

---

### SEC2-02 | MEDIUM | DNS rebinding: egress guard 가 이름 해석과 연결 사이 TOCTOU 에 취약

**위치:** `app/core/http.py:39-72, 128-137`
**카테고리:** A10 SSRF
**확신도:** Likely
**악용 가능성:** 원격, 인증 필요, 타이밍 의존

**근거:**
egress guard 는 요청 전 `getaddrinfo` 로 호스트를 해석하고 결과를 60초 캐시(`_RESOLVED`)에 저장한 뒤, httpx 에 요청을 넘긴다. 그러나 httpx 가 실제로 TCP 연결할 때는 OS 리졸버가 다시 해석한다. 공격자가 TTL 0 DNS 레코드를 써서 첫 해석에는 공인 IP, 두 번째 해석(httpx 연결)에는 169.254.169.254 를 돌려주면 guard 를 통과한 뒤 metadata 에 연결할 수 있다.

캐시 TTL 이 60초이므로 공격 창은 첫 요청 직후 60초 동안만 열리지만, 공격자가 TTL 0 레코드를 제어하면 재현 가능하다.

```python
# http.py:64-65 — 캐시가 있으면 재해석 없이 통과
hit = _RESOLVED.get(host)
return host, (hit[1] if hit and time.monotonic() - hit[0] < 60 else None)
# → 캐시 miss 일 때만 해석, httpx 자체는 별도로 해석
```

**실패 시나리오:** 공격자가 관리하는 도메인 `evil.example.com` 을 MCP 서버 endpoint / A2A agent URL / push URL 등 사용자 입력 URL 에 넣고, DNS 레코드를 첫 질의엔 공인 IP → 두 번째 질의엔 169.254.169.254 로 전환한다.

**영향 범위:** IMDS 자격 증명 탈취. 단, httpx 기본 redirect following 이 꺼져 있으므로 redirect 기반 rebinding 은 불가.

**권고:** httpx 의 `transport` 를 커스터마이징하여 연결 시점에도 해석된 IP 를 검사하거나, `httpcore` 의 `AsyncHTTPTransport` 를 감싸서 연결 직전 IP 를 검증한다.

---

### SEC2-03 | MEDIUM | IdP `internal_url` / `discover` 엔드포인트에 egress guard 가 적용되지 않는 경로가 남아 있다

**위치:** `app/api/identity.py:280-296` (`_discover_check`), `app/services/broker.py:52-53` (`discover`)
**카테고리:** A10 SSRF
**확신도:** Confirmed
**악용 가능성:** 원격, 인증 필요 (identity:write — security/operator 역할)

**근거:**
`POST /api/v1/identity/idps/discover` 는 `DiscoverIn.issuer` 와 `DiscoverIn.internal_url` 을 받아 `broker.discover()` 를 호출한다. `broker.discover()` 는 `(internal_url or issuer)` 를 base 로 `/.well-known/openid-configuration` 을 요청한다. 이 요청은 `sync_client()` 를 통해 egress guard(link-local/IMDS 차단) 가 적용되지만, `internal_url` 은 사설 IP(10.x, 172.16.x, 192.168.x)를 의도적으로 허용하는 설계이다.

그러나 discovery 응답에서 받은 `jwks_uri`(`_discover_check` 291행: `meta["jwks_uri"]`)를 `broker.srv()` 를 거쳐 다시 fetch 하는데, `srv()` 는 `issuer` 접두사가 일치하면 `internal_url` 로 치환하되, **`jwks_uri` 가 `issuer` 와 다른 도메인이면 그대로 통과한다**. 악의적 외부 IdP 가 `jwks_uri: http://169.254.169.254/...` 를 돌려주면 egress guard 의 IMDS 차단이 동작하겠지만, `http://10.0.0.1:5432/` 같은 사설 주소는 egress guard 가 차단하지 않는다(설계상 사설망 허용).

```python
# broker.py:52-53
def discover(issuer: str, internal_url: str | None = None) -> dict:
    base = (internal_url or issuer).rstrip("/")
    # → identity:write 가 internal_url 을 임의로 넣을 수 있고, base 가 사설 IP 면 그대로 요청
```

**실패 시나리오:** security 역할 사용자가 `POST /api/v1/identity/idps/discover` 에 `internal_url=http://10.0.0.1:6379/` 를 넣으면 Valkey 포트를 probe 할 수 있다. 또는 악의적 외부 IdP 의 discovery 가 `jwks_uri` 를 사설 주소로 응답하면 해당 주소로 GET 이 발생한다.

**영향 범위:** 사설망 포트 스캔·서비스 probe. 특권 역할(identity:write) 필요. IMDS 는 기존 egress guard 가 차단.

**권고:** `_discover_check` 에서 discovery 응답의 `jwks_uri` 가 `issuer` 와 같은 origin 인지 검증하고, `DiscoverIn.internal_url` 에 대해 `internal_url()` 검사를 하되 관리자 의도(split DNS)를 구분하는 확인 플래그를 추가한다.

---

### SEC2-04 | MEDIUM | scrypt `_parse` 가 DB 에 저장된 해시의 파라미터를 신뢰한다 — 파라미터 다운그레이드 가능

**위치:** `app/core/security.py:56-70`
**카테고리:** A02 Cryptographic Failures
**확신도:** Likely
**악용 가능성:** 로컬(DB 쓰기 접근 필요)

**근거:**
`_parse()` 는 저장된 해시 문자열에서 `n`, `r`, `p` 를 그대로 추출해 `check_password()` 에 전달한다. DB 에 직접 접근할 수 있는 공격자(SQL injection 이 없으므로 DB 직접 접근 = DBA/관리자)가 해시의 파라미터를 `n=1,r=1,p=1` 로 교체하면, `check_password` 가 해당 약한 파라미터로 검증을 수행한다. 이 자체는 비밀번호를 노출하지 않지만, 공격자가 약한 파라미터로 해시한 값을 삽입하면 임의 비밀번호로 로그인 가능하다.

다만 `needs_rehash()` 가 로그인 성공 시 현재 파라미터로 재해싱하므로, 다운그레이드된 해시는 로그인 1회 후 강한 파라미터로 올라간다.

**실패 시나리오:** DB 직접 쓰기 접근이 있는 공격자가 관리자의 `password_hash` 를 `scrypt$n=1,r=1,p=1$<salt>$<dk>` 형태로 교체. 하지만 DB 쓰기가 가능한 시점에서 이미 게임 오버에 가깝다.

**영향 범위:** 매우 제한적 — DB 직접 접근이 전제. 파라미터 하한 검증이 없다는 방어 심층(defense-in-depth) 부재가 본질.

**권고:**
```python
def _parse(stored: str) -> tuple[dict, bytes, bytes] | None:
    # ... 기존 파싱 ...
    if params["n"] < 2**14 or params["r"] < 8 or params["p"] < 1:
        return None  # 비정상적으로 약한 파라미터는 검증 거부
```

---

### SEC2-05 | MEDIUM | 보안 헤더 미설정 — CSP, X-Frame-Options, HSTS 없음

**위치:** `app/main.py` (전체), `app/api/idp.py:70-95` (HTML 로그인 폼)
**카테고리:** A05 Security Misconfiguration
**확신도:** Confirmed
**악용 가능성:** 원격, 인증 불필요

**근거:**
FastAPI 앱에 보안 응답 헤더가 전혀 설정되어 있지 않다:
- `Content-Security-Policy`: 미설정 — XSS 완화 레이어 부재
- `X-Frame-Options` / `frame-ancestors`: 미설정 — clickjacking 가능 (로그인 폼 iframe 삽입)
- `Strict-Transport-Security`: 미설정 — HTTPS 운영 시에도 다운그레이드 가능
- `X-Content-Type-Options: nosniff`: 미설정
- `Referrer-Policy`: 미설정

CORS 미들웨어도 없으며(API 가 SameSite 이므로 의도적일 수 있음), 로그인 HTML 폼(`_PAGE`)에도 CSP meta 태그가 없다.

**실패 시나리오:** 공격자가 `<iframe src="https://agentnexus.example/idp/authorize?...">` 로 로그인 폼을 삽입하고 clickjacking 으로 자격 증명을 가로챈다.

**권고:** FastAPI 미들웨어로 보안 헤더를 일괄 추가한다:
```python
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response
```

---

### SEC2-06 | LOW | LIKE 와일드카드 주입 미수정 — 여러 검색 엔드포인트

**위치:** `app/api/identity.py:132-133`, `app/api/chat.py:73-74`, `app/api/agents.py:123`, `app/api/observe.py:135,191`, `app/api/ops.py:540-541`
**카테고리:** A03 Injection
**확신도:** Confirmed
**악용 가능성:** 원격, 인증 필요 (각 엔드포인트의 해당 권한)

**근거:** 1차 리뷰 SEC-08 과 동일. `f"%{q}%"` 로 사용자 입력을 LIKE 패턴에 삽입하며, `%`/`_` 이스케이프가 없다. SQL injection 은 아니지만 `q=%` 로 전체 레코드 조회가 가능하다.

**영향 범위:** 제한적 정보 노출 — 해당 권한 범위 내의 데이터. 1차 리뷰 후에도 미수정.

**권고:** 1차 리뷰 SEC-08 권고 동일.

---

### SEC2-07 | LOW | CSRF — OAuth 로그인/디바이스 폼에 CSRF 토큰 없음 (미수정)

**위치:** `app/api/idp.py:244-280` (`authorize_submit`), `app/api/idp.py:386-405` (`device_submit`)
**카테고리:** A01 Broken Access Control
**확신도:** Possible
**악용 가능성:** 원격, 인증 불필요

**근거:** 1차 리뷰 SEC-06 과 동일. `POST /idp/authorize` 와 `POST /idp/device` HTML 폼에 CSRF 토큰이 없다. 폼이 쿠키 기반 세션을 사용하지 않으므로 전통적 CSRF 는 제한적이지만, Login CSRF(공격자 자격 증명으로 피해자 브라우저에서 로그인시키기)는 여전히 가능하다.

**영향 범위:** 제한적 — Login CSRF 만 해당.

**권고:** 1차 리뷰 SEC-06 권고 동일.

---

### SEC2-08 | LOW | `redirect_uri` 에 per-client 사전 등록(exact match) 미구현 (미수정)

**위치:** `app/api/idp.py:61-67` (`_check_redirect`)
**카테고리:** A07 Auth Failures
**확신도:** Confirmed
**악용 가능성:** 원격, 인증 불필요

**근거:** 1차 리뷰 SEC-12 와 동일. `_check_redirect` 는 origin 일치만 검사하고, `client_id` 별 `redirect_uri` 사전 등록·정확 일치(OAuth 2.1 요구사항) 가 없다. localhost 허용이 `app_env in ("local", "test")` 로 조건화된 것은 SEC-05 수정으로 확인 완료.

**영향 범위:** 같은 origin 내 open redirect 가 있는 경로와 조합하면 인가 코드 탈취 가능. PKCE 가 공개 클라이언트에 강제되므로 공개 클라이언트 경로는 방어됨.

**권고:** 1차 리뷰 SEC-12 권고 동일.

---

## 이전 지적 재확인

| ID | 제목 | 상태 | 근거 |
|-----|------|------|------|
| SEC-01 | JWT Algorithm Confusion | **Fixed** | `app/core/auth.py:141-148` — `EXTERNAL_ALGS` 허용 목록 추가, `alg not in EXTERNAL_ALGS` 이면 `InvalidAlgorithmError`. `jwt.decode(..., algorithms=[alg])` 로 단일 알고리즘만 전달하되 허용 목록에서 사전 검증. |
| SEC-02 | SSRF — OpenAPI fetch | **Fixed** | `app/core/http.py:36-37,123-137` — `sync_client` / `async_client` 모두 `event_hooks` 에 `_guard_sync` / `_guard_async` 적용. `_BLOCKED_NETS` 에 169.254/16, fe80::/10, fd00:ec2::254. `_BLOCKED_HOSTS` 에 metadata.google.internal 등. `blocked_url()` 로 사전 검사도 가능. `fetch_openapi` (mcp.py:557) 은 `sync_client` 경유로 guard 적용됨. |
| SEC-03 | 로그인 Brute Force | **Fixed** | `app/api/idp.py:98-117` — `LOGIN_MAX_FAILURES=10`, `LOGIN_WINDOW_S=900`, `ratelimit.backend().window_get/window_add` 으로 사용자명 기반 실패 횟수 추적. 한도 초과 시 `LOCKED` 반환, 비밀번호 검증 자체를 생략. Valkey 백엔드 시 워커간 공유. |
| SEC-04 | SSRF — Push URL | **Fixed** | `app/services/a2a_gateway.py:639-645` — `push_url_problem()` 에서 `internal_url()` (사설/루프백/link-local 거부) 검사. prod 에서만 적용 (`app_env not in ("local", "test")`). |
| SEC-05 | Open Redirect — localhost | **Fixed** | `app/api/idp.py:65` — `dev = settings.app_env in ("local", "test") and ...` 조건 추가. prod 에서 localhost redirect_uri 거부. |
| SEC-06 | CSRF — 로그인 폼 | **Not fixed** | 로그인 폼에 CSRF 토큰 없음 (SEC2-07 로 재보고). |
| SEC-07 | Access Token subject IDOR | **Fixed** | `app/api/identity.py:470-473` — `body.subject != p.sub and db.scalars(select(User.id).where(User.sub == body.subject)).first()` 이면 거부. 다른 사용자의 sub 로 발급 차단, 서비스 주체(a2a:..., mcp:...) 만 허용. |
| SEC-08 | LIKE 와일드카드 주입 | **Not fixed** | 여러 엔드포인트에서 동일 패턴 잔존 (SEC2-06 으로 재보고). |
| SEC-09 | IdP JWKS SSRF | **Partial** | egress guard 가 link-local/IMDS 는 차단하지만, discovery 응답의 `jwks_uri` 가 issuer 와 다른 도메인(사설 IP 포함)인 경우 검증 없음 (SEC2-03 으로 재보고). |
| SEC-10 | scrypt 비용 파라미터 | **Fixed** | `app/core/security.py:45` — `SCRYPT = {"n": 2**14, "r": 8, "p": 5}`. p=5 로 올려 연산 강도 5배 증가. `_LEGACY` (p=1) 해시는 로그인 성공 시 자동 재해싱(`needs_rehash`). OWASP 가 권장하는 n=2^17 에는 미달이나 D17-④ 주석에서 메모리 16MiB 제약을 근거로 p 증가로 보상한 설계. |
| SEC-11 | Password Grant | **Fixed** | `app/api/idp.py:326` — `settings.app_env in ("local", "test")` 조건. Helm configmap (`APP_ENV: prod`) 으로 prod 에서 비활성. |
| SEC-12 | redirect_uri 사전 등록 | **Not fixed** | per-client exact match 미구현 (SEC2-08 로 재보고). |

---

## 확인했으나 문제 없음

| 항목 | 결과 |
|------|------|
| SQL Injection (SQLAlchemy ORM) | OK — 전체 코드가 파라미터화 쿼리 사용. raw SQL 없음 |
| 내장 JWT 서명/검증 (verify_local) | OK — `algorithms=["RS256"]` 하드코딩, aud/iss/exp/sub 필수 |
| 외부 IdP JWT 알고리즘 혼동 | OK (수정됨) — `EXTERNAL_ALGS` 허용 목록 적용 |
| 토큰 교환 scope escalation | OK — `TokenExchangePolicy.allowed_scopes` 교차 후 허용만 부여 |
| 토큰 교환 subject IDOR | OK (수정됨) — workload auth 필수 (`authenticate_workload`) |
| Refresh Token family reuse detection | OK — `migration 0019`, `refresh()` 에서 폐기된 토큰 재제시 시 전체 family 폐기 |
| Refresh Token rotation | OK — 조건부 UPDATE 로 동시 사용 방지, 10초 grace period |
| Access Token subject IDOR | OK (수정됨) — 다른 사용자 sub 로 발급 차단 |
| egress guard IPv4-mapped IPv6 | OK — `ip.ipv4_mapped` 처리 (`http.py:51-52`), `::ffff:169.254.x.x` 차단 확인 |
| egress guard metadata hostnames | OK — `metadata`, `metadata.google.internal`, `metadata.goog` 차단 |
| egress guard AWS IMDS IPv6 | OK — `fd00:ec2::254/128` 차단 |
| httpx redirect following | OK — httpx 기본값 `follow_redirects=False` (redirect 기반 SSRF 불가) |
| 비밀번호 타이밍 공격 | OK — `hmac.compare_digest()` 사용 |
| 비밀번호 강도 (scrypt) | OK — n=2^14, r=8, p=5, 레거시 재해싱 |
| 로그인 brute force | OK (수정됨) — 사용자명 기반 10회/15분 잠금 |
| Secrets 평문 노출 | OK — API 응답에 `masked()`, Fernet 암호화 |
| Opaque token hash | OK — SHA-256 단방향 |
| MFA (TOTP) | OK — mfa_enabled 사용자 필수 |
| 세션 강제 종료 | OK — `tokens_not_before` 검사 |
| 승인 직무 분리 | OK — `can_decide` 에서 requester != approver 검증 |
| 도구 기본 거부 | OK — ToolRule 없는 도구는 노출·호출 모두 거부 |
| stdio 전체 명령 일치 | OK (수정됨) — `stdio_allowed()` 에서 공백 있는 항목은 전체 명령+인자 일치 |
| A2A task ownership | OK — `_check_task_owner` 로 다른 주체의 Task 접근 차단 |
| gRPC server limits | OK — `MAX_RECEIVE_BYTES=4MB`, `max_concurrent_streams=100`, keepalive, idle timeout |
| gRPC outbound TLS | OK — `grpc_credentials()` 로 사설 CA / mTLS 지원 |
| Helm securityContext | OK — `runAsNonRoot`, `readOnlyRootFilesystem`, `capabilities: {drop: [ALL]}`, `seccompProfile: RuntimeDefault` |
| Helm NetworkPolicy | OK — backend(DB/Valkey/demo) 는 api 파드에서만 ingress 허용 |
| Dockerfile non-root | OK — `USER app` (uid 10001), runtime stage |
| Dockerfile digest pinning | OK — `python:3.12-slim@sha256:...`, `node:22-alpine@sha256:...` |
| CI permissions | OK — `contents: read` 최소 권한 |
| CI actions SHA pinning | OK — commit SHA 고정, Dependabot github-actions 갱신 |
| Dependabot | OK — uv, npm, docker, github-actions 주간 갱신 |
| Compose network isolation | OK — `backend: internal: true`, api 만 edge+backend |
| A2A push relay | OK — `post_with_retry` 경유 → egress guard 적용, push_token 으로 Task 매칭 |
| policy caller_context header trust | OK — `agent` 는 기계 주체만, `run_id` 는 소유자만, `data_class` 는 `declared_data_class` 로 서버 판정 |
| data_class header downgrade | OK — `data:declare` 권한 없으면 기본값으로 올림 |
| multi-worker lease | OK — `scheduler.lease` 로 mcp_health, a2a_watch 등 1 워커만 실행 |
