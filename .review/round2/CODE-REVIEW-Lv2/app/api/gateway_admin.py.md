> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [api_Abstract.md](api_Abstract.md) · 1차 리뷰: [gateway_admin.py.md](../../../../CODE-REVIEW-Lv2/app/api/gateway_admin.py.md)

### 27. app/api/gateway_admin.py
**파일 요약 / File Summary**
- 역할 / Role: AI Gateway 관리 API — 정책 CRUD(관리자 비상 경로)·버전·diff·롤백·Rego 미리보기, 정책 게시 심사(초안 → 회귀 시험 → 게시 요청 → 작성자·수정자 외 승인), 정책 시험 케이스·시뮬레이터, 한도·예산, 시크릿(마스킹), 보안 가드(PII·인젝션·업스트림 mTLS), 도구 규칙·소독 규칙·에이전트 신뢰 등급, 사람 승인 대기열(조회·승인·거부·연장·동의), 라우팅 결정 로그(JSONL 내보내기).
- 진입점 거리 / Distance: 1 — `app/main.py:175` `include_router(gateway_admin_api.router)`(gateway 구성요소), `app/main.py:181` `APPROVAL_HOOKS.append(orchestrator.on_approval)`(host 구성요소). (주의: `app/main.py` 는 21:35 에 디스크에서 수정 중이라 스냅샷 시점 라인과 다를 수 있음 — 아래 다른 파일 인용 중 `app/main.py`·`app/services/{mcp_gateway,mcp_consent,policy,secrets,a2a_gateway}.py` 는 2026-10-07 21:30~21:35 디스크 기준이다. 이 파일 자체는 14:26 수정 이후 불변, 해시 `c3c0c00e…` 가 스냅샷과 일치.)
- LOC: 836 (현재 `wc -l`) · 1차 835 → +1 (`update_draft` 의 `_editors` 1줄 추가, `approve_draft` 자기 승인 판정 조건 변경)
- 주요 export: `router`, `APPROVAL_HOOKS`, `policy_out`, `draft_out`, `limit_out`, `rule_out`, `approval_out`, (내부) `_break_glass`·`_revision`·`_draft`·`_approval`·`_on_decision`
- 의존(내부): `app.core.{auth,errors,ids}`, `app.db`, `app.models`, `app.services.{audit,governance,guards,mcp_consent,policy,policy_review,ratelimit,secrets}`, (지연) `app.services.tls`
- 외부 패키지: fastapi(`APIRouter`·`Body`·`Depends`·`Query`, 지연 `PlainTextResponse`), pydantic, jsonschema, sqlalchemy
- 부수효과 / Side effects: DB 쓰기·커밋(정책·초안·시험 케이스·한도·시크릿·가드 설정·규칙·신뢰 등급·승인), 감사 체인 추가(`audit.append` 21곳 — 모두 본 트랜잭션 커밋 **뒤** 별도 트랜잭션), 워커 로컬 캐시 무효화(`ratelimit.clear_cache`·`tls.invalidate`), 승인 훅 동기 호출(오케스트레이터 실행 재개 예약).
- 동시성 / Concurrency: 모든 엔드포인트가 동기 `def` → FastAPI 가 AnyIO 스레드풀에서 실행(요청당 `get_db` 세션 1개). 행 잠금·조건부 UPDATE 가 이 파일에는 없다 — 초안 승인·반려(`approve_draft`/`reject_draft`)와 승인 결정(`governance.decide`)은 읽기-검사-쓰기. 도구 승인 **소비**만 호출 대상에서 원자적(`app/services/governance.py:177-185`).
- 테스트 커버리지 흔적: `test/test_review.py:175-255`(2인 검토·회귀 시험·롤백 확인/사유·stale 초안), `test/test_review_fixes.py:327-339`(남의 초안을 고친 사람 승인 403 `self_approval`), `test/test_gateway_policy.py:177-193`(시크릿 값이 응답·감사에 없음), `:235-254`(approvers 직무 분리·팀 간 조회 차단), `test/test_hardening.py:189-198`(approvers 거부 사유 필수), `test/test_extend.py:58-130`(연장), `test/test_mcp_consent.py:58-172`(동의 카드). **시험 없음**: 롤백 대상 버전 제한, 승인 엔드포인트의 주체 종류(workload·access_token·PAT) 제한, 동시 초안 승인, 훅 예외, `limit` 음수.
- 2차 변경 요약 / Changes since 1st review: (1) `update_draft` 가 수정자를 스냅샷 `_editors` 에 누적(L265-266), `approve_draft` 가 작성자 **또는 수정자**의 승인을 403 으로 거부(L315-316) — 1차 [중간] 2인 검토 우회 해결(Epic 39 C39-16, `test/impl/39th/scenario.md:32,103`). (2) 시크릿 이름 노출 → `secret_ref` 유출 경로는 이 파일이 아니라 참조 측(`app/services/secrets.py:24-28` `check_refs`, 호출 `app/api/mcp.py:403,429`·`app/api/a2a.py:457,535`)에서 차단(Epic 39 C39-12). (3) 그 외 블록은 1차와 문자 단위로 같다.

## 블록 리뷰 / Block Walk (Level 2)
`max_lines_per_file`(600) 초과 → 논리 블록 단위로 분석(모든 블록 커버). 블록 범위는 L1-836 을 연속으로 덮고, 모든 블록이 40줄 이하라 원문 전체를 인용한다.

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
- L13: 인가 도구 두 가지 — `require("perm")`(역할·토큰 스코프 기반 권한, `app/core/auth.py:289-295`)와 `current_principal`(로그인만 확인, 권한 없음). 승인 엔드포인트(L719-791)는 뒤쪽을 쓰고 객체 수준 검사를 `_approval`/`governance.can_decide` 에 맡긴다 — 이 차이가 아래 [위험:높음] 의 출발점이다.
- L14: `ApiError` 팩토리 — 전역 처리기가 problem+json 으로 바꾼다. 검증 오류(422) 처리기는 입력값(`input`)을 응답에 넣지 않으므로(`app/core/errors.py:70-75`) 시크릿 값이 422 로 반사되지 않는다.
- L21-22: 태그 `gateway`, 경로 접두 상수 `P`. 승인 API(`/api/v1/approvals/*`)는 접두 밖이지만 같은 라우터라 gateway 구성요소에서만 마운트된다.

