> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 28. app/services/llm.py
**파일 요약 / File Summary**
- 역할 / Role: Unified Model API 실행 — `prepare`(동기·DB: 입력 가드 → 라우팅 규칙·전략 → 정책·데이터 등급 재라우팅 → 공급자 허용 등급·에어갭 → 한도·예산·폴백), `execute`/`execute_stream`/`execute_embed`(비동기: 후보 순서 재시도·폴백·서킷 브레이커·지연 EWMA), `finalize`(사용량·비용·결정 로그·메트릭).
- 진입점 거리 / Distance: 2 (`app/api/llm.py`, `app/services/mcp_consent.py:104,182-186`, `app/api/observe.py:244`)
- LOC: 425
- 주요 export: `prepare`, `execute`, `execute_stream`, `execute_embed`, `embed_texts`, `finalize`, `cost_of`, `resolve`, `CallContext`, `Plan`, `Target`, `provider_health`, `reset_state`, `model_out`, `sync_model_catalog`, `default_data_class`, `upstream_body`
- 의존(내부): `app.core.{logs,metrics,auth,tracing,context,errors,ids}`, `app.db`, `app.models`, `app.services.{audit,guards,policy,ratelimit,secrets,providers,deploy}`
- 외부 패키지: sqlalchemy, opentelemetry, 표준 `random`·`decimal`
- 부수효과 / Side effects: 공급자 HTTP(어댑터), DB 쓰기(사용량·결정 로그·토큰 한도), 메트릭, 프로세스 전역 `_FAILS`·`_LATENCY`
- 동시성 / Concurrency: async 실행 + 동기 prepare/finalize(스레드풀), 전역 dict 상태(락 없음)
- 테스트 커버리지 흔적: `test/test_llm.py`, `test/test_gateway_policy.py`, `test/test_mtls.py`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/llm.py lines=1-33
"""Unified Model API 실행 (FR-G06·G01·G04, FR-L00~L02).

prepare(동기·DB): 입력 가드 → 라우팅 규칙·전략으로 후보 → 정책(데이터 분류) 거부 시 재라우팅 → 한도·예산(초과 시 429/폴백)
execute(비동기): 후보 순서대로 재시도·폴백 (스트리밍은 첫 청크 전까지 폴백) → finalize(동기·DB): 사용량·비용·결정 로그
"""
import fnmatch
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from app.core import logs, metrics
from app.core.auth import Principal
from app.core.tracing import span
from app.core.context import current_trace_id
from app.core.errors import ApiError
from app.core.ids import new_id
from app.db import SessionLocal
from app.models import CatalogItem, Model, Provider, RoutingDecision, RoutingRule, Setting, UsageRecord
from app.services import audit, guards, policy, ratelimit, secrets
from app.services.providers import Adapter, ProviderError, adapter_for, estimate_tokens, message_text

PASSTHROUGH = {"messages", "temperature", "top_p", "max_tokens", "max_completion_tokens", "tools", "tool_choice",
               "response_format", "stop", "n", "presence_penalty", "frequency_penalty", "seed", "user",
               "parallel_tool_calls", "logit_bias", "logprobs", "top_logprobs"}
MILLION = Decimal(1_000_000)
```
**분석 / Analysis (Level 2)**
- L1-5: 3단계 구조 — DB 작업(prepare·finalize)은 동기, 네트워크(execute)는 비동기로 분리해 스레드풀·이벤트 루프 경계를 명확히 한다.
- L30-32 `PASSTHROUGH`: 업스트림에 넘길 OpenAI 필드 화이트리스트 — 알 수 없는 필드(예: 게이트웨이 전용)가 공급자로 새지 않게.
- L33: 가격 단위(1M 토큰).

```python path=app/services/llm.py lines=35-63
# ── 공급자 상태 (프로세스 내) — 서킷 브레이커 · 지연 EWMA ──
_FAILS: dict[str, tuple[int, float]] = {}
_LATENCY: dict[str, float] = {}
CB_THRESHOLD, CB_OPEN_S = 3, 30.0


def _circuit_open(model_id: str) -> bool:
    n, ts = _FAILS.get(model_id, (0, 0.0))
    return n >= CB_THRESHOLD and time.monotonic() - ts < CB_OPEN_S


