"""예약 작업 (FR-O02·O03, NFR-S1) — 경보 평가 · 보존 기간 파기 · 끊긴 실행 복구.

여러 워커·파드가 같은 루프를 돌아도 작업별 리스(settings 행 조건부 갱신)를 잡은 하나만 수행한다.
SCHEDULER_INTERVAL_S=0 이면 루프를 끈다(시험은 함수를 직접 호출).
"""
import asyncio
import logging
import os
import socket
from datetime import timedelta

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db import SessionLocal, utcnow
from app.models import Setting

log = logging.getLogger("agentnexus.scheduler")
WORKER = f"{socket.gethostname()}:{os.getpid()}"
LAST: dict[str, str] = {}  # 작업별 마지막 수행 시각 (이 워커 기준, 상태 API 용)


def lease(name: str, ttl_s: float, owner: str = WORKER) -> bool:
    """작업 리스 획득 — 보유자가 나이거나 만료된 경우에만 조건부 UPDATE 가 1행을 바꾼다 (SQLite·PG 공통)."""
    key, now = f"lease.{name}", utcnow()
    with SessionLocal() as db:
        res = db.execute(update(Setting).where(Setting.key == key, or_(
            Setting.updated_by == owner, Setting.updated_at < now - timedelta(seconds=ttl_s)))
            .values(value={"owner": owner}, updated_by=owner, updated_at=now))
        db.commit()
        if res.rowcount == 1:
            return True
        if db.get(Setting, key) is not None:
            return False
        try:
            db.add(Setting(key=key, value={"owner": owner}, updated_by=owner, updated_at=now))
            db.commit()
            return True
        except IntegrityError:  # 다른 워커가 먼저 만듦
            db.rollback()
            return False


def evaluate_alerts() -> dict:
    from app.api.observe import evaluate
    with SessionLocal() as db:
        return evaluate(db)


def evaluate_slos() -> list:
    from app.services import slo
    with SessionLocal() as db:
        return slo.evaluate(db)


def run_retention() -> dict:
    from app.services import retention
    with SessionLocal() as db:
        return retention.run(db, "scheduler")


def registry_jobs() -> dict:
    """심사 대기 72시간 만료(ST-06) · 레지스트리 소스 예약 동기화(E06 ③)."""
    from app.core.errors import ApiError
    from app.services import registry, review
    out = {"expired": 0, "synced": []}
    with SessionLocal() as db:
        out["expired"] = review.expire_old(db)
        for src in registry.due_sources(db):
            try:
                registry.sync_source(db, src, "system:scheduler")
                out["synced"].append(src.name)
            except ApiError as exc:  # 상태는 소스에 기록됨 — 다음 주기에 다시
                db.rollback()
                log.warning("registry sync %s failed: %s", src.name, exc.code)
            except Exception as exc:  # noqa: BLE001 — 한 소스의 형식 오류가 동기화·보존 작업까지 멈추지 않게 (D19-④)
                db.rollback()
                log.warning("registry sync %s failed: %s", src.name, type(exc).__name__)
    return out


def approval_jobs() -> int:
    """승인 만료 (ST-06, SCR-C04 ⑥·D05 ⑦) — 기한이 지난 도구 승인은 자동 거부하고 실행을 재개(거부 처리 정책대로)."""
    from sqlalchemy import select

    from app.models import Approval
    from app.services import audit, orchestrator
    with SessionLocal() as db:
        rows = list(db.scalars(select(Approval).where(Approval.status == "pending", Approval.kind == "tool_call",
                                                      Approval.expires_at < utcnow())))
        for ap in rows:
            ap.status, ap.decided_by, ap.decided_at = "expired", "system:expire", utcnow()
        db.commit()
        for ap in rows:
            audit.append("approval.expire", actor="system", target=ap.target, decision="expired",
                         data={"approval_id": ap.id, "run_id": ap.run_id})
            orchestrator.on_approval(ap)
    return len(rows)


async def tick(now_s: float, last_retention: list) -> None:
    from starlette.concurrency import run_in_threadpool

    from app.services import orchestrator
    ttl = max(settings.scheduler_interval_s * 2, 30)
    from app.services import slo
    on = settings.enabled  # 구성요소별 작업 (D21-②) — 단독 배포(gateway-only·registry-only)도 자기 작업은 돈다
    await run_in_threadpool(slo.flush, WORKER)  # 모든 워커 — 자기 SLI 카운터를 DB 로 (D15-④)
    if on("observe") and await run_in_threadpool(lease, "alerts", ttl):
        await run_in_threadpool(evaluate_alerts)
        await run_in_threadpool(evaluate_slos)
        LAST["alerts"] = utcnow().isoformat()
    if on("host") and await run_in_threadpool(lease, "recovery", ttl):
        n = await orchestrator.recover_stale()
        LAST["recovery"] = utcnow().isoformat()
        if n:
            log.info("recovered %d runs", n)
    if on("admin") and await run_in_threadpool(lease, "backup", ttl):
        from app.api.ops import run_scheduled_backup
        await run_in_threadpool(run_scheduled_backup)
        LAST["backup"] = utcnow().isoformat()
    if (on("gateway") or on("host")) and await run_in_threadpool(lease, "approvals", ttl):  # 도구 승인은 게이트웨이 거버넌스
        await run_in_threadpool(approval_jobs)
        LAST["approvals"] = utcnow().isoformat()
    if on("registry") and await run_in_threadpool(lease, "registry", ttl):
        await run_in_threadpool(registry_jobs)
        LAST["registry"] = utcnow().isoformat()
    if now_s - last_retention[0] >= settings.retention_interval_s and \
            await run_in_threadpool(lease, "retention", settings.retention_interval_s):
        await run_in_threadpool(run_retention)
        last_retention[0] = now_s
        LAST["retention"] = utcnow().isoformat()


async def loop() -> None:
    if settings.scheduler_interval_s <= 0:
        return
    last_retention = [0.0]
    while True:
        await asyncio.sleep(settings.scheduler_interval_s)
        try:
            await tick(asyncio.get_running_loop().time(), last_retention)
        except Exception as exc:  # noqa: BLE001 — 한 번 실패해도 다음 주기에 다시 시도
            log.warning("scheduler tick failed: %s: %s", type(exc).__name__, exc)
