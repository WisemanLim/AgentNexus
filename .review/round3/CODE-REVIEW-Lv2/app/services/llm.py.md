> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 llm.py.md](../../../../round2/CODE-REVIEW-Lv2/app/services/llm.py.md)

### 03. app/services/llm.py (+ 호출처 app/api/llm.py `_sse`, 사용 기록 컬럼·migration 0020)
**파일 요약 / File Summary**
- 역할 / Role: 모델 게이트웨이(Unified Model API) 코어 — 라우팅 규칙 해석 → 정책·데이터 등급·에어갭 판정 → 한도 검사 → 후보별 재시도·폴백 호출 → 사용 기록·정산. D20-① 로 ① 라우팅 규칙 `timeout_ms` 를 **시도마다** 실제 적용(`_bounded`), ② 한도 예약(`Plan.holds`)을 `finalize` 에서 정산하도록 바뀌었다.
- 진입점 거리 / Distance: 1 — `POST /v1/chat/completions`(`app/api/llm.py:54-83`), 스트리밍 `_sse`(`app/api/llm.py:91-120`), 임베딩(`app/api/llm.py:128-142` → `embed_texts`). 오케스트레이터 단계도 같은 경로를 쓴다.
- LOC: 471 (2차 스냅샷 426 → +45)
- 주요 export(범위 안): `Plan`(+`timeout_s`·`holds`), `prepare`, `_bounded`, `execute`·`execute_embed`·`execute_stream`, `finalize`
- 의존(내부): `app.services.ratelimit`([ratelimit.py.md](ratelimit.py.md)), `policy`, `guards`, `secrets`, `deploy`, `audit`, `app.models`(`UsageRecord`·`RoutingDecision`·`Model`·`Provider`·`RoutingRule`)
- 외부 패키지: OpenTelemetry(span), SQLAlchemy, 표준 asyncio
- 부수효과 / Side effects: 업스트림 HTTP(공급자 어댑터), 사용 기록·라우팅 결정 INSERT, 한도 백엔드 갱신, 메트릭.
- 동시성 / Concurrency: `prepare`·`finalize` 는 동기(스레드풀), `execute*` 는 이벤트 루프. 시간 초과는 `asyncio.wait_for` 가 업스트림 코루틴을 **취소**한다.
- 테스트 커버리지 흔적: `test/test_model_gateway2.py:57-72`(C52-01 시도별 시간·전체 상한), `:74-103`(C52-02 예약·정산), `:105-115`(C52-03), `:117-130`(C52-04). 스트림을 **중간에 끊는** 시험은 없다(2차 보고서 L501 이 이미 지적 — `test/test_llm.py:104-129` 는 끝까지 읽는 경우만).
- 3차 변경 요약: D20-① — `Plan.timeout_s`·`holds`(L91-92), 규칙 시간 전달(L196), 예상 비용 계산·예약 보관(L239-242), 폴백 거절 시 예약 반환(L250), `_bounded`(L265-273) 를 `execute`·`execute_embed`·`execute_stream`(첫 청크)에 적용, `finalize` 의 `record_tokens(reserved)`·`settle`·`release`(L432-445), 사용 기록 `client_key`(L426, `app/models.py:753`, `migrations/versions/0020_usage_client_key.py`).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/llm.py lines=79-92
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
```
**분석 / Analysis (Level 2)**
- `Plan` 은 한 요청의 실행 계획(가변 dataclass). L91 `timeout_s` 는 규칙의 `timeout_ms/1000`, L92 `holds` 는 `ratelimit.check` 가 만든 예약 목록 — **이 객체가 살아 있는 동안 예약도 살아 있다**는 계약이다. `finalize` 가 끝에 `plan.holds = []` 로 비워 두 번 정산되지 않게 한다(L435, L445).
- 흔한 실수: `holds: list = []` 처럼 가변 기본값을 쓰면 모든 Plan 이 같은 리스트를 공유한다 — 여기처럼 `field(default_factory=list)` 가 맞다.

```python path=app/services/llm.py lines=190-196
def prepare(db: Session, ctx: CallContext, body: dict) -> Plan:
    findings = _guard_messages(db, body)
    requested = body.get("model") or "auto"
    ids, rule = resolve(db, requested, ctx.data_class)
    plan = Plan(requested, ctx.data_class, [], (rule.retries if rule else 1), rule.name if rule else None,
                findings=findings, candidates=list(ids),
                timeout_s=(rule.timeout_ms / 1000) if rule is not None and rule.timeout_ms else None)