def _mark(model_id: str, ok: bool, latency_ms: float | None = None) -> None:
    if ok:
        _FAILS.pop(model_id, None)
        if latency_ms is not None:
            prev = _LATENCY.get(model_id)
            _LATENCY[model_id] = latency_ms if prev is None else prev * 0.7 + latency_ms * 0.3
    else:
        n, _ = _FAILS.get(model_id, (0, 0.0))
        _FAILS[model_id] = (n + 1, time.monotonic())


def provider_health() -> dict:
    return {"circuit_open": [m for m in _FAILS if _circuit_open(m)], "latency_ewma_ms": dict(_LATENCY)}


def reset_state() -> None:
    _FAILS.clear()
    _LATENCY.clear()
```
**분석 / Analysis (Level 2)**
- L36-38: 모델별 (연속 실패 수, 마지막 실패 시각), 지연 EWMA, 서킷 임계 3회·30초.
- L41-43 `_circuit_open`: 3회 이상 실패하고 30초 이내면 열림.
- L46-54 `_mark`: 성공이면 실패 기록 삭제 + 지연 EWMA(0.7·이전 + 0.3·현재), 실패면 카운트 증가.
- L57-63: 상태 조회·초기화(시험).
**짚어볼 점 / Watch-outs**
- 30초 후 반열림(half-open) 상태에서 실패 카운트가 그대로라 **한 번만 더 실패해도** 다시 열린다 — 의도된 단순화.
- 상태가 워커별 — 워커 N 개면 각자 3회씩 실패해야 열린다.

```python path=app/services/llm.py lines=66-103
@dataclass
class Target:
    model_id: str
    upstream: str
    category: str
    provider_id: str
    adapter: Adapter
    price_in: Decimal
    price_out: Decimal


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


@dataclass
class CallContext:
    principal: Principal
    data_class: str = "internal"
    agent: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None


def default_data_class(db: Session) -> str:
    row = db.get(Setting, "gateway.default_data_class")
    return row.value if row and row.value in policy.DATA_CLASSES else "internal"
```
**분석 / Analysis (Level 2)**
- L66-75 `Target`: 실행 대상(모델 id·업스트림 이름·분류·공급자·어댑터·가격).
- L78-88 `Plan`: 요청 모델·데이터 등급·후보·재시도·규칙·노트·가드 발견·폴백 여부.
- L91-98 `CallContext`. L101-103 `default_data_class`: 설정값이 유효 분류가 아니면 internal.

```python path=app/services/llm.py lines=106-130
def resolve(db: Session, requested: str, data_class: str) -> tuple[list[str], RoutingRule | None]:
    rules = db.scalars(select(RoutingRule).where(RoutingRule.enabled.is_(True))
                       .order_by(RoutingRule.priority, RoutingRule.created_at)).all()
    rule = next((r for r in rules if fnmatch.fnmatchcase(requested, r.match_model or "*")
                 and r.match_data_class in (None, "", data_class)), None)
    if rule is None:
        return [requested], None
    targets = [t["model"] for t in rule.targets or []]
    if rule.strategy == "cost":
        price = {m.id: (m.price_in or 0) + (m.price_out or 0) for m in db.scalars(select(Model)
                                                                                .where(Model.id.in_(targets)))}
        targets.sort(key=lambda m: price.get(m, Decimal("1e9")))
    elif rule.strategy == "latency":
        targets.sort(key=lambda m: _LATENCY.get(m, 0.0))
    elif rule.strategy == "quality":  # 품질 점수(운영자 평가·피드백) 높은 순, 미평가는 0.5
        q = {m.id: (m.quality if m.quality is not None else 0.5)
             for m in db.scalars(select(Model).where(Model.id.in_(targets)))}
        targets.sort(key=lambda m: -q.get(m, 0.5))
    elif rule.strategy == "ab" and targets:
        weights = [float(t.get("weight", 1)) for t in rule.targets]
        first = random.choices(targets, weights=weights, k=1)[0]
        targets = [first] + [t for t in targets if t != first]
    ordered = targets + [f for f in rule.fallbacks or [] if f not in targets]
    healthy = [m for m in ordered if not _circuit_open(m)]
    return healthy + [m for m in ordered if m not in healthy], rule
