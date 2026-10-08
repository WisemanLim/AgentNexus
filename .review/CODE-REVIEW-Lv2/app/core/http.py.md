> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md)

### 12. app/core/http.py
**파일 요약 / File Summary**
- 역할 / Role: 아웃바운드 HTTP 클라이언트 팩토리(`async_client`·`sync_client`) — W3C traceparent 자동 주입 + **egress 가드**(링크 로컬·클라우드 메타데이터 차단) + 시험용 origin 별 transport 마운트. in-process ASGI 앱을 스트리밍으로 호출하는 `StreamingASGITransport`, 동기 클라이언트용 `SyncASGITransport`.
- 진입점 거리 / Distance: 2 (`app/builtin/a2a_agent.py:22` 에서는 1)
- LOC: 225
- 주요 export: `register_mount`, `clear_mounts`, `EgressBlocked`, `blocked_url`, `async_client`, `sync_client`, `StreamingASGITransport`, `SyncASGITransport`, `_ASYNC_MOUNTS`(외부에서 직접 참조 — `app/services/mcp_gateway.py` 의 hosted-openapi 경로)
- 의존(내부): `app.core.config.settings`
- 외부 패키지: httpx, opentelemetry(propagate), 표준 `ipaddress`·`socket`
- 부수효과 / Side effects: 모듈 전역 마운트 레지스트리, DNS 판정 캐시 `_RESOLVED`(60초)
- 동시성 / Concurrency: asyncio(StreamingASGITransport 는 백그라운드 Task + Queue, 비동기 DNS 조회), 스레드↔루프 브리지(SyncASGITransport)
- 테스트 커버리지 흔적: `test/conftest.py:62-74`(모든 in-process 연결의 기반), `test/test_review_fixes.py`(`test_egress_guard`)
- 참고: 이 파일은 리뷰 도중(13:58) 동시 작업으로 egress 가드(L34-106)가 추가되었다 — 본 리뷰는 14:03 스냅샷 기준(INDEX §1.6).

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/http.py lines=1-31
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
```
**분석 / Analysis (Level 2)**
- L1: 이 모듈의 세 책임(추적 전파·메타데이터 차단·시험 마운트)을 한 줄로 요약.
- L12-13: origin(`scheme://host`) → transport 매핑. 시험이 `http://mcp-servers` 같은 가상 호스트를 실제 네트워크 없이 같은 프로세스 앱에 연결하는 장치.
- L16-26 `register_mount`: 접두에서 경로를 떼어 `scheme://host` 로 정규화(L20-22). httpx `mounts` 키 형식과 맞춘다.
- L29-31: 시험 정리용.
**짚어볼 점 / Watch-outs**
- 운영 코드 경로에서도 `mounts=dict(_ASYNC_MOUNTS) or None`(L126) 이 매번 평가된다 — 운영에서 누군가 `register_mount` 를 호출하면 실제 트래픽이 가로채진다. 그리고 마운트된 origin 은 egress 가드에서도 통과한다(L60-61). 시험 전용임을 강제하는 가드(`settings.app_env == "test"`)가 없다.

```python path=app/core/http.py lines=34-53
# ── egress 가드 — 사용자가 넣은 URL(OpenAPI·카드·푸시·커넥터)로 클라우드 메타데이터를 읽지 못하게 (SSRF).
# 사내 사설망 업스트림은 제품 기능이라 막지 않는다 — 링크 로컬(169.254/16·fe80::/10)과 IMDS 이름·주소만.
_BLOCKED_NETS = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10"),
                 ipaddress.ip_network("fd00:ec2::254/128"))
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
```
**분석 / Analysis (Level 2)**
- L34-35 설계 결정: 사내 사설망(10/8·192.168/16 등) 업스트림은 **제품 기능이라 허용**하고, 링크 로컬(169.254/16·fe80::/10)과 IMDS 이름·주소만 막는다 — 즉 "클라우드 자격 증명 탈취"만 막는 최소 SSRF 방어다.
- L36-38: 차단 대역·차단 호스트 이름(GCP `metadata.google.internal` 등). `fd00:ec2::254` 는 AWS IPv6 IMDS 주소.
- L39 `_RESOLVED`: host → (조회 시각, 차단 여부) 캐시.
- L42-43 `EgressBlocked(httpx.TransportError)`: httpx 전송 오류의 하위 클래스로 만들어, 기존 `except httpx.HTTPError` 처리(예: `app/services/mcp_gateway.py` 의 `_post`)가 그대로 "연결 실패" 로 처리하게 했다 — 호출부 수정 없이 끼워 넣는 영리한 선택.
- L46-53 `_blocked_ip`: IPv6 zone id(`%eth0`) 제거, IPv4-mapped IPv6(`::ffff:169.254.169.254`)를 IPv4 로 풀어 우회를 막는다.
**짚어볼 점 / Watch-outs**
- [위험:중간] 차단 범위가 의도적으로 좁다 — 내부 서비스(`http://valkey:6379`, `http://db:5432`, `http://api:8000/api/v1/...`, 사설 대역 관리 콘솔)로의 요청은 여전히 허용된다. 사용자가 URL 을 넣는 경로(A2A 푸시 relay URL, OpenAPI fetch URL, MCP endpoint)에서는 이 가드만으로 SSRF 가 해소되지 않는다(`app/services/a2a_gateway.py` 푸시 중계 리뷰 참조).
- `127.0.0.0/8`·`::1`(루프백)도 허용 — 게이트웨이 자신의 관리 API 를 내부에서 호출하게 만들 수 있다(인증 헤더는 공격자가 통제하지 못하므로 영향은 제한적).

