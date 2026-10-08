"""Epic 37 — A2A gRPC 바인딩 (D16-②): 정규 lf.a2a.v1.A2AService 수신(게이트웨이 REST 로 위임 — 같은 인증·정책·감사) ·
tenant 라우팅(원격 에이전트·platform·platform/{profile}) · google.rpc.Status 트레일러(ErrorInfo) · 서버 스트림 ·
카드 GRPC 인터페이스(tenant) · gRPC 전용 원격 에이전트 호출(JSON-RPC 클라이언트 ↔ gRPC 업스트림 변환).
"""
import uuid

import grpc
import httpx
import pytest
from a2a.types import a2a_pb2 as pb
from a2a.types import a2a_pb2_grpc as pbg
from google.rpc import error_details_pb2, status_pb2
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.config import settings
from app.core.http import StreamingASGITransport, register_mount
from app.db import SessionLocal
from app.models import A2ATask
from app.services import a2a_grpc
from test.conftest import approve_agent
from test.test_a2a import AG, call, msg

GW = "http://gw-grpc.test"


def run(client, fn):
    return client.portal.call(fn)


@pytest.fixture(scope="module")
def grpc_gateway(client, H):
    assert client.post(f"/api/v1/a2a/agents/{AG}/refresh", headers=H["operator"]).status_code == 200
    register_mount(GW, async_transport=StreamingASGITransport(client.app))

    async def start():
        return await a2a_grpc.serve("127.0.0.1:0", gateway_url=GW)
    server = run(client, start)
    yield f"127.0.0.1:{server.bound_port}"

    async def stop():
        await server.stop(0)
    run(client, stop)


def md(H, who="user"):
    return [("authorization", H[who]["Authorization"])]


def req(text: str, tenant: str = AG) -> pb.SendMessageRequest:
    return pb.SendMessageRequest(tenant=tenant, message=pb.Message(message_id=uuid.uuid4().hex, role=pb.ROLE_USER, parts=[pb.Part(text=text)]))


def reason_of(exc: grpc.aio.AioRpcError) -> str | None:
    for k, v in exc.trailing_metadata() or ():
        if k == "grpc-status-details-bin":
            for d in status_pb2.Status.FromString(v).details:
                info = error_details_pb2.ErrorInfo()
                if d.Unpack(info):
                    return f"{info.reason}@{info.domain}"
    return None


# ── C37-01 수신 — send·get·stream·cancel · 오류 트레일러 · 인증 · tenant 라우팅 · Task 이벤트 client_binding GRPC ──
def test_grpc_inbound(client, H, grpc_gateway):
    async def scenario():
        out = {}
        async with grpc.aio.insecure_channel(grpc_gateway) as ch:
            stub = pbg.A2AServiceStub(ch)
            sent = await stub.SendMessage(req("orders-api 장애 분석"), metadata=md(H))
            out["send"] = (pb.TaskState.Name(sent.task.status.state), sent.task.id)
            got = await stub.GetTask(pb.GetTaskRequest(tenant=AG, id=sent.task.id, history_length=1), metadata=md(H))
            out["get"] = got.id
            events = [ev async for ev in stub.SendStreamingMessage(req("orders-api 장애 분석"), metadata=md(H))]
            out["stream"] = [ev.WhichOneof("payload") for ev in events]
            out["stream_last"] = pb.TaskState.Name(events[-1].status_update.status.state) if events[-1].HasField("status_update") \
                else pb.TaskState.Name(events[-1].task.status.state)
            for key, coro in (("cancel", stub.CancelTask(pb.CancelTaskRequest(tenant=AG, id=sent.task.id), metadata=md(H))),
                              ("missing", stub.GetTask(pb.GetTaskRequest(tenant=AG, id="no-such"), metadata=md(H))),
                              ("anon", stub.SendMessage(req("x"))),
                              ("tenant", stub.SendMessage(req("x", tenant="a/b/c"), metadata=md(H))),
                              ("unsupported", stub.ListTaskPushNotificationConfigs(
                                  pb.ListTaskPushNotificationConfigsRequest(tenant=AG, task_id=sent.task.id), metadata=md(H)))):
                try:
                    await coro
                    out[key] = "ok"
                except grpc.aio.AioRpcError as exc:
                    out[key] = (exc.code().name, reason_of(exc))
            plat = await stub.SendMessage(req("휴가 정책", tenant="platform/tpl-qa"), metadata=md(H))
            out["platform"] = pb.TaskState.Name(plat.task.status.state)
        return out
    out = run(client, scenario)
    assert out["send"][0] == "TASK_STATE_COMPLETED" and out["get"] == out["send"][1]
    assert out["stream"][0] in ("task", "status_update") and out["stream_last"] == "TASK_STATE_COMPLETED"
    assert out["cancel"] == ("FAILED_PRECONDITION", "TASK_NOT_CANCELABLE@a2a-protocol.org")
    assert out["missing"] == ("NOT_FOUND", "TASK_NOT_FOUND@a2a-protocol.org")
    assert out["anon"][0] == "UNAUTHENTICATED" and out["tenant"][0] == "INVALID_ARGUMENT"
    assert out["unsupported"] == ("FAILED_PRECONDITION", "UNSUPPORTED_OPERATION@a2a-protocol.org")
    assert out["platform"] == "TASK_STATE_COMPLETED"
    with SessionLocal() as db:
        row = db.get(A2ATask, f"{AG}:{out['send'][1]}")
        assert any((e.get("protocol") or {}).get("client_binding") == "GRPC" for e in row.events or [])