```python path=app/api/gateway_admin.py lines=24-48

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
```
**분석 / Analysis (Level 2)**
- L26-39 `PolicyIn`: 열거형은 pydantic `pattern` 으로 제한. `conditions: dict = {}` 같은 가변 기본값은 pydantic 이 인스턴스마다 깊은 복사하므로 공유 버그가 없다(일반 클래스·dataclass 와 다른 점 — 흔한 오해).
- pydantic v2 기본 `extra="ignore"` — 본문에 `_editors`·`_delete` 같은 밑줄 키를 넣어도 버려진다. 초안 스냅샷의 내부 메타 키(L244·L246·L265)를 클라이언트가 위조할 수 없는 근거가 이것이다.
- L42-47 `policy_out`: 정책 행 → dict. `PolicyRevision.snapshot`(L69)과 초안 diff(L216)의 기준 형식이다. `policy_review.FIELDS`(`app/services/policy_review.py:17-18`)와 키 집합이 일치해야 롤백(L175-177)이 모든 필드를 되돌린다(현재 13개 일치).

```python path=app/api/gateway_admin.py lines=49-71

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
```
**분석 / Analysis (Level 2)**
- L50-58 `_check_policy`: 데이터 분류 이름 검증 + `check_schema` 로 **스키마 자체**의 유효성 확인(잘못된 스키마를 저장하면 이후 `jsonschema.validate` 가 `SchemaError` 로 500 을 낸다 — 저장 시점에 막는 것이 맞다). `conditions.data_class` 가 리스트가 아니라 문자열이면 문자 단위로 순회해 "알 수 없는 분류" 400 이 난다(결과적으로 거부되므로 안전).
- L61-65 `_break_glass`: 역할 목록에 `admin` 이 있어야 직접 변경. **권한(`p.has`)이 아니라 역할 문자열**을 본다 — RBAC 재정의(`rbac.overrides`)로 다른 역할에 `*` 를 줄 수 없게 되어 있어(`app/core/auth.py:110-115` 관리자만 `*`) 의도대로 좁다. 단, admin 의 스코프 제한 PAT 라도 `policy:write` 스코프만 있으면 통과한다(스코프는 `require` 가 검사).
- 이 파일 밖에서 `GatewayPolicy` 를 직접 쓰는 경로가 있어 "정책 변경 = 초안 심사" 가 저장소 전체 불변식은 아니다: 카탈로그 즉시 사용 마법사 `app/api/catalog.py:370-420`(`catalog:write` = operator — `policy:write` 없음 — 가 `effect="allow"` 정책과 `PolicyRevision` 을 심사 없이 생성·갱신), 레지스트리 심사 승인 `app/services/review.py:194-196`(허용목록 allow 정책 추가). 이 파일의 결함은 아니므로 등급은 해당 파일 리뷰에서 매긴다(교차 확인 필요).
- L68-70 `_revision`: 변경마다 전체 스냅샷을 이력으로(`updated_at` 은 직렬화 불가 값 회피 + diff 노이즈 제거 목적으로 None). `changed_by` 가 실제 변경자다.

```python path=app/api/gateway_admin.py lines=72-97

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
- L79 `policy_review.stats`: 24시간 UsageRecord·감사 거부 이벤트를 `GROUP BY` 후 정책마다 `fnmatch` 로 근사 집계(`app/services/policy_review.py:100-114`) — 정책 수 × 집계 행 수의 파이썬 루프라 정책이 수백 개면 목록 조회가 느려진다(낮은 영향, 화면용).
- L80: `pending` 은 리스트 — L82 의 `in` 이 O(n). 정책 수가 작아 수용.
- L83 `engine_status()`: OPA 모드면 **요청마다** `GET {opa}/health`(2초 타임아웃, `app/services/policy_review.py:117-128`) — 목록 조회가 OPA 장애 시 최대 2초 지연(스레드풀이라 이벤트 루프는 막지 않는다).
- L86-96 `create_policy`: 비상 경로. `flush()` 로 INSERT 를 먼저 보내 `pol.id` 가 확정된 뒤 이력 추가 → 한 번에 커밋. 감사는 커밋 **뒤** 별도 트랜잭션(`app/services/audit.py:52-69` 자체 세션) — 감사 쓰기가 실패하면 변경은 남고 감사는 없다(파일 전체 공통 패턴, 아래 파일 평가에서 한 번만 등급).

```python path=app/api/gateway_admin.py lines=98-127

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
```
**분석 / Analysis (Level 2)**
- L107-109: 전체 필드 덮어쓰기 + 파이썬 쪽 `version += 1`. 동시에 두 관리자가 수정하면 둘 다 같은 `version` 을 읽어 같은 값으로 올린다(낙관적 잠금 `version_id_col` 미사용) — 이력에 같은 버전 번호 2건이 생길 수 있다(비상 경로·관리자 전용이라 낮음).
- L123-125: 삭제 후에도 `pol.name` 을 읽는다 — `commit()` 이 기본 `expire_on_commit=True` 로 속성을 만료시키지만, 삭제된 객체는 이미 로드된 속성 값을 유지하므로 동작한다(삭제 행 재조회가 일어나지 않음). `PolicyRevision.policy_id` 는 FK 가 아니어서(`app/models.py:696`) 삭제 후 이력이 고아로 남는다(의도된 보존으로 보인다).

```python path=app/api/gateway_admin.py lines=128-152

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
- L138 `Query(..., alias="from")`: `from` 은 파이썬 예약어라 매개변수 이름은 `from_`, 쿼리 키는 alias 로 맞춘다(흔한 패턴).
- L140-150 중첩 함수 `snap`: `to=None` 이면 현재 정책. 버전 목록에는 이력이 무제한 반환된다(페이지 없음 — 정책당 이력 수가 작아 수용).
- L147 `.first()`: 같은 버전 번호가 2건이면(위 L109 동시 수정) 임의 1건 — 결과가 비결정적일 수 있다(낮음).

