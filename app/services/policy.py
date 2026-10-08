"""Policies (FR-G03) — 누가(사용자·그룹·역할·팀·에이전트) 어떤 모델·도구·에이전트를 어떤 인자로 호출 가능한가.

내장 엔진: GatewayPolicy 를 priority 오름차순으로 평가해 첫 매치 그룹의 결정을 따른다(같은 priority 면 deny 우선).
매치 없음 = 거부(default deny, NFR-SEC2). OPA 어댑터(POLICY_ENGINE=opa)는 동일 input 을 Rego 로 평가한다.
target 표기: 정확한 id · glob(`jira.*`) · `category:<모델분류>` · `risk:<도구위험도>`.
"""
import fnmatch
from dataclasses import dataclass, field

import jsonschema
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.config import settings
from app.core.http import sync_client
from app.models import GatewayPolicy

DATA_CLASSES = ("public", "internal", "confidential")
DECLARE_LOWER = "data:declare"  # 기본값보다 낮은(덜 민감한) 데이터 등급 신고 권한 (D17-①)
MACHINE_KINDS = ("workload", "access_token", "delegation")  # agent 신고를 받는 주체 — 관리자 발급 신원·플랫폼 위임 토큰
_DEFAULT_DC: list = [0.0, "internal"]  # (읽은 시각, 값) — 워커별 5초 캐시(권한 행렬과 같은 규칙), 설정 저장 시 초기화


def default_data_class(db: Session | None = None) -> str:
    import contextlib
    import time

    from app.db import SessionLocal
    from app.models import Setting
    if time.monotonic() - _DEFAULT_DC[0] > 5:
        with contextlib.nullcontext(db) if db is not None else SessionLocal() as s:
            row = s.get(Setting, "gateway.default_data_class")
        _DEFAULT_DC[:] = [time.monotonic(), row.value if row and row.value in DATA_CLASSES else "internal"]
    return _DEFAULT_DC[1]


def reset_default_data_class() -> None:
    _DEFAULT_DC[0] = 0.0


def declared_data_class(db: Session | None, p: Principal, declared: str | None) -> str:
    """요청 데이터 등급 — 신고가 없으면 기본값. 기본값보다 낮은 신고는 `data:declare` 권한(역할 기본 부여, RBAC 로 회수·
    토큰 스코프로 제한)이 있어야 하고, 없으면 기본값으로 올려 감사에 남긴다. 형식 오류는 그대로 돌려 호출부가 판단."""
    default = default_data_class(db)
    if not declared:
        return default
    declared = declared.strip().lower()  # "Confidential" 같은 표기를 모르는 값으로 흘리지 않게 (D19-④)
    if declared not in DATA_CLASSES or DATA_CLASSES.index(declared) >= DATA_CLASSES.index(default) or p.has(DECLARE_LOWER):
        return declared
    from app.services import audit
    audit.append("gateway.data_class.raise", actor=p.sub, actor_kind=p.kind, decision="raised",
                 data={"declared": declared, "effective": default})
    return default


def caller_context(db: Session | None, p: Principal, headers, declared_dc: str | None = None) -> dict:
    """호출자 신고 헤더의 서버 판정 (D17-①) — agent 는 기계·위임 주체만(사람이 보낸 agent 는 무시: agent 주체 정책 위조 방지),
    run 은 그 주체의 실행만(남의 실행 스트림에 이벤트를 남기지 못하게), 데이터 등급은 `declared_data_class`."""
    run_id = headers.get("x-agentnexus-run")
    if run_id:  # db 가 없으면 이때만 세션을 연다 — 요청마다 DB 를 열지 않게
        import contextlib

        from app.db import SessionLocal
        from app.models import Run
        with contextlib.nullcontext(db) if db is not None else SessionLocal() as s:
            run = s.get(Run, run_id)
            owner = run.user_sub if run is not None else None
        if owner != p.sub:
            run_id = None
    return {"agent": headers.get("x-agentnexus-agent") if p.kind in MACHINE_KINDS else None,
            "data_class": declared_data_class(db, p, declared_dc or headers.get("x-agentnexus-data-class")),
            "session_id": headers.get("x-agentnexus-session"), "run_id": run_id,
            "step_id": headers.get("x-agentnexus-step") if run_id else None}


@dataclass
class PolicyInput:
    kind: str  # model | tool | agent
    target: str
    principal: Principal
    data_class: str = "internal"
    args: dict = field(default_factory=dict)
    agent: str | None = None  # 호출 에이전트 프로파일/노드
    attrs: dict = field(default_factory=dict)  # model.category · tool.risk 등

    def to_opa(self) -> dict:
        p = self.principal
        return {"kind": self.kind, "target": self.target, self.kind: self.target, "data_class": self.data_class,
                "args": self.args, "agent": self.agent, "attrs": self.attrs,
                "subject": {"sub": p.sub, "kind": p.kind, "roles": p.roles, "groups": p.groups, "team": p.team}}


@dataclass
class Decision:
    allow: bool
    reason: str
    require_approval: bool = False
    policy_id: str | None = None
    policy_name: str | None = None
    policy_version: int | None = None
    reroute: str | None = None  # 데이터 분류 라우팅 대체 모델
    matched: list[dict] = field(default_factory=list)
    engine: str = "builtin"

    def as_dict(self) -> dict:
        return {"allow": self.allow, "reason": self.reason, "require_approval": self.require_approval,
                "policy_id": self.policy_id, "policy_name": self.policy_name, "policy_version": self.policy_version,
                "reroute": self.reroute, "engine": self.engine, "matched": self.matched}


