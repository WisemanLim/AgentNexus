> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [api_Abstract.md](api_Abstract.md) · 1차 리뷰: [observe.py.md](../../../../CODE-REVIEW-Lv2/app/api/observe.py.md)

### 28. app/api/observe.py
**파일 요약 / File Summary**
- 역할 / Role: 관측 API — `/metrics`(Prometheus), `/v1/traces`(OTLP/HTTP 수신기), 트레이스 탐색·워터폴, 로그 검색, 운영 요약·시계열·파이프라인, Usage & Cost 집계, 알림 규칙·평가, SLO·오류 예산.
- 진입점 거리 / Distance: 1 (`app/main.py:27`·`:155`) · 스케줄러 경유 2 (`app/services/scheduler.py:45-48` → `evaluate`)
- LOC: 475 (현재 `wc -l`) · 1차 471 → +4
- 주요 export: `router`, `prometheus`, `otlp_ingest`, `summary_data`, `evaluate`(스케줄러 재사용), `GROUPS`, `_p95`(시험이 참조)
- 의존(내부): `app.core.{metrics,tracing,auth,config,errors,ids}`, `app.db`, `app.models`, `app.services.{audit,llm,ratelimit,slo,notify}`, (지연) `app.services.scheduler.WORKER`
- 외부 패키지: fastapi, pydantic, sqlalchemy, opentelemetry-proto·google.protobuf(지연 import)
- 부수효과 / Side effects: DB 쓰기(스팬 수신·알림 규칙·SLO·`last_fired_at`·SLI flush), 감사, 알림(Slack 웹훅 가능), Valkey ping(`ratelimit.backend_status`)
- 동시성 / Concurrency: `otlp_ingest` 만 `async def`(본문 파싱은 이벤트 루프에서, DB 저장만 스레드풀). 나머지는 동기 `def`(스레드풀). 스케줄러의 `evaluate` 와 `POST /alerts/evaluate` 가 동시에 돌 수 있다.
- 테스트 커버리지 흔적: `test/test_observe.py:90-110`(OTLP protobuf·JSON·401), `:112-123`(/metrics 인증), `:141`(summary), `:148-170`(usage 범위), `:174-177`(alerts), `test/test_perf_paths.py:43-56`(C47-01 SQL 요약 = 이전 파이썬 집계), `:59-78`(C47-02 같은 창 요약 1회), `test/test_slo.py:84-91`(SLO API), `test/test_hardening.py:170-186`(OTLP 수신 스팬 마스킹), `test/test_admin.py:157-160`·`test/test_ops.py:472`(timeseries)
- 2차 변경 요약 / Changes since 1st review: (1) `summary_data`(L204-247)가 사용 기록을 SQL `GROUP BY` 로 집계 — 종류별 호출·오류(`sum(case)`)·토큰·비용, p95 는 종류별 `ORDER BY latency LIMIT 1 OFFSET idx`, 상위 모델·팀 비용·폴백률도 SQL(Epic 47 D18-④, `test/impl/47th/scenario.md` 범위 표). (2) `evaluate`(L462-466)가 같은 창(window)의 규칙끼리 요약을 1회만 계산해 공유. 그 밖의 엔드포인트(`/metrics`·OTLP·traces·logs·timeseries·usage·SLO)는 1차 인용과 동일.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/observe.py lines=1-31
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
```
**분석 / Analysis (Level 2)**
- L1-9: 엔드포인트 목록 docstring. L17 `case`·`func`·`type_coerce` 는 2차 SQL 집계의 핵심 도구다.
- L21: `require("perm")` 은 권한 검사 의존성 팩토리, `bearer` 는 `Authorization: Bearer` 또는 `X-API-Key` 추출(`app/core/auth.py:265-269`), `authenticate_token` 은 토큰 → `Principal`.
- L25: `UTCDateTime` 을 import 하는 이유 — 집계식 결과에 타입을 명시하기 위해(L128-129).

```python path=app/api/observe.py lines=34-47
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
- L36-38: 스크레이프 토큰 — 설정 토큰이 비었거나 placeholder(`CHANGE_ME`)면 비교 자체를 하지 않는다(placeholder 로 인증되는 사고 방지).
- L39-44: 설정 토큰이 아니면 사용자 토큰으로 인증해 `observe:read` 확인. `Depends(get_db)` 대신 직접 `SessionLocal()` 을 여는 이유는 토큰이 없을 때 DB 를 아예 열지 않기 위해서다.
- L47: Prometheus 텍스트 포맷 0.0.4.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L38 `token == settings.metrics_token`(그리고 L97 OTLP 토큰 비교)이 상수 시간 비교가 아니다(1차 지적 그대로). 고엔트로피 토큰이라 실질 위험은 작지만 비용 없는 방어다. 또 L38 은 `strip()` 없이 비교하고 `app/core/tracing.py:102` 는 `strip()` 한다 — 설정 값에 개행이 섞이면 `/metrics` 만 실패. 최소 수정: `hmac.compare_digest(token, settings.metrics_token.strip())`.