```python path=app/api/gateway_admin.py lines=153-184

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
```
**분석 / Analysis (Level 2)**
- L164-167: 확인 플래그 + 사유 필수(실수 방지·감사 근거). 사유는 감사에만 남고 `PolicyRevision` 에는 없다.
- L171-177: 대상 버전 스냅샷의 `FIELDS` 13개를 현재 행에 덮어쓰고 새 버전으로 기록 — 과거 버전 번호를 재사용하지 않아 이력이 선형으로 유지된다(좋음).
- `_break_glass` 를 호출하지 **않는다** — `policy:write`(security 역할, `app/core/auth.py:29`) 1명이 단독으로 실행한다(`test/test_review.py:215-221` 이 security 역할로 성공을 확인 — 의도된 단독 경로).
**짚어볼 점 / Watch-outs**
- [위험:중간] L160-183 **단독 롤백이 2인 검토를 우회하는 정책 변경 경로가 된다.** docstring 은 "승인됐던 이전 버전" 이라고 하지만 코드는 대상 리비전이 승인을 거쳤는지 확인하지 않는다 — 리비전은 비상 경로(L93·L110, 심사 없음), 롤백 자체(L179), 초안 승인(L330·L336) 어디서든 생긴다. 시나리오: 보안 담당 A 가 사고 대응으로 `jira.*` 를 `deny` 로 바꾼 v5 를 2인 검토로 게시했다. 같은 역할의 B 가 혼자 `POST /policies/{id}/rollback {"version": 1, "confirm": true, "reason": "회귀"}` 로 허용이던 v1 을 즉시 되살린다 — 두 번째 사람 없이 정책이 **완화**된다. 관리자가 비상 경로로 만든(검토받지 않은) 버전도 같은 방식으로 단독 재적용된다. 감사(`policy.rollback`)에는 남는다. 최소 수정: (a) 대상 리비전을 "초안 승인으로 생성된 것" 으로 제한(리비전에 `draft_id`/`approved_by` 기록 후 검사), 그리고 (b) 즉시 복원은 **직전 승인 버전**으로만 허용하거나, 그 밖은 롤백 초안(`create_draft(policy=<snapshot>)`)으로 보내 2인 검토를 거치게 한다. 시험 공백: 비상 경로 리비전·완화 방향 롤백 거부 시험 없음.

```python path=app/api/gateway_admin.py lines=185-219

class RegoIn(BaseModel):
    policy: PolicyIn


@router.post(f"{P}/policies/rego")
def rego_preview(body: RegoIn, _: Principal = Depends(require("gateway:read"))):
    return {"rego": policy_review.to_rego(body.policy.model_dump())}


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
```
**분석 / Analysis (Level 2)**
- L190-192 Rego 미리보기: 문자열 생성만(`app/services/policy_review.py:78-97`) — 실행·평가하지 않으므로 이름·대상 문자열에 따옴표가 섞여도 위험 싱크는 없다(미리보기 텍스트가 깨질 뿐).
- L209-217 `draft_out`: 밑줄로 시작하는 내부 키(`_base_version`·`_delete`·`_editors`)는 `policy` 에서 숨기고 일부만 별도 필드로. **`_editors`(수정자 목록)는 응답에 노출되지 않는다** — 승인 화면에서 "누가 고쳤는지" 를 볼 수 없어, 승인자가 자신이 수정자라 403 이 나는 이유를 UI 가 미리 알려주지 못한다(낮음, 사용성).
- L216: 삭제 초안은 diff 를 비운다 — 무엇이 삭제되는지는 `policy` 필드(삭제 시점 스냅샷)로 본다.

```python path=app/api/gateway_admin.py lines=220-256

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
```
**분석 / Analysis (Level 2)**
- L226 `.all()[:200]`: **전체 행을 메모리로 읽은 뒤** 파이썬에서 200개로 자른다. DB `LIMIT 200` 이 아니어서 초안이 쌓이면(승인·반려된 초안도 지우지 않음) 매 조회가 전 행 로드 + 행마다 `draft_out` 의 `db.get`·diff·Rego 생성(N+1)을 한다. 최소 수정: `stmt.limit(200)`.
- L231-240: 기존 정책을 고치는 초안은 `_base_version` 에 그 시점 버전을 기록 — 승인 시 낙관적 충돌 감지(L320)의 기준.
- L245: `created_by` 는 처음 작성자. 이후 수정자는 L265 `_editors` 로 별도 누적한다.

```python path=app/api/gateway_admin.py lines=257-283

@router.put(f"{P}/policies/drafts/{{draft_id}}")
def update_draft(draft_id: str, body: PolicyIn, p: Principal = Depends(require("policy:write")),
                 db: Session = Depends(get_db)):
    d = _draft(db, draft_id)
    if d.status not in ("draft", "rejected"):
        raise conflict(f"{d.status} 상태 초안은 고칠 수 없습니다.", code="invalid_state")
    _check_policy(body)
    editors = sorted({*(d.snapshot.get("_editors") or []), p.sub})  # 고친 사람도 승인할 수 없다 (2인 검토)
    d.snapshot = {**body.model_dump(), "_base_version": d.snapshot.get("_base_version"), "_editors": editors}
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
- L265 (2차 신규): 기존 수정자 + 현재 사용자를 집합으로 합쳐 정렬 저장 — 같은 사람이 여러 번 고쳐도 1회. 승인 판정 L315 가 이 목록을 본다(1차 [중간] 해결).
- L266: `d.snapshot` 에 **새 dict 를 대입**한다. SQLAlchemy JSON 컬럼은 제자리 변경(`d.snapshot["x"] = ...`)을 추적하지 못하므로(MutableDict 미사용) 통째로 바꾸는 이 방식이 맞다 — 흔한 실수 지점을 피했다.
- L267: 수정하면 시험 결과를 비워 재시험 강제 — 게시 요청(L297)이 `passed`·`tested_at` 을 요구하므로 "시험 통과 후 몰래 고치기" 가 막힌다.
- L272-282 `test_draft`: 삭제 초안은 `enabled=False` 로 덮은 **임시 객체**(세션에 add 하지 않음)로 시험 — 삭제 효과를 비활성과 같게 근사. 상태 검사가 없어 `pending`·`approved` 초안도 재시험할 수 있다(내용은 바뀌지 않으므로 무해).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L266 스냅샷을 `PolicyIn` 덤프로 다시 만들면서 `_delete` 를 옮기지 않는다 — 삭제 초안을 PUT 으로 고치면 **조용히 수정 초안으로 바뀐다**(응답의 `delete` 가 false 로 바뀌는 것 외에 경고 없음). 검토자가 "삭제 요청" 으로 알고 있던 초안이 다른 의미로 게시될 수 있다. 최소 수정: `_delete` 초안이면 409 로 수정 거부, 또는 `"_delete": d.snapshot.get("_delete")` 보존.

```python path=app/api/gateway_admin.py lines=284-303

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
```
**분석 / Analysis (Level 2)**
- L293-299: 상태·사유·시험 통과를 모두 확인한 뒤 `pending`. `rejected` 에서 바로 재요청도 가능하지만 반려 후 수정하면 L267 이 시험 결과를 지우므로 실제로는 재시험이 필요하다.
- 시험 통과 여부는 **시험 시점의 케이스 집합** 기준이다. 시험 케이스는 게시 요청 뒤에도 바뀔 수 있다(아래 L359-390).
- 게시 요청자는 수정자로 기록되지 않는다 — 내용을 바꾸지 않으므로 2인 검토 의미상 문제없다.

```python path=app/api/gateway_admin.py lines=304-343

class DecideIn(BaseModel):
    note: str = ""