def _subject_match(pol: GatewayPolicy, inp: PolicyInput) -> bool:
    p = inp.principal
    v = pol.subject
    match pol.subject_type:
        case "any":
            return True
        case "user":  # 표시 이름은 내장 IdP 사용자(관리자가 정한 이름)만 — 외부 IdP 이름 클레임으로 정책을 얻거나 피하지 못하게 (D19-④)
            from app.core.security import issuer
            return fnmatch.fnmatchcase(p.sub, v) or (p.name is not None and p.name == v and p.issuer == issuer())
        case "group":
            return v in (p.groups or [])
        case "role":
            return v in (p.roles or [])
        case "team":
            return fnmatch.fnmatchcase(p.team or "", v)
        case "agent":
            return inp.agent is not None and fnmatch.fnmatchcase(inp.agent, v)
    return False


def _target_match(pol: GatewayPolicy, inp: PolicyInput) -> bool:
    if pol.target_type != inp.kind:
        return False
    t = pol.target or "*"
    if t.startswith("category:"):
        return inp.attrs.get("category") == t.split(":", 1)[1]
    if t.startswith("risk:"):
        return inp.attrs.get("risk") == t.split(":", 1)[1]
    return fnmatch.fnmatchcase(inp.target, t)


def _conditions_match(pol: GatewayPolicy, inp: PolicyInput) -> bool:
    classes = (pol.conditions or {}).get("data_class")
    return not classes or inp.data_class in classes


def _args_violation(pol: GatewayPolicy, inp: PolicyInput) -> str | None:
    """args_schema = 이 정책이 허용하는 인자 범위. 위반이면 사유 문자열."""
    if not pol.args_schema:
        return None
    try:
        jsonschema.validate(inp.args or {}, pol.args_schema)
    except jsonschema.ValidationError as exc:
        return exc.message
    return None


def evaluate_builtin(db: Session, inp: PolicyInput, override: dict | None = None) -> Decision:
    """override = {policy_id: GatewayPolicy(비영속) 또는 None(삭제)} — 초안 회귀 시험(H08)이 적용 전 판정을 본다."""
    if override:  # 초안이 사용 여부·대상 유형을 바꿀 수 있으므로 전체를 읽고 바꾼 뒤 거른다
        base = db.scalars(select(GatewayPolicy).order_by(GatewayPolicy.priority, GatewayPolicy.created_at)).all()
        ids = {p.id for p in base}
        policies = [override.get(p.id, p) for p in base if override.get(p.id, p) is not None]
        policies += [v for k, v in override.items() if v is not None and k not in ids]
        policies = sorted((p for p in policies if p.enabled and p.target_type == inp.kind), key=lambda p: p.priority)
    else:
        policies = db.scalars(select(GatewayPolicy).where(GatewayPolicy.enabled.is_(True),
                                                          GatewayPolicy.target_type == inp.kind)
                              .order_by(GatewayPolicy.priority, GatewayPolicy.created_at)).all()
    matched = [p for p in policies if _subject_match(p, inp) and _target_match(p, inp) and _conditions_match(p, inp)]
    trace = [{"id": p.id, "name": p.name, "effect": p.effect, "priority": p.priority} for p in matched]
    if not matched:
        return Decision(False, f"일치하는 허용 정책 없음 (default deny: {inp.kind} {inp.target})", matched=trace)
    top = matched[0].priority
    group = [p for p in matched if p.priority == top]
    for p in group:  # 같은 우선순위: 명시 deny → 인자 제한 위반 → allow
        if p.effect == "deny":
            return Decision(False, f"deny: {p.name}" + (f" (data_class={inp.data_class})"
                                                         if (p.conditions or {}).get("data_class") else ""),
                            policy_id=p.id, policy_name=p.name, policy_version=p.version,
                            reroute=(p.data_class_routing or {}).get(inp.data_class), matched=trace)
    for p in group:
        violation = _args_violation(p, inp)
        if violation:
            return Decision(False, f"인자 제한 위반: {p.name} — {violation}", policy_id=p.id, policy_name=p.name,
                            policy_version=p.version, matched=trace)
    winner = group[0]
    approval = any(p.approval_required for p in matched if p.effect == "allow")
    return Decision(True, f"allow: {winner.name}", require_approval=approval, policy_id=winner.id,
                    policy_name=winner.name, policy_version=winner.version,
                    reroute=(winner.data_class_routing or {}).get(inp.data_class), matched=trace)


def evaluate_opa(inp: PolicyInput) -> Decision:
    url = settings.opa_url.rstrip("/") + "/v1/data/agentnexus/gateway"
    try:
        with sync_client(timeout=3) as http:
            res = http.post(url, json={"input": inp.to_opa()})
            res.raise_for_status()
            result = res.json().get("result") or {}
    except Exception as exc:  # noqa: BLE001 — fail closed
        return Decision(False, f"opa_unavailable: {type(exc).__name__}", engine="opa")
    return Decision(bool(result.get("allow")), result.get("reason") or ("opa allow" if result.get("allow")
                                                                        else "opa deny"),
                    require_approval=bool(result.get("require_approval")), reroute=result.get("reroute"),
                    engine="opa")


def evaluate(db: Session, inp: PolicyInput) -> Decision:
    if settings.policy_engine == "opa" and settings.opa_url:
        return evaluate_opa(inp)
    return evaluate_builtin(db, inp)