```python path=app/api/observe.py lines=50-92
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
- L51: OTLP `SpanKind` 정수 → 이름.
- L54-60 `_anyvalue`: OTLP `AnyValue` → 파이썬 값. JSON 매핑에서 64비트 정수는 문자열로 오므로(`"intValue": "42"`) `int()` 변환이 필요하다. `kvlistValue`·`bytesValue` 는 `None` 으로 버린다.
- L63-72 `hexid`: OTLP/JSON 은 id 를 hex 로, protobuf → `MessageToDict` 는 `bytes` 를 base64 로 준다. 8바이트 span id 의 base64 는 12자, 16바이트 trace id 는 24자라 "길이 16·32 의 hex" 판정과 겹치지 않는다(판정이 모호하지 않음).
- L73-91: 리소스 속성의 `service.name`(80자 절단) → 스코프 → 스팬마다 행 dict. 시각은 나노초 정수 → UTC aware datetime, 상태 코드 2 또는 `STATUS_CODE_ERROR` 면 error, 이벤트는 이름만 보존.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L72·L80·L85-86 형식이 어긋난 본문이 처리되지 않은 예외가 된다: `spanId` 누락 → `KeyError`(L80), base64 아님 → `binascii.Error`(L72), 거대한 나노초 → `datetime.fromtimestamp` 의 `OverflowError/ValueError`(L85-86), `"resourceSpans": "x"` → 문자 단위 순회 후 `AttributeError`(L74). 일반 예외 처리기가 없어(`app/core/errors.py:55-75`) text/plain 500 으로 나가고 같은 배치의 정상 스팬도 모두 버려진다. 수신 토큰이 필요한 내부 경로라 영향은 낮다. 최소 수정: 스팬 단위 `try/except (KeyError, ValueError, TypeError, binascii.Error, OverflowError)` 로 건너뛰고 OTLP 규약대로 `partialSuccess.rejectedSpans` 를 돌려준다.

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
- L97-98: 수신 토큰 확인(`app/core/tracing.py:98-112` — env 또는 `data_dir/keys/otlp-ingest.token` 자동 생성). 토큰 생성에 실패하면 `ingest_token()` 이 `""` 을 돌려주는데, `bearer()` 는 빈 값을 `None` 으로 바꾸므로(`app/core/auth.py:268-269`) `None != ""` 로 항상 401 — 1차가 우려한 "빈 토큰이면 인증이 느슨해짐" 경로는 실제로 열리지 않는다(2차 확인).
- L99-108: 본문 전체를 메모리로 읽은 뒤 Content-Type 에 `json` 이 있으면 JSON, 아니면 protobuf 로 파싱해 dict 로 통일. 무거운 protobuf 모듈은 지연 import.
- L109-114: 행 변환 → DB 저장만 `run_in_threadpool`(동기 SQLAlchemy 를 이벤트 루프 밖으로). `tracing.STATS` 는 워커별 메모리 카운터(워커가 여럿이면 `/pipeline` 수치는 요청을 받은 워커 것만).
- L115-118: OTLP/HTTP 규약대로 요청 형식과 같은 형식의 빈 응답.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L99-109 본문 크기 상한이 없고(1차 지적 그대로, 앱 전역에도 본문 상한 코드 없음 — `content-length`·`max_body` 검색 0건), 파싱(`json.loads`·`ParseFromString`·`MessageToDict`·`_otlp_rows`)이 **async 함수 안에서 동기 CPU 작업**으로 실행된다. 토큰 보유자(또는 토큰 파일을 읽을 수 있는 같은 볼륨의 컨테이너)가 수십 MB 배치를 보내면 그 워커의 이벤트 루프가 파싱 동안 멈춰 MCP/A2A 프록시·SSE 가 함께 지연된다. 또 L97 `tracing.ingest_token()` 은 요청마다 토큰 파일을 동기 read 한다(`app/core/tracing.py:104-106`). 최소 수정: `Content-Length` 상한(예: 4 MiB — gRPC 수신 상한 `app/services/a2a_grpc.py:260` 과 같은 값) 검사, 파싱+변환을 `run_in_threadpool` 로 함께 넘기기, 토큰은 프로세스 캐시.
- 저장은 `db.merge`(upsert)라 같은 span id 로 기존 스팬(플랫폼 자체 스팬 포함)을 덮어쓸 수 있다(`app/core/tracing.py:64`, 1차 지적 그대로 — 교차 확인 대상).

```python path=app/api/observe.py lines=121-153
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
```
**분석 / Analysis (Level 2)**
- L123-126: `observe:read` 필요. `Query(..., le=...)` 로 상한만 검증한다.
- L128-131: trace 별 집계 — 첫 시작·마지막 종료(집계 결과에도 UTC aware 를 보장하려고 `type_coerce`), 스팬 수, 오류 수(`sum(case(...))`). 최신순 + `limit`.
- L132-142: SQLAlchemy `Select` 는 생성형(generative)이라 `.limit()` 뒤에 `.where()` 를 붙여도 SQL 은 `WHERE … GROUP BY … ORDER BY … LIMIT` 으로 올바르게 조립된다(호출 순서 ≠ SQL 절 순서). 필터는 모두 `IN (subquery)`.
- L144-152: trace 마다 루트(가장 이른 스팬)·서비스 목록을 추가 조회.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L126(그리고 L181) `limit` 에 하한(`ge=1`)이 없다. `limit=-1` 이면 SQLite 는 "음수 LIMIT = 무제한" 으로 해석해 기간 내 **모든 trace** 를 돌려주고, 그 각각에 L147-148 의 2쿼리(N+1, 1차 지적 그대로)가 붙는다 → `observe:read` 사용자 1명이 워커를 수만 쿼리로 묶을 수 있다(local SQLite). PostgreSQL 은 `LIMIT must not be negative` 로 text/plain 500. 최소 수정: `Query(50, ge=1, le=500)`(L181 은 `ge=1, le=2000`), 루트·서비스는 윈도 함수 또는 `trace_id IN (...)` 1쿼리로 묶기.
- [위험:낮음] L145-146 `errors_only` 를 **LIMIT 이후** 파이썬에서 거른다 — "오류 trace 최신 50개" 가 아니라 "최신 50개 중 오류인 것" 이 나온다. 최근 50개가 모두 정상이면 오래된 오류 trace 가 있어도 빈 목록(기능 결함). 최소 수정: `stmt = stmt.having(func.sum(case(...)) > 0)`.
- [위험:낮음] L135·L141(그리고 L191) 사용자 입력을 `like(f"%{q}%")` 에 그대로 — SQL 인젝션은 아니지만(바인드 파라미터) `%`·`_` 가 와일드카드로 해석되고 선행 `%` 라 인덱스를 못 탄다(1차 지적·2차 위임 SEC2-06 `.review/round2/delegated/security-review.md:180-189` 와 동일). 최소 수정: `q.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")` + `like(..., escape="\\")`.

