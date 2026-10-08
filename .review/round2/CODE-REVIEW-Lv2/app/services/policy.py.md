> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: 1차 리뷰 대상 아님(부록 A)

### 16. app/services/policy.py
**파일 요약 / File Summary**
- 역할 / Role: 게이트웨이 정책 엔진(FR-G03) — (1) 호출자 신고 헤더의 **서버 판정**(`caller_context`: agent·run·step·session·데이터 등급), (2) 데이터 등급 기본값·하향 신고 권한(`declared_data_class`, `data:declare`), (3) 내장 엔진(`evaluate_builtin`: priority 오름차순 첫 매치 그룹, 같은 priority 는 deny 우선, 매치 없음 = default deny), (4) OPA 어댑터(`evaluate_opa`, 오류 시 fail closed).
- 진입점 거리 / Distance: 2 (`app/api/llm.py:30`·`app/api/mcp.py:58`·`app/api/a2a.py:48`·`app/api/knowledge.py:130` 이 `caller_context`/`declared_data_class` 직접, `app/api/gateway_admin.py:416` 이 `evaluate` 직접) · 서비스 경유 `app/services/{llm.py:188, governance.py:136, governance.py:324, policy_review.py:43}`
- LOC: 211 (21:03 스냅샷 `wc -l`, 리뷰 도중 변경 후 현재 디스크 212 — 아래 스냅샷 고지) · 1차 INDEX 부록 A 기준 157(`.review/CODE-REVIEW-Lv2/INDEX.md:330`) → +54
- 주요 export: `DATA_CLASSES`, `DECLARE_LOWER`, `MACHINE_KINDS`, `default_data_class`, `reset_default_data_class`, `declared_data_class`, `caller_context`, `PolicyInput`, `Decision`, `evaluate_builtin`, `evaluate_opa`, `evaluate`
- 의존(내부): `app.core.auth.Principal`, `app.core.config.settings`, `app.core.http.sync_client`, `app.models.GatewayPolicy`(+지연 import `Setting`·`Run`, `app.db.SessionLocal`, `app.services.audit`)
- 외부 패키지: sqlalchemy, jsonschema, 표준 `fnmatch`
- 부수효과 / Side effects: DB 읽기(정책·설정·Run), 감사 쓰기(하향 신고 거부 시 `gateway.data_class.raise`), OPA HTTP 호출(egress 가드·traceparent 포함), 워커 전역 `_DEFAULT_DC` 5초 캐시
- 동시성 / Concurrency: 동기(스레드풀·요청 세션). `_DEFAULT_DC` 는 락 없는 리스트 슬라이스 갱신(GIL 하 원자적 대입, 경합 무해)
- 테스트 커버리지 흔적: `test/test_caller_trust.py:38`(`test_agent_header_only_for_machine_principals`)·`:72`(`test_lower_data_class_needs_permission`)·`:90`(`test_run_header_bound_to_owner`)·`:108`(`test_model_api_uses_server_data_class`), `test/test_gateway_policy.py:300-321`(`test_opa_adapter_and_fail_closed`), `test/test_llm.py:188`(`test_policy_denies_without_reroute`)
- 2차 변경 요약 / Changes since 1st review: 1차 라인 리뷰 대상이 아니어서 인용 원문 비교는 불가. 의도 문서로 확인되는 변경 = L19-73(Epic 40 D17-① "인증 경계 강화", `test/impl/40th/scenario.md:7-11`): agent 신고는 기계·위임 주체만(L21·L70), run 신고는 소유자만(L59-69), 기본보다 낮은 데이터 등급 신고는 `data:declare` 필요(L20·L42-53), 기본 등급 5초 캐시(L22-39). 1차 다른 보고서가 인용한 `app/services/policy.py:71-72`(agent 를 헤더 그대로 사용)는 현재 L70 으로 대체됨.
- 스냅샷 고지 / Post-snapshot note: 인용·줄 번호는 **21:03 스냅샷**(sha1 `e02cbaa7…`, 인용 블록 재구성 해시 일치 확인) 기준이다. 리뷰 도중 21:34 에 디스크의 이 파일이 바뀌었다(D19-④, 212줄): 스냅샷 L47 뒤에 `declared = declared.strip().lower()` 한 줄이 추가되어 `"Confidential"`·`" public "` 같은 대소문자·공백 변형은 정상 등급으로 정규화된다. 그러나 그다음 줄의 `declared not in DATA_CLASSES → return declared` 는 그대로라 `"secret"`·`"none"` 같은 **알 수 없는 값은 여전히 통과**한다 — 아래 L48 [위험:중간] 은 현재 디스크 기준 **부분 해소**. 등급·표는 스냅샷 기준으로 유지한다.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/policy.py lines=1-22
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
```
**분석 / Analysis (Level 2)**
- L1-5: 평가 규칙(첫 매치 그룹·deny 우선·default deny)과 target 표기 4종. "첫 매치 그룹" 이란 **가장 작은 priority 값을 가진 매치 정책들의 묶음**이다(L173-174) — 낮은 숫자가 먼저.
- L16 `sync_client`: OPA 호출도 아웃바운드 팩토리를 쓰므로 egress 가드·traceparent 가 적용된다.
- L19 `DATA_CLASSES`: 순서가 곧 민감도(public < internal < confidential) — L48 의 `index` 비교가 이 순서에 의존한다. 새 등급을 중간에 끼우면 비교 의미가 바뀐다(흔한 실수).
- L20 `DECLARE_LOWER`: 기본보다 낮은 등급 신고 권한. 역할 기본 부여(`app/core/auth.py:20` user 역할, `:29` security, `:32` finops)와 스코프 묶음(`app/core/auth.py:38` `gateway:invoke`)에 포함되어 있다.
- L21 `MACHINE_KINDS`: agent 신고를 믿는 주체 — 관리자가 발급한 워크로드·Access Token, 플랫폼 위임 토큰.
- L22 `_DEFAULT_DC`: 리스트 하나에 (시각, 값) — 슬라이스 대입으로 두 값을 한 번에 바꾼다.

```python path=app/services/policy.py lines=25-39
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
```
**분석 / Analysis (Level 2)**
- L26-30: 함수 안 지연 import — `app.db`·`app.models.Setting` 순환 import 회피 목적.
- L31-34: 5초가 지났을 때만 설정을 읽는다. L32 의 `contextlib.nullcontext(db) if db is not None else SessionLocal()` 은 "호출자 세션이 있으면 그대로(닫지 않음), 없으면 새 세션을 열고 닫음" 을 한 줄로 표현하는 관용구 — 조건식이 먼저 평가된 뒤 `with` 에 들어간다. 저장값이 유효 등급이 아니면 internal(fail safe).
- L38-39: 설정 저장 API 가 호출(`app/api/admin.py:404`, `app/api/ops.py:753`) — 같은 워커만 즉시, 다른 워커는 최대 5초 뒤.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L32-33: 호출자 세션을 쓰면 SQLAlchemy identity map 에 이미 적재된 `Setting` 객체를 돌려줄 수 있다(같은 트랜잭션 안에서 다른 워커가 바꾼 값은 보이지 않음). 5초 캐시와 겹쳐 영향은 작다.

```python path=app/services/policy.py lines=42-53
def declared_data_class(db: Session | None, p: Principal, declared: str | None) -> str:
    """요청 데이터 등급 — 신고가 없으면 기본값. 기본값보다 낮은 신고는 `data:declare` 권한(역할 기본 부여, RBAC 로 회수·
    토큰 스코프로 제한)이 있어야 하고, 없으면 기본값으로 올려 감사에 남긴다. 형식 오류는 그대로 돌려 호출부가 판단."""
    default = default_data_class(db)
    if not declared:
        return default
    if declared not in DATA_CLASSES or DATA_CLASSES.index(declared) >= DATA_CLASSES.index(default) or p.has(DECLARE_LOWER):
        return declared
    from app.services import audit
    audit.append("gateway.data_class.raise", actor=p.sub, actor_kind=p.kind, decision="raised",
                 data={"declared": declared, "effective": default})
    return default
