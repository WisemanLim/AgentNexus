"""Epic 45 — 다중 워커 일관성 (D18-②): listen 구독 한도 전역 · progress 워커 간 중계 · 백그라운드 루프 리스.

여러 워커를 한 프로세스에서 흉내 낸다 — 다른 워커가 쥔 상태(공유 카운터·공유 행·리스)를 직접 만들고, 이 워커의 동작을 본다.
"""
import asyncio

from app.services import a2a_gateway, mcp_modern, ratelimit, scheduler
from test.test_mcp_modern_ext import body, hdr, http, run

FLT = {"notifications": {"toolsListChanged": True}}


# ── C45-01 공유 카운터 — 0 아래로 내려가지 않음 · TTL 이 지나면 0(보유 워커가 죽어도 풀림) ──
def test_counter_semantics(monkeypatch):
    b = ratelimit.MemoryBackend()
    assert b.counter_add("k", 1, 60) == 1 and b.counter_add("k", 1, 60) == 2
    assert b.counter_add("k", -5, 60) == 0
    t = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t[0])
    b.counter_add("k", 3, 10)
    t[0] += 11
    assert b.counter_add("k", 0, 10) == 0


# ── C45-02 listen 한도 — 다른 워커들이 이미 MAX 개를 쥐고 있으면 이 워커에서도 429 · 반납 뒤 다시 가능 (보유자 단위 D20-④) ──
def test_listen_limit_is_global(client, H):
    key = mcp_modern._listen_key("usr_dev")
    ttl = int(mcp_modern.LISTEN_MAX_S) + 60
    others = [f"w-other:{i}" for i in range(mcp_modern.MAX_LISTENS)]  # 다른 워커 구독 5개
    for h in others:
        assert ratelimit.backend().holder_acquire(key, h, mcp_modern.MAX_LISTENS, ttl)
    try:
        async def scenario():
            async with http(client) as ac:
                return await ac.post("/mcp/jira", json=body("subscriptions/listen", FLT, rid=451), headers=hdr(H["dev"], "subscriptions/listen"))
        capped = run(client, scenario)
        assert capped.status_code == 429 and mcp_modern.LISTENS.get("usr_dev", 0) == 0
        assert mcp_modern.listen_count("usr_dev") == mcp_modern.MAX_LISTENS  # 거절한 시도는 넣지 않았다
    finally:
        for h in others:
            ratelimit.backend().holder_release(key, h)
    holder = mcp_modern.listen_acquire("usr_dev")
    assert holder
    mcp_modern.listen_release("usr_dev", holder)
    assert mcp_modern.listen_count("usr_dev") == 0


# ── C45-03 progress — 이 워커에 기다리는 요청이 없으면 공유 행으로, 재시도는 쌓인 것부터 받고 다른 워커 호출은 공유 행으로 이어 받는다 ──
def test_progress_relayed_across_workers(client, monkeypatch):
    state = "mrtr_e45"

    async def scenario():
        put = mcp_modern._sink(state, "jira")  # 다른 워커(A)의 업스트림 호출 — 하류 요청은 끊긴 상태
        await put({"progress": 1, "total": 3})
        backlog = await asyncio.to_thread(mcp_modern._take_progress, state)
        await put({"progress": 2, "total": 3})

        async def later_progress():
            await asyncio.sleep(0.2)
            await put({"progress": 3, "total": 3})

        async def fake_wait(state_id, rid, sealed, task):
            await asyncio.sleep(0.9)
            return {"__final__": {"result": {"content": []}}}
        monkeypatch.setattr(mcp_modern, "unseal", lambda *a, **k: state)
        monkeypatch.setattr(mcp_modern, "_answer", lambda *a, **k: 0)
        monkeypatch.setattr(mcp_modern, "_wait", fake_wait)
        ctx = type("C", (), {"principal": type("P", (), {"sub": "u"})(), "state_id": None})()
        q: asyncio.Queue = asyncio.Queue()
        feeder = asyncio.create_task(later_progress())
        out = await mcp_modern.resume("jira", ctx, "tools/call", {"requestState": "x"}, 1, progress=q)  # 이 워커(B) — _RUNNING 에 없음
        await feeder
        got = [q.get_nowait()["progress"] for _ in range(q.qsize())]
        return backlog, got, out, await asyncio.to_thread(mcp_modern._take_progress, state)
    backlog, got, out, left = run(client, scenario)
    assert [p["progress"] for p in backlog] == [1]
    assert got == [2, 3] and "__final__" in out and left == []


# ── C45-04 리스 — 한 워커만 백그라운드 작업 · 리스가 없는 워커의 A2A watcher 는 폴링하지 않는다 ──
def test_background_leases(client, monkeypatch):
    assert scheduler.lease("e45-job", 30, owner="w1") is True
    assert scheduler.lease("e45-job", 30, owner="w2") is False
    assert scheduler.lease("e45-job", 30, owner="w1") is True  # 보유자 갱신
    polled = []

    async def fake_poll():
        polled.append(1)
        return 0
    monkeypatch.setattr(a2a_gateway, "poll_once", fake_poll)

    async def scenario(has_lease: bool):
        monkeypatch.setattr(scheduler, "lease", lambda name, ttl, owner=scheduler.WORKER: has_lease)
        task = asyncio.create_task(a2a_gateway.watcher(0.05))
        await asyncio.sleep(0.3)
        task.cancel()
    run(client, lambda: scenario(False))
    assert polled == []
    run(client, lambda: scenario(True))
    assert len(polled) >= 2


# ── C45-05 끊긴 구독도 반납 — 스트림 도중 클라이언트가 끊으면(취소) 카운터가 돌아온다 ──
def test_listen_released_on_disconnect(client, H, monkeypatch):
    monkeypatch.setattr(mcp_modern, "LISTEN_POLL_S", 0.1)

    async def scenario():
        async with http(client) as ac:
            async with ac.stream("POST", "/mcp/jira", json=body("subscriptions/listen", FLT, rid=452),
                                 headers=hdr(H["dev"], "subscriptions/listen")) as res:
                async for chunk in res.aiter_text():
                    if "acknowledged" in chunk:
                        held = mcp_modern.listen_count("usr_dev")
                        break  # 클라이언트가 끊는다
        for _ in range(50):
            if mcp_modern.listen_count("usr_dev") == 0:
                break
            await asyncio.sleep(0.1)
        return held, mcp_modern.listen_count("usr_dev")
    held, after = run(client, scenario)
    assert (held, after) == (1, 0)
