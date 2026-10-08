> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md)

### 11. app/core/auth.py
**파일 요약 / File Summary**
- 역할 / Role: 인증·인가 핵심. `Principal`(호출 주체), 역할→권한 RBAC 표·재정의 캐시, 토큰 유형별 해석(PAT·클라이언트 키·내장 JWT(user/delegation/workload/access_token)·외부 IdP JWT + JIT 프로비저닝), FastAPI 의존성(`optional_principal`·`current_principal`·`require`).
- 진입점 거리 / Distance: 2 (`app/main.py` → `app/api/*` → `require(...)`)
- LOC: 295
- 주요 export: `Principal`, `ROLE_PERMS`, `SCOPE_GRANTS`, `ROLE_LABELS`, `role_perms`, `rbac_overrides`, `reset_rbac_cache`, `authenticate_token`, `bearer`, `optional_principal`, `current_principal`, `require`, `_user_principal`(외부에서 import — `app/api/setup.py:16`)
- 의존(내부): `app.core.{security,errors,http,ids}`, `app.db`, `app.models.{AccessToken,IdpConnection,PersonalToken,User,Workload,Setting}`
- 외부 패키지: pyjwt, fastapi, sqlalchemy
- 부수효과 / Side effects: DB 쓰기(PAT·Access Token `last_used_at`, 외부 사용자 upsert), 외부 IdP JWKS HTTP 조회, 프로세스 전역 캐시 `_RBAC`·`_JWKS`
- 동시성 / Concurrency: 동기 함수 — FastAPI 동기 의존성은 스레드풀에서 실행. 전역 dict 캐시는 락 없이 갱신(값 교체라 치명적이지 않음)
- 테스트 커버리지 흔적: `test/test_identity.py`, `test/test_sso.py`(외부 IdP), `test/test_hardening.py`, `test/test_ops.py`(RBAC 재정의·세션 종료), `test/test_review_fixes.py`(`test_external_idp_alg_allowlist`)
- 참고: L140-155 는 리뷰 도중(13:57) 동시 작업으로 바뀐 부분 — 본 리뷰는 14:03 스냅샷 기준(INDEX §1.6).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/auth.py lines=1-18
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
- L1-5: 허용 토큰 유형을 docstring 으로 계약화 — "aud 가 agentnexus 가 아닌 교환 토큰은 거부" 가 핵심 규칙(교환 토큰을 게이트웨이 API 에 재사용하지 못하게).
- L16 `sync_client`: 외부 IdP JWKS 조회용 동기 httpx(시험에서는 in-process 마운트).
- L18: 모델 import — core 계층이 ORM 모델에 의존한다(계층상 core → models 는 허용되는 방향이지만 core 가 DB 를 직접 만진다는 뜻).

```python path=app/core/auth.py lines=20-43
_USER = {"chat:use", "catalog:read", "agents:read", "models:read"}
_DEV = _USER | {"agents:write", "mcp:read", "mcp:write", "a2a:read", "a2a:write", "registry:publish",
                "observe:read", "gateway:read"}
ROLE_PERMS: dict[str, set[str]] = {
    "user": _USER,
    "developer": _DEV,
    "operator": _DEV | {"catalog:write", "catalog:approve", "gateway:write", "models:write", "admin:read",
                        "admin:write", "approvals:decide", "cost:read", "tokens:issue", "identity:read",
                        "limits:write"},
    "security": {"chat:use", "catalog:read", "catalog:approve", "gateway:read", "policy:write", "approvals:decide",
                 "identity:read", "identity:write", "audit:read", "observe:read", "tokens:issue", "mcp:read",
                 "a2a:read", "agents:read", "models:read"},
    "finops": {"chat:use", "catalog:read", "cost:read", "cost:write", "limits:write", "observe:read", "gateway:read",
               "models:read"},
    "admin": {"*"},
}
# 토큰 스코프 → 권한 (FR-L03·I04). 스코프가 권한 이름 그대로면 그 권한만, 묶음 스코프는 아래로 펼친다.
SCOPE_GRANTS: dict[str, set[str]] = {
    "gateway:invoke": {"chat:use", "models:read", "catalog:read"},  # Unified Model API·MCP·A2A 게이트웨이 호출
}
SCOPED_KINDS = ("pat", "workload", "access_token")
ROLE_LABELS = {"admin": "플랫폼 관리자", "operator": "플랫폼 운영자", "developer": "에이전트 개발자",
               "security": "보안·IAM 담당", "finops": "FinOps 담당", "user": "최종 사용자"}

```
**분석 / Analysis (Level 2)**
- L20-35 `ROLE_PERMS`: 6개 역할의 권한 집합. 집합 합집합(`|`)으로 상속 관계를 표현(`developer ⊃ user`, `operator ⊃ developer`). `admin` 은 와일드카드 `*`.
- L37-39 `SCOPE_GRANTS`: 묶음 스코프 `gateway:invoke` → 개별 권한 3개로 펼침.
- L40: 스코프로 제한되는 토큰 종류.
- L41-42: 화면 표시용 한글 역할명.
**짚어볼 점 / Watch-outs**
- [위험:중간] `developer` 가 `mcp:write`·`a2a:write`·`gateway:read` 를 가진다(L21-22). 이 조합으로 MCP 서버·원격 에이전트의 `auth`(`secret_ref`·`token_url`)와 `endpoint` 를 소유권 확인 없이 바꿀 수 있어 저장된 임의 시크릿을 외부로 보낼 수 있다 — 상세는 `app/api/mcp.py:389-413`, `app/api/a2a.py:518-546` 리뷰 참조. 권한 표 자체의 문제라기보다 객체 수준 인가 부재와 결합된 위험이다.
- `operator` 에 `policy:write` 가 없고 `security` 에 있다 — 정책 2인 검토(`app/api/gateway_admin.py:308-341`)는 `policy:write` 보유자끼리 이뤄진다.

