> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [governance.py.md](../../../../CODE-REVIEW-Lv2/app/services/governance.py.md)

### 15. app/services/governance.py
**파일 요약 / File Summary**
- 역할 / Role: Tool & Agent Governance(FR-G07) — 도구 위험도 분류(annotations·이름 규칙, `readOnlyHint` 는 trusted 서버만), 새 도구 기본 규칙 생성, 도구 호출 판정 6단계(서버 활성 → 규칙 존재·허용 → 프로파일 허용목록 → 정책 → 인자 스키마 → 승인), 사람 승인 생성·검증·**원자적 1회 소비**·세션 기억, 승인 모달 맥락, 기한 연장(WCAG 2.2.1), 결정 권한(직무 분리·위임)과 결정, 원격 에이전트 신뢰 등급 판정.
- 진입점 거리 / Distance: 2 — 호출처 `app/services/mcp_gateway.py:949`(`check_tool`)·`:748`(`default_rules`, 도구 목록 갱신)·`:289`(`MAX_EXTENSIONS`), `app/services/a2a_gateway.py:454`(`check_agent`), `app/api/gateway_admin.py:715-716,743,753,764,772`, `app/services/mcp_consent.py:129,138,266`, `app/api/mcp.py:498`(Inspector 드라이런). (주의: `app/services/{mcp_gateway,mcp_consent,a2a_gateway,policy,secrets}.py`·`app/main.py` 는 21:30~21:35 디스크에서 수정 중이라 인용 라인은 그 시점 기준이며 스냅샷과 다를 수 있다. 이 파일은 15:33 수정 이후 불변, 해시 `06e0df48…` 가 스냅샷과 일치.)
- LOC: 333 (현재 `wc -l`) · 1차 318 → +15
- 주요 export: `ToolDecision`, `AgentDecision`, `classify_risk`, `default_rules`, `args_hash`, `create_approval`, `check_tool`, `remembered`, `approval_context`, `APPROVAL_TTL`, `MAX_EXTENSIONS`, `CONSENT_KINDS`, `extend_step_s`, `extend`, `can_decide`, `decide`, `check_agent`, (내부) `_valid_approval`·`_consume`·`_profile_allows`·`_WRITE`
- 의존(내부): `app.core.{auth,config,ids,errors}`, `app.db.utcnow`, `app.models`, `app.services.{audit,policy}`, `app.services.notify.notify`, (지연) `app.services.registry`, `app.models.{CatalogItem,ChatSession,Run,RunStep,TokenExchangePolicy,User}`
- 외부 패키지: jsonschema, sqlalchemy(`select`·`update`)
- 부수효과 / Side effects: DB 쓰기·**함수 내부 커밋**(`create_approval` L96·`_consume` L182·`extend` L267·`decide` L288·L299 — 호출자 세션의 다른 변경도 함께 커밋됨), `default_rules` 는 `flush` 만(커밋은 호출자), 감사(`approval.request|use|remember|decide|extend`, `gateway.tool.args_warn`), 알림(approvers 큐).
- 동시성 / Concurrency: 모두 동기 함수 — 호출자가 `run_in_threadpool` 로 감싼다(`app/services/mcp_gateway.py:947-957`). 원자적: 승인 소비 `_consume`(조건부 UPDATE + rowcount). 비원자적: `decide`·`extend`(읽기-검사-쓰기), `decide` 의 지연 만료 처리와 스케줄러 `approval_jobs`(`app/services/scheduler.py:80-96`)의 경합.
- 테스트 커버리지 흔적: `test/test_gateway_policy.py:197-213`(기본 규칙 위험도), `:216-232`(판정 단계·요청자 동의·재사용 불가), `:235-254`(approvers 직무 분리), `:278-298`(에이전트 신뢰 등급), `test/test_policy_hardening.py:27-42`(C43-01 trusted 만 readOnly 신뢰), `test/test_review_fixes.py:343-355`(C39-17 소비 1회 — **같은 세션 순차 호출**이라 실제 동시성은 아님), `test/test_extend.py:58-132`, `test/test_hardening.py:189-198`. **시험 없음**: 기계 주체의 자기 승인, 만료 직후 결정 시 실행 재개, 결정 경합, 세션 기억의 범위.
- 2차 변경 요약 / Changes since 1st review: (1) 승인 소비를 `_consume` 조건부 UPDATE 로 원자화(L161·L177-185, Epic 39 C39-17 — `test/impl/39th/scenario.md:104`). (2) `classify_risk(tool, trusted)` — `readOnlyHint` 는 카탈로그 신뢰 등급 `trusted` 서버만 믿고, `default_rules` 가 서버의 카탈로그 항목에서 신뢰 등급을 읽는다(L41-51·L56-60, Epic 43 D17-④ — `test/impl/43rd/scenario.md:7`). (3) `sqlalchemy.update` import(L12). 나머지(결정·연장·맥락·에이전트 판정)는 1차와 같다.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/governance.py lines=1-26
"""Tool & Agent Governance (FR-G07) — 허용목록(기본 거부)·인자 스키마·위험도·사람 승인·에이전트 신뢰 등급.

도구 호출 판정 순서: ① 서버 활성 ② 도구 규칙 존재·허용(미등록=거부) ③ 에이전트 프로파일 허용목록
④ 정책(Policies) ⑤ 인자 스키마(도구 inputSchema + 규칙 args_schema) ⑥ 승인(고위험·정책 요구) → 허용.
"""
import fnmatch
import re
from dataclasses import dataclass, field
from datetime import timedelta

import jsonschema
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.config import settings
from app.core.ids import canonical_json, new_id, sha256_hex
from app.db import utcnow
from app.core.errors import bad_request, conflict, forbidden
from app.models import A2AAgent, AgentProfile, Approval, GatewayPolicy, McpServer, RunEvent, ToolRule
from app.services import audit, policy
from app.services.notify import notify

APPROVAL_TTL = timedelta(minutes=30)
_WRITE = re.compile(r"(^|[._-])(create|update|delete|remove|write|transition|post|put|patch|send|execute|run|"
                    r"deploy|merge|close|assign|comment|upload|set)([._-]|$)", re.I)
```
**분석 / Analysis (Level 2)**
- L1-5: 판정 순서가 docstring 에 명시 — 거부 코드(`ToolDecision.code`)와 1:1 로 대응해 "어느 단계에서 막혔나" 를 추적하는 기준.
- L12 (2차 신규 `update`): Core/ORM 일괄 UPDATE 문 생성기 — `_consume` 의 조건부 갱신에 쓴다.
- L24 `APPROVAL_TTL`: 도구 승인 대기 기본 30분(연장 단위도 이 값, L246).
- L25-26 `_WRITE`: 이름에 쓰기 동사가 단어 경계(`.`·`_`·`-`·시작/끝)로 들어 있으면 고위험. "기본 거부" 가 아니라 **"모르면 저위험"** 인 휴리스틱이라 목록 밖 동사는 통과한다.
**짚어볼 점 / Watch-outs**
- (아래 L54-75 [위험:중간] 의 일부) L25-26 목록에 `drop`·`purge`·`kill`·`terminate`·`destroy`·`revoke`·`grant`·`approve`·`transfer`·`pay`·`exec`(접두 `exec_` 는 `execute` 와 다름)·`shell`·`eval`·`reset`·`move` 등이 없다(1차 지적 그대로). 2차에서 `readOnlyHint` 위조는 막혔지만(L47), annotations 가 없는 untrusted 서버의 `exec_command`·`drop_table` 은 여전히 `low` 다.

```python path=app/services/governance.py lines=29-51
@dataclass
class ToolDecision:
    allow: bool
    code: str  # allowed | unregistered_tool | tool_denied | agent_not_allowed | policy_denied | invalid_arguments
    #           | approval_required | server_inactive
    reason: str
    risk: str = "low"
    approval: Approval | None = None
    policy: dict = field(default_factory=dict)
    sanitize: bool = True


