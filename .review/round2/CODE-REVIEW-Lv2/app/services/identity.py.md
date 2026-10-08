> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [identity.py.md](../../../../CODE-REVIEW-Lv2/app/services/identity.py.md)

### 17. app/services/identity.py
**파일 요약 / File Summary**
- 역할 / Role: 토큰 발급 서비스 — 사용자 토큰(access·id·refresh, 계열 `family_id`), refresh 회전(조건부 UPDATE)·재사용 탐지(계열 폐기), 워크로드 생성·회전·인증(`authenticate_workload`)·client_credentials, Access Token, PAT·클라이언트 키, 위임 토큰, RFC 8693 토큰 교환(정책 기반 최소 스코프), 세션·IP 정책 조회.
- 진입점 거리 / Distance: 2 (`app/api/idp.py:310·313·315·321·325·332·376`, `app/api/identity.py:69-70·155·410·421·474`, `app/services/mcp_gateway.py:148·653`·`app/services/a2a_gateway.py:266`(게이트웨이 내부 교환), `app/api/catalog.py:355`(즉시 사용 시험 교환), `app/services/orchestrator.py:63`(위임 토큰), `app/seed.py:190-197`)
- LOC: 217 (현재 `wc -l`) · 1차 196 → +21
- 주요 export: `user_claims`, `auth_policy`, `ip_allowed`, `issue_user_tokens`, `REUSE_GRACE_S`, `refresh`, `create_workload`, `rotate_workload`, `authenticate_workload`, `issue_workload_token`, `issue_access_token`, `create_personal_token`, `delegation_token`, `exchange`, `get_user`, `PUBLIC_CLIENTS`, 토큰 타입·grant URN 상수
- 의존(내부): `app.core.{security,auth,config,context,errors,ids}`, `app.db`, `app.models`, `app.services.audit`
- 외부 패키지: sqlalchemy(`select`, `update`)
- 부수효과 / Side effects: DB 쓰기(refresh 행·조건부 폐기·계열 일괄 폐기·교환 이력·로그인 시각), 감사(`auth.refresh_reuse`·`token.exchange` — 별도 트랜잭션 `app/services/audit.py:52-69`), JWT 서명
- 동시성 / Concurrency: 동기(스레드풀 또는 호출부가 async 면 이벤트 루프). refresh 회전은 이제 **조건부 UPDATE + rowcount** 로 원자적(L94-96). 계열 일괄 폐기도 단일 UPDATE 문(L85-86).
- 테스트 커버리지 흔적: `test/test_policy_hardening.py:92-115`(`test_refresh_reuse_revokes_family` — 유예 안 재제시 거부·계열 유지, 유예 밖 재제시 → 계열 전부 폐기 + 감사 + 최신 토큰도 무효), `test/test_review_fixes.py:401-418`(`test_refresh_rotation_single_winner` — 오래된 세션 상태로 경쟁 재현, 하나만 성공), `test/test_identity.py:45-82`(인가 코드 + refresh 회전 후 이전 토큰 재사용 거부), `test/test_caller_trust.py:20-34`(token-exchange 는 워크로드 인증 필수, `act=workload:…`), `test/test_identity.py:114-163`(워크로드·교환), `test/test_review_fixes.py:139-150`(Access Token subject). 다중 프로세스·PostgreSQL 실제 동시성은 prod 리허설(`test/impl/43rd/result.md:19`, 11초 뒤 재제시)뿐.
- 2차 변경 요약 / Changes since 1st review: 1차 인용 원문과 줄 단위 비교 — (1) `issue_user_tokens(..., family=None)` 와 `family_id=family or rt_id`(L53, L69) — 첫 토큰 id 가 계열 id. (2) `REUSE_GRACE_S = 10`(L77) + 재사용 탐지 분기(L82-90) — 유예 밖 재제시면 계열의 살아 있는 토큰 일괄 폐기·커밋·감사 후 400. (3) 회전을 `row.revoked_at = utcnow()` 에서 **조건부 UPDATE rowcount != 1 → 400** 으로(L93-96, Epic 39 "Lv2 경쟁" — `test/impl/39th/scenario.md:33`). (4) 새 토큰에 계열 승계 `family=row.family_id or row.id`(L100). (5) `authenticate_workload` 분리(L121-125, Epic 40 — `/idp/token` token-exchange grant 의 클라이언트 인증에 재사용, `test/impl/40th/scenario.md:7`). (6) import 에 `update`(L4). 그 밖은 1차와 동일.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/identity.py lines=1-30
"""Identity 서비스 — 토큰 발급(사용자·워크로드·Access Token·위임), RFC 8693 교환, PAT, 워크로드 신원."""
from datetime import timedelta