```python path=app/core/auth.py lines=45-84
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
- L45-60 `Principal` dataclass: 주체 식별(sub)·종류·표시 정보·역할·그룹·팀·스코프·토큰 id·발급자·위임 행위자(act)·원본 토큰·세션 id. `field(default_factory=list)` — 가변 기본값 공유 버그를 피하는 정석. `raw_token` 은 `repr=False` 로 로그·디버그 출력에서 숨긴다(좋음).
- L62-66 `permissions()`: 역할별 권한 합집합(재정의 반영, L110-115).
- L68-73 `scope_perms()`: 스코프 제한 토큰이면 허용 권한 집합, 아니면 `None`. PAT 는 스코프가 비면 사용자 권한 그대로(편의), 워크로드·Access Token 은 스코프가 비면 **빈 집합 = 기본 거부**(안전).
- L75-80 `has()`: 스코프 밖이면 admin 이어도 거부 → 역할 권한 확인. 스코프와 역할의 **교집합** 의미.
- L82-84 `snapshot()`: 실행 재개 시 위임 토큰 발급용 신원 스냅샷(`app/services/identity.py:145-148`).
**짚어볼 점 / Watch-outs**
- L73 `set().union(*(...))` — 스코프가 하나도 없으면 `set().union()` = 빈 집합(정상).
- `client_key` 종류는 `SCOPED_KINDS` 에 없다(L40) → 클라이언트 키는 스코프를 설정해도 `scope_perms()` 가 `None` 이라 **스코프 제한이 적용되지 않는다**. 대신 L202-204 에서 역할을 `user` 로 낮춘다. 의도라면 주석 필요, 아니라면 `SCOPED_KINDS` 에 추가해야 한다.

```python path=app/core/auth.py lines=87-115
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
- L88 `_RBAC = [timestamp, value]`: 가변 리스트로 모듈 전역 캐시(`global` 선언 없이 변경하려는 관용구).
- L91-103 `rbac_overrides()`: 5초 TTL — `Setting("rbac.overrides")` 를 읽어 캐시. 읽기 실패 시 이전 값 유지(가용성 우선).
- L110-115 `role_perms()`: 기본 권한 ∪ add − remove. admin 은 재정의 불가(자기 잠금 방지).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L94-101 `except Exception` 은 DB 오류를 조용히 삼키고 **이전 캐시**(빈 dict 일 수도)로 동작한다 — 권한 **축소** 재정의(remove)가 DB 장애 동안 무시되어 권한이 넓어질 수 있다(fail-open). 보안상으로는 장애 시 마지막 성공값을 유지하는 지금 방식이 차선책이지만, 최초 조회 실패면 기본 행렬로 돌아간다는 점을 인지해야 한다.
- 캐시는 워커별 — 재정의 변경이 다른 워커에 최대 5초 늦게 반영(설계상 허용).

