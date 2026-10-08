"""Unified Model API (OpenAI 호환) + 공급자·모델·라우팅 관리 (FR-G06·G01, FR-L00~L02 · SCR-H02·H13·I01~I04).

POST /v1/chat/completions — 헤더: X-AgentNexus-Data-Class(public|internal|confidential), X-AgentNexus-Agent,
X-AgentNexus-Session, X-AgentNexus-Run. 응답 헤더: X-AgentNexus-Model(선택 모델), X-AgentNexus-Fallback.
"""
import asyncio
import contextvars
import json
import time
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, current_principal, require
from app.core.context import current_trace_id
from app.core.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.core.ids import new_id
from app.db import SessionLocal, get_db, utcnow
from app.models import CatalogItem, Model, PersonalToken, Provider, RoutingRule, RoutingRuleVersion, Secret, User
from app.services import audit, guards, llm, policy, registry, secrets
from app.services.providers import DEFAULT_BASE, ProviderError, adapter_for

router = APIRouter(tags=["models"])


def _ctx(request: Request, p: Principal, db: Session, body: dict) -> llm.CallContext:
    c = policy.caller_context(db, p, request.headers, (body.get("metadata") or {}).get("data_class")
                              if not request.headers.get("x-agentnexus-data-class") else None)
    if c["data_class"] not in policy.DATA_CLASSES:
        raise bad_request(f"data_class 는 {policy.DATA_CLASSES} 중 하나여야 합니다.", code="invalid_data_class")
    return llm.CallContext(p, c["data_class"], c["agent"], c["session_id"], c["run_id"], c["step_id"])


def _mask_output(db: Session, res: dict) -> None:
    for ch in res.get("choices", []):
        msg = ch.get("message") or {}
        if isinstance(msg.get("content"), str):
            msg["content"] = guards.scan_output(db, msg["content"]).text


def _key_allows(db: Session, p: Principal, model: str) -> None:
    """클라이언트 키별 허용 모델 별칭 (SCR-H13 ④) — 목록이 있으면 그 별칭만(요청 모델 이름 기준)."""
    if p.kind != "client_key" or not p.token_id:
        return
    row = db.get(PersonalToken, p.token_id)
    allowed = (row.allowed_models or []) if row else []
    if allowed and model not in allowed:
        raise ApiError(403, "model_not_allowed", f"이 클라이언트 키는 모델 '{model}' 을 쓸 수 없습니다 — 허용: {', '.join(allowed)}")


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
    except BaseException:  # 취소·예상 밖 오류 — 예약·진행 중 슬롯을 만료까지 남기지 않게 (동기, 취소 중에도, 65th)
        llm.discard(plan)
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


def _mask_delta(text: str) -> str:
    with SessionLocal() as db:
        return guards.scan_output(db, text).text


@router.post("/v1/embeddings")
async def embeddings(request: Request, body: dict = Body(...), p: Principal = Depends(require("chat:use"))):
    """OpenAI 호환 임베딩 (FR-G06, RAG FR-A02.2) — 입력 가드(PII 마스킹)·데이터 등급 라우팅·사용량은 채팅과 같다."""
    raw = body.get("input")
    inputs = [raw] if isinstance(raw, str) else raw
    if not isinstance(inputs, list) or not inputs or not all(isinstance(x, str) for x in inputs) or len(inputs) > 256:
        raise ApiError(400, "invalid_request_error", "input 은 문자열 또는 문자열 목록(최대 256)이어야 합니다.")

    def _c():
        with SessionLocal() as db:
            _key_allows(db, p, body.get("model") or "embed-local")
            return _ctx(request, p, db, body)
    ctx = await run_in_threadpool(_c)
    res, model_id = await llm.embed_texts(ctx, body.get("model") or "embed-local", inputs)
    return JSONResponse(res, headers={"X-AgentNexus-Model": model_id, "X-AgentNexus-Trace-Id": current_trace_id() or ""})


