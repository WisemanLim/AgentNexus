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
from app.db import session_scope, unbounded, utcnow
from app.models import AuditArchive, AuditChainHead, AuditEvent, AuditReport

GENESIS = "0" * 64
_lock = threading.Lock()


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="microseconds")


def payload(ev: AuditEvent) -> dict:
    return {"seq": ev.seq, "event_id": ev.event_id, "ts": _iso(ev.ts), "actor": ev.actor, "actor_kind": ev.actor_kind,
            "action": ev.action, "target": ev.target, "decision": ev.decision, "trace_id": ev.trace_id,
            "data": ev.data or {}}


def compute_hash(prev_hash: str, body: dict) -> str:
    return sha256_hex(prev_hash + canonical_json(body))


def _head(db: Session, lock: bool) -> AuditChainHead:
    stmt = select(AuditChainHead).where(AuditChainHead.id == 1)
    if lock and db.bind.dialect.name != "sqlite":
        unbounded(db)  # 아카이브가 머리 행을 잡고 있는 동안 다른 감사 기록이 잠금 상한(15초)으로 실패하지 않게 (65th)
        stmt = stmt.with_for_update()
    head = db.scalars(stmt).first()
    if head is None:
        head = AuditChainHead(id=1, last_seq=0, last_hash=GENESIS, anchor_seq=0, anchor_hash=GENESIS)
        db.add(head)
        db.flush()
    return head


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
        # 같은 서명 키로 사용자 토큰도 발급한다 — 리포트 발급자·필수 클레임까지 맞아야 리포트 서명으로 인정
        claims = keys.decode_long_lived(signature, algorithms=["RS256"], issuer="agentnexus-audit",
                            options={"verify_aud": False, "require": ["iss", "report_id", "sha256"]})
    except jwt.PyJWTError as exc:
        return {"valid": False, "reason": f"signature: {exc}"}
    if content_sha256 and claims.get("sha256") != content_sha256:
        return {"valid": False, "reason": "content_hash_mismatch", "claims": claims}
    return {"valid": True, "claims": claims}
