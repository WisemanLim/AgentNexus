> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [llm.py.md](../../../../CODE-REVIEW-Lv2/app/services/llm.py.md)

### 21. app/services/llm.py
**파일 요약 / File Summary**
- 역할 / Role: Unified Model API 실행 엔진. `prepare`(동기·DB: 입력 가드 → 라우팅 규칙·전략으로 후보 → 정책·데이터 등급 재라우팅 → 공급자 허용 등급·에어갭 → 한도·예산·한도 폴백), `execute`/`execute_stream`/`execute_embed`(비동기: 후보 순서 재시도·폴백·서킷·지연 EWMA), `finalize`(결정 로그·사용량·비용·토큰 창·메트릭). 모델 카탈로그 동기화 보조 함수 포함.
- 진입점 거리 / Distance: 2 — 호출처 `app/api/llm.py:63`(prepare)·`:71`(execute)·`:79`·`:88`(finalize)·`:95`(execute_stream)·`:141`(embed_texts), `app/services/mcp_consent.py:108`·`:186`·`:190`·`:198`(샘플링), `app/services/knowledge.py:61`(지식 베이스 임베딩), `app/api/ops.py:564`(라우팅 장애 주입 시험), `provider_health` ← `app/api/llm.py:180`·`app/api/admin.py:117`·`app/api/observe.py:244`.
- LOC: 426 (현재 `wc -l`) · 1차 425 → +1
- 주요 export: `prepare`, `execute`, `execute_stream`, `execute_embed`, `embed_texts`, `finalize`, `cost_of`, `resolve`, `upstream_body`, `CallContext`·`Plan`·`Target`, `provider_health`, `reset_state`, `model_out`, `sync_model_catalog`, `default_data_class`
- 의존(내부): `app.core.{logs,metrics,auth,tracing,context,errors,ids}`, `app.db.SessionLocal`, `app.models.{CatalogItem,Model,Provider,RoutingDecision,RoutingRule,UsageRecord}`, `app.services.{audit,guards,policy,ratelimit,secrets,providers}`, (지연) `app.services.deploy`, `starlette.concurrency.run_in_threadpool`
- 외부 패키지: sqlalchemy, opentelemetry, starlette, 표준 `fnmatch`·`random`·`decimal`
- 부수효과 / Side effects: 공급자 HTTP(어댑터), DB 쓰기(`RoutingDecision`·`UsageRecord` — finalize, 감사 `gateway.model.deny` — prepare), 한도 백엔드(rps 버킷 차감 — `ratelimit.check`, 토큰 창 가산 — `ratelimit.record_tokens`, prod 는 Valkey), Prometheus 메트릭, 프로세스 전역 `_FAILS`·`_LATENCY`, **요청 `body["messages"]` 를 제자리(in-place) 변경**(마스킹본으로 교체 — 호출부가 같은 `body` 를 execute 에 넘기므로 업스트림에는 마스킹본이 간다).
- 동시성 / Concurrency: prepare·finalize 는 호출부가 `run_in_threadpool` 로 스레드풀에서, execute* 는 이벤트 루프에서 실행. `_FAILS`·`_LATENCY` 는 락 없이 루프(쓰기 L46-54)와 스레드풀(읽기 L105-129·L57-58)이 공유. 한도·예산은 검사(prepare L221-225)와 기록(finalize L384-393) 사이에 예약(reservation)이 없다.
- 테스트 커버리지 흔적: `test/test_llm.py:38`(사용량·비용), `:54`(내부 데이터 → 로컬 재라우팅), `:68`(장애 폴백·서킷), `:104`(스트리밍·첫 청크 전 폴백), `:160`(입력 가드), `:170`(429·예산 폴백 — 폴백 대상은 로컬 `domain-local` 만), `:188`(재라우팅 없는 거부); `test/test_caller_trust.py:72`(C40-03 하향 신고 권한)·`:108`(C40-05 모델 API 서버 판정 등급); `test/test_review_fixes.py:453`(C39-23 가중치 422)·`:465`(C39-24 멀티모달 마스킹); `test/test_gateway_policy.py:107`(예산 경고·차단·폴백 — `domain-local`). **공백**: 스트림 중도 연결 종료, 상용 모델을 한도 폴백으로 둔 상태의 internal/confidential 요청, `Model.data_classes`, 규칙 `timeout_ms`, 동시 요청 예산 초과.
- 2차 변경 요약 / Changes since 1st review: (1) `default_data_class` 가 `policy.default_data_class` 로 위임(L101-102, Epic 40 — 워커별 5초 캐시 `app/services/policy.py:22-35`), `Setting` import 제거(L26). 이 래퍼는 이제 호출처가 없다(`grep default_data_class app test` → `app/services/policy.py` 와 이 정의뿐). (2) `_guard_messages` 의 멀티모달 마스킹이 이미지 파트를 유지하도록 변경(L161-164, Epic 39 C39-24 — `test/impl/39th/scenario.md:39`). (3) 파일 밖 — `ctx.data_class` 가 이제 `policy.caller_context` → `declared_data_class` 의 서버 판정값(기본값보다 낮은 신고는 `data:declare` 권한 필요, 없으면 기본값으로 상향 + 감사 `gateway.data_class.raise` — `app/services/policy.py:42-53`, 호출 `app/api/llm.py:29-34`, Epic 40 `test/impl/40th/scenario.md:9`). (4) 파일 밖 — ab 가중치 저장 시 검증(`app/api/llm.py:344-352`).

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
from app.models import CatalogItem, Model, Provider, RoutingDecision, RoutingRule, UsageRecord
from app.services import audit, guards, policy, ratelimit, secrets
from app.services.providers import Adapter, ProviderError, adapter_for, estimate_tokens, message_text

PASSTHROUGH = {"messages", "temperature", "top_p", "max_tokens", "max_completion_tokens", "tools", "tool_choice",
               "response_format", "stop", "n", "presence_penalty", "frequency_penalty", "seed", "user",
               "parallel_tool_calls", "logit_bias", "logprobs", "top_logprobs"}
