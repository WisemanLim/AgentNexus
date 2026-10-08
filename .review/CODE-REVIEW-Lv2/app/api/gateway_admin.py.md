> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [api_Abstract.md](api_Abstract.md)

### 34. app/api/gateway_admin.py
**파일 요약 / File Summary**
- 역할 / Role: AI Gateway 관리 API — 정책 CRUD(비상 경로)·버전·diff·롤백·Rego 미리보기, 정책 게시 심사(초안→회귀 시험→게시 요청→작성자 외 승인), 정책 시험 케이스·시뮬레이터, 한도·예산, 시크릿(마스킹), 보안 가드, 도구/에이전트 거버넌스·소독 규칙·신뢰 등급, 승인(조회·승인·거부·연장·동의), 라우팅 결정 로그.
- 진입점 거리 / Distance: 1 (`app/main.py:19,134`)
- LOC: 835 — `max_lines_per_file`(600) 초과 → **논리 블록 단위로 분석**(모든 블록 커버, 원문 전체 인용)
- 주요 export: `router`, `APPROVAL_HOOKS`(오케스트레이터 재개 훅), `policy_out`, `approval_out`, `rule_out`, `limit_out`
- 의존(내부): `app.core.{auth,errors,ids}`, `app.db`, `app.models`, `app.services.{audit,governance,guards,mcp_consent,policy,policy_review,ratelimit,secrets}`
- 외부 패키지: fastapi, pydantic, jsonschema, sqlalchemy
- 부수효과 / Side effects: DB 쓰기·커밋(정책·초안·한도·시크릿·규칙·승인), 감사, 캐시 무효화
- 동시성 / Concurrency: 동기 엔드포인트(스레드풀), 행 잠금 없음
- 테스트 커버리지 흔적: `test/test_gateway_policy.py`, `test/test_review.py`(정책 심사·2인 검토), `test/test_extend.py`, `test/test_mcp_consent.py`

## 블록 리뷰 / Block Walk (Level 2)

```python path=app/api/gateway_admin.py lines=1-23
"""AI Gateway 관리 API (SCR-H02~H12) — 정책·한도·시크릿·보안 가드·거버넌스·승인 대기열·라우팅 결정 로그."""
import re
from datetime import timedelta
from decimal import Decimal

import jsonschema

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_principal, require
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.ids import new_id
from app.db import get_db, utcnow
from app.models import (A2AAgent, Approval, GatewayPolicy, Model, PolicyDraft, PolicyRevision, PolicyTest, RateLimit,
                        RoutingDecision, SanitizeRule, Secret, Setting, ToolRule)
from app.services import audit, governance, guards, mcp_consent, policy, policy_review, ratelimit, secrets

router = APIRouter(tags=["gateway"])
P = "/api/v1/gateway"

```
**분석 / Analysis (Level 2)**
- L22 `P = "/api/v1/gateway"`: 경로 접두 상수 — f-string 으로 라우트 데코레이터에 사용.

```python path=app/api/gateway_admin.py lines=25-96
# ───────── Policies (SCR-H07·H08) ─────────
class PolicyIn(BaseModel):
    name: str
    description: str | None = None
    subject_type: str = Field("any", pattern="^(any|user|group|role|team|agent)$")
    subject: str = "*"
    target_type: str = Field(pattern="^(model|tool|agent)$")
    target: str = "*"
    effect: str = Field("allow", pattern="^(allow|deny)$")
    conditions: dict = {}
    args_schema: dict = {}
    data_class_routing: dict[str, str] = {}
    approval_required: bool = False
    priority: int = Field(100, ge=0, le=10000)
    enabled: bool = True


def policy_out(p: GatewayPolicy) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "subject_type": p.subject_type,
            "subject": p.subject, "target_type": p.target_type, "target": p.target, "effect": p.effect,
            "conditions": p.conditions, "args_schema": p.args_schema, "data_class_routing": p.data_class_routing,
            "approval_required": p.approval_required, "priority": p.priority, "enabled": p.enabled,
            "version": p.version, "updated_at": p.updated_at}


def _check_policy(body: PolicyIn) -> None:
    bad = [c for c in (body.conditions or {}).get("data_class", []) if c not in policy.DATA_CLASSES]
    if bad:
        raise bad_request(f"알 수 없는 데이터 분류: {bad}")
    if body.args_schema:
        try:
            jsonschema.Draft202012Validator.check_schema(body.args_schema)
        except jsonschema.SchemaError as exc:
            raise bad_request(f"args_schema 오류: {exc.message}") from exc


def _break_glass(p: Principal) -> None:
    """정책 직접 변경은 관리자 비상 경로만 (감사 기록). 그 외는 초안 → 시험 → 게시 요청 → 작성자 외 승인 (H08)."""
    if "admin" not in (p.roles or []):
        raise conflict("정책 변경은 초안 → 회귀 시험 → 게시 요청 → 작성자 외 승인으로 게시합니다 (POST /policies/drafts).",
                       code="review_required")


def _revision(db: Session, pol: GatewayPolicy, actor: str) -> None:
    db.add(PolicyRevision(id=new_id("prv"), policy_id=pol.id, version=pol.version, snapshot=policy_out(pol) | {
        "updated_at": None}, changed_by=actor))


@router.get(f"{P}/policies")
def list_policies(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db),
                  target_type: str | None = None):
    stmt = select(GatewayPolicy).order_by(GatewayPolicy.target_type, GatewayPolicy.priority, GatewayPolicy.name)
    if target_type:
        stmt = stmt.where(GatewayPolicy.target_type == target_type)
    stats = policy_review.stats(db)
    pending = db.scalars(select(PolicyDraft.policy_id).where(PolicyDraft.status == "pending")).all()
    return {"items": [policy_out(p) | {"decisions_24h": stats.get(p.id, {"allow": 0, "deny": 0}),
                                       "pending_review": p.id in pending} for p in db.scalars(stmt)],
            "engine": policy.settings.policy_engine, "engine_status": policy_review.engine_status()}


@router.post(f"{P}/policies", status_code=201)
def create_policy(body: PolicyIn, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    _break_glass(p)
    _check_policy(body)
    pol = GatewayPolicy(id=new_id("pol"), version=1, **body.model_dump())
    db.add(pol)
    db.flush()
    _revision(db, pol, p.sub)
    db.commit()
    audit.append("policy.create", actor=p.sub, target=pol.id, data=policy_out(pol) | {"updated_at": None, "break_glass": True})
    return policy_out(pol)
```
**분석 / Analysis (Level 2)**
- L26-47 `PolicyIn`·`policy_out`: 정책 입력(주체·대상·효과·조건·인자 스키마·재라우팅·승인·우선순위) — pydantic `pattern` 으로 열거 제약.
- L50-58 `_check_policy`: 데이터 분류·args_schema(JSON Schema 유효성) 검증.
- L61-65 `_break_glass`: **정책 직접 변경은 관리자 비상 경로만** — 그 외는 초안→심사→승인(H08).
- L68-96 `_revision`·목록·생성(비상): 변경 시 스냅샷 이력, 생성은 `break_glass` 감사.
**짚어볼 점 / Watch-outs**
- L61-65 비상 경로는 `admin` 역할만 — 일반 변경은 2인 검토를 강제한다(좋은 통제). admin 은 직접 변경하되 감사에 `break_glass` 로 남긴다.
- L54-57 `jsonschema.Draft202012Validator.check_schema` 로 스키마 자체를 검증 — 잘못된 스키마 저장 방지(좋음).

