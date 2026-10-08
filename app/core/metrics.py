"""Prometheus 메트릭 (FR-O03·G05) — QPS·지연·오류·토큰·비용·도구 호출·Task 상태.

uvicorn 다중 워커는 PROMETHEUS_MULTIPROC_DIR 가 있으면 multiprocess 모드로 집계한다.
DB 기반 게이지(승인 대기·서버 헬스·Task 상태 분포)는 스크레이프 시점에 계산한다.
"""
import os

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily

REQUESTS = Counter("anx_http_requests_total", "HTTP 요청 수", ["component", "route", "status"])
LATENCY = Histogram("anx_http_request_duration_seconds", "HTTP 요청 지연", ["component", "route"],
                    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
TOKENS = Counter("anx_llm_tokens_total", "LLM 토큰", ["model", "direction"])
COST = Counter("anx_llm_cost_usd_total", "LLM 비용(USD)", ["model", "team"])
LLM_CALLS = Counter("anx_llm_requests_total", "LLM 호출", ["model", "status", "fallback"])
TOOL_CALLS = Counter("anx_mcp_tool_calls_total", "MCP 도구 호출", ["server", "tool", "status"])
A2A_CALLS = Counter("anx_a2a_requests_total", "A2A 호출", ["agent", "op", "status"])
RUNS = Counter("anx_runs_total", "오케스트레이션 실행 종료", ["profile", "status"])
POLICY_DENIES = Counter("anx_policy_denies_total", "정책·거버넌스 거부", ["kind", "code"])


def _route(scope) -> str:
    r = scope.get("route")
    path = getattr(r, "path", None) or scope.get("path", "")
    return path if r is not None else "/" + path.strip("/").split("/")[0]


def observe_request(component: str, scope, status: int, seconds: float, first_byte_s: float | None = None) -> None:
    route = _route(scope)
    REQUESTS.labels(component, route, str(status)).inc()
    LATENCY.labels(component, route).observe(seconds)
    if component == "api":  # 게이트웨이 데이터 경로 SLI (참조 서버·데모 에이전트 제외)
        from app.services import slo
        slo.observe(scope.get("path", ""), status, first_byte_s if first_byte_s is not None else seconds)


class DbCollector:
    """스크레이프 시점 DB 게이지."""

    def collect(self):
        from sqlalchemy import func, select

        from app.db import SessionLocal
        from app.models import A2ATask, Approval, McpServer, Run
        with SessionLocal() as db:
            g = GaugeMetricFamily("anx_approvals_pending", "사람 승인 대기 수")
            g.add_metric([], db.scalar(select(func.count()).select_from(Approval)
                                       .where(Approval.status == "pending")) or 0)
            yield g
            h = GaugeMetricFamily("anx_mcp_server_healthy", "MCP 서버 헬스(1=healthy)", labels=["server"])
            for s in db.scalars(select(McpServer).where(McpServer.status == "active")):
                h.add_metric([s.id], 1 if s.health == "healthy" else 0)
            yield h
            t = GaugeMetricFamily("anx_a2a_tasks", "A2A Task 상태 분포", labels=["state"])
            for state, n in db.execute(select(A2ATask.state, func.count()).group_by(A2ATask.state)):
                t.add_metric([state], n)
            yield t
            r = GaugeMetricFamily("anx_runs_active", "진행·승인 대기 실행", labels=["status"])
            for status, n in db.execute(select(Run.status, func.count()).where(
                    Run.status.in_(("queued", "running", "waiting_approval"))).group_by(Run.status)):
                r.add_metric([status], n)
            yield r


def status_totals() -> dict[str, int]:
    """프로세스 시작 이후 HTTP 상태별 요청 수(워커 합산) — SCR-H01 401·403·429·5xx 카운터."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        fams = registry.collect()
    else:
        fams = REQUESTS.collect()
    out: dict[str, int] = {}
    for fam in fams:
        if fam.name != "anx_http_requests":
            continue
        for s in fam.samples:
            if s.name.endswith("_total"):
                st = s.labels.get("status", "")
                out[st] = out.get(st, 0) + int(s.value)
    return out


def render() -> bytes:
    registry = CollectorRegistry()
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess
        multiprocess.MultiProcessCollector(registry)
    else:
        from prometheus_client import REGISTRY
        registry = REGISTRY
        if not getattr(render, "_db", False):
            REGISTRY.register(DbCollector())
            render._db = True
        return generate_latest(registry)
    registry.register(DbCollector())
    return generate_latest(registry)