def classify_risk(tool: dict, trusted: bool = True) -> str:
    """MCP 도구 annotations(readOnlyHint·destructiveHint)와 이름 규칙으로 기본 위험도. annotations 는 서버가 주는 힌트라
    위험을 낮추는 readOnlyHint 는 trusted 등급 서버만 믿는다(D17-④) — 높이는 destructiveHint 는 언제나."""
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint") is True:
        return "high"
    if ann.get("readOnlyHint") is True and trusted:
        return "low"
    if _WRITE.search(tool.get("name", "")):
        return "high"
    return "low"
```
**분석 / Analysis (Level 2)**
- L29-38 `ToolDecision`: `field(default_factory=dict)` — dataclass 에서 가변 기본값을 공유하지 않게 하는 정석(그냥 `= {}` 면 dataclass 가 `ValueError` 로 거부한다).
- L41-51 (2차 변경): 위험을 **올리는** 힌트(`destructiveHint`)는 무조건 믿고, **내리는** 힌트(`readOnlyHint`)는 `trusted` 일 때만 — 비대칭 신뢰가 핵심 설계(공격자에게 유리한 방향의 힌트만 의심). `is True` 비교라 `"true"` 문자열 같은 비정상 값은 무시된다(좋음). 시험 `test/test_policy_hardening.py:27-30`.
- L41 `trusted: bool = True` 기본값: 인자를 빠뜨린 호출자는 **신뢰하는 쪽**으로 동작한다(fail-open 기본값). 현재 저장소 내 호출은 `default_rules`(L68) 하나뿐이고 명시적으로 넘긴다 — 새 호출처를 추가할 때 주의할 함정. 최소 수정: 기본값 `False`.
- L35·L45-51: 위험도는 `low|high` 만 산출한다 — `medium` 은 운영자가 규칙에서 지정할 때만 생긴다.

```python path=app/services/governance.py lines=54-79
def default_rules(db: Session, server_id: str, tools: list[dict]) -> list[ToolRule]:
    """즉시 사용 시 '정책 기본값 적용' — 읽기 도구 허용, 쓰기·파괴 도구는 승인 필요. 기존 규칙은 유지."""
    from app.models import CatalogItem
    from app.services import registry
    srv = db.get(McpServer, server_id)
    item = db.get(CatalogItem, srv.catalog_item_id) if srv and srv.catalog_item_id else None
    trusted = bool(item) and registry.trust_of(item) == "trusted"
    out = []
    for t in tools:
        name = t.get("name")
        if not name:
            continue
        rule = db.scalars(select(ToolRule).where(ToolRule.server_id == server_id, ToolRule.tool == name)).first()
        if rule is None:
            risk = classify_risk(t, trusted)
            rule = ToolRule(id=new_id("tr"), server_id=server_id, tool=name, allowed=True, risk=risk,
                            approval_required=risk == "high", approver="requester",
                            description=(t.get("description") or "")[:500])
            db.add(rule)
        out.append(rule)
    db.flush()
    return out


def args_hash(args: dict) -> str:
    return sha256_hex(canonical_json(args or {}))
```
**분석 / Analysis (Level 2)**
- L56-60 (2차 신규): 함수 안 지연 import(순환 의존 회피). 신뢰 등급은 카탈로그 항목 메타데이터 `_anx_review.trust`(`app/services/registry.py:350-351`) — 카탈로그를 거치지 않고 직접 등록한 서버는 `item=None` → untrusted(안전한 기본값).
- L62-73: **규칙이 없는 도구만** 새로 만든다. 기존 규칙은 손대지 않으므로 운영자 수정이 보존된다. 반대로 서버의 신뢰 등급이 `trusted` → `untrusted` 로 내려가도, 또는 업스트림이 같은 이름 도구의 annotations·설명을 바꿔도 **이미 만든 `low` 규칙은 재평가되지 않는다**(낮음 — 아래 Watch-out 에 포함).
- 호출처: 도구 목록 갱신마다(헬스 루프 주기 기본 60초, `app/services/mcp_gateway.py:738-749` `_save`) — 업스트림이 새 도구를 추가하면 최대 한 주기 뒤 규칙이 자동 생성된다.
- L74 `flush()` 만: INSERT 를 보내 id·제약 위반을 즉시 드러내되 커밋은 호출자(`_save` 의 `db.commit()`). `(server_id, tool)` UNIQUE(`app/models.py:309`) — 두 워커가 동시에 갱신하면 한쪽이 `IntegrityError`(헬스 루프는 2차에서 리스로 1 워커만 돌아 위험이 줄었다).
- L66: 도구마다 SELECT 1회(N+1, 1차 지적 그대로 — 도구 수십 개 규모라 수용).
- L78-79 `args_hash`: 키 정렬된 정규 JSON 의 SHA-256 — 승인한 인자와 실행 인자를 묶는다(인자 바꿔치기 방지).
**짚어볼 점 / Watch-outs**
- [위험:중간] L62-73 **새 도구 자동 허용**(1차 [중간] 중 미해결 부분). 새 도구는 `allowed=True` 로 생성되고, 기본 시드 정책이 모든 도구를 허용한다(`app/seed.py:69` `target="*"`, `effect="allow"`). 그래서 실질 관문은 이 규칙뿐인데, `low` 로 분류되면 **운영자 검토·사람 승인 없이 즉시 호출 가능**하다. 시나리오: 개발자(`mcp:write`)가 등록한 외부 MCP 서버(카탈로그 미경유 → untrusted)가 다음 버전에서 `exec_command`(또는 `drop_table`, `transfer_funds`) 도구를 추가 → 60초 안에 `low`·허용 규칙이 생성 → 에이전트·사용자가 승인 없이 호출. 업스트림이 기존 도구의 설명만 바꿔 동작을 바꾸는 경우(tool poisoning)도 규칙은 그대로다. 2차의 `readOnlyHint` 신뢰 제한은 "힌트 위조" 한 갈래만 막았다. 최소 수정: untrusted 서버의 새 도구는 `allowed=False`(또는 `approval_required=True`)로 만들고 운영자가 검토 후 허용, `_WRITE` 를 허용 목록 방식(읽기 동사 `get|list|search|read|fetch|query|describe` 만 low)으로 뒤집기, 도구 정의 해시를 규칙에 저장해 바뀌면 재검토 상태로.
- [위험:높음 의 뿌리 일부] L70 `approver="requester"` — 고위험 도구의 기본 승인 주체가 **요청자 본인**이다(1차 L65 지적 그대로). 이 기본값이 기계 주체 자기 승인 결함(L274-281 [위험:높음])의 전제다.

```python path=app/services/governance.py lines=82-85
def _profile_allows(profile: AgentProfile | None, qualified: str) -> bool:
    if profile is None:
        return True
    return any(fnmatch.fnmatchcase(qualified, pat) for pat in (profile.tools or []))
