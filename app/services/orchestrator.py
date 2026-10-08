"""Local Agent Orchestration (FR-A00~A02, FR-W02·W05) — Agent Application / MCP Host.

Planner(LLM 구조화 출력 또는 템플릿) → Retriever(MCP 검색) · Summarize(LLM 요약·근거 인용) · Executor(MCP 쓰기·승인)
· Remote(A2A 위임) 를 DAG 로 병렬 팬아웃·합류한다. 모든 외부 호출은 게이트웨이(HTTP, GATEWAY_URL)를 경유하며
사용자 신원은 위임 토큰(sub=사용자, act=host)으로 전파된다. 실행 상태·이벤트는 DB 에 영속(워커 간 재개·SSE 재생).
"""
import asyncio
import contextlib
import json
import re
import time
import uuid

import httpx
from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from opentelemetry import context as otel_context
from opentelemetry.trace import SpanKind

from app.core import logs, metrics, tracing
from app.core.config import settings
from app.core.context import current_trace_id
from app.core.http import async_client
from app.core.ids import new_id
from app.db import SessionLocal, utcnow
from app.models import AgentProfile, Approval, ChatSession, Message, Run, RunEvent, RunStep
from app.services import a2a_proto as ap
from app.services import graph as G
from app.services import identity

TERMINAL = {"succeeded", "partial", "failed", "canceled"}
LOOP: asyncio.AbstractEventLoop | None = None  # lifespan 이 설정 — 승인 훅(스레드)에서 재개 예약
_TASKS: set[asyncio.Task] = set()
_LOCKS: dict[str, asyncio.Lock] = {}


# ───────────────────────── 오류 분류 ─────────────────────────
class StepError(Exception):
    def __init__(self, message: str, retryable: bool = False, data: dict | None = None):
        super().__init__(message)
        self.message, self.retryable, self.data = message, retryable, data or {}


class ApprovalNeeded(Exception):
    def __init__(self, approval_id: str, reason: str, data: dict):
        super().__init__(reason)
        self.approval_id, self.reason, self.data = approval_id, reason, data


class InputNeeded(Exception):
    """원격 에이전트가 추가 입력을 요청(input-required) — 같은 Task·contextId 로 답하면 재개 (FR-R03·R04, SCR-C05)."""
    def __init__(self, task_id: str, context_id: str | None, question: str):
        super().__init__(question)
        self.task_id, self.context_id, self.question = task_id, context_id, question


# ───────────────────────── 게이트웨이 클라이언트 (MCP Client Layer · A2A Client · LLM) ─────────────────────────
class GatewayClient:
    def __init__(self, run: dict):
        self.run = run
        self.base = settings.gateway_url.rstrip("/")
        self.token = identity.delegation_token(run["principal"], actor="agentnexus-host", ttl=1800)
        self.mcp_session: str | None = None

    def headers(self, node: str, step_id: str | None = None) -> dict:
        return {"Authorization": f"Bearer {self.token}", "X-AgentNexus-Agent": f"{self.run['profile_id']}/{node}",
                "X-AgentNexus-Run": self.run["id"], "X-AgentNexus-Session": self.run.get("session_id") or "",
                "X-AgentNexus-Data-Class": self.run["data_class"], **({"X-AgentNexus-Step": step_id} if step_id else {})}

    async def _post(self, path: str, body: dict, headers: dict, timeout: float = 120) -> httpx.Response:
        try:
            async with async_client(timeout=timeout) as http:
                return await http.post(self.base + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise StepError(f"게이트웨이 연결 실패: {type(exc).__name__}", retryable=True) from exc

    # MCP Host: 실행 단위 클라이언트 세션 (FR-A00)
    async def mcp_open(self) -> None:
        res = await self._post("/mcp/_all", {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "agentnexus-host", "version": "0.1.0", "run": self.run["id"]}}},
            {**self.headers("host"), "Accept": "application/json, text/event-stream"})
        self.mcp_session = res.headers.get("mcp-session-id") if res.status_code == 200 else None

    async def mcp_close(self) -> None:
        if not self.mcp_session:
            return
        with contextlib.suppress(Exception):
            async with async_client(timeout=10) as http:
                await http.delete(self.base + "/mcp/_all", headers={**self.headers("host"),
                                                                    "Mcp-Session-Id": self.mcp_session})

    async def mcp_tools(self, node: str) -> list[dict]:
        res = await self._post("/mcp/_all", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, self._mcp_hdr(node))
        return (res.json().get("result") or {}).get("tools", []) if res.status_code == 200 else []

    def _mcp_hdr(self, node: str, step_id: str | None = None, approval_id: str | None = None) -> dict:
        h = {**self.headers(node, step_id), "Accept": "application/json, text/event-stream"}
        if self.mcp_session:
            h["Mcp-Session-Id"] = self.mcp_session
        if approval_id:
            h["X-AgentNexus-Approval"] = approval_id
        return h

    async def mcp_call(self, node: str, step_id: str, tool: str, args: dict, approval_id: str | None) -> dict:
        res = await self._post("/mcp/_all", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                             "params": {"name": tool, "arguments": args}},
                               self._mcp_hdr(node, step_id, approval_id))
        if res.status_code >= 500:
            raise StepError(f"게이트웨이 오류 {res.status_code}", retryable=True)
        body = res.json()
        if "error" in body:
            err = body["error"]
            data = err.get("data") or {}
            if err.get("code") == -32001 and data.get("approval_id"):
                raise ApprovalNeeded(data["approval_id"], err.get("message", "승인 필요"), data)
            if err.get("code") == -32005:  # 게이트웨이 요청 한도 (MSG-012) — 재시도 가능 시각 포함
                data = {**data, "code": "rate_limited"}
            raise StepError(err.get("message", "도구 호출 실패"), retryable=err.get("code") == -32002, data=data)
        result = body.get("result") or {}
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
            raise StepError(f"도구 오류: {text[:300]}", retryable=False)
        return result

    async def chat(self, node: str, step_id: str | None, model: str, messages: list[dict], *,
                   response_format: dict | None = None, on_delta=None, max_tokens: int | None = None) -> tuple[str, dict]:
        body = {"model": model, "messages": messages, **({"response_format": response_format} if response_format
                                                         else {}), **({"max_tokens": max_tokens} if max_tokens else {})}
        hdr = self.headers(node, step_id)
        if on_delta is None:
            res = await self._post("/v1/chat/completions", body, hdr)
            if res.status_code >= 500 or res.status_code == 429:
                raise StepError(f"모델 호출 실패 {res.status_code}: {res.text[:200]}", retryable=True)
            if res.status_code >= 400:
                raise StepError(f"모델 호출 거부 {res.status_code}: {res.json().get('error', {}).get('message')}")
            out = res.json()
            return out["choices"][0]["message"].get("content") or "", {"model": out.get("model"),
                                                                          "usage": out.get("usage")}
        text, model_used = [], model
        try:
            async with async_client(timeout=180) as http:
                async with http.stream("POST", self.base + "/v1/chat/completions", json={**body, "stream": True},
                                       headers=hdr) as res:
                    if res.status_code >= 400:
                        await res.aread()
                        raise StepError(f"모델 호출 실패 {res.status_code}", retryable=res.status_code >= 500)
                    async for line in res.aiter_lines():
                        if not line.startswith("data: ") or line == "data: [DONE]":
                            continue
                        chunk = json.loads(line[6:])
                        if "error" in chunk:
                            raise StepError(chunk["error"].get("message", "stream error"), retryable=True)
                        model_used = chunk.get("model", model_used)
                        for ch in chunk.get("choices", []):
                            d = (ch.get("delta") or {}).get("content")
                            if d:
                                text.append(d)
                                await on_delta(d)
        except httpx.HTTPError as exc:
            raise StepError(f"모델 스트림 실패: {type(exc).__name__}", retryable=True) from exc
        return "".join(text), {"model": model_used}

    async def _a2a(self, step_id: str, agent_id: str, method: str, params: dict) -> dict:
        body = {"jsonrpc": "2.0", "id": 3, "method": method, "params": params}
        res = await self._post(f"/a2a/{agent_id}", body, self.headers("remote", step_id), timeout=180)
        if res.status_code >= 500:
            raise StepError(f"원격 에이전트 호출 실패 {res.status_code}", retryable=True)
        out = res.json()
        if "error" in out:
            raise StepError(out["error"].get("message", "A2A 오류"), retryable=out["error"].get("code") == -32603,
                            data=out["error"].get("data"))
        return ap.result_from(out.get("result") or {})

    async def a2a_send(self, step_id: str, agent_id: str, text: str, data: dict | None, *,
                       task_id: str | None = None, context_id: str | None = None) -> dict:
        """SendMessage (A2A 1.0 기본, D15-②) — task_id·context_id 를 주면 같은 Task 에 이어서 답한다(input-required 재개).
        업스트림이 0.3 만 지원하면 게이트웨이가 변환한다. 결과는 정준형(0.3 JSON)으로 받아 쓴다."""
        parts = [{"kind": "text", "text": text}] + ([{"kind": "data", "data": data}] if data else [])
        msg = {"kind": "message", "messageId": uuid.uuid4().hex, "role": "user", "parts": parts,
               **({"taskId": task_id} if task_id else {}), **({"contextId": context_id} if context_id else {})}
        return await self._a2a(step_id, agent_id, "SendMessage", {"message": ap.message_to_v10(msg)})

    async def a2a_wait(self, step_id: str, agent_id: str, task: dict) -> dict:
        """원격 Task 가 submitted·working 이면 tasks/get 으로 진행을 따라간다 (최대 A2A_REMOTE_WAIT_S)."""
        deadline, delay = time.monotonic() + settings.a2a_remote_wait_s, 0.5
        while task.get("kind") == "task" and (task.get("status") or {}).get("state") in ("submitted", "working"):
            if time.monotonic() >= deadline:
                raise StepError(f"원격 Task {task.get('id')} 가 {settings.a2a_remote_wait_s:g}초 안에 끝나지 않았습니다 — "
                                "다시 시도하면 같은 요청을 새로 위임합니다.", data={"task_id": task.get("id"), "state": "working"})
            await asyncio.sleep(delay)
            delay = min(delay * 2, 3)
            task = await self._a2a(step_id, agent_id, "GetTask", {"id": task["id"]})
        return task

    async def rag_search(self, step_id: str, kb: str, query: str, top_k: int) -> list[dict]:
        """RAG (FR-A02.2) — 지식 베이스 검색도 게이트웨이(API) 경유: 같은 위임 신원·데이터 등급으로 임베딩·권한 판정."""
        res = await self._post(f"/api/v1/knowledge-bases/{kb}/search", {"query": query, "top_k": top_k},
                               self.headers("retriever", step_id))
        if res.status_code >= 500:
            raise StepError(f"지식 베이스 검색 실패 {res.status_code}", retryable=True)
        if res.status_code >= 400:
            body = res.json()
            raise StepError(f"지식 베이스 검색 거부 {res.status_code}: {body.get('detail') or body.get('title')}")
        return res.json().get("items", [])


