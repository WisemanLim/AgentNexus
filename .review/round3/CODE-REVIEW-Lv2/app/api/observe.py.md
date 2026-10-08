> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 observe.py.md](../../../../round2/CODE-REVIEW-Lv2/app/api/observe.py.md)

### 05. app/api/observe.py
**파일 요약 / File Summary**
- 역할 / Role: 관측 API — OTLP 트레이스 수신(`/v1/traces`), 트레이스·로그 조회, 운영 개요 KPI(`summary_data`), 사용량·비용 집계(`/api/v1/usage`), 경보 규칙·평가. 이번 범위는 ① D21-⑤ OTLP 수신 상한·형식 오류 400(L95-126) ② D20-④ 개요 KPI 의 N+1 제거(L258-284) ③ D20-④ usage 의 SQL `GROUP BY`(L341-381).
- 진입점 거리 / Distance: 0 — HTTP 라우트. 그리고 **예약 작업**: `app/services/scheduler.py:110-111` → `evaluate_alerts` → `evaluate`(L472-489) → `summary_data` → `_overview_extra`(경보 창마다 1회).
- LOC: 489 (2차 스냅샷 475 → +14)
- 주요 export(범위 안): `otlp_ingest`, `_overview_extra`, `usage`, `evaluate`
- 의존(내부): `app.core.tracing`(수신 토큰·스팬 저장), `app.services.ratelimit`(`spend`), 모델 `Span`·`RunEvent`·`UsageRecord`·`RateLimit`·`CatalogVersion`
- 외부 패키지: SQLAlchemy(`aliased`·`correlate`·`type_coerce`), opentelemetry-proto, protobuf
- 부수효과 / Side effects: 스팬 INSERT(수신), 경보 시 알림.
- 동시성 / Concurrency: `otlp_ingest` 는 async(본문 읽기) + 스레드풀(저장). 집계는 동기 핸들러·스케줄러 스레드.
- 테스트 커버리지 흔적: `test/test_observe_perf2.py:33-64`(C55-01 개요 KPI = 이전 구현, SQLite·PostgreSQL), `:66-104`(C55-02 usage = 이전 구현), `test/test_remaining_items.py:93-101`(C60-07 OTLP 400·413).
- 3차 변경 요약: 위 ①②③. 55th r1 에서 "다음 시작" 하위 질의 상관 결함(자동 상관은 바로 위 질의만)을 찾아 `.correlate(st)` 로 고침(`test/impl/55th/result.md` r1·r2).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/api/observe.py lines=95-126
OTLP_MAX_BYTES = 8 * 1024 * 1024


@router.post("/v1/traces", include_in_schema=False)
async def otlp_ingest(request: Request):
    if bearer(request) != tracing.ingest_token():
        return Response(status_code=401)
    body = await request.body()
    if len(body) > OTLP_MAX_BYTES:  # 수신 상한 (D21-⑤)
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
    except Exception:  # noqa: BLE001 — JSON·protobuf 형식 오류는 500 이 아니라 400
        return Response(status_code=400)
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
- L100-101: 수신 토큰 확인을 **본문을 읽기 전에** 한다 — 인증 없는 대용량 본문이 메모리에 들어오지 않게 하는 올바른 순서.
- L102-104(D21-⑤): 8 MiB 상한 초과 → 413.
- L106-116: JSON 또는 protobuf 파싱. 형식 오류는 400(전에는 500 — C60-07).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L12 — 상한이 다 읽은 뒤에 적용된다.** L102 `await request.body()` 가 본문 전체를 메모리에 올린 다음에 L103 이 크기를 본다. 수신 토큰을 가진 쪽(OTel Collector 설정 실수 포함)이 수백 MB 를 보내면 그만큼 워커 메모리를 쓴다. 최소 수정: `Content-Length` 를 먼저 보고, `async for chunk in request.stream()` 으로 누적하다 상한을 넘으면 413.
- [위험:낮음] (2차 L164 이월) L106-116 파싱(`json.loads`·`ParseFromString`·`MessageToDict`)이 async 핸들러 안의 동기 CPU 작업이라 그동안 이 워커의 이벤트 루프(MCP·A2A 프록시·SSE)가 멈춘다. 8 MiB 상한으로 정지 시간이 유한해졌을 뿐이다. 파싱+`_otlp_rows` 를 함께 `run_in_threadpool` 로.
- [위험:낮음] L115 의 `try` 는 **파싱만** 감싼다. JSON 으로는 맞지만 구조가 틀린 문서(예: `{"resourceSpans": "x"}`)는 L117 `_otlp_rows` 안에서 `AttributeError` 등으로 500 이 된다 — C60-07 "형식 오류 500 → 400" 이 절반만 적용. `_otlp_rows` 도 같은 `try` 안으로.
- [위험:낮음] (기존) L100 수신 토큰 비교가 `!=`(상수 시간 비교 아님). `hmac.compare_digest` 권장.