```
**분석 / Analysis (Level 2)**
- L196(D20-①): 규칙이 있고 `timeout_ms` 가 있으면 초 단위로. 규칙이 없으면 `None` → 공급자 어댑터의 기본 타임아웃(`prov.options.timeout_s`, 기본 60초 — L141)만 적용된다.

```python path=app/services/llm.py lines=239-258
    est = sum(estimate_tokens(message_text(m)) for m in body.get("messages", []))
    est_cost = cost_of(plan.targets[0], est, int(body.get("max_tokens") or body.get("max_completion_tokens") or 0))
    lim = ratelimit.check(db, ctx.principal, "model", plan.targets[0].model_id, ctx.agent, est, est_cost)
    plan.holds = lim.holds
    if not lim.allowed:
        raise ApiError(429, "rate_limited", lim.reason or "한도 초과", headers={"Retry-After": str(lim.retry_after)},
                       extra={"limit_id": lim.limit_id, "budget": lim.budget})
    if lim.fallback_model:
        fb = db.get(Model, lim.fallback_model)
        tgt = _target(db, fb) if fb else None
        if tgt is not None and (why := _fallback_blocked(db, ctx, fb, gap)):  # 한도 폴백도 정책·허용 등급·에어갭 그대로 (D19-④)
            ratelimit.release(plan.holds)
            raise ApiError(429, "rate_limited", f"{lim.reason or '한도 초과'} — 폴백 모델 {fb.id} 은 이 요청에 쓸 수 없습니다: {why}",
                           headers={"Retry-After": str(max(1, lim.retry_after or 60))},
                           extra={"limit_id": lim.limit_id, "budget": lim.budget})
        if tgt is not None:
            plan.targets = [tgt] + [t for t in plan.targets if t.model_id != tgt.model_id]
            plan.limit_fallback = True
            plan.notes.append(lim.reason or f"한도 초과 → {lim.fallback_model}")
    return plan
```
**분석 / Analysis (Level 2)**
- L239-240: 예상 입력 토큰(`estimate_tokens`) + `max_tokens`(없으면 0)로 첫 후보 단가 기준 예상 비용.
- L241-242: 한도 검사 — 통과하면 예약(`holds`)을 Plan 에 보관. 거절이면 `ratelimit.check` 가 이미 예약을 반환했다([ratelimit.py.md](ratelimit.py.md) L354·L366).
- L246-257: 한도 초과 시 폴백 모델 — 정책·허용 등급·에어갭을 다시 판정(D19-④)하고, 막히면 예약을 반환하고 429.
**짚어볼 점 / Watch-outs**
- [위험:중간] **M9(INDEX §5) — 예약이 출력 토큰을 거의 담지 않는다.** L241 에 넘기는 `estimated_tokens` 는 `est`(입력만)이다 — 분당 토큰 한도의 예약에는 출력이 전혀 없다. 예산 예약(L240)도 `max_tokens` 가 없으면(대부분의 OpenAI 호환 클라이언트 기본) 출력 비용 0 으로 계산된다. 실패 시나리오: 분당 100,000 토큰 한도, 입력 1,000 토큰짜리 요청 20개가 동시에 들어오면 예약은 20,000 으로 모두 통과하고, 각 응답이 8,000 토큰이면 실제 사용은 180,000 — D20-① 의 수용 기준 "동시 요청이 함께 한도를 넘지 못한다"(`docs/implementation-plan.md:59`)가 출력이 큰 실제 부하에서는 성립하지 않는다(시험 C52-02 는 예상치를 직접 넣는다, `test/test_model_gateway2.py:78-79`). 최소 수정: `estimated_tokens = est + (max_tokens or 모델별 기본 출력 허용치)`, 예산 예상 비용도 같은 출력 허용치로.
- [위험:중간] **M13(INDEX §5, 인접 코드 — D20 hunk 바로 아래) — 폴백 모델이 없거나 꺼져 있으면 한도 초과 요청이 원래 모델로 진행된다.** `ratelimit.check` 는 `on_exceed=fallback` 한도를 넘으면 그 한도를 "통과" 로 두고 `fallback_model` 만 알려준다(`app/services/ratelimit.py:361-365`). 그런데 L247-248 에서 폴백 모델 행이 없거나(`fb is None`) `_target` 이 `None`(모델·공급자 비활성, mTLS 정책 위반 — L136-145)이면 L249·L254 두 분기를 모두 건너뛰어 **원래 후보 목록 그대로** 반환한다. 실패 시나리오: 팀 예산 초과 시 `cheap-local` 로 폴백하도록 설정 → 운영자가 `cheap-local` 공급자를 점검으로 끔 → 예산을 넘긴 팀의 요청이 비싼 원래 모델로 제한 없이 나간다. 최소 수정: `lim.fallback_model` 이 있는데 `tgt is None` 이면 예약 반환 후 429.

```python path=app/services/llm.py lines=265-306
async def _bounded(aw, plan: Plan):
    """규칙 시간(timeout_ms) 안에서 한 번 시도 (D20-①) — 시간 초과는 재시도·폴백 가능한 공급자 오류(504).
    전체 상한은 시간 × 시도 수(후보 × (재시도+1))."""
    if not plan.timeout_s:
        return await aw
    try:
        return await asyncio.wait_for(aw, plan.timeout_s)
    except asyncio.TimeoutError:
        raise ProviderError(f"시간 초과 ({plan.timeout_s:g}s)", 504, True) from None