from sqlalchemy import select, update
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
- L4 `update`: SQLAlchemy 2.0 ORM-enabled UPDATE 문. `db.execute(update(...).where(...).values(...))` 는 한 SQL 문으로 실행되고 `rowcount` 로 실제 바뀐 행 수를 알려 준다 — "읽고 파이썬에서 바꾸고 flush" 하는 읽기-수정-쓰기(1차 지적)를 없애는 도구.
- L17-20: RFC 8693 토큰 타입·grant, RFC 8628 디바이스 grant URN.
- L22 `PUBLIC_CLIENTS`: 시크릿 없는 공개 클라이언트(콘솔 SPA·CLI) — PKCE 필수 대상(`app/api/idp.py:152`). 이 집합 밖의 `client_id` 는 등록 모델이 없어 **아무 문자열**이 될 수 있다(idp 보고서 SEC-12).
- L25-28 `user_claims`: 액세스 토큰의 사용자 클레임. 역할 클레임은 정보용 — 인증 때는 DB 현재 역할을 다시 읽는다(`app/core/auth.py:191-192`).

```python path=app/services/identity.py lines=31-51
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
- L31-37 `auth_policy`: 설정 테이블의 액세스 토큰 수명(분, 기본 `settings.access_token_ttl // 60` = 60분 — `app/core/config.py:38`)·세션 수명(시간, 기본 14일)·로그인 허용 CIDR.
- L40-49 `ip_allowed`: 목록이 비면 모두 허용, IP 파싱 실패면 거부(fail-closed), `strict=False` 로 호스트 비트가 있는 표기 허용.
**짚어볼 점 / Watch-outs**
- L34 람다 안 `db.get` 2회(identity map 덕분에 두 번째는 캐시, 1차 지적 — 가독성만).
- (1차 L49 후속) 잘못된 CIDR 이면 로그인 전체가 `ValueError` 로 실패한다는 1차 지적은 **저장 시 검증**으로 해결됐다(`app/api/admin.py:344-349` `ipaddress.ip_network(str(n), strict=False)` 실패 → 400).
- [위험:낮음] L40 호출자가 넘기는 IP 는 `request.client.host`(`app/api/idp.py:262`)이고, Helm 은 uvicorn 을 `--proxy-headers --forwarded-allow-ips "*"` 로 띄운다(`deploy/helm/agentnexus/templates/api.yaml:43`) — 모든 출처의 `X-Forwarded-For` 를 신뢰하므로, Ingress 를 거치지 않고 파드/서비스에 직접 닿을 수 있는 경로가 있으면 IP 허용 목록을 헤더 위조로 우회할 수 있다(가능성 — 네트워크 정책·Ingress 의 XFF 처리 확인 필요). 또 이 함수는 브라우저 로그인 한 곳에서만 호출된다(디바이스 승인·SSO·password grant 는 미적용 — idp 보고서 [위험:중간]).

