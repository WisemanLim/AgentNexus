> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [api_Abstract.md](api_Abstract.md) · 1차 리뷰: [a2a.py.md](../../../../CODE-REVIEW-Lv2/app/api/a2a.py.md)

### 26. app/api/a2a.py
**파일 요약 / File Summary**
- 역할 / Role: A2A 경로 API — (1) 플랫폼 A2A 서버(FR-A05: well-known 카드·JSON-RPC·HTTP+JSON, Task 영속·Task 소유권 훅·원격 취소), (2) 게이트웨이 A2A 프록시(FR-G08: `/a2a/{id}` JSON-RPC·REST·스킬 라우팅·게이트웨이 경유 카드·푸시 수신), (3) 원격 에이전트 관리(SCR-G01~G04: 등록·dry_run 카드 점검·인증 설정·카드 재검증·시험 콘솔), (4) Task 조회·아티팩트·취소(SCR-G05·G06)·카드 캐시(SCR-H15), (5) 카탈로그 `a2a_agent` 등록 핸들러.
- 진입점 거리 / Distance: 1 — `app/main.py:179`(`include_router`, gateway 구성요소), 실행기 교체 `app/main.py:182`(`PLATFORM["executor"] = orchestrator.a2a_executor`). gRPC 바인딩은 `app/services/a2a_grpc.py` 가 이 파일의 REST 경로로 위임(`x-agentnexus-*` 헤더 전달, `a2a_grpc.py:38`).
- LOC: 727 (현재 디스크 `wc -l`) · 1차 리뷰 시점 719 → +8 — `max_lines_per_file`(600) 초과 → 논리 블록 단위로 분석(모든 블록 커버)
- 주요 export: `router`, `PLATFORM`(실행기 교체점), `PlatformExecutor`, `platform_card`, `framework_hint`, `agent_out`, `create_agent_row`, `card_review`, `task_out`, 입력 모델 `AgentIn`·`AgentPatch`·`TryIn`
- 의존(내부): `app.core.{auth,config,errors,ids}`, `app.db`, `app.models`, `app.services.{a2a_proto,a2a_rest,audit,policy,registry,secrets,a2a_gateway,a2a_server}`, 지연 import `app.services.orchestrator`(L161)·`app.services.notify`(L489)
- 외부 패키지: fastapi, pydantic, sqlalchemy, starlette(`run_in_threadpool`)
- 부수효과 / Side effects: 원격 에이전트 HTTP(프록시·카드 조회·OAuth 토큰), DB 쓰기(에이전트·카탈로그·버전·Task·시크릿·라우팅 결정), 감사·알림, 모듈 import 시 카탈로그 핸들러 등록(L713), 프로세스 전역 `_SERVERS`·`PLATFORM`
- 동시성 / Concurrency: async 핸들러 + `run_in_threadpool` 로 동기 DB 분리가 원칙이지만, `_ctx`(L47)·노출 프로파일 확인(L265·L273)·플랫폼 실행기(L193·L197)는 이벤트 루프에서 동기 DB 를 연다. 플랫폼 서버 인스턴스는 워커별(`_SERVERS`) — 다른 워커의 Task 는 영속 행(`loader`·`owner_lookup`)으로 이어 본다.
- 테스트 커버리지 흔적: `test/test_a2a.py:195-215`(등록 검증·pending·미신뢰 거부), `test/test_a2a.py:247-252`(Task 팀 간 격리·아티팩트), `test/test_task_ownership.py:19-63`(C44 플랫폼·게이트웨이 Task 소유권 — 플랫폼은 `/a2a/platform/tpl-qa` 프로파일 경로만, L10), `test/test_caller_trust.py:38,90`(C40 agent·run 헤더), `test/test_policy_hardening.py:46`(C43 푸시 URL), `test/test_review_fixes.py:255-257`(C39-12 A2A 남의 secret_ref 403), `test/test_design_gaps.py:131-158`(framework·push_verify), `test/test_idor_matrix.py:63-64`(남의 Task 조회·취소), `test/test_ops.py:520`(oauth2 token_url PATCH), `test/test_a2a_rest.py`·`test_a2a_v10.py`·`test_a2a_grpc.py`
- 2차 변경 요약 / Changes since 1st review: (1) L27·L47-48 `_ctx` 가 클라이언트 헤더를 그대로 쓰지 않고 `policy.caller_context` 로 서버 판정(Epic 40, D17-①). (2) L30·L222 `push_url_problem` 을 플랫폼 서버 `push_check` 로 연결(Epic 43, D17-④). (3) L154-157 `_platform_owner` 추가, L176-225 토큰별 `principals` 사전 제거 + `owner`·`owner_lookup` 훅(Epic 44, D18-①). (4) L457 등록·L535-537 PATCH 에 `secrets.check_refs`·`check_destination`(Epic 39, C39-12). 근거: `test/impl/{39th,40th,43rd,44th}/scenario.md`.
- 교차 참조 주의: 이 파일은 스냅샷과 같다(sha1 `8d94700e…`, `snapshot-start.sha`). 다만 리뷰 도중(21:21~21:39 KST) 교차 참조 대상인 `app/services/{a2a_gateway,a2a_server,policy,secrets,mcp_gateway}.py`·`app/main.py`·`app/core/http.py` 가 동시에 수정되고 있었다(D19-④ 주석의 새 코드) — 아래 교차 인용 라인은 21:39 디스크 기준이며 이후 바뀔 수 있다.

## 블록 리뷰 / Block Walk (Level 2)

```python path=app/api/a2a.py lines=1-35
"""A2A 경로 API (FR-G08·A04·A05, FR-R00~R06 · SCR-G01~G07·H15·D10).

게이트웨이: POST /a2a/{agent_id} (JSON-RPC 0.3/1.0) · GET /a2a/{agent_id}/.well-known/agent-card.json(게이트웨이 경유 카드)
· POST /a2a/_skill/{skill}(스킬 기반 라우팅) · POST /a2a/push/{token}(푸시 수신)
플랫폼 A2A 서버(FR-A05): GET /.well-known/agent-card.json · POST /a2a/platform[/{profile_id}]
"""
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, authenticate_token, bearer, require
from app.core.config import APP_VERSION, settings
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.core.ids import new_id, slugify
from app.db import SessionLocal, get_db, utcnow
from app.models import A2AAgent, A2ACardCheck, A2ATask, AgentProfile, CatalogItem, CatalogVersion, Run
from app.services import a2a_proto as ap
from app.services import a2a_rest
from app.services import audit, policy, registry, secrets
from app.services.a2a_gateway import (RESERVED, A2AError, CallContext, apply_card, card_url_for,
                                      ensure_exchange_policy, fetch_card, gateway_card, load_agent, proxy,
                                      PushRejected, push_url_problem, receive_push, refresh_card, route_by_skill)
from app.services.a2a_server import A2AServer, TaskContext

router = APIRouter(tags=["a2a"])


```
**분석 / Analysis (Level 2)**
- L1-6: 경로 지도 — 게이트웨이(프록시·카드·스킬·푸시)와 플랫폼 A2A 서버.
- L19: `bearer`·`authenticate_token` 을 직접 쓴다 — `/a2a/*` 는 A2A 클라이언트(JSON-RPC 오류 형식 기대)라 FastAPI `Depends(require(...))` 의 일반 HTTP 오류 대신 직접 인증하는 경로가 섞여 있다.
- L23: `SessionLocal`(스레드풀 클로저 안에서 직접 여닫음)과 `get_db`(요청 단위 의존성)를 함께 쓴다 — async 핸들러에서는 앞의 방식, 동기 핸들러에서는 뒤의 방식이 원칙.
- L27 `policy`: Epic 40 에서 `caller_context` 를 위해 추가. L28-30: 게이트웨이 서비스의 변환·인증·카드·프록시·푸시 심볼(`push_url_problem` 은 Epic 43).

```python path=app/api/a2a.py lines=36-60
def _auth(request: Request) -> Principal:
    token = bearer(request)
    if not token:
        raise ApiError(401, "unauthorized", "인증이 필요합니다.")
    with SessionLocal() as db:
        p = authenticate_token(db, token)
    if not p.has("chat:use"):
        raise ApiError(403, "forbidden", "권한 부족: chat:use")
    return p


def _ctx(request: Request, p: Principal) -> CallContext:
    return CallContext(p, **policy.caller_context(None, p, request.headers))  # 신고 헤더는 서버가 판정 (D17-①)


async def _jsonrpc(request: Request) -> tuple:
    try:
        body = await request.json()
    except ValueError:
        raise A2AError(-32700, "Parse error", http_status=400) from None
    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        raise A2AError(-32600, "Invalid Request", http_status=400)
    return body.get("id"), body["method"], body.get("params") or {}


```
**분석 / Analysis (Level 2)**
- L36-44 `_auth`: Bearer 필수(없으면 401) → `authenticate_token`(무효 토큰이면 `ApiError`) → `chat:use`. 동기 DB 라 호출부가 `run_in_threadpool` 로 부른다(L299·L325·L345).
- L47-48 `_ctx`: 신고 헤더를 서버가 판정 — `X-AgentNexus-Agent` 는 기계·위임 주체만(`app/services/policy.py:21,71`), `X-AgentNexus-Run` 은 그 주체의 실행만(`policy.py:60-70`), 데이터 등급 하향은 `data:declare` 권한(`policy.py:49`). `session_id` 는 원값(`policy.py:73`).
- L51-58 `_jsonrpc`: 본문 JSON 파싱(`ValueError` → -32700), 객체·`method` 문자열 확인(-32600).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L47-48 `_ctx` 는 동기 함수인데 async 핸들러가 이벤트 루프에서 바로 부른다(L304·L332·L349·L610·L696). `caller_context` 는 run 헤더가 있으면 `SessionLocal` 로 Run 을 읽고(`policy.py:61-70`), 5초마다 기본 데이터 등급 설정을 읽으며(`policy.py:31-34`), 하향 신고가 거부되면 감사 행을 쓴다(`policy.py:51-53`) — 그동안 그 워커의 모든 요청이 멈춘다(PostgreSQL 지연·SQLite 잠금 시 체감). 같은 문제: L265-267·L273-277 노출 확인, L193-198 실행기 내부 조회. 최소 수정: `ctx = await run_in_threadpool(_ctx, request, p)`.
- [위험:낮음] L58 `params` 형식을 확인하지 않는다 — JSON-RPC 의 위치 매개변수(list)나 문자열이 오면 `a2a_gateway._task_id_of` 등의 `params.get(...)`(`app/services/a2a_gateway.py:622-632`)에서 `AttributeError` → 500(JSON-RPC 오류 응답이 아님). 최소 수정: `if not isinstance(params, dict): raise A2AError(-32602, "Invalid params", http_status=400)`.
- (참고) 기본 역할 전부가 `data:declare` 를 가진다(`app/core/auth.py:20` `_USER`) — 하향 신고 차단은 관리자가 RBAC 에서 회수해야 동작한다(40th 결정 경위, 설계).

