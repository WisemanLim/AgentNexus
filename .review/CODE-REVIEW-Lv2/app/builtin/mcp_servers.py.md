> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [builtin_Abstract.md](builtin_Abstract.md)

### 05. app/builtin/mcp_servers.py
**파일 요약 / File Summary**
- 역할 / Role: 참조(데모) MCP 서버 5종(Jira·Confluence·Dynatrace 모사, Internal Orders(sampling·elicitation 데모), Internal API(OpenAPI→도구))을 하나의 Starlette 앱에 `/<name>/mcp` 로 마운트. 각 서버 앞에 OAuth 2.1 리소스 서버 인가(`ResourceServerAuth`)를 둔다.
- 진입점 거리 / Distance: 0 (`uvicorn app.builtin.mcp_servers:app --port 8100` — compose 서비스 `mcp-servers`, 프로파일 demo)
- LOC: 329
- 주요 export: `app`(TracingMiddleware 로 감싼 Starlette), `SERVERS`, `SCOPES`, `server_json()`, 도구 함수들(`search_issues` 등)
- 의존(내부): `app.builtin.{demo_data,internal_system,mcp_auth}`, `app.core.{tracing,config}`
- 외부 패키지: fastmcp(FastMCP·Context·ToolError·elicitation), mcp(`mcp.types`, `mcp.shared.inbound`), httpx2(전이 의존 — pyproject 에 직접 선언 없음), pydantic, starlette
- 부수효과 / Side effects: 모듈 전역 가변 데이터(`_ISSUES`·`_PAGES`) — 쓰기 도구가 프로세스 메모리를 변경, import 시점 `tracing.setup_for_service`
- 동시성 / Concurrency: async 도구(`classify_orders`·`request_return`) + 동기 도구(FastMCP 가 실행 방식 결정), stateful SSE 세션(`internal-orders`)
- 테스트 커버리지 흔적: `test/test_mcp.py`(전 서버 refresh·도구 호출·스코프 거부), `test/test_mcp_consent.py`(sampling·elicitation), `test/conftest.py:61-67`(in-process 마운트)

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/builtin/mcp_servers.py lines=1-30
"""참조 MCP 서버 (FR-S00·S01·S02) — Jira·Confluence·Dynatrace 모사 + Internal API(OpenAPI → 도구).

실제 SaaS 없이 동작(FR-P02)하도록 가상 데이터를 쓴다. 운영에서는 mcp-atlassian·dynatrace-mcp 로 교체한다
(compose 프로파일 `saas`). Streamable HTTP(stateless·JSON 응답), 경로 = /<name>/mcp.
기동: uvicorn app.builtin.mcp_servers:app --port 8100  (compose 서비스 mcp-servers, 내부 네트워크 전용)
"""
import contextlib
import copy
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Literal

import httpx2
import mcp.types as mt
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation, get_elicitation_schema
from fastmcp.server.dependencies import get_http_request
from mcp.shared.inbound import MODERN_PROTOCOL_VERSIONS
from pydantic import Field, TypeAdapter
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app.builtin import demo_data, internal_system
from app.builtin.mcp_auth import ResourceServerAuth, prm
from app.core import tracing
from app.core.config import settings
```
**분석 / Analysis (Level 2)**
- L1-6: 운영에서는 실제 SaaS MCP 서버(mcp-atlassian·dynatrace-mcp)로 교체한다는 전제 — 이 파일은 시연·시험용 대역(stub)이다.
- L15 `import httpx2`: OpenAPI→MCP 변환용 비동기 클라이언트. `pyproject.toml` 의 직접 의존이 아니라 fastmcp·mcp·openai 의 **전이 의존**(uv.lock 의 `httpx2 2.13.1`)이다.
- L16-22: MCP 타입·FastMCP 서버·도구 오류 타입·elicitation 스키마 유틸·현재 HTTP 요청 접근자·`MODERN_PROTOCOL_VERSIONS`·pydantic 검증기.
- L27-30: 데모 데이터·내부 시스템 FastAPI 앱, 리소스 서버 인가 미들웨어·PRM 생성기(`app/builtin/mcp_auth.py`), 트레이싱·설정.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 직접 import 하는 `httpx2` 를 `pyproject.toml` 에 선언하지 않았다 — fastmcp 가 의존을 바꾸면 이 파일이 ImportError 로 깨진다. 직접 의존으로 명시하거나 공용 `httpx` 를 쓰는 편이 안전하다(`app/services/mcp_gateway.py:502` 도 동일).

```python path=app/builtin/mcp_servers.py lines=32-51
READ = {"readOnlyHint": True, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}


