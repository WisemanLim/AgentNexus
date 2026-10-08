"""정책 게시 심사 (SCR-H07·H08, FR-G03·O05) — 초안 → 회귀 시험(케이스 + 최근 실제 요청 재평가) → 게시 요청(사유) → 작성자 외 승인.

회귀 시험 케이스가 하나라도 기대와 다르면 게시 요청을 할 수 없다. 최근 요청 재평가는 판정이 바뀌는 건수를 보여 준다(차단은 아님).
"""
import fnmatch
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.db import utcnow
from app.models import AuditEvent, GatewayPolicy, PolicyDraft, PolicyTest, UsageRecord, User
from app.services import policy

FIELDS = ("name", "description", "subject_type", "subject", "target_type", "target", "effect", "conditions", "args_schema",
          "data_class_routing", "approval_required", "priority", "enabled")
KIND = {"model": "model", "tool": "tool", "remote_agent": "agent"}


def transient(snapshot: dict, policy_id: str | None) -> GatewayPolicy:
    p = GatewayPolicy(id=policy_id or f"draft:{snapshot.get('name', 'new')}", **{k: snapshot.get(k) for k in FIELDS
                                                                                   if snapshot.get(k) is not None})
    for k, default in (("subject_type", "any"), ("subject", "*"), ("target", "*"), ("effect", "allow"), ("conditions", {}),
                       ("args_schema", {}), ("data_class_routing", {}), ("approval_required", False), ("priority", 100),
                       ("enabled", True)):
        if getattr(p, k) is None:
            setattr(p, k, default)
    return p


def _principal(db: Session, inp: dict) -> Principal:
    sub = inp.get("sub") or "sim"
    u = db.scalars(select(User).where(User.sub == sub)).first()
    return Principal(sub=sub, kind="user", roles=inp.get("roles") or (u.roles if u else ["user"]),
                     groups=inp.get("groups") or (u.groups if u else []), team=inp.get("team") or (u.team if u else None))


def _decide(db: Session, inp: dict, override: dict | None) -> policy.Decision:
    pi = policy.PolicyInput(inp["kind"], inp["target"], _principal(db, inp), inp.get("data_class", "internal"),
                            inp.get("args") or {}, inp.get("agent"), inp.get("attrs") or {})
    return policy.evaluate_builtin(db, pi, override)


def run_tests(db: Session, draft: PolicyDraft) -> dict:
    ov = {draft.policy_id or f"draft:{draft.id}": transient(draft.snapshot, draft.policy_id or f"draft:{draft.id}")}
    cases = []
    for t in db.scalars(select(PolicyTest).order_by(PolicyTest.created_at)):
        got = _decide(db, t.input, ov)
        cases.append({"id": t.id, "name": t.name, "expect": t.expect, "got": "allow" if got.allow else "deny",
                      "reason": got.reason, "pass": got.allow == (t.expect == "allow")})
    # 최근 실제 요청 재평가 — 판정 변화 건수 (게시 영향 미리보기)
    since = utcnow() - timedelta(hours=24)
    seen, changed, checked = set(), [], 0
    for u in db.scalars(select(UsageRecord).where(UsageRecord.ts > since).order_by(UsageRecord.ts.desc()).limit(300)):
        kind = KIND.get(u.kind)
        dc = ((u.policy_decisions or [{}])[0] or {}).get("data_class", "internal") if u.policy_decisions else "internal"
        key = (kind, u.target, u.user_sub, dc)
        if not kind or key in seen:
            continue
        seen.add(key)
        inp = {"kind": kind, "target": u.target, "sub": u.user_sub, "data_class": dc, "agent": u.agent}
        before, after = _decide(db, inp, None), _decide(db, inp, ov)
        checked += 1
        if before.allow != after.allow:
            changed.append({**inp, "before": "allow" if before.allow else "deny", "after": "allow" if after.allow else "deny"})
    return {"cases": cases, "passed": all(c["pass"] for c in cases), "replay": {"checked": checked, "changed": len(changed),
                                                                               "samples": changed[:10]},
            "tested_at": utcnow().isoformat()}


