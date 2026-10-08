> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [mcp_consent.py.md](../../../../CODE-REVIEW-Lv2/app/services/mcp_consent.py.md)

### 11. app/services/mcp_consent.py
**파일 요약 / File Summary**
- 역할 / Role: 업스트림 MCP 서버 → 클라이언트 요청(sampling/createMessage · elicitation/create · roots/list · ping) 처리. 하류 클라이언트가 능력을 선언했으면 **중계**(`Relay`, mcp_relays 공유), 아니면 요청자에게 **Host 동의 카드**(Approval kind=sampling|elicitation) — sampling 은 게이트웨이가 모델을 골라 실행(선택 검토 후 전송), elicitation 은 요청 스키마로 검증한 사용자 입력을 반환. 무응답 자동 거부, 전 과정 감사.
- 진입점 거리 / Distance: 2 — 호출처 `app/services/mcp_gateway.py:310`(레거시 SSE 역채널)·`:444`(modern MRTR)·`:599`(stdio·hosted 콜백 — 세 줄 모두 21:03 스냅샷 기준, 21:30 Epic 51 수정 뒤 현재 디스크는 `:310`·`:449`·`:611`), `app/api/gateway_admin.py:789`(`decide`), `app/api/mcp.py:92`(`answer`)·`:286`(`Relay`), `app/services/mcp_modern.py:330`·`app/services/mcp_tasks.py:150`(`Relay`).
- LOC: 337 · 1차 리뷰 시점 333 → +4 (`handle` 의 일반 예외 처리 L62-65 추가, 파일 수정 14:46)
- **스냅샷 이후 소스 변경 주의 / Post-snapshot edit**: 리뷰 도중 21:35:27 에 Epic 51(D19-④) 수정이 이 파일에 적용되어 현재 디스크는 342줄(sha1 `f246ecd…`)이다. 이 보고서의 라인·인용은 모두 **21:03 스냅샷**(sha1 `a5dd24f…`, `scratchpad/snapshot-start.sha`) 기준 — 현재 파일에서 변경분을 역적용해 같은 해시를 재현하고 인용을 대조함. 변경 내용: 중계 분기 **앞에서** sampling 이면 서버 정책을 확인(`await _sampling_allowed(server_id, ctx)`, 현재 L48-49)하고, 정책 검사를 `_sampling` 에서 떼어 `_sampling_allowed`(현재 L212-216)로 옮겼다 — 아래 L36-65 블록의 [위험:중간] "중계 경로가 deny 우회" 를 겨냥한 수정(이 보고서는 스냅샷 기준 미해결로 집계, 수정분의 시험은 재확인 대상). 스냅샷→현재 라인 대응: L1-47 동일 · L48-209 +2 · L210(`_sampling` 정의) → 현재 L219 · L211-214(정책 검사) → 현재 L213-216 · L215 이후 +5.
- 주요 export: `handle`, `decide`, `Relay`, `answer`, `ConsentError`, `CONSENT_KINDS`, `USER_REJECTED`
- 의존(내부): `app.core.{config,errors,ids}`, `app.db`, `app.models.{Approval,McpRelay,RunEvent,McpServer}`, `app.services.{audit,governance,guards,llm}`, (지연) `app.core.logs`
- 외부 패키지: jsonschema, starlette
- 부수효과 / Side effects: DB 쓰기(approvals·run_events·mcp_relays), 감사 기록, LLM 호출(사용량·비용·한도 차감).
- 동시성 / Concurrency: asyncio + 0.2초 DB 폴링으로 워커 간 동의·중계 결과 공유(다중 워커 일관). 상태 전이는 읽기-수정-쓰기.
- 테스트 커버리지 흔적: `test/test_mcp_consent.py:58`(편집 후 허용·로컬 모델), `:91`(거부·시간 초과 → 도구 오류), `:117`(검토 후 전송), `:139`(elicitation 스키마 검증·거절), `:172`(하류 중계), `:215`(오케스트레이션 동의 카드), `:267`(동의 채널 없음); `test/test_review_fixes.py:438-449`(C39-22 일반 예외 → -32603); `test/test_caller_trust.py:89-105`(C40-04 run 소유 판정); `test/test_ops.py:260-266`(샘플링 정책 deny 저장).
- 2차 변경 요약 / Changes since 1st review: (1) `handle` 에 `except Exception` → 로그 + `-32603` 응답 추가(L62-65, Epic 39 "Lv2 동의 처리 예외"). (2) 파일 밖 — `ctx.run_id` 가 이제 `policy.caller_context` 로 주체 소유 실행일 때만 채워진다(`app/services/policy.py:59-69`, Epic 40) → `_event`(L116-119)의 이벤트 주입 지적이 해소. (3) mcp_relays 보존 정리(`app/services/retention.py:33-34`).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/mcp_consent.py lines=1-9
"""MCP 서버 → 클라이언트 요청 (FR-A00·A03·G09, SCR-C08) — sampling/createMessage · elicitation/create · roots/list · ping.

업스트림 MCP 서버가 도구 호출 도중 SSE 역채널(MCP 2025-11-25)로 요청을 보내면 게이트웨이가 받는다.
- 중계: 하류 MCP 클라이언트가 해당 능력을 선언하고 SSE 로 호출했으면 요청을 하류로 그대로 보내고 답을 돌려준다(`Relay`).
- Host 동의: 그 외(플랫폼 오케스트레이션·능력 없는 클라이언트)는 요청자에게 동의 카드를 띄운다. sampling 은 사용자가 허용(또는
  프롬프트 편집 후 허용)하면 게이트웨이가 Unified Model API 로 실행한다 — 모델은 서버가 아니라 게이트웨이가 정하고 내부 데이터는
  로컬 모델로만(MSG-011). 선택 시 응답을 서버로 보내기 전에 검토한다. elicitation 은 요청 스키마로 검증한 사용자 입력을 돌려준다.
무응답은 MCP_CONSENT_TIMEOUT_S(기본 60초) 후 자동 거부하고, 요청·결정·결과는 감사 로그에 남긴다.
"""
```
**분석 / Analysis (Level 2)**
- L1-9: 두 갈래 — 중계(하류 클라이언트가 자기 모델·UI 로 처리) vs Host 동의(게이트웨이가 사람 동의를 받고 자기 모델로 실행). 서버의 `modelPreferences` 는 힌트일 뿐 모델은 게이트웨이가 정한다(데이터 등급 라우팅 MSG-011).

```python path=app/services/mcp_consent.py lines=10-27
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
- L24: 동의 대상 메서드 → 승인 종류. `Relay.supports`(L297-298)도 이 표로 하류 능력 키를 찾는다.
- L25-26: MCP 사용자 거부 -1, 메서드 없음 -32601.
**짚어볼 점 / Watch-outs**
- L27 `WAIT_STATES` 는 여전히 정의 외 참조가 없다(`grep -rn WAIT_STATES app test web/src` → 이 줄뿐). 1차 삭제 후보 지적 미해결.