```python path=app/api/observe.py lines=258-284
def _overview_extra(db: Session, since) -> dict:
    """운영 개요 KPI (SCR-B02 ②⑤) — 게이트웨이 추가 지연 p95(도구 호출 스팬 − 업스트림 자식 스팬)·첫 토큰 p95·팀 예산·등록 대기."""
    from app.models import CatalogVersion, RateLimit, RunEvent
    # 게이트웨이 추가 지연 = 도구 호출 스팬 − 가장 긴 자식 스팬 — 자식 최대값을 한 번의 집계로 (스팬마다 조회 없음, D20-④)
    kid = aliased(Span)
    kids = (select(kid.parent_id.label("pid"), kid.trace_id.label("tid"), func.max(kid.duration_ms).label("mx"))
            .group_by(kid.parent_id, kid.trace_id).subquery())
    over = [max(0.0, float(d) - float(mx)) for d, mx in db.execute(
        select(Span.duration_ms, kids.c.mx).join(kids, and_(kids.c.pid == Span.id, kids.c.tid == Span.trace_id))
        .where(Span.start_time >= since, Span.name.like("%tools/call%")).limit(500))]
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
```
**분석 / Analysis (Level 2)**
- L262-267(D20-④): "게이트웨이 추가 지연 = 도구 호출 스팬 길이 − 가장 긴 자식 스팬 길이". 전에는 도구 호출 스팬마다 자식을 따로 조회(N+1)했다. 지금은 `(parent_id, trace_id)` 별 자식 최대 길이를 집계한 서브쿼리를 한 번 조인한다.
- L269-275(D20-④): "첫 토큰 = 실행 시작 → 다음 시작 전 첫 delta". 시작 이벤트마다 상관 하위 질의 두 개 — `next_start`(같은 실행의 다음 `run.started` id), `first_delta`(그 사이 첫 delta 시각). L271·L273 `.correlate(st)` 는 하위 질의 안의 하위 질의가 **두 단계 위** 바깥 질의와 묶이게 명시한 것이다(SQLAlchemy 자동 상관은 바로 위 질의만 본다 — 55th r1 결함).
- L274-275 `type_coerce(…, UTCDateTime())`: 하위 질의 결과에도 tz-aware 변환을 적용해 `fd - s0` 계산이 naive/aware 혼합 오류 없이 된다.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M10(INDEX §5) — 자식 집계가 스팬 테이블 전체를 훑는다.** L263-264 서브쿼리에는 시간 조건이 없다 — `GROUP BY parent_id, trace_id` 가 **보존 기간 전체 스팬**을 대상으로 매번 계산되고, 바깥의 `Span.start_time >= since` 조건은 그룹 집계 서브쿼리 안으로 내려가지 않는다(그룹 키가 아닌 컬럼 조건이라 일반적인 DB 가 밀어 넣지 못한다). `spans.parent_id` 에는 인덱스도 없다(`app/models.py:828`). 이 함수는 콘솔 개요뿐 아니라 **경보 평가마다**(스케줄러 주기 60초, `deploy/helm/agentnexus/values.yaml:60`, 창이 다른 규칙마다 1회 — L476-480) 실행된다. 실패 시나리오: 스팬 수백만 행(30일 보존·트래픽 많은 설치) → 경보 주기마다 전체 스캔 + 해시 집계가 DB CPU 를 점유하고, 예약 작업 리스(`alerts`) TTL(`app/services/scheduler.py:106` — 120초)을 넘기면 다른 파드가 같은 평가를 겹쳐 시작한다. N+1(요청 수) 문제를 테이블 크기 비례 비용으로 바꾼 셈이다. 최소 수정: 서브쿼리에 `kid.start_time >= since` 추가(자식은 부모보다 늦게 시작하므로 결과 동일), `spans(parent_id, trace_id)` 인덱스, 또는 PostgreSQL 은 `LATERAL` 로 부모별 최대값.
- [위험:낮음] **L13** L267 `.limit(500)` 에 `ORDER BY` 가 없어 어떤 500건이 p95 에 들어갈지 DB 마음대로다(같은 데이터에서도 값이 흔들릴 수 있음). L275 바깥 질의는 `run_events.ts`·`type` 인덱스가 없어(`app/models.py:513-520` — `run_id` 만 인덱스) 기간 조건에도 전체 스캔이다. 최소 수정: `ORDER BY Span.start_time DESC`, `run_events(type, ts)` 인덱스.

