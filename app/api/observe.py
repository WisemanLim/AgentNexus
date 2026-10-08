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
from sqlalchemy import String, case, cast, func, or_, select, type_coerce
from sqlalchemy.orm import Session, aliased

from app.core import metrics, tracing
from app.core.auth import Principal, authenticate_token, bearer, require
from app.core.config import settings
from app.core.errors import ApiError, bad_request, not_found
from app.core.http import read_capped
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


OTLP_MAX_BYTES = 8 * 1024 * 1024


@router.post("/v1/traces", include_in_schema=False)
async def otlp_ingest(request: Request):
    if bearer(request) != tracing.ingest_token():
        return Response(status_code=401)
    body = await read_capped(request, OTLP_MAX_BYTES)  # 수신 상한 (D21-⑤) — 다 읽기 전에 (59th)
    if body is None:
        return Response(status_code=413)
    ctype = request.headers.get("content-type", "")
    try:
        if "json" in ctype:
            doc = json.loads(body or b"{}")
        else:
            from google.protobuf.json_format import MessageToDict
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
            msg = ExportTraceServiceRequest()
            msg.ParseFromString(body)
            doc = MessageToDict(msg)
        rows = _otlp_rows(doc)  # 구조가 다른 JSON 도 400 (59th)
    except Exception:  # noqa: BLE001 — JSON·protobuf 형식 오류는 500 이 아니라 400
        return Response(status_code=400)
    if rows:
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(tracing.store_spans, rows)
        tracing.STATS["ingested"] += len(rows)
        tracing.STATS["last_ingest_at"] = utcnow().isoformat()
    if "json" in ctype:
        return Response(content=b"{}", media_type="application/json")
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
    return Response(content=ExportTraceServiceResponse().SerializeToString(), media_type="application/x-protobuf")


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


# ───────────────────────── 요약 (SCR-B02·H01·K05) ─────────────────────────
def _p95(values: list[int]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return float(v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))])


def summary_data(db: Session, minutes: int) -> dict:
    """구간 요약 — 사용 기록을 SQL 로 집계한다(행을 메모리로 읽지 않음, D18-④). p95 는 종류별 정렬 위치 한 행만 읽는다."""
    since = utcnow() - timedelta(minutes=minutes)
    in_win = UsageRecord.ts >= since
    is_err = case((or_(UsageRecord.status.is_(None), UsageRecord.status != "ok"), 1), else_=0)
    kinds: dict[str, dict] = {}
    for kind, calls, errors, tokens, cost in db.execute(
            select(UsageRecord.kind, func.count(), func.sum(is_err),
                   func.coalesce(func.sum(UsageRecord.tokens_in + UsageRecord.tokens_out), 0),
                   func.coalesce(func.sum(UsageRecord.cost), 0)).where(in_win).group_by(UsageRecord.kind)):
        idx = min(calls - 1, int(round(0.95 * (calls - 1))))  # _p95 와 같은 위치
        p95 = db.scalar(select(UsageRecord.latency_ms).where(in_win, UsageRecord.kind == kind)
                        .order_by(UsageRecord.latency_ms).offset(idx).limit(1))
        kinds[kind] = {"calls": calls, "errors": int(errors or 0), "error_rate": round(int(errors or 0) / calls, 4),
                       "p95_latency_ms": float(p95) if p95 is not None else None, "tokens": int(tokens or 0),
                       "cost_usd": float(cost or 0)}
    total = sum(v["calls"] for v in kinds.values())
    dec_total, dec_fallback = db.execute(select(func.count(), func.sum(case((RoutingDecision.fallback_used.is_(True), 1), else_=0)))
                                         .where(RoutingDecision.ts >= since)).one()
    cost_sum = func.sum(UsageRecord.cost)
    top_models = db.execute(select(UsageRecord.target, cost_sum).where(in_win, UsageRecord.kind == "model")
                            .group_by(UsageRecord.target).order_by(cost_sum.desc(), UsageRecord.target).limit(5)).all()
    team = func.coalesce(UsageRecord.team, "-")
    teams = db.execute(select(team, cost_sum).where(in_win).group_by(team).order_by(cost_sum.desc(), team)).all()
    servers = db.scalars(select(McpServer).where(McpServer.status == "active")).all()
    return {
        "window_minutes": minutes,
        "qps": round(total / (minutes * 60), 4),
        "calls": total, "by_kind": kinds,
        "error_rate": round(sum(v["errors"] for v in kinds.values()) / total, 4) if total else 0.0,
        "tokens": sum(v["tokens"] for v in kinds.values()),
        "cost_usd": float(sum(v["cost_usd"] for v in kinds.values())),
        "fallback_rate": round(int(dec_fallback or 0) / dec_total, 4) if dec_total else 0.0,
        "top_models": [{"model": m, "cost_usd": float(c or 0)} for m, c in top_models],
        "cost_by_team": [{"team": t, "cost_usd": float(c or 0)} for t, c in teams],
        "approvals_pending": db.scalar(select(func.count()).select_from(Approval).where(Approval.status == "pending")),
        "runs": {s: n for s, n in db.execute(select(Run.status, func.count()).where(Run.created_at >= since)
                                             .group_by(Run.status))},
        "a2a_tasks": {s: n for s, n in db.execute(select(A2ATask.state, func.count()).group_by(A2ATask.state))},
        "mcp_servers": {"total": len(servers), "healthy": sum(1 for s in servers if s.health == "healthy")},
        "providers": llm.provider_health(), "rate_limit_backend": ratelimit.backend_status(),
        "spans": db.scalar(select(func.count()).select_from(Span).where(Span.start_time >= since)),
        **_overview_extra(db, since),
    }


