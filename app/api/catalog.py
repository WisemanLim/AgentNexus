"""통합 카탈로그 · MCP Registry API (FR-P01, C00~C03, P04 · SCR-E01~E08).

PRD 9.2: GET /v0/servers · GET /v0/servers/{id} · POST /v0/publish · GET /v1/catalog?type= ·
POST /v1/catalog/{id}/register · POST /v1/catalog/{id}/approve
"""
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, optional_principal, require
from app.core.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.core.ids import new_id
from app.db import SessionLocal, get_db, utcnow
from app.models import AgentProfile, AuditEvent, CatalogItem, CatalogVersion, RegistrySource, Setting
from app.services import audit, mcp_gateway, ratelimit, registry, review

router = APIRouter(tags=["catalog"])


def item_out(i: CatalogItem, detail: bool = False) -> dict:
    out = {"id": i.id, "type": i.type, "name": i.name, "display_name": i.display_name or i.name,
           "description": i.description, "source": i.source, "status": i.status, "version": i.version,
           "license": i.license, "license_risk": registry.license_risk(i.license), "tags": i.tags,
           "auth_scheme": i.auth_scheme, "owner": i.owner, "instance_id": i.ref_id,
           "signature_verified": i.signature_verified, "pinned_version": i.pinned_version,
           "trust": ((i.item_metadata or {}).get("_anx_review") or {}).get("trust"),
           "created_at": i.created_at, "updated_at": i.updated_at}
    if detail:
        out["metadata"] = i.item_metadata
    return out


def _get_item(db: Session, item_id: str) -> CatalogItem:
    item = db.get(CatalogItem, item_id)
    if item is None:
        raise not_found("catalog item")
    return item


def _visible_statuses(p: Principal | None) -> tuple[str, ...] | None:
    return None if p and p.has("catalog:approve") else registry.DISCOVERABLE


# ───────── /v1/catalog ─────────
@router.get("/v1/catalog")
def list_catalog(p: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db),
                 type: str | None = Query(None, pattern="^(mcp_server|a2a_agent|model|agent_profile)$"),
                 q: str | None = None, status: str | None = None, tag: str | None = None, source: str | None = None,
                 auth: str | None = None, trust: str | None = None,
                 limit: int = Query(50, le=500), offset: int = 0):
    statuses = _visible_statuses(p)
    if status:
        wanted = tuple(status.split(","))
        statuses = tuple(s for s in wanted if statuses is None or s in statuses)
    items, total = registry.search(db, q=q, type_=type, statuses=statuses, tag=tag, source=source, auth=auth,
                                   trust=trust, limit=limit, offset=offset)
    counts: dict[str, int] = {}
    for t in registry.CATALOG_TYPES:  # 유형 탭 건수 — 같은 검색 조건(유형 제외)
        counts[t] = registry.search(db, q=q, type_=t, statuses=statuses, tag=tag, source=source, auth=auth,
                                    trust=trust, limit=10_000)[1]
    terms = registry.parse_query(q)[0]
    return {"items": [item_out(i) | {"match": registry.match_hint(i, terms) if terms else None} for i in items],
            "total": total, "counts": counts}


@router.get("/v1/catalog/exposures")
def recent_exposures(_: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db)):
    """등록 → 노출 현황 (E01 ⑥) — 최근 승인·등록과 승인 후 카탈로그 노출까지 걸린 시간(목표 ≤60s), 승인 대기."""
    rows = []
    for v in db.scalars(select(CatalogVersion).where(CatalogVersion.status.in_(("approved", "pending")))
                        .order_by(CatalogVersion.created_at.desc()).limit(10)):
        item = db.get(CatalogItem, v.item_id)
        if item is None:
            continue
        lag = (item.updated_at - v.decided_at).total_seconds() if v.decided_at and item.updated_at else None
        rows.append({"item_id": item.id, "name": item.display_name or item.name, "type": item.type,
                     "version": v.version, "status": v.status, "decided_at": v.decided_at,
                     "exposed_after_s": max(0.0, round(lag, 1)) if lag is not None else None})
    return {"items": rows}


class CatalogIn(BaseModel):
    type: str = Field(pattern="^(mcp_server|a2a_agent|model|agent_profile)$")
    name: str = Field(min_length=2, max_length=255)
    version: str = "0.1.0"
    display_name: str | None = None
    description: str | None = None
    metadata: dict = {}
    tags: list[str] = []
    license: str | None = None
    auth_scheme: str | None = None


@router.post("/v1/catalog", status_code=201)
def create_catalog_item(body: CatalogIn, p: Principal = Depends(require("registry:publish")),
                        db: Session = Depends(get_db)):
    if registry.find_item(db, body.type, body.name):
        raise conflict("같은 이름의 항목이 이미 있습니다. 새 버전은 /v0/publish 또는 versions 로 게시하세요.")
    item, _, _ = registry.publish(db, body.type, body.name, body.version, body.metadata, p.sub, source="manual",
                                  tags=body.tags, license=body.license, display_name=body.display_name,
                                  description=body.description, auth_scheme=body.auth_scheme)
    return item_out(item, detail=True)