```python path=app/services/mcp_consent.py lines=30-35
class ConsentError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


```
**분석 / Analysis (Level 2)**
- L30-33 `ConsentError`: JSON-RPC 오류로 바꿔 업스트림에 돌려줄 "예상된 거부" 전용 예외.

```python path=app/services/mcp_consent.py lines=36-65
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
    except Exception as exc:  # noqa: BLE001 — 업스트림이 응답을 못 받고 시간 초과까지 기다리지 않게 오류로 답한다
        from app.core import logs
        logs.event("error", "gateway", "서버→클라이언트 요청 처리 실패", server=server_id, method=method, error=type(exc).__name__)
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": f"게이트웨이 내부 오류: {type(exc).__name__}"}}
```
**분석 / Analysis (Level 2)**
- L40-43: ping·roots(빈 목록 — 파일 시스템 루트 비노출, 최소 노출).
- L44-46: 동의 메서드는 ctx 가 없으면(시스템 호출 — `refresh_tools` 등) 거부. 사람 동의를 받을 채널이 없으면 기본 거부(fail closed).
- L47-52: 하류가 능력을 선언했으면 중계 후 감사.
- L53-56: 아니면 Host 동의.
- L62-65 (2차 신규): ConsentError 밖의 예외도 로그를 남기고 -32603 으로 즉시 답한다 — 1차 지적("업스트림 무응답·원 호출 500") 해결. 메시지에는 예외 타입명만 실어 내부 정보를 흘리지 않는다. 조용한 삼킴이 아니라 `logs.event("error", …)` 가 남는다. 시험 `test/test_review_fixes.py:438-449`.
**짚어볼 점 / Watch-outs**
- [위험:중간] L47-52 중계 분기가 L53-54 `_sampling` 보다 **먼저** 선택되므로, 서버별 샘플링 정책 `deny`(`_sampling_policy`, L203-214 — 운영자가 `PUT /api/v1/mcp-servers/{id}/sampling` 으로 "서버의 모델 호출 요청 거부" 로 설정, `app/api/ops.py:592-603`)가 **중계 경로에는 적용되지 않는다**. 하류 클라이언트가 sampling 능력을 선언하면 deny 로 막아 둔 서버의 sampling 요청도 그 클라이언트로 전달된다. 운영 정책의 의도(서버의 sampling 자체 차단)와 어긋난다(설계 의도 추가 확인 필요). 최소 수정: `method == "sampling/createMessage"` 이면 중계 여부와 무관하게 정책을 먼저 확인. 시험 공백: deny + 중계 조합 시험 없음(`test/test_ops.py:260-266` 은 저장만 확인). **스냅샷 이후**: 21:35 Epic 51 수정이 정확히 이 최소 수정(중계 전 `_sampling_allowed`, 현재 L48-49·L212-216)을 적용했다 — 집계는 스냅샷 기준 미해결로 두되, 수정분에 deny+중계 시험이 붙었는지 재확인 필요.
- [위험:낮음] L50-52 중계 응답을 받은 뒤 감사 기록이 실패하면(DB 오류) L62-65 가 -32603 을 돌려 하류가 정상 응답했는데도 업스트림에는 오류로 간다. 감사 실패를 로그만 남기고 결과는 전달할지 정책 결정 필요.