async def execute(plan: Plan, body: dict) -> tuple[dict, Target, list[dict]]:
    req = upstream_body(body)
    attempts: list[dict] = []
    for tgt in plan.targets:
        for n in range(max(1, plan.retries + 1)):
            t0 = time.perf_counter()
            with span(f"chat {tgt.model_id}", SpanKind.CLIENT, **_genai_attrs(plan, tgt, n)) as sp:
                try:
                    res = await _bounded(tgt.adapter.chat(req, tgt.upstream), plan)
                    ms = (time.perf_counter() - t0) * 1000
                    _mark(tgt.model_id, True, ms)
                    attempts.append({"model": tgt.model_id, "status": "ok", "latency_ms": round(ms, 1)})
                    res["model"] = tgt.model_id
                    u = res.get("usage") or {}
                    sp.set_attribute("gen_ai.usage.input_tokens", int(u.get("prompt_tokens", 0)))
                    sp.set_attribute("gen_ai.usage.output_tokens", int(u.get("completion_tokens", 0)))
                    sp.set_attribute("gen_ai.response.finish_reasons",
                                     [c.get("finish_reason") or "stop" for c in res.get("choices", [])])
                    return res, tgt, attempts
                except ProviderError as exc:
                    ms = (time.perf_counter() - t0) * 1000
                    _mark(tgt.model_id, False)
                    sp.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)[:120]))
                    sp.set_attribute("error.type", f"upstream_{exc.status}")
                    attempts.append({"model": tgt.model_id, "status": "error", "error": str(exc)[:200],
                                     "upstream_status": exc.status, "latency_ms": round(ms, 1), "try": n + 1})
                    logs.event("warning", "gateway", f"모델 호출 실패 → 재시도/폴백: {tgt.model_id}",
                               model=tgt.model_id, status=exc.status, attempt=n + 1)
                    if not exc.retryable:
                        break
    raise ApiError(502, "upstream_unavailable", "모든 후보 모델 호출이 실패했습니다.", extra={"attempts": attempts})