@router.get("/v1/catalog/sbom")
def catalog_sbom(_: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db),
                 format: str = Query("cyclonedx", pattern="^(cyclonedx|summary)$")):
    bom = registry.sbom(db)
    if format == "summary":
        rows = [{"name": c["name"], "version": c.get("version"),
                 "license": (c.get("licenses") or [{}])[0].get("license", {}).get("name"),
                 "risk": next(x["value"] for x in c["properties"] if x["name"] == "agentnexus:license_risk"),
                 "scope": next(x["value"] for x in c["properties"] if x["name"] == "agentnexus:scope")}
                for c in bom["components"]]
        return {"generated_at": bom["metadata"]["timestamp"], "components": rows,
                "risk_counts": {x["name"].split(":")[-1]: int(x["value"]) for x in bom["metadata"]["properties"]}}
    return bom


@router.get("/v1/catalog/{item_id}")
def get_catalog_item(item_id: str, p: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db)):
    item = _get_item(db, item_id)
    statuses = _visible_statuses(p)
    if statuses and item.status not in statuses:
        raise not_found("catalog item")
    return item_out(item, detail=True)


class CatalogPatch(BaseModel):
    display_name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    license: str | None = None
    status: str | None = Field(None, pattern="^(deprecated|approved)$")


@router.patch("/v1/catalog/{item_id}")
def patch_catalog_item(item_id: str, body: CatalogPatch, p: Principal = Depends(require("catalog:write")),
                       db: Session = Depends(get_db)):
    item = _get_item(db, item_id)
    changes = body.model_dump(exclude_none=True)
    if changes.get("status") == "approved" and item.status != "deprecated":
        raise bad_request("approved 로의 변경은 deprecated 복원에만 쓸 수 있습니다.")
    for k, v in changes.items():
        setattr(item, k, v)
    db.commit()
    audit.append("catalog.update", actor=p.sub, target=f"{item.type}:{item.name}", data=changes)
    return item_out(item, detail=True)


@router.get("/v1/catalog/{item_id}/versions")
def catalog_versions(item_id: str, _: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db)):
    item = _get_item(db, item_id)
    rows = db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id)
                      .order_by(CatalogVersion.created_at.desc()))
    return {"item_id": item.id, "current": item.version,
            "items": [{"id": v.id, "version": v.version, "status": v.status, "signature_verified": v.signature_verified,
                       "created_by": v.created_by, "created_at": v.created_at, "decided_by": v.decided_by,
                       "decided_at": v.decided_at, "note": v.note, "metadata": v.item_metadata} for v in rows]}


class DecisionIn(BaseModel):
    version: str | None = None
    note: str | None = None


class ApproveIn(DecisionIn):
    """E05 ⑤⑥⑦ — 도구 위험도 확정·신뢰 등급·허용목록(기본 해제 = 호출 거부 유지)."""
    trust: str | None = Field(None, pattern="^(official|partner|community|external)$")
    tool_risks: dict[str, str] | None = None
    allowlist_group: str | None = None


@router.post("/v1/catalog/{item_id}/approve")
def approve_item(item_id: str, body: ApproveIn = Body(default_factory=ApproveIn),
                 p: Principal = Depends(require("catalog:approve")), db: Session = Depends(get_db)):
    item = _get_item(db, item_id)
    ver = registry._version(db, item, body.version)
    row = review.queue_row(db, item, ver)
    if not row["approvable"]:
        fails = [c["detail"] for c in row["checks"] if c["level"] == "fail"]
        raise bad_request(f"점검 실패 — 승인할 수 없습니다 (MSG-007): {'; '.join(fails)}", code="checks_failed")
    if body.tool_risks and any(r not in ("low", "medium", "high") for r in body.tool_risks.values()):
        raise bad_request("tool_risks 값은 low|medium|high")
    if body.trust in ("official", "partner") and item.type == "a2a_agent" and not \
            any(c["key"] == "signature" and "✓" in c["detail"] for c in row["checks"]) and not (body.note or "").strip():
        raise bad_request("서명 없는 카드에 partner 이상 등급을 주려면 사유(note)가 필요합니다.", code="reason_required")
    registry.approve(db, item, ver.version, p.sub, body.note)
    eff = review.apply_approval(db, item, ver, p.sub, trust=body.trust, tool_risks=body.tool_risks,
                                allowlist_group=body.allowlist_group)
    return {**item_out(item, detail=True), "effects": eff["effects"]}


@router.get("/v1/catalog/{item_id}/diff")
def catalog_diff(item_id: str, a: str = Query(..., alias="from"), b: str = Query(..., alias="to"),
                 _: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db)):
    """두 버전 메타데이터 비교 (SCR-E07 ③④) — 새 고위험 도구가 있으면 재승인 필요."""
    item = _get_item(db, item_id)
    va, vb = registry._version(db, item, a), registry._version(db, item, b)
    return {"from": a, "to": b, **review.diff(item.type, va.item_metadata, vb.item_metadata)}


class PinIn(BaseModel):
    version: str | None = None  # None = 최신 승인 버전 자동


@router.put("/v1/catalog/{item_id}/pin")
def catalog_pin(item_id: str, body: PinIn, p: Principal = Depends(require("catalog:approve")), db: Session = Depends(get_db)):
    item = _get_item(db, item_id)
    if body.version:
        v = registry._version(db, item, body.version)
        if v.status not in ("approved", "superseded"):
            raise bad_request("승인된 버전만 고정할 수 있습니다.", code="not_approved")
    item.pinned_version = body.version
    db.commit()
    audit.append("registry.pin", actor=p.sub, target=f"{item.type}:{item.name}", data={"version": body.version})
    return {**item_out(item), "affected_profiles": len(_usage(db, item))}


class RollbackIn(BaseModel):
    version: str
    confirm: bool = False


