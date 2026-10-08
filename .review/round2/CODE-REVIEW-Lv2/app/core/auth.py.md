> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md) · 1차 리뷰: [auth.py.md](../../../../CODE-REVIEW-Lv2/app/core/auth.py.md)

### 05. app/core/auth.py
**파일 요약 / File Summary**
- 역할 / Role: 인증·인가 핵심. `Principal`(호출 주체), 역할→권한 RBAC 표(`ROLE_PERMS`)·스코프 묶음(`SCOPE_GRANTS`)·재정의 5초 캐시, Bearer 토큰 유형별 해석(PAT·클라이언트 키·내장 JWT user/delegation/workload/access_token·외부 IdP JWT + JIT 프로비저닝), FastAPI 의존성(`optional_principal`·`current_principal`·`require`).
- 진입점 거리 / Distance: 2 (`app/main.py` → `app/api/*` 의 `Depends(require(...))`/`current_principal` → 이 모듈). `authenticate_token` 은 `app/api/idp.py:322`(token-exchange subject_token)에서도 직접 호출.
- LOC: 295 (현재 `wc -l`) · 1차 295 → 0 (내용 4줄 변경)
- 주요 export: `Principal`, `ROLE_PERMS`, `SCOPE_GRANTS`, `SCOPED_KINDS`, `ROLE_LABELS`, `role_perms`, `rbac_overrides`, `reset_rbac_cache`, `EXTERNAL_ALGS`, `authenticate_token`, `bearer`, `optional_principal`, `current_principal`, `require`, (외부에서 쓰는 내부 심볼) `_user_principal`(`app/api/setup.py`), `_JWKS`(`app/api/identity.py:8`·`:235`), `_map_roles`(`app/api/ops.py:115`)
- 의존(내부): `app.core.{security,errors,http,ids}`, `app.db`, `app.models.{AccessToken,IdpConnection,PersonalToken,User,Workload,Setting}`
- 외부 패키지: pyjwt[crypto], fastapi, sqlalchemy
- 부수효과 / Side effects: DB 쓰기(PAT·Access Token `last_used_at`, 외부 사용자 upsert — 매 요청 commit), 외부 IdP discovery·JWKS 동기 HTTP, 프로세스 전역 캐시 `_RBAC`·`_JWKS`
- 동시성 / Concurrency: 동기 함수 — FastAPI 동기 의존성은 스레드풀(anyio 기본 40)에서 실행. 단 `app/api/idp.py:322` 는 `async def token` 안에서 직접 호출하므로 그 경로에서는 DB·JWKS HTTP 가 이벤트 루프에서 돈다. 전역 dict 캐시는 락 없이 통째 교체.
- 테스트 커버리지 흔적: `test/test_identity.py:18-33`(401·RBAC), `:206-227`(외부 OIDC 연합), `test/test_review_fixes.py:37-53`(`test_external_idp_alg_allowlist` — HS256 위조·`none` 거부), `test/test_hardening.py:98-105`(admin PAT 스코프 우회 불가), `test/test_caller_trust.py:48-71`(`data:declare` 회수), `test/test_llm.py:198-207`(클라이언트 키는 관리 API 불가), `test/test_ops.py`(RBAC 재정의·세션 종료)
- 2차 변경 요약 / Changes since 1st review: 1차 인용 원문과 줄 단위 비교 결과 **권한 표 4줄만 변경** — `data:declare` 권한을 `_USER`(L20, 따라서 developer·operator 상속)·`security`(L29)·`finops`(L32)·`SCOPE_GRANTS["gateway:invoke"]`(L38)에 추가(Epic 40 D17-①: 기본값보다 낮은 데이터 등급 신고 권한 — `test/impl/40th/scenario.md:9`, 소비처 `app/services/policy.py:20`·`:43`). 그 밖의 코드는 1차와 동일(빈 줄 차이만). 즉 1차 Watch-out 대부분은 그대로 남아 있다.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/auth.py lines=1-19
"""인증·인가 — Principal, 역할→권한(RBAC), Bearer 토큰 해석 (FR-I01·I02·I04, SCR-L03).

허용 토큰: 내장 issuer JWT(user · workload · access_token · delegation), 외부 IdP JWT(OIDC 연합, JIT 프로비저닝),
PAT(`anx_pat_`), 게이트웨이 클라이언트 키(`anx_key_`). 대상(aud) 이 'agentnexus' 가 아닌 교환 토큰은 거부한다.
"""
import time
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.errors import forbidden, unauthorized
from app.core.http import sync_client
from app.db import get_db, utcnow
from app.models import AccessToken, IdpConnection, PersonalToken, User, Workload

```
**분석 / Analysis (Level 2)**
- L1-5: 허용 토큰 유형 계약. "aud 가 agentnexus 가 아닌 교환 토큰은 거부" 는 L221 `verify_local` 기본 audience 로 구현된다.
- L16 `sync_client`: egress 가드 훅이 붙은 동기 httpx 팩토리(`app/core/http.py:178-180` — 링크 로컬·IMDS 차단, 사설망은 허용). 외부 IdP JWKS 조회에 쓰인다.
- L18: core 계층이 ORM 모델에 직접 의존 — 인증이 DB 상태(사용자 활성·PAT 폐기)를 매 요청 확인하기 때문(상태 기반 폐기의 대가).

```python path=app/core/auth.py lines=20-44
_USER = {"chat:use", "catalog:read", "agents:read", "models:read", "data:declare"}
_DEV = _USER | {"agents:write", "mcp:read", "mcp:write", "a2a:read", "a2a:write", "registry:publish",
                "observe:read", "gateway:read"}
