"""첫 실행 마법사 API (SCR-A05~A09, FR-P02·I00·L00·C00·O00·W04).

진입: 일회용 설정 코드(`POST /api/v1/setup/claim` → 설정 전용 토큰) 또는 admin:write 사용자.
진행 상태는 서버(Setting setup.progress)에 저장 — 다시 접속하면 이어서 진행. 관리자 단계 외에는 건너뛸 수 있다.
"""
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import keys
from app.core.auth import ROLE_PERMS, Principal, _user_principal, authenticate_token, bearer, require
from app.core.errors import ApiError, bad_request, conflict
from app.core.http import sync_client
from app.core.ids import new_id, slugify
from app.core.config import settings
from app.db import SessionLocal, get_db, utcnow
from app.models import (AgentProfile, IdpConnection, McpServer, Model, Provider, RegistrySource, RoutingRule, Setting,
                        User)
from app.services import audit, deploy, guards, retention, secrets, totp
from app.services import setup as S

router = APIRouter(prefix="/api/v1/setup", tags=["admin"])
LOCAL_KINDS = ("mock", "ollama", "vllm")
ROLES = set(ROLE_PERMS)


@dataclass
class Actor:
    sub: str
    via: str  # code | admin
    principal: Principal | None = None


def actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    token = bearer(request)
    if not token:
        raise ApiError(401, "unauthorized", "설정 코드로 시작하거나 관리자로 로그인하세요.")
    if S.verify_token(token):
        if S.completed(db):
            raise ApiError(403, "setup_completed", "첫 실행이 끝났습니다 — 관리자로 로그인하세요.")
        return Actor("setup", "code")
    p = authenticate_token(db, token)
    if not p.has("admin:write"):
        raise ApiError(403, "forbidden", "첫 실행 설정은 플랫폼 운영자(관리자)만 할 수 있습니다.")
    return Actor(p.sub, "admin", p)


def _run_principal(db: Session, a: Actor) -> Principal:
    """자가 시험 실행 주체 — 관리자 로그인이면 그 사람, 설정 코드면 첫 실행에서 만든 관리자."""
    if a.principal is not None:
        return a.principal
    u = db.scalars(select(User).where(User.status == "active")).all()
    admin = next((x for x in u if "admin" in (x.roles or [])), None)
    if admin is None:
        raise bad_request("먼저 ① 관리자 단계를 마치세요.", code="identity_required")
    return _user_principal(admin, "")


# ── 진입 ──
@router.get("/state")
def state(db: Session = Depends(get_db)):
    """공개 — 진입 화면용 (민감 정보 없음)."""
    pr = S.progress(db)
    return {"completed": S.completed(db), "requires_code": True, "airgap": deploy.airgap(db), "current": pr["current"],
            "steps": [{"id": s, "status": pr["steps"][s]["status"]} for s in S.STEPS], "env": settings.app_env}


class ClaimIn(BaseModel):
    code: str = Field(min_length=4, max_length=20)


@router.post("/claim")
def claim(body: ClaimIn, db: Session = Depends(get_db)):
    try:
        out = S.claim(db, body.code)
    except S.SetupError as exc:
        audit.append("setup.claim", actor="setup", decision="deny", data={"code": exc.code})
        raise ApiError(400 if exc.code != "code_locked" else 429, exc.code, exc.message) from exc
    audit.append("setup.claim", actor="setup", decision="allow")
    return out


@router.post("/code")
def reissue(p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    """설정 코드 재발급 (CLI `nexus install code` 와 같음) — 평문은 이 응답에만."""
    if S.completed(db):
        raise bad_request("첫 실행이 이미 끝났습니다.", code="setup_completed")
    code, exp = S.new_code(db, p.sub)
    audit.append("setup.code.issue", actor=p.sub)
    return {"code": code, "expires_at": exp}


@router.get("/progress")
def get_progress(_: Actor = Depends(actor), db: Session = Depends(get_db)):
    return S.progress(db)


@router.post("/{step}/skip")
def skip(step: str, a: Actor = Depends(actor), db: Session = Depends(get_db)):
    if step not in S.SKIPPABLE:
        raise bad_request("관리자 단계는 건너뛸 수 없습니다." if step == "identity" else "알 수 없는 단계", code="not_skippable")
    return S.mark(db, step, "skipped", {}, a.sub)


# ── ① 관리자·IdP (SCR-A05) ──
@router.post("/mfa")
def mfa_enroll(account: str = "nexus-admin", _: Actor = Depends(actor)):
    secret = totp.new_secret()
    return {"secret": secret, "otpauth": totp.uri(secret, account)}


class AdminIn(BaseModel):
    username: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,40}$")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str


