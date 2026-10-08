"""운영·설정 API (FR-P02·W01·W03·P04 · SCR-A05~A09·B03·B04·B05·L01~L06).

배포 프로파일(독립 실행) · 시스템 헬스 · 외부 연동 커넥터 · 백업/복구 · 일반 설정 · 알림 센터 · 전역 검색 · 첫 실행 상태 ·
업데이트·사양 버전.
"""
import importlib.metadata as md
from datetime import datetime
from decimal import Decimal

import jwt
from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Numeric, func, or_, select
from sqlalchemy.orm import Session

from app.core import keys, tracing
from app.core.auth import Principal, current_principal, require
from app.core.config import (A2A_PROTOCOL_VERSION, A2A_SUPPORTED_VERSIONS, ALL_COMPONENTS, APP_VERSION, MCP_PROTOCOL_VERSION,
                             MCP_SUPPORTED_VERSIONS, settings)
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.http import sync_client
from app.core.ids import canonical_json, new_id, sha256_hex
from app.db import UTCDateTime, engine, get_db, ping, utcnow
from app.models import (A2AAgent, AgentProfile, AgentProfileVersion, AlertRule, CatalogItem, CatalogVersion,
                        ChatSession, Connector, GatewayPolicy, IdpConnection, McpServer, Model, Notification, Provider,
                        RateLimit, RegistrySource, RoutingRule, SanitizeRule, Secret, Setting, TokenExchangePolicy,
                        ToolRule, User, VirtualServer)
from app.services import audit, deploy, guards, llm, policy, ratelimit, registry, retention, secrets

router = APIRouter(tags=["admin"])
A = "/api/v1/admin"

# ───────────────────────── 배포 프로파일 (FR-W01, SCR-L01) ─────────────────────────
PROFILES = [
    {"id": "full", "name": "전체 플랫폼", "components": "all", "compose_profiles": "demo",
     "command": "make local-all", "desc": "콘솔·게이트웨이·Host·레지스트리·IdP·관측 + 참조 MCP 서버·데모 A2A"},
    {"id": "gateway-only", "name": "AI Gateway 단독", "components": "gateway,identity", "compose_profiles": "",
     "command": "AGENTNEXUS_COMPONENTS=gateway,identity COMPOSE_PROFILES= make local-all",
     "desc": "기존 앱의 LLM·MCP·A2A 프록시로만 사용 (/v1/chat/completions 등)"},
    {"id": "registry-only", "name": "MCP Registry 단독", "components": "registry,identity", "compose_profiles": "",
     "command": "AGENTNEXUS_COMPONENTS=registry,identity COMPOSE_PROFILES= make local-all",
     "desc": "발견 전용 레지스트리(/v0/servers)·카탈로그"},
    {"id": "mcp-servers-only", "name": "참조 MCP 서버 단독", "components": "identity", "compose_profiles": "demo",
     "command": "AGENTNEXUS_COMPONENTS=identity make local-all",
     "desc": "참조 MCP 서버 + 토큰 발급(IdP)만 — 게이트웨이 없이 서버 시험"},
    {"id": "a2a-only", "name": "A2A 서버 단독", "components": "host,gateway,identity", "compose_profiles": "",
     "command": "AGENTNEXUS_COMPONENTS=host,gateway,identity COMPOSE_PROFILES= make local-all",
     "desc": "플랫폼 오케스트레이션을 A2A 서버(/.well-known/agent-card.json)로만 노출"},
    {"id": "airgap", "name": "에어갭", "components": "all", "compose_profiles": "demo,ollama",
     "command": "nexus bundle import ./agentnexus-airgap && COMPOSE_PROFILES=demo,ollama make prod-all",
     "desc": "외부 SaaS 없이 — 로컬 모델(Ollama/vLLM)·내장 IdP·내장 트레이스 저장소"},
]