ROLE_PERMS: dict[str, set[str]] = {
    "user": _USER,
    "developer": _DEV,
    "operator": _DEV | {"catalog:write", "catalog:approve", "gateway:write", "models:write", "admin:read",
                        "admin:write", "approvals:decide", "cost:read", "tokens:issue", "identity:read",
                        "limits:write"},
    "security": {"chat:use", "data:declare", "catalog:read", "catalog:approve", "gateway:read", "policy:write", "approvals:decide",
                 "identity:read", "identity:write", "audit:read", "observe:read", "tokens:issue", "mcp:read",
                 "a2a:read", "agents:read", "models:read"},
    "finops": {"chat:use", "data:declare", "catalog:read", "cost:read", "cost:write", "limits:write", "observe:read", "gateway:read",
               "models:read"},
    "admin": {"*"},
}
# 토큰 스코프 → 권한 (FR-L03·I04). 스코프가 권한 이름 그대로면 그 권한만, 묶음 스코프는 아래로 펼친다.
SCOPE_GRANTS: dict[str, set[str]] = {
    "gateway:invoke": {"chat:use", "models:read", "catalog:read", "data:declare"},  # Unified Model API·MCP·A2A 게이트웨이 호출
}
SCOPED_KINDS = ("pat", "workload", "access_token")
ROLE_LABELS = {"admin": "플랫폼 관리자", "operator": "플랫폼 운영자", "developer": "에이전트 개발자",
               "security": "보안·IAM 담당", "finops": "FinOps 담당", "user": "최종 사용자"}


```
**분석 / Analysis (Level 2)**
- L20 `data:declare` 를 `_USER` 에 넣어 모든 역할이 기본으로 가진다 — "현재 동작 유지 + 관리자가 RBAC 행렬에서 회수" 결정(`test/impl/40th/result.md:15`). 회수는 L110-115 `role_perms` 의 `remove` 재정의로 하고, 권한이 없으면 신고를 기본값으로 올리고 감사(`app/services/policy.py:43` 주석).
- L38 `gateway:invoke` 묶음에도 `data:declare` 추가 — Access Token·워크로드(스코프 제한 토큰)가 기존 묶음 스코프만으로 하향 신고를 계속할 수 있게. 단 `has()`(L75-80)는 스코프 ∩ 역할 권한이므로, 역할(user)에서 회수하면 스코프가 있어도 거부된다(일관됨).
- L40 `SCOPED_KINDS`: 스코프로 제한하는 토큰 종류. `client_key`·`delegation` 은 포함되지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L40 `client_key` 가 `SCOPED_KINDS` 에 없다(1차 지적 미해결). 클라이언트 키를 `scopes` 와 함께 만들어도(`app/api/identity.py:70`) `scope_perms()` 가 `None` 이라 스코프가 적용되지 않고 L209-210 에서 역할을 `user` 로 낮춘 권한 전체를 가진다. 사용자가 "chat 만" 으로 좁혔다고 믿는 키가 `agents:read`·`data:declare` 등도 가진다. 최소 수정: `SCOPED_KINDS` 에 `client_key` 를 추가하고 스코프가 비면 `gateway:invoke` 로 해석하거나, 의도라면 API 에서 클라이언트 키 `scopes` 입력을 거부.
- L29·L32 한 줄이 120자를 넘는다(스타일) — 권한 추가 diff 를 읽기 어렵게 한다.

```python path=app/core/auth.py lines=45-61
@dataclass
class Principal:
    sub: str
    kind: str  # user | workload | access_token | client_key | delegation
    name: str | None = None
    email: str | None = None
    roles: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    team: str | None = None
    scopes: list[str] = field(default_factory=list)
    token_id: str | None = None
    issuer: str | None = None
    actor: str | None = None  # 위임 체인(act.sub)
    user_id: str | None = None
    raw_token: str | None = field(default=None, repr=False)
    session_id: str | None = None  # 로그인 세션(refresh token id, 토큰 claim sid)

```
**분석 / Analysis (Level 2)**
- L45-60: 호출 주체 값 객체. `field(default_factory=list)` 로 가변 기본값 공유 버그를 피하고, `raw_token` 은 `repr=False` 로 로그·예외 출력에서 숨긴다. `raw_token` 이 필요한 이유는 게이트웨이가 원본 토큰을 교환 subject 로 쓰기 때문(`app/services/mcp_gateway.py:173` — 1차 핫스팟 #2).
- L48 주석의 종류 목록에 `pat` 이 빠져 있다(L207 은 `pat` 을 만든다) — 문서 불일치.
- L53 `team`: 팀 기반 KB 가시성(`app/services/knowledge.py:46-47`)·정책 주체 매칭(`app/services/policy.py:123-124`)·한도/비용 귀속(`app/services/ratelimit.py:193-196`)의 키다. 따라서 **이 값을 누가 정하느냐가 인가 경계**다(L209-211 참조).

```python path=app/core/auth.py lines=62-86
    def permissions(self) -> set[str]:
        perms: set[str] = set()
        for r in self.roles:
            perms |= role_perms(r)
        return perms

    def scope_perms(self) -> set[str] | None:
        """스코프로 제한되는 토큰이면 허용 권한 집합, 아니면 None. 워크로드·Access Token 은 스코프가 없으면 권한 없음
        (기본 거부), PAT 는 스코프를 비우면 사용자 권한 그대로."""
        if self.kind not in SCOPED_KINDS or (self.kind == "pat" and not self.scopes):
            return None
        return set().union(*(SCOPE_GRANTS.get(s, {s}) for s in self.scopes or []))

    def has(self, perm: str) -> bool:
        sp = self.scope_perms()
        if sp is not None and perm not in sp:  # 역할이 admin(*) 이어도 토큰 스코프 밖은 거부
            return False
        perms = self.permissions()
        return "*" in perms or perm in perms

    def snapshot(self) -> dict:
        return {"sub": self.sub, "kind": self.kind, "name": self.name, "email": self.email, "roles": self.roles,
                "groups": self.groups, "team": self.team, "user_id": self.user_id}


