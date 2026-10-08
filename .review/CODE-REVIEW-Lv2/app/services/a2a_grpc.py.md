> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 25. app/services/a2a_grpc.py
**파일 요약 / File Summary**
- 역할 / Role: A2A 1.0 gRPC 바인딩(정규 `lf.a2a.v1.A2AService`, a2a-sdk 생성 스텁). (1) 수신: `GatewayServicer` 가 RPC 를 ProtoJSON 으로 바꿔 게이트웨이 HTTP+JSON 바인딩으로 위임(같은 인증·정책·감사), 오류는 `grpc-status-details-bin` 트레일러. (2) 원격: gRPC 전용 에이전트를 같은 스텁으로 호출(`call`·`stream`).
- 진입점 거리 / Distance: 1 (`app/grpc_server.py:10`)
- LOC: 254
- 주요 export: `OPS`, `call`, `stream`, `GatewayServicer`, `serve`, `to_dict`, `from_dict`
- 의존(내부): `app.core.{config,http}`, `app.services.{a2a_proto,a2a_rest}`, (지연) `app.services.a2a_gateway.A2AError`(순환 import 회피)
- 외부 패키지: grpcio(`grpc.aio`), a2a-sdk(`a2a.types.a2a_pb2`·`a2a_pb2_grpc`), protobuf(`json_format`·`any_pb2`), googleapis-common-protos(`google.rpc`)
- 부수효과 / Side effects: gRPC 서버 포트 바인딩, 게이트웨이·원격 에이전트 네트워크 호출
- 동시성 / Concurrency: asyncio(`grpc.aio`), async generator 스트림
- 테스트 커버리지 흔적: `test/test_a2a_grpc.py`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/a2a_grpc.py lines=1-38
"""A2A gRPC 바인딩 (A2A 1.0 §10, D16-②, Epic 37) — 정규 `lf.a2a.v1.A2AService` (a2a-sdk 가 싣는 생성 스텁).

- 수신: compose 서비스 `a2a-grpc`(`python -m app.grpc_server`)의 `GatewayServicer` 가 RPC 를 ProtoJSON 으로 바꿔
  게이트웨이 HTTP+JSON 바인딩으로 보낸다 — 인증(metadata `authorization`)·정책·가드·감사·Task 영속은 게이트웨이 한 곳.
  대상은 요청의 `tenant`(= 카드 AgentInterface.tenant): `platform` · `platform/{profile}` · 그 밖은 원격 에이전트 id.
  오류는 google.rpc.Status(+ErrorInfo) 를 `grpc-status-details-bin` 트레일러로.
- 원격: 카드에 GRPC 인터페이스만 있으면 게이트웨이가 같은 스텁으로 호출한다(`call`·`stream`, URL `host:port[#tenant]`).
"""
import json
import re
from urllib.parse import quote

import grpc
from a2a.types import a2a_pb2 as pb
from a2a.types import a2a_pb2_grpc as pbg
from google.protobuf import any_pb2, json_format
from google.rpc import error_details_pb2, status_pb2

from app.core.config import settings
from app.core.http import async_client
from app.services import a2a_proto as ap
from app.services import a2a_rest

OPS = {  # 정준 작업 → (RPC, 요청 메시지, 서버 스트림)
    "send": ("SendMessage", pb.SendMessageRequest, False),
    "stream": ("SendStreamingMessage", pb.SendMessageRequest, True),
    "get": ("GetTask", pb.GetTaskRequest, False),
    "list": ("ListTasks", pb.ListTasksRequest, False),
    "cancel": ("CancelTask", pb.CancelTaskRequest, False),
    "resubscribe": ("SubscribeToTask", pb.SubscribeToTaskRequest, True),
    "push_set": ("CreateTaskPushNotificationConfig", pb.TaskPushNotificationConfig, False),
    "push_get": ("GetTaskPushNotificationConfig", pb.GetTaskPushNotificationConfigRequest, False),
    "card": ("GetExtendedAgentCard", pb.GetExtendedAgentCardRequest, False),
}
DETAILS_KEY = "grpc-status-details-bin"
_TENANT = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?$")
_FORWARD = ("authorization", "a2a-version", "a2a-extensions", "x-agentnexus-agent", "x-agentnexus-data-class",
            "x-agentnexus-session", "x-agentnexus-run", "x-agentnexus-step")
