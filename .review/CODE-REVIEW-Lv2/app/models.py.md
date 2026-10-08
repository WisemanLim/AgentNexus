> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../INDEX.md) · 디렉터리 개요: [app_Abstract.md](app_Abstract.md)

### 17. app/models.py
**파일 요약 / File Summary**
- 역할 / Role: SQLAlchemy 2.x 선언형 ORM 모델 전체(약 50개 엔터티) — Identity·Catalog·MCP·A2A·오케스트레이션·승인·모델 라우팅·정책·한도·시크릿·관측·감사·SLO·설정·첨부/RAG·심사.
- 진입점 거리 / Distance: 1 (`app/main.py:42` 지연 import, 그 외 거의 모든 모듈이 import — 내부 in-degree 50, 최다)
- LOC: 1036 — `max_lines_per_file`(600) 초과 → **논리 블록(도메인 영역) 단위로 분석**(모든 블록 커버, 원문 전체 인용)
- 주요 export: 모든 모델 클래스, `Money`(Numeric(14,6))
- 의존(내부): `app.db.{Base,UTCDateTime,utcnow}`
- 외부 패키지: sqlalchemy
- 부수효과 / Side effects: import 시 `Base.metadata` 에 테이블 등록(실제 DDL 은 Alembic `migrations/versions/0001~0018`)
- 동시성 / Concurrency: none(정의만)
- 테스트 커버리지 흔적: `test/test_foundation.py`(마이그레이션), 전 시험이 간접 사용

## 블록 리뷰 / Block Walk (Level 2)

```python path=app/models.py lines=1-17
"""AgentNexus 데이터 모델 — PRD 9.1 핵심 엔터티 + 운영 테이블.

SQLite(local)·PostgreSQL(prod) 공용: 범용 JSON·Numeric·UTCDateTime 만 사용한다.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow

Money = Numeric(14, 6)


def _now_col():
    return mapped_column(UTCDateTime(), default=utcnow, nullable=False)
```
**분석 / Analysis (Level 2)**
- L1-4: SQLite·PostgreSQL 공용을 위해 **범용 타입만** 쓴다는 설계 결정 — `JSON`(PG 에서는 `json`, `jsonb` 아님), `Numeric`, 사용자 정의 `UTCDateTime`.
- L13 `Money = Numeric(14, 6)`: 비용을 float 가 아닌 고정 소수(Decimal)로 — 누적 오차 방지.
- L16-17 `_now_col()`: `default=utcnow`(함수 참조 — 호출 결과가 아니라 함수를 넘겨야 행마다 새 시각) + NOT NULL.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 잠재] 이 파일의 모든 `JSON` 컬럼은 `MutableDict`/`MutableList` 로 감싸지 않았다 — `row.events.append(x)` 처럼 **제자리 변경은 변경 추적되지 않아 commit 해도 저장되지 않는다**. 현재 코드베이스는 항상 새 객체로 재할당한다(예: `app/services/a2a_gateway.py:424` `row.events = [*(row.events or []), ev][-100:]`, `app/services/mcp_gateway.py:658` `s.auth = {**(s.auth or {}), ...}`). 이 관례를 모르는 신규 기여자가 제자리 변경을 쓰면 조용히 데이터가 유실된다 — `flag_modified` 사용 또는 Mutable 확장 도입을 문서화할 것.
- PostgreSQL 에서 `JSON`(비-jsonb)은 인덱싱·포함 연산이 제한된다 — JSON 안을 검색하는 쿼리가 생기면 성능 문제.

```python path=app/models.py lines=20-58
# ───────────────────────── Identity (FR-I00~I04, L03) ─────────────────────────

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    issuer: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(120), unique=True)
    email: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    roles: Mapped[list] = mapped_column(JSON, default=list)
    groups: Mapped[list] = mapped_column(JSON, default=list)
    team: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # TOTP (RFC 6238) — 첫 실행 관리자 필수 (SCR-A05)
    mfa_secret: Mapped[str | None] = mapped_column(Text)  # Fernet 암호문
    tokens_not_before: Mapped[datetime | None] = mapped_column(UTCDateTime())  # 세션 강제 종료 — 이전 발급 토큰 거부 (SCR-J03·B06)
    claims: Mapped[dict | None] = mapped_column(JSON)  # 마지막 IdP 로그인 클레임 (그룹·IdP) — 재동기화·클레임 보기
    created_at: Mapped[datetime] = _now_col()


class PersonalToken(Base):
    """개인 액세스 토큰(PAT, SCR-B06) · 게이트웨이 클라이언트 키(SCR-H13)."""
    __tablename__ = "personal_tokens"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="pat")  # pat | client_key
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(24))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    team: Mapped[str | None] = mapped_column(String(120))
    allowed_models: Mapped[list | None] = mapped_column(JSON)  # 클라이언트 키 허용 모델 별칭 (SCR-H13 ④ — 비우면 제한 없음)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now_col()
```
**분석 / Analysis (Level 2)**
- L22-40 `User`: `sub` 고유·인덱스(토큰 주체), 로컬 계정이면 `username`·`password_hash`(scrypt), 외부 IdP 사용자는 `ext:{idp}:{sub}`(`app/core/auth.py:169`). `roles`·`groups` 는 JSON 배열. `mfa_secret` 은 Fernet 암호문(L37 주석). `tokens_not_before` 가 세션 강제 종료 기준(`auth.py:229-231`).
- L43-58 `PersonalToken`: PAT·클라이언트 키 공용. 원문이 아니라 `token_hash`(SHA-256, 고유) + 표시용 `prefix`. `ondelete="CASCADE"` 로 사용자 삭제 시 함께 삭제.
**짚어볼 점 / Watch-outs**
- L34 `status` 가 자유 문자열 — `active` 외 값 오타가 그대로 저장된다(CHECK 제약·Enum 없음). 인증은 `!= "active"` 로 판정하므로 안전 쪽으로 실패한다.
- L27 `username` 고유 제약은 대소문자를 구분한다(DB 기본 콜레이션 의존) — `Admin`/`admin` 이 별개 계정이 될 수 있다.

