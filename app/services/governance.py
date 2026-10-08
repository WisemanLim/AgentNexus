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
# 도구 이름 → 단어 (snake·kebab·점·camelCase·약어) — 동사 목록과 단어 단위로 비교 (D20-④: createIssue 같은 camelCase 와
# drop·revoke·grant·transfer 같은 동사가 빠지던 것). 부분 문자열이 아니라 단어라 address(add)·settings(set) 같은 오탐이 없다
HIGH_VERBS = frozenset("delete remove drop truncate purge destroy kill terminate transition approve reject execute exec run "
                       "deploy rollback restart grant revoke transfer pay purchase refund wipe "
                       "overwrite erase shutdown flush".split())  # 59th: 빠졌던 파괴 동사
WRITE_VERBS = HIGH_VERBS | frozenset("create update write post put patch send merge close assign comment upload set insert add "
                                     "modify edit rename move archive publish invite share reset cancel disable enable import "
                                     "push commit release schedule trigger invoke stop start link "
                                     "upsert save submit clear".split())


def tool_words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


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
    if any(w in WRITE_VERBS for w in tool_words(tool.get("name", ""))):
        return "high"
    return "low"


def default_rules(db: Session, server_id: str, tools: list[dict]) -> list[ToolRule]:
    """즉시 사용 시 '정책 기본값 적용' — 읽기 도구 허용, 쓰기·파괴 도구는 승인 필요. 기존 규칙은 유지."""
    from app.models import CatalogItem
    from app.services import registry
    srv = db.get(McpServer, server_id)
    item = db.get(CatalogItem, srv.catalog_item_id) if srv and srv.catalog_item_id else None
    # 승인자가 정한 신뢰 등급(E05 — official·partner) — 전에는 "trusted" 만 비교해 승인 경로에서는 늘 거짓이었다 (D21-⑤)
    trusted = bool(item) and registry.trust_of(item) in ("official", "partner", "trusted")
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


def _profile_allows(profile: AgentProfile | None, qualified: str) -> bool:
    if profile is None:
        return True
    return any(fnmatch.fnmatchcase(qualified, pat) for pat in (profile.tools or []))


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


def _consume(db: Session, ap: Approval) -> bool:
    """승인 1회 사용 — 조건부 UPDATE 로 동시 호출 중 하나만 성공 (같은 승인으로 고위험 도구가 두 번 실행되지 않게)."""
    now = utcnow()
    won = db.execute(update(Approval).where(Approval.id == ap.id, Approval.status == "approved")
                     .values(status="used", used_at=now)).rowcount == 1
    db.commit()
    if won:
        db.refresh(ap)
    return won


def remembered(db: Session, p: Principal, session_id: str, qualified: str) -> bool:
    """'이 세션 동안 다시 묻지 않기' (SCR-C04 ⑦) — 같은 세션·같은 도구를 승인했고 고위험이 아니면."""
    for ap in db.scalars(select(Approval).where(Approval.session_id == session_id, Approval.target == qualified,
                                                Approval.requester_sub == p.sub,
                                                Approval.status.in_(("approved", "used")))):
        if (ap.payload or {}).get("remember_session") and ap.risk != "high":
            return True
    return False


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


HUMAN_KINDS = ("user", "pat")  # 승인·동의는 사람만 — 기계 주체(워크로드·Access Token·클라이언트 키·위임)는 결정하지 못한다


def can_decide(ap: Approval, p: Principal) -> bool:
    """requester 승인 = 요청자 본인(동의) 또는 승인자, approvers 승인 = 요청자 아닌 승인 권한자(직무 분리).
    위임받은 사람(SCR-H12)은 요청자가 아니면 결정할 수 있다. 기계 주체는 자기 요청도 결정하지 못한다 (D19-④ — 사람 승인 우회)."""
    if p.kind not in HUMAN_KINDS:
        return False
    if ap.delegated_to and ap.delegated_to == p.sub and p.sub != ap.requester_sub:
        return True
    if ap.approver == "approvers":
        return p.has("approvals:decide") and p.sub != ap.requester_sub
    return p.sub == ap.requester_sub or p.has("approvals:decide")


def decide(db: Session, ap: Approval, p: Principal, approve: bool, note: str | None = None,
           remember: bool = False) -> Approval:
    if ap.expires_at and ap.expires_at < utcnow() and ap.status == "pending":
        # 만료 처리(자동 거부·실행 재개·감사)는 예약 작업(approval_jobs)이 한다 — 여기서 상태만 바꾸면 실행이 영구 대기 (D19-④)
        raise bad_request("승인 기한이 지났습니다 — 자동 거부로 처리됩니다.", code="approval_closed")
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