# ───────────────────────── DB 헬퍼 ─────────────────────────
def _emit(run_id: str, type_: str, data: dict | None = None) -> None:
    with SessionLocal() as db:
        db.add(RunEvent(run_id=run_id, type=type_, data=data or {}))
        db.commit()


async def emit(run_id: str, type_: str, data: dict | None = None) -> None:
    await run_in_threadpool(_emit, run_id, type_, data)


def _run_dict(r: Run) -> dict:
    return {"id": r.id, "session_id": r.session_id, "profile_id": r.profile_id, "principal": r.principal,
            "data_class": r.data_class, "request": r.request, "graph": r.graph, "status": r.status, "test": r.test,
            "dry_run": bool(r.dry_run)}


RESUMABLE = ("waiting_approval", "waiting_input", "recovering")


def _claim(run_id: str, from_states: tuple[str, ...]) -> dict | None:
    """원자적 상태 전이(CAS) — 같은 실행을 두 워커가 동시에 돌리지 않도록."""
    with SessionLocal() as db:
        res = db.execute(update(Run).where(Run.id == run_id, Run.status.in_(from_states))
                         .values(status="running", started_at=utcnow(), heartbeat_at=utcnow()))
        db.commit()
        if res.rowcount != 1:
            return None
        return _run_dict(db.get(Run, run_id))


def _steps(run_id: str) -> list[RunStep]:
    with SessionLocal() as db:
        return list(db.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)))


def _set_step(step_id: str, **fields) -> None:
    with SessionLocal() as db:
        st = db.get(RunStep, step_id)
        for k, v in fields.items():
            setattr(st, k, v)
        db.commit()


def _finish(run_id: str, status: str, result: str | None, citations: list, error: str | None = None,
            parts: list | None = None) -> None:
    with SessionLocal() as db:
        r = db.get(Run, run_id)
        r.status, r.result, r.citations, r.error, r.finished_at = status, result, citations, error, utcnow()
        if r.session_id and result is not None:
            db.add(Message(id=new_id("msg"), session_id=r.session_id, role="assistant", content=result,
                           parts=parts or [], run_id=r.id))
            s = db.get(ChatSession, r.session_id)
            if s:
                s.updated_at = utcnow()
        db.commit()


# ───────────────────────── 시작 · 계획 ─────────────────────────
def start_run(principal, *, profile_id: str, request: str, session_id: str | None = None,
              data_class: str | None = None, test: bool = False, dry_run: bool = False,
              version: int | None = None) -> str:
    """Run 생성(동기·DB). 실행은 schedule() 로 비동기 시작. version 이면 그 게시 버전 그래프로 (SCR-D08 ②)."""
    with SessionLocal() as db:
        prof = db.get(AgentProfile, profile_id)
        if prof is None:
            raise ValueError(f"에이전트 프로파일 없음: {profile_id}")
        graph, pver = prof.graph, prof.version
        if version is not None:
            from app.models import AgentProfileVersion
            v = db.scalars(select(AgentProfileVersion).where(AgentProfileVersion.profile_id == profile_id,
                                                             AgentProfileVersion.version == version)).first()
            if v is None:
                raise ValueError(f"프로파일 버전 없음: v{version}")
            graph, pver = v.graph, v.version
        g = G.normalize(graph)
        run = Run(id=new_id("run"), session_id=session_id, profile_id=profile_id, user_sub=principal.sub,
                  principal=principal.snapshot(), status="queued", request=request, graph=g,
                  profile_version=pver, data_class=data_class or g["data_class"], trace_id=current_trace_id(),
                  test=test, dry_run=dry_run)
        db.add(run)
        db.commit()
        return run.id


def schedule(run_id: str, resume: bool = False) -> None:
    coro = execute(run_id, resume=resume)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        t = loop.create_task(coro)
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)
    elif LOOP is not None:
        asyncio.run_coroutine_threadsafe(coro, LOOP)
    else:  # pragma: no cover
        coro.close()
        raise RuntimeError("이벤트 루프 없음")


_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def render(value, ctx: dict, keep_unknown: bool = False):
    """{{request}}·{{sources}}·{{sN.text}} 치환. keep_unknown 이면 아직 없는 단계 출력 참조를 보존한다(계획 저장 시)."""
    if isinstance(value, str):
        def sub(m):
            key = m.group(1)
            if key in ctx:
                return str(ctx[key])
            sid, _, attr = key.partition(".")
            out = ctx.get("outputs", {}).get(sid)
            if out is None:
                return m.group(0) if keep_unknown else ""
            return str(out.get(attr or "text", ""))
        return _VAR.sub(sub, value)
    if isinstance(value, list):
        return [render(v, ctx, keep_unknown) for v in value]
    if isinstance(value, dict):
        return {k: render(v, ctx, keep_unknown) for k, v in value.items()}
    return value