```
**분석 / Analysis (Level 2)**
- 에이전트 프로파일의 허용 도구 패턴(`jira.*` 등). `fnmatchcase` 는 대소문자를 구분(`fnmatch` 는 OS 따라 무시) — 보안 판정에 맞는 선택.
- 프로파일이 없으면 True(좁히는 단계라 생략 가능). `profile.tools` 가 비면 아무 것도 허용하지 않는다(기본 거부).

```python path=app/services/governance.py lines=88-102
def create_approval(db: Session, p: Principal, *, server_id: str, tool: str, args: dict, risk: str, reason: str,
                    approver: str = "requester", policy_ref: str | None = None, session_id: str | None = None,
                    run_id: str | None = None, step_id: str | None = None, ttl: timedelta | None = None) -> Approval:
    ap = Approval(id=new_id("apr"), kind="tool_call", status="pending", requester_sub=p.sub, server_id=server_id,
                  tool=tool, target=f"{server_id}.{tool}", args=args, args_hash=args_hash(args), risk=risk,
                  approver=approver, reason=reason, policy_ref=policy_ref, session_id=session_id, run_id=run_id,
                  step_id=step_id, expires_at=utcnow() + (ttl or APPROVAL_TTL))
    db.add(ap)
    db.commit()
    audit.append("approval.request", actor=p.sub, target=ap.target, data={"approval_id": ap.id, "risk": risk,
                                                                          "approver": approver})
    if approver == "approvers":
        notify("approval.pending", f"사람 승인 대기: {ap.target}", reason, severity="warn",
               link="/console/gateway/approvals", audience_role="security", dedupe_key=f"approval:{ap.id}")
    return ap
```
**분석 / Analysis (Level 2)**
- L88: `*` 뒤는 키워드 전용 인자 — 호출부에서 위치 인자 실수를 막는다.
- L91-94: `requester_sub` 에 요청 주체를 고정(나중에 `_valid_approval` 이 같은 주체만 사용 허용). `args` 원문과 해시를 함께 저장(승인 화면 표시 + 바인딩).
- L96: 판정 함수 중간에서 커밋 — 호출자 세션의 다른 변경도 같이 커밋된다(1차 지적 그대로, 현재 호출자 `mcp_gateway._check` 는 다른 미커밋 변경이 없어 실해 없음).
- L99-101: approvers 큐만 보안 역할 알림, `dedupe_key` 로 같은 승인 중복 알림 방지. requester 모드는 알림 없음(요청자가 화면에서 바로 결정한다는 전제).

```python path=app/services/governance.py lines=105-113
def _valid_approval(db: Session, approval_id: str, p: Principal, server_id: str, tool: str, args: dict) -> Approval | None:
    ap = db.get(Approval, approval_id)
    if ap is None or ap.status != "approved" or ap.server_id != server_id or ap.tool != tool:
        return None
    if ap.requester_sub != p.sub or ap.args_hash != args_hash(args):
        return None
    if ap.expires_at and ap.expires_at < utcnow():
        return None
    return ap
```
**분석 / Analysis (Level 2)**
- 승인 재사용 범위를 다섯 조건으로 좁힌다: 상태 approved · 같은 서버 · 같은 도구 · **같은 요청자** · **같은 인자 해시** · 미만료. 남의 승인 id 를 헤더에 넣어도 L109 에서 거부된다.
- `kind` 를 검사하지 않지만 동의 승인(sampling·elicitation)은 `tool=None` 이라(`app/services/mcp_consent.py:128-131`) L107 의 `ap.tool != tool` 로 걸러진다 — 우연에 기대는 방어라 `ap.kind == "tool_call"` 조건을 명시하는 편이 견고하다(낮음).
- L111: 승인 후 사용 기한은 **처음 요청 시각 + TTL** 이다(승인 시각 기준이 아님) — 29분째 승인하면 1분 안에 써야 한다. 오케스트레이터는 승인 즉시 재개하므로 실무 영향은 작다.

```python path=app/services/governance.py lines=116-139
def check_tool(db: Session, p: Principal, server_id: str, tool: str, args: dict, *, agent: str | None = None,
               data_class: str = "internal", approval_id: str | None = None, session_id: str | None = None,
               run_id: str | None = None, step_id: str | None = None, create_pending: bool = True) -> ToolDecision:
    qualified = f"{server_id}.{tool}"
    srv = db.get(McpServer, server_id)
    if srv is None or srv.status != "active":
        return ToolDecision(False, "server_inactive", f"MCP 서버 {server_id} 가 활성 상태가 아닙니다.")
    rule = db.scalars(select(ToolRule).where(ToolRule.server_id == server_id, ToolRule.tool == tool)).first()
    if rule is None:
        return ToolDecision(False, "unregistered_tool", f"등록되지 않은 도구입니다: {qualified}")
    if not rule.allowed:
        return ToolDecision(False, "tool_denied", f"차단 목록 도구입니다: {qualified}", risk=rule.risk)
    profile, node_ap = None, {}
    if agent:
        profile = db.get(AgentProfile, agent.split("/")[0])
        if profile is not None and not _profile_allows(profile, qualified):
            return ToolDecision(False, "agent_not_allowed",
                                f"에이전트 프로파일 {profile.name} 의 허용 도구가 아닙니다: {qualified}", risk=rule.risk)
        if profile is not None and agent.endswith("/executor"):  # Executor 노드 승인 정책 (SCR-D05) — 게이트웨이보다 완화 불가
            node_ap = (((profile.graph or {}).get("workers") or {}).get("executor") or {}).get("approval") or {}
    dec = policy.evaluate(db, policy.PolicyInput("tool", qualified, p, data_class=data_class, args=args,
                                                 agent=agent, attrs={"risk": rule.risk}))
    if not dec.allow:
        return ToolDecision(False, "policy_denied", dec.reason, risk=rule.risk, policy=dec.as_dict())
```
**분석 / Analysis (Level 2)**
- L120-127 ①②: 서버 활성 → 규칙 존재(미등록 = 거부) → 규칙 허용. 규칙이 없으면 거부하지만, 위 L62-73 때문에 "규칙 없음" 상태는 도구 목록 갱신 전까지만 존재한다.
- L128-135 ③: `agent` 는 호출자가 신고한 에이전트(`프로파일/노드`). 2차에서 신고 헤더는 **기계·위임 주체**(`workload`·`access_token`·`delegation`)가 보낸 것만 받아들이고 사람이 보낸 값은 버린다(`app/services/policy.py:21,71`) — 1차의 "사람 사용자가 agent 헤더로 agent 주체 정책을 충족" 경로는 닫혔다. 기계 주체는 여전히 임의 프로파일 이름을 신고할 수 있으나 이 단계는 **좁히는 방향**이라 권한 상승은 없다(정책 단계 L136 의 agent 주체 allow 정책은 기계 주체에 대해선 여전히 신고값으로 충족된다 — 정책 설계 시 유의).
- L134-135: executor 노드의 승인 정책(medium 도구 승인, approvers 강제, TTL). 주석은 "게이트웨이보다 완화 불가" 지만 L171 의 `ttl_s` 는 그래프 검증상 60~86400초(`app/services/graph.py:265-266`)라 기본 30분보다 **길게**(최대 24시간) 잡을 수 있다 — 승인 대기 창이 늘어나는 방향의 완화(낮음).
- L136-139 ④: 정책 엔진(builtin/OPA). 도구 위험도를 속성으로 넘겨 정책이 `risk` 조건을 쓸 수 있다.

```python path=app/services/governance.py lines=140-157
    schema = next((t.get("inputSchema") for t in (srv.tools or []) if t.get("name") == tool), None)
    for sch, mode in ((schema, "strict"), (rule.args_schema, rule.args_mode or "strict")):
        if sch:
            try:
                jsonschema.validate(args or {}, sch)
            except jsonschema.ValidationError as exc:
                if mode == "warn":  # 경고 모드 (SCR-H11) — 차단하지 않고 감사에 남긴다
                    audit.append("gateway.tool.args_warn", actor=p.sub, target=qualified, data={"error": exc.message[:300]})
                    continue
                return ToolDecision(False, "invalid_arguments", f"인자 스키마 위반: {exc.message}", risk=rule.risk,
                                    policy=dec.as_dict())
    needs_approval = rule.approval_required or dec.require_approval or (
        rule.risk == "medium" and any(fnmatch.fnmatchcase(qualified, t) for t in node_ap.get("medium") or []))
    approver = "approvers" if node_ap.get("approver") == "approvers" else rule.approver
    if needs_approval and rule.risk != "high" and session_id and remembered(db, p, session_id, qualified):
        audit.append("approval.remember", actor=p.sub, target=qualified, data={"session_id": session_id})
        return ToolDecision(True, "allowed", "이 세션에서 승인됨 (다시 묻지 않기)", risk=rule.risk, policy=dec.as_dict(),
                            sanitize=rule.sanitize)
