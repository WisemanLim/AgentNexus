> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [audit.py.md](../../../../CODE-REVIEW-Lv2/app/services/audit.py.md)

### 22. app/services/audit.py
**파일 요약 / File Summary**
- 역할 / Role: 불변 감사 로그 — 해시 체인(`hash = sha256(prev_hash ‖ canonical_json(payload))`) append(단일 체인 머리 행 직렬화), 전체·구간 검증, 보관기간 경과 구간의 아카이브(JSONL)·파기·앵커 이동·증명 이벤트, 서명 리포트(JWT RS256) 생성·검증.
- 진입점 거리 / Distance: 2 (`app/api/audit.py:195-198`(리포트)·`:260-262`(서명 검증)·`:296-299`(파기), 그 밖에 거의 모든 서비스·API 가 `audit.append` 호출 — 예 `app/services/policy.py:51`, `app/services/llm.py:214`)
- LOC: 243 (현재 `wc -l`) · 1차 242 → +1
- 주요 export: `append`, `verify`, `verify_range`, `count`, `archive_before`, `verify_archive`, `purge_archive`, `report_events`, `build_report`, `verify_report_signature`, `payload`, `compute_hash`, `GENESIS`
- 의존(내부): `app.core.{keys,config,context,ids}`, `app.db.{session_scope,utcnow}`, `app.models.{AuditEvent,AuditChainHead,AuditArchive,AuditReport}`
- 외부 패키지: pyjwt(>=2.9, `pyproject.toml:17`), sqlalchemy
- 부수효과 / Side effects: DB 쓰기(자체 트랜잭션), 파일 쓰기(`data_dir/audit-archive/*.jsonl`·`data_dir/reports/*.jsonl`), 파일 삭제(파기), JWT 서명(IdP 서명 키)
- 동시성 / Concurrency: 프로세스 내 `threading.Lock` + PostgreSQL/MySQL `SELECT … FOR UPDATE`(체인 머리 행) — 워커·파드 간 직렬화. SQLite 는 행 잠금 없이 DB 쓰기 잠금에 의존
- 테스트 커버리지 흔적: `test/test_foundation.py:42`(`test_audit_chain_detects_tampering`)·`:70`, `test/test_ops.py:310`(`test_audit_range_verify_and_report_wizard`), `test/test_observe.py:217`(`test_retention_archive_and_purge_proof`), `test/test_review_fixes.py:477-479`(`test_report_signature_rejects_user_token`)
- 2차 변경 요약 / Changes since 1st review: 1차 인용 원문과 비교 — L234-243 `verify_report_signature` 만 바뀌었다: `issuer="agentnexus-audit"` 검증과 `require=["iss","report_id","sha256"]` 추가, 키 공유 사실을 주석으로 명시(L236). Epic 39 "Lv2 리포트 서명"(`test/impl/39th/scenario.md:40`). 나머지(L1-231)는 1차 원문과 동일.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/audit.py lines=1-27
"""불변 감사 로그 — 해시 체인 (FR-O05, SCR-K09).

hash = sha256(prev_hash || canonical_json(payload)). 체인 머리(audit_chain_head)는 append 마다 행 잠금으로 직렬화한다.
보관기간 경과 구간은 아카이브 파일로 내보낸 뒤 파기하고, 머리의 anchor 로 검증 시작점을 옮긴다(삭제 증명).
"""
import json
import threading
from datetime import datetime
from pathlib import Path

import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import keys
from app.core.config import settings
from app.core.context import current_trace_id
from app.core.ids import canonical_json, new_id, sha256_hex
from app.db import session_scope, utcnow
from app.models import AuditArchive, AuditChainHead, AuditEvent, AuditReport