PLAN_SCHEMA = {"type": "object", "required": ["steps"], "properties": {"steps": {"type": "array", "items": {
    "type": "object", "required": ["id", "agent"], "properties": {
        "id": {"type": "string"}, "agent": {"enum": list(G.AGENTS)}, "tool": {"type": "string"},
        "kb": {"type": "string"}, "remote_agent": {"type": "string"}, "args": {"type": "object"},
        "when": {"type": "string"}, "depends_on": {"type": "array", "items": {"type": "string"}}}}}}}


async def plan(gw: GatewayClient, run: dict) -> tuple[list[dict], dict]:
    g = run["graph"]
    note: dict = {"mode": g["planner"]["mode"]}
    if g["planner"]["mode"] == "llm":
        pl = g["planner"]
        tools = await gw.mcp_tools("planner")
        tool_lines = "\n".join(f"- {t['name']}: {(t.get('description') or '')[:100]}" for t in tools)
        remotes = ", ".join(a for a in G.remote_agents_of(g) if not pl.get("assign") or a in pl["assign"]) or "(없음)"
        assign = ", ".join(pl.get("assign") or []) or "retriever·summarize·executor·remote"
        rules = f"최대 {pl['max_steps']}단계" + (f", 의존 깊이 최대 {pl['max_depth']}" if pl.get("max_depth") else "") + \
            f". 배정 대상: {assign}. 출력은 plan.v1 JSON 스키마."
        if pl.get("prompt"):  # 분해 프롬프트 (SCR-D03 ④) — {request}·{tools}·{agents} 자동 주입
            sys = str(pl["prompt"]).replace("{request}", run["request"]).replace("{tools}", tool_lines or "(없음)") \
                .replace("{agents}", remotes) + "\n" + rules
        else:
            sys = ("너는 Planner 에이전트다. 사용자 요청을 하위 작업으로 분해해 JSON 계획을 만든다. agent 는 retriever(검색 도구), "
                   f"summarize(요약), executor(쓰기 도구), remote(원격 에이전트). {rules}\n사용 가능 도구:\n"
                   f"{tool_lines or '(없음)'}\n원격 에이전트: {remotes}")
        msgs = [{"role": "system", "content": sys}, {"role": "user", "content": run["request"]}]
        fmt = {"type": "json_schema", "json_schema": {"name": "plan", "schema": PLAN_SCHEMA}}
        try:
            try:
                text, _ = await gw.chat("planner", None, pl["model"], msgs, response_format=fmt)
            except StepError:
                if not pl.get("fallback_model"):
                    raise
                note["fallback_model"] = pl["fallback_model"]  # 계획 모델 장애 → 폴백 별칭 (SCR-D03 ③, MSG-015)
                text, _ = await gw.chat("planner", None, pl["fallback_model"], msgs, response_format=fmt)
            steps = (json.loads(text) or {}).get("steps") or []
            errors = G.validate_plan(g, steps, "plan") if steps else [{"path": "plan", "message": "빈 계획"}]
            if steps and not errors and len(steps) <= g["planner"]["max_steps"]:
                note["source"] = "llm"
                return steps, note
            note.update(source="template", fallback_reason=(errors or [{"message": "단계 수 초과"}])[0]["message"])
        except (StepError, ValueError, KeyError) as exc:
            note.update(source="template", fallback_reason=str(exc)[:200])
    else:
        note["source"] = "template"
    return list(g["default_plan"]), note


def _persist_plan(run_id: str, steps: list[dict], ctx: dict, append: bool = False) -> None:
    with SessionLocal() as db:
        r = db.get(Run, run_id)
        base = len(r.plan or []) if append else 0
        r.plan = (list(r.plan or []) + steps) if append else steps
        top_k = int(((r.graph or {}).get("workers", {}).get("retriever") or {}).get("top_k") or 5)
        for i, st in enumerate(steps):
            args = render(st.get("args") or {}, ctx, keep_unknown=True)
            if st.get("when"):
                args = {**args, "_when": st["when"]}
            if st.get("kb"):
                args = {**args, "_top_k": int(st.get("top_k") or args.get("top_k") or top_k)}
            if (st.get("retry") or {}).get("max_attempts"):
                args = {**args, "_max_attempts": int(st["retry"]["max_attempts"])}
            if st.get("timeout_s"):
                args = {**args, "_timeout_s": int(st["timeout_s"])}
            db.add(RunStep(id=new_id("stp"), run_id=run_id, seq=base + i, node=st["agent"], name=st["id"],
                           tool=f"rag:{st['kb']}" if st.get("kb") else st.get("tool"), agent_id=st.get("remote_agent"),
                           args=args, depends_on=st.get("depends_on") or []))
        db.commit()


# ───────────────────────── 노드 실행 ─────────────────────────
def _sources_from(step: RunStep, result: dict) -> list[dict]:
    sc = result.get("structuredContent")
    items = sc.get("result", sc) if isinstance(sc, dict) else sc
    if not isinstance(items, list):
        items = [items] if isinstance(items, dict) else []
    out = []
    for it in items[:10]:
        if not isinstance(it, dict):
            continue
        title = it.get("key") or it.get("id") or it.get("title") or step.tool
        fields = [str(it[k]) for k in ("summary", "title", "status", "priority", "excerpt", "service", "root_cause",
                                       "severity") if it.get(k)]
        if not fields:  # 알려진 필드가 없는 결과(예: 분류 결과 dict) — 스칼라 값을 본문으로
            fields = [f"{k}: {v}" for k, v in it.items() if isinstance(v, (str, int, float)) and k not in ("id", "key")]
        out.append({"title": str(title), "text": f"{title} " + " · ".join(fields), "tool": step.tool,
                    "ref": it.get("url") or it.get("key") or it.get("id")})
    if not out:
        text = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        out.append({"title": step.tool, "text": text[:400], "tool": step.tool})
    return out


