> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 4차 리뷰(round4, D22 hunk 한정) · 스냅샷 2026-10-08 14:27 KST · [← INDEX.md](../../INDEX.md) · 3차 리뷰: [round3 llm.py.md](../../../../round3/CODE-REVIEW-Lv2/app/services/llm.py.md)

### 03. app/services/llm.py (+ `app/api/llm.py:56-136` · `app/api/ops.py:559-573` · `app/services/mcp_consent.py:91-206`)
**파일 요약 / File Summary**
- 역할 / Role: Unified Model API 실행 — `prepare`(입력 가드·라우팅·정책·한도 예약) → `execute`/`execute_stream`(후보 재시도·폴백) → `finalize`(사용 기록·정산). 13차(D22-③)에서 **출력 토큰 예약**·**주체별 진행 중 요청 상한(inflight cap)**·**`discard`(실행하지 않는 계획의 예약 반환)** 가 들어왔다.
- 진입점 거리 / Distance: 1 — `/v1/chat/completions`·`/v1/embeddings`(`app/api/llm.py:56`, `:143`), 라우팅 시험(`app/api/ops.py:559`), MCP 샘플링(`app/services/mcp_consent.py:110`, `:189`), 지식 베이스 임베딩(`app/services/knowledge.py:61`).
- LOC: 499 (3차 471 → 499) · `app/api/llm.py` 543 · `app/services/mcp_consent.py` 346
- 주요 export: `prepare`·`execute`·`execute_stream`·`execute_embed`·`embed_texts`·`finalize`·`discard`(신규)·`release_inflight`(신규)·`OUTPUT_TOKENS_DEFAULT`(신규)·`Plan`·`CallContext`
- 의존(내부): `app.services.{ratelimit, policy, guards, audit, secrets, providers}`, `app.db.SessionLocal`, `app.core.{config, logs, metrics, tracing}`
- 외부 패키지: SQLAlchemy, OpenTelemetry, (간접) httpx — 공급자 어댑터
- 부수효과 / Side effects: Valkey 보유자 키 `anx:h:inflight:<sub>`(신규), 토큰·예산 예약, `usage_records`·`routing_decisions` 쓰기, 프로세스 전역 서킷 브레이커·지연 EWMA.
- 동시성 / Concurrency: `prepare`·`finalize` 는 동기(DB) — 호출처가 스레드풀에서 부른다. `execute*` 는 async. 스트림은 `_sse` 제너레이터의 `finally` 가 정산(59th).
- 테스트 커버리지 흔적: `test/test_limit_precision.py:64`(C63-02 출력 예약), `:86`(C63-03 진행 중 상한·discard·실패 finalize), `:122`(C63-05 라우팅 시험 예약 없음). 회귀 `test/test_model_gateway2.py`·`test/test_review3_fixes.py`·`test/test_mcp_consent.py`.
- 4차 변경 요약 / Changes in D22: D22-③ — ① `max_tokens`(없으면 256)를 출력 예약·예산 예상 비용에 포함 ② `MODEL_MAX_INFLIGHT_PER_SUBJECT`(기본 32) 보유자 슬롯 ③ `prepare` 뒤 `finalize` 가 없던 4경로(라우팅 시험·샘플링 미리 보기·샘플링 실행 실패·임베딩 대상 없음)에 `discard` — 63rd 가 구현 중 찾은 D20 부터의 예약 누수(`test/impl/63rd/result.md` 결과 요약 첫 줄).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/llm.py lines=82-96
@dataclass
class Plan:
    requested: str
    data_class: str
    targets: list[Target]
    retries: int
    rule: str | None
    notes: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    limit_fallback: bool = False
    rerouted: bool = False
    candidates: list[str] = field(default_factory=list)
    timeout_s: float | None = None  # 라우팅 규칙 timeout_ms — 시도마다 (D20-①)
    holds: list = field(default_factory=list)  # 한도 예약 — finalize 에서 정산 (D20-①)
    inflight: tuple | None = None  # 주체별 진행 중 요청 슬롯 (키, 보유자) — finalize 에서 반납 (D22-③)
