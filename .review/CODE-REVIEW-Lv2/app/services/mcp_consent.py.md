> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 21. app/services/mcp_consent.py
**파일 요약 / File Summary**
- 역할 / Role: 업스트림 MCP 서버 → 클라이언트 요청(sampling·elicitation·roots·ping) 처리. 하류 클라이언트가 능력을 선언했으면 **중계**(Relay), 아니면 요청자에게 **동의 카드**(Approval)를 띄워 sampling 은 게이트웨이가 모델을 골라 실행(선택 검토 후 전송), elicitation 은 스키마 검증한 사용자 입력을 반환. 무응답 자동 거부, 전 과정 감사.
- 진입점 거리 / Distance: 2 (`app/services/mcp_gateway.py:294`, `app/api/gateway_admin.py:785-790`)
- LOC: 333
- 주요 export: `handle`, `decide`, `Relay`, `answer`, `ConsentError`, `CONSENT_KINDS`, `USER_REJECTED`
- 의존(내부): `app.core.{config,errors,ids}`, `app.db`, `app.models.{Approval,McpRelay,RunEvent,McpServer}`, `app.services.{audit,governance,guards,llm}`
- 외부 패키지: jsonschema, starlette
- 부수효과 / Side effects: DB 쓰기(approvals·run_events·mcp_relays), 감사 기록, LLM 호출(사용량·비용)
- 동시성 / Concurrency: asyncio + DB 폴링(0.2초)으로 워커 간 동의 결과 공유
- 테스트 커버리지 흔적: `test/test_mcp_consent.py`, `test/test_extend.py`(동의 연장)

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/mcp_consent.py lines=1-27
"""MCP 서버 → 클라이언트 요청 (FR-A00·A03·G09, SCR-C08) — sampling/createMessage · elicitation/create · roots/list · ping.

업스트림 MCP 서버가 도구 호출 도중 SSE 역채널(MCP 2025-11-25)로 요청을 보내면 게이트웨이가 받는다.
- 중계: 하류 MCP 클라이언트가 해당 능력을 선언하고 SSE 로 호출했으면 요청을 하류로 그대로 보내고 답을 돌려준다(`Relay`).
- Host 동의: 그 외(플랫폼 오케스트레이션·능력 없는 클라이언트)는 요청자에게 동의 카드를 띄운다. sampling 은 사용자가 허용(또는
  프롬프트 편집 후 허용)하면 게이트웨이가 Unified Model API 로 실행한다 — 모델은 서버가 아니라 게이트웨이가 정하고 내부 데이터는
  로컬 모델로만(MSG-011). 선택 시 응답을 서버로 보내기 전에 검토한다. elicitation 은 요청 스키마로 검증한 사용자 입력을 돌려준다.
무응답은 MCP_CONSENT_TIMEOUT_S(기본 60초) 후 자동 거부하고, 요청·결정·결과는 감사 로그에 남긴다.
"""
import asyncio
import time
from datetime import timedelta

import jsonschema
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.errors import ApiError, bad_request, forbidden
from app.core.ids import new_id
from app.db import SessionLocal, utcnow
from app.models import Approval, McpRelay, RunEvent
from app.services import audit, governance, guards, llm

CONSENT_KINDS = {"sampling/createMessage": "sampling", "elicitation/create": "elicitation"}
USER_REJECTED = -1  # MCP 사양: 사용자 거부
NOT_FOUND = -32601
WAIT_STATES = {"sampling": {"pending", "approved", "review", "sent"}, "elicitation": {"pending"}}
```
**분석 / Analysis (Level 2)**
- L1-9: 설계 — 중계 vs Host 동의 두 갈래, sampling 모델은 **서버가 아니라 게이트웨이가 정한다**(서버의 modelPreferences 는 힌트일 뿐, 내부 데이터는 로컬 모델로만 — MSG-011).
- L24: 동의가 필요한 메서드 → 승인 종류.
- L25: MCP 사양의 사용자 거부 코드 -1.
- L27 `WAIT_STATES`: 종류별 대기 상태(참고용 상수).
**짚어볼 점 / Watch-outs**
- L27 `WAIT_STATES` 는 저장소 전체(app·test)에서 정의 외 참조가 없다(grep 확인) — 삭제 후보.

```python path=app/services/mcp_consent.py lines=30-61
class ConsentError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


