"""자연어 진입·실행 API (FR-A00·A01·A02·W05 · SCR-C01~C08·D08·D09).

POST /api/v1/sessions/{sid}/messages 는 `Accept: text/event-stream` 이면 실행 이벤트를 SSE 로 흘린다:
run.started · plan · step.started/completed/failed/retry/skipped · approval.required/resolved · delta(답변 토큰)
· run.waiting · run.completed · run.failed. 이벤트는 DB 에 영속되어 GET /api/v1/runs/{rid}/events 로 재생된다.
"""
import asyncio
import json
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, current_principal, require
from app.core.config import settings
from app.core.errors import ApiError, bad_request, not_found
from app.core.ids import new_id
from app.db import SessionLocal, get_db, utcnow
from app.models import A2AAgent, A2ATask, AgentProfile, Approval, ChatSession, McpServer, McpSession, Message, Run, \
    RunStep, UsageRecord
from app.models import Attachment as AttachmentRow
from app.services import attachments as att
from app.services import audit
from app.services import graph as G
from app.services import orchestrator as orch
from app.services.runmeta import run_meta

router = APIRouter(tags=["chat"])
END_EVENTS = {"run.completed", "run.failed", "run.waiting", "run.canceled"}
MAX_ATTACHMENT = 200_000


def session_out(s: ChatSession) -> dict:
    return {"id": s.id, "title": s.title, "profile_id": s.profile_id, "status": s.status, "created_at": s.created_at,
            "updated_at": s.updated_at, "source": s.source}


def _own_session(db: Session, sid: str, p: Principal) -> ChatSession:
    s = db.get(ChatSession, sid)
    if s is None or s.user_sub != p.sub or s.status in ("archived", "deleted"):
        raise not_found("session")
    return s


class SessionIn(BaseModel):
    title: str | None = Field(None, max_length=255)
    profile_id: str | None = None


@router.post("/api/v1/sessions", status_code=201)
def create_session(body: SessionIn, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    if body.profile_id and db.get(AgentProfile, body.profile_id) is None:
        raise bad_request("알 수 없는 profile_id")
    s = ChatSession(id=new_id("ses"), user_sub=p.sub, team=p.team, profile_id=body.profile_id,
                    title=body.title or "새 대화", source="api" if p.kind in ("pat", "workload", "access_token") else "chat")
    db.add(s)
    db.commit()
    return session_out(s)


@router.get("/api/v1/sessions")
def list_sessions(p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db), q: str | None = None,
                  profile_id: str | None = None, days: int | None = Query(None, ge=1, le=3650),
                  source: str | None = Query(None, pattern="^(chat|api)$"), limit: int = Query(50, le=500)):
    """대화 이력 (SCR-C07) — 제목·메시지 본문 검색 · 프로파일·기간·출처 필터 · 메시지 수·비용."""
    from datetime import timedelta
    stmt = select(ChatSession).where(ChatSession.user_sub == p.sub, ChatSession.status.notin_(("archived", "deleted")))
    if q:
        hit = select(Message.session_id).where(Message.content.like(f"%{q}%"))
        stmt = stmt.where(or_(ChatSession.title.like(f"%{q}%"), ChatSession.id.in_(hit)))
    if profile_id:
        stmt = stmt.where(ChatSession.profile_id == profile_id)
    if days:
        stmt = stmt.where(ChatSession.updated_at >= utcnow() - timedelta(days=days))
    if source:
        stmt = stmt.where(ChatSession.source == source)
    rows = list(db.scalars(stmt.order_by(ChatSession.updated_at.desc()).limit(limit)))
    ids = [r.id for r in rows]
    counts = dict(db.execute(select(Message.session_id, func.count()).where(Message.session_id.in_(ids))
                             .group_by(Message.session_id)).all()) if ids else {}
    costs = dict(db.execute(select(UsageRecord.session_id, func.sum(UsageRecord.cost)).where(UsageRecord.session_id.in_(ids))
                            .group_by(UsageRecord.session_id)).all()) if ids else {}
    names = {pr.id: pr.name for pr in db.scalars(select(AgentProfile).where(AgentProfile.id.in_({r.profile_id for r in rows})))}
    return {"items": [session_out(r) | {"messages": counts.get(r.id, 0), "cost": float(costs.get(r.id) or 0),
                                        "profile_name": names.get(r.profile_id)} for r in rows], "total": len(rows)}