GENESIS = "0" * 64
_lock = threading.Lock()


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="microseconds")
```
**분석 / Analysis (Level 2)**
- L1-5: 설계 — 체인 머리 행 하나를 append 마다 잠가 순서를 직렬화하고, 아카이브 후에는 머리의 앵커(anchor_seq·anchor_hash)로 검증 시작점을 옮긴다.
- L22 `GENESIS`: 첫 이벤트의 prev_hash(0×64).
- L23 `_lock`: **같은 프로세스** 스레드 간 직렬화(워커 간은 DB 잠금 몫). `threading.Lock` 은 재진입 불가 — 잠금을 쥔 채 `append` 를 다시 부르면 교착하므로 `archive_before`·`purge_archive` 는 블록을 빠져나온 뒤 append 한다(L156·L191).
- L26-27 `_iso`: 마이크로초까지 고정 포맷 — 재계산 시 직렬화 차이로 해시가 깨지지 않게. SQLite 는 naive datetime 으로 저장되는데 `app/db.py:17-18` 의 `UTCDateTime`이 읽을 때 tz 를 붙여 주므로 `+00:00` 표기가 일관된다(이 전제가 깨지면 모든 해시가 어긋남 — 흔한 함정).

```python path=app/services/audit.py lines=30-49
def payload(ev: AuditEvent) -> dict:
    return {"seq": ev.seq, "event_id": ev.event_id, "ts": _iso(ev.ts), "actor": ev.actor, "actor_kind": ev.actor_kind,
            "action": ev.action, "target": ev.target, "decision": ev.decision, "trace_id": ev.trace_id,
            "data": ev.data or {}}


def compute_hash(prev_hash: str, body: dict) -> str:
    return sha256_hex(prev_hash + canonical_json(body))


def _head(db: Session, lock: bool) -> AuditChainHead:
    stmt = select(AuditChainHead).where(AuditChainHead.id == 1)
    if lock and db.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    head = db.scalars(stmt).first()
    if head is None:
        head = AuditChainHead(id=1, last_seq=0, last_hash=GENESIS, anchor_seq=0, anchor_hash=GENESIS)
        db.add(head)
        db.flush()
    return head
```
**분석 / Analysis (Level 2)**
- L30-33 `payload`: 해시 대상 필드(seq·id·시각·행위자·행동·대상·결정·trace·데이터). `prev_hash`·`hash` 는 대상이 아니다(prev 는 L37 에서 앞에 붙음).
- L36-37: `canonical_json` = 키 정렬·공백 없음·비ASCII 유지·`default=str`(`app/core/ids.py:28-30`) — 같은 데이터가 늘 같은 문자열이 되어야 재계산이 가능하다.
- L40-49 `_head` (초점 항목 — 단일 머리 직렬화): PG·MySQL 이면 `FOR UPDATE` 로 머리 행을 잠가 다른 워커·파드의 append 가 커밋까지 기다린다. 머리가 없으면 생성.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L42 (1차 이월): SQLite 는 행 잠금이 없고 `_lock` 은 프로세스 내만 직렬화한다 — `UVICORN_WORKERS>1` + SQLite 조합에서는 두 append 가 같은 머리를 읽고 같은 `seq` 를 만들려다 한쪽이 `database is locked`/PK 충돌로 실패해 **그 감사 기록이 유실**된다(체인 손상은 아님). 기본 local 은 1 워커(`.env.local:15`), a2a-grpc 컨테이너는 DB 를 쓰지 않는 위임 프로세스(`docker-compose.yml:61-64`)라 현재 배포에서는 완화. 최소 수정: SQLite 면 `BEGIN IMMEDIATE`(쓰기 잠금 선점) 또는 기동 시 SQLite + 다중 워커 거부.
- [위험:낮음] L44-48 (1차 이월): 머리 행이 아직 없을 때 `FOR UPDATE` 는 잠글 행이 없어 두 워커가 동시에 `id=1` 을 INSERT → 한쪽 PK 충돌(첫 이벤트 한정). 최소 수정: 마이그레이션에서 머리 행을 미리 생성.

```python path=app/services/audit.py lines=52-69
def append(action: str, *, actor: str = "system", actor_kind: str = "system", target: str | None = None,
           decision: str | None = None, data: dict | None = None, trace_id: str | None = None) -> AuditEvent:
    """감사 이벤트 1건을 체인에 추가 (자체 트랜잭션)."""
    clean = json.loads(canonical_json(data or {}))
    with _lock, session_scope() as db:
        head = _head(db, lock=True)
        # seq 는 머리 기준으로 명시 할당 — SQLite 는 삭제(아카이브) 후 rowid 를 재사용하므로 DB 자동 증가에 의존하지 않는다
        ev = AuditEvent(seq=head.last_seq + 1, event_id=new_id("aud"), ts=utcnow(), actor=actor, actor_kind=actor_kind,
                        action=action, target=target, decision=decision, trace_id=trace_id or current_trace_id(),
                        data=clean, prev_hash=head.last_hash, hash="")
        db.add(ev)
        db.flush()
        ev.hash = compute_hash(ev.prev_hash, payload(ev))
        head.last_seq = ev.seq
        head.last_hash = ev.hash
        db.flush()
        db.expunge(ev)
    return ev
