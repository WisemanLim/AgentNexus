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
    family_id: Mapped[str | None] = mapped_column(String(40), index=True)  # 회전 계열(첫 토큰 id) — 재사용 탐지 (D17-④)
    created_at: Mapped[datetime] = _now_col()


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
    __table_args__ = (Index("ix_run_events_type_ts", "type", "ts"),)  # 첫 토큰 KPI — 기간 안 실행 시작 이벤트 (D22-①)
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = _now_col()
    type: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


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
    client_key: Mapped[str | None] = mapped_column(String(40), index=True)  # 게이트웨이 클라이언트 키 id — 키 범위 예산 (D20-①)
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
    __table_args__ = (Index("ix_spans_parent_trace", "parent_id", "trace_id"),)  # 자식 스팬 집계 (D22-①)
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