@router.get(f"{A}/profiles")
def deploy_profiles(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    st = deploy.state(db)
    return {"current": {"components": [c for c in ALL_COMPONENTS if settings.enabled(c)], "env": settings.app_env,
                        "profile": st["profile"], "mode": st["mode"], "airgap": st["airgap"],
                        "applied_at": st["applied_at"], "applied_by": st["applied_by"]},
            "items": PROFILES}


class ProfileIn(BaseModel):
    profile: str = Field(pattern="^(full|gateway-only|registry-only|mcp-servers-only|a2a-only)$")
    airgap: bool = False
    mode: str | None = Field(None, pattern="^(compose|helm)$")
    confirm: bool = False


@router.post(f"{A}/profiles/plan")
def deploy_plan(body: ProfileIn, _: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    """드라이런 (SCR-L01 ⑧) — 시작·중지·유지 구성요소, 데이터 보존, 에어갭 효과, 명령. 아무것도 바꾸지 않는다."""
    return deploy.plan(db, body.profile, body.airgap)


@router.post(f"{A}/profiles/apply")
def deploy_apply(body: ProfileIn, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """적용 — 원하는 상태 저장 + 에어갭 즉시 적용. 기동 구성 변경은 응답의 명령(compose·helm)으로 반영 (MSG-002 확인 필요)."""
    if not body.confirm:
        raise bad_request("적용하려면 확인이 필요합니다 (confirm: true, MSG-002).", code="confirm_required")
    return deploy.apply(db, p.sub, body.profile, body.airgap, body.mode)


# ───────────────────────── 시스템 헬스 (SCR-B05) ─────────────────────────
def _probe(url: str, path: str = "") -> dict:
    try:
        with sync_client(timeout=3) as http:
            res = http.get(url.rstrip("/") + path)
        return {"ok": res.status_code < 500, "status": res.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


@router.get("/api/v1/system/health", tags=["system"])
def system_health(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    try:
        ping()
        dbs = {"ok": True, "engine": engine.dialect.name}
    except Exception as exc:  # noqa: BLE001
        dbs = {"ok": False, "error": type(exc).__name__}
    servers = db.scalars(select(McpServer).where(McpServer.status == "active")).all()
    agents = db.scalars(select(A2AAgent).where(A2AAgent.status == "active")).all()
    providers = db.scalars(select(Provider).where(Provider.enabled.is_(True))).all()
    comps = {c: {"enabled": settings.enabled(c)} for c in ALL_COMPONENTS}
    deps = {"database": dbs, "rate_limit": ratelimit.backend_status(),
            "policy_engine": {"engine": settings.policy_engine,
                              **(_probe(settings.opa_url, "/health") if settings.policy_engine == "opa"
                                 and settings.opa_url else {"ok": True})},
            "tracing": {"ok": tracing.STATS["db_failed"] == 0, "exported": tracing.STATS["db_exported"],
                        "otlp": tracing.STATS["otlp_configured"]},
            "mcp_servers": {"ok": all(s.health == "healthy" for s in servers),
                            "items": {s.id: s.health for s in servers}},
            "a2a_agents": {"ok": all(a.card for a in agents), "items": {a.id: bool(a.card) for a in agents}},
            "providers": {"ok": bool(providers), "items": {p.id: p.status for p in providers},
                          **llm.provider_health()},
            "idp": {"ok": True, "builtin": settings.builtin_idp,
                    "external": {i.issuer: i.status for i in db.scalars(select(IdpConnection))}}}
    overall = "ok" if all(v.get("ok", True) for v in deps.values()) else "degraded"
    return {"status": overall, "version": APP_VERSION, "components": comps, "dependencies": deps,
            "checked_at": utcnow()}


# ───────────────────────── 외부 연동 커넥터 (FR-W03, SCR-L02) ─────────────────────────
class ConnectorIn(BaseModel):
    kind: str = Field(pattern="^(idp|api_gateway|observability|registry|a2a)$")
    name: str
    config: dict = Field(description="url · product(kong|apisix|grafana|jaeger|otel) · health_path 등")
    secret: str | None = Field(None, repr=False)


def connector_out(c: Connector) -> dict:
    return {"id": c.id, "kind": c.kind, "name": c.name, "config": c.config, "status": c.status,
            "credential_configured": bool(c.secret_ref), "last_tested_at": c.last_tested_at}


@router.get(f"{A}/connectors")
def list_connectors(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    return {"items": [connector_out(c) for c in db.scalars(select(Connector).order_by(Connector.kind, Connector.name))]}


@router.post(f"{A}/connectors", status_code=201)
def create_connector(body: ConnectorIn, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    if not body.config.get("url"):
        raise bad_request("config.url 이 필요합니다.")
    c = Connector(id=new_id("con"), kind=body.kind, name=body.name, config=body.config)
    if body.secret:
        c.secret_ref = f"connector/{c.id}"
        secrets.put(db, c.secret_ref, body.secret, kind="token")
    db.add(c)
    db.commit()
    audit.append("connector.create", actor=p.sub, target=c.name, data={"kind": c.kind, "url": body.config.get("url")})
    return connector_out(c)


@router.post(f"{A}/connectors/{{cid}}/test")
def test_connector(cid: str, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if c is None:
        raise not_found("connector")
    res = _probe(c.config["url"], c.config.get("health_path", ""))
    c.status, c.last_tested_at = ("ok" if res["ok"] else "error"), utcnow()
    db.commit()
    return {**connector_out(c), "result": res}


@router.delete(f"{A}/connectors/{{cid}}")
def delete_connector(cid: str, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    c = db.get(Connector, cid)
    if c is None:
        raise not_found("connector")
    db.delete(c)
    db.commit()
    audit.append("connector.delete", actor=p.sub, target=c.name)
    return {"deleted": cid}


@router.get(f"{A}/connectors/api-gateway/config")
def api_gateway_config(product: str = Query(..., pattern="^(kong|apisix)$"), upstream: str | None = None,
                       _: Principal = Depends(require("admin:read"))):
    """기존 API 게이트웨이(Kong·APISIX) 앞단 연동 설정 생성 — AgentNexus 게이트웨이 경로로 라우팅."""
    target = (upstream or settings.public_base_url).rstrip("/")
    paths = ["/v1/chat/completions", "/v1/models", "/mcp", "/a2a", "/.well-known", "/idp"]
    if product == "kong":
        cfg = {"_format_version": "3.0", "services": [{
            "name": "agentnexus", "url": target, "routes": [{"name": "agentnexus-gateway", "paths": paths,
                                                            "strip_path": False, "preserve_host": True}],
            "plugins": [{"name": "correlation-id", "config": {"header_name": "X-Request-Id"}},
                        {"name": "opentelemetry", "config": {"propagation": {"default_format": "w3c"}}}]}]}
    else:
        cfg = {"routes": [{"uri": f"{p}*", "name": f"agentnexus{p.replace('/', '-')}",
                           "upstream": {"type": "roundrobin", "nodes": {target.split("://", 1)[1]: 1},
                                        "scheme": target.split("://", 1)[0]}} for p in paths]}
    return {"product": product, "upstream": target, "config": cfg,
            "note": "인증은 AgentNexus 가 수행(Bearer). 프록시는 Authorization·traceparent 헤더를 그대로 전달해야 한다."}


@router.get(f"{A}/connectors/grafana/dashboard")
def grafana_dashboard(_: Principal = Depends(require("admin:read"))):
    """Prometheus 지표용 Grafana 대시보드 JSON (SCR-K05 연동). Grafana 는 AGPL — 분리 배포(NFR-LIC1)."""
    def panel(i, title, expr, unit="short"):
        return {"id": i, "type": "timeseries", "title": title, "gridPos": {"h": 8, "w": 12, "x": (i % 2) * 12,
                                                                           "y": (i // 2) * 8},
                "fieldConfig": {"defaults": {"unit": unit}}, "targets": [{"expr": expr, "refId": "A"}]}
    panels = [panel(0, "요청 QPS", "sum(rate(anx_http_requests_total[5m])) by (component)", "reqps"),
              panel(1, "p95 지연", "histogram_quantile(0.95, sum(rate(anx_http_request_duration_seconds_bucket[5m])) by (le, route))", "s"),
              panel(2, "토큰/분", "sum(rate(anx_llm_tokens_total[5m])) by (model) * 60"),
              panel(3, "비용(USD)", "sum(increase(anx_llm_cost_usd_total[1h])) by (team)", "currencyUSD"),
              panel(4, "도구 호출", "sum(rate(anx_mcp_tool_calls_total[5m])) by (server, status)"),
              panel(5, "정책 거부", "sum(increase(anx_policy_denies_total[1h])) by (kind, code)"),
              panel(6, "승인 대기", "anx_approvals_pending"),
              panel(7, "MCP 서버 헬스", "anx_mcp_server_healthy")]
    return {"dashboard": {"title": "AgentNexus — AI Gateway", "uid": "agentnexus-gw", "schemaVersion": 39,
                          "panels": panels, "time": {"from": "now-6h", "to": "now"}}, "overwrite": True}


# ───────────────────────── 업데이트·사양 버전 (SCR-L04) ─────────────────────────
@router.get(f"{A}/updates")
def updates(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    def ver(pkg):
        try:
            return md.version(pkg)
        except md.PackageNotFoundError:
            return None
    with engine.connect() as conn:
        head = MigrationContext.configure(conn).get_current_revision()
    return {"version": APP_VERSION, "migration": head,
            "protocols": {"mcp": {"current": MCP_PROTOCOL_VERSION, "supported": ["2026-07-28", *MCP_SUPPORTED_VERSIONS],
                                  "modern": ["2026-07-28"]},
                          "a2a": {"current": A2A_PROTOCOL_VERSION, "supported": list(A2A_SUPPORTED_VERSIONS)},
                          "openai": "chat.completions", "otel": "GenAI semconv"},
            "dependencies": {p: ver(p) for p in ("fastapi", "sqlalchemy", "fastmcp", "mcp", "pyjwt",
                                                 "opentelemetry-sdk", "httpx", "a2a-sdk")},
            "policy": "MCP·A2A 사양은 분기 재조사 후 어댑터 계층에서 버전업 (NFR-M1)"}


# ───────────────────────── 백업·복구 (SCR-L05) ─────────────────────────
BACKUP_TABLES = [Provider, Model, RoutingRule, GatewayPolicy, RateLimit, ToolRule, SanitizeRule, TokenExchangePolicy,
                 IdpConnection, RegistrySource, CatalogItem, CatalogVersion, McpServer, VirtualServer, A2AAgent,
                 AgentProfile, AgentProfileVersion, Setting, AlertRule, Connector]


def _to_row(obj) -> dict:
    """컬럼명 기준 직렬화 (속성명이 다른 컬럼 — 예: catalog_items.metadata — 도 정확히)."""
    mapper = obj.__mapper__
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, mapper.get_property_by_column(col).key)
        out[col.name] = v.isoformat() if isinstance(v, datetime) else (str(v) if isinstance(v, Decimal) else v)
    return out


def _from_row(model, row: dict):
    attrs = {}
    for col in model.__table__.columns:
        if col.name not in row:
            continue
        v = row[col.name]
        if v is not None and isinstance(col.type, (UTCDateTime, DateTime)):
            v = datetime.fromisoformat(v)
        elif v is not None and isinstance(col.type, Numeric):
            v = Decimal(str(v))
        attrs[model.__mapper__.get_property_by_column(col).key] = v
    return model(**attrs)


@router.post(f"{A}/backup")
def backup(include_secrets: bool = Body(False, embed=True), p: Principal = Depends(require("admin:write")),
           db: Session = Depends(get_db)):
    """구성 백업(서명 번들). 시크릿은 암호문 그대로(같은 ENCRYPTION_KEY 로만 복원) 선택 포함. 워크로드 비밀 해시는 제외."""
    return make_backup(db, p.sub, include_secrets)


def make_backup(db: Session, actor: str, include_secrets: bool = False) -> dict:
    tables = BACKUP_TABLES + ([Secret] if include_secrets else [])
    data = {m.__tablename__: [_to_row(o) for o in db.scalars(select(m))] for m in tables}
    body = {"format": "agentnexus-backup/1", "version": APP_VERSION, "created_at": utcnow().isoformat(),
            "created_by": actor, "tables": data}
    digest = sha256_hex(canonical_json(body))
    sig = jwt.encode({"sha256": digest, "iss": "agentnexus-backup"}, keys.signing_key(), algorithm="RS256",
                     headers={"kid": keys.kid()})
    audit.append("admin.backup", actor=actor, data={"tables": {k: len(v) for k, v in data.items()}, "sha256": digest,
                                                    "secrets": include_secrets})
    return {"bundle": body, "sha256": digest, "signature": sig}


class RestoreIn(BaseModel):
    bundle: dict
    signature: str
    confirm: str = ""  # 'RESTORE' 를 입력해야 실제 복원 (SCR-L05 ⑥ 확인 입력)


@router.post(f"{A}/restore")
def restore(body: RestoreIn, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """서명 검증 후 병합 복원(merge) — 같은 키는 백업 값으로 덮어쓰고, 백업에 없는 행은 유지한다."""
    if body.confirm != "RESTORE":
        raise bad_request("복원은 운영 데이터를 덮어씁니다 — 확인란에 RESTORE 를 입력하세요 (MSG-002). 먼저 격리 복구 시험을 권장합니다.",
                          code="confirm_required")
    digest = sha256_hex(canonical_json(body.bundle))
    try:
        claims = keys.decode_long_lived(body.signature, algorithms=["RS256"], issuer="agentnexus-backup",  # 회전 전 키로 서명한 백업도 (D21-③)
                                        options={"require": ["iss", "sha256"]})  # 같은 키로 서명한 다른 용도 JWT 는 거부 (59th)
    except jwt.PyJWTError as exc:
        raise bad_request(f"백업 서명 검증 실패: {exc}", code="invalid_backup") from exc
    if claims.get("sha256") != digest:
        raise bad_request("백업 내용이 서명과 일치하지 않습니다(변조).", code="invalid_backup")
    from app.api import ops
    snapshot = ops.write_backup(db, f"{p.sub}:pre-restore", "-prerestore",  # 되돌릴 수 있게 복원 직전 상태를 남긴다
                                include_secrets="secrets" in body.bundle.get("tables", {}))
    models = {m.__tablename__: m for m in BACKUP_TABLES + [Secret]}
    counts = {}
    for tname, rows in body.bundle.get("tables", {}).items():
        model = models.get(tname)
        if model is None:
            continue
        for r in rows:
            db.merge(_from_row(model, r))
        counts[tname] = len(rows)
    db.commit()
    audit.append("admin.restore", actor=p.sub, data={"tables": counts, "sha256": digest, "snapshot": snapshot})
    return {"restored": counts, "sha256": digest, "snapshot": snapshot}


# ───────────────────────── 일반 설정 (SCR-L06) ─────────────────────────
SETTING_KEYS = {"ui.language": ("ko", "en"), "gateway.default_data_class": ("public", "internal", "confidential"),
                "retention": dict, "gateway.security": dict, "registry.require_signature": bool,
                # SCR-L06 ③⑤⑥⑦ · H06 · K07 · L05
                "guards.pii_rules": list, "auth.access_token_ttl_min": (5, 60), "auth.session_ttl_hours": (1, 720),
                "auth.ip_allowlist": list, "ui.timezone": str, "secrets.rotation_days": (7, 365),
                "notify.channels": dict, "backup.schedule": dict}


def _check_setting(k: str, v) -> None:
    import ipaddress
    import re as _re
    if k == "guards.pii_rules":
        for r in v:
            if not isinstance(r, dict) or not r.get("pattern"):
                raise bad_request("PII 규칙은 {name, pattern, enabled} 목록")
            try:
                _re.compile(r["pattern"])
            except _re.error as exc:
                raise bad_request(f"정규식 오류({r.get('name')}): {exc}") from exc
    if k == "auth.ip_allowlist":
        for n in v:
            try:
                ipaddress.ip_network(str(n), strict=False)
            except ValueError as exc:
                raise bad_request(f"CIDR 형식 오류: {n}") from exc
    if k == "ui.timezone":
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(v)
        except Exception as exc:  # noqa: BLE001
            raise bad_request(f"알 수 없는 시간대: {v}") from exc
    if k == "notify.channels" and v.get("slack_webhook") and not str(v["slack_webhook"]).startswith("https://"):
        raise bad_request("Slack 웹훅은 https:// URL")
    if k == "backup.schedule" and v.get("interval", "off") not in ("off", "daily", "weekly"):
        raise bad_request("backup.schedule.interval 은 off | daily | weekly")


@router.get(f"{A}/settings")
def get_settings(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    rows = {s.key: s.value for s in db.scalars(select(Setting))}
    from app.services.identity import auth_policy
    pol = auth_policy(db)
    return {"ui.language": rows.get("ui.language", "ko"),
            "gateway.default_data_class": rows.get("gateway.default_data_class", "internal"),
            "retention": retention.policy(db), "gateway.security": guards.security_settings(db),
            "registry.require_signature": bool(rows.get("registry.require_signature", False)),
            "guards.pii_rules": rows.get("guards.pii_rules", []), "auth.access_token_ttl_min": pol["access_token_ttl_min"],
            "auth.session_ttl_hours": pol["session_ttl_hours"], "auth.ip_allowlist": pol["ip_allowlist"],
            "ui.timezone": rows.get("ui.timezone", "Asia/Seoul"), "secrets.rotation_days": rows.get("secrets.rotation_days", 90),
            "notify.channels": rows.get("notify.channels", {}),
            "backup.schedule": rows.get("backup.schedule", {"interval": "off", "keep": 7})}


@router.put(f"{A}/settings")
def put_settings(body: dict, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    for k, v in body.items():
        rule = SETTING_KEYS.get(k)
        if rule is None:
            raise bad_request(f"알 수 없는 설정: {k}")
        if isinstance(rule, tuple) and isinstance(rule[0], int):
            if not isinstance(v, int) or not rule[0] <= v <= rule[1]:
                raise bad_request(f"{k} 는 {rule[0]}~{rule[1]} 정수")
        elif isinstance(rule, tuple) and v not in rule:
            raise bad_request(f"{k} 는 {rule} 중 하나")
        if rule in (list, str) and not isinstance(v, rule):
            raise bad_request(f"{k} 형식 오류")
        _check_setting(k, v)
        if rule is dict and not isinstance(v, dict):
            raise bad_request(f"{k} 는 객체")
        if rule is bool and not isinstance(v, bool):
            raise bad_request(f"{k} 는 true|false")
        if k == "retention":
            bad = [x for x in v if x not in retention.DEFAULTS or not isinstance(v[x], int) or v[x] < 1]
            if bad:
                raise bad_request(f"retention 항목 오류: {bad}")
        from app.api.ops import put_setting
        put_setting(db, k, v, p.sub)  # 이력 남김 — 되돌리기 (SCR-L06 ⑨)
    db.commit()
    guards.reset_custom_cache()
    policy.reset_default_data_class()  # 기본 데이터 등급 5초 캐시
    audit.append("admin.settings.update", actor=p.sub, data=body)
    return get_settings(p, db)


# ───────────────────────── 알림 센터 (SCR-B04) ─────────────────────────
def _audience_ok(n: Notification, p: Principal) -> bool:
    if n.audience_role is None or "admin" in p.roles:
        return True
    return n.audience_role in p.roles or p.has({"operator": "admin:write", "security": "policy:write",
                                                "finops": "cost:read"}.get(n.audience_role, "*"))


@router.get("/api/v1/notifications", tags=["admin"])
def notifications(p: Principal = Depends(current_principal), db: Session = Depends(get_db), unread: bool = False,
                  category: str | None = None, limit: int = Query(50, le=200)):
    """알림 센터 (SCR-B04) — 분류 탭(category)·수신 설정에서 끈 분류는 제외."""
    from app.api.ops import category_of
    prefs = (db.get(Setting, f"notify.prefs.{p.sub}"[:80]).value if db.get(Setting, f"notify.prefs.{p.sub}"[:80]) else {}) or {}
    muted = set(prefs.get("muted") or [])
    rows = [n for n in db.scalars(select(Notification).order_by(Notification.ts.desc()).limit(500))
            if _audience_ok(n, p) and category_of(n.kind) not in muted]
    counts: dict[str, int] = {}
    unread_counts: dict[str, int] = {}
    for n in rows:
        counts[category_of(n.kind)] = counts.get(category_of(n.kind), 0) + 1
        if p.sub not in (n.read_by or []):
            unread_counts[category_of(n.kind)] = unread_counts.get(category_of(n.kind), 0) + 1
    if category:
        rows = [n for n in rows if category_of(n.kind) == category]
    if unread:
        rows = [n for n in rows if p.sub not in (n.read_by or [])]
    return {"unread": sum(1 for n in rows if p.sub not in (n.read_by or [])), "counts": counts, "unread_counts": unread_counts,
            "items": [{"id": n.id, "ts": n.ts, "kind": n.kind, "category": category_of(n.kind), "severity": n.severity,
                       "title": n.title, "body": n.body, "link": n.link, "read": p.sub in (n.read_by or [])}
                      for n in rows[:limit]]}


@router.post("/api/v1/notifications/read", tags=["admin"])
def mark_read(ids: list[str] = Body(default_factory=list, embed=True), p: Principal = Depends(current_principal),
              db: Session = Depends(get_db)):
    q = select(Notification) if not ids else select(Notification).where(Notification.id.in_(ids))
    n = 0
    for row in db.scalars(q):
        if _audience_ok(row, p) and p.sub not in (row.read_by or []):
            row.read_by = [*(row.read_by or []), p.sub]
            n += 1
    db.commit()
    return {"marked": n}


# ───────────────────────── 전역 검색 ⌘K (SCR-B03) ─────────────────────────
@router.get("/api/v1/search", tags=["admin"])
def search(q: str = Query(..., min_length=1), p: Principal = Depends(current_principal), db: Session = Depends(get_db),
           limit: int = Query(8, le=50)):
    like = f"%{q}%"
    out: dict[str, list] = {}
    if p.has("catalog:read"):
        items, _ = registry.search(db, q=q, statuses=None if p.has("catalog:approve") else registry.DISCOVERABLE,
                                   limit=limit)
        out["catalog"] = [{"id": i.id, "title": i.display_name or i.name, "type": i.type,
                           "link": f"/console/catalog/{i.id}"} for i in items]
    if p.has("mcp:read"):
        tools = []
        for s in db.scalars(select(McpServer).where(McpServer.status == "active")):
            tools += [{"id": f"{s.id}.{t['name']}", "title": f"{s.id}.{t['name']}", "type": "tool",
                       "link": f"/console/mcp-servers/{s.id}"} for t in s.tools or []
                      if q.lower() in f"{s.id}.{t['name']} {t.get('description') or ''}".lower()]
        out["tools"] = tools[:limit]
    if p.has("agents:read"):
        out["agents"] = [{"id": a.id, "title": a.name, "type": "agent_profile", "link": f"/console/agents/{a.id}"}
                         for a in db.scalars(select(AgentProfile).where(or_(AgentProfile.name.like(like),
                                                                            AgentProfile.description.like(like)),
                                                                        AgentProfile.status != "archived").limit(limit))]
    if p.has("a2a:read"):
        out["a2a"] = [{"id": a.id, "title": a.name, "type": "a2a_agent", "link": f"/console/a2a/{a.id}"}
                      for a in db.scalars(select(A2AAgent).where(or_(A2AAgent.name.like(like),
                                                                     A2AAgent.id.like(like))).limit(limit))]
    out["sessions"] = [{"id": s.id, "title": s.title, "type": "session", "link": f"/chat/{s.id}"}
                       for s in db.scalars(select(ChatSession).where(ChatSession.user_sub == p.sub,
                                                                     ChatSession.title.like(like)).limit(limit))]
    if p.has("identity:read"):
        out["users"] = [{"id": u.id, "title": u.name or u.username, "type": "user", "link": "/console/identity/users"}
                        for u in db.scalars(select(User).where(or_(User.username.like(like), User.name.like(like),
                                                                   User.email.like(like))).limit(limit))]
    if p.has("gateway:read"):
        out["policies"] = [{"id": g.id, "title": g.name, "type": "policy", "link": f"/console/gateway/policies/{g.id}"}
                           for g in db.scalars(select(GatewayPolicy).where(GatewayPolicy.name.like(like)).limit(limit))]
    if p.has("observe:read") and len(q) >= 8 and all(c in "0123456789abcdef" for c in q.lower()):
        out["traces"] = [{"id": q, "title": f"trace {q}", "type": "trace", "link": f"/console/observe/traces/{q}"}]
    return {"q": q, "results": {k: v for k, v in out.items() if v}}


# ───────────────────────── 첫 실행 상태 (SCR-A05~A09) ─────────────────────────
@router.get("/api/v1/setup/status", tags=["admin"])
def setup_status(_: Principal = Depends(require("admin:read")), db: Session = Depends(get_db)):
    admins = [u for u in db.scalars(select(User)) if "admin" in (u.roles or [])]
    providers = db.scalars(select(Provider).where(Provider.enabled.is_(True))).all()
    servers = db.scalars(select(McpServer).where(McpServer.status == "active")).all()
    templates = db.scalar(select(func.count()).select_from(AgentProfile).where(AgentProfile.template.is_(True),
                                                                               AgentProfile.status == "published"))
    steps = [
        {"id": "identity", "title": "초기 관리자·IdP", "done": bool(admins),
         "detail": {"admins": len(admins), "external_idps": db.scalar(select(func.count()).select_from(IdpConnection))}},
        {"id": "models", "title": "LLM 공급자 연결", "done": any(p.status == "ok" or p.kind == "mock" for p in providers),
         "detail": {p.id: p.status for p in providers}},
        {"id": "registry", "title": "레지스트리·참조 MCP 서버",
         "done": bool(servers) and all(s.health == "healthy" for s in servers if s.builtin),
         "detail": {s.id: s.health for s in servers}},
        {"id": "observability", "title": "관측·기존 스택 연동", "done": tracing.STATS["db_failed"] == 0,
         "detail": {"otlp": settings.otlp_endpoint or None, "builtin_store": True}},
        {"id": "templates", "title": "템플릿·즉시 사용 확인", "done": (templates or 0) >= 3, "detail": {"templates": templates}},
    ]
    row = db.get(Setting, "setup.completed")
    return {"completed": bool(row and row.value), "ready": all(s["done"] for s in steps), "steps": steps}



# ───────── 키 회전 (D21-③) — 관리 명령으로 수동. 파일 모드만 앱이 회전, env 모드는 Secret 교체 + *_PREVIOUS ─────────
class KeyRotateIn(BaseModel):
    kind: str = Field(pattern="^(signing|encryption)$")
    confirm: str = ""


def _admin_only(p: Principal) -> None:
    if "admin" not in (p.roles or []):
        raise forbidden("키 회전은 플랫폼 관리자만 할 수 있습니다.", code="admin_only")


def _token_grace_s(db: Session) -> int:
    """직전 서명 키 유예 — 서명 토큰(액세스·Access Token·위임) 최대 수명 + 10분. PAT·갱신 토큰은 서명 토큰이 아니다."""
    from app.services import identity
    return max(settings.access_token_ttl, identity.auth_policy(db)["access_token_ttl_min"] * 60, 3600) + 600


def reencrypt(db: Session) -> dict:
    """저장 암호문을 현재(첫) 암호화 키로 — 시크릿·MFA 비밀·로그 원문·A2A 웹훅 토큰. 이전 키로도 못 푸는 행은 failed 로 센다.
    MCP 요청 상태 토큰(최대 STATE_TTL_S)은 저장하지 않으므로 대상이 아니다 — 이전 키 제거는 회전 10분 뒤 권장."""
    from cryptography.fernet import InvalidToken
    from sqlalchemy import true

    from app.models import A2ATask, LogEntry, Secret, User
    f = keys.fernet()
    out = {"secrets": 0, "mfa": 0, "logs": 0, "push_tokens": 0, "failed": 0}

    def _rot(value: str) -> str | None:
        try:
            return f.rotate(value.encode()).decode()
        except InvalidToken:
            out["failed"] += 1
            return None

    def _batches(model, cond):  # 기본키 순 500행씩, 배치마다 커밋·세션 비움 — 큰 로그 테이블도 메모리·잠금이 한 배치만큼 (59th)
        last = None
        while True:
            q = select(model).where(cond).order_by(model.id).limit(500)
            rows = db.scalars(q if last is None else q.where(model.id > last)).all()
            if not rows:
                return
            yield rows
            last = rows[-1].id
            db.commit()
            db.expunge_all()
    for model, col, label in ((Secret, "ciphertext", "secrets"), (User, "mfa_secret", "mfa"), (LogEntry, "raw_enc", "logs")):
        for rows in _batches(model, getattr(model, col).is_not(None)):
            for row in rows:
                if (v := _rot(getattr(row, col))) is not None:
                    setattr(row, col, v)
                    out[label] += 1
    for rows in _batches(A2ATask, true()):  # 클라이언트 웹훅 토큰 push_config.token_enc (D21-⑤) — 빠지면 이전 키 제거 뒤 중계 실패 (59th)
        for row in rows:
            cfg = row.push_config or {}
            if cfg.get("token_enc") and (v := _rot(cfg["token_enc"])) is not None:
                row.push_config = {**cfg, "token_enc": v}
                out["push_tokens"] += 1
    return out


@router.get("/api/v1/admin/keys")
def key_status(_: Principal = Depends(require("admin:read"))):
    return {"env_managed": keys.env_managed(), "signing": {"kid": keys.kid(), "jwks_kids": [k["kid"] for k in keys.jwks()["keys"]]},
            "encryption": {"keys": keys.encryption_key_count()}}


@router.post("/api/v1/admin/keys/rotate")
def rotate_keys(body: KeyRotateIn, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """서명 키: 새 kid, 직전 키는 서명 토큰 최대 수명 동안 JWKS·검증에 유지(공개키는 백업·보고서 검증용으로 영구 보관).
    암호화 키: 새 키로 바꾸고 저장 암호문을 즉시 재암호화, 이전 키는 복호화용으로 남김(재암호화 확인 뒤 reencrypt?drop_previous)."""
    _admin_only(p)
    if body.confirm != "ROTATE":
        raise bad_request("키 회전은 확인란에 ROTATE 를 입력하세요 (MSG-002).", code="confirm_required")
    try:
        out = keys.rotate_signing(_token_grace_s(db)) if body.kind == "signing" else keys.rotate_encryption()
    except keys.EnvManaged:
        raise conflict("키가 env(Secret)로 주입되어 앱이 회전할 수 없습니다 — 새 키를 *_NEXT 로 먼저 배포하고, 현재 키로 올리며 "
                       "이전 키를 *_PREVIOUS 로 옮긴 뒤 재암호화하세요 (SECURITY.md 키 회전).", code="keys_env_managed") from None
    # 회전은 끝났다 — 재암호화가 실패해도 회전 기록은 남긴다 (D22-②)
    audit.append("keys.rotate", actor=p.sub, target=body.kind, data={k: v for k, v in out.items() if k != "previous_valid_until"})
    if body.kind == "encryption":
        re = reencrypt(db)
        audit.append("keys.reencrypt", actor=p.sub, target="encryption", data=re)
        out = {**out, **re}
    return {"kind": body.kind, **out}


@router.post("/api/v1/admin/keys/reencrypt")
def reencrypt_keys(drop_previous: bool = False, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """env 모드 회전 뒤 또는 회전 후 확인용 재암호화 — 실패 0 이고 drop_previous 면 파일의 이전 암호화 키를 지운다."""
    _admin_only(p)
    if keys._configured(settings.encryption_key_next):  # 아직 1단계(다음 키 배포) 파드 — 옛 키로 다시 쓰게 된다 (65th)
        raise conflict("이 파드는 아직 다음 암호화 키(ENCRYPTION_KEY_NEXT)가 남은 1단계입니다 — 2단계 롤아웃을 마친 뒤 재암호화하세요.",
                       code="rotation_in_progress")
    gen = keys._generation()
    out = {**reencrypt(db), "key_fingerprint": keys.encryption_fingerprint()}
    if drop_previous and out["failed"] == 0:
        if keys._generation() != gen:  # 재암호화 중 다른 회전 — 지우면 그 사이 옛 키로 쓴 행을 못 연다 (65th)
            raise conflict("재암호화 중 키가 바뀌었습니다 — 다시 재암호화한 뒤 이전 키를 지우세요.", code="keys_changed")
        out["dropped"] = keys.drop_previous_encryption()
    audit.append("keys.reencrypt", actor=p.sub, target="encryption", data=out)
    return out