def _actor() -> str:
    try:
        claims = get_http_request().scope.get("state", {}).get("anx_claims", {})
        return claims.get("name") or claims.get("sub") or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _modern(ctx: Context) -> bool:
    """2026-07-28 요청 — 서버→클라이언트 요청이 없으므로 InputRequiredResult(MRTR 가드 패턴)로 입력을 받는다."""
    rc = ctx.request_context
    return rc is not None and rc.protocol_version in MODERN_PROTOCOL_VERSIONS


def _score(text: str, query: str) -> int:
    return sum(1 for t in query.lower().split() if t in text.lower())
```
**분석 / Analysis (Level 2)**
- L32-33: MCP 도구 annotations 프리셋 — `readOnlyHint`/`destructiveHint`/`openWorldHint`. 게이트웨이는 이 힌트로 기본 위험도를 정한다(`app/services/governance.py:41-50`).
- L36-41 `_actor()`: 리소스 서버 인가 미들웨어가 ASGI `scope["state"]["anx_claims"]` 에 넣은 토큰 클레임에서 사용자 이름을 꺼낸다 — 게이트웨이가 교환해 준 토큰으로 "누가 호출했는지" 가 백엔드까지 전파되는 것(FR-I02)을 보여 주는 지점.
- L44-47 `_modern()`: 요청의 협상 버전이 2026-07-28(modern)이면 서버→클라이언트 역요청 대신 `InputRequiredResult`(MRTR) 패턴을 써야 한다.
- L50-51 `_score()`: 질의 토큰이 본문에 몇 개 포함되는지 — 단순 키워드 점수.
**짚어볼 점 / Watch-outs**
- L40 `except Exception` 은 조용한 삼킴이지만, 요청 컨텍스트 밖(직접 함수 호출·시험) 호출을 `"unknown"` 으로 처리하려는 의도라 수용 가능하다.
- L51 `t in text.lower()` 는 부분 문자열 일치 — "api" 가 "rapid" 에도 맞는다(데모 수준).

```python path=app/builtin/mcp_servers.py lines=54-97
# ───────── Jira ─────────
jira = FastMCP("jira", instructions="Jira 이슈 검색·조회·전환 (데모: ORION 프로젝트)")
_ISSUES = copy.deepcopy(demo_data.JIRA_ISSUES)


@jira.tool(annotations=READ)
def search_issues(query: str = "", status: str | None = None, limit: int = 10) -> list[dict]:
    """JQL 대신 키워드로 이슈를 검색한다. status 예: Open, In Progress, In Review, Done."""
    rows = [i for i in _ISSUES if not status or i["status"].lower() == status.lower()]
    if query:
        scored = sorted(((_score(" ".join([i["key"], i["summary"], i["description"], *i["labels"]]), query), i)
                         for i in rows), key=lambda x: (-x[0], x[1]["key"]))
        rows = [i for s, i in scored if s > 0] or rows  # 키워드 미적중이면 최근 이슈
    return [{k: i[k] for k in ("key", "summary", "status", "priority", "assignee", "updated")} for i in rows[:limit]]


@jira.tool(annotations=READ)
def get_issue(key: str) -> dict:
    """이슈 상세."""
    for i in _ISSUES:
        if i["key"] == key:
            return i
    raise ValueError(f"issue {key} not found")


@jira.tool(annotations=WRITE)
def transition_issue(key: str, to: str) -> dict:
    """이슈 상태 전환 (쓰기)."""
    issue = get_issue(key)
    issue["status"] = to
    return {"key": key, "status": to, "by": _actor()}