```
**분석 / Analysis (Level 2)**
- L55: 데이터를 정규 JSON 으로 왕복 — `datetime`·`Decimal` 등이 문자열이 되어, 저장 후 다시 읽어 해시를 재계산해도 같은 값이 된다(저장 전 정규화가 핵심).
- L56: `with _lock, session_scope() as db:` — 프로세스 락 → 자체 트랜잭션(성공 커밋·실패 롤백, `app/db.py:75-85`). 호출자 세션과 **별도 연결**이다.
- L58-61: seq 를 머리 기준으로 명시 할당(SQLite 의 rowid 재사용 회피 — 주석의 근거).
- L62-67: INSERT → flush → 해시 계산 → 머리 갱신 → flush. 커밋은 `session_scope` 종료 시 — 커밋 순간 PG 행 잠금이 풀린다.
- L68: 세션에서 분리해 반환 — 세션이 닫혀도 속성 접근 가능(`expire_on_commit=False` 와 함께, `app/db.py:56`).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 성능] L56-67 (1차 이월): 모든 감사가 **머리 행 하나**에서 직렬화된다. 대기 중인 append 는 각자 풀 연결을 하나씩 쥐고 기다리므로(`pool_size=10`·`max_overflow=20`·`pool_timeout=10`, `app/db.py:51-52`), 머리 잠금이 오래 잡히면(아래 `archive_before`) 연결 풀이 고갈되어 감사와 무관한 요청까지 10초 뒤 실패한다. 또 PG 기본 설정에는 잠금 대기 제한(`lock_timeout`)이 없다. 최소 수정: append 트랜잭션에 `SET LOCAL lock_timeout`, 장기적으로 비동기 큐잉·배치 append.
- (1차 이월) 감사 실패는 호출자에게 전파된다 — 대부분의 호출부가 업무 커밋 **뒤에** append 하므로(예: `app/api/gateway_admin.py:575-578`) 감사 실패 시 "업무는 반영·감사는 없음·응답은 500" 이 된다(fail open 감사).

```python path=app/services/audit.py lines=72-88
def verify(db: Session) -> dict:
    """앵커 이후 전 구간 재계산. 링크 단절·내용 변조·꼬리 절단을 탐지한다."""
    head = db.scalars(select(AuditChainHead).where(AuditChainHead.id == 1)).first()
    anchor_seq = head.anchor_seq if head else 0
    prev = head.anchor_hash if head else GENESIS
    checked, last_seq = 0, anchor_seq
    stmt = (select(AuditEvent).where(AuditEvent.seq > anchor_seq).order_by(AuditEvent.seq)
            .execution_options(populate_existing=True))  # 세션 캐시가 아닌 DB 값으로 재계산
    for ev in db.scalars(stmt).yield_per(500):
        if ev.prev_hash != prev:
            return _result(False, checked, anchor_seq, last_seq, ev.seq, "link_broken")
        if compute_hash(ev.prev_hash, payload(ev)) != ev.hash:
            return _result(False, checked, anchor_seq, last_seq, ev.seq, "content_tampered")
        prev, last_seq, checked = ev.hash, ev.seq, checked + 1
    if head and prev != head.last_hash:
        return _result(False, checked, anchor_seq, last_seq, last_seq + 1, "tail_truncated")
    return _result(True, checked, anchor_seq, last_seq, None, None)