```python path=app/models.py lines=61-129
class IdpConnection(Base):
    __tablename__ = "idp_connections"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="oidc")  # oidc | entra | okta | keycloak
    issuer: Mapped[str] = mapped_column(String(255), unique=True)
    client_id: Mapped[str | None] = mapped_column(String(255))
    client_secret_ref: Mapped[str | None] = mapped_column(String(120))
    audience: Mapped[str | None] = mapped_column(String(255))
    jwks_uri: Mapped[str | None] = mapped_column(String(500))
    discovery: Mapped[dict] = mapped_column(JSON, default=dict)
    groups_claim: Mapped[str] = mapped_column(String(80), default="groups")
    roles_claim: Mapped[str] = mapped_column(String(80), default="roles")
    role_mapping: Mapped[dict] = mapped_column(JSON, default=dict)  # 그룹/역할 클레임 → AgentNexus 역할
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # 콘솔 SSO 브로커 (FR-I00·I01, SCR-A10·J02): 로그인 화면 노출 · 서버 측 호출 주소(분리 DNS) · 요청 스코프 · 마지막 시험 로그인
    login_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    internal_url: Mapped[str | None] = mapped_column(String(255))
    scopes: Mapped[str] = mapped_column(String(255), default="openid profile email")
    last_test: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _now_col()


class BrokerRequest(Base):
    """외부 IdP 로그인 위임 중인 요청 — state 로 콜백과 원래 인가 요청(콘솔 PKCE)을 잇는다 (일회용·짧은 수명)."""
    __tablename__ = "broker_requests"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    idp_id: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(10), default="login")  # login | test
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # 원래 /idp/authorize 요청 (client_id·redirect_uri·PKCE…)
    code_verifier: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSON)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now_col()
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Workload(Base):
    """에이전트·MCP 서버·서비스 자체 신원 (FR-I04) — client_credentials."""
    __tablename__ = "workloads"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="agent")  # agent | mcp_server | service
    client_id: Mapped[str] = mapped_column(String(120), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(255))
    spiffe_id: Mapped[str | None] = mapped_column(String(255))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now_col()
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AccessToken(Base):
    """원격 A2A 에이전트·MCP 서버용 단수명 토큰 (FR-L03, SCR-I05). id = jti."""
    __tablename__ = "access_tokens"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    subject: Mapped[str] = mapped_column(String(120), index=True)
    audience: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    issued_by: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now_col()
```
**분석 / Analysis (Level 2)**
- L61-83 `IdpConnection`: 외부 IdP 연동 — `issuer` 고유(토큰의 `iss` 로 찾음), 시크릿은 `client_secret_ref`(시크릿 저장소 이름)로 **간접 참조**(평문 미저장 — 좋음). `role_mapping` 으로 그룹/역할 클레임 → AgentNexus 역할.
- L86-99 `BrokerRequest`: 외부 IdP 로그인 위임 중 상태 — `state` PK, PKCE `code_verifier`·`nonce`, 일회용(`used`)·만료.
- L102-114 `Workload`: client_credentials 주체 — `secret_hash`(scrypt), SPIFFE id 표기, 허용 스코프.
- L117-129 `AccessToken`: 원격 에이전트·MCP 서버용 단수명 JWT 의 상태(id = jti) — 폐기 확인용.
**짚어볼 점 / Watch-outs**
- L93 `code_verifier` 평문 저장 — 단수명·일회용이라 수용 가능하나, DB 덤프 유출 시 진행 중 로그인을 가로챌 여지가 있다(만료 정리 필요 — 정리 작업은 `app/services/retention.py` 범위, 본 리뷰 미선정).

```python path=app/models.py lines=132-201
class TokenExchangePolicy(Base):
    """대상별 토큰 교환 규칙 (FR-I02, SCR-J04)."""
    __tablename__ = "token_exchange_policies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    audience: Mapped[str] = mapped_column(String(255), unique=True)  # mcp:jira | a2a:obs-analyst | model:* | api
    audience_type: Mapped[str] = mapped_column(String(20))
    allowed_scopes: Mapped[list] = mapped_column(JSON, default=list)
    default_scopes: Mapped[list] = mapped_column(JSON, default=list)
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=300)
    method: Mapped[str] = mapped_column(String(20), default="rfc8693")  # rfc8693 | obo
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class TokenExchange(Base):
    """9.1 TokenExchange — 교환 이력 (SCR-J05 토큰 전파 추적)."""
    __tablename__ = "token_exchanges"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ts: Mapped[datetime] = _now_col()
    subject_sub: Mapped[str] = mapped_column(String(255), index=True)
    subject_kind: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str | None] = mapped_column(String(255))
    audience: Mapped[str] = mapped_column(String(255), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=0)
    method: Mapped[str] = mapped_column(String(20))
    decision: Mapped[str] = mapped_column(String(20))  # issued | denied
    reason: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    jti: Mapped[str | None] = mapped_column(String(64))


class AuthCode(Base):
    __tablename__ = "oauth_codes"
    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(120))
    redirect_uri: Mapped[str] = mapped_column(String(500))
    user_id: Mapped[str] = mapped_column(String(40))
    code_challenge: Mapped[str | None] = mapped_column(String(128))
    code_challenge_method: Mapped[str | None] = mapped_column(String(10))
    scope: Mapped[str] = mapped_column(String(500), default="openid")
    nonce: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class DeviceCode(Base):
    __tablename__ = "oauth_device_codes"
    device_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_code: Mapped[str] = mapped_column(String(16), unique=True)
    client_id: Mapped[str] = mapped_column(String(120))
    scope: Mapped[str] = mapped_column(String(500), default="openid")
    user_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | denied | used
    interval: Mapped[int] = mapped_column(Integer, default=5)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_poll_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RefreshToken(Base):
    __tablename__ = "oauth_refresh_tokens"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    client_id: Mapped[str] = mapped_column(String(120))
    scope: Mapped[str] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now_col()
```
**분석 / Analysis (Level 2)**
- L132-144 `TokenExchangePolicy`: audience 별 RFC 8693 교환 규칙 — 허용·기본 스코프, TTL. 교환 판정의 근거(`app/services/identity.py:152-189`).
- L147-162 `TokenExchange`: 교환 이력(발급·거부) — 토큰 전파 추적 화면(SCR-J05).
- L165-176 `AuthCode`: OAuth 인가 코드(PK = 코드 값), PKCE challenge, 일회용.
- L179-189 `DeviceCode`: RFC 8628 — `device_code` PK, 사람이 입력하는 `user_code` 고유, 폴링 간격·마지막 폴링 시각(slow_down 판정).
- L192-201 `RefreshToken`: 해시 저장(`token_hash` 고유), 회전 시 `revoked_at`.
**짚어볼 점 / Watch-outs**
- L167·L180 인가 코드·디바이스 코드는 **평문 PK** — 짧은 수명(분 단위)이지만 해시 저장이 모범 관행(RefreshToken 은 해시로 저장해 일관성이 없다).
- L195 RefreshToken 에 "회전 계열(family)" 식별자가 없다 — 폐기된 refresh token 재사용 탐지(도난 탐지) 시 같은 계열 전체 폐기를 할 수 없다(`app/services/identity.py:76-84` 참조).

```python path=app/models.py lines=204-262
# ───────────────────────── Catalog / Registry (FR-P01, C00~C03) ─────────────────────────

class CatalogItem(Base):
    """9.1 CatalogItem — type: mcp_server | a2a_agent | model | agent_profile."""
    __tablename__ = "catalog_items"
    __table_args__ = (UniqueConstraint("type", "name", name="uq_catalog_type_name"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    type: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), default="manual")  # registry | manual | builtin | mirror
    source_id: Mapped[str | None] = mapped_column(String(40))
    item_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    auth_scheme: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|approved|active|rejected|deprecated
    version: Mapped[str] = mapped_column(String(40), default="0.1.0")
    license: Mapped[str | None] = mapped_column(String(80))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    owner: Mapped[str | None] = mapped_column(String(255))
    ref_id: Mapped[str | None] = mapped_column(String(40))  # 활성 인스턴스 id (mcp_servers/a2a_agents/models/agent_profiles)
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned_version: Mapped[str | None] = mapped_column(String(40))  # 버전 고정 (SCR-E07) — 없으면 최신 승인 버전
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class CatalogVersion(Base):
    __tablename__ = "catalog_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(40))
    item_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected | superseded
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now_col()


class RegistrySource(Base):
    """레지스트리 소스·미러 (SCR-E06)."""
    __tablename__ = "registry_sources"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # official | docker | custom | file
    url: Mapped[str | None] = mapped_column(String(500))
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    require_signature: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_status: Mapped[str | None] = mapped_column(Text)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    namespaces: Mapped[list] = mapped_column(JSON, default=list)  # 허용 네임스페이스 필터 (SCR-E06) — 비우면 전부
    sync_interval_s: Mapped[int | None] = mapped_column(Integer)  # 예약 동기화 주기 (없으면 수동)
    last_result: Mapped[dict] = mapped_column(JSON, default=dict)  # 마지막 동기화 성공·실패 건수·실패 목록
    created_at: Mapped[datetime] = _now_col()
```
**분석 / Analysis (Level 2)**
- L206-228 `CatalogItem`: 통합 카탈로그 항목 — `(type, name)` 복합 고유(L209), 상태 머신(pending→approved→active…), `ref_id` 로 활성 인스턴스 연결, `pinned_version`. L217 `item_metadata` 는 DB 컬럼명 `metadata`(SQLAlchemy 예약 속성과 충돌을 피한 매핑 — 중요한 관용구).
- L231-243 `CatalogVersion`: 버전별 메타데이터·심사 결정.
- L246-262 `RegistrySource`: 외부 레지스트리 미러 설정 — 자동 승인·서명 요구·네임스페이스 필터·동기화 주기.
**짚어볼 점 / Watch-outs**
- L253 `auto_approve` 와 L254 `require_signature` 기본이 False 인 것은 안전 쪽(수동 승인).