```python path=app/api/a2a.py lines=61-80
# ───────────────────────── 플랫폼 A2A 서버 (FR-A05) ─────────────────────────
# 실행기: (TaskContext, profile_id, Principal) -> None. 오케스트레이터(Epic 8)가 교체 등록한다.
PlatformExecutor = Callable[[TaskContext, str, Principal], Awaitable[None]]


async def _echo_executor(ctx: TaskContext, profile_id: str, p: Principal) -> None:
    text = " ".join(x.get("text", "") for x in ctx.message.get("parts", []) if x.get("kind") == "text")
    await ctx.artifact("echo", [{"kind": "text", "text": text}])
    await ctx.update("completed", f"[{profile_id}] {text}")


PLATFORM: dict = {"executor": _echo_executor}
_SERVERS: dict[str, A2AServer] = {}


def _exposed(db: Session) -> list[AgentProfile]:
    return list(db.scalars(select(AgentProfile).where(AgentProfile.a2a_exposed.is_(True),
                                                      AgentProfile.status == "published").order_by(AgentProfile.name)))


```
**분석 / Analysis (Level 2)**
- L63 `PlatformExecutor`: 실행기 시그니처 타입 별칭. L66-69 기본 echo 실행기 — host 구성요소가 꺼져 있으면 플랫폼 경로가 echo 로 응답한다.
- L72 `PLATFORM` dict: 실행기 교체점(가변 레지스트리). L203 이 **호출 시점에** `PLATFORM["executor"]` 를 읽으므로 import 순서와 무관하게 `app/main.py:182` 의 교체가 반영된다(흔한 실수: `from … import executor` 로 값을 복사하면 교체가 안 보인다).
- L73 `_SERVERS`: 워커(프로세스)별 A2AServer 캐시 — 워커 간 공유가 아니다.
- L76-78 `_exposed`: `a2a_exposed ∧ status == published` 프로파일.

```python path=app/api/a2a.py lines=81-107
def platform_card(profile_id: str | None = None, base_url: str | None = None) -> dict:
    base = (base_url or settings.public_base_url).rstrip("/")
    with SessionLocal() as db:
        profiles = _exposed(db)
        if profile_id:
            profiles = [pr for pr in profiles if pr.id == profile_id]
    skills = []
    for pr in profiles:  # 승인된 Agent Card 편집본(D10)이 있으면 그 스킬·설명, 없으면 프로파일 1개 = 스킬 1개
        own = (pr.a2a_card or {}).get("skills")
        skills += [{"id": sk.get("id"), "name": sk.get("name") or sk.get("id"), "description": sk.get("description") or pr.name,
                    "examples": sk.get("examples") or [], "tags": sk.get("tags") or ["agentnexus"]} for sk in own] if own else \
            [{"id": pr.id, "name": pr.name, "description": pr.description or pr.name, "tags": ["agentnexus", "orchestration"]}]
    skills = skills or [
        {"id": "none", "name": "(노출된 프로파일 없음)", "description": "콘솔에서 프로파일을 A2A 로 노출하세요.", "tags": []}]
    url = f"{base}/a2a/platform" + (f"/{profile_id}" if profile_id else "")
    own_card = (profiles[0].a2a_card or {}) if profile_id and profiles else {}
    caps = own_card.get("capabilities") or {}
    card = ap.build_card(name=own_card.get("name") or ("AgentNexus" + (f" — {profiles[0].name}" if profile_id and profiles else "")),
                         description=own_card.get("description") or
                         "AgentNexus 로컬 오케스트레이션(Planner→Retriever·Summarize·Executor)을 A2A 로 노출",
                         url=url, version=own_card.get("version") or APP_VERSION, skills=skills,
                         streaming=caps.get("streaming", True), push=caps.get("pushNotifications", True),
                         provider={"organization": "AgentNexus", "url": base}, rest_url=f"{url}/rest",
                         grpc_url=settings.a2a_grpc_public_url or None, grpc_tenant="platform" + (f"/{profile_id}" if profile_id else ""))
    return card


```
**분석 / Analysis (Level 2)**
- L82: `base_url`(요청 Host) 또는 설정 `public_base_url`.
- L88-94: 노출 프로파일마다 승인된 Agent Card 편집본(D10)의 스킬, 없으면 "프로파일 1개 = 스킬 1개". 노출 0개면 안내용 가짜 스킬.
- L95-104: 이중 바인딩 카드(JSON-RPC url · REST `rest_url` · gRPC tenant). 프로파일 카드면 편집본의 이름·설명·버전·capabilities 를 우선.
- `with SessionLocal()` 블록 밖에서 `profiles` 의 ORM 속성(`a2a_card`·`name`)을 읽는다 — 세션이 닫힌 뒤 접근이지만 `_exposed` 가 이미 로드한 컬럼이라 지연 로딩이 일어나지 않는다(관계 속성을 읽었다면 `DetachedInstanceError` 가 나는 흔한 실수).

```python path=app/api/a2a.py lines=108-128
async def _persist(task: dict, event: dict, profile_id: str, sub: str | None) -> None:
    def _do():
        with SessionLocal() as db:
            row = db.get(A2ATask, f"platform:{task['id']}")
            if row is None:
                row = A2ATask(id=f"platform:{task['id']}", remote_task_id=task["id"], agent_id=f"platform:{profile_id}",
                              direction="inbound", context_id=task.get("contextId"), principal_sub=sub, history=[],
                              artifacts=[])
                db.add(row)
            row.state, row.history, row.artifacts = task["status"]["state"], task.get("history") or [], \
                task.get("artifacts") or []
            row.run_id = (task.get("metadata") or {}).get("anx_run_id") or row.run_id
            row.status_message = ap.text_of({"kind": "task", "status": task["status"]})[:2000] or row.status_message
            row.updated_at = utcnow()
            db.commit()
    await run_in_threadpool(_do)


_RUN_TO_TASK = {"succeeded": "completed", "partial": "completed", "failed": "failed", "canceled": "canceled"}


```
**분석 / Analysis (Level 2)**
- L108-123 `_persist`: 플랫폼 수신 Task 를 `platform:{id}` 행으로 영속(방향 inbound). **주체(`principal_sub`)는 처음 만들 때만** 기록하고 이후에는 상태·이력·아티팩트·run_id·상태 메시지만 갱신 — 소유권이 나중에 바뀌지 않게.
- `A2AServer._emit` 이 상태 변경마다 `on_change` 를 await 한 뒤 구독자에게 알린다(`app/services/a2a_server.py:101-105`) — "저장된 상태만 통지" 를 노린 순서.
- L126 `_RUN_TO_TASK`: 실행 상태 → A2A Task 상태(partial 도 completed).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L117-118 이벤트마다 이력·아티팩트 JSON 전체를 다시 쓴다(긴 Task 는 쓰기량이 이벤트 수 × 크기). 같은 Task 에 두 코루틴이 동시에 상태를 바꾸면(실행기 갱신 + 클라이언트 취소) 두 스레드가 같은 행을 쓰며, 첫 영속이 겹치면 PK 중복으로 한쪽이 실패할 수 있다(가능성). 그 실패는 `a2a_server.py:102-103` 의 `contextlib.suppress(Exception)` 에 조용히 삼켜진다 — L205-210 참조.

```python path=app/api/a2a.py lines=129-153
def _load_platform(tid: str) -> dict | None:
    """영속 Task → A2A Task. 실행기가 다른 워커·재시작 전에 있었어도, 실행(run)이 끝났으면 그 결과로 Task 를 완결한다."""
    with SessionLocal() as db:
        row = db.get(A2ATask, f"platform:{tid}")
        if row is None:
            return None
        if row.state not in ap.TERMINAL and row.run_id and (r := db.get(Run, row.run_id)) is not None \
                and r.status in _RUN_TO_TASK:
            row.state = _RUN_TO_TASK[r.status]
            row.status_message = f"실행 {r.status} (run {r.id})"
            if row.state == "completed" and not any(a.get("name") == "answer.md" for a in row.artifacts or []):
                row.artifacts = [*(row.artifacts or []), {"artifactId": uuid.uuid4().hex, "name": "answer.md",
                                                           "parts": [{"kind": "text", "text": r.result or ""}],
                                                           "description": "AgentNexus 오케스트레이션 답변"}]
            row.updated_at = utcnow()
            db.commit()
        status = {"state": row.state, "timestamp": (row.updated_at or row.created_at).isoformat()}
        if row.status_message:
            status["message"] = {"kind": "message", "messageId": f"{tid}-status", "role": "agent", "taskId": tid,
                                 "contextId": row.context_id or tid, "parts": [{"kind": "text", "text": row.status_message}]}
        return {"kind": "task", "id": tid, "contextId": row.context_id or tid, "status": status,
                "artifacts": row.artifacts or [], "history": row.history or [],
                **({"metadata": {"anx_run_id": row.run_id}} if row.run_id else {})}


```
**분석 / Analysis (Level 2)**
- L129-151 `_load_platform`: 영속 Task → A2A Task. 이 워커 메모리에 없는 Task(다른 워커·재시작 전)라도 연결된 실행(run)이 끝났으면 그 결과로 상태를 완결하고 `answer.md` 아티팩트를 한 번만 덧붙인다(L139 중복 방지 검사).
- 조회(GET) 경로에서 쓰기(L137-144)가 일어난다 — 읽기 요청이 상태를 진행시키는 "지연 완결" 패턴. 동시 조회 두 개가 모두 쓰면 마지막 쓰기가 이기며 `artifactId`(uuid)가 바뀔 수 있다(영향 낮음).

```python path=app/api/a2a.py lines=154-175
def _platform_owner(tid: str) -> str | None:
    with SessionLocal() as db:
        row = db.get(A2ATask, f"platform:{tid}")
        return row.principal_sub if row else None


def _cancel_platform(tid: str) -> dict | None:
    from app.services import orchestrator
    with SessionLocal() as db:
        row = db.get(A2ATask, f"platform:{tid}")
        if row is None:
            return None
        run_id = row.run_id
    if run_id:
        orchestrator.cancel(run_id)
    with SessionLocal() as db:
        row = db.get(A2ATask, f"platform:{tid}")
        row.state, row.status_message, row.updated_at = "canceled", "다른 인스턴스에서 취소", utcnow()
        db.commit()
    return _load_platform(tid)


```
**분석 / Analysis (Level 2)**
- L154-157 `_platform_owner`(Epic 44): 영속 행의 주체 — 행이 없으면 None 이고, A2AServer 는 None 을 "남의 것" 으로 보고 TaskNotFound 를 낸다(`app/services/a2a_server.py:319`, fail-closed).
- L160-173 `_cancel_platform`: 실행 취소 → 행을 canceled 로 → 다시 로드. 두 번째 세션(L169-172)은 첫 조회와 별개 트랜잭션이다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L169-172 두 번째 조회에서 행이 사라졌으면 `row.state` 에서 `AttributeError` → 500. 또 실행이 막 끝난 순간이면 `succeeded` 실행의 Task 를 `canceled` 로 덮는다(L135 는 종료 상태면 다시 완결하지 않음). 최소 수정: 조건부 UPDATE(`WHERE state NOT IN TERMINAL`).