class MfaIn(BaseModel):
    secret: str
    code: str


class IdpSetupIn(BaseModel):
    kind: str = Field(pattern="^(entra|okta|keycloak|oidc)$")
    name: str
    issuer: str
    internal_url: str | None = None
    client_id: str
    client_secret: str | None = None
    groups_claim: str = "groups"
    mapping: dict[str, str]


class IdentityIn(BaseModel):
    admin: AdminIn
    mfa: MfaIn
    idp: IdpSetupIn | None = None


@router.post("/identity")
def identity(body: IdentityIn, a: Actor = Depends(actor), db: Session = Depends(get_db)):
    problems = S.password_problems(body.admin.password, body.admin.username)
    if problems:
        raise ApiError(400, "weak_password", "비밀번호 정책: " + " · ".join(problems), extra={"problems": problems})
    if not totp.verify(body.mfa.secret, body.mfa.code):
        raise bad_request("MFA 확인 코드가 올바르지 않습니다 — 인증 앱의 현재 6자리를 입력하세요.", code="mfa_invalid")
    if body.idp:
        bad = [r for r in body.idp.mapping.values() if r not in ROLES]
        if bad:
            raise bad_request(f"알 수 없는 역할: {bad}", code="invalid_role")
        if not set(body.idp.mapping.values()) & {"admin", "operator"}:
            raise bad_request("관리자(admin·operator)로 매핑되는 IdP 그룹이 최소 1개 필요합니다.", code="admin_group_required")
        if db.scalars(select(IdpConnection).where(IdpConnection.issuer == body.idp.issuer)).first():
            raise conflict("이미 연동된 issuer 입니다.")
    from app.core import security
    user = db.scalars(select(User).where(User.username == body.admin.username)).first()
    if user is not None and "admin" not in (user.roles or []):
        raise conflict("같은 이름의 일반 사용자가 있습니다 — 다른 관리자 ID 를 쓰세요.")
    if user is None:
        user = User(id=new_id("usr"), sub=f"usr_{slugify(body.admin.username)}", issuer=security.issuer(),
                    username=body.admin.username, name=body.admin.username, roles=["admin"], team="team-platform")
        db.add(user)
    user.email, user.status = body.admin.email, "active"
    user.password_hash = security.hash_password(body.admin.password)
    user.mfa_enabled, user.mfa_secret = True, keys.fernet().encrypt(body.mfa.secret.encode()).decode()
    idp_name = None
    if body.idp:
        idp = IdpConnection(id=new_id("idp"), name=body.idp.name, kind=body.idp.kind, issuer=body.idp.issuer,
                            internal_url=body.idp.internal_url, client_id=body.idp.client_id, audience=body.idp.client_id,
                            groups_claim=body.idp.groups_claim, role_mapping=body.idp.mapping, enabled=True,
                            login_enabled=False)  # 시험 로그인(J01·J02) 성공 후 로그인에 노출
        if body.idp.client_secret:
            idp.client_secret_ref = f"idp/{idp.id}/client_secret"
            secrets.put(db, idp.client_secret_ref, body.idp.client_secret, kind="oauth_client")
        db.add(idp)
        idp_name = idp.name
    db.commit()
    audit.append("setup.identity", actor=a.sub, target=user.sub, data={"mfa": True, "idp": idp_name})
    return S.mark(db, "identity", "done", {"admin": user.username, "mfa": True, "idp": idp_name}, a.sub)


class IdpDiscoverIn(BaseModel):
    issuer: str
    internal_url: str | None = None


@router.post("/idp/discover")
def idp_discover(body: IdpDiscoverIn, _: Actor = Depends(actor)):
    """① 외부 IdP 연결 시험 — 디스커버리·JWKS·PKCE S256 (SCR-J02 ② 와 같은 점검)."""
    from app.api.identity import _discover_check
    from app.services import broker
    return {**_discover_check(body.issuer, body.internal_url), "redirect_uri": broker.callback_url()}


# ── ② LLM 공급자 (SCR-A06) ──
DEFAULT_ALIASES = {"chat-default": "fast-commercial", "internal-data": "domain-local", "summarize": "domain-local",
                   "planner": "smart-commercial"}