```
**분석 / Analysis (Level 2)**
- L106-130 `resolve`: 우선순위 순 첫 일치 규칙(모델 패턴·데이터 등급) → 전략별 정렬(cost: 입력+출력 단가, latency: EWMA, quality: 점수(미평가 0.5), ab: 가중 무작위 첫 후보) → 폴백 추가 → 서킷 닫힌 후보 먼저.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 버그] L124-127 `random.choices(targets, weights=...)` — 가중치 합이 0 이하(모두 0)이거나 `weight` 가 숫자로 변환되지 않으면(`float("abc")`) `ValueError` 로 **모든 요청이 500** 이 된다. 규칙 생성 API 는 `model` 키만 확인한다(`app/api/llm.py:376-379`). 저장 시 `weight > 0` 검증이 필요하다.
- L119 latency 전략에서 측정값이 없는 모델은 0.0 — **처음 보는 모델이 항상 가장 빠른 것으로** 정렬된다(탐색 효과는 있으나 의도인지 명시 필요).
- L117 단가 정렬은 입력·출력 단가 합 — 실제 비용은 토큰 비율에 따라 다르다(근사).

```python path=app/services/llm.py lines=133-164
def _target(db: Session, model: Model) -> Target | None:
    prov = db.get(Provider, model.provider_id)
    if prov is None or not prov.enabled or not model.enabled:
        return None
    key = secrets.get_plain(db, prov.secret_ref)
    timeout = float((prov.options or {}).get("timeout_s", 60))
    try:
        adapter = adapter_for(prov, key, timeout)
    except ProviderError:  # mTLS 필수 정책 위반 등 — 이 공급자는 후보에서 제외(fail closed)
        return None
    return Target(model.id, model.upstream_model, model.category, prov.id, adapter,
                  Decimal(model.price_in or 0), Decimal(model.price_out or 0))


def _guard_messages(db: Session, body: dict) -> list[str]:
    findings: list[str] = []
    for m in body.get("messages", []):
        if m.get("role") not in ("user", "system", "developer", "tool"):
            continue
        text = message_text(m)
        if not text:
            continue
        r = guards.scan_input(db, text)
        if r.blocked:
            raise ApiError(400, "input_blocked", r.reason or "입력이 차단되었습니다.", extra={"findings": r.types})
        findings += r.types
        if r.text != text:
            if isinstance(m.get("content"), str):
                m["content"] = r.text
            else:
                m["content"] = [{"type": "text", "text": r.text}]
    return sorted(set(findings))