def run_out(r: Run, steps: list[RunStep] | None = None, approvals: list[Approval] | None = None) -> dict:
    out = {"id": r.id, "session_id": r.session_id, "profile_id": r.profile_id, "profile_version": r.profile_version,
           "status": r.status, "request": r.request, "result": r.result, "citations": r.citations,
           "data_class": r.data_class, "trace_id": r.trace_id, "error": r.error, "test": r.test, "dry_run": r.dry_run,
           "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at, "plan": r.plan}
    if steps is not None:
        out["steps"] = [{"id": s.id, "name": s.name, "node": s.node, "tool": s.tool, "agent": s.agent_id,
                         "args": s.args, "depends_on": s.depends_on, "status": s.status, "attempts": s.attempts,
                         "error": s.error, "approval_id": s.approval_id, "output": s.output,
                         "started_at": s.started_at, "finished_at": s.finished_at} for s in steps]
    if approvals is not None:
        out["approvals"] = [{"id": a.id, "status": a.status, "target": a.target, "args": a.args, "risk": a.risk,
                             "approver": a.approver, "expires_at": a.expires_at} for a in approvals]
    return out


@router.get("/api/v1/sessions/{sid}")
def get_session(sid: str, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    s = _own_session(db, sid, p)
    msgs = db.scalars(select(Message).where(Message.session_id == sid).order_by(Message.created_at)).all()
    runs = db.scalars(select(Run).where(Run.session_id == sid).order_by(Run.created_at)).all()
    return {**session_out(s), "messages": [message_out(m) for m in msgs],
            "runs": [run_out(r) | ({"meta": run_meta(db, r)} if r.status in orch.TERMINAL else {}) for r in runs]}


def message_out(m: Message) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content, "parts": m.parts, "run_id": m.run_id,
            "created_at": m.created_at, "feedback": m.feedback, "branch_of": m.branch_of}


@router.patch("/api/v1/sessions/{sid}")
def rename_session(sid: str, body: SessionIn, p: Principal = Depends(require("chat:use")),
                   db: Session = Depends(get_db)):
    s = _own_session(db, sid, p)
    if body.title:
        s.title = body.title
    if body.profile_id:
        s.profile_id = body.profile_id
    db.commit()
    return session_out(s)


@router.delete("/api/v1/sessions/{sid}")
def delete_session(sid: str, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    """삭제 (SCR-C07 ⑦, MSG-002) — 대화 내용은 지우고 메타(건수·시각)만 감사 목적으로 남긴다."""
    s = _own_session(db, sid, p)
    n = 0
    for m in db.scalars(select(Message).where(Message.session_id == sid)):
        m.content, m.parts, n = "", [], n + 1
    s.status, s.title = "deleted", "(삭제됨)"
    db.commit()
    audit.append("chat.session.delete", actor=p.sub, target=sid, data={"messages": n})
    return {"deleted": sid, "messages": n}


@router.get("/api/v1/sessions/{sid}/summary")
def session_summary(sid: str, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    """세션 미리보기 (SCR-C07 ④⑤) — 프로파일·메시지·위임·비용·출처·마지막 응답·trace·첨부·Artifacts."""
    s = _own_session(db, sid, p)
    msgs = list(db.scalars(select(Message).where(Message.session_id == sid).order_by(Message.created_at)))
    runs = list(db.scalars(select(Run).where(Run.session_id == sid)))
    rids = [r.id for r in runs]
    remote = list(db.scalars(select(RunStep).where(RunStep.run_id.in_(rids), RunStep.node == "remote"))) if rids else []
    cost = db.scalar(select(func.sum(UsageRecord.cost)).where(UsageRecord.session_id == sid)) or Decimal("0")
    pr = db.get(AgentProfile, s.profile_id) if s.profile_id else None
    last = next((m for m in reversed(msgs) if m.role == "assistant"), None)
    files = [{"kind": "attachment", "id": a.id, "name": a.name, "size": a.size, "created_at": a.created_at}
             for a in db.scalars(select(AttachmentRow).where(AttachmentRow.session_id == sid))]
    files += _artifacts(remote)
    return {**session_out(s), "profile_name": pr.name if pr else None, "profile_archived": bool(pr and pr.status == "archived"),
            "messages": len(msgs), "delegations": len(remote), "cost": float(cost),
            "traces": len({r.trace_id for r in runs if r.trace_id}), "last_answer": (last.content[:400] if last else None),
            "files": files}


def _artifacts(remote_steps: list[RunStep]) -> list[dict]:
    out = []
    for st in remote_steps:
        o = st.output if isinstance(st.output, dict) else {}
        for a in o.get("artifacts") or []:
            if a.get("artifactId") and o.get("task_id"):
                out.append({"kind": "artifact", "agent": st.agent_id, "task_id": f"{st.agent_id}:{o['task_id']}",
                            "artifact_id": a["artifactId"], "name": a.get("name") or a["artifactId"],
                            "created_at": st.finished_at})
    return out


@router.get("/api/v1/sessions/{sid}/export")
def export_session(sid: str, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    """내보내기 .md (SCR-C07 ⑦)."""
    s = _own_session(db, sid, p)
    lines = [f"# {s.title}", "", f"- 세션 {s.id} · 시작 {s.created_at:%Y-%m-%d %H:%M} · 출처 {s.source}", ""]
    for m in db.scalars(select(Message).where(Message.session_id == sid).order_by(Message.created_at)):
        lines += [f"## {'나' if m.role == 'user' else '에이전트'} · {m.created_at:%m-%d %H:%M}", "", m.content, ""]
    audit.append("chat.session.export", actor=p.sub, target=sid)
    return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{sid}.md"'})


@router.get("/api/v1/chat/files")
def my_files(p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db), limit: int = Query(100, le=500)):
    """첨부·Artifacts 모아보기 (SCR-C07 ⑤) — 세션 횡단."""
    files = [{"kind": "attachment", "id": a.id, "name": a.name, "size": a.size, "session_id": a.session_id,
              "created_at": a.created_at} for a in db.scalars(select(AttachmentRow).where(AttachmentRow.owner_sub == p.sub)
                                                              .order_by(AttachmentRow.created_at.desc()).limit(limit))]
    rids = list(db.scalars(select(Run.id).where(Run.user_sub == p.sub).order_by(Run.created_at.desc()).limit(200)))
    remote = list(db.scalars(select(RunStep).where(RunStep.run_id.in_(rids), RunStep.node == "remote",
                                                   RunStep.status == "succeeded"))) if rids else []
    return {"items": (files + _artifacts(remote))[:limit]}


class FeedbackIn(BaseModel):
    rating: str | None = Field(None, pattern="^(up|down)$")
    comment: str | None = Field(None, max_length=2000)


@router.post("/api/v1/sessions/{sid}/messages/{mid}/feedback")
def message_feedback(sid: str, mid: str, body: FeedbackIn, p: Principal = Depends(require("chat:use")),
                     db: Session = Depends(get_db)):
    """👍👎 피드백 (SCR-C03 ⑧) — 응답 메시지 단위, 다시 누르면 취소(rating=null)."""
    _own_session(db, sid, p)
    m = db.get(Message, mid)
    if m is None or m.session_id != sid or m.role != "assistant":
        raise not_found("message")
    m.feedback = {"rating": body.rating, "comment": body.comment, "at": utcnow().isoformat()} if body.rating else None
    db.commit()
    audit.append("chat.feedback", actor=p.sub, target=mid, data={"rating": body.rating, "run_id": m.run_id})
    return message_out(m)


def _user_msg(db: Session, sid: str, mid: str) -> Message:
    m = db.get(Message, mid)
    if m is None or m.session_id != sid:
        raise not_found("message")
    if m.role == "assistant":  # 응답 메시지에서 다시 생성 → 그 실행의 요청 메시지
        m = db.scalars(select(Message).where(Message.run_id == m.run_id, Message.role == "user")).first() or m
    if m.role != "user":
        raise bad_request("사용자 메시지 또는 그 응답만 다시 보낼 수 있습니다.")
    return m


async def _resend(sid: str, p: Principal, mid: str, content: str | None, kind: str) -> dict:
    def _do():
        with SessionLocal() as db:
            s = _own_session(db, sid, p)
            src = _user_msg(db, sid, mid)
            prev = db.scalars(select(Run).where(Run.id == src.run_id)).first()
            run_id = orch.start_run(p, profile_id=s.profile_id or (prev.profile_id if prev else None),
                                    request=content or src.content, session_id=sid,
                                    data_class=prev.data_class if prev else None)
            msg = Message(id=new_id("msg"), session_id=sid, role="user", content=content or src.content, run_id=run_id,
                          parts=[x for x in src.parts or [] if x.get("type") == "attachment"] + [{"type": kind}],
                          branch_of=src.branch_of or src.id)
            db.add(msg)
            s.updated_at = utcnow()
            db.commit()
            return run_id, msg.id
    run_id, msg_id = await run_in_threadpool(_do)
    orch.schedule(run_id)
    return {"run_id": run_id, "message_id": msg_id, "events": f"/api/v1/runs/{run_id}/events"}


@router.post("/api/v1/sessions/{sid}/messages/{mid}/regenerate")
async def regenerate(sid: str, mid: str, p: Principal = Depends(require("chat:use"))):
    """다시 생성 (SCR-C03 ⑧) — 같은 요청·첨부로 새 실행, 원래 메시지에 분기로 붙는다."""
    return await _resend(sid, p, mid, None, "regenerate")


class EditIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


@router.post("/api/v1/sessions/{sid}/messages/{mid}/edit")
async def edit_resend(sid: str, mid: str, body: EditIn, p: Principal = Depends(require("chat:use"))):
    """편집 후 재전송 (SCR-C03 ①) — 새 분기로 실행하고 이전 대화는 이력에 남긴다."""
    return await _resend(sid, p, mid, body.content, "edited")


class Attachment(BaseModel):
    """텍스트를 바로 보내거나(name+text), 먼저 올린 첨부를 참조(attachment_id — PDF·docx·xlsx·이미지)."""
    name: str | None = None
    mime_type: str = "text/plain"
    text: str | None = Field(None, max_length=MAX_ATTACHMENT)
    attachment_id: str | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    profile_id: str | None = None
    data_class: str | None = Field(None, pattern="^(public|internal|confidential)$")
    attachments: list[Attachment] = Field(default_factory=list, max_length=5)


@router.post("/api/v1/sessions/{sid}/messages")
async def post_message(sid: str, body: MessageIn, request: Request, p: Principal = Depends(require("chat:use"))):
    def _create():
        with SessionLocal() as db:
            s = _own_session(db, sid, p)
            profile_id = body.profile_id or s.profile_id
            if not profile_id or db.get(AgentProfile, profile_id) is None:
                raise bad_request("에이전트 프로파일을 선택하세요 (profile_id).", code="profile_required")
            run_id = orch.start_run(p, profile_id=profile_id, request=body.content, session_id=sid,
                                    data_class=body.data_class)
            parts = []
            for a in body.attachments:
                if a.attachment_id:
                    row = db.get(AttachmentRow, a.attachment_id)
                    if row is None or row.owner_sub != p.sub:
                        raise not_found("attachment")
                    row.session_id = row.session_id or sid
                    parts.append({"type": "attachment", "name": row.name, "mime_type": row.mime, "kind": row.kind,
                                  "attachment_id": row.id, "size": row.size,
                                  "text": row.text or f"(이미지 {row.name} — 텍스트 추출 없음, 모델이 이미지를 읽지 않으면 근거에서 제외)"})
                elif a.name and a.text is not None:
                    parts.append({"type": "attachment", "name": a.name, "mime_type": a.mime_type, "text": a.text})
                else:
                    raise bad_request("첨부는 name+text 또는 attachment_id 가 필요합니다.", code="invalid_attachment")
            msg = Message(id=new_id("msg"), session_id=sid, role="user", content=body.content, run_id=run_id,
                          parts=parts)
            db.add(msg)
            if s.title == "새 대화":
                s.title = body.content[:60]
            s.profile_id, s.updated_at = profile_id, utcnow()
            db.commit()
            return run_id, msg.id
    run_id, msg_id = await run_in_threadpool(_create)
    orch.schedule(run_id)
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(_stream(run_id, 0), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-Id": run_id})
    return {"run_id": run_id, "message_id": msg_id, "events": f"/api/v1/runs/{run_id}/events"}


async def _stream(run_id: str, after: int, timeout: float = 900):
    loop = asyncio.get_running_loop()
    deadline, last_beat = loop.time() + timeout, loop.time()
    yield f"event: run\ndata: {json.dumps({'run_id': run_id})}\n\n"
    while loop.time() < deadline:
        events = await run_in_threadpool(orch.events_after, run_id, after)
        for ev in events:
            after = ev.id
            yield f"id: {ev.id}\nevent: {ev.type}\ndata: {json.dumps(ev.data, ensure_ascii=False, default=str)}\n\n"
            if ev.type in END_EVENTS:
                return
        if not events:
            if loop.time() - last_beat > 15:
                last_beat = loop.time()
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.1)


def _visible_run(db: Session, rid: str, p: Principal) -> Run:
    r = db.get(Run, rid)
    if r is None or (r.user_sub != p.sub and not p.has("observe:read")):
        raise not_found("run")
    return r


@router.get("/api/v1/runs/{rid}")
def get_run(rid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    r = _visible_run(db, rid, p)
    steps = list(db.scalars(select(RunStep).where(RunStep.run_id == rid).order_by(RunStep.seq)).all())
    approvals = db.scalars(select(Approval).where(Approval.run_id == rid).order_by(Approval.created_at)).all()
    return run_out(r, steps, list(approvals)) | {"meta": run_meta(db, r, steps)}


@router.get("/api/v1/runs/{rid}/events")
async def run_events(rid: str, request: Request, after: int = 0, p: Principal = Depends(current_principal)):
    def _check():
        with SessionLocal() as db:
            _visible_run(db, rid, p)
    await run_in_threadpool(_check)
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(_stream(rid, after), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    events = await run_in_threadpool(orch.events_after, rid, after)
    return {"items": [{"id": e.id, "ts": e.ts, "type": e.type, "data": e.data} for e in events]}


@router.post("/api/v1/runs/{rid}/retry")
async def retry_run(rid: str, p: Principal = Depends(require("chat:use"))):
    def _check():
        with SessionLocal() as db:
            r = _visible_run(db, rid, p)
            if r.user_sub != p.sub:
                raise not_found("run")
    await run_in_threadpool(_check)
    if not await run_in_threadpool(orch.retry_failed, rid):
        raise bad_request("재시도할 수 있는 실행이 아닙니다 (partial|failed 만).", code="not_retryable")
    return {"run_id": rid, "status": "retrying"}


@router.post("/api/v1/runs/{rid}/continue")
def continue_run(rid: str, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    """부분 결과로 계속 (SCR-C06 ④)."""
    r = _visible_run(db, rid, p)
    if r.user_sub != p.sub:
        raise not_found("run")
    try:
        return {"run_id": rid, "result": orch.continue_partial(rid, p.sub)}
    except ValueError as exc:
        raise bad_request(str(exc), code="not_continuable") from exc


class InputIn(BaseModel):
    step: str
    text: str = Field(min_length=1, max_length=20000)


@router.post("/api/v1/runs/{rid}/input")
def run_input(rid: str, body: InputIn, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    """원격 에이전트 input-required 에 답하기 (SCR-C05 인라인 답변, FR-R03·R04) — 같은 Task·contextId 로 이어 보낸다."""
    r = _visible_run(db, rid, p)
    if r.user_sub != p.sub:
        raise not_found("run")
    try:
        return orch.provide_input(rid, body.step, body.text, p.sub)
    except ValueError as exc:
        raise bad_request(str(exc), code="not_waiting_input") from exc


@router.post("/api/v1/chat/attachments", status_code=201)
async def upload_attachment(file: UploadFile = File(...), session_id: str | None = Form(None),
                            p: Principal = Depends(require("chat:use"))):
    """첨부 업로드 (FR-A01, SCR-C01) — PDF·docx·xlsx·이미지·텍스트, ATTACHMENT_MAX_MB 이하. 텍스트를 추출해 요약 근거로 쓴다."""
    data = await file.read(settings.attachment_max_mb * 1024 * 1024 + 1)

    def _save():
        with SessionLocal() as db:
            if session_id:
                _own_session(db, session_id, p)
            try:
                return att.out(att.save(db, p.sub, file.filename or "upload", data, session_id))
            except att.AttachmentError as exc:
                raise bad_request(str(exc), code="invalid_attachment") from exc
    return await run_in_threadpool(_save)


@router.get("/api/v1/chat/attachments/{aid}")
def download_attachment(aid: str, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    row = db.get(AttachmentRow, aid)
    if row is None or row.owner_sub != p.sub:
        raise not_found("attachment")
    return FileResponse(row.path, media_type=row.mime, filename=row.name)


class FromArtifactIn(BaseModel):
    task_id: str
    artifact_id: str
    session_id: str | None = None


@router.post("/api/v1/chat/attachments/from-artifact", status_code=201)
def attach_artifact(body: FromArtifactIn, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    """원격 Artifact → 대화 첨부 (SCR-C05 ⑥)."""
    t = db.get(A2ATask, body.task_id)
    if t is None or (t.principal_sub != p.sub and not p.has("a2a:write")):
        raise not_found("task")
    art = next((a for a in t.artifacts or [] if a.get("artifactId") == body.artifact_id), None)
    if art is None:
        raise not_found("artifact")
    text = "\n".join(x.get("text", "") for x in art.get("parts", []) if x.get("kind") == "text") or \
        json.dumps([x.get("data") for x in art.get("parts", []) if x.get("kind") == "data"], ensure_ascii=False)
    name = art.get("name") or f"{body.artifact_id}.md"
    if "." not in name:
        name += ".md"
    if body.session_id:
        _own_session(db, body.session_id, p)
    try:
        return att.out(att.save(db, p.sub, name, text.encode(), body.session_id))
    except att.AttachmentError as exc:
        raise bad_request(str(exc), code="invalid_attachment") from exc


def readiness(db: Session, pr: AgentProfile) -> list[str]:
    """프로파일이 쓰는 MCP 서버·원격 에이전트가 준비됐는지 (SCR-C01 ③) — 미설정이면 템플릿 비활성 + 사유."""
    g = G.normalize(pr.graph)
    servers = sorted({t.split(".", 1)[0] for t in G.tools_of(g) if "." in t})
    missing = [f"MCP {s}" for s in servers if (m := db.get(McpServer, s)) is None or m.status != "active"]
    missing += [f"원격 {a}" for a in G.remote_agents_of(g) if (x := db.get(A2AAgent, a)) is None or x.status != "active"]
    return missing


@router.get("/api/v1/chat/profiles")
def chat_profiles(_: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    """채팅 홈 프로파일·템플릿 카드 (SCR-C01 ②③) — 게시된 프로파일 + 준비 상태."""
    from app.services.setup import SAMPLE
    rows = db.scalars(select(AgentProfile).where(AgentProfile.status == "published")
                      .order_by(AgentProfile.template.desc(), AgentProfile.name))
    return {"items": [{"id": pr.id, "name": pr.name, "description": pr.description, "template": pr.template,
                       "missing": (m := readiness(db, pr)), "ready": not m, "sample": SAMPLE.get(pr.id)} for pr in rows]}


@router.post("/api/v1/runs/{rid}/cancel")
def cancel_run(rid: str, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    r = _visible_run(db, rid, p)
    if r.user_sub != p.sub:
        raise not_found("run")
    if not orch.cancel(rid):
        raise bad_request("이미 종료된 실행입니다.", code="run_closed")
    return {"run_id": rid, "status": "canceled"}


def _reconnecting(s: McpSession) -> bool:
    """수동 재연결 뒤 아직 다시 쓰이지 않았으면 재연결 중."""
    last = (s.events or [])[-1:] or [{}]
    if last[0].get("type") != "reconnect":
        return False
    from datetime import datetime
    at = datetime.fromisoformat(last[0]["at"])
    used = s.last_used_at
    return used is None or (used.replace(tzinfo=at.tzinfo) if used.tzinfo is None else used) <= at


def _host_row(db: Session, s: McpSession, servers: dict, runs: dict) -> dict:
    srv = servers.get(s.server_id)
    run = runs.get((s.client_info or {}).get("run"))
    tools = (srv.tools if srv else None) or []
    caps = (s.client_info or {}).get("_capabilities") or {}
    return {"id": s.id, "short": s.id[:8] + "…", "kind": s.kind, "server": s.server_id,
            "transport": ("stdio" if srv and srv.transport == "stdio" else "HTTP·GW") if s.server_id != "_all" else "HTTP·GW",
            "tools": len(tools) if s.server_id != "_all" else None, "principal": s.principal_sub,
            "protocol_version": s.protocol_version, "run_id": (s.client_info or {}).get("run"),
            "profile_id": run.profile_id if run else None, "test": bool(run and run.test),
            "client": (s.client_info or {}).get("name"), "capabilities": caps, "created_at": s.created_at,
            "last_used_at": s.last_used_at, "closed_at": s.closed_at, "reconnects": s.reconnects or 0,
            "state": "closed" if s.closed_at else ("reconnecting" if _reconnecting(s) else "open"),
            "events": (s.events or [])[-10:]}


@router.get("/api/v1/host/sessions", tags=["agents"])
def host_sessions(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db),
                  limit: int = Query(100, le=500), state: str | None = None, server: str | None = None):
    """MCP Host 세션·클라이언트 (SCR-D09) — KPI · 서버·전송·도구·상태 · 이벤트 · capabilities · 동의 대기."""
    stmt = select(McpSession).order_by(McpSession.created_at.desc()).limit(limit)
    if server:
        stmt = stmt.where(McpSession.server_id == server)
    rows = [s for s in db.scalars(stmt) if s.kind == "upstream" or (s.client_info or {}).get("name") == "agentnexus-host"]
    servers = {m.id: m for m in db.scalars(select(McpServer))}
    rids = [(s.client_info or {}).get("run") for s in rows if (s.client_info or {}).get("run")]
    runs = {r.id: r for r in db.scalars(select(Run).where(Run.id.in_(rids)))} if rids else {}
    items = [_host_row(db, s, servers, runs) for s in rows]
    if state:
        items = [i for i in items if i["state"] == state]
    consents = list(db.scalars(select(Approval).where(Approval.status == "pending",
                                                     Approval.kind.in_(("sampling", "elicitation", "tool_call")))
                                .order_by(Approval.created_at.desc()).limit(20)))
    live = [i for i in items if i["state"] != "closed"]
    return {"items": items, "kpi": {"active": len(live), "servers": len({i["server"] for i in live}),
                                    "reconnecting": sum(1 for i in live if i["state"] == "reconnecting"),
                                    "consents": len(consents), "tests": sum(1 for i in live if i["test"])},
            "pending_consents": [{"id": a.id, "kind": a.kind, "target": a.target, "run_id": a.run_id,
                                  "created_at": a.created_at} for a in consents]}


def _host_session(db: Session, sid: str) -> McpSession:
    s = db.get(McpSession, sid)
    if s is None:
        raise not_found("host session")
    return s


@router.post("/api/v1/host/sessions/{sid}/reconnect", tags=["agents"])
def host_reconnect(sid: str, p: Principal = Depends(require("mcp:write")), db: Session = Depends(get_db)):
    """수동 재연결 (SCR-D09 ⑨) — 업스트림 세션 캐시를 비워 다음 호출에서 initialize 부터 다시 맺는다."""
    from app.services import mcp_gateway as gw
    s = _host_session(db, sid)
    if s.closed_at:
        raise bad_request("종료된 세션입니다.", code="session_closed")
    gw._UPSTREAM_SESSIONS.pop((s.server_id, s.principal_sub), None)
    s.reconnects = (s.reconnects or 0) + 1
    s.events = (s.events or [])[-19:] + [{"type": "reconnect", "at": utcnow().isoformat(), "by": p.sub}]
    db.commit()
    audit.append("mcp.host.reconnect", actor=p.sub, target=sid, data={"server": s.server_id})
    return _host_row(db, s, {m.id: m for m in db.scalars(select(McpServer))}, {})


@router.delete("/api/v1/host/sessions/{sid}", tags=["agents"])
def host_end(sid: str, p: Principal = Depends(require("mcp:write")), db: Session = Depends(get_db)):
    """세션 종료 (SCR-D09 ⑨, MSG-002) — 게이트웨이 세션을 닫아 진행 중 호출 이후 요청을 거부한다."""
    from app.services import mcp_gateway as gw
    s = _host_session(db, sid)
    if s.closed_at:
        raise bad_request("이미 종료된 세션입니다.", code="session_closed")
    gw._UPSTREAM_SESSIONS.pop((s.server_id, s.principal_sub), None)
    s.closed_at = utcnow()
    s.events = (s.events or [])[-19:] + [{"type": "closed", "at": utcnow().isoformat(), "by": p.sub}]
    db.commit()
    audit.append("mcp.host.end", actor=p.sub, target=sid, data={"server": s.server_id})
    return {"closed": sid}


# ───────── 입력창 `/` 메뉴 — 프로파일이 쓰는 MCP 서버의 리소스·프롬프트 (SCR-C08 ①~④, FR-A03) ─────────
def _profile_servers(pid: str, p: Principal) -> tuple[list[str], str]:
    with SessionLocal() as db:
        pr = db.get(AgentProfile, pid)
        if pr is None or pr.status == "archived":
            raise not_found("agent profile")
        g = pr.graph or {}
        tools = list(pr.tools or []) + [t for w in (g.get("workers") or {}).values() for t in (w or {}).get("tools", [])]
        return sorted({t.split(".", 1)[0] for t in tools if "." in t}), g.get("data_class") or "internal"


def _mcp_ctx(p: Principal, data_class: str):
    from app.services.mcp_gateway import RpcContext
    return RpcContext(p, agent="host/slash-menu", data_class=data_class)


@router.get("/api/v1/chat/mcp/context")
async def mcp_context(profile_id: str, p: Principal = Depends(require("chat:use"))):
    """`/` 메뉴 목록 — 게이트웨이 경유 resources/list·prompts/list (권한·정책 밖 서버는 빠진다)."""
    from app.services import mcp_gateway as gw
    servers, dc = await run_in_threadpool(_profile_servers, profile_id, p)
    ctx = _mcp_ctx(p, dc)
    resources, prompts = [], []
    for sid in servers:
        for method, key, bucket in (("resources/list", "resources", resources), ("prompts/list", "prompts", prompts)):
            try:
                res = await gw.generic(sid, ctx, method, {})
            except (gw.RpcError, ApiError):
                continue
            bucket += [{**it, "server": sid} for it in res.get(key, [])]
    return {"servers": servers, "resources": resources, "prompts": prompts}


class McpResourceIn(BaseModel):
    profile_id: str
    server: str
    uri: str = Field(min_length=1, max_length=2000)


class McpPromptIn(BaseModel):
    profile_id: str
    server: str
    name: str
    arguments: dict[str, str] = Field(default_factory=dict)


async def _checked(pid: str, server: str, p: Principal):
    servers, dc = await run_in_threadpool(_profile_servers, pid, p)
    if server not in servers:
        raise bad_request("프로파일에 연결되지 않은 MCP 서버입니다.", code="server_not_in_profile")
    return _mcp_ctx(p, dc)


@router.post("/api/v1/chat/mcp/resource")
async def mcp_read_resource(body: McpResourceIn, p: Principal = Depends(require("chat:use"))):
    """리소스를 대화 컨텍스트(첨부)로 — resources/read 결과는 응답 소독 후 전달."""
    from app.services import mcp_gateway as gw
    ctx = await _checked(body.profile_id, body.server, p)
    try:
        res = await gw.generic(body.server, ctx, "resources/read", {"uri": body.uri})
    except gw.RpcError as exc:
        raise ApiError(403 if exc.code == gw.FORBIDDEN else 502, "mcp_error", exc.message) from exc
    contents = res.get("contents") or []
    text = "\n\n".join(c.get("text", "") for c in contents if "text" in c)
    return {"uri": body.uri, "server": body.server, "mime_type": (contents[0].get("mimeType") if contents else None)
            or "text/plain", "text": text[:MAX_ATTACHMENT]}


@router.post("/api/v1/chat/mcp/prompt")
async def mcp_get_prompt(body: McpPromptIn, p: Principal = Depends(require("chat:use"))):
    """프롬프트 템플릿을 인자와 함께 펼쳐 입력창에 넣을 텍스트로 — prompts/get (필수 인자는 서버가 검증)."""
    from app.services import mcp_gateway as gw
    ctx = await _checked(body.profile_id, body.server, p)
    try:
        res = await gw.generic(body.server, ctx, "prompts/get", {"name": body.name, "arguments": body.arguments})
    except gw.RpcError as exc:
        raise ApiError(400 if exc.code == gw.INVALID_PARAMS else 502, "mcp_error", exc.message) from exc
    texts = []
    for m in res.get("messages", []):
        c = m.get("content")
        texts += [x.get("text", "") for x in (c if isinstance(c, list) else [c]) if isinstance(x, dict) and "text" in x]
    return {"name": body.name, "server": body.server, "description": res.get("description"), "text": "\n".join(texts)}
