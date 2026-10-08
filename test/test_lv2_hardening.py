"""Epic 49 — 남은 Lv2 하드닝 (D19-②): gRPC 수신 상한 · 원격 grpcs 사설 CA · stdio 전체 명령 허용 · hosted 클라이언트 닫기 ·
업스트림 세션 유휴 TTL · 푸시 전달 재시도.
"""
import asyncio
import datetime
import ipaddress
import time
import types
import uuid

import grpc
import httpx
from a2a.types import a2a_pb2 as pb
from a2a.types import a2a_pb2_grpc as pbg
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core import http
from app.core.config import settings
from app.core.http import StreamingASGITransport, register_mount
from app.db import SessionLocal
from app.services import a2a_grpc, mcp_gateway, secrets
from test.conftest import approve_agent
from test.test_a2a import AG, call, msg
from test.test_a2a_grpc import _EchoAgent, md, req

GW = "http://gw-grpc49.test"


def _ip_cert() -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    return (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            cert.public_bytes(serialization.Encoding.PEM))


# ── C49-01 gRPC 수신 상한 — 4MiB 넘는 메시지는 RESOURCE_EXHAUSTED · keepalive·동시 스트림 옵션 ──
def test_grpc_server_limits(client, H):
    opts = dict(a2a_grpc.SERVER_OPTIONS)
    assert opts["grpc.max_receive_message_length"] == 4 * 1024 * 1024 and opts["grpc.max_concurrent_streams"] == 100
    assert opts["grpc.keepalive_time_ms"] > 0 and a2a_grpc.MAX_CONCURRENT_RPCS > 0
    assert client.post(f"/api/v1/a2a/agents/{AG}/refresh", headers=H["operator"]).status_code == 200
    register_mount(GW, async_transport=StreamingASGITransport(client.app))

    async def scenario():
        server = await a2a_grpc.serve("127.0.0.1:0", gateway_url=GW)
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}",
                                                 options=[("grpc.max_send_message_length", 8 * 1024 * 1024)]) as ch:
                stub = pbg.A2AServiceStub(ch)
                try:
                    await stub.SendMessage(req("x" * (5 * 1024 * 1024)), metadata=md(H), timeout=10)
                    big = "ok"
                except grpc.aio.AioRpcError as exc:
                    big = exc.code().name
                small = pb.TaskState.Name((await stub.SendMessage(req("orders-api 장애 분석"), metadata=md(H))).task.status.state)
        finally:
            await server.stop(0)
        return big, small
    big, small = client.portal.call(scenario)
    assert (big, small) == ("RESOURCE_EXHAUSTED", "TASK_STATE_COMPLETED")


# ── C49-02 원격 grpcs — 에이전트 auth.tls.ca_secret 의 사설 CA 를 신뢰 (없으면 연결 실패) ──
def test_outbound_grpcs_private_ca(client, H):
    key, cert = _ip_cert()
    seen: list = []

    async def start():
        server = grpc.aio.server()
        pbg.add_A2AServiceServicer_to_server(_EchoAgent(seen), server)
        port = server.add_secure_port("127.0.0.1:0", grpc.ssl_server_credentials([(key, cert)]))
        await server.start()
        return server, port
    server, port = client.portal.call(start)
    try:
        card = {"name": "gRPCS only", "description": "사설 CA", "version": "1", "capabilities": {"streaming": True},
                "skills": [{"id": "g", "name": "G"}], "securitySchemes": {},
                "supportedInterfaces": [{"url": f"grpcs://127.0.0.1:{port}", "protocolBinding": "GRPC", "protocolVersion": "1.0",
                                         "tenant": "echo"}]}
        register_mount("http://grpcs-agent.test", async_transport=httpx.ASGITransport(app=Starlette(routes=[
            Route("/.well-known/agent-card.json", lambda _: JSONResponse(card))])))
        reg = client.post("/api/v1/a2a/agents", json={"url": "http://grpcs-agent.test", "id": "grpcs-only", "auth": {"scheme": "none"}},
                          headers=H["dev"])
        assert reg.status_code == 201, reg.text
        approve_agent(client, H, "grpcs-only")
        client.put("/api/v1/gateway/governance/agents/grpcs-only/trust", json={"trust_tier": "trusted"}, headers=H["security"])
        no_ca = call(client, H["user"], "message/send", {"message": msg("hello")}, path="/a2a/grpcs-only").json()
        assert "error" in no_ca  # 시스템 신뢰 저장소로는 사설 CA 서버를 믿지 않는다
        with SessionLocal() as db:
            secrets.put(db, "a2a/grpcs-only/ca", cert.decode(), kind="certificate")
            db.commit()
        assert client.patch("/api/v1/a2a/agents/grpcs-only", json={"auth": {"tls": {"ca_secret": "a2a/grpcs-only/ca"}}},
                            headers=H["dev"]).status_code == 200
        ok = call(client, H["user"], "message/send", {"message": msg("hello")}, path="/a2a/grpcs-only").json()
        assert ok["result"]["status"]["message"]["parts"][0]["text"] == "grpc ok: hello", ok
    finally:
        async def stop():
            await server.stop(0)
        client.portal.call(stop)