@jira.tool(annotations=WRITE)
def add_comment(key: str, body: str) -> dict:
    """이슈에 댓글 추가 (쓰기)."""
    get_issue(key)
    return {"key": key, "comment": body, "by": _actor()}


@jira.prompt
def weekly_report(project: str = "ORION") -> str:
    """주간 이슈 보고 프롬프트."""
    return f"{project} 프로젝트의 이번 주 이슈를 우선순위별로 요약하고 블로커와 다음 주 계획을 제시하라."
```
**분석 / Analysis (Level 2)**
- L55: `FastMCP("jira", instructions=...)` — MCP initialize 응답의 `instructions` 로 클라이언트(LLM)에게 서버 용도를 알린다.
- L56: `copy.deepcopy` 로 데모 데이터를 복제 — 원본 상수(`demo_data.JIRA_ISSUES`)를 쓰기 도구가 오염시키지 않게.
- L59-67 `search_issues`: 상태 필터 → 점수 정렬(점수 내림차순, 키 오름차순) → 점수 0 은 제외하되 하나도 안 맞으면 전체 반환 → 필드 일부만 노출.
- L70-76 `get_issue`: 없으면 `ValueError` — FastMCP 가 도구 오류(`isError: true`)로 변환한다.
- L79-84 `transition_issue`: 전역 이슈 dict 를 직접 변경(쓰기). `by` 에 호출자 표시.
- L87-91 `add_comment`: 존재 확인만 하고 저장하지 않는다(데모).
- L94-97: MCP 프롬프트 — `prompts/list`·`prompts/get` 으로 노출(`test/test_mcp.py:173-178`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L82-83 `_ISSUES` 는 모든 사용자·요청이 공유하는 프로세스 전역 상태 — 한 사용자의 전환이 다른 사용자에게 보이고, 재시작하면 사라진다. FastMCP 가 동기 도구를 스레드에서 실행하는 경우 동시 쓰기 경쟁도 가능하다(데모라 수용).
- L60 `limit` 에 상한이 없다 — 음수면 슬라이스 의미가 바뀐다(`rows[:-1]`).

```python path=app/builtin/mcp_servers.py lines=100-137
# ───────── Confluence ─────────
confluence = FastMCP("confluence", instructions="Confluence 페이지 검색·조회·작성 (데모)")
_PAGES = copy.deepcopy(demo_data.CONFLUENCE_PAGES)


@confluence.tool(annotations=READ)
def search_pages(query: str = "", space: str | None = None, limit: int = 5) -> list[dict]:
    """페이지 검색."""
    rows = [p for p in _PAGES if not space or p["space"] == space]
    rows.sort(key=lambda p: -_score(p["title"] + " " + p["body"], query))
    return [{"id": p["id"], "space": p["space"], "title": p["title"], "updated": p["updated"],
             "excerpt": p["body"][:120]} for p in rows[:limit]]


@confluence.tool(annotations=READ)
def get_page(page_id: str) -> dict:
    """페이지 본문."""
    for p in _PAGES:
        if p["id"] == page_id:
            return p
    raise ValueError(f"page {page_id} not found")


@confluence.tool(annotations=WRITE)
def create_page(space: str, title: str, body: str) -> dict:
    """새 페이지 작성 (쓰기 — 고위험, 사람 승인 대상)."""
    page = {"id": str(1000 + len(_PAGES) + 1), "space": space, "title": title, "body": body,
            "updated": datetime.now(timezone.utc).date().isoformat(), "created_by": _actor()}
    _PAGES.append(page)
    return {"id": page["id"], "url": f"https://wiki.hangyeol.example/{space}/{page['id']}", "title": title,
            "created_by": page["created_by"]}


@confluence.resource("confluence://page/{page_id}")
def page_resource(page_id: str) -> str:
    """페이지를 MCP 리소스로 노출."""
    p = get_page(page_id)
    return f"# {p['title']}\n\n{p['body']}"
