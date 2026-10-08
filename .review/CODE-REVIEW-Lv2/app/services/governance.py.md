> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 26. app/services/governance.py
**파일 요약 / File Summary**
- 역할 / Role: 도구·에이전트 거버넌스 — 위험도 분류, 기본 도구 규칙, 도구 호출 판정(서버 활성→규칙 허용→프로파일 허용목록→정책→인자 스키마→승인), 승인 생성·검증·소비·결정·연장·권한(직무 분리), 승인 모달 맥락, 원격 에이전트 신뢰 등급 판정.
- 진입점 거리 / Distance: 2 (`app/services/mcp_gateway.py:880`, `app/services/a2a_gateway.py:451`, `app/api/gateway_admin.py:752,763,771`)
- LOC: 318
- 주요 export: `ToolDecision`, `AgentDecision`, `classify_risk`, `default_rules`, `args_hash`, `create_approval`, `check_tool`, `remembered`, `approval_context`, `MAX_EXTENSIONS`, `extend_step_s`, `extend`, `can_decide`, `decide`, `check_agent`, `APPROVAL_TTL`
- 의존(내부): `app.core.{auth,config,ids,errors}`, `app.db`, `app.models`, `app.services.{audit,policy,notify}`
- 외부 패키지: jsonschema, sqlalchemy
- 부수효과 / Side effects: DB 쓰기·커밋(승인 생성·소비·결정·연장, 기본 규칙), 감사, 알림
- 동시성 / Concurrency: 동기(스레드풀에서 호출), 행 잠금·조건부 갱신 없음
- 테스트 커버리지 흔적: `test/test_gateway_policy.py`, `test/test_mcp.py`(승인 흐름·미등록 도구), `test/test_extend.py`, `test/test_review.py`

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
from sqlalchemy import select
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
- L1-5: 판정 순서를 docstring 으로 명시 — 감사·디버깅 시 "어느 단계에서 거부됐는가" 를 추적하는 기준.
- L24 `APPROVAL_TTL = 30분`: 도구 승인 기본 유효 시간.
- L25-26 `_WRITE`: 이름에 쓰기 동사(create·delete·send·execute·deploy 등)가 단어 경계(`.`·`_`·`-` 또는 끝)로 들어 있으면 쓰기로 간주(대소문자 무시).
**짚어볼 점 / Watch-outs**
- L25-26 이름 휴리스틱은 우회가 쉽다 — `remove` 는 있지만 `drop`·`purge`·`kill`·`terminate`·`approve`·`grant`·`transfer`·`pay` 등은 없다. 업스트림이 `readOnlyHint: true` 를 거짓으로 달면 L46-47 이 먼저 `low` 로 판정한다(annotations 는 MCP 사양상 **신뢰하지 않는 힌트**). 기본 규칙은 결국 운영자 검토가 필요하다.

```python path=app/services/governance.py lines=29-50
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


def classify_risk(tool: dict) -> str:
    """MCP 도구 annotations(readOnlyHint·destructiveHint)와 이름 규칙으로 기본 위험도."""
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint") is True:
        return "high"
    if ann.get("readOnlyHint") is True:
        return "low"
    if _WRITE.search(tool.get("name", "")):
        return "high"
    return "low"
```
**분석 / Analysis (Level 2)**
- L29-38 `ToolDecision`: 허용 여부·코드·사유·위험도·승인·정책 판정 dict·소독 여부.
- L41-50 `classify_risk`: annotations 우선(`destructiveHint` → high, `readOnlyHint` → low), 없으면 이름 규칙.
**짚어볼 점 / Watch-outs**
- [위험:중간] L46-47 업스트림이 자기 도구에 `readOnlyHint: true` 를 붙이면 승인 없이 허용(`low`)된다 — MCP 사양은 annotations 를 신뢰할 수 없는 서버의 힌트로 취급하라고 한다. `trusted` 등급 서버에서만 annotations 를 믿도록 조건을 두는 것이 안전하다.