```python path=app/services/identity.py lines=52-79
def issue_user_tokens(db: Session, user: User, client_id: str, scope: str = "openid profile",
                      nonce: str | None = None, with_refresh: bool = True, family: str | None = None) -> dict:
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
                            scope=scope, family_id=family or rt_id,
                            expires_at=utcnow() + timedelta(hours=pol["session_ttl_hours"])))
        out["refresh_token"] = raw
    user.last_login_at = utcnow()
    db.commit()
    return out


REUSE_GRACE_S = 10  # 같은 토큰으로 거의 동시에 온 갱신(탭 두 개 등)은 재사용으로 보지 않는다


```
**분석 / Analysis (Level 2)**
- L55 TTL = min(설정, 정책). L56-58 refresh id 를 `sid` 클레임으로 — 현재 세션 식별(`app/api/ops.py:128-132`). 회전할 때마다 새 `rt_id` 라 `sid` 도 바뀐다.
- L60-65 openid 스코프면 id_token(`aud=client_id`, nonce). `anx_kind` 가 없다(auth 보고서 L224 [위험:낮음] — `client_id=agentnexus` 이면 액세스 토큰처럼 통과).
- L66-71: refresh 원문은 응답에만, DB 에는 SHA-256 해시. **계열** `family_id = family or rt_id` — 로그인으로 처음 만들 때는 자기 id 가 계열 id, 회전 때는 부모의 계열을 물려받는다(L100).
- L73 `db.commit()`: 이 커밋이 refresh 경로에서는 **회전 UPDATE(L94-95)와 새 토큰 INSERT 를 한 트랜잭션으로** 확정한다 — 중간 실패 시 `get_db` 의 `close()`(`app/db.py:65-71`)가 롤백해 "이전 토큰은 폐기됐는데 새 토큰은 없음" 상태가 남지 않는다.
- L77 `REUSE_GRACE_S = 10`: 유예 창. 의미는 L82 참조.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L70 회전마다 `expires_at = 지금 + 14일` — 절대 세션 상한이 없어 계속 갱신하면 세션이 무기한(1차 미해결). 이번 차수에 계열 id 가 생겨 "계열 최초 발급 시각 + 상한" 으로 쉽게 막을 수 있게 됐다. 최소 수정: 회전 시 `min(부모.expires_at 의 계열 상한, now+ttl)` 또는 계열 루트 `created_at + 최대 세션`.
- L72 refresh 때도 `last_login_at` 을 갱신 — "마지막 로그인" 이 "마지막 토큰 갱신" 이 된다(관리 화면 의미 혼동, 낮음).

```python path=app/services/identity.py lines=80-102
def refresh(db: Session, raw: str, client_id: str) -> dict:
    row = db.scalars(select(RefreshToken).where(RefreshToken.token_hash == security.token_hash(raw))).first()
    if row is not None and row.revoked_at and row.revoked_at < utcnow() - timedelta(seconds=REUSE_GRACE_S):
        # 이미 회전·폐기된 토큰의 재제시 = 유출 신호 (OAuth 2.0 Security BCP) — 같은 계열의 살아 있는 토큰을 모두 폐기
        fam = row.family_id or row.id
        n = db.execute(update(RefreshToken).where((RefreshToken.family_id == fam) | (RefreshToken.id == fam),
                                                  RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow())).rowcount
        db.commit()
        audit.append("auth.refresh_reuse", actor=f"user:{row.user_id}", actor_kind="user", decision="deny",
                     data={"family": fam, "revoked": n, "client_id": client_id})
        raise ApiError(400, "invalid_grant", "refresh token 이 유효하지 않습니다.")
    if row is None or row.revoked_at or row.expires_at < utcnow() or row.client_id != client_id:
        raise ApiError(400, "invalid_grant", "refresh token 이 유효하지 않습니다.")
    # 회전 — 조건부 UPDATE 로 같은 refresh token 의 동시 요청 중 하나만 새 토큰을 받는다
    if db.execute(update(RefreshToken).where(RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None))
                  .values(revoked_at=utcnow())).rowcount != 1:
        raise ApiError(400, "invalid_grant", "refresh token 이 유효하지 않습니다.")
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise ApiError(400, "invalid_grant", "사용자가 비활성 상태입니다.")
    return issue_user_tokens(db, user, client_id, row.scope, family=row.family_id or row.id)


```
**분석 / Analysis (Level 2)**
- L81: 해시로 조회. `revoked_at`·`expires_at` 은 `UTCDateTime`(`app/db.py:17-35`)이라 SQLite 에서도 tz-aware 로 돌아와 L82 의 aware `utcnow()` 와 비교해도 `TypeError` 가 나지 않는다(이식성 확인).
- L82-90 재사용 탐지: 이미 폐기된 토큰이 **폐기 후 10초가 지나서** 다시 오면 유출 신호로 보고 계열(`family_id == fam` 또는 레거시 루트 `id == fam`)의 살아 있는 토큰을 한 UPDATE 로 폐기 → 커밋 → 감사 → 일반 `invalid_grant`(공격자에게 탐지 사실을 알리지 않는 같은 오류 문구). 감사는 자체 트랜잭션이라 L87 커밋 뒤에 둬야 폐기가 감사 실패와 무관하게 확정된다(순서 맞음).
- L91-92: 없음·폐기(유예 안)·만료·클라이언트 불일치는 모두 같은 400.
- L93-96 회전: `WHERE id = :id AND revoked_at IS NULL` 조건부 UPDATE. 동시성 동작:
  - PostgreSQL(READ COMMITTED, 기본) — 두 워커가 같은 토큰으로 동시에 L81 을 통과해도, 먼저 UPDATE 한 쪽이 행 잠금을 잡고 다른 쪽 UPDATE 는 커밋까지 대기 → 커밋 뒤 WHERE 를 **새 행 버전으로 재평가**(`revoked_at IS NULL` 거짓) → rowcount 0 → 400. 새 토큰은 한 쪽만 받는다(이중 발급 없음).
  - SQLite — 쓰기 잠금이 DB 단위라 두 번째 UPDATE 는 `timeout=30`(`app/db.py:46`) 동안 기다렸다가 같은 결과(0행). pysqlite 는 SELECT 를 트랜잭션 밖에서 실행하므로 오래된 스냅샷 승격 오류(SQLITE_BUSY_SNAPSHOT) 경로는 타지 않는 것으로 보인다(가능성 — 드라이버 격리 설정 확인 필요). `test/test_review_fixes.py:401-418` 이 오래된 세션 상태로 이 경로를 재현.
  - MySQL/MariaDB(`app/db.py:1` 지원 표기) — 조건이 `revoked_at IS NULL` 이라 "일치 행 = 변경 행" 이므로 `CLIENT_FOUND_ROWS` 차이와 무관하게 rowcount 판정이 같다.
