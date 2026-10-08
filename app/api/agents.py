"""에이전트 프로파일·오케스트레이션 템플릿 API (FR-A02·W02·W04·A05 · SCR-D01~D08·D10)."""
import fnmatch
import re
from types import SimpleNamespace

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api import chat as chat_api
from app.core.auth import Principal, require
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.core.ids import new_id, slugify
from app.db import SessionLocal, get_db
from app.models import A2AAgent, AgentProfile, AgentProfileVersion, KnowledgeBase, McpServer, Model, Run, ToolRule, User
from app.services import audit, profiles
from app.services import graph as G
from app.services import orchestrator as orch

router = APIRouter(tags=["agents"])


def profile_out(pr: AgentProfile, detail: bool = False) -> dict:
    g = G.normalize(pr.graph)
    out = {"id": pr.id, "name": pr.name, "description": pr.description, "status": pr.status, "version": pr.version,
           "template": pr.template, "a2a_exposed": pr.a2a_exposed, "tools": pr.tools, "remote_agents": pr.remote_agents,
           "models": pr.models, "nodes": [s.get("agent") for s in g["default_plan"]], "planner_mode": g["planner"]["mode"],
           "updated_at": pr.updated_at, "created_by": pr.created_by,
           "a2a_card": f"/a2a/platform/{pr.id}/.well-known/agent-card.json" if pr.a2a_exposed else None,
           "a2a_status": "published" if pr.a2a_exposed else (pr.a2a_status or "draft")}
    if detail:
        out["graph"], out["yaml"], out["canvas"] = g, G.to_yaml(g), G.to_canvas(g)
        out["draft_code"], out["sync"] = pr.draft_code, pr.draft_code is None  # 그래프 ↔ 코드 동기 (SCR-D07 ②)
    return out


def db_checks(db: Session, g: dict) -> tuple[list[dict], list[dict]]:
    """참조 검증 (SCR-D04·D05·D07 ④) — Retriever 에 쓰기 도구면 오류, 미등록 도구·신뢰 미달 원격은 경고(MSG-006)."""
    g = G.normalize(g)
    errors: list[dict] = []
    warnings: list[dict] = []
    w = g["workers"]
    rules = {f"{r.server_id}.{r.tool}": r for r in db.scalars(select(ToolRule))}
    for node in ("retriever", "executor"):
        for i, pat in enumerate((w.get(node) or {}).get("tools") or []):
            hits = [r for q, r in rules.items() if fnmatch.fnmatchcase(q, pat)]
            path = f"workers.{node}.tools[{i}]"
            if not hits:
                warnings.append({"path": path, "message": f"등록되지 않았거나 허용목록에 없는 도구 — 호출 시 거부됩니다 (MSG-006): {pat}"})
            elif node == "retriever" and any(r.risk != "low" for r in hits):
                bad = [f"{r.server_id}.{r.tool}" for r in hits if r.risk != "low"]
                if "*" in pat:  # 와일드카드가 쓰기 도구까지 덮음 — 계획 단계에서 쓰면 오류
                    warnings.append({"path": path, "message": f"와일드카드가 쓰기 도구를 포함합니다 ({', '.join(bad[:3])}) — Retriever 단계에서는 쓸 수 없습니다."})
                else:
                    errors.append({"path": path, "message": f"Retriever 에는 읽기 도구만 — {bad[0]} 는 쓰기 도구입니다. Executor 로 옮기세요."})
            elif any(not r.allowed for r in hits):
                warnings.append({"path": path, "message": f"차단된 도구가 포함됨: {pat}"})
    for i, st in enumerate(g["default_plan"]):  # Retriever 단계가 쓰기 도구를 부르면 오류
        r = rules.get(st.get("tool") or "") if isinstance(st, dict) and st.get("agent") == "retriever" else None
        if r is not None and r.risk != "low":
            errors.append({"path": f"default_plan[{i}].tool", "message": f"Retriever 단계에 쓰기 도구 {st['tool']} — Executor 로 옮기세요."})
    for i, aid in enumerate(G.remote_agents_of(g)):
        a = db.get(A2AAgent, aid)
        path = f"workers.remote.agents[{i}]"
        if a is None:
            warnings.append({"path": path, "message": f"등록되지 않은 원격 에이전트: {aid}"})
        elif a.status != "active":
            warnings.append({"path": path, "message": f"원격 에이전트 {aid} 상태 {a.status} — 승인 후 위임 가능"})
        elif a.trust_tier in ("untrusted", "blocked"):
            warnings.append({"path": path, "message": f"신뢰 등급 미달({a.trust_tier}) — 게이트웨이가 위임을 거부합니다 (ST-04)"})
    return errors, warnings