# ── C49-03 stdio 허용 목록 — 공백 없는 항목은 실행 파일, 공백이 있는 항목은 명령+인자 전체 일치 ──
def test_stdio_allowlist_full_command(monkeypatch):
    monkeypatch.setattr(settings, "mcp_stdio_allowed_commands", "uvx, npx -y @mcp/server-fs@0.6.2 /data")
    ok = mcp_gateway.stdio_allowed
    assert ok(["uvx", "mcp-server-x"]) and ok(["npx", "-y", "@mcp/server-fs@0.6.2", "/data"])
    assert not ok(["npx", "-y", "@mcp/server-fs@0.6.2", "/etc"]) and not ok(["npx", "evil"]) and not ok(["python", "-c", "x"])
    monkeypatch.setattr(settings, "mcp_stdio_allowed_commands", "")
    assert not ok(["uvx", "x"])


# ── C49-04 hosted 클라이언트 — 설정이 바뀌거나 캐시를 비우면 이전 클라이언트를 닫는다 ──
def test_hosted_clients_closed():
    closed = []

    class Fake:
        def __init__(self, n):
            self.n = n

        async def aclose(self):
            closed.append(self.n)

    async def scenario():
        mcp_gateway._HOSTED.update({"e49:a": object(), "e49:b": object()})
        mcp_gateway._HOSTED_CLIENTS.update({"e49:a": Fake("a"), "e49:b": Fake("b")})
        mcp_gateway._close_hosted(["e49:a"])
        await asyncio.sleep(0.05)
        first = list(closed)
        mcp_gateway.clear_caches()
        await asyncio.sleep(0.05)
        return first
    first = asyncio.run(scenario())
    assert first == ["a"] and sorted(closed) == ["a", "b"] and "e49:b" not in mcp_gateway._HOSTED


# ── C49-05 업스트림 세션 — 유휴 시간이 지나면 다시 initialize ──
def test_upstream_session_idle_reinit(client, monkeypatch):
    inits = []

    async def fake_post(srv, body, hdr):
        if body.get("method") == "initialize":
            inits.append(1)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": "2025-11-25"}},
                                  headers={"mcp-session-id": f"s{len(inits)}"})
        return httpx.Response(202)
    monkeypatch.setattr(mcp_gateway, "_post", fake_post)
    srv = types.SimpleNamespace(id="jira")
    p = types.SimpleNamespace(sub="e49-user")
    key = ("jira", "e49-user")

    async def scenario():
        a = await mcp_gateway._ensure_upstream_session(srv, p, {})
        b = await mcp_gateway._ensure_upstream_session(srv, p, {})
        mcp_gateway._UPSTREAM_SEEN[key] = time.monotonic() - mcp_gateway.UPSTREAM_SESSION_IDLE_S - 1
        c = await mcp_gateway._ensure_upstream_session(srv, p, {})
        return a, b, c
    try:
        a, b, c = client.portal.call(scenario)
    finally:
        mcp_gateway._UPSTREAM_SESSIONS.pop(key, None)
        mcp_gateway._UPSTREAM_SEEN.pop(key, None)
    assert a[0] == b[0] == "s1" and c[0] == "s2" and len(inits) == 2


# ── C49-06 푸시 전달 재시도 — 5xx 는 다시(성공), 4xx 는 바로 포기, 차단 대상은 시도 안 함 ──
def test_post_with_retry():
    hits = {"flaky": 0, "bad": 0}

    def handler(request):
        if request.url.path == "/flaky":
            hits["flaky"] += 1
            return httpx.Response(503 if hits["flaky"] < 3 else 200)
        hits["bad"] += 1
        return httpx.Response(400)
    host = f"http://e49-hook-{uuid.uuid4().hex[:6]}.test"
    register_mount(host, async_transport=httpx.MockTransport(handler))

    async def scenario():
        return (await http.post_with_retry(f"{host}/flaky", base_delay=0.01, json={}),
                await http.post_with_retry(f"{host}/bad", base_delay=0.01, json={}),
                await http.post_with_retry("http://169.254.169.254/x", base_delay=0.01, json={}))
    assert asyncio.run(scenario()) == (True, False, False)
    assert hits == {"flaky": 3, "bad": 1}