- L97-99: 사용자 비활성 확인은 UPDATE 뒤지만 예외면 커밋 전이라 롤백된다(무해).
- L100: 계열 승계 — 레거시 행(`family_id` NULL)이면 자기 id 를 새 계열 루트로.
- 흔한 실수 대비: "탭 두 개가 동시에 갱신" 하면 두 번째 탭은 유예 안이라 계열 폐기 없이 400 만 받는다(L91-92) — 계열 전체 로그아웃을 피하는 의도. 대신 두 번째 탭은 다시 로그인해야 할 수 있다(UX, `test/impl/43rd/result.md:48` "유예 조정 가능").
**짚어볼 점 / Watch-outs**
- [위험:낮음] L82 유예 창이 **탐지 공백**도 만든다: 공격자가 탈취한 최신 refresh token 을 먼저 회전하고, 피해자의 정상 갱신이 10초 안에 오면 피해자 요청은 L91 의 일반 400 일 뿐 계열이 폐기되지 않는다 → 공격자는 새 계열 토큰을 계속 회전하며 세션을 유지(절대 상한 없음과 결합하면 무기한). 반대로 정상 클라이언트가 응답 유실 후 10초 넘어 재시도하면 계열 전체가 폐기된다(오탐 로그아웃). 최소 수정: 유예 안 재제시도 감사(`auth.refresh_reuse_grace`)는 남기고, 유예 안 두 번째 요청에는 같은 응답을 재전송(멱등)하거나, 유예 안 재제시가 다른 `client_id`·IP 면 탐지로 승격.
- [위험:낮음] L82 `revoked_at` 은 "회전으로 폐기" 와 "로그아웃(`app/api/idp.py:418-420`)·관리자 강제 종료(`app/api/ops.py:96-97`)·IdP 연동 중지(`app/api/identity.py:248-249`)·다른 세션 종료(`app/api/ops.py:140-142`)" 를 구분하지 않는다. 정상적으로 폐기된 세션의 클라이언트(예: 로그아웃 후 남은 탭)가 10초 뒤 갱신을 시도하면 `auth.refresh_reuse`(유출 신호) 감사가 남는다 — 보안 경보 오탐·감사 잡음. 최소 수정: `RefreshToken` 에 `revoked_reason`(rotated|logout|admin|reuse) 또는 `replaced_by` 를 두고 `rotated` 일 때만 재사용으로 판정.
- [위험:낮음] L85-86 (PostgreSQL) 계열 일괄 폐기 UPDATE 와 같은 계열의 정상 회전이 **동시에** 일어나면, UPDATE 문 시작 시점 이후 커밋된 새 행(L68 INSERT)은 그 문의 스냅샷에 보이지 않아 폐기되지 않는다 — 탐지 직후에도 새 토큰 하나가 살아남는 창(수 ms). 가능성(추가 확인 필요). 최소 수정: 탐지 시 계열 루트 행을 `SELECT … FOR UPDATE` 로 잠근 뒤 폐기하거나, 회전 경로(L94)에 "계열이 폐기되었는가" 조건(계열 루트의 `revoked_at`)을 추가.
- [위험:낮음] L85-87 계열 폐기는 refresh token 만 무효화한다 — 그 계열에서 발급된 액세스 토큰(`sid` = 계열 구성원 id)은 `app/core/auth.py:225-233` 가 `sid` 폐기를 확인하지 않아 만료까지(기본 최대 60분 — L35·L55) 유효. 탈취자가 마지막으로 받은 액세스 토큰으로 최대 1시간 더 활동 가능. 최소 수정: 재사용 탐지 시 사용자 `tokens_not_before = now`(모든 세션 종료 — 강한 조치) 또는 인증 시 `sid` 의 refresh 행 `revoked_at` 확인(조회 1회 추가).
- L88 감사 actor 가 `user:{row.user_id}`(내부 id)라 다른 인증 감사의 actor(`user.sub`, 예: `app/api/idp.py:278`)와 형식이 다르다 — 사용자별 감사 검색에서 빠질 수 있다(스타일·운영).
- 0019 이전 사슬의 계열 누락은 마이그레이션 보고서 [위험:낮음] 참조(`migrations/versions/0019_refresh_family.py:19`).