```python path=app/api/a2a.py lines=176-204
def _platform_server(profile_id: str | None) -> A2AServer:
    key = profile_id or "_default"
    if key in _SERVERS:
        return _SERVERS[key]
    async def authenticate(request: Request):
        token = bearer(request)
        if not token:
            return None
        try:
            p = await run_in_threadpool(_principal_of, token)
        except ApiError:
            return None
        return {"principal": p}

    async def executor(ctx: TaskContext) -> None:
        pid = profile_id or (ctx.message.get("metadata") or {}).get("skill")
        if pid and not profile_id:  # 스킬 id → 그 스킬을 선언한 프로파일 (D10 ⑤ 스킬 ↔ 그래프 진입점)
            with SessionLocal() as db:
                pid = next((pr.id for pr in _exposed(db) if pr.id == pid or any(
                    sk.get("id") == pid for sk in (pr.a2a_card or {}).get("skills") or [])), pid)
        if not pid:
            with SessionLocal() as db:
                exposed = _exposed(db)
            if not exposed:
                await ctx.update("rejected", "A2A 로 노출된 에이전트 프로파일이 없습니다.")
                return
            pid = exposed[0].id
        await PLATFORM["executor"](ctx, pid, ctx.auth["principal"])

```
**분석 / Analysis (Level 2)**
- L177-179: 동기 함수 안의 "확인 후 생성" — await 이 없어 단일 이벤트 루프에서 경쟁이 없다(이 함수를 async 로 바꾸고 중간에 await 을 넣으면 같은 키로 서버가 둘 생기는 흔한 실수).
- L180-188 `authenticate`: 토큰 → 주체(스레드풀), `ApiError` 면 None → A2AServer 가 401(`app/services/a2a_server.py:226-228`). Epic 44 전의 토큰별 `principals` 사전(1차 [낮음] 누수)은 제거됐다.
- L190-203 `executor`: 프로파일 경로면 그 프로파일, 기본 경로면 메시지 `metadata.skill` → 그 스킬을 선언한 노출 프로파일, 없으면 첫 노출 프로파일.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L194-195 `next(…, pid)` 의 기본값이 **클라이언트가 보낸 skill 문자열 그대로**다 — 노출 프로파일·스킬과 맞지 않으면 그 문자열을 프로파일 id 로 `PLATFORM["executor"]` 에 넘기고, `orchestrator.start_run` 은 존재만 확인한다(`app/services/orchestrator.py:268-291`). 따라서 `/a2a/platform` 호출자가 `metadata.skill = "<노출 안 된·미게시 프로파일 id>"` 를 보내면 A2A 노출(a2a_exposed·published) 경계를 건너 실행된다. 채팅 API 도 존재만 확인하므로(`app/api/chat.py:290-293`) 같은 주체에게 새 권한을 주지는 않지만, "콘솔에서 A2A 로 노출한 프로파일만" 이라는 D10 의도와 어긋난다. 최소 수정: 기본값을 `None` 으로 두고 매칭 실패면 `ctx.update("rejected", …)`. 시험 공백: C44 는 프로파일 경로만 본다.

```python path=app/api/a2a.py lines=205-235
    async def on_change(task: dict, event: dict) -> None:
        sub = None
        for c in server.ctxs.values():
            if c.task is task:
                sub = c.auth["principal"].sub
        await _persist(task, event, profile_id or "default", sub)

    async def loader(tid: str) -> dict | None:
        return await run_in_threadpool(_load_platform, tid)

    async def remote_cancel(tid: str) -> dict | None:
        return await run_in_threadpool(_cancel_platform, tid)

    async def owner_lookup(tid: str) -> str | None:
        return await run_in_threadpool(_platform_owner, tid)

    server = A2AServer(lambda: platform_card(profile_id), executor, authenticate=authenticate, on_change=on_change,
                       blocking_timeout=60, loader=loader, remote_cancel=remote_cancel, push_check=push_url_problem,
                       owner=lambda auth: auth["principal"].sub, owner_lookup=owner_lookup)
    _SERVERS[key] = server
    return server


def _principal_of(token: str) -> Principal:
    with SessionLocal() as db:
        p = authenticate_token(db, token)
    if not p.has("chat:use"):
        raise ApiError(403, "forbidden", "권한 부족")
    return p


```
**분석 / Analysis (Level 2)**
- L205-210 `on_change`: 이 Task 의 실행 문맥을 찾아 주체를 얻는다(문맥 수 ≤ `MAX_TASKS` 1000, O(n) 순회 — 1차 지적, 수용). 종료 이벤트에서 문맥이 정리되기 전에 호출되므로(`a2a_server.py:101-114`) 주체를 찾을 수 있다.
- L221-223: `push_check=push_url_problem`(운영에서 사설·루프백 푸시 URL 거부, Epic 43), `owner`·`owner_lookup`(Epic 44 — list 는 내 Task 만, get·cancel·resubscribe·푸시·이어 보내기는 남의 Task 면 -32001), `blocking_timeout=60`.
- L221 `lambda: platform_card(profile_id)`: 카드 작업은 Host 헤더가 아니라 설정 `public_base_url` 기준.
- L228-233 `_principal_of`: `_auth` 와 같은 검사(메시지만 다름) — 두 경로를 함께 고쳐야 하는 중복.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L205-210·L108-123 영속 실패가 보이지 않는다 — `A2AServer._emit` 이 `on_change` 를 `contextlib.suppress(Exception)` 로 감싸(`app/services/a2a_server.py:102-103`) DB 잠금·PK 충돌·연결 끊김이 로그 없이 사라진다. 첫 영속이 실패하면 행이 없어 다른 워커에서 주인 자신도 TaskNotFound(`owner_lookup` None → fail-closed)를 받고, 원인 추적 단서가 없다. 최소 수정: `_persist` 안에서 예외를 잡아 `logs.event("warning", …)` 후 재발생, 또는 `_emit` 의 suppress 를 로그로 교체.

```python path=app/api/a2a.py lines=236-253
@router.get("/.well-known/agent-card.json")
def platform_well_known(request: Request):
    return platform_card(base_url=str(request.base_url))


@router.post("/a2a/platform")
async def platform_rpc(request: Request):
    return await _platform_server(None).handle(request)


@router.get("/a2a/platform/{profile_id}/.well-known/agent-card.json")
def platform_profile_card(profile_id: str, request: Request):
    with SessionLocal() as db:
        if profile_id not in {pr.id for pr in _exposed(db)}:
            raise not_found("exposed profile")
    return platform_card(profile_id, base_url=str(request.base_url))


```
**분석 / Analysis (Level 2)**
- L236-238: 플랫폼 well-known 카드 — 인증 없음(A2A 사양상 공개). 동기 `def` 라 FastAPI 가 스레드풀에서 실행(내부 DB OK).
- L241-243: 기본 플랫폼 JSON-RPC.
- L246-251: 프로파일 카드 — 노출 프로파일이 아니면 404.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L238·L251 `str(request.base_url)` 은 요청 `Host` 헤더에서 온다(1차 지적 유지) — 조작된 Host 로 받은 카드의 `url`·`provider.url` 이 공격자 도메인을 가리키고, 앞단 캐시가 이 응답을 저장하면 다른 클라이언트가 그 주소로 메시지·토큰을 보낼 수 있다(캐시 오염 — 가능성). 최소 수정: `settings.public_base_url` 고정 또는 신뢰 Host 허용 목록(`TrustedHostMiddleware`).

```python path=app/api/a2a.py lines=254-280
_REST_METHODS = ["GET", "POST", "DELETE"]


@router.api_route("/a2a/platform/rest/{path:path}", methods=_REST_METHODS)
async def platform_rest(path: str, request: Request):
    """플랫폼 A2A 서버 HTTP+JSON 바인딩 (D16-①) — JSON-RPC 와 같은 실행기."""
    return await _platform_server(None).rest(request)


@router.api_route("/a2a/platform/{profile_id}/rest/{path:path}", methods=_REST_METHODS)
async def platform_profile_rest(profile_id: str, path: str, request: Request):
    with SessionLocal() as db:
        if profile_id not in {pr.id for pr in _exposed(db)}:
            return a2a_rest.error_response(ap.TASK_NOT_FOUND, "노출되지 않은 프로파일", http_status=404)
    return await _platform_server(profile_id).rest(request)


@router.post("/a2a/platform/{profile_id}")
async def platform_profile_rpc(profile_id: str, request: Request):
    with SessionLocal() as db:
        if profile_id not in {pr.id for pr in _exposed(db)}:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": ap.TASK_NOT_FOUND,
                                                                         "message": "노출되지 않은 프로파일"}},
                                status_code=404)
    return await _platform_server(profile_id).handle(request)


```
**분석 / Analysis (Level 2)**
- L254 `_REST_METHODS`: HTTP+JSON 바인딩이 쓰는 메서드.
- L257-260: 기본 플랫폼 REST — 경로 `/a2a/platform/rest/{path:path}` 가 L271 `/a2a/platform/{profile_id}` 보다 먼저 등록돼 `rest` 가 프로파일 id 로 해석되지 않는다(Starlette 는 등록 순서대로 첫 매칭).
- L263-268·L271-278: 노출 확인 후 프로파일 서버로. 노출되지 않은 프로파일은 REST 404 / JSON-RPC `TASK_NOT_FOUND` 코드로 존재를 숨긴다.
- (L265-267·L273-277 의 이벤트 루프 동기 DB 는 L47-48 항목 참조.)

