> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 20. app/services/mcp_tasks.py
**파일 요약 / File Summary**
- 역할 / Role: MCP Tasks 확장(`io.modelcontextprotocol/tasks`) — modern 하류 클라이언트의 장기 `tools/call` 을 게이트웨이가 작업으로 실행. 2초 안에 끝나면 일반 결과, 아니면 `CreateTaskResult`. `tasks/get`·`update`·`cancel`, 생존 신호·취소 감시.
- 진입점 거리 / Distance: 2 (`app/api/mcp.py:146-163`)
- LOC: 231
- 주요 export: `EXT`, `METHODS`, `MISSING_CAPABILITY`, `wants`, `start`, `handle`, `peek`, `owned`
- 의존(내부): `app.core.ids`, `app.db`, `app.models.{McpTask,McpRelay}`, `app.services.mcp_modern`, (지연) `app.services.mcp_consent`, `app.core.logs`
- 외부 패키지: sqlalchemy, starlette
- 부수효과 / Side effects: DB 쓰기(mcp_tasks·mcp_relays 삭제), 워커 로컬 `_JOBS`·`_WATCHERS`, 백그라운드 Task 2개/작업
- 동시성 / Concurrency: asyncio Task(작업·감시), 스레드풀 DB
- 테스트 커버리지 흔적: `test/test_mcp_tasks.py`, `test/test_review_fixes.py`(BUG-03/10 생존 신호·작업 보존)
- 참고: 리뷰 도중(13:59) 동시 작업으로 `_WATCHERS`(L34)·감시 루프 예외 처리(L170-179)·중계 정리(L74)가 추가되었다 — 본 리뷰는 14:03 스냅샷 기준(INDEX §1.6).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/mcp_tasks.py lines=1-34
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
```
**분석 / Analysis (Level 2)**
- L1-10: 설계 — 작업 행을 **먼저 저장**(내구 생성)하고 실행, 남의·만료·모르는 작업은 모두 같은 오류(-32602)로 존재 여부를 숨긴다, `tasks/list` 는 없다(다른 주체 작업 존재를 흘리지 않음).
- L24-32: 확장 키, 메서드, 오류 코드, TTL 1시간, 폴링 간격 1초, 인라인 2초, 생존 신호 2초, 정체 판정 30초, 종료 상태.
- L33 `_JOBS`: 이 워커에서 실행 중인 작업 Task.
- L34 `_WATCHERS`: 감시 Task 의 **강한 참조** 집합 — 주석대로 이벤트 루프는 Task 를 약하게만 참조하므로 참조를 버린 `create_task` 는 실행 중 GC 될 수 있다(asyncio 공식 문서 경고). 올바른 수정.

```python path=app/services/mcp_tasks.py lines=37-46
def wants(params: dict) -> bool:
    return EXT in ((mm.client_caps(params).get("extensions") or {}))


def _iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _not_found() -> mm.ModernError:
    return mm.ModernError(-32602, "Failed to retrieve task: Task not found")