# ── C37-02 정책 동일 — 차단 등급은 gRPC 로도 PERMISSION_DENIED (게이트웨이 사유) ──
def test_grpc_policy_parity(client, H, grpc_gateway):
    client.put(f"/api/v1/gateway/governance/agents/{AG}/trust", json={"trust_tier": "blocked"}, headers=H["security"])

    async def scenario():
        async with grpc.aio.insecure_channel(grpc_gateway) as ch:
            try:
                await pbg.A2AServiceStub(ch).SendMessage(req("x"), metadata=md(H))
                return "ok"
            except grpc.aio.AioRpcError as exc:
                return exc.code().name, reason_of(exc)
    try:
        code, reason = run(client, scenario)
    finally:
        client.put(f"/api/v1/gateway/governance/agents/{AG}/trust", json={"trust_tier": "trusted"}, headers=H["security"])
    assert code == "PERMISSION_DENIED" and reason.endswith("@agentnexus")


# ── C37-03 카드 GRPC 인터페이스(tenant) — 광고 주소가 있을 때만 ──
def test_cards_grpc_interface(client, monkeypatch):
    monkeypatch.setattr(settings, "a2a_grpc_public_url", "")
    assert not any(i["protocolBinding"] == "GRPC" for i in client.get("/.well-known/agent-card.json").json()["supportedInterfaces"])
    monkeypatch.setattr(settings, "a2a_grpc_public_url", "127.0.0.1:8851")
    pcard = client.get("/a2a/platform/tpl-qa/.well-known/agent-card.json").json()
    gcard = client.get(f"/a2a/{AG}/.well-known/agent-card.json").json()
    g = lambda card: next(i for i in card["supportedInterfaces"] if i["protocolBinding"] == "GRPC")  # noqa: E731
    assert g(pcard) == {"url": "127.0.0.1:8851", "protocolBinding": "GRPC", "protocolVersion": "1.0", "tenant": "platform/tpl-qa"}
    assert g(gcard)["tenant"] == AG


# ── C37-04 gRPC 전용 원격 에이전트 — JSON-RPC 0.3·1.0 클라이언트를 게이트웨이가 gRPC 로 (tenant·인증 메타데이터) ──
class _EchoAgent(pbg.A2AServiceServicer):
    def __init__(self, seen: list):
        self.seen, self.tasks = seen, {}

    def _task(self, tid: str, text: str, state=pb.TASK_STATE_COMPLETED) -> pb.Task:
        return pb.Task(id=tid, context_id="ctx-g", status=pb.TaskStatus(state=state, message=pb.Message(
            message_id=uuid.uuid4().hex, role=pb.ROLE_AGENT, parts=[pb.Part(text=f"grpc ok: {text}")])))

    async def SendMessage(self, request, context):
        self.seen.append(("SendMessage", request.tenant, dict(context.invocation_metadata()).get("a2a-version")))
        tid = uuid.uuid4().hex
        self.tasks[tid] = self._task(tid, request.message.parts[0].text)
        return pb.SendMessageResponse(task=self.tasks[tid])

    async def GetTask(self, request, context):
        self.seen.append(("GetTask", request.tenant, None))
        if request.id not in self.tasks:
            await context.abort(grpc.StatusCode.NOT_FOUND, "없음")
        return self.tasks[request.id]

    async def SendStreamingMessage(self, request, context):
        self.seen.append(("SendStreamingMessage", request.tenant, None))
        tid = uuid.uuid4().hex
        yield pb.StreamResponse(task=self._task(tid, request.message.parts[0].text, pb.TASK_STATE_WORKING))
        yield pb.StreamResponse(status_update=pb.TaskStatusUpdateEvent(task_id=tid, context_id="ctx-g",
                                                                        status=pb.TaskStatus(state=pb.TASK_STATE_COMPLETED)))