```
**분석 / Analysis (Level 2)**
- L1-8: 두 방향 역할. 수신 대상 결정 규칙 — 요청의 `tenant`(카드 AgentInterface.tenant): `platform`·`platform/{profile}`·그 밖은 원격 에이전트 id.
- L24-34 `OPS`: 정준 작업 → (RPC 이름, 요청 메시지 클래스, 서버 스트림 여부).
- L35: 상세 오류 트레일러 키(gRPC 표준).
- L36 `_TENANT`: tenant 허용 형식(영숫자·`._-`, 최대 한 단계 `/`) — 경로 주입 방지용 화이트리스트.
- L37-38 `_FORWARD`: 게이트웨이로 전달할 metadata(인증·버전·확장·추적 헤더)만.

```python path=app/services/a2a_grpc.py lines=41-58
def to_dict(msg) -> dict:
    return json_format.MessageToDict(msg, preserving_proto_field_name=False)


def from_dict(data: dict, cls):
    return json_format.ParseDict(data or {}, cls(), ignore_unknown_fields=True)


# ───────────────────────── 원격 호출 (게이트웨이 → gRPC 전용 에이전트) ─────────────────────────
def _target(url: str) -> tuple[str, str, bool]:
    """`grpc[s]://host:port#tenant` | `host:port` → (주소, tenant, TLS)."""
    tls = url.startswith("grpcs://")
    addr, _, tenant = url.split("://", 1)[-1].partition("#")
    return addr.rstrip("/"), tenant, tls


def _channel(addr: str, tls: bool) -> grpc.aio.Channel:
    return grpc.aio.secure_channel(addr, grpc.ssl_channel_credentials()) if tls else grpc.aio.insecure_channel(addr)
```
**분석 / Analysis (Level 2)**
- L41-46: protobuf ↔ dict(ProtoJSON — camelCase 필드명, 알 수 없는 필드 무시).
- L50-54 `_target`: `grpc[s]://host:port#tenant` 또는 `host:port` → (주소, tenant, TLS 여부).
- L57-58 `_channel`: TLS 면 시스템 루트 인증서로 secure channel, 아니면 **insecure channel**.
**짚어볼 점 / Watch-outs**
- [위험:중간] L52-58 스킴이 `grpcs://` 가 아니면 평문 채널이다 — 카드가 `host:port` 만 광고하면(이 프로젝트의 자기 카드도 `A2A_GRPC_PUBLIC_URL=127.0.0.1:8851` 형식, `.env.example`) 게이트웨이가 원격 에이전트로 보내는 교환 토큰(`authorization` metadata)이 평문으로 전송된다. 원격 호출은 기본 TLS, 평문은 명시 허용일 때만이 안전하다.
- L50-54 주소는 원격 카드(신뢰하지 않는 입력)에서 온다 — gRPC 채널은 httpx egress 가드(`app/core/http.py`)를 거치지 않으므로 링크 로컬·메타데이터 주소 차단도 적용되지 않는다(SSRF 경로). 등록은 승인 대상이지만 카드 재조회(`refresh_card`)로 주소가 바뀔 수 있다.