```
**분석 / Analysis (Level 2)**
- L62-66 `permissions()`: 역할별 권한 합집합(재정의 반영). 호출마다 `role_perms` → `rbac_overrides()` 캐시 조회(5초 TTL).
- L68-73 `scope_perms()`: 스코프 제한 토큰이면 허용 집합, 아니면 None. PAT 는 스코프가 비면 None = **사용자 역할 권한 전체**(편의 규칙). 워크로드·Access Token 은 스코프가 비면 빈 집합 = 기본 거부.
- L75-80 `has()`: (스코프 허용) ∩ (역할 권한). admin(`*`)이어도 스코프 밖은 거부 — `test/test_hardening.py:98-105` 가 보증.
- L82-84 `snapshot()`: 위임 토큰 발급용 신원 스냅샷(`app/services/orchestrator.py:63` → `app/services/identity.py:166-169`). `scopes`·`kind` 제한은 위임 토큰에 실리지 않는다(위임 토큰은 L225 에서 사용자 역할 전체로 인증 — services/identity 보고서 참조).
**짚어볼 점 / Watch-outs**
- (교차 · 높음은 `app/api/identity.py` 에서 집계) L71 "스코프 빈 PAT = 사용자 권한 전체" 규칙과 `POST /api/v1/me/tokens` 가 `require` 없이 `current_principal` 만 요구하는 점(`app/api/identity.py:55-75`)이 결합하면, **스코프가 좁은 PAT·클라이언트 키(역할 user 로 낮춤)·위임 토큰으로 스코프 없는 새 PAT 를 발급**해 소유자 역할 전체 권한을 얻는다. 이 파일의 규칙 자체는 의도대로지만 "토큰으로 토큰을 만드는" 경로에서 `has()` 의 상한이 지켜지지 않는다. 최소 수정은 API 쪽(호출자 `kind == "user"` 만 허용, 또는 새 토큰 권한 ⊆ 호출자 `scope_perms()`).

```python path=app/core/auth.py lines=87-117
# ── 역할 권한 재정의 (SCR-L03 권한 행렬) — Setting rbac.overrides {role: {add: [...], remove: [...]}} · 5초 캐시 ──
_RBAC: list = [0.0, {}]


def rbac_overrides() -> dict:
    if time.monotonic() - _RBAC[0] < 5:
        return _RBAC[1]
    try:
        from app.db import SessionLocal
        from app.models import Setting
        with SessionLocal() as db:
            row = db.get(Setting, "rbac.overrides")
            val = row.value if row and isinstance(row.value, dict) else {}
    except Exception:  # noqa: BLE001 — 읽지 못하면 기본 행렬
        val = _RBAC[1]
    _RBAC[0], _RBAC[1] = time.monotonic(), val
    return val


def reset_rbac_cache() -> None:
    _RBAC[0] = 0.0


def role_perms(role: str, overrides: dict | None = None) -> set[str]:
    base = set(ROLE_PERMS.get(role, set()))
    if "*" in base:  # 관리자는 재정의하지 않는다
        return base
    ov = (rbac_overrides() if overrides is None else overrides).get(role) or {}
    return (base | set(ov.get("add") or [])) - set(ov.get("remove") or [])


```
**분석 / Analysis (Level 2)**
- L88 `_RBAC = [시각, 값]`: `global` 없이 모듈 전역을 바꾸는 가변 리스트 관용구. L102 에서 두 칸을 한 문장으로 교체 — 다른 스레드가 중간 상태(새 시각 + 옛 값)를 볼 수는 있지만 값 자체는 항상 완전한 dict 라 치명적이지 않다.
- L91-103 `rbac_overrides()`: 5초 TTL, 별도 세션으로 `Setting("rbac.overrides")` 조회. 워커별 캐시라 변경 반영이 워커마다 최대 5초 늦다 — `test/impl/40th/result.md:24` 가 "6초 대기 후 두 워커 모두 반영" 으로 확인.
- L110-115 `role_perms()`: base ∪ add − remove, admin 은 재정의 대상 아님(자기 잠금 방지).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L100-101 DB 읽기 실패 시 이전 캐시를 유지 — 프로세스 기동 후 **첫** 조회가 실패하면 `{}`(재정의 없음)로 동작한다. Epic 40 이후 `data:declare` 회수가 이 재정의에 의존하므로(`test/impl/40th/scenario.md:9`), DB 순간 장애 + 워커 재시작 조합에서 회수된 하향 신고 권한이 되살아난다(fail-open). 1차 지적 미해결이며 의미가 조금 커졌다. 최소 수정: 첫 조회 실패 시 짧게 재시도하거나, 실패를 경고 로그로 남기고 "회수형 재정의가 있는 역할" 은 마지막 성공값을 디스크/Valkey 에 보존.

```python path=app/core/auth.py lines=118-139
# ── 외부 IdP JWKS 캐시 ──
_JWKS: dict[str, tuple[float, dict]] = {}