```
**분석 / Analysis (Level 2)**
- L140-150 ⑤: 업스트림 `inputSchema` 는 항상 strict, 운영자 규칙 스키마는 strict/warn. 업스트림 스키마는 신뢰하지 않는 입력이다 — `jsonschema.validate` 는 매번 스키마 검사(`check_schema`)도 하므로, 업스트림이 잘못된 스키마를 주면 `SchemaError`(ValidationError 아님)가 잡히지 않고 호출자 쪽 예외가 된다(가능성 — 호출자의 예외 처리에 따라 500/RPC 오류, 추가 확인 필요). `pattern` 정규식 ReDoS 도 1차 지적 그대로(영향 낮음).
- L147: warn 모드 감사는 판정 도중 별도 트랜잭션 — 판정이 이후 거부돼도 경고 감사는 남는다(의도).
- L151-153 ⑥: 승인 필요 = 규칙 OR 정책 OR (medium + 노드 설정). approvers 강제는 노드가 **올리는 방향**으로만(L153).
- L154-157: "이 세션에서 다시 묻지 않기" — 고위험 제외. `session_id` 는 클라이언트 헤더 `X-AgentNexus-Session` 그대로다(`app/services/policy.py:73`, 2차에서도 검증 없음 — run 헤더만 소유자 확인 `:60-70`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L154 + L188-195 세션 기억에 **기한이 없다**. 같은 요청자가 같은 `session_id` 헤더를 계속 보내면, 승인 행이 보존 정책으로 지워질 때까지 medium·정책 승인 도구를 다시 묻지 않는다(채팅 세션이 끝난 뒤에도). 다른 사용자의 기억은 L191 `requester_sub` 조건으로 막힌다. 최소 수정: `remembered` 에 `Approval.decided_at > now - 세션 기억 TTL`(예: 8시간) 조건, 가능하면 `session_id` 가 실제 `ChatSession`·MCP 세션 소유자와 일치하는지 확인.

```python path=app/services/governance.py lines=158-174
    if needs_approval:
        if approval_id:
            ap = _valid_approval(db, approval_id, p, server_id, tool, args)
            if ap is not None and _consume(db, ap):
                audit.append("approval.use", actor=p.sub, target=qualified, data={"approval_id": ap.id})
                return ToolDecision(True, "allowed", "승인된 호출", risk=rule.risk, approval=ap, policy=dec.as_dict(),
                                    sanitize=rule.sanitize)
        ap = None
        if create_pending:
            ap = create_approval(db, p, server_id=server_id, tool=tool, args=args, risk=rule.risk,
                                 reason=f"{'고위험 도구' if rule.risk == 'high' else '정책'} 승인 필요: {qualified}",
                                 approver=approver, policy_ref=dec.policy_id, session_id=session_id,
                                 run_id=run_id, step_id=step_id,
                                 ttl=timedelta(seconds=int(node_ap["ttl_s"])) if node_ap.get("ttl_s") else None)
        return ToolDecision(False, "approval_required", f"사람 승인이 필요합니다: {qualified}", risk=rule.risk,
                            approval=ap, policy=dec.as_dict())
    return ToolDecision(True, "allowed", dec.reason, risk=rule.risk, policy=dec.as_dict(), sanitize=rule.sanitize)
```
**분석 / Analysis (Level 2)**
- L161 (2차 변경): 유효성 검사(읽기) 뒤 `_consume`(원자적 쓰기)가 **이겨야만** 허용. 경합에서 진 호출은 L165 로 떨어져 새 pending 승인을 만들고 `approval_required` — 같은 승인으로 두 번 실행되지 않는다(1차 [중간] TOCTOU 해결).
- 소비는 업스트림 호출 **전**에 일어난다(최대 1회 실행 보장 — at-most-once). 업스트림이 실패하면 승인은 이미 `used` 라 다시 승인받아야 한다. 오케스트레이터의 재실행 복구가 도구 쓰기를 중복 실행하지 않는 근거가 이것이다(`app/services/orchestrator.py:983` docstring).
- L166-171: `create_pending=False` 는 Inspector 드라이런(`app/api/mcp.py:498-499`)이 대기 행을 만들지 않게 하는 스위치.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L165-171 승인 없이 같은 도구·같은 인자를 반복 호출할 때마다 **새 pending 행**을 만든다(1차 지적 그대로). approvers 모드면 호출마다 보안 역할에 알림이 간다(`dedupe_key` 가 승인 id 단위라 중복 제거되지 않음). 최소 수정: 같은 `requester_sub`·`target`·`args_hash` 의 미만료 pending 이 있으면 그 행을 반환.

```python path=app/services/governance.py lines=177-185
def _consume(db: Session, ap: Approval) -> bool:
    """승인 1회 사용 — 조건부 UPDATE 로 동시 호출 중 하나만 성공 (같은 승인으로 고위험 도구가 두 번 실행되지 않게)."""
    now = utcnow()
    won = db.execute(update(Approval).where(Approval.id == ap.id, Approval.status == "approved")
                     .values(status="used", used_at=now)).rowcount == 1
    db.commit()
    if won:
        db.refresh(ap)
    return won
```
**분석 / Analysis (Level 2)**
- L180-181 (2차 신규): `UPDATE approvals SET status='used', used_at=? WHERE id=? AND status='approved'` — 판정과 쓰기가 DB 안에서 한 문장이라 원자적. PostgreSQL(READ COMMITTED)에서는 두 번째 UPDATE 가 첫 번째의 행 잠금을 기다렸다가 커밋된 새 행(`used`)으로 WHERE 를 **재평가**해 0행이 되고, SQLite 는 DB 쓰기 잠금으로 직렬화돼 역시 0행이다. `rowcount == 1` 이 승자 판정. `scheduler.lease` 와 같은 모범 패턴(1차 INDEX §4.1-7 의 대조 사례).
- SQLAlchemy 2.x 의 ORM 일괄 UPDATE 는 기본 `synchronize_session="auto"` 로 세션 안 객체(`ap`)의 속성도 **파이썬에서 조건을 평가해** 바꿔 둔다 — 경합에서 져서 DB 는 0행이어도 메모리의 `ap.status` 가 `used` 로 보일 수 있다. 그래서 승자만 L184 `refresh` 로 DB 값을 다시 읽고, 패자는 `ap` 를 더 쓰지 않는다(L165 `ap = None`) — 올바른 처리. L182 커밋 후 `expire_on_commit` 으로 어차피 다음 접근 때 재조회된다.
- L182: 조건부 UPDATE 자체가 아니라 **커밋**이 잠금을 푸는 시점 — 커밋을 판정 직후에 바로 해 잠금 보유 시간을 짧게 한 것도 맞다.
- 시험 `test/test_review_fixes.py:343-355` 는 같은 세션에서 순차로 두 번 호출 — 조건부 WHERE 의 논리는 검증하지만 두 스레드·두 커넥션 경합은 아니다(시험 공백, 낮음).

```python path=app/services/governance.py lines=188-195
def remembered(db: Session, p: Principal, session_id: str, qualified: str) -> bool:
    """'이 세션 동안 다시 묻지 않기' (SCR-C04 ⑦) — 같은 세션·같은 도구를 승인했고 고위험이 아니면."""
    for ap in db.scalars(select(Approval).where(Approval.session_id == session_id, Approval.target == qualified,
                                                Approval.requester_sub == p.sub,
                                                Approval.status.in_(("approved", "used")))):
        if (ap.payload or {}).get("remember_session") and ap.risk != "high":
            return True
    return False