async def run_node(gw: GatewayClient, run: dict, st: RunStep, outputs: dict, sources: list[dict]) -> dict:
    g = run["graph"]
    w = g["workers"]
    if st.node == "retriever" and (st.tool or "").startswith("rag:"):
        args = dict(st.args or {})
        kb = st.tool.split(":", 1)[1]
        hits = await gw.rag_search(st.id, kb, str(args.get("query") or run["request"]), int(args.get("_top_k") or 5))
        srcs = [{"title": h["title"], "text": f"{h['title']} — {h['text'][:600]}", "tool": st.tool, "kb": kb,
                 "ref": h.get("source") or h["doc_id"], "score": h["score"]} for h in hits]
        return {"text": "\n".join(x["text"] for x in srcs), "sources": srcs, "count": len(srcs),
                "raw": [{k: h[k] for k in ("doc_id", "chunk_id", "score")} for h in hits]}
    if st.node == "retriever":
        result = await gw.mcp_call("retriever", st.id, st.tool, _call_args(st), None)
        srcs = _sources_from(st, result)
        text = "\n".join(s["text"] for s in srcs)
        sc = result.get("structuredContent")
        items = sc.get("result", sc) if isinstance(sc, dict) else sc
        empty = isinstance(items, list) and not items  # 구조화 결과가 빈 목록일 때만 0건 (텍스트 결과는 근거로 유지)
        return {"text": text, "sources": [] if empty else srcs, "raw": sc, "count": 0 if empty else len(srcs),
                "sanitized": len((result.get("_meta") or {}).get("io.agentnexus/sanitized") or [])}
    if st.node == "summarize":
        dep_sources = [s for d in st.depends_on for s in outputs.get(d, {}).get("sources", [])]
        if not dep_sources:
            dep_sources = sources
        numbered = []
        for s in dep_sources:
            if s not in sources:
                sources.append(s)
            numbered.append(f"[{sources.index(s) + 1}] {s['text']}")
        for a in run.get("attachments") or []:  # 첨부도 번호 근거로 (FR-A01 첨부)
            src = {"title": f"첨부:{a['name']}", "text": f"[첨부 {a['name']}] {a.get('text', '')[:2000]}"}
            if src not in sources:
                sources.append(src)
            numbered.append(f"[{sources.index(src) + 1}] {src['text']}")
        cfg = w.get("summarize") or {}
        cite = cfg.get("cite", True)
        sys = ("다음 근거로 사용자 요청에 답하고 핵심을 요약하라. 근거 번호를 [n] 형식으로 인용하라."
               if cite else "다음 내용을 요약하라.")
        if cfg.get("max_tokens"):
            sys += f" 최대 {cfg['max_tokens']} 토큰 이내."
        user = f"요청: {run['request']}\n\n근거:\n" + "\n".join(numbered)

        async def on_delta(d: str):
            await emit(run["id"], "delta", {"step": st.name, "text": d})
        text, meta = await gw.chat("summarize", st.id, cfg.get("model", "auto"),
                                   [{"role": "system", "content": sys}, {"role": "user", "content": user}],
                                   on_delta=on_delta, max_tokens=cfg.get("max_tokens"))
        out = {"text": text, "model": meta.get("model"), "cited": [sources.index(s) + 1 for s in dep_sources],
               "citation_format": cfg.get("citation_format", "n")}
        if cite and numbered and not re.search(r"\[\d+\]", text):  # 인용 필수 — 1회 재생성, 그래도 없으면 '근거 없음'
            await emit(run["id"], "summarize.regenerate", {"step": st.name, "reason": "인용 없음"})
            text2, meta2 = await gw.chat("summarize", st.id, cfg.get("model", "auto"), [
                {"role": "system", "content": sys + " 모든 문장 끝에 근거 번호 [n] 을 붙여라. 근거가 없는 문장은 쓰지 마라."},
                {"role": "user", "content": user}], max_tokens=cfg.get("max_tokens"))
            if re.search(r"\[\d+\]", text2):
                out.update(text=text2, model=meta2.get("model"), regenerated=True)
            else:
                out.update(text="근거 없음 — 인용할 수 있는 근거로 답을 만들지 못했습니다.", uncited=True, regenerated=True)
        return out
    if st.node == "executor":
        if run.get("dry_run"):  # 드라이런 (SCR-D08 ③) — 쓰기 도구는 호출하지 않고 인자만 기록
            await emit(run["id"], "step.dry_run", {"step": st.name, "tool": st.tool, "args": _call_args(st)})
            return {"text": f"[드라이런] {st.tool} → 실행하지 않음 (인자만 기록)", "dry_run": True,
                    "would_call": st.tool, "args": _call_args(st)}
        result = await gw.mcp_call("executor", st.id, st.tool, _call_args(st), st.approval_id)
        sc = result.get("structuredContent")
        text = json.dumps(sc, ensure_ascii=False) if sc is not None else " ".join(
            c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        return {"text": text, "raw": sc}
    if st.node == "remote":
        args = dict(st.args or {})
        cont = args.get("_continue")  # input-required 에 사용자가 답함 → 같은 Task·contextId 로 이어서 보낸다
        if cont:
            task = await gw.a2a_send(st.id, st.agent_id, cont["reply"], None, task_id=cont["task_id"],
                                     context_id=cont.get("context_id"))
        else:
            task = await gw.a2a_send(st.id, st.agent_id, args.get("message") or run["request"], args.get("data"),
                                     context_id=args.get("context_id"))
        task = await gw.a2a_wait(st.id, st.agent_id, task)
        state = (task.get("status") or {}).get("state", "completed") if task.get("kind") == "task" else "completed"
        text = ap.text_of(task)
        if state == "input-required":
            msg = (task.get("status") or {}).get("message") or {}
            question = " ".join(p.get("text", "") for p in msg.get("parts", []) if p.get("kind") == "text") or text
            raise InputNeeded(task.get("id"), task.get("contextId"), question)
        if state not in ("completed",):
            raise StepError(f"원격 에이전트 상태 {state}: {text[:200]}", retryable=False,
                            data={"task_id": task.get("id"), "state": state})
        src = {"title": f"원격 에이전트 {st.agent_id}", "text": text[:600], "agent": st.agent_id,
               "ref": task.get("id")}
        return {"text": text, "task_id": task.get("id"), "context_id": task.get("contextId"), "state": state,
                "sources": [src], "turns": 1 + int(bool(cont)),
                "artifacts": [{"name": a.get("name"), "artifactId": a.get("artifactId")}
                              for a in task.get("artifacts") or []]}
    raise StepError(f"알 수 없는 노드: {st.node}")


def _branch_skip(error: str | None) -> bool:
    return (error or "").startswith(("조건 불충족", "분기 미선택"))


def _call_args(st: RunStep) -> dict:
    """도구 인자 — 오케스트레이터 내부 키(_when·_top_k·_continue)는 업스트림으로 보내지 않는다."""
    return {k: v for k, v in (st.args or {}).items() if not k.startswith("_")}


_OP = {"retriever": "execute_tool", "executor": "execute_tool", "summarize": "chat", "remote": "invoke_agent"}


async def _exec_step(gw: GatewayClient, run: dict, st: RunStep, outputs: dict, sources: list[dict]) -> None:
    with tracing.span(f"{st.node} {st.name}", **{"gen_ai.operation.name": _OP.get(st.node, "chat"),
                                                 "anx.step": st.name, "anx.node": st.node, "gen_ai.tool.name": st.tool,
                                                 "gen_ai.agent.name": st.agent_id}):
        await _exec_step_inner(gw, run, st, outputs, sources)


def _step_timeout(g: dict, st: RunStep) -> float | None:
    """노드 타임아웃 (SCR-D06 ④) — 단계 설정 > Retriever 설정 > 팬아웃 노드 타임아웃."""
    t = (st.args or {}).get("_timeout_s") or (((g["workers"].get("retriever") or {}).get("timeout_s"))
                                               if st.node == "retriever" else None) or g["fanout"].get("node_timeout_s")
    return float(t) if t else None


def _backoff_s(g: dict, attempt: int) -> float:
    base = g["retry"].get("backoff_ms", 200) / 1000
    return base * (2 ** (attempt - 1)) if g["retry"].get("backoff") == "exponential" else base * attempt


async def _exec_step_inner(gw: GatewayClient, run: dict, st: RunStep, outputs: dict, sources: list[dict]) -> None:
    g = run["graph"]
    attempts, waited = st.attempts, 0
    max_attempts = int((st.args or {}).get("_max_attempts") or g["retry"]["max_attempts"])
    timeout = _step_timeout(g, st)
    await run_in_threadpool(_set_step, st.id, status="running", started_at=utcnow())
    await emit(run["id"], "step.started", {"step": st.name, "node": st.node, "tool": st.tool, "agent": st.agent_id})
    while True:
        attempts += 1
        try:
            try:
                out = await asyncio.wait_for(run_node(gw, run, st, outputs, sources), timeout) if timeout else \
                    await run_node(gw, run, st, outputs, sources)
            except asyncio.TimeoutError as exc:
                raise StepError(f"노드 타임아웃 {timeout:g}초", retryable=True, data={"code": "timeout"}) from exc
            outputs[st.name] = out
            await run_in_threadpool(_set_step, st.id, status="succeeded", attempts=attempts, output=out,
                                    finished_at=utcnow(), error=None)
            await emit(run["id"], "step.completed", {"step": st.name, "node": st.node, "preview": out.get("text", "")[:300],
                                                     **({"task_id": out["task_id"]} if out.get("task_id") else {})})
            return
        except InputNeeded as exc:
            await run_in_threadpool(_set_step, st.id, status="waiting_input", attempts=attempts, error=exc.question,
                                    output={"task_id": exc.task_id, "context_id": exc.context_id,
                                            "question": exc.question, "state": "input-required"})
            await emit(run["id"], "input.required", {"step": st.name, "agent": st.agent_id, "task_id": exc.task_id,
                                                     "context_id": exc.context_id, "question": exc.question})
            return
        except ApprovalNeeded as exc:
            await run_in_threadpool(_set_step, st.id, status="waiting_approval", attempts=attempts,
                                    approval_id=exc.approval_id, error=exc.reason)
            await emit(run["id"], "approval.required", {"step": st.name, "approval_id": exc.approval_id,
                                                        "tool": st.tool, "args": st.args, "risk": exc.data.get("risk"),
                                                        "approver": exc.data.get("approver"), "reason": exc.reason})
            return
        except StepError as exc:
            if exc.data.get("code") == "rate_limited":  # 도구 429 (SCR-D06 ⑤·C06 ⑦) — ≤10초면 기다렸다 재시도, 아니면 제외
                wait = float(exc.data.get("retry_after") or 1)
                act = "wait" if g["fallbacks"].get("tool_429") == "wait" and wait <= 10 and waited < 2 else "skip"
                await emit(run["id"], "step.rate_limited", {"step": st.name, "tool": st.tool, "retry_after": wait,
                                                            "action": act, "attempt": attempts})
                if act == "wait":
                    waited += 1
                    await asyncio.sleep(wait)
                    continue
            elif exc.retryable and attempts < max_attempts:
                await emit(run["id"], "step.retry", {"step": st.name, "attempt": attempts, "error": exc.message})
                await asyncio.sleep(_backoff_s(g, attempts))
                continue
            await run_in_threadpool(_set_step, st.id, status="failed", attempts=attempts, error=exc.message,
                                    finished_at=utcnow(), output={"error": exc.message, **exc.data})
            await emit(run["id"], "step.failed", {"step": st.name, "node": st.node, "error": exc.message,
                                                  "retryable": exc.retryable, "attempts": attempts})
            return


# ───────────────────────── 실행 루프 ─────────────────────────
DONE = ("succeeded", "failed", "skipped", "rejected", "replanned")


def _ready(steps: list[RunStep], fanout: dict | str) -> tuple[list[RunStep], list[tuple[RunStep, str]]]:
    """합류 조건 (SCR-D06 ③) — all · any · required(필수 노드) · n_of_m(N 중 M). (실행할 단계, (건너뛸 단계, 사유))."""
    fo = fanout if isinstance(fanout, dict) else {"join": fanout}
    join, required, need = fo.get("join", "all"), set(fo.get("required") or []), int(fo.get("n") or 1)
    by = {s.name: s for s in steps}
    ready, skip = [], []
    for s in steps:
        if s.status != "pending":
            continue
        deps = [by[d] for d in s.depends_on if d in by]
        if not deps:
            ready.append(s)
            continue
        ok = [d for d in deps if d.status == "succeeded"]
        done = all(d.status in DONE for d in deps)
        if join == "any" and ok:
            ready.append(s)
        elif join == "n_of_m" and len(deps) > 1:
            if len(ok) >= min(need, len(deps)):
                ready.append(s)
            elif done:
                skip.append((s, f"합류 조건 미달: {len(ok)}/{len(deps)} 완료 (필요 {need})"))
        elif join == "required" and required & {d.name for d in deps}:
            req = [d for d in deps if d.name in required]
            if any(d.status in ("failed", "skipped", "rejected") for d in req) and done:
                skip.append((s, "필수 노드 실패: " + ", ".join(d.name for d in req if d.status != "succeeded")))
            elif done and all(d.status == "succeeded" for d in req):
                ready.append(s)
        elif done and ok:
            ready.append(s)
        elif done and not ok:
            skip.append((s, "의존 단계가 모두 실패"))
    return ready, skip


def _beat(run_id: str) -> None:
    with SessionLocal() as db:
        db.execute(update(Run).where(Run.id == run_id, Run.status == "running").values(heartbeat_at=utcnow()))
        db.commit()


async def _heartbeat(run_id: str) -> None:
    """실행 중 생존 신호 — 워커가 죽으면 RUN_STALE_S 뒤 다른 워커가 이어받는다 (recover_stale)."""
    period = max(1.0, settings.run_stale_s / 4)
    while True:
        await asyncio.sleep(period)
        with contextlib.suppress(Exception):
            await run_in_threadpool(_beat, run_id)


async def execute(run_id: str, resume: bool = False) -> None:
    lock = _LOCKS.setdefault(run_id, asyncio.Lock())
    async with lock:
        run = await run_in_threadpool(_claim, run_id, RESUMABLE if resume else ("queued",))
        if run is None:
            return
        beat = asyncio.create_task(_heartbeat(run_id))
        run["attachments"] = await run_in_threadpool(_attachments_of, run_id)
        token = None
        if resume:  # 승인 후 재개도 최초 실행과 같은 trace 로 (FR-O01 단일 trace_id)
            root = await run_in_threadpool(_root_span, run_id)
            parent = tracing.context_from(*root)
            token = otel_context.attach(parent) if parent is not None else None
        try:
            with tracing.span(f"invoke_agent {run['profile_id']}" + (" (resume)" if resume else ""), SpanKind.INTERNAL,
                              **{"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": run["profile_id"],
                                 "anx.run_id": run_id, "anx.data_class": run["data_class"]}):
                await _execute(run_id, run, resume)
        finally:
            beat.cancel()
            if token is not None:
                otel_context.detach(token)


def _root_span(run_id: str) -> tuple[str | None, str | None]:
    with SessionLocal() as db:
        ev = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.type == "run.started")).first()
        return ((ev.data or {}).get("trace_id"), (ev.data or {}).get("span_id")) if ev else (None, None)