@router.get("/v1/models")
def list_openai_models(p: Principal = Depends(require("chat:use")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Model).where(Model.enabled.is_(True)).order_by(Model.id)).all()
    rules = db.scalars(select(RoutingRule).where(RoutingRule.enabled.is_(True))).all()
    data = [{"id": m.id, "object": "model", "created": int(m.created_at.timestamp()), "owned_by": m.provider_id,
             "agentnexus": {"category": m.category, "price_in": float(m.price_in), "price_out": float(m.price_out)}}
            for m in rows]
    data += [{"id": r.match_model, "object": "model", "created": int(r.created_at.timestamp()), "owned_by": "router",
              "agentnexus": {"routing_rule": r.name, "strategy": r.strategy}} for r in rules
             if "*" not in r.match_model and r.match_model not in {m.id for m in rows}]
    return {"object": "list", "data": data}


# ───────── 공급자 (SCR-I01·I02) ─────────
class ProviderIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    kind: str = Field(pattern="^(openai|anthropic|gemini|vllm|ollama|litellm|openai_compat|mock)$")
    base_url: str | None = None
    api_key: str | None = Field(None, repr=False)
    secret_ref: str | None = None  # 기존 게이트웨이 시크릿 이름 (SCR-I02 — 키를 다시 입력하지 않음)
    region: str | None = None
    options: dict = {}
    enabled: bool = True


def provider_out(pv: Provider) -> dict:
    return {"id": pv.id, "name": pv.name, "kind": pv.kind, "base_url": pv.base_url or DEFAULT_BASE.get(pv.kind),
            "api_key": "••••" if pv.secret_ref else None, "secret_ref": pv.secret_ref, "region": pv.region,
            "options": pv.options, "enabled": pv.enabled, "status": pv.status, "last_tested_at": pv.last_tested_at}


@router.get("/api/v1/providers")
def list_providers(_: Principal = Depends(require("models:read")), db: Session = Depends(get_db)):
    return {"items": [provider_out(pv) for pv in db.scalars(select(Provider).order_by(Provider.id))],
            "health": llm.provider_health()}


def _apply_key(db: Session, pv: Provider, api_key: str | None, secret_ref: str | None = None) -> None:
    if secret_ref and not api_key:
        name = secret_ref.removeprefix("secret://")
        if db.scalars(select(Secret).where(Secret.name == name)).first() is None:
            raise bad_request(f"시크릿 '{name}' 이 없습니다 — 게이트웨이 시크릿 저장소에 먼저 등록하세요.", code="secret_not_found")
        pv.secret_ref = name
    if api_key:
        pv.secret_ref = pv.secret_ref or f"provider/{pv.id}/api_key"
        secrets.put(db, pv.secret_ref, api_key, kind="api_key", description=f"{pv.name} API key")


@router.post("/api/v1/providers", status_code=201)
def create_provider(body: ProviderIn, p: Principal = Depends(require("models:write")), db: Session = Depends(get_db)):
    if db.get(Provider, body.id):
        raise conflict("같은 id 의 공급자가 있습니다.")
    pv = Provider(**body.model_dump(exclude={"api_key", "secret_ref"}))
    db.add(pv)
    db.flush()
    _apply_key(db, pv, body.api_key, body.secret_ref)
    db.commit()
    audit.append("provider.create", actor=p.sub, target=pv.id, data={"kind": pv.kind, "base_url": pv.base_url})
    return provider_out(pv)


@router.patch("/api/v1/providers/{provider_id}")
def patch_provider(provider_id: str, body: dict, p: Principal = Depends(require("models:write")),
                   db: Session = Depends(get_db)):
    pv = db.get(Provider, provider_id)
    if pv is None:
        raise not_found("provider")
    for k in ("name", "base_url", "region", "options", "enabled"):
        if k in body:
            setattr(pv, k, body[k])
    _apply_key(db, pv, body.get("api_key"), body.get("secret_ref"))
    db.commit()
    audit.append("provider.update", actor=p.sub, target=pv.id, data={k: v for k, v in body.items() if k != "api_key"})
    return provider_out(pv)


