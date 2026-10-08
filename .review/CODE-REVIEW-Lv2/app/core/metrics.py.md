> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md)

### 14. app/core/metrics.py
**파일 요약 / File Summary**
- 역할 / Role: Prometheus 카운터·히스토그램 정의, 요청 관측(+SLI), 스크레이프 시점 DB 게이지 수집기, 상태 코드별 합계, `/metrics` 렌더(단일/멀티프로세스).
- 진입점 거리 / Distance: 2 (`app/core/tracing.py:224` → `observe_request`, `app/api/observe.py:47` → `render`)
- LOC: 99
- 주요 export: `REQUESTS`, `LATENCY`, `TOKENS`, `COST`, `LLM_CALLS`, `TOOL_CALLS`, `A2A_CALLS`, `RUNS`, `POLICY_DENIES`, `observe_request`, `DbCollector`, `status_totals`, `render`
- 의존(내부): (지연) `app.services.slo`, `app.db`, `app.models`
- 외부 패키지: prometheus-client
- 부수효과 / Side effects: 전역 레지스트리 등록(import 시 메트릭 생성), 멀티프로세스 모드에서 `PROMETHEUS_MULTIPROC_DIR` 파일 쓰기(라이브러리가 수행)
- 동시성 / Concurrency: prometheus-client 메트릭은 스레드 안전, `render` 의 등록 플래그는 락 없음
- 테스트 커버리지 흔적: `test/test_observe.py`(`/metrics`), `test/test_ops.py`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/metrics.py lines=1-20
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
```
**분석 / Analysis (Level 2)**
- L1-5: uvicorn 다중 워커는 프로세스마다 메모리가 따로라 `PROMETHEUS_MULTIPROC_DIR` 공유 디렉터리(엔트리포인트가 기동마다 비움 — `scripts/entrypoint.sh:4-6`)로 합산한다.
- L11-20: 메트릭 정의. 라벨은 저카디널리티 값만(구성요소·라우트 템플릿·상태·모델·팀 등). `Counter` 이름은 `_total` 접미가 자동으로 붙는다.
**짚어볼 점 / Watch-outs**
- L15 `COST` 에 `team` 라벨 — 팀 수가 많으면 시계열 수가 늘어난다(수십 개 수준이면 무방).
- L17 `TOOL_CALLS` 의 `tool` 라벨은 업스트림이 정한 도구 이름 — 악의적 업스트림이 도구를 수천 개 노출하면 시계열 폭증(카디널리티 공격) 가능.

```python path=app/core/metrics.py lines=23-35
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
```
**분석 / Analysis (Level 2)**
- L23-26 `_route`: 매칭된 라우트가 있으면 템플릿 경로(`/mcp/{server_id}`), 없으면(404·미들웨어 단계) 첫 경로 세그먼트만 — 존재하지 않는 경로를 마구 찔러도 라벨이 폭증하지 않게 하는 방어(좋음).
- L29-35: 요청 수·지연 기록, api 구성요소면 SLI(`app/services/slo.py:46-57`)에도 첫 바이트 지연으로 기록.
**짚어볼 점 / Watch-outs**
- L35 `scope.get("path")` 원본 경로로 SLI 분류 — 분류는 접두만 보므로(`/mcp/`·`/a2a/`) 카디널리티 문제는 없다.

```python path=app/core/metrics.py lines=38-63
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
```
**분석 / Analysis (Level 2)**
- L38-63 `DbCollector`: prometheus-client 의 사용자 정의 수집기 프로토콜(`collect()` 가 MetricFamily 를 yield). 스크레이프마다 DB 를 조회해 승인 대기 수·MCP 서버 헬스·A2A Task 상태 분포·진행 중 실행 수를 게이지로 낸다. `with SessionLocal()` 로 세션을 닫는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 스크레이프 주기(보통 15~30초)마다 4개 집계 쿼리 — 테이블이 커지면 `GROUP BY` 비용이 커진다(`a2a_tasks.state` 인덱스는 있음, `models.py:388`).
- `describe()` 가 없어 `REGISTRY.register(DbCollector())`(L95) 시 라이브러리가 **등록 시점에 `collect()` 를 한 번 호출**해 이름을 알아낸다(auto_describe) — 즉 첫 `/metrics` 요청에서 DB 쿼리가 두 번 돈다.

```python path=app/core/metrics.py lines=66-83
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
```
**분석 / Analysis (Level 2)**
- L66-83 `status_totals`: 프로세스 시작 이후 상태 코드별 요청 합계 — 멀티프로세스면 공유 디렉터리를 읽는 새 레지스트리, 아니면 `REQUESTS` 직접. 운영 개요 화면(401·403·429·5xx 카운터)용.
**짚어볼 점 / Watch-outs**
- L77 `fam.name != "anx_http_requests"` — prometheus-client 가 Counter 패밀리 이름에서 `_total` 을 떼는 동작에 의존한다(라이브러리 버전 변경에 민감).

```python path=app/core/metrics.py lines=86-99
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
```
**분석 / Analysis (Level 2)**
- L86-99 `render`: 멀티프로세스면 요청마다 새 레지스트리 + `MultiProcessCollector` + `DbCollector`. 단일 프로세스면 전역 `REGISTRY` 에 `DbCollector` 를 한 번만 등록(함수 속성 `render._db` 를 플래그로 사용).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 경쟁] L94-96 확인-후-등록이 원자적이지 않다. `/metrics` 는 동기 엔드포인트라 스레드풀에서 동시에 두 요청이 들어오면 둘 다 `_db` 가 없다고 보고 등록 → 두 번째가 `ValueError: Duplicated timeseries` 로 500. 모듈 수준 `threading.Lock` 또는 import 시 1회 등록으로 바꿔야 한다.
- 함수 객체에 속성을 붙이는 플래그는 읽기 어렵다 — 모듈 전역 `_DB_REGISTERED` 가 명확하다.

## 파일 평가 / File Assessment
- 강점: (1) 라우트 템플릿·첫 세그먼트로 라벨 카디널리티 억제. (2) 멀티프로세스 집계 지원. (3) 업무 지표(승인 대기·Task 상태)를 스크레이프 시점 계산으로 정확히.
- 위험: (1) [낮음] DbCollector 등록 경쟁(동시 첫 스크레이프 500). (2) 업스트림 도구 이름 라벨 카디널리티. (3) 라이브러리 내부 명명 규칙 의존.
- 개선 권고(영향순): (1) 등록을 import 시 1회(단일 프로세스 모드) 또는 락으로. (2) `tool` 라벨을 등록된 규칙의 도구로 제한. (3) `DbCollector.describe()` 구현으로 등록 시 DB 쿼리 제거.
- 재읽기 권고 레벨: 3.