```python path=app/models.py lines=265-347
# ───────────────────────── MCP Servers (FR-S00~S03, G09) ─────────────────────────

class McpServer(Base):
    """9.1 McpServer — 호스팅/등록된 MCP 서버 인스턴스. id = namespace."""
    __tablename__ = "mcp_servers"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    catalog_item_id: Mapped[str | None] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(255))
    transport: Mapped[str] = mapped_column(String(20), default="streamable-http")  # streamable-http | stdio
    endpoint: Mapped[str | None] = mapped_column(String(500))
    command: Mapped[list] = mapped_column(JSON, default=list)
    server_json: Mapped[dict] = mapped_column(JSON, default=dict)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    resources: Mapped[list] = mapped_column(JSON, default=list)
    prompts: Mapped[list] = mapped_column(JSON, default=list)
    auth: Mapped[dict] = mapped_column(JSON, default=dict)  # {type, resource_metadata, authorization_server, scopes, secret_ref}
    health: Mapped[str] = mapped_column(String(20), default="unknown")
    health_detail: Mapped[str | None] = mapped_column(Text)
    sampling_policy: Mapped[str] = mapped_column(String(10), default="ask")  # ask(사용자 동의) | deny (SCR-F02)
    protocol: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")  # auto | legacy | 2026-07-28 (D14-②)
    upstream_protocol: Mapped[str | None] = mapped_column(String(20))  # 마지막 갱신 때 발견한 업스트림 버전
    health_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    tools_refreshed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(20), default="active")
    owner: Mapped[str | None] = mapped_column(String(255))
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class VirtualServer(Base):
    """가상 MCP 서버 합성 (FR-G01·G09, SCR-H03)."""
    __tablename__ = "virtual_servers"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    members: Mapped[list] = mapped_column(JSON, default=list)  # [{server_id, tools: [..] | null}]
    created_at: Mapped[datetime] = _now_col()


class ToolRule(Base):
    """도구 허용목록·위험도·인자 스키마 (FR-G07, SCR-H10/H11). 규칙 없는 도구 = 거부."""
    __tablename__ = "tool_rules"
    __table_args__ = (UniqueConstraint("server_id", "tool", name="uq_tool_rule"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(64), index=True)
    tool: Mapped[str] = mapped_column(String(128))
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    risk: Mapped[str] = mapped_column(String(10), default="low")  # low | medium | high
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    approver: Mapped[str] = mapped_column(String(20), default="requester")  # requester(사용자 동의) | approvers(승인자 큐)
    args_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    args_mode: Mapped[str] = mapped_column(String(10), default="strict")  # strict(위반 차단) | warn(기록만) — SCR-H11
    sanitize: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class SanitizeRule(Base):
    __tablename__ = "sanitize_rules"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    pattern: Mapped[str] = mapped_column(Text)
    applies_to: Mapped[str] = mapped_column(String(20), default="tool_response")  # tool_response | input | output
    action: Mapped[str] = mapped_column(String(10), default="strip")  # strip | mask | block
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class McpSession(Base):
    """게이트웨이 MCP 세션 (클라이언트 측 종료·업스트림 세션 매핑, NFR-S1)."""
    __tablename__ = "mcp_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), default="client")  # client | upstream
    server_id: Mapped[str] = mapped_column(String(64), index=True)
    principal_sub: Mapped[str] = mapped_column(String(255), index=True)
    upstream_session_id: Mapped[str | None] = mapped_column(String(255))
    protocol_version: Mapped[str | None] = mapped_column(String(20))
    client_info: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _now_col()
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    events: Mapped[list] = mapped_column(JSON, default=list)  # 최근 세션 이벤트 (SCR-D09 ④) — 열림·재동기·재연결·종료
    reconnects: Mapped[int] = mapped_column(Integer, default=0)
```
**분석 / Analysis (Level 2)**
- L267-292 `McpServer`: id = 네임스페이스(게이트웨이 경로 `/mcp/{id}`). 전송(`streamable-http`|`stdio`|`hosted-openapi`), `endpoint`, `command`(stdio), 레지스트리 `server_json`, 캐시된 `tools`·`resources`·`prompts`, 인증 설정 `auth`(JSON — type·authorization_server·tool_scopes·secret_ref·tls 등), 헬스, 샘플링 정책, 프로토콜 세대.
- L295-302 `VirtualServer`: 여러 서버 도구를 합성(멤버별 도구 제한).
- L305-320 `ToolRule`: **도구 허용목록**(규칙 없는 도구 = 거부, L306 docstring) — 위험도·승인 필요·승인 주체·인자 스키마·strict/warn·소독. `(server_id, tool)` 고유.
- L323-330 `SanitizeRule`: 정규식 소독 규칙.
- L333-347 `McpSession`: 클라이언트 측 세션(게이트웨이가 종료) 및 업스트림 세션 매핑, 최근 이벤트.
**짚어볼 점 / Watch-outs**
- [위험:중간] L280 `auth` JSON 에 `secret_ref` 가 들어가고, 관리 API 가 이 dict 를 요청 본문으로 **병합**한다(`app/api/mcp.py:400-401`). 모델 수준에서 "어떤 키를 사용자가 쓸 수 있는가" 가 정의되지 않아 임의 `secret_ref` 지정이 가능하다 — 스키마(pydantic 모델)로 허용 키를 제한해야 한다.
- L275 `command`(stdio 실행 명령)도 JSON 으로 저장 — 허용 목록 검사는 실행 시점(`app/services/mcp_gateway.py:564-567`)에만 있다. 저장 시점 검증도 있으면 오설정을 빨리 잡는다.
- L310·L338 `server_id` 에 FK 가 없다 — 서버 삭제 시 규칙·세션이 고아로 남는다.