async def handle(server_id: str, ctx, msg: dict) -> dict:
    """업스트림이 보낸 JSON-RPC 요청 → 업스트림에 돌려줄 JSON-RPC 응답."""
    rid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    try:
        if method == "ping":
            result = {}
        elif method == "roots/list":
            result = {"roots": []}  # 게이트웨이는 파일 시스템 루트를 노출하지 않는다
        elif method in CONSENT_KINDS:
            if ctx is None:
                raise ConsentError(USER_REJECTED, "동의 채널이 없는 시스템 호출에서는 허용하지 않습니다.")
            relay = getattr(ctx, "relay", None)
            if relay is not None and relay.supports(method):
                result = await relay.request(method, params)
                await run_in_threadpool(lambda: audit.append(
                    "mcp.relay", actor=ctx.principal.sub, actor_kind=ctx.principal.kind, target=f"{server_id}:{method}",
                    decision="relayed", data={"session": relay.session_id}))
            elif method == "sampling/createMessage":
                result = await _sampling(server_id, ctx, params)
            else:
                result = await _elicitation(server_id, ctx, params)
        else:
            raise ConsentError(NOT_FOUND, f"Method not found: {method}")
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    except ConsentError as exc:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": exc.code, "message": exc.message}}
```
**분석 / Analysis (Level 2)**
- L36-61 `handle`: 업스트림 JSON-RPC 요청 → 업스트림에 돌려줄 응답. ping·roots(빈 목록 — 게이트웨이는 파일 시스템 루트를 노출하지 않는다: 최소 노출), 동의 메서드는 ctx 없으면 거부(시스템 호출에서 사람 동의 불가), 중계 가능하면 하류로, 아니면 Host 동의. 모르는 메서드는 -32601.
- L60-61: `ConsentError` 만 JSON-RPC 오류로 변환.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 버그] L60 `except ConsentError` 만 잡는다 — `_generate`·`_open`·`audit.append` 의 DB 오류, `llm.execute` 의 `ApiError` 이외 예외, `Relay.request` 의 DB 오류는 그대로 전파된다. 호출부(`app/services/mcp_gateway.py:294-296`)는 이 반환값을 업스트림에 POST 하는데, 예외가 나면 **업스트림은 응답을 받지 못하고** 자기 타임아웃까지 대기하며 원 도구 호출 전체가 500 으로 끝난다. `except Exception` → `{"code": -32603}` 응답을 추가해야 한다.

```python path=app/services/mcp_consent.py lines=64-97
# ───────────────────────── Host 동의 ─────────────────────────
def _text_of(content) -> str:
    parts = content if isinstance(content, list) else [content]
    out = []
    for c in parts:
        if isinstance(c, dict):
            out.append(c.get("text") if c.get("type") == "text" else f"[{c.get('type', 'content')} 생략]")
        elif isinstance(c, str):
            out.append(c)
    return "\n".join(x for x in out if x)


