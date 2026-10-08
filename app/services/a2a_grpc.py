"""A2A gRPC 바인딩 (A2A 1.0 §10, D16-②, Epic 37) — 정규 `lf.a2a.v1.A2AService` (a2a-sdk 가 싣는 생성 스텁).

- 수신: compose 서비스 `a2a-grpc`(`python -m app.grpc_server`)의 `GatewayServicer` 가 RPC 를 ProtoJSON 으로 바꿔
  게이트웨이 HTTP+JSON 바인딩으로 보낸다 — 인증(metadata `authorization`)·정책·가드·감사·Task 영속은 게이트웨이 한 곳.
  대상은 요청의 `tenant`(= 카드 AgentInterface.tenant): `platform` · `platform/{profile}` · 그 밖은 원격 에이전트 id.
  오류는 google.rpc.Status(+ErrorInfo) 를 `grpc-status-details-bin` 트레일러로.
- 원격: 카드에 GRPC 인터페이스만 있으면 게이트웨이가 같은 스텁으로 호출한다(`call`·`stream`, URL `host:port[#tenant]`).
"""
import asyncio
import json
import re
import time
from urllib.parse import quote

import grpc
import httpx
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


def _channel(addr: str, tls: bool, credentials=None) -> grpc.aio.Channel:
    """credentials — 대상 TLS 설정(사설 CA·mTLS)으로 만든 자격 증명, 없으면 시스템 신뢰 저장소."""
    if not tls:
        return grpc.aio.insecure_channel(addr)
    return grpc.aio.secure_channel(addr, credentials or grpc.ssl_channel_credentials())


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


async def call(url: str, op: str, params: dict, headers: dict, credentials=None) -> dict:
    from app.services.a2a_gateway import A2AError
    addr, tenant, tls = _target(url)
    try:
        rpc, req, _ = _request(op, params, tenant)
    except ValueError:
        raise A2AError(ap.UNSUPPORTED_OP, f"gRPC 바인딩에서 지원하지 않는 작업: {op}") from None
    async with _channel(addr, tls, credentials) as ch:
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


async def stream(url: str, op: str, params: dict, headers: dict, credentials=None):
    """서버 스트림 → JSON-RPC 형태 메시지({result} | {error}) — 게이트웨이 _stream 이 정준형으로 바꾼다."""
    addr, tenant, tls = _target(url)
    rpc, req, _ = _request(op, params, tenant)
    async with _channel(addr, tls, credentials) as ch:
        try:
            async for ev in getattr(pbg.A2AServiceStub(ch), rpc)(req, metadata=_metadata(headers), timeout=settings.upstream_timeout):
                yield {"result": to_dict(ev)}
        except grpc.aio.AioRpcError as exc:
            code, msg = _error_of(exc)
            yield {"error": {"code": code, "message": msg}}


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


def server_tls() -> tuple[bytes, bytes] | None:
    """(key, cert) PEM — A2A_GRPC_TLS_CERT·A2A_GRPC_TLS_KEY 가 모두 있으면."""
    if settings.a2a_grpc_tls_cert and settings.a2a_grpc_tls_key:
        with open(settings.a2a_grpc_tls_key, "rb") as k, open(settings.a2a_grpc_tls_cert, "rb") as c:
            return k.read(), c.read()
    return None


# 수신 상한 (D19-②) — 거대한 메시지·연결당 스트림 폭주·유휴 연결로 자원을 소모시키지 못하게
MAX_RECEIVE_BYTES = 4 * 1024 * 1024
SERVER_OPTIONS = [("grpc.max_receive_message_length", MAX_RECEIVE_BYTES), ("grpc.max_send_message_length", 16 * 1024 * 1024),
                  ("grpc.max_concurrent_streams", 100), ("grpc.keepalive_time_ms", 60_000), ("grpc.keepalive_timeout_ms", 20_000),
                  ("grpc.http2.min_ping_interval_without_data_ms", 30_000), ("grpc.max_connection_idle_ms", 15 * 60_000)]
MAX_CONCURRENT_RPCS = 256


# gRPC 표준 헬스 (grpc.health.v1.Health/Check) — 생성 코드·추가 의존성 없이 바이트로 (D20-③).
# HealthCheckRequest{string service = 1} · HealthCheckResponse{ServingStatus status = 1} (1 SERVING · 2 NOT_SERVING)
HEALTH_SERVICE = "grpc.health.v1.Health"
_STATUS = {True: b"\x08\x01", False: b"\x08\x02"}


READINESS_CACHE_S = 2.0  # readiness 결과 공유 시간 — 프로브·외부 호출이 몰려도 api /health 는 2초에 1번


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def health_request(service: str = "") -> bytes:
    """HealthCheckRequest{service} — 필드 1(문자열) 길이는 protobuf varint (128 바이트 이상도, D22-⑥)."""
    raw = service.encode()
    return b"\x0a" + _varint(len(raw)) + raw if raw else b""


def _service_of(req: bytes) -> str:
    if len(req) < 2 or req[0] != 0x0A:
        return ""
    n, shift, i = 0, 0, 1
    while i < len(req) and i < 6:  # 길이 varint (최대 5 바이트)
        n |= (req[i] & 0x7F) << shift
        shift, i = shift + 7, i + 1
        if not req[i - 1] & 0x80:
            return req[i:i + n].decode("utf-8", "replace")
    return ""


def health_handler(gateway_url: str | None = None) -> grpc.GenericRpcHandler:
    """""(기본·liveness) = 프로세스가 응답하면 SERVING · "readiness" = 위임 대상 게이트웨이 /health 가 200 일 때만 SERVING."""
    base = (gateway_url or settings.gateway_url).rstrip("/")
    last, lock = {"at": -READINESS_CACHE_S, "ok": False}, asyncio.Lock()

    async def check(request: bytes, context) -> bytes:
        if _service_of(request) != "readiness":
            return _STATUS[True]
        if time.monotonic() - last["at"] < READINESS_CACHE_S or lock.locked():  # 확인 중이면 줄 서지 않고 직전 값 (65th)
            return _STATUS[last["ok"]]
        async with lock:  # 인증 없는 공개 readiness 가 api /health(DB)로 증폭되지 않게 — 결과를 잠깐 공유 (D22-④)
            if time.monotonic() - last["at"] >= READINESS_CACHE_S:
                try:
                    async with async_client(timeout=2) as http:
                        last["ok"] = (await http.get(f"{base}/health")).status_code == 200
                except httpx.HTTPError:
                    last["ok"] = False
                last["at"] = time.monotonic()
            return _STATUS[last["ok"]]
    return grpc.method_handlers_generic_handler(HEALTH_SERVICE, {"Check": grpc.unary_unary_rpc_method_handler(check)})


async def serve(listen: str | None = None, gateway_url: str | None = None,
                tls: tuple[bytes, bytes] | None = None) -> grpc.aio.Server:
    server = grpc.aio.server(options=SERVER_OPTIONS, maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS)
    pbg.add_A2AServiceServicer_to_server(GatewayServicer(gateway_url), server)
    server.add_generic_rpc_handlers((health_handler(gateway_url),))
    tls = tls or server_tls()
    addr = listen or settings.a2a_grpc_listen
    port = server.add_secure_port(addr, grpc.ssl_server_credentials([tls])) if tls else server.add_insecure_port(addr)
    server.tls = bool(tls)
    await server.start()
    server.bound_port = port  # 시험에서 0 번 포트로 띄울 때
    return server
