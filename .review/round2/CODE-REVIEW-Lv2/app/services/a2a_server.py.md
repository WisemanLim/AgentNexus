> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: 1차 리뷰 대상 아님(부록 A)

### 13. app/services/a2a_server.py
**파일 요약 / File Summary**
- 역할 / Role: A2A 서버 엔진(FR-A05). JSON-RPC 0.3(`message/send` 등)·1.0(`SendMessage` 등)과 HTTP+JSON(REST) 바인딩을 같은 `dispatch` 로 받아, 실행기(Executor)를 asyncio Task 로 돌리고 상태·아티팩트를 SSE 구독자·푸시 웹훅으로 흘린다. 플랫폼 A2A 서버(`/a2a/platform[/{profile}]`)와 데모 원격 에이전트(`app/builtin/a2a_agent.py`)가 공용으로 쓴다. 저장소는 프로세스 메모리 — 플랫폼 서버는 `on_change`·`loader`·`owner_lookup`·`remote_cancel` 훅으로 DB(`a2a_tasks`)에 영속하고 다른 워커의 Task 를 다룬다.
- 진입점 거리 / Distance: 2 — `app/api/a2a.py:241-278`(JSON-RPC·REST 라우트) → `app/api/a2a.py:176-225` `_platform_server` → `A2AServer.handle`/`rest`. gRPC 는 `app/services/a2a_grpc.py:128-133` 이 같은 REST 경로로 돌린다. 데모 에이전트는 `app/builtin/a2a_agent.py:186`(별도 앱).
- LOC: 394 · 1차 리뷰 시점 361(1차 INDEX 부록 A, `.review/CODE-REVIEW-Lv2/INDEX.md:320`) → +33
- 주요 export: `TaskContext`(`update`·`artifact`·`ask`·`canceled`), `Executor`, `A2AServer`(`handle`·`rest`·`dispatch`·`card_endpoint`, `MAX_TASKS`)
- 의존(내부): `app.core.http.post_with_retry`, `app.services.a2a_proto`(op·버전·변환·오류 코드), (지연) `app.services.a2a_rest`, `app.core.logs`
- 외부 패키지: starlette(`Request`·`JSONResponse`·`StreamingResponse`), 표준 asyncio·contextlib·json·uuid
- 부수효과 / Side effects: 프로세스 메모리 dict 7개(`tasks`·`ctxs`·`push`·`subscribers`·`runners`·`owners`·`_bg`), 실행기 Task·푸시 Task 생성, 아웃바운드 HTTP(푸시), `on_change` 경유 DB 쓰기(호출처 구현).
- 동시성 / Concurrency: 단일 이벤트 루프 안의 asyncio Task(실행기·푸시) + SSE 제너레이터. 워커 간 공유는 없음 — 플랫폼 서버는 훅으로 DB 를 거친다. 실행기 취소는 `asyncio.Task.cancel()`.
- 테스트 커버리지 흔적: `test/test_task_ownership.py:19-36`(C44-01 남의 Task 전 연산 -32001·목록 제외), `:40-47`(C44-02 메모리에 없는 Task 를 영속 행 주체로 판정), `:67-77`(C44-04 owner 없는 서버), `test/test_review_fixes.py:204-221`(C39-09 상한·종료 문맥 해제), `test/test_a2a.py:84`(input-required 재개)·`:93`(스트리밍)·`:127-157`(취소·푸시 — 게이트웨이 경유), `test/test_reliability.py:300-316`(영속 Task 완결·resubscribe), `test/test_policy_hardening.py:46`(푸시 URL 검사), `test/test_lv2_hardening.py:178-195`(`post_with_retry`). 스냅샷 이후(21:30) 추가된 `test/test_review2_fixes.py:56-77` 은 플랫폼 push_set 의 이름 해석이 루프 밖이기를 기대한다(아래 L281-288 참조).
- 2차 변경 요약 / Changes since 1st review: 1차 원문 사본이 없어(부록 A) 1차 위임 리뷰 인용(`.review/delegated/code-review.md:31-40` BUG-02, `:169-179` BUG-12)과 현재 코드·`test/impl/{39th,43rd,44th,49th}/scenario.md` 로 대조했다. (1) Epic 39 BUG-02 — 종료 시 `ctxs`·`runners` 해제(L111-113), 빈 구독 목록 제거(L131-132), `MAX_TASKS`·`_evict`(L69, L134-140, L163-164), 푸시 Task 강한 참조 `_bg`(L97, L108-110). (2) Epic 43 — `push_check` 훅(L75, L83, L283-284). (3) Epic 44 — `owner`·`owner_lookup`·`owners` 와 `_check_owner`(L76-77, L84-88, L146, L161-162, L246-250, L297-298, L307-317), 플랫폼 서버의 토큰→주체 사전 제거(`test/impl/44th/result.md` "영향" 2번째 항목 — 호출처 `app/api/a2a.py:180-188` 은 매 요청 인증). (4) Epic 49 — `_push` 가 `post_with_retry`(L119)·실패 경고(L120-121). Epic 51 C51-03(푸시 URL 검사를 루프 밖으로, `test/impl/51st/scenario.md:19`)은 진행 중이며 스냅샷 소스에는 반영되지 않았다(result.md 없음).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/a2a_server.py lines=1-18
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
```
**분석 / Analysis (Level 2)**
- L1-6: 설계 계약. 핵심 세 가지 — (1) 실행기는 `ctx.update()`/`ctx.artifact()` 로만 상태를 바꾼다(이벤트가 한 곳 `_emit` 으로 모인다), (2) `send` 는 기본 blocking(종료·input-required 까지 대기), (3) 저장소는 메모리이고 영속은 호출처 훅 책임. 다중 워커에서 "어느 워커의 메모리에 Task 가 있나" 가 모든 연산의 분기점이 된다는 점을 먼저 기억해 두면 뒤가 읽기 쉽다.
- L17: 푸시 전달은 공용 재시도 헬퍼(`app/core/http.py:156-170`) — 연결 오류·5xx·429 만 지수 백오프 3회, 그 밖의 4xx·egress 차단은 즉시 포기.
- L18: 0.3 ↔ 1.0 메서드·상태·오류 코드 변환은 `a2a_proto` 가 맡는다(`app/services/a2a_proto.py:33` `TERMINAL = {"completed", "failed", "canceled", "rejected"}`, `:37` 오류 코드).

```python path=app/services/a2a_server.py lines=21-32
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskContext:
    def __init__(self, server: "A2AServer", task: dict, message: dict, auth: dict):
        self.server, self.task, self.message, self.auth = server, task, message, auth
        self.inbox: asyncio.Queue = asyncio.Queue()  # input-required 후 추가 메시지

    @property
    def canceled(self) -> bool:
        return self.task["status"]["state"] == "canceled"
