> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [mcp_tasks.py.md](../../../../CODE-REVIEW-Lv2/app/services/mcp_tasks.py.md)

### 10. app/services/mcp_tasks.py
**파일 요약 / File Summary**
- 역할 / Role: MCP Tasks 확장(`io.modelcontextprotocol/tasks`, SEP-2663). 2026-07-28 하류 클라이언트가 확장을 선언한 `tools/call` 을 게이트웨이가 작업(Task)으로 실행 — `INLINE_S`(2초) 안에 끝나면 일반 결과, 아니면 `CreateTaskResult`. `tasks/get`·`tasks/update`·`tasks/cancel`, 생존 신호(heartbeat)·협조적 취소.
- 진입점 거리 / Distance: 2 — `app/api/mcp.py:144-150`(tasks/* 처리), `app/api/mcp.py:156-161`(start), `app/api/mcp.py:253`(listen `owned`), `app/services/mcp_modern.py:453`(`peek`).
- LOC: 231 · 1차 리뷰 시점 231 → 변화 없음(파일 수정 시각 13:59, 1차 스냅샷 14:03 이전 — 1차 인용 원문과 동일함을 대조 확인).
- 주요 export: `EXT`, `METHODS`, `MISSING_CAPABILITY`, `TTL_MS`, `wants`, `start`, `handle`, `peek`, `owned`
- 의존(내부): `app.core.ids.token_urlsafe`, `app.db.{SessionLocal,utcnow}`, `app.models.{McpRelay,McpTask}`, `app.services.mcp_modern`(`client_caps`·`complete`·`ModernError`·`_answer`), (지연) `app.services.mcp_consent.Relay`, `app.core.logs`
- 외부 패키지: sqlalchemy(`delete`), starlette(`run_in_threadpool`)
- 부수효과 / Side effects: DB 쓰기(mcp_tasks 생성·갱신·삭제, mcp_relays 삭제), 작업마다 asyncio Task 2개(작업·감시), 워커 로컬 `_JOBS`·`_WATCHERS`.
- 동시성 / Concurrency: asyncio Task + 스레드풀 DB. 워커 간 공유는 DB(취소 플래그·생존 신호·mcp_relays). 상태 전이는 읽기-수정-쓰기(조건부 UPDATE 아님).
- 테스트 커버리지 흔적: `test/test_mcp_tasks.py:103`(인라인), `:114`(input_required→update→complete), `:147`(cancel), `:165`(Host 동의), `:190`(listen 작업 상태), `:220`(만료·정체 워커); `test/test_review_fixes.py:152-169`(C39-07 보존 정리), `:171-201`(C39-08 생존 신호 오류 후 지속·감시 Task 강한 참조).
- 2차 변경 요약 / Changes since 1st review: 소스 변경 없음. 주변 변경으로 영향받은 점 — `app/services/retention.py:33-34` 가 mcp_relays·mcp_tasks 의 1일 경과분을 삭제(Epic 39, BUG-01·04 대응), `app/api/mcp.py:58` 이 `policy.caller_context` 로 run/agent 신고를 서버 판정(Epic 40)해 이 파일의 `ctx` 가 받는 값이 달라졌다.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/mcp_tasks.py lines=1-11
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
```
**분석 / Analysis (Level 2)**
- L1-11: 설계 계약. 핵심은 (1) 작업은 게이트웨이가 돌리므로 일반 `tools/call` 과 **같은 정책 경로**(`run` = `app/api/mcp.py:62` `dispatch`)를 탄다, (2) 작업 행을 실행 **전에** 저장해 워커가 죽어도 흔적이 남는다(내구 생성), (3) 존재 은닉 — 남의·만료·모르는 작업을 같은 -32602 로 답해 taskId 추측으로 다른 주체의 작업 존재를 알아낼 수 없게 한다.
- 흔한 실수: "Task = 백그라운드 실행" 으로만 이해하고 정책 경로를 따로 만드는 것. 여기서는 `dispatch` 를 그대로 넘겨 우회 경로를 만들지 않았다.

```python path=app/services/mcp_tasks.py lines=12-34
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
- L24-32: 확장 키·메서드 집합·오류 코드(-32021 능력 누락)·TTL 1시간·폴링 안내 1초·인라인 2초·생존 신호 2초·정체 판정 30초·종료 상태.
- L33 `_JOBS`: 이 워커에서 돌고 있는 작업 Task(취소 즉시 반영용). 워커 로컬이라 다른 워커의 작업은 DB 플래그로만 취소된다(L223 주석).
- L34 `_WATCHERS`: asyncio 이벤트 루프는 `create_task` 로 만든 Task 를 약한 참조로만 들고 있으므로, 반환값을 버리면 실행 중 GC 될 수 있다(asyncio 문서 경고). 강한 참조 집합 + `add_done_callback(discard)` 는 표준 해법(delegated BUG-10 해결).
- 흔한 실수: `STALE_S`(30초)를 `HEARTBEAT_S`(2초)의 몇 배로 둘지 — 스레드풀이 포화되면 `_beat` 가 늦어지므로 여유가 필요하다(아래 L98-114 Watch-out).

```python path=app/services/mcp_tasks.py lines=37-46
def wants(params: dict) -> bool:
    return EXT in ((mm.client_caps(params).get("extensions") or {}))


def _iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _not_found() -> mm.ModernError:
    return mm.ModernError(-32602, "Failed to retrieve task: Task not found")
```
**분석 / Analysis (Level 2)**
- L37-38 `wants`: `mm.client_caps` 는 `_meta.io.modelcontextprotocol/clientCapabilities` 가 dict 일 때만 돌려준다(`app/services/mcp_modern.py:174-176`). `extensions` 가 dict 가 아닌 리스트여도 `in` 이 동작하므로 관대하다.
- L41-42 `_iso`: tz-aware UTC(`UTCDateTime`, `app/db.py:17-34`)를 `Z` 접미로. 반환 타입 힌트는 `str` 이지만 None 도 돌려준다(사소).
- L45-46: 존재 은닉용 단일 오류. 남의 작업에도 같은 문구 — 의도.

```python path=app/services/mcp_tasks.py lines=49-56
# ───────────────────────── 저장 ─────────────────────────
def _insert(task_id: str, server_id: str, sub: str, tool: str | None) -> None:
    with SessionLocal() as db:
        now = utcnow()
        db.add(McpTask(id=task_id, server_id=server_id, principal_sub=sub, tool=tool, status="working", ttl_ms=TTL_MS,
                       created_at=now, updated_at=now, heartbeat_at=now))
        db.commit()
```
**분석 / Analysis (Level 2)**
- L50-55: working 상태로 생성. `principal_sub`·`server_id` 를 함께 저장해 이후 모든 접근(`_owned`)이 이 둘에 묶인다 — 객체 수준 인가의 근거.
- `server_id` 는 URL 경로 id(단일·가상·`_all`)다. 같은 사용자가 같은 작업을 다른 경로 id 로 조회하면 -32602 — 의도적으로 좁다.

```python path=app/services/mcp_tasks.py lines=58-75
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
```
**분석 / Analysis (Level 2)**
- L58-66 `_finish`: "이미 종료면 무시" — 첫 종료가 이기도록 하는 의도. SQLAlchemy ORM 은 바뀐 컬럼만 UPDATE 하므로, 동시에 `handle` 이 `cancel_requested` 만 바꿔도 `status` 를 덮어쓰지 않는다.
- L69-75 `_discard`: 인라인으로 끝난 작업 행 + 그 작업 id 로 묶인 mcp_relays 를 한 트랜잭션에서 삭제(delegated BUG-09 해결 — 고아 중계 행 방지).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L60-66 종료 판정이 읽기-수정-쓰기다. `work()`(L153-163) 가 `_finish(completed)` 를 스레드에서 실행하는 도중 같은 워커의 `tasks/cancel` 이 `job.cancel()`(L224)을 부르면, 대기 중이던 `await` 에서 CancelledError 가 나 `_finish(cancelled)` 가 **다른 스레드**에서 거의 동시에 실행될 수 있다. 두 스레드가 모두 working 을 읽으면 마지막 커밋이 이겨 결과가 있는데 cancelled 로 남거나 그 반대가 된다. 최소 수정: `update(McpTask).where(id==…, status=="working").values(...)` 후 rowcount 확인.

```python path=app/services/mcp_tasks.py lines=78-91
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
- L78-86 `_beat`: 생존 신호 갱신과 취소 플래그 확인을 한 번에. 행이 사라졌으면(TTL 지연 정리 L121-123·보존 정리) 취소로 간주 — 고아 작업이 계속 돌지 않게 하는 안전한 기본값.
- L89-91 `_pending`: 미응답 중계 행(`response IS NULL`)만. progress 공유 행(`app/services/mcp_modern.py:291-295`)은 `response={}` 로 저장되므로 여기 걸리지 않는다.
- 흔한 실수: JSON 컬럼의 SQL NULL 과 JSON `null` 구분. 여기서는 `Relay.request` 가 `response` 를 아예 넣지 않아(`app/services/mcp_consent.py:305-306`) SQL NULL 로 남고, 시험(`test/test_mcp_tasks.py:114`)이 이 경로를 확인한다.

```python path=app/services/mcp_tasks.py lines=94-114
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
```
**분석 / Analysis (Level 2)**
- L94-95 `_expired`: 생성 시각 + TTL. tz-aware 끼리 비교라 TypeError 없음(`UTCDateTime` 이 읽을 때 UTC 부여).
- L98-114 `_view`: 상태별 DetailedTask. 생존 신호가 30초 넘게 없으면 **보이는 상태만** failed 로 바꾼다(행은 working 유지). input_required 는 미응답 중계가 있을 때.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L102-103 정체 판정은 표시만 바꾸고 행에 기록하지 않는다. 실행 워커가 살아 있지만 스레드풀 포화(anyio 기본 40 스레드)로 `_beat` 가 30초 이상 밀리면 클라이언트는 failed(종료 상태)를 본 뒤, 작업이 끝나면 같은 taskId 가 completed 로 바뀐다 — 사양상 종료 상태는 다시 바뀌지 않아야 한다. failed 를 보고 클라이언트가 재시도하면 같은 도구가 두 번 실행될 수 있다. 최소 수정: 정체 판정 시 조건부 UPDATE 로 failed 를 확정하고 실행 워커의 `_finish` 는 working 일 때만 쓰게(위 L58-66 수정과 같이).
- 고아 working 행은 이제 `app/services/retention.py:33-34` 가 1일 뒤 지운다(1차 지적의 정리 배치 부재는 부분 해결).

```python path=app/services/mcp_tasks.py lines=117-141
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
- L117-125 `_owned`: 객체 수준 인가 — 문자열 id 만(`isinstance` 로 숫자·객체 거부), 주체·서버 일치, 만료면 지연 삭제. 모든 진입(get·update·cancel·listen·peek)이 이 함수를 거친다.
- L128-131 `peek`: listen 스트림이 작업 상태를 알릴 때(`app/services/mcp_modern.py:453`).
- L134-141 `owned`: listen `taskIds` 중 이 주체의 살아 있는 작업만 승인(최대 50) — 남의 id 는 존재도 알리지 않는다(`app/api/mcp.py:253-254`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L121-123 만료 삭제가 작업 행만 지우고 `_discard`(L74)처럼 mcp_relays 를 지우지 않는다. 고아 중계 행은 보존 정리(1일)까지 남는다. 또 만료로 행을 지우면 실행 중인 작업은 다음 `_beat`(L83)에서 취소되는데, 그 사실이 클라이언트에게는 "not found" 로만 보인다.
- L138 `list(task_ids or [])` — 문자열이 오면 글자 단위로 쪼개진다. 형식 검증은 `mcp_modern.listen_filter` 몫(교차 확인 대상).

```python path=app/services/mcp_tasks.py lines=144-165
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
```
**분석 / Analysis (Level 2)**
- L148: `task_` + 192비트 난수 — 추측 불가 id. 소유 검사와 겹쳐 이중 방어.
- L149-150: 행을 먼저 저장(내구 생성)한 뒤 중계를 설치 — 큐 없는 `Relay`(MRTR 형) 라서 업스트림의 sampling·elicitation 은 mcp_relays 행으로만 남고 `tasks/get` 의 inputRequests 로 노출된다. `mm.client_caps(params)` 가 하류 능력을 그대로 넘기므로 클라이언트가 sampling 을 선언했으면 중계, 아니면 Host 동의(`app/services/mcp_consent.py:47-56`).
- L153-163 `work`: `run`(= `dispatch`) 결과를 completed 로, 취소는 cancelled, 그 밖의 예외는 JSON-RPC 오류 본문을 failed 의 error 로. `finally` 로 `_JOBS` 정리.
- 흔한 실수: `except asyncio.CancelledError` 를 다시 던지지 않으면 상위의 취소 의미(구조적 동시성)가 사라진다. 여기서는 "작업 자신의 취소를 상태로 변환" 이 목적이라 수용.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L157-158 서버 종료(lifespan 종료 시 남은 Task 취소)도 같은 분기를 타 "클라이언트 요청으로 취소했습니다." 로 기록된다(1차 지적 미해결). 사유를 구분하려면 `handle` 쪽 취소 요청 여부(`cancel_requested`)를 보고 문구를 고른다.
- [위험:낮음] L155·L224 `job.cancel()` 은 asyncio 대기 지점만 끊는다. 이미 스레드풀에서 진행 중인 DB 작업이나 업스트림에 전달된 HTTP 요청은 멈추지 않으므로 "cancelled" 가 "도구가 실행되지 않음" 을 뜻하지 않는다. 고위험 도구라면 statusMessage 에 그 한계를 알리는 편이 정확하다.
- L156·L158·L161 `_finish` 자체가 DB 오류로 실패하면 예외가 `work()` 밖으로 나가 "Task exception was never retrieved" 로그만 남고 행은 working 으로 남는다(30초 뒤 표시상 failed).

```python path=app/services/mcp_tasks.py lines=167-179
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
```
**분석 / Analysis (Level 2)**
- L167-176 `watch`: 2초마다 생존 신호 + 취소 확인. 다른 워커에서 들어온 `tasks/cancel` 은 DB 플래그로만 전달되므로 이 루프가 다중 워커 취소의 유일한 경로다(최대 2초 지연).
- L173-176: 일시적 DB 오류는 경고 로그 후 다음 주기 재시도 — 한 번의 오류로 감시가 끝나 거짓 failed 가 되던 문제(delegated BUG-03) 해결. 조용한 삼킴이 아니라 로그가 남는다.
- L177-179: 강한 참조 + 완료 시 제거(BUG-10 해결).

```python path=app/services/mcp_tasks.py lines=181-201
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
- L181-188: 최대 2초 동안 0.1초 간격으로 입력 요청 발생 여부를 본다 — 생기면 즉시 작업으로 응답(클라이언트가 `tasks/get` 으로 inputRequests 를 받게).
- L189-198: 인라인 완료(completed·failed)면 작업 행을 지우고 일반 결과(`__final__`)로 — 짧은 호출은 작업을 남기지 않는다. `resultType` 은 하류 세대에 맞춰 `app/api/mcp.py:177-183` 이 다시 붙인다.
- L199-201: 아니면 DetailedTask 형태의 CreateTaskResult.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L199-200 `async def` 안에서 `SessionLocal()` 동기 조회 2회(`db.get` + `_pending`)를 **이벤트 루프 스레드에서 직접** 실행한다. 이 파일의 다른 DB 접근은 모두 `run_in_threadpool` 인데 여기만 빠졌다(1차 미지적). PostgreSQL 지연·풀 대기 동안 같은 워커의 모든 요청이 멈춘다. 최소 수정: `_final` 처럼 함수로 감싸 `run_in_threadpool`.
- [위험:낮음] L181-188 인라인 대기 동안 최대 20회 DB 조회(1차 지적 미해결). 고빈도 호출이면 부하.
- L193 행이 없으면 `("failed", None, None)` → L197-198 `{"error": None}` — JSON-RPC 오류 본문이 null 이 된다(드문 경로). L200 도 행이 없으면 `_view(None)` AttributeError(1차 지적 미해결).
- 주체별 작업 수 상한이 없다(1차 지적 미해결). 상한은 도구 한도(`app/services/mcp_gateway.py:940`)가 설정돼 있을 때만 간접 적용.

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
- L207-219 `_do`: 소유 확인이 먼저 — 남의 작업이면 즉시 -32602. get 은 DetailedTask, cancel 은 종료 전일 때 플래그.
- L221-224: 소유 확인을 통과한 뒤에만 `_JOBS` 를 본다 — 남의 작업을 이 워커에서 취소할 수 없다.
- L225-230 update: `inputResponses` dict 확인 후 `mm._answer`(`app/services/mcp_modern.py:247-258`)가 행마다 `session_id == task_id` 를 다시 확인 — 작업 소유 → 중계 행 바인딩으로 이어지는 2단 인가.
**짚어볼 점 / Watch-outs**
- L229 다른 모듈의 private `mm._answer` 직접 호출(1차 지적 미해결) — 공용 이름으로 승격 권장.
- `tasks/update` 는 결과만 넣을 수 있다(`row.response = {"result": res}`) — 하류가 "거부"를 오류로 알릴 방법이 없다. 사양 해석 확인 필요(가능성).

## 파일 평가 / File Assessment
- 강점: (1) 작업 소유권(주체·경로 서버)과 존재 은닉(-32602 통일), 192비트 id. (2) 내구 생성 + 생존 신호 + DB 취소 플래그로 다중 워커에서도 취소·장애가 드러난다. (3) 감시 Task 강한 참조·일시 오류 재시도·인라인 완료 시 중계 행 정리(delegated BUG-03·09·10 해결 상태 유지).
- 위험: (1) [위험:낮음] 정체 판정이 표시만 바꿔 종료 상태가 뒤바뀔 수 있음 — `app/services/mcp_tasks.py:102-103`. (2) [위험:낮음] 종료 전이 읽기-수정-쓰기 — `:60-66`·`:224`. (3) [위험:낮음] 이벤트 루프에서 동기 DB 조회 — `:199-200`.
- 개선 권고(영향순): (1) 상태 전이를 `UPDATE … WHERE status='working'` 로 바꾸고 정체 판정 시 failed 확정. (2) `start` 마지막 조회를 스레드풀로. (3) 취소 사유 구분·주체별 동시 작업 상한·인라인 대기를 이벤트 기반으로.
- 파일 위험 등급 / Risk grade: 낮음 — 소유권·은닉은 견고하고, 남은 지적은 상태 일관성·성능 수준의 [위험:낮음] 뿐.
- 재읽기 권고 레벨: 3 — 흐름이 짧고 패턴이 명확하다. 상태 전이 경쟁만 줄 단위로 다시 보면 된다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| L58-66 `_finish` 종료 판정 읽기-쓰기 경쟁 | 미해결 | `app/services/mcp_tasks.py:60-66` 그대로 |
| L102-103 정체 판정이 표시만 — 고아 working 행 정리 배치 없음 | 부분 해결 | 보존이 1일 뒤 삭제 `app/services/retention.py:33-34`; 표시만 failed 는 `mcp_tasks.py:102-103` 그대로 |
| L121-123 조회 경로에서 삭제·커밋 | 미해결(수용 가능) | `mcp_tasks.py:121-123` |
| L157-158 CancelledError 미재발생 · 종료 시 같은 경로 | 미해결 | `mcp_tasks.py:157-158` |
| L181-188 인라인 대기 최대 20회 DB 조회 | 미해결 | `mcp_tasks.py:181-188` |
| L200 행 None 이면 AttributeError | 미해결 | `mcp_tasks.py:199-200` |
| 작업 수 상한 없음 | 미해결 | `mcp_tasks.py:145-165` 상한 검사 없음 |
| L229 private `mm._answer` 직접 호출 | 미해결 | `mcp_tasks.py:229` |
| (delegated BUG-03) watch 예외 시 생존 신호 중단 | 해결 | `mcp_tasks.py:170-176` · `test/test_review_fixes.py:171-201` |
| (delegated BUG-04) McpTask 정리 없음 | 해결 | `app/services/retention.py:33-34` · `test/test_review_fixes.py:152-169` |
| (delegated BUG-09) `_discard` 후 고아 McpRelay | 해결 | `mcp_tasks.py:74` |
| (delegated BUG-10) watch fire-and-forget | 해결 | `mcp_tasks.py:34,177-179` |