@router.post(f"{P}/policies/drafts/{{draft_id}}/approve")
def approve_draft(draft_id: str, body: DecideIn, p: Principal = Depends(require("policy:write")),
                  db: Session = Depends(get_db)):
    d = _draft(db, draft_id)
    if d.status != "pending":
        raise conflict(f"{d.status} 상태 초안은 승인할 수 없습니다.", code="invalid_state")
    if d.created_by == p.sub or p.sub in (d.snapshot.get("_editors") or []):
        raise forbidden("작성자·수정자는 자신의 정책 변경을 승인할 수 없습니다 (2인 검토).", code="self_approval")
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
```
**분석 / Analysis (Level 2)**
- L315-316 (2차 변경): 작성자 **또는** 수정자 목록에 있으면 403 `self_approval`. 승인 권한은 `policy:write`(security·admin) — 즉 2인 검토 = 서로 다른 두 `policy:write` 보유자.
- L317-321: 대상 정책이 사라졌거나 기준 버전이 바뀌었으면 409 `stale_draft`(낙관적 충돌 감지). 새 정책 초안(`_base_version=None`)은 이 검사가 없다.
- L322-337: 삭제·생성·수정 세 갈래를 **한 트랜잭션**으로 적용하고 초안 상태까지 L339 에서 함께 커밋 — 적용과 상태 변경 사이에 다른 커밋이 끼지 않는다.
- L330·L336 `_revision(..., d.created_by or p.sub)`: 이력의 `changed_by` 는 승인자가 아니라 **작성자**다. 실제 수정자(`_editors`)와 승인자는 이력이 아니라 감사(`author`·`actor`)에만 남는다 — 이력만 보면 마지막 내용을 쓴 사람이 누구인지 알 수 없다(낮음, 추적성).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L312-339 상태 전이가 조건부 UPDATE 가 아니다(읽기 `d.status == "pending"` → 쓰기). (a) 같은 승인자가 버튼을 두 번 눌러 요청 2개가 겹치면 둘 다 `pending` 을 읽고 통과 — **새 정책 초안이면 `GatewayPolicy` 가 2개 생성**되고(L327, id 가 서로 다름), 수정 초안이면 버전이 2번 올라 이력 2건. (b) 승인자 B 의 승인과 C 의 반려가 겹치면 정책은 적용됐는데 초안 상태는 나중 커밋인 `rejected` 로 남을 수 있다 — 화면은 "반려" 인데 정책은 게시된 상태. 최소 수정: 첫 쓰기를 `db.execute(update(PolicyDraft).where(PolicyDraft.id == d.id, PolicyDraft.status == "pending").values(status="approving"))` 의 `rowcount == 1` 로 선점(같은 파일이 이미 쓰는 `governance._consume` 패턴), 반려(L353)도 같은 조건부 전이.

```python path=app/api/gateway_admin.py lines=344-358

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
- L345-356: 사유 필수 반려. 작성자 본인도 반려할 수 있다(사실상 "철회" — 내용을 게시하지 않는 방향이라 2인 검토 원칙과 충돌하지 않음). 반려 사유는 감사에만 저장되고 초안 행에는 없다(`PolicyDraft` 에 `decision_note` 컬럼 없음, `app/models.py:1014-1026`) — 작성자가 화면에서 반려 사유를 다시 보려면 감사를 조회해야 한다(낮음).
- 경쟁 조건은 L312-339 Watch-out 에 포함.