```python path=app/api/gateway_admin.py lines=99-152
@router.put(f"{P}/policies/{{policy_id}}")
def update_policy(policy_id: str, body: PolicyIn, p: Principal = Depends(require("policy:write")),
                  db: Session = Depends(get_db)):
    _break_glass(p)
    pol = db.get(GatewayPolicy, policy_id)
    if pol is None:
        raise not_found("policy")
    _check_policy(body)
    for k, v in body.model_dump().items():
        setattr(pol, k, v)
    pol.version += 1
    _revision(db, pol, p.sub)
    db.commit()
    audit.append("policy.update", actor=p.sub, target=pol.id, data={"version": pol.version, **body.model_dump(),
                                                                      "break_glass": True})
    return policy_out(pol)


@router.delete(f"{P}/policies/{{policy_id}}")
def delete_policy(policy_id: str, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    _break_glass(p)
    pol = db.get(GatewayPolicy, policy_id)
    if pol is None:
        raise not_found("policy")
    db.delete(pol)
    db.commit()
    audit.append("policy.delete", actor=p.sub, target=policy_id, data={"name": pol.name, "break_glass": True})
    return {"deleted": policy_id}


@router.get(f"{P}/policies/{{policy_id}}/versions")
def policy_versions(policy_id: str, _: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    rows = db.scalars(select(PolicyRevision).where(PolicyRevision.policy_id == policy_id)
                      .order_by(PolicyRevision.version.desc()))
    return {"items": [{"version": r.version, "changed_by": r.changed_by, "created_at": r.created_at,
                       "snapshot": r.snapshot} for r in rows]}


@router.get(f"{P}/policies/{{policy_id}}/diff")
def policy_diff(policy_id: str, from_: int = Query(..., alias="from"), to: int | None = None,
                _: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    def snap(v: int | None) -> dict:
        if v is None:
            pol = db.get(GatewayPolicy, policy_id)
            if pol is None:
                raise not_found("policy")
            return policy_out(pol)
        r = db.scalars(select(PolicyRevision).where(PolicyRevision.policy_id == policy_id,
                                                    PolicyRevision.version == v)).first()
        if r is None:
            raise not_found(f"policy version {v}")
        return r.snapshot
    return {"from": from_, "to": to, "changes": policy_review.diff(snap(from_), snap(to))}

```
**분석 / Analysis (Level 2)**
- L99-126 수정·삭제(비상): 버전 증가·이력.
- L129-152 버전 목록·diff.
**짚어볼 점 / Watch-outs**
- L107-109 `setattr(pol, k, v)` 로 전체 필드 덮어쓰기 — `PolicyIn` 에 없는 필드는 그대로(모델 필드와 입력 필드가 일치해야 함).

```python path=app/api/gateway_admin.py lines=154-193
class RollbackIn(BaseModel):
    version: int
    reason: str = ""
    confirm: bool = False


@router.post(f"{P}/policies/{{policy_id}}/rollback")
def rollback_policy(policy_id: str, body: RollbackIn, p: Principal = Depends(require("policy:write")),
                    db: Session = Depends(get_db)):
    """승인됐던 이전 버전으로 즉시 복원 (H08 ⑥) — 확인·사유 필수, 새 버전으로 기록."""
    if not body.confirm:
        raise bad_request("되돌리기는 확인(confirm=true)이 필요합니다.", code="confirm_required")
    if not body.reason.strip():
        raise bad_request("되돌리는 사유를 입력하세요 (MSG-003).", code="reason_required")
    pol = db.get(GatewayPolicy, policy_id)
    if pol is None:
        raise not_found("policy")
    r = db.scalars(select(PolicyRevision).where(PolicyRevision.policy_id == policy_id,
                                                PolicyRevision.version == body.version)).first()
    if r is None:
        raise not_found(f"policy version {body.version}")
    for k in policy_review.FIELDS:
        if k in r.snapshot:
            setattr(pol, k, r.snapshot[k])
    pol.version += 1
    _revision(db, pol, p.sub)
    db.commit()
    audit.append("policy.rollback", actor=p.sub, target=pol.id,
                 data={"to_version": body.version, "new_version": pol.version, "reason": body.reason})
    return policy_out(pol)


class RegoIn(BaseModel):
    policy: PolicyIn


@router.post(f"{P}/policies/rego")
def rego_preview(body: RegoIn, _: Principal = Depends(require("gateway:read"))):
    return {"rego": policy_review.to_rego(body.policy.model_dump())}

```
**분석 / Analysis (Level 2)**
- L154-183 롤백: 확인·사유 필수, 이전 버전 스냅샷으로 복원(새 버전 기록).
- L186-193 Rego 미리보기.
**짚어볼 점 / Watch-outs**
- L164-167 확인·사유를 요구하는 것은 좋은 통제 — 되돌리기도 감사에 사유가 남는다.

