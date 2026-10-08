"""Epic 15 — 업스트림 mTLS (FR-G02): MCP 서버·LLM 공급자·원격 A2A 에이전트에 클라이언트 인증서로 접속.

시험용 TLS 서버(uvicorn, ssl_cert_reqs=CERT_REQUIRED)를 별도 스레드에서 띄우고, CA·서버·클라이언트 인증서는 시험마다 생성한다.
인증서·키는 시크릿 저장소(kind=certificate)에만 저장되고 API 응답·감사에 평문이 나오지 않아야 한다.
"""
import datetime as dt
import ipaddress
import json
import socket
import ssl
import threading
import time

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

MCP_H = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem_key(k) -> bytes:
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _cert(subject: str, key, issuer_name, issuer_key, *, ca=False, server=False):
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
         .issuer_name(issuer_name or x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if server:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                         x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        b = b.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
    elif not ca:
        b = b.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), False)
    return b.sign(issuer_key, hashes.SHA256())


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("pki")
    ca_key, rogue_key = _key(), _key()
    ca = _cert("AgentNexus Test CA", ca_key, None, ca_key, ca=True)
    rogue = _cert("Rogue CA", rogue_key, None, rogue_key, ca=True)
    srv_key, cli_key, bad_key = _key(), _key(), _key()
    srv = _cert("localhost", srv_key, ca.subject, ca_key, server=True)
    cli = _cert("agentnexus-gateway", cli_key, ca.subject, ca_key)
    bad_cli = _cert("intruder", bad_key, rogue.subject, rogue_key)
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM)  # noqa: E731
    (d / "ca.pem").write_bytes(pem(ca))
    (d / "srv.pem").write_bytes(pem(srv))
    (d / "srv.key").write_bytes(_pem_key(srv_key))
    return {"dir": d, "ca": pem(ca).decode(), "rogue_ca": pem(rogue).decode(),
            "client": (pem(cli) + _pem_key(cli_key)).decode(), "bad_client": (pem(bad_cli) + _pem_key(bad_key)).decode()}


@pytest.fixture(scope="module")
def tls_server(pki):
    """클라이언트 인증서 필수 HTTPS — MCP(stateless JSON) · OpenAI 호환 chat · A2A Agent Card."""
    mcp = FastMCP("mtls-echo")

    @mcp.tool(annotations={"readOnlyHint": True})
    def echo(text: str) -> dict:
        """에코."""
        return {"echo": text}
    mcp_app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)

    async def chat(request):
        body = await request.json()
        return JSONResponse({"id": "c1", "object": "chat.completion", "model": body.get("model"),
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": "mTLS pong"}}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})

    async def card(request):
        base = f"https://127.0.0.1:{request.url.port}"
        return JSONResponse({"name": "mTLS 에이전트", "description": "mTLS 시험", "version": "1.0.0",
                             "protocolVersion": "0.3.0", "url": f"{base}/a2a", "preferredTransport": "JSONRPC",
                             "capabilities": {"streaming": False}, "defaultInputModes": ["text/plain"],
                             "defaultOutputModes": ["text/plain"],
                             "skills": [{"id": "echo", "name": "echo", "description": "에코", "tags": ["test"]}]})
    app = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]),
                            Route("/.well-known/agent-card.json", card), Mount("/echo", app=mcp_app)],
                    lifespan=mcp_app.lifespan)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    d = pki["dir"]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on",
                                           ssl_certfile=str(d / "srv.pem"), ssl_keyfile=str(d / "srv.key"),
                                           ssl_ca_certs=str(d / "ca.pem"), ssl_cert_reqs=ssl.CERT_REQUIRED))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"https://127.0.0.1:{port}"
    server.should_exit = True
    th.join(timeout=5)


@pytest.fixture(scope="module")
def secrets_stored(client, H, pki):
    for name, value in (("tls/test-ca", pki["ca"]), ("tls/rogue-ca", pki["rogue_ca"]),
                        ("tls/gateway-client", pki["client"]), ("tls/intruder-client", pki["bad_client"])):
        res = client.post("/api/v1/gateway/secrets", json={"name": name, "value": value, "kind": "certificate"},
                          headers=H["admin"])
        assert res.status_code in (200, 201), res.text
        assert "PRIVATE KEY" not in res.text and "BEGIN CERT" not in res.text  # 평문 비노출 (MSG-018)


def _tls(ca="tls/test-ca", cert="tls/gateway-client"):
    return {k: v for k, v in (("ca_secret", ca), ("client_cert_secret", cert)) if v}


def _set_tls(client, H, sid, tls):
    res = client.patch(f"/api/v1/mcp-servers/{sid}", json={"auth": {"tls": tls}}, headers=H["admin"])
    assert res.status_code == 200, res.text
    return client.post(f"/api/v1/mcp-servers/{sid}/refresh", headers=H["admin"]).json()


