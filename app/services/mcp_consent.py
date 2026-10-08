"""MCP 서버 → 클라이언트 요청 (FR-A00·A03·G09, SCR-C08) — sampling/createMessage · elicitation/create · roots/list · ping.

업스트림 MCP 서버가 도구 호출 도중 SSE 역채널(MCP 2025-11-25)로 요청을 보내면 게이트웨이가 받는다.
- 중계: 하류 MCP 클라이언트가 해당 능력을 선언하고 SSE 로 호출했으면 요청을 하류로 그대로 보내고 답을 돌려준다(`Relay`).
- Host 동의: 그 외(플랫폼 오케스트레이션·능력 없는 클라이언트)는 요청자에게 동의 카드를 띄운다. sampling 은 사용자가 허용(또는
  프롬프트 편집 후 허용)하면 게이트웨이가 Unified Model API 로 실행한다 — 모델은 서버가 아니라 게이트웨이가 정하고 내부 데이터는
  로컬 모델로만(MSG-011). 선택 시 응답을 서버로 보내기 전에 검토한다. elicitation 은 요청 스키마로 검증한 사용자 입력을 돌려준다.
무응답은 MCP_CONSENT_TIMEOUT_S(기본 60초) 후 자동 거부하고, 요청·결정·결과는 감사 로그에 남긴다.
"""
import asyncio
import time
from datetime import timedelta

import jsonschema
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.errors import ApiError, bad_request, forbidden
from app.core.ids import new_id
from app.db import SessionLocal, utcnow
from app.models import Approval, McpRelay, RunEvent
from app.services import audit, governance, guards, llm

CONSENT_KINDS = {"sampling/createMessage": "sampling", "elicitation/create": "elicitation"}
USER_REJECTED = -1  # MCP 사양: 사용자 거부
NOT_FOUND = -32601
WAIT_STATES = {"sampling": {"pending", "approved", "review", "sent"}, "elicitation": {"pending"}}


class ConsentError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


async def handle(server_id: str, ctx, msg: dict) -> dict:
    """업스트림이 보낸 JSON-RPC 요청 → 업스트림에 돌려줄 JSON-RPC 응답."""
    rid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    try:
        if method == "ping":
            result = {}
        elif method == "roots/list":
            result = {"roots": []}  # 게이트웨이는 파일 시스템 루트를 노출하지 않는다
        elif method in CONSENT_KINDS:
            if ctx is None:
                raise ConsentError(USER_REJECTED, "동의 채널이 없는 시스템 호출에서는 허용하지 않습니다.")
            relay = getattr(ctx, "relay", None)
            if method == "sampling/createMessage":
                await _sampling_allowed(server_id, ctx)  # 중계(클라이언트가 직접 샘플링)여도 서버별 정책은 게이트웨이가 (D19-④)
            if relay is not None and relay.supports(method):
                result = await relay.request(method, params)
                await run_in_threadpool(lambda: audit.append(
                    "mcp.relay", actor=ctx.principal.sub, actor_kind=ctx.principal.kind, target=f"{server_id}:{method}",
                    decision="relayed", data={"session": relay.session_id}))
            elif method == "sampling/createMessage":
                result = await _sampling(server_id, ctx, params)
            else:
                result = await _elicitation(server_id, ctx, params)
        else:
            raise ConsentError(NOT_FOUND, f"Method not found: {method}")
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    except ConsentError as exc:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": exc.code, "message": exc.message}}
    except Exception as exc:  # noqa: BLE001 — 업스트림이 응답을 못 받고 시간 초과까지 기다리지 않게 오류로 답한다
        from app.core import logs
        logs.event("error", "gateway", "서버→클라이언트 요청 처리 실패", server=server_id, method=method, error=type(exc).__name__)
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": f"게이트웨이 내부 오류: {type(exc).__name__}"}}


# ───────────────────────── Host 동의 ─────────────────────────
def _text_of(content) -> str:
    parts = content if isinstance(content, list) else [content]
    out = []
    for c in parts:
        if isinstance(c, dict):
            out.append(c.get("text") if c.get("type") == "text" else f"[{c.get('type', 'content')} 생략]")
        elif isinstance(c, str):
            out.append(c)
    return "\n".join(x for x in out if x)


def _chat_messages(params: dict, messages: list | None) -> list[dict]:
    """MCP SamplingMessage(또는 화면에서 편집한 {role, text}) → OpenAI 형식."""
    out = [{"role": "system", "content": params["systemPrompt"]}] if params.get("systemPrompt") else []
    for m in messages if messages is not None else params.get("messages") or []:
        text = m.get("text") if "text" in m else _text_of(m.get("content"))
        out.append({"role": "assistant" if m.get("role") == "assistant" else "user", "content": text or ""})
    return out


