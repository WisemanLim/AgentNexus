"""아웃바운드 HTTP 클라이언트 팩토리 — W3C traceparent 전파 + 메타데이터 대역 차단 + (시험용) 호스트별 transport 마운트."""
import asyncio
import ipaddress
import socket
import time

import httpx
from opentelemetry import propagate

from app.core.config import settings

_ASYNC_MOUNTS: dict[str, httpx.AsyncBaseTransport] = {}
_SYNC_MOUNTS: dict[str, httpx.BaseTransport] = {}


def register_mount(prefix: str, async_transport: httpx.AsyncBaseTransport | None = None,
                   sync_transport: httpx.BaseTransport | None = None) -> None:
    """prefix 예: 'http://mcp-servers' — 해당 origin 요청을 주어진 transport 로 보낸다 (시험·in-process)."""
    key = prefix.rstrip("/")
    if "://" in key and not key.startswith("all://"):
        scheme, host = key.split("://", 1)
        key = f"{scheme}://{host}"
    if async_transport is not None:
        _ASYNC_MOUNTS[key] = async_transport
    if sync_transport is not None:
        _SYNC_MOUNTS[key] = sync_transport


def clear_mounts() -> None:
    _ASYNC_MOUNTS.clear()
    _SYNC_MOUNTS.clear()


# ── egress 가드 — 사용자가 넣은 URL(OpenAPI·카드·푸시·커넥터)로 클라우드 메타데이터를 읽지 못하게 (SSRF).
# 사내 사설망 업스트림은 제품 기능이라 막지 않는다 — 링크 로컬(169.254/16·fe80::/10)과 IMDS 이름·주소만.
_BLOCKED_NETS = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10"),
                 ipaddress.ip_network("fd00:ec2::254/128"), ipaddress.ip_network("100.100.100.200/32"))  # 마지막: Alibaba Cloud IMDS (D19-④)
_BLOCKED_HOSTS = {"metadata", "metadata.google.internal", "metadata.goog"}
_RESOLVED: dict[str, tuple[float, bool]] = {}  # host → (시각, 차단 여부) 60초


class EgressBlocked(httpx.TransportError):
    """메타데이터·링크 로컬 대상으로의 아웃바운드 요청."""


def _blocked_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _BLOCKED_NETS if net.version == ip.version)


def _target(url: httpx.URL) -> tuple[str, bool | None]:
    """(정규화한 host, 판정) — 판정이 None 이면 이름 해석이 필요하다. 시험 마운트는 네트워크를 타지 않으므로 통과."""
    host = url.host.strip("[]").rstrip(".").lower()
    origin = f"{url.scheme}://{url.netloc.decode()}"
    if origin in _ASYNC_MOUNTS or origin in _SYNC_MOUNTS:
        return host, False
    if host in _BLOCKED_HOSTS or _blocked_ip(host):
        return host, True
    hit = _RESOLVED.get(host)
    return host, (hit[1] if hit and time.monotonic() - hit[0] < 60 else None)


def _remember(host: str, infos: list) -> bool:
    blocked = any(_blocked_ip(info[4][0]) for info in infos)  # 해석 실패(빈 목록)는 연결 단계에서 그대로 실패한다
    if len(_RESOLVED) > 4096:
        _RESOLVED.clear()
    _RESOLVED[host] = (time.monotonic(), blocked)
    return blocked


def _blocked_error(request: httpx.Request) -> EgressBlocked:
    return EgressBlocked(f"메타데이터·링크 로컬 주소로는 요청할 수 없습니다: {request.url.host}", request=request)


def blocked_url(url: str | httpx.URL) -> bool:
    """메타데이터·링크 로컬 대상이면 True (시험 마운트 제외) — 이 팩토리를 거치지 않는 클라이언트의 사전 점검용."""
    host, blocked = _target(httpx.URL(url))
    if blocked is None:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            infos = []
        blocked = _remember(host, infos)
    return blocked


def _internal_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast


def internal_url(url: str) -> bool:
    """사설·루프백·링크 로컬·예약 대상이면 True (시험 마운트 제외) — 클라이언트가 넣는 콜백(푸시) URL 검사용 (D17-④).
    업스트림 등록과 달리 콜백은 공인 수신처여야 한다 — 게이트웨이를 내부망 요청 발판으로 쓰지 못하게."""
    try:
        u = httpx.URL(url)
    except (httpx.InvalidURL, TypeError):
        return True
    if u.scheme not in ("http", "https") or not u.host:
        return True
    host = u.host.strip("[]").rstrip(".").lower()
    origin = f"{u.scheme}://{u.netloc.decode()}"
    if origin in _ASYNC_MOUNTS or origin in _SYNC_MOUNTS:
        return False
    if host in ("localhost",) or host in _BLOCKED_HOSTS or _internal_ip(host):
        return True
    try:
        return any(_internal_ip(info[4][0]) for info in socket.getaddrinfo(host, None))
    except OSError:
        return False  # 해석 안 되는 이름은 전송 단계에서 실패한다