@router.post("/v1/catalog/{item_id}/rollback")
async def catalog_rollback(item_id: str, body: RollbackIn, p: Principal = Depends(require("catalog:approve"))):
    """이전 버전으로 되돌림 (SCR-E07 ⑥, MSG-002) — 그 버전을 다시 승인 상태로, MCP 인스턴스는 도구 목록 즉시 갱신."""
    if not body.confirm:
        raise bad_request("롤백은 확인이 필요합니다 (confirm: true, MSG-002).", code="confirm_required")

    def _do():
        with SessionLocal() as db:
            item = _get_item(db, item_id)
            v = registry._version(db, item, body.version)
            if v.status not in ("approved", "superseded"):
                raise bad_request("승인된 적이 있는 버전으로만 롤백할 수 있습니다.", code="not_approved")
            for o in db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id,
                                                             CatalogVersion.status == "approved")):
                o.status = "superseded"
            v.status = "approved"
            registry._apply_version(item, v)
            item.pinned_version = None if item.pinned_version and item.pinned_version != v.version else item.pinned_version
            db.commit()
            audit.append("registry.rollback", actor=p.sub, target=f"{item.type}:{item.name}@{v.version}")
            return item_out(item, detail=True), item.type, item.ref_id, len(_usage(db, item))
    out, kind, ref, n = await run_in_threadpool(_do)
    if kind == "mcp_server" and ref:
        out["refresh"] = await mcp_gateway.refresh_tools(ref, p)
    return {**out, "affected_profiles": n}


def _usage(db: Session, item: CatalogItem) -> list[dict]:
    """사용처 (SCR-E02) — 이 항목을 쓰는 에이전트 프로파일."""
    ref = item.ref_id or item.name
    out = []
    for pr in db.scalars(select(AgentProfile).where(AgentProfile.status != "archived")):
        uses = (item.type == "mcp_server" and any(str(t).split(".")[0] == ref for t in pr.tools or [])) or \
            (item.type == "a2a_agent" and ref in (pr.remote_agents or [])) or (item.type == "model" and ref in (pr.models or []))
        if uses:
            out.append({"id": pr.id, "name": pr.name, "status": pr.status})
    return out


@router.get("/v1/catalog/{item_id}/usage")
def catalog_usage(item_id: str, _: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db)):
    item = _get_item(db, item_id)
    newer = db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id, CatalogVersion.status == "pending")
                       .order_by(CatalogVersion.created_at.desc())).first()
    return {"items": _usage(db, item), "pending_version": newer.version if newer else None}


@router.get("/v1/catalog/{item_id}/history")
def catalog_history(item_id: str, _: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db)):
    """변경 이력 (SCR-E07 ⑦) — 게시·승인·고정·롤백·반려, 불변 감사 로그에서."""
    item = _get_item(db, item_id)
    prefix = f"{item.type}:{item.name}"
    rows = db.scalars(select(AuditEvent).where(AuditEvent.action.like("registry.%"), AuditEvent.target.like(f"{prefix}%"))
                      .order_by(AuditEvent.seq.desc()).limit(100))
    return {"items": [{"ts": e.ts, "actor": e.actor, "action": e.action, "target": e.target, "seq": e.seq,
                       "hash": e.hash[:12]} for e in rows]}


@router.post("/v1/catalog/{item_id}/reject")
def reject_item(item_id: str, body: DecisionIn = Body(default_factory=DecisionIn),
                p: Principal = Depends(require("catalog:approve")), db: Session = Depends(get_db)):
    if not (body.note or "").strip():
        raise bad_request("거부 사유를 입력하세요 (MSG-003) — 게시자에게 전달되고 감사에 남습니다.", code="reason_required")
    return item_out(registry.reject(db, _get_item(db, item_id), body.version, p.sub, body.note), detail=True)


def _check_reregister(db: Session, item: CatalogItem, p: Principal, options: dict) -> None:
    """이미 있는 MCP 인스턴스에 다시 등록 — 엔드포인트·인증 전송처가 바뀌면 저장된 시크릿이 새 주소로 간다.
    PATCH 와 같은 규칙(시크릿 관리 권한)으로 (D19-④ — `catalog:write` 만 있는 토큰의 시크릿 전송처 변경 차단)."""
    from app.models import McpServer
    from app.services import secrets
    srv = db.get(McpServer, options.get("namespace") or registry.namespace_of(item.name))
    if srv is None:
        return
    meta = item.item_metadata or {}
    remotes = meta.get("remotes") or []
    endpoint = options.get("endpoint") or (remotes[0]["url"] if remotes else None)
    new_auth = dict((meta.get("_meta") or {}).get("auth") or {}) or dict(srv.auth or {})
    secrets.check_destination(p, {**(srv.auth or {}), **new_auth},
                              endpoint != srv.endpoint or secrets.destination_changed(srv.auth, new_auth), False)


@router.post("/v1/catalog/{item_id}/register")
async def register_item(item_id: str, options: dict = Body(default_factory=dict),
                        p: Principal = Depends(require("catalog:write"))):
    """즉시 사용 (FR-W04) — 인스턴스 생성 후 MCP 서버면 인증 발견·도구 조회·기본 규칙까지 적용."""
    def _do():
        with SessionLocal() as db:
            item = _get_item(db, item_id)
            if item.type == "mcp_server":
                _check_reregister(db, item, p, options)
            instance = registry.register(db, item, p.sub, options)
            return item_out(item), instance, item.type
    out, instance, kind = await run_in_threadpool(_do)
    if kind == "mcp_server":
        instance["refresh"] = await mcp_gateway.refresh_tools(instance["id"], p)
        instance["review_applied"] = await run_in_threadpool(_apply_review_risks, item_id, instance["id"])
    return {"item": out, "instance": instance}