```
**분석 / Analysis (Level 2)**
- L133-144 `_target`: 공급자·모델 활성 확인, 시크릿 조회, 어댑터 생성(mTLS 정책 위반이면 후보 제외 — fail closed).
- L147-164 `_guard_messages`: user·system·developer·tool 메시지에 입력 가드(인젝션 차단·PII 마스킹), 차단이면 400, 마스킹되면 메시지 내용 교체.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L150-151 `assistant` 메시지는 검사하지 않는다 — 클라이언트가 대화 이력에 **가짜 assistant 턴**을 넣어 인젝션·PII 를 보낼 수 있다(OpenAI API 는 클라이언트가 이력을 통째로 보내는 구조). 입력은 모든 역할을 검사하는 편이 안전하다.
- [위험:낮음 · 버그] L162-163 내용이 리스트(멀티모달)인데 마스킹이 일어나면 `[{"type": "text", "text": ...}]` 로 **통째로 교체**해 이미지 파트가 사라진다. 텍스트 파트만 교체해야 한다.
- L137 `secrets.get_plain` 이 후보마다 복호화 — 후보 수만큼 Fernet 복호화(가벼움).

```python path=app/services/llm.py lines=167-232
def prepare(db: Session, ctx: CallContext, body: dict) -> Plan:
    findings = _guard_messages(db, body)
    requested = body.get("model") or "auto"
    ids, rule = resolve(db, requested, ctx.data_class)
    plan = Plan(requested, ctx.data_class, [], (rule.retries if rule else 1), rule.name if rule else None,
                findings=findings, candidates=list(ids))
    seen: set[str] = set()
    queue = list(ids)
    denied_reason = None
    from app.services import deploy
    gap = deploy.airgap(db)
    while queue:
        mid = queue.pop(0)
        if mid in seen:
            continue
        seen.add(mid)
        model = db.get(Model, mid)
        if model is None:
            plan.notes.append(f"{mid}: 미등록 모델")
            continue
        dec = policy.evaluate(db, policy.PolicyInput("model", mid, ctx.principal, data_class=ctx.data_class,
                                                     agent=ctx.agent, attrs={"category": model.category}))
        if not dec.allow:
            denied_reason = dec.reason
            plan.notes.append(f"{mid}: 정책 거부 — {dec.reason}")
            if dec.reroute and dec.reroute not in seen:
                queue.insert(0, dec.reroute)
                plan.rerouted = True
                plan.notes.append(f"데이터 분류({ctx.data_class}) 재라우팅 → {dec.reroute}")
            continue
        tgt = _target(db, model)
        if tgt is None:
            plan.notes.append(f"{mid}: 공급자/모델 비활성")
            continue
        allowed = (db.get(Provider, model.provider_id).options or {}).get("data_classes")
        if allowed and ctx.data_class not in allowed:  # 공급자 허용 데이터 등급 (SCR-I02 ⑤) — 모델 정책보다 좁힐 수만 있다
            plan.notes.append(f"{mid}: 공급자 허용 등급 밖 ({ctx.data_class} ∉ {allowed})")
            denied_reason = denied_reason or f"공급자 {model.provider_id} 는 {ctx.data_class} 데이터를 받지 않습니다."
            continue
        if gap and deploy.is_commercial(db.get(Provider, model.provider_id).kind, model.category):
            plan.notes.append(f"{mid}: 에어갭 모드 — 상용 모델 제외 (ST-09)")
            denied_reason = denied_reason or "에어갭 모드에서는 상용 모델을 쓸 수 없습니다."
            continue
        plan.targets.append(tgt)
    if not plan.targets:
        if denied_reason:
            audit.append("gateway.model.deny", actor=ctx.principal.sub, actor_kind=ctx.principal.kind,
                         target=requested, decision="deny", data={"reason": denied_reason, "data_class": ctx.data_class})
            metrics.POLICY_DENIES.labels("model", "model_denied").inc()
            logs.event("warning", "gateway", f"모델 정책 거부: {requested}", reason=denied_reason)
            raise ApiError(403, "model_denied", f"모델 호출이 정책으로 거부되었습니다: {denied_reason}",
                           extra={"notes": plan.notes})
        raise ApiError(404, "model_not_found", f"사용 가능한 모델이 없습니다: {requested}", extra={"notes": plan.notes})
    est = sum(estimate_tokens(message_text(m)) for m in body.get("messages", []))
    lim = ratelimit.check(db, ctx.principal, "model", plan.targets[0].model_id, ctx.agent, est)
    if not lim.allowed:
        raise ApiError(429, "rate_limited", lim.reason or "한도 초과", headers={"Retry-After": str(lim.retry_after)},
                       extra={"limit_id": lim.limit_id, "budget": lim.budget})
    if lim.fallback_model:
        fb = db.get(Model, lim.fallback_model)
        tgt = _target(db, fb) if fb else None
        if tgt is not None:
            plan.targets = [tgt] + [t for t in plan.targets if t.model_id != tgt.model_id]
            plan.limit_fallback = True
            plan.notes.append(lim.reason or f"한도 초과 → {lim.fallback_model}")
    return plan
