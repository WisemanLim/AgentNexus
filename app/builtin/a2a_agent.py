"""데모 원격 A2A 에이전트 — '관측 분석 에이전트(Observability Analyst)' (PRD 4.3 시나리오 2, FR-R00~R06).

자체 워크로드 신원(client_credentials, FR-I04)으로 AgentNexus 게이트웨이를 경유해 Dynatrace MCP 도구와 도메인 특화 모델을
호출(FR-L03 '원격 에이전트가 토큰으로 모델·API 접근')하고, 장애 분석 보고서를 Artifact 로 돌려준다.
게이트웨이에 닿지 못하면 내장 가상 데이터로 분석한다(오프라인 시연).
기동: uvicorn app.builtin.a2a_agent:app --port 8200  (compose 서비스 a2a-demo, 내부 네트워크 전용)
"""
import json
import re
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.builtin import demo_data
from app.builtin.mcp_auth import verify
from app.core import tracing
from app.core.config import settings
from app.core.http import async_client
from app.services.a2a_proto import build_card
from app.services.a2a_server import A2AServer, TaskContext

AGENT_ID = "observability-analyst"
SERVICES = sorted({p["service"] for p in demo_data.DYNATRACE_PROBLEMS} | {s for s, _ in demo_data.METRICS})
_TOKEN: dict = {}


def card() -> dict:
    return build_card(
        name="관측 분석 에이전트 (Observability Analyst)",
        description="Dynatrace 문제·메트릭을 분석해 장애 원인·영향·조치를 보고하는 원격 A2A 에이전트 (데모)",
        url=settings.a2a_demo_url.rstrip("/") + "/", version="1.0.0", rest_url=settings.a2a_demo_url.rstrip("/") + "/rest",
        skills=[{"id": "incident-analysis", "name": "장애 분석", "description": "서비스 장애의 근본 원인·영향 분석 보고서",
                 "tags": ["sre", "incident", "dynatrace", "장애"], "examples": ["orders-api 장애 원인 분석해줘"]},
                {"id": "metric-trend", "name": "메트릭 추세", "description": "서비스 지연·오류율 추세 요약",
                 "tags": ["metrics", "observability"]}],
        provider={"organization": "한결테크 SRE (가상)", "url": "https://sre.hangyeol.example"})


async def _auth(request: Request) -> dict | None:
    hdr = request.headers.get("authorization", "")
    if not hdr.lower().startswith("bearer "):
        return None
    try:
        return await verify(hdr[7:].strip(), f"a2a:{AGENT_ID}")
    except Exception:  # noqa: BLE001
        return None


# ── 게이트웨이 경유 호출 (워크로드 토큰) ──
def _credentials() -> dict | None:
    if settings.a2a_demo_client_secret not in ("", "CHANGE_ME"):
        return {"client_id": "wl-a2a-demo", "client_secret": settings.a2a_demo_client_secret}
    path = Path(settings.data_dir) / "workloads" / "a2a-demo.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


async def _gateway_token() -> str | None:
    if _TOKEN.get("exp", 0) - 30 > time.time():
        return _TOKEN["token"]
    cred = _credentials()
    if not cred:
        return None
    async with async_client(timeout=10) as http:
        res = await http.post(settings.gateway_url.rstrip("/") + "/idp/token", data={
            "grant_type": "client_credentials", "client_id": cred["client_id"], "client_secret": cred["client_secret"]})
    if res.status_code != 200:
        return None
    body = res.json()
    _TOKEN.update(token=body["access_token"], exp=time.time() + body.get("expires_in", 300))
    return _TOKEN["token"]


async def _mcp(tool: str, args: dict) -> dict | list | None:
    token = await _gateway_token()
    if not token:
        return None
    async with async_client(timeout=20) as http:
        res = await http.post(settings.gateway_url.rstrip("/") + "/mcp/dynatrace", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                     "X-AgentNexus-Agent": f"a2a:{AGENT_ID}"})
    body = res.json() if res.status_code == 200 else {}
    sc = (body.get("result") or {}).get("structuredContent")
    return sc.get("result", sc) if isinstance(sc, dict) and "result" in sc else sc