```
**분석 / Analysis (Level 2)**
- L95-96: `Plan` 이 정산할 것 두 가지를 들고 다닌다 — `holds`(한도 예약 목록, D20-①)와 `inflight`(진행 중 슬롯 `(키, 보유자 id)`, D22-③). `finalize` 또는 `discard` 가 둘 다 비운다.
- 흔한 실수: `Plan` 은 dataclass 라 `field(default_factory=list)` 를 써야 인스턴스마다 다른 리스트가 생긴다(L89-95 모두 지켜짐). `holds: list = []` 로 쓰면 모든 계획이 같은 리스트를 공유해 예약이 섞인다.

```python path=app/services/llm.py lines=243-284
    est = sum(estimate_tokens(message_text(m)) for m in body.get("messages", []))
    out_est = int(body.get("max_tokens") or body.get("max_completion_tokens") or OUTPUT_TOKENS_DEFAULT)
    est_cost = cost_of(plan.targets[0], est, out_est)
    if (cap := settings.model_max_inflight_per_subject) > 0:  # 한 주체가 큰 요청을 동시에 많이 열어 팀 예산 예약을 잠그지 않게 (D22-③)
        slot = (f"inflight:{ctx.principal.sub}", uuid.uuid4().hex)
        if not ratelimit.backend().holder_acquire(*slot, cap, ratelimit.HOLD_TTL_S):
            raise ApiError(429, "rate_limited", f"동시 요청 상한 초과 ({cap}개) — 진행 중인 요청이 끝난 뒤 다시 시도하세요.",
                           headers={"Retry-After": "5"}, extra={"limit_id": "inflight"})
        plan.inflight = slot
    lim = ratelimit.check(db, ctx.principal, "model", plan.targets[0].model_id, ctx.agent, est, est_cost, output_tokens=out_est)
    plan.holds = lim.holds
    if not lim.allowed:
        release_inflight(plan)
        raise ApiError(429, "rate_limited", lim.reason or "한도 초과", headers={"Retry-After": str(lim.retry_after)},
                       extra={"limit_id": lim.limit_id, "budget": lim.budget})
    if lim.fallback_model:
        fb = db.get(Model, lim.fallback_model)
        tgt = _target(db, fb) if fb else None
        why = "모델이 없거나 비활성" if tgt is None else _fallback_blocked(db, ctx, fb, gap)  # 없으면 원래 모델로 한도 없이 가지 않게 (59th)
        if why:  # 한도 폴백도 정책·허용 등급·에어갭 그대로 (D19-④)
            discard(plan)
            raise ApiError(429, "rate_limited", f"{lim.reason or '한도 초과'} — 폴백 모델 {lim.fallback_model} 은 이 요청에 쓸 수 없습니다: {why}",
                           headers={"Retry-After": str(max(1, lim.retry_after or 60))},
                           extra={"limit_id": lim.limit_id, "budget": lim.budget})
        plan.targets = [tgt] + [t for t in plan.targets if t.model_id != tgt.model_id]
        plan.limit_fallback = True
        plan.notes.append(lim.reason or f"한도 초과 → {lim.fallback_model}")
    return plan


def release_inflight(plan: Plan) -> None:
    if plan.inflight:
        ratelimit.backend().holder_release(*plan.inflight)
        plan.inflight = None


def discard(plan: Plan) -> None:
    """실행하지 않는 계획(미리 보기·라우팅 시험·준비 뒤 거절·호출 실패) — 한도 예약과 진행 중 슬롯을 돌려준다 (D22-③)."""
    ratelimit.release(plan.holds)
    plan.holds = []
    release_inflight(plan)