```
**분석 / Analysis (Level 2)**
- L21-22: tz-aware UTC ISO 문자열 — A2A 의 `timestamp` 필드.
- L25-28 `TaskContext`: 실행기 1회 실행의 문맥. `task` 는 서버의 `self.tasks[tid]` 와 **같은 dict 객체**(복사 아님)라 실행기가 바꾸면 곧바로 서버 상태가 된다. `auth` 는 `authenticate` 결과(플랫폼은 `{"principal": Principal}`, `app/api/a2a.py:188`).
- L28 `inbox`: 상한 없는 큐. input-required 응답 메시지를 실행기에 넘기는 통로(L151).
- L30-32 `canceled`: 협조적 취소 확인용 — 실행기가 루프 안에서 이 값을 보고 스스로 멈추는 패턴(`app/services/orchestrator.py:1133-1135`). 단 L276 이 실행기 Task 자체를 취소하므로 이 경로는 거의 쓰이지 않는다(아래 L264-280 Watch-out).

```python path=app/services/a2a_server.py lines=34-46
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
```
**분석 / Analysis (Level 2)**
- L35-36: 취소된 Task 에 대한 늦은 갱신은 버린다 — 취소 뒤 실행기가 "completed" 로 덮어쓰지 못하게 하는 가드.
- L38-42: 텍스트·데이터가 있으면 agent 메시지를 만들어 `history` 에 쌓는다. 진행 메시지("계획 중", "단계 완료")마다 history 가 늘어나므로 Task 당 history 크기는 실행기 동작에 비례한다.
- L43-46: 상태 교체 후 이벤트 발행. `final` 은 종료 상태 **또는** input-required — blocking `send`(L182-195)·SSE(L362-366)가 여기서 멈춘다.
- 흔한 실수: 종료 상태 판정에 `"canceled"` 철자(A2A 는 미국식 한 개 l). `ap.TERMINAL` 상수만 쓰면 된다.

```python path=app/services/a2a_server.py lines=48-62
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
```
**분석 / Analysis (Level 2)**
- L48-54 `artifact`: 아티팩트는 항상 한 번에 완결(`append: False`·`lastChunk: True`) — 청크 스트리밍은 지원하지 않는다. `update` 와 달리 `canceled` 가드가 없다(실행기 Task 가 취소되면 호출될 일이 거의 없어 실질 영향은 작다).
- L56-62 `ask`: input-required 로 바꾸고 5분 동안 `inbox` 를 기다린다. 응답은 `_start`(L149-151)가 넣는다. 시간 초과면 None — 이후 처리는 실행기 몫.
- 흔한 실수: 다중 워커에서 input-required 응답이 **다른 워커**로 가면 그 워커 메모리에는 이 `ctx` 가 없어 응답이 도달하지 않는다(L144-160 Watch-out). 현재 `ask` 를 쓰는 실행기는 데모 에이전트(`app/builtin/a2a_agent.py:134`, 단일 프로세스)뿐이고 플랫폼 실행기(`app/services/orchestrator.py:1107-1137`)는 쓰지 않는다.

```python path=app/services/a2a_server.py lines=65-97
Executor = Callable[[TaskContext], Awaitable[None]]


class A2AServer:
    MAX_TASKS = 1000  # 메모리에 두는 작업 수 상한 (넘으면 오래된 종료 작업부터)

    def __init__(self, card: Callable[[], dict], executor: Executor, *, authenticate=None, blocking_timeout: float = 30,
                 on_change: Callable[[dict, dict], Awaitable[None]] | None = None,
                 loader: Callable[[str], Awaitable[dict | None]] | None = None,
                 remote_cancel: Callable[[str], Awaitable[dict | None]] | None = None,
                 push_check: Callable[[str | None], str | None] | None = None,
                 owner: Callable[[dict], str | None] | None = None,
                 owner_lookup: Callable[[str], Awaitable[str | None]] | None = None):
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
```
**분석 / Analysis (Level 2)**
- L69 `MAX_TASKS`: 메모리 상한(종료 작업만 축출 — L134-140).
- L71-77: 모든 다중 워커·보안 동작이 **선택 훅**이다. 플랫폼 서버는 전부 넘기고(`app/api/a2a.py:221-223` — `push_check=push_url_problem`, `owner=lambda auth: auth["principal"].sub`), 데모 에이전트는 `authenticate` 만 넘긴다(`app/builtin/a2a_agent.py:186`). 훅이 없으면 소유권 검사·URL 검사도 없다는 뜻이라, 새 서버를 만들 때 훅 누락이 곧 인가 누락이 된다.
- L75 `push_check`: **동기** 콜백 타입(`Callable[[str | None], str | None]`) — 호출처가 넘기는 `push_url_problem` 은 내부에서 동기 DNS 를 한다(L281-288 Watch-out).
- L84-88: Epic 44 소유권. `owners` 는 tid → 주체 sub. 메모리에 없는 Task 는 `owner_lookup`(영속 행의 `principal_sub`, `app/api/a2a.py:154-157`)으로 판정 — 워커가 달라도 같은 결론.
- L92-97: 프로세스 메모리 저장소. `_bg` 는 asyncio 가 Task 를 약한 참조로만 들고 있다는 문서 경고에 대한 표준 해법(강한 참조 집합 + `add_done_callback(discard)`).
- 흔한 실수: `self.owners` 를 `self.tasks` 와 따로 관리하다 축출 때 하나만 지우는 것 — 여기서는 `_evict` 가 다섯 저장소를 함께 지운다(L139). 다만 `subscribers` 는 그 목록에 없다(L134-140 Watch-out).

```python path=app/services/a2a_server.py lines=99-121
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
        headers = {"Content-Type": "application/json"}
        if cfg.get("token"):
            headers["X-A2A-Notification-Token"] = cfg["token"]
        if not await post_with_retry(cfg["url"], content=json.dumps(task, ensure_ascii=False), headers=headers):
            from app.core import logs
            logs.event("warning", "a2a", "푸시 알림 전달 실패(재시도 후)", task_id=task.get("id"))