def _set_trace(run_id: str, trace_id: str | None) -> None:
    with SessionLocal() as db:
        r = db.get(Run, run_id)
        if r.trace_id is None:
            r.trace_id = trace_id
            db.commit()


async def _execute(run_id: str, run: dict, resume: bool) -> None:
    gw = GatewayClient(run)
    try:
        await gw.mcp_open()
        if not resume:
            trace_id, span_id = tracing.current_span_ids()
            await run_in_threadpool(_set_trace, run_id, trace_id)
            await emit(run_id, "run.started", {"profile_id": run["profile_id"], "data_class": run["data_class"],
                                               "trace_id": trace_id, "span_id": span_id})
            steps, note = await plan(gw, run)
            await run_in_threadpool(_persist_plan, run_id, steps, {"request": run["request"], "outputs": {}})
            await emit(run_id, "plan", {"steps": steps, **note})
        await _loop(gw, run)
    except Exception as exc:  # noqa: BLE001 — 실행 단위 실패를 기록하고 사용자에게 사유 안내
        await run_in_threadpool(_finish, run_id, "failed", f"실행 실패: {exc}", [], str(exc))
        await emit(run_id, "run.failed", {"error": f"{type(exc).__name__}: {exc}"})
        metrics.RUNS.labels(run["profile_id"], "failed").inc()
        logs.event("error", "host", f"실행 실패: {run_id}", error=str(exc)[:300])
    finally:
        await gw.mcp_close()


def _attachments_of(run_id: str) -> list[dict]:
    """요청 메시지의 첨부(텍스트) — 요약 근거에 포함 (FR-A01 첨부 파일)."""
    with SessionLocal() as db:
        m = db.scalars(select(Message).where(Message.run_id == run_id, Message.role == "user")).first()
        return [p for p in (m.parts or []) if p.get("type") == "attachment"] if m else []


