"""Epic 30 — 동의·승인 시간 연장 (D14-③, WCAG 2.2.1 Timing Adjustable): 한 번에 기본 시간만큼(동의 60초 · 도구 승인 30분),
최대 10회(늘어난 합계 ≤ 기본의 10배) · 요청자·결정 권한자만 · 대기/검토 중에만 · 감사 approval.extend · 실행 이벤트 · 대기 루프가 새 기한을 따른다.
"""
import asyncio
import time
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.auth import Principal
from app.core.config import settings
from app.core.http import StreamingASGITransport
from app.db import SessionLocal, utcnow
from app.models import Approval, AuditEvent, RunEvent
from app.services import governance

MCP_H = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
USER = Principal(sub="usr_user", kind="user", name="사용자", roles=["user"])


@pytest.fixture(scope="module", autouse=True)
def refreshed(client, H):
    assert client.post("/api/v1/mcp-servers/internal-orders/refresh", headers=H["operator"]).json()["health"] == "healthy"


def run(client, fn):
    return client.portal.call(fn)


def http(client) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=StreamingASGITransport(client.app), base_url="http://testserver", timeout=60)


def ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


async def pending_sampling(ac, hdr, since: str) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < 20:
        items = (await ac.get("/api/v1/approvals", params={"status": "pending"}, headers=hdr)).json()["items"]
        found = [a for a in items if a["kind"] == "sampling" and a["created_at"] >= since]
        if found:
            return found[0]
        await asyncio.sleep(0.1)
    raise AssertionError("샘플링 동의 요청이 생기지 않음")


def tool_approval(**kw) -> str:
    with SessionLocal() as db:
        return governance.create_approval(db, USER, server_id="jira", tool="transition_issue", args={"key": "ORI-1"},
                                          risk="high", reason="E30", **kw).id


# ── C30-01 동의(샘플링) 연장 — +60초 · 횟수·상한 노출 · 감사 · 다른 사용자 불가 → 거부로 마무리 ──
def test_consent_extend_moves_expiry_and_audits(client, H):
    async def scenario():
        async with http(client) as ac:
            since = utcnow().isoformat()
            call = asyncio.create_task(ac.post("/mcp/internal-orders", headers={**H["user"], **MCP_H}, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "classify_orders", "arguments": {}}}))
            ap = await pending_sampling(ac, H["user"], since)
            ext = await ac.post(f"/api/v1/approvals/{ap['id']}/extend", headers=H["user"])
            other = await ac.post(f"/api/v1/approvals/{ap['id']}/extend", headers=H["user2"])
            await ac.post(f"/api/v1/approvals/{ap['id']}/consent", headers=H["user"], json={"decision": "deny"})
            return ap, ext, other, (await call).json()
    ap, ext, other, res = run(client, scenario)
    assert ap["extensions"] == 0 and ap["max_extensions"] == 10 and ap["extend_step_s"] == int(settings.mcp_consent_timeout_s)
    out = ext.json()
    assert ext.status_code == 200 and out["extensions"] == 1
    assert ts(out["expires_at"]) - ts(ap["expires_at"]) == timedelta(seconds=ap["extend_step_s"])
    assert other.status_code == 404  # 남의 요청은 보이지도 않는다
    assert res["result"]["isError"] is True
    with SessionLocal() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "approval.extend").order_by(AuditEvent.seq.desc())).first()
        assert ev.data["approval_id"] == ap["id"] and ev.data["extensions"] == 1 and ev.actor == "usr_user"


# ── C30-02 상한 — 10회(늘어난 합계 = 기본의 10배) 뒤 extend_limit · 실행 이벤트 approval.extended ──
def test_extend_limit_ten_times(client, H):
    aid = tool_approval(run_id="run_e30")
    first = client.get(f"/api/v1/approvals/{aid}", headers=H["user"]).json()
    for i in range(10):
        r = client.post(f"/api/v1/approvals/{aid}/extend", headers=H["user"])
        assert r.status_code == 200 and r.json()["extensions"] == i + 1
    last = r.json()
    assert ts(last["expires_at"]) - ts(first["expires_at"]) == timedelta(seconds=10 * 1800)
    over = client.post(f"/api/v1/approvals/{aid}/extend", headers=H["user"])
    assert over.status_code == 400 and over.json()["code"] == "extend_limit"
    with SessionLocal() as db:
        evs = db.scalars(select(RunEvent).where(RunEvent.run_id == "run_e30", RunEvent.type == "approval.extended")).all()
        assert len(evs) == 10 and evs[-1].data["extensions"] == 10


# ── C30-03 권한·상태 — 승인자(직무 분리 승인)도 연장 · 끝난 요청 409 · 만료 409 · 기한 없는 승인 400 ──
def test_extend_permissions_and_states(client, H):
    aid = tool_approval(approver="approvers")
    assert client.post(f"/api/v1/approvals/{aid}/extend", headers=H["security"]).json()["extensions"] == 1
    assert client.post(f"/api/v1/approvals/{aid}/reject", headers=H["security"], json={"note": "E30"}).status_code == 200
    closed = client.post(f"/api/v1/approvals/{aid}/extend", headers=H["user"])
    assert closed.status_code == 409 and closed.json()["code"] == "approval_closed"
    old = tool_approval(ttl=timedelta(seconds=-1))
    assert client.post(f"/api/v1/approvals/{old}/extend", headers=H["user"]).json()["code"] == "approval_closed"
    with SessionLocal() as db:
        reg = Approval(id="apr_e30_reg", kind="registry", status="pending", requester_sub="usr_user", target="registry:x", args={})
        db.merge(reg)
        db.commit()
    nolimit = client.post("/api/v1/approvals/apr_e30_reg/extend", headers=H["user"])
    assert nolimit.status_code == 400 and nolimit.json()["code"] == "no_time_limit"


# ── C30-04 대기 루프가 새 기한을 따른다 — 1초 기한을 연장하면 1초가 지나도 만료되지 않고, 허용하면 도구가 완료된다 ──
def test_extension_keeps_consent_alive(client, H, monkeypatch):
    monkeypatch.setattr(settings, "mcp_consent_timeout_s", 1.0)

    async def scenario():
        async with http(client) as ac:
            since = utcnow().isoformat()
            call = asyncio.create_task(ac.post("/mcp/internal-orders", headers={**H["user"], **MCP_H}, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "classify_orders", "arguments": {}}}))
            ap = await pending_sampling(ac, H["user"], since)
            for _ in range(3):
                await ac.post(f"/api/v1/approvals/{ap['id']}/extend", headers=H["user"])
            await asyncio.sleep(1.5)  # 원래 기한(1초)은 지났다
            mid = (await ac.get(f"/api/v1/approvals/{ap['id']}", headers=H["user"])).json()
            allow = await ac.post(f"/api/v1/approvals/{ap['id']}/consent", headers=H["user"], json={"decision": "allow"})
            return mid, allow.status_code, (await call).json()
    mid, allow, res = run(client, scenario)
    assert mid["status"] == "pending" and mid["extensions"] == 3
    assert allow == 200 and not res["result"].get("isError") and res["result"]["structuredContent"]["classification"]