```
**분석 / Analysis (Level 2)**
- L100-103: 영속 → 통지 순서. 플랫폼 `on_change` 는 스레드풀에서 `a2a_tasks` 행을 upsert 한다(`app/api/a2a.py:108-123`, `:205-210`). 첫 이벤트(`submitted`, L167)에서 행이 생기며 `principal_sub` 가 이때 한 번만 기록된다 — 다른 워커의 소유권 판정(`owner_lookup`)이 이 행에 기대므로 첫 영속이 보안상 중요하다.
- L104-105: 구독자 큐에 그대로 넣기(상한 없는 큐 — 느린 소비자도 막지 않는다). `list(...)` 로 복사해 순회 중 구독 해제(`_unsubscribe`)가 일어나도 안전.
- L106-110: 푸시 설정이 있으면 이벤트마다 백그라운드 Task 하나. `_bg` 로 GC 방지(delegated BUG-10 패턴).
- L111-113: 종료 상태면 실행 문맥·러너 참조 해제(1차 delegated BUG-02 대응). `tasks`·`owners`·`push` 는 남겨 조회·재구독에 쓴다.
- L115-121 `_push`: 알림 토큰 헤더 + Task 전체 JSON 을 POST. 실패해도 예외를 올리지 않고 경고 로그(1차 BUG-12 의 "조용한 삼킴" 해결).
**짚어볼 점 / Watch-outs**
- [위험:중간] L119 **전달 시점에는 사설망 검사가 없다.** 등록 시점 검사(L283, 운영 모드에서만 `internal_url` — 사설·루프백·링크 로컬·예약 거부, `app/core/http.py:102-120`)를 통과한 URL 이 실제 전달 때는 `async_client` 기본 가드(링크 로컬·IMDS 만, `app/core/http.py:36-38`, `:128-137`)만 거친다. 시나리오: chat:use 사용자가 플랫폼 Task 에 `https://hook.attacker.example/x` 를 등록(그 순간 공인 IP → 통과)한 뒤 DNS 를 `10.0.0.5`(사내 관리 콘솔)로 바꾸거나, 등록 때는 해석되지 않던 이름(L119-120 이 `False` 로 통과)에 나중에 사설 레코드를 만들면, 이후 상태 변화마다 게이트웨이가 내부 주소로 Task JSON(history 포함)을 POST 한다(블라인드 SSRF). 같은 지적이 `app/core/http.py` 2차 보고서(L181)·delegated SEC2-02 에 있다. 최소 수정: 콜백 전용 엄격 모드 — `post_with_retry` 에 매 요청 `internal_url` 판정(가능하면 해석 IP 고정 연결) 옵션을 두고 `_push` 가 그것을 쓴다.
- [위험:낮음] L101-103 `contextlib.suppress(Exception)` 은 **로그 없이** 영속 실패를 삼킨다(1차 INDEX §4.1 패턴 6). 주석의 "클라이언트가 받은 상태는 항상 저장된 상태" 는 영속이 실패하면 거짓이 된다. 첫 영속이 실패하면 행이 없으므로 다른 워커의 `owner_lookup` 이 None → 주인도 그 워커에서는 -32001(안전 쪽 실패지만 원인 추적 불가). 최소 수정: `except Exception as exc: logs.event("warning", "a2a", "Task 영속 실패", task_id=..., error=type(exc).__name__)`.
- [위험:낮음] L106-110 이벤트마다 독립 Task 로 전달 + 재시도(최대 0.5+1초 대기, 시도당 5초)라 **수신 순서가 뒤바뀔 수 있다.** 시나리오: `working` 푸시가 5xx 로 재시도 중일 때 `completed` 푸시가 먼저 도착하면, 수신 측의 마지막 메시지가 `working` 이 된다. 또 L119 의 `json.dumps(task)` 는 Task 가 스케줄된 뒤 실행될 때의 **현재 상태**를 보내므로 이벤트와 본문 상태가 다를 수 있다. 동시 전송 상한도 없다. 최소 수정: Task 별 직렬 큐(마지막 상태만 보내는 coalescing) 또는 본문에 단조 증가 시퀀스.
- [위험:낮음] L103 은 살아 있는 `task` dict 를 그대로 넘긴다. 플랫폼 `_persist` 는 이것을 **스레드풀**에서 읽어 `row.history = task.get("history")` 로 같은 리스트 객체를 ORM 에 넘긴다(`app/api/a2a.py:117-118`). 직렬화 도중 루프 스레드가 같은 리스트에 append 하면(다른 이벤트) 저장 내용이 어긋날 수 있다(가능성). 최소 수정: `on_change(_snapshot(task), event)`.

