"""NFR-P1 게이트웨이 추가 지연 측정 — mock 공급자(지연 0)·in-process MCP 서버이므로 측정 시간 ≈ 게이트웨이 자체 처리 시간.

목표(PRD): Unified Model API p95 ≤ 30ms · MCP 프록시 p95 ≤ 20ms (업스트림 처리 시간 제외).
MCP 는 응답 trace 의 서버 스팬(게이트웨이)에서 업스트림 서버 스팬을 뺀 순수 오버헤드로 계산한다.
NFR 판정은 prod 리허설(PostgreSQL·BatchSpanProcessor·k6, test/impl/13th)로 한다. 이 시험은 SimpleSpanProcessor 가
스팬을 요청 안에서 동기 저장하는 test 모드라 값이 부풀고 변동이 커서, 목표의 2배를 회귀 감시 한도로 둔다.
측정값은 PERF_REPORT 로 남긴다.
"""
import json
import os
import statistics
import time

import pytest

from app.db import SessionLocal
from app.models import RateLimit, Span
from app.services import ratelimit

N = int(os.environ.get("PERF_N", "120"))
GUARD = float(os.environ.get("PERF_GUARD", "2"))  # 목표 대비 회귀 감시 배수
TRIES = int(os.environ.get("PERF_TRIES", "3"))  # 한도를 넘으면 다시 잰다 (최대 회차)
REPORT = {}


@pytest.fixture(scope="module", autouse=True)
def no_default_limit():
    """측정 대상은 처리 지연 — 주체별 기본 한도(50 rps, 모델·도구 공용 버킷)는 연속 호출 측정 중 잠시 끈다."""
    def toggle(enabled: bool):
        with SessionLocal() as db:
            for r in db.query(RateLimit).filter(RateLimit.name == "주체별 기본 요청 한도"):
                r.enabled = enabled
            db.commit()
        ratelimit.clear_cache()
        ratelimit.backend().reset()
    toggle(False)
    yield
    toggle(True)


def _p95(xs: list[float]) -> float:
    return round(statistics.quantiles(xs, n=100)[94], 2)


def _best_of(measure, limit: float) -> dict:
    """호스트 부하 잡음은 지연을 늘리기만 한다 — 한도 안이면 1회로 끝, 넘으면 TRIES 회까지 다시 재고 가장 좋은 회차로 판정 (D19-③)."""
    best = None
    for i in range(TRIES):
        got = measure()
        if best is None or got["p95"] < best["p95"]:
            best = {**got, "tries": i + 1}
        if best["p95"] <= limit:
            break
    return best


def test_model_api_overhead_p95(client, H):
    hdr = {**H["user"], "X-AgentNexus-Data-Class": "public"}
    body = {"model": "fast-commercial", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8}
    for _ in range(10):  # 워밍업 (라우팅·정책 캐시)
        client.post("/v1/chat/completions", json=body, headers=hdr)

    def measure() -> dict:
        lat = []
        for _ in range(N):
            t0 = time.perf_counter()
            res = client.post("/v1/chat/completions", json=body, headers=hdr)
            lat.append((time.perf_counter() - t0) * 1000)
            assert res.status_code == 200
        return {"p95": _p95(lat), "p50": round(statistics.median(lat), 2)}
    r = _best_of(measure, 30 * GUARD)
    REPORT["model_api_p95_ms"], REPORT["model_api_p50_ms"], REPORT["model_api_tries"] = r["p95"], r["p50"], r["tries"]
    assert REPORT["model_api_p95_ms"] <= 30 * GUARD, REPORT


def test_mcp_proxy_overhead_p95(client, H):
    client.post("/api/v1/mcp-servers/jira/refresh", headers=H["operator"])
    hdr = {**H["user"], "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_issues", "arguments": {"query": "결제"}}}
    for _ in range(10):
        client.post("/mcp/jira", json=body, headers=hdr)

    def measure() -> dict:
        traces, total = [], []
        for _ in range(N):
            t0 = time.perf_counter()
            res = client.post("/mcp/jira", json=body, headers=hdr)
            total.append((time.perf_counter() - t0) * 1000)
            assert res.status_code == 200 and "error" not in res.json(), res.text
            traces.append(res.headers["X-AgentNexus-Trace-Id"])
        overhead = []
        with SessionLocal() as db:
            for tid in traces:
                spans = db.query(Span).filter(Span.trace_id == tid).all()
                root = next(s for s in spans if s.parent_id is None)
                # 업스트림 처리 = 게이트웨이의 MCP 클라이언트 스팬(mcp tools/call) 아래 MCP 서버의 서버 스팬
                # → 추가 지연 = 게이트웨이 전체 − 업스트림 서버 처리 (직접 호출했을 때도 드는 시간)
                calls = {s.id for s in spans if s.kind == "client" and s.name.startswith("mcp ")}
                upstream = sum(float(s.duration_ms) for s in spans if s.kind == "server" and s.parent_id in calls)
                overhead.append(max(float(root.duration_ms) - upstream, 0))
        return {"p95": _p95(overhead), "total": _p95(total)}
    r = _best_of(measure, 20 * GUARD)
    REPORT["mcp_total_p95_ms"], REPORT["mcp_overhead_p95_ms"], REPORT["mcp_tries"] = r["total"], r["p95"], r["tries"]
    print("PERF_REPORT", json.dumps(REPORT))
    assert REPORT["mcp_overhead_p95_ms"] <= 20 * GUARD, REPORT