```python path=app/services/governance.py lines=53-79
def default_rules(db: Session, server_id: str, tools: list[dict]) -> list[ToolRule]:
    """즉시 사용 시 '정책 기본값 적용' — 읽기 도구 허용, 쓰기·파괴 도구는 승인 필요. 기존 규칙은 유지."""
    out = []
    for t in tools:
        name = t.get("name")
        if not name:
            continue
        rule = db.scalars(select(ToolRule).where(ToolRule.server_id == server_id, ToolRule.tool == name)).first()
        if rule is None:
            risk = classify_risk(t)
            rule = ToolRule(id=new_id("tr"), server_id=server_id, tool=name, allowed=True, risk=risk,
                            approval_required=risk == "high", approver="requester",
                            description=(t.get("description") or "")[:500])
            db.add(rule)
        out.append(rule)
    db.flush()
    return out


def args_hash(args: dict) -> str:
    return sha256_hex(canonical_json(args or {}))


def _profile_allows(profile: AgentProfile | None, qualified: str) -> bool:
    if profile is None:
        return True
    return any(fnmatch.fnmatchcase(qualified, pat) for pat in (profile.tools or []))
```
**분석 / Analysis (Level 2)**
- L53-69 `default_rules`: 즉시 사용 기본값 — **기존 규칙은 유지**하고 새 도구만 추가(읽기 허용, 고위험은 승인 필요, 승인 주체는 요청자). `flush` 만 하고 커밋은 호출자.
- L72-73 `args_hash`: 정규 JSON(키 정렬)의 SHA-256 — 승인한 인자와 실행 인자를 바인딩.
- L76-79 `_profile_allows`: 프로파일의 허용 패턴(fnmatch, 예: `jira.*`).
**짚어볼 점 / Watch-outs**
- L60 도구마다 SELECT 1회(N+1) — 도구 수십 개면 무시 가능.
- L64 기본 `allowed=True` — 새로 나타난 도구는 위험도만 다르게 **자동 노출**된다(쓰기는 승인 필요). "기본 거부" 원칙(규칙 없는 도구 = 거부)과 달리, 도구 목록 갱신 자체가 규칙을 만들어 허용하는 셈이다. 업스트림이 새 도구를 추가하면 운영자 검토 없이 사용 가능해진다(읽기 휴리스틱 통과 시 승인도 없음).
- L65 `approver="requester"` — 고위험 쓰기도 기본은 **요청자 본인 승인**(사용자 동의)이다. 직무 분리(`approvers`)는 운영자가 규칙을 바꿔야 적용된다.

```python path=app/services/governance.py lines=82-107
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
- L82-96 `create_approval`: pending 승인 생성(인자·해시·만료) → 커밋 → 감사, 승인자 큐면 보안 담당 알림.
- L99-107 `_valid_approval`: 승인됨·같은 서버·도구·**같은 요청자**·**같은 인자 해시**·미만료일 때만 유효.
**짚어볼 점 / Watch-outs**
- L90 커밋을 이 함수 안에서 한다 — `check_tool` 의 판정 흐름 중간에 트랜잭션이 끊긴다(호출자 세션의 다른 변경도 함께 커밋됨).

```python path=app/services/governance.py lines=110-170
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
    if needs_approval:
        if approval_id:
            ap = _valid_approval(db, approval_id, p, server_id, tool, args)
            if ap is not None:
                ap.status, ap.used_at = "used", utcnow()
                db.commit()
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
- L114-121: 서버 활성, 규칙 존재(**미등록 = 거부**), 규칙 허용.
- L122-129: 호출 에이전트 프로파일 허용 목록, executor 노드면 노드 승인 정책(게이트웨이보다 완화 불가 — 추가 요구만).
- L130-133: 정책 엔진(builtin/OPA) 판정 — 기본 거부.
- L134-144: 인자 스키마 — 도구 inputSchema(strict) + 규칙 args_schema(strict/warn), warn 이면 감사만.
- L145-151: 승인 필요 여부, "이 세션 동안 다시 묻지 않기"(고위험 제외).
- L152-169: 승인 id 가 유효하면 **used 로 소비**하고 허용, 아니면 새 pending 승인 생성 후 `approval_required`.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 경쟁(TOCTOU)] L153-157 `_valid_approval`(읽기) → `ap.status = "used"; db.commit()`(쓰기)가 원자적이지 않다. 같은 approval_id 로 동시에 두 호출이 들어오면 둘 다 `approved` 를 읽고 둘 다 통과해 **고위험 도구가 한 번의 승인으로 두 번 실행**된다. `UPDATE approvals SET status='used' WHERE id=:id AND status='approved'` 의 rowcount 로 소비해야 한다(CWE-367).
- [위험:낮음 · 자원] L162-167 승인 없이 고위험 도구를 호출할 때마다 **새 pending 승인 행**을 만든다(같은 요청자·도구·인자 해시의 대기 건 재사용 없음). 반복 호출로 승인 대기열·알림(approvers 모드)을 스팸할 수 있다.
- L134 `srv.tools` 의 inputSchema 는 업스트림이 준 스키마 — 신뢰하지 않는 스키마의 `pattern` 정규식으로 인자 검증(ReDoS 가능성, 영향 낮음).
- L124 `agent` 는 클라이언트 헤더 값 — 프로파일 제한은 좁히는 방향이라 위조로 권한이 넓어지지는 않지만, `policy.evaluate(... agent=agent)`(L130) 의 agent 주체 allow 정책은 위조로 충족될 수 있다(`app/services/policy.py:71-72`).
- L141 warn 모드 감사를 **판정 함수 안에서** 별도 트랜잭션으로 기록 — 판정이 이후 거부돼도 경고 감사는 남는다(의도대로).