```python path=app/core/auth.py lines=118-155
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
- L119 `_JWKS`: issuer 별 (조회 시각, 키셋) 캐시, TTL 600초.
- L128-130 `srv()`: 분리 DNS — 서버 측 호출은 issuer 대신 `internal_url` 로 바꾼다(브라우저용 외부 주소와 컨테이너 내부 주소가 다를 때).
- L131-135: `jwks_uri` 가 없으면 OIDC discovery 로 찾는다. `with sync_client(...)` 로 연결을 닫는다.
- L140-141 `EXTERNAL_ALGS`: 외부 IdP 서명 허용 알고리즘을 비대칭(RS·PS·ES·EdDSA)으로 고정 — 토큰 헤더의 `alg` 가 `HS256`·`none` 을 골라 검증 방식을 바꾸는 알고리즘 혼동 공격을 차단한다. (스냅샷 시점에 동시 작업으로 추가된 코드 — `test/test_review_fixes.py` 의 `test_external_idp_alg_allowlist` 가 검증)
- L144-155 `_external_verify`: 헤더 `alg` 를 허용 목록으로 먼저 거르고(L146-148), `kid` 가 같고 키의 `alg`(있으면)가 헤더와 같은 키만 사용(L151). 못 찾으면 캐시를 무시하고 한 번 더 조회(키 회전 대응) 후 검증. audience 는 연동 설정(`audience` 또는 `client_id`), issuer 일치, 시계 오차 30초.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L149-150 존재하지 않는 `kid` 를 가진 토큰을 보낼 때마다 `refresh=True` 로 외부 JWKS 를 다시 조회한다(음성 캐시 없음) — 인증 없는 공격자가 요청당 1회 외부 HTTP 를 유발할 수 있다(증폭). 강제 재조회에 최소 간격(예: 60초)을 두어야 한다.
- L151 `kid` 가 없는 토큰은 alg 가 맞는 첫 번째 키로 검증 — 다중 키 IdP 에서 그 키가 아니면 서명 오류로 실패(다음 키 재시도 없음, `jwt.decode` 예외가 바로 전파).
- L146 헤더에 `alg` 가 없으면 `RS256` 으로 간주 — PyJWT 는 헤더 `alg` 를 필수로 보므로 실제로는 디코드 단계에서 거부된다(무해).
- 동기 HTTP 가 인증 경로에 있다 — 스레드풀에서 돌므로 이벤트 루프는 막지 않지만, IdP 지연 시 스레드풀(anyio 기본 40)이 고갈될 수 있다.

```python path=app/core/auth.py lines=158-194
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


def _user_principal(user: User, token: str, kind: str = "user", actor: str | None = None,
                    token_id: str | None = None, session_id: str | None = None) -> Principal:
    return Principal(sub=user.sub, kind=kind, name=user.name or user.username, email=user.email,
                     roles=list(user.roles or ["user"]), groups=list(user.groups or []), team=user.team,
                     issuer=user.issuer, user_id=user.id, raw_token=token, actor=actor, token_id=token_id,
                     session_id=session_id)
```
**분석 / Analysis (Level 2)**
- L158-164 `_map_roles`: 그룹 클레임 + 역할 클레임 값을 연동의 `role_mapping` 으로 AgentNexus 역할로 변환. 매핑이 없으면 최소 권한 `user`.
- L167-186 `_upsert_external_user`: JIT 프로비저닝 — `sub` 를 `ext:{idp_id}:{원래 sub}` 로 네임스페이스화(IdP 간 sub 충돌 방지). 로그인마다 역할·이메일·그룹을 IdP 값으로 덮어쓰고 커밋.
- L189-194 `_user_principal`: User 행 → Principal.
**짚어볼 점 / Watch-outs**
- L171 `groups = claims.get(...)` 와 L159-160 이 같은 정규화(list 화)를 두 번 한다 — 작은 중복.
- L185 인증 경로에서 **매 요청 commit** — 외부 IdP 토큰으로 API 를 호출할 때마다 사용자 행을 갱신한다(쓰기 부하, SQLite 잠금 경합). `last_login_at` 갱신은 로그인 시점에만 하는 편이 맞다.
- 동시 첫 로그인 두 요청이 모두 `user is None` 을 보고 INSERT 하면 `sub` 유니크 제약으로 한쪽이 `IntegrityError`(500) — 재시도 처리 없음.

```python path=app/core/auth.py lines=197-212
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
- L198-212 PAT·클라이언트 키: 토큰 원문이 아니라 **SHA-256 해시로 조회**(DB 유출 시 원문 노출 방지). 폐기·만료·사용자 상태 확인, `last_used_at` 갱신. 클라이언트 키는 역할을 `user` 로 낮추고 팀을 키 설정으로.
**짚어볼 점 / Watch-outs**
- L206 매 요청 `db.commit()` — PAT 사용 빈도만큼 쓰기 발생. `last_used_at` 은 1분 단위로만 갱신하는 식의 쓰로틀이 일반적이다.
- 해시 비교를 DB 등호 조회로 하므로 타이밍 공격 우려는 사실상 없다(해시 전 원문 비교가 아님).

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
- L214-217: 서명 검증 **전** 클레임을 읽어 issuer 로 분기(내장 vs 외부) — 이후 반드시 검증 경로를 거치므로 안전한 패턴이다(L219-223, L255-258).
- L219-223: 내장 issuer 면 `verify_local`(aud=`agentnexus` 필수, `app/core/security.py:28-35`). 교환 토큰(`aud=mcp:*` 등)은 여기서 거부된다.
- L224-233 user/delegation: 사용자 활성 확인, `tokens_not_before` 이전 발급 토큰 거부(세션 강제 종료), 위임 체인 `act.sub` 보존.
- L234-239 workload: 워크로드 활성 확인, 토큰 `scope` 로 권한 제한(역할 user).
- L240-248 access_token: DB 행(id=jti)으로 폐기·만료 확인 → 즉시 폐기 가능한 JWT(상태ful 확인).
- L249: 그 외 `anx_kind`(예: `exchanged`)는 거부.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L223 `f"토큰 검증 실패: {exc}"` — PyJWT 예외 메시지(예: "Signature has expired", "Invalid audience")를 클라이언트에 그대로 노출. 공격자에게 검증 단계 정보를 준다(정보 노출 CWE-209, 영향 낮음).
- L230 `iat < nbf.timestamp()` 비교는 초 단위 — 같은 초에 강제 종료와 재로그인이 일어나면 새 토큰도 거부될 수 있다(경계 조건).
- workload 토큰은 jti 기반 폐기 목록이 없다 — 워크로드를 비활성화(L236)해야만 즉시 차단된다.