```python path=app/services/mcp_consent.py lines=68-86
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
```
**분석 / Analysis (Level 2)**
- L69-77 `_text_of`: MCP content(단일·목록) → 텍스트. 비텍스트는 `[image 생략]` 으로 표시만 — 이미지가 모델로 가지 않는다.
- L80-86 `_chat_messages`: SamplingMessage 또는 동의 화면에서 편집한 `{role, text}` → OpenAI 메시지. role 은 assistant 외엔 모두 user 로 정규화(서버가 임의 role 을 끼워 넣지 못함).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L82 서버가 보낸 `systemPrompt` 를 그대로 system 메시지로 쓴다(1차 지적 미해결). 사용자가 편집할 수 있는 것은 messages(L265)뿐이다. 동의 카드에 systemPrompt 가 표시되는지는 UI 확인 필요(이벤트 `params` 에 원문은 실린다, L134).
- L83 `m.get(...)` — messages 원소가 dict 가 아니면 AttributeError → L62-65 에서 -32603(이제 업스트림 대기 없음).

```python path=app/services/mcp_consent.py lines=89-101
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
- L89-96 `_body`: 게이트웨이 모델 API 요청 — `model: "auto"`(라우팅 규칙이 결정), `maxTokens` 기본 256.
- L99-101 `_cctx`: LLM 호출 맥락. 에이전트 표기 `mcp:{server}/sampling` 으로 사용량이 서버별로 남는다(`test/test_mcp_consent.py:83`). `ctx.data_class` 는 이제 서버 판정 값(`app/services/policy.py:71`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L91 `maxTokens` 상한이 없다(1차 지적 미해결). `llm.prepare` 의 한도 확인은 입력 토큰 추정만 본다(`app/services/llm.py:221-222`). 비용 상한은 한도·예산 정책에 의존 — 동의 카드에 요청 토큰 수를 보여 주거나 서버별 상한을 두는 편이 안전.

```python path=app/services/mcp_consent.py lines=104-119
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
- L104-113 `_preview`: 동의 카드의 라우팅 미리보기를 실제 실행과 같은 `prepare` 로 계산 — 화면과 실행이 어긋나지 않게.
- L116-119 `_event`: 실행(run)에 속한 승인이면 SSE 재생용 RunEvent. `ap.run_id` 의 출처 `ctx.run_id` 가 이제 "그 주체의 실행" 일 때만 채워진다(`app/services/policy.py:59-69`, `app/api/mcp.py:58`) — 1차 [위험:중간] 이벤트 주입 지적 해결. 시험 `test/test_caller_trust.py:89-105`.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L108 `llm.prepare` 는 끝에서 `ratelimit.check` 를 호출하고(`app/services/llm.py:222`), 이 함수는 토큰 버킷을 차감한다(`app/services/ratelimit.py:258` `backend().take`). 미리보기(L215)와 실제 생성(`_generate` L186)이 각각 `prepare` 를 부르므로 sampling 1건이 요청 한도를 2번 소모한다(1차 지적 미해결). 정책 거부 시 감사·지표(`app/services/llm.py:214` `gateway.model.deny`)도 두 번 남는다. 최소 수정: 미리보기 전용으로 한도 차감 없는 판정(`prepare(..., dry_run=True)`).
- L109-110 미리보기가 정책 거부여도 동의 카드는 열린다 — 사용자가 허용해도 `_generate` 에서 거부된다(혼란). 거부면 카드 없이 바로 ConsentError 가 더 명확.