```python path=app/services/a2a_grpc.py lines=61-87
def _error_of(exc: grpc.aio.AioRpcError) -> tuple[int, str]:
    """gRPC 오류 → (A2A/JSON-RPC 코드, 메시지) — ErrorInfo reason 우선, 없으면 상태 코드로."""
    for k, v in exc.trailing_metadata() or ():
        if k == DETAILS_KEY:
            st = status_pb2.Status.FromString(v)
            for d in st.details:
                info = error_details_pb2.ErrorInfo()
                if d.Unpack(info) and info.reason in a2a_rest.REASON_CODE:
                    return a2a_rest.REASON_CODE[info.reason], st.message or exc.details()
    code = exc.code()
    return ({grpc.StatusCode.NOT_FOUND: ap.TASK_NOT_FOUND, grpc.StatusCode.INVALID_ARGUMENT: -32602,
             grpc.StatusCode.UNIMPLEMENTED: ap.UNSUPPORTED_OP}.get(code, -32603), exc.details() or code.name)


def _request(op: str, params: dict, tenant: str):
    if op not in OPS:
        raise ValueError(op)
    rpc, cls, streaming = OPS[op]
    req = from_dict(params, cls)
    if tenant and hasattr(req, "tenant"):
        req.tenant = tenant
    return rpc, req, streaming


def _metadata(headers: dict) -> list[tuple[str, str]]:
    md = [(k.lower(), str(v)) for k, v in (headers or {}).items() if k.lower() in _FORWARD]
    return md + ([("a2a-version", ap.V10)] if not any(k == "a2a-version" for k, _ in md) else [])
```
**분석 / Analysis (Level 2)**
- L61-72 `_error_of`: gRPC 오류 → A2A 코드 — 상세 트레일러의 `ErrorInfo.reason` 우선, 없으면 상태 코드 매핑.
- L75-82 `_request`: 작업 → 요청 메시지(ParseDict), tenant 필드가 있으면 설정. 모르는 작업은 ValueError.
- L85-87 `_metadata`: 전달 허용 헤더만 소문자 키로, `a2a-version` 없으면 1.0 추가.
**짚어볼 점 / Watch-outs**
- L66 `d.Unpack(info)` — Any 타입이 ErrorInfo 가 아니면 False(안전).
- L86 `_FORWARD` 화이트리스트로 임의 헤더(예: 내부 `X-AgentNexus-Binding`) 전달을 막는다 — 좋은 최소 전달 원칙.

```python path=app/services/a2a_grpc.py lines=90-120
async def call(url: str, op: str, params: dict, headers: dict) -> dict:
    from app.services.a2a_gateway import A2AError
    addr, tenant, tls = _target(url)
    try:
        rpc, req, _ = _request(op, params, tenant)
    except ValueError:
        raise A2AError(ap.UNSUPPORTED_OP, f"gRPC 바인딩에서 지원하지 않는 작업: {op}") from None
    async with _channel(addr, tls) as ch:
        try:
            resp = await getattr(pbg.A2AServiceStub(ch), rpc)(req, metadata=_metadata(headers), timeout=settings.upstream_timeout)
        except grpc.aio.AioRpcError as exc:
            if exc.code() in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED):
                raise A2AError(-32003, f"원격 에이전트 인증 실패 ({exc.code().name})", http_status=502) from exc
            if exc.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                raise A2AError(-32603, f"원격 에이전트 연결 실패: {exc.code().name}", http_status=502) from exc
            code, msg = _error_of(exc)
            raise A2AError(code, msg, http_status=200 if code in a2a_rest.A2A_ERRORS else 502) from exc
    return to_dict(resp)


async def stream(url: str, op: str, params: dict, headers: dict):
    """서버 스트림 → JSON-RPC 형태 메시지({result} | {error}) — 게이트웨이 _stream 이 정준형으로 바꾼다."""
    addr, tenant, tls = _target(url)
    rpc, req, _ = _request(op, params, tenant)
    async with _channel(addr, tls) as ch:
        try:
            async for ev in getattr(pbg.A2AServiceStub(ch), rpc)(req, metadata=_metadata(headers), timeout=settings.upstream_timeout):
                yield {"result": to_dict(ev)}
        except grpc.aio.AioRpcError as exc:
            code, msg = _error_of(exc)
            yield {"error": {"code": code, "message": msg}}
```
**분석 / Analysis (Level 2)**
- L90-107 `call`: 단항 RPC — 채널을 `async with` 로 닫고(자원 누수 없음), 인증 오류·연결 오류·그 외를 A2AError 로 구분.
- L110-120 `stream`: 서버 스트림을 JSON-RPC 형태(`{result}`/`{error}`)로 — 게이트웨이 `_stream` 이 정준형으로 바꾼다.
**짚어볼 점 / Watch-outs**
- L91 함수 안 import(`A2AError`) — `a2a_gateway` ↔ `a2a_grpc` **순환 의존**을 지연 import 로 피한다(`a2a_gateway.py:345` 도 지연 import). 구조적으로는 오류 타입을 공용 모듈로 분리하는 편이 깔끔하다.
- L113 `stream` 은 `_request` 의 `ValueError` 를 처리하지 않는다(`call` 은 처리, L95-96) — 지원하지 않는 작업으로 스트림을 부르면 예외가 그대로 전파된다(호출부는 stream/resubscribe 만 넘겨 실제 발생은 없음).
- L116 스트림 `timeout=settings.upstream_timeout`(30초)은 **전체 스트림 기한**(gRPC deadline)이다 — 30초가 넘는 장기 스트림은 DEADLINE_EXCEEDED 로 끊긴다(HTTP SSE 경로의 읽기 타임아웃과 의미가 다름).

