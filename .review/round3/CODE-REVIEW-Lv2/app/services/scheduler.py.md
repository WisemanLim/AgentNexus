> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 3차 리뷰(round3, D20·D21 hunk 한정) · 스냅샷 2026-10-08 12:23 KST · [← INDEX.md](../../INDEX.md) · 2차 리뷰: [round2 main.py.md](../../../../round2/CODE-REVIEW-Lv2/app/main.py.md)(scheduler 는 2차 대상 아님)

### 11. app/services/scheduler.py + app/main.py (lifespan)
**파일 요약 / File Summary**
- 역할 / Role: 예약 작업 루프 — 경보·SLO 판정, 끊긴 실행 복구, 예약 백업, 도구 승인 만료, 레지스트리 심사 만료·미러 동기화, 보존 기간 파기. 여러 워커·파드가 같은 루프를 돌아도 **작업별 리스**(settings 행 조건부 UPDATE)를 잡은 하나만 수행한다. 12차 D21-② 로 ① 스케줄러는 구성요소 조합과 무관하게 시작(`main.py:118-119`), ② 작업은 구성요소별로 고른다(`scheduler.py:108-128`) — gateway-only·registry-only 단독 배포에서도 자기 작업이 돈다(2차 M51 해결).
- 진입점 거리 / Distance: 0 — `app/main.py:98-129` lifespan 이 `scheduler.loop()` 를 Task 로 띄운다(워커마다).
- LOC: `scheduler.py` 145, `main.py` 237 (2차 스냅샷 203 → +34, 이번 범위는 L118-119)
- 주요 export: `WORKER`·`lease`·`approval_jobs`·`registry_jobs`·`tick`·`loop`
- 의존(내부): `app.services.orchestrator`(승인 후 실행 재개·복구), `slo`, `retention`, `registry`·`review`, `app.api.observe.evaluate`·`app.api.ops.run_scheduled_backup`(서비스 → API 역방향 import, 2차 §4.2 계층 위반 그대로)
- 외부 패키지: SQLAlchemy, starlette(`run_in_threadpool`)
- 부수효과 / Side effects: `settings` 의 `lease.*` 행 갱신, 승인 상태 변경·감사, 실행 재개(Task 생성), 보존 파기, 백업 파일.
- 동시성 / Concurrency: 워커마다 asyncio Task 1개. 작업 본문은 스레드풀. `orchestrator.on_approval` 은 스레드에서 이벤트 루프로 실행 재개를 예약한다(`orchestrator.LOOP`).
- 테스트 커버리지 흔적: `test/test_components.py:35-40`(C57-01 조합 5종 — 해당 작업만), `:42-47`(C57-02 구성요소 조건 없이 시작 — lifespan **소스 문자열** 단언이라 동작 시험은 아니다), live `test/impl/57th/logs/live-components.log`(L57-01).
- 3차 변경 요약: D21-② — `main.py:118-119`, `scheduler.py:108`·`:110`·`:114`·`:119`·`:123`·`:126`.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/main.py lines=98-129
@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    orchestrator.LOOP = asyncio.get_running_loop()  # 승인 훅(스레드)에서 실행 재개를 이 루프로 예약
    with contextlib.suppress(Exception):  # 재시작 횟수(SCR-B05) — 워커마다 1건
        logs.event("info", "system", f"프로세스 시작 (pid {os.getpid()})", sync=True, version=APP_VERSION)
    async with contextlib.AsyncExitStack() as stack:
        for sub in EMBEDDED:
            await stack.enter_async_context(sub.router.lifespan_context(sub))
        tasks = []
        if settings.enabled("gateway") and settings.mcp_refresh_on_start_s > 0:
            tasks.append(asyncio.create_task(_refresh_builtin_mcp()))
        if settings.enabled("gateway") and settings.mcp_health_interval_s > 0:
            tasks.append(asyncio.create_task(_health_loop()))
        if settings.enabled("gateway") and settings.a2a_watch_interval_s > 0:
            from app.services import a2a_gateway
            tasks.append(asyncio.create_task(a2a_gateway.watcher(settings.a2a_watch_interval_s)))
        if settings.enabled("admin") and settings.enabled("identity"):
            from app.services import setup as setup_svc
            with contextlib.suppress(Exception), db.SessionLocal() as s0:  # 첫 실행 미완료면 일회용 설정 코드를 로그에
                setup_svc.ensure_code(s0)
        from app.services import scheduler  # 어떤 구성요소 조합이든 — 작업은 구성요소별로 고른다 (D21-②: gateway·registry 단독 배포)
        tasks.append(asyncio.create_task(scheduler.loop()))  # 경보·보존·실행 복구·승인 만료·레지스트리 (리스로 워커 1개만)
        tasks += [asyncio.create_task(fn()) for fn in BACKGROUND]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            from app.services import scheduler, slo
            with contextlib.suppress(Exception):  # 종료 전 남은 SLI 카운터 반영 (DB 가 이미 내려갔으면 버림)
                slo.flush(scheduler.WORKER)