```python path=app/services/mcp_consent.py lines=122-141
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
- L125-131: 동의 승인 행 — `approver="requester"`(요청자 본인 결정), 위험 medium, 만료 = 지금 + 60초(연장 최대 10회, `app/services/governance.py:237,250-271`), 인자 원문·해시, 라우팅 미리보기.
- L132-137: `flush` 로 id 확정 후 실행 이벤트 추가 → 커밋. L139-140 감사는 커밋 **후**(별도 세션 — SQLite 잠금 회피 관례).
**짚어볼 점 / Watch-outs**
- L127 `args=params` — sampling 프롬프트 원문(개인정보 포함 가능)이 approvals 에 평문 저장된다. approvals 는 보존 정리 대상이 아니다(`app/services/retention.py:29-34` 에 Approval 없음). 1차 지적 미해결 — 보존 기간 정책 확인 필요.

```python path=app/services/mcp_consent.py lines=144-162
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
```
**분석 / Analysis (Level 2)**
- L144-153 `_snapshot`: 현재 상태 조회 + 만료 지연 처리(expired 기록·이벤트·감사). 연장(`governance.extend`)은 `expires_at` 만 늘리므로 이 함수가 자연스럽게 따라간다.
- L156-162 `_wait`: 상태가 `phase` 를 벗어날 때까지 0.2초 폴링 — 결정 API 가 다른 워커로 들어와도 DB 로 만난다(다중 워커 일관). 자체 타임아웃은 없고 만료(최대 60초 × 11)에 의존.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L147-150 의 만료 처리와 `decide`(L258-282)가 동시에 같은 행을 읽고 각자 커밋하면 마지막 쓰기가 이긴다(조건부 UPDATE 없음 — 1차 지적 미해결). 대조적으로 도구 승인 소비는 이미 원자화됐다(`app/services/governance.py:180-181` `UPDATE … WHERE status='approved'` + rowcount). 같은 방식으로 `status IN ('pending','review')` 조건부 전이 권장.
- L146 행이 없으면 AttributeError — 이제 L62-65 가 -32603 으로 답해 업스트림 무한 대기는 없다(부분 해결).
- 대기자 하나당 초당 5회 DB 조회(1차 지적 미해결).

```python path=app/services/mcp_consent.py lines=165-177
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
- L165-177 `_set`: 상태·결과 병합(새 dict 로 재할당 — JSON 제자리 변경 미추적 문제를 피함)·만료 갱신·이벤트를 한 트랜잭션으로.
- 흔한 실수: `ap.result.update(...)` 처럼 제자리 변경하면 SQLAlchemy JSON 컬럼은 변경을 감지하지 못한다. 여기서는 L172 가 새 dict 를 대입한다(올바름).