```python path=app/api/gateway_admin.py lines=195-281
# ───────── 정책 게시 심사 — 초안 → 회귀 시험 → 게시 요청(사유) → 작성자 외 승인 (SCR-H08) ─────────
class DraftIn(BaseModel):
    policy_id: str | None = None
    policy: PolicyIn | None = None
    delete: bool = False


def _draft(db: Session, draft_id: str) -> PolicyDraft:
    d = db.get(PolicyDraft, draft_id)
    if d is None:
        raise not_found("policy draft")
    return d


def draft_out(db: Session, d: PolicyDraft) -> dict:
    cur = db.get(GatewayPolicy, d.policy_id) if d.policy_id else None
    snap = {k: v for k, v in d.snapshot.items() if not k.startswith("_")}
    return {"id": d.id, "policy_id": d.policy_id, "policy": snap, "delete": bool(d.snapshot.get("_delete")),
            "base_version": d.snapshot.get("_base_version"), "current_version": cur.version if cur else None,
            "status": d.status, "reason": d.reason, "test_result": d.test_result, "created_by": d.created_by,
            "decided_by": d.decided_by, "decided_at": d.decided_at, "created_at": d.created_at,
            "diff": policy_review.diff(policy_out(cur) if cur else None, snap) if not d.snapshot.get("_delete") else [],
            "rego": policy_review.to_rego(snap) if snap else ""}


@router.get(f"{P}/policies/drafts")
def list_drafts(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db),
                status: str | None = None):
    stmt = select(PolicyDraft).order_by(PolicyDraft.created_at.desc())
    if status:
        stmt = stmt.where(PolicyDraft.status == status)
    return {"items": [draft_out(db, d) for d in db.scalars(stmt).all()[:200]]}


@router.post(f"{P}/policies/drafts", status_code=201)
def create_draft(body: DraftIn, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    cur = db.get(GatewayPolicy, body.policy_id) if body.policy_id else None
    if body.policy_id and cur is None:
        raise not_found("policy")
    if body.policy is not None:
        _check_policy(body.policy)
        snap = body.policy.model_dump()
    elif cur is not None:
        snap = {k: v for k, v in policy_out(cur).items() if k in policy_review.FIELDS}
    else:
        raise bad_request("policy 또는 policy_id 가 필요합니다.")
    if body.delete:
        if cur is None:
            raise bad_request("삭제 초안에는 policy_id 가 필요합니다.")
        snap = {**snap, "_delete": True}
    d = PolicyDraft(id=new_id("pdr"), policy_id=body.policy_id, created_by=p.sub,
                    snapshot={**snap, "_base_version": cur.version if cur else None})
    db.add(d)
    db.commit()
    audit.append("policy.draft", actor=p.sub, target=d.id, data={"policy_id": body.policy_id, "delete": body.delete})
    return draft_out(db, d)


@router.get(f"{P}/policies/drafts/{{draft_id}}")
def get_draft(draft_id: str, _: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    return draft_out(db, _draft(db, draft_id))


@router.put(f"{P}/policies/drafts/{{draft_id}}")
def update_draft(draft_id: str, body: PolicyIn, p: Principal = Depends(require("policy:write")),
                 db: Session = Depends(get_db)):
    d = _draft(db, draft_id)
    if d.status not in ("draft", "rejected"):
        raise conflict(f"{d.status} 상태 초안은 고칠 수 없습니다.", code="invalid_state")
    _check_policy(body)
    d.snapshot = {**body.model_dump(), "_base_version": d.snapshot.get("_base_version")}
    d.status, d.test_result = "draft", {}  # 수정하면 시험을 다시 통과해야 한다
    db.commit()
    return draft_out(db, d)


@router.post(f"{P}/policies/drafts/{{draft_id}}/test")
def test_draft(draft_id: str, _: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    d = _draft(db, draft_id)
    if d.snapshot.get("_delete"):
        ov_snap = {**d.snapshot, "enabled": False}
        res = policy_review.run_tests(db, PolicyDraft(id=d.id, policy_id=d.policy_id, snapshot=ov_snap))
    else:
        res = policy_review.run_tests(db, d)
    d.test_result = res
    db.commit()
    return draft_out(db, d)
```
**분석 / Analysis (Level 2)**
- L196-250 초안 — 생성(기준 버전 기록)·조회·수정(수정하면 시험 재통과 필요, L266).
- L271-281 초안 시험(회귀 케이스 + 최근 실제 요청 재평가).
**짚어볼 점 / Watch-outs**
- [위험:중간 · 2인 검토 우회] L258-268 `update_draft` 는 `created_by` 를 바꾸지 않는다 — 작성자 A 가 만든 초안을 B 가 수정해도 L314 승인 시 "작성자 == 승인자" 검사가 **A 기준**이다. 따라서 B 가 초안을 실질적으로 작성(수정)하고 자신이 승인할 수 있다(직무 분리 우회). "마지막 수정자" 를 기록하고 승인자 ≠ 모든 기여자로 판정해야 한다(`app/models.py:1013-1025` 리뷰 참조).