MILLION = Decimal(1_000_000)
```
**분석 / Analysis (Level 2)**
- L1-5: 3단계 구조 — DB 를 만지는 prepare·finalize 는 동기 함수(스레드풀에서 실행), 네트워크를 기다리는 execute 는 `async`(이벤트 루프). 동기 DB 호출을 루프에서 직접 하지 않게 하려는 경계다. 이 경계는 호출부가 지켜야 한다(예: `app/api/llm.py:60-64` 가 `run_in_threadpool(_prep)`).
- L25: `SessionLocal` 은 `embed_texts`(L299-328)가 스스로 세션을 열기 위해 필요. 나머지 함수는 세션을 인자로 받는다(세션·트랜잭션 범위는 호출자 책임).
- L26 (2차): `Setting` 제거 — `default_data_class` 가 policy 로 위임되면서 불필요해졌다.
- L30-32 `PASSTHROUGH`: 업스트림으로 보낼 OpenAI 필드 화이트리스트(`upstream_body` L236-237). 게이트웨이 전용 필드(`metadata`·`stream` 등)가 공급자로 새지 않는다. `n`(완성 n 개)·`max_tokens` 는 비용을 키우지만 게이트웨이가 상한을 두지 않는다 — 사용량은 업스트림 `usage` 로 기록되므로 회계는 맞지만, 한도 사전 추정(L221)에는 반영되지 않는다(아래 L221-225).
- L33: 단가 단위(1M 토큰당 USD). `Decimal` 로 둬 부동소수 누적 오차를 피한다.

```python path=app/services/llm.py lines=34-63

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
- L36-38: 모델별 (연속 실패 수, 마지막 실패 시각)과 지연 EWMA. 임계 3회·열림 30초. **워커 프로세스마다 따로** 존재한다(uvicorn `--workers 2` 기본, `Dockerfile:47`) — 워커 간 공유되지 않으므로 워커 수만큼 실패해야 모두 열린다.
- L41-43: 3회 이상 실패했고 마지막 실패 후 30초 이내면 "열림". 30초 뒤엔 카운트가 그대로라 한 번만 더 실패해도 다시 열린다(암묵적 half-open — 1차 지적, 의도된 단순화).
- L46-54: 성공이면 실패 기록 삭제 + EWMA(0.7·이전 + 0.3·현재), 실패면 카운트 +1. `time.monotonic()` 은 시스템 시계 변경에 영향을 받지 않는다.
- L57-63: 상태 조회(관리·관측 API)와 시험용 초기화.
**짚어볼 점 / Watch-outs**
- [위험:낮음] 이름은 서킷 브레이커지만 **호출을 막지 않는다**. `_circuit_open` 은 `resolve` 의 후보 정렬(L128-129)에만 쓰이고 `execute`(L243-248)·`execute_stream`(L335-338)은 열린 후보도 그대로 호출한다. 규칙이 없는 직접 모델 요청은 `resolve` 가 `[requested]` 만 돌려주므로(L110-111) 정렬조차 없다. 시나리오: 상용 모델 하나가 연결 무응답 상태이면 요청마다 `retries+1`(기본 2)회 × httpx 타임아웃(기본 60초, L137)을 기다린 뒤 502 — 30초 열림 구간에도 업스트림을 계속 두드린다. 최소 수정: execute 에서 `_circuit_open(tgt.model_id)` 이고 뒤에 다른 후보가 남아 있으면 건너뛰고, 마지막 후보일 때만 시도(또는 열림 중 1건만 통과시키는 half-open).
- [위험:낮음] L58 `[m for m in _FAILS ...]` 는 딕셔너리를 순회한다. 호출처 `list_providers`(`app/api/llm.py:177-180`)는 동기 `def` 라 스레드풀에서 돌고, 같은 순간 이벤트 루프의 `_mark`(L54)가 **새 키**를 넣으면 `RuntimeError: dictionary changed size during iteration` 이 날 수 있다(가능성 — GIL 아래에서도 순회 중 크기 변경은 검출된다, 빈도는 낮음). 최소 수정: `[m for m in list(_FAILS) if _circuit_open(m)]`.

```python path=app/services/llm.py lines=64-103


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
    return policy.default_data_class(db)
```
**분석 / Analysis (Level 2)**
- L66-74 `Target`: 실행 대상 1개 — 게이트웨이 모델 id(별칭), 업스트림 모델명, 분류, 공급자 id, 어댑터(이미 시크릿·TLS 가 묶인 객체), 단가. prepare 단계에서 만들어 execute 로 넘긴다(execute 는 DB 를 다시 보지 않음).
- L77-88 `Plan`: `targets` 는 실제로 시도할 순서, `candidates` 는 `resolve` 가 낸 원래 후보(폴백 판정 L374-375 의 기준). `field(default_factory=list)` — 가변 기본값을 인스턴스마다 새로 만든다(`notes: list = []` 로 쓰면 모든 Plan 이 같은 리스트를 공유하는 흔한 실수).
- L91-98 `CallContext`: 호출 주체·데이터 등급·호출 에이전트·세션/실행/단계 id. 2차부터 API 경로의 `data_class`·`agent`·`run_id` 는 `policy.caller_context`(`app/services/policy.py:56-73`)의 서버 판정값이다. 기본값 `"internal"` 은 조직 기본값(`gateway.default_data_class`)과 무관한 하드코딩이지만, 현재 모든 생성처(`app/api/llm.py:34`, `app/services/knowledge.py:57`, `app/services/mcp_consent.py:100`, `app/api/ops.py:564`)가 값을 명시하므로 실제로 쓰이지 않는다.
- L101-102 (2차): 정책 모듈의 5초 캐시 함수로 위임. 호출처가 없는 래퍼라 지워도 되고, 남긴다면 "하위 호환용" 주석이 필요하다.

