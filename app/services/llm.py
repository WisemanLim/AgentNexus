"""Unified Model API 실행 (FR-G06·G01·G04, FR-L00~L02).

prepare(동기·DB): 입력 가드 → 라우팅 규칙·전략으로 후보 → 정책(데이터 분류) 거부 시 재라우팅 → 한도·예산(초과 시 429/폴백)
execute(비동기): 후보 순서대로 재시도·폴백 (스트리밍은 첫 청크 전까지 폴백) → finalize(동기·DB): 사용량·비용·결정 로그
"""
import asyncio
import contextlib
import fnmatch
import random
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from app.core import logs, metrics
from app.core.auth import Principal
from app.core.config import settings
from app.core.tracing import span
from app.core.context import current_trace_id
from app.core.errors import ApiError
from app.core.ids import new_id
from app.db import SessionLocal
from app.models import CatalogItem, Model, Provider, RoutingDecision, RoutingRule, UsageRecord
from app.services import audit, guards, policy, ratelimit, secrets
from app.services.providers import Adapter, ProviderError, adapter_for, estimate_tokens, message_text

PASSTHROUGH = {"messages", "temperature", "top_p", "max_tokens", "max_completion_tokens", "tools", "tool_choice",
               "response_format", "stop", "n", "presence_penalty", "frequency_penalty", "seed", "user",
               "parallel_tool_calls", "logit_bias", "logprobs", "top_logprobs"}
MILLION = Decimal(1_000_000)
OUTPUT_TOKENS_DEFAULT = 256  # max_tokens 없는 요청의 출력 예약 — 정산 때 안 쓴 만큼 돌려준다 (D22-③)

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
    timeout_s: float | None = None  # 라우팅 규칙 timeout_ms — 시도마다 (D20-①)
    holds: list = field(default_factory=list)  # 한도 예약 — finalize 에서 정산 (D20-①)
    inflight: tuple | None = None  # 주체별 진행 중 요청 슬롯 (키, 보유자) — finalize 에서 반납 (D22-③)


@dataclass
class CallContext:
    principal: Principal
    data_class: str = "internal"
    agent: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None


def default_data_class(db: Session) -> str:
    return policy.default_data_class(db)


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
            else:  # 멀티모달 — 텍스트 파트만 마스킹본 하나로 바꾸고 이미지 등 다른 파트는 순서대로 유지
                rest = [c for c in m["content"] if not (isinstance(c, dict) and c.get("type") == "text")]
                first = next((k for k, c in enumerate(m["content"]) if isinstance(c, dict) and c.get("type") == "text"), 0)
                m["content"] = rest[:first] + [{"type": "text", "text": r.text}] + rest[first:]
    return sorted(set(findings))


def _blocked(db: Session, ctx: CallContext, model: Model, gap: bool) -> str | None:
    """정책 밖 모델 허용 조건 — 모델·공급자 허용 데이터 등급(SCR-I02 ⑤, 정책보다 좁힐 수만), 에어갭의 상용 제외(ST-09). 막히면 사유."""
    from app.services import deploy
    prov = db.get(Provider, model.provider_id)
    for allowed, who in ((model.data_classes, f"모델 {model.id}"), ((prov.options or {}).get("data_classes"), f"공급자 {model.provider_id}")):
        if allowed and ctx.data_class not in allowed:
            return f"{who} 는 {ctx.data_class} 데이터를 받지 않습니다 (허용 {allowed})."
    if gap and deploy.is_commercial(prov.kind, model.category):
        return "에어갭 모드에서는 상용 모델을 쓸 수 없습니다."
    return None


def _fallback_blocked(db: Session, ctx: CallContext, model: Model, gap: bool) -> str | None:
    dec = policy.evaluate(db, policy.PolicyInput("model", model.id, ctx.principal, data_class=ctx.data_class,
                                                 agent=ctx.agent, attrs={"category": model.category}))
    return f"정책 거부 — {dec.reason}" if not dec.allow else _blocked(db, ctx, model, gap)