```
**분석 / Analysis (Level 2)**
- L74-77: 머리의 앵커부터 시작(아카이브된 앞부분은 아카이브 파일 검증 몫).
- L78-80: `populate_existing=True` — 같은 세션에 이미 적재된 객체가 있어도 **DB 값으로 덮어써** 재계산한다(세션 캐시를 보면 DB 변조를 놓친다 — 중요한 세부). `yield_per(500)` 로 500행씩 스트리밍해 메모리 상한.
- L81-85: 링크(prev_hash) 단절 → 내용 변조(재계산 불일치) 순으로 판정.
- L86-87: 마지막 해시가 머리와 다르면 꼬리 절단(마지막 이벤트들을 지운 경우).
**짚어볼 점 / Watch-outs**
- (1차 이월) 해시 체인은 **탐지**용이다 — DB 쓰기 권한자는 머리까지 포함해 체인 전체를 재계산할 수 있다. 외부 앵커(머리 해시를 주기적으로 서명해 외부 저장)가 있어야 방지력이 생긴다. 전체 검증은 O(n) — 체인이 길면 `/api/v1/audit/verify` 와 리포트 생성(L213)이 느려진다.

```python path=app/services/audit.py lines=91-116
def verify_range(db: Session, from_seq: int, to_seq: int) -> dict:
    """구간 검증 (SCR-K09 ③) — from_seq 직전 해시부터 to_seq 까지 링크·내용을 다시 계산한다."""
    head = db.scalars(select(AuditChainHead).where(AuditChainHead.id == 1)).first()
    anchor_seq = head.anchor_seq if head else 0
    start = max(from_seq, anchor_seq + 1)
    before = db.scalars(select(AuditEvent).where(AuditEvent.seq == start - 1)).first()
    prev = before.hash if before else (head.anchor_hash if head else GENESIS)
    checked, last_seq = 0, start - 1
    stmt = (select(AuditEvent).where(AuditEvent.seq >= start, AuditEvent.seq <= to_seq).order_by(AuditEvent.seq)
            .execution_options(populate_existing=True))
    for ev in db.scalars(stmt):
        if ev.prev_hash != prev:
            return {**_result(False, checked, anchor_seq, last_seq, ev.seq, "link_broken"), "range": [start, to_seq]}
        if compute_hash(ev.prev_hash, payload(ev)) != ev.hash:
            return {**_result(False, checked, anchor_seq, last_seq, ev.seq, "content_tampered"), "range": [start, to_seq]}
        prev, last_seq, checked = ev.hash, ev.seq, checked + 1
    return {**_result(True, checked, anchor_seq, last_seq, None, None), "range": [start, to_seq], "last_hash": prev}


def _result(valid: bool, checked: int, anchor_seq: int, last_seq: int, broken_at: int | None, reason: str | None):
    return {"valid": valid, "checked": checked, "anchor_seq": anchor_seq, "last_seq": last_seq,
            "broken_at": broken_at, "reason": reason}


def count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AuditEvent)) or 0
```
**분석 / Analysis (Level 2)**
- L95: 시작을 앵커 이후로 당긴다(아카이브된 seq 는 DB 에 없음).
- L96-97: 시작 직전 이벤트의 해시가 기준점 — 직전이 앵커(삭제됨)면 머리의 `anchor_hash`.
- L99-107: 구간 재계산. `yield_per` 가 없어(L101) 구간이 넓으면 전체를 메모리로 읽는다(전체 검증 L80 과 비대칭).
- L110-116: 결과 dict·전체 건수.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L99-107: 구간 검증은 "구간 안의 링크·내용" 만 보고 꼬리 절단은 보지 않는다 — `to_seq` 가 실제 마지막 seq 보다 크면 지워진 꼬리를 정상으로 보고한다(`checked` 로만 드러남). 최소 수정: `last_seq < min(to_seq, head.last_seq)` 이면 `range_incomplete` 로 표시.

```python path=app/services/audit.py lines=119-157
# ───────────────────────── 보관기간 경과 아카이브·파기 (COMPLIANCE testing_additions) ─────────────────────────
def _archive_dir() -> Path:
    p = Path(settings.data_dir) / "audit-archive"
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_before(before: datetime, actor: str) -> dict | None:
    """before 이전 이벤트를 JSONL 로 내보내고 삭제한 뒤, 체인 앵커를 옮기고 'audit.archive' 이벤트로 증명을 남긴다."""
    with _lock, session_scope() as db:
        head = _head(db, lock=True)
        rows = list(db.scalars(select(AuditEvent).where(AuditEvent.seq > head.anchor_seq, AuditEvent.ts < before)
                               .order_by(AuditEvent.seq)))
        # 연속 구간만 (앵커 직후부터) — 중간에 보관기간 이내 이벤트가 끼면 거기서 멈춘다
        chain, prev = [], head.anchor_hash
        for ev in rows:
            if ev.prev_hash != prev:
                break
            chain.append(ev)
            prev = ev.hash
        if not chain:
            return None
        aid = new_id("arc")
        path = _archive_dir() / f"{aid}.jsonl"
        lines = [canonical_json({**payload(ev), "prev_hash": ev.prev_hash, "hash": ev.hash}) for ev in chain]
        content = "\n".join(lines) + "\n"
        path.write_text(content, encoding="utf-8")
        arc = AuditArchive(id=aid, from_seq=chain[0].seq, to_seq=chain[-1].seq, first_prev_hash=chain[0].prev_hash,
                           last_hash=chain[-1].hash, event_count=len(chain), file_path=str(path),
                           file_sha256=sha256_hex(content))
        db.add(arc)
        for ev in chain:
            db.delete(ev)
        head.anchor_seq, head.anchor_hash = chain[-1].seq, chain[-1].hash
        db.flush()
        out = {"archive_id": aid, "from_seq": arc.from_seq, "to_seq": arc.to_seq, "count": arc.event_count,
               "file_sha256": arc.file_sha256, "last_hash": arc.last_hash}
    append("audit.archive", actor=actor, target=aid, decision="archived", data=out)
    return out