```python path=app/services/identity.py lines=103-138
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


def authenticate_workload(db: Session, client_id: str, client_secret: str) -> Workload:
    wl = db.scalars(select(Workload).where(Workload.client_id == client_id)).first()
    if wl is None or not wl.enabled or not security.check_password(client_secret, wl.secret_hash):
        raise ApiError(401, "invalid_client", "클라이언트 인증 실패")
    return wl


def issue_workload_token(db: Session, client_id: str, client_secret: str, scope: str | None) -> dict:
    wl = authenticate_workload(db, client_id, client_secret)
    allowed = set(wl.scopes or [])
    requested = set(scope.split()) if scope else allowed
    granted = sorted(requested & allowed)
    ttl = settings.access_token_ttl
    token, _ = security.sign({"sub": f"workload:{wl.name}", "anx_kind": "workload", "client_id": wl.client_id,
                              "aud": security.API_AUDIENCE, "scope": " ".join(granted)}, ttl)
    return {"access_token": token, "token_type": "Bearer", "expires_in": ttl, "scope": " ".join(granted)}


```
**분석 / Analysis (Level 2)**
- L104-110 `create_workload`: 256비트 시크릿(`anx_ws_`) → scrypt 해시 저장, `client_id = wl-{name}`(이름에서 결정 — 예측 가능), SPIFFE 표기. 평문은 반환값으로 한 번만.
- L121-125 `authenticate_workload`: Epic 40 에서 분리 — client_credentials(L129)와 `/idp/token` token-exchange grant(`app/api/idp.py:321`)가 같은 클라이언트 인증을 쓴다. 실패는 모두 `401 invalid_client`(존재·비활성·시크릿 불일치 구분 없음 — 메시지 열거 방지).
- L128-136: 요청 스코프 ∩ 허용 스코프(허용 밖은 조용히 제외 — OAuth 관례), 워크로드 토큰 서명.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L123 `client_id` 가 없거나 비활성이면 scrypt 를 계산하지 않는다 → 응답 시간 차로 유효 `client_id` 추정(1차 미해결). p=5 로 시간 차가 1차보다 약 5배 커졌고(`app/core/security.py:45`), `client_id` 는 `wl-{name}` 이라 원래도 추측 쉽다(예: 시드 워크로드 `wl-a2a-demo` — `app/seed.py:186-195`). 열거 자체보다, 유효 `client_id` 를 알면 **인증 없이 요청마다 scrypt(p=5)를 강제**할 수 있다는 점이 중요 — 호출부가 `async def`(`app/api/idp.py:288`)라 이벤트 루프 차단으로 이어진다(idp 보고서 [위험:중간]). 최소 수정: 시크릿은 SHA-256+`compare_digest`(고엔트로피), 없는 client_id 에도 같은 비용의 더미 비교.
- [위험:낮음] L121-125 (1차 [중간] "교환 엔드포인트를 게이트웨이(내부 클라이언트) 전용으로 제한" 후속) 클라이언트 인증은 추가됐지만 **어떤 워크로드가 교환을 해도 되는지**(허용 클라이언트 목록·워크로드별 허용 audience)는 없다 — 스코프가 빈 워크로드도 교환 가능(`test/test_caller_trust.py:25-26` 이 `scopes: []` 워크로드로 성공 확인). 현재 워크로드가 손에 넣을 수 있는 플랫폼 audience 사용자 토큰이 제한적이라(원격 에이전트·MCP 서버는 `aud=a2a:*`·`mcp:*` 교환 토큰만 받음 — `app/core/auth.py:221` 에서 subject 로 거부) 낮음으로 본다. 최소 수정: `Workload` 에 `can_exchange`/`exchange_audiences` 를 두고 L321 호출부에서 확인.
- L113-118 회전은 기존 워크로드 토큰을 무효화하지 않는다(jti 목록 없음 — 1차 미해결, 만료 최대 `settings.access_token_ttl`).