```python path=app/models.py lines=350-398
# ───────────────────────── A2A (FR-R00~R06, G08) ─────────────────────────

class A2AAgent(Base):
    """9.1 A2AAgentCard — 원격 에이전트 등록·카드 캐시. id = slug."""
    __tablename__ = "a2a_agents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    catalog_item_id: Mapped[str | None] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(500))
    card_url: Mapped[str] = mapped_column(String(500))
    card: Mapped[dict] = mapped_column(JSON, default=dict)
    card_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(40))
    skills: Mapped[list] = mapped_column(JSON, default=list)
    trust_tier: Mapped[str] = mapped_column(String(20), default="untrusted")  # verified | trusted | untrusted | blocked
    auth: Mapped[dict] = mapped_column(JSON, default=dict)  # {scheme, secret_ref, token_exchange, header}
    status: Mapped[str] = mapped_column(String(20), default="active")
    card_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    etag: Mapped[str | None] = mapped_column(String(255))  # Agent Card 캐시 (SCR-G03)
    cache_ttl_s: Mapped[int] = mapped_column(Integer, default=3600)
    owner_team: Mapped[str | None] = mapped_column(String(120))
    framework: Mapped[str | None] = mapped_column(String(40))  # ADK·LangGraph·CrewAI… (SCR-G01 — 카드 힌트 또는 운영자 지정)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class A2ATask(Base):
    """A2A Task 영속 (FR-R03) — outbound(원격 위임) / inbound(플랫폼 A2A 서버)."""
    __tablename__ = "a2a_tasks"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # 게이트웨이 내부 id
    remote_task_id: Mapped[str | None] = mapped_column(String(120), index=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(10), default="outbound")
    context_id: Mapped[str | None] = mapped_column(String(120))
    session_id: Mapped[str | None] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    principal_sub: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(20), default="submitted", index=True)
    status_message: Mapped[str | None] = mapped_column(Text)
    history: Mapped[list] = mapped_column(JSON, default=list)
    artifacts: Mapped[list] = mapped_column(JSON, default=list)
    push_token: Mapped[str | None] = mapped_column(String(80), index=True)
    push_config: Mapped[dict] = mapped_column(JSON, default=dict)  # 클라이언트 웹훅 {url, token} (게이트웨이가 중계)
    events: Mapped[list | None] = mapped_column(JSON, default=list)  # 상태 전이·send/sse/push/poll 이벤트 (SCR-G06, 최근 100)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    last_polled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
```
**분석 / Analysis (Level 2)**
- L352-374 `A2AAgent`: 원격 에이전트 등록 — 카드 캐시·해시(변경 감지)·신뢰 등급(verified|trusted|untrusted|blocked)·인증 설정(`auth` JSON — scheme·secret_ref·token_url·tls·push_verify)·ETag/TTL·프레임워크 힌트.
- L377-398 `A2ATask`: 원격 위임(outbound)·플랫폼 수신(inbound) Task 영속 — id 는 `{agent_id}:{remote_id}`(`app/services/a2a_gateway.py:377-378`), 상태·이력·아티팩트·푸시 토큰·이벤트(최근 100).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L393 `push_config`(클라이언트 웹훅 `{url, token}`)의 `token` 이 **평문 JSON** 으로 저장된다 — 클라이언트가 자기 웹훅 인증에 쓰는 비밀이므로 시크릿 저장소(Fernet) 또는 해시가 맞다. `task_out()` 은 이 필드를 응답에 싣지 않는다(`app/api/a2a.py:609-620` — 확인됨).
- L396 `last_polled_at` 은 정의돼 있지만 감시기(`poll_once`)가 갱신하지 않는다 — 감시기 기아 문제(`a2a_gateway.py` 리뷰)의 해결에 이 컬럼을 쓸 수 있다.
- L398 `updated_at` 에 인덱스가 없는데 감시기는 `state IN (...) ORDER BY updated_at LIMIT 20` 을 실행한다 — Task 가 많아지면 정렬 비용(복합 인덱스 `(direction, state, updated_at)` 권장).

```python path=app/models.py lines=401-519
# ───────────────────────── Agent profiles / Orchestration (FR-A02, W02) ─────────────────────────

class AgentProfile(Base):
    """9.1 AgentProfile — 오케스트레이션 템플릿(graph YAML/JSON)."""
    __tablename__ = "agent_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    catalog_item_id: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published | archived
    version: Mapped[int] = mapped_column(Integer, default=1)
    graph: Mapped[dict] = mapped_column(JSON, default=dict)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    remote_agents: Mapped[list] = mapped_column(JSON, default=list)
    models: Mapped[list] = mapped_column(JSON, default=list)
    policies: Mapped[list] = mapped_column(JSON, default=list)
    template: Mapped[bool] = mapped_column(Boolean, default=False)
    a2a_exposed: Mapped[bool] = mapped_column(Boolean, default=False)
    a2a_card: Mapped[dict] = mapped_column(JSON, default=dict)  # 게시용 Agent Card 편집본 (SCR-D10)
    a2a_status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | pending | published
    draft_code: Mapped[str | None] = mapped_column(Text)  # 검증 오류가 남은 YAML — '코드만 저장' (SCR-D07 ⑧, MSG-001)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AgentProfileVersion(Base):
    __tablename__ = "agent_profile_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("agent_profiles.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    graph: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now_col()


class ChatSession(Base):
    """9.1 Session — 대화 세션 (FR-A01)."""
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_sub: Mapped[str] = mapped_column(String(255), index=True)
    team: Mapped[str | None] = mapped_column(String(120))
    profile_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255), default="새 대화")
    source: Mapped[str] = mapped_column(String(10), default="chat")  # chat | api (SCR-C07 출처 필터)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, default="")
    parts: Mapped[list] = mapped_column(JSON, default=list)  # 첨부·인용·카드
    run_id: Mapped[str | None] = mapped_column(String(40))
    feedback: Mapped[dict | None] = mapped_column(JSON)  # 👍👎 (SCR-C03 ⑧) — {rating, comment, at}
    branch_of: Mapped[str | None] = mapped_column(String(40))  # 편집 후 재전송·다시 생성의 원래 사용자 메시지
    created_at: Mapped[datetime] = _now_col()


class Run(Base):
    """오케스트레이션 실행 1회 (요청 1건)."""
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(40), index=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True)
    user_sub: Mapped[str] = mapped_column(String(255), index=True)
    principal: Mapped[dict] = mapped_column(JSON, default=dict)  # 재개 시 위임 토큰 발급용 신원 스냅샷
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    request: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[list] = mapped_column(JSON, default=list)
    graph: Mapped[dict] = mapped_column(JSON, default=dict)  # 실행 시점 프로파일 그래프 스냅샷 (재개·재시도 일관성)
    profile_version: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    data_class: Mapped[str] = mapped_column(String(20), default="internal")
    trace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    test: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)  # 쓰기 도구는 호출하지 않고 인자만 기록 (SCR-D08 ③)
    created_at: Mapped[datetime] = _now_col()
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)  # 실행 워커 생존 신호 (복구 판정)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RunStep(Base):
    __tablename__ = "run_steps"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(20))  # planner | retriever | summarize | executor | remote
    name: Mapped[str] = mapped_column(String(255))
    tool: Mapped[str | None] = mapped_column(String(255))
    agent_id: Mapped[str | None] = mapped_column(String(64))
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    output: Mapped[dict | list | str | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RunEvent(Base):
    """SSE 재생용 실행 이벤트 로그 (워커 간 공유)."""
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = _now_col()
    type: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
```
**분석 / Analysis (Level 2)**
- L403-424 `AgentProfile`: 오케스트레이션 템플릿 — `graph`(YAML/JSON 그래프), 허용 도구 패턴 `tools`(fnmatch — `app/services/governance.py:76-79`), 원격 에이전트·모델·정책, A2A 노출 카드, 검증 실패 초안 코드.
- L427-435 `AgentProfileVersion`: 그래프 버전 이력(FK CASCADE).
- L438-450 `ChatSession`·L453-463 `Message`: 대화·메시지(첨부·인용·피드백·분기).
- L466-489 `Run`: 요청 1건 실행 — 재개용 신원 스냅샷(`principal`), 그래프 스냅샷(재개 일관성), 생존 신호 `heartbeat_at`(인덱스 — 복구 스캔용).
- L492-509 `RunStep`: 노드별 단계 — 승인 대기 시 `approval_id`(`app/services/orchestrator.py` 의 `on_approval` 이 이 값으로 단계를 찾는다).
- L512-519 `RunEvent`: SSE 재생용 이벤트 로그 — `BigInteger().with_variant(Integer, "sqlite")`(SQLite 는 INTEGER PRIMARY KEY 만 자동 증가하므로 변형 타입이 필요하다 — 핵심 이식성 관용구).
**짚어볼 점 / Watch-outs**
- L473 `Run.principal` 에 신원 스냅샷(이름·이메일·역할·팀)을 JSON 으로 저장 — 개인정보가 실행 기록에 복제된다(보존 정책 대상인지 확인 필요).
- L516 `RunEvent.run_id` 에 FK 가 없다(인덱스만) — 실행 삭제 시 이벤트가 남는다.
- RunEvent 는 다른 사용자의 `run_id` 로도 행이 추가될 수 있다 — 클라이언트 헤더 `X-AgentNexus-Run` 을 그대로 쓰는 경로가 있다(`app/api/mcp.py:55-61` → `app/services/mcp_consent.py:112-115`). 모델에는 소유자 컬럼이 없어 DB 수준 방어가 불가능하다.