```python path=app/services/a2a_grpc.py lines=123-148
# ───────────────────────── 수신 (gRPC → 게이트웨이 HTTP+JSON) ─────────────────────────
def _base(tenant: str) -> str:
    if not tenant or tenant == "platform":
        return "/a2a/platform/rest"
    if not _TENANT.match(tenant):
        raise ValueError(tenant)
    if tenant.startswith("platform/"):
        return f"/a2a/platform/{quote(tenant.split('/', 1)[1], safe='')}/rest"
    return f"/a2a/{quote(tenant, safe='')}/rest"


def _status_of(http_status: int, body: dict | None) -> tuple[grpc.StatusCode, str, bytes | None]:
    """REST google.rpc.Status → (gRPC 코드, 메시지, details 트레일러) — 같은 ErrorInfo 를 그대로 싣는다."""
    err = (body or {}).get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    name = err.get("status") or a2a_rest.HTTP_STATUS_NAME.get(http_status, "UNKNOWN")
    code = getattr(grpc.StatusCode, name, grpc.StatusCode.UNKNOWN)
    st = status_pb2.Status(code=code.value[0], message=err.get("message") or name)
    for d in err.get("details") or []:
        if isinstance(d, dict) and d.get("reason"):
            info = error_details_pb2.ErrorInfo(reason=d["reason"], domain=d.get("domain", ""),
                                               metadata={k: str(v) for k, v in (d.get("metadata") or {}).items()})
            packed = any_pb2.Any()
            packed.Pack(info)
            st.details.append(packed)
    return code, st.message, st.SerializeToString()
```
**분석 / Analysis (Level 2)**
- L124-131 `_base`: tenant → 게이트웨이 REST 경로(`/a2a/platform/rest`·`/a2a/platform/{profile}/rest`·`/a2a/{agent}/rest`), 형식 위반은 ValueError, 세그먼트는 `quote(..., safe="")` 로 인코딩.
- L134-148 `_status_of`: REST google.rpc.Status → gRPC 상태 코드·메시지·상세 트레일러(같은 ErrorInfo 를 Any 로 포장).
**짚어볼 점 / Watch-outs**
- L139 `getattr(grpc.StatusCode, name, UNKNOWN)` — 응답의 `status` 문자열로 enum 속성을 찾는다. 게이트웨이 응답이라 신뢰 범위 안이지만, 임의 이름이면 UNKNOWN 으로 안전하게 떨어진다.

