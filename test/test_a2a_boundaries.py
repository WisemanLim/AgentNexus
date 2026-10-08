"""Epic 56 — 원격 A2A·푸시 경계 (D21-①): 푸시 JWT 발급자·jti·본문 해시 필수 · 원격 응답 상한·비 JSON · 스트림 오류 이벤트 ·
history 상한 · 프록시 카드 URL = PUBLIC_BASE_URL."""
import asyncio
import hashlib
import json
import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.core.config import settings
from app.core.http import register_mount
from app.db import SessionLocal
from app.models import A2AAgent, A2ATask
from app.services import a2a_gateway
from app.services.a2a_gateway import A2AError
from test.conftest import approve_agent
from test.test_a2a import AG, call, msg, sse

ISS = "https://agent.e56.example"


# ── C56-01 푸시 JWT — 발급자·jti·본문 해시 필수, 같은 jti 재전송 거부 · JWKS 를 쓰면 issuer 도 설정해야 ──
def test_push_jwt_requires_iss_jti_hash(client, H):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="e56", alg="RS256", use="sig")
    register_mount("http://jwks-e56", sync_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"keys": [jwk]})))
    with SessionLocal() as db:
        old = dict(db.get(A2AAgent, AG).auth or {})
        db.merge(A2ATask(id=f"{AG}:e56-push", remote_task_id="e56-push", agent_id=AG, state="working", push_token="e56-tok",
                         history=[], artifacts=[]))
        db.commit()
    no_iss = client.patch(f"/api/v1/a2a/agents/{AG}", json={"auth": {"push_verify": {"jwks_url": "http://jwks-e56/jwks"}}},
                          headers=H["operator"])
    assert no_iss.status_code == 400 and no_iss.json()["code"] == "push_verify_issuer_required"
    assert client.patch(f"/api/v1/a2a/agents/{AG}", json={"auth": {"push_verify": {"jwks_url": "http://jwks-e56/jwks", "issuer": ISS}}},
                        headers=H["operator"]).status_code == 200
    body = json.dumps({"kind": "task", "id": "e56-push", "status": {"state": "working"}}).encode()
    base = {"iss": ISS, "iat": int(time.time()), "request_body_sha256": hashlib.sha256(body).hexdigest()}

    def sign(**claims):
        c = {**base, "jti": uuid.uuid4().hex, **claims}
        return jwt.encode({k: v for k, v in c.items() if v is not None}, key, algorithm="RS256", headers={"kid": "e56"})
    post = lambda t: client.post("/a2a/push/e56-tok", content=body,  # noqa: E731
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {t}"}).status_code
    try:
        assert post(sign(iss=None)) == 401 and post(sign(iss="https://evil.example")) == 401
        assert post(sign(jti=None)) == 401 and post(sign(request_body_sha256=None)) == 401
        once = sign()
        assert post(once) == 200 and post(once) == 401  # 같은 토큰 재전송 거부
        assert post(sign()) == 200
    finally:
        with SessionLocal() as db:
            db.get(A2AAgent, AG).auth = old
            db.delete(db.get(A2ATask, f"{AG}:e56-push"))
            db.commit()


# ── C56-02 원격 응답 — 상한을 넘으면 오류(전부 읽지 않음) · 200 인데 JSON 이 아니면 502 ──
def test_remote_response_cap_and_non_json(monkeypatch):
    monkeypatch.setattr(a2a_gateway, "MAX_REMOTE_BYTES", 2000)
    big = {"jsonrpc": "2.0", "id": 1, "result": {"kind": "task", "id": "t", "pad": "x" * 5000}}
    register_mount("http://e56-big.test", async_transport=httpx.MockTransport(lambda r: httpx.Response(200, json=big)))
    register_mount("http://e56-html.test", async_transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>oops</html>")))
    register_mount("http://e56-ok.test", async_transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"kind": "task", "id": "t"}})))

    def rpc(host):
        return asyncio.run(a2a_gateway._rpc(f"{host}/", "message/send", {}, {}, "0.3"))
    with pytest.raises(A2AError) as exc:
        rpc("http://e56-big.test")
    assert "상한" in exc.value.message and exc.value.http_status == 502
    with pytest.raises(A2AError) as exc:
        rpc("http://e56-html.test")
    assert "JSON" in exc.value.message
    assert rpc("http://e56-ok.test") == {"kind": "task", "id": "t"}


# ── C56-03 스트림 — 원격 이벤트가 JSON 이 아니면 오류 이벤트로 알리고 끝낸다 (전에는 말없이 끊김) ──
def test_stream_bad_event_becomes_error_event(client, H):
    card = {"name": "E56 stream", "description": "깨진 이벤트", "version": "1", "capabilities": {"streaming": True},
            "skills": [{"id": "s", "name": "S"}], "securitySchemes": {},
            "supportedInterfaces": [{"url": "http://e56-sse.test/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]}

    async def rpc(_req):
        good = {"jsonrpc": "2.0", "id": 1, "result": {"statusUpdate": {"taskId": "e56t", "contextId": "c",
                                                                        "status": {"state": "TASK_STATE_WORKING"}}}}
        return Response(f"data: {json.dumps(good)}\n\ndata: {{not json\n\n", media_type="text/event-stream")
    register_mount("http://e56-sse.test", async_transport=httpx.ASGITransport(app=Starlette(routes=[
        Route("/.well-known/agent-card.json", lambda _: JSONResponse(card)), Route("/", rpc, methods=["POST"])])))
    assert client.post("/api/v1/a2a/agents", json={"url": "http://e56-sse.test", "id": "e56-sse", "auth": {"scheme": "none"}},
                       headers=H["dev"]).status_code == 201
    approve_agent(client, H, "e56-sse")
    client.put("/api/v1/gateway/governance/agents/e56-sse/trust", json={"trust_tier": "trusted"}, headers=H["security"])
    events = sse(call(client, H["user"], "message/stream", {"message": msg("x")}, path="/a2a/e56-sse").text)
    assert "result" in events[0] and events[-1]["error"]["message"] == "원격 이벤트가 JSON 이 아닙니다."


# ── C56-04 Task history — 최근 HISTORY_MAX 개만 저장 ──
def test_history_capped():
    hist = [{"kind": "message", "messageId": f"m{i}", "role": "agent", "parts": [{"kind": "text", "text": str(i)}]} for i in range(300)]
    a2a_gateway.record(AG, {"kind": "task", "id": "e56-hist", "contextId": "c", "status": {"state": "working"}, "history": hist})
    with SessionLocal() as db:
        row = db.get(A2ATask, f"{AG}:e56-hist")
        assert len(row.history) == a2a_gateway.HISTORY_MAX and row.history[-1]["messageId"] == "m299"
        db.delete(row)
        db.commit()


# ── C56-05 카드 URL — Host 헤더가 아니라 PUBLIC_BASE_URL (게이트웨이 프록시·플랫폼 카드) ──
def test_card_urls_ignore_host_header(client):
    evil = {"Host": "evil.e56.example"}
    checked = 0
    for path in (f"/a2a/{AG}/.well-known/agent-card.json", "/.well-known/agent-card.json", "/a2a/platform/tpl-qa/.well-known/agent-card.json"):
        res = client.get(path, headers=evil)
        if res.status_code == 404:
            continue
        assert res.status_code == 200 and "evil.e56.example" not in res.text and settings.public_base_url.rstrip("/") in res.text, path
        checked += 1
    assert checked >= 2