# ───────── 즉시 사용 위저드 (SCR-E03) — ① 등록(/register) → ② 인증·시험 교환 → ③ 정책 기본값 → ④ 60초 노출 확인 ─────────
def _instance(db: Session, item_id: str) -> tuple[CatalogItem, str, str, str]:
    """(항목, 인스턴스 id, 교환 audience, 게이트웨이 대상 패턴)."""
    item = _get_item(db, item_id)
    if item.status != "active" or not item.ref_id:
        raise conflict("먼저 ① 선택 단계에서 등록하세요 (POST /v1/catalog/{id}/register).", code="not_registered")
    if item.type == "mcp_server":
        return item, item.ref_id, f"mcp:{item.ref_id}", f"{item.ref_id}.*"
    if item.type == "a2a_agent":
        return item, item.ref_id, f"a2a:{item.ref_id}", item.ref_id
    raise bad_request(f"{item.type} 는 즉시 사용 위저드 대상이 아닙니다.")


class InstantAuthIn(BaseModel):
    scopes: list[str] = []
    ttl_seconds: int = Field(300, ge=60, le=900)  # 교환 토큰 최대 15분 (SCR-J04)


@router.post("/v1/catalog/{item_id}/instant-use/auth")
def instant_auth(item_id: str, body: InstantAuthIn, p: Principal = Depends(require("catalog:write")),
                 db: Session = Depends(get_db)):
    """② 인증 설정 — 대상별 최소 스코프·짧은 수명 교환 정책을 저장하고 호출자 신원으로 시험 교환 (실패 = MSG-019, 단계 유지)."""
    from app.models import McpServer, TokenExchangePolicy
    from app.services import identity
    item, iid, aud, _ = _instance(db, item_id)
    pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == aud)).first()
    if pol is None:
        if item.type == "mcp_server" and db.get(McpServer, iid) is not None:
            mcp_gateway.ensure_exchange_policy(db, db.get(McpServer, iid))
        else:
            from app.services.a2a_gateway import ensure_exchange_policy
            ensure_exchange_policy(db, iid)
        db.flush()
        pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == aud)).first()
    scopes = body.scopes or list(pol.default_scopes or [])
    broad = [s for s in scopes if review.scope_kind(s) == "admin"]
    srv = db.get(McpServer, iid) if item.type == "mcp_server" else None
    supported = set(pol.allowed_scopes or []) | set(((srv.auth or {}) if srv else {}).get("tool_scopes", {}).values()) | \
        set(((srv.auth or {}) if srv else {}).get("scopes_supported") or [])
    ok = [s for s in scopes if s in supported] if supported - {"mcp:invoke"} else scopes  # 서버가 선언한 스코프 안에서만
    pol.allowed_scopes = sorted(set(pol.allowed_scopes or []) | set(ok))
    pol.default_scopes, pol.ttl_seconds = ok or list(pol.default_scopes or []), body.ttl_seconds
    db.commit()
    audit.append("instant_use.auth", actor=p.sub, target=aud, data={"scopes": scopes, "ttl": body.ttl_seconds})
    tok = identity.exchange(db, p, aud, scopes or None, actor="instant-use-test")  # 실패 → 403 token_exchange_failed
    return {"ok": True, "audience": aud, "scopes": tok["scope"].split(), "expires_in": tok["expires_in"],
            "method": pol.method, "subject": p.sub, "warnings": [f"관리 스코프 {broad} — 최소 권한인지 확인"] if broad else []}


class InstantPolicyIn(BaseModel):
    tools: dict[str, dict] = Field(default_factory=dict, description="{tool: {allowed, approval_required}} — 비우면 위험도 기본값")
    subject_type: str = Field("any", pattern="^(any|user|group|role|team|agent)$")
    subject: str = "*"
    data_class_routing: dict[str, str] = {}
    calls_per_min: int | None = Field(60, ge=1, le=100000)
    profile_id: str | None = Field(None, description="사전 템플릿 프로파일에 도구 붙이기 (선택)")
    dry_run: bool = False