def _external_keys(idp: IdpConnection, refresh: bool = False) -> dict:
    cached = _JWKS.get(idp.issuer)
    if cached and not refresh and time.time() - cached[0] < 600:
        return cached[1]
    jwks_uri = idp.jwks_uri

    def srv(url: str) -> str:  # 분리 DNS — 서버 측 호출은 internal_url 로 (broker.srv 와 같은 규칙)
        iss = idp.issuer.rstrip("/")
        return idp.internal_url.rstrip("/") + url[len(iss):] if idp.internal_url and url.startswith(iss) else url
    with sync_client(timeout=10) as http:
        if not jwks_uri:
            disc = http.get(srv(idp.issuer.rstrip("/") + "/.well-known/openid-configuration")).json()
            jwks_uri = disc["jwks_uri"]
        keyset = http.get(srv(jwks_uri)).json()
    _JWKS[idp.issuer] = (time.time(), keyset)
    return keyset


```
**분석 / Analysis (Level 2)**
- L119-125: issuer 별 (조회 시각, 키셋) 캐시, TTL 600초. `refresh=True` 면 캐시 무시.
- L128-130 `srv()`: 분리 DNS — 브라우저용 issuer 주소를 컨테이너 내부 주소(`internal_url`)로 바꿔 서버 측 조회.
- L131-135: `jwks_uri` 미설정이면 OIDC discovery. `with` 로 클라이언트를 닫는다(자원 누수 없음). `sync_client` 의 요청 훅이 링크 로컬·IMDS 를 막는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L131-135 (1차 SEC-09 후속) discovery 응답의 `jwks_uri` 를 검증 없이 따라간다. egress 가드가 IMDS·링크 로컬은 막지만 사설망 주소는 허용(`app/core/http.py:35-38`, 제품 기능) — 등록된 IdP 가 악의적이거나 탈취되면 내부 주소로 GET 을 유도할 수 있다(응답은 JSON 파싱 실패로 버려져 블라인드). IdP 등록이 `identity:write` 권한이고 39th 판정(`test/impl/39th/scenario.md:55`)에서 Google 호환 때문에 같은 origin 제한을 기각했다. 수용된 위험으로 기록.
- [위험:낮음] L133-135 응답 상태 코드 확인(`raise_for_status`)이 없다 — 404 HTML 이면 `.json()` 이 `JSONDecodeError` → L257 에서 401 로 바뀌어 원인(IdP 장애)이 가려진다.
- L136 `_JWKS` 무효화는 `app/api/identity.py:235` 의 `pop` 뿐이고 그 워커에만 적용 — 다른 워커는 최대 600초 이전 키를 계속 신뢰한다(IdP 키 유출 대응 시 지연).

```python path=app/core/auth.py lines=140-157
# 외부 IdP 서명은 비대칭만 — 토큰 헤더의 alg 가 HS*·none 으로 검증 방식을 고르지 못하게 (알고리즘 혼동 방지)
EXTERNAL_ALGS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA")


def _external_verify(idp: IdpConnection, token: str) -> dict:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "RS256")
    if alg not in EXTERNAL_ALGS:
        raise jwt.InvalidAlgorithmError(f"허용하지 않는 서명 알고리즘: {alg}")
    for attempt in (False, True):
        for k in _external_keys(idp, refresh=attempt).get("keys", []):
            if header.get("kid") in (None, k.get("kid")) and k.get("alg") in (None, alg):
                key = jwt.PyJWK(k).key
                return jwt.decode(token, key, algorithms=[alg],
                                  audience=idp.audience or idp.client_id, issuer=idp.issuer, leeway=30)
    raise jwt.InvalidTokenError("signing key not found")


```
**분석 / Analysis (Level 2)**
- L141 `EXTERNAL_ALGS`: 비대칭 알고리즘만 허용 — 헤더 `alg=HS256` 으로 공개키를 HMAC 비밀로 쓰게 하는 알고리즘 혼동, `alg=none` 을 차단(`test/test_review_fixes.py:37-53`).
- L149-154: 캐시 키로 먼저, 못 찾으면 강제 재조회(키 회전 대응). `kid` 일치 + JWK 의 `alg`(있으면)가 헤더와 같을 때만 사용. `jwt.decode(..., algorithms=[alg])` 는 허용 목록에서 이미 거른 단일 alg. audience 는 연동 설정(`audience` 또는 `client_id`), issuer 일치, 시계 오차 30초.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L149-150 존재하지 않는 `kid` 의 토큰마다 `refresh=True` 로 외부 JWKS 를 다시 가져온다(음성 캐시·최소 간격 없음, 1차 미해결). 인증 없는 공격자가 등록된 issuer 를 `iss` 로 넣은 쓰레기 토큰을 보내면 요청 1건당 외부 HTTP 1~2건(discovery 포함) + 최대 10초×2 스레드 점유. 스레드풀 40개를 이 경로로 채우면 모든 동기 엔드포인트가 지연된다. 최소 수정: 강제 재조회에 issuer 별 최소 간격(예: 60초)과 실패 결과 단기 캐시.
- [위험:낮음] L153-154 `options={"require": [...]}` 가 없어 외부 토큰에 `exp` 가 **없으면** 만료 검증 없이 통과한다(PyJWT 는 있는 클레임만 검증). `verify_local`(`app/core/security.py:31`)과 비대칭. 표준 IdP 는 항상 `exp` 를 넣지만, 잘못 설정된 IdP·장기 토큰 유형이면 영구 토큰이 된다. 최소 수정: `options={"require": ["exp", "iat", "iss", "sub"]}`.
- L151-154 `kid` 없는 토큰은 alg 가 맞는 **첫** 키로만 검증하고 서명 오류는 그대로 전파 — 다중 키 IdP 에서 다음 키를 시도하지 않는다(1차 미해결, 가용성 문제).

```python path=app/core/auth.py lines=158-188
def _map_roles(idp: IdpConnection, claims: dict) -> list[str]:
    values = claims.get(idp.groups_claim, []) or []
    values = values if isinstance(values, list) else [values]
    roles_v = claims.get(idp.roles_claim, []) or []
    values += roles_v if isinstance(roles_v, list) else [roles_v]
    roles = sorted({idp.role_mapping[v] for v in values if v in (idp.role_mapping or {})})
    return roles or ["user"]