```
**분석 / Analysis (Level 2)**
- L105-111 `search_pages`: 공간 필터 후 점수 정렬, 본문 앞 120자를 발췌로.
- L114-120 `get_page`. L123-130 `create_page`: 고위험 쓰기 도구(docstring 이 "사람 승인 대상" 명시) — 이름에 `create` 가 있어 게이트웨이 기본 규칙이 승인 필요로 분류한다(`app/services/governance.py:25-26,48`).
- L133-137: 리소스 템플릿 `confluence://page/{page_id}` — `resources/templates/list`·`resources/read` 시연(`test/test_mcp.py:179-182`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L126 `id = 1000 + len(_PAGES) + 1` — 동시 호출 시 같은 id 가 나올 수 있다(경쟁 조건). 데모.
- L129 하드코딩된 가상 도메인 `wiki.hangyeol.example` — `.example` 예약 TLD 라 실제 트래픽은 없다.

```python path=app/builtin/mcp_servers.py lines=140-176
# ───────── Dynatrace ─────────
dynatrace = FastMCP("dynatrace", instructions="Dynatrace 문제·메트릭 조회 (데모)")


@dynatrace.tool(annotations=READ)
def list_problems(service: str | None = None, status: str = "OPEN") -> list[dict]:
    """문제(Problem) 목록."""
    return [{k: p[k] for k in ("id", "title", "service", "status", "severity", "started")}
            for p in demo_data.DYNATRACE_PROBLEMS
            if (not service or p["service"] == service) and (status == "ALL" or p["status"] == status)]


@dynatrace.tool(annotations=READ)
def get_problem(problem_id: str) -> dict:
    """문제 상세(근본 원인·영향)."""
    for p in demo_data.DYNATRACE_PROBLEMS:
        if p["id"] == problem_id:
            return p
    raise ValueError(f"problem {problem_id} not found")


@dynatrace.tool(annotations=READ)
def query_metrics(service: Annotated[str, Field(json_schema_extra={"x-mcp-header": "Service"})],
                  metric: str = "p95_latency_ms") -> dict:
    """최근 8구간 메트릭 시계열. service 는 2026-07-28 에서 `Mcp-Param-Service` 헤더로도 보낸다(중개자 라우팅용)."""
    return {"service": service, "metric": metric, "series": demo_data.METRICS.get((service, metric), [])}


LIVE_INTERVAL_S = 10.0  # 실시간 지표 리소스 갱신 주기 (subscriptions/listen resourceSubscriptions 데모)


@dynatrace.resource("dynatrace://live/{service}", mime_type="application/json")
def live_metrics(service: str) -> str:
    """서비스 실시간 지표 — LIVE_INTERVAL_S 마다 새 구간 값으로 바뀐다(가상 데이터)."""
    window = int(time.time() // LIVE_INTERVAL_S)
    series = demo_data.METRICS.get((service, "p95_latency_ms")) or [120, 135, 128, 150]
    return json.dumps({"service": service, "metric": "p95_latency_ms", "value": series[window % len(series)], "window": window})
```
**분석 / Analysis (Level 2)**
- L144-149 `list_problems`: 서비스·상태 필터(`ALL` 은 전체).
- L152-158 `get_problem`. L161-165 `query_metrics`: 인자 `service` 에 `x-mcp-header: Service` 확장을 붙여, 2026-07-28 클라이언트가 `Mcp-Param-Service` 헤더로도 보내게 한다(중개자가 본문을 열지 않고 라우팅). 게이트웨이는 헤더↔본문 일치를 검증한다(`app/services/mcp_modern.py:102-108`).
- L168-176: 실시간 리소스 — `time.time() // 10` 구간마다 값이 바뀌어 `subscriptions/listen` 의 리소스 변경 알림을 시연한다.
**짚어볼 점 / Watch-outs**
- L175 `series[window % len(series)]` — 기본값 리스트가 있어 0 나눗셈은 없다.