```python path=app/api/gateway_admin.py lines=284-355
class SubmitIn(BaseModel):
    reason: str = ""


@router.post(f"{P}/policies/drafts/{{draft_id}}/submit")
def submit_draft(draft_id: str, body: SubmitIn, p: Principal = Depends(require("policy:write")),
                 db: Session = Depends(get_db)):
    d = _draft(db, draft_id)
    if d.status not in ("draft", "rejected"):
        raise conflict(f"{d.status} 상태 초안은 게시 요청할 수 없습니다.", code="invalid_state")
    if not body.reason.strip():
        raise bad_request("게시 사유를 입력하세요 (MSG-003) — 승인자에게 전달되고 감사에 남습니다.", code="reason_required")
    if not (d.test_result or {}).get("passed") or not (d.test_result or {}).get("tested_at"):
        raise conflict("회귀 시험을 통과해야 게시 요청할 수 있습니다.", code="tests_required")
    d.status, d.reason = "pending", body.reason.strip()
    db.commit()
    audit.append("policy.submit", actor=p.sub, target=d.id, data={"policy_id": d.policy_id, "reason": d.reason})
    return draft_out(db, d)


class DecideIn(BaseModel):
    note: str = ""


@router.post(f"{P}/policies/drafts/{{draft_id}}/approve")
def approve_draft(draft_id: str, body: DecideIn, p: Principal = Depends(require("policy:write")),
                  db: Session = Depends(get_db)):
    d = _draft(db, draft_id)
    if d.status != "pending":
        raise conflict(f"{d.status} 상태 초안은 승인할 수 없습니다.", code="invalid_state")
    if d.created_by == p.sub:
        raise forbidden("작성자는 자신의 정책 변경을 승인할 수 없습니다 (2인 검토).", code="self_approval")
    cur = db.get(GatewayPolicy, d.policy_id) if d.policy_id else None
    if d.policy_id and cur is None:
        raise conflict("대상 정책이 이미 삭제됐습니다.", code="stale_draft")
    if cur is not None and d.snapshot.get("_base_version") not in (None, cur.version):
        raise conflict(f"초안 작성 후 정책이 v{cur.version} 으로 바뀌었습니다 — 새 초안으로 다시 시험하세요.", code="stale_draft")
    snap = {k: v for k, v in d.snapshot.items() if k in policy_review.FIELDS}
    if d.snapshot.get("_delete"):
        db.delete(cur)
        action, target = "policy.delete", cur.id
    elif cur is None:
        cur = GatewayPolicy(id=new_id("pol"), version=1, **snap)
        db.add(cur)
        db.flush()
        _revision(db, cur, d.created_by or p.sub)
        d.policy_id, action, target = cur.id, "policy.create", cur.id
    else:
        for k, v in snap.items():
            setattr(cur, k, v)
        cur.version += 1
        _revision(db, cur, d.created_by or p.sub)
        action, target = "policy.update", cur.id
    d.status, d.decided_by, d.decided_at = "approved", p.sub, utcnow()
    db.commit()
    audit.append(action, actor=p.sub, target=target, data={"draft": d.id, "author": d.created_by, "reason": d.reason,
                                                           "note": body.note, "tests": len((d.test_result or {}).get("cases", []))})
    return draft_out(db, d)


@router.post(f"{P}/policies/drafts/{{draft_id}}/reject")
def reject_draft(draft_id: str, body: DecideIn, p: Principal = Depends(require("policy:write")),
                 db: Session = Depends(get_db)):
    d = _draft(db, draft_id)
    if d.status != "pending":
        raise conflict(f"{d.status} 상태 초안은 반려할 수 없습니다.", code="invalid_state")
    if not body.note.strip():
        raise bad_request("반려 사유를 입력하세요 (MSG-003).", code="reason_required")
    d.status, d.decided_by, d.decided_at = "rejected", p.sub, utcnow()
    db.commit()
    audit.append("policy.reject", actor=p.sub, target=d.id, data={"note": body.note})
    return draft_out(db, d)
```
**분석 / Analysis (Level 2)**
- L288-301 게시 요청: 사유 필수, 회귀 시험 통과 필수.
- L308-341 승인: pending 만, **작성자 본인 승인 금지**(2인 검토, L314-315), 기준 버전이 바뀌었으면 stale 거부(L319-320), 적용(삭제·생성·수정) 후 approved.
- L344-355 거부: 사유 필수.
**짚어볼 점 / Watch-outs**
- L314-315 `created_by == p.sub` 거부는 L258 의 수정자 미기록과 결합해 우회 가능(위 참조).
- L319-320 `_base_version` 으로 낙관적 충돌 감지 — 초안 작성 후 정책이 바뀌면 재시험 요구(좋음).
- L337 승인 트랜잭션에서 정책 적용·초안 상태 변경을 한 번에 커밋 — 원자적.

```python path=app/api/gateway_admin.py lines=358-417
class PolicyTestIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    input: dict
    expect: str = Field(pattern="^(allow|deny)$")


@router.get(f"{P}/policy-tests")
def list_policy_tests(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    return {"items": [{"id": t.id, "name": t.name, "input": t.input, "expect": t.expect, "created_by": t.created_by}
                      for t in db.scalars(select(PolicyTest).order_by(PolicyTest.created_at))]}


@router.post(f"{P}/policy-tests", status_code=201)
def create_policy_test(body: PolicyTestIn, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    if body.input.get("kind") not in ("model", "tool", "agent") or not body.input.get("target"):
        raise bad_request("input 에 kind(model|tool|agent)·target 이 필요합니다.")
    t = PolicyTest(id=new_id("ptc"), name=body.name, input=body.input, expect=body.expect, created_by=p.sub)
    db.add(t)
    db.commit()
    return {"id": t.id, "name": t.name, "input": t.input, "expect": t.expect, "created_by": t.created_by}


@router.delete(f"{P}/policy-tests/{{test_id}}")
def delete_policy_test(test_id: str, _: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    t = db.get(PolicyTest, test_id)
    if t is None:
        raise not_found("policy test")
    db.delete(t)
    db.commit()
    return {"deleted": test_id}


class SimulateIn(BaseModel):
    kind: str = Field(pattern="^(model|tool|agent)$")
    target: str
    data_class: str = "internal"
    args: dict = {}
    agent: str | None = None
    attrs: dict = {}
    subject: dict = Field(default_factory=dict, description="{sub, roles, groups, team} — 비우면 호출자")


@router.post(f"{P}/policies/simulate")
def simulate(body: SimulateIn, p: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    subj = p if not body.subject else Principal(sub=body.subject.get("sub", "sim"), kind="user",
                                           roles=body.subject.get("roles", ["user"]),
                                           groups=body.subject.get("groups", []), team=body.subject.get("team"))
    attrs = dict(body.attrs)
    if body.kind == "model" and "category" not in attrs:
        m = db.get(Model, body.target)
        if m:
            attrs["category"] = m.category
    if body.kind == "tool" and "risk" not in attrs and "." in body.target:
        srv, tool = body.target.split(".", 1)
        rule = db.scalars(select(ToolRule).where(ToolRule.server_id == srv, ToolRule.tool == tool)).first()
        if rule:
            attrs["risk"] = rule.risk
    dec = policy.evaluate(db, policy.PolicyInput(body.kind, body.target, subj, body.data_class, body.args,
                                                 body.agent, attrs))
    return {"input": {**body.model_dump(), "attrs": attrs, "subject": subj.snapshot()}, "decision": dec.as_dict()}
```
**분석 / Analysis (Level 2)**
- L358-387 정책 시험 케이스 CRUD.
- L390-417 시뮬레이터: 가상 주체·속성으로 정책 판정 미리보기.
**짚어볼 점 / Watch-outs**
- L401-404 `subject` 를 비우면 호출자, 아니면 임의 주체(sub·roles·groups·team)로 시뮬레이션 — `gateway:read` 권한자가 임의 역할의 판정을 볼 수 있다(정보성, 실제 발급 아님 — 수용).

