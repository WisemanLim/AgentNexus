> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [api_Abstract.md](api_Abstract.md)

### 35. app/api/observe.py
**파일 요약 / File Summary**
- 역할 / Role: 관측 API — `/metrics`(Prometheus), `/v1/traces`(OTLP 수신기), 트레이스 탐색·워터폴, 로그 검색, 운영 요약·시계열·파이프라인, Usage & Cost 집계, 알림 규칙·평가, SLO·오류 예산 조회·수정·평가.
- 진입점 거리 / Distance: 1 (`app/main.py:26,149`)
- LOC: 471
- 주요 export: `router`, `prometheus`, `otlp_ingest`, `summary_data`, `evaluate`(스케줄러 재사용), `GROUPS`
- 의존(내부): `app.core.{metrics,tracing,auth,config,errors,ids}`, `app.db`, `app.models`, `app.services.{audit,llm,ratelimit,slo,notify}`
- 외부 패키지: fastapi, pydantic, sqlalchemy, opentelemetry-proto(지연), google.protobuf(지연)
- 부수효과 / Side effects: DB 쓰기(스팬 수신·알림 규칙·SLO), 감사, 알림
- 동시성 / Concurrency: async(OTLP 수신) + 동기 조회(스레드풀), 나머지 동기 엔드포인트
- 테스트 커버리지 흔적: `test/test_observe.py`, `test/test_slo.py`, `test/test_hardening.py`(메트릭 토큰)

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/observe.py lines=1-47
"""Observability API (FR-O00~O04·G05 · SCR-B02·H01·K01~K07).

- GET /metrics — Prometheus (Bearer: observe:read 사용자 또는 METRICS_TOKEN)
- POST /v1/traces — OTLP/HTTP 수신기(protobuf·JSON). mcp-servers·a2a-demo 의 스팬을 받아 단일 trace 로 조회
- GET /api/v1/traces · /api/v1/traces/{trace_id} — 트레이스 탐색·워터폴
- GET /api/v1/logs — 구조화 로그 검색 (PII 마스킹 저장)
- GET /api/v1/observe/summary · /pipeline — 운영 개요·게이트웨이 개요·파이프라인 상태
- GET /api/v1/usage — Usage & Cost 집계 (cost:read 는 전체, 그 외 본인)
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, type_coerce
from sqlalchemy.orm import Session

from app.core import metrics, tracing
from app.core.auth import Principal, authenticate_token, bearer, require
from app.core.config import settings
from app.core.errors import ApiError, bad_request, not_found
from app.core.ids import new_id
from app.db import SessionLocal, UTCDateTime, get_db, utcnow
from app.models import A2ATask, AlertRule, Approval, LogEntry, McpServer, RoutingDecision, Run, Slo, Span, UsageRecord
from app.services import audit, llm, ratelimit
from app.services import slo as slo_svc
from app.services.notify import notify

router = APIRouter(tags=["observe"])


# ───────────────────────── /metrics ─────────────────────────
@router.get("/metrics")
def prometheus(request: Request):
    token = bearer(request)
    ok = bool(token) and settings.metrics_token not in ("", "CHANGE_ME") and token == settings.metrics_token
    if not ok and token:
        with SessionLocal() as db:
            try:
                ok = authenticate_token(db, token).has("observe:read")
            except ApiError:
                ok = False
    if not ok:
        raise ApiError(401, "unauthorized", "metrics: observe:read 토큰 또는 METRICS_TOKEN 이 필요합니다.")
    return Response(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")
```
**분석 / Analysis (Level 2)**
- L35-47 `prometheus`: `/metrics` 인증 — `METRICS_TOKEN`(설정, CHANGE_ME 제외) 또는 `observe:read` 사용자 토큰. 둘 다 아니면 401.
**짚어볼 점 / Watch-outs**
- L38 `token == settings.metrics_token` — **상수 시간 비교가 아니다**(`==`). 토큰 비교는 `hmac.compare_digest` 가 안전하다(타이밍 측면, 영향 낮음 — 토큰이 고엔트로피라 실질 위험은 작음).
- L39-44 토큰이 있으면 사용자 인증도 시도 — 잘못된 토큰에 대해 DB 조회가 일어난다(인증 없는 스크레이프 엔드포인트에 DB 부하 유발 가능, 영향 낮음).

```python path=app/api/observe.py lines=50-93
# ───────────────────────── OTLP/HTTP 수신기 ─────────────────────────
_KINDS = {0: "unspecified", 1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer"}


def _anyvalue(v: dict):
    for k in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if k in v:
            return int(v[k]) if k == "intValue" else v[k]
    if "arrayValue" in v:
        return [_anyvalue(x) for x in v["arrayValue"].get("values", [])]
    return None


def _otlp_rows(doc: dict) -> list[dict]:
    import base64
    rows = []

    def hexid(x: str | None) -> str | None:
        if not x:
            return None
        if all(c in "0123456789abcdefABCDEF" for c in x) and len(x) in (16, 32):
            return x.lower()
        return base64.b64decode(x).hex()
    for rs in doc.get("resourceSpans", []):
        res_attrs = {a["key"]: _anyvalue(a.get("value", {})) for a in (rs.get("resource") or {}).get("attributes", [])}
        service = str(res_attrs.get("service.name", "unknown"))[:80]
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                start, end = int(sp.get("startTimeUnixNano", 0)), int(sp.get("endTimeUnixNano", 0))
                kind = sp.get("kind", 1)
                rows.append({"id": hexid(sp["spanId"]), "trace_id": hexid(sp["traceId"]),
                             "parent_id": hexid(sp.get("parentSpanId")), "name": sp.get("name", "")[:255],
                             "service": service,
                             "kind": _KINDS.get(kind, str(kind)).lower() if isinstance(kind, int)
                             else str(kind).replace("SPAN_KIND_", "").lower(),
                             "start_time": datetime.fromtimestamp(start / 1e9, tz=timezone.utc),
                             "end_time": datetime.fromtimestamp(end / 1e9, tz=timezone.utc),
                             "duration_ms": round((end - start) / 1e6, 3),
                             "status": "error" if (sp.get("status") or {}).get("code") in (2, "STATUS_CODE_ERROR")
                             else "ok",
                             "attributes": {a["key"]: _anyvalue(a.get("value", {})) for a in sp.get("attributes", [])},
                             "events": [{"name": e.get("name")} for e in sp.get("events", [])]})
    return rows

```
**분석 / Analysis (Level 2)**
- L51-60 `_anyvalue`: OTLP AnyValue → Python 값.
- L63-92 `_otlp_rows`: OTLP ExportTraceServiceRequest(dict) → 스팬 행. id 는 hex 또는 base64 디코드, 서비스 이름·시각·상태·속성·이벤트.
**짚어볼 점 / Watch-outs**
- L67-72 `hexid`: 16·32자 hex 또는 base64 — 형식이 다르면 `base64.b64decode` 가 예외(수신 처리에서 500). 신뢰된 내부 컨테이너만 보내지만 방어적 처리가 낫다.

```python path=app/api/observe.py lines=95-118
@router.post("/v1/traces", include_in_schema=False)
async def otlp_ingest(request: Request):
    if bearer(request) != tracing.ingest_token():
        return Response(status_code=401)
    body = await request.body()
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        doc = json.loads(body or b"{}")
    else:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        msg = ExportTraceServiceRequest()
        msg.ParseFromString(body)
        doc = MessageToDict(msg)
    rows = _otlp_rows(doc)
    if rows:
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(tracing.store_spans, rows)
        tracing.STATS["ingested"] += len(rows)
        tracing.STATS["last_ingest_at"] = utcnow().isoformat()
    if "json" in ctype:
        return Response(content=b"{}", media_type="application/json")
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
    return Response(content=ExportTraceServiceResponse().SerializeToString(), media_type="application/x-protobuf")
```
**분석 / Analysis (Level 2)**
- L95-118 `otlp_ingest`: 수신 토큰(`tracing.ingest_token`) 확인 → JSON 또는 protobuf 파싱 → 스팬 저장(스레드풀) → 통계 갱신.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L97 `bearer(request) != tracing.ingest_token()` — 상수 시간 비교 아님. 또 토큰이 빈 문자열이면(`ingest_token` 생성 실패 시) `bearer` 가 `None` 이라 `None != ""` 로 거부되지만, 설정 토큰이 실수로 비면 인증이 느슨해질 수 있다(`tracing.ingest_token` 은 파일/자동 생성이라 보통 채워짐).
- L99-108 수신 본문 크기 제한이 없다 — 거대한 OTLP 페이로드가 메모리에 적재된다(내부 서비스 전용이지만 상한 권장).
- 저장 스팬은 `db.merge`(upsert)라 같은 span id 로 기존 스팬을 덮어쓸 수 있다(`app/core/tracing.py:64` 리뷰 참조).

```python path=app/api/observe.py lines=121-173
# ───────────────────────── 트레이스 (SCR-K02·K03) ─────────────────────────
@router.get("/api/v1/traces")
def list_traces(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db),
                service: str | None = None, q: str | None = None, errors_only: bool = False,
                user: str | None = None, tool: str | None = None,
                since_minutes: int = Query(60, le=60 * 24 * 30), limit: int = Query(50, le=500)):
    since = utcnow() - timedelta(minutes=since_minutes)
    stmt = select(Span.trace_id, type_coerce(func.min(Span.start_time), UTCDateTime()),
                  type_coerce(func.max(Span.end_time), UTCDateTime()), func.count(),
                  func.sum(case((Span.status == "error", 1), else_=0))) \
        .where(Span.start_time >= since).group_by(Span.trace_id).order_by(func.min(Span.start_time).desc()).limit(limit)
    if service:
        stmt = stmt.where(Span.trace_id.in_(select(Span.trace_id).where(Span.service == service)))
    if q:
        stmt = stmt.where(Span.trace_id.in_(select(Span.trace_id).where(Span.name.like(f"%{q}%"))))
    if user or tool:  # 사용자·도구 필터 (SCR-K02 ②) — 사용 기록의 trace_id 로
        sub = select(UsageRecord.trace_id).where(UsageRecord.ts >= since)
        if user:
            sub = sub.where(UsageRecord.user_sub == user)
        if tool:
            sub = sub.where(UsageRecord.kind == "tool", UsageRecord.target.like(f"%{tool}%"))
        stmt = stmt.where(Span.trace_id.in_(sub))
    out = []
    for trace_id, start, end, n, errs in db.execute(stmt):
        if errors_only and not errs:
            continue
        root = db.scalars(select(Span).where(Span.trace_id == trace_id).order_by(Span.start_time)).first()
        services = sorted({s for (s,) in db.execute(select(Span.service).where(Span.trace_id == trace_id).distinct())})
        out.append({"trace_id": trace_id, "root": root.name if root else None, "start": start,
                    "duration_ms": round((end - start).total_seconds() * 1000, 1) if end and start else None,
                    "spans": n, "errors": int(errs or 0), "services": services,
                    "user": (root.attributes or {}).get("enduser.id") if root else None})
    return {"items": out}


@router.get("/api/v1/traces/{trace_id}")
def get_trace(trace_id: str, _: Principal = Depends(require("observe:read")), db: Session = Depends(get_db)):
    spans = db.scalars(select(Span).where(Span.trace_id == trace_id).order_by(Span.start_time)).all()
    if not spans:
        raise not_found("trace")
    t0 = min(s.start_time for s in spans)
    rows = [{"span_id": s.id, "parent_id": s.parent_id, "name": s.name, "service": s.service, "kind": s.kind,
             "status": s.status, "start_offset_ms": round((s.start_time - t0).total_seconds() * 1000, 3),
             "duration_ms": float(s.duration_ms), "attributes": s.attributes, "events": s.events} for s in spans]
    ids = {r["span_id"] for r in rows}
    roots = [r for r in rows if not r["parent_id"] or r["parent_id"] not in ids]
    gen_ai = [r for r in rows if (r["attributes"] or {}).get("gen_ai.operation.name")]
    usage = db.scalars(select(UsageRecord).where(UsageRecord.trace_id == trace_id)).all()
    return {"trace_id": trace_id, "spans": rows, "roots": [r["span_id"] for r in roots],
            "services": sorted({r["service"] for r in rows}),
            "duration_ms": round((max(s.end_time for s in spans) - t0).total_seconds() * 1000, 3),
            "gen_ai_spans": len(gen_ai), "cost_usd": float(sum((u.cost for u in usage), Decimal(0))),
            "tokens": sum(u.tokens_in + u.tokens_out for u in usage)}
```
**분석 / Analysis (Level 2)**
- L122-153 트레이스 목록(`observe:read`): 기간·서비스·검색·오류·사용자·도구 필터, trace 별 집계.
- L156-173 트레이스 상세: 스팬 워터폴·GenAI 스팬·사용량·비용.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 성능] L144-152 trace 마다 루트 스팬·서비스 목록을 **추가 쿼리**(N+1) — limit 50 이면 trace 당 2쿼리 = 100+쿼리. 대량 트레이스에서 느리다.
- L134-135·L141 `q`·`tool` 을 `like(f"%{q}%")` 로 — 와일드카드 해석·풀스캔(LIKE 패턴 미이스케이프).

```python path=app/api/observe.py lines=176-193
# ───────────────────────── 로그 (SCR-K04) ─────────────────────────
@router.get("/api/v1/logs")
def search_logs(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db),
                level: str | None = None, component: str | None = None, q: str | None = None,
                trace_id: str | None = None, since_minutes: int = Query(1440, le=60 * 24 * 90),
                limit: int = Query(200, le=2000)):
    stmt = select(LogEntry).where(LogEntry.ts >= utcnow() - timedelta(minutes=since_minutes)) \
        .order_by(LogEntry.id.desc()).limit(limit)
    if level:
        stmt = stmt.where(LogEntry.level == level.upper())
    if component:
        stmt = stmt.where(LogEntry.component == component)
    if trace_id:
        stmt = stmt.where(LogEntry.trace_id == trace_id)
    if q:
        stmt = stmt.where(or_(LogEntry.message.like(f"%{q}%"), LogEntry.logger.like(f"%{q}%")))
    return {"items": [{"id": e.id, "ts": e.ts, "level": e.level, "component": e.component, "logger": e.logger,
                       "message": e.message, "trace_id": e.trace_id, "data": e.data} for e in db.scalars(stmt)]}
```
**분석 / Analysis (Level 2)**
- L177-193 로그 검색(`observe:read`): 기간·수준·구성요소·trace·검색.
**짚어볼 점 / Watch-outs**
- 로그는 저장 시 PII 마스킹됨(`app/core/logs.py`) — 검색 결과에 원문 PII 는 없다(마스킹 해제는 별도 사유·권한 경로).

```python path=app/api/observe.py lines=196-247
# ───────────────────────── 요약 (SCR-B02·H01·K05) ─────────────────────────
def _p95(values: list[int]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return float(v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))])


def summary_data(db: Session, minutes: int) -> dict:
    since = utcnow() - timedelta(minutes=minutes)
    usage = db.scalars(select(UsageRecord).where(UsageRecord.ts >= since)).all()
    by_kind: dict[str, dict] = {}
    for u in usage:
        k = by_kind.setdefault(u.kind, {"calls": 0, "errors": 0, "latency": [], "tokens": 0, "cost": Decimal(0)})
        k["calls"] += 1
        k["errors"] += 1 if u.status not in ("ok",) else 0
        k["latency"].append(u.latency_ms)
        k["tokens"] += u.tokens_in + u.tokens_out
        k["cost"] += u.cost
    kinds = {k: {"calls": v["calls"], "errors": v["errors"], "error_rate": round(v["errors"] / v["calls"], 4),
                 "p95_latency_ms": _p95(v["latency"]), "tokens": v["tokens"], "cost_usd": float(v["cost"])}
             for k, v in by_kind.items()}
    total = len(usage)
    decisions = db.scalars(select(RoutingDecision).where(RoutingDecision.ts >= since)).all()
    top_models: dict[str, Decimal] = {}
    for u in usage:
        if u.kind == "model":
            top_models[u.target] = top_models.get(u.target, Decimal(0)) + u.cost
    teams: dict[str, Decimal] = {}
    for u in usage:
        teams[u.team or "-"] = teams.get(u.team or "-", Decimal(0)) + u.cost
    servers = db.scalars(select(McpServer).where(McpServer.status == "active")).all()
    return {
        "window_minutes": minutes,
        "qps": round(total / (minutes * 60), 4),
        "calls": total, "by_kind": kinds,
        "error_rate": round(sum(v["errors"] for v in kinds.values()) / total, 4) if total else 0.0,
        "tokens": sum(v["tokens"] for v in kinds.values()),
        "cost_usd": float(sum((u.cost for u in usage), Decimal(0))),
        "fallback_rate": round(sum(1 for d in decisions if d.fallback_used) / len(decisions), 4) if decisions else 0.0,
        "top_models": [{"model": m, "cost_usd": float(c)} for m, c in sorted(top_models.items(),
                                                                              key=lambda x: -x[1])[:5]],
        "cost_by_team": [{"team": t, "cost_usd": float(c)} for t, c in sorted(teams.items(), key=lambda x: -x[1])],
        "approvals_pending": db.scalar(select(func.count()).select_from(Approval).where(Approval.status == "pending")),
        "runs": {s: n for s, n in db.execute(select(Run.status, func.count()).where(Run.created_at >= since)
                                             .group_by(Run.status))},
        "a2a_tasks": {s: n for s, n in db.execute(select(A2ATask.state, func.count()).group_by(A2ATask.state))},
        "mcp_servers": {"total": len(servers), "healthy": sum(1 for s in servers if s.health == "healthy")},
        "providers": llm.provider_health(), "rate_limit_backend": ratelimit.backend_status(),
        "spans": db.scalar(select(func.count()).select_from(Span).where(Span.start_time >= since)),
        **_overview_extra(db, since),
    }
```
**분석 / Analysis (Level 2)**
- L197-201 `_p95`. L204-247 `summary_data`: 사용량 기반 종류별 호출·오류·지연·토큰·비용, QPS, 폴백율, 상위 모델·팀 비용, 승인 대기·실행·Task·서버·공급자·백엔드·스팬 + 운영 KPI.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 성능] L206 `select(UsageRecord).where(ts >= since)` 로 기간 내 **모든 사용 기록을 메모리로** 읽어 Python 집계한다(L209-226 다중 순회). 기본 창 60분이지만 알림 평가(L463)가 창을 `window_s // 60` 로 부르고, 긴 창(최대 31일, L279)이면 수십만 행을 적재한다. SQL `GROUP BY` 집계로 옮겨야 한다.
- L254-257 `_overview_extra` 는 스팬 500개마다 자식 스팬을 추가 조회(N+1) — 게이트웨이 추가 지연 계산 비용이 크다.

```python path=app/api/observe.py lines=250-305
def _overview_extra(db: Session, since) -> dict:
    """운영 개요 KPI (SCR-B02 ②⑤) — 게이트웨이 추가 지연 p95(도구 호출 스팬 − 업스트림 자식 스팬)·첫 토큰 p95·팀 예산·등록 대기."""
    from app.models import CatalogVersion, RateLimit, RunEvent
    over = []
    for sp in db.scalars(select(Span).where(Span.start_time >= since, Span.name.like("%tools/call%")).limit(500)):
        kids = [float(c.duration_ms) for c in db.scalars(select(Span).where(Span.parent_id == sp.id, Span.trace_id == sp.trace_id))]
        if kids:
            over.append(max(0.0, float(sp.duration_ms) - max(kids)))
    first: list[float] = []
    starts: dict[str, object] = {}
    for ev in db.scalars(select(RunEvent).where(RunEvent.ts >= since, RunEvent.type.in_(("run.started", "delta")))
                         .order_by(RunEvent.id)):
        if ev.type == "run.started":
            starts[ev.run_id] = ev.ts
        elif ev.run_id in starts:
            first.append((ev.ts - starts.pop(ev.run_id)).total_seconds() * 1000)
    budgets = []
    for lim in db.scalars(select(RateLimit).where(RateLimit.enabled.is_(True), RateLimit.budget_usd.is_not(None),
                                                  RateLimit.scope_type == "team")):
        used = ratelimit.spend(db, "team", lim.scope, lim.period)
        budgets.append({"team": lim.scope, "name": lim.name, "used_usd": float(used), "budget_usd": float(lim.budget_usd),
                        "pct": round(float(used / lim.budget_usd * 100), 1) if lim.budget_usd else 0.0})
    return {"gateway_overhead_p95_ms": _p95([int(x) for x in over]), "first_token_p95_ms": _p95([int(x) for x in first]),
            "team_budgets": sorted(budgets, key=lambda b: -b["pct"]),
            "registry_pending": db.scalar(select(func.count()).select_from(CatalogVersion).where(CatalogVersion.status == "pending"))}


@router.get("/api/v1/observe/summary")
def summary(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db),
            window_minutes: int = Query(60, ge=1, le=60 * 24 * 31)):
    return summary_data(db, window_minutes)


@router.get("/api/v1/observe/timeseries")
def timeseries(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db),
               window_minutes: int = Query(60, ge=5, le=60 * 24 * 7), buckets: int = Query(12, ge=2, le=96)):
    """프로토콜별 호출 추이 (SCR-B02 ④) — 구간별 LLM·MCP·A2A 호출·오류·비용. 엔진 무관하게 앱에서 버킷팅."""
    now = utcnow()
    start = now - timedelta(minutes=window_minutes)
    step = (now - start) / buckets
    rows = [{"t": start + step * i, "model": 0, "tool": 0, "remote_agent": 0, "errors": 0, "cost_usd": 0.0}
            for i in range(buckets)]
    lat: list[list[int]] = [[] for _ in range(buckets)]
    for ts, kind, status, cost, ms in db.execute(select(UsageRecord.ts, UsageRecord.kind, UsageRecord.status,
                                                        UsageRecord.cost, UsageRecord.latency_ms).where(UsageRecord.ts >= start)):
        i = min(int((ts - start) / step), buckets - 1)
        r = rows[i]
        if ms:
            lat[i].append(int(ms))
        if kind in ("model", "tool", "remote_agent"):
            r[kind] += 1
        r["errors"] += 0 if status == "ok" else 1
        r["cost_usd"] += float(cost or 0)
    for r, xs in zip(rows, lat):  # 구간별 지연 p95 (SCR-K05 ③)
        r["p95_latency_ms"] = sorted(xs)[max(0, int(len(xs) * 0.95 + 0.5) - 1)] if xs else None
    return {"window_minutes": window_minutes, "bucket_seconds": int(step.total_seconds()), "items": rows}
```
**분석 / Analysis (Level 2)**
- L250-274 `_overview_extra`: 게이트웨이 추가 지연 p95·첫 토큰 p95·팀 예산·등록 대기.
- L277-305 요약·시계열 엔드포인트.
**짚어볼 점 / Watch-outs**
- L260-265 `run.started`→`delta` 이벤트로 첫 토큰 시간 — RunEvent 를 기간만큼 읽어 Python 처리(위와 같은 메모리 패턴).

```python path=app/api/observe.py lines=308-367
@router.get("/api/v1/observe/pipeline")
def pipeline(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db)):
    """관측 파이프라인 상태 (SCR-K01)."""
    since = utcnow() - timedelta(hours=1)
    per_service = {s: n for s, n in db.execute(select(Span.service, func.count()).where(Span.start_time >= since)
                                               .group_by(Span.service))}
    return {"exporters": {"builtin_store": {"exported": tracing.STATS["db_exported"],
                                            "failed": tracing.STATS["db_failed"]},
                          "otlp": {"configured": tracing.STATS["otlp_configured"], "endpoint": settings.otlp_endpoint
                                   or None}},
            "receiver": {"endpoint": "/v1/traces", "ingested": tracing.STATS["ingested"],
                         "last_ingest_at": tracing.STATS["last_ingest_at"]},
            "spans_last_hour": per_service, "service": tracing.STATS["service"],
            "logs_last_hour": db.scalar(select(func.count()).select_from(LogEntry).where(LogEntry.ts >= since)),
            "semconv": "OpenTelemetry GenAI (gen_ai.*) · W3C Trace Context"}


# ───────────────────────── Usage & Cost (FR-O04, SCR-K06) ─────────────────────────
GROUPS = {"model": UsageRecord.target, "tool": UsageRecord.target, "agent": UsageRecord.agent,
          "team": UsageRecord.team, "user": UsageRecord.user_sub, "kind": UsageRecord.kind,
          "provider": UsageRecord.provider}


@router.get("/api/v1/usage")
def usage(p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db),
          group_by: str = Query("model", pattern="^(model|tool|agent|team|user|kind|provider|day)$"),
          kind: str | None = None, team: str | None = None, since: datetime | None = None,
          until: datetime | None = None):
    """비용 집계 — cost:read 권한자는 전체·팀 필터, 그 외는 본인 사용분만 (팀 간 누출 방지)."""
    since = since or utcnow() - timedelta(days=30)
    stmt = select(UsageRecord).where(UsageRecord.ts >= since)
    if until:
        stmt = stmt.where(UsageRecord.ts <= until)
    if not p.has("cost:read"):
        stmt = stmt.where(UsageRecord.user_sub == p.sub)
    elif team:
        stmt = stmt.where(UsageRecord.team == team)
    if group_by == "model":
        stmt = stmt.where(UsageRecord.kind == "model")
    elif group_by == "tool":
        stmt = stmt.where(UsageRecord.kind == "tool")
    if kind:
        stmt = stmt.where(UsageRecord.kind == kind)
    rows: dict[str, dict] = {}
    for u in db.scalars(stmt):
        key = u.ts.date().isoformat() if group_by == "day" else (getattr(u, GROUPS[group_by].key) or "-")
        r = rows.setdefault(key, {"key": key, "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": Decimal(0),
                                  "latency": 0, "errors": 0})
        r["calls"] += 1
        r["tokens_in"] += u.tokens_in
        r["tokens_out"] += u.tokens_out
        r["cost"] += u.cost
        r["latency"] += u.latency_ms
        r["errors"] += 0 if u.status == "ok" else 1
    items = [{**{k: v for k, v in r.items() if k not in ("cost", "latency")}, "cost_usd": float(r["cost"]),
              "avg_latency_ms": round(r["latency"] / r["calls"], 1)} for r in rows.values()]
    items.sort(key=lambda r: -r["cost_usd"])
    total = sum((r["cost"] for r in rows.values()), Decimal(0))
    return {"group_by": group_by, "scope": "all" if p.has("cost:read") else "self", "items": items,
            "total_cost_usd": float(total), "currency": "USD"}
```
**분석 / Analysis (Level 2)**
- L308-322 파이프라인 상태.
- L326-367 Usage & Cost 집계(`chat:use`): **cost:read 는 전체·팀 필터, 그 외 본인만**(팀 간 누출 방지), group_by 별 집계.
**짚어볼 점 / Watch-outs**
- L341-342 권한 없는 사용자는 `user_sub == p.sub` 으로 강제 — 크로스테넌트 비용 누출 차단(좋음).
- L351-361 여기도 전체 행 메모리 집계(기본 30일, L337) — 대량 데이터에서 느리다.
- L353 `getattr(u, GROUPS[group_by].key)` — group_by 는 pattern 으로 제한(L333)되어 임의 속성 접근은 없다.

```python path=app/api/observe.py lines=370-471
# ───────────────────────── 알림 규칙 (SCR-K05) ─────────────────────────
class AlertIn(BaseModel):
    name: str
    metric: str = Field(pattern="^(error_rate|p95_latency_ms|cost_usd|qps|fallback_rate|approvals_pending)$")
    op: str = Field(">", pattern="^(>|<|>=|<=)$")
    threshold: float
    window_s: int = Field(300, ge=60, le=86400)
    severity: str = Field("warn", pattern="^(info|warn|danger)$")
    enabled: bool = True


@router.get("/api/v1/observe/alerts")
def list_alerts(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db)):
    return {"items": [{"id": a.id, "name": a.name, "metric": a.metric, "op": a.op, "threshold": float(a.threshold),
                       "window_s": a.window_s, "severity": a.severity, "enabled": a.enabled,
                       "last_fired_at": a.last_fired_at} for a in db.scalars(select(AlertRule))]}


@router.post("/api/v1/observe/alerts", status_code=201)
def create_alert(body: AlertIn, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    a = AlertRule(id=new_id("alr"), **body.model_dump())
    db.add(a)
    db.commit()
    audit.append("observe.alert.create", actor=p.sub, target=a.name, data=body.model_dump())
    return {"id": a.id, **body.model_dump()}


def _metric_value(data: dict, metric: str) -> float:
    if metric == "p95_latency_ms":
        vals = [v["p95_latency_ms"] for v in data["by_kind"].values() if v["p95_latency_ms"] is not None]
        return max(vals) if vals else 0.0
    return float(data.get(metric) or 0)


# ───────── SLO · 오류 예산 (D15-④) ─────────
class SloPatch(BaseModel):
    target: float | None = Field(None, ge=0.9, le=0.99999, description="목표 (0.9 ~ 0.99999)")
    threshold_ms: int | None = Field(None, description=f"지연 SLO 기준 — {slo_svc.BOUNDS} 중 하나")
    window_days: int | None = Field(None, ge=7, le=90)
    enabled: bool | None = None
    reason: str = Field(min_length=1, max_length=500, description="변경 사유 (감사)")


def _slo_or_404(db: Session, slo_id: str):
    row = db.get(Slo, slo_id)
    if row is None:
        raise not_found("slo")
    return row


@router.get("/api/v1/observe/slos")
def list_slos(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db)):
    from app.services.scheduler import WORKER
    slo_svc.flush(WORKER)  # 이 워커의 최근 카운터까지
    return {"items": [slo_svc.report(db, s) for s in db.scalars(select(Slo).order_by(Slo.id))],
            "thresholds": list(slo_svc.BOUNDS), "scopes": ["all", *slo_svc.CLASSES],
            "alerting": {"page_burn": slo_svc.PAGE_BURN, "ticket_burn": slo_svc.TICKET_BURN, "min_events": slo_svc.MIN_EVENTS}}


@router.patch("/api/v1/observe/slos/{slo_id}")
def patch_slo(slo_id: str, body: SloPatch, p: Principal = Depends(require("admin:write")), db: Session = Depends(get_db)):
    row = _slo_or_404(db, slo_id)
    if body.threshold_ms is not None:
        if row.kind != "latency" or body.threshold_ms not in slo_svc.BOUNDS:
            raise bad_request(f"threshold_ms 는 지연 SLO 에만, {list(slo_svc.BOUNDS)} 중 하나", code="invalid_threshold")
    before = {k: getattr(row, k) for k in ("target", "threshold_ms", "window_days", "enabled")}
    for k in ("target", "threshold_ms", "window_days", "enabled"):
        if getattr(body, k) is not None:
            setattr(row, k, getattr(body, k))
    row.updated_by, row.updated_at = p.sub, utcnow()
    db.commit()
    audit.append("observe.slo.update", actor=p.sub, target=slo_id,
                 data={"before": before, "after": {k: getattr(row, k) for k in before}, "reason": body.reason})
    return slo_svc.report(db, row)


@router.post("/api/v1/observe/slos/evaluate")
def evaluate_slos(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db)):
    from app.services.scheduler import WORKER
    slo_svc.flush(WORKER)
    return {"changed": slo_svc.evaluate(db)}


@router.post("/api/v1/observe/alerts/evaluate")
def evaluate_alerts(_: Principal = Depends(require("observe:read")), db: Session = Depends(get_db)):
    return evaluate(db)


def evaluate(db: Session) -> dict:
    """경보 규칙 평가 — 콘솔 "지금 평가" 와 예약 작업(services/scheduler)이 함께 쓴다 (FR-O03)."""
    fired = []
    ops = {">": float.__gt__, "<": float.__lt__, ">=": float.__ge__, "<=": float.__le__}
    for a in db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))):
        value = _metric_value(summary_data(db, max(1, a.window_s // 60)), a.metric)
        if ops[a.op](float(value), float(a.threshold)):
            a.last_fired_at = utcnow()
            fired.append({"id": a.id, "name": a.name, "value": value})
            notify("alert", f"알림: {a.name}", f"{a.metric} {a.op} {a.threshold} (현재 {value})", severity=a.severity,
                   link="/console/observe/metrics", audience_role="operator", dedupe_key=f"alert:{a.id}",
                   dedupe_window_s=a.window_s)
    db.commit()
    return {"fired": fired}
```
**분석 / Analysis (Level 2)**
- L371-394 알림 규칙 CRUD(생성은 `admin:write`).
- L397-401 `_metric_value`. L404-451 SLO 조회·수정(`admin:write`, 사유 필수)·평가.
- L453-472 알림 평가 — 규칙마다 요약을 계산해 임계 비교·발화·알림.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 성능] L462-463 `evaluate` 는 활성 규칙 **마다** `summary_data(db, window)` 를 호출한다 — 규칙 N 개면 사용 기록을 N 번 읽어 집계한다. 스케줄러가 주기마다 부르므로(`app/services/scheduler.py:107`) 규칙·데이터가 늘면 비용이 급증한다. 요약을 1회 계산해 공유해야 한다.
- L441 SLO 수정 감사에 before/after·사유 기록(좋음).
- L447·L454 평가 엔드포인트는 `observe:read` — 누구나 평가를 트리거해 알림을 발생시킬 수 있다(중복 억제 키로 완화되지만 부하 유발).

## 파일 평가 / File Assessment
- 강점: (1) `/metrics`·OTLP 수신·Usage 의 인증·크로스테넌트 격리. (2) SLO 수정 사유·감사. (3) OTLP 수신기로 다중 서비스 단일 trace.
- 위험: (1) [중간] 요약·시계열·Usage·알림 평가가 기간 내 전체 행을 메모리로 집계(성능·메모리). (2) [낮음] 토큰 비교 비상수 시간, OTLP 수신 크기 제한 부재·스팬 upsert 덮어쓰기. (3) [낮음] N+1 쿼리·LIKE 미이스케이프.
- 개선 권고(영향순): (1) 집계를 SQL `GROUP BY`·사전 집계 테이블(SliMinute 유사)로, 알림 평가는 요약 1회 공유. (2) OTLP 수신 크기 상한·스팬 INSERT-only. (3) 토큰 `compare_digest`·LIKE 이스케이프.
- 재읽기 권고 레벨: 3 — 집계 성능이 주 관심사.