```
**분석 / Analysis (Level 2)**
- L37-38 `wants`: 요청 `_meta.clientCapabilities.extensions` 에 확장이 선언됐는지.
- L41-42 `_iso`: UTC ISO 를 `Z` 접미로.
- L45-46: "작업 없음" 오류(-32602) — 남의 작업에도 같은 문구.

```python path=app/services/mcp_tasks.py lines=49-91
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
```
**분석 / Analysis (Level 2)**
- L50-55 `_insert`: working 상태로 생성, 생성·갱신·생존 시각 동일.
- L58-66 `_finish`: 이미 종료 상태면 덮어쓰지 않는다(취소 후 완료가 덮어쓰는 경쟁 방지 — 첫 종료가 이긴다).
- L69-75 `_discard`: 즉시 끝난 작업 삭제 + 그 작업에 묶인 입력 요청 중계 행도 삭제(L74 — 고아 mcp_relays 방지).
- L78-86 `_beat`: 생존 신호 갱신 + 취소 요청 확인(행이 없으면 취소로 간주).
- L89-91 `_pending`: 미응답 입력 요청(SQL 에서 `response IS NULL` 필터 — 효율적).
**짚어볼 점 / Watch-outs**
- L58-66 의 "종료 상태면 무시" 는 읽고-쓰기 사이 경쟁이 남는다(두 워커가 동시에 working 을 보고 각자 기록) — 작업은 한 워커만 실행하므로 실제 충돌원은 `handle` 의 cancel 플래그뿐이라 영향이 작다.

```python path=app/services/mcp_tasks.py lines=94-141
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
```
**분석 / Analysis (Level 2)**
- L94-95 `_expired`: 생성 + TTL < 지금.
- L98-114 `_view`: DetailedTask — working 인데 생존 신호가 30초 넘게 없으면 **failed 로 보여 준다**(행은 그대로), 미응답 입력이 있으면 input_required + inputRequests, 완료면 result, 실패면 error.
- L117-125 `_owned`: 문자열 id 만, 주체·서버 일치, 만료면 삭제 후 None — 소유권 검사와 지연 정리를 겸한다.
- L128-141 `peek`·`owned`: listen 용 조회·승인(최대 50개).
**짚어볼 점 / Watch-outs**
- L102-103 정체 판정은 **보이는 상태만** failed — 실제 행은 working 으로 남아 TTL 만료까지 정리되지 않는다. 재시작으로 고아가 된 작업을 정리하는 배치가 없다.
- L118 `isinstance(task_id, str)` 검사로 숫자·객체 id 를 거부(타입 혼동 방지 — 좋음).
- L121-123 조회 경로에서 삭제·커밋 — GET 성격 요청이 쓰기를 한다(수용 가능한 지연 정리).

```python path=app/services/mcp_tasks.py lines=144-201
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
```
**분석 / Analysis (Level 2)**
- L148-150: 192비트 작업 id(`task_` + `token_urlsafe(24)`) — 추측 불가. 먼저 행 저장 후 중계(Relay, 큐 없음)를 ctx 에 설치 — 입력 요청은 `tasks/get` 의 inputRequests 로 노출된다.
- L153-163 `work`: 실제 호출(`run` = `app.api.mcp.dispatch`) → completed / 취소면 cancelled / 그 외 예외는 JSON-RPC 오류를 failed 작업의 error 로. `finally` 에서 `_JOBS` 정리.
- L167-179 `watch`: 2초마다 생존 신호, 취소 요청이 있으면 작업 취소. **일시적 DB 오류는 경고 로그 후 다음 주기 재시도**(L170-176) — 한 번의 오류로 감시가 끝나 거짓 failed 가 되던 문제를 막는다. Task 는 `_WATCHERS` 에 넣고 끝나면 `add_done_callback(discard)` 로 제거.
- L181-188: 최대 2초 동안 0.1초 간격으로 입력 요청 발생 여부 확인 — 생기면 즉시 작업으로 응답.
- L189-198: 인라인으로 끝났고 completed/failed 면 작업 행을 지우고 일반 결과(`__final__`)로 반환.
- L199-201: 아니면 `CreateTaskResult`(DetailedTask 형태).
**짚어볼 점 / Watch-outs**
- L157-158 `except asyncio.CancelledError` 후 재발생하지 않는다 — 작업 자신의 취소를 "cancelled 상태" 로 변환하는 의도라 수용. 단, **서버 종료**로 루프가 취소될 때도 같은 경로를 타므로 종료 중 DB 쓰기를 시도한다.
- [위험:낮음 · 성능] L181-188 인라인 대기 2초 동안 최대 20회 DB 조회(스레드풀) — 고빈도 도구 호출에서 지연·부하.
- L200 `db.get(McpTask, task_id)` 가 `None`(그 사이 TTL 정리·삭제)이면 `_view` 에서 `AttributeError` — 정상 흐름에서는 발생하지 않는다.
- 작업 수 상한이 없다 — 한 주체가 장기 작업을 대량 생성하면 워커 메모리·백그라운드 Task 가 늘어난다(TTL 1시간).

```python path=app/services/mcp_tasks.py lines=204-231
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
```
**분석 / Analysis (Level 2)**
- L207-219 `_do`(스레드풀): 소유 확인 → get 이면 DetailedTask, cancel 이면 종료 전일 때 취소 플래그(협조적 취소 — 실행 워커가 다음 생존 신호 때 확인).
- L221-224: 이 워커가 실행 중이면 즉시 Task 취소.
- L225-230 update: `inputResponses` 가 dict 인지 확인 후 `mcp_modern._answer` 로 연결(대기 중이 아닌 키는 무시 — 사양). `_answer` 는 행의 `session_id == task_id` 를 확인하므로 다른 작업의 요청에 답할 수 없다(`app/services/mcp_modern.py:229-240`).
**짚어볼 점 / Watch-outs**
- L229 다른 모듈의 private 함수(`mm._answer`)를 직접 호출 — 공용 API 로 승격하는 편이 계약이 명확하다.

## 파일 평가 / File Assessment
- 강점: (1) 작업 소유권(주체·서버 바인딩)과 존재 은닉(-32602 통일)·추측 불가 id. (2) 내구 생성 + 생존 신호로 워커 장애를 조회 시점에 드러냄. (3) 감시 Task 강한 참조·일시 오류 재시도(스냅샷 시점 반영).
- 위험: (1) [낮음] 고아 working 행 정리 배치 부재. (2) [낮음] 인라인 대기 DB 폴링·작업 수 상한 부재. (3) 종료 시 CancelledError 경로의 DB 쓰기.
- 개선 권고(영향순): (1) 정체 작업을 failed 로 확정하는 정리 작업(스케줄러). (2) 주체별 동시 작업 상한. (3) 인라인 대기를 이벤트(Queue) 기반으로.
- 재읽기 권고 레벨: 3.