@router.post("/v1/catalog/{item_id}/instant-use/policy")
def instant_policy(item_id: str, body: InstantPolicyIn, p: Principal = Depends(require("catalog:write")),
                   db: Session = Depends(get_db)):
    """③ 정책 기본값 — 읽기 허용·쓰기 명시 허용·고위험 사람 승인, 적용 주체·데이터 분류·한도 → 정책(Rego 미리보기)·버전·감사."""
    from app.models import GatewayPolicy, McpServer, PolicyRevision, RateLimit, ToolRule
    from app.services import policy_review
    item, iid, _, target = _instance(db, item_id)
    target_type = "tool" if item.type == "mcp_server" else "agent"
    plan = []
    if item.type == "mcp_server":
        srv = db.get(McpServer, iid)
        approved = ((item.item_metadata or {}).get("_anx_review") or {}).get("tool_risks") or {}
        for t in (srv.tools if srv else []) or []:
            name = t.get("name")
            # 승인자가 확정한 위험도(E05) → 없으면 읽기·쓰기·고위험 자동 분류 (쓰기 = 명시 허용 필요)
            risk = approved.get(name) or approved.get(f"{iid}.{name}") or review.tool_risk(name, t.get("annotations"))
            ov = body.tools.get(name) or {}
            allowed = ov.get("allowed", risk != "medium")  # 쓰기는 명시 허용 필요
            approval = ov.get("approval_required", risk == "high")
            plan.append({"tool": name, "risk": risk, "allowed": bool(allowed), "approval_required": bool(approval or risk == "high")})
    name = f"{'tool' if target_type == 'tool' else 'agent'}-allow/{iid}"
    snap = {"name": name, "description": f"즉시 사용 기본값 (E03) — {item.name}", "subject_type": body.subject_type,
            "subject": body.subject, "target_type": target_type, "target": target, "effect": "allow", "conditions": {},
            "args_schema": {}, "data_class_routing": body.data_class_routing,
            "approval_required": False, "priority": 100, "enabled": True}
    existing = db.scalars(select(GatewayPolicy).where(GatewayPolicy.name == name)).first()
    rego = policy_review.to_rego(snap)
    approvals = [x["tool"] for x in plan if x["approval_required"] and x["allowed"]]
    if approvals:
        rego += f"\napproval_required := {{{', '.join(chr(34) + a + chr(34) for a in approvals)}}}\n"
    summary = {"read_allowed": sum(1 for x in plan if x["risk"] == "low" and x["allowed"]),
               "write_blocked": sum(1 for x in plan if x["risk"] == "medium" and not x["allowed"]),
               "approval": len(approvals), "policy": name, "version": (existing.version + 1) if existing else 1}
    if body.dry_run:
        return {"dry_run": True, "tools": plan, "rego": rego, "summary": summary}
    for x in plan:
        r = db.scalars(select(ToolRule).where(ToolRule.server_id == iid, ToolRule.tool == x["tool"])).first()
        if r is None:
            r = ToolRule(id=new_id("tr"), server_id=iid, tool=x["tool"])
            db.add(r)
        r.risk, r.allowed, r.approval_required = x["risk"], x["allowed"], x["approval_required"]
    if existing is None:
        existing = GatewayPolicy(id=new_id("pol"), version=1, **snap)
        db.add(existing)
    else:
        for k, v in snap.items():
            setattr(existing, k, v)
        existing.version += 1
    db.flush()
    db.add(PolicyRevision(id=new_id("prv"), policy_id=existing.id, version=existing.version,
                          snapshot={**snap, "id": existing.id, "version": existing.version}, changed_by=p.sub))
    if body.calls_per_min:
        lim_name = f"instant:{iid}"
        lim = db.scalars(select(RateLimit).where(RateLimit.name == lim_name)).first()
        if lim is None:
            lim = RateLimit(id=new_id("rl"), name=lim_name, scope_type="user", scope="*", target_type=target_type,
                            target=target)
            db.add(lim)
        lim.rps, lim.burst = round(body.calls_per_min / 60, 3), max(1, body.calls_per_min // 6)
    attached = None
    if body.profile_id:
        prof = db.get(AgentProfile, body.profile_id)
        if prof is None:
            raise not_found("agent profile")
        field = "tools" if target_type == "tool" else "remote_agents"
        cur = list(getattr(prof, field) or [])
        if target not in cur:
            setattr(prof, field, cur + [target])
        attached = prof.id
    item.item_metadata = {**(item.item_metadata or {}), "_anx_instant": {"applied_at": utcnow().isoformat(), "by": p.sub,
                                                                        "policy_id": existing.id}}
    db.commit()
    mcp_gateway.clear_caches()
    audit.append("policy.create" if summary["version"] == 1 else "policy.update", actor=p.sub, target=existing.id,
                 data={"via": "instant_use", "item": item.name, "summary": summary})
    return {"dry_run": False, "tools": plan, "rego": rego, "summary": summary, "policy_id": existing.id,
            "attached_profile": attached, "applied_at": item.item_metadata["_anx_instant"]["applied_at"]}


@router.get("/v1/catalog/{item_id}/exposure")
async def exposure(item_id: str, p: Principal = Depends(require("catalog:read"))):
    """④ 노출 확인 — 적용 후 60초 안에 호스트 도구 목록(게이트웨이 tools/list)·콘솔 카탈로그에 보이는지 (MSG-009)."""
    from datetime import datetime

    def _load():
        with SessionLocal() as db:
            item = _get_item(db, item_id)
            applied = ((item.item_metadata or {}).get("_anx_instant") or {}).get("applied_at")
            return item.type, item.ref_id, item.status, applied
    kind, ref, status, applied = await run_in_threadpool(_load)
    checks = [{"key": "catalog", "ok": status == "active", "detail": f"카탈로그 상태 {status}"}]
    if kind == "mcp_server" and ref:
        try:
            tools = await mcp_gateway.list_tools(ref, mcp_gateway.RpcContext(p))
            checks.append({"key": "host_tools", "ok": bool(tools), "detail": f"호스트 도구 목록 {len(tools)}개 (/mcp/{ref})"})
        except mcp_gateway.RpcError as exc:
            checks.append({"key": "host_tools", "ok": False, "detail": exc.message})
    elif kind == "a2a_agent" and ref:
        checks.append({"key": "agent_card", "ok": True, "detail": f"/a2a/{ref} 게이트웨이 경로"})
    elapsed = (utcnow() - datetime.fromisoformat(applied)).total_seconds() if applied else None
    ok = all(c["ok"] for c in checks)
    return {"exposed": ok, "checks": checks, "elapsed_s": round(elapsed, 1) if elapsed is not None else None,
            "within_slo": ok and (elapsed is None or elapsed <= 60)}


def _apply_review_risks(item_id: str, server_id: str) -> int:
    """승인 때 확정한 도구 위험도(E05 ⑤)를 등록 인스턴스 규칙에 반영 — 고위험은 사람 승인."""
    from app.models import ToolRule
    with SessionLocal() as db:
        risks = (((db.get(CatalogItem, item_id).item_metadata or {}).get("_anx_review")) or {}).get("tool_risks") or {}
        for tool, risk in risks.items():
            row = db.scalars(select(ToolRule).where(ToolRule.server_id == server_id, ToolRule.tool == tool)).first()
            if row is None:
                row = ToolRule(id=new_id("tr"), server_id=server_id, tool=tool)
                db.add(row)
            row.risk, row.approval_required, row.allowed = risk, risk == "high", True
        db.commit()
        return len(risks)


# ───────── MCP Registry 호환 /v0 (발견 전용) ─────────
V0_ANON_RPS, V0_ANON_BURST = 5.0, 30  # 익명 발견 API — IP 별 (D22-④)
V0_ANON_TOTAL_RPS, V0_ANON_TOTAL_BURST = 50.0, 100  # 익명 전체 — IP 를 속여(X-Forwarded-For) 버킷을 바꿔도 총량은 여기서 (65th)


@router.get("/v0/servers", tags=["registry"])
def v0_servers(request: Request, db: Session = Depends(get_db), p: Principal | None = Depends(optional_principal),
               search: str | None = None, limit: int = Query(30, ge=1, le=100), cursor: str | None = None,
               include_pending: bool = False):
    if p is None:  # 인증 없는 호출 — IP 별 요청 한도 (전체 목록을 반복해 읽는 부하를 막는다)
        ok, wait = ratelimit.backend().take(f"rl:v0anon:{request.client.host if request.client else '-'}", V0_ANON_RPS, V0_ANON_BURST)
        if ok:
            ok, wait = ratelimit.backend().take("rl:v0anon:*", V0_ANON_TOTAL_RPS, V0_ANON_TOTAL_BURST)
        if not ok:
            raise ApiError(429, "rate_limited", "발견 API 요청이 너무 많습니다 — 인증하거나 잠시 뒤 다시 시도하세요.",
                           headers={"Retry-After": str(max(1, int(wait) + 1))})
    statuses = None if include_pending and p and p.has("catalog:approve") else registry.DISCOVERABLE
    start = int(cursor) if cursor and cursor.isdigit() else 0
    # search = 서버 이름 부분 일치(MCP 레지스트리 사양) — SQL 로 거르고 SQL 로 페이지 (전체를 읽어 파이썬으로 거르지 않음, D22-④)
    page, total = registry.search(db, type_="mcp_server", statuses=statuses, name_contains=search, limit=limit, offset=start)
    nxt = str(start + limit) if start + limit < total else None
    return {"servers": [registry.server_entry(i) for i in page], "metadata": {"count": len(page), "nextCursor": nxt}}


@router.get("/v0/servers/{server_id:path}", tags=["registry"])
def v0_server(server_id: str, db: Session = Depends(get_db)):
    item = db.get(CatalogItem, server_id) or registry.find_item(db, "mcp_server", server_id)
    if item is None or item.type != "mcp_server" or item.status not in registry.DISCOVERABLE:
        raise not_found("server")
    return registry.server_entry(item)


@router.post("/v0/publish", status_code=201, tags=["registry"])
def v0_publish(doc: dict = Body(...), p: Principal = Depends(require("registry:publish")),
               db: Session = Depends(get_db)):
    existing = registry.find_item(db, "mcp_server", str(doc.get("name", "")))
    # 소유자 없는 항목(시드·내장)도 승인 권한자만 — 누구나 다음 버전을 선점하지 못하게 (D22-④)
    if existing is not None and not p.has("catalog:approve") and (existing.source == "mirror" or existing.owner != p.sub):
        # 새 버전은 그 항목의 게시자(또는 승인 권한자)만 — 다른 팀·미러 항목의 다음 버전 선점 방지 (D20-②)
        raise forbidden("다른 게시자의 항목입니다 — 새 버전은 소유자 또는 카탈로그 승인 권한자만 게시합니다.", code="not_item_owner")
    item, ver, created = registry.publish(db, "mcp_server", str(doc.get("name", "")), str(doc.get("version", "")), doc,
                                          p.sub, source="registry")
    return {"item": item_out(item), "version": {"id": ver.id, "version": ver.version, "status": ver.status},
            "created": created}


def _line_of(text: str, key: str) -> int | None:
    import re as _re
    m = _re.search(r'"' + _re.escape(key) + r'"\s*:', text)
    return text.count("\n", 0, m.start()) + 1 if m else None


@router.post("/api/v1/registry/validate", tags=["registry"])
def validate_publish(doc: dict = Body(...), _: Principal = Depends(require("registry:publish")),
                     db: Session = Depends(get_db)):
    """server.json 게시 전 검증 (SCR-E04 ②~⑥, MSG-004·007) — 공식 스키마 + AgentNexus 확장(_meta.auth·tool_scopes).
    `{"text": "<원문>"}` 으로 보내면 줄 번호 붙은 인라인 오류(JSON 구문 포함)를 준다. 도구 위험도 자동 분류·스코프 누락·서명 상태."""
    import json as _json
    text = doc.get("text") if isinstance(doc, dict) and isinstance(doc.get("text"), str) and len(doc) == 1 else None
    if text is not None:
        try:
            doc = _json.loads(text)
        except _json.JSONDecodeError as exc:
            return {"valid": False, "errors": [f"JSON 구문 오류: {exc.msg}"], "inline": [{"line": exc.lineno, "message": exc.msg}],
                    "name": None, "version": None, "auth": None, "tools": [], "remotes": [], "risks": [], "signature": None}
    errors = registry.validate_server_json(doc)
    meta = (doc.get("_meta") or {}) if isinstance(doc, dict) else {}
    auth = meta.get("auth") or {}
    declared = set(auth.get("scopes") or [])
    for tool, scope in (auth.get("tool_scopes") or {}).items():
        if declared and scope not in declared:
            errors.append(f"_meta.auth.tool_scopes.{tool}: 도구가 요구하는 스코프 '{scope}' 가 _meta.auth.scopes 에 없습니다")
    inline = []
    for e in errors:
        key = e.split(":", 1)[0].split(".")[-1].split("/")[-1].strip("() ")
        inline.append({"line": (_line_of(text, key) if text and key and key != "root" else None) or 1, "message": e})
    present, valid, kid = registry.verify_signature(db, doc) if isinstance(doc, dict) else (False, False, None)
    ann = meta.get("annotations") or {}
    return {"valid": not errors, "errors": errors, "inline": inline, "name": doc.get("name") if isinstance(doc, dict) else None,
            "version": doc.get("version") if isinstance(doc, dict) else None,
            "auth": auth.get("type"), "tools": meta.get("tools") or [],
            "remotes": [r.get("url") for r in doc.get("remotes") or []] if isinstance(doc, dict) else [],
            "risks": [{"tool": t, "risk": review.tool_risk(t, ann.get(t))} for t in meta.get("tools") or []],
            "scopes": [{"scope": sc, "kind": review.scope_kind(sc)} for sc in sorted(set((auth.get("tool_scopes") or {}).values()) | declared)],
            "signature": {"present": present, "valid": valid, "kid": kid,
                          "required": registry.require_signature_setting(db)}}


class DraftIn(BaseModel):
    text: str = Field(max_length=500_000)


@router.get("/api/v1/registry/draft", tags=["registry"])
def get_draft(p: Principal = Depends(require("registry:publish")), db: Session = Depends(get_db)):
    """server.json 임시 저장본 (SCR-E04 ⑧) — 사용자별 1개."""
    row = db.get(Setting, f"registry.draft.{p.sub}")
    return row.value if row else {"text": None}


@router.put("/api/v1/registry/draft", tags=["registry"])
def put_draft(body: DraftIn, p: Principal = Depends(require("registry:publish")), db: Session = Depends(get_db)):
    from app.db import utcnow
    key = f"registry.draft.{p.sub}"[:80]
    row = db.get(Setting, key)
    val = {"text": body.text, "saved_at": utcnow().isoformat()}
    if row is None:
        db.add(Setting(key=key, value=val, updated_by=p.sub))
    else:
        row.value = val
    db.commit()
    return val


class FetchIn(BaseModel):
    url: str = Field(pattern=r"^https?://")


@router.post("/api/v1/registry/fetch", tags=["registry"])
def fetch_server_json(body: FetchIn, _: Principal = Depends(require("registry:publish"))):
    """URL 에서 server.json 가져오기 (SCR-E04 ② URL 경로) — 게이트웨이 egress, 1MB 제한."""
    from app.core.http import sync_client
    try:
        with sync_client(timeout=10) as http:
            res = http.get(body.url)
    except Exception as exc:  # noqa: BLE001
        raise bad_request(f"가져오기 실패: {type(exc).__name__}", code="fetch_failed") from exc
    if res.status_code >= 400 or len(res.content) > 1_000_000:
        raise bad_request(f"가져오기 실패: HTTP {res.status_code}" if res.status_code >= 400 else "1MB 초과", code="fetch_failed")
    return {"text": res.text, "status": res.status_code}


# ───────── 승인 대기열 (SCR-E05) ─────────
@router.get("/api/v1/registry/approvals", tags=["registry"])
def registry_approvals(_: Principal = Depends(require("catalog:approve")), db: Session = Depends(get_db),
                       status: str = Query("pending", pattern="^(pending|approved|rejected|expired)$")):
    """등록 승인 대기열 (SCR-E05) — 상태 탭·건수, 대기 항목은 점검·스코프·도구 위험도·이전 버전 diff 포함. 72시간 경과 자동 만료."""
    review.expire_old(db)
    rows = db.execute(select(CatalogVersion, CatalogItem).join(CatalogItem, CatalogItem.id == CatalogVersion.item_id)
                      .where(CatalogVersion.status == status).order_by(CatalogVersion.created_at.desc()
                                                                       if status != "pending" else CatalogVersion.created_at)
                      .limit(200)).all()
    counts = {s: 0 for s in ("pending", "approved", "rejected", "expired")}
    for (st,) in db.execute(select(CatalogVersion.status)):
        if st in counts:
            counts[st] += 1
    items = []
    for v, i in rows:
        row = {"item": item_out(i), "version": v.version, "version_id": v.id, "created_by": v.created_by,
               "created_at": v.created_at, "signature_verified": v.signature_verified, "status": v.status,
               "note": v.note, "decided_by": v.decided_by, "decided_at": v.decided_at, "metadata": v.item_metadata}
        if status == "pending":
            row["review"] = review.queue_row(db, i, v)
        items.append(row)
    return {"items": items, "counts": counts}


# ───────── 레지스트리 소스·미러 (SCR-E06) ─────────
class SourceIn(BaseModel):
    name: str
    kind: str = Field(pattern="^(official|docker|custom|file)$")
    url: str | None = None
    auto_approve: bool = False
    require_signature: bool = False
    enabled: bool = True
    namespaces: list[str] = Field(default_factory=list, description="허용 네임스페이스(glob) — 비우면 전부")
    sync_interval_s: int | None = Field(None, ge=300, le=7 * 86400, description="예약 동기화 주기 — 비우면 수동")


def _source_out(s: RegistrySource) -> dict:
    nxt = (s.last_sync_at + timedelta(seconds=s.sync_interval_s)) if s.sync_interval_s and s.last_sync_at else None
    return {"id": s.id, "name": s.name, "kind": s.kind, "url": s.url, "auto_approve": s.auto_approve,
            "require_signature": s.require_signature, "enabled": s.enabled, "last_sync_at": s.last_sync_at,
            "last_status": s.last_status, "item_count": s.item_count, "namespaces": s.namespaces or [],
            "sync_interval_s": s.sync_interval_s, "next_sync_at": nxt, "last_result": s.last_result or {}}


@router.get("/api/v1/registry/sources", tags=["registry"])
def list_sources(_: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db)):
    return {"items": [_source_out(s) for s in db.scalars(select(RegistrySource).order_by(RegistrySource.name))]}


