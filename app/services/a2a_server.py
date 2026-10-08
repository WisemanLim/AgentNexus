"""A2A 서버 엔진 (FR-A05 플랫폼 A2A 서버 · 데모 원격 에이전트 공용) — JSON-RPC 0.3/1.0 동시 수용.

Executor(ctx) 가 작업을 수행하며 ctx.update()/ctx.artifact() 로 상태·결과를 낸다. 구독자(SSE)·푸시 웹훅에 즉시 전달.
send 는 기본 blocking(터미널·input-required 까지 대기, timeout 시 현재 상태 반환), 1.0 returnImmediately / 0.3
configuration.blocking=false 면 즉시 반환. 저장소는 메모리(기본) — 플랫폼 서버는 on_change 훅으로 DB 에 영속한다.
"""
import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from app.core.http import post_with_retry
from app.services import a2a_proto as ap


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskContext:
    def __init__(self, server: "A2AServer", task: dict, message: dict, auth: dict):
        self.server, self.task, self.message, self.auth = server, task, message, auth
        self.inbox: asyncio.Queue = asyncio.Queue()  # input-required 후 추가 메시지

    @property
    def canceled(self) -> bool:
        return self.task["status"]["state"] == "canceled"

    async def update(self, state: str, text: str | None = None, data: dict | None = None) -> None:
        if self.canceled:
            return
        msg = None
        if text or data:
            parts = ([{"kind": "text", "text": text}] if text else []) + ([{"kind": "data", "data": data}] if data else [])
            msg = {"kind": "message", "messageId": uuid.uuid4().hex, "role": "agent", "parts": parts,
                   "taskId": self.task["id"], "contextId": self.task["contextId"]}
            self.task["history"].append(msg)
        self.task["status"] = {"state": state, "timestamp": _now(), **({"message": msg} if msg else {})}
        await self.server._emit(self.task, {"kind": "status-update", "taskId": self.task["id"],
                                            "contextId": self.task["contextId"], "status": self.task["status"],
                                            "final": state in ap.TERMINAL or state == "input-required"})

    async def artifact(self, name: str, parts: list[dict], description: str | None = None) -> None:
        art = {"artifactId": uuid.uuid4().hex, "name": name, "parts": parts,
               **({"description": description} if description else {})}
        self.task["artifacts"].append(art)
        await self.server._emit(self.task, {"kind": "artifact-update", "taskId": self.task["id"],
                                            "contextId": self.task["contextId"], "artifact": art, "append": False,
                                            "lastChunk": True})

    async def ask(self, question: str, timeout: float = 300) -> dict | None:
        """input-required 로 전환 후 사용자의 추가 메시지를 기다린다."""
        await self.update("input-required", question)
        try:
            return await asyncio.wait_for(self.inbox.get(), timeout)
        except asyncio.TimeoutError:
            return None


Executor = Callable[[TaskContext], Awaitable[None]]