```python path=app/api/gateway_admin.py lines=420-502
# ───────── Rate limits · 예산 (SCR-H09·K07) ─────────
class LimitIn(BaseModel):
    name: str
    scope_type: str = Field(pattern="^(any|user|team|agent|client_key)$")
    scope: str = "*"
    target_type: str = Field("any", pattern="^(any|model|tool|agent)$")
    target: str = "*"
    rps: float | None = Field(None, gt=0)
    burst: int | None = Field(None, ge=1)
    tokens_per_min: int | None = Field(None, ge=1)
    budget_usd: Decimal | None = Field(None, ge=0)
    period: str = Field("month", pattern="^(day|month)$")
    alert_pct: int = Field(80, ge=1, le=100)
    on_exceed: str = Field("block", pattern="^(block|fallback|warn)$")  # warn = 경고만 (SCR-H09)
    fallback_model: str | None = None
    enabled: bool = True


def limit_out(r: RateLimit) -> dict:
    return {"id": r.id, "name": r.name, "scope_type": r.scope_type, "scope": r.scope, "target_type": r.target_type,
            "target": r.target, "rps": float(r.rps) if r.rps is not None else None, "burst": r.burst,
            "tokens_per_min": r.tokens_per_min, "budget_usd": float(r.budget_usd) if r.budget_usd is not None else None,
            "period": r.period, "alert_pct": r.alert_pct, "on_exceed": r.on_exceed, "fallback_model": r.fallback_model,
            "enabled": r.enabled}


@router.get(f"{P}/limits")
def list_limits(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    return {"items": [limit_out(r) for r in db.scalars(select(RateLimit).order_by(RateLimit.name))],
            "backend": ratelimit.backend_status()}


@router.post(f"{P}/limits", status_code=201)
def create_limit(body: LimitIn, p: Principal = Depends(require("limits:write")), db: Session = Depends(get_db)):
    if body.on_exceed == "fallback" and not body.fallback_model:
        raise bad_request("fallback 에는 fallback_model 이 필요합니다.")
    if not (body.rps or body.tokens_per_min or body.budget_usd is not None):
        raise bad_request("rps·tokens_per_min·budget_usd 중 하나 이상 필요합니다.")
    row = RateLimit(id=new_id("lim"), **body.model_dump())
    db.add(row)
    db.commit()
    audit.append("limit.create", actor=p.sub, target=row.id, data=limit_out(row))
    return limit_out(row)


@router.put(f"{P}/limits/{{limit_id}}")
def update_limit(limit_id: str, body: LimitIn, p: Principal = Depends(require("limits:write")),
                 db: Session = Depends(get_db)):
    row = db.get(RateLimit, limit_id)
    if row is None:
        raise not_found("limit")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    db.commit()
    ratelimit.clear_cache()
    audit.append("limit.update", actor=p.sub, target=row.id, data=limit_out(row))
    return limit_out(row)


@router.delete(f"{P}/limits/{{limit_id}}")
def delete_limit(limit_id: str, p: Principal = Depends(require("limits:write")), db: Session = Depends(get_db)):
    row = db.get(RateLimit, limit_id)
    if row is None:
        raise not_found("limit")
    db.delete(row)
    db.commit()
    audit.append("limit.delete", actor=p.sub, target=limit_id)
    return {"deleted": limit_id}


@router.get(f"{P}/limits/status")
def limits_status(p: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """예산 한도별 현재 사용액 (FinOps)."""
    out = []
    for lim in db.scalars(select(RateLimit).where(RateLimit.enabled.is_(True), RateLimit.budget_usd.is_not(None))):
        scopes = [lim.scope] if lim.scope not in ("*", "per-subject") else []
        for sv in scopes:
            used = ratelimit.spend(db, lim.scope_type, sv, lim.period, use_cache=False)
            out.append({"limit_id": lim.id, "name": lim.name, "scope_type": lim.scope_type, "scope": sv,
                        "period": lim.period, "used_usd": float(used), "budget_usd": float(lim.budget_usd),
                        "pct": round(float(used / lim.budget_usd * 100), 1) if lim.budget_usd else None})
    return {"items": out}

```
**분석 / Analysis (Level 2)**
- L421-443 `LimitIn`·`limit_out`: 한도(rps·토큰·예산·주기·초과 동작).
- L446-488 CRUD: fallback 에는 fallback_model 필수, 최소 한 지표 필요.
- L490-501 예산 사용 현황.
**짚어볼 점 / Watch-outs**
- L495 `scope not in ("*", "per-subject")` 인 것만 현황 — 와일드카드·주체별 한도는 집계에서 제외(주체별은 조회 시점에 알 수 없으므로).

```python path=app/api/gateway_admin.py lines=504-548
# ───────── 시크릿 저장소 (SCR-H06) ─────────
class SecretIn(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9._/-]{2,120}$")
    value: str = Field(min_length=1, repr=False)
    kind: str = Field("api_key", pattern="^(api_key|oauth_client|token|password|certificate)$")  # certificate = PEM(mTLS)
    description: str | None = None


@router.get(f"{P}/secrets")
def list_secrets(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    return {"items": [secrets.masked(s) for s in db.scalars(select(Secret).order_by(Secret.name))]}


@router.post(f"{P}/secrets", status_code=201)
def create_secret(body: SecretIn, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    if db.scalars(select(Secret).where(Secret.name == body.name)).first():
        raise bad_request("같은 이름의 시크릿이 있습니다. 회전(rotate)을 사용하세요.", code="exists")
    row = secrets.put(db, body.name, body.value, body.kind, body.description)
    db.commit()
    audit.append("secret.create", actor=p.sub, target=row.name, data={"kind": row.kind})
    return secrets.masked(row)


@router.post(f"{P}/secrets/{{secret_id}}/rotate")
def rotate_secret(secret_id: str, value: str = Body(..., embed=True), p: Principal = Depends(require("gateway:write")),
                  db: Session = Depends(get_db)):
    row = db.get(Secret, secret_id)
    if row is None:
        raise not_found("secret")
    secrets.put(db, row.name, value)
    db.commit()
    audit.append("secret.rotate", actor=p.sub, target=row.name)
    return secrets.masked(row)


@router.delete(f"{P}/secrets/{{secret_id}}")
def delete_secret(secret_id: str, p: Principal = Depends(require("gateway:write")), db: Session = Depends(get_db)):
    row = db.get(Secret, secret_id)
    if row is None:
        raise not_found("secret")
    db.delete(row)
    db.commit()
    audit.append("secret.delete", actor=p.sub, target=row.name)
    return {"deleted": secret_id}

```
**분석 / Analysis (Level 2)**
- L505-524 시크릿 생성(`gateway:write`): 이름 패턴 제약, 중복 시 회전 안내, 값은 `repr=False`.
- L527-547 회전·삭제.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 간접] L517-524 시크릿 생성·목록(`list_secrets`, `gateway:read`)은 **값을 노출하지 않지만 이름을 노출**한다(`secrets.masked`). 이름을 알면 MCP·A2A `auth.secret_ref` 로 지정해 게이트웨이가 외부로 보내게 할 수 있다(상세 `app/services/mcp_gateway.py` 리뷰). 시크릿은 `gateway:read` 로 이름이 보이고 `secret_ref` 지정은 `mcp:write`/`a2a:write` 라 두 권한의 조합이 유출 경로다.
- L507 `value: str = Field(min_length=1, repr=False)` — 로그·에러에 값이 찍히지 않게(좋음).