```python path=app/services/a2a_grpc.py lines=151-202
class GatewayServicer(pbg.A2AServiceServicer):
    """A2AService 수신 — 요청마다 게이트웨이 REST 바인딩으로 위임(같은 인증 토큰·추적 헤더)."""

    def __init__(self, gateway_url: str | None = None):
        self.gateway_url = (gateway_url or settings.gateway_url).rstrip("/")

    def _headers(self, context) -> dict:
        md = {k.lower(): v for k, v in (context.invocation_metadata() or ())}
        return {"Content-Type": a2a_rest.MEDIA, "X-AgentNexus-Binding": "GRPC", **{k: md[k] for k in _FORWARD if k in md}}

    async def _abort(self, context, http_status: int, body: dict | None):
        code, msg, details = _status_of(http_status, body)
        await context.abort(code, msg, trailing_metadata=((DETAILS_KEY, details),))

    async def _url(self, context, tenant: str, path: str) -> str:
        try:
            return f"{self.gateway_url}{_base(tenant)}/{path}"
        except ValueError:
            await self._abort(context, 400, {"error": {"status": "INVALID_ARGUMENT", "message": f"잘못된 tenant: {tenant}"}})

    async def _unary(self, context, method: str, tenant: str, path: str, resp_cls, query=None, body=None):
        url = await self._url(context, tenant, path)
        async with async_client(timeout=settings.upstream_timeout + 30) as http:
            res = await http.request(method, url, params=query, headers=self._headers(context),
                                     **({"json": body} if body is not None else {}))
        data = res.json() if res.content else {}
        if res.status_code >= 400:
            await self._abort(context, res.status_code, data)
        return from_dict(data, resp_cls)

    async def _stream(self, context, method: str, tenant: str, path: str, body=None):
        url = await self._url(context, tenant, path)
        async with async_client(timeout=settings.upstream_timeout + 600) as http:
            async with http.stream(method, url, headers={**self._headers(context), "Accept": "text/event-stream"},
                                   **({"json": body} if body is not None else {})) as res:
                if res.status_code >= 400:
                    await res.aread()
                    await self._abort(context, res.status_code, res.json() if res.content else {})
                event = "message"
                async for line in res.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        if event == "error":
                            await self._abort(context, int((data.get("error") or {}).get("code") or 500), data)
                        yield from_dict(data, pb.StreamResponse)
                        event = "message"

    @staticmethod
    def _body(req, drop=("tenant",)) -> dict:
        return {k: v for k, v in to_dict(req).items() if k not in drop}
```
**분석 / Analysis (Level 2)**
- L151-159: 수신 서비스 — 요청 metadata 중 전달 허용 헤더 + `X-AgentNexus-Binding: GRPC`(기록용 라벨).
- L161-163 `_abort`: 상태·상세 트레일러로 중단(`context.abort` 는 예외를 던져 RPC 를 끝낸다).
- L165-169 `_url`: 잘못된 tenant 는 INVALID_ARGUMENT.
- L171-179 `_unary`: 게이트웨이 REST 호출 → 4xx/5xx 면 abort, 아니면 응답 메시지로 파싱.
- L181-198 `_stream`: 게이트웨이 SSE → `event:`·`data:` 파싱 → 오류 이벤트면 abort, 아니면 `StreamResponse` 로 yield.
- L200-202 `_body`: 요청 메시지 → dict(tenant 등 경로 필드 제외).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L176·L188 `res.json()` 이 JSON 이 아니면(프록시 502 HTML 등) `ValueError` → gRPC 는 UNKNOWN 으로 끝나고 원인이 사라진다.
- L173 `upstream_timeout + 30`, L183 `+ 600` — 게이트웨이 내부 처리 시간을 감안한 여유(스트림 10분).
- L159 `X-AgentNexus-Binding` 은 클라이언트가 직접 게이트웨이에 보내도 같은 라벨이 붙는다(주석대로 권한과 무관한 기록용이라 수용 — `app/api/a2a.py:329-330`).