```
**분석 / Analysis (Level 2)**
- L128-129: append 와 같은 프로세스 락 + 머리 행 잠금 — 아카이브 중에는 새 append 가 끼어들지 못한다(앵커 이동과 경합 방지).
- L130-138: 앵커 이후·기준 시각 이전 이벤트 중 **연속 구간만**(링크가 끊기면 멈춤 — 변조된 구간을 아카이브로 "세탁" 하지 못하게).
- L141-148: JSONL(이벤트 + prev/hash) → 파일 SHA-256 을 아카이브 행에. 파일을 **커밋 전에** 쓴다 — 커밋 실패 시 고아 파일(무해), 반대 순서는 파일 실패 시 이벤트 유실이라 현재 순서가 더 안전(1차 분석 유지).
- L150-153: 이벤트 삭제(ORM 이 PK 기준 DELETE 를 묶어 실행) → 앵커 이동.
- L156: 트랜잭션이 끝난 뒤 증명 이벤트 `audit.archive`.
- 초점 항목(보존 삭제와 체인 상호작용): 호출은 `app/services/retention.py:36` 에서 `now - audit_days` 기준. 스케줄러는 1시간마다(`app/core/config.py:53`, `app/services/scheduler.py:125-126` 리스) 실행되므로 평상시에는 1시간 분량씩 옮긴다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L128-153: 대상 전체를 **메모리로 읽고**(L130-131, 1차 이월) 그동안 `_lock` 과 머리 행 잠금을 쥔다 — 평상시(1시간 분량)는 짧지만, 보존 실행이 오래 멈췄다가 재개되거나 보존 일수를 크게 줄여 실행하면(`app/services/retention.py:25-27` 의 무검증 overrides) 수백만 행을 적재·직렬화·삭제하는 동안 **모든 워커의 append 가 대기**하고 위 연결 풀 고갈로 이어진다. 최소 수정: 배치(예: 5,000행) 단위로 아카이브 파일을 여러 개 만들고 배치마다 커밋.
- [위험:낮음] L145 (1차 이월): `write_text` 는 원자적 쓰기가 아니다(중간 실패 시 잘린 파일) — 임시 파일 + `os.replace` 패턴.
- [위험:낮음] L153-156: 아카이브 커밋 뒤 증명 이벤트 append 가 실패하면 "이벤트는 삭제·앵커는 이동·증명 이벤트는 없음" 상태가 된다(아카이브 행은 남아 사후 확인은 가능). 최소 수정: 증명 이벤트를 같은 트랜잭션 안에서 직접 체인에 추가하는 내부 함수(락 재진입 없이).

```python path=app/services/audit.py lines=160-178
def verify_archive(arc) -> dict:
    """아카이브 파일 무결성 — 파일 해시 일치 + 내부 체인(first_prev_hash → last_hash) 재계산."""
    p = Path(arc.file_path)
    if arc.purged_at or not p.exists():
        return {"archive_id": arc.id, "file_present": False, "purged_at": arc.purged_at, "file_sha256": arc.file_sha256,
                "last_hash": arc.last_hash, "valid": None}
    content = p.read_text(encoding="utf-8")
    if sha256_hex(content) != arc.file_sha256:
        return {"archive_id": arc.id, "file_present": True, "valid": False, "reason": "file_hash_mismatch"}
    prev = arc.first_prev_hash
    for line in content.splitlines():
        rec = json.loads(line)
        body = {k: rec[k] for k in ("seq", "event_id", "ts", "actor", "actor_kind", "action", "target", "decision",
                                    "trace_id", "data")}
        if rec["prev_hash"] != prev or compute_hash(prev, body) != rec["hash"]:
            return {"archive_id": arc.id, "file_present": True, "valid": False, "reason": "chain_broken",
                    "at_seq": rec["seq"]}
        prev = rec["hash"]
    return {"archive_id": arc.id, "file_present": True, "valid": prev == arc.last_hash, "events": arc.event_count}