def _body(params: dict, messages: list | None = None) -> dict:
    body = {"model": "auto", "messages": _chat_messages(params, messages),
            "max_tokens": params["maxTokens"] if params.get("maxTokens") is not None else 256}  # 형식 확인은 llm.prepare (400, 65th)
    if params.get("temperature") is not None:
        body["temperature"] = params["temperature"]
    if params.get("stopSequences"):
        body["stop"] = params["stopSequences"]
    return body


def _cctx(server_id: str, ctx) -> llm.CallContext:
    return llm.CallContext(ctx.principal, ctx.data_class or "internal", f"mcp:{server_id}/sampling",
                           ctx.session_id, ctx.run_id)


def _preview(server_id: str, ctx, params: dict) -> dict:
    """동의 카드에 보여 줄 라우팅 — 실제 실행과 같은 prepare 로 계산."""
    with SessionLocal() as db:
        try:
            plan = llm.prepare(db, _cctx(server_id, ctx), _body(params))
        except ApiError as exc:
            return {"error": exc.detail, "data_class": ctx.data_class}
        llm.discard(plan)  # 미리 보기 — 한도 예약을 남기지 않는다 (D22-③)
        return {"model": plan.targets[0].model_id, "category": plan.targets[0].category, "data_class": plan.data_class,
                "rerouted": plan.rerouted, "hints": [h.get("name") for h in
                                                     (params.get("modelPreferences") or {}).get("hints") or []]}


def _event(db, ap: Approval, type_: str, data: dict) -> None:
    if ap.run_id:
        db.add(RunEvent(run_id=ap.run_id, type=type_, data={"approval_id": ap.id, "server": ap.server_id,
                                                            "step_id": ap.step_id, **data}))


def _open(kind: str, server_id: str, ctx, params: dict, result: dict | None) -> str:
    timeout = settings.mcp_consent_timeout_s
    with SessionLocal() as db:
        ap = Approval(id=new_id("apr"), kind=kind, status="pending", requester_sub=ctx.principal.sub,
                      session_id=ctx.session_id, run_id=ctx.run_id, step_id=ctx.step_id, server_id=server_id,
                      target=f"{server_id}:{kind}", args=params, args_hash=governance.args_hash(params), risk="medium",
                      approver="requester", policy_ref="mcp-consent",
                      reason=("MSG-016 서버가 모델 호출(샘플링)을 요청합니다. 프롬프트를 확인하고 허용하세요." if kind == "sampling"
                              else "MSG-017 서버가 추가 입력을 요청합니다."),
                      expires_at=utcnow() + timedelta(seconds=timeout), result=result)
        db.add(ap)
        db.flush()
        _event(db, ap, f"{kind}.required", {"params": params, "routing": (result or {}).get("routing"),
                                            "expires_at": ap.expires_at.isoformat(), "extend_step_s": int(timeout),
                                            "max_extensions": governance.MAX_EXTENSIONS})
        db.commit()
        aid = ap.id
    audit.append("mcp.consent.request", actor=ctx.principal.sub, actor_kind=ctx.principal.kind,
                 target=f"{server_id}:{kind}", data={"approval_id": aid, "run": ctx.run_id})
    return aid


def _snapshot(aid: str) -> dict:
    with SessionLocal() as db:
        ap = db.get(Approval, aid)
        if ap.status in ("pending", "review") and ap.expires_at and ap.expires_at < utcnow():
            prev, ap.status = ap.status, "expired"
            _event(db, ap, f"{ap.kind}.resolved", {"status": "expired", "phase": prev})
            db.commit()
            audit.append("mcp.consent.expire", actor="system", target=ap.target, decision="expired",
                         data={"approval_id": ap.id, "phase": prev})
        return {"status": ap.status, "payload": ap.payload or {}, "result": ap.result or {}}


async def _wait(aid: str, phase: str) -> dict:
    """phase 상태를 벗어날 때까지 대기 — 워커 간 공유를 위해 DB 를 폴링한다."""
    while True:
        snap = await run_in_threadpool(_snapshot, aid)
        if snap["status"] != phase:
            return snap
        await asyncio.sleep(0.2)


def _set(aid: str, status: str | None = None, result: dict | None = None, event: tuple[str, dict] | None = None,
         expires_in: float | None = None) -> None:
    with SessionLocal() as db:
        ap = db.get(Approval, aid)
        if status:
            ap.status = status
        if result is not None:
            ap.result = {**(ap.result or {}), **result}
        if expires_in is not None:
            ap.expires_at = utcnow() + timedelta(seconds=expires_in)
        if event:
            _event(db, ap, *event)
        db.commit()