```
**분석 / Analysis (Level 2)**
- L168-172: 가드 → 후보 해석 → Plan.
- L178-210: 후보 큐 순회 — 미등록 제외, **정책 판정**(모델·데이터 등급·호출 에이전트·분류), 거부 시 재라우팅 대상(`data_class_routing`)을 큐 앞에 넣는다(내부 데이터 → 로컬 모델), 공급자·모델 비활성 제외, 공급자 허용 데이터 등급(모델 정책보다 좁히기만), 에어갭 모드면 상용 모델 제외.
- L211-219: 후보가 없으면 정책 거부(403·감사·메트릭) 또는 404.
- L220-231: 토큰 추정 → 첫 후보 기준 한도·예산 확인 → 초과면 429, 폴백 모델이면 맨 앞으로.
**짚어볼 점 / Watch-outs**
- L187 `ctx.data_class` 는 하류 헤더(`X-AgentNexus-Data-Class`)에서 오는 경우가 있다(`app/api/mcp.py:58` 등) — 클라이언트가 `public` 으로 낮춰 신고하면 데이터 등급 재라우팅·거부 정책을 피할 수 있다. 분류를 신고에 의존하는 설계의 한계(서버 측 분류기·최소 등급 하한 필요).
- L221 한도는 **첫 후보 모델** 기준으로만 확인 — 폴백으로 다른(더 비싼) 모델이 실제 사용되면 그 모델의 한도는 검사되지 않는다.
- L201·L206 `db.get(Provider, ...)` 반복 — identity map 캐시로 쿼리는 1회.

```python path=app/services/llm.py lines=235-269
def upstream_body(body: dict) -> dict:
    return {k: v for k, v in body.items() if k in PASSTHROUGH}


async def execute(plan: Plan, body: dict) -> tuple[dict, Target, list[dict]]:
    req = upstream_body(body)
    attempts: list[dict] = []
    for tgt in plan.targets:
        for n in range(max(1, plan.retries + 1)):
            t0 = time.perf_counter()
            with span(f"chat {tgt.model_id}", SpanKind.CLIENT, **_genai_attrs(plan, tgt, n)) as sp:
                try:
                    res = await tgt.adapter.chat(req, tgt.upstream)
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
- L235-236 `upstream_body`: 화이트리스트 필드만.
- L239-269 `execute`: 후보마다 `retries+1` 회 — CLIENT 스팬(GenAI 속성) 안에서 어댑터 호출, 성공이면 서킷·EWMA 갱신·응답 모델 id 를 게이트웨이 별칭으로 덮어쓰고 사용량 속성 기록, `ProviderError` 면 실패 기록·재시도 불가 오류는 다음 후보로. 모두 실패면 502.
**짚어볼 점 / Watch-outs**
- L258 `ProviderError` 만 잡는다 — 어댑터가 다른 예외(파싱 오류 등)를 던지면 폴백 없이 즉시 실패하고 서킷에도 기록되지 않는다(어댑터 구현 `app/services/providers.py` 가 모두 감싸는지는 본 리뷰 범위 밖).
- L243 재시도 사이 대기(backoff)가 없다 — 429·503 에 즉시 재시도해 공급자 부하를 키운다.
- L251 응답의 `model` 을 게이트웨이 별칭으로 바꾼다 — 클라이언트에게 업스트림 모델명을 숨기는 의도.