```python path=app/core/auth.py lines=251-262
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
- L251-254: 외부 issuer 는 **활성 연동(IdpConnection)으로 등록된 경우만** 신뢰 — 임의 issuer 토큰 거부.
- L255-258: 검증 실패(네트워크·서명·만료·허용 외 alg)는 모두 401.
- L259-262: JIT upsert 후 상태 확인.
**짚어볼 점 / Watch-outs**
- L257 `except Exception` 은 401 변환이라 조용한 삼킴은 아니지만, L258 이 내부 오류 문자열(`{exc}`, 예: 연결 대상 호스트)을 응답에 싣는다.
- 외부 IdP 사용자도 `tokens_not_before` 를 확인하지 않는다(L224-233 은 내장 토큰만) — 세션 강제 종료가 외부 IdP 토큰에는 적용되지 않는다.

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
- L265-269 `bearer`: `Authorization: Bearer` 우선, 없으면 `X-API-Key`(OpenAI 호환 클라이언트 편의).
- L272-280 `optional_principal`: 요청당 1회만 인증하도록 `request.state.principal` 에 캐시 — 같은 요청에서 여러 의존성이 호출해도 DB 조회 1회.
- L283-286 `current_principal`: 없으면 401.
- L289-295 `require(*perms)`: 의존성 팩토리 — 클로저로 권한 목록을 캡처한 함수를 반환. 부족한 권한을 `required` 로 응답에 포함(디버깅 용이).
**짚어볼 점 / Watch-outs**
- L269 `X-API-Key` 도 같은 경로로 처리되므로, 브라우저 CORS 설정이 느슨하면 다른 출처가 헤더를 붙여 보낼 수 있다(쿠키 기반이 아니라 CSRF 위험은 낮음).
- `require` 는 권한(RBAC)만 본다 — **객체 수준 인가**(이 서버·이 에이전트의 소유자인가)는 각 엔드포인트가 따로 해야 하며, MCP·A2A 관리 API 에는 그 검사가 없다(위 L20-43 참조).

## 파일 평가 / File Assessment
- 강점: (1) 토큰 유형별 경로가 명확하고 모든 JWT 가 서명 검증을 거친다(외부 IdP 는 비대칭 alg 허용 목록 + 키 alg 일치). (2) 스코프와 역할의 교집합, 워크로드·Access Token 기본 거부. (3) PAT 해시 조회·Access Token 상태ful 폐기·세션 강제 종료.
- 위험: (1) [중간] 객체 수준 인가 부재 + developer 쓰기 권한(다른 파일에서 악용 경로 형성). (2) [낮음] kid 미스 시 무제한 JWKS 재조회·외부 IdP 토큰에 세션 강제 종료 미적용. (3) [낮음] 인증 경로의 매 요청 커밋(성능·잠금).
- 개선 권고(영향순): (1) MCP·A2A 리소스에 소유자/팀 기반 객체 인가 추가. (2) JWKS 강제 재조회 쓰로틀·외부 사용자에도 `tokens_not_before` 적용. (3) `last_used_at` 갱신 쓰로틀, 검증 실패 메시지 일반화.
- 재읽기 권고 레벨: 2 — 보안 경계의 중심이라 줄 단위 재검토 가치가 있다.