```python path=app/builtin/mcp_servers.py lines=179-216
# ───────── Internal Orders (stateful·SSE — 서버→클라이언트 요청 데모: sampling·elicitation, SCR-C08) ─────────
internal_orders = FastMCP("internal-orders", instructions="사내 주문 메모 조회·이상 분류(모델 호출=샘플링 요청)·반품 접수(추가 입력)")
_ORDER_MEMOS = {
    "2026-09-30": [
        {"order": "O-2401", "memo": "결제 승인 후 배송지 3회 변경 요청"}, {"order": "O-2402", "memo": "정상 출고"},
        {"order": "O-2403", "memo": "동일 카드로 10분 내 주문 7건"}, {"order": "O-2404", "memo": "포장 파손 문의"},
    ],
}


def _memos(date: str) -> list[dict]:
    return _ORDER_MEMOS.get(date, _ORDER_MEMOS["2026-09-30"])


@internal_orders.tool(annotations=READ)
async def classify_orders(ctx: Context, date: str = "2026-09-30") -> dict:
    """주문 메모를 정상/이상/보류로 분류 — 서버가 클라이언트(게이트웨이)에 모델 호출(샘플링)을 요청한다. 진행률을 알린다."""
    memos = _memos(date)
    lines = "\n".join(f"- {m['order']}: {m['memo']}" for m in memos)
    await ctx.report_progress(1, 3, "주문 메모 수집")
    ask = {"messages": [mt.SamplingMessage(role="user", content=mt.TextContent(type="text", text=f"{date} 주문 메모:\n{lines}"))],
           "system_prompt": "주문 메모를 정상/이상/보류로 분류하라. 각 줄에 '주문번호: 분류 — 근거' 형식으로 답하라.",
           "max_tokens": 256, "include_context": "thisServer",
           "model_preferences": mt.ModelPreferences(hints=[mt.ModelHint(name="small-fast")], costPriority=0.8)}
    if _modern(ctx):
        res = (ctx.input_responses or {}).get("classify")
        if res is None:
            return mt.InputRequiredResult(inputRequests={"classify": mt.CreateMessageRequest(params=mt.CreateMessageRequestParams(**ask))})
    else:
        try:
            res = await ctx.session.create_message(**ask, related_request_id=ctx.request_id)
        except Exception as exc:  # noqa: BLE001 — 거부·만료는 도구 오류로 알린다
            raise ToolError(f"샘플링이 거부되어 분류하지 못했습니다: {exc}") from exc
    await ctx.report_progress(2, 3, "분류 결과 정리")
    content = getattr(res, "content", None)
    text = content.text if isinstance(content, mt.TextContent) else str(content)
    await ctx.report_progress(3, 3, "완료")
    return {"date": date, "count": len(memos), "classification": text, "model": getattr(res, "model", None)}
```
**분석 / Analysis (Level 2)**
- L180: 상태ful(SSE) 서버 — 서버→클라이언트 요청(sampling·elicitation)을 보내려면 세션과 SSE 응답이 필요하다(L282 `STATEFUL`).
- L181-190: 날짜별 주문 메모, 없는 날짜는 기본 날짜 데이터로 대체.
- L193-216 `classify_orders`: 진행률 알림(1/3→3/3), 샘플링 요청 본문 구성(`SamplingMessage`·시스템 프롬프트·`max_tokens`·모델 힌트).
  - L203-206 modern(2026-07-28): 이전 응답(`ctx.input_responses["classify"]`)이 없으면 `InputRequiredResult` 로 "이 입력이 필요하다" 를 돌려주고 끝 — 클라이언트(게이트웨이)가 응답을 채워 재호출한다(MRTR).
  - L207-211 legacy: `ctx.session.create_message(...)` 로 SSE 역채널에서 클라이언트에게 모델 호출을 요청하고 기다린다. 거부·만료는 `ToolError` 로 변환.
- L213-216: 결과 content 가 텍스트면 본문, 아니면 문자열화.
**짚어볼 점 / Watch-outs**
- L210 `except Exception` → `ToolError` 변환은 조용한 삼킴이 아니라 의미 변환이다(좋음). 단 원인 메시지 `{exc}` 를 그대로 노출 — 데모라 수용.
- L206 의 반환형은 dict 가 아니라 `InputRequiredResult` — 함수 시그니처 `-> dict` 와 다르다(타입 힌트 불일치, 동작은 FastMCP 가 처리).