```python path=app/services/a2a_server.py lines=123-140
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
```
**분석 / Analysis (Level 2)**
- L123-132: 구독 = Task 별 큐 목록. 마지막 구독이 빠지면 키까지 지워 빈 목록이 쌓이지 않게 한다(C39-09, `test/test_review_fixes.py:221`). `ValueError` 억제는 "이미 빠진 큐" 의 이중 해제를 허용하려는 의도 — 정당한 억제다.
- L134-140 `_evict`: dict 는 삽입 순서를 유지하므로 목록의 앞쪽이 오래된 Task. 종료 작업만 대상이고, 영속 저장소가 있으면 `get` 이 `loader` 로 다시 읽는다(L259).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L136 **진행 중(working·input-required) Task 는 축출하지 않는다**(delegated CR2-02 미해결). 플랫폼 실행기는 900초 마감(`app/services/orchestrator.py:1114`)이 있어 무한 증가는 아니지만, 주체별 동시 Task 상한이 이 파일에도 호출처(`app/api/a2a.py:241-278`)에도 없어 한 사용자가 `returnImmediately` 로 빠르게 Task 를 만들면 15분 창 안에 1000개를 넘길 수 있다(각 Task 가 오케스트레이션 실행 1건 — 메모리보다 LLM 비용이 먼저 문제다. 예산 한도 검사가 `start_run` 쪽에 있는지는 추가 확인 필요). 또 상한을 넘은 상태에서는 Task 생성마다 L136 이 전체를 훑는다(O(n)). 최소 수정: 주체별 동시 Task 상한(`owners` 집계) + 상한 초과 시 오래된 진행 Task 의 메모리 사본만 내리고(영속 있음) 러너는 유지.
- [위험:낮음] L139 축출 대상에 `subscribers` 가 없다. 보통은 구독이 끝나며 비지만, 구독만 걸리고 제너레이터가 시작되지 않은 SSE(L353-354 Watch-out)는 큐가 영구히 남고 이후 같은 tid 의 이벤트가 계속 쌓인다.

```python path=app/services/a2a_server.py lines=142-180
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
            except Exception as exc:  # noqa: BLE001
                await ctx.update("failed", f"에이전트 오류: {type(exc).__name__}: {exc}")
        self.runners[task["id"]] = asyncio.create_task(run())
        return task
```
**분석 / Analysis (Level 2)**
- L144-155 이어 보내기: 이 워커 메모리에 Task 가 있으면 먼저 소유권(Epic 44) → history 추가 → input-required 이고 실행 문맥이 있으면 `working` 으로 바꾼 뒤 inbox 로 전달. 종료 Task 면 UNSUPPORTED_OP.
- L156-159: 새 Task — id 는 서버가 128비트 난수로 정한다(클라이언트 id 를 쓰지 않음 — Task id 추측·충돌 방지). 첫 history 항목의 `taskId` 를 새 id 로 바꾼다.
- L160-162: 소유자 기록은 `tasks` 등록 직후, 첫 `await` 전 — 동시에 들어온 `get` 이 "있는데 주인 없음" 상태를 볼 틈이 없다(`_check_owner` L315 에서 None 이면 거부되므로 어차피 안전 쪽).
- L165-168: 실행 문맥 등록 → `submitted` 발행(여기서 영속 행 생성, L101-103).
- L170-179 `run`: 실행기가 종료 상태를 남기지 않고 끝나면 completed 로 마무리. `CancelledError` 는 다시 던져 취소 의미를 보존(구조적 동시성 — 올바름). 그 밖의 예외는 failed.
- 흔한 실수: `asyncio.create_task(run())` 반환값을 버리는 것 — 여기서는 `runners` 에 보관(종료 때 L113 이 해제).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L145 `tid in self.tasks` 가 거짓이면(다른 워커의 Task·축출된 Task) **클라이언트가 준 `taskId` 를 조용히 버리고 새 Task 를 만든다**(L156). 결과: 2 워커 운영에서 이어 보내기의 절반이 새 Task·새 오케스트레이션 실행이 되고, 다른 워커에 있는 종료 Task 에 이어 보내도 UNSUPPORTED_OP 대신 새 Task 가 생긴다. 남의 Task 에 끼어들 수는 없으므로(새 Task 의 주인은 호출자) 인가 문제는 아니다. 최소 수정: `tid` 가 있는데 메모리에 없으면 `owner_lookup`/`loader` 로 확인해 주인이면 "다른 인스턴스 Task — 이어 보내기 불가"(-32004), 아니면 -32001.
- [위험:낮음] L148 history 추가가 L153 종료 검사보다 먼저라 종료 Task 의 메모리 history 가 오류 응답과 함께 늘어난다. 또 working 중인 Task 에 메시지를 계속 보내면(L155) 실행기에 전달되지 않는 메시지가 history 에만 무한히 쌓인다(주인만 가능, 요청 크기 상한에 기댐).
- [위험:낮음] L178 예외 문자열을 그대로 클라이언트에게 보낸다(`에이전트 오류: {type}: {exc}`) — DB·내부 호스트 이름이 담긴 예외면 정보 노출. 최소 수정: 클라이언트에는 일반 문구 + 추적 id, 상세는 로그.

```python path=app/services/a2a_server.py lines=182-195
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
```
**분석 / Analysis (Level 2)**
- L182-184: 이미 끝났거나 입력 대기면 기다리지 않는다.
- L185: 상태 확인과 구독 사이에 `await` 가 없어 이벤트를 놓치지 않는다(단일 이벤트 루프의 원자 구간). 이 순서를 바꾸면(구독 전에 await) 그 사이 `final` 을 놓쳐 timeout 까지 기다리게 된다.
- L187: `asyncio.timeout`(Python 3.11+) — 만료 시 내장 `TimeoutError`. `finally` 로 구독 해제를 보장(취소돼도 실행).
- 흔한 실수: `asyncio.wait_for(q.get(), …)` 를 루프마다 새로 거는 것 — 전체 마감이 아니라 이벤트 간격 마감이 된다. 여기서는 전체 마감이 맞게 구현됐다.