@router.post("/api/v1/providers/{provider_id}/test")
async def test_provider(provider_id: str, model: str | None = Body(None, embed=True),
                        p: Principal = Depends(require("models:write"))):
    """연결 시험 — 짧은 프롬프트 1회 (SCR-A06 '연결 시험')."""
    def _load():
        with SessionLocal() as db:
            pv = db.get(Provider, provider_id)
            if pv is None:
                raise not_found("provider")
            m = db.scalars(select(Model).where(Model.provider_id == pv.id)).first()
            return pv, secrets.get_plain(db, pv.secret_ref), model or (m.upstream_model if m else "test")
    pv, key, upstream = await run_in_threadpool(_load)
    t0 = time.perf_counter()
    try:
        res = await adapter_for(pv, key, 20).chat({"messages": [{"role": "user", "content": "ping"}],
                                                   "max_tokens": 8}, upstream)
        result = {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "model": upstream,
                  "reply": ((res.get("choices") or [{}])[0].get("message") or {}).get("content")}
    except ProviderError as exc:
        result = {"ok": False, "error": str(exc), "status": exc.status}

    def _save():
        with SessionLocal() as db:
            row = db.get(Provider, provider_id)
            row.status, row.last_tested_at = ("ok" if result["ok"] else "error"), utcnow()
            db.commit()
    await run_in_threadpool(_save)
    return result


# ───────── 모델 (SCR-I01·I03) ─────────
class ModelIn(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,119}$")
    provider_id: str
    upstream_model: str
    display_name: str | None = None
    category: str = Field("commercial", pattern="^(commercial|domain|local|mock)$")
    data_classes: list[str] = []
    price_in: Decimal = Field(Decimal("0"), ge=0)
    price_out: Decimal = Field(Decimal("0"), ge=0)
    context_window: int = 8192
    capabilities: list[str] = ["stream"]
    quality: float | None = Field(None, ge=0, le=1)
    enabled: bool = True


model_out = llm.model_out


@registry.register_handler("model")
def _register_model(db: Session, item: CatalogItem, actor: str, options: dict) -> dict:
    """카탈로그 model 항목 등록 → 게이트웨이 모델 별칭 생성 (즉시 사용)."""
    meta = {**(item.item_metadata or {}), **options}
    body = ModelIn(**{**meta, "id": meta.get("id") or item.name})
    if db.get(Provider, body.provider_id) is None:
        raise bad_request("알 수 없는 provider_id")
    m = db.get(Model, body.id) or Model(id=body.id)
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    db.merge(m)
    db.flush()
    return {"id": body.id, "type": "model", "endpoint": "/v1/chat/completions"}


@router.get("/api/v1/models")
def list_models(_: Principal = Depends(require("models:read")), db: Session = Depends(get_db)):
    return {"items": [model_out(m) for m in db.scalars(select(Model).order_by(Model.id))]}


@router.post("/api/v1/models", status_code=201)
def create_model(body: ModelIn, p: Principal = Depends(require("models:write")), db: Session = Depends(get_db)):
    if db.get(Provider, body.provider_id) is None:
        raise bad_request("알 수 없는 provider_id")
    if db.get(Model, body.id):
        raise conflict("같은 id 의 모델이 있습니다.")
    m = Model(**body.model_dump())
    db.add(m)
    db.flush()
    llm.sync_model_catalog(db, m, p.sub)
    db.commit()
    audit.append("model.create", actor=p.sub, target=m.id, data={"provider": m.provider_id, "category": m.category})
    return model_out(m)


