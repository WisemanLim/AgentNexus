> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 30. app/services/scheduler.py
**파일 요약 / File Summary**
- 역할 / Role: 예약 작업 루프 — 매 주기 SLI flush(모든 워커), 작업별 **리스**(settings 행 조건부 갱신)를 잡은 워커 하나만 경보·SLO 평가, 끊긴 실행 복구, 예약 백업, 승인 만료, 레지스트리 심사 만료·동기화, 보존 기간 파기.
- 진입점 거리 / Distance: 1 (`app/main.py:107-108`)
- LOC: 141
- 주요 export: `WORKER`, `LAST`, `lease`, `tick`, `loop`, `evaluate_alerts`, `evaluate_slos`, `run_retention`, `registry_jobs`, `approval_jobs`
- 의존(내부): `app.core.config`, `app.db`, `app.models.Setting`, (지연) `app.api.observe`·`app.api.ops`·`app.services.{slo,retention,registry,review,audit,orchestrator}`·`app.core.errors`
- 외부 패키지: sqlalchemy, 표준 `asyncio`·`socket`·`os`·`logging`
- 부수효과 / Side effects: DB 쓰기(리스·승인 만료·각 작업), 알림·감사, 백업 파일
- 동시성 / Concurrency: asyncio 루프 + 각 작업은 스레드풀(동기 DB), 다중 워커·파드 간 리스로 상호 배제
- 테스트 커버리지 흔적: `test/test_reliability.py`(복구), `test/test_ops.py`, 시험은 `SCHEDULER_INTERVAL_S=0` 으로 루프를 끄고 함수를 직접 호출(`test/conftest.py:26`)

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/scheduler.py lines=1-21
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
```
**분석 / Analysis (Level 2)**
- L1-5: 리스 설계 — 여러 워커·파드가 같은 루프를 돌아도 작업별 리스를 잡은 하나만 수행.
- L20 `WORKER = "호스트:pid"`: 워커 식별자(리스 소유자·SLI 행 id).
- L21 `LAST`: 작업별 마지막 수행 시각(이 워커 기준 — 상태 API 용).

```python path=app/services/scheduler.py lines=24-42
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
```
**분석 / Analysis (Level 2)**
- L24-42 `lease`: `UPDATE settings SET value, updated_by=나, updated_at=지금 WHERE key=lease.X AND (updated_by=나 OR updated_at < 지금−TTL)` — **조건부 UPDATE 의 rowcount** 로 획득 판정(SQLite·PG 공통 원자 연산). 행이 없으면 INSERT 시도, 동시 INSERT 경쟁은 PK 충돌(`IntegrityError`)로 패자를 가린다.
**짚어볼 점 / Watch-outs**
- 이 코드베이스에서 **올바른 원자적 상태 전이의 모범 예** — 승인 소비(`app/services/governance.py:152-160`)·refresh 회전 등 다른 경합 지점도 같은 패턴을 쓰면 된다.
- [위험:낮음] 작업 수행 시간이 TTL(`max(주기×2, 30초)`, L103)보다 길면 다른 워커가 리스를 가져가 **동시에 같은 작업**을 실행한다(예: 백업·보존 파기). 장기 작업은 진행 중 리스 갱신(heartbeat)이 필요하다.
- 시계 차이: `updated_at` 비교는 각 워커의 시스템 시각 — 파드 간 시계가 어긋나면 리스 만료 판정이 틀어진다(NTP 전제).

```python path=app/services/scheduler.py lines=45-77
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
    return out