```python path=app/services/llm.py lines=272-327
async def execute_embed(plan: Plan, inputs: list[str]) -> tuple[dict, Target, list[dict]]:
    """임베딩 (OpenAI /v1/embeddings 호환) — 채팅과 같은 후보·재시도·폴백·회로 차단. 임베딩 미지원 대상은 건너뛴다."""
    attempts: list[dict] = []
    for tgt in plan.targets:
        for n in range(max(1, plan.retries + 1)):
            t0 = time.perf_counter()
            with span(f"embeddings {tgt.model_id}", SpanKind.CLIENT, **{**_genai_attrs(plan, tgt, n),
                                                                      "gen_ai.operation.name": "embeddings"}) as sp:
                try:
                    res = await tgt.adapter.embed(inputs, tgt.upstream)
                    ms = (time.perf_counter() - t0) * 1000
                    _mark(tgt.model_id, True, ms)
                    attempts.append({"model": tgt.model_id, "status": "ok", "latency_ms": round(ms, 1)})
                    res["model"] = tgt.model_id
                    sp.set_attribute("gen_ai.usage.input_tokens", int((res.get("usage") or {}).get("prompt_tokens", 0)))
                    return res, tgt, attempts
                except ProviderError as exc:
                    _mark(tgt.model_id, exc.status < 500 and exc.status != 429)
                    sp.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)[:120]))
                    attempts.append({"model": tgt.model_id, "status": "error", "error": str(exc)[:200],
                                     "upstream_status": exc.status, "try": n + 1})
                    if not exc.retryable:
                        break
    raise ApiError(502, "upstream_unavailable", "임베딩 후보 모델 호출이 모두 실패했습니다.", extra={"attempts": attempts})


async def embed_texts(ctx: CallContext, model: str, inputs: list[str]) -> tuple[dict, str]:
    """임베딩 공통 경로 (/v1/embeddings·지식 베이스) — 입력 가드·데이터 등급 라우팅·임베딩 지원 대상만·사용량 기록."""
    from starlette.concurrency import run_in_threadpool
    req = {"model": model or "embed-local", "messages": [{"role": "user", "content": x} for x in inputs]}

    def _prep():
        with SessionLocal() as db:
            plan = prepare(db, ctx, req)
            ok = {m.id for m in db.scalars(select(Model).where(Model.id.in_([t.model_id for t in plan.targets])))
                  if "embeddings" in (m.capabilities or [])}
            plan.targets = [t for t in plan.targets if t.model_id in ok]
            if not plan.targets:
                raise ApiError(404, "model_not_found", "임베딩을 지원하는 사용 가능한 모델이 없습니다.",
                               extra={"notes": plan.notes})
            return plan
    plan = await run_in_threadpool(_prep)
    masked = [m["content"] for m in req["messages"]]  # 입력 가드가 마스킹한 본문
    t0 = time.perf_counter()

    def _fin(tgt, usage, attempts, status):
        with SessionLocal() as db:
            finalize(db, ctx, plan, tgt, usage, (time.perf_counter() - t0) * 1000, attempts, status)
    try:
        res, tgt, attempts = await execute_embed(plan, masked)
    except ApiError as exc:
        await run_in_threadpool(_fin, None, None, exc.extra.get("attempts", []), "error")
        raise
    await run_in_threadpool(_fin, tgt, {"prompt_tokens": (res.get("usage") or {}).get("prompt_tokens", 0),
                                        "completion_tokens": 0}, attempts, "ok")
    return res, tgt.model_id
```
**분석 / Analysis (Level 2)**
- L272-295 `execute_embed`: 임베딩 — 채팅과 같은 후보·재시도·폴백.
- L298-327 `embed_texts`: 입력을 메시지로 감싸 `prepare` 를 재사용(가드·라우팅·한도) → 임베딩 지원 모델만 남김 → **마스킹된 본문**으로 실행 → 사용량 기록(실패 시에도).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 불일치] L289 임베딩은 `exc.status < 500 and exc.status != 429` 면 **성공으로 표시**(`_mark(..., True)`)한다 — 4xx 응답이 실패 카운트를 초기화한다. 채팅(L260)은 모든 ProviderError 를 실패로 센다. 두 경로의 서킷 의미가 다르다.
- L314 가드가 마스킹한 본문을 임베딩한다 — 원문 검색 정확도가 떨어질 수 있다(PII 보호와의 의도된 교환).

```python path=app/services/llm.py lines=330-362
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
                    first = await agen.__anext__()
            except (ProviderError, StopAsyncIteration) as exc:
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


def _genai_attrs(plan: Plan, tgt: Target, attempt: int) -> dict:
    return {"gen_ai.operation.name": "chat", "gen_ai.system": tgt.provider_id, "gen_ai.request.model": plan.requested,
            "gen_ai.response.model": tgt.model_id, "anx.upstream_model": tgt.upstream, "anx.attempt": attempt + 1,
            "anx.data_class": plan.data_class}
```
**분석 / Analysis (Level 2)**
- L330-356 `execute_stream`: 첫 청크 **전** 실패만 다음 후보로 폴백(사용자에게 일부가 이미 보이면 바꿀 수 없음), 첫 청크 후 `start`(대상·시도) → 청크 중계.
- L359-362 `_genai_attrs`: OpenTelemetry GenAI 시맨틱 컨벤션 속성.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 자원] L337 비동기 제너레이터 `agen` 을 첫 청크 이후 소비자가 중간에 멈추면(클라이언트 연결 종료) 명시적 `aclose()` 가 없다 — 업스트림 스트림(HTTP 연결) 정리가 GC 의 async generator finalizer 에 맡겨진다. `try/finally: await agen.aclose()` 권장.
- L341 `StopAsyncIteration`(빈 스트림)도 실패로 처리해 폴백 — 합리적.

