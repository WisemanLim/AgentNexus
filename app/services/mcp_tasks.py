"""MCP Tasks 확장 (`io.modelcontextprotocol/tasks`, SEP-2663) — 2026-07-28 하류 클라이언트의 장기 도구 호출 (D15-①, Epic 32).

- 클라이언트가 요청 `_meta.clientCapabilities.extensions` 에 확장을 선언한 `tools/call` 만 대상. 작업은 게이트웨이가 직접 돌린다
  (업스트림 세대와 무관 — 정책·승인·토큰 교환·소독은 일반 호출과 같은 경로).
- 서버가 정한다: 작업 행을 먼저 저장(내구 생성)하고 실행해 `INLINE_S` 안에 끝나면 일반 결과, 아니면(또는 입력 요청이 생기면)
  `CreateTaskResult {resultType: "task", taskId, status, …}`.
- `tasks/get` → DetailedTask (working · input_required(inputRequests) · completed(result) · failed(error) · cancelled),
  `tasks/update {inputResponses}` → 대기 중인 입력 요청에 연결(mcp_relays — 워커 간 공유), `tasks/cancel` → 협조적 취소.
  `tasks/list` 는 사양에 없다(다른 주체의 작업 존재를 흘리지 않음). 남의·만료된·모르는 작업은 모두 -32602.
- 실행 워커가 생존 신호(heartbeat)를 남기고 취소 요청을 본다. 신호가 끊기면 조회 때 failed 로 보인다.
"""
import asyncio
import time
from datetime import timedelta

from sqlalchemy import delete
from starlette.concurrency import run_in_threadpool

from app.core.ids import token_urlsafe
from app.db import SessionLocal, utcnow
from app.models import McpRelay, McpTask
from app.services import mcp_modern as mm

EXT = "io.modelcontextprotocol/tasks"
METHODS = {"tasks/get", "tasks/update", "tasks/cancel"}
MISSING_CAPABILITY = -32021
TTL_MS = 3_600_000
POLL_MS = 1000
INLINE_S = 2.0       # 이 안에 끝나는 호출은 작업을 만들지 않고 결과를 바로 준다
HEARTBEAT_S = 2.0
STALE_S = 30.0
TERMINAL = {"completed", "failed", "cancelled"}
_JOBS: dict[str, asyncio.Task] = {}
_WATCHERS: set[asyncio.Task] = set()  # 루프는 Task 를 약하게만 참조한다 — 강한 참조가 없으면 실행 중에 회수될 수 있다


def wants(params: dict) -> bool:
    return EXT in ((mm.client_caps(params).get("extensions") or {}))


def _iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _not_found() -> mm.ModernError:
    return mm.ModernError(-32602, "Failed to retrieve task: Task not found")


# ───────────────────────── 저장 ─────────────────────────
def _insert(task_id: str, server_id: str, sub: str, tool: str | None) -> None:
    with SessionLocal() as db:
        now = utcnow()
        db.add(McpTask(id=task_id, server_id=server_id, principal_sub=sub, tool=tool, status="working", ttl_ms=TTL_MS,
                       created_at=now, updated_at=now, heartbeat_at=now))
        db.commit()


def _finish(task_id: str, status: str, result: dict | None = None, error: dict | None = None, message: str | None = None) -> None:
    with SessionLocal() as db:
        row = db.get(McpTask, task_id)
        if row is None or row.status in TERMINAL:
            return
        row.status, row.result, row.error, row.updated_at = status, result, error, utcnow()
        if message:
            row.status_message = message
        db.commit()


def _discard(task_id: str) -> None:
    with SessionLocal() as db:
        row = db.get(McpTask, task_id)
        if row is not None:
            db.delete(row)
            db.execute(delete(McpRelay).where(McpRelay.session_id == task_id))  # 작업에 묶인 입력 요청 중계도 함께
            db.commit()


def _beat(task_id: str) -> bool:
    """생존 신호 + 취소 요청 확인 (True = 취소)."""
    with SessionLocal() as db:
        row = db.get(McpTask, task_id)
        if row is None:
            return True
        row.heartbeat_at = utcnow()
        db.commit()
        return bool(row.cancel_requested)


def _pending(db, task_id: str) -> dict:
    rows = db.query(McpRelay).filter(McpRelay.session_id == task_id, McpRelay.response.is_(None)).all()
    return {r.id: {"method": r.method, "params": r.params} for r in rows}


def _expired(row: McpTask) -> bool:
    return row.ttl_ms is not None and row.created_at + timedelta(milliseconds=row.ttl_ms) < utcnow()


def _view(db, row: McpTask) -> dict:
    """DetailedTask — 상태별로 inputRequests · result · error 를 싣는다."""
    out = {"taskId": row.id, "status": row.status, "createdAt": _iso(row.created_at), "lastUpdatedAt": _iso(row.updated_at),
           "ttlMs": row.ttl_ms, "pollIntervalMs": POLL_MS}
    if row.status == "working" and row.heartbeat_at and row.heartbeat_at < utcnow() - timedelta(seconds=STALE_S):
        out.update(status="failed", error={"code": -32603, "message": "작업을 실행하던 게이트웨이 워커가 중단되었습니다."})
    elif row.status == "working":
        pending = _pending(db, row.id)
        if pending:
            out.update(status="input_required", inputRequests=pending)
    elif row.status == "completed":
        out["result"] = row.result or {}
    elif row.status == "failed":
        out["error"] = row.error or {"code": -32603, "message": "failed"}
    if row.status_message:
        out["statusMessage"] = row.status_message
    return out