```python path=app/api/a2a.py lines=281-308
# ───────────────────────── 게이트웨이 A2A 프록시 (FR-G08) ─────────────────────────
@router.post("/a2a/push/{token}")
async def push_webhook(token: str, request: Request):
    raw = await request.body()
    try:
        body = json.loads(raw)
    except ValueError:
        return Response(status_code=400)
    try:
        ok = await receive_push(token, body, request.headers.get("x-a2a-notification-token"),
                                request.headers.get("authorization"), raw)
    except PushRejected as exc:  # 서명 검증 실패 (SCR-G07 ⑧)
        return JSONResponse({"error": "push_signature_invalid", "message": str(exc)}, status_code=401)
    return Response(status_code=200 if ok else 404)


@router.post("/a2a/_skill/{skill}")
async def skill_rpc(skill: str, request: Request):
    p = await run_in_threadpool(_auth, request)
    rid = None
    try:
        rid, method, params = await _jsonrpc(request)
        agent_id = await run_in_threadpool(route_by_skill, skill, p)
        return await proxy(agent_id, _ctx(request, p), rid, method, params)
    except A2AError as exc:
        return exc.response(rid)


```
**분석 / Analysis (Level 2)**
- L282-294 `push_webhook`: 원격 에이전트 → 게이트웨이 푸시. 인증 미들웨어가 아니라 URL 의 무작위 토큰(+ `X-A2A-Notification-Token` 일치) + (설정 시) JWKS 서명으로 인증. `json.loads(bytes)` 는 깨진 UTF-8 도 `ValueError` 계열(`UnicodeDecodeError`)로 던져 400 이 된다. 서명 실패는 401 + 사유(`PushRejected` 메시지는 예외 타입 이름 수준, `a2a_gateway.py:782-801`).
- `receive_push` 는 이제 본문 Task id 가 토큰의 Task 와 다르면 거부(`a2a_gateway.py:828-833`, Epic 39)하고, 차단·비활성 에이전트의 푸시를 받지 않으며 소독을 거친다(`a2a_gateway.py:834-839`) — 1차 "토큰으로 다른 Task 조작" 은 해결.
- L297-306 `skill_rpc`: 인증(스레드풀) → 스킬 → 신뢰 등급 우선 에이전트(`a2a_gateway.route_by_skill`, 라우팅 결정 행 기록) → 같은 프록시 경로(정책·한도·가드).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L284-291 본문이 JSON 객체인지 확인하지 않는다 — 배열·문자열이면 `receive_push` 안의 `ap.result_from`/`event_from` 에서 예외 → 500(가능성). 또 서명(`push_verify`)은 에이전트 설정에 있을 때만 검사하고(`a2a_gateway.py:820`), 그 외에는 URL 경로의 토큰이 유일한 자격 증명이라 앞단 접근 로그·프록시 로그에 그대로 남는다(1차 지적 유지). 최소 수정: `if not isinstance(body, dict): return Response(status_code=400)`, 토큰은 헤더로만 받거나 로그 마스킹.
- [위험:낮음] L299 `_auth` 가 `try` 밖이라 인증 실패는 JSON-RPC 오류 형식이 아닌 FastAPI 일반 오류(`{"code","detail"}`)로 나간다(L345 도 같음) — A2A 클라이언트 SDK 가 파싱하지 못할 수 있다.

```python path=app/api/a2a.py lines=309-320
@router.get("/a2a/{agent_id}/.well-known/agent-card.json")
async def proxied_card(agent_id: str, request: Request):
    try:
        agent = await run_in_threadpool(load_agent, agent_id)
        if not agent.card:
            await refresh_card(agent_id)
            agent = await run_in_threadpool(load_agent, agent_id)
    except A2AError as exc:
        raise ApiError(exc.http_status if exc.http_status >= 400 else 502, "agent_card_unavailable", exc.message) from exc
    return gateway_card(agent, str(request.base_url))


```
**분석 / Analysis (Level 2)**
- L309-318 게이트웨이 경유 카드: 등록된 원격 에이전트의 카드를 게이트웨이 주소·AgentNexus 보안 스킴으로 바꿔 돌려준다. 카드가 없으면 그 자리에서 조회.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L309-318 **인증이 없고 상태를 보지 않는다** — `load_agent` 는 존재만 확인하므로(`app/services/a2a_gateway.py:442-447`) pending(승인 전)·disabled·blocked 에이전트의 이름·설명·스킬이 비인증 호출자에게 공개되고, 카드가 없는 에이전트면 비인증 요청이 원격 카드 조회(외부 HTTP)를 유발한다. 최소 수정: `status == "active" and trust_tier != "blocked"` 아니면 404.

```python path=app/api/a2a.py lines=321-353
@router.api_route("/a2a/{agent_id}/rest/{path:path}", methods=_REST_METHODS)
async def a2a_proxy_rest(agent_id: str, path: str, request: Request):
    """게이트웨이 A2A 프록시 HTTP+JSON 바인딩 (D16-①) — REST → 1.0 JSON-RPC 와 같은 정책·가드·감사·변환 → REST 응답."""
    try:
        p = await run_in_threadpool(_auth, request)
        raw = await request.body() if request.method not in ("GET", "DELETE") else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            raise a2a_rest.RestError(-32700, "Parse error", http_status=400) from None
        method, params = a2a_rest.to_rpc(request.method, path, request.query_params, body)
        ctx = _ctx(request, p)
        # a2a-grpc 서비스가 위임한 요청은 기록상 GRPC (값은 라벨일 뿐 권한과 무관)
        ctx.binding = "GRPC" if request.headers.get("x-agentnexus-binding") == "GRPC" else "HTTP+JSON"
        return a2a_rest.from_rpc_response(await proxy(agent_id, ctx, "rest", method, params))
    except (a2a_rest.RestError, A2AError) as exc:
        return a2a_rest.error_response(exc.code, exc.message, exc.data, exc.http_status,
                                       headers={"Retry-After": str(exc.data["retry_after"])} if (exc.data or {}).get("retry_after") else None)
    except ApiError as exc:
        return a2a_rest.error_response(-32603, exc.detail or exc.code, {"code": exc.code}, exc.status, headers=exc.headers or None)


@router.post("/a2a/{agent_id}")
async def a2a_proxy(agent_id: str, request: Request):
    p = await run_in_threadpool(_auth, request)
    rid = None
    try:
        rid, method, params = await _jsonrpc(request)
        return await proxy(agent_id, _ctx(request, p), rid, method, params)
    except A2AError as exc:
        return exc.response(rid)


```
**분석 / Analysis (Level 2)**
- L321-340 REST 프록시: 인증 → (GET·DELETE 외) 본문 파싱 → `a2a_rest.to_rpc` 로 정준 작업 → 같은 `proxy` → REST 응답. 오류는 google.rpc.Status 형태로, 한도 초과면 `Retry-After`.
- L334: `X-AgentNexus-Binding: GRPC` 면 기록상 바인딩을 GRPC 로(a2a-grpc 서비스 위임 표시).
- L343-351 JSON-RPC 프록시.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L334 바인딩 라벨을 **어떤 클라이언트든** 헤더로 바꿀 수 있다 — 권한과는 무관하지만 사용량·감사·지표의 바인딩 값이 조작된다(무결성). 최소 수정: a2a-grpc 서비스의 워크로드 주체(`p.kind == "workload"` + 이름)일 때만 수용.
- [위험:낮음] L326 본문 크기 상한은 ASGI 서버·앞단 프록시에 의존(1차 지적 유지).

```python path=app/api/a2a.py lines=354-389
# ───────────────────────── 원격 에이전트 관리 (SCR-G01~G04) ─────────────────────────
_FRAMEWORKS = (("google-adk", "ADK"), ("adk", "ADK"), ("langgraph", "LangGraph"), ("crewai", "CrewAI"), ("autogen", "AutoGen"),
               ("semantic-kernel", "Semantic Kernel"), ("beeai", "BeeAI"), ("pydantic-ai", "PydanticAI"), ("a2a-sdk", "a2a-sdk"),
               ("agentnexus", "AgentNexus"))


def framework_hint(card: dict | None) -> str | None:
    """Agent Card 의 provider·확장 URI·문서 주소에서 프레임워크 추정 (SCR-G01 — 표준 필드가 없어 힌트, 운영자 지정이 우선)."""
    if not card:
        return None
    hay = json.dumps({k: card.get(k) for k in ("provider", "documentationUrl", "capabilities", "url", "name")},
                     ensure_ascii=False).lower()
    return next((label for key, label in _FRAMEWORKS if key in hay), None)


def agent_out(a: A2AAgent, detail: bool = False) -> dict:
    info = ap.card_info(a.card) if a.card else {}
    out = {"id": a.id, "name": a.name, "url": a.url, "card_url": a.card_url, "version": a.version,
           "trust_tier": a.trust_tier, "status": a.status, "skills": a.skills, "card_changed": a.card_changed,
           "last_fetched_at": a.last_fetched_at, "auth": {k: v for k, v in (a.auth or {}).items() if k != "secret_ref"},
           "credential_configured": bool((a.auth or {}).get("secret_ref")), "gateway_path": f"/a2a/{a.id}",
           "protocol_versions": info.get("protocol_versions", []), "bindings": info.get("bindings", []),
           "streaming": info.get("streaming"),
           "push": info.get("push"), "catalog_item_id": a.catalog_item_id, "owner_team": a.owner_team,
           "framework": a.framework or framework_hint(a.card), "framework_source": "operator" if a.framework else
           ("card" if framework_hint(a.card) else None), "push_verify": bool(((a.auth or {}).get("push_verify") or {}).get("jwks_url")),
           "cache": {"etag": a.etag, "ttl_s": a.cache_ttl_s, "last_fetched_at": a.last_fetched_at,
                     "next_check_at": (a.last_fetched_at + timedelta(seconds=a.cache_ttl_s or 3600))
                     if a.last_fetched_at else None},
           "index": sorted({t for sk in a.skills or [] for t in (sk.get("tags") or [])} | {sk.get("id") for sk in a.skills or []}
                           | set((a.card or {}).get("securitySchemes") or {}))}
    if detail:
        out["card"], out["card_hash"] = a.card, a.card_hash
    return out


```
**분석 / Analysis (Level 2)**
- L355-366 `framework_hint`: 표준 필드가 없어 카드 일부를 JSON 문자열로 만들어 키워드 검색 — 운영자 지정(`framework`)이 우선.
- L369-387 `agent_out`: `auth` 에서 `secret_ref` 만 빼고 내보내며 자격 설정 여부는 불리언(`credential_configured`). 캐시(ETag·TTL·다음 점검), 검색 인덱스(태그·스킬 id·보안 스킴 이름).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L373 `auth` 는 자유 형식 dict(L394·L518)인데 응답에서 `secret_ref` **하나만** 뺀다 — 사용자가 실수로 `client_secret`·`token`·`password` 같은 키에 평문을 넣으면 그대로 저장되고 `a2a:read`(developer·security) 전원에게 응답되며 PATCH 감사에도 남는다(L552). 게이트웨이는 이런 인라인 값을 쓰지 않으므로(`a2a_gateway.py:246-259` 는 `secret_ref` 만 읽음) 거부해도 기능 손실이 없다. 최소 수정: 허용 키 스키마(`scheme`·`header`·`token_url`·`client_id`·`scopes`·`token_exchange`·`tls`·`push_verify`)로 입력·출력 제한.
- L378-379 `framework_hint` 를 두 번 계산(사소).