```
**분석 / Analysis (Level 2)**
- L162-165: 파기됐거나 파일이 없으면 판정 불가(`valid: None`) + 남은 매니페스트(해시) 반환.
- L166-168: 파일 SHA-256 이 매니페스트와 같은지.
- L169-178: 파일 안 체인을 `first_prev_hash` 부터 재계산하고 마지막이 `last_hash` 인지. 파일 레코드에서 해시 대상 키만 골라(L172-173) `payload()` 와 같은 모양을 만든다 — 두 곳의 키 목록이 어긋나면 모든 아카이브가 깨진 것으로 보이므로 함께 바꿔야 한다(흔한 실수).
**짚어볼 점 / Watch-outs**
- (1차 이월) L162 DB 에 저장된 경로를 그대로 연다 — DB 쓰기 권한이 있으면 임의 파일 읽기(해시 비교 결과만 노출)로 이어지지만 신뢰 경계 안.

```python path=app/services/audit.py lines=181-192
def purge_archive(arc_id: str, actor: str) -> dict:
    """아카이브 파일 파기 — 매니페스트(해시)는 남겨 '무엇을 파기했는지' 증명한다."""
    with session_scope() as db:
        arc = db.get(AuditArchive, arc_id)
        if arc is None:
            raise ValueError("archive not found")
        Path(arc.file_path).unlink(missing_ok=True)
        arc.purged_at = utcnow()
        out = {"archive_id": arc.id, "from_seq": arc.from_seq, "to_seq": arc.to_seq, "file_sha256": arc.file_sha256,
               "last_hash": arc.last_hash, "purged_at": arc.purged_at.isoformat()}
    append("audit.archive.purge", actor=actor, target=arc_id, decision="purged", data=out)
    return out
```
**분석 / Analysis (Level 2)**
- L183-190: 파일 삭제 + 파기 시각 기록(매니페스트 해시는 남김) → 트랜잭션 종료 후 증명 이벤트.
- L187: 파일을 **DB 커밋 전에** 지운다 — 커밋이 실패하면 파일은 사라졌는데 `purged_at` 은 비어 있는 상태가 된다(검증은 `file_present: False` 로 드러남).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L181-192: 파기 전에 "보관기간이 지났는지" 를 확인하지 않는다 — 호출 API 는 `audit:read`+`policy:write`(security 역할 보유, `app/api/audit.py:296-297`)만 요구하므로, 방금 만든 아카이브도 즉시 파기할 수 있다. `app/services/retention.py` 의 무검증 overrides(audit_days=0)와 결합하면 한 사람이 감사 원문 전체를 아카이브→파기할 수 있다(`retention.py` 보고서 [위험:중간]). 최소 수정: `arc` 의 마지막 이벤트 시각 + 최소 보관(365일) 경과 전에는 거부, 또는 2인 승인.

```python path=app/services/audit.py lines=195-231
# ───────────────────────── 서명 리포트 (SCR-K10) ─────────────────────────
def report_events(db: Session, since: datetime | None, until: datetime | None, actions: list[str] | None,
                  actors: set[str] | None = None, decision: str | None = None) -> list[AuditEvent]:
    stmt = select(AuditEvent).order_by(AuditEvent.seq)
    if since:
        stmt = stmt.where(AuditEvent.ts >= since)
    if until:
        stmt = stmt.where(AuditEvent.ts <= until)
    if decision:
        stmt = stmt.where(AuditEvent.decision == decision)
    return [e for e in db.scalars(stmt) if (not actions or any(e.action.startswith(a) for a in actions))
            and (actors is None or e.actor in actors)]