def prepare(db: Session, ctx: CallContext, body: dict) -> Plan:
    findings = _guard_messages(db, body)
    requested = body.get("model") or "auto"
    ids, rule = resolve(db, requested, ctx.data_class)
    plan = Plan(requested, ctx.data_class, [], (rule.retries if rule else 1), rule.name if rule else None,
                findings=findings, candidates=list(ids),
                timeout_s=(rule.timeout_ms / 1000) if rule is not None and rule.timeout_ms else None)
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
        if blocked := _blocked(db, ctx, model, gap):
            plan.notes.append(f"{mid}: {blocked}")
            denied_reason = denied_reason or blocked
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
    raw_out = body.get("max_tokens") if body.get("max_tokens") is not None else body.get("max_completion_tokens")
    if raw_out is not None and (isinstance(raw_out, bool) or not isinstance(raw_out, int) or raw_out < 1):  # 음수면 예약 우회, 문자열이면 500 (65th)
        raise ApiError(400, "invalid_request_error", "max_tokens 는 1 이상의 정수여야 합니다.")
    out_est = int(raw_out or OUTPUT_TOKENS_DEFAULT)
    first = db.get(Model, plan.targets[0].model_id)
    if first is not None and first.context_window:  # 모델이 낼 수 없는 출력까지 예약하지 않게
        out_est = min(out_est, int(first.context_window))
    est_cost = cost_of(plan.targets[0], est, out_est)
    if (cap := settings.model_max_inflight_per_subject) > 0:  # 한 주체가 큰 요청을 동시에 많이 열어 팀 예산 예약을 잠그지 않게 (D22-③)
        p = ctx.principal  # 클라이언트 키는 키마다 — 한 사람의 여러 연동이 상한 하나를 나눠 쓰지 않게 (65th)
        slot = (f"inflight:key:{p.token_id}" if p.kind == "client_key" and p.token_id else f"inflight:{p.sub}", uuid.uuid4().hex)
        if not ratelimit.backend().holder_acquire(*slot, cap, ratelimit.HOLD_TTL_S):
            raise ApiError(429, "rate_limited", f"동시 요청 상한 초과 ({cap}개) — 진행 중인 요청이 끝난 뒤 다시 시도하세요.",
                           headers={"Retry-After": "5"}, extra={"limit_id": "inflight"})
        plan.inflight = slot
    try:
        lim = ratelimit.check(db, ctx.principal, "model", plan.targets[0].model_id, ctx.agent, est, est_cost, output_tokens=out_est)
    except BaseException:  # 한도 검사 자체가 실패해도 진행 중 슬롯을 남기지 않게 (65th)
        release_inflight(plan)
        raise
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


def upstream_body(body: dict) -> dict:
    return {k: v for k, v in body.items() if k in PASSTHROUGH}


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


async def execute_embed(plan: Plan, inputs: list[str]) -> tuple[dict, Target, list[dict]]:
    """임베딩 (OpenAI /v1/embeddings 호환) — 채팅과 같은 후보·재시도·폴백·회로 차단. 임베딩 미지원 대상은 건너뛴다."""
    attempts: list[dict] = []
    for tgt in plan.targets:
        for n in range(max(1, plan.retries + 1)):
            t0 = time.perf_counter()
            with span(f"embeddings {tgt.model_id}", SpanKind.CLIENT, **{**_genai_attrs(plan, tgt, n),
                                                                      "gen_ai.operation.name": "embeddings"}) as sp:
                try:
                    res = await _bounded(tgt.adapter.embed(inputs, tgt.upstream), plan)
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
                discard(plan)
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
    except BaseException:  # 취소·예상 밖 오류 — 예약·진행 중 슬롯 반환 (65th)
        discard(plan)
        raise
    await run_in_threadpool(_fin, tgt, {"prompt_tokens": (res.get("usage") or {}).get("prompt_tokens", 0),
                                        "completion_tokens": 0}, attempts, "ok")
    return res, tgt.model_id


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


def _genai_attrs(plan: Plan, tgt: Target, attempt: int) -> dict:
    return {"gen_ai.operation.name": "chat", "gen_ai.system": tgt.provider_id, "gen_ai.request.model": plan.requested,
            "gen_ai.response.model": tgt.model_id, "anx.upstream_model": tgt.upstream, "anx.attempt": attempt + 1,
            "anx.data_class": plan.data_class}


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
    try:
        db.commit()
    finally:  # 커밋이 실패해도 예약·진행 중 슬롯은 돌려준다 (65th)
        holds, plan.holds = plan.holds, []  # 정산은 사용 기록 커밋 뒤 — 예약 반환과 기록 사이에 둘 다 빠진 틈이 없게 (59th)
        if settled is not None:
            ratelimit.settle(holds, settled)
        elif holds:  # 호출 실패 — 예약 반환
            ratelimit.release(holds)
        release_inflight(plan)
    return rec


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