@router.patch("/api/v1/models/{model_id:path}")
def patch_model(model_id: str, body: dict, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)):
    """설정만으로 모델 교체 (FR-G06 수용 기준) — 별칭은 유지하고 provider·upstream 을 바꾼다.
    단가만 바꾸는 요청은 FinOps(`cost:write`)도 가능 (SCR-K06 ③)."""
    price_only = bool(body) and set(body) <= {"price_in", "price_out"}
    if not (p.has("models:write") or (price_only and p.has("cost:write"))):
        raise forbidden("models:write" + (" 또는 cost:write" if price_only else ""))
    m = db.get(Model, model_id)
    if m is None:
        raise not_found("model")
    if "provider_id" in body and db.get(Provider, body["provider_id"]) is None:
        raise bad_request("알 수 없는 provider_id")
    if "quality" in body and body["quality"] is not None and not 0 <= float(body["quality"]) <= 1:
        raise bad_request("quality 는 0~1 입니다.")
    for k in ("provider_id", "upstream_model", "display_name", "category", "data_classes", "price_in", "price_out",
              "context_window", "capabilities", "quality", "enabled"):
        if k in body:
            setattr(m, k, Decimal(str(body[k])) if k.startswith("price") else body[k])
    llm.sync_model_catalog(db, m, p.sub)
    db.commit()
    audit.append("model.update", actor=p.sub, target=m.id, data=body)
    return model_out(m)


# ───────── 라우팅 규칙 (SCR-H02) ─────────
class RuleIn(BaseModel):
    name: str
    match_model: str = "*"
    match_data_class: str | None = None
    strategy: str = Field("priority", pattern="^(priority|cost|latency|quality|ab)$")
    targets: list[dict] = Field(min_length=1)
    fallbacks: list[str] = []
    retries: int = Field(1, ge=0, le=5)
    timeout_ms: int = Field(30000, ge=100, le=600000)
    priority: int = 100
    enabled: bool = True

    @model_validator(mode="after")
    def _weights(self):  # ab 는 random.choices — 숫자 아닌 값·음수·합 0 이면 모든 요청이 500 이 된다
        try:
            weights = [float(t.get("weight", 1)) for t in self.targets]
        except (TypeError, ValueError) as exc:
            raise ValueError("targets[].weight 는 숫자여야 합니다.") from exc
        if any(w < 0 for w in weights) or (self.strategy == "ab" and sum(weights) <= 0):
            raise ValueError("targets[].weight 는 0 이상이고, ab 전략은 합이 0보다 커야 합니다.")
        return self


_RULE_FIELDS = ("name", "match_model", "match_data_class", "strategy", "targets", "fallbacks", "retries", "timeout_ms",
                "priority", "enabled")


def _rule_snapshot(r: RoutingRule) -> dict:
    return {k: getattr(r, k) for k in _RULE_FIELDS}


def rule_out(r: RoutingRule, published: dict | None = None) -> dict:
    out = {"id": r.id, "name": r.name, "match_model": r.match_model, "match_data_class": r.match_data_class,
           "strategy": r.strategy, "targets": r.targets, "fallbacks": r.fallbacks, "retries": r.retries,
           "timeout_ms": r.timeout_ms, "priority": r.priority, "enabled": r.enabled, "updated_at": r.updated_at,
           "version": r.version}
    if published is not None:  # 게시본과 다르면 '게시 안 된 변경' (SCR-H02 ⑤)
        out["unpublished_changes"] = published != _rule_snapshot(r)
    return out


def _published(db: Session, r: RoutingRule) -> dict | None:
    v = db.scalars(select(RoutingRuleVersion).where(RoutingRuleVersion.rule_id == r.id, RoutingRuleVersion.version == r.version)).first()
    return v.snapshot if v else None