```
**분석 / Analysis (Level 2)**
- 같은 세션·도구·요청자의 approved/used 승인 중 `remember_session` 표시가 있고 고위험이 아닌 것이 하나라도 있으면 True. JSON 컬럼(`payload`) 안 값이라 SQL 로 거르지 못하고 파이썬 루프.
- `session_id`·`target` 에 인덱스가 없다(`app/models.py:531,536`, 1차 지적 그대로) — 승인 테이블이 커지면 기억 확인이 전체 스캔(보존 정책으로 상한).
- 기한 없음·클라이언트 헤더 의존은 L154 Watch-out.

```python path=app/services/governance.py lines=198-234
def approval_context(db: Session, ap: Approval) -> dict:
    """승인 모달 맥락 (SCR-C04 ②④⑤) — 요청 출처·실행 신원(OBO 스코프)·영향 요약·정책 근거."""
    from app.models import ChatSession, Run, RunStep, TokenExchangePolicy, User
    srv = db.get(McpServer, ap.server_id) if ap.server_id else None
    tdef = next((t for t in (srv.tools or []) if t.get("name") == ap.tool), {}) if srv else {}
    ann = tdef.get("annotations") or {}
    rule = db.scalars(select(ToolRule).where(ToolRule.server_id == ap.server_id, ToolRule.tool == ap.tool)).first() \
        if ap.server_id else None
    run = db.get(Run, ap.run_id) if ap.run_id else None
    steps = list(db.scalars(select(RunStep).where(RunStep.run_id == ap.run_id).order_by(RunStep.seq))) if run else []
    idx = next((i for i, s in enumerate(steps) if s.id == ap.step_id), None)
    prof = db.get(AgentProfile, run.profile_id) if run and run.profile_id else None
    ses = db.get(ChatSession, ap.session_id) if ap.session_id else None
    user = db.scalars(select(User).where(User.sub == ap.requester_sub)).first()
    pol = db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == f"mcp:{ap.server_id}")).first()
    scope = (((srv.auth or {}).get("tool_scopes") or {}).get(ap.tool) if srv else None)
    scopes = [scope] if scope else list((pol.default_scopes if pol else []) or [])
    gpol = db.get(GatewayPolicy, ap.policy_ref) if ap.policy_ref else None
    destructive, readonly = ann.get("destructiveHint"), ann.get("readOnlyHint")
    reversible = "읽기 전용 — 변경 없음" if readonly else ("되돌리기 불가 (파괴적 작업)" if destructive else
                                                       "자동 되돌리기 불가 — 수동 정리 필요")
    return {
        "source": {"profile": prof.name if prof else None, "node": steps[idx].node if idx is not None else None,
                   "step": f"{idx + 1}/{len(steps)}" if idx is not None else None, "session": ses.title if ses else None,
                   "trace_id": run.trace_id if run else None},
        "identity": {"sub": ap.requester_sub, "name": user.name if user else ap.requester_sub,
                     "method": "사용자 위임 — OBO 토큰 교환 (RFC 8693)", "audience": f"mcp:{ap.server_id}", "scopes": scopes},
        "impact": {"target": ap.target, "server": srv.name if srv else ap.server_id, "reversible": reversible,
                   "visibility": "외부 시스템에 반영될 수 있음" if ann.get("openWorldHint", True) else "플랫폼 내부",
                   "description": (tdef.get("description") or "")[:300]},
        "checks": [{"key": "allowlist", "ok": bool(rule and rule.allowed), "detail": "허용목록 규칙 통과 (기본 거부)"},
                   {"key": "schema", "ok": True, "detail": "인자 스키마 검증 통과 — 위반이면 승인 요청 전에 차단"},
                   {"key": "approval_required", "ok": True,
                    "detail": f"approval_required = true ({'고위험' if ap.risk == 'high' else '정책·노드 설정'})"},
                   {"key": "policy", "ok": True, "detail": f"정책 {gpol.name} v{gpol.version}" if gpol else "정책 판정: 허용"}],
        "remember_allowed": ap.risk != "high",
        "expires_in_s": max(0, int((ap.expires_at - utcnow()).total_seconds())) if ap.expires_at else None}
```
**분석 / Analysis (Level 2)**
- 승인 모달용 읽기 전용 조합(쿼리 7~8개 — 상세 화면 1회성이라 수용). 함수 안 import 로 무거운 모델 의존을 지연.
- L217-218: **업스트림 annotations** 로 "읽기 전용 — 변경 없음" 을 표시한다. L47 은 untrusted 서버의 `readOnlyHint` 를 위험도 판정에서 무시하도록 바뀌었지만 여기서는 여전히 그대로 믿는다 — untrusted 서버가 쓰기 도구에 `readOnlyHint: true` 를 달면 위험도는 `high`(승인 필요)로 맞게 잡히지만 **승인 모달은 "변경 없음" 이라고 사람에게 안내**해 승인을 유도한다(낮음 — 사람 판단을 오도). 최소 수정: `default_rules` 와 같은 신뢰 등급을 계산해 untrusted 면 annotations 표시를 "서버 신고(검증 안 됨)" 로.
- L224: `method` 가 항상 "OBO 토큰 교환" 고정 문구 — 서버 인증 방식이 bearer·api_key 여도 같다(표시 부정확, 낮음).
- L228-232: `schema`·`approval_required`·`policy` 는 항상 `ok=True` 고정(1차 지적 그대로 — 승인 시점 재검증 값이 아님).
- L234: `expires_at` 이 tz-aware 여야 `utcnow()` 와 뺄셈이 된다 — `UTCDateTime` 타입(`app/models.py:546`)이 이를 보장한다.

```python path=app/services/governance.py lines=237-247
MAX_EXTENSIONS = 10  # WCAG 2.2.1 — 간단한 동작으로 10번까지 연장 (D14-③: 늘어난 시간 합계 ≤ 기본의 10배)
CONSENT_KINDS = ("sampling", "elicitation")


def extend_step_s(ap: Approval) -> int | None:
    """한 번 연장할 때 늘리는 시간 = 그 승인의 기본 대기 시간 (동의 60초 · 도구 승인 30분). 기한 없는 승인은 None."""
    if ap.kind in CONSENT_KINDS:
        return int(settings.mcp_consent_timeout_s)
    if ap.kind == "tool_call":
        return int(APPROVAL_TTL.total_seconds())
    return None