```
**분석 / Analysis (Level 2)**
- L45-48: 경보 규칙 평가 — **API 모듈**(`app.api.observe.evaluate`)을 import 한다.
- L51-54: SLO 평가. L57-60: 보존 파기.
- L63-77 `registry_jobs`: 72시간 지난 심사 만료 + 예약 동기화 대상 소스 동기화(실패한 소스는 롤백 후 다음 주기 재시도, 상태는 소스에 기록됨).
**짚어볼 점 / Watch-outs**
- [계층 위반] L46·L116 서비스 계층이 API 계층(`app.api.observe`·`app.api.ops`)을 import 한다 — 의존 방향이 거꾸로다(API → 서비스여야 함). 지연 import 로 순환 오류는 피했지만, `evaluate`·`run_scheduled_backup` 은 서비스 모듈로 옮겨야 한다.
- L74 `except ApiError` 만 잡는다 — 네트워크 예외 등 다른 예외는 `tick` 의 상위 `except`(L140)까지 올라가 **그 주기의 나머지 작업(보존 파기 등)이 건너뛰어진다**.

```python path=app/services/scheduler.py lines=80-96
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
```
**분석 / Analysis (Level 2)**
- L80-96 `approval_jobs`: 기한 지난 pending 도구 승인을 expired 로 일괄 변경·커밋 → 각각 감사·오케스트레이터 훅(대기 실행을 거부 정책대로 재개).
**짚어볼 점 / Watch-outs**
- L87-91 조회 후 일괄 변경이 조건부가 아니다 — 같은 순간 사용자가 승인(`governance.decide`)하면 approved → expired 로 덮어쓸 수 있다(마지막 쓰기 승). 리스 덕분에 워커 간 중복은 없지만 사용자 결정과의 경합은 남는다.
- L95 `orchestrator.on_approval` 은 스레드풀 스레드에서 실행 재개를 예약한다(`orchestrator.LOOP` 사용) — 이벤트 루프가 있는 워커에서만 동작(주석 `app/services/orchestrator.py` on_approval 의 RuntimeError 처리).

```python path=app/services/scheduler.py lines=99-129
async def tick(now_s: float, last_retention: list) -> None:
    from starlette.concurrency import run_in_threadpool

    from app.services import orchestrator
    ttl = max(settings.scheduler_interval_s * 2, 30)
    from app.services import slo
    await run_in_threadpool(slo.flush, WORKER)  # 모든 워커 — 자기 SLI 카운터를 DB 로 (D15-④)
    if await run_in_threadpool(lease, "alerts", ttl):
        await run_in_threadpool(evaluate_alerts)
        await run_in_threadpool(evaluate_slos)
        LAST["alerts"] = utcnow().isoformat()
    if await run_in_threadpool(lease, "recovery", ttl):
        n = await orchestrator.recover_stale()
        LAST["recovery"] = utcnow().isoformat()
        if n:
            log.info("recovered %d runs", n)
    if await run_in_threadpool(lease, "backup", ttl):
        from app.api.ops import run_scheduled_backup
        await run_in_threadpool(run_scheduled_backup)
        LAST["backup"] = utcnow().isoformat()
    if await run_in_threadpool(lease, "approvals", ttl):
        await run_in_threadpool(approval_jobs)
        LAST["approvals"] = utcnow().isoformat()
    if await run_in_threadpool(lease, "registry", ttl):
        await run_in_threadpool(registry_jobs)
        LAST["registry"] = utcnow().isoformat()
    if now_s - last_retention[0] >= settings.retention_interval_s and \
            await run_in_threadpool(lease, "retention", settings.retention_interval_s):
        await run_in_threadpool(run_retention)
        last_retention[0] = now_s
        LAST["retention"] = utcnow().isoformat()
```
**분석 / Analysis (Level 2)**
- L99-129 `tick`: 모든 워커가 SLI flush → 리스별로 경보+SLO, 실행 복구(비동기), 예약 백업, 승인 만료, 레지스트리 작업 → 보존은 `RETENTION_INTERVAL_S` 경과 + 보존 리스를 잡았을 때만.
**짚어볼 점 / Watch-outs**
- L103 TTL 계산을 매 tick 마다 — 단순하지만 주기 설정 변경을 즉시 반영한다.
- [위험:낮음] L125 `now_s - last_retention[0]` 의 `now_s` 는 `loop.time()`(단조 시계, 보통 부팅 후 경과 초)이고 초기값은 0 — 호스트(또는 컨테이너가 쓰는 시계) 부팅 후 1시간이 안 되면 첫 보존 파기가 그만큼 늦어진다. 초기값을 `-inf` 로 두는 편이 의도에 맞다.
- 작업들이 **순차** 실행된다 — 백업이 오래 걸리면 승인 만료·레지스트리 작업이 그만큼 늦어진다(같은 tick 안).

```python path=app/services/scheduler.py lines=132-141
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
```
**분석 / Analysis (Level 2)**
- L132-141 `loop`: 주기 0 이면 끔, 아니면 대기 → tick, 예외는 경고 로그 후 다음 주기(조용한 삼킴이 아니라 로그 있음 — 좋음).
**짚어볼 점 / Watch-outs**
- L141 경고 로그에 `exc` 메시지를 그대로 — 예외 메시지에 시크릿이 섞일 가능성은 낮지만 구조화 로그 마스킹(`app/core/logs.py`)은 `logging` 메시지에도 적용된다(JsonFormatter `_mask`).
- 첫 tick 은 기동 후 `SCHEDULER_INTERVAL_S`(60초) 뒤 — 기동 직후 정체 실행 복구가 1분 늦다.

## 파일 평가 / File Assessment
- 강점: (1) 조건부 UPDATE 리스로 다중 워커·파드 상호 배제(이식 가능). (2) 작업 실패가 루프를 멈추지 않는다(로그 후 다음 주기). (3) SLI flush 는 리스 없이 모든 워커가 자기 몫만.
- 위험: (1) 서비스 → API 역방향 의존. (2) [낮음] 장기 작업의 리스 만료 → 동시 실행, 일부 예외가 같은 주기의 나머지 작업을 건너뜀. (3) 승인 만료와 사용자 결정의 경합.
- 개선 권고(영향순): (1) `evaluate`·`run_scheduled_backup` 을 서비스 모듈로 이동. (2) 작업별 try/except 로 격리 + 장기 작업 리스 갱신. (3) 승인 만료를 `UPDATE ... WHERE status='pending' AND expires_at < now` 로.
- 재읽기 권고 레벨: 3.