```python path=app/services/a2a_server.py lines=197-216
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
```
**분석 / Analysis (Level 2)**
- L198-203 `handle`: JSON 파싱 실패만 -32700. 인증은 `dispatch` 첫머리(L222-226)에서 — 파싱 오류 응답은 인증 전에 나간다(정보 노출 없음).
- L205-216 `rest`: REST 경로·쿼리·본문 → (메서드, params) 변환 후 같은 `dispatch`. 바인딩이 셋(JSON-RPC·REST·gRPC→REST)이어도 인가·소유권 검사가 한 곳에 모인다 — 좋은 구조.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L203 본문이 dict 가 아니면(`[]`·`"x"`) `body.get` 에서 AttributeError → 500. `params` 가 list 면 L231 등의 `params.get` 에서 500. 호출처의 게이트웨이 경로는 `_jsonrpc`(`app/api/a2a.py:51-58`)로 -32600 을 돌려주는데 플랫폼 서버는 이 검사가 없다. 최소 수정: `if not isinstance(body, dict) or not isinstance(body.get("method"), str): return -32600`, params 도 dict 확인.

```python path=app/services/a2a_server.py lines=218-245
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
```
**분석 / Analysis (Level 2)**
- L219-220: 메서드 이름 → 연산·버전(1.0 은 대문자 시작, `app/services/a2a_proto.py:40-45`).
- L221-226: 인증이 **모든 연산보다 먼저** — 메서드 존재 여부도 인증 뒤에 알려 준다. 플랫폼 인증은 매 요청 토큰 검증(`app/api/a2a.py:180-188`, `chat:use` 필요 — `:228-233`).
- L230-239 send: 메시지 정규화(`message_from` — messageId 없으면 생성, `app/services/a2a_proto.py:101-110`) → 시작 → blocking 이면 최대 `blocking_timeout`(플랫폼 60초) 대기 → 스냅샷(깊은 복사) 응답.
- L240-245 stream: 시작 후 SSE. 시작과 구독(`_sse` 의 L354) 사이에 await 가 없어 첫 이벤트를 놓치지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L234 `configuration.pushNotificationConfig`(A2A `MessageSendConfiguration` — send 와 함께 푸시 등록)를 무시한다. `returnImmediately` 로 보내고 푸시로 결과를 받으려는 클라이언트는 알림을 하나도 받지 못한다(별도 `pushNotificationConfig/set` 필요). 넣을 때는 L283 과 같은 URL 검사를 거쳐야 한다.
- L231·L234 `message`·`configuration` 이 dict 가 아니면 AttributeError → 500(위 L203 과 같은 부류).

```python path=app/services/a2a_server.py lines=246-263
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
```
**분석 / Analysis (Level 2)**
- L246-250: Epic 44 — Task id 를 받는 모든 연산이 **존재 확인보다 먼저** 소유권을 본다. 남의 Task 와 없는 Task 가 같은 -32001 이라 id 추측으로 존재를 알 수 없다. push_set 도 URL 검사(L283)보다 먼저 소유권 — `test/impl/44th/result.md` "영향" 1번째 항목(남의 Task 는 URL 검사보다 먼저 -32001)과 일치.
- L247-248 push_get 은 `taskId`(0.3)·`id`(1.0 REST 변환) 둘 다 받는다 — L290 과 같은 규칙으로 고른다(검사한 id 와 쓰는 id 가 같아야 한다는 점이 중요 — 다르면 TOCTOU 형 인가 우회가 된다. 여기서는 일치).
- L251-256 resubscribe: 메모리에 없고 영속 저장소가 있으면 폴링 SSE(L330-351), 있으면 실시간 SSE.
- L257-263 get: 메모리 → 영속 순. `historyLength` 로 history 꼬리만.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L262 `int(params["historyLength"])` — 숫자가 아닌 문자열이면 ValueError → 500, 음수면 `[-(-3):]` = 앞 3개를 **뺀** 나머지가 나온다(의미 반전). 최소 수정: `n = params.get("historyLength"); if not isinstance(n, int) or n < 0: -32602`.
- L247 `_check_owner` 는 `tid` 가 비면 곧바로 통과(L308) — 이어지는 `_task`/`_load` 가 -32001 을 내므로 결과는 안전하다. `tid` 가 dict·list 면 L310 의 `in self.owners` 에서 TypeError → 500.