```python path=app/api/gateway_admin.py lines=359-390

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
```
**분석 / Analysis (Level 2)**
- 회귀 시험 케이스 CRUD. 케이스는 전역이며 모든 초안 시험(`app/services/policy_review.py:49-52`)에 쓰인다 — "하나라도 기대와 다르면 게시 불가" 의 기준 집합.
- L373: `input` 의 형태는 kind·target 만 검사. `input` dict 크기 제한이 없다(정책 작성자 권한이라 낮음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L371-388 시험 케이스 생성·**삭제에 감사 기록이 없다**(같은 파일의 다른 쓰기 엔드포인트는 모두 `audit.append`). 회귀 시험은 2인 검토의 게이트인데, `policy:write` 보유자가 자기 초안을 막는 케이스를 지우고 시험 → 게시 요청하면 흔적이 남지 않는다(승인자는 `tests` 개수만 감사에서 본다, L341). 최소 수정: 두 엔드포인트에 `audit.append("policy.test.create|delete", ...)` 추가, 가능하면 초안 `test_result` 에 케이스 집합 해시를 넣어 승인 시 현재 집합과 비교.

```python path=app/api/gateway_admin.py lines=391-420

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
- 가상 주체로 정책 판정만(발급·실행 없음). `gateway:read`(developer 포함)로 임의 역할의 판정을 미리 볼 수 있다 — 정책 내용 자체가 `gateway:read` 로 공개돼 있어 추가 노출은 없다(1차와 같은 판단, 수용).
- L403: `subject.roles` 가 문자열이면 `Principal.permissions()` 가 문자 단위로 순회 — 판정이 엉뚱해질 뿐 권한 상승은 없다(시뮬레이션 전용).
- L416 `policy.evaluate`: OPA 모드면 OPA 를 호출(실제 엔진과 같은 경로) — 시뮬레이터가 OPA 부하원이 될 수 있다(낮음).

```python path=app/api/gateway_admin.py lines=421-452

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
```
**분석 / Analysis (Level 2)**
- L431 `budget_usd: Decimal`: 금액은 부동소수점 오차를 피하려 `Decimal`. 출력(L442)은 JSON 직렬화를 위해 `float` 로 — 표시용이라 수용.
- L428 `rps: float` 와 `gt=0` — 0 이하 거부. `burst` 미지정이면 `ceil(rps)`(호출 대상 `app/services/ratelimit.py:256-257`).
- L450 `backend_status()`: Valkey(Redis) 백엔드 상태 — 다중 워커에서 버킷 공유 여부를 화면에 표시.

```python path=app/api/gateway_admin.py lines=453-490

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
```
**분석 / Analysis (Level 2)**
- 권한 `limits:write`(operator·finops). 한도 행은 요청마다 DB 에서 읽으므로(`app/services/ratelimit.py:248`) 생성·삭제가 즉시 반영된다. L475 `clear_cache()` 는 **예산 사용액 캐시**(5초, `app/services/ratelimit.py:221-242`)만 비운다 — 한도 정의 캐시는 원래 없다. 생성·삭제가 이 호출을 하지 않아도 문제없다.
- 한도 삭제·완화는 단독 권한(2인 검토 없음) — 비용 통제라 정책과 같은 수준의 심사를 요구하지 않는 설계로 보인다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L466-477 `update_limit` 는 `create_limit` 의 두 검증(L455-458: fallback 이면 모델 필수, 지표 1개 이상)을 하지 않는다. `on_exceed=fallback`·`fallback_model=null` 로 고치면 초과 시 폴백 조건(`app/services/ratelimit.py:286`)을 못 맞춰 **차단**으로 떨어지고(의도와 다른 동작이지만 fail-closed), 지표를 모두 null 로 고치면 아무 것도 제한하지 않는 행이 남는다. 최소 수정: 두 검증을 함수로 빼서 생성·수정 모두에서 호출.

```python path=app/api/gateway_admin.py lines=491-504

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
- 와일드카드·주체별(`per-subject`) 한도는 조회 시점에 대상 주체를 알 수 없어 제외(1차와 같음). 한도마다 `SUM` 쿼리 1회, 캐시 우회(`use_cache=False`) — 한도 수가 작아 수용.
- L501 `if lim.budget_usd`: 예산 0 이면 0 나눗셈을 피하고 `pct=None`.
- `scope_type="any"` 이면 `spend` 가 필터 없이 전체 합계를 낸다(`app/services/ratelimit.py:229-236` 에 any 분기 없음) — 실제 집행 측은 any 를 팀 기준으로 계산하므로(`:267-268`) 화면 수치와 집행 기준이 다를 수 있다(낮음, 표시 불일치).

```python path=app/api/gateway_admin.py lines=505-527

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
```
**분석 / Analysis (Level 2)**
- L508 `repr=False`: 모델의 `repr()`·디버그 로그에 값이 찍히지 않는다. 422 응답도 입력값을 반사하지 않는다(`app/core/errors.py:70-75`).
- L515·L525 `secrets.masked`: 이름·종류·`••••` + 마지막 4자(값 8자 이상일 때만)·시각 — 평문·암호문 모두 응답에 없다(`app/services/secrets.py:87-90`, 21:32 디스크). 감사 데이터도 `kind` 만(L524). 시험 `test/test_gateway_policy.py:177-193` 이 응답·감사 덤프에 값이 없음을 확인.
- 이름 노출 자체는 남아 있다(`gateway:read` = developer 포함). 1차는 "이름을 알면 `secret_ref` 로 지정해 외부 전송" 을 지적했는데, 2차에서 다른 네임스페이스 시크릿 참조는 `gateway:write` 가 필요해졌다(`app/services/secrets.py:24-28`, 호출 `app/api/mcp.py:403,429`·`app/api/a2a.py:457,535`, 시험 `test/test_review_fixes.py:247`) — 유출 경로는 닫혔다.
- L520-522: 이름 중복을 먼저 조회한 뒤 삽입(check-then-insert). 동시에 같은 이름으로 2건이 오면 `Secret.name` UNIQUE(`app/models.py:728`)가 두 번째를 `IntegrityError` → 처리기 없는 500 으로 막는다(정합성은 지켜짐, 오류 형식만 문제 — 낮음).

```python path=app/api/gateway_admin.py lines=528-550

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
- L529 `Body(..., embed=True)`: 본문 `{"value": "..."}` 형태. `secrets.put`(`app/services/secrets.py:54-68`)이 같은 이름 행을 찾아 암호문·last4·`rotated_at` 갱신 — `put` 은 `flush` 만 하고 커밋은 L535.
- L537: `put` 이 갱신한 같은 ORM 객체(`row`)를 마스킹해 반환 — `commit()` 후 속성 만료로 한 번 재조회(SELECT)가 일어난다(정상 동작).
- L545: 삭제 전에 참조 여부(MCP 서버·A2A 에이전트의 `auth.secret_ref`)를 확인하지 않는다 — 참조 중인 시크릿을 지우면 해당 업스트림 인증이 다음 호출부터 실패한다(운영 실수 방지 수준, 낮음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L529 회전 값에 `SecretIn` 과 같은 `min_length=1` 이 없다 — `{"value": ""}` 로 회전하면 빈 자격 증명이 암호화돼 저장되고(`last4=None`, 200 응답), 그 시크릿을 쓰는 MCP 업스트림 호출은 다음부터 "백엔드 자격 증명이 설정되지 않았습니다" 로 실패한다(`app/services/mcp_gateway.py:135-137`, fail-closed 지만 실수 한 번으로 서비스 중단). 최소 수정: `value: str = Body(..., embed=True, min_length=1)`.

```python path=app/api/gateway_admin.py lines=551-581

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
```
**분석 / Analysis (Level 2)**
- L558 `body: dict`: pydantic 모델 대신 임의 dict — 허용 키만 L569 에서 골라 병합(화이트리스트). 모르는 키는 **조용히 무시**(1차 지적 그대로).
- L568 `security_settings`: `{**DEFAULT_SECURITY, **저장값}` 으로 항상 새 dict 를 반환(`app/services/guards.py:79-81`) — 그 위에 `update` 하므로 공유 기본값 dict 를 변이하지 않는다(얕은 복사라 `mtls` 중첩 dict 는 공유되지만 L569 가 통째로 교체하므로 안전).
- L571-574: 행이 없으면 INSERT, 있으면 새 dict 대입(JSON 변경 추적 문제 회피). 동시 수정 2건은 나중 커밋이 이긴다(관리 설정이라 수용).
- L576-577 `tls.invalidate()`: **이 워커의** mTLS 모드 캐시만 비운다. 다른 워커는 `MODE_TTL_S`=5초(`app/services/tls.py:22`) 뒤 반영 — 주석과 일치.
- 권한 `policy:write`(security·admin) 단독 — PII 마스킹·인젝션 차단을 `allow` 로, 업스트림 mTLS 를 `disabled` 로 1명이 내릴 수 있다. 정책(GatewayPolicy)과 달리 2인 검토가 없다(아래 L625-641 Watch-out 과 같은 설계 질문).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L566-569 `mtls` 는 `upstream` 값만 검사하고 dict 를 통째로 저장 — 임의 키·큰 값이 설정 행과 감사(L578 `data=cur`)에 그대로 들어간다. `tool_response_sanitize` 는 형식 검사가 없다(`"false"` 문자열이면 참으로 평가돼 소독이 켜진 채 유지 — fail-safe 이지만 화면과 실제가 다를 수 있음, `app/services/guards.py:260`). 최소 수정: `SecurityIn` pydantic 모델(`extra="forbid"`, `tool_response_sanitize: bool`, `mtls: {upstream: Literal[...]}`).

```python path=app/api/gateway_admin.py lines=582-594

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
- 실제 가드 함수로 텍스트 검사만(저장 없음). `mode` 는 열거 검증이 없어 오타면 입력 검사로 떨어진다(무해).
- `text` 길이 제한이 없다 — 사용자 정의 정규식(아래 소독 규칙·PII 사용자 규칙 `app/services/guards.py:86-98`)이 파국적 역추적 패턴이면 `gateway:read`(developer) 가 긴 입력으로 워커 스레드를 오래 점유시킬 수 있다(L655-679 ReDoS Watch-out 과 같은 뿌리).

```python path=app/api/gateway_admin.py lines=595-624

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
```
**분석 / Analysis (Level 2)**
- `ToolRuleIn` 기본값이 `allowed=True`·`risk=low`·`approval_required=False`·`approver=requester` — 본문에서 필드를 빠뜨리면 **가장 느슨한 값**으로 저장된다(upsert 가 전체 필드를 덮어씀, L637-638). 예: 위험도만 바꾸려고 `{"server_id":"jira","tool":"delete_issue","risk":"high"}` 를 보내면 `approval_required` 가 False 로 꺼진다. 콘솔은 전체 행을 보내겠지만 API 직접 사용자는 쉽게 실수한다(낮음 — 아래 Watch-out 에 포함).
- `approver` 기본 `requester` — 요청자 본인이 승인하는 모드. 이 기본값과 승인 엔드포인트(L746-755)가 결합한 결함이 아래 [위험:높음].

```python path=app/api/gateway_admin.py lines=625-654

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
```
**분석 / Analysis (Level 2)**
- L632-638 upsert: `(server_id, tool)` UNIQUE(`app/models.py:309`). 조회-후-삽입이라 동시 2건이면 두 번째가 `IntegrityError` 500(낮음). 서버 존재 여부를 확인하지 않아 없는 서버의 규칙도 만들어진다(나중에 그 id 로 서버가 등록되면 즉시 적용 — 낮음).
- L640 감사에 전체 본문 — 무엇이 바뀌었는지(이전 값)는 없다.
- L644-652 삭제: 규칙이 없으면 도구는 `unregistered_tool` 로 거부(기본 거부, `app/services/governance.py:123-125`) — 단, 다음 도구 목록 갱신(헬스 루프 주기, `app/services/mcp_gateway.py:748` `default_rules`)에서 **기본 규칙이 다시 생성**된다. 즉 규칙 삭제는 "차단" 이 아니라 "기본값으로 초기화" 다. 차단하려면 `allowed=false` 로 남겨야 한다(운영 문서·UI 에 명시 필요, 낮음).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 설계 확인 필요] L625-641 `policy:write` 1명이 고위험 도구의 `approval_required=false`·`risk=low`·`allowed=true` 를 즉시 적용한다 — 효과는 "사람 승인 없이 파괴적 도구 실행 허용" 으로 정책 완화와 같지만, 2인 검토(H08)는 `GatewayPolicy` 에만 걸려 있다. 보안 가드(L557-579)·에이전트 신뢰 등급(L693-703)·소독 규칙도 같다. 감사에는 남는다. 또 위 `ToolRuleIn` 기본값 때문에 필드 누락 = 완화다. 최소 수정: 완화 방향 변경(`approval_required` true→false, `risk` 하향, `allowed` false→true, `trust_tier` 상향)만 초안 심사로 보내거나, 최소한 `ToolRuleIn` 기본값을 `None` 으로 두고 PATCH 의미(보낸 필드만 변경)로 바꾼다.

