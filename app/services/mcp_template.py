"""Custom MCP Server 템플릿 (FR-S03, SCR-F05) — FastMCP 기반 독립 프로젝트 파일 세트를 생성한다.

포함: server.py(도구·리소스·프롬프트 예시) · anx_auth.py(OAuth 2.1 리소스 서버, RFC 9728 PRM, JWKS 검증, 도구별 스코프)
· anx_register.py(기동 시 레지스트리 자동 등록, FR-S00) · telemetry.py(OTel) · server.json(레지스트리 게시용 자동 생성)
· pyproject.toml · Dockerfile · README.md.
CLI: `nexus mcp scaffold <name>` 가 이 결과를 디스크에 쓴다.
"""
import json
import re

from app.core.ids import slugify

_SERVER = '''"""{title} — AgentNexus Custom MCP Server (FastMCP, Streamable HTTP)."""
import asyncio
import contextlib
import os

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from anx_auth import ResourceServerAuth, protected_resource_metadata
from anx_register import register_on_startup
from telemetry import setup_tracing

NAME = "{name}"
mcp = FastMCP(NAME, instructions="{description}")

# 도구별 필요 스코프 (게이트웨이가 RFC 8693 으로 이 스코프만 담은 토큰을 교환해 준다)
TOOL_SCOPES = {{"echo": "{name}:read", "create_note": "{name}:write"}}
_NOTES: list[dict] = []


@mcp.tool(annotations={{"readOnlyHint": True}})
def echo(text: str) -> str:
    """입력을 그대로 돌려준다 (읽기)."""
    return text


@mcp.tool(annotations={{"readOnlyHint": False, "destructiveHint": False}})
def create_note(title: str, body: str = "") -> dict:
    """메모를 만든다 (쓰기 — 게이트웨이 정책상 사람 승인 대상)."""
    note = {{"id": len(_NOTES) + 1, "title": title, "body": body}}
    _NOTES.append(note)
    return note


@mcp.resource("{name}://notes")
def notes() -> str:
    """메모 목록 리소스."""
    return "\\n".join(f"- {{n['title']}}" for n in _NOTES) or "(없음)"


@mcp.prompt
def summarize_notes() -> str:
    """메모 요약 프롬프트."""
    return "다음 메모들을 3줄로 요약하라."


_mcp_app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8080")


async def _prm(_):
    return JSONResponse(protected_resource_metadata(f"{{PUBLIC_URL}}/{{NAME}}/mcp", sorted(set(TOOL_SCOPES.values()))))


async def _health(_):
    return JSONResponse({{"status": "ok"}})


@contextlib.asynccontextmanager
async def lifespan(app):
    async with _mcp_app.lifespan(app):
        # 기동 시 레지스트리 자동 등록 (AGENTNEXUS_URL·AGENTNEXUS_TOKEN 이 있을 때) → 운영자 승인 → 카탈로그
        task = asyncio.create_task(register_on_startup())
        yield
        task.cancel()


app = Starlette(routes=[Route("/healthz", _health),
                        Route(f"/.well-known/oauth-protected-resource/{{NAME}}/mcp", _prm),
                        Mount(f"/{{NAME}}", app=ResourceServerAuth(_mcp_app, NAME, TOOL_SCOPES, PUBLIC_URL))],
                lifespan=lifespan)
setup_tracing(app, NAME)
'''