```python path=app/services/mcp_consent.py lines=180-207
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
- L180-200 `_generate`: 동의 후 실제 생성 — `prepare`(입력 가드·라우팅·한도) → `execute`(재시도·폴백) → 출력 가드(`guards.scan_output` — PII 마스킹, `app/services/guards.py:152-157`) → `finalize`(사용량·비용). 사용자가 편집한 messages 도 `prepare` 의 입력 가드를 다시 거친다.
- L191-192: 정책 거부(ApiError)는 사용자 거부 코드로. 그 밖의 예외(공급자 네트워크 오류 등)는 이제 `handle` L62-65 가 -32603 으로 정리(1차 연결 지적 해결).
- L203-207 `_sampling_policy`: 서버별 ask|deny, 기본 ask.
**짚어볼 점 / Watch-outs**
- L194-199 응답 파싱과 사용량 기록이 한 트랜잭션 — 파싱·가드 예외면 사용량도 기록되지 않는다(1차 지적 미해결, 낮음).

```python path=app/services/mcp_consent.py lines=210-239
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
- L211-214: 서버 정책 deny 면 즉시 거부·감사 — 단, 이 검사는 Host 동의 경로에만 있다(위 L47-52 Watch-out).
- L215-220: 미리보기 → 동의 카드 → pending 이탈 대기 → approved 가 아니면 거부(rejected=사용자 거부, 그 외=시간 초과).
- L222: 편집한 messages 가 있으면 그것으로 생성.
- L223-232: 검토 옵션이면 초안을 review 로 올리고 만료를 새로 60초로 — 사용자가 send 할 때까지 대기, 수정한 텍스트로 교체 가능.
- L233-239: completed 기록·감사 → MCP CreateMessageResult.
**짚어볼 점 / Watch-outs**
- L232 `… or text` — 사용자가 빈 문자열로 보내면 초안이 전송된다(1차 지적 미해결, 낮음).
- L239 게이트웨이 내부 모델 id 를 서버에 알린다(1차, 낮음 — 사양 필드).

```python path=app/services/mcp_consent.py lines=242-250
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
- L242-250: 동의 카드 → 결정 대기 → accept 면 `decide` 에서 이미 스키마 검증된 content 반환, 만료면 cancel, 그 외 decline. 결과 행에 action 기록.

```python path=app/services/mcp_consent.py lines=253-285
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
- L256-262: 종류 확인 → 만료 처리 → 결정 권한(`governance.can_decide`). 호출처 `app/api/gateway_admin.py:785-791` 의 `_approval`(`:733-737`)이 먼저 "결정 권한자 또는 요청자·위임자" 만 행을 볼 수 있게 거른다.
- L263-280: 상태 머신 — sampling pending→approved/rejected, review→sent/rejected; elicitation pending→approved/rejected. accept 는 서버가 보낸 `requestedSchema` 로 입력 검증.
- L281-284: 결정자·시각·메모 → 커밋 → 감사.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L261 `can_decide` 는 requester 승인에 대해 "요청자 본인 **또는** `approvals:decide` 보유자" 를 허용한다(`app/services/governance.py:274-280`). 도구 승인에는 맞는 규칙이지만, 동의 카드는 본인의 결정·입력이다 — 운영자·보안 역할이 다른 사용자의 sampling 프롬프트를 편집해 허용하거나 elicitation 입력을 대신 채울 수 있다(감사에는 `decided_by` 가 남는다). 최소 수정: `ap.kind in CONSENT_KINDS` 면 요청자(또는 명시 위임자)만.
- [위험:낮음] L271-273 서버가 보낸 `requestedSchema` 의 원격 `$ref` — 가능성(추가 확인 필요). 잠금 파일의 jsonschema 4.26.0·referencing 0.37.0(`uv.lock:1037-1038,1883-1884`)에서 `jsonschema.validate` 의 기본 레지스트리는, 알려진 기억으로는, 모르는 `http(s)://` 참조를 경고(DeprecationWarning)와 함께 `urllib.request.urlopen` 으로 **자동 조회**하는 호환 동작을 아직 유지한다(설치본이 없어 라이브러리 소스로 확인하지 못함 — 저장소에 `.venv` 없음). 그렇다면 악성 업스트림이 `{"type": "object", "properties": {"x": {"$ref": "http://10.0.0.5:8080/admin"}}}` 를 보내고 사용자가 accept 하면, 게이트웨이 프로세스가 httpx egress 가드(`app/core/http.py:34-38` — 링크 로컬·IMDS 차단)를 **거치지 않고** 그 주소(169.254.169.254 포함, urllib 은 리디렉션도 따른다)로 GET 한다. 응답은 업스트림에 돌아가지 않아 블라인드지만, 검증 오류 메시지(L275 `exc.message`)에 원격 문서 값이 섞여 결정자에게 보일 수 있다. 최소 수정: `jsonschema.validators.validator_for(schema)(schema, registry=referencing.Registry())` 처럼 **빈 레지스트리**로 검증(원격 조회 금지)하거나, MCP 사양대로 `requestedSchema` 를 평면 원시 속성만 허용하도록 `_open` 에서 거른다.
- [위험:낮음] L271-276 서버가 보낸 스키마 자체가 잘못되면 `jsonschema.validate` 가 `SchemaError` 를 던진다 — `ValidationError` 만 잡으므로 결정 API 가 500 이 되고 사용자는 accept 할 수 없다(decline 만 가능). `jsonschema.exceptions.SchemaError` 도 잡아 400 으로. 신뢰하지 않는 스키마의 `pattern` 정규식 비용(1차 지적)도 그대로.
- L258-260 여기서의 만료 처리는 이벤트·감사를 남기지 않는다(`_snapshot` 과 비대칭). 경쟁 조건은 L144-162 참조.