```python path=app/api/gateway_admin.py lines=655-692

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
```
**분석 / Analysis (Level 2)**
- L671-674: `re.compile` 로 문법만 검증. 파이썬 `re` 는 백트래킹 엔진이고 타임아웃이 없다.
- 규칙 수정 엔드포인트가 없다(생성·삭제만) — 고치려면 삭제 후 재생성(감사 2건).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L671-674 파국적 역추적(ReDoS) 패턴(예: `(a+)+$`)을 거르지 않고, `pattern` 길이 제한도 없다(1차 지적 그대로). 규칙은 도구 응답·입력·출력마다 적용되므로(`app/services/guards.py:160-162,179`) 한 규칙이 모든 요청의 워커 스레드를 묶을 수 있다. `policy:write` 보유자만 만들 수 있어 악의보다 실수가 현실적인 시나리오다. 최소 수정: `max_length`(예: 500) + 중첩 수량자 휴리스틱 거부, 또는 `regex` 패키지의 `timeout=`/`re2` 사용.

```python path=app/api/gateway_admin.py lines=693-705

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
- 원격 에이전트 신뢰 등급 변경 — `blocked` 는 즉시 차단(`app/services/governance.py:318-319`), `untrusted` 는 명시 허용 정책 필요(`:326-330`). 감사에 이전·이후 값(좋음 — 이 파일에서 이전 값을 남기는 유일한 쓰기).
- 단독 권한 — L625-641 Watch-out 의 설계 질문에 포함.

```python path=app/api/gateway_admin.py lines=706-739

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
```
**분석 / Analysis (Level 2)**
- L708: DB 에는 아직 `pending` 이지만 기한이 지난 건은 **표시만** `expired`(실제 전이는 스케줄러 `approval_jobs`, 기본 60초 주기 `app/core/config.py:52`, `app/services/scheduler.py:80-96`). 그래서 `?status=pending` 조회 결과에 표시 상태 `expired` 가 섞인다(낮음, 화면 혼동).
- L710·L714: `args`·`payload`(sampling 편집 프롬프트·elicitation 응답)·`result` 가 그대로 나간다 — `approvals:decide`(operator·security) 보유자는 모든 팀의 인자를 본다(docstring 의 의도).
- L725·L735: 비승인자는 본인 요청 또는 위임받은 건만 — 객체 수준 인가(BOLA 방지). 존재 여부도 404 로 숨긴다(좋음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L721 `limit: int = Query(100, le=500)` 에 하한이 없다(L808 `routing_decisions` 의 `le=10000` 도 같음). `limit=-1` 이면 SQLite 는 음수 LIMIT 을 "상한 없음" 으로 해석해 상한을 우회하고(전체 승인·라우팅 로그를 한 번에 메모리 로드·직렬화), PostgreSQL 은 `LIMIT must not be negative` 오류 → 처리기 없는 500. 최소 수정: `Query(100, ge=1, le=500)`.

```python path=app/api/gateway_admin.py lines=740-773

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
```
**분석 / Analysis (Level 2)**
- L743: 도구 승인만 모달 맥락(출처·OBO 신원·영향·검사) 추가 — 호출 대상 `app/services/governance.py:198-234`(쿼리 7~8개).
- L746-755 승인: 권한 의존성 없이 `current_principal` → `_approval`(가시성) → `governance.decide`(상태·권한 `can_decide`·고위험 remember 금지) → 훅. 호출 대상 `app/services/governance.py:284-302`.
- L751: `elicitation` 만 막고 `sampling` 은 이 경로로 승인된다 — `mcp_consent.decide`(`approval.consent` 감사·편집·검토 선택)를 거치지 않고 `approval.decide` 감사로 `approved` 가 된다(대기 중인 중계는 `approved` 면 `payload.get("messages")`=None 으로 `_generate` 를 진행하고 검토 단계도 건너뛴다 — `app/services/mcp_consent.py:223-228`, 21:35 디스크). 기능상 동등하지만 감사 액션이 둘로 갈린다(낮음).
- L762: approvers 큐의 거부는 사유 필수(시험 `test/test_hardening.py:189-198`). `requester` 모드 자기 거부는 사유 없이 가능.
- L769-772 연장: 호출 대상 `governance.extend`(요청자 또는 결정권자, 대기·검토 중, 최대 10회).
- 호출처: 콘솔 승인 화면·채팅 승인 카드(SPA), MCP 클라이언트(오류 데이터의 `approval_id` 로 직접 호출 가능 — `app/services/mcp_gateway.py:955,975`).
**짚어볼 점 / Watch-outs**
- [위험:높음] L746-755 **승인 엔드포인트가 주체 종류를 보지 않아, 기계 주체(workload·access_token·client_key·PAT)가 자기 요청을 스스로 승인한다 — 고위험 도구의 "사람 승인"(PRD FR-G07, `PRD.md:50`)이 사람 없이 통과된다.** 근거 사슬: (1) 새 도구 기본 규칙은 고위험이면 `approval_required=True, approver="requester"`(`app/services/governance.py:69-70`), 저장소에 `approvers` 를 기본으로 주는 시드·설정이 없다(grep 0건). (2) MCP 게이트웨이는 승인 필요 시 `approval_id` 를 JSON-RPC 오류 데이터로 **호출자에게 돌려준다**(`app/services/mcp_gateway.py:955-956,975-976`). (3) 이 엔드포인트는 `current_principal` 만 요구 — `gateway:invoke` 스코프만 가진 access token·workload 도 통과(`app/core/auth.py:283-286`; 스코프 검사는 `p.has` 를 부를 때만 적용, `:75-80`). (4) `_approval` 은 요청자 본인이면 통과(L735), `can_decide` 는 requester 모드에서 `p.sub == ap.requester_sub` 면 True(`app/services/governance.py:281`). (5) 재호출 시 `X-AgentNexus-Approval` 헤더(`app/api/mcp.py:59`)로 승인을 소비해 실행(`app/services/governance.py:159-164`). 시나리오: 운영자가 `gateway:invoke` 로 발급한 서비스 access token(예: `test/test_hardening.py:77-87` 과 같은 방식)을 쓰는 자동화 클라이언트, 또는 사용자 PAT 를 쥔 AI 에이전트가 `tools/call jira.delete_issue` → JSON-RPC 오류 `-32001 approval_required {approval_id}`(`app/services/mcp_gateway.py:45`) → `POST /api/v1/approvals/{id}/approve`(같은 토큰) → 같은 호출에 헤더를 붙여 재시도 → 파괴적 도구가 사람 개입 없이 실행된다. 감사 `approval.decide` 에도 `actor_kind` 가 없어(`app/services/governance.py:300`, 기본값 `"system"`) 사후에 "기계가 승인했다" 를 구분하기 어렵다. 최소 수정: `governance.can_decide`(또는 이 엔드포인트)에서 requester 모드 자기 승인은 대화형 사용자 세션(`p.kind == "user"`, 필요하면 로그인 세션 `p.session_id` 존재까지)만 허용하고, `pat`·`client_key`·`workload`·`access_token`·`delegation` 은 403 — 그리고 감사에 `actor_kind=p.kind`. 고위험 기본 규칙의 `approver` 를 `approvers` 로 바꾸는 것도 선택지. 시험 공백: 스코프 토큰·workload 의 자기 승인 거부 시험 없음(`test/test_gateway_policy.py:216-232` 는 사용자 주체의 자기 동의 성공만 확인). (같은 결함의 뿌리는 `app/services/governance.py` 보고서에도 기록.)
- [위험:낮음] L753-754·L764-765 기한이 지난 `pending` 승인을 누르면 `governance.decide` 가 `expired` 로 커밋한 뒤 400 을 던져 `_on_decision` 이 **호출되지 않는다** — 실행이 영구 대기에 빠지는 결함의 상세·등급은 `app/services/governance.py:286-290` 보고서([위험:중간])에서 매긴다. 이 파일의 최소 수정 위치: `decide` 가 `expired` 로 바꿨으면 예외 전에 훅을 부르도록(또는 `decide` 안에서 처리).

```python path=app/api/gateway_admin.py lines=774-801

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
- L775-783 `ConsentIn`: 결정 값 열거 + 검토 텍스트 2만 자 제한. `messages` 는 개수·크기 제한이 없다(편집된 프롬프트가 그대로 LLM 으로 — 요청자 본인 데이터라 낮음).
- L786-791: 동의 결정은 호출 대상 `mcp_consent.decide`(종류·상태·결정 조합 검사, `elicitation` 은 `requestedSchema` 로 입력 검증). 동의 쪽은 중계 대기 루프가 DB 를 0.2초 폴링해 결과를 가져가므로(`app/services/mcp_consent.py` `_wait`) 훅을 부르지 않는다 — 의도된 차이.
- L794 `APPROVAL_HOOKS`: 모듈 전역 리스트에 `app/main.py:181` 이 `orchestrator.on_approval` 을 추가(전역 레지스트리를 통한 의존성 역전 — api 계층이 host 서비스를 import 하지 않게).
- L797-799 `_on_decision`: 요청 스레드에서 **동기** 호출. 호출 대상 `orchestrator.on_approval`(`app/services/orchestrator.py:937-959`)은 자체 세션으로 대기 단계를 `pending`/`rejected` 로 바꾸고 `schedule()` 로 실행 재개를 이벤트 루프에 예약(`run_coroutine_threadsafe`, `:294-308`). `RuntimeError`(루프 없음)만 잡는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L797-799 훅 예외를 격리하지 않는다(1차 지적 그대로). `on_approval` 의 DB 오류(장애조치 구간 `OperationalError` 등)가 나면 승인은 이미 커밋됐는데 API 는 500 을 돌려주고, 대기 단계는 `waiting_approval` 로 남는다. 복구 작업은 "대기 단계가 **없는**" 대기 실행만 재개하므로(`app/services/orchestrator.py:1007-1012`) 이 실행은 다시 재개되지 않는다 — 사용자가 재시도하면 `approval_closed`(이미 approved). 최소 수정: 훅마다 `try/except Exception: log.warning(...)` 로 격리하고, 복구 작업에 "승인이 approved/rejected/expired 인데 단계가 waiting_approval" 인 실행을 재처리하는 분기 추가.