```python path=app/services/a2a_server.py lines=264-280
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
```
**분석 / Analysis (Level 2)**
- L266-270 원격 취소: 다른 워커의 Task — 영속 상태로 종료 여부 확인 후 호출처 훅. 플랫폼 훅은 **오케스트레이션 실행을 취소**하고 행을 canceled 로 만든다(`app/api/a2a.py:160-173` → `orchestrator.cancel(run_id)`).
- L271-280 로컬 취소: 러너 Task 취소 → 상태 canceled → 이벤트(영속·구독·푸시). `_emit` 이 종료 상태라 `ctxs`·`runners` 를 해제한다.
**짚어볼 점 / Watch-outs**
- [위험:중간] L274-276 **로컬 취소는 실행기 Task 만 끊고 오케스트레이션 실행(run)은 계속 돈다.** 플랫폼 실행기 `orchestrator.a2a_executor` 는 `schedule(run_id)`(`app/services/orchestrator.py:1113`)로 실행을 **별도 Task** 로 띄운 뒤 이벤트만 폴링하고, 실행 취소는 루프 안의 협조적 분기 `if ctx.canceled: cancel(run_id)`(`:1133-1135`)에만 있다. L276 의 `runner.cancel()` 은 그 루프의 `await`(`:1116` 또는 `:1136`)에서 CancelledError 를 일으켜 실행기를 즉시 끝내므로 `cancel(run_id)` 에 도달하지 못한다(`schedule` 의 Task 는 `:294-303` 에서 독립 생성). 시나리오: A2A 클라이언트가 같은 워커로 `tasks/cancel` → 응답은 canceled, 하지만 실행은 계획·도구 호출·LLM 호출을 끝까지 진행(비용·부작용). 다른 워커로 간 취소(L266-270)만 실행을 멈추는 비대칭이다. 또 끝까지 간 실행 결과는 `_load_platform` 이 비종료 행을 실행 상태로 완결할 때 쓰이지만(`app/api/a2a.py:135-144`), L278 의 영속이 먼저 canceled(종료)를 기록하므로 보통은 덮어쓰지 않는다 — 대신 L101-103 영속이 실패하거나 순서가 꼬이면 다른 워커가 completed 를 보여 줄 수 있다(가능성). 최소 수정: A2AServer 에 `on_cancel(task)` 훅을 두고 L276 전에 호출(플랫폼은 `task["metadata"]["anx_run_id"]` 로 `orchestrator.cancel`), 또는 플랫폼 실행기가 `except asyncio.CancelledError: cancel(run_id); raise`. 시험 공백: 플랫폼 Task 로컬 취소 후 Run 상태를 확인하는 시험 없음(`test/test_a2a.py:127-135` 는 게이트웨이 경유 데모 에이전트 취소만).
- [위험:낮음] L266-270 원격 취소는 L267 조회와 훅 사이 TOCTOU — 그 사이 실행이 끝나도 `_cancel_platform` 은 조건 없이 canceled 로 덮는다(`app/api/a2a.py:169-172`). 완료된 Task 가 canceled 로 보일 수 있다. 최소 수정: 훅 쪽 `UPDATE … WHERE state NOT IN (terminal)`.

```python path=app/services/a2a_server.py lines=281-305
            if op == "push_set":
                tid, cfg = ap.push_config_of(params)
                if self.push_check and (reason := self.push_check(cfg.get("url"))):
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
                mine = [t for t in list(self.tasks.values())
                        if self.owner is None or self.owners.get(t["id"]) == self.owner(auth)]
                items = [ap.result_to(_snapshot(t), version, "get") for t in mine[-50:]]
                return _ok(rid, {"tasks": items})
            if op == "card":
                return _ok(rid, self.card())
        except _RpcErr as exc:
            return _err(rid, exc.code, exc.message)
        return _err(rid, ap.UNSUPPORTED_OP, f"지원하지 않는 작업: {method}")
```
**분석 / Analysis (Level 2)**
- L282-286 push_set: 소유권(L249-250) → URL 검사(호출처 정책, 거부 사유가 있으면 -32602) → Task 존재 → 메모리에 설정 저장. `push_config_of` 는 0.3 `pushNotificationConfig` 객체·1.0 평면 필드 둘 다 받는다(`app/services/a2a_proto.py:246-250`).
- L289-295 push_get: 메모리 설정만 돌려준다(토큰 포함 — 등록한 주인에게만 보이므로 수용).
- L296-300 list: 이 서버(=이 워커 메모리)의 내 Task 만, 최근 50개.
- L301-302 card: 확장 카드(인증된 호출자).
- L303-305: 도메인 오류만 JSON-RPC 오류로 — 그 밖의 예외는 500 으로 올라간다.
**짚어볼 점 / Watch-outs**
- [위험:중간] L283 `self.push_check(...)` 는 **동기 함수를 이벤트 루프에서 직접** 부른다. 플랫폼이 넘기는 `push_url_problem`(`app/services/a2a_gateway.py:649-656`)은 운영 모드에서 `internal_url` → `socket.getaddrinfo`(`app/core/http.py:117-118`)를 한다. 시나리오: 운영(prod)에서 chat:use 사용자가 자기 Task 에 응답이 느린 권한 DNS 를 가진 도메인(`https://slow.attacker.example/h`)을 등록하면, 해석이 끝날 때까지(리졸버 타임아웃·재시도로 수 초~수십 초) 그 워커의 **모든** 요청(SSE·LLM 스트림·MCP 중계)이 멈춘다. 반복하면 2 워커 전체가 정지. delegated CR2-01(HIGH) 미해결 — 스냅샷 이후 작성된 `test/test_review2_fixes.py:56-77` 와 `test/impl/51st/scenario.md:19`(C51-03)가 이를 고치는 중임을 보여 주지만 스냅샷 소스는 그대로다. 최소 수정: `reason = await run_in_threadpool(self.push_check, cfg.get("url"))` 또는 훅을 async 로.
- [위험:낮음] L285-286 **push_set 은 이 워커 메모리에 Task 가 있어야 한다.** 소유권은 L250 이 영속 행으로 통과시키지만 L285 `_task` 가 -32001 을 낸다 — 2 워커에서 주인의 push_set 이 절반 확률로 "Task not found". 설정도 메모리에만 있어 재시작·축출(L139)로 사라지고, push_get(L291)도 다른 워커에서는 "푸시 설정 없음". 최소 수정: 플랫폼 영속 행에 push 설정(토큰은 암호화)을 저장하고, 이벤트를 내는 워커(러너가 있는 워커)가 영속 설정을 읽게.
- [위험:낮음] L297 list 는 이 워커 메모리만 본다 — 2 워커에서 같은 사용자의 목록이 요청마다 달라진다. 최소 수정: 영속 저장소 조회 훅(`lister(sub)`).
- L301-302 `self.card()` — 플랫폼 카드 함수는 동기 DB 조회를 한다(`app/api/a2a.py:81-86`). 이벤트 루프에서 직접 실행되므로 DB 지연이 루프 정지로 이어진다(사소, 호출 빈도 낮음).