```python path=app/services/a2a_grpc.py lines=204-245
    async def SendMessage(self, request, context):
        return await self._unary(context, "POST", request.tenant, "message:send", pb.SendMessageResponse, body=self._body(request))

    async def SendStreamingMessage(self, request, context):
        async for ev in self._stream(context, "POST", request.tenant, "message:stream", self._body(request)):
            yield ev

    async def GetTask(self, request, context):
        q = {"historyLength": request.history_length} if request.HasField("history_length") else None
        return await self._unary(context, "GET", request.tenant, f"tasks/{quote(request.id, safe='')}", pb.Task, query=q)

    async def ListTasks(self, request, context):
        q = {k: v for k, v in self._body(request).items() if not isinstance(v, (dict, list))}
        return await self._unary(context, "GET", request.tenant, "tasks", pb.ListTasksResponse, query=q)

    async def CancelTask(self, request, context):
        return await self._unary(context, "POST", request.tenant, f"tasks/{quote(request.id, safe='')}:cancel", pb.Task,
                                 body=self._body(request, ("tenant", "id")))

    async def SubscribeToTask(self, request, context):
        async for ev in self._stream(context, "GET", request.tenant, f"tasks/{quote(request.id, safe='')}:subscribe"):
            yield ev

    async def CreateTaskPushNotificationConfig(self, request, context):
        return await self._unary(context, "POST", request.tenant, f"tasks/{quote(request.task_id, safe='')}/pushNotificationConfigs",
                                 pb.TaskPushNotificationConfig, body=self._body(request, ("tenant", "taskId")))

    async def GetTaskPushNotificationConfig(self, request, context):
        return await self._unary(context, "GET", request.tenant, f"tasks/{quote(request.task_id, safe='')}/pushNotificationConfigs/"
                                 f"{quote(request.id, safe='')}", pb.TaskPushNotificationConfig)

    async def ListTaskPushNotificationConfigs(self, request, context):
        return await self._unary(context, "GET", request.tenant, f"tasks/{quote(request.task_id, safe='')}/pushNotificationConfigs",
                                 pb.ListTaskPushNotificationConfigsResponse)

    async def DeleteTaskPushNotificationConfig(self, request, context):
        from google.protobuf import empty_pb2
        return await self._unary(context, "DELETE", request.tenant, f"tasks/{quote(request.task_id, safe='')}/pushNotificationConfigs/"
                                 f"{quote(request.id, safe='')}", empty_pb2.Empty)

    async def GetExtendedAgentCard(self, request, context):
        return await self._unary(context, "GET", request.tenant, "extendedAgentCard", pb.AgentCard)
```
**분석 / Analysis (Level 2)**
- L204-245: RPC 별 메서드 — 각자 게이트웨이 REST 경로·메서드·응답 타입으로 위임. id 는 `quote(..., safe="")` 로 경로 인코딩(L213 등). List/Delete push 설정도 위임하지만 게이트웨이 REST 가 UNSUPPORTED 로 응답한다(`app/services/a2a_rest.py:21`).
**짚어볼 점 / Watch-outs**
- L212 `HasField("history_length")` — proto3 optional 필드 존재 확인(0 과 미설정 구분)이 올바르다.
- 요청마다 새 httpx 클라이언트(L173·L183) — 연결 재사용 없음.

```python path=app/services/a2a_grpc.py lines=248-254
async def serve(listen: str | None = None, gateway_url: str | None = None) -> grpc.aio.Server:
    server = grpc.aio.server()
    pbg.add_A2AServiceServicer_to_server(GatewayServicer(gateway_url), server)
    port = server.add_insecure_port(listen or settings.a2a_grpc_listen)
    await server.start()
    server.bound_port = port  # 시험에서 0 번 포트로 띄울 때
    return server
```
**분석 / Analysis (Level 2)**
- L248-254 `serve`: aio 서버 생성·서비스 등록·**insecure 포트** 바인딩·시작, 바인딩된 포트를 속성으로(시험에서 0번 포트 사용).
**짚어볼 점 / Watch-outs**
- [위험:중간] L251 `add_insecure_port` — TLS 없이 수신한다(`app/grpc_server.py` 리뷰 참조). 서버 옵션(최대 메시지 크기·동시 스트림·keepalive)도 기본값이다 — 거대한 메시지로 메모리를 소모시키는 공격에 대한 상한이 gRPC 기본(4MB 수신)에만 의존한다.

## 파일 평가 / File Assessment
- 강점: (1) 상태 없는 프로토콜 변환기로 정책 집행을 게이트웨이 한 곳에 위임. (2) tenant·metadata·경로 세그먼트에 화이트리스트·인코딩. (3) 오류 상세(ErrorInfo)를 REST↔gRPC 간 보존.
- 위험: (1) [중간] 원격 호출 기본 평문 채널·egress 가드 미적용. (2) [중간] 수신 서버 평문. (3) [낮음] 비 JSON 응답 처리, 스트림 deadline 30초.
- 개선 권고(영향순): (1) 원격 gRPC 기본 TLS + 대상 주소 검증. (2) 수신 TLS 옵션. (3) 스트림 deadline 분리 설정, JSON 파싱 오류를 INTERNAL 로 명시 변환.
- 재읽기 권고 레벨: 3.