```python path=app/services/identity.py lines=139-171
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
- L140-150 `issue_access_token`: TTL 1초~24시간, DB 행(id=jti)으로 폐기 가능. `subject`·`audience` 는 호출자 지정 — 검사는 API 계층(`app/api/identity.py:470-473` subject 가 다른 사용자 sub 면 400).
- L154-162 `create_personal_token`: 접두로 종류 구분, 해시·접두 12자 저장(목록 표시용). `ttl_days` 가 falsy(None·0)면 **만료 없음**(L159).
- L166-169 `delegation_token`: 사용자 부재 시(승인 후 재개) 스냅샷 신원 + `act.sub`.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 교차, 집계는 `app/api/identity.py`] L144 `audience` 에 제약이 없다 — API 도 `AccessTokenIn.audience: str`(`app/api/identity.py:445`, 패턴 없음)라 `tokens:issue`(operator·security) 보유자가 `aud=mcp:<서버>`·임의 `scopes`·최대 60분 토큰을 직접 만들 수 있다. 내장 MCP 서버 쪽 검증은 서명·`aud` 만 본다(`app/builtin/mcp_auth.py:30-38`, `anx_kind` 미확인) → 토큰 교환 정책(허용 스코프·최대 15분 — `app/api/identity.py:337-339`)과 게이트웨이 승인·감사를 우회해 업스트림을 직접 호출할 수 있다(네트워크로 업스트림에 닿을 때). 1차 [중간](교환 우회)이 token-exchange grant 에서 막힌 뒤 남은 같은 종류의 문. 최소 수정: Access Token `audience` 를 `API_AUDIENCE` 로 고정(원래 용도: 원격 에이전트·MCP 서버가 **플랫폼**을 호출 — `app/models.py:117-118` docstring).
- [위험:중간 · 교차, 집계는 `app/api/identity.py`] L159 `ttl_days=None` → 만료 없는 PAT. API 는 `int | None = Field(90, ge=1, le=90)`(`app/api/identity.py:35`)이라 JSON `null` 이 그대로 통과한다(Pydantic v2 는 `None` 분기에 범위 제약을 적용하지 않음) → "최대 90일"(SCR-B06) 우회.
- [위험:낮음] L166-169 위임 토큰은 스코프 제한 없이 사용자 권한 전체(`app/core/auth.py:225`)로 인증되고, 실제 호출부는 `ttl=1800`(30분 — `app/services/orchestrator.py:63`)이다(1차 "10분" 전제보다 길다). 게다가 위임 토큰 주체(`user_id` 보유)는 `POST /api/v1/me/tokens` 로 영구 PAT 를 만들 수 있다(api 보고서 [위험:높음]). 최소 수정: 위임 토큰에 `scope`(재개에 필요한 `chat:use` 등)를 싣고 `SCOPED_KINDS` 에 `delegation` 추가.

```python path=app/services/identity.py lines=172-197
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
```
**분석 / Analysis (Level 2)**
- L177: audience 정확 일치로 정책 조회. 정책 API 의 audience 패턴은 `*` 를 허용하지만(`app/api/identity.py:336` `[A-Za-z0-9._*-]+`) 조회는 정확 일치뿐이라 와일드카드 정책은 어떤 교환에도 쓰이지 않는다(죽은 설정 — 낮음).
- L179-187 `_deny`: 거부 이력(DB)·감사를 남기고 예외 객체를 **반환** — 호출부가 `raise _deny(...)`. 이력 커밋을 먼저 해야 이후 예외로 세션이 롤백돼도 거부 기록이 남는다(의도).
- L189-197: 정책 없음·비활성 거부, 명시 요청 스코프에 허용 밖이 하나라도 있으면 전체 거부(부분 발급 안 함 — 명확), 부여할 것이 없으면 거부.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L183 `_deny` 의 `db.commit()` 은 호출자 세션의 **다른 미커밋 변경**도 함께 확정한다(1차 미해결). `app/api/catalog.py:353-355` 는 정책을 바꾸고 커밋한 뒤 호출해 문제없지만, 미래 호출자가 커밋 전 변경을 가진 채 호출하면 거부 경로에서 의도치 않게 확정된다. 최소 수정: 거부 이력은 `session_scope()` 별도 세션으로.
- L191 요청 스코프가 없고 정책 `default_scopes` 도 비면 `allowed_scopes` **전체**를 준다 — "최소 스코프" 원칙과 어긋나는 기본값(정책 작성자가 default 를 비우면 최대 권한). 정책 저장 시 `default_scopes` 필수화를 검토.
- (1차 [중간] 후속) 이 함수는 여전히 **주체가 그 audience 를 써도 되는지**(역할·팀·도구 규칙)를 보지 않는다. 게이트웨이 경로는 앞 단계(`governance.check_tool`)가 막고, 외부 경로(`/idp/token`)는 이제 워크로드 인증이 필요하다(`app/api/idp.py:318-325`) — 일반 사용자가 자기 토큰만으로 업스트림 토큰을 받는 1차 시나리오는 닫혔다. 남은 것은 L121-125 Watch-out(어느 워크로드나 교환 가능).

```python path=app/services/identity.py lines=198-217
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
- L198-202: 교환 토큰 서명 — `anx_kind=exchanged`, `aud`=대상(플랫폼 API 에서는 거부됨), `act.sub`=행위자. `/idp/token` 경로의 행위자는 이제 **인증된 워크로드 이름**(`app/api/idp.py:325` `actor=f"workload:{wl.name}"`)이라 신고값이 아니다(Epic 40 — 1차 "act 신고값" 우려 해결).
- L203-208: 발급 이력(jti 포함)·감사 → RFC 8693 응답(`issued_token_type`).
- L213-217 `get_user`: 없으면 404.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L200-201 교환 토큰에 이메일·이름·팀·역할을 싣는다 — 업스트림(외부일 수 있음)에 사용자 PII·내부 역할 구조를 전달(1차 미해결, 최소 클레임 원칙). 최소 수정: 정책에 `claims` 허용 목록(기본 `sub`·`scope`·`act` 만).
- [위험:낮음] L200 `act` 를 행위자 하나로 덮어쓴다 — 주체가 위임 토큰(이미 `actor="agentnexus-host"`, `app/core/auth.py:232`)이면 원래 위임 체인이 사라진다. RFC 8693 §4.1 은 중첩 `act`(`{"sub": 새 행위자, "act": {이전}}`)로 체인을 보존하도록 한다 — 감사 추적성 손실. 최소 수정: `subject.actor` 가 있으면 중첩.