```
**분석 / Analysis (Level 2)**
- L243-245: 입력 토큰 추정 + **출력 추정**(`max_tokens` → `max_completion_tokens` → 256) → 첫 후보 단가로 예상 비용. 출력까지 넣어 예산 예약이 실제 비용에 가까워졌다(3차 M9).
- L246-251(D22-③): 진행 중 슬롯 확보. 키 = `inflight:<sub>`, 보유자 = 새 uuid, 만료 = `HOLD_TTL_S`(300초, 고정 만료). 상한을 넘으면 429 + `Retry-After: 5`. 목적은 3차 L35 — "한 사람이 큰 `max_tokens` 요청을 동시에 많이 열어 팀 예산 예약을 잠그는 것" 을 동시 수로 막는다.
- L252-257: 한도 검사. 거절이면 진행 중 슬롯을 먼저 반납(L255) — 한도 검사 안에서 쌓은 예약은 `check` 가 스스로 반환한다(`app/services/ratelimit.py:443`).
- L258-266: 폴백 모델이 없거나 쓸 수 없으면 `discard` 후 429(59th M13 수정 유지).
- L273-283 `release_inflight`·`discard`: 둘 다 **멱등** — 비운 뒤 다시 불러도 아무것도 하지 않는다(`plan.holds = []`, `plan.inflight = None`). `finalize` 뒤에 실수로 `discard` 를 불러도 이중 반환이 없다.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M1** L244 `int(body.get("max_tokens") or ...)` — 클라이언트 값 그대로. 형식 확인이 없어 `"max_tokens": "abc"` 는 `ValueError` → 처리되지 않은 500, 음수는 예약량을 음수로 만들어 같은 버킷의 토큰 창을 줄이고(그 요청이 도는 동안 다른 요청이 한도 우회), 매우 큰 값은 자기 요청은 통과시키면서 같은 버킷 전체를 그 분 동안 429 로 만든다(판정 규칙은 [ratelimit.py.md](ratelimit.py.md) `check` 절). MCP 샘플링은 **외부 MCP 서버가 보낸 `maxTokens`** 를 그대로 쓴다(`app/services/mcp_consent.py:93`) — 신뢰하지 않는 서버가 같은 효과를 낸다. 최소 수정: 정수 ≥1 검증(400), `min(값, Model.context_window)`(`app/models.py:615`)로 자르기, 샘플링도 같은 함수로.
- [위험:낮음] **L1** 진행 중 슬롯은 한도 검사(L252) **앞에서** 잡는다. `ratelimit.check` 가 `ApiError` 가 아닌 예외(예: D22-① 문장 상한으로 `spend` 쿼리 취소, Valkey 스크립트 오류)를 던지면 L255 에 닿지 않아 슬롯이 300초 남는다(`check` 안에서 이미 쌓은 예약도). 아래 호출처 누수와 합쳐 300초 안에 32번이면 그 사용자는 최대 5분 429. 최소 수정: L252 를 `try/except BaseException: discard(plan); raise` 로.
- [위험:낮음] **L4** 상한 키가 `ctx.principal.sub` 라 한 사용자의 **모든 클라이언트 키·PAT·채팅 세션·Host 실행**이 32 하나를 나눠 쓴다. 클라이언트 키로 서비스 연동을 하던 팀은 동시 33번째부터 429 를 받는다(13차 전에는 없던 거절). 의도된 상한이지만 키 단위 분리 여부는 결정이 필요하다 — 최소 수정: `client_key` 는 `inflight:key:<token_id>`, 아니면 README 에 "사용자 단위" 명시.

```python path=app/services/llm.py lines=365-376
    def _prep():
        with SessionLocal() as db:
            plan = prepare(db, ctx, req)
            ok = {m.id for m in db.scalars(select(Model).where(Model.id.in_([t.model_id for t in plan.targets])))
                  if "embeddings" in (m.capabilities or [])}
            plan.targets = [t for t in plan.targets if t.model_id in ok]
            if not plan.targets:
                discard(plan)
                raise ApiError(404, "model_not_found", "임베딩을 지원하는 사용 가능한 모델이 없습니다.",
                               extra={"notes": plan.notes})
            return plan
    plan = await run_in_threadpool(_prep)