def _chat_messages(params: dict, messages: list | None) -> list[dict]:
    """MCP SamplingMessage(또는 화면에서 편집한 {role, text}) → OpenAI 형식."""
    out = [{"role": "system", "content": params["systemPrompt"]}] if params.get("systemPrompt") else []
    for m in messages if messages is not None else params.get("messages") or []:
        text = m.get("text") if "text" in m else _text_of(m.get("content"))
        out.append({"role": "assistant" if m.get("role") == "assistant" else "user", "content": text or ""})
    return out


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
```
**분석 / Analysis (Level 2)**
- L65-73 `_text_of`: MCP content(단일·목록) → 텍스트, 비텍스트는 `[image 생략]` 표기.
- L76-82 `_chat_messages`: SamplingMessage(또는 동의 화면에서 편집한 `{role, text}`) → OpenAI 메시지. `systemPrompt` 는 system 역할로.
- L85-92 `_body`: 게이트웨이 모델 API 요청 — `model: "auto"`(라우팅 규칙이 결정), `maxTokens` 기본 256, temperature·stop 전달.
- L95-97 `_cctx`: LLM 호출 맥락 — 에이전트 표기 `mcp:{server}/sampling`.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L78 서버가 보낸 `systemPrompt` 를 그대로 system 메시지로 쓴다 — 동의 화면에서 사용자가 이를 확인할 수 있어야 한다(편집 가능한 것은 messages — `decide` L259-261). 악의적 서버의 시스템 프롬프트 인젝션은 출력 가드(L193)만 통과하면 서버로 돌아간다.
- L87 `int(params.get("maxTokens") or 256)` — 상한이 없다. 서버가 `maxTokens: 1000000` 을 요구하면 비용이 커진다(라우팅·한도 정책이 막아야 함).

```python path=app/services/mcp_consent.py lines=100-115
def _preview(server_id: str, ctx, params: dict) -> dict:
    """동의 카드에 보여 줄 라우팅 — 실제 실행과 같은 prepare 로 계산."""
    with SessionLocal() as db:
        try:
            plan = llm.prepare(db, _cctx(server_id, ctx), _body(params))
        except ApiError as exc:
            return {"error": exc.detail, "data_class": ctx.data_class}
        return {"model": plan.targets[0].model_id, "category": plan.targets[0].category, "data_class": plan.data_class,
                "rerouted": plan.rerouted, "hints": [h.get("name") for h in
                                                     (params.get("modelPreferences") or {}).get("hints") or []]}


def _event(db, ap: Approval, type_: str, data: dict) -> None:
    if ap.run_id:
        db.add(RunEvent(run_id=ap.run_id, type=type_, data={"approval_id": ap.id, "server": ap.server_id,
                                                            "step_id": ap.step_id, **data}))
```
**분석 / Analysis (Level 2)**
- L100-109 `_preview`: 동의 카드에 보여 줄 라우팅(실제 실행과 같은 `llm.prepare` 로 계산 — 화면과 실제가 어긋나지 않게). 정책 거부면 오류 문구.
- L112-115 `_event`: 실행(run)에 속한 승인이면 SSE 재생용 RunEvent 추가.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 버그] L104 `llm.prepare` 는 입력 가드뿐 아니라 한도 확인(`ratelimit.check`)까지 수행하고, `ratelimit.check` 는 `backend().take(...)` 로 토큰 버킷을 **차감**한다(`app/services/ratelimit.py:234` — 확인). 미리보기(L211)와 실제 생성(L182 `_prep`)이 각각 `prepare` 를 부르므로 sampling 1건이 요청 한도를 2번 소모한다. 미리보기는 한도 차감 없는 판정 함수를 써야 한다.
- [위험:중간] L113-115 `ap.run_id` 는 `ctx.run_id` 에서 오고, 하류 MCP 클라이언트 경로에서는 그 값이 **클라이언트 헤더** `X-AgentNexus-Run` 이다(`app/api/mcp.py:55-61`). 다른 사용자의 실행 id 를 넣으면 그 사용자의 채팅 SSE 에 가짜 `sampling.required` 이벤트를 주입할 수 있다(동의 결정 권한은 없지만 UI 스푸핑·혼란). run_id 소유자 확인이 필요하다.

```python path=app/services/mcp_consent.py lines=118-137
def _open(kind: str, server_id: str, ctx, params: dict, result: dict | None) -> str:
    timeout = settings.mcp_consent_timeout_s
    with SessionLocal() as db:
        ap = Approval(id=new_id("apr"), kind=kind, status="pending", requester_sub=ctx.principal.sub,
                      session_id=ctx.session_id, run_id=ctx.run_id, step_id=ctx.step_id, server_id=server_id,
                      target=f"{server_id}:{kind}", args=params, args_hash=governance.args_hash(params), risk="medium",
                      approver="requester", policy_ref="mcp-consent",
                      reason=("MSG-016 서버가 모델 호출(샘플링)을 요청합니다. 프롬프트를 확인하고 허용하세요." if kind == "sampling"
                              else "MSG-017 서버가 추가 입력을 요청합니다."),
                      expires_at=utcnow() + timedelta(seconds=timeout), result=result)
        db.add(ap)
        db.flush()
        _event(db, ap, f"{kind}.required", {"params": params, "routing": (result or {}).get("routing"),
                                            "expires_at": ap.expires_at.isoformat(), "extend_step_s": int(timeout),
                                            "max_extensions": governance.MAX_EXTENSIONS})
        db.commit()
        aid = ap.id
    audit.append("mcp.consent.request", actor=ctx.principal.sub, actor_kind=ctx.principal.kind,
                 target=f"{server_id}:{kind}", data={"approval_id": aid, "run": ctx.run_id})
    return aid