```python path=app/api/gateway_admin.py lines=802-836

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
- L810: SQLAlchemy 는 `.limit()` 을 `.where()` 보다 먼저 써도 SQL 은 `WHERE … ORDER BY … LIMIT` 으로 조립한다(체이닝 순서 무관 — 흔한 오해).
- L822 `.is_(fallback)`: 불리언 컬럼 비교는 `IS true/false` — `== None` 대신 `is_`/`is_not` 를 쓰는 SQLAlchemy 관례.
- L828-835 JSONL 내보내기: 최대 1만 행을 메모리에서 문자열로 만든 뒤 한 번에 응답(스트리밍 아님), 내보내기 행위는 감사(`gateway.routing.export`). `subject` 필드에 사용자 sub 가 들어 있어 `gateway:read`(developer) 가 다른 사용자 활동 로그를 내보낼 수 있다(라우팅 로그 공개 범위는 설계 결정 — 정보성).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L824 `q` 를 LIKE 패턴에 그대로 넣는다(1차 지적 그대로). 바인딩 파라미터라 SQL 인젝션은 아니지만 `%`·`_` 가 와일드카드로 해석되고, 선행 `%` 패턴이라 인덱스를 못 써 전체 스캔이다. 최소 수정: `q` 길이 제한 + `.like(f"%{esc(q)}%", escape="\\")`(또는 `contains(q, autoescape=True)`). `limit` 하한 없음은 L721 Watch-out 에 포함.

## 파일 평가 / File Assessment
- 강점: (1) 정책 2인 검토가 작성자·**수정자** 모두를 승인에서 배제하고(L265·L315), 수정 시 재시험 강제·기준 버전 낙관적 충돌 감지·비상 경로 감사까지 한 흐름으로 갖췄다. (2) 승인 조회의 객체 수준 인가(본인·위임·결정권자, 없으면 404)와 approvers 거부 사유 강제. (3) 시크릿 평문이 응답·감사·422 어디에도 나가지 않고(`repr=False`·`masked`·검증 처리기), JSON 컬럼은 통째 대입으로 변경 추적 함정을 피했다.
- 위험: (1) [위험:높음] 승인 엔드포인트가 주체 종류를 보지 않아 기계 주체의 고위험 도구 자기 승인 — `app/api/gateway_admin.py:746-755` (+ `app/services/governance.py:69-70,274-281`). (2) [위험:중간] 단독 롤백으로 2인 검토 없이 정책 완화·비검토 버전 재적용 — `app/api/gateway_admin.py:160-183`. (3) [위험:낮음] 초안 승인·반려의 비원자 전이(중복 정책 생성·상태 불일치) `:312-339`, 훅 예외 미격리 `:797-799`, 시험 케이스 감사 누락 `:371-388`, `limit` 하한 없음 `:721,808`, ReDoS `:671-674`.
- 개선 권고(영향순): (1) 승인 결정에서 requester 모드 자기 승인은 대화형 사용자 세션만 허용 + 감사 `actor_kind`. (2) 롤백 대상을 승인된 리비전(가능하면 직전 승인 버전)으로 제한하고 그 밖은 롤백 초안으로. (3) 초안 승인·반려를 조건부 UPDATE 로 선점, 훅 예외 격리 + 복구 분기. (4) 시험 케이스 감사, `Query(ge=1)`, `SecurityIn` 모델, 회전 값 `min_length`, 소독 정규식 길이·복잡도 제한, `list_drafts` DB LIMIT.
- 파일 위험 등급 / Risk grade: 높음 — 미해결 [위험:높음] 1건(기계 주체 자기 승인) + 보안 경계 핵심 모듈(정책·승인 관리)에 [위험:중간] 1건.
- 재읽기 권고 레벨: 3 — 개별 엔드포인트는 단순하지만 "누가 어떤 통제를 혼자 바꿀 수 있는가"(롤백·도구 규칙·가드·승인 주체)를 권한 행렬(`app/core/auth.py:20-35`)과 맞춰 구조적으로 다시 볼 가치가 있다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| [중간] L258-268 `update_draft` 가 수정자를 기록하지 않아 남의 초안을 고친 사람이 승인 → 2인 검토 우회 (1차 INDEX §4.3 핫스팟 #4 일부) | 해결 | L265-266 `_editors` 누적, L315-316 작성자·수정자 403 `self_approval`; 시험 `test/test_review_fixes.py:327-339`(C39-16) |
| L314-315 `created_by == p.sub` 거부만으로는 위 우회와 결합 | 해결 | L315 `d.created_by == p.sub or p.sub in (d.snapshot.get("_editors") or [])` |
| [중간·간접] L517-524 시크릿 이름 노출 + `secret_ref` 지정 = 유출 경로 | 해결(이름 노출은 의도상 유지) | 다른 네임스페이스 참조는 `gateway:write` 필요 `app/services/secrets.py:24-28`, 호출 `app/api/mcp.py:403,429`·`app/api/a2a.py:457,535`; 시험 `test/test_review_fixes.py:247`. 이 파일 L513-515 는 그대로 이름 공개 |
| L670-672 소독 정규식 ReDoS 미검사 | 미해결 | L671-674 `re.compile` 만, `pattern` 길이 제한 없음 |
| L568 `put_security` 모르는 키 조용히 무시 | 미해결 | L569 |
| L796-798 훅 예외 미격리 → 승인 커밋 후 500 | 미해결(영향 구체화) | L797-799; 복구 미대상 `app/services/orchestrator.py:1007-1012` |
| L823 LIKE 패턴 미이스케이프·풀스캔 | 미해결 | L824 |
| L495 와일드카드·주체별 예산 현황 제외 | 미해결(설계 의도) | L496 |
| L107-109 `setattr` 전체 덮어쓰기 | 미해결(수용) | L107-108 (같은 패턴이 `upsert_tool_rule` L637-638 에서는 기본값 완화 문제로 이어짐 — L625-641 Watch-out) |
| L401-404 시뮬레이터 임의 주체 | 미해결(수용) | L403-405 |
| L732-735 `_approval` 객체 수준 인가(강점) | 유지 | L733-737 |
| (1차 위임 보안 리뷰) 승인 직무 분리 OK — `self_approval` (`.review/delegated/security-review.md:376`) | 유지 + 보강 | L315-316. 단 requester 모드 도구 승인의 기계 주체 자기 승인은 새 [위험:높음](L746-755) |