_AUTH = '''"""OAuth 2.1 리소스 서버 미들웨어 — AgentNexus 게이트웨이가 교환한 토큰(aud=mcp:<name>)을 JWKS 로 검증 (MCP Authorization)."""
import json
import os
import time

import httpx
import jwt

ISSUER = os.environ.get("AGENTNEXUS_ISSUER", "http://localhost:8800/idp")
JWKS_URL = os.environ.get("AGENTNEXUS_JWKS_URL", ISSUER.rstrip("/") + "/jwks")
_cache: dict = {{}}


def protected_resource_metadata(resource: str, scopes: list[str]) -> dict:
    return {{"resource": resource, "authorization_servers": [ISSUER], "scopes_supported": scopes,
            "bearer_methods_supported": ["header"]}}


async def _keys() -> dict:
    if _cache.get("at", 0) > time.time() - 300:
        return _cache["keys"]
    async with httpx.AsyncClient(timeout=5) as http:
        _cache.update(keys=(await http.get(JWKS_URL)).json(), at=time.time())
    return _cache["keys"]


async def verify(token: str, audience: str) -> dict:
    kid = jwt.get_unverified_header(token).get("kid")
    for k in (await _keys()).get("keys", []):
        if k.get("kid") == kid:
            return jwt.decode(token, jwt.PyJWK(k).key, algorithms=["RS256"], audience=audience, issuer=ISSUER)
    raise jwt.InvalidTokenError("unknown kid")


class ResourceServerAuth:
    def __init__(self, app, name: str, scope_map: dict, public_url: str):
        self.app, self.audience, self.scope_map = app, f"mcp:{{name}}", scope_map
        self.prm = f"{{public_url}}/.well-known/oauth-protected-resource/{{name}}/mcp"

    async def _deny(self, send, status, error, extra=""):
        body = json.dumps({{"error": error}}).encode()
        hdr = f'Bearer resource_metadata="{{self.prm}}"{{extra}}'.encode()
        await send({{"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", hdr)]}})
        await send({{"type": "http.response.body", "body": body}})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        auth = dict(scope["headers"]).get(b"authorization", b"").decode()
        if not auth.lower().startswith("bearer "):
            return await self._deny(send, 401, "unauthorized")
        try:
            claims = await verify(auth[7:], self.audience)
        except Exception:
            return await self._deny(send, 401, "invalid_token", ', error="invalid_token"')
        body, more = b"", True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
        try:
            rpc = json.loads(body or b"{{}}")
        except ValueError:
            rpc = {{}}
        if isinstance(rpc, dict) and rpc.get("method") == "tools/call":
            need = self.scope_map.get((rpc.get("params") or {{}}).get("name", ""))
            if need and need not in (claims.get("scope") or "").split():
                return await self._deny(send, 403, "insufficient_scope", f', error="insufficient_scope", scope="{{need}}"')
        scope.setdefault("state", {{}})["anx_claims"] = claims
        done = False

        async def replay():
            nonlocal done
            if not done:
                done = True
                return {{"type": "http.request", "body": body, "more_body": False}}
            return await receive()
        return await self.app(scope, replay, send)
'''

_TELEMETRY = '''"""OpenTelemetry — OTEL_EXPORTER_OTLP_ENDPOINT 가 있으면 OTLP 로 스팬 전송 (게이트웨이 traceparent 를 이어 받는다)."""
import os


def setup_tracing(app, service: str) -> None:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    provider = TracerProvider(resource=Resource.create({{"service.name": service}}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")))
    trace.set_tracer_provider(provider)
'''

_PYPROJECT = '''[project]
name = "{name}-mcp"
version = "0.1.0"
description = "{description}"
requires-python = ">=3.12"
dependencies = ["fastmcp>=4", "uvicorn[standard]>=0.30", "httpx>=0.27", "pyjwt[crypto]>=2.9",
                "opentelemetry-sdk>=1.27", "opentelemetry-exporter-otlp-proto-http>=1.27"]

[tool.uv]
package = false
'''

_DOCKERFILE = '''FROM python:3.12-slim
RUN pip install --no-cache-dir uv
WORKDIR /srv
COPY pyproject.toml ./
RUN uv sync --no-install-project
COPY . .
ENV PATH=/srv/.venv/bin:$PATH
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
'''

