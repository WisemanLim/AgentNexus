> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 31. app/services/audit.py
**파일 요약 / File Summary**
- 역할 / Role: 불변 감사 로그 — 해시 체인(`hash = sha256(prev_hash ‖ canonical_json(payload))`) append(체인 머리 직렬화), 전체·구간 검증, 보관기간 경과 구간 아카이브(JSONL)·파기·앵커 이동·증명 이벤트, 서명 리포트(JWT RS256).
- 진입점 거리 / Distance: 2 (내부 in-degree 28 — 거의 모든 서비스·API 가 `audit.append` 호출)
- LOC: 242
- 주요 export: `append`, `verify`, `verify_range`, `count`, `archive_before`, `verify_archive`, `purge_archive`, `report_events`, `build_report`, `verify_report_signature`, `payload`, `compute_hash`, `GENESIS`
- 의존(내부): `app.core.{keys,config,context,ids}`, `app.db.{session_scope,utcnow}`, `app.models.{AuditEvent,AuditChainHead,AuditArchive,AuditReport}`
- 외부 패키지: pyjwt, sqlalchemy
- 부수효과 / Side effects: DB 쓰기(자체 트랜잭션), 파일 쓰기(`data_dir/audit-archive`·`reports`), 파일 삭제(파기)
- 동시성 / Concurrency: 프로세스 내 `threading.Lock` + PostgreSQL `SELECT … FOR UPDATE`(SQLite 는 DB 쓰기 잠금에 의존)
- 테스트 커버리지 흔적: `test/test_foundation.py`(해시 체인), `test/test_observe.py`(아카이브·리포트)

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
- L1-5: 설계 — 체인 머리 행을 append 마다 잠가 직렬화, 아카이브 후 앵커로 검증 시작점을 옮긴다(삭제 증명).
- L22 `GENESIS`: 첫 이벤트의 prev_hash(0×64).
- L23 `_lock`: 같은 프로세스 안 스레드 간 직렬화.
- L26-27 `_iso`: 마이크로초까지 고정 포맷 — 해시 재계산이 직렬화 차이로 깨지지 않게.

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
- L30-33 `payload`: 해시 대상 필드(seq·id·시각·행위자·행동·대상·결정·trace·데이터).
- L36-37 `compute_hash`: `sha256(prev_hash + canonical_json(body))` — 키 정렬·공백 없는 정규 JSON(`app/core/ids.py:28-30`).
- L40-49 `_head`: 체인 머리 조회 — PG 면 `FOR UPDATE`(행 잠금), 없으면 생성.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L42 SQLite 는 행 잠금이 없고 `_lock` 은 **프로세스 내**만 직렬화한다 — 다중 프로세스(워커 2+)가 SQLite 를 쓰면 두 append 가 같은 머리를 읽고 같은 `seq` 를 만들 수 있다. 실제로는 SQLite 의 쓰기 잠금 때문에 한쪽이 `database is locked`·PK 충돌로 **실패**(체인 손상은 아님)하고, 그 감사 기록이 유실된다. local 모드는 단일 워커라 완화된다.
- L48 머리 생성도 동시 경쟁 시 PK 충돌 가능(첫 이벤트에 한정).

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
- L55 데이터를 정규 JSON 왕복으로 정리(`default=str` — datetime 등이 문자열화되어 해시 재계산 시 같은 값).
- L56 `with _lock, session_scope() as db:` — 프로세스 락 + 자체 트랜잭션(성공 커밋·실패 롤백).
- L58-61: `seq` 를 머리 기준으로 **명시 할당**(SQLite 가 삭제 후 rowid 를 재사용하는 문제 회피 — 주석의 근거).
- L62-67: 추가 → flush → 해시 계산(seq 가 정해진 뒤) → 머리 갱신.
- L68: 세션에서 분리(expunge)해 반환 — 세션 닫힌 뒤에도 속성 접근 가능.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 성능] 모든 감사 기록이 **하나의 체인 머리 행**에서 직렬화된다 — 요청마다 여러 감사(도구 호출·교환·승인)를 남기므로 고부하에서 PG 행 잠금 대기가 게이트웨이 지연으로 이어진다. 또 `_lock` 을 DB I/O 동안 잡아 같은 워커의 다른 스레드도 기다린다.
- 호출부의 큰 트랜잭션 **안에서** `append` 를 부르면 별도 연결로 쓰므로 SQLite 에서는 잠금 대기가 생긴다 — 코드베이스는 대부분 "커밋 후 감사" 관례를 지킨다(예: `app/services/governance.py:90-91`).
- 감사 실패(예외)는 호출자에게 전파된다 — 감사 실패 시 업무 처리를 막을지(fail closed) 계속할지 정책이 호출부마다 다르다.

