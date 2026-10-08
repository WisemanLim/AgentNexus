"""Epic 57 — 구성요소 단독 배포 정합성 (D21-②): 예약 작업을 구성요소별로 — gateway-only·registry-only 에서도 자기 작업은 돈다."""
import asyncio

import pytest

from app.core.config import settings
from app.services import orchestrator, scheduler, slo


@pytest.fixture
def jobs(monkeypatch):
    ran: list[str] = []
    monkeypatch.setattr(scheduler, "lease", lambda name, ttl, owner=None: True)
    monkeypatch.setattr(slo, "flush", lambda worker: ran.append("slo_flush"))
    for fn in ("evaluate_alerts", "evaluate_slos", "approval_jobs", "registry_jobs", "run_retention"):
        monkeypatch.setattr(scheduler, fn, lambda fn=fn: ran.append(fn))

    async def recover():
        ran.append("recover_stale")
        return 0
    monkeypatch.setattr(orchestrator, "recover_stale", recover)
    import app.api.ops as ops
    monkeypatch.setattr(ops, "run_scheduled_backup", lambda: ran.append("backup"))
    return ran


# ── C57-01 예약 작업 — 구성요소 조합마다 해당 작업만 (보존·SLI 반영은 언제나) ──
@pytest.mark.parametrize("components,expected", [
    ("all", {"slo_flush", "evaluate_alerts", "evaluate_slos", "recover_stale", "backup", "approval_jobs", "registry_jobs", "run_retention"}),
    ("gateway,identity", {"slo_flush", "approval_jobs", "run_retention"}),
    ("registry", {"slo_flush", "registry_jobs", "run_retention"}),
    ("host,identity", {"slo_flush", "recover_stale", "approval_jobs", "run_retention"}),
    ("observe", {"slo_flush", "evaluate_alerts", "evaluate_slos", "run_retention"}),
])
def test_tick_runs_component_jobs(jobs, monkeypatch, components, expected):
    monkeypatch.setattr(settings, "components", components)
    asyncio.run(scheduler.tick(10**9, [0.0]))
    assert set(jobs) == expected, jobs


# ── C57-02 기동 — 스케줄러는 구성요소와 무관하게 시작한다 (전에는 host·observe 가 없으면 시작 안 함) ──
def test_scheduler_started_for_any_components():
    import inspect

    from app import main
    src = inspect.getsource(main.lifespan)
    assert 'scheduler.loop()' in src and 'if settings.enabled("host") or settings.enabled("observe"):' not in src