def build_report(db: Session, *, actor: str, since: datetime | None, until: datetime | None,
                 actions: list[str] | None, actors: set[str] | None = None, decision: str | None = None,
                 meta: dict | None = None) -> dict:
    events = report_events(db, since, until, actions, actors, decision)
    chain = verify(db)
    rid = new_id("rpt")
    lines = [canonical_json({**payload(e), "prev_hash": e.prev_hash, "hash": e.hash}) for e in events]
    content = "\n".join(lines) + ("\n" if lines else "")
    digest = sha256_hex(content)
    path = _archive_dir().parent / "reports"
    path.mkdir(parents=True, exist_ok=True)
    fpath = path / f"{rid}.jsonl"
    fpath.write_text(content, encoding="utf-8")
    claims = {"report_id": rid, "sha256": digest, "count": len(events), "chain_valid": chain["valid"],
              "since": since.isoformat() if since else None, "until": until.isoformat() if until else None,
              "actions": actions or [], "issued_at": int(utcnow().timestamp()), "iss": "agentnexus-audit"}
    signature = jwt.encode(claims, keys.signing_key(), algorithm="RS256", headers={"kid": keys.kid(), "typ": "JWT"})
    rep = AuditReport(id=rid, created_by=actor, range_from=since, range_to=until, filters={"actions": actions or [], **(meta or {})},
                      file_path=str(fpath), sha256=digest, signature=signature, event_count=len(events),
                      chain_valid=bool(chain["valid"]))
    db.add(rep)
    db.commit()
    return {"id": rid, "sha256": digest, "count": len(events), "chain": chain, "signature": signature}
```
**분석 / Analysis (Level 2)**
- L196-206 `report_events`: 기간·결정은 SQL, 행동 접두·행위자는 Python 필터(기간 내 전체를 메모리로 — 1차 이월).
- L209-231 `build_report`: 이벤트 → JSONL·SHA-256 → 전체 체인 검증 결과 포함 → 클레임(리포트 id·해시·건수·체인 유효·범위·`iss=agentnexus-audit`)을 **IdP 서명 키**로 RS256 서명 → 리포트 행 커밋.
- L222-224: `exp` 가 없다 — 리포트 서명은 영구 증빙이므로 의도에 맞다. 대신 키가 바뀌면 검증이 불가능해진다(아래).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L218-221: 리포트 파일은 **정리되지 않고** 감사 원문(개인정보 포함 가능)을 평문으로 `data_dir/reports` 에 쌓는다. 생성 권한은 `audit:read`(`app/api/audit.py:195-196`)이고 생성마다 전체 체인 검증(O(n), L213)도 수행 — 반복 생성으로 디스크·CPU 를 소모시킬 수 있다. 최소 수정: 리포트 보관 기간·개수 상한, 파일 권한 0600.
- [위험:낮음] L225 (1차 이월): 사용자 토큰과 같은 서명 키 — 검증 측 클레임 분리(L237-238)로 혼용 공격은 막혔지만 키가 하나라(`app/core/keys.py:50-72`, 회전·이전 키 목록 없음) 서명 키를 교체하면 **과거 리포트 서명이 모두 검증 불가**가 된다. 최소 수정: 감사 전용 키 + `kid` 별 공개키 보관.

```python path=app/services/audit.py lines=234-243
def verify_report_signature(signature: str, content_sha256: str | None = None) -> dict:
    try:
        # 같은 서명 키로 사용자 토큰도 발급한다 — 리포트 발급자·필수 클레임까지 맞아야 리포트 서명으로 인정
        claims = jwt.decode(signature, keys.signing_key().public_key(), algorithms=["RS256"], issuer="agentnexus-audit",
                            options={"verify_aud": False, "require": ["iss", "report_id", "sha256"]})
    except jwt.PyJWTError as exc:
        return {"valid": False, "reason": f"signature: {exc}"}
    if content_sha256 and claims.get("sha256") != content_sha256:
        return {"valid": False, "reason": "content_hash_mismatch", "claims": claims}
    return {"valid": True, "claims": claims}
