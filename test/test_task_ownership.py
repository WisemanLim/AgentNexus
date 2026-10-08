"""Epic 44 — A2A Task 소유권 (D18-①): 플랫폼 A2A 서버와 게이트웨이 경유 Task 는 만든 주체만 보고·바꾼다.
남의 Task 는 존재를 알리지 않는다(TaskNotFound -32001) · 목록은 내 Task 만 · 영속 저장소에서 읽은 Task 도 같은 규칙.
"""
import asyncio

from app.services import a2a_proto as ap
from app.services.a2a_server import A2AServer
from test.test_a2a import AG, call, msg

PLAT = "/a2a/platform/tpl-qa"


def _platform_task(client, H) -> dict:
    res = call(client, H["user"], "message/send", {"message": msg("휴가 정책")}, path=PLAT).json()
    return res["result"]


# ── C44-01 플랫폼 — 남의 Task: list 에 없음 · get·cancel·push·resubscribe·이어 보내기 -32001 · 주인은 그대로 ──
def test_platform_task_owner_only(client, H):
    t = _platform_task(client, H)
    tid = t["id"]
    other = H["user2"]
    listed = call(client, other, "tasks/list", {}, path=PLAT).json()["result"]["tasks"]
    assert tid not in {x["id"] for x in listed}
    assert tid in {x["id"] for x in call(client, H["user"], "tasks/list", {}, path=PLAT).json()["result"]["tasks"]}
    for method, params in (("tasks/get", {"id": tid}), ("tasks/cancel", {"id": tid}), ("GetTask", {"id": tid}),
                           ("tasks/pushNotificationConfig/set", {"taskId": tid, "pushNotificationConfig": {"url": "http://e44-hook.test/h"}}),
                           ("tasks/pushNotificationConfig/get", {"id": tid}),
                           ("message/send", {"message": msg("끼어들기", task_id=tid)})):
        err = call(client, other, method, params, path=PLAT).json().get("error") or {}
        assert err.get("code") == ap.TASK_NOT_FOUND, (method, err)
    sub = client.post(PLAT, json={"jsonrpc": "2.0", "id": 9, "method": "tasks/resubscribe", "params": {"id": tid}}, headers=other)
    assert sub.json()["error"]["code"] == ap.TASK_NOT_FOUND
    rest = client.get(f"{PLAT}/rest/tasks/{tid}", headers=other)
    assert rest.status_code == 404
    assert call(client, H["user"], "tasks/get", {"id": tid}, path=PLAT).json()["result"]["id"] == tid


# ── C44-02 메모리에 없는 Task(다른 복제·재시작 뒤) — 영속 행의 주체로 판정 ──
def test_platform_owner_from_store(client, H):
    from app.api.a2a import _SERVERS
    t = _platform_task(client, H)
    server = _SERVERS["tpl-qa"]
    server.tasks.pop(t["id"], None)
    server.owners.pop(t["id"], None)
    assert call(client, H["user2"], "tasks/get", {"id": t["id"]}, path=PLAT).json()["error"]["code"] == ap.TASK_NOT_FOUND
    assert call(client, H["user"], "tasks/get", {"id": t["id"]}, path=PLAT).json()["result"]["id"] == t["id"]


# ── C44-03 게이트웨이 경유 원격 Task — 다른 주체의 get·cancel -32001 · 목록은 내 Task 만 ──
def test_gateway_task_owner_only(client, H):
    t = call(client, H["user"], "message/send", {"message": msg("orders-api 장애 분석")}).json()["result"]
    path = f"/a2a/{AG}"
    for method in ("tasks/get", "tasks/cancel"):
        err = call(client, H["user2"], method, {"id": t["id"]}, path=path).json().get("error") or {}
        assert err.get("code") == ap.TASK_NOT_FOUND, method
    assert call(client, H["user"], "tasks/get", {"id": t["id"]}, path=path).json()["result"]["id"] == t["id"]
    listed = call(client, H["user2"], "tasks/list", {}, path=path).json()
    tasks = (listed.get("result") or {}).get("tasks")
    assert tasks is not None, listed  # 데모 원격 에이전트는 목록을 지원한다 — 남의 Task 는 걸러진다
    assert t["id"] not in {x.get("id") for x in tasks}
    mine = call(client, H["user"], "tasks/list", {}, path=path).json()["result"]["tasks"]
    assert t["id"] in {x.get("id") for x in mine}


# ── C44-04 owner 없는 서버(데모 원격 에이전트) — 기존 동작 그대로 ──
def test_server_without_owner_unchanged():
    async def executor(ctx):
        return None
    server = A2AServer(lambda: {}, executor)

    async def scenario():
        task = await server._start({"kind": "message", "messageId": "m1", "role": "user", "parts": [{"kind": "text", "text": "x"}]},
                                   {"sub": "a"})
        await server._check_owner(task["id"], {"sub": "b"})  # 검사 없음
        return task["id"]
    tid = asyncio.run(scenario())
    assert tid in server.tasks and not server.owners