```
**분석 / Analysis (Level 2)**
- L371-374(D22-③ 경로): 임베딩 지원 대상이 없어 404 를 낼 때 `discard` — 전에는 `prepare` 의 예약·(이제) 진행 중 슬롯이 남았다.

```python path=app/services/llm.py lines=435-479
def finalize(db: Session, ctx: CallContext, plan: Plan, tgt: Target | None, usage: dict | None, latency_ms: float,
             attempts: list[dict], status: str = "ok") -> UsageRecord | None:
    trace_id = current_trace_id()
    fallback_used = plan.limit_fallback or plan.rerouted or bool(
        tgt is not None and plan.candidates and tgt.model_id != plan.candidates[0])
    reason = "; ".join(([f"rule={plan.rule}"] if plan.rule else []) + plan.notes) or "direct"
    db.add(RoutingDecision(id=new_id("rd"), trace_id=trace_id, kind="model", subject=ctx.principal.sub,
                           requested=plan.requested, chosen=tgt.model_id if tgt else None,
                           candidates=[t.model_id for t in plan.targets] or plan.candidates, attempts=attempts,
                           reason=reason[:2000], fallback_used=fallback_used,
                           status=status))
    rec, settled = None, None
    if tgt is not None:
        tin = int((usage or {}).get("prompt_tokens", 0))
        tout = int((usage or {}).get("completion_tokens", 0))
        rec = UsageRecord(id=new_id("use"), trace_id=trace_id, session_id=ctx.session_id, run_id=ctx.run_id,
                          step_id=ctx.step_id, user_sub=ctx.principal.sub, team=ctx.principal.team, agent=ctx.agent, kind="model",
                          client_key=ctx.principal.token_id if ctx.principal.kind == "client_key" else None,
                          target=tgt.model_id, provider=tgt.provider_id, tokens_in=tin, tokens_out=tout,
                          cost=cost_of(tgt, tin, tout), latency_ms=int(latency_ms), status=status,
                          policy_decisions=[{"data_class": plan.data_class, "notes": plan.notes,
                                             "findings": plan.findings}])
        db.add(rec)
        ratelimit.record_tokens(db, ctx.principal, "model", tgt.model_id, tin + tout, ctx.agent,
                                reserved={h[1] for h in plan.holds if h[0] == "tok"})
        settled = tin + tout
        metrics.TOKENS.labels(tgt.model_id, "input").inc(tin)
        metrics.TOKENS.labels(tgt.model_id, "output").inc(tout)
        metrics.COST.labels(tgt.model_id, ctx.principal.team or "-").inc(float(rec.cost))
        cur = trace.get_current_span()
        cur.set_attribute("gen_ai.usage.input_tokens", tin)
        cur.set_attribute("gen_ai.usage.output_tokens", tout)
        cur.set_attribute("anx.cost_usd", float(rec.cost))
    metrics.LLM_CALLS.labels(tgt.model_id if tgt else plan.requested, status, str(bool(fallback_used)).lower()).inc()
    if fallback_used:
        logs.event("info", "gateway", f"모델 라우팅 폴백: {plan.requested} → {tgt.model_id if tgt else '-'}",
                   notes=plan.notes, data_class=plan.data_class)
    db.commit()
    holds, plan.holds = plan.holds, []  # 정산은 사용 기록 커밋 뒤 — 예약 반환과 기록 사이에 둘 다 빠진 틈이 없게 (59th)
    if settled is not None:
        ratelimit.settle(holds, settled)
    elif holds:  # 호출 실패 — 예약 반환
        ratelimit.release(holds)
    release_inflight(plan)
    return rec