```python path=app/builtin/mcp_servers.py lines=219-263
@dataclass
class ReturnRequest:
    reason: Literal["파손", "오배송", "단순 변심"]
    quantity: int


@internal_orders.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
async def request_return(ctx: Context, order_id: str) -> dict:
    """반품 접수 — 사유·수량을 사용자에게 추가로 묻는다(elicitation)."""
    message = f"{order_id} 반품 사유와 수량을 입력하세요"
    if _modern(ctx):
        answer = (ctx.input_responses or {}).get("return")
        if answer is None:
            return mt.InputRequiredResult(inputRequests={"return": mt.ElicitRequest(params=mt.ElicitRequestFormParams(
                message=message, requested_schema=get_elicitation_schema(ReturnRequest)))})
        if getattr(answer, "action", None) != "accept":
            return {"order_id": order_id, "status": "declined" if getattr(answer, "action", None) == "decline" else "canceled"}
        try:
            data = TypeAdapter(ReturnRequest).validate_python(answer.content or {})
        except ValueError as exc:
            raise ToolError(f"반품 입력이 올바르지 않습니다: {exc}") from exc
    else:
        answer = await ctx.elicit(message, response_type=ReturnRequest)
        if not isinstance(answer, AcceptedElicitation):
            return {"order_id": order_id, "status": "declined" if type(answer).__name__.startswith("Declined") else "canceled"}
        data = answer.data
    return {"order_id": order_id, "status": "requested", "reason": data.reason, "quantity": data.quantity, "by": _actor()}


@internal_orders.resource("orders://daily/2026-09-30", mime_type="text/plain")
def daily_orders() -> str:
    """2026-09-30 주문 메모 (내부 데이터)."""
    return "\n".join(f"{m['order']}: {m['memo']}" for m in _memos("2026-09-30"))


@internal_orders.resource("orders://policy/returns", mime_type="text/markdown")
def return_policy() -> str:
    """반품 정책."""
    return "# 반품 정책\n\n- 수령 후 7일 이내\n- 파손·오배송은 왕복 배송비 무료\n- 단순 변심은 반품 배송비 고객 부담"


@internal_orders.prompt
def anomaly_triage(date: str) -> str:
    """주문 이상 건 분류 요청 프롬프트."""
    return f"{date} 주문 중 이상 건을 찾아 분류하고, 조치가 필요한 건은 담당 팀과 함께 정리해줘"
```
**분석 / Analysis (Level 2)**
- L219-222: elicitation 스키마용 dataclass — `Literal` 로 선택지 3개, `quantity: int`. `get_elicitation_schema` 가 JSON Schema 로 바꾼다.
- L225-245 `request_return`: modern 이면 `ElicitRequest` 를 `InputRequiredResult` 로 반환, 응답의 `action` 이 accept 가 아니면 declined/canceled, accept 면 `TypeAdapter(...).validate_python` 으로 **서버 측에서 다시 검증**. legacy 는 `ctx.elicit(...)` 가 같은 일을 한다.
- L248-263: 리소스 2종·프롬프트 1종.
**짚어볼 점 / Watch-outs**
- L237-239 사용자 입력을 서버가 재검증하는 것은 올바른 방어(게이트웨이도 스키마 검증 — `app/services/mcp_consent.py:265-272`). `quantity` 의 음수·0 범위 검증은 없다(데모).
- L243 `type(answer).__name__.startswith("Declined")` — 클래스 이름 문자열 비교는 라이브러리 리네이밍에 취약하다. fastmcp 가 제공하는 거부·취소 결과 클래스로 `isinstance` 판별하는 편이 안전하다.