```python path=app/models.py lines=522-582
class Approval(Base):
    """사람 승인 대기열 (FR-G07, SCR-C04/H12/E05)."""
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="tool_call")  # tool_call | registry | sampling | elicitation
    # pending|approved|rejected|expired|used — sampling 은 review(응답 검토 대기)|sent(검토 후 전송)|completed 추가
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requester_sub: Mapped[str] = mapped_column(String(255))
    session_id: Mapped[str | None] = mapped_column(String(40))
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    step_id: Mapped[str | None] = mapped_column(String(40))
    server_id: Mapped[str | None] = mapped_column(String(64))
    tool: Mapped[str | None] = mapped_column(String(255))
    target: Mapped[str | None] = mapped_column(String(255))
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    args_hash: Mapped[str | None] = mapped_column(String(64))
    risk: Mapped[str] = mapped_column(String(10), default="high")
    approver: Mapped[str] = mapped_column(String(20), default="requester")  # requester | approvers
    reason: Mapped[str | None] = mapped_column(Text)
    policy_ref: Mapped[str | None] = mapped_column(String(120))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decision_note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    extensions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 시간 연장 횟수 (WCAG 2.2.1, D14-③)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    payload: Mapped[dict | None] = mapped_column(JSON)  # 동의 결정 내용 (편집한 프롬프트·검토 여부·elicitation 응답)
    delegated_to: Mapped[str | None] = mapped_column(String(255))  # 승인 위임 대상 sub (SCR-H12)
    result: Mapped[dict | None] = mapped_column(JSON)   # 라우팅 미리보기·생성 초안·최종 응답 (SCR-C08)
    created_at: Mapped[datetime] = _now_col()


class McpRelay(Base):
    """하류 MCP 클라이언트로 중계한 서버→클라이언트 요청과 그 응답 (워커 간 공유, FR-G09·A00)."""
    __tablename__ = "mcp_relays"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 하류로 보낸 JSON-RPC id
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    server_id: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    response: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _now_col()
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class McpTask(Base):
    """MCP Tasks 확장 작업 (io.modelcontextprotocol/tasks, D15-①) — 게이트웨이가 돌리는 장기 도구 호출. 주체에 묶이고 워커 간 공유."""
    __tablename__ = "mcp_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 추측 불가한 taskId (task_ + 192비트)
    server_id: Mapped[str] = mapped_column(String(64))
    principal_sub: Mapped[str] = mapped_column(String(255), index=True)
    tool: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="working")  # working | completed | failed | cancelled
    status_message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[dict | None] = mapped_column(JSON)
    ttl_ms: Mapped[int | None] = mapped_column(Integer)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # 실행 워커 생존 신호
```
**분석 / Analysis (Level 2)**
- L522-551 `Approval`: 사람 승인 대기열 — 종류(tool_call|registry|sampling|elicitation), 상태 머신(pending→approved|rejected|expired→used, sampling 은 review·sent·completed 추가), 요청자, 대상, **인자 원문과 해시**(`args_hash` — 승인한 인자와 실행 인자가 같은지 확인), 만료·연장 횟수·위임 대상·결과.
- L554-564 `McpRelay`: 서버→클라이언트 요청 중계 상태(워커 간 공유).
- L567-582 `McpTask`: MCP Tasks 확장 작업 — 추측 불가 id, 주체·서버에 묶임, 취소 요청 플래그, 생존 신호.
**짚어볼 점 / Watch-outs**
- [위험:중간] L528 `status` 를 조건부 갱신(`UPDATE ... WHERE status='approved'`)으로 소비하지 않는 서비스 코드와 결합하면 같은 승인이 두 번 사용될 수 있다(`app/services/governance.py:152-160`). 모델에는 낙관적 잠금(`version_id_col`)이 없어 ORM 수준 경합 탐지도 없다 — `__mapper_args__ = {"version_id_col": ...}` 를 고려.
- L536 `args`·L548 `payload` 에 도구 인자·sampling 프롬프트·elicitation 응답 원문이 **평문**으로 저장된다(COMPLIANCE.md 41행이 의도를 기록). 감사에는 id 만 남기지만 이 테이블 자체가 민감 데이터 저장소이므로 보존·접근 통제가 중요하다.
- `remembered()`(`governance.py:173-180`)가 `session_id`·`target`·`requester_sub` 로 조회하지만 L530·L535 에 인덱스가 없다(`status`·`run_id` 만 인덱스).
- L581 `McpTask.updated_at` 에 `onupdate` 가 없다 — 코드가 수동 갱신한다(`mcp_tasks._finish`). 다른 테이블(`onupdate=utcnow`)과 관례가 다르다.