class A2AServer:
    MAX_TASKS = 1000  # 메모리에 두는 작업 수 상한 (넘으면 오래된 종료 작업부터)

    def __init__(self, card: Callable[[], dict], executor: Executor, *, authenticate=None, blocking_timeout: float = 30,
                 on_change: Callable[[dict, dict], Awaitable[None]] | None = None,
                 loader: Callable[[str], Awaitable[dict | None]] | None = None,
                 remote_cancel: Callable[[str], Awaitable[dict | None]] | None = None,
                 push_check: Callable[[str | None], str | None] | None = None,
                 owner: Callable[[dict], str | None] | None = None,
                 owner_lookup: Callable[[str], Awaitable[str | None]] | None = None,
                 owned_lookup: Callable[[str | None], Awaitable[list[str]]] | None = None):
        """loader·remote_cancel: 이 프로세스 메모리에 없는 Task(다른 복제·재시작 전)를 영속 저장소에서 다룬다 (NFR-S1)."""
        self.card = card
        self.executor = executor
        self.loader = loader
        self.remote_cancel = remote_cancel
        self.push_check = push_check  # 클라이언트 푸시 URL 검사 → 거부 사유 (None 이면 통과)
        # Task 소유권 (D18-①) — owner(auth) 가 있으면 Task 는 만든 주체만 보고·바꾼다. 남의 Task 는 없는 것처럼(TaskNotFound).
        # owner_lookup 은 이 프로세스 메모리에 없는(다른 복제·재시작 전) Task 의 주체를 영속 저장소에서 찾는다.
        self.owner = owner
        self.owner_lookup = owner_lookup
        self.owned_lookup = owned_lookup  # 주체의 최근 Task id (영속) — 목록은 다른 워커·재시작 전 Task 도 (D19-④)
        self.owners: dict[str, str | None] = {}
        self.authenticate = authenticate  # async (Request) -> dict | None (None=401)
        self.blocking_timeout = blocking_timeout
        self.on_change = on_change
        self.tasks: dict[str, dict] = {}
        self.ctxs: dict[str, TaskContext] = {}
        self.push: dict[str, dict] = {}
        self.subscribers: dict[str, list[asyncio.Queue]] = {}
        self.runners: dict[str, asyncio.Task] = {}
        self._bg: set[asyncio.Task] = set()  # 푸시 전송 — 루프는 Task 를 약하게만 참조하므로 끝날 때까지 붙잡아 둔다

    # ── 이벤트 전파 ──
    async def _emit(self, task: dict, event: dict) -> None:
        if self.on_change:  # 영속 → 통지 순서 (클라이언트가 받은 상태는 항상 저장된 상태)
            with contextlib.suppress(Exception):
                await self.on_change(task, event)
        for q in list(self.subscribers.get(task["id"], [])):
            q.put_nowait(event)
        cfg = self.push.get(task["id"])
        if cfg and cfg.get("url"):
            bg = asyncio.create_task(self._push(cfg, task))
            self._bg.add(bg)
            bg.add_done_callback(self._bg.discard)
        if task["status"]["state"] in ap.TERMINAL:  # 끝난 작업의 실행 문맥은 더 쓰지 않는다
            self.ctxs.pop(task["id"], None)
            self.runners.pop(task["id"], None)

    async def _push(self, cfg: dict, task: dict) -> None:
        from app.core import logs
        if self.push_check and (reason := await asyncio.to_thread(self.push_check, cfg["url"])):  # 등록 뒤 이름이 사설 주소로 — 전달 직전 다시 (D19-④)
            logs.event("warning", "a2a", f"푸시 알림 전달 거부: {reason}", task_id=task.get("id"))
            return
        headers = {"Content-Type": "application/json"}
        if cfg.get("token"):
            headers["X-A2A-Notification-Token"] = cfg["token"]
        if not await post_with_retry(cfg["url"], content=json.dumps(task, ensure_ascii=False), headers=headers):
            logs.event("warning", "a2a", "푸시 알림 전달 실패(재시도 후)", task_id=task.get("id"))

    def _subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.setdefault(task_id, []).append(q)
        return q

    def _unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        with contextlib.suppress(ValueError):
            self.subscribers.get(task_id, []).remove(q)
        if not self.subscribers.get(task_id):
            self.subscribers.pop(task_id, None)

    def _evict(self) -> None:
        """메모리 상한 — 오래된 종료 작업부터 내린다(영속 저장소가 있으면 loader 가 다시 읽는다)."""
        for tid in [t for t, task in self.tasks.items() if task["status"]["state"] in ap.TERMINAL]:
            if len(self.tasks) <= self.MAX_TASKS:
                break
            for store in (self.tasks, self.push, self.ctxs, self.runners, self.owners):
                store.pop(tid, None)

    # ── 작업 실행 ──
    async def _start(self, message: dict, auth: dict) -> dict:
        tid = message.get("taskId")
        if tid and tid in self.tasks:
            await self._check_owner(tid, auth)  # 남의 Task 에 이어 보내기 금지
            task = self.tasks[tid]
            task["history"].append(message)
            if task["status"]["state"] == "input-required" and tid in self.ctxs:
                await self.ctxs[tid].update("working")  # 응답 수신 즉시 working — blocking send 가 결과까지 대기
                self.ctxs[tid].inbox.put_nowait(message)
                return task
            if task["status"]["state"] in ap.TERMINAL:
                raise _RpcErr(ap.UNSUPPORTED_OP, f"이미 종료된 작업입니다: {tid}")
            return task
        task = {"kind": "task", "id": uuid.uuid4().hex, "contextId": message.get("contextId") or uuid.uuid4().hex,
                "status": {"state": "submitted", "timestamp": _now()}, "artifacts": [],
                "history": [{**message, "taskId": None}]}
        task["history"][0]["taskId"] = task["id"]
        self.tasks[task["id"]] = task
        if self.owner is not None:
            self.owners[task["id"]] = self.owner(auth)
        if len(self.tasks) > self.MAX_TASKS:
            self._evict()
        ctx = TaskContext(self, task, message, auth)
        self.ctxs[task["id"]] = ctx
        await self._emit(task, {"kind": "status-update", "taskId": task["id"], "contextId": task["contextId"],
                                "status": task["status"], "final": False})

        async def run():
            try:
                await self.executor(ctx)
                if task["status"]["state"] not in ap.TERMINAL:
                    await ctx.update("completed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 원문(내부 경로·질의)은 로그에만, 원격에는 상관 id (D21-⑤)
                from app.core import logs
                ref = uuid.uuid4().hex[:12]
                logs.event("error", "a2a", f"플랫폼 실행기 오류 {ref}: {type(exc).__name__}: {exc}", task_id=task["id"])
                await ctx.update("failed", f"에이전트 내부 오류 (참조 {ref})")
        self.runners[task["id"]] = asyncio.create_task(run())
        return task

    async def _wait(self, task: dict, timeout: float) -> None:
        if task["status"]["state"] in ap.TERMINAL or task["status"]["state"] == "input-required":
            return
        q = self._subscribe(task["id"])
        try:
            async with asyncio.timeout(timeout):
                while True:
                    ev = await q.get()
                    if ev.get("kind") == "status-update" and ev.get("final"):
                        return
        except TimeoutError:
            return
        finally:
            self._unsubscribe(task["id"], q)

    # ── JSON-RPC ──
    async def handle(self, request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        return await self.dispatch(request, body.get("id"), body.get("method", ""), body.get("params") or {})

    async def rest(self, request: Request) -> Response:
        """HTTP+JSON 바인딩 (D16-①) — 같은 dispatch 로, 응답은 REST 형태."""
        from app.services import a2a_rest
        try:
            raw = await request.body() if request.method not in ("GET", "DELETE") else b""
            body = json.loads(raw) if raw else None
            method, params = a2a_rest.to_rpc(request.method, request.path_params.get("path", ""), request.query_params, body)
        except ValueError:
            return a2a_rest.error_response(-32700, "Parse error", http_status=400)
        except a2a_rest.RestError as exc:
            return a2a_rest.error_response(exc.code, exc.message, exc.data, exc.http_status)
        return a2a_rest.from_rpc_response(await self.dispatch(request, "rest", method, params))

    async def dispatch(self, request: Request, rid, method: str, params: dict) -> Response:
        op = ap.op_of(method)
        version = ap.version_of(method)
        auth = {}
        if self.authenticate is not None:
            auth = await self.authenticate(request)
            if auth is None:
                return JSONResponse({"error": "unauthorized"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
        if op is None:
            return _err(rid, -32601, f"Method not found: {method}")
        try:
            if op == "send":
                msg = ap.message_from(params.get("message"))
                if not msg:
                    return _err(rid, -32602, "params.message 가 필요합니다.")
                cfg = params.get("configuration") or {}
                task = await self._start(msg, auth)
                immediate = cfg.get("returnImmediately") or cfg.get("blocking") is False
                if not immediate:
                    await self._wait(task, self.blocking_timeout)
                return _ok(rid, ap.result_to(_snapshot(task), version, "send"))
            if op == "stream":
                msg = ap.message_from(params.get("message"))
                if not msg:
                    return _err(rid, -32602, "params.message 가 필요합니다.")
                task = await self._start(msg, auth)
                return self._sse(rid, task, version, include_initial=True)
            if op in ("resubscribe", "get", "cancel", "push_get"):
                await self._check_owner(params.get("taskId") if op == "push_get" and params.get("taskId")
                                        else params.get("id"), auth)
            if op == "push_set":
                await self._check_owner(ap.push_config_of(params)[0], auth)
            if op == "resubscribe":
                tid = params.get("id")
                if tid not in self.tasks and self.loader is not None:
                    return self._sse_polling(rid, await self._load(tid), version)
                task = self._task(tid)
                return self._sse(rid, task, version, include_initial=True)
            if op == "get":
                tid = params.get("id")
                task = self.tasks.get(tid) or (await self._load(tid) if self.loader else self._task(tid))
                snap = _snapshot(task)
                if params.get("historyLength") is not None:
                    snap["history"] = snap["history"][-int(params["historyLength"]):] if params["historyLength"] else []
                return _ok(rid, ap.result_to(snap, version, "get"))
            if op == "cancel":
                tid = params.get("id")
                if tid not in self.tasks and self.remote_cancel is not None:
                    task = await self._load(tid)
                    if task["status"]["state"] in ap.TERMINAL:
                        return _err(rid, ap.TASK_NOT_CANCELABLE, f"취소할 수 없는 상태: {task['status']['state']}")
                    return _ok(rid, ap.result_to(await self.remote_cancel(tid) or task, version, "get"))
                task = self._task(tid)
                if task["status"]["state"] in ap.TERMINAL:
                    return _err(rid, ap.TASK_NOT_CANCELABLE, f"취소할 수 없는 상태: {task['status']['state']}")
                runner = self.runners.get(task["id"])
                if runner:
                    runner.cancel()
                task["status"] = {"state": "canceled", "timestamp": _now()}
                await self._emit(task, {"kind": "status-update", "taskId": task["id"], "contextId": task["contextId"],
                                        "status": task["status"], "final": True})
                return _ok(rid, ap.result_to(_snapshot(task), version, "get"))
            if op == "push_set":
                tid, cfg = ap.push_config_of(params)
                if self.push_check and (reason := await asyncio.to_thread(self.push_check, cfg.get("url"))):  # 이름 해석 — 루프 밖
                    return _err(rid, -32602, reason)
                self._task(tid)
                self.push[tid] = cfg
                out = {"taskId": tid, "pushNotificationConfig": cfg} if version == ap.V03 else {"taskId": tid, **cfg}
                return _ok(rid, out)
            if op == "push_get":
                tid = params.get("taskId") or params.get("id")
                cfg = self.push.get(tid)
                if cfg is None:
                    return _err(rid, ap.TASK_NOT_FOUND, "푸시 설정 없음")
                return _ok(rid, {"taskId": tid, "pushNotificationConfig": cfg} if version == ap.V03
                           else {"taskId": tid, **cfg})
            if op == "list":
                who = self.owner(auth) if self.owner is not None else None
                mine = {t["id"]: t for t in list(self.tasks.values()) if self.owner is None or self.owners.get(t["id"]) == who}
                if self.owned_lookup is not None and self.loader is not None:  # 이 워커 메모리 밖(다른 워커·재시작 전)의 내 Task
                    for tid in await self.owned_lookup(who):
                        if tid not in mine and (t := await self.loader(tid)) is not None:
                            mine[tid] = t
                ordered = sorted(mine.values(), key=lambda t: str((t.get("status") or {}).get("timestamp") or ""))
                items = [ap.result_to(_snapshot(t), version, "get") for t in ordered[-50:]]
                return _ok(rid, {"tasks": items})
            if op == "card":
                return _ok(rid, self.card())
        except _RpcErr as exc:
            return _err(rid, exc.code, exc.message)
        return _err(rid, ap.UNSUPPORTED_OP, f"지원하지 않는 작업: {method}")

    async def _check_owner(self, tid: str | None, auth: dict) -> None:
        if self.owner is None or not tid:
            return
        if tid in self.owners:
            who = self.owners[tid]
        elif tid not in self.tasks and self.owner_lookup is not None:
            who = await self.owner_lookup(tid)
        else:
            who = None
        if who is None or who != self.owner(auth):
            raise _RpcErr(ap.TASK_NOT_FOUND, f"Task not found: {tid}")

    def _task(self, tid: str | None) -> dict:
        if not tid or tid not in self.tasks:
            raise _RpcErr(ap.TASK_NOT_FOUND, f"Task not found: {tid}")
        return self.tasks[tid]

    async def _load(self, tid: str | None) -> dict:
        task = await self.loader(tid) if tid and self.loader else None
        if task is None:
            raise _RpcErr(ap.TASK_NOT_FOUND, f"Task not found: {tid}")
        return task

    def _sse_polling(self, rid, task: dict, version: str, interval: float = 1.0, limit_s: float = 900) -> StreamingResponse:
        """다른 인스턴스가 실행 중인 Task 재구독 — 영속 상태를 조회해 바뀐 상태만 흘린다."""
        async def gen():
            cur = task
            yield _sse_line(rid, ap.event_to(_snapshot(cur), version))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + limit_s
            while cur["status"]["state"] not in ap.TERMINAL and loop.time() < deadline:
                await asyncio.sleep(interval)
                nxt = await self.loader(cur["id"]) or cur
                if nxt["status"]["state"] != cur["status"]["state"] or len(nxt.get("artifacts") or []) != len(
                        cur.get("artifacts") or []):
                    for art in (nxt.get("artifacts") or [])[len(cur.get("artifacts") or []):]:
                        yield _sse_line(rid, ap.event_to({"kind": "artifact-update", "taskId": nxt["id"],
                                                          "contextId": nxt["contextId"], "artifact": art,
                                                          "append": False, "lastChunk": True}, version))
                    st = nxt["status"]["state"]
                    yield _sse_line(rid, ap.event_to({"kind": "status-update", "taskId": nxt["id"],
                                                      "contextId": nxt["contextId"], "status": nxt["status"],
                                                      "final": st in ap.TERMINAL or st == "input-required"}, version))
                cur = nxt
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    def _sse(self, rid, task: dict, version: str, include_initial: bool) -> StreamingResponse:
        q = self._subscribe(task["id"])

        async def gen():
            try:
                if include_initial:
                    yield _sse_line(rid, ap.event_to(_snapshot(task), version))
                if task["status"]["state"] in ap.TERMINAL:
                    return
                while True:
                    ev = await q.get()
                    yield _sse_line(rid, ap.event_to(ev, version))
                    if ev.get("kind") == "status-update" and ev.get("final"):
                        return
            finally:
                self._unsubscribe(task["id"], q)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def card_endpoint(self, _: Request) -> JSONResponse:
        return JSONResponse(self.card())


class _RpcErr(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _snapshot(task: dict) -> dict:
    return json.loads(json.dumps(task, ensure_ascii=False))


def _ok(rid, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})


def _err(rid, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def _sse_line(rid, result) -> str:
    return f"data: {json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result}, ensure_ascii=False)}\n\n"