```python path=app/api/observe.py lines=156-173
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
- L158-164: trace 의 모든 스팬(인덱스 `ix_spans_trace_id`) → 워터폴용 상대 오프셋(ms). `Numeric` 인 `duration_ms` 는 `Decimal` 로 오므로 `float()` 로 JSON 직렬화 가능하게.
- L165-166: 부모가 없거나 부모가 이 trace 에 없는 스팬을 루트로(다른 서비스가 일부만 보낸 경우에도 트리가 끊기지 않게).
- L167-173: GenAI 스팬 수(`gen_ai.operation.name` 속성), 사용 기록의 비용·토큰 합(`Decimal` 로 더한 뒤 마지막에 float — 부동소수 누적 오차 회피).
- 인가: `observe:read` 면 모든 trace 를 본다(운영자 역할용 설계). 속성은 저장 시 마스킹(`test/test_hardening.py:170-186`).

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
- L182-191: 기간(기본 24시간·최대 90일) + 수준·구성요소·trace·검색어 필터, 최신 id 순. `level.upper()` 로 대소문자 정규화.
- 로그는 저장 시 PII 마스킹(`app/core/logs.py`) — 검색 결과에 원문 PII 가 없다.
- `limit` 하한 부재·LIKE 미이스케이프는 위 L126·L135 Watch-out 에 포함.

```python path=app/api/observe.py lines=196-201
# ───────────────────────── 요약 (SCR-B02·H01·K05) ─────────────────────────
def _p95(values: list[int]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return float(v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))])
```
**분석 / Analysis (Level 2)**
- L197-201: 최근접 순위(nearest-rank) 변형 p95 — 정렬 후 `round(0.95·(n-1))` 위치. 2차 SQL 집계(L214)가 **같은 위치 규칙**을 써서 이전 값과 동일함을 시험이 보장한다(`test/test_perf_paths.py:14-27`·`:52-55`). `round()` 는 은행가 반올림(0.5 → 짝수)이라 위치가 직관과 1칸 다를 수 있지만 두 구현이 같으므로 일관된다.

```python path=app/api/observe.py lines=204-247
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
```
**분석 / Analysis (Level 2)**
- L207-208 (2차): 창 조건·오류 판정을 **식 객체**로 한 번 만들어 재사용. `is_err` 는 `status IS NULL OR status != 'ok'` — SQL 에서 `NULL != 'ok'` 는 참이 아니라 NULL 이므로 `IS NULL` 을 따로 넣어야 이전 파이썬 판정(`u.status not in ("ok",)` → None 도 오류)과 같아진다. 주니어가 가장 자주 틀리는 3값 논리 지점이다.
- L210-213: 종류별 `count`·오류 합·토큰 합·비용 합을 `GROUP BY kind` 한 번에. `coalesce(sum(...), 0)` 은 빈 그룹의 NULL 합을 0 으로. `func.sum(UsageRecord.cost)` 는 인자 타입(`Numeric(14,6)`)을 물려받아 `Decimal` 로 온다.
- L214-216: p95 를 종류별로 **정렬 위치 한 행만** 읽는다(`ORDER BY latency_ms OFFSET idx LIMIT 1`). 종류는 3개 안팎이라 추가 쿼리는 3번. 위치 규칙은 `_p95` 와 동일. `GROUP BY` 결과와 이 쿼리 사이에 행이 바뀌면(READ COMMITTED 는 문장마다 새 스냅샷) 위치가 1~2칸 어긋날 수 있으나 요약 지표로는 무시할 수준.
- L221-222: 폴백률도 `count` + `sum(case)` 한 줄. `.one()` 은 정확히 1행을 기대(집계 쿼리는 항상 1행).
- L223-227: 상위 모델 5개·팀별 비용 — `ORDER BY 비용 DESC, 이름` 으로 동률일 때 결과 순서를 결정적으로 만든다(시험이 이 순서를 비교, `test/test_perf_paths.py:37-38`). `func.coalesce(team, "-")` 를 `select`·`group_by`·`order_by` 에 같은 식 객체로 써야 SQL 에서 같은 표현식으로 인식된다.
- L235: 총비용이 종류별 `float` 의 합으로 바뀌었다(이전은 `Decimal` 합) — 시험은 `1e-9` 오차를 허용한다(`test/test_perf_paths.py:56`). 표시용이라 문제없다.
- L244: `ratelimit.backend_status()` 는 Valkey 백엔드면 요약마다 `PING` 네트워크 왕복을 한다(`app/services/ratelimit.py:162-167`).
- L246: `_overview_extra` 결과를 병합 — **이 부분은 2차 SQL 화 대상이 아니었다**(아래).

```python path=app/api/observe.py lines=250-274
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
```
**분석 / Analysis (Level 2)**
- L254-257: 게이트웨이 추가 지연 = `tools/call` 스팬 시간 − 가장 긴 자식(업스트림) 스팬. 최대 500 스팬.
- L258-265: 첫 토큰 시간 = 실행별 `run.started` → 첫 `delta` 이벤트 간격. `pop` 으로 실행당 첫 delta 만 센다.
- L266-271: 팀 예산 소진율 — `ratelimit.spend` 는 키별 5초 캐시 + SUM 1쿼리(`app/services/ratelimit.py:224-238`).
**짚어볼 점 / Watch-outs**
- [위험:중간] L254-265 **2차 성능 정리(D18-④)가 비켜 간 전체 행 로드** — 1차 핫스팟 #5 의 남은 절반. (a) L255 는 스팬마다 자식 조회(최대 500회 N+1, 1차 지적 그대로). `spans.parent_id` 는 인덱스가 없다(`app/models.py:827`, 마이그레이션은 `start_time`·`trace_id` 만 — `migrations/versions/0001_initial_schema.py:590-591`). (b) L260-261 은 창 안의 `run.started`·`delta` **RunEvent 를 ORM 객체(JSON `data` 포함)로 전부** 읽는다. `delta` 는 스트리밍 청크마다 1행(`app/services/orchestrator.py:470`)이라 실행 수 × 청크 수만큼 쌓이고, 보존 기본 90일(`app/services/retention.py:15`)이며, `run_events` 에는 `run_id` 인덱스만 있어(`migrations/versions/0001_initial_schema.py:501`) `ts` 조건은 **전체 테이블 스캔**이다. (c) 이 함수는 `summary_data` 안에서 항상 불리므로(L246), 요약 창 최대 31일(L279)이면 31일치 delta 를 메모리로 올리고, 스케줄러의 경보 평가(`app/services/scheduler.py:105-107` → L466)도 **주기(기본 60초)마다, 서로 다른 창마다** 이 비용을 낸다 — 그런데 경보 지표(`_metric_value` L397-401: `by_kind`·`error_rate`·`cost_usd`·`qps`·`fallback_rate`·`approvals_pending`)는 `_overview_extra` 의 키를 하나도 쓰지 않는다. 즉 L205 docstring 의 "행을 메모리로 읽지 않음" 은 UsageRecord 에만 맞다. 시나리오: 일 1만 실행 × 실행당 delta 200개 → 24시간 창 경보 규칙 1개만 있어도 매분 약 200만 행 스캔·적재 → 스케줄러 워커의 메모리·DB CPU 급증, 같은 DB 를 쓰는 요청 지연. 최소 수정: `summary_data(db, m, extra=False)` 로 경보 평가에서 제외, 첫 토큰은 `SELECT run_id, MIN(ts) … GROUP BY run_id, type` 집계 후 조인, 자식 최대 시간은 `GROUP BY parent_id` 1쿼리, `run_events(type, ts)`·`spans(parent_id)` 인덱스. 시험 공백: `test/test_perf_paths.py` 는 UsageRecord 집계만 비교.

```python path=app/api/observe.py lines=277-305
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
- L277-280: 요약 엔드포인트 — 창 1분~31일(`ge`·`le` 모두 있음).
- L283-305: 시계열 — 엔진별 날짜 함수 차이를 피하려고 **앱에서** 버킷팅. ORM 객체가 아니라 컬럼 튜플만 읽어(L293-294) 1차 `usage` 보다 가볍다. `timedelta / timedelta` 는 float 를 돌려주므로 `int()` 로 버킷 번호(L295), 경계값은 `min(..., buckets-1)`.
- L301: `status == "ok"` 가 아니면 오류(None 포함) — `summary_data` 의 `is_err` 와 같은 판정.
- L304: 여기 p95 는 `_p95` 와 **다른 위치 규칙**(`ceil` 근사 `int(n·0.95+0.5)-1`)이다 — 같은 데이터에서 요약 카드와 시계열의 p95 가 1칸 다를 수 있다(표시 불일치, 기능 영향 작음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L293-302 창 안의 모든 사용 기록을 파이썬으로 순회하고 지연 값을 버킷별 리스트에 모두 담는다(최대 7일) — 메모리 O(행 수). 1차 핫스팟 #5 의 잔여분이지만 컬럼 튜플이라 `usage` 보다 영향이 작다. 최소 수정: 버킷 번호를 SQL 식(`floor((epoch(ts) - epoch(start)) / step)`, 엔진별 분기)으로 `GROUP BY`, p95 는 버킷별 `percentile_cont`(PG) 또는 표본 추출.

```python path=app/api/observe.py lines=308-322
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
```
**분석 / Analysis (Level 2)**
- L311-313: 최근 1시간 서비스별 스팬 수(SQL `GROUP BY` — 가볍다).
- L314-322: 내보내기·수신 통계는 `tracing.STATS` 의 **워커별 메모리 값**이다 — 다중 워커에서는 요청을 받은 워커의 수치만 보인다(운영자가 "수신 0" 으로 오해할 수 있음, 표시 한계).

```python path=app/api/observe.py lines=325-367
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
- L326-328: 그룹 키 → ORM 컬럼 매핑. L353 의 `GROUPS[group_by].key` 는 `InstrumentedAttribute.key`(= 속성 이름 문자열)라 `getattr(u, "target")` 처럼 쓴다. `group_by` 는 L333 `pattern` 으로 제한돼 임의 속성 접근은 없다.
- L332: `chat:use` 만 있으면 호출 가능 — 대신 L341-342 에서 `cost:read` 가 없으면 **본인 사용분으로 강제**(객체 수준 격리, 팀 간 비용 누출 차단 — 좋음). `team` 필터는 `cost:read` 일 때만 적용(L343-344).
- L337: `since` 기본 30일. 클라이언트가 오프셋 없는 시각을 주면 `UTCDateTime` 이 UTC 로 간주(`app/db.py:26-27`).
- L351-361: 그룹별 호출·토큰·비용·지연·오류를 **파이썬에서** 누적. 비용은 `Decimal` 로 더해 마지막에 float.
**짚어볼 점 / Watch-outs**
- [위험:중간] L338·L352-361 **전체 ORM 행 메모리 집계가 그대로**(1차 지적, 2차 D18-④ 범위 밖). `select(UsageRecord)` 는 JSON `policy_decisions`(`app/models.py:761`)까지 포함한 ORM 객체를 행마다 만든다. `since` 에 하한이 없어(L334·L337) `cost:read` 사용자(finops·admin)가 `since=2000-01-01` 로 부르면 보존 기본 400일(`app/services/retention.py:15` `usage_days`)치 사용 기록 전체가 한 요청에서 메모리로 올라온다. 게이트웨이 호출마다 1행이 쌓이므로(모델·도구·원격 에이전트) 수백만 행이면 워커 메모리 급증(OOM 가능성)·요청 수십 초·풀 연결 점유. 시나리오: FinOps 대시보드가 "전체 기간" 버튼으로 이 API 를 부르면 그 순간 같은 워커의 다른 요청이 함께 느려진다. 최소 수정: `summary_data` 와 같은 방식으로 `select(group_col, count(), sum(tokens_in), sum(tokens_out), sum(cost), avg(latency_ms), sum(is_err)).group_by(group_col)`(day 는 `func.date(ts)` — SQLite·PG 공통), 기간 상한(예: 400일) 검증. 시험 공백: `test/test_observe.py:148-170` 는 정확도·범위만 검증, 대량·장기간 경로 없음.

```python path=app/api/observe.py lines=370-401
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
```
**분석 / Analysis (Level 2)**
- L371-378: 알림 규칙 입력 — 지표·연산자·심각도는 정규식 화이트리스트, 창 60초~24시간. 연산자를 문자열로 받아 L461 의 `ops` 사전으로만 해석하므로 `eval` 류 위험이 없다(좋은 패턴).
- L388-394: 생성은 `admin:write` + 감사.
- L397-401: 지표 값 추출 — p95 는 종류별 p95 중 최댓값, 나머지는 요약 dict 의 최상위 키. **`_overview_extra` 키는 쓰지 않는다**(위 L250-274 Watch-out 의 근거).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L372·L375 `name: str` 에 길이 제한이 없는데 컬럼은 `String(120)`(`app/models.py:904`), `threshold: float` 는 `Numeric(14, 4)`(`app/models.py:907`)다. PostgreSQL 에서 121자 이름이나 `1e12` 임계값은 `DataError` → text/plain 500(SQLite 는 길이를 강제하지 않아 local 시험에서 드러나지 않음). 관리자 입력이라 영향은 낮다. 최소 수정: `Field(min_length=1, max_length=120)`, `threshold: float = Field(ge=-1e9, le=1e9)`.

```python path=app/api/observe.py lines=404-450
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
```
**분석 / Analysis (Level 2)**
- L405-410: SLO 수정 입력 — 목표·창 범위 검증, **사유 필수**(감사 근거, `test/test_slo.py:90` 이 422 확인). `threshold_ms` 는 L433 에서 허용 경계값(`slo_svc.BOUNDS`) 중 하나로 제한.
- L420-426: 목록 조회 전에 이 워커의 SLI 카운터를 DB 로 flush — GET 이 DB 쓰기를 하는 부수효과가 있지만 내부 카운터라 멱등에 가깝다.
- L429-443: 변경 전·후·사유를 감사에 남긴다(좋음). `setattr` 루프는 `None` 이 아닌 필드만 반영하므로 `enabled=False` 도 정상 반영된다(`False is not None`).
- L446-450: SLO 평가 트리거 — `observe:read` 로 가능(아래 L453-455 와 같은 판단).

```python path=app/api/observe.py lines=453-475
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
```
**분석 / Analysis (Level 2)**
- L458-459: 콘솔 "지금 평가" 와 스케줄러(`app/services/scheduler.py:45-48`, 리스 `alerts` 보유 워커만 `:105-107`)가 함께 쓰는 함수.
- L461: 연산자 → 메서드 매핑. 사용자 입력 문자열을 코드로 해석하지 않는다.
- L462-466 (2차): 창(분)별 요약 캐시 — 같은 창 규칙 N개면 `summary_data` 1회(`test/test_perf_paths.py:59-78` 이 3규칙 → 1회 확인). 창이 서로 다르면 창 수만큼 계산.
- L468-473: 임계 초과 시 `last_fired_at` 갱신·알림. `notify` 는 **자체 세션**(`session_scope`, `app/services/notify.py:13-22`)으로 즉시 커밋하고, 이 함수의 `db` 는 `autoflush=False`(`app/db.py:56`)라 L469 변경이 아직 flush 되지 않아 SQLite 쓰기 잠금 충돌이 없다 — 세션 두 개를 섞을 때의 흔한 교착을 피하는 구조다.
- L474: 마지막에 한 번 commit.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L453-455·L446-450 평가 트리거가 `observe:read` 다(1차 지적 그대로). 읽기 권한 사용자가 반복 호출하면 매번 요약(+ 위 `_overview_extra` 전체 스캔)을 돌리고, 스케줄러와 동시에 돌면 `notify` 의 중복 억제가 "조회 후 삽입"(`app/services/notify.py:14-21`, 조건부 INSERT 아님)이라 같은 경보 알림이 2건 생길 수 있다(가능성 — 동시 실행 창이 좁음). 최소 수정: 트리거는 `admin:write` 또는 `operator` 전용 + 레이트리밋, 중복 억제는 `dedupe_key` 고유 제약 + 시간 버킷.

## 파일 평가 / File Assessment
- 강점: (1) 2차 `summary_data` SQL 집계 — 3값 논리(`IS NULL`)·동률 순서·p95 위치 규칙까지 이전 구현과 같은 값을 내도록 시험으로 고정(`test/test_perf_paths.py:43-56`). (2) Usage 의 객체 수준 격리(`cost:read` 없으면 본인만)·SLO 수정 사유 감사·연산자 화이트리스트. (3) OTLP 수신기 토큰 인증과 저장 전 마스킹으로 다중 서비스 단일 trace.
- 위험: (1) [위험:중간] `_overview_extra` 의 RunEvent 전체 스캔·스팬 N+1 이 요약·경보 평가마다 실행 — `app/api/observe.py:254-265`. (2) [위험:중간] `usage` 의 전체 ORM 행 메모리 집계·기간 하한 없음 — `app/api/observe.py:338,352-361`. (3) [위험:낮음] `limit` 하한 부재(SQLite 무제한)·`errors_only` 사후 필터·OTLP 본문 상한/루프 내 파싱 — `app/api/observe.py:126,145,181,99-109`.
- 개선 권고(영향순): (1) 경보 평가에서 `_overview_extra` 제외 + 첫 토큰·자식 지연을 SQL 집계로, `run_events(type, ts)`·`spans(parent_id)` 인덱스. (2) `usage` 를 `GROUP BY` 로, 기간 상한. (3) `Query(ge=1)`·`HAVING` 으로 trace 목록 수정, OTLP `Content-Length` 상한·파싱 스레드풀 이동. (4) 토큰 `hmac.compare_digest`, LIKE 이스케이프.
- 파일 위험 등급 / Risk grade: 중간 — 미해결 [위험:중간] 2건(성능·가용성), 높음 없음.
- 재읽기 권고 레벨: 3 — 2차 집계 변경은 시험으로 고정됐고, 남은 관심사는 `_overview_extra`·`usage` 의 집계 경로다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| L38 `token == metrics_token` 비상수 시간 비교 | 미해결 | L38 |
| L39-44 잘못된 토큰에도 사용자 인증 DB 조회 | 미해결(설계) | L39-44 |
| L67-72 `hexid` 의 base64 디코드 예외 → 500 | 미해결 | L72 (L80·L85-86 도 같은 계열) |
| [낮음] L97 OTLP 토큰 비상수 비교·빈 토큰이면 느슨해질 우려 | 미해결(비교 방식) — 빈 토큰 경로는 실제로 열리지 않음 확인 | L97; `app/core/auth.py:268-269`(`bearer` 는 빈 값 → `None`) |
| L99-108 OTLP 본문 크기 제한 없음 | 미해결 | L99 (앱 전역 본문 상한 코드 없음) |
| 스팬 `db.merge` upsert 로 기존 스팬 덮어쓰기 | 미해결 | `app/core/tracing.py:64` |
| [낮음] L144-152 trace 목록 N+1 | 미해결 | L147-148 |
| L134-135·L141 LIKE 미이스케이프 | 미해결 | L135·L141·L191 |
| [중간] L206 `summary_data` 기간 내 사용 기록 전체 메모리 집계 | 해결 | L204-247 SQL `GROUP BY`·`OFFSET` p95; `test/test_perf_paths.py:43-56` |
| L254-257 `_overview_extra` 스팬 N+1 | 미해결 | L254-257 |
| L260-265 RunEvent 를 기간만큼 읽어 파이썬 처리 | 미해결(경보 평가 경로로 매분 실행) | L260-265 |
| L351-361 `usage` 전체 행 메모리 집계 | 미해결 | L338·L352-361 |
| [중간] L462-463 `evaluate` 가 규칙마다 `summary_data` | 해결 | L462-466; `test/test_perf_paths.py:59-78` |
| L447·L454 평가 엔드포인트가 `observe:read` | 미해결 | L446-455 |
| (1차 INDEX §4.3 핫스팟 #5) 관측 집계 전체 행 메모리 로드 | 부분 해결 | `summary_data`·`evaluate` 해결 / `usage`(L338)·`_overview_extra`(L254-265)·`timeseries`(L293-302) 잔존 |
