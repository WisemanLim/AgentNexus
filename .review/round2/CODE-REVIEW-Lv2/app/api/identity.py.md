> 인용 검증 메모(2026-10-08): 인용 블록 17개(L1-490)를 스냅샷 원문(21:42 번호 추출본에서 재구성, sha1 `245ec05d…` = `snapshot-start.sha`)과 줄 단위 대조 — 불일치 0, 정정한 인용·줄 범위 없음. 보고된 "L1-17 인용에 `from app.core.config import settings` 포함" 은 이 파일 최종본에 없다(L1-19 블록이 원문과 일치). 현재 디스크 `app/api/identity.py` 는 Epic 51 편집으로 스냅샷과 다르므로(2026-10-08 08:5x 확인 시점에도 변경 중) 줄 번호는 스냅샷 기준이다.
> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [api_Abstract.md](api_Abstract.md) · 1차 리뷰: 1차 리뷰 대상 아님(부록 A)

### 30. app/api/identity.py
**파일 요약 / File Summary**
- 역할 / Role: Identity & Authorization 관리 API — 내 계정(`/api/v1/me`)·내 PAT/클라이언트 키 발급·폐기, 역할 행렬 조회, 사용자 생성·수정, 외부 IdP 연동 CRUD·디스커버리·시험 로그인, 토큰 교환 정책·이력, 워크로드 아이덴티티, Access Token 발급·폐기.
- 진입점 거리 / Distance: 1 (`app/main.py` 가 `identity` 구성요소일 때 `identity_api.router` 마운트)
- LOC: 490 (현재 `wc -l`) · 1차 부록 A 490 → 0
- 주요 export: `router`, 요청 모델 `TokenIn`·`UserIn`·`UserPatch`·`IdpIn`·`DiscoverIn`·`TxPolicyIn`·`WorkloadIn`·`AccessTokenIn`, 내부 `_check_roles`·`_end_idp_sessions`·`_discover_check`
- 의존(내부): `app.core.{security,auth(ROLE_LABELS·ROLE_PERMS·Principal·_JWKS·current_principal·require),errors,http,ids}`, `app.db`, `app.models`, `app.services.{audit,broker,identity,secrets}`
- 외부 패키지: fastapi, pydantic(`BaseModel`·`Field`), sqlalchemy
- 부수효과 / Side effects: DB 쓰기(사용자·토큰·IdP·정책·워크로드), 감사, 시크릿 저장(`secrets.put`), 외부 IdP discovery·JWKS HTTP(`_discover_check`), 이 워커의 `_JWKS` 캐시 무효화
- 동시성 / Concurrency: 모두 동기 핸들러(스레드풀). 이름 중복 검사 후 INSERT(사용자·IdP·워크로드)는 비원자 — 동시 생성 시 UNIQUE 위반 500.
- 테스트 커버리지 흔적: `test/test_identity.py:101-113`(PAT 수명주기), `:114-133`(워크로드), `:134-163`(교환 정책·이력), `:164-205`(Access Token), `:206-227`(외부 OIDC), `:228-243`(사용자 관리·비활성화), `:244-`(역할 행렬), `test/test_review_fixes.py:139-150`(Access Token subject), `test/test_hardening.py:98-105`(PAT 스코프), `test/test_ops.py:97`(PAT `ttl_days` 365 → 422), `test/test_design_gaps.py:58-62`(클라이언트 키 allowed_models), `test/test_llm.py:198-207`(클라이언트 키는 관리 API 불가), `test/test_idor_matrix.py:28·62`(남의 PAT 폐기 404), `test/test_sso.py:107-143`(IdP 마법사·시험 로그인). **토큰으로 토큰 발급, `ttl_days: null`, 클라이언트 키 팀 지정, security 역할의 admin 부여 시험 없음.**
- 2차 변경 요약 / Changes since 1st review: 파일 수정 시각 13:59 < 1차 스냅샷 14:03 이고 LOC 동일 → **1차 이후 변경 없음**으로 판단. SEC-07 대응(`subject_is_user`, L470-473)은 1차 스냅샷 직전 동시 작업으로 이미 들어가 있었다(1차 identity 보고서 L204 언급과 일치).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/identity.py lines=1-19
"""Identity & Authorization 관리 API (SCR-B06·I05·J01~J07·L03)."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import security
from app.core.auth import ROLE_LABELS, ROLE_PERMS, Principal, _JWKS, current_principal, require
from app.core.errors import bad_request, conflict, not_found
from app.core.http import sync_client
from app.core.ids import new_id
from app.db import get_db, utcnow
from app.models import (AccessToken, BrokerRequest, IdpConnection, PersonalToken, RefreshToken, TokenExchange, TokenExchangePolicy, User,
                        Workload)
from app.services import audit, broker, identity, secrets

router = APIRouter(tags=["identity"])


```
**분석 / Analysis (Level 2)**
- L8 `_JWKS`: 다른 모듈의 밑줄(내부) 전역을 import 해 직접 `pop` 한다(L235) — 캐시 무효화 API(`auth.invalidate_jwks(issuer)`)가 없어서 생긴 결합. 다른 워커 캐시는 지우지 못한다.
- L10 `sync_client`: 디스커버리 시험의 JWKS 조회(L290) — egress 가드 적용 팩토리.

```python path=app/api/identity.py lines=20-46
# ───────── 내 계정 (SCR-B06) ─────────
@router.get("/api/v1/me")
def me(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    perms = sorted(p.permissions())
    u = db.get(User, p.user_id) if p.user_id else None
    own = {"issuer": u.issuer, "idp": (u.claims or {}).get("idp"), "mfa_enabled": u.mfa_enabled, "last_login_at": u.last_login_at,
           "claims": {k: v for k, v in (u.claims or {}).items() if k != "keys"}} if u else {}  # 내 IdP·그룹 클레임 (SCR-B06 ⑥)
    return {**p.snapshot(), "scopes": p.scopes, "actor": p.actor, "permissions": perms,
            "role_labels": {r: ROLE_LABELS.get(r, r) for r in p.roles}, **own}


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field("pat", pattern="^(pat|client_key)$")
    scopes: list[str] = []
    ttl_days: int | None = Field(90, ge=1, le=90)  # SCR-B06 — 최대 90일
    team: str | None = None
    allowed_models: list[str] | None = Field(None, max_length=50)  # 클라이언트 키 허용 모델 별칭 (SCR-H13 ④)


def _token_out(t: PersonalToken) -> dict:
    return {"id": t.id, "kind": t.kind, "name": t.name, "prefix": t.prefix, "scopes": t.scopes, "team": t.team,
            "allowed_models": t.allowed_models,
            "expires_at": t.expires_at, "revoked_at": t.revoked_at, "last_used_at": t.last_used_at,
            "created_at": t.created_at}


```
**분석 / Analysis (Level 2)**
- L21-28 `me`: 주체 스냅샷 + 실효 권한 + (사용자면) IdP·MFA·클레임. `claims` 에서 `keys`(원 클레임 키 목록)는 뺀다. `permissions` 는 역할 권한이라 스코프 제한 토큰이면 실제 허용보다 넓게 보인다(표시만의 문제 — `scopes` 를 함께 반환).
- L31-37 `TokenIn`: 이름 길이, 종류 `pat|client_key`, 만료 1~90일, 클라이언트 키 허용 모델 50개 상한. pydantic 이 422 로 거부(`test/test_ops.py:97` 365일 → 422).
- L40-44 `_token_out`: 원문·해시는 내보내지 않고 접두(`prefix`)만.
**짚어볼 점 / Watch-outs**
- [위험:중간] L35 `ttl_days: int | None = Field(90, ge=1, le=90)` — Pydantic v2 에서 `ge`·`le` 는 `int` 분기에만 적용되고 `None` 은 그대로 통과한다. JSON `{"ttl_days": null}` → `create_personal_token(..., ttl_days=None)` → `expires_at=None`(`app/services/identity.py:159`) → **만료 없는 PAT/클라이언트 키**. 콘솔은 항상 숫자를 보내지만(`web/src/pages/Me.tsx:116`, `web/src/pages/gateway/GatewayMore.tsx:725`) API 를 직접 부르면 "최대 90일"(SCR-B06) 정책이 우회된다. 시나리오: 퇴사 예정자가 만료 없는 PAT 를 만들어 두면 계정이 비활성화될 때까지(비활성화는 즉시 차단 — `app/core/auth.py:203`) 살아 있다 — 정기 만료로 잊힌 토큰을 정리하는 통제가 사라진다. 최소 수정: `ttl_days: int = Field(90, ge=1, le=90)`(None 금지). 시험 공백: null 입력 시험 없음.
- [위험:중간] L36 `team` 을 발급자가 자유롭게 정한다 — 클라이언트 키 인증 시 `p.team = row.team`(`app/core/auth.py:211`)으로 그대로 쓰여 팀 전용 KB(`app/services/knowledge.py:46-47`)·팀 주체 정책(`app/services/policy.py` team 매칭)·팀 한도/비용 귀속(`app/services/ratelimit.py:193-196`)을 남의 팀으로 통과·전가한다. 상세 시나리오는 auth 보고서 L189-213. 최소 수정: `body.team` 은 `user.team` 과 같거나 호출자가 `admin:write`(또는 `gateway:write`)일 때만 허용.

```python path=app/api/identity.py lines=47-77
@router.get("/api/v1/me/tokens")
def my_tokens(p: Principal = Depends(current_principal), db: Session = Depends(get_db), kind: str | None = None):
    stmt = select(PersonalToken).where(PersonalToken.user_id == p.user_id).order_by(PersonalToken.created_at.desc())
    if kind:
        stmt = stmt.where(PersonalToken.kind == kind)
    return {"items": [_token_out(t) for t in db.scalars(stmt)]}


@router.post("/api/v1/me/tokens", status_code=201)
def create_my_token(body: TokenIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if p.user_id is None:
        raise bad_request("사용자 토큰으로만 발급할 수 있습니다.")
    if body.kind == "client_key" and not p.has("gateway:read"):
        raise bad_request("클라이언트 키는 게이트웨이 권한이 필요합니다.")
    if body.allowed_models and body.kind != "client_key":
        raise bad_request("allowed_models 는 클라이언트 키에만 지정합니다.")
    if body.allowed_models:
        from app.models import Model, RoutingRule
        known = {m.id for m in db.scalars(select(Model))} | {r.match_model for r in db.scalars(select(RoutingRule))} | {"auto"}
        bad = [m for m in body.allowed_models if m not in known]
        if bad:
            raise bad_request(f"알 수 없는 모델 별칭: {', '.join(bad)} (MSG-004)", code="unknown_model")
    user = identity.get_user(db, p.user_id)
    row, raw = identity.create_personal_token(db, user, body.name, body.kind, body.scopes, body.ttl_days,
                                              body.team or user.team)
    row.allowed_models = sorted(set(body.allowed_models)) if body.allowed_models else None
    db.commit()
    audit.append("token.personal.create", actor=p.sub, target=row.id, data={"kind": row.kind, "name": row.name})
    return {**_token_out(row), "token": raw}


```
**분석 / Analysis (Level 2)**
- L47-52 `my_tokens`: 자기 `user_id` 의 토큰만(객체 인가 OK). 워크로드·Access Token 주체는 `user_id=None` → `WHERE user_id IS NULL` → 빈 목록(모델상 `user_id` 는 FK 필수 — `app/models.py:47`).
- L55-75 `create_my_token`: 사용자 주체만(L57), 클라이언트 키는 `gateway:read` 필요(L59), 허용 모델은 알려진 별칭만(L63-68), 발급·감사·원문 1회 반환.
**짚어볼 점 / Watch-outs**
- [위험:높음] L55-57 이 엔드포인트는 `require(...)` 없이 `current_principal` 만 요구하고, 막는 조건은 `p.user_id is None` 하나다. 그래서 **사용자 세션이 아닌 토큰 — 스코프가 좁은 PAT, 역할을 `user` 로 낮춘 클라이언트 키(`app/core/auth.py:209-210`), 위임 토큰(`kind=delegation`, 30분 — `app/services/orchestrator.py:63`) — 도 새 PAT 를 만들 수 있다.** 새 PAT 는 `scopes` 기본 `[]` = 스코프 없음 = 소유자 역할 권한 전체(`app/core/auth.py:71`), 그리고 L35 때문에 만료 없음도 가능. 시나리오: 운영자(operator)가 앱에 배포한 클라이언트 키(`anx_key_…` — "게이트웨이 호출 전용, 관리 API 불가" 를 `test/test_llm.py:206-207` 가 보증)가 유출 → 공격자가 그 키로 `POST /api/v1/me/tokens {"name":"x","ttl_days":null}` → 운영자 역할 전체(`admin:write`·`gateway:write`·`tokens:issue` …)의 만료 없는 PAT 를 받는다. 같은 방식으로 `catalog:read` 만 가진 CI 용 PAT 가 무제한 PAT 로 승격된다(`test/test_hardening.py:98-105` 가 지키려는 "스코프 밖 거부" 불변식의 우회). 확신: 코드 경로 확인됨(실행 미검증). 최소 수정: L57 을 `if p.kind != "user" or p.user_id is None: raise forbidden(...)`(대화형 세션만 발급) 로 바꾸거나, 최소한 새 토큰 스코프를 호출자 `scope_perms()` 의 부분집합으로 강제하고 클라이언트 키·위임 토큰은 거부. 같은 이유로 L78 폐기 엔드포인트도 사용자 세션으로 제한 검토. 시험 공백: PAT·클라이언트 키·위임 토큰으로 `/api/v1/me/tokens` POST 하는 음성 시험 없음.
- L59-60 권한 부족을 `400 bad_request` 로 응답 — 의미상 403 이 맞다(클라이언트 오류 처리 분기 혼동).
- L69-71 클라이언트 키의 팀(`body.team or user.team`) 문제는 위 L36 [위험:중간].

```python path=app/api/identity.py lines=78-97
@router.delete("/api/v1/me/tokens/{token_id}")
def revoke_my_token(token_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    row = db.get(PersonalToken, token_id)
    if row is None or row.user_id != p.user_id:
        raise not_found("token")
    row.revoked_at = utcnow()
    db.commit()
    audit.append("token.personal.revoke", actor=p.sub, target=row.id)
    return _token_out(row)


# ───────── 역할·권한 (SCR-L03) ─────────
@router.get("/api/v1/identity/roles")
def roles(_: Principal = Depends(require("identity:read"))):
    all_perms = sorted({x for v in ROLE_PERMS.values() for x in v if x != "*"})
    return {"roles": [{"id": r, "label": ROLE_LABELS.get(r, r),
                       "permissions": all_perms if "*" in perms else sorted(perms)} for r, perms in ROLE_PERMS.items()],
            "permissions": all_perms}


```
**분석 / Analysis (Level 2)**
- L78-86 `revoke_my_token`: 남의 토큰이면 **404**(존재 비노출 — Epic 48 원칙, `test/test_idor_matrix.py:62`). 폐기는 즉시(`app/core/auth.py:200`).
- L90-95 `roles`: 기본 역할 행렬(재정의 미반영 — 재정의 반영 행렬은 `app/api/ops.py` 쪽). admin 은 모든 권한으로 펼쳐 표시.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L79 도 `current_principal` 만 — 스코프가 좁은 PAT·클라이언트 키가 소유자의 다른 토큰을 모두 폐기할 수 있다(유출 토큰으로 서비스 방해). L55 수정과 함께 사용자 세션 전용으로.
- L83 이미 폐기된 토큰도 `revoked_at` 을 새 시각으로 덮어쓴다 — 최초 폐기 시각이 사라진다(`app/api/identity.py:487` Access Token 쪽은 `row.revoked_at or utcnow()` 로 보존 — 일관성).

```python path=app/api/identity.py lines=98-126
# ───────── 사용자·그룹·역할 (SCR-J03) ─────────
def _user_out(u: User) -> dict:
    return {"id": u.id, "sub": u.sub, "username": u.username, "email": u.email, "name": u.name, "roles": u.roles,
            "groups": u.groups, "team": u.team, "status": u.status, "issuer": u.issuer,
            "last_login_at": u.last_login_at, "created_at": u.created_at}


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=120, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=8)
    name: str | None = None
    email: str | None = None
    roles: list[str] = ["user"]
    team: str | None = None


class UserPatch(BaseModel):
    roles: list[str] | None = None
    team: str | None = None
    status: str | None = Field(None, pattern="^(active|disabled)$")
    name: str | None = None


def _check_roles(roles: list[str]) -> None:
    bad = [r for r in roles if r not in ROLE_PERMS]
    if bad:
        raise bad_request(f"알 수 없는 역할: {', '.join(bad)}")


```
**분석 / Analysis (Level 2)**
- L99-102 `_user_out`: 비밀번호 해시·MFA 비밀은 제외.
- L105-111 `UserIn`: 사용자 이름 문자 집합 제한(로그인 키 정규화 `strip().lower()` 와 충돌 없음), 비밀번호 8자 이상.
- L114-118 `UserPatch`: 수정 가능한 필드를 모델로 한정 — `body: dict` 를 쓰는 다른 PATCH(L217·L428)보다 안전한 패턴.
- L121-124 `_check_roles`: 역할 이름이 행렬에 있는지만 확인.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L107 비밀번호 정책이 8자뿐 — 첫 관리자 설정은 14자·3종·유출 목록 검사(`app/services/setup.py:133-145` `password_problems`)를 적용하는데 관리 API 로 만드는 사용자에게는 적용하지 않는다. 사용자 이름별 실패 한도(10회/15분 ≈ 하루 960회 — `app/api/idp.py:99-100`)와 결합하면 약한 8자 비밀번호는 추측 가능 범위. 최소 수정: `create_user` 에서도 `password_problems` 적용.
- L121-124 이름만 확인하고 **호출자가 그 역할을 부여해도 되는지**는 보지 않는다 — 아래 L152-163 [위험:중간].

```python path=app/api/identity.py lines=127-165
@router.get("/api/v1/identity/users")
def list_users(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db), q: str | None = None,
               role: str | None = None, limit: int = Query(200, le=1000)):
    stmt = select(User).order_by(User.created_at).limit(limit)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.username.like(like), User.email.like(like), User.name.like(like)))
    users = [u for u in db.scalars(stmt) if not role or role in (u.roles or [])]
    return {"items": [_user_out(u) for u in users]}


@router.post("/api/v1/identity/users", status_code=201)
def create_user(body: UserIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    _check_roles(body.roles)
    if db.scalars(select(User).where(User.username == body.username)).first():
        raise conflict("이미 존재하는 사용자 이름입니다.")
    uid = new_id("usr")
    user = User(id=uid, sub=uid, issuer=security.issuer(), username=body.username, name=body.name, email=body.email,
                password_hash=security.hash_password(body.password), roles=body.roles, team=body.team)
    db.add(user)
    db.commit()
    audit.append("identity.user.create", actor=p.sub, target=user.sub, data={"roles": body.roles})
    return _user_out(user)


@router.patch("/api/v1/identity/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, p: Principal = Depends(require("identity:write")),
               db: Session = Depends(get_db)):
    user = identity.get_user(db, user_id)
    changes = body.model_dump(exclude_none=True)
    if "roles" in changes:
        _check_roles(changes["roles"])
    for k, v in changes.items():
        setattr(user, k, v)
    db.commit()
    audit.append("identity.user.update", actor=p.sub, target=user.sub, data=changes)
    return _user_out(user)


```
**분석 / Analysis (Level 2)**
- L127-135 `list_users`: `identity:read`, 이름·이메일·표시명 LIKE 검색(파라미터 바인딩이라 SQL 인젝션 없음), 역할은 파이썬에서 필터.
- L138-149 `create_user`: `identity:write`, 역할 이름 검증, 사용자 이름 중복 409, 내장 사용자는 `sub = id`(외부 사용자 `ext:` 와 네임스페이스 분리), scrypt(p=5) 해시.
- L152-163 `patch_user`: 모델에 있는 필드만 `setattr`, 변경분 감사. 역할·상태 변경은 다음 요청부터 반영(인증이 매번 DB 역할·상태를 읽음 — `app/core/auth.py:191-192`·`:203`·`:227`).
**짚어볼 점 / Watch-outs**
- [위험:중간] L138-163 `identity:write` 보유자는 **어떤 역할이든** 부여할 수 있다. `security` 역할은 `identity:write` 를 갖지만 `admin:write`·`gateway:write` 등은 없도록 설계됐는데(`app/core/auth.py:29-31`), `PATCH /api/v1/identity/users/{자기 id} {"roles":["admin"]}` 한 번으로 플랫폼 관리자(`*`)가 된다. 새 사용자 생성(L140)·IdP 역할 매핑(L202-203·L225-226 — 자기가 통제하는 그룹을 `admin` 으로 매핑) 경로도 같다. 관리자 계정을 `disabled` 로 바꿔 잠그는 것도 가능. 역할 분리(보안 담당 ≠ 플랫폼 관리자)가 의도라면 권한 상승이다(설계 의도 확인 필요). 최소 수정: 부여할 역할의 권한이 호출자 실효 권한의 부분집합일 때만 허용(`role_perms(r) <= p.permissions()` 또는 `"*"` 보유), `admin` 부여·관리자 상태 변경은 admin 만. 시험 공백: `test/test_identity.py:228-243` 은 security 가 `finops` 를 부여하는 정상 경로만.
- [위험:낮음] L129 `limit` 에 하한이 없다 — `limit=-1` 이면 PostgreSQL 은 `LIMIT must not be negative` 오류(500), SQLite 는 무제한. 또 L134 역할 필터를 `LIMIT` **뒤**에 파이썬으로 하므로 사용자가 200명을 넘으면 해당 역할 사용자가 결과에서 빠진다(기능 결함). 최소 수정: `Query(200, ge=1, le=1000)`, 역할 필터는 JSON 포함 조건 또는 페이지네이션 후 안내.
- L132-133 LIKE 와일드카드(`%`·`_`) 미이스케이프(SEC-08, 39th 판정 "노출 없음" 으로 미수정) — 권한 범위 안의 검색 동작 차이.
- L141-146 이름 중복 검사 후 INSERT 는 비원자 — 동시 생성 시 `username` UNIQUE 위반 500.

```python path=app/api/identity.py lines=166-199
# ───────── IdP 연동 (SCR-J01·J02) ─────────
class IdpIn(BaseModel):
    name: str
    kind: str = Field("oidc", pattern="^(oidc|entra|okta|keycloak)$")
    issuer: str
    client_id: str | None = None
    client_secret: str | None = None
    audience: str | None = None
    jwks_uri: str | None = None
    groups_claim: str = "groups"
    roles_claim: str = "roles"
    role_mapping: dict[str, str] = {}
    enabled: bool = True
    login_enabled: bool = False  # 시험 로그인 성공 후 켠다 (SCR-J02 ④)
    internal_url: str | None = None
    scopes: str = "openid profile email"


def _idp_out(i: IdpConnection) -> dict:
    return {"id": i.id, "name": i.name, "kind": i.kind, "issuer": i.issuer, "client_id": i.client_id,
            "client_secret": "••••" if i.client_secret_ref else None, "audience": i.audience, "jwks_uri": i.jwks_uri,
            "groups_claim": i.groups_claim, "roles_claim": i.roles_claim, "role_mapping": i.role_mapping,
            "enabled": i.enabled, "login_enabled": i.login_enabled, "internal_url": i.internal_url, "scopes": i.scopes,
            "redirect_uri": broker.callback_url(), "last_test": i.last_test or {}, "status": i.status,
            "last_tested_at": i.last_tested_at, "created_at": i.created_at}


@router.get("/api/v1/identity/idps")
def list_idps(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    builtin = {"id": "builtin", "name": "AgentNexus 내장 IdP", "kind": "builtin", "issuer": security.issuer(),
               "enabled": True, "status": "ok"}
    return {"items": [builtin] + [_idp_out(i) for i in db.scalars(select(IdpConnection).order_by(IdpConnection.name))]}


```
**분석 / Analysis (Level 2)**
- L167-181 `IdpIn`: 생성 입력. `login_enabled` 기본 False — 시험 로그인 성공 후에만 켤 수 있게(L223-224). `client_secret` 은 시크릿 저장소로(L207-209), 응답에는 `••••`(L186).
- L184-190 `_idp_out`: 브로커 콜백 URL(외부 IdP 에 등록할 값) 포함.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L170·L174·L180 `issuer`·`jwks_uri`·`internal_url` 형식 검증이 없다(스킴·호스트). `identity:write` 보유자가 사설망 주소를 넣으면 서버가 그 주소로 GET 한다(L264-296 시험, 인증 시 `app/core/auth.py:131-135`) — egress 가드는 IMDS·링크 로컬만 막는다. 1차 SEC-09 와 같은 수용된 위험(39th 판정). 최소 수정: 운영 모드에서 https 강제·`internal_url` 은 운영자 설정 허용 목록.

```python path=app/api/identity.py lines=200-215
@router.post("/api/v1/identity/idps", status_code=201)
def create_idp(body: IdpIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    for v in body.role_mapping.values():
        _check_roles([v])
    if db.scalars(select(IdpConnection).where(IdpConnection.issuer == body.issuer)).first():
        raise conflict("이미 연동된 issuer 입니다.")
    idp = IdpConnection(id=new_id("idp"), **body.model_dump(exclude={"client_secret"}))
    if body.client_secret:
        idp.client_secret_ref = f"idp/{idp.id}/client_secret"
        secrets.put(db, idp.client_secret_ref, body.client_secret, kind="oauth_client")
    db.add(idp)
    db.commit()
    audit.append("identity.idp.create", actor=p.sub, target=idp.issuer, data={"kind": idp.kind})
    return _idp_out(idp)


```
**분석 / Analysis (Level 2)**
- L202-203: 매핑 대상 역할 이름 검증. L204-205: issuer 중복 409(인증 시 issuer 로 연동을 찾으므로 유일해야 함 — `app/core/auth.py:251-252`).
- L206-211: `client_secret` 만 빼고 모델로, 시크릿은 `idp/{id}/client_secret` 이름으로 암호화 저장 후 같은 트랜잭션에서 커밋(`secrets.put` 은 flush 만).
**짚어볼 점 / Watch-outs**
- (L138-163 [위험:중간] 의 한 경로) L202-203 `role_mapping` 값으로 `admin` 을 지정할 수 있다 — 호출자 권한 상한 검사 없음.

```python path=app/api/identity.py lines=216-241
@router.patch("/api/v1/identity/idps/{idp_id}")
def patch_idp(idp_id: str, body: dict, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    allowed = {"name", "client_id", "audience", "jwks_uri", "groups_claim", "roles_claim", "role_mapping", "enabled",
               "login_enabled", "internal_url", "scopes"}
    if body.get("login_enabled") and not idp.login_enabled and (idp.last_test or {}).get("status") != "ok":
        raise bad_request("콘솔 로그인에 노출하려면 먼저 시험 로그인에 성공해야 합니다 (SCR-J02 ④).", code="test_login_required")
    if isinstance(body.get("role_mapping"), dict):
        _check_roles(list(body["role_mapping"].values()))
    disabling = idp.enabled and body.get("enabled") is False
    for k, v in body.items():
        if k in allowed:
            setattr(idp, k, v)
    ended = _end_idp_sessions(db, idp) if disabling else 0  # 연동 중지 = 그 IdP 사용자 세션 종료 (SCR-J01)
    if body.get("client_secret"):
        idp.client_secret_ref = idp.client_secret_ref or f"idp/{idp.id}/client_secret"
        secrets.put(db, idp.client_secret_ref, body["client_secret"], kind="oauth_client")
    _JWKS.pop(idp.issuer, None)
    db.commit()
    audit.append("identity.idp.update", actor=p.sub, target=idp.issuer, data={k: v for k, v in body.items()
                                                                              if k != "client_secret"} | ({"sessions_ended": ended} if disabling else {}))
    return _idp_out(idp) | ({"sessions_ended": ended} if disabling else {})


```
**분석 / Analysis (Level 2)**
- L221-222: 수정 가능 키 허용 목록 — `issuer`·`client_secret_ref`·`status`·`last_test` 는 직접 못 바꾼다(좋음).
- L223-224: 로그인 노출은 시험 로그인 성공 뒤에만(SCR-J02 ④).
- L227·L231: `enabled` 를 `True → False` 로 바꾸면 그 IdP 사용자 세션 종료(L242-250). `is False` 비교라 정확히 불리언 false 일 때만.
- L232-234: 새 client_secret 은 같은 이름으로 회전. L235: 이 워커의 JWKS 캐시 무효화. L237-238: 감사에서 client_secret 제외.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L217 `body: dict` — 값의 **타입 검증이 없다**. `role_mapping` 이 dict 가 아니면(L225 의 `isinstance` 를 건너뛰고) 리스트·문자열이 그대로 저장되어 인증 시 `_map_roles` 의 `idp.role_mapping[v]` 에서 `TypeError` → 그 IdP 의 모든 토큰 인증 500(`app/core/auth.py:163`). `enabled: "false"`(문자열)는 `is False` 가 아니라 세션 종료 없이 저장되고, 저장값은 진리값으로 참 — "끈 줄 알았는데 켜져 있음". 최소 수정: `IdpPatch(BaseModel)`(모든 필드 Optional, 타입 지정) + `model_dump(exclude_unset=True)`.
- [위험:낮음] L235 `_JWKS.pop` 은 요청을 받은 워커에만 적용 — 다른 워커는 최대 600초(`app/core/auth.py:124`) 이전 `jwks_uri`·키를 계속 신뢰한다. 키 유출 대응으로 `jwks_uri` 를 바꿔도 즉시 반영되지 않는다. 최소 수정: 캐시 키에 `jwks_uri`·`updated_at` 을 포함하거나 공유 저장소(Valkey) 버전 번호로 무효화.

```python path=app/api/identity.py lines=242-263
def _end_idp_sessions(db: Session, idp: IdpConnection) -> int:
    """IdP 사용자(sub=ext:{id}:*)의 refresh 폐기 + tokens_not_before — 발급된 액세스 토큰도 다음 요청부터 401."""
    now, n = utcnow(), 0
    users = list(db.scalars(select(User).where(User.sub.like(f"ext:{idp.id}:%"))))
    for u in users:
        u.tokens_not_before = now
        for r in db.scalars(select(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None))):
            r.revoked_at, n = now, n + 1
    return n


@router.delete("/api/v1/identity/idps/{idp_id}")
def delete_idp(idp_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    db.delete(idp)
    db.commit()
    audit.append("identity.idp.delete", actor=p.sub, target=idp.issuer)
    return {"deleted": idp_id}


```
**분석 / Analysis (Level 2)**
- L242-250 `_end_idp_sessions`: `sub LIKE 'ext:{id}:%'` 로 그 IdP 사용자를 찾아 `tokens_not_before = now`(내장 액세스 토큰 거부 — `app/core/auth.py:229-231`) + 살아 있는 refresh 폐기. IdP id 는 `new_id` 생성값이라 `%`·`_` 가 없어 LIKE 패턴이 안전하다. 커밋은 호출자(L236).
- L253-261 `delete_idp`: 행 삭제·감사.
**짚어볼 점 / Watch-outs**
- [위험:중간] L253-261 연동 **삭제**는 세션을 끝내지 않는다 — **중지**(L227·L231)는 끝낸다. 삭제 뒤에도 그 IdP 로 로그인했던 사용자의 내장 refresh token 은 살아 있고, `refresh()` 는 사용자 상태만 확인하므로(`app/services/identity.py:97-99`) 회전마다 14일씩 연장되어 사실상 무기한 유지된다(외부 원본 토큰만 issuer 미등록으로 401 — `app/core/auth.py:253-254`). 시나리오: 외부 IdP 가 탈취돼 관리자가 "연동 삭제" 로 대응 → 이미 탈취 IdP 로 로그인한 공격자 세션은 계속 갱신된다. 최소 수정: 삭제 전에 `_end_idp_sessions(db, idp)` 호출(또는 "먼저 중지해야 삭제 가능" 규칙). 시험 공백: 삭제 후 refresh 거부 시험 없음.
- L245-249 사용자마다 refresh 조회(N+1) — 사용자가 많은 IdP 중지 시 요청이 길어진다(단일 UPDATE `WHERE user_id IN (…)` 로 대체 가능).

```python path=app/api/identity.py lines=264-298
@router.post("/api/v1/identity/idps/{idp_id}/test")
def test_idp(idp_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    """OIDC 디스커버리 → JWKS 조회로 연결 시험 (SCR-J02 'Issuer·디스커버리')."""
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    result = _discover_check(idp.issuer, idp.internal_url)
    if result["discovery"]:
        idp.discovery = result["endpoints"]
        idp.jwks_uri = idp.jwks_uri or result["endpoints"].get("jwks_uri")
    idp.status = "ok" if result["discovery"] and result["jwks"] else "error"
    idp.last_tested_at = utcnow()
    db.commit()
    return result


def _discover_check(issuer: str, internal_url: str | None) -> dict:
    """디스커버리(issuer 일치) → JWKS 키 → PKCE S256 지원 여부 (SCR-J02 ②)."""
    out = {"issuer": issuer, "discovery": False, "jwks": False, "keys": 0, "pkce_s256": False, "endpoints": {}, "error": None}
    try:
        meta = broker.discover(issuer, internal_url)
        out["discovery"] = True
        out["endpoints"] = {k: meta.get(k) for k in ("authorization_endpoint", "token_endpoint", "jwks_uri",
                                                     "userinfo_endpoint", "code_challenge_methods_supported")}
        out["pkce_s256"] = "S256" in (meta.get("code_challenge_methods_supported") or [])
        tmp = IdpConnection(issuer=issuer, internal_url=internal_url)
        with sync_client(timeout=10) as http:
            ks = http.get(broker.srv(tmp, meta["jwks_uri"])).json()
        out["keys"] = len(ks.get("keys", []))
        out["jwks"] = out["keys"] > 0
    except Exception as exc:  # noqa: BLE001 — 네트워크·형식·issuer 불일치
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


```
**분석 / Analysis (Level 2)**
- L264-277 `test_idp`: 디스커버리 결과를 저장(브로커가 쓰는 엔드포인트 — `app/services/broker.py:63-64`), `jwks_uri` 가 비어 있으면 채움, 상태 갱신.
- L280-296 `_discover_check`: `broker.discover` 가 `issuer` 일치를 확인(`app/services/broker.py:52-60` — 메타데이터 위조로 다른 issuer 를 끼워 넣지 못함) → JWKS 키 수 → PKCE S256 지원. 예외는 결과 객체의 `error` 로(시험 화면 표시용 — 삼킴이 아니라 보고). `tmp` 는 세션에 추가하지 않는 임시 ORM 객체(`srv` 계산용).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L295 예외 메시지 원문을 응답에 싣는다 — 사설망 주소를 넣은 시험에서 연결 오류·HTTP 오류 문구(호스트·포트 개방 여부)가 그대로 돌아와 내부망 탐색 신호가 된다. L286-287 은 임의 URL 의 JSON 필드 일부도 반사한다. 권한은 `identity:write` 라 낮음(round2 위임 보안 리뷰도 `_discover_check` 를 다룸). 최소 수정: 오류는 분류 코드(`unreachable`·`issuer_mismatch`·`invalid_json`)만.
- L290-291 상태 코드 확인 없이 `.json()` — HTML 오류 페이지면 `JSONDecodeError` 문구가 `error` 로.

```python path=app/api/identity.py lines=299-333
class DiscoverIn(BaseModel):
    issuer: str
    internal_url: str | None = None


@router.post("/api/v1/identity/idps/discover")
def discover_idp(body: DiscoverIn, _: Principal = Depends(require("identity:write"))):
    """저장 전 디스커버리 시험 (SCR-J02 ②) — 연결을 만들지 않는다."""
    return {**_discover_check(body.issuer, body.internal_url), "redirect_uri": broker.callback_url()}


@router.post("/api/v1/identity/idps/{idp_id}/test-login")
def start_test_login(idp_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    """시험 로그인 (SCR-J02 ④) — 새 창에서 외부 IdP 로그인 → 클레임·역할 매핑 결과만 저장(로그인 세션은 만들지 않음)."""
    idp = db.get(IdpConnection, idp_id)
    if idp is None:
        raise not_found("idp")
    try:
        url = broker.start(db, idp, {}, mode="test", actor=p.sub)
    except broker.BrokerError as exc:
        raise bad_request(exc.detail, code=exc.code) from exc
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(url).query)["state"][0]
    return {"url": url, "state": state}


@router.get("/api/v1/identity/idps/{idp_id}/test-login/{state}")
def test_login_result(idp_id: str, state: str, _: Principal = Depends(require("identity:write")),
                      db: Session = Depends(get_db)):
    req = db.get(BrokerRequest, state)
    if req is None or req.idp_id != idp_id or req.mode != "test":
        raise not_found("test login")
    return req.result or {"status": "pending"}


```
**분석 / Analysis (Level 2)**
- L304-307 `discover_idp`: 저장 없이 디스커버리만 — 마법사 2단계.
- L310-322 `start_test_login`: `mode="test"` 브로커 요청 — 콜백에서 세션을 만들지 않고 결과만 저장(`app/api/idp.py:219-228`). 생성된 URL 의 `state` 를 콘솔이 폴링 키로 쓴다.
- L325-331 `test_login_result`: state·IdP·모드가 모두 맞아야 결과 반환(다른 IdP 의 state 로 조회 불가).
**짚어볼 점 / Watch-outs**
- L304-307 (L280-296 과 같은 [위험:낮음]) 저장 없는 임의 URL 조회 도구 — `identity:write` 보유자에게 내부망 GET 프로브가 된다.
- L326-331 시험 결과에 시험 사용자의 클레임·역할이 들어 있다 — `created_by`(`app/models.py:97`)를 확인하지 않아 다른 `identity:write` 보유자도 state 를 알면 볼 수 있다(같은 권한군이라 영향 낮음).

```python path=app/api/identity.py lines=334-371
# ───────── 토큰 교환 정책·이력 (SCR-J04·J05) ─────────
class TxPolicyIn(BaseModel):
    audience: str = Field(pattern=r"^(mcp|a2a|model|api):[A-Za-z0-9._*-]+$")
    allowed_scopes: list[str]
    default_scopes: list[str] = []
    ttl_seconds: int = Field(300, ge=30, le=900)  # SCR-J04 — 교환 토큰은 최대 15분
    method: str = Field("rfc8693", pattern="^(rfc8693|obo)$")
    enabled: bool = True


def _txp_out(t: TokenExchangePolicy) -> dict:
    return {"id": t.id, "audience": t.audience, "audience_type": t.audience_type, "allowed_scopes": t.allowed_scopes,
            "default_scopes": t.default_scopes, "ttl_seconds": t.ttl_seconds, "method": t.method, "enabled": t.enabled,
            "updated_at": t.updated_at}


@router.get("/api/v1/identity/token-exchange/policies")
def list_tx_policies(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    return {"items": [_txp_out(t) for t in db.scalars(select(TokenExchangePolicy).order_by(TokenExchangePolicy.audience))]}


@router.put("/api/v1/identity/token-exchange/policies")
def upsert_tx_policy(body: TxPolicyIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    if not set(body.default_scopes) <= set(body.allowed_scopes):
        raise bad_request("default_scopes 는 allowed_scopes 의 부분집합이어야 합니다.")
    row = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == body.audience)).first()
    kind = {"mcp": "mcp_server", "a2a": "a2a_agent", "model": "model", "api": "api"}[body.audience.split(":")[0]]
    if row is None:
        row = TokenExchangePolicy(id=new_id("txp"), audience_type=kind, **body.model_dump())
        db.add(row)
    else:
        for k, v in body.model_dump().items():
            setattr(row, k, v)
    db.commit()
    audit.append("identity.token_exchange_policy.upsert", actor=p.sub, target=row.audience, data=body.model_dump())
    return _txp_out(row)


```
**분석 / Analysis (Level 2)**
- L335-341 `TxPolicyIn`: audience 형식(`종류:이름`), TTL 30초~15분, 방식 `rfc8693|obo`.
- L355-369 `upsert_tx_policy`: default ⊆ allowed 검증, audience 접두로 종류 결정(정규식이 접두를 보장하므로 dict 조회 KeyError 없음), 생성·갱신·감사.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L336 패턴이 `*` 를 허용하지만 교환은 audience **정확 일치**로만 정책을 찾는다(`app/services/identity.py:177`, 저장소 전체 `TokenExchangePolicy.audience ==` 만 사용) — `mcp:*` 정책은 저장되지만 어떤 교환에도 적용되지 않는 죽은 설정(운영자는 와일드카드가 동작한다고 오해). 최소 수정: 패턴에서 `*` 제거 또는 조회에 와일드카드 매칭 구현.
- L338 `default_scopes` 를 비우면 교환 시 `allowed_scopes` 전체가 기본 부여된다(`app/services/identity.py:191`) — 최소 스코프 원칙상 default 필수 검토.

```python path=app/api/identity.py lines=372-400
@router.get("/api/v1/identity/token-exchanges")
def list_exchanges(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db),
                   trace_id: str | None = None, subject: str | None = None, audience: str | None = None,
                   limit: int = Query(100, le=1000)):
    stmt = select(TokenExchange).order_by(TokenExchange.ts.desc()).limit(limit)
    if trace_id:
        stmt = stmt.where(TokenExchange.trace_id == trace_id)
    if subject:
        stmt = stmt.where(TokenExchange.subject_sub == subject)
    if audience:
        stmt = stmt.where(TokenExchange.audience == audience)
    return {"items": [{"id": t.id, "ts": t.ts, "subject": t.subject_sub, "subject_kind": t.subject_kind,
                       "actor": t.actor, "audience": t.audience, "scopes": t.scopes, "ttl_seconds": t.ttl_seconds,
                       "method": t.method, "decision": t.decision, "reason": t.reason, "trace_id": t.trace_id}
                      for t in db.scalars(stmt)]}


# ───────── 워크로드 아이덴티티 (SCR-J07) ─────────
class WorkloadIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    kind: str = Field("agent", pattern="^(agent|mcp_server|service)$")
    scopes: list[str] = []


def _wl_out(w: Workload) -> dict:
    return {"id": w.id, "name": w.name, "kind": w.kind, "client_id": w.client_id, "spiffe_id": w.spiffe_id,
            "scopes": w.scopes, "enabled": w.enabled, "created_at": w.created_at, "rotated_at": w.rotated_at}


```
**분석 / Analysis (Level 2)**
- L372-386 `list_exchanges`: 교환 이력 필터(trace·주체·대상). `jti` 는 응답에 없다(토큰 자체는 저장하지 않음).
- L390-393 `WorkloadIn`: 이름 패턴(소문자·숫자·하이픈, 2~63자) — `client_id = wl-{name}`·SPIFFE 경로에 안전한 문자만.
- L396-398 `_wl_out`: 시크릿 해시 제외.
**짚어볼 점 / Watch-outs**
- L375 `limit` 하한 없음(L129 와 같은 [위험:낮음] 패턴 — PostgreSQL 500).
- L393 `scopes` 자유 입력 — 워크로드는 역할 `user` 로 인증되므로(`app/core/auth.py:238`) 스코프에 관리 권한을 넣어도 교집합으로 막힌다(권한 상승 없음).

```python path=app/api/identity.py lines=401-440
@router.get("/api/v1/identity/workloads")
def list_workloads(_: Principal = Depends(require("identity:read")), db: Session = Depends(get_db)):
    return {"items": [_wl_out(w) for w in db.scalars(select(Workload).order_by(Workload.name))]}


@router.post("/api/v1/identity/workloads", status_code=201)
def create_workload(body: WorkloadIn, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    if db.scalars(select(Workload).where(Workload.name == body.name)).first():
        raise conflict("이미 존재하는 워크로드입니다.")
    wl, secret = identity.create_workload(db, body.name, body.kind, body.scopes)
    db.commit()
    audit.append("identity.workload.create", actor=p.sub, target=wl.client_id, data={"kind": wl.kind})
    return {**_wl_out(wl), "client_secret": secret}


@router.post("/api/v1/identity/workloads/{wl_id}/rotate")
def rotate_workload(wl_id: str, p: Principal = Depends(require("identity:write")), db: Session = Depends(get_db)):
    wl = db.get(Workload, wl_id)
    if wl is None:
        raise not_found("workload")
    secret = identity.rotate_workload(db, wl)
    db.commit()
    audit.append("identity.workload.rotate", actor=p.sub, target=wl.client_id)
    return {**_wl_out(wl), "client_secret": secret}


@router.patch("/api/v1/identity/workloads/{wl_id}")
def patch_workload(wl_id: str, body: dict, p: Principal = Depends(require("identity:write")),
                   db: Session = Depends(get_db)):
    wl = db.get(Workload, wl_id)
    if wl is None:
        raise not_found("workload")
    for k in ("enabled", "scopes"):
        if k in body:
            setattr(wl, k, body[k])
    db.commit()
    audit.append("identity.workload.update", actor=p.sub, target=wl.client_id, data=body)
    return _wl_out(wl)


```
**분석 / Analysis (Level 2)**
- L406-413 `create_workload`: 이름 중복 409 → 생성 → 커밋 → 평문 시크릿 1회 반환(감사에는 없음).
- L416-424 `rotate_workload`: 새 시크릿 즉시 적용(이전 시크릿은 바로 무효). 이미 발급된 워크로드 토큰은 만료까지 유효(services/identity 보고서).
- L427-438 `patch_workload`: `enabled`·`scopes` 만 반영. 비활성화는 다음 요청부터 그 워크로드 토큰을 거부(`app/core/auth.py:236-237`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L428 `body: dict` — 값 타입을 검증하지 않는다. `{"enabled": "false"}`(문자열)는 진리값 참이라 **끄려던 워크로드가 켜진 채** 남고, `{"scopes": "gateway:invoke"}`(문자열)는 `set(wl.scopes)` 가 문자 집합이 되어(`app/services/identity.py:130`) 의도와 다른 스코프가 된다. 감사(L437)는 요청 본문을 그대로 남겨 "비활성화함" 으로 읽힐 수 있다. 최소 수정: `WorkloadPatch(BaseModel)`(`enabled: bool | None`, `scopes: list[str] | None`).
- L406-413 워크로드 생성 권한(`identity:write`)이 곧 **token-exchange grant 사용 권한**이 된다(`app/api/idp.py:318-325` 는 워크로드 종류·스코프를 보지 않음) — 서비스 계정 하나를 만들면 교환 클라이언트도 생긴다(services/identity 보고서 잔여 [위험:낮음]).

```python path=app/api/identity.py lines=441-466
# ───────── Access Tokens 발급·폐기 (FR-L03, SCR-I05) ─────────
class AccessTokenIn(BaseModel):
    name: str
    subject: str = Field(description="토큰 사용 주체 (예: a2a:observability-analyst, mcp:internal-api)")
    audience: str = security.API_AUDIENCE
    scopes: list[str] = ["gateway:invoke"]
    ttl_seconds: int = Field(3600, ge=300, le=3600)  # SCR-I05 — 5~60분


def _at_out(t: AccessToken) -> dict:
    now = utcnow()
    state = "revoked" if t.revoked_at else ("expired" if t.expires_at < now else "active")
    return {"id": t.id, "name": t.name, "subject": t.subject, "audience": t.audience, "scopes": t.scopes,
            "issued_by": t.issued_by, "expires_at": t.expires_at, "revoked_at": t.revoked_at,
            "last_used_at": t.last_used_at, "created_at": t.created_at, "state": state}


@router.get("/api/v1/access-tokens")
def list_access_tokens(_: Principal = Depends(require("tokens:issue")), db: Session = Depends(get_db),
                       subject: str | None = None):
    stmt = select(AccessToken).order_by(AccessToken.created_at.desc())
    if subject:
        stmt = stmt.where(AccessToken.subject == subject)
    return {"items": [_at_out(t) for t in db.scalars(stmt)]}


```
**분석 / Analysis (Level 2)**
- L442-447 `AccessTokenIn`: 서비스 주체(원격 A2A 에이전트·MCP 서버)가 **플랫폼을** 호출할 때 쓰는 단수명 토큰(`app/models.py:117-118` docstring "원격 A2A 에이전트·MCP 서버용"). TTL 5~60분.
- L450-455 `_at_out`: 상태 계산(폐기·만료·활성).
**짚어볼 점 / Watch-outs**
- [위험:중간] L445 `audience: str` 에 제약이 없다. `tokens:issue`(operator·security) 보유자가 `{"audience":"mcp:jira","scopes":["issues:write"],"ttl_seconds":3600}` 으로 업스트림용 토큰을 직접 만들 수 있다 — 서명은 플랫폼 키, `aud` 는 업스트림. 내장 MCP 서버 검증은 서명·`aud` 만 확인하므로(`app/builtin/mcp_auth.py:30-38`) 이 토큰을 받아들인다. 결과: 토큰 교환 정책(허용 스코프·최대 15분 — L337-339)과 게이트웨이의 도구 규칙·사람 승인·사용량 기록을 모두 거치지 않고 업스트림을 직접 호출(네트워크로 업스트림에 닿을 때). 1차 [중간](token-exchange 로 게이트웨이 우회)을 Epic 40 이 닫은 뒤 남은 같은 종류의 문. 최소 수정: `audience` 를 `API_AUDIENCE` 로 고정(Field 제거 또는 `pattern="^agentnexus$"`), 업스트림 토큰은 교환으로만. 시험 공백: `audience` 가 업스트림인 Access Token 거부 시험 없음.
- L458-464 페이지네이션 없음 — Access Token 이 많아지면 전체 반환(낮음).

```python path=app/api/identity.py lines=467-490
@router.post("/api/v1/access-tokens", status_code=201)
def issue_access_token(body: AccessTokenIn, p: Principal = Depends(require("tokens:issue")),
                       db: Session = Depends(get_db)):
    if body.subject != p.sub and db.scalars(select(User.id).where(User.sub == body.subject)).first():
        # 서비스 주체용 — 다른 사람의 sub 로 발급하면 그 사람의 세션·Task 를 열람하는 대리 토큰이 된다
        raise bad_request("다른 사용자의 sub 로는 Access Token 을 발급할 수 없습니다 — 서비스 주체(예: a2a:…, mcp:…)를 쓰세요.",
                          code="subject_is_user")
    row, token = identity.issue_access_token(db, body.name, body.subject, body.audience, body.scopes,
                                             body.ttl_seconds, issued_by=p.sub)
    db.commit()
    audit.append("token.access.issue", actor=p.sub, target=row.subject,
                 data={"jti": row.id, "aud": row.audience, "scopes": row.scopes, "ttl": body.ttl_seconds})
    return {**_at_out(row), "token": token}


@router.post("/api/v1/access-tokens/{token_id}/revoke")
def revoke_access_token(token_id: str, p: Principal = Depends(require("tokens:issue")), db: Session = Depends(get_db)):
    row = db.get(AccessToken, token_id)
    if row is None:
        raise not_found("access token")
    row.revoked_at = row.revoked_at or utcnow()
    db.commit()
    audit.append("token.access.revoke", actor=p.sub, target=row.subject, data={"jti": row.id})
    return _at_out(row)
```
**분석 / Analysis (Level 2)**
- L470-473: SEC-07 대응 — 자기 자신이 아닌 **실재 사용자** sub 로는 발급 거부(400 `subject_is_user`). 서비스 주체(`a2a:…`·`mcp:…`)와 자기 sub 는 허용.
- L474-479: 발급·커밋·감사(jti·aud·스코프·TTL) — 토큰 원문은 응답에만.
- L482-490 `revoke_access_token`: 멱등(최초 폐기 시각 보존 — `row.revoked_at or utcnow()`), 즉시 반영(`app/core/auth.py:241-243`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L470 검사는 "지금 존재하는 사용자" 기준이다. 외부 IdP 사용자는 첫 로그인 때 JIT 로 생기므로(`app/core/auth.py:172-174`) 아직 로그인하지 않은 사람의 sub(`ext:{idp_id}:{외부 sub}` — 형식 예측 가능)로는 발급된다. 그 사람이 나중에 로그인하면 이 토큰의 `p.sub` 가 그 사용자와 같아져 `user_sub`/`principal_sub` 로 소유를 판단하는 자원(세션·Task)을 열람할 수 있다(가능성 — 소유 판단 위치별 확인 필요). 최소 수정: 사용자 sub 형식(`usr_`·`ext:` 접두)이면 존재 여부와 무관하게 거부, 서비스 주체는 `a2a:`·`mcp:`·`svc:` 접두만 허용.
- L483-490 `tokens:issue` 보유자는 남이 발급한 토큰도 폐기할 수 있다 — 관리 기능으로 의도된 것으로 보임(`issued_by` 확인 없음, 감사로 추적).

## 파일 평가 / File Assessment
- 강점: (1) 관리 엔드포인트가 모두 `require(...)` 로 RBAC 를 거치고, 자기 토큰 조회·폐기는 소유자 확인 + 남의 것은 404(Epic 48 원칙). (2) 시크릿·해시는 응답에서 제외·마스킹, 감사에 평문 없음, IdP 로그인 노출은 시험 로그인 성공 후, IdP 중지 시 세션 일괄 종료. (3) SEC-07(다른 사용자 sub 대리 발급) 차단, 대부분의 입력이 pydantic 모델로 형식 검증.
- 위험: (1) [위험:높음] 토큰으로 토큰 발급 — 스코프 PAT·클라이언트 키·위임 토큰이 무제한·무기한 PAT 를 만들어 소유자 역할 전체로 승격(`app/api/identity.py:55-75`, `:35`). (2) [위험:중간] `identity:write`(security)의 admin 부여·IdP 역할 매핑으로 권한 상승(`:138-163`, `:202-203`), 클라이언트 키 팀 자유 지정(`:36`·`:70-71`). (3) [위험:중간] Access Token `audience` 무제약 → 업스트림 토큰 직접 발급(`:445`), IdP 삭제 시 세션 미종료(`:253-261`), `ttl_days: null` 무기한 토큰(`:35`).
- 개선 권고(영향순): (1) `/api/v1/me/tokens` POST·DELETE 를 대화형 사용자 세션(`kind == "user"`)으로 제한 + `ttl_days` None 금지. (2) 역할 부여 상한(호출자 권한의 부분집합) + 클라이언트 키 팀은 소속 팀만 + Access Token audience 고정. (3) IdP 삭제 전 세션 종료, `patch_idp`·`patch_workload` 를 pydantic 모델로, `limit` 하한·역할 필터 순서, 사용자 생성 비밀번호 정책 통일, 와일드카드 audience 정리.
- 파일 위험 등급 / Risk grade: 높음 — 미해결 [위험:높음] 1건(토큰 승격) + [위험:중간] 5건, 모두 신원·인가 경계.
- 재읽기 권고 레벨: 2 — 엔드포인트마다 "누가(주체 종류·스코프) 무엇을(어떤 권한·어떤 객체) 바꿀 수 있나" 를 `app/core/auth.py` 의 `has()`·`scope_perms()` 규칙과 대조하며 읽을 것.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 1차 위임 보안 리뷰 SEC-07 [MEDIUM] Access Token `subject` 임의 지정 — 다른 사용자 데이터 열람(IDOR) | 해결(잔여 [위험:낮음] — 아직 JIT 되지 않은 외부 사용자 sub) | `app/api/identity.py:470-473`, `test/test_review_fixes.py:139-150` |
| SEC-08 [MEDIUM] LIKE 와일드카드 미이스케이프(`identity.py:133` 포함) | 미해결(의도적 — 39th 판정 "노출 없음") | `app/api/identity.py:132-133`, `test/impl/39th/scenario.md:54` |
| SEC-09 [MEDIUM] 외부 IdP JWKS/discovery SSRF | 부분 해결 — egress 가드(IMDS·링크 로컬), 사설망·오류 문구 반사는 남음 | `app/api/identity.py:280-296`, `:304-307`, `app/core/http.py:35-38` |
| 1차 `app/services/identity.py.md` L204 — Access Token 대리 발급 검사가 서비스에 없음(API 계층에 `subject_is_user` 추가) | 해결(API 계층) — 단 같은 엔드포인트의 `audience` 무제약이 새 [위험:중간] | `app/api/identity.py:470-473`, `:445` |
| 1차 `app/core/auth.py.md` L128 — 클라이언트 키 스코프 미적용 | 미해결 — 발급 API 가 클라이언트 키 `scopes` 를 그대로 받는다(적용 안 됨) | `app/api/identity.py:34`, `:70`, `app/core/auth.py:40` |
| 1차 INDEX §4.3 핫스팟 #4 "identity — 교환에 주체 권한 검사 없음 + 공개 token-exchange grant" | 부분 해결(이 파일 관련분) — 워크로드 생성이 곧 교환 클라이언트 권한(워크로드별 교환 허용 없음) | `app/api/identity.py:406-413`, `app/api/idp.py:318-325` |