```python path=app/services/llm.py lines=104-130

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
- L106-109: 활성 규칙을 우선순위·생성 순으로 읽고, 요청 모델명이 glob(`fnmatchcase` — 대소문자 구분)에 맞고 데이터 등급 조건이 비었거나 같은 **첫** 규칙을 고른다. 규칙은 요청마다 DB 에서 다시 읽는다(캐시 없음 — 변경이 즉시 반영되는 대신 요청마다 조회 1회).
- L110-111: 규칙이 없으면 요청 이름 그대로 단일 후보.
- L113-126: 전략별 정렬 — cost(입력+출력 단가 합, 미등록 1e9 로 뒤로), latency(EWMA, 미측정 0.0 → 맨 앞), quality(높은 순, 미평가 0.5), ab(가중 무작위로 첫 후보만 뽑고 나머지는 원래 순서). `list.sort` 는 안정 정렬이라 같은 값이면 규칙에 적힌 순서가 유지된다.
- L127-129: 폴백 목록을 뒤에 붙이고(중복 제외), 서킷이 닫힌 후보를 앞으로. 열린 후보도 **뒤에 남는다**(제외가 아님 — 위 L34-63 Watch-out).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L112 `t["model"]` — 규칙 저장 스키마 `targets: list[dict]`(`app/api/llm.py:336`)는 각 항목의 `model` 키를 검증하지 않는다(2차에서 가중치만 검증, `:344-352`). `{"name": "gpt-4o"}` 처럼 키를 잘못 쓴 규칙을 저장하면 그 규칙에 맞는 **모든 요청이 `KeyError` → 500** 이 된다(C39-23 이 고친 가중치 문제와 같은 부류). 또 가중치 검증은 API 경로에만 있어 구성 복원(`app/api/admin.py:313-316` 의 `db.merge`)으로 들어온 규칙은 L124 에서 여전히 `ValueError` 를 낼 수 있다. 최소 수정: `RuleIn.targets: list[TargetIn]`(`model: str` 필수, `weight: float = Field(1, ge=0)`), 그리고 `resolve` 에서 `t.get("model")` 없는 항목은 건너뛰고 가중치 변환 실패 시 priority 로 대체.
- L118 latency 전략: 처음 보는 모델이 항상 가장 빠른 것으로 정렬(1차 지적 그대로). 탐색(exploration) 효과는 있지만 의도라면 주석이 필요하다.
- L114-116 단가 합 정렬은 근사(1차 지적 그대로, 수용 가능).

```python path=app/services/llm.py lines=131-166

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
```
**분석 / Analysis (Level 2)**
- L132-143 `_target`: 공급자·모델 활성 확인 → 공급자 API 키 복호화(`secrets.get_plain`, 후보마다 1회) → 공급자 `options.timeout_s`(기본 60초)로 어댑터 생성. `adapter_for` 가 mTLS 필수 정책 위반이면 `ProviderError(403, retryable=False)` 를 던지고(`app/services/providers.py:398-402`) 여기서 후보 제외 — 정책 위반 공급자로는 절대 보내지 않는 fail-closed.
- L146-165 `_guard_messages`: user·system·developer·tool 메시지 본문을 입력 가드(`guards.scan_input`, `app/services/guards.py:134-149`)로 검사 — 인젝션 패턴·PII/시크릿. 설정이 block 이면 400, mask 면 마스킹본으로 **원래 dict 를 직접 교체**한다(반환값이 아니라 부수효과로 전달 — 호출부가 같은 `body` 를 execute 에 넘기기 때문에 동작한다. 복사본을 넘기는 리팩터링을 하면 마스킹이 조용히 사라지는 지점).
- L151: `message_text`(`app/services/providers.py:46-52`)는 텍스트 파트를 **구분자 없이** 이어 붙인다.
- L161-164 (2차): 멀티모달(list content) — 텍스트가 아닌 파트(`rest`)를 순서대로 두고, 원래 목록의 첫 텍스트 파트 위치(`first`)에 마스킹본 하나를 넣는다. `first` 앞의 원소는 모두 비텍스트라 `rest[:first]` 와 일치하므로 인덱스 계산은 맞다.
**짚어볼 점 / Watch-outs**
- [위험:중간] L137 **라우팅 규칙의 `timeout_ms` 가 어디에도 적용되지 않는다.** 시간 제한은 공급자 `options.timeout_s`(기본 60초) 하나뿐이다. `RoutingRule.timeout_ms`(`app/models.py:631`, 기본 30000)는 API 가 입력받아 저장·응답하고(`app/api/llm.py:340`·`:355`·`:366`) 콘솔이 "재시도 N회 · 타임아웃 30s" 로 표시·편집하지만(`web/src/pages/gateway/GatewayMore.tsx:1057`·`:1110`), `grep -rn timeout_ms app/` 의 서비스 코드 사용처는 0건이다. 시나리오: 운영자가 빠른 장애 전환을 위해 규칙 타임아웃을 5초로 낮춰도, 첫 후보가 무응답이면 httpx 읽기 타임아웃 60초 × (retries+1) 회를 기다린 뒤에야 다음 후보로 간다 — 후보 2개·retries=1 이면 최악 약 4분(60×2×2), 콘솔은 5초라고 보여 준다. 또 httpx `timeout=` 은 연결·읽기·쓰기·풀 각 단계의 제한이지 **요청 전체 시간 제한이 아니어서**, 바이트를 조금씩 흘리는 업스트림은 60초를 훨씬 넘겨도 끊기지 않는다. 최소 수정: `Plan` 에 `timeout_s = rule.timeout_ms / 1000`(규칙 없으면 공급자 값)을 담고 execute 에서 시도마다 `await asyncio.wait_for(tgt.adapter.chat(req, tgt.upstream), plan.timeout_s)` 로 감싼 뒤 `asyncio.TimeoutError` 를 `ProviderError(504, retryable=True)` 로 변환, 전체 마감(deadline)도 추가. 시험 공백: 규칙 타임아웃 시험 없음.
- [위험:낮음] L137 `float(...)` — 공급자 `options` 는 자유 dict(`app/api/llm.py:166`)라 `"timeout_s": "60s"` 같은 값이 저장될 수 있고, 그러면 그 공급자를 후보로 가진 **모든 요청이 prepare 에서 `ValueError` → 500**. 최소 수정: 공급자 저장 시 `timeout_s` 숫자 검증, 여기서는 변환 실패 시 기본값.
- [위험:낮음] L149-150 `assistant` 역할 미검사(1차 지적, 미해결). OpenAI 호환 API 는 클라이언트가 대화 이력을 통째로 보내므로 가짜 assistant 턴에 PII·인젝션 문구를 넣어 업스트림으로 보낼 수 있다. 최소 수정: 역할 필터를 없애고 모든 메시지 검사(assistant 는 mask 만, block 은 user/tool 만 등 정책 분리).
- [위험:낮음] L161-164 (2차 수정의 남은 부분) 이미지 파트는 보존되지만, 텍스트 파트가 여럿이면 하나로 **합쳐** 첫 텍스트 자리에 둔다. `[text1, image, text2]` → `[text1+text2(마스킹본), image]` 가 되어 텍스트·이미지의 상대 순서가 바뀌고, L151 이 구분자 없이 이어 붙였으므로 단어가 붙는다(예: "이 그림을 설명해줘" + "그리고 요약" → "이 그림을 설명해줘그리고 요약"). 시험(`test/test_review_fixes.py:465-473`)이 이 합침을 기대값으로 고정했다. 최소 수정: 텍스트 파트마다 `scan_input` 을 따로 돌려 각 파트의 `text` 만 바꾼다.
- [위험:낮음] L148-149 `m.get` — `messages` 원소가 dict 가 아니면(문자열 등) `AttributeError` → 500. 호출부는 list·비어 있지 않음만 검사한다(`app/api/llm.py:57-58`). 최소 수정: 원소가 dict 가 아니면 400 `invalid_request_error`.

```python path=app/services/llm.py lines=167-197

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
```
**분석 / Analysis (Level 2)**
- L169: 입력 가드를 라우팅보다 **먼저** — 차단이면 모델·한도를 보기 전에 400. 마스킹본이 `body` 에 반영된다(위 L146-165).
- L171: `ctx.data_class` 로 규칙을 고른다. 2차부터 이 값은 서버 판정값 — `public` 같은 하향 신고는 `data:declare` 권한이 있어야 유지되고, 없으면 기본값으로 올라간다(`app/services/policy.py:42-53`). 단 `data:declare` 는 `user`·`security`·`finops` 역할과 `gateway:invoke` 스코프에 **기본 부여**되어 있어(`app/core/auth.py:20`·`:29`·`:32`·`:37`) 관리자가 RBAC 에서 회수하기 전까지는 1차의 "자기 신고" 상태가 그대로다(Epic 40 의 의도된 결정 — `test/impl/40th/scenario.md:9`).
- L172-173: 규칙이 없으면 재시도 1회(= 후보당 2번 시도). `candidates` 는 원래 후보 사본.
- L174-178: BFS 형태의 큐 — `seen` 으로 재라우팅 순환(A→B→A)을 막는다. `deploy` 는 함수 안 지연 import(모듈 로드 순서·순환 회피), `airgap` 은 5초 캐시(`app/services/deploy.py:41-45`).
- L188-189: 정책 판정 입력 — 대상 모델·주체·데이터 등급·호출 에이전트·모델 분류. 기본 정책 "내부·기밀 데이터는 로컬 모델만"(`app/seed.py:63-67`)이 `category:commercial` + `data_class in (internal, confidential)` 를 deny 하고 `domain-local` 로 재라우팅을 지정한다.
- L190-197: 거부면 사유 기록, 재라우팅 대상이 있으면 **큐 맨 앞**에 넣는다 — 재라우팅 대상도 다음 반복에서 정책·공급자·에어갭 검사를 다시 받는다(좋은 설계: 재라우팅이 검사를 건너뛰지 않음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L188-197 정책 판정의 `require_approval` 을 보지 않는다. 모델 대상 정책도 `approval_required=True` 로 저장할 수 있고(`app/api/gateway_admin.py:31`·`:37` — target_type 제한 없음), 판정이 이를 돌려주지만(`app/services/policy.py:187-188`), prepare 는 `dec.allow` 만 보고 그대로 허용한다 — 운영자가 건 "사람 승인 필요" 가 모델 호출에서는 조용히 무시된다(도구 경로는 `app/services/governance.py:151` 에서 사용). 최소 수정: 모델·에이전트 대상 정책의 `approval_required=True` 는 API 에서 422 로 거부하거나, prepare 에서 `require_approval` 이면 거부하고 노트를 남긴다.

```python path=app/services/llm.py lines=198-234
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
- L198-201: 공급자·모델 비활성 또는 mTLS 정책 위반이면 제외.
- L202-206: 공급자 `options.data_classes`(콘솔 공급자 마법사가 상용 공급자에 `['public']` 을 넣는다 — `web/src/pages/models/ModelsMore.tsx:91`·`:99`)에 요청 등급이 없으면 제외. 정책보다 좁히기만 한다. `db.get` 은 같은 세션의 identity map 에서 꺼내므로 L133 의 조회를 다시 쓰지 않는다.
- L207-210: 에어갭 모드면 상용(공급자 종류 openai·anthropic·gemini 또는 분류 commercial, `app/services/deploy.py:125-126`) 제외.
- L212-220: 후보가 없을 때 — 거부 사유가 하나라도 있으면 403(감사·메트릭·경고 로그), 아니면 404. 감사는 **전 후보가 막혔을 때만** 남는다(재라우팅 성공은 `RoutingDecision.reason` 에 노트로 남음, L376).
- L221: 입력 토큰 추정 = 텍스트 글자 수 / 4(`app/services/providers.py:42-43`). 마스킹 후 본문 기준.
- L222-225: **첫 후보**만 기준으로 한도·예산 검사. 차단이면 429 + `Retry-After`.
- L226-232: `on_exceed=fallback` 한도가 걸리면 폴백 모델을 맨 앞에 넣는다.
**짚어볼 점 / Watch-outs**
- [위험:높음] L226-232 **한도 폴백 모델이 데이터 등급 정책·공급자 허용 등급·에어갭 검사를 모두 건너뛴다.** 폴백 모델은 `_target`(활성·mTLS)만 확인하고 곧바로 `plan.targets` **맨 앞**에 들어간다 — L188-210 의 `policy.evaluate`·`options.data_classes`·`deploy.is_commercial` 이 적용되지 않는다. 한도의 `fallback_model` 은 아무 모델 id 나 저장된다(`app/api/gateway_admin.py:435`·`:455-456` — 존재·등급 검증 없음). 시나리오: finops 담당자(`finops` 역할은 `limits:write` 는 있고 `policy:write` 는 없다 — `app/core/auth.py:32`)가 "팀 월 예산 초과 시 저렴한 `gpt-4o-mini` 로 폴백" 을 설정(목적은 비용 절감, 흔한 구성). 예산을 넘긴 뒤 그 팀이 `X-AgentNexus-Data-Class: confidential` 로 요청하면 기본 정책이 상용 모델을 거부하고 `domain-local` 로 재라우팅(L190-196) → `plan.targets=[domain-local]` → L222 예산 초과 → L226-230 이 `gpt-4o-mini` 를 맨 앞에 넣음 → **기밀 데이터가 상용 공급자로 전송**된다. 에어갭 모드(ST-09)에서도 같은 경로로 상용 모델이 후보 1순위가 된다. 결과적으로 security 역할의 2인 검토 정책(Epic 39 "정책 2인 검토")을 limits 권한만으로 우회할 수 있다. 시험(`test/test_llm.py:170-187`, `test/test_gateway_policy.py:107-137`)은 폴백 대상이 로컬 `domain-local` 인 경우만 다룬다. 확신: 확인됨(코드 경로). 최소 수정: 루프 본문(L184-211)을 `_admit(db, ctx, model, gap) -> tuple[Target | None, str | None]` 로 빼서 폴백 모델에도 그대로 적용하고, 거부되면 폴백 대신 429(원래 차단 사유)로 처리:
  ```python
  if lim.fallback_model:
      fb = db.get(Model, lim.fallback_model)
      tgt, why = _admit(db, ctx, fb, gap) if fb else (None, "미등록 모델")
      if tgt is None:  # 폴백도 정책·등급·에어갭을 통과해야 한다 — 아니면 원래 한도 초과로 차단
          raise ApiError(429, "rate_limited", f"{lim.reason} (폴백 불가: {why})", headers={"Retry-After": "3600"})
      ...
  ```
  더해 한도 저장 시 `fallback_model` 존재 검증. 시험 공백: "상용 폴백 + confidential → 상용으로 가지 않음" 시험 필요.