async def _loop(gw: GatewayClient, run: dict) -> None:
    g = run["graph"]
    outputs: dict = {}
    sources: list[dict] = []
    t0, total = time.monotonic(), g["fanout"].get("total_timeout_s")
    sem = asyncio.Semaphore(int(g["fanout"].get("max_concurrency") or 64))  # 최대 동시 실행 (SCR-D06 ②)

    async def _bounded(s: RunStep) -> None:
        async with sem:
            if total:
                left = total - (time.monotonic() - t0)
                try:
                    await asyncio.wait_for(_exec_step(gw, run, s, outputs, sources), max(left, 0.01))
                except asyncio.TimeoutError:
                    await run_in_threadpool(_set_step, s.id, status="failed", error=f"전체 타임아웃 {total}초",
                                            finished_at=utcnow())
                    await emit(run["id"], "step.failed", {"step": s.name, "node": s.node, "error": "전체 타임아웃"})
            else:
                await _exec_step(gw, run, s, outputs, sources)
    for s in await run_in_threadpool(_steps, run["id"]):
        if s.status == "succeeded" and s.output:
            outputs[s.name] = s.output
            sources += [x for x in (s.output.get("sources") or []) if x not in sources]
    while True:
        steps = await run_in_threadpool(_steps, run["id"])
        if g["on_failure"] == "fail" and any(s.status in ("failed", "rejected") for s in steps):
            if await _replan(gw, run, steps, outputs):
                continue
            for s in steps:
                if s.status == "pending":
                    await run_in_threadpool(_set_step, s.id, status="skipped", error="이전 단계 실패 (on_failure=fail)")
            break
        if total and time.monotonic() - t0 >= total:  # 전체 타임아웃 — 남은 단계는 건너뛰고 지금까지 결과로 (SCR-D06 ④)
            for s in steps:
                if s.status == "pending":
                    await run_in_threadpool(_set_step, s.id, status="skipped", error=f"전체 타임아웃 {total}초")
                    await emit(run["id"], "step.skipped", {"step": s.name, "reason": "total_timeout"})
            break
        ready, skip = _ready(steps, g["fanout"])
        by = {x.name: x for x in steps}
        for s, why in skip:
            # 의존 단계가 모두 조건(분기)으로 건너뛰어졌으면 선택되지 않은 분기 — 실패가 아니다 (FR-W02)
            branch = all((by[d].status == "skipped" and _branch_skip(by[d].error)) for d in s.depends_on if d in by)
            reason = "분기 미선택: 의존 단계가 조건으로 건너뜀" if branch else why
            await run_in_threadpool(_set_step, s.id, status="skipped", error=reason)
            await emit(run["id"], "step.skipped", {"step": s.name, **({"reason": "branch"} if branch else {})})
        if not ready:
            if not skip and not await _replan(gw, run, steps, outputs):
                break
            continue
        ctx = {"request": run["request"], "outputs": outputs,
               "sources": "\n".join(f"[{i + 1}] {x['text']}" for i, x in enumerate(sources))}
        go = []
        for s in ready:  # 조건 분기 (FR-W02) — 거짓이면 건너뜀, 하위 단계는 join 규칙대로
            cond = (s.args or {}).get("_when")
            if cond and not G.eval_condition(cond, run["request"], outputs):
                await run_in_threadpool(_set_step, s.id, status="skipped", error=f"조건 불충족: {cond}",
                                        finished_at=utcnow())
                await emit(run["id"], "step.skipped", {"step": s.name, "reason": "condition", "when": cond})
                continue
            go.append(s)
        if not go:
            continue
        ready = go
        for s in ready:  # 의존 출력으로 인자 재렌더 (예: {{s3.text}})
            s.args = render(s.args or {}, ctx)
            await run_in_threadpool(_set_step, s.id, args=s.args)
        if g["fanout"]["parallel"]:
            await asyncio.gather(*(_bounded(s) for s in ready))
        else:
            for s in ready:
                await _bounded(s)
        if "empty" in _replan_on(g) and any(outputs.get(s.name, {}).get("count") == 0 for s in ready if s.node == "retriever"):
            await _replan(gw, run, await run_in_threadpool(_steps, run["id"]), outputs)  # 검색 0건 → 즉시 재계획
    await gw.mcp_close()  # 완료 알림 전에 실행 단위 MCP 세션을 닫는다 — 구독자가 완료 시점에 종료 기록을 보도록
    gw.mcp_session = None
    await _complete(run, outputs, sources)


async def _complete(run: dict, outputs: dict, sources: list[dict]) -> None:
    steps = await run_in_threadpool(_steps, run["id"])
    waiting = [s for s in steps if s.status == "waiting_approval"]
    inputs = [s for s in steps if s.status == "waiting_input"]
    if waiting or inputs:
        def _wait():
            with SessionLocal() as db:
                db.get(Run, run["id"]).status = "waiting_approval" if waiting else "waiting_input"
                db.commit()
        await run_in_threadpool(_wait)
        await emit(run["id"], "run.waiting", {"approvals": [s.approval_id for s in waiting],
                                              "inputs": [s.name for s in inputs]})
        return
    failed = [s for s in steps if s.status in ("failed", "rejected")]
    skipped = [s for s in steps if s.status == "skipped" and not _branch_skip(s.error)]
    ok = [s for s in steps if s.status == "succeeded"]
    replanned = [s for s in steps if s.status == "replanned"]
    summary = next((outputs[s.name]["text"] for s in reversed(ok) if s.node == "summarize"), None)
    lines = [summary] if summary else [f"- {s.name}: {(outputs[s.name].get('text') or '')[:300]}" for s in ok]
    for s in ok:
        if s.node == "executor":
            raw = outputs[s.name].get("raw") or {}
            lines.append(f"\n✅ 실행 완료 — {s.tool}" + (f": {raw.get('url') or raw.get('id')}" if isinstance(raw, dict)
                                                       and (raw.get("url") or raw.get("id")) else ""))
        if s.node == "remote":
            lines.append(f"\n🤝 원격 에이전트 {s.agent_id} 위임 완료 (Task {outputs[s.name].get('task_id')})")
    if replanned:
        lines.append("\n🔁 재계획으로 대체한 단계: " + ", ".join(f"{s.name}({s.error or ''})"[:120] for s in replanned))
    if failed or skipped:
        lines.append("\n⚠️ 일부 단계가 완료되지 않았습니다:")
        lines += [f"- {s.name} ({s.tool or s.agent_id or s.node}): {s.error}" for s in failed]
        lines += [f"- {s.name}: 건너뜀 — {s.error}" for s in skipped]
    required = set(run["graph"]["fanout"].get("required") or []) if run["graph"]["fanout"].get("join") == "required" else set()
    req_failed = [s for s in failed + skipped if s.name in required]
    status = "succeeded" if not failed and not skipped else ("partial" if ok and run["graph"]["on_failure"] ==
                                                             "partial" and not req_failed else "failed")
    if req_failed:
        lines.append("\n⛔ 필수 노드 실패로 전체 실패: " + ", ".join(s.name for s in req_failed))
    text = "\n".join(x for x in lines if x).strip() or "결과가 없습니다."
    citations = [{"n": i + 1, **{k: v for k, v in src.items() if k in ("title", "tool", "agent", "ref")}}
                 for i, src in enumerate(sources)]
    parts = [{"type": "citations", "items": citations},
             {"type": "steps", "items": [{"step": s.name, "node": s.node, "status": s.status, "tool": s.tool,
                                          "agent": s.agent_id, "error": s.error} for s in steps]}]
    parts += [{"type": "remote_task", "agent": s.agent_id, "task_id": outputs[s.name].get("task_id"),
               "artifacts": outputs[s.name].get("artifacts")} for s in ok if s.node == "remote"]
    await run_in_threadpool(_finish, run["id"], status, text, citations, None if status != "failed" else
                            "; ".join(s.error or "" for s in failed), parts)
    await emit(run["id"], "run.completed", {"status": status, "text": text, "citations": citations,
                                            "failed": [s.name for s in failed], "skipped": [s.name for s in skipped]})
    metrics.RUNS.labels(run["profile_id"], status).inc()
    if status != "succeeded":
        logs.event("warning", "host", f"실행 {status}: {run['id']}", failed=[s.name for s in failed],
                   skipped=[s.name for s in skipped])