async def _generate(server_id: str, ctx, params: dict, messages: list | None) -> tuple[str, str]:
    body = _body(params, messages)

    def _prep():
        with SessionLocal() as db:
            c = _cctx(server_id, ctx)
            return c, llm.prepare(db, c, body)
    cctx, plan, t0 = None, None, time.perf_counter()

    def _failed(attempts: list):  # 실행 실패도 라우팅 결정·실패 지표로 남기고 예약·진행 중 슬롯 반환 (D22-③·65th)
        with SessionLocal() as db:
            llm.finalize(db, cctx, plan, None, None, (time.perf_counter() - t0) * 1000, attempts, "error")
    try:
        cctx, plan = await run_in_threadpool(_prep)
        t0 = time.perf_counter()
        res, tgt, attempts = await llm.execute(plan, body)
    except ApiError as exc:
        if plan is not None:
            await run_in_threadpool(_failed, exc.extra.get("attempts", []))
        raise ConsentError(USER_REJECTED, f"게이트웨이가 샘플링 모델 호출을 거부했습니다: {exc.detail}") from exc
    except BaseException:  # 취소·예상 밖 오류 (65th)
        if plan is not None:
            llm.discard(plan)
        raise

    def _fin():
        with SessionLocal() as db:
            text = ((res.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            text = guards.scan_output(db, text).text
            llm.finalize(db, cctx, plan, tgt, res.get("usage"), (time.perf_counter() - t0) * 1000, attempts)
            return text
    return await run_in_threadpool(_fin), tgt.model_id


def _sampling_policy(server_id: str) -> str:
    from app.models import McpServer
    with SessionLocal() as db:
        s = db.get(McpServer, server_id)
        return (s.sampling_policy if s is not None else None) or "ask"


async def _sampling_allowed(server_id: str, ctx) -> None:
    if await run_in_threadpool(_sampling_policy, server_id) == "deny":  # 서버별 샘플링 정책 (SCR-F02)
        await run_in_threadpool(lambda: audit.append("mcp.sampling.deny", actor=ctx.principal.sub, target=server_id,
                                                     decision="deny", data={"reason": "server_policy"}))
        raise ConsentError(USER_REJECTED, f"{server_id} 서버의 샘플링은 운영 정책으로 막혀 있습니다.")


async def _sampling(server_id: str, ctx, params: dict) -> dict:
    routing = await run_in_threadpool(_preview, server_id, ctx, params)
    aid = await run_in_threadpool(_open, "sampling", server_id, ctx, params, {"routing": routing})
    snap = await _wait(aid, "pending")
    if snap["status"] != "approved":
        raise ConsentError(USER_REJECTED, "사용자가 샘플링 요청을 거부했습니다." if snap["status"] == "rejected"
                           else "동의 대기 시간이 지나 자동 거부했습니다.")
    payload = snap["payload"]
    text, model = await _generate(server_id, ctx, params, payload.get("messages"))
    if payload.get("review"):  # 응답을 서버로 보내기 전에 사용자 검토 (SCR-C08 ⑦)
        until = (utcnow() + timedelta(seconds=settings.mcp_consent_timeout_s)).isoformat()
        await run_in_threadpool(_set, aid, "review", {"draft": {"text": text, "model": model}},
                                ("sampling.review", {"draft": text, "model": model, "expires_at": until}),
                                settings.mcp_consent_timeout_s)
        snap = await _wait(aid, "review")
        if snap["status"] != "sent":
            raise ConsentError(USER_REJECTED, "사용자가 응답 검토에서 전송을 거부했습니다." if snap["status"] == "rejected"
                               else "응답 검토 대기 시간이 지나 자동 거부했습니다.")
        text = snap["payload"].get("text") or text
    await run_in_threadpool(_set, aid, "completed", {"final": {"text": text, "model": model}},
                            ("sampling.resolved", {"status": "completed", "model": model}))
    await run_in_threadpool(lambda: audit.append(
        "mcp.sampling", actor=ctx.principal.sub, actor_kind=ctx.principal.kind, target=f"{server_id}:sampling",
        decision="allow", data={"approval_id": aid, "model": model, "edited": bool(payload.get("messages")),
                                "reviewed": bool(payload.get("review")), "data_class": ctx.data_class}))
    return {"role": "assistant", "content": {"type": "text", "text": text}, "model": model, "stopReason": "endTurn"}


async def _elicitation(server_id: str, ctx, params: dict) -> dict:
    aid = await run_in_threadpool(_open, "elicitation", server_id, ctx, params, None)
    snap = await _wait(aid, "pending")
    payload = snap["payload"]
    action = payload.get("action") or ("accept" if snap["status"] == "approved" else
                                       "cancel" if snap["status"] == "expired" else "decline")
    await run_in_threadpool(_set, aid, None, {"response": {"action": action}},
                            ("elicitation.resolved", {"status": snap["status"], "action": action}))
    return {"action": action, **({"content": payload.get("content") or {}} if action == "accept" else {})}


def decide(db, ap: Approval, p, decision: str, *, messages: list | None = None, review: bool = False,
           text: str | None = None, content: dict | None = None, note: str | None = None) -> Approval:
    """동의 카드 결정 (POST /api/v1/approvals/{id}/consent)."""
    if ap.kind not in ("sampling", "elicitation"):
        raise bad_request("MCP 동의 요청이 아닙니다 — /approve·/reject 를 쓰세요.", code="not_consent")
    if ap.status in ("pending", "review") and ap.expires_at and ap.expires_at < utcnow():
        ap.status = "expired"
        db.commit()
    if not governance.can_decide(ap, p):
        raise forbidden("이 동의 요청을 처리할 권한이 없습니다.", code="approval_forbidden")
    if ap.kind == "sampling" and ap.status == "pending" and decision in ("allow", "deny"):
        ap.status = "approved" if decision == "allow" else "rejected"
        ap.payload = {"messages": messages, "review": review} if decision == "allow" else {}
    elif ap.kind == "sampling" and ap.status == "review" and decision in ("send", "deny"):
        ap.status = "sent" if decision == "send" else "rejected"
        ap.payload = {**(ap.payload or {}), "text": text}
    elif ap.kind == "elicitation" and ap.status == "pending" and decision in ("accept", "decline", "cancel"):
        if decision == "accept":
            schema = (ap.args or {}).get("requestedSchema") or {"type": "object"}
            try:
                jsonschema.validate(content or {}, schema)
            except jsonschema.ValidationError as exc:
                raise bad_request(f"입력이 요청 형식과 맞지 않습니다: {exc.message}",
                                  code="invalid_elicitation") from exc
        ap.status = "approved" if decision == "accept" else "rejected"
        ap.payload = {"action": decision, "content": content if decision == "accept" else None}
    else:
        raise bad_request(f"지금 처리할 수 없는 결정입니다 ({ap.kind}·{ap.status}·{decision}).", code="approval_closed")
    ap.decided_by, ap.decided_at, ap.decision_note = p.sub, utcnow(), note
    db.commit()
    audit.append("approval.consent", actor=p.sub, target=ap.target, decision=decision,
                 data={"approval_id": ap.id, "kind": ap.kind, "edited": bool(messages) or bool(text)})
    return ap


# ───────────────────────── 하류 중계 ─────────────────────────
class Relay:
    """하류 MCP 클라이언트(능력 선언·SSE 호출)로 서버→클라이언트 요청을 중계.

    응답 POST 가 다른 워커로 들어와도 DB(mcp_relays)로 만난다."""

    def __init__(self, session_id: str, server_id: str, capabilities: dict, queue: asyncio.Queue | None):
        self.session_id, self.server_id, self.capabilities, self.queue = session_id, server_id, capabilities, queue

    def supports(self, method: str) -> bool:
        return CONSENT_KINDS.get(method) in self.capabilities

    async def request(self, method: str, params: dict) -> dict:
        rid = new_id("rly")

        def _store():
            with SessionLocal() as db:
                db.add(McpRelay(id=rid, session_id=self.session_id, server_id=self.server_id, method=method,
                                params=params))
                db.commit()
        await run_in_threadpool(_store)
        if self.queue is not None:  # SSE 중계(2025-11-25). 없으면 MRTR(2026-07-28) — 요청은 InputRequiredResult 로 나간다
            await self.queue.put({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + settings.mcp_consent_timeout_s

        def _answer():
            with SessionLocal() as db:
                row = db.get(McpRelay, rid)
                return row.response if row else None
        while time.monotonic() < deadline:
            res = await run_in_threadpool(_answer)
            if res is not None:
                if "error" in res:
                    e = res["error"] or {}
                    raise ConsentError(e.get("code", USER_REJECTED), e.get("message", "client error"))
                return res.get("result") or {}
            await asyncio.sleep(0.2)
        raise ConsentError(USER_REJECTED, "하류 클라이언트가 제한 시간 안에 응답하지 않았습니다.")


def answer(session_id: str, body: dict) -> bool:
    """하류 클라이언트가 POST 한 JSON-RPC 응답을 대기 중인 중계 요청에 연결."""
    with SessionLocal() as db:
        row = db.get(McpRelay, str(body.get("id")))
        if row is None or row.session_id != session_id or row.response is not None:
            return False
        row.response = {k: body[k] for k in ("result", "error") if k in body}
        row.answered_at = utcnow()
        db.commit()
        return True