```python path=app/api/observe.py lines=341-381
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
```
**분석 / Analysis (Level 2)**
- L351-354: 권한 없는 주체는 본인 사용분만(팀 간 누출 방지) — 그대로 유지.
- L361-370(D20-④): ORM 전체 적재 → SQL 집계. 그룹 키는 `day` 면 UTC 날짜, 그 밖은 `coalesce(nullif(값, ''), '-')`(빈 문자열·NULL 을 "-" 로 — 이전 파이썬 구현과 같은 결과, C55-02). PostgreSQL 은 `timezone('UTC', ts)` 로 세션 시간대와 무관하게 UTC 날짜를 만든다 — `ts` 가 `timestamptz`(`app/db.py:17-20` `DateTime(timezone=True)`)라 이 변환이 맞다.
- L369 오류 수: `status == "ok"` 가 아니면 1 — NULL 도 오류로 센다(이전 구현과 같음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] (2차 이월) L347 `since` 하한이 없어 `since=1970-01-01` 이면 사용 기록 전체를 `GROUP BY` 한다. 메모리 문제는 사라졌지만 DB 시간은 남는다(`chat:use` 로 누구나 호출 가능, 단 비권한자는 본인 행만). 최대 기간(예: 400일) 제한.

```python path=app/api/observe.py lines=472-489
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
- 경보 규칙마다 창(분) 단위 요약을 한 번만 만든다(D18-④). 이 요약이 위 `_overview_extra` 를 포함하므로 M10 의 비용이 예약 작업 주기마다 반복된다. 개요 KPI 가 경보 지표(`_metric_value`)에 쓰이지 않는다면 경보 평가 경로에서는 `_overview_extra` 를 빼는 것이 가장 싼 해법이다(2차 M53 최소 수정안 "경보에서 제외" 와 같음).

## 파일 평가 / File Assessment
- 강점: (1) usage 집계를 SQL 로 옮기면서 이전 구현과의 동등성을 SQLite·PostgreSQL 양쪽에서 시험으로 고정했다(`test/test_observe_perf2.py:33-104`). (2) 상관 하위 질의의 상관 대상을 명시해 재시작·delta 없는 실행까지 의미를 보존했다(`app/api/observe.py:270-273`). (3) OTLP 수신은 인증 → 읽기 → 상한 → 파싱 순서이고 파싱 오류를 400 으로(`app/api/observe.py:100-116`).
- 위험: (1) [위험:중간] 자식 스팬 집계가 시간 조건 없이 전체 테이블 — 경보 주기마다(`app/api/observe.py:263-264`). (2) [위험:낮음] OTLP 상한이 전체 읽기 뒤, 구조 오류 500(`app/api/observe.py:102-117`). (3) [위험:낮음] 순서 없는 `limit(500)`·인덱스 없는 `run_events` 기간 조건(`app/api/observe.py:267`, `:275`).
- 개선 권고(영향순): (1) 서브쿼리 시간 조건 + 인덱스, 경보 평가에서 개요 KPI 제외. (2) 스트리밍 수신 상한. (3) 정렬·인덱스.
- 파일 위험 등급 / Risk grade: 중간 — 성능·가용성 [위험:중간] 1건(예약 작업 경로).
- 재읽기 권고 레벨: 3 — 남은 위험은 질의 계획(인덱스·서브쿼리 밀어 넣기) 문제라 블록 단위로 실행 계획(`EXPLAIN`)과 같이 보는 편이 효율적이다.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 (2차 INDEX §5) | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| M53 `_overview_extra` N+1·RunEvent 전체 로드, 경보 평가마다 반복 (`observe.py:254-265`) | 부분 해결 | N+1 → 집계 조인 `app/api/observe.py:262-267`, 이벤트 전체 로드 → 상관 하위 질의 `:269-275`. 시간 조건 없는 전체 집계(M10)·경보 경로 포함은 남음 `:476-480` |
| M54 `usage` 가 ORM 전체를 메모리 집계, `since` 하한 없음 (`observe.py:338`·`:352-361`) | 부분 해결 | SQL `GROUP BY` `:361-370`. `since` 하한 없음은 남음 `:347` |
| (2차 observe.py.md L164) OTLP 본문 상한 없음·파싱이 이벤트 루프 안 동기 CPU · (L130) 구조 오류 500 | 부분 해결 | 413·파싱 오류 400 `:102-116`. 상한이 전체 읽기 뒤, 구조 오류 500(`_otlp_rows` 가 `try` 밖 `:117`), 파싱이 여전히 async 핸들러 안(상한 8 MiB 라 정지 시간은 유한) — L12 |