```
**분석 / Analysis (Level 2)**
- L100: `orchestrator.LOOP` 를 **구성요소와 무관하게** 이 프로세스의 이벤트 루프로 설정한다 — 스레드(동기 핸들러·스케줄러 작업)에서 실행 재개를 예약할 대상.
- L118-119(D21-②): 전에는 host·observe 가 켜진 프로세스만 스케줄러를 시작했다. 지금은 언제나 시작하고 작업 선택은 `tick` 이 한다. `SCHEDULER_INTERVAL_S=0` 이면 `loop()` 가 바로 끝난다(`scheduler.py:137-138`).
- L123-125: 종료 시 Task 취소. 스케줄러 Task 가 작업 도중(스레드풀 실행 중) 취소되면 스레드 작업은 끝까지 돌고 결과만 버려진다 — 리스는 TTL 로 풀린다.

```python path=app/services/scheduler.py lines=83-99
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
- 기한 지난 `pending` 도구 승인을 `expired` 로 커밋한 뒤, 건마다 감사와 `orchestrator.on_approval`(대기 중 실행 재개 — 만료면 그 단계는 거부 처리) 호출.
- 흔한 실수: 상태 변경 커밋 전에 훅을 부르면 훅이 DB 에서 아직 `pending` 을 본다. 여기서는 커밋 후 호출(L94 → L95-98)이라 맞다.

```python path=app/services/scheduler.py lines=102-145
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
```
**분석 / Analysis (Level 2)**
- L106: 리스 TTL = max(주기 × 2, 30초). 보유 워커가 죽으면 2 주기 뒤 다른 워커가 잇는다.
- L108(D21-②): `on = settings.enabled` — 작업마다 구성요소 조건. observe → 경보·SLO, host → 실행 복구, admin → 예약 백업, **gateway 또는 host** → 도구 승인 만료(L123), registry → 심사 만료·미러 동기화, 조건 없음 → SLI 반영(L109)·보존(L129-133).
- L129-130: 보존은 주기가 길어 리스 TTL 을 주기 자체로 — 어느 구성요소 프로세스든 하나가 수행.
- L136-145: 한 주기 실패는 로그만 남기고 다음 주기에 재시도(루프가 죽지 않게).
**짚어볼 점 / Watch-outs**
- [위험:중간] **M7(INDEX §5) — 분리 배포에서 게이트웨이 프로세스가 Host 의 실행을 자기 안에서 재개한다.** L123 은 승인 만료 리스를 gateway **또는** host 프로세스 중 누구나 잡게 한다. 같은 DB 를 쓰는 분리 배포(host 프로세스 + gateway-only 프로세스 — 57th 결과 문서가 "여러 구성요소 프로세스가 같은 DB 를 쓰면 작업별 리스로 한 프로세스만 수행" 이라고 이 배치를 전제)에서 gateway 프로세스가 리스를 잡으면, L98 `orchestrator.on_approval` → `schedule(run_id, resume=True)`(`app/services/orchestrator.py:955-957`) → `orchestrator.LOOP`(= 이 gateway 프로세스의 루프, `app/main.py:100`)에서 **실행 전체가 이어서 돈다**(`app/services/orchestrator.py:294-305`). 결과: Host 구성요소가 꺼진 프로세스가 오케스트레이션 실행(모델·도구·원격 에이전트 호출)을 수행하고, 그 실행의 진행 이벤트는 Host 프로세스의 구독자가 아니라 gateway 프로세스 메모리로 간다. 자원 격리(게이트웨이 파드 크기·스케일링)와 구성요소 경계가 깨진다. 전에는 스케줄러가 host·observe 프로세스에서만 돌아 이 일이 없었다. 최소 수정: `on_approval` 의 재개 예약을 `settings.enabled("host")` 일 때만 하고, 그 밖에서는 상태만 바꾼 뒤 Host 쪽 복구 작업(`recover_stale`)이 "모든 승인이 끝났는데 멈춘 실행" 도 집어 가게 하거나, 승인 만료 리스를 host 우선(host 가 하나라도 살아 있으면 host 만)으로.
- [위험:낮음] (2차 §4.2 이월) L46·L120 서비스 계층이 API 모듈(`app.api.observe.evaluate`·`app.api.ops.run_scheduled_backup`)을 import 한다. 이번 변경으로 admin·observe 가 없는 프로세스에서는 그 분기가 돌지 않아 import 도 안 되지만, 계층 역전은 그대로다.

