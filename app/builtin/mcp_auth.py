"""MCP 서버 측 인가 (FR-I03 · MCP Authorization: OAuth 2.1 Resource Server, RFC 9728 Protected Resource Metadata).

- 토큰 없음/무효 → 401 + `WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource/<path>"`
- 토큰 = AgentNexus 게이트웨이가 RFC 8693 으로 교환해 준 대상 전용 토큰(aud=mcp:<name>) — JWKS 로 서명 검증
- tools/call 은 도구별 필요 스코프(scope_map)를 검사(최소 권한). 부족하면 403 insufficient_scope.
"""
import json
import time

import jwt

from app.core import security
from app.core.config import settings
from app.core.http import async_client

_JWKS: tuple[float, dict] | None = None


async def _jwks() -> dict:
    global _JWKS
    if _JWKS and time.time() - _JWKS[0] < 300:
        return _JWKS[1]
    async with async_client(timeout=5) as http:
        res = await http.get(settings.idp_internal_url.rstrip("/") + "/idp/jwks")
        res.raise_for_status()
        _JWKS = (time.time(), res.json())
    return _JWKS[1]


async def verify(token: str, audience: str) -> dict:
    header = jwt.get_unverified_header(token)
    for refresh in (False, True):
        if refresh:
            global _JWKS
            _JWKS = None
        for k in (await _jwks()).get("keys", []):
            if k.get("kid") == header.get("kid"):
                return jwt.decode(token, jwt.PyJWK(k).key, algorithms=["RS256"], audience=audience,
                                  issuer=security.issuer(), leeway=5)
    raise jwt.InvalidTokenError("unknown signing key")


def prm(resource: str, scopes: list[str]) -> dict:
    return {"resource": resource, "authorization_servers": [security.issuer()], "scopes_supported": sorted(set(scopes)),
            "bearer_methods_supported": ["header"], "resource_name": resource.rsplit("/", 2)[-2]}


class ResourceServerAuth:
    """ASGI 미들웨어 — FastMCP http_app 을 감싼다."""

    def __init__(self, app, name: str, scope_map: dict[str, str], public_base: str):
        self.app = app
        self.name = name
        self.audience = f"mcp:{name}"
        self.scope_map = scope_map
        self.resource = f"{public_base.rstrip('/')}/{name}/mcp"
        self.prm_url = f"{public_base.rstrip('/')}/.well-known/oauth-protected-resource/{name}/mcp"

    async def _reply(self, send, status: int, body: dict, extra_headers: list | None = None):
        raw = json.dumps(body, ensure_ascii=False).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
        await send({"type": "http.response.start", "status": status, "headers": headers + (extra_headers or [])})
        await send({"type": "http.response.body", "body": raw})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        challenge = f'Bearer resource_metadata="{self.prm_url}"'
        if not auth.lower().startswith("bearer "):
            return await self._reply(send, 401, {"error": "unauthorized"},
                                     [(b"www-authenticate", challenge.encode())])
        try:
            claims = await verify(auth[7:].strip(), self.audience)
        except Exception as exc:  # noqa: BLE001
            return await self._reply(send, 401, {"error": "invalid_token", "error_description": str(exc)},
                                     [(b"www-authenticate", f'{challenge}, error="invalid_token"'.encode())])
        chunks, more = [], True
        while more:
            msg = await receive()
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        body = b"".join(chunks)
        granted = set((claims.get("scope") or "").split())
        try:
            rpc = json.loads(body) if body else {}
        except ValueError:
            rpc = {}
        if isinstance(rpc, dict) and rpc.get("method") == "tools/call":
            tool = (rpc.get("params") or {}).get("name", "")
            need = self.scope_map.get(tool)
            if need and need not in granted:
                return await self._reply(send, 403, {"error": "insufficient_scope", "scope": need},
                                         [(b"www-authenticate",
                                           f'{challenge}, error="insufficient_scope", scope="{need}"'.encode())])
        scope.setdefault("state", {})["anx_claims"] = claims
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        return await self.app(scope, replay, send)