- [위험:중간] L202-206 **모델 단위 허용 데이터 등급 `Model.data_classes` 가 강제되지 않는다.** 모델 행에 "허용 데이터 분류" 컬럼이 있고(`app/models.py:611`), API 가 저장하며(`app/api/llm.py:259`·`:321`), 콘솔 모델 목록이 "허용 데이터 등급" 배지로 보여 주고(`web/src/pages/models/Models.tsx:40`), 모델 등록 마법사는 상용 모델에 `['public']` 만 넣는다(`web/src/pages/models/ModelsMore.tsx:226`·`:292`). 그러나 prepare 는 공급자 `options.data_classes` 만 확인하고 `model.data_classes` 는 읽지 않는다(`grep data_classes app/` — 이 파일의 L202 와 표시용 L411 뿐). 시나리오: API 로 등록한 공급자(options 에 `data_classes` 없음)에 모델을 `data_classes=["public"]`·`category="general"` 로 등록하면, 콘솔은 "public 만 허용" 으로 보여 주지만 confidential 요청도 그 모델로 간다(기본 정책은 `category:commercial` 만 막음). 최소 수정: L206 뒤에 `if model.data_classes and ctx.data_class not in model.data_classes: notes·denied_reason; continue` — 공급자 검사와 같은 형태.
- [위험:중간] L221-225 **한도·예산은 예약 없는 사전 검사라 초과가 구조적으로 가능하다.** (a) 추정치 `est` 는 입력 텍스트만 — 이미지·`max_tokens`(출력)는 빠진다. (b) 토큰 창은 검사 때 읽기만 하고(`app/services/ratelimit.py:261-263` `window_get`) 실제 가산은 호출이 끝난 뒤 finalize(L393 → `ratelimit.py:295-301`)에서 한다. (c) 예산은 `UsageRecord` 합계를 **워커별 5초 캐시**로 본다(`ratelimit.py:224-238`) — UsageRecord 는 호출 후 커밋된다. (d) 첫 후보만 검사(1차 지적, 미해결) — execute 가 다음 후보로 폴백하면 그 모델 전용 한도는 검사되지 않는다. 시나리오: 팀 월 예산 $1,000, 현재 $999. 에이전트가 장문 요청 50건을 동시에 보내면(건당 약 $2) 모두 "used < budget" 으로 통과해 $1,099 가 된다. 토큰 분당 한도도 동시 N 건이 같은 `used` 를 읽어 N 배까지 넘는다. 최소 수정: 검사 시 `est_in + (max_tokens 또는 기본 출력 상한)` 을 창에 **예약 가산**(`window_add`)하고 finalize 에서 실제와의 차이를 보정, 예산은 진행 중 예약액을 Valkey 카운터로 합산(또는 캐시를 끄고 예약액 포함), 폴백 대상마다 `check`. 이 문제는 아래 execute_stream 의 교차 문제(스트림 중도 종료 시 기록 누락)와 합쳐지면 예산 통제가 사실상 무력화된다.
- [위험:낮음] L222 라우팅 장애 주입 시험 API(`app/api/ops.py:557-565`, 주석 "호출하지 않음")도 prepare 를 그대로 호출해 rps 버킷을 차감하고(`ratelimit.py:254-258` "요청 버킷은 이 시점에 차감된다"), 거부 시 `gateway.model.deny` 감사(L214)와 예산 알림(`ratelimit.py:273-276`)을 남긴다 — 시뮬레이션이 실제 한도·감사 기록에 흔적을 남긴다. 최소 수정: `prepare(..., simulate=True)` 이면 `ratelimit.check`·감사를 건너뛰고 결과만.