```python path=app/api/a2a.py lines=390-426
class AgentIn(BaseModel):
    url: str = Field(description="에이전트 기준 URL 또는 agent-card.json URL")
    id: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    trust_tier: str = Field("untrusted", pattern="^(verified|trusted|untrusted|blocked)$")
    auth: dict = {}
    secret: str | None = Field(None, repr=False)
    owner_team: str | None = None
    dry_run: bool = False  # 조회·검증만 (SCR-G02 ②~⑥) — 등록하지 않음


def create_agent_row(db: Session, card: dict, card_url: str, actor: str, *, agent_id: str | None = None,
                     trust_tier: str = "untrusted", auth: dict | None = None, catalog_item: CatalogItem | None = None
                     ) -> A2AAgent:
    info = ap.card_info(card)
    aid = agent_id or slugify(card.get("name", "agent"))[:60]
    if aid in RESERVED:
        raise bad_request(f"예약된 id 입니다: {aid}")
    if db.get(A2AAgent, aid):
        raise conflict(f"이미 등록된 에이전트 id: {aid}")
    if catalog_item is None:
        catalog_item = registry.find_item(db, "a2a_agent", aid)
    if catalog_item is None:
        catalog_item = CatalogItem(id=new_id("cat"), type="a2a_agent", name=aid, display_name=card.get("name"),
                                   description=card.get("description"), source="manual", status="active",
                                   version=str(card.get("version")), item_metadata=card, owner=actor, ref_id=aid,
                                   tags=sorted({t for s in info["skills"] for t in (s.get("tags") or [])})[:20])
        db.add(catalog_item)
        db.flush()
    a = A2AAgent(id=aid, catalog_item_id=catalog_item.id, name=card.get("name", aid), url=info["url"],
                 card_url=card_url, trust_tier=trust_tier, auth=auth or {})
    db.add(a)
    apply_card(db, a, card)
    catalog_item.status, catalog_item.ref_id = "active", aid
    ensure_exchange_policy(db, aid)
    return a


```
**분석 / Analysis (Level 2)**
- L390-397 `AgentIn`: id 패턴(소문자·숫자·하이픈 2~63자), 신뢰 등급 열거, 시크릿 `repr=False`, `dry_run`.
- L400-424 `create_agent_row`: id 결정(지정 또는 카드 이름 slug) → 예약어(`RESERVED` — `platform`·`_skill`·`push` 등 라우트 충돌 방지) → 중복 → 카탈로그 항목 찾기/만들기(`flush` 로 id 확정) → 에이전트 행 → 카드 적용 → 토큰 교환 정책(`a2a:{id}` audience).
- 커밋하지 않는다 — 호출자(L487, 카탈로그 핸들러는 `registry.register`)가 한 트랜잭션으로 커밋.
- L407-408 존재 확인 후 INSERT 는 동시 등록 시 PK 충돌 → 500(빈도 낮음).

```python path=app/api/a2a.py lines=427-451
def card_review(card: dict, card_url: str, fetch_ms: float | None = None) -> dict:
    """등록 전 카드 점검 (SCR-G02 ⑤⑥) — 스키마·필수·url 도메인·TLS·capabilities·skills·securitySchemes·서명."""
    from urllib.parse import urlparse
    info = ap.card_info(card)
    errs = ap.validate_card(card)
    host = lambda u: (urlparse(u).hostname or "")  # noqa: E731
    items = [{"key": "schema", "ok": not errs, "detail": "; ".join(errs) or "A2A Agent Card 스키마 ✓"},
             {"key": "url_domain", "ok": host(info["url"]) == host(card_url) or not info["url"],
              "detail": f"카드 url {info['url'] or '없음'} · 조회 {card_url}"},
             {"key": "tls", "ok": card_url.startswith("https://"), "detail": "HTTPS" if card_url.startswith("https://") else "HTTP — 운영은 HTTPS 권장", "warn": True},
             {"key": "capabilities", "ok": True, "detail": f"streaming {'✓' if info['streaming'] else '✗'} · push {'✓' if info['push'] else '✗'}"},
             {"key": "skills", "ok": bool(info["skills"]), "detail": f"{len(info['skills'])}개"},
             {"key": "security_schemes", "ok": True, "detail": ", ".join(card.get("securitySchemes") or {}) or "선언 없음"},
             {"key": "signature", "ok": bool(card.get("signatures")), "warn": True,
              "detail": "JWS 서명 ✓" if card.get("signatures") else "서명 없음 — partner 이상 등급은 사유 필요"}]
    suggest = "partner" if card.get("signatures") and card_url.startswith("https://") else "community"
    return {"checks": items, "ok": all(i["ok"] or i.get("warn") for i in items), "suggested_trust": suggest,
            "fetch_ms": fetch_ms, "card": card}


@router.get("/api/v1/a2a/agents")
def list_agents(_: Principal = Depends(require("a2a:read")), db: Session = Depends(get_db)):
    return {"items": [agent_out(a) for a in db.scalars(select(A2AAgent).order_by(A2AAgent.id))]}


```
**분석 / Analysis (Level 2)**
- L427-444 `card_review`: 등록 전 점검표(스키마·url 도메인·TLS·capabilities·스킬·보안 스킴·서명)와 권장 신뢰. `warn: True` 항목은 실패해도 전체 `ok` 를 떨어뜨리지 않는다.
- L447-449 목록(`a2a:read`), 페이지 없음.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L434-435 `url_domain` 은 카드의 대표 url(`info["url"]`) 하나만 조회 주소와 비교한다 — 실제 호출은 `url_v10`·`url_v03`·`url_rest`·`url_grpc` 중 클라이언트 버전에 맞는 것을 고르므로(`app/services/a2a_gateway.py:305-318`) 다른 인터페이스가 다른 호스트를 가리켜도 통과한다. 게다가 점검표는 **권고일 뿐** 등록은 `ok` 와 무관하게 진행된다(L465-495). L442 `suggested_trust` 는 심사 어휘(`partner|community`)이고 `AgentIn.trust_tier` 는 등급 어휘(`verified|trusted|…`)라 두 체계가 섞여 있다(`app/services/governance.py:60` 어휘 불일치와 같은 뿌리).

```python path=app/api/a2a.py lines=452-471
@router.post("/api/v1/a2a/agents", status_code=201)
async def register_agent(body: AgentIn, p: Principal = Depends(require("a2a:write"))):
    """원격 에이전트 등록 (SCR-G02) — Agent Card 조회(게이트웨이 egress)·검증 → 승인 권한이 없으면 등록 요청(pending,
    E05 승인 후 활성), 있으면 바로 활성. dry_run 이면 조회·점검 결과만."""
    import time as _t
    secrets.check_refs(p, body.auth, f"a2a/{body.id}/" if body.id else "\0")
    cu = card_url_for(body.url)
    t0 = _t.perf_counter()
    try:
        card = await fetch_card(cu, (body.auth or {}).get("tls"))
    except A2AError as exc:
        raise ApiError(exc.http_status if exc.http_status >= 400 else 502, "agent_card_invalid", exc.message,
                       extra=exc.data) from exc
    rv = card_review(card, cu, round((_t.perf_counter() - t0) * 1000, 1))
    if body.dry_run:
        return rv
    if body.trust_tier in ("verified", "trusted") and not p.has("policy:write"):
        raise ApiError(403, "forbidden", "신뢰 등급 지정은 보안 담당 권한이 필요합니다.")
    review_needed = not p.has("catalog:approve")

```
**분석 / Analysis (Level 2)**
- L457 `secrets.check_refs`(Epic 39): `auth.secret_ref`·`tls.ca_secret`·`tls.client_cert_secret` 가 자기 네임스페이스(`a2a/{id}/`) 밖이면 `gateway:write` 필요. id 를 안 주면 접두어가 `"\0"` 라 **모든** 참조가 남의 것으로 취급된다(안전한 기본값). 카드 조회 **전에** 검사해 TLS 시크릿이 조회에 쓰이기 전에 막는다.
- L458-465: 카드 URL 정규화 → 게이트웨이 egress 로 조회(시간 측정) → 점검표. `dry_run` 이면 여기서 끝.
- L468-469: 등급 상향(verified·trusted)은 `policy:write`(보안 담당) — 직무 분리.
- L470: 카탈로그 승인 권한이 없으면 등록 요청(pending, MSG-008).
**짚어볼 점 / Watch-outs**
- [위험:중간] L458-461 **사용자 지정 URL 조회(SSRF) 미해결**(1차 지적 유지) — `a2a:write`(developer 역할)만으로 게이트웨이가 임의 URL 을 GET 한다. egress 가드는 링크 로컬·IMDS 만 막고 사설망은 제품 기능으로 허용한다(`app/core/http.py:35-36`, 39th "사설망 SSRF — 결정 항목"). `fetch_card` 의 오류가 상태 코드·예외 타입을 그대로 돌려주므로(`app/services/a2a_gateway.py:93-96`) `http://db:5432/`·`http://valkey:6379/`·`http://api:8000/internal` 같은 내부 주소의 열림/닫힘·HTTP 상태를 응답으로 구분할 수 있고(포트 스캔 오라클), 카드 형식에 맞는 JSON 이면 `dry_run` 이 본문을 돌려준다(L466-467 `rv["card"]`). 또 `body.auth.tls.verify=false` 를 그대로 받아 TLS 검증을 끈 채 조회한다(`app/services/tls.py:83`). 최소 수정: 운영(local·test 밖)에서는 푸시와 같은 `internal_url` 거부(`a2a_gateway.push_url_problem` 재사용)를 기본으로, 사설망 에이전트는 `gateway:write` 또는 허용 목록으로.

```python path=app/api/a2a.py lines=472-497
    def _do():
        with SessionLocal() as db:
            auth = dict(body.auth)
            a = create_agent_row(db, card, cu, p.sub, agent_id=body.id, trust_tier=body.trust_tier, auth=auth)
            a.owner_team = body.owner_team or p.team
            if review_needed:  # 등록 요청 — 승인(E05) 전에는 호출 불가 (MSG-008)
                a.status = "pending"
                item = db.get(CatalogItem, a.catalog_item_id)
                item.status = "pending"
                db.add(CatalogVersion(id=new_id("cver"), item_id=item.id, version=str(card.get("version") or "0"),
                                      item_metadata=card, status="pending", created_by=p.sub))
            if body.secret:
                ref = f"a2a/{a.id}/credential"
                secrets.put(db, ref, body.secret, kind="token")
                a.auth = {**a.auth, "secret_ref": ref}
            db.commit()
            if review_needed:  # 커밋 후 — 알림은 별도 세션이라 열린 쓰기 트랜잭션 중이면 SQLite 가 잠긴다
                from app.services.notify import notify
                notify("registry.pending", f"원격 에이전트 등록 요청: {a.id}", f"{card.get('name')} — {p.sub}", severity="warn",
                       link="/console/registry/approvals", audience_role="operator", dedupe_key=f"a2a.pending:{a.id}")
            audit.append("a2a.agent.register", actor=p.sub, target=a.id, data={"url": a.url, "trust": a.trust_tier,
                                                                               "status": a.status})
            return {**agent_out(a, detail=True), "review": {k: rv[k] for k in ("checks", "ok", "suggested_trust")}}
    return await run_in_threadpool(_do)


```
**분석 / Analysis (Level 2)**
- L472-495 `_do`(스레드풀): 행 생성 → 소유 팀 → (승인 권한 없으면) 에이전트·카탈로그 pending + 버전 행 → 시크릿을 `a2a/{id}/credential` 에 Fernet 저장하고 `secret_ref` 연결 → 커밋 → (커밋 후) 알림 → 감사(인증 설정은 감사에 넣지 않음).
- L488 주석: 알림은 별도 세션이라 열린 쓰기 트랜잭션 중에 부르면 SQLite 가 잠긴다 — "커밋 뒤 부수효과" 규칙의 실례.
- `create_agent_row` 가 던진 `ApiError`(예약어·중복)는 `run_in_threadpool` 을 통해 그대로 전파되고, `with SessionLocal()` 이 세션을 닫는다(롤백).