```python path=app/services/a2a_server.py lines=307-328
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
```
**분석 / Analysis (Level 2)**
- L307-317 `_check_owner`: 판정 순서 — 메모리 주인 기록 → (메모리에 없으면) 영속 행 주인 → 그 밖은 None. **주인을 모르면 거부**(fail-closed): 메모리에 Task 는 있는데 주인 기록이 없거나, 영속 행이 없거나, 행의 `principal_sub` 가 비면 모두 -32001. Epic 44 의 핵심 — 다른 워커가 만든 Task 도 영속 행으로 같은 판정(`test/test_task_ownership.py:40-47`).
- L308: `owner` 훅이 없는 서버(데모 에이전트)는 검사 없음 — 의도(`:67-77`).
- L319-328: 메모리·영속 조회의 단일 오류 경로(존재 은닉과 같은 코드·문구).
- 흔한 실수: "Forbidden(403)" 을 따로 돌려주는 것 — 그러면 남의 Task 존재가 드러난다. 여기서는 NotFound 로 통일.
**짚어볼 점 / Watch-outs**
- L313 `owner_lookup` 은 매 요청 DB 조회(스레드풀, `app/api/a2a.py:218-219`) — 다른 워커 Task 접근마다 1회. 캐시가 없어도 정확성 쪽이 낫다(주인은 바뀌지 않으니 캐시해도 안전하긴 하다).