def _parse_db(db: Session, body: dict) -> tuple[dict, list[dict], list[dict]]:
    g, errors = _parse(body)
    if not g or errors:
        return g, errors, []
    lines = {}
    if body.get("yaml") is not None:
        try:
            lines = G.parse(body["yaml"])[1]
        except Exception:  # noqa: BLE001
            lines = {}
    errs, warns = db_checks(db, g)
    return g, G.with_lines(errs, lines), G.with_lines(warns, lines)


_apply_graph = profiles.apply_graph


def _parse(body: dict) -> tuple[dict, list[dict]]:
    """{graph: dict} 또는 {yaml: str} → (graph, 오류(줄 번호 포함))."""
    try:
        g, lines = G.parse(body.get("yaml") if body.get("yaml") is not None else body.get("graph") or {})
    except Exception as exc:  # noqa: BLE001 — YAML 구문 오류
        line = getattr(getattr(exc, "problem_mark", None), "line", None)
        return {}, [{"path": "(yaml)", "message": str(exc).split("\n")[0], "line": line + 1 if line is not None else None}]
    return g, G.with_lines(G.validate(g), lines)


def _get(db: Session, pid: str) -> AgentProfile:
    pr = db.get(AgentProfile, pid)
    if pr is None or pr.status == "archived":
        raise not_found("agent profile")
    return pr


sync_catalog = profiles.sync_catalog


@router.get("/api/v1/agents")
def list_profiles(_: Principal = Depends(require("agents:read")), db: Session = Depends(get_db),
                  template: bool | None = None, status: str | None = Query(None, pattern="^(draft|published|archived)$"),
                  team: str | None = None, q: str | None = None):
    """프로파일 목록 (SCR-D01) — 상태·팀(작성자 팀)·검색 필터. 보관함은 status=archived 로."""
    stmt = select(AgentProfile).order_by(AgentProfile.template.desc(), AgentProfile.name)
    stmt = stmt.where(AgentProfile.status == status) if status else stmt.where(AgentProfile.status != "archived")
    if template is not None:
        stmt = stmt.where(AgentProfile.template.is_(template))
    if q:
        stmt = stmt.where(AgentProfile.name.like(f"%{q}%") | AgentProfile.id.like(f"%{q}%"))
    rows = list(db.scalars(stmt))
    teams = {u.sub: u.team for u in db.scalars(select(User).where(User.sub.in_({r.created_by for r in rows if r.created_by})))}
    if team:
        rows = [r for r in rows if teams.get(r.created_by) == team]
    return {"items": [profile_out(pr) | {"team": teams.get(pr.created_by)} for pr in rows]}


@router.get("/api/v1/agents/tools")
def building_blocks(p: Principal = Depends(require("agents:read")), db: Session = Depends(get_db)):
    """조합 재료 (FR-W02) — 등록 즉시 노출되는 MCP 도구·원격 에이전트·모델."""
    rules = {(r.server_id, r.tool): r for r in db.scalars(select(ToolRule))}
    tools = []
    for s in db.scalars(select(McpServer).where(McpServer.status == "active").order_by(McpServer.id)):
        for t in s.tools or []:
            r = rules.get((s.id, t.get("name")))
            if r is None or not r.allowed:
                continue
            tools.append({"qualified": f"{s.id}.{t['name']}", "server": s.id, "description": t.get("description"),
                          "risk": r.risk, "approval_required": r.approval_required,
                          "suggested_node": "executor" if r.risk == "high" else "retriever",
                          "input_schema": t.get("inputSchema")})
    agents = [{"id": a.id, "name": a.name, "skills": a.skills, "trust_tier": a.trust_tier}
              for a in db.scalars(select(A2AAgent).where(A2AAgent.status == "active"))]
    models = [{"id": m.id, "category": m.category} for m in db.scalars(select(Model).where(Model.enabled.is_(True)))]
    return {"tools": tools, "remote_agents": agents, "models": models + [{"id": "auto", "category": "router"}]}


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = None
    graph: dict | None = None
    yaml: str | None = None
    from_template: str | None = None
    id: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")