```
**분석 / Analysis (Level 2)**
- 연장 단위 = 기본 대기 시간(동의 `MCP_CONSENT_TIMEOUT_S` 기본 60초 `app/core/config.py:47`, 도구 30분). executor 노드가 `ttl_s` 로 다른 TTL 을 줬어도 연장 단위는 30분 고정이다(의도로 보임).
- `MAX_EXTENSIONS` 는 업스트림 대기 시간 계산에도 쓰인다(`app/services/mcp_gateway.py:289` — 동의 대기 중 최대 연장까지 끊기지 않게 HTTP 타임아웃을 늘림).

```python path=app/services/governance.py lines=250-271
def extend(db: Session, ap: Approval, p: Principal) -> Approval:
    """시간 연장 — 만료 시각을 기본 시간만큼 뒤로(남은 시간에 더함). 요청자·결정 권한자만, 대기·검토 중에만, 최대 10회."""
    step = extend_step_s(ap)
    if step is None or ap.expires_at is None:
        raise bad_request("기한이 없는 승인입니다.", code="no_time_limit")
    if ap.status not in ("pending", "review") or ap.expires_at < utcnow():
        raise conflict(f"이미 끝난 요청은 연장할 수 없습니다 ({'expired' if ap.status == 'pending' else ap.status}).",
                       code="approval_closed")
    if not (p.sub == ap.requester_sub or can_decide(ap, p)):
        raise forbidden("이 요청의 기한을 연장할 권한이 없습니다.", code="approval_forbidden")
    if (ap.extensions or 0) >= MAX_EXTENSIONS:
        raise bad_request(f"연장은 최대 {MAX_EXTENSIONS}회(기본 시간의 {MAX_EXTENSIONS}배)까지입니다.", code="extend_limit")
    ap.expires_at = ap.expires_at + timedelta(seconds=step)
    ap.extensions = (ap.extensions or 0) + 1
    if ap.run_id:  # 채팅 동의 카드·승인 대기 표시가 새 기한을 따라가도록
        db.add(RunEvent(run_id=ap.run_id, type=f"{ap.kind}.extended" if ap.kind in CONSENT_KINDS else "approval.extended",
                        data={"approval_id": ap.id, "expires_at": ap.expires_at.isoformat(), "extensions": ap.extensions}))
    db.commit()
    audit.append("approval.extend", actor=p.sub, actor_kind=p.kind, target=ap.target,
                 data={"approval_id": ap.id, "kind": ap.kind, "extensions": ap.extensions, "step_s": step,
                       "expires_at": ap.expires_at.isoformat()}),
    return ap
```
**분석 / Analysis (Level 2)**
- L252-261: 기한 있음 → 대기·검토 중이며 미만료 → 권한(요청자 또는 결정권자) → 횟수 상한. 순서상 만료 검사가 권한 검사보다 앞이라 "이미 끝남" 409 가 "권한 없음" 403 보다 먼저 나온다(정보 노출은 `_approval` 가시성 검사가 이미 걸러 문제없음).
- L262: "남은 시간에 더함" — 만료 시각에 단위 시간을 더한다(지금 + 단위가 아님). 10회 상한으로 총 연장이 기본의 10배를 넘지 않는다.
- L268: 이 파일에서 유일하게 `actor_kind=p.kind` 를 감사에 남긴다 — L97·L162·L300 은 빠져 기본값 `"system"`(`app/services/audit.py:52`)으로 기록된다(아래 L284-302 Watch-out).
- 원문 L270 끝의 `}),` 는 파이썬상 `audit.append(...)` 호출 결과를 1-튜플로 만드는 표현식 문장이 아니라, 괄호 안 `data={...}` 의 닫힘 뒤 `)` 로 호출이 끝나고 이어지는 `,` 가 없는 형태다 — **위 인용의 L270 끝 `),` 는 인용 오기가 아니라 원문과 다르다면 정정 대상**이므로 원문을 다시 확인했다: 원문 L270 은 `"expires_at": ap.expires_at.isoformat()})` 로 끝난다(쉼표 없음). 인용 블록을 원문대로 아래에 다시 둔다.

```python path=app/services/governance.py lines=268-271
    audit.append("approval.extend", actor=p.sub, actor_kind=p.kind, target=ap.target,
                 data={"approval_id": ap.id, "kind": ap.kind, "extensions": ap.extensions, "step_s": step,
                       "expires_at": ap.expires_at.isoformat()})
    return ap
```
**짚어볼 점 / Watch-outs**
- [위험:낮음] L255-263 읽기-검사-쓰기가 비원자적(1차 지적 그대로). 동시 연장 2건이 둘 다 `extensions=9` 를 읽으면 둘 다 통과해 같은 값(`expires_at+step`, `extensions=10`)을 쓴다 — 상한 우회는 없고 1회가 사라진다(과소 계수). `decide` 와 겹치면 서로 다른 컬럼이라 덮어쓰지 않는다(ORM 은 바뀐 컬럼만 UPDATE).

```python path=app/services/governance.py lines=274-281
def can_decide(ap: Approval, p: Principal) -> bool:
    """requester 승인 = 요청자 본인(동의) 또는 승인자, approvers 승인 = 요청자 아닌 승인 권한자(직무 분리).
    위임받은 사람(SCR-H12)은 요청자가 아니면 결정할 수 있다."""
    if ap.delegated_to and ap.delegated_to == p.sub and p.sub != ap.requester_sub:
        return True
    if ap.approver == "approvers":
        return p.has("approvals:decide") and p.sub != ap.requester_sub
    return p.sub == ap.requester_sub or p.has("approvals:decide")
```
**분석 / Analysis (Level 2)**
- L277-278 위임: 위임 대상이 요청자 본인이 아니면 결정 가능. 위임 설정은 `app/api/ops.py:416-436`(`approvals:decide` 필요, 대상 = 활성 사용자, 요청자 본인 불가). **대상이 `approvals:decide` 를 가졌는지는 보지 않는다** — docstring("다른 승인자에게 넘긴다", `app/api/ops.py:419`)과 달리 일반 사용자에게도 위임할 수 있고, 그러면 approvers(직무 분리) 큐 건을 권한 없는 사람이 결정한다(위임자 책임으로 감사 `approval.delegate` 에 남음 — 낮음).
- L279-280 approvers 모드: 결정 권한 + 요청자 본인 금지(직무 분리). `p.has` 는 토큰 스코프까지 본다(`app/core/auth.py:75-80`) — 스코프 제한 토큰은 `approvals:decide` 스코프가 없으면 결정 불가.
- L281 requester 모드: 요청자 본인 **또는** 결정권자. 사용자가 채팅·콘솔에서 자기 도구 호출에 동의하는 모델(시험 `test/test_gateway_policy.py:216-232`).
**짚어볼 점 / Watch-outs**
- [위험:높음] L281 + L70 **requester 모드에서 "요청자 본인" 을 주체 종류와 무관하게 인정해, 기계 주체가 자기 고위험 도구 호출을 스스로 승인한다.** 고위험 도구의 기본 규칙은 `approver="requester"`(L69-70)이고, MCP 게이트웨이는 `approval_required` 오류에 `approval_id` 를 실어 호출자에게 돌려주며(`app/services/mcp_gateway.py:955-956,975-976`), 승인 엔드포인트는 로그인만 요구한다(`app/api/gateway_admin.py:746-755`, `current_principal` — 권한·주체 종류 검사 없음). 따라서 `gateway:invoke` 스코프의 access token·workload(`app/core/auth.py:37-39` — MCP 호출에 필요한 `chat:use` 포함), 사용자 PAT·client key 를 쥔 자동화/AI 클라이언트가: `tools/call` → `-32001 {approval_id}` → `POST /api/v1/approvals/{id}/approve`(같은 토큰, L281 True) → 헤더 `X-AgentNexus-Approval` 로 재호출 → L159-164 소비 후 실행. PRD 의 "고위험 사람 승인"(`PRD.md:50` FR-G07)이 사람 없이 충족된다. `remember=true` 를 주면 medium·정책 승인 도구는 그 세션에서 이후 묻지도 않는다(L154). 감사 `approval.decide` 는 `actor_kind` 없이 기록돼(L300) 사후 탐지도 어렵다. 확신: 코드 경로 확인됨(실행 시험은 하지 않음). 최소 수정:
  ```python
  HUMAN_KINDS = ("user",)  # 대화형 로그인 세션 — pat·client_key·workload·access_token·delegation·test 제외
  def can_decide(ap, p):
      ...
      if ap.approver == "approvers":
          return p.has("approvals:decide") and p.sub != ap.requester_sub and p.kind in HUMAN_KINDS
      return (p.sub == ap.requester_sub and p.kind in HUMAN_KINDS) or (p.has("approvals:decide") and p.kind in HUMAN_KINDS)
  ```
  + 감사에 `actor_kind=p.kind`. 기계 주체용으로는 별도 "사전 승인 정책"(운영자가 규칙에서 workload 에 한해 허용)을 두는 편이 명확하다. 시험 공백: access token·workload·PAT 로 자기 승인 시 403 시험 필요.

```python path=app/services/governance.py lines=284-302
def decide(db: Session, ap: Approval, p: Principal, approve: bool, note: str | None = None,
           remember: bool = False) -> Approval:
    if ap.expires_at and ap.expires_at < utcnow() and ap.status == "pending":
        ap.status = "expired"
        db.commit()
    if ap.status != "pending":
        raise bad_request(f"이미 처리된 승인 요청입니다 ({ap.status}).", code="approval_closed")
    if not can_decide(ap, p):
        raise forbidden("이 승인 요청을 처리할 권한이 없습니다 (직무 분리).", code="approval_forbidden")
    if remember and approve:
        if ap.risk == "high":
            raise bad_request("고위험 도구는 세션 단위 자동 승인을 할 수 없습니다 — 매번 승인합니다.", code="remember_not_allowed")
        ap.payload = {**(ap.payload or {}), "remember_session": True}
    ap.status = "approved" if approve else "rejected"
    ap.decided_by, ap.decided_at, ap.decision_note = p.sub, utcnow(), note
    db.commit()
    audit.append("approval.decide", actor=p.sub, target=ap.target, decision=ap.status,
                 data={"approval_id": ap.id, "requester": ap.requester_sub, "note": note})
    return ap