```python path=app/services/governance.py lines=173-180
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
- L173-180 `remembered`: 같은 세션·같은 도구·같은 요청자의 approved/used 승인 중 `remember_session` 이 표시되고 고위험이 아닌 것이 있으면 True.
**짚어볼 점 / Watch-outs**
- `session_id` 는 클라이언트 헤더(`X-AgentNexus-Session`)에서 올 수 있다(`app/api/mcp.py:60`) — 같은 요청자 조건이 있어 다른 사용자의 기억을 쓸 수는 없지만, 사용자가 세션 id 를 재사용해 "다시 묻지 않기" 범위를 임의로 넓힐 수 있다(세션 수명과 무관하게).
- L175-177 조회 컬럼(`session_id`·`target`)에 인덱스가 없다(`app/models.py:530,535`).

```python path=app/services/governance.py lines=183-219
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
- L183-219 `approval_context`: 승인 모달 맥락 — 요청 출처(프로파일·노드·단계·세션·trace), 실행 신원(OBO 스코프), 영향(되돌리기 가능성·외부 반영 여부·설명), 통과한 검사 목록, 남은 시간.
**짚어볼 점 / Watch-outs**
- L213-217 `checks` 의 `schema`·`approval_required`·`policy` 항목은 **항상 ok=True** 로 고정 문구 — 실제 재검증 결과가 아니다(승인 요청이 만들어졌다는 사실에서 유도). 화면이 "검증 통과" 로 보이지만 승인 시점에 다시 확인한 값은 아니다.
- L186-200 쿼리 7~8개 — 상세 화면 1회성이라 수용.

```python path=app/services/governance.py lines=222-256
MAX_EXTENSIONS = 10  # WCAG 2.2.1 — 간단한 동작으로 10번까지 연장 (D14-③: 늘어난 시간 합계 ≤ 기본의 10배)
CONSENT_KINDS = ("sampling", "elicitation")


def extend_step_s(ap: Approval) -> int | None:
    """한 번 연장할 때 늘리는 시간 = 그 승인의 기본 대기 시간 (동의 60초 · 도구 승인 30분). 기한 없는 승인은 None."""
    if ap.kind in CONSENT_KINDS:
        return int(settings.mcp_consent_timeout_s)
    if ap.kind == "tool_call":
        return int(APPROVAL_TTL.total_seconds())
    return None


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
                       "expires_at": ap.expires_at.isoformat()})
    return ap
```
**분석 / Analysis (Level 2)**
- L222-223: 연장 최대 10회(WCAG 2.2.1 — 시간 제한 조절), 동의 종류.
- L226-232 `extend_step_s`: 연장 단위 = 그 승인의 기본 대기 시간(동의 60초·도구 30분).
- L235-256 `extend`: 기한 있음·대기/검토 중·미만료·권한(요청자 또는 결정권자)·횟수 확인 → 만료 시각 연장 → 실행 이벤트 → 커밋 → 감사.
**짚어볼 점 / Watch-outs**
- L247-248 읽기-수정-쓰기(`extensions + 1`)가 비원자적 — 동시 연장 두 번이 1회로 기록될 수 있다(상한 우회는 아니고 과소 계수).