def _upsert_external_user(db: Session, idp: IdpConnection, claims: dict) -> User:
    from app.core.ids import new_id
    sub = f"ext:{idp.id}:{claims['sub']}"
    user = db.scalars(select(User).where(User.sub == sub)).first()
    groups = claims.get(idp.groups_claim, []) or []
    if user is None:
        user = User(id=new_id("usr"), sub=sub, issuer=idp.issuer, roles=_map_roles(idp, claims))
        db.add(user)
    else:
        user.roles = _map_roles(idp, claims)
    user.email = claims.get("email")
    user.name = claims.get("name") or claims.get("preferred_username")
    user.groups = groups if isinstance(groups, list) else [groups]
    user.last_login_at = utcnow()
    roles_v = claims.get(idp.roles_claim, []) or []
    user.claims = {"idp": idp.id, "issuer": idp.issuer, idp.groups_claim: user.groups,  # 재동기화·클레임 보기 (SCR-J03)
                   idp.roles_claim: roles_v if isinstance(roles_v, list) else [roles_v], "keys": sorted(claims),
                   "at": utcnow().isoformat()}
    db.commit()
    return user


```
**분석 / Analysis (Level 2)**
- L158-164 `_map_roles`: 그룹·역할 클레임 값을 연동의 `role_mapping` 으로 플랫폼 역할로 변환, 매핑 없으면 최소 권한 `user`. 문자열 단일 값도 리스트로 정규화.
- L167-186 `_upsert_external_user`: JIT 프로비저닝. `sub` 를 `ext:{idp_id}:{원래 sub}` 로 네임스페이스화(IdP 간 sub 충돌·내장 사용자 사칭 방지). 매 인증마다 역할·이메일·그룹을 IdP 값으로 덮어쓴다 → IdP 에서 그룹을 빼면 다음 요청부터 권한이 줄어든다(좋음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L169 `claims['sub']` 가 없으면 `KeyError` — L255-258 의 `try` 밖이라 401 이 아니라 500. 최소 수정: `try` 안으로 옮기거나 `require` 에 `sub` 포함(위 L153 수정과 함께 해결).
- [위험:낮음] L185 인증 경로에서 매 요청 `commit`(1차 미해결) — 외부 IdP 토큰을 직접 Bearer 로 쓰는 클라이언트는 요청마다 `users` 행 UPDATE. SQLite 단일 작성자 잠금·PostgreSQL 행 잠금 경합, `last_login_at` 이 "마지막 API 호출 시각" 으로 변질. 동시 첫 로그인 두 요청은 `sub` UNIQUE(`app/models.py:25`) 위반으로 한쪽 500(재시도 없음).
- L163 `idp.role_mapping` 이 dict 가 아니면(`PATCH /api/v1/identity/idps/{id}` 가 `body: dict` 원본을 그대로 쓰므로 리스트도 저장 가능 — `app/api/identity.py:225-230`) `v in list` 후 `list[str]` 인덱싱으로 `TypeError` → 그 IdP 의 모든 토큰 인증 500. 저장 시 검증 문제(교차).

```python path=app/core/auth.py lines=189-213
def _user_principal(user: User, token: str, kind: str = "user", actor: str | None = None,
                    token_id: str | None = None, session_id: str | None = None) -> Principal:
    return Principal(sub=user.sub, kind=kind, name=user.name or user.username, email=user.email,
                     roles=list(user.roles or ["user"]), groups=list(user.groups or []), team=user.team,
                     issuer=user.issuer, user_id=user.id, raw_token=token, actor=actor, token_id=token_id,
                     session_id=session_id)


def authenticate_token(db: Session, token: str) -> Principal:
    if token.startswith(("anx_pat_", "anx_key_")):
        row = db.scalars(select(PersonalToken).where(PersonalToken.token_hash == security.token_hash(token))).first()
        if row is None or row.revoked_at or (row.expires_at and row.expires_at < utcnow()):
            raise unauthorized("토큰이 유효하지 않거나 폐기되었습니다.")
        user = db.get(User, row.user_id)
        if user is None or user.status != "active":
            raise unauthorized("사용자가 비활성 상태입니다.")
        row.last_used_at = utcnow()
        db.commit()
        p = _user_principal(user, token, kind="pat" if row.kind == "pat" else "client_key", token_id=row.id)
        p.scopes = list(row.scopes or [])
        if row.kind == "client_key":
            p.roles = ["user"]
            p.team = row.team or user.team
        return p