```python path=app/services/llm.py lines=235-271

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
- L236-237: 화이트리스트 필드만 복사 — 이 시점의 `messages` 는 이미 마스킹본.
- L240-258: 후보마다 `retries+1` 회 시도. 각 시도를 OTel CLIENT 스팬(GenAI 시맨틱 속성, L360-363)으로 감싸고, 성공이면 서킷 리셋·EWMA·시도 기록·응답 `model` 을 게이트웨이 별칭으로 덮어씀(업스트림 모델명 은닉)·토큰 속성 기록 후 즉시 반환. `return` 이 `with span(...)` 안에 있어도 컨텍스트 관리자의 `__exit__` 가 먼저 실행되어 스팬은 닫힌다.
- L259-269: `ProviderError` 면 실패 기록·스팬 오류·경고 로그. 재시도 불가(4xx 대부분, `app/services/providers.py:57` — 408·409·425·429·5xx 만 재시도 가능)면 `break` 로 **다음 후보**로 간다.
- L270: 모두 실패하면 502 + 시도 목록(`extra.attempts`) — 호출부가 이 목록으로 실패 결정 로그를 남긴다(`app/api/llm.py:72-74`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L259 `ProviderError` 만 잡는다(1차 "어댑터가 다 감싸는지 범위 밖" → 2차 확인: **감싸지 않는 경로가 있다**). `OpenAICompatAdapter.chat` 의 `return res.json()`(`app/services/providers.py:116`)과 스트림의 `json.loads(data)`(`:131`)는 `httpx.HTTPError` 감싸기 바깥이라, 업스트림(또는 앞단 프록시)이 200 과 HTML 오류 페이지를 주면 `JSONDecodeError`(ValueError)가 그대로 올라온다 → 다음 후보로 폴백하지 않고, 서킷에도 안 남고, 호출부(`app/api/llm.py:70-74` 는 `ApiError` 만 잡음)에서 finalize 없이 500. L254-255 `int(u.get(...))` 도 usage 값이 `null` 이면 `TypeError` — 업스트림 비용은 이미 발생했는데 기록이 없다. 최소 수정: `except ProviderError` 뒤에 `except (ValueError, TypeError, KeyError) as exc: exc = ProviderError(f"malformed upstream response: {type(exc).__name__}", 502)` 로 변환해 같은 경로로 처리, usage 는 `int(u.get("prompt_tokens") or 0)`.
- [위험:낮음] L244-248 재시도 사이 대기(backoff)가 없다(1차 지적, 미해결) — 429·503 에 즉시 재시도하고 `Retry-After` 도 보지 않는다. 최소 수정: `await asyncio.sleep(min(2**n * 0.2, 2) + random.random() * 0.1)`, 429 면 `Retry-After` 존중.
- L270 은 502 인데 콘솔 안내는 "마지막 대상까지 실패하면 503 (MSG-015)"(`web/src/pages/gateway/GatewayMore.tsx:1057`)이다 — 문서·UI 문구 정합성 문제(동작 결함은 아님).

```python path=app/services/llm.py lines=272-298
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