def test_mcp_server_requires_client_certificate(client, H, tls_server, secrets_stored):
    doc = {"$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
           "name": "io.test/mtls-echo", "title": "mTLS Echo", "description": "mTLS 시험 서버", "version": "1.0.0",
           "remotes": [{"type": "streamable-http", "url": f"{tls_server}/echo/mcp"}]}
    reg = client.post("/api/v1/mcp-servers", json={"server_json": doc, "namespace": "mtls-echo"}, headers=H["admin"])
    assert reg.status_code == 201, reg.text
    no_cert = _set_tls(client, H, "mtls-echo", _tls(cert=None))       # CA 만 — 서버가 클라이언트 인증서 요구 → 실패
    assert no_cert["health"] != "healthy"
    rogue = _set_tls(client, H, "mtls-echo", _tls(cert="tls/intruder-client"))  # 다른 CA 가 서명한 인증서 → 거부
    assert rogue["health"] != "healthy"
    wrong_ca = _set_tls(client, H, "mtls-echo", _tls(ca="tls/rogue-ca"))       # 서버 인증서를 신뢰하지 않음 → 검증 실패
    assert wrong_ca["health"] != "healthy"
    ok = _set_tls(client, H, "mtls-echo", _tls())
    assert ok["health"] == "healthy", ok
    res = client.post("/mcp/mtls-echo", headers={**H["dev"], **MCP_H}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "안녕"}}})
    assert res.json()["result"]["structuredContent"] == {"echo": "안녕"}
    detail = client.get("/api/v1/mcp-servers/mtls-echo", headers=H["admin"]).text
    assert "PRIVATE KEY" not in detail and "tls/gateway-client" in detail  # 설정은 시크릿 이름만


def test_required_policy_fails_closed_for_plain_upstreams(client, H, tls_server, secrets_stored):
    try:
        assert client.put("/api/v1/gateway/security", json={"mtls": {"upstream": "nope"}},
                          headers=H["security"]).status_code == 400
        client.put("/api/v1/gateway/security", json={"mtls": {"upstream": "required"}}, headers=H["security"])
        plain = client.post("/mcp/jira", headers={**H["user"], **MCP_H}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_issues", "arguments": {}}})
        err = plain.json()["error"]
        assert err.get("data", {}).get("code") == "mtls_required" and "mTLS 필수" in err["message"], err
        _set_tls(client, H, "mtls-echo", _tls())  # https + 클라이언트 인증서 → 허용
        ok = client.post("/mcp/mtls-echo", headers={**H["dev"], **MCP_H}, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "필수"}}})
        assert ok.json()["result"]["structuredContent"] == {"echo": "필수"}
        # mock 공급자는 네트워크가 없으므로 영향 없음 — 모델 호출은 계속 동작
        chat = client.post("/v1/chat/completions", headers={**H["user"], "X-AgentNexus-Data-Class": "public"},
                           json={"model": "fast-commercial", "messages": [{"role": "user", "content": "x"}]})
        assert chat.status_code == 200
    finally:
        client.put("/api/v1/gateway/security", json={"mtls": {"upstream": "optional", "client": "disabled"}},
                   headers=H["security"])
        assert client.post("/api/v1/mcp-servers/jira/refresh", headers=H["operator"]).json()["health"] == "healthy"


def test_llm_provider_over_mtls(client, H, tls_server, secrets_stored):
    base = {"id": "mtls-llm", "name": "mTLS LLM", "kind": "openai_compat", "base_url": f"{tls_server}/v1"}
    assert client.post("/api/v1/providers", json={**base, "options": {}}, headers=H["admin"]).status_code == 201
    fail = client.post("/api/v1/providers/mtls-llm/test", json={}, headers=H["admin"]).json()
    assert fail["ok"] is False  # 클라이언트 인증서·CA 없음
    client.patch("/api/v1/providers/mtls-llm", json={"options": {"tls": _tls()}}, headers=H["admin"])
    ok = client.post("/api/v1/providers/mtls-llm/test", json={}, headers=H["admin"]).json()
    assert ok["ok"] is True and ok["reply"] == "mTLS pong", ok


def test_a2a_agent_card_over_mtls(client, H, tls_server, secrets_stored):
    no = client.post("/api/v1/a2a/agents", json={"url": tls_server, "id": "mtls-agent-x"}, headers=H["admin"])
    assert no.status_code >= 400
    ok = client.post("/api/v1/a2a/agents", json={"url": tls_server, "id": "mtls-agent", "auth": {"tls": _tls()}},
                     headers=H["admin"])
    assert ok.status_code == 201, ok.text
    assert ok.json()["name"] == "mTLS 에이전트" and json.dumps(ok.json()).count("PRIVATE") == 0
    assert client.post("/api/v1/a2a/agents/mtls-agent/refresh", headers=H["admin"]).status_code == 200