```
**분석 / Analysis (Level 2)**
- L237-238 (2차 수정): 알고리즘 RS256 고정(알고리즘 혼동 방지), `issuer="agentnexus-audit"` 일치, `iss`·`report_id`·`sha256` 필수 — 같은 키로 서명된 **사용자 액세스 토큰**은 발급자(`security.issuer()`)가 달라 거부된다(`test/test_review_fixes.py:477-479`). 반대 방향(리포트 JWT 를 액세스 토큰으로)도 `app/core/auth.py:219` 의 발급자 분기에서 막힌다.
- L239-240: 서명 오류는 사유와 함께 invalid.
- L241-243: 내용 해시가 주어지면 클레임과 비교.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L241: 내용을 주지 않으면(`app/api/audit.py:255-262` 에서 `content` 는 선택) 서명만 맞으면 `valid: True` — 화면이 "리포트 유효" 로 표시하면 **내용 위변조 여부는 확인되지 않은** 상태를 유효로 오해할 수 있다. 최소 수정: 내용 없이 검증하면 `content_checked: False` 를 함께 반환.

## 파일 평가 / File Assessment
- 강점: (1) 정규 JSON·고정 시각 포맷·`populate_existing` 으로 재현 가능한 해시 체인과 캐시 우회 검증. (2) PG/MySQL 행 잠금 + 명시적 seq 할당으로 다중 워커에서도 체인이 갈라지지 않고, 연속 구간 아카이브·앵커·파기 매니페스트로 보존 삭제와 무결성을 양립. (3) 2차에서 리포트 서명 검증에 발급자·필수 클레임을 추가해 키 공유로 인한 혼용 공격을 차단.
- 위험: (1) [위험:낮음] 단일 머리 잠금의 처리량·대량 아카이브 중 전역 append 대기·연결 풀 고갈 가능성 (L56·L128-153). (2) [위험:낮음] 파기 시 보관기간 미확인 — retention overrides 와 결합 시 감사 원문 소거 (L181-192). (3) [위험:낮음] SQLite 다중 프로세스 유실·머리 생성 경합, 리포트 키 회전 불가·리포트 파일 무정리 (L42-48·L225·L218-221).
- 개선 권고(영향순): (1) 파기에 최소 보관 경과·2인 승인 요구. (2) 아카이브 배치화 + append `lock_timeout`. (3) 감사 전용 서명 키·kid 별 공개키 보관, 리포트 보관 상한, 머리 행 사전 생성.
- 파일 위험 등급 / Risk grade: 낮음 — 미해결 지적이 모두 [위험:낮음]이며 체인 무결성 핵심 경로(append·verify)는 정확하다.
- 재읽기 권고 레벨: 3 — 정확성은 높지만 잠금 범위·트랜잭션 경계가 성능·가용성을 좌우하므로 운영 부하 관점으로 다시 볼 것.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| L30-49 [위험:낮음] SQLite 행 잠금 없음 — 다중 프로세스에서 감사 유실 | 미해결(현 배포는 1 워커로 완화) | `app/services/audit.py:42`, `.env.local:15` |
| L30-49 머리 생성 동시 경쟁 PK 충돌 | 미해결 | `:44-48` |
| L52-69 [위험:낮음 · 성능] 단일 머리 직렬화·`_lock` 을 I/O 동안 보유 | 미해결 | `:56-67` |
| L52-69 호출부 트랜잭션 안 append 시 SQLite 잠금 대기 | 미해결(관례 의존) | `:56` 별도 세션 |
| L52-69 감사 실패가 호출자에게 전파(정책 불일치) | 미해결 | `:56` `session_scope` 예외 전파 |
| L72-116 해시 체인은 탐지용 — 외부 앵커 필요 | 미해결 | `:72-88` |
| L72-116 전체 검증 O(n) | 미해결 | `:80`, 리포트마다 호출 `:213` |
| L119-157 [위험:낮음] 커밋 전 파일 쓰기(고아 파일) | 미해결(현 순서 유지가 합리적) | `:145` |
| L119-157 대상 전체 메모리 적재 | 미해결 | `:130-131` |
| L119-157 `write_text` 비원자적 | 미해결 | `:145`·`:221` |
| L160-192 DB 저장 경로를 그대로 열고 삭제 | 미해결(신뢰 경계 안) | `:162`·`:187` |
| L195-242 [위험:낮음 · 키 용도 혼용] 같은 키 + 검증이 iss·필수 클레임 미확인 → 사용자 토큰 통과 | 해결(검증 측) · 키 공유는 잔존 | `:237-238`, `test/test_review_fixes.py:477-479`; 키 단일 `app/core/keys.py:50` |
| L195-242 행동·행위자 Python 필터 | 미해결 | `:205-206` |
| L195-242 리포트마다 전체 체인 검증 | 미해결 | `:213` |
| INDEX §4.3 보조 핫스팟: audit 단일 머리 직렬화 처리량·리포트 서명 키 공유 | 부분 해결 | 서명 검증 분리(`:237-238`), 직렬화 구조 그대로(`:56`) |