```
**분석 / Analysis (Level 2)**
- L265-273 `_bounded`(D20-①): 시도 한 번을 규칙 시간 안에서. 시간 초과는 `ProviderError(…, 504, retryable=True)` 로 바꿔 재시도·다음 후보로 넘어가게 한다. `asyncio.wait_for` 는 시간이 지나면 안쪽 코루틴을 **취소**하므로 업스트림 HTTP 연결도 닫힌다(httpx 는 취소 시 연결을 정리한다).
- L279-306: 후보 × (재시도+1) 시도. 52nd r1 에서 "전체 마감" 을 따로 두었다가 폴백이 시작조차 못 하는 설계 오류를 찾아, 시도별 시간만 두고 전체 상한은 "시간 × 시도 수" 로 자연히 묶이게 단순화했다(`test/impl/52nd/result.md` r1·r2).
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L11 — 전체 최악 시간.** 시드 규칙 30초(`README.md:545`) × 후보 3 × 시도 2 = 180초. 일반적인 인그레스·로드밸런서 유휴 타임아웃(60초 전후)보다 길어 클라이언트는 먼저 끊기는데 서버는 남은 시도를 계속하고 비용도 발생할 수 있다. 의도된 설계라면 콘솔 규칙 화면에 "최악 = 시간 × 시도 수" 를 표시하고, 요청 단위 상한(예: `X-AgentNexus-Deadline`)을 받는 것을 검토.

```python path=app/services/llm.py lines=367-395
async def execute_stream(plan: Plan, body: dict) -> AsyncIterator[tuple[str, object]]:
    """('start', (target, attempts)) → ('chunk', dict)… — 첫 청크 전 실패는 다음 후보로 폴백."""
    req = upstream_body(body)
    attempts: list[dict] = []
    for tgt in plan.targets:
        for n in range(max(1, plan.retries + 1)):
            t0 = time.perf_counter()
            agen = tgt.adapter.stream(req, tgt.upstream)
            try:
                with span(f"chat {tgt.model_id} (stream)", SpanKind.CLIENT, **_genai_attrs(plan, tgt, n)):
                    first = await _bounded(agen.__anext__(), plan)  # 규칙 시간 = 첫 청크까지
            except (ProviderError, StopAsyncIteration) as exc:
                with contextlib.suppress(Exception):
                    await agen.aclose()
                _mark(tgt.model_id, False)
                attempts.append({"model": tgt.model_id, "status": "error", "error": str(exc)[:200], "try": n + 1,
                                 "upstream_status": getattr(exc, "status", None)})
                if isinstance(exc, ProviderError) and not exc.retryable:
                    break
                continue
            ttft = (time.perf_counter() - t0) * 1000
            attempts.append({"model": tgt.model_id, "status": "ok", "ttft_ms": round(ttft, 1)})
            _mark(tgt.model_id, True, ttft)
            yield "start", (tgt, attempts)
            yield "chunk", first
            async for chunk in agen:
                yield "chunk", chunk
            return
    raise ApiError(502, "upstream_unavailable", "모든 후보 모델 호출이 실패했습니다.", extra={"attempts": attempts})