```
**분석 / Analysis (Level 2)**
- L272-296: 채팅과 같은 후보·재시도 골격. `{**_genai_attrs(...), "gen_ai.operation.name": "embeddings"}` 는 dict 병합에서 뒤 키가 이긴다는 성질로 연산 이름만 덮어쓴다. 임베딩 미지원 어댑터는 `ProviderError(400, retryable=False)`(`app/services/providers.py:74-76`)를 던져 `break` 로 다음 후보로 간다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L290 4xx(429 제외)를 **성공으로** 표시해 실패 카운트를 지운다 — 채팅(L261)은 모든 `ProviderError` 를 실패로 센다(1차 지적, 미해결). 같은 모델을 채팅·임베딩이 함께 쓰면 임베딩 4xx 가 채팅의 서킷 상태를 리셋한다. 최소 수정: 두 경로가 같은 판정 함수(`_is_failure(exc)`)를 쓰도록.
- 채팅과 달리 실패 경고 로그(`logs.event`)가 없다 — 운영 로그에서 임베딩 장애가 덜 보인다.

```python path=app/services/llm.py lines=299-330
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
- L302: 입력 문자열을 user 메시지로 감싸 `prepare` 를 재사용 — 입력 가드·데이터 등급 라우팅·한도를 채팅과 같은 코드로 적용한다.
- L304-314: 동기 DB 작업을 함수로 묶어 `run_in_threadpool` 로 실행(이벤트 루프 비차단). 세션은 `with` 로 닫힌다. 임베딩 능력(`capabilities`)이 있는 모델만 남긴다.
- L315: 가드가 제자리 교체한 마스킹본을 다시 읽어 임베딩 — 마스킹 토큰(`[MASKED:email]`)이 벡터에 들어가 검색 정확도는 떨어지지만 PII 가 공급자·색인에 남지 않는다(의도된 교환).
- L318-327: 실패(ApiError)여도 결정 로그를 남기고, 성공이면 입력 토큰만 사용량으로 기록.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L306-309 한도 검사(L222)는 임베딩 지원 여부로 거르기 **전** 첫 후보 기준이다. 첫 후보가 채팅 전용이면 다른 모델의 한도로 검사·rps 차감이 되고, 한도 폴백(L226-232)이 넣은 모델이 임베딩 미지원이면 L309 에서 다시 빠져 폴백이 조용히 무효가 된다. 최소 수정: prepare 에 `capability="embeddings"` 인자를 두어 후보 단계에서 거른 뒤 한도 검사.
- L321-325 `ApiError` 외 예외(위 L259 의 `JSONDecodeError` 등)는 결정 로그 없이 전파된다.