def _owned(db, task_id, server_id: str, sub: str) -> McpTask | None:
    row = db.get(McpTask, str(task_id)) if isinstance(task_id, str) else None
    if row is None or row.principal_sub != sub or row.server_id != server_id:
        return None
    if _expired(row):
        db.delete(row)
        db.commit()
        return None
    return row


def peek(server_id: str, sub: str, task_id: str) -> dict | None:
    with SessionLocal() as db:
        row = _owned(db, task_id, server_id, sub)
        return _view(db, row) if row else None


def owned(server_id: str, sub: str, task_ids) -> list[str]:
    """listen `taskIds` 승인 — 이 주체·서버의 살아 있는 작업만 (최대 50개)."""
    out = []
    with SessionLocal() as db:
        for tid in list(task_ids or [])[:50]:
            if _owned(db, tid, server_id, sub) is not None:
                out.append(tid)
    return out


# ───────────────────────── 실행 ─────────────────────────
async def start(server_id: str, ctx, params: dict, run) -> dict:
    """작업 행을 먼저 저장한 뒤 실행. INLINE_S 안에 끝나면 {"__final__": …}, 아니면 CreateTaskResult."""
    from app.services import mcp_consent
    task_id = "task_" + token_urlsafe(24)
    await run_in_threadpool(_insert, task_id, server_id, ctx.principal.sub, params.get("name"))
    ctx.relay = mcp_consent.Relay(task_id, server_id, mm.client_caps(params), None)  # 입력 요청 → tasks/get inputRequests
    call = {"name": params.get("name"), "arguments": params.get("arguments") or {}}

    async def work():
        try:
            res = await run(server_id, ctx, "tools/call", call)
            await run_in_threadpool(_finish, task_id, "completed", mm.complete("tools/call", res))
        except asyncio.CancelledError:
            await run_in_threadpool(_finish, task_id, "cancelled", None, None, "클라이언트 요청으로 취소했습니다.")
        except Exception as exc:  # noqa: BLE001 — JSON-RPC 오류는 failed 작업의 error 로
            err = exc.body(None)["error"] if hasattr(exc, "body") else {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}
            await run_in_threadpool(_finish, task_id, "failed", None, err)
        finally:
            _JOBS.pop(task_id, None)
    job = asyncio.create_task(work())
    _JOBS[task_id] = job

    async def watch():
        while not job.done():
            await asyncio.sleep(HEARTBEAT_S)
            try:
                if not job.done() and await run_in_threadpool(_beat, task_id):
                    job.cancel()
            except Exception as exc:  # noqa: BLE001 — 일시적 DB 오류로 신호가 끊기면 STALE_S 뒤 거짓 failed 가 된다 → 다음 주기에 재시도
                from app.core import logs
                logs.event("warning", "gateway", "MCP 작업 생존 신호 실패 — 다음 주기에 재시도", task_id=task_id,
                           error=type(exc).__name__)
    watcher = asyncio.create_task(watch())
    _WATCHERS.add(watcher)
    watcher.add_done_callback(_WATCHERS.discard)

    deadline = time.monotonic() + INLINE_S
    while time.monotonic() < deadline and not job.done():
        def _has_input():
            with SessionLocal() as db:
                return bool(_pending(db, task_id))
        if await run_in_threadpool(_has_input):
            break  # 입력이 필요하다 — 작업으로 돌려준다
        await asyncio.sleep(0.1)
    if job.done():
        def _final():
            with SessionLocal() as db:
                row = db.get(McpTask, task_id)
                return (row.status, row.result, row.error) if row else ("failed", None, None)
        status, result, error = await run_in_threadpool(_final)
        if status in ("completed", "failed"):
            await run_in_threadpool(_discard, task_id)  # 바로 끝난 호출은 작업을 남기지 않는다
            return {"__final__": {"result": {k: v for k, v in (result or {}).items() if k != "resultType"}} if status == "completed"
                    else {"error": error}}
    with SessionLocal() as db:
        view = _view(db, db.get(McpTask, task_id))
    return {"resultType": "task", **view}


async def handle(server_id: str, p, method: str, params: dict) -> dict:
    task_id = params.get("taskId")

    def _do():
        with SessionLocal() as db:
            row = _owned(db, task_id, server_id, p.sub)
            if row is None:
                raise _not_found()
            if method == "tasks/get":
                return _view(db, row)
            if method == "tasks/cancel":
                if row.status not in TERMINAL:
                    row.cancel_requested, row.updated_at = True, utcnow()
                    db.commit()
                return {}
            return None  # tasks/update — 아래에서 연결
    out = await run_in_threadpool(_do)
    if method == "tasks/cancel":
        job = _JOBS.get(task_id)
        if job is not None:  # 이 워커가 돌리고 있으면 바로, 아니면 실행 워커가 다음 생존 신호 때
            job.cancel()
    if method == "tasks/update":
        responses = params.get("inputResponses")
        if not isinstance(responses, dict):
            raise mm.ModernError(-32602, "Invalid params: inputResponses 객체가 필요합니다.")
        await run_in_threadpool(mm._answer, task_id, responses)  # 대기 중이 아닌 키는 무시 (사양)
        return {}
    return out