```python path=app/builtin/mcp_servers.py lines=266-284
# ───────── Internal API (OpenAPI → MCP 도구, FR-S02) ─────────
_internal_client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=internal_system.app),
                                      base_url="http://internal-system")
internal_api = FastMCP.from_openapi(internal_system.app.openapi(), client=_internal_client, name="internal-api")

SCOPES = {
    "jira": {"search_issues": "issues:read", "get_issue": "issues:read", "transition_issue": "issues:write",
             "add_comment": "issues:write"},
    "confluence": {"search_pages": "pages:read", "get_page": "pages:read", "create_page": "pages:write"},
    "dynatrace": {"list_problems": "problems:read", "get_problem": "problems:read", "query_metrics": "metrics:read"},
    # FastMCP.from_openapi 는 operationId 의 '.' 를 '_' 로 정규화한다 (orders.search → orders_search)
    "internal-api": {"orders_search": "orders:read", "orders_get": "orders:read", "tickets_create": "tickets:write"},
    "internal-orders": {"classify_orders": "orders:read", "request_return": "orders:write"},
}
SERVERS = {"jira": jira, "confluence": confluence, "dynatrace": dynatrace, "internal-api": internal_api,
           "internal-orders": internal_orders}
STATEFUL = {"internal-orders"}  # 서버→클라이언트 요청은 세션 + SSE 응답이 필요 (나머지는 stateless JSON)
_HTTP_APPS = {name: srv.http_app(path="/mcp", stateless_http=name not in STATEFUL, json_response=name not in STATEFUL)
              for name, srv in SERVERS.items()}
```
**분석 / Analysis (Level 2)**
- L267-269: Internal API — 같은 프로세스의 FastAPI 앱(`internal_system.app`)을 `ASGITransport` 로 직접 호출하는 클라이언트를 만들고, `FastMCP.from_openapi` 가 OpenAPI operation 마다 도구를 생성한다(FR-S02).
- L271-279 `SCOPES`: 서버별 도구 → 필요한 OAuth 스코프. `server_json()`(L299) 과 PRM(L308) 에 그대로 실려 게이트웨이가 도구별 최소 스코프로 토큰을 교환한다(`app/services/mcp_gateway.py:103-107`).
- L276-277 주석: FastMCP 가 operationId 의 `.` 를 `_` 로 바꾼다는 라이브러리 동작을 기록 — 이런 주석은 인수인계에 중요하다.
- L280-284: 서버 이름 → FastMCP 인스턴스, 각자 `http_app(path="/mcp")` 생성. stateful 이 아닌 서버는 `stateless_http=True, json_response=True`(세션 없음, SSE 대신 JSON 응답).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L267 모듈 전역 `httpx2.AsyncClient` 는 닫히지 않는다(lifespan 정리 없음) — 프로세스 수명 동안 하나라 실제 누수는 아니지만, 시험에서 이벤트 루프가 바뀌면 "attached to a different loop" 오류 원인이 될 수 있다.

```python path=app/builtin/mcp_servers.py lines=287-312
def server_json(name: str, base_url: str) -> dict:
    """참조 서버의 server.json (레지스트리 게시·자동 등록용, 도구별 스코프 확장 포함)."""
    titles = {"jira": "Jira MCP Server (demo)", "confluence": "Confluence MCP Server (demo)",
              "dynatrace": "Dynatrace MCP Server (demo)", "internal-api": "Internal API MCP Server",
              "internal-orders": "Internal Orders MCP Server (demo)"}
    return {"$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
            "name": f"io.agentnexus/{name}", "title": titles[name],
            "description": {"jira": "Jira 이슈 검색·전환 (가상 데이터)", "confluence": "Confluence 페이지 검색·작성 (가상 데이터)",
                            "dynatrace": "Dynatrace 문제·메트릭 (가상 데이터)",
                            "internal-api": "Internal System REST → MCP tools",
                            "internal-orders": "주문 메모 이상 분류(샘플링)·반품 접수(추가 입력) — 가상 데이터"}[name],
            "version": "0.1.0", "remotes": [{"type": "streamable-http", "url": f"{base_url.rstrip('/')}/{name}/mcp"}],
            "_meta": {"auth": {"type": "oauth2", "tool_scopes": SCOPES[name]},
                      "tools": sorted(SCOPES[name]), "license": "MIT", "builtin": True}}


async def _prm(request):
    name = request.path_params["name"]
    if name not in SERVERS:
        return JSONResponse({"error": "not_found"}, status_code=404)
    base = settings.mcp_servers_url
    return JSONResponse(prm(f"{base.rstrip('/')}/{name}/mcp", list(SCOPES[name].values())))


async def _health(_):
    return JSONResponse({"status": "ok", "servers": sorted(SERVERS)})
```
**분석 / Analysis (Level 2)**
- L287-300 `server_json`: MCP Registry `server.json` 스키마 형식으로 메타데이터 생성 — 레지스트리 게시·자동 등록에 쓰인다. `_meta.auth.tool_scopes` 가 AgentNexus 확장.
- L303-308 `_prm`: RFC 9728 Protected Resource Metadata — `/.well-known/oauth-protected-resource/{name}/mcp` 경로로 인가 서버·지원 스코프를 알린다. 모르는 이름은 404.
- L311-312: 헬스 체크(compose healthcheck 대상).
**짚어볼 점 / Watch-outs**
- L293·L297 dict 인덱싱 — 모르는 `name` 이면 `KeyError`. 호출자는 내부 코드(시드·시험)뿐이라 수용.