_REGISTER = '''"""기동 시 레지스트리 자동 등록 (AgentNexus FR-S00) — server.json 을 /v0/publish 로 게시한다.

AGENTNEXUS_URL(예 http://agentnexus:8000)·AGENTNEXUS_TOKEN(registry:publish 권한 PAT)이 없으면 아무것도 하지 않는다.
같은 버전·같은 내용 재게시는 멱등이고, 내용이 바뀌었는데 버전이 같으면 409 — server.json 의 version 을 올린다.
게시 후에는 운영자 승인(SCR-E05)을 거쳐 카탈로그에 노출된다.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger("anx.register")


async def register(base: str, token: str, path: str = "server.json", transport=None) -> str:
    doc = json.loads(Path(path).read_text())
    async with httpx.AsyncClient(timeout=10, transport=transport) as http:
        res = await http.post(base.rstrip("/") + "/v0/publish", json=doc, headers={{"Authorization": f"Bearer {{token}}"}})
    if res.status_code in (200, 201):
        state = res.json().get("status") or res.json().get("version_status") or "published"
        log.info("registry: %s@%s %s", doc["name"], doc["version"], state)
        return "published"
    if res.status_code == 409:
        log.warning("registry: %s@%s 내용이 바뀌었습니다 — version 을 올리세요.", doc["name"], doc["version"])
        return "conflict"
    raise RuntimeError(f"registry publish {{res.status_code}}: {{res.text[:200]}}")


async def register_on_startup(retries: int = 10, delay: float = 3.0) -> str | None:
    base, token = os.environ.get("AGENTNEXUS_URL"), os.environ.get("AGENTNEXUS_TOKEN")
    if not base or not token:
        return None
    for _ in range(retries):  # 게이트웨이가 아직 안 떴을 수 있다
        try:
            return await register(base, token)
        except (httpx.HTTPError, RuntimeError) as exc:
            log.warning("registry 등록 재시도: %s", exc)
            await asyncio.sleep(delay)
    return None
'''

_README = '''# {title}

AgentNexus Custom MCP Server 템플릿 (FastMCP · Streamable HTTP · OAuth 2.1 리소스 서버).

```bash
uv sync && AGENTNEXUS_ISSUER=http://<agentnexus>/idp PUBLIC_URL=http://<this-host>:8080 uv run uvicorn server:app --port 8080
nexus registry publish server.json      # 레지스트리 게시 → 운영자 승인 → 카탈로그 등록(즉시 사용)
# 또는 기동 시 자동 등록: AGENTNEXUS_URL=http://<agentnexus> AGENTNEXUS_TOKEN=<registry:publish PAT> 를 함께 준다
```

- 엔드포인트: `/{name}/mcp` · PRM: `/.well-known/oauth-protected-resource/{name}/mcp`
- 게이트웨이 `/mcp/{name}` 뒤에서만 접근하도록 네트워크를 구성한다(우회 차단).
- 도구별 스코프는 `server.py` 의 `TOOL_SCOPES` 와 `server.json` 의 `_meta.auth.tool_scopes` 를 일치시킨다.
'''


OPTIONAL = ("docker", "register", "readme")  # SCR-F05 ③ 포함 선택 — 인증(anx_auth)·관측(telemetry)은 기본 포함(해제 불가)


def render(name: str, title: str | None = None, description: str | None = None, public_url: str | None = None,
           version: str = "0.1.0", include: list[str] | None = None) -> dict[str, str]:
    ns = slugify(name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", ns):
        raise ValueError("name 은 영문 소문자·숫자·하이픈")
    title = title or f"{ns} MCP Server"
    description = (description or f"{title} (custom)")[:100].replace('"', "'")
    url = (public_url or f"http://{ns}:8080").rstrip("/")
    scopes = {"echo": f"{ns}:read", "create_note": f"{ns}:write"}
    server_json = {"$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
                   "name": f"io.agentnexus.custom/{ns}", "title": title[:100], "description": description,
                   "version": version, "remotes": [{"type": "streamable-http", "url": f"{url}/{ns}/mcp"}],
                   "_meta": {"auth": {"type": "oauth2", "tool_scopes": scopes}, "tools": sorted(scopes),
                             "license": "MIT", "template": "agentnexus-fastmcp@1"}}
    fmt = {"name": ns, "title": title, "description": description}
    inc = set(OPTIONAL if include is None else include)
    files = {"server.py": _SERVER.format(**fmt), "anx_auth.py": _AUTH.format(), "telemetry.py": _TELEMETRY.format(),
             "pyproject.toml": _PYPROJECT.format(**fmt),
             "server.json": json.dumps(server_json, indent=2, ensure_ascii=False) + "\n"}
    if "register" in inc:
        files["anx_register.py"] = _REGISTER.format()
    if "docker" in inc:
        files["Dockerfile"] = _DOCKERFILE
    if "readme" in inc:
        files["README.md"] = _README.format(**fmt)
    return files