```
**분석 / Analysis (Level 2)**
- L189-194 `_user_principal`: User 행 → Principal. 역할은 **매 요청 DB 현재값**(토큰 클레임의 roles 는 정보용) — 역할 변경·비활성화가 즉시 반영된다.
- L198-212 PAT·클라이언트 키: 원문이 아니라 SHA-256 해시로 조회(DB 유출 시 원문 보호, 타이밍 문제 없음). 폐기·만료(`expires_at` 이 None 이면 무기한)·사용자 상태 확인. 클라이언트 키는 역할을 `user` 로 낮추고 팀을 키 설정값으로.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 교차] L211 `p.team = row.team or user.team` — 클라이언트 키의 팀을 **키 행에 저장된 값**으로 신뢰한다. 그런데 그 값은 발급 요청자가 자유롭게 정한다(`app/api/identity.py:36`·`:70-71` `body.team or user.team`, 소속 확인 없음). 시나리오: developer(`gateway:read` 보유, 팀 `team-agents`)가 `{"kind":"client_key","team":"team-biz"}` 로 키를 만든다 → 그 키의 `p.team="team-biz"` → 팀 전용 KB 가시성 검사 `kb.team == p.team`(`app/services/knowledge.py:46-47`)을 통과해 `POST /api/v1/knowledge/{kid}/search`(`chat:use` — 키 역할 user 가 보유, `app/api/knowledge.py:127-128`)로 남의 팀 문서를 검색한다. 팀 주체 정책(`app/services/policy.py:123-124`)·팀 한도/비용 귀속(`app/services/ratelimit.py:193-196`)도 남의 팀으로 우회·전가된다. 최소 수정: 발급 API 에서 `body.team` 이 `user.team`(또는 관리자 권한) 일 때만 허용 — 이 줄은 그대로 두되 저장값이 검증된 것이어야 한다. 시험 공백: 클라이언트 키 팀 지정으로 팀 KB 접근 시험 없음(`test/test_idor_matrix.py` 는 같은 사용자 토큰만 비교).
- [위험:낮음] L205-206 매 요청 `commit`(1차 미해결) — PAT 사용 빈도만큼 쓰기. 1분 단위 쓰로틀이 일반적.

```python path=app/core/auth.py lines=214-249
    try:
        claims = security.unverified_claims(token)
    except jwt.PyJWTError as exc:
        raise unauthorized("토큰 형식이 올바르지 않습니다.") from exc

    if claims.get("iss") == security.issuer():
        try:
            claims = security.verify_local(token)
        except jwt.PyJWTError as exc:
            raise unauthorized(f"토큰 검증 실패: {exc}") from exc
        kind = claims.get("anx_kind", "user")
        if kind in ("user", "delegation"):
            user = db.scalars(select(User).where(User.sub == claims["sub"])).first()
            if user is None or user.status != "active":
                raise unauthorized("사용자가 비활성 상태입니다.")
            nbf = user.tokens_not_before
            if nbf is not None and int(claims.get("iat") or 0) < int(nbf.timestamp()):  # 세션 강제 종료 (SCR-J03·B06) — iat 는 초 단위
                raise unauthorized("세션이 종료되었습니다 — 다시 로그인하세요.")
            act = (claims.get("act") or {}).get("sub")
            return _user_principal(user, token, kind=kind, actor=act, token_id=claims.get("jti"), session_id=claims.get("sid"))
        if kind == "workload":
            wl = db.scalars(select(Workload).where(Workload.client_id == claims.get("client_id"))).first()
            if wl is None or not wl.enabled:
                raise unauthorized("워크로드가 비활성 상태입니다.")
            return Principal(sub=claims["sub"], kind="workload", name=wl.name, roles=["user"],
                             scopes=claims.get("scope", "").split(), raw_token=token, token_id=claims.get("jti"))
        if kind == "access_token":
            row = db.get(AccessToken, claims.get("jti"))
            if row is None or row.revoked_at or row.expires_at < utcnow():
                raise unauthorized("Access Token 이 폐기되었거나 만료되었습니다.")
            row.last_used_at = utcnow()
            db.commit()
            return Principal(sub=claims["sub"], kind="access_token", name=row.name, roles=["user"],
                             scopes=list(row.scopes or []), raw_token=token, token_id=row.id,
                             actor=row.issued_by)
        raise unauthorized("지원하지 않는 토큰 유형입니다.")
```
**분석 / Analysis (Level 2)**
- L214-219: 서명 검증 **전** 클레임으로 issuer 분기 — 이후 반드시 검증 경로를 거치므로 안전한 패턴(서명 전 값으로 권한을 판단하지 않음).
- L221: `verify_local` — aud=`agentnexus` 필수. 교환 토큰(`anx_kind=exchanged`, aud=`mcp:*` 등)은 여기서 거부된다.
- L225-233 user/delegation: 사용자 활성, `tokens_not_before` 이전 발급 거부(세션 강제 종료·IdP 연동 중지), `act.sub` 보존, `sid` → `session_id`.
- L234-239 workload: 워크로드 활성 확인(비활성화하면 즉시 차단), 토큰 `scope` 로 제한.
- L240-248 access_token: DB 행(id=jti)으로 폐기·만료 확인(즉시 폐기 가능한 JWT).
- L249: 그 밖의 `anx_kind`(예: `exchanged`·`setup`)는 거부.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L224 `anx_kind` 기본값 `"user"` — 클레임이 없는 내장 서명 토큰을 사용자 토큰으로 간주(fail-open 기본값). 실제 예: id_token(`app/services/identity.py:61-65` — `anx_kind` 없음)은 `aud=client_id` 인데 `client_id` 사전 등록이 없어 `agentnexus` 로 만들 수 있다(`app/api/idp.py:245`·`:301`) → id_token 이 액세스 토큰처럼 통과. 같은 사용자라 권한 상승은 아니나 경계가 흐려진다. 최소 수정: `kind = claims.get("anx_kind")` 로 두고 없으면 401(또는 `typ=at+jwt` 확인 — security 보고서).
- [위험:낮음] L223·L258 PyJWT·내부 예외 메시지를 응답에 그대로 싣는다(`f"토큰 검증 실패: {exc}"`, `f"외부 IdP 토큰 검증 실패: {exc}"` — 예: 내부 호스트명이 든 연결 오류). 1차 미해결(CWE-209, 영향 낮음).
- L230 `iat`·`tokens_not_before` 초 단위 비교 — 강제 종료와 재로그인이 같은 초에 일어나면 새 토큰도 거부(1차 경계 조건, 미해결·영향 낮음).
- L234-239 워크로드 토큰은 jti 폐기 목록이 없다 — 시크릿 회전(`app/services/identity.py:113-118`)은 이미 발급된 토큰을 무효화하지 않는다(만료까지 `settings.access_token_ttl` 기본 3600초 — `app/core/config.py:38`). 비활성화만 즉시 차단.
- L225-233 이 경로는 `sid`(세션 = refresh token id)가 폐기됐는지 확인하지 않는다 — refresh 계열 폐기(재사용 탐지, `app/services/identity.py:82-90`)나 `/idp/revoke` 뒤에도 그 세션의 액세스 토큰은 만료까지 유효(services/identity 보고서 [위험:낮음]).

```python path=app/core/auth.py lines=250-264

    idp = db.scalars(select(IdpConnection).where(IdpConnection.issuer == claims.get("iss"),
                                                 IdpConnection.enabled.is_(True))).first()
    if idp is None:
        raise unauthorized("신뢰하지 않는 발급자(issuer)입니다.")
    try:
        verified = _external_verify(idp, token)
    except Exception as exc:  # noqa: BLE001 — 네트워크·서명 오류 모두 401
        raise unauthorized(f"외부 IdP 토큰 검증 실패: {exc}") from exc
    user = _upsert_external_user(db, idp, verified)
    if user.status != "active":
        raise unauthorized("사용자가 비활성 상태입니다.")
    return _user_principal(user, token)