```
**분석 / Analysis (Level 2)**
- L118-137 `_open`: 동의 승인 행 생성 — 요청자 본인 승인(`approver="requester"`), 위험 medium, 만료 = 지금 + 동의 대기(기본 60초), 인자 원문·해시, 라우팅 미리보기(result). 실행 이벤트 추가 후 커밋, 커밋 **후** 감사(별도 세션 — SQLite 잠금 회피 관례).
**짚어볼 점 / Watch-outs**
- L123 `args=params` — sampling 프롬프트 원문(개인정보 포함 가능)이 승인 테이블에 평문 저장된다(COMPLIANCE.md 41행이 의도를 기록). 보존 기간 정리 대상인지 확인 필요.

```python path=app/services/mcp_consent.py lines=140-173
def _snapshot(aid: str) -> dict:
    with SessionLocal() as db:
        ap = db.get(Approval, aid)
        if ap.status in ("pending", "review") and ap.expires_at and ap.expires_at < utcnow():
            prev, ap.status = ap.status, "expired"
            _event(db, ap, f"{ap.kind}.resolved", {"status": "expired", "phase": prev})
            db.commit()
            audit.append("mcp.consent.expire", actor="system", target=ap.target, decision="expired",
                         data={"approval_id": ap.id, "phase": prev})
        return {"status": ap.status, "payload": ap.payload or {}, "result": ap.result or {}}


async def _wait(aid: str, phase: str) -> dict:
    """phase 상태를 벗어날 때까지 대기 — 워커 간 공유를 위해 DB 를 폴링한다."""
    while True:
        snap = await run_in_threadpool(_snapshot, aid)
        if snap["status"] != phase:
            return snap
        await asyncio.sleep(0.2)


def _set(aid: str, status: str | None = None, result: dict | None = None, event: tuple[str, dict] | None = None,
         expires_in: float | None = None) -> None:
    with SessionLocal() as db:
        ap = db.get(Approval, aid)
        if status:
            ap.status = status
        if result is not None:
            ap.result = {**(ap.result or {}), **result}
        if expires_in is not None:
            ap.expires_at = utcnow() + timedelta(seconds=expires_in)
        if event:
            _event(db, ap, *event)
        db.commit()