@router.post("/api/v1/registry/sources", status_code=201, tags=["registry"])
def create_source(body: SourceIn, p: Principal = Depends(require("catalog:write")), db: Session = Depends(get_db)):
    if body.kind != "file" and not body.url:
        raise bad_request("url 이 필요합니다.")
    if db.scalars(select(RegistrySource).where(RegistrySource.name == body.name)).first():
        raise conflict("같은 이름의 소스가 있습니다.")
    src = RegistrySource(id=new_id("src"), **body.model_dump())
    db.add(src)
    db.commit()
    audit.append("registry.source.create", actor=p.sub, target=src.name, data=body.model_dump())
    return _source_out(src)


@router.patch("/api/v1/registry/sources/{source_id}", tags=["registry"])
def patch_source(source_id: str, body: dict, p: Principal = Depends(require("catalog:write")),
                 db: Session = Depends(get_db)):
    src = db.get(RegistrySource, source_id)
    if src is None:
        raise not_found("source")
    if "sync_interval_s" in body and body["sync_interval_s"] is not None and not 300 <= int(body["sync_interval_s"]) <= 7 * 86400:
        raise bad_request("sync_interval_s 는 300초~7일 사이여야 합니다.")
    if "namespaces" in body and not isinstance(body["namespaces"], list):
        raise bad_request("namespaces 는 목록이어야 합니다.")
    for k in ("url", "auto_approve", "require_signature", "enabled", "namespaces", "sync_interval_s"):
        if k in body:
            setattr(src, k, body[k])
    db.commit()
    audit.append("registry.source.update", actor=p.sub, target=src.name, data=body)
    return _source_out(src)