```python path=app/api/gateway_admin.py lines=550-592
# ───────── Security 가드 (SCR-H05) ─────────
@router.get(f"{P}/security")
def get_security(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    return guards.security_settings(db)


@router.put(f"{P}/security")
def put_security(body: dict, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    allowed = {"pii_input": ("mask", "block", "allow"), "pii_output": ("mask", "allow"),
               "injection_input": ("block", "flag", "allow")}
    for k, opts in allowed.items():
        if k in body and body[k] not in opts:
            raise bad_request(f"{k} 는 {opts} 중 하나여야 합니다.")
    if "mtls" in body:  # 업스트림 mTLS 정책 (FR-G02) — 하류(인바운드) mTLS 는 TLS 종단(Ingress·게이트웨이 프록시)에서
        m = body["mtls"]
        if not isinstance(m, dict) or m.get("upstream", "optional") not in ("disabled", "optional", "required"):
            raise bad_request("mtls.upstream 은 disabled·optional·required 중 하나여야 합니다.", code="invalid_mtls")
    cur = guards.security_settings(db)
    cur.update({k: v for k, v in body.items() if k in {*allowed, "tool_response_sanitize", "mtls"}})
    row = db.get(Setting, "gateway.security")
    if row is None:
        db.add(Setting(key="gateway.security", value=cur, updated_by=p.sub))
    else:
        row.value, row.updated_by = cur, p.sub
    db.commit()
    from app.services import tls
    tls.invalidate()  # mTLS 정책 즉시 반영 (다른 워커는 최대 MODE_TTL_S 뒤)
    audit.append("security.update", actor=p.sub, target="gateway.security", data=cur)
    return cur


@router.post(f"{P}/security/scan")
def scan(text: str = Body(..., embed=True), mode: str = Body("input", embed=True),
         _: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    """가드 시험 — 입력/출력/도구응답 텍스트를 실제 가드로 검사."""
    if mode == "tool_response":
        r = guards.sanitize_tool_text(db, text)
    elif mode == "output":
        r = guards.scan_output(db, text)
    else:
        r = guards.scan_input(db, text)
    return {"text": r.text, "blocked": r.blocked, "reason": r.reason, "findings": [f.as_dict() for f in r.findings]}

```
**분석 / Analysis (Level 2)**
- L551-578 보안 가드 설정(`policy:write`): PII·인젝션·mTLS 정책 열거 검증 후 저장, mTLS 즉시 반영.
- L581-591 가드 시험(입력/출력/도구응답 텍스트 검사).
**짚어볼 점 / Watch-outs**
- L557 `put_security` 는 `policy:write`(보안 담당·관리자) — 가드 완화를 보안 권한으로 제한(좋음).
- L568 알 수 없는 키는 무시(화이트리스트 업데이트) — 오타가 조용히 무시된다.

```python path=app/api/gateway_admin.py lines=594-702
# ───────── Tool & Agent Governance (SCR-H10·H11) ─────────
class ToolRuleIn(BaseModel):
    server_id: str
    tool: str
    allowed: bool = True
    risk: str = Field("low", pattern="^(low|medium|high)$")
    approval_required: bool = False
    approver: str = Field("requester", pattern="^(requester|approvers)$")
    args_schema: dict = {}
    args_mode: str = Field("strict", pattern="^(strict|warn)$")  # SCR-H11 — 위반 차단 | 경고만
    sanitize: bool = True
    description: str | None = None


def rule_out(r: ToolRule) -> dict:
    return {"id": r.id, "server_id": r.server_id, "tool": r.tool, "qualified": f"{r.server_id}.{r.tool}",
            "allowed": r.allowed, "risk": r.risk, "approval_required": r.approval_required, "approver": r.approver,
            "args_schema": r.args_schema, "args_mode": r.args_mode or "strict", "sanitize": r.sanitize,
            "description": r.description, "updated_at": r.updated_at}


@router.get(f"{P}/governance/tools")
def list_tool_rules(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db),
                    server_id: str | None = None):
    stmt = select(ToolRule).order_by(ToolRule.server_id, ToolRule.tool)
    if server_id:
        stmt = stmt.where(ToolRule.server_id == server_id)
    return {"items": [rule_out(r) for r in db.scalars(stmt)]}


@router.put(f"{P}/governance/tools")
def upsert_tool_rule(body: ToolRuleIn, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    if body.args_schema:
        try:
            jsonschema.Draft202012Validator.check_schema(body.args_schema)
        except jsonschema.SchemaError as exc:
            raise bad_request(f"args_schema 오류: {exc.message}") from exc
    row = db.scalars(select(ToolRule).where(ToolRule.server_id == body.server_id, ToolRule.tool == body.tool)).first()
    if row is None:
        row = ToolRule(id=new_id("tr"), **body.model_dump())
        db.add(row)
    else:
        for k, v in body.model_dump().items():
            setattr(row, k, v)
    db.commit()
    audit.append("governance.tool.upsert", actor=p.sub, target=f"{body.server_id}.{body.tool}", data=body.model_dump())
    return rule_out(row)


@router.delete(f"{P}/governance/tools/{{rule_id}}")
def delete_tool_rule(rule_id: str, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    row = db.get(ToolRule, rule_id)
    if row is None:
        raise not_found("tool rule")
    db.delete(row)
    db.commit()
    audit.append("governance.tool.delete", actor=p.sub, target=f"{row.server_id}.{row.tool}")
    return {"deleted": rule_id}


class SanitizeIn(BaseModel):
    name: str
    pattern: str
    applies_to: str = Field("tool_response", pattern="^(tool_response|input|output)$")
    action: str = Field("strip", pattern="^(strip|mask|block)$")
    enabled: bool = True


@router.get(f"{P}/governance/sanitize-rules")
def list_sanitize(_: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db)):
    return {"items": [{"id": r.id, "name": r.name, "pattern": r.pattern, "applies_to": r.applies_to,
                       "action": r.action, "enabled": r.enabled} for r in db.scalars(select(SanitizeRule))]}


@router.post(f"{P}/governance/sanitize-rules", status_code=201)
def create_sanitize(body: SanitizeIn, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    try:
        re.compile(body.pattern)
    except re.error as exc:
        raise bad_request(f"정규식 오류: {exc}") from exc
    row = SanitizeRule(id=new_id("san"), **body.model_dump())
    db.add(row)
    db.commit()
    audit.append("governance.sanitize.create", actor=p.sub, target=row.name, data=body.model_dump())
    return {"id": row.id, **body.model_dump()}


@router.delete(f"{P}/governance/sanitize-rules/{{rule_id}}")
def delete_sanitize(rule_id: str, p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    row = db.get(SanitizeRule, rule_id)
    if row is None:
        raise not_found("sanitize rule")
    db.delete(row)
    db.commit()
    audit.append("governance.sanitize.delete", actor=p.sub, target=row.name)
    return {"deleted": rule_id}


@router.put(f"{P}/governance/agents/{{agent_id}}/trust")
def set_agent_trust(agent_id: str,
                    trust_tier: str = Body(..., embed=True, pattern="^(verified|trusted|untrusted|blocked)$"),
                    p: Principal = Depends(require("policy:write")), db: Session = Depends(get_db)):
    agent = db.get(A2AAgent, agent_id)
    if agent is None:
        raise not_found("a2a agent")
    old, agent.trust_tier = agent.trust_tier, trust_tier
    db.commit()
    audit.append("governance.agent.trust", actor=p.sub, target=agent_id, data={"from": old, "to": trust_tier})
    return {"id": agent.id, "trust_tier": agent.trust_tier}
```
**분석 / Analysis (Level 2)**
- L595-651 도구 규칙 CRUD(`policy:write`): args_schema 검증, upsert.
- L654-689 소독 규칙 CRUD: 정규식 컴파일 검증.
- L692-702 에이전트 신뢰 등급(`policy:write`).
**짚어볼 점 / Watch-outs**
- L670-672 `re.compile(body.pattern)` 로 정규식 유효성만 확인 — **ReDoS(파국적 역추적) 패턴은 걸러지지 않는다**. 이 규칙은 도구 응답·입력·출력에 적용되므로(`guards`), 악의적·실수 정규식이 요청마다 CPU 를 소모시킬 수 있다. `policy:write` 권한자만 만들지만 타임아웃·복잡도 제한이 필요하다.
- L631-637 도구 규칙 upsert — `(server_id, tool)` 유일. 전체 필드 덮어쓰기.