def test_outbound_grpc_only_agent(client, H):
    seen: list = []

    async def start():
        server = grpc.aio.server()
        pbg.add_A2AServiceServicer_to_server(_EchoAgent(seen), server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        return server, port
    server, port = run(client, start)
    try:
        card = {"name": "gRPC only", "description": "GRPC 전용", "version": "1", "capabilities": {"streaming": True},
                "skills": [{"id": "g", "name": "G"}], "securitySchemes": {},
                "supportedInterfaces": [{"url": f"127.0.0.1:{port}", "protocolBinding": "GRPC", "protocolVersion": "1.0", "tenant": "echo"}]}
        register_mount("http://grpc-agent.test", async_transport=httpx.ASGITransport(app=Starlette(routes=[
            Route("/.well-known/agent-card.json", lambda _: JSONResponse(card))])))
        reg = client.post("/api/v1/a2a/agents", json={"url": "http://grpc-agent.test", "id": "grpc-only", "auth": {"scheme": "none"}}, headers=H["dev"])
        assert reg.status_code == 201, reg.text
        assert "GRPC" in reg.json()["bindings"]
        approve_agent(client, H, "grpc-only")
        client.put("/api/v1/gateway/governance/agents/grpc-only/trust", json={"trust_tier": "trusted"}, headers=H["security"])
        v03 = call(client, H["user"], "message/send", {"message": msg("hello")}, path="/a2a/grpc-only").json()["result"]
        assert v03["kind"] == "task" and v03["status"]["message"]["parts"][0]["text"] == "grpc ok: hello"
        assert seen[0] == ("SendMessage", "echo", "1.0")
        got = call(client, H["user"], "GetTask", {"id": v03["id"]}, path="/a2a/grpc-only").json()["result"]
        assert got["id"] == v03["id"]
        assert call(client, H["user"], "tasks/get", {"id": "nope"}, path="/a2a/grpc-only").json()["error"]["code"] == -32001
        with client.stream("POST", "/a2a/grpc-only", json={"jsonrpc": "2.0", "id": 7, "method": "message/stream",
                                                           "params": {"message": msg("s")}}, headers=H["user"]) as res:
            import json
            evs = [json.loads(line[5:].strip()) for line in res.iter_lines() if line.startswith("data:")]
        assert evs[-1]["result"]["status"]["state"] == "completed" and ("SendStreamingMessage", "echo", None) in seen
        with SessionLocal() as db:
            row = db.get(A2ATask, f"grpc-only:{v03['id']}")
            assert any((e.get("protocol") or {}).get("upstream_binding") == "GRPC" for e in row.events or [])
    finally:
        async def stop():
            await server.stop(0)
        run(client, stop)


# ── C41-01 수신 TLS (D17-②) — 인증서·키가 있으면 TLS 로만 수신: 신뢰한 클라이언트는 성공, 평문 클라이언트는 실패 · 파일 경로 설정 ──
def _self_signed(host: str = "localhost") -> tuple[bytes, bytes]:
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM))


def test_grpc_inbound_tls(client, H, tmp_path, monkeypatch):
    key, cert = _self_signed()
    (tmp_path / "tls.key").write_bytes(key)
    (tmp_path / "tls.crt").write_bytes(cert)
    monkeypatch.setattr(settings, "a2a_grpc_tls_cert", str(tmp_path / "tls.crt"))
    monkeypatch.setattr(settings, "a2a_grpc_tls_key", str(tmp_path / "tls.key"))
    assert a2a_grpc.server_tls() == (key, cert)
    assert client.post(f"/api/v1/a2a/agents/{AG}/refresh", headers=H["operator"]).status_code == 200
    register_mount(GW, async_transport=StreamingASGITransport(client.app))

    async def scenario():
        server = await a2a_grpc.serve("127.0.0.1:0", gateway_url=GW)  # 설정 경로에서 읽는다
        addr = f"127.0.0.1:{server.bound_port}"
        out = {"tls": server.tls}
        try:
            creds = grpc.ssl_channel_credentials(root_certificates=cert)
            async with grpc.aio.secure_channel(addr, creds, options=[("grpc.ssl_target_name_override", "localhost")]) as ch:
                sent = await pbg.A2AServiceStub(ch).SendMessage(req("orders-api 장애 분석"), metadata=md(H))
                out["secure"] = pb.TaskState.Name(sent.task.status.state)
            async with grpc.aio.insecure_channel(addr) as ch:
                try:
                    await pbg.A2AServiceStub(ch).SendMessage(req("x"), metadata=md(H), timeout=3)
                    out["plain"] = "ok"
                except grpc.aio.AioRpcError as exc:
                    out["plain"] = exc.code().name
        finally:
            await server.stop(0)
        return out
    out = run(client, scenario)
    assert out == {"tls": True, "secure": "TASK_STATE_COMPLETED", "plain": "UNAVAILABLE"}