def _guard_sync(request: httpx.Request) -> None:
    if blocked_url(request.url):
        raise _blocked_error(request)


async def _guard_async(request: httpx.Request) -> None:
    host, blocked = _target(request.url)
    if blocked is None:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        except OSError:
            infos = []
        blocked = _remember(host, infos)
    if blocked:
        raise _blocked_error(request)


async def _inject_async(request: httpx.Request) -> None:
    await _guard_async(request)
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    for k, v in carrier.items():
        request.headers.setdefault(k, v)


def _inject_sync(request: httpx.Request) -> None:
    _guard_sync(request)
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    for k, v in carrier.items():
        request.headers.setdefault(k, v)


async def post_with_retry(url: str, attempts: int = 3, base_delay: float = 0.5, timeout: float = 5, **kw) -> bool:
    """웹훅(푸시) 전달 — 연결 오류·5xx·429 는 지수 백오프(0.5·1초 …)로 다시 보낸다. 다른 4xx·차단 대상은 바로 포기 (D19-②)."""
    for i in range(attempts):
        try:
            async with async_client(timeout=timeout) as http:
                res = await http.post(url, **kw)
            if res.status_code < 500 and res.status_code != 429:
                return res.status_code < 400
        except EgressBlocked:
            return False
        except httpx.HTTPError:
            pass
        if i < attempts - 1:
            await asyncio.sleep(base_delay * 2 ** i)
    return False


async def read_capped(request, limit: int) -> bytes | None:
    """수신 본문을 상한까지만 — Content-Length 가 넘거나 받는 중 넘으면 None (본문을 다 읽기 전에 끊는다, 59th)."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            return None
    return bytes(buf)


def async_client(timeout: float | None = None, **kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout or settings.upstream_timeout, mounts=dict(_ASYNC_MOUNTS) or None,
                             event_hooks={"request": [_inject_async]}, **kw)


def sync_client(timeout: float | None = None, **kw) -> httpx.Client:
    return httpx.Client(timeout=timeout or settings.upstream_timeout, mounts=dict(_SYNC_MOUNTS) or None,
                        event_hooks={"request": [_inject_sync]}, **kw)


class StreamingASGITransport(httpx.AsyncBaseTransport):
    """응답 본문을 도착하는 대로 흘리는 in-process ASGI 전송.

    httpx.ASGITransport 는 앱이 응답을 끝낼 때까지 모은 뒤 돌려주므로, SSE 응답 도중 서버가 클라이언트에게 요청을 보내고
    답을 기다리는 흐름(MCP sampling·elicitation)이 교착된다. 시험·in-process 마운트에서 이 전송을 쓴다.
    """

    def __init__(self, app, client: tuple[str, int] = ("127.0.0.1", 50000)) -> None:
        self.app = app
        self.client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import asyncio
        body = await request.aread()
        url = request.url
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": request.method,
            "scheme": url.scheme, "path": url.path, "raw_path": url.raw_path.split(b"?")[0],
            "query_string": url.query, "root_path": "", "client": self.client,
            "server": (url.host, url.port or (443 if url.scheme == "https" else 80)),
            "headers": [(k.lower(), v) for k, v in request.headers.raw],
        }
        disconnected = asyncio.Event()
        sent_body = False

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        chunks: asyncio.Queue = asyncio.Queue()
        start: dict = {}
        started = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.start":
                start.update(status=message["status"], headers=message.get("headers", []))
                started.set()
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    await chunks.put(message["body"])
                if not message.get("more_body", False):
                    await chunks.put(None)

        async def run():
            try:
                await self.app(scope, receive, send)
            except Exception as exc:  # noqa: BLE001 — 시작 전 오류는 500 으로
                if not started.is_set():
                    start.update(status=500, headers=[], error=exc)
                    started.set()
            finally:
                await chunks.put(None)

        task = asyncio.create_task(run())
        await started.wait()

        class _Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                while True:
                    chunk = await chunks.get()
                    if chunk is None:
                        break
                    yield chunk

            async def aclose(self):
                disconnected.set()
                if not task.done():
                    await asyncio.wait({task}, timeout=5)

        return httpx.Response(start["status"], headers=start["headers"], stream=_Stream(), request=request)


class SyncASGITransport(httpx.BaseTransport):
    """동기 클라이언트 → 같은 프로세스 ASGI 앱 (시험용). 호출 스레드(스레드풀)가 앱 이벤트 루프에 요청을 넘기고 기다린다 —
    루프 스레드에서 부르면 교착되므로 동기 엔드포인트(스레드풀)에서만 쓴다."""

    def __init__(self, app, loop_getter):
        self.app, self.loop_getter = app, loop_getter

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        req = httpx.Request(request.method, request.url, headers=request.headers, content=request.read())

        async def call():
            res = await httpx.ASGITransport(app=self.app).handle_async_request(req)
            return res.status_code, res.headers, await res.aread()
        status, headers, body = asyncio.run_coroutine_threadsafe(call(), self.loop_getter()).result(timeout=60)
        return httpx.Response(status, headers=headers, content=body, request=request)