```python path=app/api/gateway_admin.py lines=705-799
# ───────── 승인 (SCR-C04·H12) ─────────
def approval_out(a: Approval) -> dict:
    status = "expired" if a.status == "pending" and a.expires_at and a.expires_at < utcnow() else a.status
    return {"id": a.id, "kind": a.kind, "status": status, "requester": a.requester_sub, "approver": a.approver,
            "target": a.target, "server_id": a.server_id, "tool": a.tool, "args": a.args, "risk": a.risk,
            "reason": a.reason, "policy_ref": a.policy_ref, "session_id": a.session_id, "run_id": a.run_id,
            "decided_by": a.decided_by, "decided_at": a.decided_at, "decision_note": a.decision_note,
            "expires_at": a.expires_at, "created_at": a.created_at, "step_id": a.step_id,
            "payload": a.payload, "result": a.result, "delegated_to": a.delegated_to,
            "extensions": a.extensions or 0, "max_extensions": governance.MAX_EXTENSIONS,
            "extend_step_s": governance.extend_step_s(a)}


@router.get("/api/v1/approvals")
def list_approvals(p: Principal = Depends(current_principal), db: Session = Depends(get_db),
                   status: str | None = None, run_id: str | None = None, limit: int = Query(100, le=500)):
    """승인자는 전체(직무 분리 대상 포함), 일반 사용자는 본인 요청만 — 팀 간 누출 방지."""
    stmt = select(Approval).order_by(Approval.created_at.desc()).limit(limit)
    if not p.has("approvals:decide"):
        stmt = stmt.where(or_(Approval.requester_sub == p.sub, Approval.delegated_to == p.sub))
    if status:
        stmt = stmt.where(Approval.status == status)
    if run_id:
        stmt = stmt.where(Approval.run_id == run_id)
    return {"items": [approval_out(a) for a in db.scalars(stmt)]}


def _approval(db: Session, p: Principal, approval_id: str) -> Approval:
    ap = db.get(Approval, approval_id)
    if ap is None or (not p.has("approvals:decide") and p.sub not in (ap.requester_sub, ap.delegated_to)):
        raise not_found("approval")
    return ap


@router.get("/api/v1/approvals/{approval_id}")
def get_approval(approval_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    ap = _approval(db, p, approval_id)
    return approval_out(ap) | ({"context": governance.approval_context(db, ap)} if ap.kind == "tool_call" else {})


@router.post("/api/v1/approvals/{approval_id}/approve")
def approve(approval_id: str, note: str | None = Body(None, embed=True), remember: bool = Body(False, embed=True),
            p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """승인 (SCR-C04 ⑧) — remember=true 면 이 세션 동안 같은 도구를 다시 묻지 않는다(고위험 제외)."""
    ap = _approval(db, p, approval_id)
    if ap.kind == "elicitation":
        raise bad_request("입력 요청은 내용과 함께 응답하세요 — POST /api/v1/approvals/{id}/consent", code="consent_required")
    ap = governance.decide(db, ap, p, True, note, remember=remember)
    _on_decision(ap)
    return approval_out(ap)


@router.post("/api/v1/approvals/{approval_id}/reject")
def reject(approval_id: str, note: str | None = Body(None, embed=True), p: Principal = Depends(current_principal),
           db: Session = Depends(get_db)):
    ap = _approval(db, p, approval_id)
    if ap.status == "pending" and ap.approver == "approvers" and not (note or "").strip():
        raise bad_request("거부 사유를 입력하세요 (MSG-003) — 요청자에게 전달되고 감사에 남습니다.", code="reason_required")
    ap = governance.decide(db, ap, p, False, note)
    _on_decision(ap)
    return approval_out(ap)


@router.post("/api/v1/approvals/{approval_id}/extend")
def extend_approval(approval_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """시간 연장 (D14-③, WCAG 2.2.1) — 동의·승인 기한을 기본 시간만큼 늘린다 (최대 10회 · 감사 approval.extend)."""
    return approval_out(governance.extend(db, _approval(db, p, approval_id), p))


class ConsentIn(BaseModel):
    """SCR-C08 동의 카드 — sampling: allow(편집 messages·review)|deny, 검토 단계 send(text 수정)|deny ·
    elicitation: accept(content)|decline|cancel."""
    decision: str = Field(pattern="^(allow|deny|send|accept|decline|cancel)$")
    messages: list[dict] | None = None
    review: bool = False
    text: str | None = Field(None, max_length=20000)
    content: dict | None = None
    note: str | None = None


@router.post("/api/v1/approvals/{approval_id}/consent")
def consent(approval_id: str, body: ConsentIn, p: Principal = Depends(current_principal),
            db: Session = Depends(get_db)):
    ap = mcp_consent.decide(db, _approval(db, p, approval_id), p, body.decision, messages=body.messages,
                            review=body.review, text=body.text, content=body.content, note=body.note)
    return approval_out(ap)


APPROVAL_HOOKS: list = []  # 오케스트레이터가 승인 결과로 실행을 재개하도록 등록


def _on_decision(ap: Approval) -> None:
    for hook in APPROVAL_HOOKS:
        hook(ap)

```
**분석 / Analysis (Level 2)**
- L706-729 승인 목록·조회: **승인자는 전체, 일반 사용자는 본인·위임만**(팀 간 누출 방지).
- L745-771 승인/거부/연장: elicitation 은 consent 로(L750-751), approvers 거부는 사유 필수, 결정 후 오케스트레이터 훅(`_on_decision`).
- L774-790 동의(sampling·elicitation 결정).
- L793-799 `APPROVAL_HOOKS`·`_on_decision`: 오케스트레이터가 등록(`app/main.py:140`).
**짚어볼 점 / Watch-outs**
- L732-735 `_approval` 은 승인자·요청자·위임 대상만 조회 가능 — 객체 수준 인가가 제대로 적용된 예(다른 API 와 대조).
- L796-798 훅 호출은 예외를 잡지 않는다 — 훅(오케스트레이터 재개)이 실패하면 승인 API 응답이 500 이 된다(승인은 이미 커밋됨 → 상태 불일치 가능).