@router.delete("/api/v1/registry/sources/{source_id}", tags=["registry"])
def delete_source(source_id: str, p: Principal = Depends(require("catalog:write")), db: Session = Depends(get_db)):
    src = db.get(RegistrySource, source_id)
    if src is None:
        raise not_found("source")
    db.delete(src)
    db.commit()
    audit.append("registry.source.delete", actor=p.sub, target=src.name)
    return {"deleted": source_id}


@router.post("/api/v1/registry/sources/{source_id}/sync", tags=["registry"])
def sync_source(source_id: str, p: Principal = Depends(require("catalog:write")), db: Session = Depends(get_db)):
    src = db.get(RegistrySource, source_id)
    if src is None:
        raise not_found("source")
    return registry.sync_source(db, src, p.sub)


@router.post("/api/v1/registry/sources/{source_id}/import", tags=["registry"])
def import_source(source_id: str, entries: list[dict] = Body(...), p: Principal = Depends(require("catalog:write")),
                  db: Session = Depends(get_db)):
    """에어갭 — server.json 목록(파일 내용)을 직접 가져온다."""
    src = db.get(RegistrySource, source_id)
    if src is None:
        raise not_found("source")
    return registry.import_entries(db, src, entries, p.sub)


# ───────── 신뢰 게시자 키 · 서명 필수 여부 ─────────
class TrustedKeysIn(BaseModel):
    keys: dict[str, str] = Field(description="kid → PEM 공개키")
    require_signature: bool = False


@router.get("/api/v1/registry/trust", tags=["registry"])
def get_trust(_: Principal = Depends(require("catalog:read")), db: Session = Depends(get_db)):
    return {"keys": registry.trusted_keys(db), "require_signature": registry.require_signature_setting(db)}


@router.put("/api/v1/registry/trust", tags=["registry"])
def put_trust(body: TrustedKeysIn, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    for key, value in (("registry.trusted_keys", body.keys), ("registry.require_signature", body.require_signature)):
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value, updated_by=p.sub))
        else:
            row.value, row.updated_by = value, p.sub
    db.commit()
    audit.append("registry.trust.update", actor=p.sub, data={"kids": sorted(body.keys),
                                                             "require_signature": body.require_signature})
    return {"keys": body.keys, "require_signature": body.require_signature}
