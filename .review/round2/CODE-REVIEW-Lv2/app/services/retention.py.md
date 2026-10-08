> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: 1차 리뷰 대상 아님(부록 A)

### 23. app/services/retention.py
**파일 요약 / File Summary**
- 역할 / Role: 보존 정책 실행(FR-O02, COMPLIANCE CC6.5/A.8.10) — 설정 `retention`(+기본값)에 따라 텔레메트리(스팬·로그·사용 기록·실행 이벤트)의 기간 경과분을 삭제하고, MCP 실행 상태(`mcp_relays`·`mcp_tasks`)는 1일 경과분을 삭제하며, 감사 로그는 `audit.archive_before` 로 아카이브(해시 체인 증명)한 뒤 실행 결과를 감사에 남긴다.
- 진입점 거리 / Distance: 2 (`app/api/audit.py:270-277` 수동 실행, `app/api/setup.py:341,370`·`app/api/admin.py:369` 정책 조회) · 스케줄러 `app/services/scheduler.py:57-60`(1시간 주기 리스, `app/services/scheduler.py:125-126`, `app/core/config.py:53`)
- LOC: 39 (현재 `wc -l`) · 1차 INDEX 부록 A 기준 39(`.review/CODE-REVIEW-Lv2/INDEX.md:336`) → 줄 수는 같지만 내용 변경(아래)
- 주요 export: `DEFAULTS`, `STATE_DAYS`, `policy`, `run`
- 의존(내부): `app.db.utcnow`, `app.models.{LogEntry,McpRelay,McpTask,RunEvent,Setting,Span,UsageRecord}`, `app.services.audit`
- 외부 패키지: sqlalchemy(`delete` 구문)
- 부수효과 / Side effects: DB 대량 삭제·커밋, 감사 아카이브 파일 쓰기·감사 이벤트 삭제(`audit.archive_before`), 감사 이벤트 추가
- 동시성 / Concurrency: 동기. 스케줄러는 DB 리스로 워커 하나만 실행(`app/services/scheduler.py:126`), 수동 API 실행과는 상호 배제가 없다(동시에 돌면 각자 삭제 — 결과는 멱등에 가깝고 아카이브는 `audit._lock`·머리 행 잠금으로 직렬화)
- 테스트 커버리지 흔적: `test/test_observe.py:217-246`(`test_retention_archive_and_purge_proof` — 텔레메트리 삭제·아카이브·파기 증명), `test/test_review_fixes.py:153-168`(`test_retention_mcp_state` — 1일 경과 중계·작업 삭제, 최근 유지)
- 2차 변경 요약 / Changes since 1st review: 1차 라인 리뷰 대상이 아니어서 인용 원문 비교는 불가. 위임 보고서가 인용한 이전 코드(`.review/delegated/code-review.md:19-22` — 4개 텔레메트리 표만 정리)와 비교하면 L13 import 에 `McpRelay`·`McpTask`, L17 `STATE_DAYS`, L33-34 MCP 상태 삭제가 추가되었다(Epic 39 BUG-01·04·09, `test/impl/39th/scenario.md:24`).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/retention.py lines=1-17
"""보존 정책 (FR-O02 보존, COMPLIANCE: 텔레메트리 보관/삭제 검증 CC6.5/A.8.10).

Setting 'retention' = {audit_days, spans_days, logs_days, usage_days, run_events_days}. 감사 로그는 삭제하지 않고
아카이브(해시 체인 증명) 후 파기 단계로 넘기며, 텔레메트리는 기간 경과분을 삭제하고 건수를 감사에 남긴다.
MCP 중계(mcp_relays)·Tasks(mcp_tasks) 는 수명이 짧은 실행 상태라 정책과 무관하게 1일 경과분을 지운다(작업 TTL 1시간).
"""
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import LogEntry, McpRelay, McpTask, RunEvent, Setting, Span, UsageRecord
from app.services import audit

DEFAULTS = {"audit_days": 365, "spans_days": 14, "logs_days": 30, "usage_days": 400, "run_events_days": 90}
STATE_DAYS = 1
```
**분석 / Analysis (Level 2)**
- L1-5: 설계 — 감사는 "삭제" 가 아니라 "아카이브 → (별도) 파기" 두 단계, 텔레메트리는 삭제 + 건수 감사, MCP 실행 상태는 정책과 무관한 고정 1일.
- L9 `delete`: SQLAlchemy 2.0 의 **벌크 DELETE 구문** — ORM 객체를 읽지 않고 SQL 한 문장으로 지운다(빠르지만 ORM 이벤트·cascade 는 실행되지 않는다는 점이 흔한 함정 — 이 표들은 자식 관계가 없어 안전).
- L16 `DEFAULTS`: 감사 365일(1년 최소), 스팬 14일, 로그 30일, 사용 기록 400일(연간 정산 + 여유), 실행 이벤트 90일.
- L17 `STATE_DAYS`: Task TTL 은 1시간(`app/services/mcp_tasks.py:27`) — 1일이면 충분한 여유.

```python path=app/services/retention.py lines=20-39
def policy(db: Session) -> dict:
    row = db.get(Setting, "retention")
    return {**DEFAULTS, **(row.value if row and isinstance(row.value, dict) else {})}