```python path=app/api/gateway_admin.py lines=801-835
# ───────── 라우팅 결정 로그 (SCR-H04) ─────────
@router.get(f"{P}/routing/decisions")
def routing_decisions(p: Principal = Depends(require("gateway:read")), db: Session = Depends(get_db),
                      kind: str | None = None, trace_id: str | None = None, fallback: bool | None = None,
                      q: str | None = None, status: str | None = None, subject: str | None = None,
                      window_minutes: int | None = Query(None, ge=1, le=60 * 24 * 90),
                      format: str = Query("json", pattern="^(json|jsonl)$"), limit: int = Query(100, le=10000)):
    """라우팅 결정 로그 (SCR-H04) — 기간·종류·상태·폴백·주체·검색 필터, JSONL 내보내기(감사)."""
    stmt = select(RoutingDecision).order_by(RoutingDecision.ts.desc()).limit(limit)
    if window_minutes:
        stmt = stmt.where(RoutingDecision.ts >= utcnow() - timedelta(minutes=window_minutes))
    if status:
        stmt = stmt.where(RoutingDecision.status == status)
    if subject:
        stmt = stmt.where(RoutingDecision.subject == subject)
    if kind:
        stmt = stmt.where(RoutingDecision.kind == kind)
    if trace_id:
        stmt = stmt.where(RoutingDecision.trace_id == trace_id)
    if fallback is not None:
        stmt = stmt.where(RoutingDecision.fallback_used.is_(fallback))
    if q:
        stmt = stmt.where(or_(RoutingDecision.requested.like(f"%{q}%"), RoutingDecision.chosen.like(f"%{q}%")))
    items = [{"id": r.id, "ts": r.ts, "trace_id": r.trace_id, "kind": r.kind, "subject": r.subject,
              "requested": r.requested, "chosen": r.chosen, "candidates": r.candidates, "attempts": r.attempts,
              "reason": r.reason, "fallback_used": r.fallback_used, "status": r.status} for r in db.scalars(stmt)]
    if format == "jsonl":
        import json as _json
        from fastapi.responses import PlainTextResponse
        audit.append("gateway.routing.export", actor=p.sub, data={"rows": len(items), "kind": kind, "status": status,
                                                                  "window_minutes": window_minutes})
        return PlainTextResponse("\n".join(_json.dumps(i, ensure_ascii=False, default=str) for i in items) + "\n",
                                 media_type="application/x-ndjson",
                                 headers={"Content-Disposition": 'attachment; filename="routing-decisions.jsonl"'})
    return {"items": items}
```
**분석 / Analysis (Level 2)**
- L802-835 라우팅 결정 로그(`gateway:read`): 기간·종류·상태·폴백·주체·검색 필터, JSONL 내보내기(감사 기록).
**짚어볼 점 / Watch-outs**
- L823 `RoutingDecision.requested.like(f"%{q}%")` — `q` 를 LIKE 패턴에 직접(사용자 입력) 넣는다. SQL 인젝션은 아니지만(파라미터 바인딩) `%`·`_` 가 와일드카드로 해석되고, 긴 패턴은 풀스캔을 유발한다. 이스케이프·길이 제한 권장.

## 파일 평가 / File Assessment
- 강점: (1) 정책 2인 검토·비상 경로 감사·롤백 사유·낙관적 충돌 감지. (2) 승인 조회의 객체 수준 인가(요청자·위임·승인자). (3) 시크릿 값 미노출·스키마/정규식 유효성 검증.
- 위험: (1) [중간] 초안 수정자 미기록으로 2인 검토 우회. (2) [중간] 소독 규칙 ReDoS·시크릿 이름 노출(secret_ref 유출 경로). (3) [낮음] 훅 예외 전파·LIKE 패턴 미이스케이프.
- 개선 권고(영향순): (1) `PolicyDraft` 수정자 기록 + 승인자 ≠ 기여자. (2) 정규식 복잡도·타임아웃 제한. (3) 훅 예외 격리·LIKE 이스케이프.
- 재읽기 권고 레벨: 3 — 통제가 대체로 탄탄해 2인 검토 경로만 집중 재검토.