```
**분석 / Analysis (Level 2)**
- L251-254: 외부 issuer 는 **활성 연동으로 등록된 경우만** 신뢰 — 임의 issuer 거부.
- L255-258: 네트워크·서명·만료·허용 외 alg 모두 401(조용한 삼킴 아님 — 변환).
- L259-262: JIT upsert 후 상태 확인 — 관리자가 `status=disabled` 로 바꾸면 IdP 가 계속 유효 토큰을 줘도 차단(`_upsert` 가 status 는 덮어쓰지 않음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L259-262 외부 IdP 토큰에는 `tokens_not_before`(세션 강제 종료) 검사가 없다(1차 미해결). `revoke_user_sessions`(`app/api/ops.py:85-102`)·IdP 연동 중지(`app/api/identity.py:242-250`)는 내장 토큰만 끊고, 외부 IdP 토큰을 직접 Bearer 로 쓰는 클라이언트는 그 토큰 만료까지 계속 통과한다(연동 중지는 L252 `enabled` 조건으로 막힘 — 강제 종료 경로만 해당). 최소 수정: L230 과 같은 `iat < tokens_not_before` 검사를 L260 뒤에 추가.
- 동기 HTTP(L256 → L131-135)가 인증 경로에 있다 — `app/api/idp.py:322`(async 핸들러)에서 호출되면 이벤트 루프에서 최대 10초×2 블로킹(교차, idp 보고서).

```python path=app/core/auth.py lines=265-295
def bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.headers.get("x-api-key") or None