def _probe(base: str | None) -> dict:
    if not base:
        return {"ok": False, "note": "주소 없음"}
    t0 = time.perf_counter()
    try:
        with sync_client(timeout=1.5) as http:
            res = http.get(base.rstrip("/") + "/models")
        return {"ok": res.status_code < 500, "latency_ms": round((time.perf_counter() - t0) * 1000), "status": res.status_code}
    except Exception as exc:  # noqa: BLE001 — 미기동 로컬 모델
        return {"ok": False, "note": f"응답 없음 ({type(exc).__name__})"}


@router.get("/models")
def models_get(_: Actor = Depends(actor), db: Session = Depends(get_db)):
    gap = deploy.airgap(db)
    rows = []
    for pv in db.scalars(select(Provider).order_by(Provider.id)):
        local = pv.kind in LOCAL_KINDS
        detect = {"ok": True, "note": "내장"} if pv.kind == "mock" else (_probe(pv.base_url) if local else None)
        rows.append({"id": pv.id, "name": pv.name, "kind": pv.kind, "category": "local" if local else "commercial",
                     "enabled": pv.enabled, "status": pv.status, "has_key": bool(pv.secret_ref and secrets.get_plain(db, pv.secret_ref)),
                     "detected": detect, "disabled_reason": "에어갭 모드 — 상용 공급자 비활성 (ST-09)" if gap and not local else None,
                     "models": [m.id for m in db.scalars(select(Model).where(Model.provider_id == pv.id))]})
    aliases = {r.match_model: (r.targets or [{}])[0].get("model") for r in db.scalars(select(RoutingRule))
               if r.name.startswith("alias:")}
    models = [{"id": m.id, "category": m.category, "provider": m.provider_id} for m in db.scalars(select(Model)
                                                                                               .order_by(Model.id))]
    return {"airgap": gap, "providers": rows, "aliases": {**DEFAULT_ALIASES, **aliases}, "configured": sorted(aliases),
            "models": models, "endpoint": "POST /v1/chat/completions (OpenAI 호환) · model: <별칭>"}


class ProviderTestIn(BaseModel):
    provider_id: str


@router.post("/models/test")
async def models_test(body: ProviderTestIn, _: Actor = Depends(actor)):
    """연결 시험 — 짧은 프롬프트 1회 (I02 와 같은 시험, 키는 저장된 시크릿으로)."""
    from app.api.llm import test_provider
    return await test_provider(body.provider_id, None, None)


class ProviderSet(BaseModel):
    id: str
    enabled: bool
    api_key: str | None = Field(None, repr=False)


class ModelsIn(BaseModel):
    providers: list[ProviderSet] = []
    aliases: dict[str, str] = {}


@router.post("/models")
def models_set(body: ModelsIn, a: Actor = Depends(actor), db: Session = Depends(get_db)):
    gap = deploy.airgap(db)
    for ps in body.providers:
        pv = db.get(Provider, ps.id)
        if pv is None:
            raise bad_request(f"알 수 없는 공급자: {ps.id}")
        commercial = pv.kind not in LOCAL_KINDS
        if commercial and ps.enabled and gap:
            raise bad_request(f"에어갭 모드에서는 상용 공급자({pv.name})를 켤 수 없습니다 (ST-09).", code="airgap")
        if ps.api_key:
            pv.secret_ref = pv.secret_ref or f"provider/{pv.id}/api_key"
            secrets.put(db, pv.secret_ref, ps.api_key, kind="api_key")  # 게이트웨이 시크릿에만 (MSG-018)
        pv.enabled = ps.enabled
    for alias, target in body.aliases.items():
        m = db.get(Model, target)
        if m is None:
            raise bad_request(f"별칭 {alias}: 알 수 없는 모델 {target}", code="invalid_alias")
        if alias == "internal-data" and m.category not in ("local", "domain"):
            raise bad_request("internal-data 별칭은 로컬·도메인 모델만 가리킬 수 있습니다 (MSG-011).", code="internal_data_local_only")
        row = db.scalars(select(RoutingRule).where(RoutingRule.name == f"alias:{alias}")).first()
        if row is None:
            row = RoutingRule(id=new_id("rr"), name=f"alias:{alias}", match_model=alias, strategy="priority", priority=10)
            db.add(row)
        row.targets, row.retries = [{"model": target}], 1
        row.fallbacks = [] if alias == "internal-data" else ["domain-local"]
        row.match_data_class = None
    db.commit()
    local_on = len(db.scalars(select(Provider).where(Provider.enabled.is_(True), Provider.kind.in_(LOCAL_KINDS))).all())
    com_on = len(db.scalars(select(Provider).where(Provider.enabled.is_(True), Provider.kind.notin_(LOCAL_KINDS))).all())
    audit.append("setup.models", actor=a.sub, data={"aliases": body.aliases, "providers": [p.id for p in body.providers]})
    return S.mark(db, "models", "done", {"local": local_on, "commercial": com_on, "aliases": body.aliases}, a.sub)