```python path=app/services/llm.py lines=365-405
def cost_of(tgt: Target, tokens_in: int, tokens_out: int) -> Decimal:
    c = (Decimal(tokens_in) * tgt.price_in + Decimal(tokens_out) * tgt.price_out) / MILLION
    return c.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


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
                          target=tgt.model_id, provider=tgt.provider_id, tokens_in=tin, tokens_out=tout,
                          cost=cost_of(tgt, tin, tout), latency_ms=int(latency_ms), status=status,
                          policy_decisions=[{"data_class": plan.data_class, "notes": plan.notes,
                                             "findings": plan.findings}])
        db.add(rec)
        ratelimit.record_tokens(db, ctx.principal, "model", tgt.model_id, tin + tout, ctx.agent)
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
    return rec
```
**분석 / Analysis (Level 2)**
- L365-367 `cost_of`: Decimal 단가 × 토큰 / 1M, 소수 6자리 반올림.
- L370-405 `finalize`: 결정 로그(요청·선택·후보·시도·사유·폴백), 사용량(토큰·비용·지연·정책 노트), 토큰 한도 기록, 메트릭·현재 스팬 속성, 폴백 이벤트 로그 → 커밋.
**짚어볼 점 / Watch-outs**
- L373-374 폴백 판정 = 한도 폴백 또는 재라우팅 또는 첫 후보와 다른 모델 — 정의가 명확하다.
- L395 `float(rec.cost)` 로 Prometheus 카운터 — Decimal → float 변환은 메트릭에만 쓰여 정밀도 문제는 없다.

```python path=app/services/llm.py lines=408-425
def model_out(m: Model) -> dict:
    return {"id": m.id, "provider_id": m.provider_id, "upstream_model": m.upstream_model,
            "display_name": m.display_name or m.id, "category": m.category, "data_classes": m.data_classes,
            "price_in": float(m.price_in), "price_out": float(m.price_out), "context_window": m.context_window,
            "capabilities": m.capabilities, "quality": m.quality, "enabled": m.enabled}


def sync_model_catalog(db: Session, m: Model, actor: str) -> None:
    """모델도 통합 카탈로그 항목(type=model)으로 노출 (FR-P01)."""
    item = db.scalars(select(CatalogItem).where(CatalogItem.type == "model", CatalogItem.name == m.id)).first()
    meta = model_out(m)
    status = "active" if m.enabled else "approved"
    if item is None:
        db.add(CatalogItem(id=new_id("cat"), type="model", name=m.id, display_name=m.display_name or m.id,
                           description=f"{m.provider_id} · {m.upstream_model}", source="manual", status=status,
                           version="1", item_metadata=meta, owner=actor, ref_id=m.id, tags=[m.category]))
    else:
        item.item_metadata, item.status = meta, status
```
**분석 / Analysis (Level 2)**
- L408-412 `model_out`: 모델 API 표현.
- L415-425 `sync_model_catalog`: 모델을 카탈로그 항목(type=model)으로 동기화 — 활성이면 active, 아니면 approved.

## 파일 평가 / File Assessment
- 강점: (1) 정책·데이터 등급 재라우팅·공급자 등급·에어갭을 후보 단계에서 일관 적용(fail closed). (2) 스트리밍 폴백을 첫 청크 전으로 한정하는 정확한 경계. (3) 비용을 Decimal 로 계산.
- 위험: (1) [낮음 · 버그] ab 가중치 검증 부재로 전체 500. (2) [낮음] assistant 메시지 미검사·멀티모달 파트 손실·데이터 등급 자기 신고. (3) [낮음] 백오프 없는 재시도, 스트림 제너레이터 미정리, 서킷 의미 불일치.
- 개선 권고(영향순): (1) 라우팅 규칙 저장 시 가중치 검증(+ `resolve` 방어 코드). (2) 모든 역할 입력 가드·텍스트 파트만 교체. (3) 지수 백오프, `aclose()`, 서킷 판정 통일.
- 재읽기 권고 레벨: 3.