@router.post("/api/v1/agents", status_code=201)
def create_profile(body: ProfileIn, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    if body.from_template:
        tpl = _get(db, body.from_template)
        if not tpl.template:
            raise bad_request("템플릿 프로파일이 아닙니다.")
        g, errors = G.normalize(tpl.graph), []
    else:
        g, errors = _parse(body.model_dump())
    if errors:
        raise ApiError(422, "invalid_graph", "그래프 정의 오류", extra={"errors": errors})
    pid = body.id or slugify(body.name)[:50] + "-" + new_id("p")[-6:]
    if db.get(AgentProfile, pid) or db.scalars(select(AgentProfile).where(AgentProfile.name == body.name)).first():
        raise conflict("같은 id/이름의 프로파일이 있습니다.")
    pr = AgentProfile(id=pid, name=body.name, description=body.description, status="draft", version=0,
                      created_by=p.sub)
    _apply_graph(pr, g)
    db.add(pr)
    db.commit()
    audit.append("agent.profile.create", actor=p.sub, target=pid, data={"from_template": body.from_template})
    return profile_out(pr, detail=True)


@router.get("/api/v1/agents/{pid}")
def get_profile(pid: str, _: Principal = Depends(require("agents:read")), db: Session = Depends(get_db)):
    pr = _get(db, pid)
    errs, warns = db_checks(db, pr.graph)
    return profile_out(pr, detail=True) | {"checks": {"errors": errs, "warnings": warns}}


@router.post("/api/v1/agents/{pid}/validate")
def validate_graph(pid: str, body: dict = Body(...), _: Principal = Depends(require("agents:read")),
                   db: Session = Depends(get_db)):
    _get(db, pid)
    g, errors, warnings = _parse_db(db, body)
    bad = {e["path"].split("[")[1].split("]")[0] for e in errors if e["path"].startswith("default_plan[")}
    canvas = G.to_canvas(g) if g and not any(e["path"] == "(yaml)" for e in errors) else None
    if canvas:  # 오류 노드 적색 (SCR-D07 ⑥)
        ids = [st.get("id") for st in G.normalize(g)["default_plan"] if isinstance(st, dict)]
        for n in canvas["nodes"]:
            n["error"] = n["id"] in {ids[int(i)] for i in bad if i.isdigit() and int(i) < len(ids)}
    return {"valid": not errors, "errors": errors, "warnings": warnings, "canvas": canvas}


@router.put("/api/v1/agents/{pid}/graph")
def save_graph(pid: str, body: dict = Body(...), p: Principal = Depends(require("agents:write")),
               db: Session = Depends(get_db)):
    """그래프 편집(SCR-D02)·YAML/코드 편집(SCR-D07) — 저장 전 스키마·참조 검증, 오류 줄 표시."""
    pr = _get(db, pid)
    if pr.template:
        raise bad_request("템플릿은 직접 수정할 수 없습니다. 복제(from_template) 후 편집하세요.")
    g, errors, warnings = _parse_db(db, body)
    if errors:
        if body.get("allow_invalid") and body.get("yaml") is not None:  # 코드만 저장 (SCR-D07 ⑧, MSG-001) — 그래프 반영 보류
            pr.draft_code = body["yaml"]
            db.commit()
            audit.append("agent.profile.code_only", actor=p.sub, target=pid, data={"errors": len(errors)})
            return profile_out(pr, detail=True) | {"saved": "code_only", "errors": errors, "warnings": warnings}
        raise ApiError(422, "invalid_graph", "그래프 정의 오류", extra={"errors": errors})
    _apply_graph(pr, g)
    pr.draft_code = None
    if pr.status == "published":
        pr.status = "draft"  # 게시본과 차이 — 다시 게시 필요
    db.commit()
    audit.append("agent.profile.graph", actor=p.sub, target=pid, data={"nodes": len(pr.graph.get("default_plan", []))})
    return profile_out(pr, detail=True) | {"saved": "graph", "warnings": warnings}


@router.post("/api/v1/agents/{pid}/publish")
def publish_profile(pid: str, note: str | None = Body(None, embed=True), p: Principal = Depends(require("agents:write")),
                    db: Session = Depends(get_db)):
    pr = _get(db, pid)
    errors = G.validate(pr.graph) + db_checks(db, pr.graph)[0]
    if errors:
        raise ApiError(422, "invalid_graph", "게시 불가 — 그래프 정의 오류", extra={"errors": errors})
    if pr.draft_code is not None:
        raise ApiError(409, "code_only_draft", "코드만 저장된 변경이 있습니다 — 오류를 고쳐 저장하거나 되돌린 뒤 게시하세요 (MSG-001).")
    pr.version += 1
    pr.status = "published"
    db.add(AgentProfileVersion(id=new_id("apv"), profile_id=pid, version=pr.version, graph=pr.graph, note=note,
                               created_by=p.sub))
    sync_catalog(db, pr, p.sub)
    db.commit()
    audit.append("agent.profile.publish", actor=p.sub, target=pid, data={"version": pr.version, "note": note})
    return profile_out(pr, detail=True)


@router.get("/api/v1/agents/{pid}/versions")
def profile_versions(pid: str, _: Principal = Depends(require("agents:read")), db: Session = Depends(get_db)):
    pr = _get(db, pid)
    rows = db.scalars(select(AgentProfileVersion).where(AgentProfileVersion.profile_id == pid)
                      .order_by(AgentProfileVersion.version.desc()))
    return {"current": pr.version, "items": [{"version": v.version, "note": v.note, "created_by": v.created_by,
                                              "created_at": v.created_at, "graph": v.graph} for v in rows]}


@router.post("/api/v1/agents/{pid}/rollback")
def rollback_profile(pid: str, version: int = Body(..., embed=True), p: Principal = Depends(require("agents:write")),
                     db: Session = Depends(get_db)):
    pr = _get(db, pid)
    v = db.scalars(select(AgentProfileVersion).where(AgentProfileVersion.profile_id == pid,
                                                     AgentProfileVersion.version == version)).first()
    if v is None:
        raise not_found("profile version")
    _apply_graph(pr, v.graph)
    pr.version += 1
    pr.status = "published"
    db.add(AgentProfileVersion(id=new_id("apv"), profile_id=pid, version=pr.version, graph=pr.graph,
                               note=f"rollback to v{version}", created_by=p.sub))
    sync_catalog(db, pr, p.sub)
    db.commit()
    audit.append("agent.profile.rollback", actor=p.sub, target=pid, data={"to": version, "new_version": pr.version})
    return profile_out(pr, detail=True)


class TestIn(BaseModel):
    request: str = Field(min_length=1)
    data_class: str | None = Field(None, pattern="^(public|internal|confidential)$")
    wait: bool = True
    timeout_s: float = Field(60, le=300)
    dry_run: bool = True  # 쓰기 도구는 호출하지 않고 인자만 기록 (SCR-D08 ③) — 기본 켬
    version: int | None = Field(None, ge=1, description="게시 버전으로 실행 — 비우면 현재 초안")


@router.post("/api/v1/agents/{pid}/test")
async def test_profile(pid: str, body: TestIn, p: Principal = Depends(require("agents:write"))):
    """프로파일 테스트 실행 (SCR-D08) — 초안·게시 버전, 드라이런(기본), 노드별 입출력·지연·비용·trace 반환. 실행은 본인 권한(OBO)."""
    def _start():
        with SessionLocal() as db:
            _get(db, pid)
        try:
            return orch.start_run(p, profile_id=pid, request=body.request, data_class=body.data_class, test=True,
                                  dry_run=body.dry_run, version=body.version)
        except ValueError as exc:
            raise not_found(str(exc)) from exc
    run_id = await run_in_threadpool(_start)
    orch.schedule(run_id)
    status = await orch.wait(run_id, body.timeout_s) if body.wait else "queued"

    def _detail():
        with SessionLocal() as db:
            return chat_api.get_run(run_id, p, db)
    return {"run_id": run_id, "status": status, "run": await run_in_threadpool(_detail)}


@router.get("/api/v1/agents/{pid}/test-runs")
def test_runs(pid: str, _: Principal = Depends(require("agents:read")), db: Session = Depends(get_db),
              limit: int = Query(20, le=100)):
    """이전 테스트 실행 (SCR-D08 ⑤ 비교)."""
    from app.services.runmeta import run_meta
    _get(db, pid)
    rows = db.scalars(select(Run).where(Run.profile_id == pid, Run.test.is_(True)).order_by(Run.created_at.desc()).limit(limit))
    out = []
    for r in rows:
        m = run_meta(db, r)
        out.append({"id": r.id, "status": r.status, "version": r.profile_version, "dry_run": r.dry_run, "request": r.request,
                    "created_at": r.created_at, "trace_id": r.trace_id, "latency_ms": m["latency_ms"], "cost": m["cost"],
                    "first_token_ms": m["first_token_ms"],
                    "nodes": [{k: n[k] for k in ("step", "node", "status", "latency_ms", "cost")} for n in m["nodes"]]})
    return {"items": out}


def health(db: Session, pr: AgentProfile) -> list[dict]:
    """연결 상태 (SCR-D01 ③·D08 ⑧) — 프로파일이 쓰는 MCP 서버·원격 에이전트·모델·지식 베이스."""
    g = G.normalize(pr.graph)
    out = []
    for sid in sorted({t.split(".", 1)[0] for t in G.tools_of(g) if "." in t}):
        m = db.get(McpServer, sid)
        ok = bool(m and m.status == "active" and m.health != "unhealthy")
        out.append({"kind": "mcp", "id": sid, "ok": ok,
                    "detail": "없음" if m is None else f"{m.status} · {m.health or '미확인'}" + (f" ({m.health_detail})" if m.health_detail else "")})
    for aid in G.remote_agents_of(g):
        a = db.get(A2AAgent, aid)
        out.append({"kind": "a2a", "id": aid, "ok": bool(a and a.status == "active" and a.trust_tier not in ("untrusted", "blocked")),
                    "detail": "없음" if a is None else f"{a.status} · 신뢰 {a.trust_tier}"})
    for mid in G.models_of(g):
        m = db.get(Model, mid)
        out.append({"kind": "model", "id": mid, "ok": mid == "auto" or bool(m and m.enabled),
                    "detail": "게이트웨이 라우팅" if mid == "auto" else ("없음" if m is None else ("사용" if m.enabled else "중지"))})
    for kb in G.knowledge_bases_of(g):
        k = db.get(KnowledgeBase, kb)
        out.append({"kind": "kb", "id": kb, "ok": k is not None, "detail": "있음" if k else "없음"})
    return out


@router.get("/api/v1/agents/{pid}/health")
def profile_health(pid: str, _: Principal = Depends(require("agents:read")), db: Session = Depends(get_db)):
    items = health(db, _get(db, pid))
    return {"ok": all(i["ok"] for i in items), "items": items}


@router.get("/api/v1/agents/{pid}/checks")
def prepublish_checks(pid: str, _: Principal = Depends(require("agents:read")), db: Session = Depends(get_db)):
    """게시 전 점검 (SCR-D08 ⑧) — 검증 오류 0 · 최근 테스트 3회 성공 · 승인 정책 · 연결 · 코드만 저장 없음."""
    pr = _get(db, pid)
    g = G.normalize(pr.graph)
    errs = G.validate(pr.graph) + db_checks(db, pr.graph)[0]
    recent = list(db.scalars(select(Run).where(Run.profile_id == pid, Run.test.is_(True),
                                               Run.status.in_(orch.TERMINAL)).order_by(Run.created_at.desc()).limit(3)))
    passed = sum(1 for r in recent if r.status == "succeeded")
    ex_tools = (g["workers"].get("executor") or {}).get("tools") or []
    rules = [r for r in db.scalars(select(ToolRule)) if any(fnmatch.fnmatchcase(f"{r.server_id}.{r.tool}", t) for t in ex_tools)]
    loose = [f"{r.server_id}.{r.tool}" for r in rules if r.risk == "high" and not r.approval_required]
    hl = health(db, pr)
    items = [{"key": "validation", "ok": not errs, "detail": f"검증 오류 {len(errs)}"},
             {"key": "tests", "ok": bool(recent) and passed == len(recent),
              "detail": f"최근 테스트 {len(recent)}회 중 성공 {passed}" if recent else "테스트 실행 기록 없음"},
             {"key": "approval", "ok": not loose,
              "detail": "쓰기 도구 사람 승인 설정됨" if not loose else f"고위험인데 승인 없음: {', '.join(loose)}"},
             {"key": "health", "ok": all(i["ok"] for i in hl), "detail": f"연결 {sum(i['ok'] for i in hl)}/{len(hl)}"},
             {"key": "code", "ok": pr.draft_code is None, "detail": "코드만 저장된 변경 없음" if pr.draft_code is None
              else "코드만 저장된 변경 있음 (MSG-001)"}]
    return {"publishable": all(i["ok"] for i in items), "items": items, "version": pr.version, "status": pr.status}


@router.get("/api/v1/agents/{pid}/diff")
def profile_diff(pid: str, version: int | None = None, _: Principal = Depends(require("agents:read")),
                 db: Session = Depends(get_db)):
    """게시본 비교 (SCR-D07 ⑦) — 게시 버전(기본 최신) 대비 현재 초안(코드만 저장분이 있으면 그 코드)."""
    pr = _get(db, pid)
    stmt = select(AgentProfileVersion).where(AgentProfileVersion.profile_id == pid)
    stmt = stmt.where(AgentProfileVersion.version == version) if version else stmt.order_by(AgentProfileVersion.version.desc())
    v = db.scalars(stmt).first()
    cur = pr.graph
    if pr.draft_code:
        try:
            cur = G.parse(pr.draft_code)[0]
        except Exception:  # noqa: BLE001 — 구문 오류면 저장된 그래프와 비교
            cur = pr.graph
    return {"base_version": v.version if v else None, "changes": G.diff(v.graph if v else {}, cur)}


@router.get("/api/v1/agents/{pid}/code")
def profile_code(pid: str, format: str = Query("yaml", pattern="^(yaml|adk)$"), _: Principal = Depends(require("agents:read")),
                 db: Session = Depends(get_db)):
    """정의 형식 (SCR-D07 ①) — YAML(편집) · ADK Python(변환 보기, 읽기 전용)."""
    pr = _get(db, pid)
    if format == "yaml":
        return {"format": "yaml", "code": pr.draft_code or G.to_yaml(G.normalize(pr.graph)), "sync": pr.draft_code is None,
                "editable": True, "warnings": []}
    code, warn = G.to_adk(pr.graph, pr.id)
    return {"format": "adk", "code": code, "sync": pr.draft_code is None, "editable": False, "warnings": warn}


@router.post("/api/v1/agents/{pid}/code/revert")
def revert_code(pid: str, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    """되돌리기 (SCR-D07 ②) — 코드만 저장된 변경을 버리고 저장된 그래프로."""
    pr = _get(db, pid)
    pr.draft_code = None
    db.commit()
    audit.append("agent.profile.code_revert", actor=p.sub, target=pid)
    return profile_out(pr, detail=True)


class CloneIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)


@router.post("/api/v1/agents/{pid}/clone", status_code=201)
def clone_profile(pid: str, body: CloneIn, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    """복제 (SCR-D01 행 동작) — 그래프를 복사한 새 초안."""
    src = _get(db, pid)
    if db.scalars(select(AgentProfile).where(AgentProfile.name == body.name)).first():
        raise conflict("같은 이름의 프로파일이 있습니다.")
    new = AgentProfile(id=slugify(body.name)[:50] + "-" + new_id("p")[-6:], name=body.name, description=src.description,
                       status="draft", version=0, created_by=p.sub)
    _apply_graph(new, src.graph)
    db.add(new)
    db.commit()
    audit.append("agent.profile.create", actor=p.sub, target=new.id, data={"cloned_from": pid})
    return profile_out(new, detail=True)


@router.post("/api/v1/agents/{pid}/restore")
def restore_profile(pid: str, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    pr = db.get(AgentProfile, pid)
    if pr is None or pr.status != "archived":
        raise not_found("archived profile")
    pr.status = "draft"
    db.commit()
    audit.append("agent.profile.restore", actor=p.sub, target=pid)
    return profile_out(pr)


class SimulateIn(BaseModel):
    failed: list[str] = []
    timeout: list[str] = []


@router.post("/api/v1/agents/{pid}/simulate")
def simulate_failures(pid: str, body: SimulateIn, _: Principal = Depends(require("agents:read")),
                      db: Session = Depends(get_db)):
    """실패 시뮬레이션 (SCR-D06 ⑧) — 가정한 실패·타임아웃 조합으로 합류 성립·부분 결과 여부를 미리 본다(조건 분기는 참으로 가정)."""
    g = G.normalize(_get(db, pid).graph)
    steps = [SimpleNamespace(name=st["id"], status="pending", depends_on=st.get("depends_on") or [], error=None)
             for st in g["default_plan"] if isinstance(st, dict) and st.get("id")]
    bad = set(body.failed) | set(body.timeout)
    for _ in range(len(steps) + 1):
        ready, skip = orch._ready(steps, g["fanout"])
        if not ready and not skip:
            break
        for st in ready:
            st.status = "failed" if st.name in bad else "succeeded"
            st.error = ("타임아웃" if st.name in body.timeout else "실패") if st.name in bad else None
        for st, why in skip:
            st.status, st.error = "skipped", why
    if g["on_failure"] == "fail" and any(s.status == "failed" for s in steps):
        for st in steps:
            if st.status == "pending":
                st.status, st.error = "skipped", "이전 단계 실패 (on_failure=fail)"
    failed = [s for s in steps if s.status in ("failed", "skipped")]
    ok = [s for s in steps if s.status == "succeeded"]
    req = set(g["fanout"].get("required") or []) if g["fanout"].get("join") == "required" else set()
    req_failed = [s.name for s in failed if s.name in req]
    status = "succeeded" if not failed else ("partial" if ok and g["on_failure"] == "partial" and not req_failed else "failed")
    msg = {"succeeded": "모든 단계 완료", "partial": "합류 성립 · 부분 결과 반환 (MSG-014)",
           "failed": "전체 실패" + (f" — 필수 노드 실패: {', '.join(req_failed)}" if req_failed else "")}[status]
    return {"status": status, "message": msg, "steps": [{"step": s.name, "status": s.status, "reason": s.error} for s in steps]}


@router.post("/api/v1/agents/{pid}/a2a")
def expose_a2a(pid: str, exposed: bool = Body(..., embed=True), p: Principal = Depends(require("agents:write")),
               db: Session = Depends(get_db)):
    """플랫폼 에이전트의 A2A 서버 노출 (FR-A05, SCR-D10)."""
    pr = _get(db, pid)
    if exposed and pr.status != "published":
        raise bad_request("게시된 프로파일만 노출할 수 있습니다.")
    if exposed and not p.has("catalog:approve"):  # 노출은 심사 후 (D10 ⑨) — 중지는 누구나 즉시
        raise ApiError(403, "review_required", "A2A 게시는 승인이 필요합니다 — POST /api/v1/agents/{id}/a2a-card/publish 로 요청하세요.")
    pr.a2a_exposed = exposed
    pr.a2a_status = "published" if exposed else "draft"
    db.commit()
    audit.append("agent.profile.a2a", actor=p.sub, target=pid, data={"exposed": exposed})
    return profile_out(pr)


# ───────── A2A 서버 노출 — Agent Card 편집·검증·게시 요청 (SCR-D10, FR-A05·R01) ─────────
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _card_draft(pr: AgentProfile) -> dict:
    d = dict(pr.a2a_card or {})
    return {"name": d.get("name") or pr.id, "version": d.get("version") or f"{pr.version}.0.0",
            "description": d.get("description") or pr.description or pr.name,
            "capabilities": {"streaming": True, "pushNotifications": True, **(d.get("capabilities") or {})},
            "skills": d.get("skills") or [{"id": pr.id, "name": pr.name, "description": pr.description or pr.name,
                                           "examples": [], "tags": ["agentnexus"]}],
            "security": d.get("security") or {"scheme": "oauth2", "scopes": ["a2a:invoke"]}}


def card_preview(pr: AgentProfile, draft: dict, base: str) -> tuple[dict, list[str]]:
    """게시될 agent-card.json 과 검증 오류 (D10 ⑦⑧) — url 은 게이트웨이로 고정, 보안 스킴은 AgentNexus OIDC."""
    from app.core.config import settings as _s
    from app.services import a2a_proto as ap
    errs = []
    if not draft.get("name"):
        errs.append("name: 필수 (MSG-003)")
    if not _SEMVER.match(str(draft.get("version") or "")):
        errs.append("version: SemVer 형식 (예 1.0.0) (MSG-004)")
    if not draft.get("description"):
        errs.append("description: 필수 (MSG-003)")
    ids = [sk.get("id") for sk in draft.get("skills") or []]
    if not ids or any(not i for i in ids):
        errs.append("skills: 1개 이상, 각 id 필수")
    if len(ids) != len(set(ids)):
        errs.append("skills: id 중복")
    if pr.status != "published":
        errs.append("프로파일이 게시(published) 상태여야 합니다")
    issuer = _s.public_base_url.rstrip("/") + "/idp"
    schemes = {"oauth2": {"type": "oauth2", "flows": {"clientCredentials": {
        "tokenUrl": f"{issuer}/token", "scopes": {sc: "" for sc in (draft.get("security") or {}).get("scopes") or ["a2a:invoke"]}}}}}
    card = ap.build_card(name=draft.get("name") or pr.id, description=draft.get("description") or "", version=str(draft.get("version")),
                         url=f"{base.rstrip('/')}/a2a/platform/{pr.id}",
                         skills=[{"id": sk.get("id"), "name": sk.get("name") or sk.get("id"), "description": sk.get("description") or "",
                                  "examples": sk.get("examples") or [], "tags": sk.get("tags") or []} for sk in draft.get("skills") or []],
                         streaming=bool((draft.get("capabilities") or {}).get("streaming")),
                         push=bool((draft.get("capabilities") or {}).get("pushNotifications")), schemes=schemes,
                         provider={"organization": "AgentNexus", "url": base})
    errs += [f"A2A 스키마: {e}" for e in ap.validate_card(card)]
    return card, errs


@router.get("/api/v1/agents/{pid}/a2a-card")
def get_a2a_card(pid: str, _: Principal = Depends(require("agents:read")), db: Session = Depends(get_db)):
    from app.core.config import settings as _s
    pr = _get(db, pid)
    draft = _card_draft(pr)
    card, errs = card_preview(pr, draft, _s.public_base_url)
    return {"status": "published" if pr.a2a_exposed else (pr.a2a_status or "draft"), "draft": draft, "preview": card,
            "errors": errs, "url": card.get("url")}


class CardIn(BaseModel):
    name: str
    version: str
    description: str
    capabilities: dict = {}
    skills: list[dict]
    security: dict = {}


@router.put("/api/v1/agents/{pid}/a2a-card")
def put_a2a_card(pid: str, body: CardIn, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    from app.core.config import settings as _s
    pr = _get(db, pid)
    pr.a2a_card = body.model_dump()
    if not pr.a2a_exposed:
        pr.a2a_status = "draft"
    db.commit()
    card, errs = card_preview(pr, pr.a2a_card, _s.public_base_url)
    return {"status": "published" if pr.a2a_exposed else pr.a2a_status, "draft": pr.a2a_card, "preview": card, "errors": errs}


@router.post("/api/v1/agents/{pid}/a2a-card/publish")
def publish_a2a_card(pid: str, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    """게시 요청 (D10 ⑨) — 승인 권한이 없으면 등록 승인 대기열(E05)로, 승인 후 공개(MSG-008)."""
    from app.core.config import settings as _s
    from app.models import CatalogItem, CatalogVersion
    from app.services.notify import notify
    pr = _get(db, pid)
    draft = _card_draft(pr)
    card, errs = card_preview(pr, draft, _s.public_base_url)
    if errs:
        raise ApiError(422, "card_invalid", "검증을 통과해야 게시를 요청할 수 있습니다.", extra={"errors": errs})
    profiles.sync_catalog(db, pr, p.sub)
    item = db.scalars(select(CatalogItem).where(CatalogItem.type == "agent_profile", CatalogItem.name == pr.id)).first()
    meta = {"kind": "a2a_card", "card": card}
    if p.has("catalog:approve"):
        pr.a2a_card, pr.a2a_status, pr.a2a_exposed = draft, "published", True
        db.commit()
        audit.append("agent.profile.a2a", actor=p.sub, target=pid, data={"exposed": True, "version": card["version"]})
        return {**profile_out(pr), "requested": False}
    exists = db.scalars(select(CatalogVersion).where(CatalogVersion.item_id == item.id, CatalogVersion.version == card["version"],
                                                     CatalogVersion.status == "pending")).first()
    if exists is None:
        db.add(CatalogVersion(id=new_id("cver"), item_id=item.id, version=card["version"], item_metadata=meta,
                              status="pending", created_by=p.sub))
    pr.a2a_card, pr.a2a_status = draft, "pending"
    db.commit()
    notify("registry.pending", f"A2A 게시 요청: {pr.name}", f"Agent Card {card['version']} — {p.sub}", severity="warn",
           link="/console/registry/approvals", audience_role="operator", dedupe_key=f"a2a.card:{pr.id}:{card['version']}")
    audit.append("agent.profile.a2a.request", actor=p.sub, target=pid, data={"version": card["version"]})
    return {**profile_out(pr), "requested": True}


@router.delete("/api/v1/agents/{pid}")
def archive_profile(pid: str, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    pr = _get(db, pid)
    if pr.template:
        raise bad_request("템플릿은 보관할 수 없습니다.")
    pr.status, pr.a2a_exposed = "archived", False
    db.commit()
    audit.append("agent.profile.archive", actor=p.sub, target=pid)
    return {"archived": pid}