# ── ③ 레지스트리·참조 MCP 서버 (SCR-A07) ──
@router.get("/registry")
def registry_get(_: Actor = Depends(actor), db: Session = Depends(get_db)):
    src = list(db.scalars(select(RegistrySource).order_by(RegistrySource.name)))
    servers = [{"id": s.id, "name": s.name, "status": s.status, "health": s.health, "tools": len(s.tools or []),
                "auth": (s.auth or {}).get("type", "none"), "trust": "official"}
               for s in db.scalars(select(McpServer).where(McpServer.builtin.is_(True)).order_by(McpServer.id))]
    official = next((x for x in src if x.kind == "official"), None)
    return {"airgap": deploy.airgap(db), "mode": "self-hosted",
            "sources": [{"id": x.id, "name": x.name, "kind": x.kind, "enabled": x.enabled} for x in src],
            "approval": "official_auto" if official and official.auto_approve else "all_pending", "servers": servers}


class RegistryIn(BaseModel):
    mirror_docker: bool = False
    approval: str = Field("official_auto", pattern="^(official_auto|all_pending)$")
    servers: dict[str, bool] = {}


@router.post("/registry")
def registry_set(body: RegistryIn, a: Actor = Depends(actor), db: Session = Depends(get_db)):
    gap = deploy.airgap(db)
    if body.mirror_docker and gap:
        raise bad_request("에어갭 모드에서는 외부 레지스트리 동기화를 켤 수 없습니다 (ST-09).", code="airgap")
    for x in db.scalars(select(RegistrySource)):
        if x.kind == "docker":
            x.enabled = body.mirror_docker
        if x.kind == "official":
            x.auto_approve = body.approval == "official_auto"
            x.enabled = x.enabled and not gap
    for sid, on in body.servers.items():
        s = db.get(McpServer, sid)
        if s is None or not s.builtin:
            raise bad_request(f"참조 서버가 아닙니다: {sid}")
        s.status = "active" if on else "disabled"
    db.commit()
    audit.append("setup.registry", actor=a.sub, data=body.model_dump())
    on = [k for k, v in body.servers.items() if v]
    return S.mark(db, "registry", "done", {"mirror_docker": body.mirror_docker, "approval": body.approval, "servers": on},
                  a.sub)


# ── ④ 관측·기존 스택 (SCR-A08) ──
@router.get("/observability")
def observability_get(_: Actor = Depends(actor), db: Session = Depends(get_db)):
    sec = guards.security_settings(db)
    saved = (db.get(Setting, "observe.stack").value if db.get(Setting, "observe.stack") else {}) or {}
    return {"mode": saved.get("mode", "builtin"), "otlp_endpoint": saved.get("otlp_endpoint") or settings.otlp_endpoint or None,
            "grafana_url": saved.get("grafana_url"), "jaeger_url": saved.get("jaeger_url"),
            "retention": retention.policy(db), "pii_masking": sec["pii_input"] != "allow" and sec["pii_output"] != "allow",
            "components": {"collector": True, "trace_store": True, "metrics": True, "usage_cost": True},
            "instrumentation": ["게이트웨이 — LLM·MCP·A2A 스팬", "호스트 — Planner·하위 에이전트", "MCP 서버 — 도구 실행",
                                "W3C Trace Context 전 구간 전파"],
            "license_note": "Grafana(AGPL-3.0)는 기본 설치에 넣지 않고 외부 연동만 (MSG-023)"}


class RetentionIn(BaseModel):
    spans_days: int = Field(14, ge=1, le=3650)
    logs_days: int = Field(30, ge=1, le=3650)
    audit_days: int = Field(365, ge=365, le=3650)  # 감사는 불변·최소 1년


class ObservabilityIn(BaseModel):
    mode: str = Field("builtin", pattern="^(builtin|external)$")
    otlp_endpoint: str | None = None
    grafana_url: str | None = None
    jaeger_url: str | None = None
    retention: RetentionIn = RetentionIn()
    pii_masking: bool = True