def _overview_extra(db: Session, since) -> dict:
    """운영 개요 KPI (SCR-B02 ②⑤) — 게이트웨이 추가 지연 p95(도구 호출 스팬 − 업스트림 자식 스팬)·첫 토큰 p95·팀 예산·등록 대기."""
    from app.models import CatalogVersion, RateLimit, RunEvent
    # 게이트웨이 추가 지연 = 도구 호출 스팬 − 가장 긴 자식 스팬 — 자식 최대값을 한 번의 집계로 (스팬마다 조회 없음, D20-④)
    kid = aliased(Span)
    # 표본 부모마다 상관 하위 질의 — (parent_id, trace_id) 인덱스로 자식만 본다. GROUP BY 파생 테이블은 바깥 조건이
    # 안으로 들어가지 못해 기간 안 전체 스팬을 집계했다 (59th 시간 조건 → 65th)
    mx = (select(func.max(kid.duration_ms)).where(kid.parent_id == Span.id, kid.trace_id == Span.trace_id)
          .correlate(Span).scalar_subquery())
    over = [max(0.0, float(d) - float(m)) for d, m in db.execute(
        select(Span.duration_ms, mx).where(Span.start_time >= since, Span.name.like("%tools/call%"))
        .order_by(Span.start_time.desc()).limit(500)) if m is not None]  # 최근 500 — 순서 없는 표본은 p95 가 흔들린다 (D22-①)
    # 첫 토큰 = 실행 시작 → 다음 시작 전 첫 delta — 이벤트를 모두 읽지 않고 시작마다 하위 질의 (D20-④)
    st, d, nx = aliased(RunEvent), aliased(RunEvent), aliased(RunEvent)
    next_start = (select(func.min(nx.id)).where(nx.run_id == st.run_id, nx.type == "run.started", nx.id > st.id)
                  .correlate(st).scalar_subquery())  # 두 단계 위(바깥 시작 이벤트)와 상관 — 자동 상관은 바로 위만 본다
    first_delta = (select(func.min(d.ts)).where(d.run_id == st.run_id, d.type == "delta", d.id > st.id, d.ts >= since,
                                                d.id < func.coalesce(next_start, 2 ** 62)).correlate(st).scalar_subquery())
    first = [(fd - s0).total_seconds() * 1000 for s0, fd in db.execute(
        select(st.ts, type_coerce(first_delta, UTCDateTime())).where(st.ts >= since, st.type == "run.started")) if fd is not None]
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
    # 행을 모두 읽지 않고 SQL 로 묶는다 (D20-④) — day 는 UTC 날짜(PostgreSQL 은 세션 시간대가 아니라 UTC 로)
    if group_by == "day":
        col = func.date(func.timezone("UTC", UsageRecord.ts)) if db.bind.dialect.name == "postgresql" else func.date(UsageRecord.ts)
        gkey = cast(col, String)
    else:
        gkey = func.coalesce(func.nullif(GROUPS[group_by], ""), "-")
    agg = (stmt.with_only_columns(gkey.label("k"), func.count().label("calls"), func.sum(UsageRecord.tokens_in),
                                  func.sum(UsageRecord.tokens_out), func.sum(UsageRecord.cost), func.sum(UsageRecord.latency_ms),
                                  func.sum(case((UsageRecord.status == "ok", 0), else_=1)))
           .group_by(gkey))
    rows: dict[str, dict] = {}
    for key, calls, tin, tout, cost, lat, errs in db.execute(agg):
        key = str(key)[:10] if group_by == "day" else str(key)
        rows[key] = {"key": key, "calls": int(calls), "tokens_in": int(tin or 0), "tokens_out": int(tout or 0),
                     "cost": Decimal(str(cost or 0)), "latency": int(lat or 0), "errors": int(errs or 0)}
    items = [{**{k: v for k, v in r.items() if k not in ("cost", "latency")}, "cost_usd": float(r["cost"]),
              "avg_latency_ms": round(r["latency"] / r["calls"], 1)} for r in rows.values()]
    items.sort(key=lambda r: -r["cost_usd"])
    total = sum((r["cost"] for r in rows.values()), Decimal(0))
    return {"group_by": group_by, "scope": "all" if p.has("cost:read") else "self", "items": items,
            "total_cost_usd": float(total), "currency": "USD"}


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
    summaries: dict[int, dict] = {}  # 같은 창의 규칙은 요약 한 번 (D18-④)
    for a in db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))):
        m = max(1, a.window_s // 60)
        if m not in summaries:
            summaries[m] = summary_data(db, m)
        value = _metric_value(summaries[m], a.metric)
        if ops[a.op](float(value), float(a.threshold)):
            a.last_fired_at = utcnow()
            fired.append({"id": a.id, "name": a.name, "value": value})
            notify("alert", f"알림: {a.name}", f"{a.metric} {a.op} {a.threshold} (현재 {value})", severity=a.severity,
                   link="/console/observe/metrics", audience_role="operator", dedupe_key=f"alert:{a.id}",
                   dedupe_window_s=a.window_s)
    db.commit()
    return {"fired": fired}