```
**분석 / Analysis (Level 2)**
- L377: 스트리밍은 규칙 시간을 **첫 청크까지만** 적용한다(첫 토큰이 오면 이후는 제한 없음). 첫 청크 전 실패는 다음 후보로 폴백, 첫 청크 뒤에는 폴백하지 않는다(이미 클라이언트에 보낸 내용과 섞이지 않게 — 맞는 선택).
- L378-380: `__anext__` 를 `wait_for` 로 감쌌다가 시간이 지나면 안쪽 제너레이터가 취소된다. `agen.aclose()` 는 이미 끝난 제너레이터에도 안전하다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] (2차 이월) L392-393 첫 청크 이후의 `agen` 을 소비자가 중간에 멈춰도 명시적으로 닫지 않는다(2차 보고서 L499 지적, 미해결). 업스트림 연결이 GC 시점까지 남을 수 있다.

```python path=app/api/llm.py lines=91-120
async def _sse(ctx: llm.CallContext, plan: llm.Plan, body: dict, t0: float):
    tgt, attempts, usage, out_chars = None, [], None, 0
    status = "ok"
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
    if usage is None and tgt is not None:
        pin = sum(len(json.dumps(m, ensure_ascii=False)) for m in body.get("messages", [])) // 4
        usage = {"prompt_tokens": pin, "completion_tokens": max(1, out_chars // 4)}
    await run_in_threadpool(_final, ctx, plan, tgt, usage, t0, attempts, status)
```
**분석 / Analysis (Level 2)**
- 스트리밍 응답 제너레이터. 업스트림 청크를 SSE 로 흘리고, 오류는 SSE 오류 이벤트로, 끝나면 `[DONE]` 후 `_final`(= `llm.finalize`) 로 사용 기록·정산.
- L117-119: 공급자가 usage 청크를 주지 않으면 글자 수로 대략 추정.
**짚어볼 점 / Watch-outs**
- [위험:높음] **H2(INDEX §5) — 클라이언트가 끊으면 `finalize` 가 돌지 않는다 → 사용량 미기록 + D20-① 예약 누수.** L120 은 `try` 블록 **뒤**에 있고 `finally` 가 아니다. 클라이언트가 `[DONE]` 전에 연결을 끊으면 Starlette(잠금 버전 1.7.0, `uv.lock:2187-2188`)가 응답 Task 를 취소하거나 제너레이터를 닫는다 — 어느 쪽이든 `CancelledError`·`GeneratorExit` 는 L109·L113 의 `except` 에 걸리지 않아 L120 이 실행되지 않는다(Python 제너레이터 의미론). 결과 ① **사용 기록·라우팅 결정·토큰 창 기록이 빠진다** — 마지막 내용 청크를 받고 바로 끊는 클라이언트는 예산·토큰 한도를 반복해서 우회한다(2차 보고서 L501 이 같은 위치를 지적, 미해결). ② 11차에 새로 생긴 결과: `plan.holds` 의 예산 예약이 반환되지 않고 남는데, 그 카운터는 같은 범위의 다음 요청마다 TTL 이 연장되어 **바쁜 팀에서는 영영 풀리지 않는다**([ratelimit.py.md](ratelimit.py.md) L336-352 H2). 채팅 UI 의 "생성 중지" 같은 일상 동작만으로 누적되어 결국 팀 전원 429. 최소 수정: L94-120 을 `try: … finally:` 로 바꾸고, `finally` 안에서는 `await` 없이 동기로 정산하거나(`mcp_modern.listen_events` 가 같은 이유로 동기 반납을 쓴다 — `app/services/mcp_modern.py:504-507` 주석 "연결이 끊기면 anyio 취소 범위가 finally 안의 await 도 다시 취소해 반납이 빠진다") `anyio.CancelScope(shield=True)` 로 감싼 `run_in_threadpool(_final, …)`. 끊긴 경우의 사용량은 받은 청크까지의 추정치로 기록.
- [위험:낮음] L73-74 비스트리밍 경로도 `ApiError` 만 잡는다 — 어댑터가 `ProviderError` 가 아닌 예외(형식 오류 `KeyError` 등)를 내면 `_final` 이 돌지 않아 같은 예약 누수가 생긴다. 같은 `finally` 원칙으로.

```python path=app/services/llm.py lines=409-451
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
    rec = None
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
                                reserved={k for kind, k, _ in plan.holds if kind == "tok"})
        ratelimit.settle(plan.holds, tin + tout)
        plan.holds = []
        metrics.TOKENS.labels(tgt.model_id, "input").inc(tin)
        metrics.TOKENS.labels(tgt.model_id, "output").inc(tout)
        metrics.COST.labels(tgt.model_id, ctx.principal.team or "-").inc(float(rec.cost))
        cur = trace.get_current_span()
        cur.set_attribute("gen_ai.usage.input_tokens", tin)
        cur.set_attribute("gen_ai.usage.output_tokens", tout)
        cur.set_attribute("anx.cost_usd", float(rec.cost))
    if plan.holds:  # 호출 실패 — 예약 반환
        ratelimit.release(plan.holds)
        plan.holds = []
    metrics.LLM_CALLS.labels(tgt.model_id if tgt else plan.requested, status, str(bool(fallback_used)).lower()).inc()
    if fallback_used:
        logs.event("info", "gateway", f"모델 라우팅 폴백: {plan.requested} → {tgt.model_id if tgt else '-'}",
                   notes=plan.notes, data_class=plan.data_class)
    db.commit()
    return rec
```
**분석 / Analysis (Level 2)**
- L415-419: 성공·실패 모두 라우팅 결정 1건.
- L421-442: 성공(대상 있음)이면 사용 기록(L424-431, L426 클라이언트 키 id — D20-①) → 토큰 창 기록(예약한 창은 건너뜀, L432-433) → 예약 정산(L434) → 메트릭.
- L443-445: 대상이 없으면(호출 실패) 예약 반환.
- L450: 마지막에 커밋.
**짚어볼 점 / Watch-outs**
- [위험:낮음] **L10 — 정산이 커밋보다 먼저다.** L434 `settle` 은 예산 예약을 반환하고 이 워커의 spend 캐시를 비우는데, 이 호출 비용을 담은 사용 기록은 L450 에서야 커밋된다. 그 사이(그리고 커밋이 실패하면 영원히) 같은 워커의 다른 요청이 `spend` 를 읽으면 이 비용이 빠진 합계가 5초 캐시되고 예약도 이미 사라져, 그 5초 동안 예산을 이 호출 비용만큼 넘길 수 있다. 시험은 반대 순서(커밋 → `settle`)로 검증한다(`test/test_model_gateway2.py:96-99`). 최소 수정: `db.commit()` 뒤에 `settle`/`release` 를 부르도록 L432-445 를 재배치(토큰 창 기록도 함께).

```python path=app/models.py lines=750-754
    user_sub: Mapped[str | None] = mapped_column(String(255), index=True)
    team: Mapped[str | None] = mapped_column(String(120))
    agent: Mapped[str | None] = mapped_column(String(120))
    client_key: Mapped[str | None] = mapped_column(String(40), index=True)  # 게이트웨이 클라이언트 키 id — 키 범위 예산 (D20-①)
    kind: Mapped[str] = mapped_column(String(20))  # model | tool | remote_agent
```
```python path=migrations/versions/0020_usage_client_key.py lines=17-26
def upgrade() -> None:
    with op.batch_alter_table('usage_records', schema=None) as b:
        b.add_column(sa.Column('client_key', sa.String(length=40), nullable=True))
        b.create_index('ix_usage_records_client_key', ['client_key'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('usage_records', schema=None) as b:
        b.drop_index('ix_usage_records_client_key')
        b.drop_column('client_key')
```
**분석 / Analysis (Level 2)**
- 사용 기록에 클라이언트 키 id(인덱스)를 추가해 `scope_type=client_key` 예산이 키별 합으로 판정된다(`app/services/ratelimit.py:280-281`). 모델 호출만 채운다(L426) — 도구·원격 에이전트 사용 기록에는 키가 없으므로 클라이언트 키 예산은 **모델 비용만** 센다(도구 비용은 현재 0 이라 실해 없음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L20 PostgreSQL 에서 `CREATE INDEX` 는 기본적으로 테이블 쓰기를 막는다. 사용 기록이 큰 운영 DB 에서는 마이그레이션 동안 모든 모델 호출의 `finalize` 커밋이 대기한다. 대형 설치는 `postgresql_concurrently=True`(트랜잭션 밖, `op.get_context().autocommit_block()`)를 고려.

## 파일 평가 / File Assessment
- 강점: (1) 규칙 시간을 시도 단위로 적용하고 시간 초과를 재시도 가능한 504 로 일관되게 바꿔 폴백 경로가 그대로 동작한다(`app/services/llm.py:265-273`, 시험 `test/test_model_gateway2.py:57-72`). (2) 예약을 Plan 에 묶어 성공·실패·폴백 거절 모든 분기에서 한 번만 정산하도록 정리했다(`app/services/llm.py:242`, `:250`, `:434-445`). (3) 사용 기록에 키 id 를 남겨 클라이언트 키 예산을 사용 기록 하나로 판정한다(`app/services/llm.py:426`).
- 위험: (1) [위험:높음] 스트림 단절 시 `finalize` 미실행 — 사용량 미기록 + 예약 누수(`app/api/llm.py:91-120`). (2) [위험:중간] 예약이 출력 토큰을 담지 않아 동시 요청 초과가 남는다(`app/services/llm.py:239-241`). (3) [위험:중간] 폴백 모델이 없으면 한도 초과 요청이 원래 모델로(`app/services/llm.py:246-257`, 인접 코드).
- 개선 권고(영향순): (1) `_sse`·비스트리밍 경로의 정산을 `finally`(동기 또는 shield)로 — 끊긴 스트림도 추정 사용량 기록. (2) 예상 출력 토큰을 예약에 포함. (3) 폴백 대상이 없으면 429. (4) 커밋 뒤 정산.
- 파일 위험 등급 / Risk grade: 높음 — [위험:높음] 1건(호출처 `_sse` 와 공동), [위험:중간] 2건.
- 재읽기 권고 레벨: 2 — 예약·정산 계약이 `ratelimit` 과 `app/api/llm.py` 에 걸쳐 있어, 정상·오류·취소 세 갈래를 줄 단위로 추적해야 한다.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 (2차 INDEX §5) | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| M34 라우팅 규칙 `timeout_ms` 미적용 (`llm.py:137`) | 해결 | `app/services/llm.py:196`, `:265-273`, `:284`, `:318`, `:377` · 시험 `test/test_model_gateway2.py:57-72` |
| M35 한도·예산 예약 없는 사전 검사 (`llm.py:221-225`) | 부분 해결 | 선가산·진행 중 예약 `app/services/ratelimit.py:326-352`. 출력 토큰 미예약(M9), 정산 누락(H2) 남음 |
| 2차 보고서 L501 교차 — `_sse` 의 `_final` 이 `try` 뒤(단절 시 기록 누락) | 미해결 + 영향 확대 | `app/api/llm.py:94-120` 그대로. 11차 예약이 더해져 누수가 팀 전체 429 로 번진다(H2) |
| 2차 보고서 L499 — `agen` 명시적 닫기 없음 | 미해결 | `app/services/llm.py:392-393` |
| H7 한도 폴백의 데이터 등급 우회 | 해결 유지 | `app/services/llm.py:249-253`(D19-④). 단 폴백 대상 부재 분기는 M13 |
