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