```python path=app/services/orchestrator.py lines=937-959
def on_approval(ap_row: Approval) -> None:
    """게이트웨이 승인 API 훅(스레드) — 승인/거부 후 대기 중인 실행을 재개한다."""
    if not ap_row.run_id:
        return
    with SessionLocal() as db:
        st = db.scalars(select(RunStep).where(RunStep.run_id == ap_row.run_id,
                                              RunStep.approval_id == ap_row.id)).first()
        if st is None or st.status != "waiting_approval":
            return
        if ap_row.status == "approved":
            st.status = "pending"
        else:
            st.status, st.error, st.finished_at = "rejected", f"사람 승인 {'만료' if ap_row.status == 'expired' else '거부'} ({ap_row.decided_by or 'system'})", utcnow()
        db.commit()
        pending_left = db.scalars(select(RunStep).where(RunStep.run_id == ap_row.run_id,
                                                        RunStep.status == "waiting_approval")).first()
    _emit(ap_row.run_id, "approval.resolved", {"approval_id": ap_row.id, "status": ap_row.status,
                                               "decided_by": ap_row.decided_by})
    if pending_left is None:
        try:
            schedule(ap_row.run_id, resume=True)
        except RuntimeError:  # 이벤트 루프 없는 프로세스(CLI·운영 스크립트) — 복구 작업(recover_stale)이 이어받는다
            logs.event("warning", "host", f"재개 예약 보류 — 복구 작업이 이어받음: {ap_row.run_id}")
```
**분석 / Analysis (Level 2)**
- (M7 근거 — 범위 밖 파일, 인용만) 승인 결과를 단계에 반영하고, 남은 승인 대기가 없으면 `schedule(…, resume=True)`. 이벤트 루프가 없는 프로세스(CLI)는 `RuntimeError` → 복구 작업이 이어받는다(L958-959) — 즉 "재개를 다른 프로세스가 이어받는 경로" 는 이미 있으므로, gateway-only 프로세스에서 재개를 건너뛰어도 기능 손실이 적다.

## 파일 평가 / File Assessment
- 강점: (1) 작업별 구성요소 조건이 한 함수(`tick`)에 표로 읽히듯 정리되어 단독 배포 정합성(2차 M51)을 해결했다(`app/services/scheduler.py:108-133`, 시험 `test/test_components.py:35-47`). (2) 리스가 조건부 UPDATE + 생성 경합 처리로 SQLite·PostgreSQL 공통(`app/services/scheduler.py:24-42`). (3) 한 주기 실패가 루프를 끝내지 않는다(`app/services/scheduler.py:142-145`).
- 위험: (1) [위험:중간] 승인 만료 리스를 gateway 프로세스가 잡으면 Host 실행을 그 프로세스에서 재개(`app/services/scheduler.py:123-125` → `app/services/orchestrator.py:955-957`, `app/main.py:100`). (2) [위험:낮음] 서비스 → API 역방향 import(`app/services/scheduler.py:46`, `:120`).
- 개선 권고(영향순): (1) 실행 재개는 host 프로세스에서만. (2) 경보 평가·예약 백업 함수를 서비스 계층으로 이동.
- 파일 위험 등급 / Risk grade: 중간 — [위험:중간] 1건(분리 배포에서만 발현).
- 재읽기 권고 레벨: 3 — 작업 선택 표는 블록 단위로 충분하고, M7 만 `orchestrator` 재개 경로와 같이 따라가면 된다.

## 2차 리뷰 대비 / vs 2nd Review
| 2차 지적 (2차 INDEX §5) | 3차 상태 | 근거(현재 path:line) |
|---|---|---|
| M51 스케줄러가 host·observe 일 때만 기동 → gateway-only·registry-only 에서 보존·승인 만료·레지스트리 작업 정지 (`main.py:112`) | 해결 · 부작용(M7) | `app/main.py:118-119`, `app/services/scheduler.py:108-133` |
| M18 미러 독 항목이 동기화·보존을 멈춤 | 해결(D19-④) | `app/services/scheduler.py:77-79` |
| §4.2 `services.scheduler` → `api.observe`·`api.ops` | 미해결 | `app/services/scheduler.py:46`, `:120` |