```python path=app/services/llm.py lines=331-358
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

```
**분석 / Analysis (Level 2)**
- L331-332: 이 함수는 비동기 제너레이터다 — 호출해도 본문이 실행되지 않고, 소비자가 `async for` 로 당길 때마다 다음 `yield` 까지 진행한다.
- L338: `tgt.adapter.stream(...)` 도 비동기 제너레이터를 **만들기만** 한다(HTTP 연결은 L341 첫 `__anext__()` 에서 열림 — `app/services/providers.py:118-134` 의 `async with async_client(...)`·`http.stream(...)`).
- L339-348: 첫 청크를 기다리는 동안의 실패(`ProviderError`, 빈 스트림 `StopAsyncIteration`)만 다음 시도·후보로 폴백 — 첫 청크가 사용자에게 나간 뒤에는 바꿀 수 없다는 정확한 경계. 스팬은 첫 청크(TTFT)까지만 덮는다.
- L349-356: TTFT 를 지연 EWMA 에 반영, `start`(대상·시도) → 첫 청크 → 나머지 청크 중계 → `return` 으로 제너레이터 정상 종료.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L338·L354 `agen` 을 명시적으로 닫지 않는다(1차 지적, 미해결). 소비자가 중간에 멈추면(클라이언트 연결 종료) 이 제너레이터는 `yield` 지점에서 버려지거나 `aclose()`/취소를 받는데, 안쪽 `agen` 의 `async with`(업스트림 HTTP 연결)는 취소 경로에서는 곧바로, 버려지는 경로에서는 GC 의 비동기 제너레이터 finalizer 가 돌 때에야 닫힌다 — 그 사이 업스트림은 계속 토큰을 생성한다. 최소 수정: L339-356 을 `try: ... finally: await agen.aclose()` 로 감싸기.
- [위험:낮음] L342 첫 청크 전 `ProviderError`·`StopAsyncIteration` 외의 예외(위 L259 의 `JSONDecodeError` 등)는 폴백 없이 전파된다. 첫 청크 **후** 중간 실패(`ProviderError`)는 그대로 소비자로 전파되는데 `_mark(False)` 가 없어 서킷이 모른다.
- (교차 — 결함 위치는 `app/api/llm.py`, 등급은 그 보고서·최종 보고 FINDINGS 에서) 이 제너레이터의 소비자 `_sse`(`app/api/llm.py:91-120`)는 사용량·결정 로그 기록(`_final`, `:120`)을 `try` 블록 **뒤**에 두었다. 클라이언트가 `[DONE]` 전에 연결을 끊으면 제너레이터에 `GeneratorExit` 또는 `CancelledError`(둘 다 `except ApiError`/`except ProviderError` 에 잡히지 않음)가 들어와 `_final` 이 실행되지 않는다 → `RoutingDecision`·`UsageRecord`·토큰 창(`record_tokens`) 기록이 **전부 빠진다**. 클라이언트가 마지막 내용 청크를 받고 즉시 끊으면 비용은 발생했는데 예산·토큰 한도에 쌓이지 않아 위 L221-225 의 통제를 반복 우회할 수 있다(Python 제너레이터 의미론상 확인됨, 시험 `test/test_llm.py:104-129` 는 끝까지 읽는 경우만). 또 출력 PII 마스킹이 델타 1개 단위(`app/api/llm.py:104-107`·`:123-125`)라 실제 공급자처럼 하위 단어 토큰으로 흘러오면 이메일·전화번호가 여러 델타에 나뉘어 정규식(`app/services/guards.py:30-35`)에 걸리지 않는다(시험의 mock 은 단어 단위로 흘려 `app/services/providers.py:386-388` 이 문제를 가린다).

```python path=app/services/llm.py lines=359-370

def _genai_attrs(plan: Plan, tgt: Target, attempt: int) -> dict:
    return {"gen_ai.operation.name": "chat", "gen_ai.system": tgt.provider_id, "gen_ai.request.model": plan.requested,
            "gen_ai.response.model": tgt.model_id, "anx.upstream_model": tgt.upstream, "anx.attempt": attempt + 1,
            "anx.data_class": plan.data_class}


def cost_of(tgt: Target, tokens_in: int, tokens_out: int) -> Decimal:
    c = (Decimal(tokens_in) * tgt.price_in + Decimal(tokens_out) * tgt.price_out) / MILLION
    return c.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

```
**분석 / Analysis (Level 2)**
- L360-363: OpenTelemetry GenAI 시맨틱 컨벤션 속성 + 제품 고유 `anx.*`. `gen_ai.system` 에 공급자 **id**(예: 운영자가 붙인 이름)를 넣는다 — 컨벤션은 `openai`·`anthropic` 같은 공급자 종류를 기대하므로 표준 대시보드와는 값이 맞지 않을 수 있다(관측 품질 문제, 결함 아님). 프롬프트·응답 본문은 속성에 넣지 않는다(데이터 최소화 측면에서 좋음).
- L366-368: `Decimal` 로 토큰×단가/1M, 소수 6자리 반올림(`ROUND_HALF_UP` — 회계에서 흔히 쓰는 사사오입). 아주 작은 호출은 0.000001 단위로 올림·내림된다.

```python path=app/services/llm.py lines=371-407
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
- L373-376: trace id(요청 컨텍스트 변수 — `run_in_threadpool` 은 contextvars 를 스레드로 복사하므로 스레드풀에서도 같은 값), 폴백 여부(한도 폴백·재라우팅·첫 후보와 다른 모델), 사유 문자열.
- L377-381: 라우팅 결정 로그 — 실패(tgt None)여도 남긴다. 사유는 2000자로 자른다(DB 컬럼 보호).
- L383-392: 성공 대상이 있으면 사용량 행 — 토큰·비용·지연·정책 노트·가드 발견 유형. 프롬프트·응답 본문은 저장하지 않는다(데이터 최소화).
- L393: 토큰 분당 한도 창에 실제 토큰 가산(prod 는 Valkey, 커밋 전에 실행되는 비트랜잭션 쓰기 — 커밋이 실패해도 한도 쪽은 더해진 채로 남아 보수적 방향).
- L394-400: Prometheus 카운터·현재 스팬 속성(`float` 변환은 메트릭 전용이라 정밀도 문제 없음).
- L405: 결정 로그와 사용량을 **한 번에 커밋** — 둘이 어긋나지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L384-385 `usage` 가 없으면 0 토큰·0 비용으로 기록된다. 비스트리밍 경로는 업스트림 `res.get("usage")` 를 그대로 넘기고(`app/api/llm.py:79`) 추정하지 않는다 — 스트리밍 경로만 글자 수/4 로 추정한다(`app/api/llm.py:117-119`). usage 를 돌려주지 않는 OpenAI 호환 서버(일부 vLLM·Ollama·프록시 설정)를 공급자로 쓰면 예산·토큰 한도가 전혀 쌓이지 않는다. 최소 수정: usage 누락 시 입력은 `estimate_tokens`, 출력은 응답 텍스트 길이로 추정하고 `policy_decisions` 에 `"usage_estimated": true` 표시.
- [위험:낮음] L393 토큰 창 가산은 **실제 사용 모델** 기준인데 검사(L222)는 첫 후보 기준이다 — 실행 폴백 시 "검사한 한도" 와 "가산한 한도" 가 다를 수 있다(위 L221-225 (d) 와 같은 뿌리).

```python path=app/services/llm.py lines=408-426

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
- L409-413 `model_out`: 모델 API·카탈로그 메타데이터 표현. `data_classes` 를 노출하지만 위 L202-206 처럼 실행 경로에서는 강제되지 않는 값이다.
- L416-426 `sync_model_catalog`: 모델을 통합 카탈로그(type=model) 항목으로 동기화 — 활성이면 active, 아니면 approved. 커밋하지 않는다(호출부 `app/api/llm.py:299-301` 가 모델 행과 함께 커밋 — 같은 트랜잭션으로 묶는 올바른 패턴).
- 같은 모델을 두 요청이 동시에 등록하면 둘 다 `item is None` 을 볼 수 있지만, 모델 id 중복이 먼저 409 로 막힌다(`app/api/llm.py:295-296`) — 실질 위험 없음.