# ───────────────────────── 재계획 (FR-A02.1, SCR-C02) ─────────────────────────
def _replan_rounds(run_id: str) -> int:
    with SessionLocal() as db:
        return len(list(db.scalars(select(RunEvent.id).where(RunEvent.run_id == run_id, RunEvent.type == "plan.revised"))))


def _replan_on(g: dict) -> set[str]:
    """재계획 조건 (SCR-D03 ⑦) — 기본 실패만. Executor 거부 처리가 replan 이면 사람 거부도."""
    on = set(G.replan_triggers(g["planner"].get("replan") or {}) or ["failed"])
    if (((g["workers"].get("executor") or {}).get("approval")) or {}).get("on_deny") == "replan":
        on.add("rejected")
    return on


async def _replan(gw: GatewayClient, run: dict, steps: list[RunStep], outputs: dict) -> bool:
    """재계획 조건(실패·0건·거부)에 맞는 단계가 남았고 라운드가 남았으면 대체 계획을 붙인다. 붙였으면 True (루프 계속)."""
    g = run["graph"]
    cfg = g["planner"].get("replan") or {}
    on = _replan_on(g)
    failed = [s for s in steps if (s.status == "failed" and "failed" in on) or (s.status == "rejected" and "rejected" in on)
              or ("empty" in on and s.status == "succeeded" and s.node == "retriever"
                  and (outputs.get(s.name) or s.output or {}).get("count") == 0)]
    if not failed or any(s.status in ("waiting_approval", "waiting_input") for s in steps):
        return False
    rounds = await run_in_threadpool(_replan_rounds, run["id"])
    if rounds >= int(cfg.get("max_rounds", 0) or 0):
        return False
    n = rounds + 1
    ok = {s.name for s in steps if s.status == "succeeded"}
    reason = "; ".join(f"{s.name}({s.tool or s.agent_id or s.node}): {(s.error or '')[:160]}" for s in failed)
    new, source = None, "fallback"
    if g["planner"]["mode"] == "llm":
        done = "\n".join(f"- {s.name}: {(outputs.get(s.name, {}).get('text') or '')[:200]}" for s in steps if s.name in ok)
        sys = ("너는 Planner 다. 이전 계획의 일부 단계가 실패했다. 실패한 단계를 대체할 단계만 JSON 계획으로 다시 만든다. "
               f"이미 성공한 단계({', '.join(sorted(ok)) or '없음'})는 depends_on 으로 참조할 수 있다.")
        user = f"요청: {run['request']}\n실패: {reason}\n성공 결과:\n{done or '(없음)'}"
        with contextlib.suppress(StepError, ValueError, KeyError):
            text, _ = await gw.chat("planner", None, g["planner"]["model"],
                                    [{"role": "system", "content": sys}, {"role": "user", "content": user}],
                                    response_format={"type": "json_schema", "json_schema": {"name": "plan",
                                                                                            "schema": PLAN_SCHEMA}})
            cand = (json.loads(text) or {}).get("steps") or []
            if cand and not G.validate_plan(g, cand, "replan", known=ok):
                new, source = cand, "llm"
    if new is None:
        new = [dict(x) for x in cfg.get("fallback_plan") or []]
        if not new or G.validate_plan(g, new, "replan", known=ok):
            return False
    ids = {x["id"] for x in new}
    for x in new:  # 라운드 접두사 — 이전 단계 이름과 충돌하지 않게, 같은 계획 안의 의존만 이름을 바꾼다
        x["id"] = f"r{n}_{x['id']}"
        x["depends_on"] = [f"r{n}_{d}" if d in ids else d for d in x.get("depends_on") or []]
    gone = {s.name for s in failed}
    changed = True
    while changed:  # 대체되는 단계에 (전이적으로) 기대던 대기·건너뜀 단계도 함께 넘긴다
        changed = False
        for s in steps:
            if s.name not in gone and s.status in ("pending", "skipped") and any(d in gone for d in s.depends_on or []):
                gone.add(s.name)
                changed = True
    replaced = failed + [s for s in steps if s.name in gone and s not in failed]
    for s in replaced:
        await run_in_threadpool(_set_step, s.id, status="replanned")  # 대체 계획으로 넘긴 단계 — 실패로 집계하지 않음
    await run_in_threadpool(_persist_plan, run["id"], new, {"request": run["request"], "outputs": outputs}, True)
    await emit(run["id"], "plan.revised", {"round": n, "source": source, "reason": reason,
                                           "previous": [s.name for s in replaced], "steps": new})
    return True


# ───────────────────────── 승인 결과 → 재개 ─────────────────────────
def on_approval(ap_row: Approval) -> None:
    """게이트웨이 승인 API 훅(스레드) — 승인/거부 후 대기 중인 실행을 재개한다."""
    if not ap_row.run_id:
        return
    with SessionLocal() as db:
        st = db.scalars(select(RunStep).where(RunStep.run_id == ap_row.run_id,
                                              RunStep.approval_id == ap_row.id)).first()
        if st is None or st.status != "waiting_approval":
            return
        if ap_row.status == "approved":
            st.status = "pending"
        else:
            st.status, st.error, st.finished_at = "rejected", f"사람 승인 {'만료' if ap_row.status == 'expired' else '거부'} ({ap_row.decided_by or 'system'})", utcnow()
        db.commit()
        pending_left = db.scalars(select(RunStep).where(RunStep.run_id == ap_row.run_id,
                                                        RunStep.status == "waiting_approval")).first()
    _emit(ap_row.run_id, "approval.resolved", {"approval_id": ap_row.id, "status": ap_row.status,
                                               "decided_by": ap_row.decided_by})
    if pending_left is None:
        if not settings.enabled("host"):  # 실행은 Host 구성요소에서만 — 게이트웨이 단독 프로세스가 승인을 만료·결정해도 (59th)
            logs.event("info", "host", f"재개는 Host 의 복구 작업이 이어받음: {ap_row.run_id}")
            return
        try:
            schedule(ap_row.run_id, resume=True)
        except RuntimeError:  # 이벤트 루프 없는 프로세스(CLI·운영 스크립트) — 복구 작업(recover_stale)이 이어받는다
            logs.event("warning", "host", f"재개 예약 보류 — 복구 작업이 이어받음: {ap_row.run_id}")


def provide_input(run_id: str, step_name: str, reply: str, actor: str) -> dict:
    """input-required 에 답하기 (SCR-C05 인라인 답변) — 같은 원격 Task·contextId 로 이어 보내도록 단계를 되돌리고 재개."""
    with SessionLocal() as db:
        st = db.scalars(select(RunStep).where(RunStep.run_id == run_id, RunStep.name == step_name)).first()
        if st is None or st.status != "waiting_input":
            raise ValueError("입력을 기다리는 단계가 아닙니다.")
        out = st.output or {}
        st.args = {**(st.args or {}), "_continue": {"task_id": out.get("task_id"), "context_id": out.get("context_id"),
                                                    "reply": reply}}
        st.status, st.error = "pending", None
        db.commit()
        left = db.scalars(select(RunStep).where(RunStep.run_id == run_id,
                                                RunStep.status.in_(("waiting_input", "waiting_approval")))).first()
    _emit(run_id, "input.resolved", {"step": step_name, "by": actor, "task_id": out.get("task_id")})
    if left is None:
        schedule(run_id, resume=True)
    return {"step": step_name, "task_id": out.get("task_id"), "resumed": left is None}