@router.post("/observability")
def observability_set(body: ObservabilityIn, a: Actor = Depends(actor), db: Session = Depends(get_db)):
    if not body.pii_masking and not (a.principal and a.principal.has("policy:write") and "security" in a.principal.roles):
        raise bad_request("PII 마스킹을 끄려면 보안·IAM 담당이 Security 설정(H05)에서 승인해야 합니다 (MSG-021).",
                          code="security_approval_required")
    if body.mode == "external" and not body.otlp_endpoint:
        raise bad_request("기존 스택 연동에는 OTLP 엔드포인트가 필요합니다.", code="otlp_required")
    cur = retention.policy(db)
    pol = {**cur, **body.retention.model_dump()}
    row = db.get(Setting, "retention")
    if row is None:
        db.add(Setting(key="retention", value=pol, updated_by=a.sub))
    else:
        row.value = pol
    stack = body.model_dump(exclude={"retention", "pii_masking"})
    srow = db.get(Setting, "observe.stack")
    if srow is None:
        db.add(Setting(key="observe.stack", value=stack, updated_by=a.sub))
    else:
        srow.value = stack
    db.commit()
    audit.append("setup.observability", actor=a.sub, data={"mode": body.mode, "retention": pol})
    return S.mark(db, "observability", "done", {"mode": body.mode, "retention": pol, "pii_masking": body.pii_masking,
                                                "note": "외부 OTLP 내보내기는 OTLP_ENDPOINT 로 재기동 시 적용"}, a.sub)


class OtlpTestIn(BaseModel):
    otlp_endpoint: str


@router.post("/observability/test")
def observability_test(body: OtlpTestIn, _: Actor = Depends(actor)):
    """OTLP/HTTP 수신 시험 — 빈 내보내기 요청에 2xx 면 통과. 실패 시 원인 표시 (SCR-A08 ⑥)."""
    url = body.otlp_endpoint.rstrip("/") + ("" if body.otlp_endpoint.rstrip("/").endswith("/v1/traces") else "/v1/traces")
    try:
        with sync_client(timeout=3) as http:
            res = http.post(url, json={"resourceSpans": []})
        return {"ok": res.status_code < 300, "status": res.status_code, "url": url,
                "error": None if res.status_code < 300 else res.text[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": None, "url": url, "error": f"{type(exc).__name__}: {exc}"[:200]}


# ── ⑤ 템플릿·즉시 사용 확인 (SCR-A09) ──
@router.get("/templates")
def templates_get(_: Actor = Depends(actor), db: Session = Depends(get_db)):
    return {"items": S.readiness(db), "target_seconds": 60}


class TemplateTestIn(BaseModel):
    template: str = Field(pattern="^tpl-[a-z-]+$")


@router.post("/templates/test")
async def templates_test(body: TemplateTestIn, a: Actor = Depends(actor)):
    def _p():
        with SessionLocal() as db:
            if db.get(AgentProfile, body.template) is None:
                raise bad_request("알 수 없는 템플릿")
            return _run_principal(db, a)
    principal = await run_in_threadpool(_p)
    return await S.self_test(principal, body.template)


class TemplatesIn(BaseModel):
    templates: list[str]
    self_test: dict | None = None


@router.post("/templates")
def templates_set(body: TemplatesIn, a: Actor = Depends(actor), db: Session = Depends(get_db)):
    from app.services.profiles import sync_catalog
    for pid in body.templates:
        pr = db.get(AgentProfile, pid)
        if pr is None or not pr.template:
            raise bad_request(f"템플릿이 아닙니다: {pid}")
        pr.status = "published"
        sync_catalog(db, pr, a.sub)
    db.commit()
    return S.mark(db, "templates", "done", {"templates": body.templates, "self_test": body.self_test}, a.sub)


# ── 완료 ──
@router.post("/complete")
def complete(a: Actor = Depends(actor), db: Session = Depends(get_db)):
    pr = S.progress(db)
    if a.via == "code" and pr["steps"]["identity"]["status"] != "done":
        raise bad_request("① 관리자 단계를 먼저 마치세요.", code="identity_required")
    row = db.get(Setting, "setup.completed")
    if row is None:
        db.add(Setting(key="setup.completed", value=True, updated_by=a.sub))
    else:
        row.value = True
    code = db.get(Setting, "setup.code")
    if code is not None and isinstance(code.value, dict):
        code.value = {**code.value, "used": True}
    db.commit()
    audit.append("setup.complete", actor=a.sub, data={s: pr["steps"][s]["status"] for s in S.STEPS})
    return {"completed": True, "summary": pr, "at": utcnow().isoformat()}
