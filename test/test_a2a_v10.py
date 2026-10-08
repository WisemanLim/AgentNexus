"""Epic 33 — A2A 1.0 기본화 (D15-②): 시스템·업데이트 정보 1.0 기본 · 카드 1.0 인터페이스 우선(0.3 필드 유지) ·
오케스트레이터 위임이 SendMessage(1.0) · 시험 콘솔 기본 1.0 · 0.3 클라이언트·0.3 전용 원격 에이전트 회귀 없음(변환 기록).
"""
import httpx
import pytest
from sqlalchemy import select

from app.core.http import register_mount
from app.db import SessionLocal
from app.models import A2ATask
from test.conftest import approve_agent
from test.test_a2a import AG, _mock_agent, call, msg
from test.test_orchestration import ask, wait_run


@pytest.fixture(scope="module", autouse=True)
def ready(client, H):
    assert client.post(f"/api/v1/a2a/agents/{AG}/refresh", headers=H["operator"]).status_code == 200


def protocols(task_id_prefix: str, run_id: str | None = None) -> list[dict]:
    with SessionLocal() as db:
        stmt = select(A2ATask).where(A2ATask.agent_id == task_id_prefix)
        if run_id:
            stmt = stmt.where(A2ATask.run_id == run_id)
        rows = db.scalars(stmt.order_by(A2ATask.updated_at.desc())).all()
        return [e["protocol"] for r in rows for e in (r.events or []) if e.get("protocol")]


# ── C33-01 1.0 기본 표시 — info·업데이트 정보·플랫폼 카드(1.0 인터페이스 먼저, 0.3 필드 유지) ──
def test_defaults_advertise_v10(client, H):
    info = client.get("/api/v1/system/info", headers=H["dev"]).json()["protocols"]
    assert info["a2a"] == "1.0" and info["a2a_supported"] == ["1.0", "0.3"]
    upd = client.get("/api/v1/admin/updates", headers=H["admin"]).json()["protocols"]["a2a"]
    assert upd["current"] == "1.0" and upd["supported"] == ["1.0", "0.3"]
    card = client.get("/.well-known/agent-card.json").json()
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert card["url"] and card["protocolVersion"] == "0.3.0"  # 0.3 클라이언트용 필드는 그대로


# ── C33-02 오케스트레이터 위임은 1.0 — 1.0 업스트림이면 변환 없이 1.0 ──
def test_orchestrator_delegates_with_v10(client, H):
    _, rid = ask(client, H["user"], "tpl-incident", "orders-api 장애 원인 분석해줘")
    run = wait_run(client, H["user"], rid)
    assert run["status"] == "succeeded", run
    seen = protocols(AG, rid)
    assert seen and all(p == {"client": "1.0", "upstream": "1.0"} for p in seen)


# ── C33-03 0.3 클라이언트 회귀 없음 — message/send 결과는 0.3 형식, 0.3 인터페이스로 그대로 ──
def test_v03_client_still_supported(client, H):
    out = call(client, H["user"], "message/send", {"message": msg("orders-api 장애 분석")}).json()["result"]
    assert out["kind"] == "task" and out["status"]["state"] == "completed"
    assert protocols(AG)[0] == {"client": "0.3", "upstream": "0.3"}


# ── C33-04 0.3 전용 원격 에이전트 — 1.0 클라이언트(오케스트레이터·시험 콘솔 기본)를 게이트웨이가 변환 ──
def test_v10_default_against_v03_only_agent(client, H):
    card = {"protocolVersion": "0.3.0", "name": "Legacy 33", "description": "0.3 전용", "url": "http://legacy33.test/",
            "version": "1", "capabilities": {"streaming": False}, "skills": [{"id": "l33", "name": "L33", "description": "d"}],
            "securitySchemes": {}}
    seen: list = []
    register_mount("http://legacy33.test", async_transport=httpx.ASGITransport(app=_mock_agent(card, seen)))
    assert client.post("/api/v1/a2a/agents", json={"url": "http://legacy33.test", "id": "legacy33", "auth": {"scheme": "none"}},
                       headers=H["dev"]).status_code == 201
    approve_agent(client, H, "legacy33")
    client.put("/api/v1/gateway/governance/agents/legacy33/trust", json={"trust_tier": "trusted"}, headers=H["security"])
    tried = client.post("/api/v1/a2a/agents/legacy33/try", json={"text": "hello"}, headers=H["dev"]).json()  # version 생략 = 1.0
    assert seen[-1] == "message/send"  # 업스트림은 0.3 메서드로
    assert tried["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"  # 하류(시험 콘솔)는 1.0 형식
    assert protocols("legacy33")[0] == {"client": "1.0", "upstream": "0.3"}