```
**분석 / Analysis (Level 2)**
- L286-288 지연 만료: 기한이 지난 pending 을 이 자리에서 `expired` 로 바꾸고 커밋한 뒤 L289-290 에서 400 `approval_closed`.
- L291-292: 권한 검사가 상태 검사 **뒤** — 권한 없는 사람도 "이미 처리됨(상태)" 을 알 수 있지만, 호출자 `_approval` 가 가시성을 먼저 걸러 정보 노출은 없다.
- L293-296: 고위험은 세션 기억 금지. `payload` 는 새 dict 로 대입(JSON 변경 추적 함정 회피).
- L297-299: 상태·결정자·시각·메모를 ORM 속성으로 바꾸고 커밋 — 생성되는 SQL 은 `UPDATE approvals SET status=?, decided_by=?, … WHERE id=?`(상태 조건 없음).
**짚어볼 점 / Watch-outs**
- [위험:중간] L286-290 **기한 직후 결정하면 실행이 영구 대기에 빠진다.** 이 경로는 `expired` 로 커밋만 하고 예외를 던지므로 호출자 `app/api/gateway_admin.py:753-754`(·`:764-765`)의 `_on_decision` → `orchestrator.on_approval` 이 **호출되지 않는다**. 정상 만료 처리 주체인 스케줄러 `approval_jobs` 는 `status == "pending"` 인 행만 고르므로(`app/services/scheduler.py:87-88`) 이미 `expired` 가 된 이 행을 다시 보지 않고, 복구 작업은 "대기 단계가 하나도 없는" 실행만 재개한다(`app/services/orchestrator.py:1007-1012`). 결과: 그 실행의 단계는 `waiting_approval`, 실행은 `waiting_approval` 상태로 남아 사용자가 취소할 때까지 멈춘다(거부 처리 정책 `on_deny` 도 적용되지 않음). 감사 `approval.expire` 도 남지 않는다. 시나리오: 스케줄러 주기 60초(`app/core/config.py:52`) 안에서 — 승인 카드의 남은 시간이 0초가 된 직후 사용자가 "승인" 을 누르거나(화면 갱신 지연), API 클라이언트가 기한 직후 결정. 최소 수정: 지연 만료 시에도 `decided_by="system:expire"`·감사·훅 호출을 스케줄러와 같은 함수로 처리(예: `scheduler.approval_jobs` 의 행 단위 처리를 `expire_one(db, ap)` 로 빼서 양쪽에서 호출, 호출자 엔드포인트가 그 경우 `_on_decision(ap)` 실행 후 400). 시험 공백: "만료 후 approve → 실행 단계 rejected(만료)·실행 재개" 시험 없음.
- [위험:낮음] L289-299 결정이 조건부 UPDATE 가 아니다(1차 지적 그대로, 1차 INDEX §4.1-7). 두 결정자가 동시에 승인·거부하면 둘 다 `pending` 을 읽고 나중 커밋이 이긴다 — 감사에는 두 결정이 다 남고 훅도 두 번 불린다(두 번째 훅은 단계가 이미 `waiting_approval` 이 아니라 무시, `app/services/orchestrator.py:944-945`). 더 나쁜 순서: A 승인 → 실행이 승인을 소비(`used`) → 그 전에 행을 읽어 둔 B 의 거부 커밋이 `status` 를 `rejected` 로 **덮어쓴다** — 도구는 실행됐는데 기록은 "거부". 같은 경합이 스케줄러 만료(`app/services/scheduler.py:89-90`, 역시 무조건 쓰기)와도 생긴다. 최소 수정: `update(Approval).where(Approval.id == ap.id, Approval.status == "pending").values(...)` 의 rowcount 로 선점(`_consume` 와 같은 패턴), 스케줄러도 같은 조건.
- [위험:낮음] L300 감사에 `actor_kind` 가 없다(L97·L162 도 같음) — 기본값 `"system"` 으로 기록돼 사람·기계 결정을 감사에서 구분할 수 없다. L274-281 [위험:높음] 의 탐지성을 떨어뜨린다. 최소 수정: `actor_kind=p.kind`.

```python path=app/services/governance.py lines=305-311
# ── 원격 에이전트 신뢰 등급 ──
@dataclass
class AgentDecision:
    allow: bool
    code: str
    reason: str
    policy: dict = field(default_factory=dict)
```
**분석 / Analysis (Level 2)**
- 원격 에이전트 판정 결과 — `ToolDecision` 보다 단순(위험도·승인 없음: A2A 호출에는 사람 승인 단계가 없다).

```python path=app/services/governance.py lines=314-333
def check_agent(db: Session, p: Principal, agent: A2AAgent, *, caller_agent: str | None = None,
                data_class: str = "internal") -> AgentDecision:
    if agent.status != "active":
        return AgentDecision(False, "agent_inactive", f"원격 에이전트 {agent.id} 비활성")
    if agent.trust_tier == "blocked":
        return AgentDecision(False, "agent_blocked", f"차단된 에이전트입니다: {agent.id}")
    if caller_agent:
        profile = db.get(AgentProfile, caller_agent.split("/")[0])
        if profile is not None and agent.id not in (profile.remote_agents or []):
            return AgentDecision(False, "agent_not_allowed", f"프로파일 {profile.name} 의 허용 원격 에이전트가 아닙니다.")
    dec = policy.evaluate(db, policy.PolicyInput("agent", agent.id, p, data_class=data_class, agent=caller_agent,
                                                 attrs={"trust_tier": agent.trust_tier}))
    if agent.trust_tier == "untrusted" and dec.allow:
        pol = db.get(GatewayPolicy, dec.policy_id) if dec.policy_id else None
        if pol is None or pol.target in ("*", ""):
            return AgentDecision(False, "agent_untrusted",
                                 f"미신뢰 에이전트는 명시적 허용 정책이 필요합니다: {agent.id}", dec.as_dict())
    if not dec.allow:
        return AgentDecision(False, "policy_denied", dec.reason, dec.as_dict())
    return AgentDecision(True, "allowed", dec.reason, dec.as_dict())