```
**분석 / Analysis (Level 2)**
- L472-478: 사용 기록 **커밋 뒤** 정산(59th L10 수정) → 마지막에 진행 중 슬롯 반납(L478, D22-③). 실패(`tgt is None`)면 예약 반환.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L1** L472 `db.commit()` 이 실패하면(DB 장애·D22-① 문장 상한) L473-478 이 돌지 않는다 — 예약·진행 중 슬롯이 만료까지 남는다. 예약은 고정 만료(예산 300초·토큰은 그 분)라 피해는 제한적이다. 최소 수정: L473-478 을 `try/finally` 로 감싸 커밋 실패에도 반환.

```python path=app/api/llm.py lines=56-91
@router.post("/v1/chat/completions")
async def chat_completions(request: Request, body: dict = Body(...), p: Principal = Depends(require("chat:use"))):
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise ApiError(400, "invalid_request_error", "messages 가 필요합니다.")

    def _prep():
        with SessionLocal() as db:
            _key_allows(db, p, body.get("model") or "auto")
            ctx = _ctx(request, p, db, body)
            return ctx, llm.prepare(db, ctx, body)
    ctx, plan = await run_in_threadpool(_prep)
    t0 = time.perf_counter()
    if body.get("stream"):
        return StreamingResponse(_sse(ctx, plan, body, t0), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                          "X-AgentNexus-Trace-Id": current_trace_id() or ""})
    try:
        res, tgt, attempts = await llm.execute(plan, body)
    except ApiError as exc:
        await run_in_threadpool(_final, ctx, plan, None, None, t0, exc.extra.get("attempts", []), "error")
        raise

    def _post():
        with SessionLocal() as db:
            _mask_output(db, res)
            llm.finalize(db, ctx, plan, tgt, res.get("usage"), (time.perf_counter() - t0) * 1000, attempts)
    await run_in_threadpool(_post)
    fallback = plan.limit_fallback or plan.rerouted or tgt.model_id != plan.candidates[0]
    return JSONResponse(res, headers={"X-AgentNexus-Model": tgt.model_id, "X-AgentNexus-Fallback": str(fallback).lower(),
                                      "X-AgentNexus-Trace-Id": current_trace_id() or ""})


def _final(ctx, plan, tgt, usage, t0, attempts, status):
    with SessionLocal() as db:
        llm.finalize(db, ctx, plan, tgt, usage, (time.perf_counter() - t0) * 1000, attempts, status)