async def recover_stale() -> int:
    """끊긴 실행 복구 (NFR-S1·A1) — 생존 신호가 RUN_STALE_S 넘게 없는 running 실행, 시작되지 못한 queued 실행을 이어 돌린다.
    조건부 UPDATE(CAS) 로 한 워커만 가져간다. 진행 중이던 단계는 다시 실행한다(도구 쓰기는 승인 id 로 중복 방지)."""
    from datetime import timedelta
    cutoff = utcnow() - timedelta(seconds=settings.run_stale_s)

    def _take() -> list[tuple[str, bool]]:
        out = []
        with SessionLocal() as db:
            stale = db.scalars(select(Run.id).where(Run.status == "running", (Run.heartbeat_at < cutoff) | (
                Run.heartbeat_at.is_(None) & (Run.started_at < cutoff))))
            for rid in list(stale):
                res = db.execute(update(Run).where(Run.id == rid, Run.status == "running", (Run.heartbeat_at < cutoff) | (
                    Run.heartbeat_at.is_(None))).values(status="recovering"))
                if res.rowcount != 1:
                    continue
                n = 0
                for st in db.scalars(select(RunStep).where(RunStep.run_id == rid, RunStep.status == "running")):
                    st.status, n = "pending", n + 1
                has_plan = db.scalars(select(RunStep.id).where(RunStep.run_id == rid)).first() is not None
                if not has_plan:
                    db.get(Run, rid).status = "queued"
                db.add(RunEvent(run_id=rid, type="run.recovered", data={"steps_reset": n, "replan": not has_plan}))
                out.append((rid, has_plan))
            for rid in list(db.scalars(select(Run.id).where(Run.status == "queued", Run.created_at < cutoff))):
                out.append((rid, False))
            # 기다릴 단계가 없는데 대기 상태로 남은 실행(재개 예약이 유실됨) — 재개. claim 이 CAS 라 중복 실행은 없다
            for rid in list(db.scalars(select(Run.id).where(Run.status.in_(("waiting_approval", "waiting_input"))))):
                left = db.scalars(select(RunStep.id).where(RunStep.run_id == rid, RunStep.status.in_(
                    ("waiting_approval", "waiting_input")))).first()
                if left is None:
                    db.add(RunEvent(run_id=rid, type="run.recovered", data={"orphaned_wait": True}))
                    out.append((rid, True))
            db.commit()
        return out
    taken = await run_in_threadpool(_take)
    for rid, resume in taken:
        schedule(rid, resume=resume)
    return len(taken)


def retry_failed(run_id: str) -> bool:
    """부분 실패 재시도 (SCR-C06) — 실패·건너뜀 단계와 그 하위 의존 단계를 pending 으로 되돌리고 재개."""
    with SessionLocal() as db:
        r = db.get(Run, run_id)
        if r is None or r.status not in ("partial", "failed"):
            return False
        steps = list(db.scalars(select(RunStep).where(RunStep.run_id == run_id)))
        reset = {s.name for s in steps if s.status in ("failed", "skipped", "rejected")}
        changed = True
        while changed:  # 전이적 하위 의존
            changed = False
            for s in steps:
                if s.name not in reset and any(d in reset for d in s.depends_on or []):
                    reset.add(s.name)
                    changed = True
        for st in steps:
            if st.name in reset:
                st.status, st.error, st.attempts, st.output, st.approval_id = "pending", None, 0, None, None
        r.status = "waiting_approval"  # 재개 가능한 상태로 (claim 대상)
        db.commit()
    _emit(run_id, "run.retry", {})
    schedule(run_id, resume=True)
    return True


def continue_partial(run_id: str, actor: str) -> str:
    """부분 결과로 계속 (SCR-C06 ④) — partial 은 결과를 확정, on_failure=fail 로 멈춘 실행은 남은 단계를 부분 결과 모드로 이어 돌린다."""
    with SessionLocal() as db:
        r = db.get(Run, run_id)
        if r is None or r.status not in ("partial", "failed"):
            raise ValueError("부분 실패한 실행만 이어갈 수 있습니다.")
        if r.status == "partial":
            db.add(RunEvent(run_id=run_id, type="run.accepted_partial", data={"by": actor}))
            db.commit()
            return "accepted"
        steps = list(db.scalars(select(RunStep).where(RunStep.run_id == run_id)))
        if not any(s.status == "succeeded" for s in steps):
            raise ValueError("성공한 단계가 없어 부분 결과를 만들 수 없습니다 — 다시 시도하세요.")
        r.graph = {**r.graph, "on_failure": "partial"}
        n = 0
        for st in steps:
            if st.status == "skipped" and (st.error or "").startswith("이전 단계 실패"):
                st.status, st.error, n = "pending", None, n + 1
        r.status = "waiting_approval"
        db.add(RunEvent(run_id=run_id, type="run.continue_partial", data={"by": actor, "resumed": n}))
        db.commit()
    schedule(run_id, resume=True)
    return "resumed"


def cancel(run_id: str) -> bool:
    with SessionLocal() as db:
        r = db.get(Run, run_id)
        if r is None or r.status in TERMINAL:
            return False
        r.status, r.finished_at = "canceled", utcnow()
        for st in db.scalars(select(RunStep).where(RunStep.run_id == run_id,
                                                   RunStep.status.in_(("pending", "waiting_approval", "waiting_input")))):
            st.status = "skipped"
        db.commit()
    _emit(run_id, "run.canceled", {})
    return True


async def wait(run_id: str, timeout: float = 120, until: tuple = ("succeeded", "partial", "failed", "canceled",
                                                                   "waiting_approval", "waiting_input")) -> str:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        def _st():
            with SessionLocal() as db:
                return db.get(Run, run_id).status
        st = await run_in_threadpool(_st)
        if st in until:
            return st
        await asyncio.sleep(0.1)
    return "timeout"


def events_after(run_id: str, after: int) -> list[RunEvent]:
    with SessionLocal() as db:
        return list(db.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.id > after)
                               .order_by(RunEvent.id)))


# ───────────────────────── 플랫폼 A2A 서버 실행기 (FR-A05) ─────────────────────────
async def a2a_executor(ctx, profile_id: str, principal) -> None:
    """원격 클라이언트의 A2A Task → 로컬 오케스트레이션 실행 → 진행 상태 중계 → 답변 Artifact."""
    text = " ".join(p.get("text", "") for p in ctx.message.get("parts", []) if p.get("kind") == "text")
    run_id = await run_in_threadpool(lambda: start_run(principal, profile_id=profile_id, request=text))
    ctx.task.setdefault("metadata", {})["anx_run_id"] = run_id  # 영속 Task ↔ 실행 — 다른 복제·재시작 후에도 결과 조회
    await ctx.update("working", f"계획 중 (run {run_id})")
    schedule(run_id)
    try:
        await _relay_run(ctx, run_id)
    except asyncio.CancelledError:  # 같은 워커의 tasks/cancel 은 실행기만 끊는다 — 오케스트레이션 실행도 멈춘다 (D19-④)
        cancel(run_id)
        raise


async def _relay_run(ctx, run_id: str) -> None:
    after, deadline = 0, time.monotonic() + 900
    while time.monotonic() < deadline:
        for ev in await run_in_threadpool(events_after, run_id, after):
            after = ev.id
            if ev.type == "step.completed":
                await ctx.update("working", f"{ev.data.get('step')} 완료 ({ev.data.get('node')})")
            elif ev.type == "approval.required":
                await ctx.update("working", f"사람 승인 대기: {ev.data.get('tool')} (AgentNexus 콘솔에서 승인)")
            elif ev.type == "run.completed":
                await ctx.artifact("answer.md", [{"kind": "text", "text": ev.data.get("text", "")}],
                                   "AgentNexus 오케스트레이션 답변")
                if ev.data.get("citations"):
                    await ctx.artifact("citations", [{"kind": "data", "data": {"citations": ev.data["citations"]}}])
                await ctx.update("completed" if ev.data.get("status") != "failed" else "failed",
                                 f"실행 {ev.data.get('status')} (run {run_id})")
                return
            elif ev.type in ("run.failed", "run.canceled"):
                await ctx.update("failed" if ev.type == "run.failed" else "canceled", ev.data.get("error"))
                return
        if ctx.canceled:
            cancel(run_id)
            return
        await asyncio.sleep(0.2)
    await ctx.update("failed", "실행 시간 초과")