```python path=app/builtin/mcp_servers.py lines=315-329
@contextlib.asynccontextmanager
async def _lifespan(app):
    async with contextlib.AsyncExitStack() as stack:
        for sub in _HTTP_APPS.values():
            await stack.enter_async_context(sub.router.lifespan_context(sub))
        yield


app = Starlette(
    routes=[Route("/healthz", _health), Route("/.well-known/oauth-protected-resource/{name}/mcp", _prm)]
    + [Mount(f"/{name}", app=ResourceServerAuth(sub, name, SCOPES[name], settings.mcp_servers_url))
       for name, sub in _HTTP_APPS.items()],
    lifespan=_lifespan)
tracing.setup_for_service("agentnexus-mcp-servers")
app = tracing.TracingMiddleware(app, component="mcp-servers")
```
**분석 / Analysis (Level 2)**
- L315-320: 하위 ASGI 앱들의 lifespan 을 `AsyncExitStack` 으로 함께 진입 — FastMCP stateful 세션 매니저가 lifespan 에서 작업 그룹을 띄우므로 반드시 필요하다(빠뜨리면 stateful 서버의 세션 매니저가 초기화되지 않아 요청이 실패한다).
- L323-327: 라우트 = 헬스·PRM + 서버별 `Mount("/<name>", ResourceServerAuth(...))`. 인가 미들웨어가 Bearer 토큰을 검증하고 도구별 스코프를 확인한다(`test/test_mcp.py:53-72` 가 401·403 을 검증).
- L328-329: 별도 컨테이너면 OTLP 로 api 수신기에 스팬 전송, 앱을 트레이싱 미들웨어로 감싼다. `TracingMiddleware.router` 프로퍼티가 감싼 앱의 lifespan 을 위임한다(`app/core/tracing.py:183-185`).
**짚어볼 점 / Watch-outs**
- L329 `app` 이름을 재바인딩 — 시험(`test/conftest.py:64`)이 이 감싼 객체를 `EMBEDDED` 에 넣고 `sub.router.lifespan_context(sub)` 를 호출하는데, 이는 `TracingMiddleware.router` 위임 덕분에 동작한다. 미들웨어를 하나 더 감싸면 이 위임이 끊긴다.
- compose 에서 이 서비스는 `backend` 내부 네트워크에만 있다(`docker-compose.yml`) — 게이트웨이 우회 차단(FR-G00)은 네트워크 격리에 의존한다.

## 파일 평가 / File Assessment
- 강점: (1) 도구별 스코프·annotations·PRM 을 한 곳에서 선언해 게이트웨이 정책 기본값과 맞물린다. (2) legacy(SSE 역요청)와 modern(MRTR) 두 세대를 같은 도구에서 분기해 시연. (3) elicitation 응답을 서버 측에서 재검증.
- 위험: (1) 전역 가변 데모 데이터 — 사용자 간 공유·경쟁. (2) 직접 import 하는 전이 의존 `httpx2`. (3) 클래스 이름 문자열 비교(L243).
- 개선 권고(영향순): (1) `httpx2` 직접 의존 선언 또는 제거. (2) 쓰기 도구에 `threading.Lock` 또는 불변 반환. (3) `isinstance` 로 elicitation 결과 판별.
- 재읽기 권고 레벨: 3 — 데모 코드이므로 설계 의도(스코프·세대 분기)만 이해하면 된다.