```
**분석 / Analysis (Level 2)**
- L61-66: `prepare` 를 스레드풀에서(동기 DB). 여기서 진행 중 슬롯과 예약이 생긴다.
- L68-71: 스트림이면 `StreamingResponse(_sse(...))` 를 돌려주고 끝 — 정산은 `_sse` 의 `finally` 가 맡는다.
- L72-82: 비스트림은 `ApiError` 만 잡아 `_final(..., "error")`(L74-76).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L1** ① L74 는 `ApiError` 만 잡는다(같은 모양: 임베딩 `app/services/llm.py:383-387`, 샘플링 실행 `app/services/mcp_consent.py:195-198`) — 어댑터가 `ProviderError` 로 감싸지 못한 예외(예: 응답 JSON `KeyError`)나 취소(`CancelledError`)면 `finalize` 가 없다. ② 스트림은 `StreamingResponse` 가 제너레이터를 **한 번도 시작하지 않으면**(응답 시작 `send` 단계에서 클라이언트가 이미 끊김 — `prepare` 가 오래 걸려 클라이언트 타임아웃이 먼저 난 경우) `_sse` 의 `finally`(L128-135)도 실행되지 않는다 — 시작하지 않은 async 제너레이터는 닫혀도 본문을 실행하지 않는다. 두 경우 모두 D20 부터 예약이 남던 경로인데, D22-③ 은 여기에 진행 중 슬롯을 더했다(만료 300초). 최소 수정: ① `except BaseException` 으로 넓혀 `_final(..., "error")` 후 재발생 ② `StreamingResponse(..., background=BackgroundTask(...))` 또는 응답 객체에 "정산 완료" 표시를 두고 미완료면 `discard`.

```python path=app/api/llm.py lines=93-136
async def _sse(ctx: llm.CallContext, plan: llm.Plan, body: dict, t0: float):
    tgt, attempts, usage, out_chars = None, [], None, 0
    status, final = "ok", False

    def _usage():
        if usage is not None or tgt is None:
            return usage
        pin = sum(len(json.dumps(m, ensure_ascii=False)) for m in body.get("messages", [])) // 4
        return {"prompt_tokens": pin, "completion_tokens": max(1, out_chars // 4)}
    try:
        try:
            async for kind, payload in llm.execute_stream(plan, body):
                if kind == "start":
                    tgt, attempts = payload
                    continue
                chunk = payload
                chunk["model"] = tgt.model_id
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for ch in chunk.get("choices", []):
                    d = ch.get("delta") or {}
                    if isinstance(d.get("content"), str) and d["content"]:
                        out_chars += len(d["content"])
                        d["content"] = await run_in_threadpool(_mask_delta, d["content"])
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except ApiError as exc:
            status = "error"
            attempts = exc.extra.get("attempts", attempts)
            yield f"data: {json.dumps({'error': {'message': exc.detail, 'type': exc.code, 'attempts': attempts}}, ensure_ascii=False)}\n\n"
        except ProviderError as exc:
            status = "error"
            yield f"data: {json.dumps({'error': {'message': str(exc), 'type': 'upstream_error'}}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        final = True  # 스레드풀 작업은 취소돼도 끝까지 돈다 — 아래 finally 와 두 번 기록하지 않게 먼저 표시
        await run_in_threadpool(_final, ctx, plan, tgt, _usage(), t0, attempts, status)
    finally:
        if not final:  # 클라이언트가 끊음(GeneratorExit·취소) — 받은 만큼 추정해 기록·정산. 기록 없이 끊으면 예산·한도를 피하고
            # 예약도 남는다. 취소 중 await 는 다시 취소되므로 스레드에 넘기고 기다리지 않는다 (59th)
            args = (ctx, plan, tgt, _usage(), t0, attempts, "cancelled")
            try:
                asyncio.get_running_loop().run_in_executor(None, contextvars.copy_context().run, _final, *args)
            except RuntimeError:  # 루프 없음(종료 중) — 동기로
                _final(*args)

```
**분석 / Analysis (Level 2)**
- 59th 의 끊김 정산 — `final` 을 먼저 표시하고(L126), 끊기면 `finally` 에서 받은 만큼 추정(`_usage`, L97-101)해 `cancelled` 로 기록. 취소 중 `await` 는 다시 취소되므로 실행기(executor)에 넘기고 기다리지 않는다(L131-135). `finalize` → `release_inflight`(`app/services/llm.py:478`)까지 이 경로로 반납된다 — D22-③ 슬롯도 끊김에 안전하다.

```python path=app/api/ops.py lines=559-573
@gateway_router.post("/api/v1/gateway/routing/test")
def routing_test(body: RoutingTestIn, p: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """장애 주입 시험 (SCR-H02 ⑥) — 가정한 대상 장애로 어떤 모델이 선택되는지 (호출하지 않음)."""
    from app.services import llm
    try:
        plan = llm.prepare(db, llm.CallContext(p, body.data_class, "routing-test"),
                           {"model": body.model, "messages": [{"role": "user", "content": "routing test"}]})
    except ApiError as exc:
        return {"ok": False, "reason": exc.detail, "candidates": [], "chosen": None}
    llm.discard(plan)  # 호출하지 않는 시험 — 한도 예약을 남기지 않는다 (D22-③)
    ids = [t.model_id for t in plan.targets]
    alive = [m for m in ids if m not in set(body.fail)]
    return {"ok": bool(alive), "rule": plan.rule, "candidates": ids, "failed": [m for m in ids if m in body.fail],
            "chosen": alive[0] if alive else None, "fallback_used": bool(alive) and alive[0] != (ids[0] if ids else None),
            "notes": plan.notes, "reason": None if alive else "모든 후보 장애 — 요청 실패 (MSG-015)"}
```
**분석 / Analysis (Level 2)**
- L568(D22-③): 라우팅 시험은 호출하지 않으므로 `prepare` 직후 `discard`. 전에는 시험 1회마다 실제 토큰 창·예산 예약을 차지했다(C63-05 — `test/test_limit_precision.py:122`).
- 남는 부수효과: `prepare` → `check` 는 rps 버킷을 실제로 차감하고(`app/services/ratelimit.py:398`) 예산 경보 알림(`:423-426`)도 낼 수 있다 — 운영자 화면의 "시험" 이 실제 한도를 조금 쓴다. 등급 없음(관찰).

```python path=app/services/mcp_consent.py lines=91-117
def _body(params: dict, messages: list | None = None) -> dict:
    body = {"model": "auto", "messages": _chat_messages(params, messages),
            "max_tokens": int(params.get("maxTokens") or 256)}
    if params.get("temperature") is not None:
        body["temperature"] = params["temperature"]
    if params.get("stopSequences"):
        body["stop"] = params["stopSequences"]
    return body


def _cctx(server_id: str, ctx) -> llm.CallContext:
    return llm.CallContext(ctx.principal, ctx.data_class or "internal", f"mcp:{server_id}/sampling",
                           ctx.session_id, ctx.run_id)


def _preview(server_id: str, ctx, params: dict) -> dict:
    """동의 카드에 보여 줄 라우팅 — 실제 실행과 같은 prepare 로 계산."""
    with SessionLocal() as db:
        try:
            plan = llm.prepare(db, _cctx(server_id, ctx), _body(params))
        except ApiError as exc:
            return {"error": exc.detail, "data_class": ctx.data_class}
        llm.discard(plan)  # 미리 보기 — 한도 예약을 남기지 않는다 (D22-③)
        return {"model": plan.targets[0].model_id, "category": plan.targets[0].category, "data_class": plan.data_class,
                "rerouted": plan.rerouted, "hints": [h.get("name") for h in
                                                     (params.get("modelPreferences") or {}).get("hints") or []]}

```
**분석 / Analysis (Level 2)**
- L93: MCP 서버가 보낸 `maxTokens`(없으면 256)를 `max_tokens` 로. L113(D22-③): 동의 카드 미리 보기는 실행하지 않으므로 `discard`.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M1(교차)** L93 `int(params.get("maxTokens") or 256)` — 샘플링을 요청한 **외부 MCP 서버** 값이다. 미리 보기만으로도 `prepare` 가 예약했다가 반환하므로 짧지만, 사용자가 허용하면 실제 호출이 그 값으로 예약한다. `"maxTokens": "x"` 면 미리 보기에서 `ValueError`(→ `_preview` 는 `ApiError` 만 잡음 L111) → 샘플링 요청 처리 실패.

```python path=app/services/mcp_consent.py lines=183-206
async def _generate(server_id: str, ctx, params: dict, messages: list | None) -> tuple[str, str]:
    body = _body(params, messages)

    def _prep():
        with SessionLocal() as db:
            c = _cctx(server_id, ctx)
            return c, llm.prepare(db, c, body)
    cctx, plan = None, None
    try:
        cctx, plan = await run_in_threadpool(_prep)
        t0 = time.perf_counter()
        res, tgt, attempts = await llm.execute(plan, body)
    except ApiError as exc:
        if plan is not None:  # 실행 실패 — 예약·진행 중 슬롯 반환 (D22-③)
            llm.discard(plan)
        raise ConsentError(USER_REJECTED, f"게이트웨이가 샘플링 모델 호출을 거부했습니다: {exc.detail}") from exc

    def _fin():
        with SessionLocal() as db:
            text = ((res.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            text = guards.scan_output(db, text).text
            llm.finalize(db, cctx, plan, tgt, res.get("usage"), (time.perf_counter() - t0) * 1000, attempts)
            return text
    return await run_in_threadpool(_fin), tgt.model_id
```
**분석 / Analysis (Level 2)**
- L195-198(D22-③): 실행이 `ApiError` 로 실패하면 `discard` 후 `ConsentError`. 전에는 예약이 남았다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L19** 실패 경로가 `discard` 만 한다 — `/v1/chat/completions` 의 실패 경로(`app/api/llm.py:74-76` → `finalize(..., "error")`)와 달리 `RoutingDecision`·실패 지표(`app/services/llm.py:441-445`, `:468`)가 남지 않아, 샘플링 실패는 관측 화면의 라우팅 결정·오류율에 보이지 않는다. 최소 수정: `discard` 대신 `finalize(db, cctx, plan, None, None, ..., exc.extra.get("attempts", []), "error")`.

## 파일 평가 / File Assessment
- 강점: (1) `prepare` 뒤 실행하지 않는 4경로의 예약 누수를 찾아 `discard` 로 막았다(`app/api/ops.py:568`, `app/services/mcp_consent.py:113`, `:196-197`, `app/services/llm.py:372`). (2) `discard`·`release_inflight` 가 멱등이라 이중 반환이 없다(`app/services/llm.py:273-283`). (3) 진행 중 슬롯도 59th 의 끊김 정산 경로로 반납된다(`app/api/llm.py:128-135` → `app/services/llm.py:478`).
- 위험: (1) [위험:중간] M1 — 검증 없는 `max_tokens`·`maxTokens` 가 예약 산수에 들어간다(`app/services/llm.py:244`, `app/services/mcp_consent.py:93`). (2) [위험:낮음] L1 — 예약·슬롯 누수 경로(`app/services/llm.py:246-256`, `:383-387`, `:472-478`, `app/api/llm.py:68-76`, `app/services/mcp_consent.py:195-198`). 슬롯 키가 그 사용자 sub 라 누수는 주로 **그 사용자 자신**을 막고, 예약은 고정 만료라 Low 로 둔다(위임 CR4-01 은 HIGH — 부록 D). (3) [위험:낮음] L4·L19 — 사용자 단위 상한, 샘플링 실패 미기록.
- 개선 권고(영향순): (1) `max_tokens` 검증·상한 + 출력 예약을 여유분까지만. (2) 슬롯 확보 뒤 모든 예외 경로에서 `discard`. (3) 샘플링 실패는 `finalize(..., "error")`.
- 파일 위험 등급 / Risk grade: **중간** — [위험:중간] 1(M1).
- 재읽기 권고 레벨: 2 — 예약이 생기는 곳(`prepare`)과 사라지는 곳(`finalize`·`discard`·`_sse` finally)이 네 파일에 흩어져 있다. 새 호출처를 만들 때는 이 쌍을 반드시 맞춰야 한다.

## 3차 리뷰 대비 / vs 3rd Review
| 3차 지적 | 4차 상태 | 근거(현재 path:line) |
|---|---|---|
| H2 `_sse` 끊김 시 정산 누락(59th 수정) | 유지 · 진행 중 슬롯도 같은 경로 | `app/api/llm.py:128-135` |
| M9 출력 토큰 미예약 | 해결 · 입력 검증 공백(M1) | `app/services/llm.py:243-245` |
| M13 폴백 대상 부재(59th 수정) | 유지 | `:258-266` |
| L10 정산이 커밋 전(59th 수정) | 유지 · 커밋 실패 시 반환 없음(L1) | `:472-478` |
| L35 한 사람이 팀 예산 잠금 | 동시 수로 완화(32) · 크기로는 여전히 가능(M1) | `:246-251` |