```
**분석 / Analysis (Level 2)**
- L316-319: 비활성·차단 즉시 거부. 호출처 `app/services/a2a_gateway.py:452-456`(같은 세션에서 행을 다시 읽어 판정).
- L320-323: `caller_agent` 는 `ctx.agent` — 2차에서 사람 주체의 신고는 버려진다(`app/services/policy.py:71`). 프로파일 제한은 좁히는 방향(신고를 생략하면 제한이 빠지지만 권한이 넓어지지는 않음 — 1차와 같은 판단).
- L326-330: untrusted 는 **와일드카드가 아닌** 허용 정책이 필요. 시드의 "신뢰 원격 에이전트 허용"(`app/seed.py:70`, `target="*"`)으로는 통과하지 못한다(좋음). 다만 `pol.target` 이 `"*"`·`""` 만 와일드카드로 보므로 `obs-*` 같은 패턴 정책도 "명시" 로 인정된다(1차 지적 그대로, 의도 확인 필요 — 낮음).
- `trusted` 와 `verified` 는 이 함수에서 구분되지 않는다(둘 다 일반 정책 판정만) — 등급 차이는 정책이 `trust_tier` 속성으로 표현해야 한다.

## 파일 평가 / File Assessment
- 강점: (1) 승인 소비가 조건부 UPDATE + rowcount 로 원자화돼 같은 승인으로 고위험 도구가 두 번 실행되지 않고, 승인 재사용 범위를 요청자·인자 해시·도구·기한으로 좁혔다. (2) `readOnlyHint` 는 trusted 서버만 믿고 `destructiveHint` 는 항상 믿는 비대칭 신뢰, 미등록 도구 기본 거부, untrusted 에이전트의 와일드카드 정책 불인정. (3) 판정 단계와 거부 코드가 1:1 로 대응하고 연장 상한·고위험 세션 기억 금지·approvers 직무 분리가 일관된다.
- 위험: (1) [위험:높음] requester 모드 자기 승인이 주체 종류를 보지 않아 기계 주체가 고위험 도구를 사람 없이 승인 — `app/services/governance.py:274-281` + 기본값 `:69-70`. (2) [위험:중간] 기한 직후 결정 시 지연 만료가 훅·감사를 건너뛰어 실행 영구 대기 — `:286-290`. (3) [위험:중간] 새 도구 자동 허용 + "모르면 저위험" 이름 휴리스틱 — `:25-26,62-73`. (그 밖 [위험:낮음]: 결정 비원자 `:289-299`, 감사 `actor_kind` 누락 `:300`, 세션 기억 무기한 `:154,188-195`, pending 스팸 `:165-171`, 연장 과소 계수 `:255-263`.)
- 개선 권고(영향순): (1) `can_decide` 에 대화형 사용자 주체 조건 + 감사 `actor_kind`. (2) 지연 만료를 스케줄러와 같은 처리(감사·훅)로 통일하고 결정을 조건부 UPDATE 로. (3) untrusted 서버의 새 도구는 비허용 또는 승인 필요로 생성, 이름 휴리스틱을 읽기 동사 허용 목록으로 뒤집기, 도구 정의 변경 시 재검토. (4) 세션 기억 TTL, 동일 pending 재사용, `trusted` 기본값 False, `_valid_approval` 에 `kind` 조건.
- 파일 위험 등급 / Risk grade: 높음 — 미해결 [위험:높음] 1건 + 보안 경계 핵심 모듈(사람 승인 통제)에 [위험:중간] 2건.
- 재읽기 권고 레벨: 2 — 사람 승인이라는 핵심 통제의 정확성이 걸려 있고, 상태 전이(pending → approved/rejected/expired → used)가 이 파일·스케줄러·오케스트레이터·API 에 흩어져 있어 줄 단위로 다시 맞춰 볼 가치가 있다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| [중간·TOCTOU] L153-157 승인 소비가 읽기→쓰기 비원자 → 고위험 도구 이중 실행 (1차 INDEX §4.3 핫스팟 #4) | 해결 | L161 `_consume` 승자만 허용, L177-185 `UPDATE … WHERE status='approved'` + `rowcount == 1`; 시험 `test/test_review_fixes.py:343-355`(순차 — 실제 경합 시험은 공백) |
| [중간] L46-47 업스트림 `readOnlyHint` 신뢰 → 승인 없이 low | 해결 | L41-51 `trusted` 일 때만, L56-60 카탈로그 신뢰 등급 조회; 시험 `test/test_policy_hardening.py:27-42`(C43-01). 단 승인 모달 표시(L217-218)는 여전히 신뢰 — 새 [낮음] |
| L25-26 이름 휴리스틱 동사 누락 | 미해결 | L25-26 (`drop`·`purge`·`exec_`·`kill` 등 없음) |
| L64 새 도구 `allowed=True` 자동 노출 (1차 INDEX 핫스팟 #4 "새 도구 자동 허용") | 미해결 | L69; 시드 전체 도구 허용 정책 `app/seed.py:69` 로 실효 — 본 보고서 [위험:중간] |
| L65 고위험 기본 `approver="requester"` | 미해결(영향 확대) | L70 — 기계 주체 자기 승인과 결합해 [위험:높음](L274-281) |
| L60 도구마다 SELECT(N+1) | 미해결(수용) | L66 |
| L90 `create_approval` 이 판정 중간에 커밋 | 미해결 | L96 (`_consume` L182 도 같은 성격) |
| [낮음] L162-167 승인 없이 반복 호출 시 pending 스팸 | 미해결 | L165-171 |
| L134 업스트림 inputSchema 의 정규식(ReDoS) | 미해결(영향 낮음) | L140-144 |
| L124 `agent` 클라이언트 헤더 위조로 agent 주체 정책 충족 | 부분 해결 | 사람 주체 신고는 무시 `app/services/policy.py:21,71`; 기계 주체(`workload`·`access_token`·`delegation`)는 여전히 신고값 사용 |
| L141 warn 모드 감사가 판정 중 별도 트랜잭션 | 유지(의도) | L146-148 |
| `remembered` 의 `session_id` 가 클라이언트 헤더 → 범위 임의 확대 | 미해결 | `app/services/policy.py:73` 검증 없음; L154·L188-195 — 기한 없음까지 [낮음] |
| L175-177 `session_id`·`target` 인덱스 없음 | 미해결 | `app/models.py:531,536` |
| L213-217 `checks` 항목 항상 ok=True | 미해결 | L228-232 |
| L247-248 연장 읽기-수정-쓰기 비원자(과소 계수) | 미해결 | L255-263 |
| L271-284 결정이 조건부 갱신 아님 → 마지막 쓰기 승리 | 미해결 | L289-299 (승인 소비 후 거부 덮어쓰기 시나리오 추가) |
| L262 `delegated_to` 설정 경로·권한 미확인 | 확인 — 부분 결함 | `app/api/ops.py:416-436` 위임자는 `approvals:decide` 필요하나 대상의 결정 권한은 미검사 → L277-278 이 무조건 인정 [낮음] |
| L305-308 `caller_agent` 헤더 | 부분 해결 | `app/services/policy.py:71` (사람 주체 신고 무시) |
| L313 `obs-*` 패턴도 "명시" 로 인정 | 미해결(의도 확인 필요) | L328 |