```
**분석 / Analysis (Level 2)**
- L140-149 `_snapshot`: 현재 상태 조회 — pending·review 인데 만료가 지났으면 expired 로 바꾸고 이벤트·감사. 상태·페이로드·결과 반환.
- L152-158 `_wait`: 상태가 `phase` 를 벗어날 때까지 0.2초 폴링(워커 간 공유를 위해 DB). 자체 타임아웃은 없고 `_snapshot` 의 만료 처리에 의존한다.
- L161-173 `_set`: 상태·결과 병합·만료 갱신·이벤트를 한 트랜잭션으로.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 경쟁] L143-146 의 만료 처리와 `decide`(L254-276)가 동시에 같은 행을 읽고 각자 커밋하면 **마지막 쓰기가 이긴다**(조건부 UPDATE·버전 잠금 없음). 예: `_snapshot` 이 expired 로 바꾼 직후 `decide` 가 이전에 읽은 pending 상태를 기준으로 approved 를 써서, 대기자는 이미 거부로 진행했는데 DB 는 approved 로 남을 수 있다.
- L142 `ap` 가 `None`(보존 정리로 삭제)이면 `AttributeError` 로 대기 루프가 깨진다.
- 대기자 하나당 초당 5회 DB 조회.

```python path=app/services/mcp_consent.py lines=176-203
async def _generate(server_id: str, ctx, params: dict, messages: list | None) -> tuple[str, str]:
    body = _body(params, messages)

    def _prep():
        with SessionLocal() as db:
            c = _cctx(server_id, ctx)
            return c, llm.prepare(db, c, body)
    try:
        cctx, plan = await run_in_threadpool(_prep)
        t0 = time.perf_counter()
        res, tgt, attempts = await llm.execute(plan, body)
    except ApiError as exc:
        raise ConsentError(USER_REJECTED, f"게이트웨이가 샘플링 모델 호출을 거부했습니다: {exc.detail}") from exc

    def _fin():
        with SessionLocal() as db:
            text = ((res.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            text = guards.scan_output(db, text).text
            llm.finalize(db, cctx, plan, tgt, res.get("usage"), (time.perf_counter() - t0) * 1000, attempts)
            return text
    return await run_in_threadpool(_fin), tgt.model_id


def _sampling_policy(server_id: str) -> str:
    from app.models import McpServer
    with SessionLocal() as db:
        s = db.get(McpServer, server_id)
        return (s.sampling_policy if s is not None else None) or "ask"
```
**분석 / Analysis (Level 2)**
- L176-196 `_generate`: 동의 후 실제 모델 호출 — `llm.prepare`(입력 가드·라우팅·한도) → `llm.execute`(재시도·폴백) → 응답 텍스트에 **출력 가드**(`guards.scan_output` — PII 마스킹) → `llm.finalize`(사용량·비용 기록). 게이트웨이 정책 거부(ApiError)는 사용자 거부 코드로 변환.
- L199-203 `_sampling_policy`: 서버별 샘플링 정책(ask|deny).
**짚어볼 점 / Watch-outs**
- L186 `llm.execute` 는 `ProviderError` 외 예외를 그대로 던진다(`app/services/llm.py:258`) — 여기서는 `ApiError` 만 잡으므로 네트워크 계열 예외가 `handle` 밖으로 새어 나간다(위 L60 문제와 연결).
- L190-195 `_fin` 이 응답 파싱(`choices[0].message.content`)과 사용량 기록을 한 트랜잭션에서 — 파싱 실패 시 사용량도 기록되지 않는다.

```python path=app/services/mcp_consent.py lines=206-235
async def _sampling(server_id: str, ctx, params: dict) -> dict:
    if await run_in_threadpool(_sampling_policy, server_id) == "deny":  # 서버별 샘플링 정책 (SCR-F02)
        await run_in_threadpool(lambda: audit.append("mcp.sampling.deny", actor=ctx.principal.sub, target=server_id,
                                                     decision="deny", data={"reason": "server_policy"}))
        raise ConsentError(USER_REJECTED, f"{server_id} 서버의 샘플링은 운영 정책으로 막혀 있습니다.")
    routing = await run_in_threadpool(_preview, server_id, ctx, params)
    aid = await run_in_threadpool(_open, "sampling", server_id, ctx, params, {"routing": routing})
    snap = await _wait(aid, "pending")
    if snap["status"] != "approved":
        raise ConsentError(USER_REJECTED, "사용자가 샘플링 요청을 거부했습니다." if snap["status"] == "rejected"
                           else "동의 대기 시간이 지나 자동 거부했습니다.")
    payload = snap["payload"]
    text, model = await _generate(server_id, ctx, params, payload.get("messages"))
    if payload.get("review"):  # 응답을 서버로 보내기 전에 사용자 검토 (SCR-C08 ⑦)
        until = (utcnow() + timedelta(seconds=settings.mcp_consent_timeout_s)).isoformat()
        await run_in_threadpool(_set, aid, "review", {"draft": {"text": text, "model": model}},
                                ("sampling.review", {"draft": text, "model": model, "expires_at": until}),
                                settings.mcp_consent_timeout_s)
        snap = await _wait(aid, "review")
        if snap["status"] != "sent":
            raise ConsentError(USER_REJECTED, "사용자가 응답 검토에서 전송을 거부했습니다." if snap["status"] == "rejected"
                               else "응답 검토 대기 시간이 지나 자동 거부했습니다.")
        text = snap["payload"].get("text") or text
    await run_in_threadpool(_set, aid, "completed", {"final": {"text": text, "model": model}},
                            ("sampling.resolved", {"status": "completed", "model": model}))
    await run_in_threadpool(lambda: audit.append(
        "mcp.sampling", actor=ctx.principal.sub, actor_kind=ctx.principal.kind, target=f"{server_id}:sampling",
        decision="allow", data={"approval_id": aid, "model": model, "edited": bool(payload.get("messages")),
                                "reviewed": bool(payload.get("review")), "data_class": ctx.data_class}))
    return {"role": "assistant", "content": {"type": "text", "text": text}, "model": model, "stopReason": "endTurn"}
```
**분석 / Analysis (Level 2)**
- L207-210: 서버 정책이 deny 면 즉시 거부·감사.
- L211-216: 라우팅 미리보기 → 동의 카드 → pending 이탈 대기 → approved 가 아니면 거부(rejected=사용자 거부, 그 외=시간 초과).
- L217-218: 사용자가 편집한 messages 가 있으면 그것으로 생성.
- L219-228: 검토 옵션이면 초안을 review 상태로 올리고 사용자가 send 할 때까지 대기 — 사용자가 수정한 텍스트로 교체 가능.
- L229-235: completed 기록·감사 → MCP CreateMessageResult(`stopReason: endTurn`).
**짚어볼 점 / Watch-outs**
- L228 `snap["payload"].get("text") or text` — 사용자가 빈 문자열로 보내면 원래 초안이 전송된다(빈 응답을 의도적으로 보낼 수 없음).
- L235 `model` 에 게이트웨이 내부 모델 id 를 서버에 알려 준다 — 정보 노출 수준은 낮다.

```python path=app/services/mcp_consent.py lines=238-246
async def _elicitation(server_id: str, ctx, params: dict) -> dict:
    aid = await run_in_threadpool(_open, "elicitation", server_id, ctx, params, None)
    snap = await _wait(aid, "pending")
    payload = snap["payload"]
    action = payload.get("action") or ("accept" if snap["status"] == "approved" else
                                       "cancel" if snap["status"] == "expired" else "decline")
    await run_in_threadpool(_set, aid, None, {"response": {"action": action}},
                            ("elicitation.resolved", {"status": snap["status"], "action": action}))
    return {"action": action, **({"content": payload.get("content") or {}} if action == "accept" else {})}
```
**분석 / Analysis (Level 2)**
- L238-246 `_elicitation`: 동의 카드 → 결정 대기 → accept 면 사용자가 입력한(이미 스키마 검증된) content 반환, 만료면 cancel, 그 외 decline.

```python path=app/services/mcp_consent.py lines=249-281
def decide(db, ap: Approval, p, decision: str, *, messages: list | None = None, review: bool = False,
           text: str | None = None, content: dict | None = None, note: str | None = None) -> Approval:
    """동의 카드 결정 (POST /api/v1/approvals/{id}/consent)."""
    if ap.kind not in ("sampling", "elicitation"):
        raise bad_request("MCP 동의 요청이 아닙니다 — /approve·/reject 를 쓰세요.", code="not_consent")
    if ap.status in ("pending", "review") and ap.expires_at and ap.expires_at < utcnow():
        ap.status = "expired"
        db.commit()
    if not governance.can_decide(ap, p):
        raise forbidden("이 동의 요청을 처리할 권한이 없습니다.", code="approval_forbidden")
    if ap.kind == "sampling" and ap.status == "pending" and decision in ("allow", "deny"):
        ap.status = "approved" if decision == "allow" else "rejected"
        ap.payload = {"messages": messages, "review": review} if decision == "allow" else {}
    elif ap.kind == "sampling" and ap.status == "review" and decision in ("send", "deny"):
        ap.status = "sent" if decision == "send" else "rejected"
        ap.payload = {**(ap.payload or {}), "text": text}
    elif ap.kind == "elicitation" and ap.status == "pending" and decision in ("accept", "decline", "cancel"):
        if decision == "accept":
            schema = (ap.args or {}).get("requestedSchema") or {"type": "object"}
            try:
                jsonschema.validate(content or {}, schema)
            except jsonschema.ValidationError as exc:
                raise bad_request(f"입력이 요청 형식과 맞지 않습니다: {exc.message}",
                                  code="invalid_elicitation") from exc
        ap.status = "approved" if decision == "accept" else "rejected"
        ap.payload = {"action": decision, "content": content if decision == "accept" else None}
    else:
        raise bad_request(f"지금 처리할 수 없는 결정입니다 ({ap.kind}·{ap.status}·{decision}).", code="approval_closed")
    ap.decided_by, ap.decided_at, ap.decision_note = p.sub, utcnow(), note
    db.commit()
    audit.append("approval.consent", actor=p.sub, target=ap.target, decision=decision,
                 data={"approval_id": ap.id, "kind": ap.kind, "edited": bool(messages) or bool(text)})
    return ap
```
**분석 / Analysis (Level 2)**
- L249-281 `decide`: 동의 카드 결정 API — 종류 확인 → 만료면 expired → **결정 권한**(`governance.can_decide` — 요청자 본인 또는 승인자) → 상태 머신(sampling: pending→approved/rejected, review→sent/rejected; elicitation: pending→approved/rejected) → elicitation accept 는 서버가 보낸 `requestedSchema` 로 **입력 검증**(L265-272) → 결정자·시각·메모 → 감사.
**짚어볼 점 / Watch-outs**
- L267-269 서버(신뢰하지 않는 업스트림)가 보낸 JSON Schema 로 `jsonschema.validate` — 스키마의 `pattern` 에 파국적 정규식(ReDoS)이 있으면 사용자 입력 검증 시 CPU 를 소모시킬 수 있다. 입력은 사용자 자신이 만들어 영향은 낮다. jsonschema 4.x 는 원격 `$ref` 를 자동으로 가져오지 않는다(SSRF 아님).
- L254-256 만료 갱신과 L277-278 결정 커밋 사이에 대기자의 `_snapshot` 이 끼어드는 경쟁은 위 L140-173 참조.

```python path=app/services/mcp_consent.py lines=284-333
# ───────────────────────── 하류 중계 ─────────────────────────
class Relay:
    """하류 MCP 클라이언트(능력 선언·SSE 호출)로 서버→클라이언트 요청을 중계.

    응답 POST 가 다른 워커로 들어와도 DB(mcp_relays)로 만난다."""

    def __init__(self, session_id: str, server_id: str, capabilities: dict, queue: asyncio.Queue | None):
        self.session_id, self.server_id, self.capabilities, self.queue = session_id, server_id, capabilities, queue

    def supports(self, method: str) -> bool:
        return CONSENT_KINDS.get(method) in self.capabilities

    async def request(self, method: str, params: dict) -> dict:
        rid = new_id("rly")

        def _store():
            with SessionLocal() as db:
                db.add(McpRelay(id=rid, session_id=self.session_id, server_id=self.server_id, method=method,
                                params=params))
                db.commit()
        await run_in_threadpool(_store)
        if self.queue is not None:  # SSE 중계(2025-11-25). 없으면 MRTR(2026-07-28) — 요청은 InputRequiredResult 로 나간다
            await self.queue.put({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + settings.mcp_consent_timeout_s

        def _answer():
            with SessionLocal() as db:
                row = db.get(McpRelay, rid)
                return row.response if row else None
        while time.monotonic() < deadline:
            res = await run_in_threadpool(_answer)
            if res is not None:
                if "error" in res:
                    e = res["error"] or {}
                    raise ConsentError(e.get("code", USER_REJECTED), e.get("message", "client error"))
                return res.get("result") or {}
            await asyncio.sleep(0.2)
        raise ConsentError(USER_REJECTED, "하류 클라이언트가 제한 시간 안에 응답하지 않았습니다.")


def answer(session_id: str, body: dict) -> bool:
    """하류 클라이언트가 POST 한 JSON-RPC 응답을 대기 중인 중계 요청에 연결."""
    with SessionLocal() as db:
        row = db.get(McpRelay, str(body.get("id")))
        if row is None or row.session_id != session_id or row.response is not None:
            return False
        row.response = {k: body[k] for k in ("result", "error") if k in body}
        row.answered_at = utcnow()
        db.commit()
        return True
```
**분석 / Analysis (Level 2)**
- L285-294 `Relay`: 하류 클라이언트로 중계 — 세션(또는 MRTR 상태 id·작업 id)·서버·클라이언트 능력·큐. `supports` 는 클라이언트가 해당 능력을 선언했는지.
- L296-321 `request`: 중계 행 저장(워커 간 공유) → 큐가 있으면 SSE 스트림에 요청을 넣고(2025-11-25 중계), 없으면 MRTR(2026-07-28 — 요청은 InputRequiredResult 로 나감). 응답 행이 채워질 때까지 0.2초 폴링, 동의 대기 시간 초과면 거부.
- L324-333 `answer`: 하류가 POST 한 JSON-RPC 응답을 대기 중인 행에 연결 — 세션 일치·미응답일 때만(다른 세션의 중계에 답할 수 없음).
**짚어볼 점 / Watch-outs**
- L330 클라이언트가 보낸 `result`/`error` 를 검증 없이 저장하고 그대로 업스트림에 전달한다 — 게이트웨이는 sampling 결과의 형식·내용(출력 가드)을 중계 경로에서는 검사하지 않는다(Host 동의 경로는 L193 에서 검사). 중계 경로도 출력 가드를 적용하는 편이 일관된다.
- mcp_relays 행 삭제(보존 정리)가 이 파일에는 없다.

## 파일 평가 / File Assessment
- 강점: (1) 서버가 아닌 게이트웨이가 모델·데이터 등급 라우팅을 결정하고, 미리보기와 실행이 같은 `prepare` 를 쓴다. (2) 동의·검토·만료·연장·감사가 상태 머신으로 명확. (3) 중계 응답을 세션에 바인딩.
- 위험: (1) [낮음 · 버그] ConsentError 외 예외에서 업스트림 무응답. (2) [중간] 클라이언트 헤더 run_id 로 다른 실행에 이벤트 주입. (3) [낮음] 만료/결정 경쟁(조건부 갱신 없음), 중계 경로 출력 가드 부재.
- 개선 권고(영향순): (1) `handle` 에 일반 예외 → -32603 응답. (2) run_id 를 서버 측 실행 소유자와 대조(또는 헤더 run_id 무시). (3) 상태 전이를 `UPDATE ... WHERE status=:expected` 로.
- 재읽기 권고 레벨: 2 — 사람 동의라는 안전 장치의 구현이라 상태 전이를 줄 단위로 확인할 가치가 있다.