```python path=app/services/governance.py lines=259-287
def can_decide(ap: Approval, p: Principal) -> bool:
    """requester 승인 = 요청자 본인(동의) 또는 승인자, approvers 승인 = 요청자 아닌 승인 권한자(직무 분리).
    위임받은 사람(SCR-H12)은 요청자가 아니면 결정할 수 있다."""
    if ap.delegated_to and ap.delegated_to == p.sub and p.sub != ap.requester_sub:
        return True
    if ap.approver == "approvers":
        return p.has("approvals:decide") and p.sub != ap.requester_sub
    return p.sub == ap.requester_sub or p.has("approvals:decide")


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
- L259-266 `can_decide`: 위임받은 사람(요청자 아님) 허용, `approvers` 모드는 결정 권한 + **요청자 본인 금지**(직무 분리), `requester` 모드는 요청자 본인 또는 결정 권한자.
- L269-287 `decide`: 만료면 expired, pending 아니면 거부, 권한 확인, 고위험은 "세션 기억" 불가, 상태·결정자·메모 기록 → 커밋 → 감사.
**짚어볼 점 / Watch-outs**
- L271-284 결정도 조건부 갱신이 아니다 — 두 승인자가 동시에 승인/거부하면 마지막 쓰기가 이기고 감사에는 두 결정이 모두 남는다.
- L262 `delegated_to` 를 설정하는 경로·권한은 이 파일에 없다(`app/api/ops.py` 등 — 본 리뷰 미선정). 위임 설정 권한이 느슨하면 직무 분리가 약해진다.

```python path=app/services/governance.py lines=290-318
# ── 원격 에이전트 신뢰 등급 ──
@dataclass
class AgentDecision:
    allow: bool
    code: str
    reason: str
    policy: dict = field(default_factory=dict)


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
- L290-296 `AgentDecision`.
- L299-318 `check_agent`: 비활성·차단 거부 → 호출 프로파일의 허용 원격 에이전트 → 정책 판정 → **미신뢰 에이전트는 와일드카드(`*`)가 아닌 명시 허용 정책이 있어야** 허용(L311-315).
**짚어볼 점 / Watch-outs**
- L305-308 `caller_agent` 는 클라이언트 헤더 — 헤더를 생략하면 프로파일 제한이 적용되지 않는다(좁히는 방향이라 우회해도 권한이 넓어지지는 않음). 반대로 agent 주체 정책은 위조로 충족 가능(L309-310).
- L313 정책 대상이 `"*"` 가 아니면 "명시적" 으로 본다 — `obs-*` 같은 패턴도 명시로 인정된다(의도인지 확인).

## 파일 평가 / File Assessment
- 강점: (1) 미등록 도구 기본 거부·인자 해시 바인딩·요청자 일치로 승인 재사용 범위를 좁혔다. (2) 직무 분리(approvers)·위임·연장 상한·고위험 세션 기억 금지. (3) 판정 단계가 명확하고 거부 사유 코드가 구체적.
- 위험: (1) [중간] 승인 소비의 TOCTOU(이중 실행). (2) [중간] 업스트림 annotations·새 도구 자동 허용(기본 규칙). (3) [낮음] 승인 스팸, 결정·연장의 비원자적 갱신.
- 개선 권고(영향순): (1) 조건부 UPDATE 로 승인 소비·결정. (2) 새 도구는 기본 `allowed=False`(운영자 검토 후 허용) 또는 신뢰 등급별 annotations 신뢰. (3) 동일 요청 pending 승인 재사용.
- 재읽기 권고 레벨: 2 — 사람 승인이라는 핵심 통제의 정확성이 걸린 파일이다.