def diff(a: dict | None, b: dict) -> list[dict]:
    a = a or {}
    return [{"field": k, "from": a.get(k), "to": b.get(k)} for k in FIELDS if a.get(k) != b.get(k)]


def to_rego(snapshot: dict) -> str:
    """정책 → Rego 미리보기 (E03 ⑦·H08) — OPA 번들 형식과 같은 의미. 내장 엔진은 같은 규칙을 직접 평가한다."""
    st, subj = snapshot.get("subject_type", "any"), snapshot.get("subject", "*")
    who = "true" if st == "any" or subj == "*" else {
        "group": f'"{subj}" in input.subject.groups', "role": f'"{subj}" in input.subject.roles',
        "team": f'input.subject.team == "{subj}"', "user": f'input.subject.sub == "{subj}"',
        "agent": f'input.agent == "{subj}"'}.get(st, "true")
    dcs = (snapshot.get("conditions") or {}).get("data_class") or []
    dc = f'\n    input.data_class in {{{", ".join(chr(34) + d + chr(34) for d in dcs)}}}' if dcs else ""
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(snapshot.get("name") or "policy")).strip("_").lower() or "policy"
    name = name if name[0].isalpha() else f"p_{name}"  # Rego 패키지 경로는 식별자
    effect = "allow" if snapshot.get("effect", "allow") == "allow" else "deny"
    out = (f"package agentnexus.gateway.{name}\n\n# {snapshot.get('description') or ''}\n{effect} if {{\n"
           f"    input.kind == \"{snapshot.get('target_type')}\"\n    glob.match(\"{snapshot.get('target', '*')}\", [\".\"], input.target)\n"
           f"    {who}{dc}\n}}\n")
    if snapshot.get("approval_required"):
        out += "\napproval_required if { " + effect + " }\n"
    for k, v in (snapshot.get("data_class_routing") or {}).items():
        out += f'\nreroute := "{v}" if {{ input.data_class == "{k}" }}\n'
    return out


def stats(db: Session) -> dict:
    """정책별 24시간 결정 수 (H07 ②) — 허용 = 사용 기록, 거부 = 감사 거부 이벤트. 일치 정책 기준 근사(대상 패턴)."""
    since = utcnow() - timedelta(hours=24)
    allow = db.execute(select(UsageRecord.kind, UsageRecord.target, func.count()).where(UsageRecord.ts > since)
                       .group_by(UsageRecord.kind, UsageRecord.target)).all()
    deny = db.execute(select(AuditEvent.action, AuditEvent.target, func.count()).where(
        AuditEvent.ts > since, AuditEvent.action.in_(("gateway.tool.deny", "gateway.a2a.deny", "gateway.model.deny")))
        .group_by(AuditEvent.action, AuditEvent.target)).all()
    out = {}
    for p in db.scalars(select(GatewayPolicy)):
        a = sum(n for k, t, n in allow if KIND.get(k) == p.target_type and t and fnmatch.fnmatchcase(t, p.target or "*"))
        d = sum(n for act, t, n in deny if act.split(".")[1] in (p.target_type, "a2a" if p.target_type == "agent" else "")
                and t and fnmatch.fnmatchcase(t, p.target or "*"))
        out[p.id] = {"allow": a if p.effect == "allow" else 0, "deny": d}
    return out


def engine_status() -> dict:
    from app.core.config import settings
    from app.core.http import sync_client
    if settings.policy_engine != "opa" or not settings.opa_url:
        return {"engine": "builtin", "bundle": "내장 정책 (DB)", "synced": True, "detail": "모든 워커가 같은 DB 정책을 평가"}
    try:
        with sync_client(timeout=2) as http:
            ok = http.get(settings.opa_url.rstrip("/") + "/health").status_code == 200
    except Exception:  # noqa: BLE001
        ok = False
    return {"engine": "opa", "bundle": "policies/agentnexus.rego", "synced": ok,
            "detail": "OPA 정상 — 번들 동기" if ok else "OPA 응답 없음 — 이전 번들 유지(요청은 기본 거부)"}