def optional_principal(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    if getattr(request.state, "principal", None) is not None:
        return request.state.principal
    token = bearer(request)
    if not token:
        return None
    p = authenticate_token(db, token)
    request.state.principal = p
    return p


def current_principal(p: Principal | None = Depends(optional_principal)) -> Principal:
    if p is None:
        raise unauthorized()
    return p


def require(*perms: str):
    def _dep(p: Principal = Depends(current_principal)) -> Principal:
        missing = [x for x in perms if not p.has(x)]
        if missing:
            raise forbidden(f"권한 부족: {', '.join(missing)}", code="forbidden", required=missing)
        return p
    return _dep
```
**분석 / Analysis (Level 2)**
- L265-269 `bearer`: `Authorization: Bearer` 우선, 없으면 `X-API-Key`(OpenAI 호환 클라이언트). 쿠키를 쓰지 않으므로 CSRF 대상이 아니다.
- L272-280 `optional_principal`: 요청당 1회 인증 — `request.state.principal` 캐시(같은 요청의 여러 의존성이 DB 를 반복 조회하지 않음). FastAPI 는 같은 요청 안에서 같은 의존성 함수를 기본 캐시하므로 이중 안전장치.
- L289-295 `require(*perms)`: 의존성 팩토리 — 권한 목록을 클로저로 캡처. 부족 권한을 `required` 로 응답.
**짚어볼 점 / Watch-outs**
- `require` 는 RBAC 만 본다 — 객체 수준 인가는 엔드포인트 책임. Epic 48 이 **일반 사용자가 닿는** 경로 66개를 OWNED/SHARED 로 분류·시험했고(`test/impl/48th/scenario.md:7`, `test/test_idor_matrix.py`), 운영자·개발자 전용(priv) 97개 경로는 범위 밖이다 — 1차 [중간](developer 가 남의 MCP 서버·에이전트 설정을 소유 확인 없이 변경)은 시크릿 참조·전송처 쪽만 `app/services/secrets.py:24-38` 로 막혔다.
- `current_principal` 만 쓰는 엔드포인트(예: `POST /api/v1/me/tokens`)는 토큰 종류·스코프를 전혀 확인하지 않는다 — 위 L62-86 교차 항목 참조.

## 파일 평가 / File Assessment
- 강점: (1) 모든 JWT 가 서명 검증을 거치고 외부 IdP 는 비대칭 alg 허용 목록 + 키 alg 일치 + 등록 issuer 만 신뢰. (2) 스코프 ∩ 역할의 교집합, 워크로드·Access Token 기본 거부, 역할은 매 요청 DB 현재값. (3) PAT 해시 조회·Access Token 상태 기반 폐기·세션 강제 종료·`data:declare` 의 RBAC 회수 가능 설계.
- 위험: (1) [위험:중간 · 교차] 클라이언트 키 팀을 발급자가 정한 값으로 신뢰 → 팀 KB·팀 정책·팀 한도 우회(`app/core/auth.py:211` ↔ `app/api/identity.py:70-71`). (2) [위험:낮음] JWKS 강제 재조회 무제한·외부 토큰 `exp` 미강제·`sub` 누락 500(`app/core/auth.py:149-154`, `:169`). (3) [위험:낮음] fail-open 기본값들 — `anx_kind` 기본 user(`:224`), RBAC 재정의 첫 조회 실패 시 `{}`(`:100-101`), `client_key` 스코프 미적용(`:40`), 외부 토큰 강제 종료 미적용(`:259-262`).
- 개선 권고(영향순): (1) 클라이언트 키 팀을 발급자 소속(또는 관리자 승인)으로 제한 — API 수정 + 이 줄의 신뢰 근거 주석. (2) 외부 IdP 검증 `require` 클레임·강제 재조회 쓰로틀·`tokens_not_before` 적용. (3) `anx_kind` 필수화, `client_key` 를 `SCOPED_KINDS` 에 포함, 인증 경로 커밋 쓰로틀, 오류 메시지 일반화.
- 파일 위험 등급 / Risk grade: 중간 — 보안 경계 핵심 모듈에 [위험:중간] 1건(교차, 팀 신뢰) + 1차에서 이월된 [위험:낮음] 다수. 이번 차수 코드 변경은 권한 표 4줄뿐이라 1차 지적 대부분이 그대로다.
- 재읽기 권고 레벨: 2 — 인증 경계의 중심. 특히 `Principal.team`·`scope_perms` 를 소비하는 쪽(knowledge·policy·ratelimit·me/tokens)과 함께 읽을 것.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 1차 L75 [위험:중간] developer 의 `mcp:write`·`a2a:write` + 객체 수준 인가 부재로 `secret_ref`·`endpoint` 변경 → 저장 시크릿 외부 전송 | 부분 해결 — 시크릿 참조·전송처 변경은 `gateway:write` 필요(차단), 서버·에이전트 객체 소유권 검사는 여전히 없음(priv 경로는 Epic 48 범위 밖) | `app/services/secrets.py:24-38`, `app/api/mcp.py:403-405`, `app/api/a2a.py:535-537`, `test/impl/48th/scenario.md:7` |
| 1차 L128 `client_key` 가 `SCOPED_KINDS` 에 없어 스코프 미적용 | 미해결 | `app/core/auth.py:40`, `:71` |
| 1차 L166 [위험:낮음] RBAC 재정의 읽기 실패 시 이전/빈 캐시(fail-open) | 미해결(`data:declare` 회수 의존으로 의미 증가) | `app/core/auth.py:100-101` |
| 1차 L216 [위험:낮음] 없는 `kid` 마다 JWKS 강제 재조회(음성 캐시 없음) | 미해결 | `app/core/auth.py:149-150` |
| 1차 L217 `kid` 없는 토큰은 첫 키만 시도 | 미해결 | `app/core/auth.py:151-154` |
| 1차 L219 동기 HTTP 가 인증 경로 — 스레드풀 고갈 | 미해결(+ `async def token` 에서 직접 호출되는 경로 추가) | `app/core/auth.py:131-135`, `app/api/idp.py:322` |
| 1차 L266 인증 경로 매 요청 commit(외부 사용자 upsert) · 동시 첫 로그인 IntegrityError | 미해결 | `app/core/auth.py:185`, `app/models.py:25` |
| 1차 L290 PAT `last_used_at` 매 요청 commit | 미해결 | `app/core/auth.py:205-206` |
| 1차 L339·L362 [위험:낮음] 검증 예외 메시지 응답 노출 | 미해결 | `app/core/auth.py:223`, `:258` |
| 1차 L340 `iat`·`tokens_not_before` 초 단위 경계 | 미해결(영향 낮음) | `app/core/auth.py:230` |
| 1차 L341 워크로드 토큰 jti 폐기 없음 | 미해결 | `app/core/auth.py:234-239` |
| 1차 L363 외부 IdP 토큰에 `tokens_not_before` 미적용 | 미해결 | `app/core/auth.py:259-262` |
| 1차 L405 `require` 는 RBAC 만 — MCP·A2A 관리 API 객체 인가 없음 | 부분 해결 — 일반 사용자 경로 66개는 Epic 48 행렬 시험, priv 경로는 미적용 | `test/test_idor_matrix.py:28-79`, `test/impl/48th/scenario.md:7` |
| 1차 위임 보안 리뷰 SEC-01 [HIGH] 외부 IdP alg 혼동 | 해결(1차 스냅샷 시점 반영, 유지) | `app/core/auth.py:140-148`, `test/test_review_fixes.py:37-53` |
| 1차 위임 보안 리뷰 SEC-09 [MEDIUM] 외부 IdP JWKS/discovery SSRF | 부분 해결 — egress 가드가 IMDS·링크 로컬 차단, 사설망·discovery `jwks_uri` 검증은 의도적 미적용(39th 판정) | `app/core/http.py:35-38`, `app/core/auth.py:131-135`, `test/impl/39th/scenario.md:55` |