```python path=app/api/a2a.py lines=498-514
@router.get("/api/v1/a2a/agents/{agent_id}/checks")
def card_checks(agent_id: str, _: Principal = Depends(require("a2a:read")), db: Session = Depends(get_db)):
    """Agent Card 재검증 이력 (SCR-G03 ⑦)."""
    rows = db.scalars(select(A2ACardCheck).where(A2ACardCheck.agent_id == agent_id)
                      .order_by(A2ACardCheck.checked_at.desc()).limit(50))
    return {"items": [{"id": r.id, "status": r.status, "http_status": r.http_status, "etag": r.etag, "changes": r.changes,
                       "error": r.error, "checked_at": r.checked_at} for r in rows]}


@router.get("/api/v1/a2a/agents/{agent_id}")
def get_agent(agent_id: str, _: Principal = Depends(require("a2a:read")), db: Session = Depends(get_db)):
    a = db.get(A2AAgent, agent_id)
    if a is None:
        raise not_found("a2a agent")
    return agent_out(a, detail=True)


```
**분석 / Analysis (Level 2)**
- L498-504 카드 재검증 이력(최근 50건), L507-512 상세(카드 원문·해시 포함) — 둘 다 `a2a:read`. 이력 조회는 에이전트 존재를 확인하지 않아 없는 id 면 빈 목록.

```python path=app/api/a2a.py lines=515-522
class AgentPatch(BaseModel):
    status: str | None = Field(None, pattern="^(active|disabled)$")
    framework: str | None = Field(None, max_length=40)
    auth: dict | None = None
    secret: str | None = Field(None, repr=False)
    acknowledge_card_change: bool = False


```
**분석 / Analysis (Level 2)**
- L515-520 `AgentPatch`: 상태(`active|disabled`)·프레임워크·`auth`(자유 형식 dict)·시크릿(`repr=False`)·카드 변경 확인.

