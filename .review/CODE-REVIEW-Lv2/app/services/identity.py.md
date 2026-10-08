> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 27. app/services/identity.py
**파일 요약 / File Summary**
- 역할 / Role: 토큰 발급 서비스 — 사용자 토큰(access·id·refresh), refresh 회전, 워크로드 생성·회전·client_credentials 토큰, Access Token, PAT·클라이언트 키, 위임 토큰, RFC 8693 토큰 교환(정책 기반 최소 스코프), 세션·IP 정책.
- 진입점 거리 / Distance: 2 (`app/services/mcp_gateway.py:147`, `app/services/a2a_gateway.py:264`, `app/api/idp.py`)
- LOC: 196
- 주요 export: `user_claims`, `auth_policy`, `ip_allowed`, `issue_user_tokens`, `refresh`, `create_workload`, `rotate_workload`, `issue_workload_token`, `issue_access_token`, `create_personal_token`, `delegation_token`, `exchange`, `get_user`, `PUBLIC_CLIENTS`, 토큰 타입·grant 상수
- 의존(내부): `app.core.{security,auth,config,context,errors,ids}`, `app.db`, `app.models`, `app.services.audit`
- 외부 패키지: sqlalchemy
- 부수효과 / Side effects: DB 쓰기(refresh·교환 이력·사용자 로그인 시각), 감사, JWT 서명
- 동시성 / Concurrency: 동기(스레드풀), 행 잠금 없음
- 테스트 커버리지 흔적: `test/test_identity.py`, `test/test_mcp.py:53-72`(교환 토큰 aud·스코프)

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/identity.py lines=1-28
"""Identity 서비스 — 토큰 발급(사용자·워크로드·Access Token·위임), RFC 8693 교환, PAT, 워크로드 신원."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.auth import Principal
from app.core.config import settings
from app.core.context import current_trace_id
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.core.ids import new_id
from app.db import utcnow
from app.models import AccessToken, PersonalToken, RefreshToken, TokenExchange, TokenExchangePolicy, User, Workload
from app.services import audit

TOKEN_TYPE_ACCESS = "urn:ietf:params:oauth:token-type:access_token"
TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"
GRANT_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"

PUBLIC_CLIENTS = {"agentnexus-console", "nexus-cli"}


def user_claims(user: User) -> dict:
    return {"sub": user.sub, "anx_kind": "user", "name": user.name or user.username, "email": user.email,
            "preferred_username": user.username, "roles": list(user.roles or []), "groups": list(user.groups or []),
            "team": user.team}
```
**분석 / Analysis (Level 2)**
- L17-20: RFC 8693 토큰 타입·grant, RFC 8628 디바이스 grant URN.
- L22 `PUBLIC_CLIENTS`: 시크릿 없는 공개 클라이언트(콘솔 SPA·CLI) — PKCE·디바이스 흐름 대상.
- L25-28 `user_claims`: 액세스 토큰 사용자 클레임(역할·그룹·팀 포함 — 게이트웨이가 토큰만으로 판단할 수 있게).
**짚어볼 점 / Watch-outs**
- 역할을 토큰에 싣지만 인증 시에는 DB 의 현재 역할을 다시 읽는다(`app/core/auth.py:224-233`) — 역할 변경이 즉시 반영된다(좋음). 토큰의 역할 클레임은 정보용.

```python path=app/services/identity.py lines=31-49
def auth_policy(db: Session) -> dict:
    """세션·토큰·IP 정책 (SCR-L06 ⑤⑥) — 액세스 토큰 수명(분)·세션(갱신 토큰) 수명(시간)·로그인 허용 IP(CIDR)."""
    from app.models import Setting
    get = lambda k, d: (db.get(Setting, k).value if db.get(Setting, k) is not None else d)  # noqa: E731
    return {"access_token_ttl_min": int(get("auth.access_token_ttl_min", settings.access_token_ttl // 60)),
            "session_ttl_hours": int(get("auth.session_ttl_hours", 14 * 24)),
            "ip_allowlist": list(get("auth.ip_allowlist", []) or [])}


def ip_allowed(db: Session, ip: str | None) -> bool:
    import ipaddress
    nets = auth_policy(db)["ip_allowlist"]
    if not nets:
        return True
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(n, strict=False) for n in nets)
```
**분석 / Analysis (Level 2)**
- L31-37 `auth_policy`: 설정 테이블의 액세스 토큰 수명(분)·세션(refresh) 수명(시간, 기본 14일)·로그인 허용 IP CIDR.
- L40-49 `ip_allowed`: 허용 목록이 비면 모두 허용, 아니면 CIDR 포함 여부(`strict=False` 로 호스트 비트가 있는 표기도 허용).
**짚어볼 점 / Watch-outs**
- L34 람다 안에서 `db.get(Setting, k)` 를 두 번 호출 — identity map 덕분에 두 번째는 캐시지만 가독성이 낮다.
- L46 클라이언트 IP 는 호출자가 넘긴다 — 프록시 뒤에서는 `X-Forwarded-For` 신뢰 설정에 따라 실제 IP 가 아닐 수 있다(호출부 확인 필요).
- L49 `ipaddress.ip_network(n)` — 설정에 잘못된 CIDR 이 있으면 `ValueError` 로 로그인 전체가 실패한다(설정 저장 시 검증 필요).

```python path=app/services/identity.py lines=52-84
def issue_user_tokens(db: Session, user: User, client_id: str, scope: str = "openid profile",
                      nonce: str | None = None, with_refresh: bool = True) -> dict:
    pol = auth_policy(db)
    ttl = min(settings.access_token_ttl, pol["access_token_ttl_min"] * 60)
    rt_id = new_id("rt") if with_refresh else None
    access, _ = security.sign({**user_claims(user), "aud": security.API_AUDIENCE, "azp": client_id, "scope": scope,
                               **({"sid": rt_id} if rt_id else {})}, ttl)  # sid = 세션(refresh token) — 현재 세션 식별 (SCR-B06)
    out = {"access_token": access, "token_type": "Bearer", "expires_in": ttl, "scope": scope}
    if "openid" in scope.split():
        id_claims = {"sub": user.sub, "aud": client_id, "name": user.name or user.username, "email": user.email,
                     "preferred_username": user.username}
        if nonce:
            id_claims["nonce"] = nonce
        out["id_token"], _ = security.sign(id_claims, ttl, typ="JWT")
    if with_refresh:
        raw = security.opaque("anx_rt_")
        db.add(RefreshToken(id=rt_id, token_hash=security.token_hash(raw), user_id=user.id, client_id=client_id,
                            scope=scope, expires_at=utcnow() + timedelta(hours=pol["session_ttl_hours"])))
        out["refresh_token"] = raw
    user.last_login_at = utcnow()
    db.commit()
    return out


def refresh(db: Session, raw: str, client_id: str) -> dict:
    row = db.scalars(select(RefreshToken).where(RefreshToken.token_hash == security.token_hash(raw))).first()
    if row is None or row.revoked_at or row.expires_at < utcnow() or row.client_id != client_id:
        raise ApiError(400, "invalid_grant", "refresh token 이 유효하지 않습니다.")
    row.revoked_at = utcnow()  # 회전
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise ApiError(400, "invalid_grant", "사용자가 비활성 상태입니다.")
    return issue_user_tokens(db, user, client_id, row.scope)
```
**분석 / Analysis (Level 2)**
- L52-73 `issue_user_tokens`: TTL = min(설정, 정책), refresh id 를 `sid`(세션 id) 클레임으로 — 현재 세션 식별. openid 스코프면 id_token(nonce 포함). refresh 는 불투명 토큰의 **해시만 저장**. 로그인 시각 갱신 후 커밋.
- L76-84 `refresh`: 해시로 조회 → 폐기·만료·클라이언트 일치 확인 → **회전**(이전 토큰 폐기) → 사용자 활성 확인 → 새 토큰 세트.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 경쟁] L77-84 조회-확인-폐기가 원자적이지 않다 — 같은 refresh token 으로 동시에 두 요청이 오면 둘 다 `revoked_at is None` 을 보고 각자 새 토큰 세트를 받는다(회전 무력화). 또 **재사용 탐지가 없다** — 이미 폐기된 refresh token 이 다시 제시되면(도난 신호) 같은 계열 전체를 폐기해야 하지만(OAuth 2.0 Security BCP), 단순 400 만 반환한다.
- L80-84 사용자 비활성 확인 **전에** 이전 토큰을 폐기 표시하지만 예외로 커밋되지 않으면 롤백된다 — 비활성 사용자의 refresh 는 그대로 남는다(무해).
- L84 새 세트의 refresh 수명은 이전과 무관하게 다시 14일 — "슬라이딩 세션" 으로 무기한 연장된다(절대 세션 상한 없음).

```python path=app/services/identity.py lines=87-115
# ── 워크로드 (FR-I04) ──
def create_workload(db: Session, name: str, kind: str, scopes: list[str]) -> tuple[Workload, str]:
    secret = security.opaque("anx_ws_")
    wl = Workload(id=new_id("wl"), name=name, kind=kind, client_id=f"wl-{name}", secret_hash=security.hash_password(secret),
                  scopes=scopes, spiffe_id=f"spiffe://agentnexus/{kind}/{name}")
    db.add(wl)
    db.flush()
    return wl, secret


def rotate_workload(db: Session, wl: Workload) -> str:
    secret = security.opaque("anx_ws_")
    wl.secret_hash = security.hash_password(secret)
    wl.rotated_at = utcnow()
    db.flush()
    return secret


def issue_workload_token(db: Session, client_id: str, client_secret: str, scope: str | None) -> dict:
    wl = db.scalars(select(Workload).where(Workload.client_id == client_id)).first()
    if wl is None or not wl.enabled or not security.check_password(client_secret, wl.secret_hash):
        raise ApiError(401, "invalid_client", "클라이언트 인증 실패")
    allowed = set(wl.scopes or [])
    requested = set(scope.split()) if scope else allowed
    granted = sorted(requested & allowed)
    ttl = settings.access_token_ttl
    token, _ = security.sign({"sub": f"workload:{wl.name}", "anx_kind": "workload", "client_id": wl.client_id,
                              "aud": security.API_AUDIENCE, "scope": " ".join(granted)}, ttl)
    return {"access_token": token, "token_type": "Bearer", "expires_in": ttl, "scope": " ".join(granted)}
```
**분석 / Analysis (Level 2)**
- L88-94 `create_workload`: `anx_ws_` 불투명 시크릿 생성 → scrypt 해시 저장, client_id `wl-{name}`, SPIFFE 표기. 평문 시크릿은 반환값으로 한 번만.
- L97-102 `rotate_workload`: 새 시크릿·해시·회전 시각.
- L105-115 `issue_workload_token`: client_id 조회 → 활성·시크릿 검증 → 요청 스코프 ∩ 허용 스코프 → 워크로드 토큰.
**짚어볼 점 / Watch-outs**
- L107 client_id 가 없으면 scrypt 계산을 하지 않는다 — 응답 시간 차이로 유효한 client_id 를 추정할 수 있다(타이밍 기반 열거, 영향 낮음).
- L109-111 요청 스코프 중 허용되지 않은 것은 조용히 제외(OAuth 관례) — 빈 스코프 토큰도 발급된다(이후 기본 거부되므로 안전).
- 워크로드 토큰은 jti 를 DB 에 기록하지 않는다 — 개별 토큰 폐기 불가(워크로드 비활성화로만).

```python path=app/services/identity.py lines=118-148
# ── Access Tokens (FR-L03) ──
def issue_access_token(db: Session, name: str, subject: str, audience: str, scopes: list[str], ttl: int,
                       issued_by: str) -> tuple[AccessToken, str]:
    if ttl <= 0 or ttl > 86400:
        raise bad_request("ttl 은 1초 ~ 24시간 범위여야 합니다.")
    token, claims = security.sign({"sub": subject, "anx_kind": "access_token", "aud": audience,
                                   "scope": " ".join(scopes)}, ttl)
    row = AccessToken(id=claims["jti"], name=name, subject=subject, audience=audience, scopes=scopes,
                      issued_by=issued_by, expires_at=utcnow() + timedelta(seconds=ttl))
    db.add(row)
    db.flush()
    return row, token


# ── PAT·클라이언트 키 ──
def create_personal_token(db: Session, user: User, name: str, kind: str = "pat", scopes: list[str] | None = None,
                          ttl_days: int | None = 90, team: str | None = None) -> tuple[PersonalToken, str]:
    raw = security.opaque("anx_pat_" if kind == "pat" else "anx_key_")
    row = PersonalToken(id=new_id("pat"), user_id=user.id, kind=kind, name=name, token_hash=security.token_hash(raw),
                        prefix=raw[:12], scopes=scopes or [], team=team,
                        expires_at=utcnow() + timedelta(days=ttl_days) if ttl_days else None)
    db.add(row)
    db.flush()
    return row, raw


# ── 위임 토큰 (Host 가 승인 재개 등 사용자 부재 시 사용자 신원으로 게이트웨이 호출) ──
def delegation_token(snapshot: dict, actor: str, ttl: int = 600) -> str:
    token, _ = security.sign({"sub": snapshot["sub"], "anx_kind": "delegation", "aud": security.API_AUDIENCE,
                              "act": {"sub": actor}, "name": snapshot.get("name"), "team": snapshot.get("team")}, ttl)
    return token
```
**분석 / Analysis (Level 2)**
- L119-129 `issue_access_token`: TTL 1초~24시간, 임의 audience·subject 로 서명, DB 행(jti)으로 폐기 가능.
- L133-141 `create_personal_token`: PAT(`anx_pat_`)·클라이언트 키(`anx_key_`) — 해시·접두 저장, 기본 90일.
- L145-148 `delegation_token`: 사용자 부재 시(승인 후 재개) 스냅샷 신원 + `act.sub` 로 10분 토큰.
**짚어볼 점 / Watch-outs**
- L119-124 `subject` 는 호출자가 지정 — 다른 사용자의 sub 로 발급하는 대리 발급을 막는 검사는 이 함수에 없다(동시 작업에서 API 계층에 `subject_is_user` 검사가 추가됨 — `test/test_review_fixes.py` `test_access_token_subject_not_other_user`, API 파일은 본 리뷰 미선정).
- L146 위임 토큰은 `anx_kind=delegation` 으로 사용자 토큰처럼 인증된다(`app/core/auth.py:225`) — 10분 동안 사용자 권한 전체를 가진다(스코프 제한 없음). 재개에 필요한 최소 스코프로 좁히는 것이 바람직하다.

```python path=app/services/identity.py lines=151-196
# ── RFC 8693 토큰 교환 (FR-I02) ──
def exchange(db: Session, subject: Principal, audience: str, scopes: list[str] | None, actor: str = "gateway",
             method_hint: str | None = None) -> dict:
    """사용자(또는 워크로드) 토큰 → 대상(audience)별 최소 스코프 토큰."""
    trace_id = current_trace_id()
    pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == audience)).first()

    def _deny(reason: str) -> ApiError:
        db.add(TokenExchange(id=new_id("tx"), subject_sub=subject.sub, subject_kind=subject.kind, actor=actor,
                             audience=audience, scopes=scopes or [], ttl_seconds=0, method=method_hint or "rfc8693",
                             decision="denied", reason=reason, trace_id=trace_id))
        db.commit()
        audit.append("token.exchange", actor=subject.sub, actor_kind=subject.kind, target=audience, decision="deny",
                     data={"reason": reason, "scopes": scopes or [], "actor": actor})
        return forbidden(f"{audience}용 토큰을 발급하지 못했습니다(스코프 {' '.join(scopes or []) or '-'}): {reason}",
                         code="token_exchange_failed")

    if pol is None or not pol.enabled:
        raise _deny("교환 정책 없음")
    requested = list(scopes) if scopes else list(pol.default_scopes or pol.allowed_scopes or [])
    granted = [s for s in requested if s in (pol.allowed_scopes or [])]
    denied = [s for s in requested if s not in (pol.allowed_scopes or [])]
    if scopes and denied:
        raise _deny(f"허용되지 않은 스코프: {' '.join(denied)}")
    if not granted:
        raise _deny("부여 가능한 스코프 없음")
    ttl = pol.ttl_seconds
    token, claims = security.sign({"sub": subject.sub, "anx_kind": "exchanged", "aud": audience,
                                   "scope": " ".join(granted), "act": {"sub": actor}, "name": subject.name,
                                   "email": subject.email, "team": subject.team, "roles": subject.roles,
                                   "anx_trace": trace_id}, ttl)
    db.add(TokenExchange(id=new_id("tx"), subject_sub=subject.sub, subject_kind=subject.kind, actor=actor,
                         audience=audience, scopes=granted, ttl_seconds=ttl, method=pol.method, decision="issued",
                         trace_id=trace_id, jti=claims["jti"]))
    db.commit()
    audit.append("token.exchange", actor=subject.sub, actor_kind=subject.kind, target=audience, decision="issued",
                 data={"scopes": granted, "ttl": ttl, "method": pol.method, "actor": actor, "jti": claims["jti"]})
    return {"access_token": token, "issued_token_type": TOKEN_TYPE_ACCESS, "token_type": "Bearer",
            "expires_in": ttl, "scope": " ".join(granted)}


def get_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise not_found("user")
    return user
```
**분석 / Analysis (Level 2)**
- L152-166: 교환 정책 조회, 거부 시 이력·감사를 남기는 내부 함수(`_deny` 는 커밋 후 예외 객체를 **반환**하고 호출부가 `raise`).
- L168-176: 정책 없음·비활성 거부, 요청 스코프(없으면 기본) ∩ 허용, 명시 요청에 허용 밖 스코프가 있으면 거부, 부여할 것이 없으면 거부.
- L177-189: 교환 토큰 서명(`anx_kind=exchanged`, aud=대상, act=행위자, 사용자 표시 정보·역할·trace) → 이력·감사 → RFC 8693 응답.
- L192-196 `get_user`.
**짚어볼 점 / Watch-outs**
- [위험:중간] `exchange` 는 **주체가 그 audience 를 써도 되는지**(역할·팀·도구 규칙)를 확인하지 않는다 — 정책의 허용 스코프 안이면 누구에게나 발급한다. 게이트웨이 경로에서는 이전 단계(`governance.check_tool`)가 막지만, `/idp/token` 의 token-exchange grant(`app/api/idp.py:313-317`(14:21 재확인 — 동시 수정 중인 파일))는 클라이언트 인증 없이 임의 subject_token 으로 이 함수를 직접 호출한다. 따라서 일반 사용자가 `mcp:jira` audience 의 `issues:write` 토큰을 직접 받아 **업스트림에 직접 접근할 수 있으면**(네트워크 격리가 없는 배포) 게이트웨이의 승인·정책을 우회한다. 교환 엔드포인트는 게이트웨이(내부 클라이언트) 전용으로 제한하거나 주체 권한 검사를 추가해야 한다.
- L159-166 거부 이력을 커밋하는 시점에 호출자 세션의 다른 미커밋 변경도 함께 커밋된다(같은 `db`).
- L178-181 교환 토큰에 이메일·역할·팀을 싣는다 — 업스트림(외부일 수 있음)에 사용자 PII 를 전달한다(최소 클레임 원칙 검토).

## 파일 평가 / File Assessment
- 강점: (1) 모든 불투명 토큰·시크릿을 해시로 저장. (2) 정책 기반 최소 스코프 교환·거부 이력·감사. (3) refresh 회전·sid 로 세션 식별.
- 위험: (1) [중간] 교환 함수에 주체 권한 검사 없음 + 공개 token-exchange grant. (2) [중간] refresh 회전 비원자적·재사용 탐지 없음·절대 세션 상한 없음. (3) [낮음] 위임 토큰의 넓은 권한, 교환 토큰의 PII.
- 개선 권고(영향순): (1) token-exchange grant 에 클라이언트 인증·허용 클라이언트 목록. (2) refresh 조건부 폐기 + 계열(family) 재사용 탐지. (3) 위임 토큰 스코프 축소·교환 토큰 클레임 최소화.
- 재읽기 권고 레벨: 2.