```python path=app/core/http.py lines=56-106
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
```
**분석 / Analysis (Level 2)**
- L56-65 `_target`: host 정규화(대괄호·끝 점·소문자). 시험 마운트 origin 은 통과, 차단 이름·IP 면 차단, 캐시가 60초 이내면 그 판정, 아니면 `None`(DNS 조회 필요).
- L68-73 `_remember`: 해석된 **모든** 주소 중 하나라도 차단 대역이면 차단 — 여러 A 레코드 중 하나를 메타데이터로 섞는 우회를 막는다. 캐시가 4096 개를 넘으면 통째로 비운다(단순한 메모리 상한).
- L80-89 `blocked_url`: 동기 사전 점검 — 이 팩토리를 거치지 않는 클라이언트(예: hosted-openapi 의 httpx2)용.
- L92-106 `_guard_sync`·`_guard_async`: 요청 이벤트 훅에서 호출. 비동기 판은 `loop.getaddrinfo` 로 이벤트 루프를 막지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:중간 · TOCTOU] **DNS 재바인딩**: 가드는 훅 시점에 이름을 해석해 판정하고(L101), 실제 연결은 httpx 가 **다시** 해석한다. 공격자 도메인이 첫 조회엔 공인 IP, 두 번째엔 169.254.169.254 를 돌려주면(TTL 0) 우회된다. 또 판정을 60초 캐시(L65)하므로 그 사이 레코드가 바뀌어도 통과한다. 완전한 방어는 해석한 IP 로 직접 연결(커스텀 transport)하거나 연결 후 소켓의 peer 주소를 확인해야 한다.
- L69 해석 실패(빈 목록)는 `False`(차단 안 함)로 캐시된다 — 연결 단계에서 어차피 실패하므로 무해하다(주석이 근거를 명시).
- L85 동기 `socket.getaddrinfo` 는 `blocked_url` 을 비동기 코드에서 부르면 이벤트 루프를 막는다.
- 리다이렉트: httpx 는 기본 `follow_redirects=False` 라 대부분 안전하지만, 리다이렉트를 따르는 호출에서는 각 홉마다 훅이 다시 실행되는지 httpx 동작에 의존한다(요청 훅은 홉마다 호출됨).

```python path=app/core/http.py lines=109-132
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


def async_client(timeout: float | None = None, **kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout or settings.upstream_timeout, mounts=dict(_ASYNC_MOUNTS) or None,
                             event_hooks={"request": [_inject_async]}, **kw)


def sync_client(timeout: float | None = None, **kw) -> httpx.Client:
    return httpx.Client(timeout=timeout or settings.upstream_timeout, mounts=dict(_SYNC_MOUNTS) or None,
                        event_hooks={"request": [_inject_sync]}, **kw)
```
**분석 / Analysis (Level 2)**
- L109-122: 요청 훅 — 먼저 egress 가드, 그다음 현재 OTel 컨텍스트를 `traceparent` 로 주입(`setdefault` 라 호출자가 준 헤더는 유지).
- L125-127 `async_client`: 타임아웃 기본값(`UPSTREAM_TIMEOUT`), 마운트, 훅. `**kw` 로 `verify`·`cert`(mTLS) 등을 넘긴다.
- L130-132 `sync_client`: 동기 버전.
**짚어볼 점 / Watch-outs**
- L126 `timeout or settings.upstream_timeout` — `timeout=0` 을 주면 기본값으로 바뀐다. 실제 호출부는 양수만 쓴다.
- 팩토리는 매번 새 클라이언트(새 연결 풀)를 만든다 — 호출부가 `async with` 로 닫으므로 누수는 없지만 연결 재사용(keep-alive)이 없어 업스트림 호출마다 TCP/TLS 핸드셰이크와 DNS 조회(가드)가 생긴다(게이트웨이 지연의 한 요인).
- `event_hooks` 를 호출자가 `**kw` 로 넘기면 `TypeError`(중복 키워드) — 훅 확장이 필요하면 병합 로직이 필요하다.