```python path=app/api/a2a.py lines=523-556
@router.patch("/api/v1/a2a/agents/{agent_id}")
def patch_agent(agent_id: str, body: AgentPatch, p: Principal = Depends(require("a2a:write")),
                db: Session = Depends(get_db)):
    """원격 에이전트 인증 설정 (SCR-G04) — scheme(bearer 교환·apiKey·none)·시크릿·카드 변경 확인."""
    a = db.get(A2AAgent, agent_id)
    if a is None:
        raise not_found("a2a agent")
    if body.status:
        a.status = body.status
    if body.framework is not None:
        a.framework = body.framework.strip() or None
    if body.auth is not None:
        secrets.check_refs(p, body.auth, f"a2a/{a.id}/")
        secrets.check_destination(p, {**(a.auth or {}), **body.auth},
                                  body.auth.get("token_url") not in (None, (a.auth or {}).get("token_url")), bool(body.secret))
        pv = (body.auth.get("push_verify") or {}) if isinstance(body.auth.get("push_verify"), dict) else None
        if pv is not None and pv.get("jwks_url") and not str(pv["jwks_url"]).startswith(("https://", "http://")):
            raise bad_request("push_verify.jwks_url 은 http(s) URL (MSG-004).", code="invalid_jwks_url")
        if body.auth.get("scheme") == "oauth2" and not str(body.auth.get("token_url") or (a.auth or {}).get("token_url") or "").startswith("https://") \
                and settings.app_env == "prod":
            raise bad_request("OAuth2 token_url 은 https 여야 합니다 (MSG-004).", code="invalid_token_url")
        a.auth = {**(a.auth or {}), **body.auth}
    if body.secret:
        ref = f"a2a/{a.id}/credential"
        secrets.put(db, ref, body.secret, kind="token")
        a.auth = {**(a.auth or {}), "secret_ref": ref}
    if body.acknowledge_card_change:
        a.card_changed = False
    db.commit()
    audit.append("a2a.agent.update", actor=p.sub, target=a.id, data={"status": body.status, "auth": body.auth,
                                                                    "secret": bool(body.secret)})
    return agent_out(a, detail=True)


```
**분석 / Analysis (Level 2)**
- L527-529: 대상 확인. L530-533 상태·프레임워크.
- L535 `check_refs`: 남의 시크릿 참조는 `gateway:write`(Epic 39, `test/test_review_fixes.py:255-257`).
- L536-537 `check_destination`: **병합 후** auth 를 넘기고, `token_url` 이 새 값으로 바뀌는데 새 시크릿을 함께 넣지 않으면 `gateway:write` 필요(`app/services/secrets.py:40-51`).
- L538-543: `push_verify.jwks_url` http(s) 형식, prod 에서 oauth2 `token_url` 은 https.
- L544: 기존 auth 위에 입력을 **얕은 병합**(`{**old, **new}` — 중첩 dict 인 `tls`·`push_verify` 는 통째로 교체).
- L545-548 새 시크릿 → 자기 네임스페이스에 저장·연결. L549-550 카드 변경 확인 해제. L551 커밋 → L552 감사.
**짚어볼 점 / Watch-outs**
- [위험:중간] L530-531 **등록 승인(E05) 우회** — `status` 를 `active` 로 바꾸는 데 `a2a:write` 만 필요하다. 승인 권한이 없는 developer 가 등록하면 pending 이 되지만(L477-478, MSG-008 "승인 전에는 호출 불가"), 같은 사람이 곧바로 `PATCH {"status": "active"}` 로 활성화할 수 있다. 판정은 `agent.status == "active"` 만 본다(`app/services/governance.py:316`, `a2a_gateway.route_by_skill` 도 active 만). 카탈로그 항목은 pending 으로 남아 화면과 실제가 어긋난다. 완화 요인: 기본 등급 untrusted 라 명시(비와일드카드) 허용 정책이 있어야 호출된다(`governance.py:326-330`) — 그러나 `partner-*` 같은 패턴 정책에 맞는 id 를 골라 등록하면 바로 호출 가능. 같은 이유로 보안 담당이 `disabled` 로 내린 에이전트도 developer 가 다시 켤 수 있다. 최소 수정: `active` 로의 전환은 `catalog:approve` 필요 + 카탈로그 항목 상태가 approved/active 일 때만. 시험 공백: `test/test_a2a.py:209-211` 은 pending 호출 거부만 보고 PATCH 우회는 보지 않는다.
- [위험:중간] L534-544 **인증 설정 병합의 잔여 경로**(1차 [높음] 핫스팟 #1 의 남은 부분) — 남의 `secret_ref` 와 `token_url` 변경은 막혔지만, 저장된 시크릿의 **전송처를 바꾸는 다른 키**는 검사하지 않는다. (a) `scheme` 변경: oauth2(시크릿 = client_secret, 전송처 = `token_url`)를 `{"scheme": "apiKey", "header": "X-Api-Key"}` 로 바꾸면 같은 시크릿이 원격 에이전트 인터페이스 주소로 헤더에 실려 간다(`app/services/a2a_gateway.py:250-254`; `token_exchange: false` 면 Bearer 로, L257-259). 원격 에이전트 운영자가 IdP 의 client_secret 을 얻는다. (b) `{"tls": {"verify": false}}` — 자격 증명이 실리는 업스트림·토큰 엔드포인트 호출의 인증서 검증을 끈다(`app/services/tls.py:76-83`, `_build` 의 `CERT_NONE`). (c) `scopes` 로 저장된 클라이언트 자격의 요청 스코프 확대. (d) **객체 수준 인가 없음** — 어떤 developer 든 다른 팀(`owner_team`)·builtin·trusted 에이전트의 인증을 바꾸거나 끌 수 있다(`push_verify` 제거로 서명 검증 해제 포함). 최소 수정: auth 를 pydantic 스키마로 제한하고, `scheme`·`tls.verify`·`scopes`·`push_verify` 변경도 `check_destination` 과 같은 규칙(새 시크릿 동반 또는 `gateway:write`)으로, 소유 팀 또는 `gateway:write` 만 수정. 참고: 같은 시각 `secrets.py:31-37` 에 `destination_changed()`(D19-④)가 생겼지만 이 파일 L537 은 여전히 `token_url` 만 비교한다.
- [위험:낮음] L541-543 https 검사는 **입력에 `scheme: oauth2` 가 있을 때만** — 이미 oauth2 인 에이전트에 `{"auth": {"token_url": "http://…"}}` 만 보내면(새 시크릿 동반 시 L536-537 도 통과) 검사를 건너뛴다. L538-540 `jwks_url` 은 형식만 보고 내부 주소를 허용한다(1차 지적 유지; 조회는 `sync_client` egress 가드 = 링크 로컬만, `a2a_gateway.py:765-773`).
- [위험:낮음] L549-550 카드 변경 확인을 developer 가 스스로 해제한다 — 카드 변경(`card_changed`)은 호출을 막지 않고(`governance.check_agent` 가 보지 않음) 보안 담당 알림만 있어(`a2a_gateway.py:212-219` 부근), 원격이 카드의 인터페이스 URL 을 바꾸면 새 주소로 자격 증명이 바로 간다(`a2a_gateway.py:305-318`). L552 감사는 `body.auth` 원본을 그대로 남긴다(L373 항목의 인라인 평문 문제와 결합).

```python path=app/api/a2a.py lines=557-566
@router.post("/api/v1/a2a/agents/{agent_id}/refresh")
async def refresh_agent(agent_id: str, p: Principal = Depends(require("a2a:write"))):
    try:
        res = await refresh_card(agent_id, p.sub)
    except A2AError as exc:
        raise ApiError(exc.http_status if exc.http_status >= 400 else 502, "agent_card_invalid", exc.message,
                       extra=exc.data) from exc
    return {"agent_id": agent_id, "changed": res["changed"], "info": res["info"]}


```
**분석 / Analysis (Level 2)**
- L557-564 카드 재검증(`a2a:write`): ETag 로 304 면 시각만 갱신, 바뀌면 적용·이력·보안 알림(`app/services/a2a_gateway.py:153-220`). 실패해도 이전 캐시 유지.
- 재검증으로 받은 새 카드의 인터페이스 URL 이 **즉시** 호출 대상이 된다(승인 없음) — L549-550 항목 참조.

```python path=app/api/a2a.py lines=567-597
class TryIn(BaseModel):
    text: str
    data: dict | None = None
    files: list[dict] = Field(default_factory=list, max_length=10,
                              description="[{name, mimeType, bytes(base64)} | {name, mimeType, uri}] — file Part (SCR-G07 ②)")
    task_id: str | None = None
    context_id: str | None = None
    version: str = Field("1.0", pattern=r"^(0\.3|1\.0)$")  # D15-② 1.0 기본
    blocking: bool = True


def _file_parts(files: list[dict]) -> list[dict]:
    import base64
    out, total = [], 0
    for f in files:
        name, mime = str(f.get("name") or "file")[:255], str(f.get("mimeType") or "application/octet-stream")[:120]
        if f.get("bytes"):
            try:
                total += len(base64.b64decode(f["bytes"], validate=True))
            except ValueError as exc:
                raise bad_request(f"{name}: bytes 는 base64 (MSG-004)", code="invalid_file") from exc
            out.append({"kind": "file", "file": {"name": name, "mimeType": mime, "bytes": f["bytes"]}})
        elif str(f.get("uri") or "").startswith(("https://", "http://")):
            out.append({"kind": "file", "file": {"name": name, "mimeType": mime, "uri": f["uri"]}})
        else:
            raise bad_request(f"{name}: bytes 또는 http(s) uri 가 필요합니다 (MSG-004)", code="invalid_file")
    if total > settings.attachment_max_mb * 1024 * 1024:
        raise bad_request(f"파일 합계가 {settings.attachment_max_mb}MB 를 넘습니다.", code="file_too_large")
    return out


```
**분석 / Analysis (Level 2)**
- L567-575 `TryIn`: 텍스트·데이터·파일(최대 10개)·이어 보내기 id·버전(0.3|1.0)·blocking.
- L578-595 `_file_parts`: `base64.b64decode(..., validate=True)` 는 형식 오류에 `binascii.Error`(`ValueError` 하위)를 던져 400. 합계 크기는 디코딩 후 계산(디코딩 자체가 메모리를 쓰므로 요청 본문 상한이 앞단에 있어야 한다). uri 는 http(s)만 — 그 uri 를 가져오는 것은 원격 에이전트(게이트웨이 SSRF 아님).

```python path=app/api/a2a.py lines=598-615
@router.post("/api/v1/a2a/agents/{agent_id}/try")
async def try_agent(agent_id: str, body: TryIn, request: Request, p: Principal = Depends(require("a2a:read"))):
    """A2A 시험 콘솔 (SCR-G07) — 게이트웨이 경로 그대로 message/send."""
    parts = [{"kind": "text", "text": body.text}] + ([{"kind": "data", "data": body.data}] if body.data else []) + _file_parts(body.files)
    msg = {"kind": "message", "messageId": uuid.uuid4().hex, "role": "user", "parts": parts,
           **({"taskId": body.task_id} if body.task_id else {}), **({"contextId": body.context_id} if body.context_id
                                                                     else {})}
    method = ap.method_for("send", body.version)
    params = {"message": msg if body.version == ap.V03 else ap.message_to_v10(msg),
              "configuration": {"blocking": body.blocking} if body.version == ap.V03 else
              {"returnImmediately": not body.blocking}}
    try:
        res = await proxy(agent_id, _ctx(request, p), "try", method, params)
    except A2AError as exc:
        return {"error": {"code": exc.code, "message": exc.message, "data": exc.data}}
    return json.loads(res.body)


```
**분석 / Analysis (Level 2)**
- L598-613 시험 콘솔(`a2a:read`): 게이트웨이 경로 그대로 message/send — 정책·한도·가드·감사가 실제 호출과 같다. 1.0 이면 `returnImmediately`(blocking 의 반대) 로 변환.
- `task_id` 로 이어 보내기: 게이트웨이가 그 Task 의 기록 주체와 다르면 -32001(`a2a_gateway._task_id_of`·`_check_task_owner`, `a2a_gateway.py:561,622-638`, Epic 44).
- L611-612 `A2AError` 는 200 + `error` 객체(콘솔 표시용), L613 `res.body` 는 send 가 항상 `JSONResponse` 라 안전.

```python path=app/api/a2a.py lines=616-634
# ───────────────────────── Task (SCR-G05·G06) ─────────────────────────
def task_out(t: A2ATask, detail: bool = False) -> dict:
    out = {"id": t.id, "remote_task_id": t.remote_task_id, "agent_id": t.agent_id, "direction": t.direction,
           "state": t.state, "status_message": t.status_message, "context_id": t.context_id,
           "session_id": t.session_id, "run_id": t.run_id, "principal": t.principal_sub,
           "artifacts_count": len(t.artifacts or []), "push": bool(t.push_token), "trace_id": t.trace_id,
           "created_at": t.created_at, "updated_at": t.updated_at}
    if detail:
        out["history"], out["artifacts"] = t.history, t.artifacts
        out["events"] = t.events or []
        out["transitions"] = [{"state": "submitted", "at": t.created_at}] + [
            {"state": e["state"], "at": e["at"], "source": e.get("source")} for e in (t.events or []) if e.get("transition")]
    return out


def _task_visible(p: Principal, t: A2ATask) -> bool:
    return p.has("a2a:write") or p.has("gateway:read") or t.principal_sub == p.sub


```
**분석 / Analysis (Level 2)**
- L617-628 `task_out`: 푸시 설정(url·token)은 내보내지 않고 `push` 불리언만(클라이언트 웹훅 비밀 보호). 상세면 이력·아티팩트·이벤트·상태 전이.
- L631-632 `_task_visible`: `a2a:write` ∨ `gateway:read` ∨ 본인 Task.
**짚어볼 점 / Watch-outs**
- [위험:중간] L631-632 특권 판정이 너무 넓다 — `a2a:write` 와 `gateway:read` 는 **developer 역할**에 둘 다 있다(`app/core/auth.py:21-22`). 따라서 모든 개발자가 팀과 무관하게 **모든 사용자의 A2A Task 이력(사용자 메시지 원문)·아티팩트(답변)·이벤트**를 상세·목록·아티팩트 API 로 읽는다(L640-641·L666·L681 도 같은 판정). Epic 44 가 게이트웨이 JSON-RPC 경로를 "만든 주체만" 으로 고친 것(`app/services/a2a_gateway.py:634-638`)과 같은 데이터를 관리 API 는 넓게 연다. L641 주석 "팀 간 누출 방지" 와도 어긋난다. 시험 공백: C48 IDOR 행렬은 `user` 대 `user2` 만 본다(`test/test_idor_matrix.py:63-64`, `test/impl/48th/scenario.md` "priv 97개 제외"). 최소 수정: 특권을 감사·운영 전용 권한(예: `audit:read` 또는 `gateway:write`)으로 좁히거나 `owner_team`·사용자 팀 범위로 제한.

```python path=app/api/a2a.py lines=635-661
@router.get("/api/v1/a2a/tasks")
def list_tasks(p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db),
               agent_id: str | None = None, state: str | None = None, run_id: str | None = None,
               limit: int = Query(100, le=500)):
    stmt = select(A2ATask).order_by(A2ATask.updated_at.desc()).limit(limit)
    if not (p.has("a2a:write") or p.has("gateway:read")):
        stmt = stmt.where(A2ATask.principal_sub == p.sub)  # 팀 간 누출 방지
    if agent_id:
        stmt = stmt.where(A2ATask.agent_id == agent_id)
    if state:
        stmt = stmt.where(A2ATask.state == state)
    if run_id:
        stmt = stmt.where(A2ATask.run_id == run_id)
    rows = list(db.scalars(stmt))
    agents = {a.id: a for a in db.scalars(select(A2AAgent).where(A2AAgent.id.in_({t.agent_id for t in rows})))}
    out = []
    for t in rows:  # 위임 카드 헤더(SCR-C05 ①)·이벤트 로그(④) — 채팅 사용자는 에이전트 API 권한이 없어 함께 준다
        a = agents.get(t.agent_id)
        out.append(task_out(t) | {"agent": {"name": a.name, "trust_tier": a.trust_tier, "version": a.version,
                                            "skills": [s.get("name") or s.get("id") for s in a.skills or []][:5]} if a else None,
                                  "events": [{"state": (h.get("status") or {}).get("state") or h.get("kind"),
                                              "text": " ".join(x.get("text", "") for x in (h.get("parts") or [])
                                                               if x.get("kind") == "text")[:160]}
                                             for h in (t.history or [])[-8:]] if run_id else None})
    return {"items": out}


```
**분석 / Analysis (Level 2)**
- L639-647: 최신 순·상한 → 비특권이면 본인 Task 만 → 에이전트·상태·실행 필터.
- L649: 보이는 Task 의 에이전트를 `IN` 한 번으로(N+1 회피).
- L651-658: 채팅 위임 카드(SCR-C05)용으로 에이전트 요약을 함께 주고, `run_id` 필터면 최근 이력 8개를 이벤트 요약(160자)으로 — 채팅 사용자는 에이전트 API 권한이 없어서.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L638 `limit: int = Query(100, le=500)` 에 하한이 없다 — `limit=-1` 이면 SQLite 는 제한 없이 전체 행, PostgreSQL 은 음수 LIMIT 오류(500)(가능성; `app/api/gateway_admin.py:721` 과 같은 패턴). 최소 수정: `ge=1`.

```python path=app/api/a2a.py lines=662-685
@router.get("/api/v1/a2a/tasks/{task_id:path}/artifacts/{artifact_id}")
def get_artifact(task_id: str, artifact_id: str, p: Principal = Depends(require("chat:use")),
                 db: Session = Depends(get_db)):
    t = db.get(A2ATask, task_id)
    if t is None or not _task_visible(p, t):
        raise not_found("task")
    art = next((a for a in t.artifacts or [] if a.get("artifactId") == artifact_id), None)
    if art is None:
        raise not_found("artifact")
    texts = [x.get("text", "") for x in art.get("parts", []) if x.get("kind") == "text"]
    if texts and len(texts) == len(art.get("parts", [])):
        media = "text/markdown" if (art.get("name") or "").endswith(".md") else "text/plain"
        return PlainTextResponse("\n".join(texts), media_type=media + "; charset=utf-8")
    return art


@router.get("/api/v1/a2a/tasks/{task_id:path}")
def get_task(task_id: str, p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    t = db.get(A2ATask, task_id)
    if t is None or not _task_visible(p, t):
        raise not_found("task")
    return task_out(t, detail=True)


```
**분석 / Analysis (Level 2)**
- L662-675 아티팩트: `{task_id:path}` 는 슬래시를 포함한 Task id(`{agent}:{remote}`)를 받기 위한 변환기 — 더 구체적인 이 라우트가 L678 `get_task` 보다 먼저 등록돼 있어 `/artifacts/…` 가 Task id 로 먹히지 않는다(순서 의존, 1차 지적 그대로). 전부 텍스트 파트면 `text/markdown|plain; charset=utf-8` 평문으로(원격 내용이지만 `X-Content-Type-Options: nosniff` 가 전역 적용 `app/main.py:133` — 브라우저가 HTML 로 해석하지 않음), 아니면 JSON.
- L678-683 상세: 같은 가시성 판정.

```python path=app/api/a2a.py lines=686-711
@router.post("/api/v1/a2a/tasks/{task_id:path}/cancel")
async def cancel_task(task_id: str, request: Request, p: Principal = Depends(require("chat:use"))):
    def _load():
        with SessionLocal() as db:
            t = db.get(A2ATask, task_id)
            if t is None or not _task_visible(p, t) or t.direction != "outbound":
                raise not_found("task")
            return t.agent_id, t.remote_task_id
    agent_id, remote = await run_in_threadpool(_load)
    try:
        res = await proxy(agent_id, _ctx(request, p), "cancel", "tasks/cancel", {"id": remote})
    except A2AError as exc:
        raise ApiError(400, "cancel_failed", exc.message) from exc
    return json.loads(res.body)


@router.get("/api/v1/gateway/a2a/cards", tags=["gateway"])
def card_cache(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """A2A 프록시·Agent Card 캐시 (SCR-H15)."""
    return {"items": [{"id": a.id, "name": a.name, "version": a.version, "card_hash": a.card_hash,
                       "last_fetched_at": a.last_fetched_at, "card_changed": a.card_changed,
                       "trust_tier": a.trust_tier, "upstream": a.url, "gateway_card": f"/a2a/{a.id}/.well-known/agent-card.json",
                       "protocol_versions": ap.card_info(a.card).get("protocol_versions") if a.card else []}
                      for a in db.scalars(select(A2AAgent).order_by(A2AAgent.id))]}


```
**분석 / Analysis (Level 2)**
- L686-699 취소: 스레드풀에서 가시성·방향(outbound 만) 확인 → 게이트웨이 프록시로 `tasks/cancel`(0.3 메서드명, `op_of` 가 버전 변환). 실패는 400 `cancel_failed`.
- L702-709 카드 캐시(`gateway:read`): 해시·마지막 조회·변경 여부·업스트림 주소.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L691 의 가시성(특권 포함)과 프록시의 소유권 검사(`a2a_gateway.py:561,634-638`, 주인만)가 달라, 운영자가 남의 Task 를 취소하면 400 `cancel_failed`("Task not found")가 난다 — 안전한 쪽이지만 화면은 "취소 가능" 으로 보인다. 정책(운영자 강제 취소 허용 여부)을 한쪽으로 정해야 한다.

```python path=app/api/a2a.py lines=712-727
# ───────────────────────── 카탈로그 등록 핸들러 ─────────────────────────
@registry.register_handler("a2a_agent")
def _register_a2a(db: Session, item: CatalogItem, actor: str, options: dict) -> dict:
    card = item.item_metadata or {}
    errors = ap.validate_card(card)
    if errors:
        raise ApiError(422, "invalid_agent_card", "Agent Card 검증 실패", extra={"errors": errors})
    existing = db.get(A2AAgent, options.get("id") or item.name)
    if existing is None:
        a = create_agent_row(db, card, options.get("card_url") or card_url_for(ap.card_info(card)["url"]), actor,
                             agent_id=options.get("id") or slugify(item.name)[:60],
                             trust_tier=options.get("trust_tier", "untrusted"), catalog_item=item)
    else:
        a = existing
    db.flush()
    return {"id": a.id, "type": "a2a_agent", "gateway_path": f"/a2a/{a.id}"}
```
**분석 / Analysis (Level 2)**
- L713 `@registry.register_handler("a2a_agent")`: 모듈 import 부수효과로 카탈로그 "즉시 사용" 핸들러 등록(1차 지적 그대로 — `app/main.py` 가 이 모듈을 import 하면 구성요소 설정과 무관하게 실행).
- L715-718: 카탈로그 메타데이터 = 카드, 검증 실패면 422.
- L719-725: 없으면 생성(카드 url 로 카드 주소 추정), 있으면 그대로(갱신 안 함). 커밋은 `registry.register`.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L719-723 `options` 를 검증 없이 쓴다 — `trust_tier` 는 열거 검사가 없고, `catalog:write`(operator) 만으로 `verified`·`trusted` 를 지정할 수 있어 L468-469 의 "등급 지정은 보안 담당" 규칙과 다르다(심사 승인 경로도 `catalog:approve` 로 등급을 정하므로 `app/services/review.py:166-168` 운영자에게 새 권한은 아니다). 더 문제는 **목록 밖 문자열**(예: `"x"`)이 들어가면 `governance.check_agent` 가 untrusted 도 blocked 도 아닌 것으로 보고 명시 정책 요구를 건너뛴다(`app/services/governance.py:318,326`; 컬럼 제약 없음 `app/models.py:365`). `id`·`card_url` 도 `AgentIn` 패턴 검사 없이 들어간다(`:`·`/` 가 섞이면 `_tid`·라우트와 충돌). 최소 수정: `AgentIn` 과 같은 패턴 검증, `trust_tier != "untrusted"` 는 `policy:write`.

## 파일 평가 / File Assessment
- 강점: (1) 신고 헤더의 서버 판정(L47-48), 플랫폼 서버 Task 소유권 훅(L218-223)·푸시 URL 검사(L222)로 1차 신뢰 경계 지적 다수를 고쳤다. (2) 남의 시크릿 참조·`token_url` 전송처 변경에 시크릿 관리 권한을 요구하고(L457·L535-537), 응답에서 `secret_ref`·푸시 설정을 숨긴다. (3) 등록 승인 흐름(pending)·등급 상향 직무 분리(L468-470)·파일 입력 검증이 갖춰져 있다.
- 위험: (1) [위험:중간] PATCH 로 등록 승인 우회(`status=active`) — `app/api/a2a.py:530-531`. (2) [위험:중간] 인증 설정의 잔여 전송처 경로(scheme·tls.verify·객체 인가 없음) — `app/api/a2a.py:534-544`. (3) [위험:중간] developer 가 모든 사용자 Task 이력 열람 — `app/api/a2a.py:631-632`; [위험:중간] 카드 조회 SSRF — `app/api/a2a.py:458-461`. 그 밖에 이벤트 루프 동기 DB, 영속 실패 무음, 비인증 카드 노출, A2A 노출 경계 우회([위험:낮음]).
- 개선 권고(영향순): (1) `active` 전환을 `catalog:approve` 로, auth 를 스키마 + 전송처 규칙(scheme·tls.verify 포함) + 소유 팀 인가로. (2) `_task_visible` 특권을 감사 전용 권한·팀 범위로. (3) 운영 환경 카드 조회에 `internal_url` 거부 기본. (4) `_ctx`·노출 확인을 스레드풀로, `_persist` 실패 로그, `proxied_card` 상태 검사, skill 매칭 실패 시 거부.
- 파일 위험 등급 / Risk grade: 높음 — A2A 진입·원격 자격 증명 관리라는 보안 경계 핵심 모듈에 미해결 [위험:중간] 4건(승인 우회·인증 잔여 경로·Task 열람 범위·SSRF).
- 재읽기 권고 레벨: 2 — 관리 API 블록(L452-556·L616-661)은 권한 행렬(`app/core/auth.py:20-34`)과 함께 줄 단위로 다시 읽을 것.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 1차 [중간] L47-51 `_ctx` 가 agent·data_class·session·run·step 헤더를 그대로 신뢰 | 부분 해결 | `app/api/a2a.py:47-48` → `app/services/policy.py:57-74`(agent 기계·위임 주체만, run 주인만, 하향 신고 `data:declare`); `session_id` 원값 `policy.py:73`·`data:declare` 기본 부여 `app/core/auth.py:20` |
| 1차 L98·L107 카드 URL 을 Host 헤더 `base_url` 로 | 미해결 | `a2a.py:238,251` |
| 1차 L177·L187 토큰별 `principals` 사전 누수 | 대상 코드 제거 | `a2a.py:176-225` 사전 없음, `owner`·`owner_lookup` 로 대체(Epic 44) |
| 1차 L207-210 `on_change` 의 문맥 O(n) 순회 | 미해결 | `a2a.py:205-210` (1차 수용 범위) |
| 1차 L245·L262·L270 노출 확인 매 요청 DB | 미해결 | `a2a.py:248-250,265-267,273-277` (+ 이벤트 루프 동기 DB) |
| 1차 L279-290 푸시가 URL 토큰 단독 인증 → Task 조작 | 부분 해결 | 본문 Task id 묶음·비활성 에이전트 거부 `app/services/a2a_gateway.py:828-839`; 서명은 `push_verify` 설정 시만 `a2a_gateway.py:820` |
| 1차 L322 REST 본문 크기 상한 없음 | 미해결 | `a2a.py:326` |
| 1차 [중간] L448-462 dry_run 등록으로 내부 URL 조회(SSRF) | 미해결 | `a2a.py:458-461`; egress 가드 `app/core/http.py:35-36`(링크 로컬만, 39th 결정 항목) |
| 1차 [높음] L529-540 `patch_agent` 가 `auth`(secret_ref·token_url) 병합 → 시크릿 외부 전송 | 부분 해결 | 남의 참조 `a2a.py:535`·등록 `a2a.py:457`, token_url 변경 `a2a.py:536-537` 차단(`test/test_review_fixes.py:255-257`); scheme·tls.verify·scopes·객체 인가는 미해결 `a2a.py:534-544` |
| 1차 L530-532 `jwks_url` 내부 주소 허용 | 미해결 | `a2a.py:538-540` |
| 1차 L533-535 token_url https 검사는 prod 만·외부 https 허용 | 미해결 | `a2a.py:541-543` (scheme 없는 PATCH 는 검사 생략) |
| 1차 L581-582 파일 uri 는 원격 책임 | 미해결 | `a2a.py:589-590` (설계) |
| 1차 L654·L670·L678 `task_id:path` 라우트 순서 의존 | 미해결 | `a2a.py:662,678,686` (1차 수용) |
| 1차 L705-719 import 부수효과로 핸들러 등록 | 미해결 | `a2a.py:713` (1차 수용) |