def run(db: Session, actor: str, overrides: dict | None = None, now=None) -> dict:
    now = now or utcnow()
    pol = {**policy(db), **(overrides or {})}
    deleted = {}
    for key, model, col in (("spans_days", Span, Span.start_time), ("logs_days", LogEntry, LogEntry.ts),
                            ("usage_days", UsageRecord, UsageRecord.ts), ("run_events_days", RunEvent, RunEvent.ts)):
        cutoff = now - timedelta(days=pol[key])
        deleted[model.__tablename__] = db.execute(delete(model).where(col < cutoff)).rowcount
    for model in (McpRelay, McpTask):
        deleted[model.__tablename__] = db.execute(delete(model).where(model.created_at < now - timedelta(days=STATE_DAYS))).rowcount
    db.commit()
    archive = audit.archive_before(now - timedelta(days=pol["audit_days"]), actor)
    audit.append("retention.run", actor=actor, data={"policy": pol, "deleted": deleted,
                                                     "archive": archive["archive_id"] if archive else None})
    return {"policy": pol, "deleted": deleted, "archive": archive}
```
**분석 / Analysis (Level 2)**
- L20-22 `policy`: 저장값을 기본값 위에 덮는다(dict 가 아니면 무시 — 손상된 설정에 fail safe). 저장 시 검증은 설정 API 쪽(`app/api/setup.py:348-351` — 감사 365~3650일 등).
- L25-27: `now` 주입은 시험용. **실행 시 overrides 가 저장 정책 위에 덮인다.**
- L29-32: 텔레메트리 4종 — 기준 시각 이전 행을 벌크 DELETE, `rowcount` 로 건수(SQLite·PostgreSQL 모두 DELETE 의 영향 행 수를 돌려준다).
- L33-34 (2차 추가): MCP 중계·Task — 생성 1일 경과분 삭제. 위임 보고서 BUG-01·04 의 "무한 증가" 해결(`test/test_review_fixes.py:153-168`).
- L35: 위 6개 삭제를 **한 트랜잭션으로** 커밋.
- L36-38 (초점 항목 — 감사 체인 상호작용): 감사는 별도 트랜잭션의 `archive_before`(체인 머리 잠금, 연속 구간만, 앵커 이동 — `app/services/audit.py:126-157`) → 그다음 실행 결과를 감사 이벤트로.
**짚어볼 점 / Watch-outs**
- [위험:중간] L26-27: `overrides` 값이 **검증 없이** 저장 정책을 덮는다. 수동 실행 API 는 키 이름만 확인하고(`app/api/audit.py:270-277` — `dict[str, int]`, 키 ∈ DEFAULTS) 값의 범위는 보지 않으며, 설정 API 가 강제하는 "감사 최소 1년"(`app/api/setup.py:351` `ge=365`)도 이 경로에는 없다. 시나리오: `audit:read`+`policy:write` 를 가진 보안 담당 한 사람(역할 `security` 에 둘 다 포함, `app/core/auth.py:29`)이 `{"overrides": {"audit_days": 0, "spans_days": 0, "logs_days": 0, "usage_days": 0, "run_events_days": 0}}` 로 실행하면 모든 텔레메트리·사용 기록이 즉시 삭제되고 감사 이벤트 전체가 아카이브되며, 이어서 같은 권한으로 파기 API(`app/api/audit.py:296-299` → `app/services/audit.py:181-192`, 보관 경과 확인 없음)를 부르면 감사 원문이 사라지고 매니페스트 해시만 남는다. 음수 값은 미래 기준 시각이 되어 같은 효과다. 시험은 이 경로를 정상 기능으로 쓴다(`test/test_observe.py:228-229`, admin). 최소 수정: `run()` 안에서 `audit_days >= 365`, 나머지 `>= 1` 로 하한을 강제(시험 환경만 예외), 또는 overrides 실행을 2인 승인으로.
- [위험:낮음] L29-35: 표마다 **한 문장·한 트랜잭션** 삭제다. 보존 실행이 오래 멈췄다가 재개되면(또는 위 overrides) 수백만 행 DELETE 가 한 트랜잭션에 묶여, SQLite 에서는 그동안 DB 전체 쓰기 잠금으로 다른 쓰기(감사 append 포함)가 30초 타임아웃(`app/db.py:46`) 뒤 `database is locked` 로 실패하고, PostgreSQL 에서는 긴 트랜잭션·WAL·테이블 팽창이 생긴다. 최소 수정: PK 또는 시각 범위로 배치(예: 10,000행) 삭제 + 배치마다 커밋.
- [위험:낮음] L35-38: 텔레메트리 삭제를 **먼저 커밋**한 뒤 아카이브·감사를 한다 — `archive_before` 가 예외를 던지면(아카이브 디렉터리 쓰기 불가·디스크 가득 등) 삭제는 확정됐는데 `retention.run` 감사 이벤트(삭제 건수 증빙)는 남지 않는다. docstring 의 "건수를 감사에 남긴다" 가 깨지는 경로. 최소 수정: L35 직후 삭제 건수 감사를 먼저 남기고, 아카이브는 별도 try 로 감싸 실패도 감사.
- [위험:낮음] L29-34: 정리 대상 밖에서 계속 쌓이는 표가 있다 — 결정 로그 `RoutingDecision`(LLM 호출마다 1행, `app/services/llm.py:377`)·알림 `Notification`·토큰 교환 이력 `TokenExchange`, 외부 IdP 로그인 위임 `BrokerRequest`(PKCE `code_verifier` 평문 보관, `app/models.py:86-93`)·인가 코드·디바이스 코드 — 저장소 전역에 이 모델들의 삭제 코드가 없다(`grep "delete(RoutingDecision|Notification|..."` 결과 없음). 사용 기록은 400일로 지우는데 같은 호출의 결정 로그는 영구 보관되는 불일치. 최소 수정: 위 표들을 DEFAULTS 에 추가(결정 로그 = 사용 기록과 같은 기간, 일회용 인증 상태 = 1일).

## 파일 평가 / File Assessment
- 강점: (1) 감사는 삭제가 아니라 체인 증명이 있는 아카이브로 넘겨 보존 삭제와 무결성을 양립하고, 실행 결과(정책·건수·아카이브 id)를 감사에 남긴다. (2) 벌크 DELETE 로 ORM 적재 없이 빠르게 정리하고, 2차에서 MCP 실행 상태의 무한 증가(BUG-01·04)를 해소. (3) 손상된 정책 설정에 기본값으로 fail safe.
- 위험: (1) [위험:중간] overrides 값 무검증 — 감사 최소 1년 우회·전체 소거 경로 (L26-27, `app/api/audit.py:270-277`). (2) [위험:낮음] 무배치 단일 트랜잭션 대량 삭제 (L29-35). (3) [위험:낮음] 삭제 커밋 후 아카이브 실패 시 삭제 증빙 누락, 결정 로그·일회용 인증 상태 등 미정리 표 (L35-38·L29-34).
- 개선 권고(영향순): (1) `run()` 에서 하한 강제(감사 ≥ 365일, 그 밖 ≥ 1일). (2) 배치 삭제 + 배치별 커밋. (3) 삭제 건수 감사를 아카이브 전에, 정리 대상 표 확대.
- 파일 위험 등급 / Risk grade: 중간 — 미해결 [위험:중간] 1건(컴플라이언스 통제 우회).
- 재읽기 권고 레벨: 1 — 짧은 파일이지만 감사 아카이브·파기 API 와의 결합 효과를 함께 이해해야 한다.

## 1차 리뷰 대비 / vs 1st Review
1차 라인 리뷰 대상이 아니므로(부록 A) 1차 다른 보고서·위임 보고서 중 이 파일 관련 항목만 비교한다.

| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| 위임 `code-review.md` BUG-01 (High): `mcp_relays` 보존 정책 없음 — 무한 증가 | 해결 | `app/services/retention.py:33-34`(1일 경과 삭제), `test/test_review_fixes.py:153-168` |
| 위임 `code-review.md` BUG-04 (Medium): `McpTask` 주기 정리 없음 | 해결 | `:33-34`, 같은 시험 |
| 위임 `code-review.md` BUG-09: `_discard` 후 고아 McpRelay 잔류 | 해결(이 파일은 안전망) | 1일 경과분 일괄 삭제(`:33-34`) — `_discard` 쪽 수정은 `app/services/mcp_tasks.py` 범위 |
| 1차 `app/services/mcp_modern.py.md:320`: mcp_relays 는 삭제되지 않음 — 보존 정리 대상이어야 | 해결 | `:33-34` |
| 1차 `app/models.py.md:169`: 브로커 요청의 `code_verifier` 평문 — 만료 정리 필요(retention 범위) | 미해결 | `BrokerRequest`(`app/models.py:86-93`) 삭제 코드 없음, 정리 대상 목록 `:29-34` 에 없음 |