```python path=app/services/mcp_consent.py lines=288-298
# ───────────────────────── 하류 중계 ─────────────────────────
class Relay:
    """하류 MCP 클라이언트(능력 선언·SSE 호출)로 서버→클라이언트 요청을 중계.

    응답 POST 가 다른 워커로 들어와도 DB(mcp_relays)로 만난다."""

    def __init__(self, session_id: str, server_id: str, capabilities: dict, queue: asyncio.Queue | None):
        self.session_id, self.server_id, self.capabilities, self.queue = session_id, server_id, capabilities, queue

    def supports(self, method: str) -> bool:
        return CONSENT_KINDS.get(method) in self.capabilities
```
**분석 / Analysis (Level 2)**
- L289-298 `Relay`: `session_id` 는 세 가지 중 하나 — 레거시 클라이언트 세션 id(`app/api/mcp.py:286`), MRTR 상태 id(`app/services/mcp_modern.py:330`), Task id(`app/services/mcp_tasks.py:150`). 응답 연결 시 이 값이 소유권 바인딩 키가 된다.
- L297-298 `supports`: 하류가 선언한 능력 키(`sampling`·`elicitation`) 확인.

```python path=app/services/mcp_consent.py lines=300-325
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
```
**분석 / Analysis (Level 2)**
- L301-308: 게이트웨이가 만든 id(`rly_…`)로 중계 행 저장 — 업스트림이 준 id 를 하류에 그대로 쓰지 않아 id 충돌·추측을 피한다.
- L309-310: 큐가 있으면 SSE 스트림으로 요청을 흘린다(레거시). 없으면 MRTR·Task — 행 자체가 inputRequests 로 노출된다.
- L311-325: 응답이 채워질 때까지 0.2초 폴링, 60초 초과면 사용자 거부로.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L321-323 하류가 보낸 `result` 를 형식·내용 검사 없이 업스트림에 전달한다 — Host 동의 경로는 출력 가드(L197)를 거치지만 중계 경로는 거치지 않는다(1차 지적 미해결). 하류가 자기 모델 응답을 보낼 때 PII 마스킹 정책이 일관되지 않다.
- [위험:낮음] L311 중계 대기 시간은 고정 60초 — Host 동의처럼 연장(최대 10회)할 수 없다. MRTR·Task 경로에서 사람이 하류 UI 에서 답하는 데 60초를 넘기면 거부된다.
- L305-306 `params`(sampling 메시지 원문)가 mcp_relays 에 평문 저장 — 이제 1일 뒤 삭제(`app/services/retention.py:33-34`).

