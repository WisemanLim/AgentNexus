> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [a2a_grpc.py.md](../../../../CODE-REVIEW-Lv2/app/services/a2a_grpc.py.md)

### 14. app/services/a2a_grpc.py
**파일 요약 / File Summary**
- 역할 / Role: A2A 1.0 gRPC 바인딩(정규 `lf.a2a.v1.A2AService`, a2a-sdk 생성 스텁). (1) **수신**: `GatewayServicer` 가 RPC 를 ProtoJSON 으로 바꿔 게이트웨이 HTTP+JSON 바인딩으로 위임(인증·정책·소유권·감사는 게이트웨이), 오류는 `grpc-status-details-bin` 트레일러. (2) **원격 호출**: gRPC 전용 에이전트를 같은 스텁으로 호출(`call`·`stream`). 2차에서 수신 서버 **상한 옵션**(4 MiB·동시 스트림·keepalive·동시 RPC, D19-②)·**선택 TLS 수신**(D17-②), 원격 **grpcs 자격 증명**(사설 CA·mTLS, D19-②)이 더해졌다.
- 진입점 거리 / Distance: 1 (`app/grpc_server.py:16` `a2a_grpc.serve()`) · 2 (`app/services/a2a_gateway.py:346-348`·`:665-667` 원격 호출)
- LOC: 277 (현재 `wc -l`) · 1차 254 → **+23**
- 주요 export: `OPS`, `DETAILS_KEY`, `to_dict`, `from_dict`, `call`, `stream`, `GatewayServicer`, `server_tls`(신규), `MAX_RECEIVE_BYTES`·`SERVER_OPTIONS`·`MAX_CONCURRENT_RPCS`(신규), `serve`
- 의존(내부): `app.core.{config,http}`, `app.services.{a2a_proto,a2a_rest}`, 지연 import `app.services.a2a_gateway.A2AError`(순환 회피)
- 외부 패키지: grpcio(`grpc.aio`), a2a-sdk(`a2a.types.a2a_pb2`·`a2a_pb2_grpc`), protobuf(`json_format`·`any_pb2`·`empty_pb2`), googleapis-common-protos(`google.rpc`)
- 부수효과 / Side effects: gRPC 포트 바인딩(평문 또는 TLS), TLS 키·인증서 파일 읽기(기동 시), 게이트웨이·원격 에이전트 네트워크 호출
- 동시성 / Concurrency: asyncio(`grpc.aio`) — 서버 전체 동시 RPC 256, 연결당 스트림 100. 상태 없음(요청마다 httpx 클라이언트·gRPC 채널 생성).
- 테스트 커버리지 흔적: `test/test_a2a_grpc.py:66`(수신)·`:107`(정책 동등성)·`:125`(카드 GRPC 인터페이스)·`:165`(gRPC 전용 원격)·`:226`(수신 TLS), `test/test_lv2_hardening.py:49`(수신 상한 C49-01)·`:76`(원격 grpcs 사설 CA C49-02), prod 스모크 P44-05(`test/impl/44th/result.md`, gRPC GetTask 소유권)
- 2차 변경 요약 / Changes since 1st review (1차 인용 원문과 대조):
  - L57-61 `_channel(addr, tls, credentials=None)` — 1차 `secure_channel(addr, grpc.ssl_channel_credentials())` 한 줄에서 자격 증명 인자 추가.
  - L93·L100, L113·L117 `call`·`stream` 에 `credentials` 인자.
  - L251-256 `server_tls()` 신규(PEM 파일 쌍).
  - L259-264 `MAX_RECEIVE_BYTES`·`SERVER_OPTIONS`·`MAX_CONCURRENT_RPCS` 신규, L267-277 `serve` 가 옵션·TLS 를 적용(1차 `grpc.aio.server()` + `add_insecure_port`).
  - L24-249 의 나머지(OPS·오류 변환·tenant·서비서)는 1차 원문과 동일, 줄 번호만 이동.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/a2a_grpc.py lines=1-23
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
```
**분석 / Analysis (Level 2)**
- L1-8: 두 방향 역할. 수신은 **상태 없는 프로토콜 변환기**라 정책 집행이 게이트웨이 한 곳에 모인다 — Epic 44 소유권도 별도 구현 없이 gRPC 에 적용된다(P44-05). 원격 URL 형식이 `host:port[#tenant]` 로 문서화되어 있어 **스킴 없는 주소가 정상 형식**이라는 점이 아래 평문 기본값(L52·L59-60)과 연결된다.
- L13-17: `a2a.types` 는 a2a-sdk 가 싣는 생성 스텁 — 저장소에 `.proto` 를 두지 않고 SDK 버전에 묶인다(SDK 업그레이드 시 필드 호환 확인 필요).
- L19-22: `async_client` 는 서비서가 게이트웨이를 부를 때만 쓴다(원격 gRPC 호출은 httpx 팩토리를 거치지 않는다 — egress 가드 미적용의 원인).

```python path=app/services/a2a_grpc.py lines=24-48
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
```
**분석 / Analysis (Level 2)**
- L24-34 `OPS`: 정준 작업 → (RPC 이름, 요청 메시지 클래스, 서버 스트림 여부). 원격 호출용 — push 설정 목록·삭제는 없다(수신은 L238-245 에서 위임).
- L35: gRPC 표준 상세 오류 트레일러 키(`-bin` 접미사 = 바이너리 metadata).
- L36 `_TENANT`: tenant 화이트리스트(영숫자·`._-`, 최대 한 단계 `/`) — 경로 주입 방지.
- L37-38 `_FORWARD`: 게이트웨이(수신)·원격(호출)으로 넘길 metadata 화이트리스트.
- L41-46: protobuf ↔ dict(ProtoJSON, camelCase). `ignore_unknown_fields=True` 라 모르는 필드는 무시하지만 **타입이 틀린 값**(예: `historyLength: "abc"`)은 `json_format.ParseError` 를 올린다. 이 예외는 `ValueError` 가 아니다 — protobuf `json_format.py` 의 `class Error(Exception)` · `class ParseError(Error)`(로컬 설치본 소스에서 확인, 버전 차이 가능성).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L36 세그먼트로 `.`·`..` 만 있는 값도 통과한다 — `platform/..` 는 L133 에서 `/a2a/platform/../rest/...` 가 된다. httpx 가 점 세그먼트를 정규화하면(가능성, 추가 확인 필요) 다른 게이트웨이 경로로 갈 수 있는데, 현재 라우트 구성(`app/api/a2a.py:241-351`)에서는 맞는 경로가 없어 404 로 끝난다. 최소 수정: 정규식을 `(?!\.{1,2}(?:/|$))` 류로 점 전용 세그먼트 거부.

```python path=app/services/a2a_grpc.py lines=49-63
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
```
**분석 / Analysis (Level 2)**
- L50-54 `_target`: `grpcs://` 만 TLS, 그 밖 스킴(`grpc://`·`http://`)·스킴 없음은 모두 평문 주소로 본다. `#` 뒤는 tenant.
- L57-61 (2차 변경): 평문이면 insecure 채널, TLS 면 넘겨받은 자격 증명(`app/services/tls.py:97-121` — 대상 `auth.tls.ca_secret`·`client_cert_secret`, mTLS 정책 `required` 면 grpcs + 인증서 없을 때 거부) 또는 시스템 신뢰 저장소. 사설 CA 서버는 `ca_secret` 이 있어야 연결된다(`test/test_lv2_hardening.py:76-112`).
**짚어볼 점 / Watch-outs**
- [위험:중간 · 평문 자격 증명] L52·L59-60 스킴이 `grpcs://` 가 아니면 **평문 HTTP/2** 다(1차 지적, 미해결). 게이트웨이는 이 채널로 `authorization` metadata(사용자 명의 교환 토큰 또는 고정 Bearer — `app/services/a2a_gateway.py:245-272`, L88-90 로 전달)를 보낸다. 원격 카드가 `supportedInterfaces` 에 `host:port` 형식 GRPC 만 광고하면(이 제품의 자기 카드도 `A2A_GRPC_PUBLIC_URL=127.0.0.1:8851` — `.env.prod:56`) 그대로 평문 전송되고, 카드 변경은 재검토 전에 즉시 적용된다(`app/services/a2a_gateway.py:119-121`). mTLS 정책이 `required` 일 때만 막힌다(`app/services/tls.py:74-75`·`:107-108`). 최소 수정: prod 에서 원격 gRPC 는 `grpcs://` 만 허용(평문은 에이전트별 명시 플래그), 위반이면 A2AError.
- [위험:낮음 · egress 가드 미적용] L57-61 gRPC 채널은 httpx 팩토리를 거치지 않아 링크 로컬·IMDS 차단(`app/core/http.py:34-38`)이 적용되지 않는다(1차 지적, 미해결). 같은 문제를 MCP hosted 클라이언트는 생성 전 `blocked_url` 로 막는다(`app/services/mcp_gateway.py:582-587`) — 여기도 같은 사전 점검이 필요하다. HTTP/2 전용이라 HTTP/1.1 메타데이터 서비스를 읽기는 어렵지만, 내부 포트 탐색(오류 코드·지연 차이)은 가능하다.

```python path=app/services/a2a_grpc.py lines=64-92
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
- L64-75 `_error_of`: 상세 트레일러의 `ErrorInfo.reason` → A2A 코드 우선, 없으면 상태 코드 매핑. `Any.Unpack` 은 타입이 다르면 False(안전).
- L78-85 `_request`: 작업 → 요청 메시지, tenant 필드가 있으면 설정. 모르는 작업은 `ValueError`.
- L88-90 `_metadata`: 화이트리스트 헤더만 소문자 키로, `a2a-version` 이 없으면 1.0 추가. 내부 라벨(`X-AgentNexus-Binding`)이 원격으로 새지 않는다(1차가 칭찬한 최소 전달).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 기능 결함] L89 화이트리스트가 **apiKey 스킴의 헤더**(기본 `X-API-Key`, `app/services/a2a_gateway.py:253`)를 버린다 — gRPC 전용 원격 에이전트에 apiKey 인증을 설정하면 키가 전혀 전달되지 않아 항상 인증 실패한다(안전한 쪽 실패). 최소 수정: 원격 호출에서는 게이트웨이가 만든 인증 헤더(`upstream_headers` 결과의 키)를 화이트리스트에 더해 전달.
- [위험:낮음] L68 원격이 보낸 트레일러 바이트가 깨져 있으면 `Status.FromString` 이 `DecodeError` 를 올린다 — L103 `except` 블록 안에서 나므로 `A2AError` 로 감싸지지 않고 게이트웨이까지 500 으로 번진다(`stream` 은 L121-123 에서 제너레이터가 예외로 끝남).
- [위험:낮음] L82 `from_dict` 의 `ParseError` 는 `ValueError` 가 아니라 `call` 의 L98 `except ValueError` 에 걸리지 않는다 — 클라이언트가 타입이 틀린 params 를 보내면 -32602 대신 500, 게이트웨이 사용량은 "ok"(`app/services/a2a_gateway.py:585-587`). 최소 수정: `except (ValueError, json_format.ParseError)` → `A2AError(-32602)`.

```python path=app/services/a2a_grpc.py lines=93-112
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
```
**분석 / Analysis (Level 2)**
- L94: `A2AError` 지연 import — `a2a_gateway` ↔ `a2a_grpc` 순환 의존 회피(1차 지적, 구조 개선 미해결).
- L100: 채널을 `async with` 로 열고 닫는다(자원 누수 없음). 호출마다 새 채널이라 TCP·TLS 핸드셰이크를 매번 한다(성능).
- L102: `rpc` 는 `OPS` 의 고정 문자열이라 `getattr` 동적 호출에 주입 위험이 없다. `timeout` 은 gRPC deadline(호출 전체 기한).
- L103-109: 인증 오류·연결 오류·그 외를 A2AError 로 구분.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L102 응답 메시지 크기는 gRPC 클라이언트 기본 수신 상한(4 MiB)만 적용 — 채널 옵션이 없다. 원격이 상한을 넘기면 `RESOURCE_EXHAUSTED` → `_error_of` → -32603 으로 처리된다(안전).

```python path=app/services/a2a_grpc.py lines=113-125
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
```
**분석 / Analysis (Level 2)**
- L113-123: 서버 스트림 → `{result}`/`{error}` — 게이트웨이 `_stream`(`app/services/a2a_gateway.py:665-669`)이 정준형으로 바꾼다. 오류는 예외 대신 오류 메시지로 내보내 SSE 오류 이벤트가 된다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L116 `_request` 의 `ValueError`·`ParseError` 를 처리하지 않는다(1차 지적, 미해결) — 호출부는 stream·resubscribe 만 넘기지만 타입이 틀린 params 는 실제로 들어올 수 있고, 그때 게이트웨이 `gen` 의 `except`(A2AError·HTTPError, `app/services/a2a_gateway.py:711-716`)에 걸리지 않아 스트림이 오류 이벤트 없이 끊긴다.
- [위험:낮음] L119 `timeout=settings.upstream_timeout`(30초)은 **스트림 전체 기한**이다(1차 지적, 미해결) — HTTP SSE 경로는 이벤트 간격 기준이라(`app/services/a2a_gateway.py:700`) 같은 장기 Task 가 바인딩에 따라 30초에 끊기기도, 계속 흐르기도 한다. 최소 수정: 스트림용 별도 기한 설정(예: 수신 서비서와 같은 `+600`).

```python path=app/services/a2a_grpc.py lines=126-153
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
- L127-134 `_base`: tenant → 게이트웨이 REST 경로(`/a2a/platform/rest`·`/a2a/platform/{profile}/rest`·`/a2a/{agent}/rest`). 형식 위반은 `ValueError`, 세그먼트는 `quote(safe="")` 로 `/` 까지 인코딩. 비어 있으면 플랫폼(기본 tenant).
- L137-151 `_status_of`: 게이트웨이 REST 오류(google.rpc.Status JSON — `app/services/a2a_rest.py:107-121`) → gRPC 상태·메시지·상세 트레일러. `status` 이름이 임의 문자열이면 `getattr` 기본값 UNKNOWN 으로 안전하게 떨어진다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L141 이름 우선·HTTP 상태 보조 — SSE `event: error` 본문은 `error.code` 에 HTTP 상태를 싣고 `status` 이름도 함께 준다(`app/services/a2a_rest.py:144-147`)라 L199 의 `int(...code...)` 와 일관된다. 형식이 바뀌면 둘 다 고쳐야 하는 결합(문서화 권장).

```python path=app/services/a2a_grpc.py lines=154-183
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
```
**분석 / Analysis (Level 2)**
- L157-158: 위임 대상 게이트웨이 URL(기본 `settings.gateway_url` = `http://127.0.0.1:8000`, `app/core/config.py:27`).
- L160-162 `_headers`: 클라이언트 metadata 중 화이트리스트만 전달 + 기록용 바인딩 라벨. `authorization` 을 그대로 넘기므로 인증은 게이트웨이 `_auth`(`app/api/a2a.py:325`)가 하고, 호출자 신고 헤더(`x-agentnexus-*`)는 게이트웨이가 서버 판정한다(`app/services/policy.py:56-73`) — 이 서비서는 신뢰 판단을 하지 않는다(좋은 분리).
- L164-166 `_abort`: `context.abort` 는 예외를 던져 RPC 를 끝낸다 — 그래서 L168-172 `_url` 은 실패 시 값을 돌려주지 않아도 된다.
- L174-182 `_unary`: 게이트웨이 REST 호출 → 4xx/5xx 면 같은 ErrorInfo 로 abort, 아니면 응답 메시지로.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L179 `res.json()` 이 JSON 이 아니면(앞단 프록시 502 HTML 등) `ValueError` → gRPC UNKNOWN 으로 끝나 원인이 사라진다(1차 지적, 미해결). L182 `from_dict` 의 `ParseError` 도 같다.
- [위험:낮음] L176 요청마다 새 httpx 클라이언트(1차 지적, 미해결) — 연결 재사용이 없어 지연·포트 소모. 서비서 인스턴스에 클라이언트 하나를 두고 `serve` 종료 시 닫는 편이 낫다.
- [위험:낮음] L176-178 게이트웨이 연결 실패(`httpx.ConnectError`·`ReadTimeout` — api 재시작·롤아웃 중)를 잡지 않는다. 서비서 밖으로 나간 예외는 grpc.aio 가 `UNKNOWN`(예외 문자열 포함)으로 끝내므로, 클라이언트는 재시도해도 되는 `UNAVAILABLE`/`DEADLINE_EXCEEDED` 를 받지 못한다(`_stream` L186-188 도 같음). 최소 수정: `except httpx.TransportError` → `_abort(context, 503, …)`(→ `UNAVAILABLE`, `app/services/a2a_rest.py:57-59` 표), 타임아웃은 504.
- [위험:낮음] 서비서 → 게이트웨이 구간은 기본 `http://`(L158) 라 Bearer 토큰이 클러스터 내부망에서 평문으로 간다 — Helm 은 NetworkPolicy 로 이 구간을 좁힌다(`test/impl/41st/result.md` G41-06~09), compose 는 내부 네트워크 의존.

```python path=app/services/a2a_grpc.py lines=184-206
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
- L184-201 `_stream`: 게이트웨이 SSE → `event:`·`data:` 파싱 → 오류 이벤트면 abort, 아니면 `StreamResponse`. 읽기 타임아웃은 이벤트 간격 기준 630초(`upstream_timeout + 600`). 오류 응답은 `aread()` 로 본문을 다 읽은 뒤 파싱(스트림 응답에서 `res.content` 를 쓰기 전 필수 — 흔한 실수 회피).
- L203-205 `_body`: 요청 메시지 → dict(경로로 간 tenant·id 제외).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L197 비 JSON `data:`·여러 줄 `data:` 미처리(1차 지적, 미해결) → UNKNOWN 으로 스트림 종료.
- [위험:낮음 · 자원] L186 긴 스트림(최대 630초 무이벤트 허용)이 L264 서버 전체 동시 RPC 256 슬롯을 오래 잡는다 — 인증된 사용자 하나가 `SubscribeToTask` 를 256개 열면 다른 모든 gRPC 클라이언트가 `RESOURCE_EXHAUSTED` 를 받는다. 게이트웨이 한도(`app/services/a2a_gateway.py:454`)는 호출 시작 시 한 번 차감이라 동시 개수를 막지 않는다. 최소 수정: 주체(또는 피어)별 동시 스트림 상한, 스트림 유휴 기한 단축.

```python path=app/services/a2a_grpc.py lines=207-229
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
```
**분석 / Analysis (Level 2)**
- L207-228: RPC 별로 게이트웨이 REST 경로·메서드·응답 타입에 위임. id 는 `quote(safe="")` 로 경로 인코딩. `HasField("history_length")` 는 proto3 optional 의 0 과 미설정을 구분한다(올바름).
- 소유권: `GetTask`·`CancelTask`·`SubscribeToTask` 는 게이트웨이에서 `_check_task_owner`(원격) 또는 `A2AServer._check_owner`(플랫폼)를 거친다(P44-05).

```python path=app/services/a2a_grpc.py lines=230-250
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
- L230-248: 푸시 설정 생성·조회·목록·삭제·확장 카드 위임. 목록·삭제는 게이트웨이 REST 가 미지원으로 응답한다(1차 분석과 동일).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L234-236 원격 에이전트 tenant 의 `GetTaskPushNotificationConfig` 는 게이트웨이가 업스트림 응답을 그대로 돌려주므로 게이트웨이 내부 웹훅 URL·푸시 토큰이 gRPC 클라이언트에게도 노출된다(원인은 `app/services/a2a_gateway.py:610`, 그쪽 보고서 항목).

```python path=app/services/a2a_grpc.py lines=251-266
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
```
**분석 / Analysis (Level 2)**
- L251-256 `server_tls` (2차 신규, D17-②): 키·인증서 파일이 **모두** 설정되면 PEM 바이트 쌍. 파일이 없으면 기동 시 `FileNotFoundError` 로 실패한다(fail-closed — 잘못된 설정으로 조용히 평문이 되지 않음, 좋음). Helm 은 두 값을 configmap 에서 주입한다(`deploy/helm/agentnexus/templates/a2a-grpc.yaml:27`).
- L259-264 (2차 신규, C49-01): 수신 4 MiB(gRPC 기본값과 같지만 이제 명시), 송신 16 MiB, 연결당 동시 스트림 100, 서버 keepalive 핑 60초·응답 대기 20초, 데이터 없는 클라이언트 핑 최소 간격 30초(더 잦으면 서버가 연결을 끊는다), 유휴 연결 15분, 서버 전체 동시 RPC 256(초과 시 `RESOURCE_EXHAUSTED`). 5 MiB 요청 거부를 시험한다(`test/test_lv2_hardening.py:49-72`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L253 둘 중 하나만 설정하면 경고 없이 평문으로 뜬다 — 기동 로그에 "평문" 이 찍히지만(`app/grpc_server.py:17`) 설정 오류로 보기는 어렵다. 한쪽만 있으면 기동 실패가 더 안전하다.
- [위험:낮음] `grpc.max_connection_age_ms` 가 없어 오래 사는 연결이 재분산되지 않는다(L4 로드밸런서 뒤 다중 파드에서 부하 쏠림 — 운영 메모 수준).

```python path=app/services/a2a_grpc.py lines=267-277
async def serve(listen: str | None = None, gateway_url: str | None = None,
                tls: tuple[bytes, bytes] | None = None) -> grpc.aio.Server:
    server = grpc.aio.server(options=SERVER_OPTIONS, maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS)
    pbg.add_A2AServiceServicer_to_server(GatewayServicer(gateway_url), server)
    tls = tls or server_tls()
    addr = listen or settings.a2a_grpc_listen
    port = server.add_secure_port(addr, grpc.ssl_server_credentials([tls])) if tls else server.add_insecure_port(addr)
    server.tls = bool(tls)
    await server.start()
    server.bound_port = port  # 시험에서 0 번 포트로 띄울 때
    return server
```
**분석 / Analysis (Level 2)**
- L267-277 (2차 변경): 상한 옵션·동시 RPC 로 aio 서버 생성 → 서비서 등록 → TLS 가 있으면 `ssl_server_credentials([(key, cert)])`(순서가 gRPC API 와 일치), 없으면 평문 → 시작. `server.tls`·`server.bound_port` 는 Python 객체에 붙인 속성(기동 로그·시험용). 클라이언트 인증서는 요구하지 않는다(`require_client_auth` 미지정 — 인증은 Bearer).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 1차 [중간] 잔여] L273 TLS 는 **선택**이고 기본은 평문(`app/core/config.py:69-70` 빈 값, 주석 "앞단 TLS 종료")이다. compose 는 호스트 `127.0.0.1` 에만 바인딩한다(`docker-compose.yml:65`), Helm 은 TLS 시크릿을 쓴다(41st). 앞단 종료 없이 외부에 노출하면 Bearer 토큰이 평문 — 배포 문서의 필수 조건으로 명시 권장.

## 파일 평가 / File Assessment
- 강점: (1) 상태 없는 변환기라 인증·정책·소유권이 게이트웨이 한 곳에서 집행되고(L154-182), metadata·tenant·경로 세그먼트에 화이트리스트·인코딩(L36-38, L127-134, L216). (2) 수신 상한 옵션·동시 RPC·선택 TLS 로 1차의 "기본값 의존·평문 수신" 지적 대부분 해소(L251-277). (3) 원격 grpcs 가 HTTP 와 같은 대상 TLS 설정(사설 CA·mTLS·정책)을 따른다(L57-61).
- 위험: (1) [위험:중간] 원격 gRPC 가 스킴에 따라 평문이 기본 — 사용자 명의 토큰이 평문 전송(`app/services/a2a_grpc.py:52`, `:59-60`). (2) [위험:낮음] 원격 주소 egress 가드 미적용(`:57-61`), `ParseError`·`DecodeError` 미처리로 500(`:68`, `:82`, `:98`, `:116`). (3) [위험:낮음] apiKey 헤더 미전달(`:89`), 스트림 기한 30초(`:119`), 긴 스트림의 RPC 슬롯 독점(`:186`, `:264`).
- 개선 권고(영향순): (1) prod 원격 gRPC 는 `grpcs://` 만(평문은 명시 플래그) + 채널 생성 전 `blocked_url` 점검. (2) `except (ValueError, json_format.ParseError)`·트레일러 디코드 보호로 A2AError 변환. (3) 원격 호출에 인증 헤더 전달, 스트림 기한 분리, 주체별 동시 스트림 상한. (4) 서비서 httpx 클라이언트 재사용, TLS 설정 한쪽만 있을 때 기동 실패.
- 파일 위험 등급 / Risk grade: **중간** — 미해결 [위험:중간] 1건(평문 기본값). 수신 쪽 1차 [중간]은 상한·TLS 옵션으로 해결·완화.
- 재읽기 권고 레벨: 3 — 프로토콜 변환 로직은 단순하고, 남은 쟁점은 원격 채널 보안 정책(스킴·가드)에 집중되어 있다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| [중간] L52-58 `grpcs://` 아니면 평문 채널 — 교환 토큰 평문 전송(INDEX §4.3 #3 일부) | 미해결 | `app/services/a2a_grpc.py:52`, `:59-60` |
| L50-54 원격 카드 주소에 egress 가드 미적용(SSRF 경로) | 미해결 | `app/services/a2a_grpc.py:57-61` (가드 호출 없음; 선례 `app/services/mcp_gateway.py:582-587`) |
| L57-58 `_channel` 이 시스템 루트 인증서만 사용(분석 언급 — 사설 CA·mTLS 불가) | 해결 | `app/services/a2a_grpc.py:57-61`, `app/services/a2a_gateway.py:80-81`, `app/services/tls.py:97-121`; `test/test_lv2_hardening.py:76` |
| L91 함수 안 `A2AError` import — 순환 의존 | 미해결 | `app/services/a2a_grpc.py:94` |
| L113 `stream` 이 `_request` 의 ValueError 미처리 | 미해결 | `app/services/a2a_grpc.py:116` (+ `call` 도 `ParseError` 미처리 `:98`) |
| L116 스트림 `timeout` 30초 = 전체 deadline | 미해결 | `app/services/a2a_grpc.py:119` |
| [낮음] L176·L188 비 JSON 응답 → UNKNOWN | 미해결 | `app/services/a2a_grpc.py:179`, `:191`, `:197` |
| L159 `X-AgentNexus-Binding` 라벨은 클라이언트도 보낼 수 있음(1차 수용) | 미해결 (수용 유지) | `app/services/a2a_grpc.py:162`; `app/api/a2a.py:333-334` |
| 요청마다 새 httpx 클라이언트 | 미해결 | `app/services/a2a_grpc.py:176`, `:186` |
| [중간] L251 `add_insecure_port` — 평문 수신 | 부분 해결 | 선택 TLS `app/services/a2a_grpc.py:251-256`, `:271-273`(`test/test_a2a_grpc.py:226`); 기본은 평문 `app/core/config.py:69-70` |
| L251 서버 옵션 기본값(메시지 크기·동시 스트림·keepalive 상한 없음) | 해결 | `app/services/a2a_grpc.py:259-264`, `:269`; `test/test_lv2_hardening.py:49` |
