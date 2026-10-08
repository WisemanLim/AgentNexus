"""감사 로그 조회·해시 체인 검증 (FR-O05, SCR-K09)."""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, current_principal, require
from app.core.errors import bad_request, not_found
from app.core.ids import new_id, sha256_hex
from app.db import SessionLocal, get_db, utcnow
from app.models import (A2ATask, Approval, AuditArchive, AuditEvent, AuditReport, ChatSession, Message, RoutingDecision,
                        Run, RunStep, Setting, TokenExchange, UsageRecord)
from app.services import audit, retention
from app.services import orchestrator as orch

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


def event_out(ev: AuditEvent) -> dict:
    return {"seq": ev.seq, "event_id": ev.event_id, "ts": ev.ts, "actor": ev.actor, "actor_kind": ev.actor_kind,
            "action": ev.action, "target": ev.target, "decision": ev.decision, "trace_id": ev.trace_id,
            "data": ev.data, "prev_hash": ev.prev_hash, "hash": ev.hash}


@router.get("/events")
def list_events(_: Principal = Depends(require("audit:read")), db: Session = Depends(get_db),
                action: str | None = None, actor: str | None = None, trace_id: str | None = None, since: datetime | None = None, until: datetime | None = None,
                limit: int = Query(100, le=1000), before_seq: int | None = None):
    stmt = select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if actor:
        stmt = stmt.where(AuditEvent.actor == actor)
    if trace_id:
        stmt = stmt.where(AuditEvent.trace_id == trace_id)
    if since:
        stmt = stmt.where(AuditEvent.ts >= since)
    if until:
        stmt = stmt.where(AuditEvent.ts <= until)
    if before_seq:
        stmt = stmt.where(AuditEvent.seq < before_seq)
    items = [event_out(e) for e in db.scalars(stmt)]
    return {"items": items, "next_before_seq": items[-1]["seq"] if len(items) == limit else None}


@router.get("/verify")
def verify_chain(p: Principal = Depends(require("audit:read")), db: Session = Depends(get_db),
                 from_seq: int | None = Query(None, ge=1), to_seq: int | None = Query(None, ge=1)):
    if from_seq is not None or to_seq is not None:  # 구간 검증 (SCR-K09 ③) — 전역 배너 상태는 바꾸지 않는다
        return audit.verify_range(db, from_seq or 1, to_seq or 10**12)
    res = audit.verify(db)
    row = db.get(Setting, "audit.last_verify")  # 전역 배너(MSG-022)가 마지막 검증 결과를 본다
    val = {**res, "at": utcnow().isoformat(), "by": p.sub}
    if row is None:
        db.add(Setting(key="audit.last_verify", value=val, updated_by=p.sub))
    else:
        row.value = val
    db.commit()
    return res