```python path=app/services/mcp_consent.py lines=328-337
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
- L328-337 `answer`: 레거시 경로의 하류 응답 연결 — 행의 `session_id` 일치·미응답일 때만. 호출처 `app/api/mcp.py:90-93` 이 먼저 `check_session(sid, server_id, p)`(세션 소유 주체·서버 확인, `app/services/mcp_gateway.py:1072-1079`)을 통과시키므로 "세션 소유 → 중계 행" 2단 인가가 된다. 다른 사용자의 중계에 답할 수 없다.
- 흔한 실수: `row.response is not None` 확인과 대입 사이가 원자적이지 않다 — 같은 세션의 중복 POST 두 개가 동시에 오면 둘 다 True 를 받을 수 있다(첫 쓰기가 덮인다, 영향 낮음).

## 파일 평가 / File Assessment
- 강점: (1) 모델·데이터 등급 라우팅을 서버가 아닌 게이트웨이가 결정하고, 미리보기·실행이 같은 `prepare` 를 쓴다. (2) 동의·검토·만료·연장·감사 상태 머신과 DB 폴링으로 다중 워커에서도 결정이 만난다. (3) 중계 응답을 세션·상태·작업 id 에 바인딩, 예외 시 업스트림에 즉시 -32603(2차 개선).
- 위험: (1) [위험:중간] 중계 경로가 서버 샘플링 정책 deny 를 우회 — `app/services/mcp_consent.py:47-52`. (2) [위험:낮음] 동의 카드를 `approvals:decide` 보유자도 대리 결정·편집 — `:261` + `app/services/governance.py:280`. (3) [위험:낮음] 미리보기의 한도 이중 차감·만료/결정 경쟁·잘못된 스키마 500·원격 `$ref` 자동 조회 가능성(egress 가드 밖) — `:108`·`:147-150`·`:271-276`.
- 개선 권고(영향순): (1) `handle` 에서 sampling 이면 중계 전에 서버 정책 확인. (2) 동의 카드 결정은 요청자(·명시 위임자)로 한정, 상태 전이를 조건부 UPDATE 로. (3) 미리보기는 한도 차감 없는 판정 함수로, `SchemaError` 처리·빈 레지스트리 검증(원격 `$ref` 금지), 중계 경로에도 출력 가드 적용.
- 파일 위험 등급 / Risk grade: 중간 — 사람 동의 경계 모듈에 [위험:중간] 1건(정책 우회 — 스냅샷 이후 21:35 수정 적용, 재확인 대상) + 낮음 다수(신뢰 않는 elicitation 스키마의 원격 `$ref` 자동 조회 가능성 포함).
- 재읽기 권고 레벨: 2 — 사람 동의라는 안전 장치이므로 분기 순서(중계 vs 정책)와 상태 전이를 줄 단위로 다시 볼 가치가 있다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| L27 `WAIT_STATES` 미참조 | 미해결 | `app/services/mcp_consent.py:27` (참조 없음) |
| L60 `except ConsentError` 만 — 업스트림 무응답 | 해결 | `mcp_consent.py:62-65` · `test/test_review_fixes.py:438-449` |
| L78 서버 `systemPrompt` 그대로 system 메시지 | 미해결 | `mcp_consent.py:82` |
| L87 `maxTokens` 상한 없음 | 미해결 | `mcp_consent.py:91` |
| L104 미리보기가 한도 2번 차감 | 미해결 | `mcp_consent.py:108` → `app/services/llm.py:222` → `app/services/ratelimit.py:258` |
| L113-115 헤더 run_id 로 다른 실행에 이벤트 주입 [중간] | 해결 | `app/services/policy.py:59-69` · `app/api/mcp.py:58` · `test/test_caller_trust.py:89-105` |
| L123 `args=params` 평문 저장 | 미해결 | `mcp_consent.py:127` (approvals 보존 정리 없음 `app/services/retention.py:29-34`) |
| L143-146 만료/결정 경쟁 | 미해결 | `mcp_consent.py:147-150`·`:258-282` |
| L142 행 None → AttributeError | 부분 해결 | `mcp_consent.py:146` 그대로, 결과는 `:62-65` 가 -32603 으로 |
| 대기자당 초당 5회 DB 조회 | 미해결 | `mcp_consent.py:156-162` |
| L186 `llm.execute` 비 ApiError 예외 누출 | 해결 | `mcp_consent.py:62-65` 가 처리 |
| L190-195 파싱·사용량 한 트랜잭션 | 미해결 | `mcp_consent.py:194-199` |
| L228 빈 문자열이면 초안 전송 | 미해결 | `mcp_consent.py:232` |
| L235 모델 id 노출 | 미해결(수용) | `mcp_consent.py:239` |
| L267-269 신뢰 않는 스키마 정규식 | 미해결 | `mcp_consent.py:271-276` (+ SchemaError 500 신규) |
| L330 중계 결과 출력 가드 없음 | 미해결 | `mcp_consent.py:321-323` |
| mcp_relays 보존 정리 없음 (delegated BUG-01) | 해결 | `app/services/retention.py:33-34` · `test/test_review_fixes.py:152-169` |