```python path=app/models.py lines=585-664
# ───────────────────────── Models / Providers / Routing (FR-L00~L02, G01, G06) ─────────────────────────

class Provider(Base):
    __tablename__ = "providers"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # openai | anthropic | gemini | vllm | ollama | litellm | openai_compat | mock
    base_url: Mapped[str | None] = mapped_column(String(500))
    secret_ref: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(40))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now_col()


class Model(Base):
    """게이트웨이 노출 모델 별칭 (Unified Model API)."""
    __tablename__ = "models"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    upstream_model: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(20), default="commercial")  # commercial | domain | local | mock
    data_classes: Mapped[list] = mapped_column(JSON, default=list)  # 허용 데이터 분류
    price_in: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))  # USD / 1M input tokens
    price_out: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))  # USD / 1M output tokens
    context_window: Mapped[int] = mapped_column(Integer, default=8192)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)  # stream | tools | json_schema | vision | embeddings
    quality: Mapped[float | None] = mapped_column(Float)  # 0~1 품질 점수 (라우팅 전략 quality, FR-G01) — 운영자 평가·피드백
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now_col()


class RoutingRule(Base):
    __tablename__ = "routing_rules"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    match_model: Mapped[str] = mapped_column(String(120), default="*")
    match_data_class: Mapped[str | None] = mapped_column(String(20))
    strategy: Mapped[str] = mapped_column(String(20), default="priority")  # priority | cost | latency | ab
    targets: Mapped[list] = mapped_column(JSON, default=list)  # [{model, weight}]
    fallbacks: Mapped[list] = mapped_column(JSON, default=list)
    retries: Mapped[int] = mapped_column(Integer, default=1)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=30000)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=0)  # 마지막 게시 버전 (SCR-H02 ⑤, 0 = 미게시)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class RoutingRuleVersion(Base):
    """라우팅 규칙 게시본 (SCR-H02 ⑤) — 게시 시점 스냅샷·사유, 되돌리기 대상."""
    __tablename__ = "routing_rule_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(255))
    ts: Mapped[datetime] = _now_col()


class RoutingDecision(Base):
    """라우팅 결정 로그 (FR-G01, SCR-H04)."""
    __tablename__ = "routing_decisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ts: Mapped[datetime] = _now_col()
    trace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # model | mcp | a2a
    subject: Mapped[str | None] = mapped_column(String(255))
    requested: Mapped[str] = mapped_column(String(255))
    chosen: Mapped[str | None] = mapped_column(String(255))
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(Text)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="ok")
```
**분석 / Analysis (Level 2)**
- L587-599 `Provider`: LLM 공급자 — `secret_ref` 로 API 키 간접 참조, 옵션(timeout·data_classes·tls).
- L602-617 `Model`: 게이트웨이 노출 별칭 → 공급자·업스트림 모델, 허용 데이터 등급, 가격(USD/1M 토큰, Money), 품질 점수.
- L620-647 `RoutingRule`·`RoutingRuleVersion`: 라우팅 전략(priority|cost|latency|quality|ab)·대상·폴백·재시도, 게시 스냅샷.
- L650-664 `RoutingDecision`: 라우팅 결정 로그(모델·MCP·A2A).
**짚어볼 점 / Watch-outs**
- L627 `targets` JSON 의 `weight` 형식이 모델에 정의되지 않는다 — 0·음수·문자열 가중치가 저장되면 `random.choices` 가 실패한다(`app/services/llm.py:124-127` 리뷰 참조).
- L593 시크릿 이름이 예측 가능한 관례(`provider/{id}/api_key`, `app/seed.py:118`)로 만들어진다 — 이름만 알면 다른 기능의 `secret_ref` 로 지정할 수 있는 구조(위 L280 참조).

```python path=app/models.py lines=667-734
# ───────────────────────── Gateway policy / limits / secrets (FR-G02~G04) ─────────────────────────

class GatewayPolicy(Base):
    """9.1 GatewayPolicy."""
    __tablename__ = "gateway_policies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    subject_type: Mapped[str] = mapped_column(String(20), default="any")  # any | user | group | role | team | agent
    subject: Mapped[str] = mapped_column(String(255), default="*")
    target_type: Mapped[str] = mapped_column(String(20))  # model | tool | agent
    target: Mapped[str] = mapped_column(String(255), default="*")
    effect: Mapped[str] = mapped_column(String(10), default="allow")  # allow | deny
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)  # {data_class: [...], args: {...}}
    args_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    rate_limit: Mapped[dict] = mapped_column(JSON, default=dict)
    data_class_routing: Mapped[dict] = mapped_column(JSON, default=dict)  # {internal: "domain-local"}
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class PolicyRevision(Base):
    __tablename__ = "policy_revisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    changed_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now_col()


class RateLimit(Base):
    """요청/토큰/비용 한도 (FR-G04, SCR-H09/K07)."""
    __tablename__ = "rate_limits"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    scope_type: Mapped[str] = mapped_column(String(20))  # user | team | agent | client_key | any
    scope: Mapped[str] = mapped_column(String(255), default="*")
    target_type: Mapped[str] = mapped_column(String(20), default="any")  # model | tool | agent | any
    target: Mapped[str] = mapped_column(String(255), default="*")
    rps: Mapped[float | None] = mapped_column(Numeric(10, 3))
    burst: Mapped[int | None] = mapped_column(Integer)
    tokens_per_min: Mapped[int | None] = mapped_column(Integer)
    budget_usd: Mapped[Decimal | None] = mapped_column(Money)
    period: Mapped[str] = mapped_column(String(10), default="month")  # day | month
    alert_pct: Mapped[int] = mapped_column(Integer, default=80)
    on_exceed: Mapped[str] = mapped_column(String(10), default="block")  # block | fallback
    fallback_model: Mapped[str | None] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now_col()


class Secret(Base):
    """시크릿 저장소 (FR-G02, SCR-H06) — 평문은 저장·반환하지 않는다."""
    __tablename__ = "secrets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="api_key")
    ciphertext: Mapped[str] = mapped_column(Text)
    last4: Mapped[str | None] = mapped_column(String(8))
    description: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now_col()
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
```
**분석 / Analysis (Level 2)**
- L669-689 `GatewayPolicy`: 주체 유형(any|user|group|role|team|agent)·대상 유형(model|tool|agent)·효과·조건(데이터 등급)·인자 스키마·데이터 등급 재라우팅·승인 요구·우선순위.
- L692-699 `PolicyRevision`: 정책 스냅샷 이력.
- L702-720 `RateLimit`: 요청·토큰·비용 한도, 초과 시 block|fallback.
- L723-734 `Secret`: Fernet 암호문 + `last4`(표시용).
**짚어볼 점 / Watch-outs**
- [위험:중간] L675-676 `subject_type="agent"` 는 호출 에이전트 이름으로 매칭되는데, 그 값이 클라이언트가 보내는 헤더(`X-AgentNexus-Agent`)에서 온다(`app/api/mcp.py:58`, `app/services/policy.py:71-72`). 모델 설계상 "에이전트 주체" 가 인증된 신원이 아니므로 agent 대상 allow 정책은 위조 가능하다.
- L711 `rps: Mapped[float | None] = Numeric(10, 3)` — 타입 힌트는 float 지만 실제 반환은 `Decimal`(L832 `Span.duration_ms`, L906 `AlertRule.threshold` 도 동일). float 와 섞어 연산하면 `TypeError` — 호출부가 `float(...)` 로 변환해 쓰고 있다(`app/api/gateway_admin.py:440`, `app/api/observe.py:464`).
- L730 `last4` 는 8자 이상일 때만 저장(`app/services/secrets.py:16`) — 짧은 시크릿의 노출 비율을 줄이는 배려.