@router.get("/api/v1/gateway/routing/rules", tags=["gateway"])
def list_rules(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    return {"items": [rule_out(r, _published(db, r) or {}) for r in db.scalars(select(RoutingRule).order_by(RoutingRule.priority))]}


@router.post("/api/v1/gateway/routing/rules", status_code=201, tags=["gateway"])
def create_rule(body: RuleIn, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    for t in body.targets:
        if "model" not in t:
            raise bad_request("targets 항목에는 model 이 필요합니다.")
    r = RoutingRule(id=new_id("rr"), **body.model_dump())
    db.add(r)
    db.commit()
    audit.append("routing.rule.create", actor=p.sub, target=r.name, data=body.model_dump())
    return rule_out(r)


@router.put("/api/v1/gateway/routing/rules/{rule_id}", tags=["gateway"])
def update_rule(rule_id: str, body: RuleIn, p: Principal = Depends(require("gateway:write")),
                db: Session = Depends(get_db)):
    r = db.get(RoutingRule, rule_id)
    if r is None:
        raise not_found("routing rule")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    db.commit()
    audit.append("routing.rule.update", actor=p.sub, target=r.name, data=body.model_dump())
    return rule_out(r)


@router.delete("/api/v1/gateway/routing/rules/{rule_id}", tags=["gateway"])
def delete_rule(rule_id: str, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    r = db.get(RoutingRule, rule_id)
    if r is None:
        raise not_found("routing rule")
    db.delete(r)
    db.commit()
    audit.append("routing.rule.delete", actor=p.sub, target=r.name)
    return {"deleted": rule_id}


class PublishIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class RollbackIn(BaseModel):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


def _diff(a: dict, b: dict) -> list[dict]:
    return [{"field": k, "from": a.get(k), "to": b.get(k)} for k in _RULE_FIELDS if a.get(k) != b.get(k)]


@router.get("/api/v1/gateway/routing/rules/{rule_id}/versions", tags=["gateway"])
def rule_versions(rule_id: str, _: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """라우팅 규칙 게시 이력 (SCR-H02 ⑤) — 버전별 사유·게시자·직전 게시본 대비 변경, 현재 편집본과의 차이."""
    r = db.get(RoutingRule, rule_id)
    if r is None:
        raise not_found("routing rule")
    rows = list(db.scalars(select(RoutingRuleVersion).where(RoutingRuleVersion.rule_id == rule_id)
                           .order_by(RoutingRuleVersion.version.desc())))
    items = []
    for i, v in enumerate(rows):
        prev = rows[i + 1].snapshot if i + 1 < len(rows) else {}
        items.append({"version": v.version, "actor": v.actor, "ts": v.ts, "reason": v.reason, "diff": _diff(prev, v.snapshot)})
    cur = _published(db, r) or {}
    return {"rule_id": rule_id, "published_version": r.version, "items": items, "pending": _diff(cur, _rule_snapshot(r))}


@router.post("/api/v1/gateway/routing/rules/{rule_id}/publish", tags=["gateway"])
def publish_rule(rule_id: str, body: PublishIn, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    """현재 규칙을 새 버전으로 게시 (사유 필수, MSG-003) — 되돌리기 기준점이 된다."""
    r = db.get(RoutingRule, rule_id)
    if r is None:
        raise not_found("routing rule")
    snap = _rule_snapshot(r)
    if (_published(db, r) or None) == snap:
        raise bad_request("게시본과 같습니다 — 바뀐 내용이 없습니다.", code="no_changes")
    r.version = (db.scalar(select(func.max(RoutingRuleVersion.version)).where(RoutingRuleVersion.rule_id == rule_id)) or 0) + 1
    db.add(RoutingRuleVersion(id=new_id("rrv"), rule_id=rule_id, version=r.version, snapshot=snap, reason=body.reason, actor=p.sub))
    db.commit()
    audit.append("routing.rule.publish", actor=p.sub, target=r.name, data={"version": r.version, "reason": body.reason})
    return {"version": r.version, **rule_out(r, snap)}


@router.post("/api/v1/gateway/routing/rules/{rule_id}/rollback", tags=["gateway"])
def rollback_rule(rule_id: str, body: RollbackIn, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    """게시본으로 되돌리기 — 그 스냅샷을 적용하고 새 버전으로 다시 게시(이력 보존)."""
    r = db.get(RoutingRule, rule_id)
    if r is None:
        raise not_found("routing rule")
    v = db.scalars(select(RoutingRuleVersion).where(RoutingRuleVersion.rule_id == rule_id,
                                                    RoutingRuleVersion.version == body.version)).first()
    if v is None:
        raise not_found("rule version")
    for k, val in v.snapshot.items():
        setattr(r, k, val)
    r.version = (db.scalar(select(func.max(RoutingRuleVersion.version)).where(RoutingRuleVersion.rule_id == rule_id)) or 0) + 1
    db.add(RoutingRuleVersion(id=new_id("rrv"), rule_id=rule_id, version=r.version, snapshot=dict(v.snapshot),
                              reason=f"v{body.version} 로 되돌림: {body.reason}", actor=p.sub))
    db.commit()
    audit.append("routing.rule.rollback", actor=p.sub, target=r.name, data={"to": body.version, "version": r.version,
                                                                            "reason": body.reason})
    return {"version": r.version, "restored_from": body.version, **rule_out(r, v.snapshot)}


# ───────── 클라이언트 키 현황 (SCR-H13) ─────────
@router.post("/api/v1/gateway/client-keys/{key_id}/revoke", tags=["gateway"])
def revoke_client_key(key_id: str, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    """클라이언트 키 관리자 폐기 (SCR-H13 ④, MSG-002) — 소유자가 아니어도 게이트웨이 운영자가 폐기."""
    t = db.get(PersonalToken, key_id)
    if t is None or t.kind != "client_key":
        raise not_found("client key")
    if t.revoked_at is None:
        t.revoked_at = utcnow()
        db.commit()
        audit.append("gateway.client_key.revoke", actor=p.sub, target=t.id, data={"name": t.name, "prefix": t.prefix})
    return {"id": t.id, "revoked_at": t.revoked_at}


class KeyModelsIn(BaseModel):
    allowed_models: list[str] = Field(default_factory=list, max_length=50)


@router.patch("/api/v1/gateway/client-keys/{key_id}", tags=["gateway"])
def patch_client_key(key_id: str, body: KeyModelsIn, p: Principal = Depends(require("gateway:write")),
                     db: Session = Depends(get_db)):
    """클라이언트 키 허용 모델 별칭 지정 (SCR-H13 ④) — 빈 목록 = 제한 없음. 다음 호출부터 적용."""
    t = db.get(PersonalToken, key_id)
    if t is None or t.kind != "client_key":
        raise not_found("client key")
    known = {m.id for m in db.scalars(select(Model))} | {r.match_model for r in db.scalars(select(RoutingRule))} | {"auto"}
    bad = [m for m in body.allowed_models if m not in known]
    if bad:
        raise bad_request(f"알 수 없는 모델 별칭: {', '.join(bad)} (MSG-004)", code="unknown_model")
    t.allowed_models = sorted(set(body.allowed_models)) or None
    db.commit()
    audit.append("gateway.client_key.models", actor=p.sub, target=t.id, data={"allowed_models": t.allowed_models})
    return {"id": t.id, "allowed_models": t.allowed_models}


@router.get("/api/v1/gateway/client-keys", tags=["gateway"])
def client_keys(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    rows = db.execute(select(PersonalToken, User).join(User, User.id == PersonalToken.user_id)
                      .where(PersonalToken.kind == "client_key").order_by(PersonalToken.created_at.desc())).all()
    return {"endpoint": "/v1/chat/completions",
            "items": [{"id": t.id, "name": t.name, "prefix": t.prefix, "owner": u.username or u.sub, "team": t.team,
                       "allowed_models": t.allowed_models,
                       "expires_at": t.expires_at, "revoked_at": t.revoked_at, "last_used_at": t.last_used_at}
                      for t, u in rows]}