```python path=app/services/a2a_server.py lines=330-351
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
```
**분석 / Analysis (Level 2)**
- L330-351: 다른 워커의 Task 재구독 — 1초마다 `loader` 로 영속 상태를 읽어 상태 변화·새 아티팩트만 이벤트로. 최대 15분. 구독 큐가 없으므로 자원 해제 걱정이 없다(제너레이터가 끝나면 그만).
- L339 `or cur`: 행이 사라지면(보존 정리) 직전 상태를 유지.
- 흔한 실수: 아티팩트 diff 를 "개수" 로만 하는 것 — 영속 행의 artifacts 가 순서 보존 append-only 라서 성립한다(`app/api/a2a.py:117-118`·`:140-142`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L337-338 구독자마다 1초 DB 조회(플랫폼 `loader` 는 실행이 끝났으면 행을 **쓰기**까지 한다 — `app/api/a2a.py:135-144`), 동시 재구독 상한 없음. L340 은 상태·아티팩트 수만 비교해 working 중의 진행 메시지("단계 완료")는 흘리지 않는다. 15분 마감에 도달하면 `final` 없이 스트림이 끝나 클라이언트는 이유를 모른다. 최소 수정: 주체별 동시 재구독 상한, `status.timestamp` 비교, 마감 때 현재 상태 `final:false` 이벤트 + 주석으로 재구독 안내.

```python path=app/services/a2a_server.py lines=353-372
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
```
**분석 / Analysis (Level 2)**
- L354: 구독은 **응답 객체를 만들 때** 건다(제너레이터 밖) — `_start` 직후 이벤트를 놓치지 않으려는 의도.
- L356-368: 첫 스냅샷 → 종료면 끝 → 이벤트를 흘리다 `final` 이면 끝. `finally` 의 `_unsubscribe` 는 동기라 클라이언트 단절로 제너레이터가 취소되어도 확실히 실행된다(finally 안에 await 가 없다는 점이 중요 — `app/services/mcp_modern.py:499` 주석과 같은 교훈).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L354 와 L356 의 분리 때문에 **제너레이터가 한 번도 시작되지 않으면 `finally` 도 실행되지 않는다**(시작 전 async 제너레이터는 try 블록에 들어간 적이 없다). StreamingResponse 가 본문 반복을 시작하기 전에 클라이언트가 끊어 응답이 취소되면(Starlette 가 단절 감지로 응답 Task 그룹을 취소하는 경로 — 버전·ASGI spec 에 따라 다름, 가능성) 큐가 `subscribers` 에 남고 `_evict` 도 지우지 않는다(L139). 이후 그 Task 의 이벤트가 계속 쌓인다(Task 수명 동안). 최소 수정: 구독을 제너레이터 안 첫 줄로 옮기고 "구독 → 스냅샷" 순서로 바꾼다(스냅샷이 구독 뒤라 이벤트를 놓치지 않는다), 또는 `StreamingResponse(background=...)` 로 해제.
- [위험:낮음] L362-366 실시간 SSE 에는 최대 시간이 없다(폴링 SSE 는 15분). 실행기가 멈춰 `final` 이 오지 않으면 연결이 무기한 열린다. 프록시 유휴 종료를 막는 주석 핑도 없다.
- L360: 이미 끝난 Task 는 스냅샷 1개 후 종료 — `final` 이벤트가 따로 없으므로 클라이언트는 스냅샷의 상태로 판단해야 한다(사양상 허용).

```python path=app/services/a2a_server.py lines=375-394
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
```
**분석 / Analysis (Level 2)**
- L375-378: 도메인 오류 → JSON-RPC 오류(L303-304).
- L381-382 `_snapshot`: JSON 왕복 깊은 복사 — 응답 직렬화 중 실행기가 같은 dict 를 바꿔도 응답이 일관되게. 부수 효과로 JSON 불가 값(실행기가 넣은 datetime 등)이 있으면 TypeError → 500.
- L393-394 `_sse_line`: SSE `data:` 한 줄 JSON-RPC 응답(A2A 스트리밍 응답 형식). JSON 안의 줄바꿈은 이스케이프되므로 SSE 프레이밍이 깨지지 않는다.

## 파일 평가 / File Assessment
- 강점: (1) 바인딩 셋(JSON-RPC·REST·gRPC→REST)이 한 `dispatch` 로 모여 인증·소유권 검사가 한 곳 — Epic 44 소유권이 존재 은닉(-32001 통일)·fail-closed·영속 행 판정까지 일관(`app/services/a2a_server.py:246-250`, `:307-317`). (2) 구독·대기의 "확인 → 구독" 사이 await 없음, `finally` 의 동기 해제, 실행기·푸시 Task 강한 참조 등 asyncio 수명 관리가 정확(`:182-195`, `:97`, `:179`). (3) 1차 위임 지적(BUG-02 종료 문맥 해제·상한, BUG-12 푸시 재시도·실패 로그) 반영과 시험(`test/test_review_fixes.py:204-221`, `test/test_lv2_hardening.py:178-195`).
- 위험: (1) [위험:중간] 로컬 취소가 오케스트레이션 실행을 멈추지 않음 — `app/services/a2a_server.py:274-276`(+ `app/services/orchestrator.py:1113`, `:1133-1135`). (2) [위험:중간] push_set 의 동기 DNS 가 이벤트 루프 정지 — `:283`(CR2-01). (3) [위험:중간] 푸시 전달 시점 사설망 미검사(DNS 재바인딩) — `:119`(+ `app/core/http.py:128-137`).
- 개선 권고(영향순): (1) `on_cancel` 훅(또는 플랫폼 실행기의 CancelledError 처리)으로 로컬 취소 시 `orchestrator.cancel(run_id)`. (2) `push_check` 를 스레드풀/async 로, 전달은 콜백 전용 엄격 가드(매 요청 `internal_url`·IP 고정). (3) 다중 워커 일관성 — push 설정·목록·이어 보내기를 영속 저장소 기준으로, 주체별 동시 Task 상한, `on_change` 실패 로그, 입력 형식 검사(-32600/-32602)로 500 제거.
- 파일 위험 등급 / Risk grade: 높음 — 보안 경계(Task 소유권·사용자 지정 푸시 URL) 모듈에 미해결 [위험:중간] 3건(취소 비대칭·루프 정지 DNS·전달 시점 SSRF). 소유권 자체는 견고하다.
- 재읽기 권고 레벨: 2 — 다중 워커 분기("메모리에 있나")와 훅 계약이 연산마다 달라, 훅 구현(`app/api/a2a.py:108-225`)과 나란히 줄 단위로 다시 읽어야 한다.

## 1차 리뷰 대비 / vs 1st Review
1차 라인 리뷰 대상이 아니었으므로(부록 A, `.review/CODE-REVIEW-Lv2/INDEX.md:320`) 1차 INDEX §4.1·§4.3 과 `.review/delegated/{code-review,security-review}.md`·`.review/round2/delegated/code-review.md` 중 이 파일 관련 항목만 비교한다.

| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| delegated BUG-02 (`.review/delegated/code-review.md:30-40`) — `tasks`·`ctxs`·`runners`·`push` 추가만, 제거 없음 | 부분 해결 | 종료 시 해제 `app/services/a2a_server.py:111-113`, 빈 구독 제거 `:131-132`, 상한·종료 작업 축출 `:69`, `:134-140`, `:163-164` · 시험 `test/test_review_fixes.py:204-221`. 진행 Task 미축출(round2 CR2-02)·`subscribers` 축출 목록 누락 `:139` 남음 |
| delegated BUG-12 (`.review/delegated/code-review.md:168-179`) — `_push` fire-and-forget·`suppress(Exception)`·재시도 없음 | 해결 | `post_with_retry` `:119`(재시도 `app/core/http.py:156-170`) · 실패 경고 `:120-121` · 강한 참조 `:97`, `:108-110` · 시험 `test/test_lv2_hardening.py:178-195` |
| delegated SEC-04 (`.review/delegated/security-review.md:160-179`) — 사용자 입력 푸시 URL 로 서버측 요청 | 부분 해결 | 등록 시 검사 `:283-284`(운영 모드에서만 사설·루프백 거부, `app/services/a2a_gateway.py:649-656`). 전달 시점은 링크 로컬·IMDS 만(`:119` → `app/core/http.py:128-137`) — DNS 재바인딩·나중 레코드로 우회 가능. 로컬·test 모드는 검사 없음(`app_env` 기본 `local`, `app/core/config.py:17`) |
| 1차 INDEX §4.1-6 조용한 예외 삼킴(`contextlib.suppress(Exception)` — a2a 계열 포함) | 미해결(이 파일) | `on_change` 실패 무로그 삼킴 `:101-103` |
| 1차 INDEX §4.3-3 클라이언트 웹훅 SSRF(조용한 실패) | 부분 해결 | 실패 로그 `:120-121`로 조용한 실패는 해소, 전달 시점 검사 공백은 위 SEC-04 와 같음 |
| round2 delegated CR2-01 (`.review/round2/delegated/code-review.md:18-65`) — `push_check` 동기 DNS 가 이벤트 루프 차단 | 미해결(스냅샷 기준) | `:283` 동기 호출 그대로. Epic 51 C51-03 진행 중(`test/impl/51st/scenario.md:19`, 스냅샷 이후 시험 `test/test_review2_fixes.py:56-77`) |
| round2 delegated CR2-02 (`.review/round2/delegated/code-review.md:67-100`) — `_evict` 가 종료 작업만 | 미해결 | `:136` |
| (참고) round2 delegated security "A2A task ownership OK" (`.review/round2/delegated/security-review.md:270`) | 해결 확인 | 플랫폼 서버 `:246-250`, `:146`, `:297-298`, `:307-317` · 시험 `test/test_task_ownership.py:19-47` |