```python path=app/services/audit.py lines=72-116
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
- L72-88 `verify`: 앵커 이후 전 구간을 순서대로 — 링크(prev_hash) 단절, 내용 변조(재계산 불일치), 꼬리 절단(머리 해시와 마지막 해시 불일치) 탐지. `populate_existing=True` 로 세션 캐시가 아닌 DB 값으로 재계산(중요한 세부 — 같은 세션에서 수정된 객체를 보면 변조를 놓친다), `yield_per(500)` 로 메모리 제한.
- L91-107 `verify_range`: 구간 검증(시작 직전 해시부터).
- L110-116: 결과 dict, 전체 개수.
**짚어볼 점 / Watch-outs**
- 해시 체인은 변조 **탐지**용이다 — DB 쓰기 권한이 있는 공격자는 머리까지 포함해 체인 전체를 재계산할 수 있다. 외부 앵커(주기적으로 머리 해시를 서명해 외부 저장 — 리포트 서명이 일부 역할)를 두면 방지력이 생긴다.
- L80 전체 검증은 이벤트 수에 비례(O(n)) — 대량 체인에서 API 호출(`/api/v1/audit/verify`)이 오래 걸린다.

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
- L120-123: 아카이브 디렉터리.
- L126-157 `archive_before`: 앵커 이후·기준 시각 이전 이벤트 중 **연속 구간만**(중간에 끊기면 멈춤) → JSONL 파일(이벤트 + prev/hash) → 아카이브 행(파일 SHA-256) → 이벤트 삭제 → 앵커 이동 → 트랜잭션 종료 후 `audit.archive` 증명 이벤트.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L145 파일을 **DB 커밋 전에** 쓴다 — 커밋이 실패하면 고아 파일이 남는다(무해하지만 정리 필요). 반대 순서(커밋 후 쓰기)는 파일 쓰기 실패 시 이벤트가 사라지므로 현재 순서가 더 안전한 선택이다.
- L130-131 기준 시각 이전 이벤트를 **전부 메모리로** 읽는다 — 첫 아카이브 시 대량 이벤트면 메모리 사용이 크다(배치 처리 권장).
- L145 `write_text` 는 원자적 쓰기가 아니다(중간 실패 시 잘린 파일) — 임시 파일 + rename 패턴이 안전하다.

```python path=app/services/audit.py lines=160-192
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
- L160-178 `verify_archive`: 파일 존재·SHA-256 일치·내부 체인 재계산·마지막 해시 일치.
- L181-192 `purge_archive`: 파일 삭제(`missing_ok`) + 파기 시각 기록(매니페스트 해시는 남겨 "무엇을 파기했는지" 증명) → 증명 이벤트.
**짚어볼 점 / Watch-outs**
- L162 `Path(arc.file_path)` — DB 에 저장된 경로를 그대로 열고(L166) 삭제한다(L187). DB 값을 조작할 수 있으면 임의 파일 삭제가 가능하지만, 그 정도 권한이면 이미 더 큰 문제가 있다(신뢰 경계 안).

```python path=app/services/audit.py lines=195-242
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


def verify_report_signature(signature: str, content_sha256: str | None = None) -> dict:
    try:
        claims = jwt.decode(signature, keys.signing_key().public_key(), algorithms=["RS256"],
                             options={"verify_aud": False})
    except jwt.PyJWTError as exc:
        return {"valid": False, "reason": f"signature: {exc}"}
    if content_sha256 and claims.get("sha256") != content_sha256:
        return {"valid": False, "reason": "content_hash_mismatch", "claims": claims}
    return {"valid": True, "claims": claims}
```
**분석 / Analysis (Level 2)**
- L196-206 `report_events`: 기간·결정은 SQL, 행동 접두·행위자는 Python 필터.
- L209-231 `build_report`: 이벤트 → JSONL 파일·SHA-256 → 전체 체인 검증 결과 포함 → 클레임(리포트 id·해시·건수·체인 유효·범위)을 **IdP 서명 키로 RS256 서명**.
- L234-242 `verify_report_signature`: 서명 검증 + (선택) 내용 해시 일치.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 키 용도 혼용] L225 리포트 서명에 사용자 액세스 토큰과 **같은 키**를 쓰고, L236-237 검증은 `iss`·`typ`·필수 클레임을 확인하지 않는다 — 아무 사용자 액세스 토큰도 "유효한 리포트 서명" 으로 통과한다(`content_sha256` 미제공 시 L240 검사도 생략). `issuer="agentnexus-audit"`·`require=["report_id","sha256"]` 를 검증하거나 별도 키를 써야 한다.
- L205 행동·행위자 필터를 Python 에서 — 기간이 길면 전체 이벤트를 메모리로 읽는다.
- L213 리포트마다 전체 체인 검증(O(n)) — 리포트 생성 비용이 체인 길이에 비례.

## 파일 평가 / File Assessment
- 강점: (1) 정규 JSON·고정 시각 포맷으로 재계산 가능한 해시 체인, `populate_existing` 으로 캐시 우회 검증. (2) 연속 구간 아카이브 + 앵커 + 파기 증명으로 보존 기간 삭제와 무결성을 양립. (3) PG 행 잠금으로 다중 워커 직렬화.
- 위험: (1) [낮음] 단일 머리 행 직렬화의 처리량 한계·SQLite 다중 프로세스 시 감사 유실. (2) [낮음] 리포트 서명 키·검증 클레임 미분리. (3) 대량 이벤트 메모리 적재(아카이브·리포트).
- 개선 권고(영향순): (1) 리포트 검증에 iss·필수 클레임 확인(또는 전용 키). (2) 감사 쓰기를 큐잉(비동기 배치)하거나 샤드 체인 검토. (3) 아카이브·리포트 배치 처리와 원자적 파일 쓰기.
- 재읽기 권고 레벨: 3.