# ───────────────────────── 세션 리플레이 (FR-O05, SCR-K08) ─────────────────────────
@router.get("/replay/{session_id}")
def replay(session_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """세션 단위 프롬프트·계획·도구 호출·모델 호출·정책 결정·승인·원격 Task·감사 이벤트를 시간순으로 재생."""
    s = db.get(ChatSession, session_id)
    if s is None or (s.user_sub != p.sub and not p.has("audit:read")):
        raise not_found("session")
    runs = db.scalars(select(Run).where(Run.session_id == session_id).order_by(Run.created_at)).all()
    run_ids = [r.id for r in runs]
    trace_ids = [r.trace_id for r in runs if r.trace_id]
    timeline: list[dict] = []
    for m in db.scalars(select(Message).where(Message.session_id == session_id)):
        timeline.append({"ts": m.created_at, "kind": f"message.{m.role}", "run_id": m.run_id, "content": m.content,
                         "parts": [x for x in (m.parts or []) if x.get("type") != "attachment"]})
    for st in db.scalars(select(RunStep).where(RunStep.run_id.in_(run_ids))):
        timeline.append({"ts": st.started_at or st.finished_at, "kind": f"step.{st.node}", "run_id": st.run_id,
                         "step": st.name, "tool": st.tool, "agent": st.agent_id, "args": st.args, "status": st.status,
                         "attempts": st.attempts, "error": st.error,
                         "output_preview": (st.output or {}).get("text", "")[:500] if isinstance(st.output, dict)
                         else None})
    for u in db.scalars(select(UsageRecord).where(UsageRecord.run_id.in_(run_ids))):
        timeline.append({"ts": u.ts, "kind": f"call.{u.kind}", "run_id": u.run_id, "target": u.target, "agent": u.agent,
                         "tokens_in": u.tokens_in, "tokens_out": u.tokens_out, "cost": float(u.cost),
                         "latency_ms": u.latency_ms, "status": u.status, "policy": u.policy_decisions})
    for a in db.scalars(select(Approval).where(Approval.run_id.in_(run_ids))):
        timeline.append({"ts": a.created_at, "kind": "approval", "run_id": a.run_id, "target": a.target,
                         "args": a.args, "status": a.status, "decided_by": a.decided_by, "risk": a.risk})
    for t in db.scalars(select(TokenExchange).where(TokenExchange.trace_id.in_(trace_ids))):
        timeline.append({"ts": t.ts, "kind": "token.exchange", "audience": t.audience, "scopes": t.scopes,
                         "decision": t.decision, "subject_kind": t.subject_kind})
    for d in db.scalars(select(RoutingDecision).where(RoutingDecision.trace_id.in_(trace_ids))):
        timeline.append({"ts": d.ts, "kind": f"routing.{d.kind}", "requested": d.requested, "chosen": d.chosen,
                         "fallback": d.fallback_used, "reason": d.reason})
    for t in db.scalars(select(A2ATask).where(A2ATask.run_id.in_(run_ids))):
        timeline.append({"ts": t.created_at, "kind": "a2a.task", "agent": t.agent_id, "task": t.remote_task_id,
                         "state": t.state, "artifacts": [a.get("name") for a in t.artifacts or []]})
    for e in db.scalars(select(AuditEvent).where(AuditEvent.trace_id.in_(trace_ids))):
        timeline.append({"ts": e.ts, "kind": "audit", "action": e.action, "target": e.target, "decision": e.decision,
                         "seq": e.seq, "hash": e.hash})
    timeline.sort(key=lambda x: (x["ts"] is None, x["ts"]))
    return {"session_id": session_id, "user": s.user_sub, "runs": [{"id": r.id, "profile_id": r.profile_id,
                                                                   "profile_version": r.profile_version,
                                                                   "status": r.status, "trace_id": r.trace_id}
                                                                  for r in runs],
            "timeline": timeline}


@router.post("/replay/{run_id}/rerun")
async def rerun(run_id: str, p: Principal = Depends(current_principal)):
    """같은 요청·같은 그래프 스냅샷으로 재실행(test run) — 결정 근거 재현 (PRD 4.3 시나리오 4)."""
    def _new():
        with SessionLocal() as db:
            r = db.get(Run, run_id)
            if r is None or (r.user_sub != p.sub and not p.has("audit:read")):
                raise not_found("run")
            new = Run(id=new_id("run"), profile_id=r.profile_id, user_sub=p.sub, principal=p.snapshot(),
                      status="queued", request=r.request, graph=r.graph, profile_version=r.profile_version,
                      data_class=r.data_class, test=True)
            db.add(new)
            db.commit()
            audit.append("audit.replay.rerun", actor=p.sub, target=run_id, data={"new_run": new.id})
            return new.id
    new_id_ = await run_in_threadpool(_new)
    orch.schedule(new_id_)
    return {"run_id": new_id_, "source_run": run_id}


# ───────────────────────── 서명 리포트 (SCR-K10) ─────────────────────────
# 리포트 템플릿 (SCR-K10 ①) — 개인정보보호법 접속기록 점검 · 감사 요약 · 보안 이벤트 · 사용자 지정
TEMPLATES = {
    "pipa_access": {"label": "개인정보보호법 접속기록 점검", "actions": ["auth.login", "observe.log.unmask", "chat.session.",
                                                                "identity.session.", "audit.report.", "observe.usage.export"]},
    "audit_summary": {"label": "감사 요약", "actions": []},
    "security": {"label": "보안 이벤트", "actions": [], "decision": "deny"},
    "custom": {"label": "사용자 지정", "actions": []},
}
REPORT_COLUMNS = ["seq", "ts", "actor", "action", "target", "decision", "trace_id"]


class ReportIn(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    actions: list[str] = []
    template: str = Field("custom", pattern="^(pipa_access|audit_summary|security|custom)$")
    team: str | None = None
    user: str | None = None
    pii: bool = True  # 행위자·대상의 PII 마스킹 (끄면 감사에 남는다)


def _report_args(db: Session, body: ReportIn) -> dict:
    from app.models import User
    t = TEMPLATES[body.template]
    actors = None
    if body.user:
        actors = {body.user}
    elif body.team:
        actors = {u.sub for u in db.scalars(select(User).where(User.team == body.team))}
    return {"since": body.since, "until": body.until, "actions": body.actions or t["actions"] or None,
            "actors": actors, "decision": t.get("decision")}


def _row(e: AuditEvent, pii: bool) -> dict:
    from app.services.guards import mask_pii
    r = {"seq": e.seq, "ts": e.ts.isoformat() if e.ts else None, "actor": e.actor, "action": e.action, "target": e.target,
         "decision": e.decision, "trace_id": e.trace_id, "ip": (e.data or {}).get("ip")}
    if pii:
        r["actor"] = mask_pii(str(r["actor"] or ""))[0]
        r["target"] = mask_pii(str(r["target"] or ""))[0]
    return r


@router.get("/reports/templates")
def report_templates(_: Principal = Depends(require("audit:read"))):
    return {"items": [{"id": k, **v} for k, v in TEMPLATES.items()]}


@router.post("/reports/preview")
def preview_report(body: ReportIn, _: Principal = Depends(require("audit:read")), db: Session = Depends(get_db)):
    """미리보기 (SCR-K10 ④) — 건수·행위별 분포·처음 20행 (생성·서명 전)."""
    events = audit.report_events(db, **_report_args(db, body))
    by: dict[str, int] = {}
    for e in events:
        by[e.action] = by.get(e.action, 0) + 1
    return {"template": body.template, "count": len(events), "columns": REPORT_COLUMNS + ["ip"],
            "by_action": sorted(({"action": k, "count": v} for k, v in by.items()), key=lambda x: -x["count"])[:20],
            "rows": [_row(e, body.pii) for e in events[:20]]}


@router.post("/reports", status_code=201)
def create_report(body: ReportIn, p: Principal = Depends(require("audit:read")), db: Session = Depends(get_db)):
    args = _report_args(db, body)
    rep = audit.build_report(db, actor=p.sub, meta={"template": body.template, "team": body.team, "user": body.user,
                                                    "pii": body.pii}, **args)
    audit.append("audit.report.create", actor=p.sub, target=rep["id"], data={"count": rep["count"], "template": body.template,
                                                                            "sha256": rep["sha256"], "pii_masked": body.pii})
    return rep


@router.get("/reports")
def list_reports(_: Principal = Depends(require("audit:read")), db: Session = Depends(get_db)):
    return {"items": [{"id": r.id, "created_by": r.created_by, "created_at": r.created_at, "range_from": r.range_from,
                       "range_to": r.range_to, "filters": r.filters, "sha256": r.sha256, "event_count": r.event_count,
                       "chain_valid": r.chain_valid} for r in db.scalars(select(AuditReport)
                                                                         .order_by(AuditReport.created_at.desc()))]}


@router.get("/reports/{report_id}/download")
def download_report(report_id: str, _: Principal = Depends(require("audit:read")), db: Session = Depends(get_db),
                    format: str = Query("jsonl", pattern="^(jsonl|csv|md)$")):
    """다운로드 (SCR-K10 ⑤) — 서명된 원본 JSONL · CSV · Markdown(인쇄 → PDF). 서명·해시는 JSONL 원본 기준."""
    import csv
    import io
    import json as _json
    r = db.get(AuditReport, report_id)
    if r is None:
        raise not_found("report")
    raw = Path(r.file_path).read_text(encoding="utf-8")
    pii = (r.filters or {}).get("pii", True)
    if format != "jsonl":
        from app.services.guards import mask_pii
        rows = []
        for line in raw.splitlines():
            e = _json.loads(line)
            row = {k: e.get(k) for k in REPORT_COLUMNS}
            if pii:
                row["actor"], row["target"] = mask_pii(str(row["actor"] or ""))[0], mask_pii(str(row["target"] or ""))[0]
            rows.append(row)
        if format == "csv":
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=REPORT_COLUMNS)
            w.writeheader()
            w.writerows(rows)
            body, media = buf.getvalue(), "text/csv; charset=utf-8"
        else:
            t = TEMPLATES.get((r.filters or {}).get("template") or "custom", TEMPLATES["custom"])["label"]
            head = [f"# {t} — {report_id}", "", f"- 기간: {r.range_from or '처음'} ~ {r.range_to or '현재'}",
                    f"- 건수: {r.event_count} · 체인 검증: {'정상' if r.chain_valid else '손상'} · SHA-256 `{r.sha256}`",
                    f"- PII 마스킹: {'켬' if pii else '끔'}", "", "| " + " | ".join(REPORT_COLUMNS) + " |",
                    "|" + "---|" * len(REPORT_COLUMNS)]
            body = "\n".join(head + ["| " + " | ".join(str(x.get(c) or "") for c in REPORT_COLUMNS) + " |" for x in rows]) + "\n"
            media = "text/markdown; charset=utf-8"
        return PlainTextResponse(body, media_type=media, headers={"X-AgentNexus-SHA256": r.sha256,
                                 "Content-Disposition": f'attachment; filename="{report_id}.{format}"'})
    return PlainTextResponse(raw, media_type="application/x-ndjson",
                             headers={"X-AgentNexus-Signature": r.signature, "X-AgentNexus-SHA256": r.sha256,
                                      "Content-Disposition": f'attachment; filename="{report_id}.jsonl"'})


class VerifyIn(BaseModel):
    signature: str
    content: str | None = None


@router.post("/reports/verify")
def verify_report(body: VerifyIn, _: Principal = Depends(require("audit:read"))):
    digest = sha256_hex(body.content) if body.content is not None else None
    return audit.verify_report_signature(body.signature, digest)


# ───────────────────────── 보존·아카이브·파기 증명 ─────────────────────────
class RetentionIn(BaseModel):
    overrides: dict[str, int] = {}


@router.post("/retention/run")
def retention_run(body: RetentionIn, p: Principal = Depends(require("audit:read", "policy:write")),
                  db: Session = Depends(get_db)):
    bad = [k for k in body.overrides if k not in retention.DEFAULTS]
    if bad:
        raise bad_request(f"알 수 없는 보존 항목: {bad}")
    return retention.run(db, p.sub, body.overrides)


@router.get("/archives")
def list_archives(_: Principal = Depends(require("audit:read")), db: Session = Depends(get_db)):
    return {"items": [{"id": a.id, "from_seq": a.from_seq, "to_seq": a.to_seq, "event_count": a.event_count,
                       "first_prev_hash": a.first_prev_hash, "last_hash": a.last_hash, "file_sha256": a.file_sha256,
                       "created_at": a.created_at, "purged_at": a.purged_at}
                      for a in db.scalars(select(AuditArchive).order_by(AuditArchive.from_seq))]}


@router.post("/archives/{archive_id}/verify")
def verify_archive(archive_id: str, _: Principal = Depends(require("audit:read")), db: Session = Depends(get_db)):
    a = db.get(AuditArchive, archive_id)
    if a is None:
        raise not_found("archive")
    return audit.verify_archive(a)


@router.post("/archives/{archive_id}/purge")
def purge_archive(archive_id: str, p: Principal = Depends(require("audit:read", "policy:write"))):
    try:
        return audit.purge_archive(archive_id, p.sub)
    except ValueError as exc:
        raise not_found("archive") from exc