## 파일 평가 / File Assessment
- 강점: (1) 후보 단계의 fail-closed 판정 — 정책 거부 시 재라우팅 대상도 정책·공급자 등급·에어갭·mTLS 를 다시 거친다(L179-211). (2) 2차(Epic 40)부터 데이터 등급이 서버 판정값으로 들어와 하향 신고가 `data:declare` 권한·감사로 통제된다(`app/api/llm.py:29-34`). (3) 스트리밍 폴백을 첫 청크 전으로 한정한 정확한 경계, Decimal 비용, 결정 로그·사용량 단일 커밋, 프롬프트 본문을 사용량·스팬에 남기지 않는 데이터 최소화.
- 위험: (1) [위험:높음] 한도 폴백 모델이 데이터 등급 정책·공급자 허용 등급·에어갭을 건너뜀 — `app/services/llm.py:226-232`. (2) [위험:중간] 예약 없는 한도·예산 검사(동시성·출력 토큰·첫 후보만) — `app/services/llm.py:221-225` (+ 교차: 스트림 중도 종료 시 사용량 미기록 `app/api/llm.py:91-120`). (3) [위험:중간] 규칙 `timeout_ms` 미적용·전체 마감 없음 — `app/services/llm.py:137`·`:244-248`. (4) [위험:중간] `Model.data_classes` 미강제 — `app/services/llm.py:202-206`.
- 개선 권고(영향순): (1) 후보 판정 루프를 `_admit()` 로 추출해 한도 폴백에도 적용(+ 상용 폴백 시험). (2) 한도·예산 예약 가산과 finalize 보정, 스트림 소비자 `_final` 을 `finally` 로(호출부). (3) `Plan.timeout_s` + `asyncio.wait_for` + 전체 마감. (4) `model.data_classes` 강제. (5) 비 ProviderError 응답 결함을 ProviderError 로 변환, `agen.aclose()`, 백오프, 서킷이 실제로 호출을 건너뛰게.
- 파일 위험 등급 / Risk grade: 높음 — 미해결 [위험:높음] 1건(L226-232 한도 폴백의 데이터 등급 정책 우회), 데이터 분류 경계(FR-G03)의 핵심 모듈에 [위험:중간] 3건.
- 재읽기 권고 레벨: 3 — prepare 의 판정 순서(가드 → 규칙 → 정책·재라우팅 → 공급자 등급 → 에어갭 → 한도 → 폴백)와 호출부(`app/api/llm.py`)의 기록 시점을 한 그림으로 다시 볼 필요가 있다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| [낮음·버그] L124-127 ab 가중치 검증 부재 → 모든 요청 500 | 해결(API 경로) — 방어 코드는 없음 | `app/api/llm.py:344-352` · `test/test_review_fixes.py:453-461`; `resolve` 는 그대로 `float()`·`random.choices` (L123-126), 구성 복원 `app/api/admin.py:313-316` 은 미검증 |
| L119 latency 미측정 모델 0.0 → 항상 맨 앞 | 미해결 | L118 |
| L117 단가 합 정렬 근사 | 미해결(수용) | L114-116 |
| [낮음] L150-151 assistant 메시지 미검사 | 미해결 | L149-150 |
| [낮음·버그] L162-163 멀티모달 마스킹 시 이미지 파트 손실 | 부분 해결 | L161-164 이미지 유지 · `test/test_review_fixes.py:465-473`; 여러 텍스트 파트는 합쳐지고 순서 변경 |
| L137 후보마다 시크릿 복호화 | 미해결(수용) | L136 |
| L187 `ctx.data_class` 하류 헤더 자기 신고 | 부분 해결 | `app/services/policy.py:42-53`·`:56-73`, `app/api/llm.py:29-34`, `test/test_caller_trust.py:72`·`:108` — 단 `data:declare` 가 기본 역할에 부여(`app/core/auth.py:20`·`:32`·`:37`) |
| L221 한도는 첫 후보 기준만 | 미해결 | L222 (+ 본 보고서 L221-225 [중간]) |
| L201·L206 `db.get(Provider)` 반복 | 미해결(identity map 으로 무해) | L202·L207 |
| L258 `ProviderError` 만 잡음(어댑터 확인 범위 밖) | 미해결 — 2차 확인으로 실제 누출 경로 있음 | L259 · `app/services/providers.py:116`·`:131` |
| L243 재시도 백오프 없음 | 미해결 | L244-248 |
| L251 응답 model 을 별칭으로 | 유지(의도) | L252 |
| [낮음·불일치] L289 임베딩 4xx 를 성공 표시 | 미해결 | L290 |
| L314 마스킹본 임베딩 | 유지(의도) | L315 |
| [낮음·자원] L337 `agen` `aclose()` 없음 | 미해결 | L338·L354 |
| 서킷 half-open 단순화·워커별 상태 | 미해결(의도) | L36-54 |
| L101-103 `default_data_class` 가 설정을 직접 조회 | 변경(위임) | L101-102 → `app/services/policy.py:25-35` (호출처 없음) |