## 파일 평가 / File Assessment
- 강점: (1) refresh 회전이 조건부 UPDATE 로 원자화되어 동시 요청 이중 발급이 막혔고(PostgreSQL READ COMMITTED 재평가·SQLite 직렬화 모두), 새 토큰 INSERT 와 같은 트랜잭션으로 확정된다. (2) 계열 id + 유예 창 + 일괄 폐기 + 감사로 OAuth 2.0 Security BCP 의 재사용 탐지를 구현(시험 `test/test_policy_hardening.py:92-115`). (3) 클라이언트 인증 함수 분리로 token-exchange grant 의 행위자가 인증된 워크로드가 됐고, 불투명 토큰·시크릿은 모두 해시 저장.
- 위험: (1) [위험:중간 · 교차] Access Token `audience` 무제약(`app/services/identity.py:144` ↔ `app/api/identity.py:445`)·PAT `ttl_days=None` 무기한(`:159` ↔ `app/api/identity.py:35`) — 집계는 api 보고서. (2) [위험:낮음] 재사용 탐지의 경계 — 유예 안 공격자 선점 미탐지·로그아웃 토큰 오탐 감사·계열 폐기 후 액세스 토큰 최대 60분 유효·PostgreSQL 동시 회전 창(`:82-90`). (3) [위험:낮음] 절대 세션 상한 없음(`:70`)·교환 토큰 PII·`act` 체인 손실·`_deny` 의 호출자 세션 커밋(`:183`, `:200-201`).
- 개선 권고(영향순): (1) (api 와 함께) Access Token audience 고정, PAT `ttl_days` null 거부. (2) `revoked_reason` 도입으로 회전 폐기만 재사용 판정 + 재사용 시 액세스 토큰까지 끊기(`tokens_not_before` 또는 `sid` 확인) + 계열 루트 기준 절대 세션 상한. (3) 교환 토큰 클레임 최소화·중첩 `act`, 워크로드별 교환 허용, 고엔트로피 시크릿의 SHA-256 검증.
- 파일 위험 등급 / Risk grade: 낮음 — 이 파일 자체의 미해결 항목은 [위험:낮음]이고, [위험:중간] 2건은 입력 검증 위치가 API(`app/api/identity.py`)라 그쪽에서 집계했다. 1차 [중간] 2건(교환 무인증·refresh 비원자)은 해결 또는 낮음으로 축소.
- 재읽기 권고 레벨: 2 — refresh 동시성·재사용 탐지 의미는 DB 격리 수준과 함께 이해해야 한다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 1차 L122 [위험:중간 · 경쟁] refresh 조회-확인-폐기 비원자 → 동시 요청 둘 다 새 토큰 | 해결 — 조건부 UPDATE rowcount, 새 토큰 INSERT 와 한 트랜잭션 | `app/services/identity.py:93-96`, `:73`, `test/test_review_fixes.py:401-418` |
| 1차 L122 재사용 탐지 없음(폐기 토큰 재제시 시 계열 폐기 필요) | 해결(경계 한계 있음) — 계열 id·10초 유예·일괄 폐기·감사. 유예 안 선점 미탐지·로그아웃 오탐·액세스 토큰 존속은 [위험:낮음]으로 남김 | `app/services/identity.py:69`, `:77`, `:82-90`, `:100`, `test/test_policy_hardening.py:92-115` |
| 1차 L123 비활성 사용자 refresh 시 폐기 표시 후 롤백(무해) | 해결(유지·무해) — 조건부 UPDATE 도 커밋 전 예외면 롤백 | `app/services/identity.py:97-99` |
| 1차 L124 절대 세션 상한 없음(슬라이딩 14일) | 미해결 | `app/services/identity.py:70` |
| 1차 L162 client_id 없을 때 scrypt 생략 → 타이밍 열거 | 미해결(p=5 로 시간 차 증가) | `app/services/identity.py:123`, `app/core/security.py:45` |
| 1차 L164 워크로드 토큰 jti 미기록 → 개별 폐기 불가 | 미해결 | `app/services/identity.py:134-135` |
| 1차 L204 Access Token `subject` 대리 발급 검사가 서비스에 없음(API 계층에 추가) | 해결(API 계층) — 다른 사용자 sub 거부. 단 `audience` 무제약은 새 [위험:중간](교차) | `app/api/identity.py:470-473`, `test/test_review_fixes.py:139-150`, `app/api/identity.py:445` |
| 1차 L205 위임 토큰 권한 전체(스코프 제한 없음), 10분 | 미해결 — 실제 호출 TTL 은 30분 | `app/services/identity.py:166-169`, `app/services/orchestrator.py:63` |
| 1차 L261 [위험:중간] `exchange` 주체 권한 검사 없음 + `/idp/token` token-exchange 무인증 → 게이트웨이 우회 | 부분 해결 — `/idp/token` 은 워크로드 인증 필수·`act`=인증된 워크로드(사용자 직접 우회 차단). 함수 자체의 주체 검사·워크로드별 교환 허용은 없음(잔여 [위험:낮음]) | `app/api/idp.py:318-325`, `app/services/identity.py:121-125`, `test/test_caller_trust.py:20-34` |
| 1차 L262 `_deny` 커밋이 호출자 세션의 다른 변경도 커밋 | 미해결 | `app/services/identity.py:183` |
| 1차 L263 교환 토큰 PII(이메일·역할·팀) | 미해결 | `app/services/identity.py:200-201` |
| 1차 L79 람다 안 `db.get` 2회 | 미해결(가독성) | `app/services/identity.py:34` |
| 1차 L80 클라이언트 IP 는 호출자 전달 — 프록시 신뢰 설정 확인 필요 | 미해결(확인 결과 Helm 이 모든 출처 XFF 신뢰 — 가능성) | `app/api/idp.py:262`, `deploy/helm/agentnexus/templates/api.yaml:43` |
| 1차 L81 잘못된 CIDR 이면 로그인 전체 실패 | 해결 — 설정 저장 시 CIDR 검증 | `app/api/admin.py:344-349` |
| 1차 INDEX §4.1-7 "refresh 회전 읽기-수정-쓰기" | 해결 | `app/services/identity.py:93-96` |