```python path=app/core/http.py lines=135-166
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
```
**분석 / Analysis (Level 2)**
- L135-140: 왜 필요한가 — `httpx.ASGITransport` 는 앱이 응답을 끝낼 때까지 본문을 모아서 돌려준다. MCP sampling·elicitation 은 SSE 응답 **도중** 서버가 클라이언트에 요청을 보내고 답을 기다리므로, 모아서 주는 방식이면 서로 기다리며 교착한다. 그래서 청크를 도착 즉시 흘리는 전송을 직접 구현했다.
- L146-156: httpx Request → ASGI scope 변환(메서드·경로·쿼리·헤더·서버/클라이언트 주소).
- L157-166 `receive`: 요청 본문을 한 번 주고, 그 뒤에는 클라이언트가 스트림을 닫을 때(disconnected)까지 대기 후 `http.disconnect` — 앱이 연결 종료를 감지할 수 있게.
**짚어볼 점 / Watch-outs**
- L147 함수 안에서 `import asyncio` 를 다시 한다(모듈 상단 L2 에 이미 있음) — 불필요한 중복.

```python path=app/core/http.py lines=168-208
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
```
**분석 / Analysis (Level 2)**
- L168-180 `send`: 응답 시작(상태·헤더)을 기록하고 이벤트 set, 본문 청크는 큐에 넣고 `more_body=False` 면 종료 표시(None).
- L182-190 `run`: 앱 실행 — 시작 전 예외면 500 으로 응답 시작 처리, 어떤 경우든 마지막에 None.
- L192-193: 앱을 백그라운드 Task 로 돌리고 응답 시작까지만 기다린다.
- L195-206 `_Stream`: httpx 가 본문을 읽을 때 큐에서 꺼내 흘린다. `aclose` 는 disconnect 를 알리고 최대 5초 앱 종료를 기다린다.
- L208: 스트리밍 응답 반환.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L185-188 응답 **시작 후** 앱이 예외를 던지면 `start` 는 이미 있으니 아무 표시 없이 스트림이 끝난다 — 예외가 조용히 삼켜지고 클라이언트는 잘린 본문을 정상 종료로 받는다. 시험 전용이지만 디버깅을 어렵게 한다(최소 로그).
- L187 `error=exc` 를 `start` 에 저장하지만 어디서도 읽지 않는다(죽은 데이터).
- L206 `asyncio.wait({task}, timeout=5)` 후에도 끝나지 않은 Task 를 취소하지 않는다 — 이벤트 루프에 남는다.

```python path=app/core/http.py lines=211-225
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
```
**분석 / Analysis (Level 2)**
- L211-213: 동기 httpx 클라이언트(예: `auth._external_keys` 의 JWKS 조회)가 같은 프로세스의 ASGI 앱을 부를 때 쓰는 전송. 스레드풀 스레드가 앱의 이벤트 루프에 코루틴을 넘기고 결과를 기다린다(`run_coroutine_threadsafe(...).result(timeout=60)`).
- L215-216: 루프는 지연 조회(`loop_getter`) — 시험 fixture 가 `orchestrator.LOOP` 를 넘긴다(`test/conftest.py:74`).
**짚어볼 점 / Watch-outs**
- docstring 경고대로 **이벤트 루프 스레드에서 호출하면 교착**한다(루프가 자기 자신을 기다림). 비동기 코드에서 `sync_client` 를 부르는 경로가 생기면 시험이 60초 타임아웃으로 멈춘다.
- L222 요청마다 `httpx.ASGITransport` 를 새로 만든다 — 시험 전용이라 수용.

## 파일 평가 / File Assessment
- 강점: (1) 모든 아웃바운드 호출에 traceparent 를 일관 주입 — 분산 추적의 기반. (2) egress 가드를 `TransportError` 하위 클래스로 만들어 기존 오류 처리에 자연스럽게 합류, IPv4-mapped·다중 레코드 우회 고려. (3) SSE 역요청 교착 문제를 정확히 진단해 스트리밍 전송을 구현.
- 위험: (1) [중간] 가드 범위가 링크 로컬·IMDS 로 좁아 내부 서비스 SSRF 는 남는다. (2) [중간] 판정과 연결의 이중 DNS 해석(재바인딩 TOCTOU)·60초 판정 캐시. (3) 운영 코드에 시험용 마운트 훅이 상시 존재(마운트 origin 은 가드도 통과).
- 개선 권고(영향순): (1) 사용자 입력 URL 경로(푸시 relay·OpenAPI fetch)에 사설 대역·루프백 차단 옵션 적용. (2) 해석한 IP 로 직접 연결하는 transport 로 재바인딩 차단. (3) `register_mount` 를 `app_env == "test"` 에서만 허용.
- 재읽기 권고 레벨: 2 — 새로 들어온 egress 가드는 보안 경계라 줄 단위 재검토 가치가 있다.