```python path=app/models.py lines=737-848
# ───────────────────────── Observability & Audit (FR-O00~O05) ─────────────────────────

class UsageRecord(Base):
    """9.1 UsageRecord."""
    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_team_ts", "team", "ts"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    session_id: Mapped[str | None] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40))
    step_id: Mapped[str | None] = mapped_column(String(40), index=True)  # 노드별 지연·비용 (SCR-D08 ⑥)
    user_sub: Mapped[str | None] = mapped_column(String(255), index=True)
    team: Mapped[str | None] = mapped_column(String(120))
    agent: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # model | tool | remote_agent
    target: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    policy_decisions: Mapped[list] = mapped_column(JSON, default=list)


class AuditEvent(Base):
    """9.1 AuditEvent — 불변 해시 체인 (prev_hash → hash)."""
    __tablename__ = "audit_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(40), unique=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(255), index=True)
    actor_kind: Mapped[str] = mapped_column(String(20), default="user")
    action: Mapped[str] = mapped_column(String(80), index=True)
    target: Mapped[str | None] = mapped_column(String(255))
    decision: Mapped[str | None] = mapped_column(String(40))
    trace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditChainHead(Base):
    """체인 머리 — append 직렬화(PG: SELECT FOR UPDATE) + 아카이브 앵커."""
    __tablename__ = "audit_chain_head"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    last_hash: Mapped[str] = mapped_column(String(64))
    anchor_seq: Mapped[int] = mapped_column(Integer, default=0)  # 이 seq 까지 아카이브·파기됨
    anchor_hash: Mapped[str] = mapped_column(String(64))  # anchor_seq 이벤트의 hash (검증 시작점)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AuditArchive(Base):
    """보관기간 경과 구간 아카이브·파기 증명 (COMPLIANCE testing_additions)."""
    __tablename__ = "audit_archives"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    from_seq: Mapped[int] = mapped_column(Integer)
    to_seq: Mapped[int] = mapped_column(Integer)
    first_prev_hash: Mapped[str] = mapped_column(String(64))
    last_hash: Mapped[str] = mapped_column(String(64))
    event_count: Mapped[int] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(String(500))
    file_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now_col()
    purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuditReport(Base):
    __tablename__ = "audit_reports"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(255))
    range_from: Mapped[datetime | None] = mapped_column(UTCDateTime())
    range_to: Mapped[datetime | None] = mapped_column(UTCDateTime())
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    file_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(Text)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    chain_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now_col()


class Span(Base):
    """내장 트레이스 저장소 (FR-O01 조회용, OTLP 내보내기와 병행)."""
    __tablename__ = "spans"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # span_id hex
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    service: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20))
    start_time: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime())
    duration_ms: Mapped[float] = mapped_column(Numeric(12, 3))
    status: Mapped[str] = mapped_column(String(10), default="ok")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    events: Mapped[list] = mapped_column(JSON, default=list)


class LogEntry(Base):
    __tablename__ = "log_entries"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10), index=True)
    logger: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    component: Mapped[str | None] = mapped_column(String(40))
    trace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_enc: Mapped[str | None] = mapped_column(Text)  # 마스킹 전 원문(Fernet) — 사유·30분 마스킹 해제용 (SCR-K04)
```
**분석 / Analysis (Level 2)**
- L739-760 `UsageRecord`: 사용량·비용(Money)·지연·상태·정책 결정 — `(team, ts)` 복합 인덱스(L742)로 팀별 기간 집계.
- L763-777 `AuditEvent`: 불변 해시 체인(`prev_hash`→`hash`), `seq` PK.
- L780-788 `AuditChainHead`: 체인 머리(직렬화 지점)·아카이브 앵커.
- L791-818 `AuditArchive`·`AuditReport`: 보관기간 경과 구간 아카이브·파기 증명, 서명 리포트.
- L821-835 `Span`: 내장 트레이스 저장소. L838-848 `LogEntry`: 구조화 로그 + 마스킹 전 원문 암호문(`raw_enc`).
**짚어볼 점 / Watch-outs**
- L766 `seq` 는 `autoincrement=True` 지만 실제로는 머리(`last_seq + 1`) 기준으로 명시 할당한다(`app/services/audit.py:58-61` — SQLite rowid 재사용 회피). 둘 다 선언돼 있어 읽는 사람이 혼동한다.
- 감사 테이블에 DB 수준 불변성(UPDATE·DELETE 금지 트리거·권한 분리)이 없다 — 해시 체인이 변조를 **탐지**하지만 방지하지는 않는다. DB 권한을 가진 공격자는 체인 전체를 재계산해 바꿀 수 있다(머리·아카이브 해시를 외부에 앵커링하지 않는 한).
- L826 `Span.parent_id`·L825 `trace_id` 는 문자열 — 대량 데이터에서 인덱스 크기가 커진다(수용).

```python path=app/models.py lines=851-910
class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info | warn | danger
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    audience_role: Mapped[str | None] = mapped_column(String(40))
    dedupe_key: Mapped[str | None] = mapped_column(String(255), index=True)
    read_by: Mapped[list] = mapped_column(JSON, default=list)


class Slo(Base):
    """SLO (D15-④) — 데이터 경로(모델·MCP·A2A) 가용성·지연 목표와 이동 창. 오류 예산·소진율은 sli_minutes 로 계산."""
    __tablename__ = "slos"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # availability(5xx 아님) | latency(첫 응답 ≤ threshold_ms)
    scope: Mapped[str] = mapped_column(String(10), default="all")  # all | model | mcp | a2a
    target: Mapped[float] = mapped_column(Float)
    threshold_ms: Mapped[int | None] = mapped_column(Integer)
    window_days: Mapped[int] = mapped_column(Integer, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    alert_state: Mapped[str | None] = mapped_column(String(20))  # ok | ticket | page | exhausted
    alert_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SliMinute(Base):
    """분 단위 SLI 카운터 — 워커별 행(경합 없음), 읽을 때 합산. gtN = 첫 응답이 N ms 를 넘긴 요청 수."""
    __tablename__ = "sli_minutes"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)  # {분}:{cls}:{worker}
    minute: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    cls: Mapped[str] = mapped_column(String(10))  # model | mcp | a2a
    worker: Mapped[str] = mapped_column(String(80))
    total: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    gt100: Mapped[int] = mapped_column(Integer, default=0)
    gt250: Mapped[int] = mapped_column(Integer, default=0)
    gt500: Mapped[int] = mapped_column(Integer, default=0)
    gt1000: Mapped[int] = mapped_column(Integer, default=0)
    gt2000: Mapped[int] = mapped_column(Integer, default=0)
    gt5000: Mapped[int] = mapped_column(Integer, default=0)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    metric: Mapped[str] = mapped_column(String(80))  # error_rate | p95_latency_ms | cost_usd | qps
    op: Mapped[str] = mapped_column(String(4), default=">")
    threshold: Mapped[float] = mapped_column(Numeric(14, 4))
    window_s: Mapped[int] = mapped_column(Integer, default=300)
    severity: Mapped[str] = mapped_column(String(10), default="warn")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
```
**분석 / Analysis (Level 2)**
- L851-862 `Notification`: 알림 센터 — 대상 역할, 중복 억제 키(인덱스), 읽음 사용자 목록(JSON).
- L865-879 `Slo`: SLO 정의 — 가용성/지연, 범위, 목표, 지연 기준, 창, 경보 상태. L875 `server_default=true()` 로 마이그레이션 시 기존 행 기본값.
- L882-897 `SliMinute`: 분 단위·분류·워커별 카운터 행(경합 없는 쓰기 — 읽을 때 합산). 지연 구간별 초과 수(`gt100`…`gt5000`).
- L900-910 `AlertRule`: 지표 임계 경보.
**짚어볼 점 / Watch-outs**
- L862 `read_by` JSON 배열에 사용자를 추가하는 방식은 동시 읽음 처리에서 마지막 쓰기가 이전 추가를 덮어쓸 수 있다(경쟁 조건 — 제자리 변경 추적 문제와 별개로 행 단위 lost update).
- L873 `threshold_ms` 는 L892-897 의 컬럼 이름과 1:1 이어야 한다(`app/services/slo.py:92` 가 `getattr(SliMinute, f"gt{threshold_ms}")`) — API 가 값을 제한한다(`app/api/observe.py:432-434`).