async def _llm(prompt: str) -> str | None:
    token = await _gateway_token()
    if not token:
        return None
    async with async_client(timeout=30) as http:
        res = await http.post(settings.gateway_url.rstrip("/") + "/v1/chat/completions", json={
            "model": "domain-local", "messages": [
                {"role": "system", "content": "너는 SRE 장애 분석가다. 근거를 요약하라."},
                {"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {token}", "X-AgentNexus-Data-Class": "internal",
                     "X-AgentNexus-Agent": f"a2a:{AGENT_ID}"})
    if res.status_code != 200:
        return None
    return res.json()["choices"][0]["message"]["content"]


def _service_in(text: str, data: dict) -> str | None:
    if data.get("service") in SERVICES:
        return data["service"]
    for s in SERVICES:
        if s in text:
            return s
    lowered = text.lower()
    for key, svc in (("주문", "orders-api"), ("order", "orders-api"), ("결제", "payment-gateway"),
                     ("payment", "payment-gateway"), ("정산", "settlement-batch")):
        if key in lowered:
            return svc
    return None


def _text_and_data(message: dict) -> tuple[str, dict]:
    text = " ".join(p.get("text", "") for p in message.get("parts", []) if p.get("kind") == "text")
    data = next((p.get("data") or {} for p in message.get("parts", []) if p.get("kind") == "data"), {})
    return text, data


async def analyze(ctx: TaskContext) -> None:
    text, data = _text_and_data(ctx.message)
    service = _service_in(text, data)
    if service is None:
        reply = await ctx.ask("어떤 서비스의 장애를 분석할까요? (예: " + ", ".join(SERVICES) + ")")
        if reply is None:
            await ctx.update("failed", "입력 대기 시간이 초과되었습니다.")
            return
        service = _service_in(*_text_and_data(reply))
        if service is None:
            await ctx.update("rejected", "알 수 없는 서비스입니다.")
            return
    await ctx.update("working", f"{service} 의 Dynatrace 문제·메트릭을 조회합니다.")
    via = "gateway"
    problems = await _mcp("list_problems", {"service": service, "status": "ALL"})
    if problems is None:
        via = "offline"
        problems = [p for p in demo_data.DYNATRACE_PROBLEMS if p["service"] == service]
    detail = None
    if problems:
        detail = await _mcp("get_problem", {"problem_id": problems[0]["id"]}) if via == "gateway" else None
        detail = detail or next((p for p in demo_data.DYNATRACE_PROBLEMS if p["id"] == problems[0]["id"]), None)
    metrics = (await _mcp("query_metrics", {"service": service}) if via == "gateway" else None) or \
        {"series": demo_data.METRICS.get((service, "p95_latency_ms"), [])}
    series = metrics.get("series") or []
    trend = f"p95 {series[0]}→{series[-1]}ms" if len(series) >= 2 else "메트릭 없음"
    root = (detail or {}).get("root_cause", "근본 원인 미상")
    impacted = (detail or {}).get("impacted", [])
    summary = await _llm(f"[1] 서비스 {service} 문제 {(detail or {}).get('title', '-')}\n[2] 근본 원인: {root}\n"
                         f"[3] 영향: {', '.join(impacted) or '-'}\n[4] 추세: {trend}") if via == "gateway" else None
    report = (f"# 장애 분석 보고서 — {service}\n\n"
              f"- 문제: {(detail or {}).get('id', '-')} {(detail or {}).get('title', '열린 문제 없음')}\n"
              f"- 근본 원인: {root}\n- 영향 서비스: {', '.join(impacted) or '-'}\n- 메트릭 추세: {trend}\n"
              f"- 권고 조치: {_advice(root)}\n\n## 분석 요약\n{summary or '(오프라인 분석) ' + root}\n\n"
              f"_데이터 경로: {via} (AgentNexus 게이트웨이 경유 여부)_\n")
    await ctx.artifact("incident-report.md", [{"kind": "text", "text": report}], "장애 분석 보고서 (Markdown)")
    await ctx.artifact("incident-data", [{"kind": "data", "data": {
        "service": service, "problem_id": (detail or {}).get("id"), "root_cause": root, "impacted": impacted,
        "series": series, "via": via}}])
    await ctx.update("completed", f"{service} 분석 완료 — 근본 원인: {root}")


def _advice(root: str) -> str:
    if re.search("인덱스", root):
        return "누락 인덱스 생성 마이그레이션 재실행 후 p95 재측정, 필요 시 v2.8 롤백"
    if re.search("인증서", root):
        return "샌드박스 인증서 갱신 및 만료 알림(30일 전) 설정"
    if re.search("중복", root):
        return "배치 분산 락 적용 PR 머지 및 재처리"
    return "추가 로그·트레이스 확인"


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "agent": AGENT_ID})


server = A2AServer(card, analyze, authenticate=_auth, blocking_timeout=30)
app = Starlette(routes=[Route("/.well-known/agent-card.json", server.card_endpoint),
                        Route("/", server.handle, methods=["POST"]),
                        Route("/rest/{path:path}", server.rest, methods=["GET", "POST", "DELETE"]),  # HTTP+JSON 바인딩 (D16-①)
                        Route("/healthz", _health)])
tracing.setup_for_service("agentnexus-a2a-demo")
app = tracing.TracingMiddleware(app, component="a2a-demo")