```
**분석 / Analysis (Level 2)**
- L45-47: 신고가 없으면 서버 기본값.
- L48-49: 세 경우에 신고값을 그대로 쓴다 — (a) 알 수 없는 값(**형식 오류는 호출부 몫**), (b) 기본값 이상(상향은 언제나 허용), (c) `data:declare` 권한 보유. `p.has` 는 역할 권한과 토큰 스코프를 함께 본다(`app/core/auth.py:75-80` — 스코프 토큰은 스코프 밖 권한을 거부).
- L50-53: 권한 없는 하향 신고는 기본값으로 **올리고** 감사.
**짚어볼 점 / Watch-outs**
- [위험:중간] L48 (a) 경로: **알 수 없는 등급 문자열이 그대로 통과**하고, 호출부 중 LLM(`app/api/llm.py:32-33`)·지식 검색(`app/api/knowledge.py:131`)만 검증하며 **MCP(`app/api/mcp.py:58`)·A2A(`app/api/a2a.py:48`)는 검증하지 않는다.** 이 값은 `governance.check_tool`/`check_agent` 를 거쳐 정책 입력이 되고(`app/services/mcp_gateway.py:936`, `app/services/a2a_gateway.py:453`), L141-143 의 `data_class` 조건은 정확 일치이므로 `"Confidential"`·`"secret"` 같은 값은 **어떤 등급 조건 정책에도 매치되지 않는다.** 시나리오: 보안 담당이 user 역할에서 `data:declare` 를 회수(Epic 40 의 통제)하고 "internal·confidential 데이터로 외부 SaaS 도구 호출 금지" deny 정책(조건 `data_class: [internal, confidential]`)을 두었을 때, 사용자가 `X-AgentNexus-Data-Class: none` 헤더로 MCP 도구를 부르면 deny 가 매치되지 않고 더 낮은 우선순위의 일반 allow 로 통과한다. 같은 값이 MCP sampling 의 LLM 호출로도 흘러간다(`app/services/mcp_consent.py:100` → `llm.prepare`). 최소 수정: L48 에서 `declared not in DATA_CLASSES` 이면 기본값을 돌려주고(또는 예외) 감사 — 판정 지점을 이 함수 한 곳으로 모은다.
- [위험:낮음] L48 (c): `data:declare` 가 **user 역할 기본 권한**이라(`app/core/auth.py:20`) 회수하지 않은 기본 배포에서는 누구나 `public` 으로 낮춰 신고해 등급 재라우팅·거부를 피할 수 있다 — 1차 INDEX §4.1 #4 의 "데이터 등급 자기 신고" 는 통제 수단이 생겼을 뿐 기본 동작은 그대로다(40차가 의도적으로 유지, `test/impl/40th/scenario.md:9`). 최소 수정: 기본 부여를 끄고 필요한 팀만 RBAC 로 추가하는 운영 가이드, 또는 설정 키로 기본값 전환.
- [위험:낮음] L51-52: 권한 없는 하향 신고 **요청마다** 감사 이벤트가 쓰인다 — 감사는 단일 체인 머리로 직렬화되므로(`app/services/audit.py:56-57`) 클라이언트가 헤더 하나로 감사 쓰기 부하를 키울 수 있다. 최소 수정: (주체·신고값)별 짧은 dedupe.

```python path=app/services/policy.py lines=56-73
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
```
**분석 / Analysis (Level 2)**
- L59-69: run 신고는 그 Run 의 소유자(`user_sub`)일 때만 유지, 아니면 버린다(step 도 함께, L73). 위임 토큰 주체의 `sub` 는 사용자 sub 이므로(`app/core/auth.py:225-232` — `delegation` 은 사용자 행으로 `_user_principal` 생성) 오케스트레이터 경로도 통과한다.
- L70: agent 는 `MACHINE_KINDS` 주체만 — 사람(user·pat·client_key)이 보낸 값은 None(`test/test_caller_trust.py:38-43`). 1차에서 "agent 주체 정책 위조 가능"(1차 `app/models.py.md:849`, `app/services/governance.py.md:228`)으로 지적된 경로가 닫혔다.
- L71: 본문 신고(`declared_dc`, LLM 의 `metadata.data_class`)가 헤더보다 우선.
- L72: `session_id` 는 검증 없이 그대로.
- `headers` 는 Starlette `Headers`(대소문자 무시 조회) — 소문자 키로 조회해도 된다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L72: `X-AgentNexus-Session` 은 소유 검사가 없다 — 다른 사용자의 세션 id 를 넣으면 사용 기록(`UsageRecord.session_id`, `app/services/llm.py:386`)이 남의 세션에 섞인다(관측 데이터 오염). 최소 수정: run 과 같은 방식의 소유 검사 또는 주체 sub 를 접두로 붙여 저장.
- [위험:낮음] L70: `access_token` 주체(운영자가 발급, `app/core/auth.py:246`)는 임의 agent 이름을 신고할 수 있다 — 발급 권한(`tokens:issue`)이 있는 사람에게는 agent 주체 정책을 고르는 능력이 된다. 발급 시 허용 agent 를 토큰에 묶는 것이 근본 수정.

```python path=app/services/policy.py lines=76-108
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
```
**분석 / Analysis (Level 2)**
- L76-84 `PolicyInput`: 엔진 공통 입력. `field(default_factory=dict)` 로 가변 기본값 공유 버그를 피했다(dataclass 에서 `args: dict = {}` 는 오류).
- L86-90 `to_opa`: Rego 가 `input.model`/`input.tool` 처럼 종류 이름으로도 대상을 읽을 수 있게 `self.kind: self.target` 동적 키를 넣는다. 주체는 sub·kind·역할·그룹·팀만 — 토큰 스코프·이름은 넘기지 않는다.
- L93-108 `Decision`: 결정·사유·승인 필요·정책 식별(버전 포함 — 감사·결정 로그 근거)·재라우팅·매치 추적·엔진.

```python path=app/services/policy.py lines=111-154
def _subject_match(pol: GatewayPolicy, inp: PolicyInput) -> bool:
    p = inp.principal
    v = pol.subject
    match pol.subject_type:
        case "any":
            return True
        case "user":
            return fnmatch.fnmatchcase(p.sub, v) or (p.name is not None and p.name == v)
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
```
**분석 / Analysis (Level 2)**
- L111-127 `_subject_match`: `match` 문(Python 3.10+)으로 주체 유형별 매칭. `fnmatchcase` 는 대소문자 구분 glob — `fnmatch` 와 달리 OS 별 대소문자 정규화를 하지 않아 결정이 플랫폼에 따라 달라지지 않는다(올바른 선택). 알 수 없는 유형은 False(매치 안 함 → default deny 쪽).
- L124: 팀 없는 주체는 `""` 로 비교 — `team:*` 정책은 팀 없는 주체도 매치한다.
- L130-138 `_target_match`: 종류 일치 → `category:`·`risk:` 속성 비교 → glob.
- L141-143: 정책 조건의 등급 목록이 비어 있으면 무조건, 있으면 정확 일치(L48 의 미검증 값과 결합하면 앞의 [위험:중간]).
- L146-154 `_args_violation`: 인자를 정책의 JSON Schema 로 검증. 스키마 자체 오류(`SchemaError`)는 저장 시 `Draft202012Validator.check_schema` 로 걸러진다(`app/api/gateway_admin.py:54-58`) — 그래서 여기서는 `ValidationError` 만 잡는다.
**짚어볼 점 / Watch-outs**
- [위험:중간] L118: user 유형은 sub glob 외에 **표시 이름(`p.name`) 정확 일치**도 매치로 본다. 외부 IdP 사용자의 `name` 은 로그인 때마다 IdP 클레임(`name`→`preferred_username`)으로 덮어쓰이고(`app/core/auth.py:178`), 로컬 사용자는 `user.name or user.username`(`app/core/auth.py:191`)이다. 콘솔의 정책 주체 입력은 자유 텍스트다(`web/src/pages/gateway/Gateway.tsx:556`). 시나리오: 운영자가 "kim.admin 에게 jira 쓰기 허용" 을 이름으로 적어 두면, 프로필 이름을 스스로 고칠 수 있는 IdP(예: 셀프서비스 계정 콘솔)의 다른 사용자가 표시 이름을 `kim.admin` 으로 바꿔 그 allow 를 얻는다. 반대로 이름으로 건 deny 는 이름 변경으로 회피된다(가능성 — IdP 설정에 의존). 최소 수정: user 매칭은 `sub`(와 로컬 `username`)만 — 표시 이름 매칭 제거.
- [위험:낮음] L146-154: 매 평가마다 `jsonschema.validate` 가 스키마를 다시 컴파일한다(정책 수 × 요청). 성능이 문제면 정책 버전별 검증기 캐시.

```python path=app/services/policy.py lines=157-190
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
```
**분석 / Analysis (Level 2)**
- L159-164 override(초안 회귀 시험, `app/services/policy_review.py:43`): 저장된 정책 전체를 읽고 초안으로 바꾸거나(None 이면 삭제) 추가한 뒤, 활성·종류로 거르고 priority 로 정렬. `sorted` 는 안정 정렬이라 같은 priority 안에서는 DB 의 `created_at` 순서가 유지된다(새로 추가된 초안은 끝).
- L166-168: 일반 경로는 SQL 에서 활성·종류로 거르고 정렬.
- L169-172: 주체·대상·조건이 모두 맞는 정책만, 없으면 default deny.
- L173-185: 최상위 priority 그룹 안에서 명시 deny → 인자 제한 위반 → allow 순. deny 결정에도 `data_class_routing` 의 재라우팅 대상을 실어 LLM 이 대체 모델로 다시 시도하게 한다(`app/services/llm.py:190-197`).
- L186-190: 그룹 첫 정책이 승자. 승인 필요는 **모든 priority 의 매치 allow** 중 하나라도 요구하면 True — 상위 allow 가 이겨도 하위 allow 의 승인 요구가 살아남는 보수적 설계(우회 방지).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L190: allow 결정에도 `reroute` 를 싣지만 LLM 은 거부 분기에서만 재라우팅을 본다(`app/services/llm.py:190-197`) — allow 정책의 `data_class_routing` 은 **아무 효과가 없다.** 운영자가 "허용하되 confidential 은 로컬로" 를 allow 정책에 적으면 무시된다. 최소 수정: 저장 시 allow 정책의 `data_class_routing` 을 거부하거나, LLM 에서 allow+reroute 도 처리.

```python path=app/services/policy.py lines=193-211
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
```
**분석 / Analysis (Level 2)**
- L193-201 (초점 항목 — OPA fail-open/closed): 연결 오류·타임아웃(3초)·4xx/5xx(`raise_for_status`)·JSON 오류·`result` 가 dict 가 아닌 경우(`.get` 의 `AttributeError`) **모두 deny** — fail closed(`test/test_gateway_policy.py:319-321` 가 503 을 확인). `result` 가 없으면 `{}` → allow False(정의되지 않은 규칙도 거부).
- L202-205: `allow` 는 `bool()` 로 강제 — Rego 가 문자열 `"false"` 를 주면 truthy 로 **허용**된다는 점은 Rego 작성자가 알아야 할 계약(정책 파일 `policies/agentnexus.rego` 범위).
- L208-211: `POLICY_ENGINE=opa` **이고** URL 이 있을 때만 OPA.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L209: `POLICY_ENGINE=opa` 인데 `OPA_URL` 이 비어 있으면 **조용히 내장 엔진**으로 평가한다 — 운영자가 OPA 로 판정된다고 믿는 상태에서 다른 규칙이 적용된다. 최소 수정: 기동 시 설정 검증으로 실패시키거나, 이 경우 fail closed(deny).
- [위험:낮음] L196: 평가마다 새 HTTP 클라이언트(keep-alive 없음) + 3초 타임아웃 — LLM `prepare` 는 후보마다 평가하므로(`app/services/llm.py:188`) OPA 지연 시 후보 수 × 3초. 또 OPA 결정에는 `policy_id` 가 없어(L202-205) 원격 에이전트 untrusted 등급 판정이 "일반 허용" 으로 취급된다(`app/services/governance.py:326-327` — 그 파일 범위, 가능성).

## 파일 평가 / File Assessment
- 강점: (1) default deny·같은 priority deny 우선·OPA fail closed 로 판정 실패가 거부로 귀결. (2) 신고 헤더를 서버가 판정하는 지점을 `caller_context` 한 곳으로 모았고, agent(기계 주체만)·run(소유자만) 위조 경로를 닫았다. (3) 초안 회귀 시험(override)이 같은 평가 코드를 재사용해 시험과 실제 판정이 어긋나지 않는다.
- 위험: (1) [위험:중간] 알 수 없는 데이터 등급 문자열이 통과해 MCP·A2A 에서 등급 조건 정책·`data:declare` 회수를 우회 (L48, `app/api/mcp.py:58`, `app/api/a2a.py:48`). (2) [위험:중간] user 주체를 표시 이름으로도 매치 — 외부 IdP 이름 클레임으로 정책 획득·회피 가능성 (L118, `app/core/auth.py:178`). (3) [위험:낮음] `data:declare` 기본 부여로 자기 신고 하향이 기본 허용, 세션 헤더 무검증, OPA 설정 누락 시 조용한 내장 엔진 전환 (L48·L72·L209).
- 개선 권고(영향순): (1) `declared_data_class` 에서 알 수 없는 값 → 기본값(+감사)으로 판정 통일. (2) user 매칭에서 표시 이름 제거. (3) `data:declare` 기본 부여 해제 옵션·세션 소유 검사·OPA 설정 검증.
- 파일 위험 등급 / Risk grade: 높음 — 보안 경계 핵심 모듈(인가 판정)에 미해결 [위험:중간] 2건.
- 재읽기 권고 레벨: 2 — 판정 순서(그룹·deny 우선·승인 합산)와 신고 판정 규칙이 여러 호출부의 안전성을 결정한다.

## 1차 리뷰 대비 / vs 1st Review
1차 라인 리뷰 대상이 아니므로(부록 A) 1차 INDEX·다른 1차 보고서가 이 파일을 근거로 든 항목만 비교한다.

| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| INDEX §4.1 #4 클라이언트 헤더(`Agent`·`Data-Class`·`Session/Run/Step`)를 신뢰 입력으로 사용 | 부분 해결 | agent 는 기계 주체만(`app/services/policy.py:70`), run·step 은 소유자만(`:59-69`·`:73`), 등급 하향은 `data:declare` 필요(`:48`). 그러나 `data:declare` 기본 부여(`app/core/auth.py:20`), 알 수 없는 등급 통과(`:48`), session 무검증(`:72`) |
| 1차 `app/models.py.md:849` [위험:중간]·`app/services/governance.py.md:228`·`app/services/a2a_gateway.py.md:572`: agent 주체 allow 정책이 헤더 위조로 충족(`policy.py:71-72`) | 해결 | `:21`·`:70` — user·pat·client_key 의 agent 신고는 None(`test/test_caller_trust.py:38-43`) |
| 1차 `app/services/llm.py.md` L187: `ctx.data_class` 자기 신고로 등급 재라우팅·거부 회피 | 부분 해결 | `:42-53` 하향에 권한 요구·감사 — 기본 부여라 기본 배포에서는 여전히 가능, MCP·A2A 는 형식 검증 없음 |
| 1차 `app/api/mcp.py.md:90` [위험:중간]: `agent`·`data_class`·`session_id`·`run_id` 를 헤더 그대로 사용 | 부분 해결 | 위 세 행과 동일 근거(`:56-73`) |