```python path=app/models.py lines=913-943
# ───────────────────────── Admin (FR-W01, W03, P02) ─────────────────────────

class Connector(Base):
    """외부 연동 커넥터 (FR-W03, SCR-L02)."""
    __tablename__ = "connectors"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # idp | api_gateway | observability | registry | a2a
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now_col()


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSON)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class SettingHistory(Base):
    """설정 변경 이력 — 되돌리기 (SCR-L06 ⑨)."""
    __tablename__ = "setting_history"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSON)
    changed_by: Mapped[str | None] = mapped_column(String(255))
    ts: Mapped[datetime] = _now_col()
```
**분석 / Analysis (Level 2)**
- L915-925 `Connector`: 외부 연동(시크릿은 ref).
- L928-933 `Setting`: 키-값 설정(JSON 값) — 리스(lease) 구현(`app/services/scheduler.py:24-42`)도 이 테이블의 조건부 UPDATE 를 쓴다.
- L936-943 `SettingHistory`: 설정 되돌리기 이력.
**짚어볼 점 / Watch-outs**
- L930 `key` 길이 80 — 리스 키 `lease.{name}` 은 짧아 문제없다. `SettingHistory.key` 는 120(L940)으로 서로 다르다(일관성).
- `Setting` 이 범용 저장소로 쓰여(보안 설정·RBAC 재정의·리스·설정 코드 해시) 키 이름 충돌·권한 경계가 코드 관례에만 의존한다.

```python path=app/models.py lines=946-996
# ───────────────────────── 첨부 · 지식 베이스 (FR-A01 첨부 · FR-A02.2 RAG) ─────────────────────────
class Attachment(Base):
    """채팅 첨부 원본 (data/attachments) + 추출 텍스트. 이미지는 텍스트 없이 메타데이터만."""
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_sub: Mapped[str] = mapped_column(String(255), index=True)
    session_id: Mapped[str | None] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(10), default="text")  # text | image | binary
    text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    team: Mapped[str | None] = mapped_column(String(64), index=True)  # None = 전체 공개, 아니면 팀 범위
    data_class: Mapped[str] = mapped_column(String(20), default="internal")
    embedding_model: Mapped[str] = mapped_column(String(120), default="embed-local")
    dims: Mapped[int | None] = mapped_column(Integer)
    owner: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class KbDocument(Base):
    __tablename__ = "kb_documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now_col()


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), index=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("kb_documents.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSON, default=list)  # L2 정규화 벡터 — 코사인 = 내적 (SQLite·PG 공통)
```
**분석 / Analysis (Level 2)**
- L947-960 `Attachment`: 채팅 첨부 메타·추출 텍스트·파일 경로(`data/attachments`).
- L963-974 `KnowledgeBase`: 팀 범위(`team` None = 전체 공개), 데이터 등급, 임베딩 모델.
- L977-996 `KbDocument`·`KbChunk`: 문서·청크·임베딩(JSON 배열 — L996 주석: L2 정규화 벡터, 코사인 = 내적).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 확장성] L996 임베딩을 JSON 으로 저장하면 검색 시 모든 청크를 읽어 애플리케이션에서 내적을 계산해야 한다(벡터 인덱스 없음). 소규모 데모에는 충분하지만 문서 수만 건이면 pgvector 등이 필요하다.
- L957 `path` 에 파일 시스템 경로 저장 — 경로 조작 방지는 저장 시점(서비스 코드)의 몫이다(`app/services/attachments.py`, 본 리뷰 미선정).

```python path=app/models.py lines=999-1036
# ───────────────────────── 등록·승인·정책 게시 (Epic 21 · SCR-G03·H07·H08) ─────────────────────────
class A2ACardCheck(Base):
    """Agent Card 주기 재검증 이력 — 실패 시 이전 캐시 유지 (SCR-G03 ⑦)."""
    __tablename__ = "a2a_card_checks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20))  # ok | not_modified | changed | error
    http_status: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String(255))
    changes: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = _now_col()


class PolicyDraft(Base):
    """정책 초안 → 회귀 시험 → 게시 요청(사유) → 작성자 외 승인 → 적용 (SCR-H07·H08)."""
    __tablename__ = "policy_drafts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    policy_id: Mapped[str | None] = mapped_column(String(40), index=True)  # None = 새 정책
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | pending | approved | rejected
    reason: Mapped[str | None] = mapped_column(Text)
    test_result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(255))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now_col()


class PolicyTest(Base):
    """정책 회귀 시험 케이스 — 입력과 기대 판정. 하나라도 어긋나면 게시 불가."""
    __tablename__ = "policy_tests"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    input: Mapped[dict] = mapped_column(JSON, default=dict)  # {kind, target, data_class, user/agent ...}
    expect: Mapped[str] = mapped_column(String(10))  # allow | deny
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now_col()
```
**분석 / Analysis (Level 2)**
- L1000-1010 `A2ACardCheck`: 카드 재검증 이력.
- L1013-1025 `PolicyDraft`: 정책 초안 → 회귀 시험 → 게시 요청 → 작성자 외 승인. `created_by` 와 `decided_by` 비교로 2인 검토를 판정한다.
- L1028-1036 `PolicyTest`: 회귀 시험 케이스(입력·기대 판정).
**짚어볼 점 / Watch-outs**
- [위험:중간] L1022 `created_by` 만 있고 "마지막 수정자" 컬럼이 없다 — 초안을 다른 사람이 수정해도 작성자가 바뀌지 않아 2인 검토가 우회된다(`app/api/gateway_admin.py:258-268,308-315` 리뷰 참조). `updated_by` 를 추가하고 승인자 ≠ 모든 수정자 조건으로 판정해야 한다.

## 파일 평가 / File Assessment
- 강점: (1) DB 이식성을 위한 일관된 타입 선택(UTCDateTime·Money·BigInteger 변형). (2) 시크릿·토큰을 해시 또는 ref 로 저장(PAT·refresh·워크로드·IdP·공급자). (3) 도메인별 주석이 요구사항 ID 와 연결돼 추적성이 높다.
- 위험: (1) [중간] JSON 컬럼 제자리 변경 미추적(관례 의존), 사용자 병합 가능한 `auth` JSON. (2) [중간] 정책 초안 수정자 미기록·승인 행 낙관적 잠금 부재 → 2인 검토 우회·승인 이중 사용 위험의 근원. (3) [낮음] FK 부재로 고아 행, 일부 평문 저장(push token·인가 코드), 조회 패턴 대비 인덱스 누락.
- 개선 권고(영향순): (1) `PolicyDraft.updated_by`·`Approval` 버전 컬럼 추가. (2) `auth` JSON 에 대한 pydantic 스키마(허용 키) 도입, push token 암호화. (3) `(direction,state,updated_at)`·`approvals(session_id,target)` 인덱스, JSON Mutable 사용 지침 문서화.
- 재읽기 권고 레벨: 3 — 스키마는 블록 단위로 관계·제약만 다시 보면 된다.
