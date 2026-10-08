> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 24. app/services/a2a_rest.py
**파일 요약 / File Summary**
- 역할 / Role: A2A 1.0 HTTP+JSON(REST) 바인딩 — REST 요청 → 1.0 JSON-RPC (메서드, params) 변환(같은 처리 경로 재사용), JSON-RPC 결과 → REST 응답(봉투 없음, 오류는 `google.rpc.Status`+`ErrorInfo`), 스트림 SSE 변환, 게이트웨이 → REST 전용 원격 호출용 경로 생성, REST 오류 → A2A 코드.
- 진입점 거리 / Distance: 2 (`app/api/a2a.py:26,317-336`)
- LOC: 194
- 주요 export: `MEDIA`, `to_rpc`, `RestError`, `error_body`, `error_response`, `from_rpc_response`, `rest_request`, `code_from_status`, `A2A_ERRORS`, `REASON_CODE`, `HTTP_STATUS_NAME`
- 의존(내부): `app.services.a2a_proto`
- 외부 패키지: starlette
- 부수효과 / Side effects: 없음
- 동시성 / Concurrency: async generator(스트림 변환)
- 테스트 커버리지 흔적: `test/test_a2a_rest.py`, `test/test_a2a_grpc.py`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/a2a_rest.py lines=1-21
"""A2A HTTP+JSON/REST 바인딩 (A2A 1.0 §11, D16-①, Epic 36).

REST 요청을 1.0 JSON-RPC 와 같은 (메서드, params) 로 바꿔 **같은 처리 경로**(게이트웨이 프록시·플랫폼 서버)로 보내고,
결과는 JSON-RPC 봉투 없이, 오류는 `google.rpc.Status`(+ `ErrorInfo` reason·domain)로 돌려준다. 스트림은 SSE `data:` 에
`StreamResponse` 객체만. 원격 에이전트가 HTTP+JSON 인터페이스만 내면 게이트웨이가 같은 표로 REST 호출을 만든다.

경로는 인터페이스 URL 기준(정규 a2a.proto 의 google.api.http): `message:send` · `message:stream` · `tasks` · `tasks/{id}` ·
`tasks/{id}:cancel` · `tasks/{id}:subscribe`(GET, 본문 표기의 POST 도 받음) · `tasks/{id}/pushNotificationConfigs[/{configId}]` ·
`extendedAgentCard`.
"""
import json
import re
from urllib.parse import quote

from starlette.responses import JSONResponse, Response, StreamingResponse

from app.services import a2a_proto as ap

MEDIA = "application/a2a+json"
DOMAIN = "a2a-protocol.org"
UNSUPPORTED = {"ListTaskPushNotificationConfigs", "DeleteTaskPushNotificationConfig"}  # 게이트웨이는 푸시 설정을 대신 등록하므로 목록·삭제는 미지원
```
**분석 / Analysis (Level 2)**
- L1-9: 설계 — REST 를 1.0 JSON-RPC 와 **같은 처리 경로**로 보낸다(정책·가드·감사 중복 구현 방지). 경로는 정규 `a2a.proto` 의 `google.api.http` 규칙(`message:send` 처럼 콜론 동사 포함).
- L19-21: 미디어 타입 `application/a2a+json`, ErrorInfo 도메인, 미지원 작업(게이트웨이가 푸시 설정을 대신 등록하므로 목록·삭제 불가).

```python path=app/services/a2a_rest.py lines=22-52
_ID = r"(?P<id>[^/]+?)"
_ROUTES = [
    ("POST", re.compile(r"^message:send$"), "SendMessage"),
    ("POST", re.compile(r"^message:stream$"), "SendStreamingMessage"),
    ("GET", re.compile(r"^tasks$"), "ListTasks"),
    ("GET", re.compile(rf"^tasks/{_ID}:subscribe$"), "SubscribeToTask"),
    ("POST", re.compile(rf"^tasks/{_ID}:subscribe$"), "SubscribeToTask"),
    ("POST", re.compile(rf"^tasks/{_ID}:cancel$"), "CancelTask"),
    ("GET", re.compile(rf"^tasks/{_ID}$"), "GetTask"),
    ("POST", re.compile(rf"^tasks/{_ID}/pushNotificationConfigs$"), "CreateTaskPushNotificationConfig"),
    ("GET", re.compile(rf"^tasks/{_ID}/pushNotificationConfigs$"), "ListTaskPushNotificationConfigs"),
    ("GET", re.compile(rf"^tasks/{_ID}/pushNotificationConfigs/(?P<cid>[^/]+)$"), "GetTaskPushNotificationConfig"),
    ("DELETE", re.compile(rf"^tasks/{_ID}/pushNotificationConfigs/(?P<cid>[^/]+)$"), "DeleteTaskPushNotificationConfig"),
    ("GET", re.compile(r"^extendedAgentCard$"), "GetExtendedAgentCard"),
]
# A2A 오류 → (HTTP, google.rpc 상태, ErrorInfo reason) — 사양 §5.4
A2A_ERRORS = {
    ap.TASK_NOT_FOUND: (404, "NOT_FOUND", "TASK_NOT_FOUND"),
    ap.TASK_NOT_CANCELABLE: (400, "FAILED_PRECONDITION", "TASK_NOT_CANCELABLE"),
    ap.PUSH_NOT_SUPPORTED: (400, "FAILED_PRECONDITION", "PUSH_NOTIFICATION_NOT_SUPPORTED"),
    ap.UNSUPPORTED_OP: (400, "FAILED_PRECONDITION", "UNSUPPORTED_OPERATION"),
    -32005: (400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED"),
    -32006: (500, "INTERNAL", "INVALID_AGENT_RESPONSE"),
    -32007: (400, "FAILED_PRECONDITION", "EXTENDED_AGENT_CARD_NOT_CONFIGURED"),
    -32008: (400, "FAILED_PRECONDITION", "EXTENSION_SUPPORT_REQUIRED"),
    -32009: (400, "FAILED_PRECONDITION", "VERSION_NOT_SUPPORTED"),
}
REASON_CODE = {reason: code for code, (_, _, reason) in A2A_ERRORS.items()}
HTTP_STATUS_NAME = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND", 409: "ABORTED",
                    415: "INVALID_ARGUMENT", 429: "RESOURCE_EXHAUSTED", 500: "INTERNAL", 502: "UNAVAILABLE", 503: "UNAVAILABLE",
                    504: "DEADLINE_EXCEEDED"}
```
**분석 / Analysis (Level 2)**
- L22 `_ID`: 슬래시 없는 최소 일치 id 그룹.
- L23-36 `_ROUTES`: (HTTP 메서드, 경로 정규식, RPC 이름) 표 — `^…$` 앵커로 전체 일치.
- L38-48 `A2A_ERRORS`: A2A 오류 코드 → (HTTP 상태, google.rpc 코드명, ErrorInfo reason)(사양 §5.4).
- L49: reason → 코드 역매핑. L50-52: HTTP 상태 → google.rpc 코드명.
**짚어볼 점 / Watch-outs**
- L22 `[^/]+?` 는 `:` 를 포함할 수 있다 — `GET tasks/abc:subscribe` 는 L27(구독)과 L30(`GET tasks/{id}`, id=`abc:subscribe`) 둘 다에 일치하므로 **표 순서**(L27 이 먼저)로 구독이 선택된다. 순서를 바꾸면 구독 요청이 GetTask 로 해석된다. 반대로 `GET tasks/abc:cancel` 은 L29 가 POST 전용이라 L30 에서 id `abc:cancel` 인 GetTask 가 된다(사양상 무해).

```python path=app/services/a2a_rest.py lines=55-74
class RestError(Exception):
    def __init__(self, code: int, message: str, data: dict | None = None, http_status: int = 0):
        super().__init__(message)
        self.code, self.message, self.data, self.http_status = code, message, data or {}, http_status


def _query(q) -> dict:
    out: dict = {}
    for k in ("contextId", "status", "pageToken"):
        if q.get(k):
            out[k] = q[k]
    for k in ("pageSize", "historyLength"):
        if q.get(k) is not None:
            try:
                out[k] = int(q[k])
            except ValueError as exc:
                raise RestError(-32602, f"{k} 는 정수여야 합니다.") from exc
    if q.get("includeArtifacts") is not None:
        out["includeArtifacts"] = q["includeArtifacts"] == "true"
    return out
```
**분석 / Analysis (Level 2)**
- L55-58 `RestError`: A2A 코드 + HTTP 상태.
- L61-74 `_query`: 허용 쿼리만 추출 — 문자열 3개, 정수 2개(변환 실패 400), `includeArtifacts` 불리언.

```python path=app/services/a2a_rest.py lines=77-104
def to_rpc(http_method: str, path: str, query, body: dict | None) -> tuple[str, dict]:
    """REST → (A2A 1.0 JSON-RPC 메서드, params)."""
    path = path.strip("/")
    for method, rx, rpc in _ROUTES:
        m = rx.match(path)
        if m is None or method != http_method:
            continue
        if rpc in UNSUPPORTED:
            raise RestError(ap.UNSUPPORTED_OP, f"지원하지 않는 작업: {rpc}")
        tid = m.groupdict().get("id")
        if rpc in ("SendMessage", "SendStreamingMessage"):
            if not isinstance(body, dict) or not isinstance(body.get("message"), dict):
                raise RestError(-32602, "본문에 message 객체가 필요합니다 (SendMessageRequest).")
            return rpc, body
        if rpc == "ListTasks":
            return rpc, _query(query)
        if rpc == "GetTask":
            return rpc, {"id": tid, **{k: v for k, v in _query(query).items() if k == "historyLength"}}
        if rpc in ("CancelTask", "SubscribeToTask"):
            return rpc, {"id": tid, **({k: v for k, v in (body or {}).items() if k != "id"} if rpc == "CancelTask" else {})}
        if rpc == "CreateTaskPushNotificationConfig":
            if not isinstance(body, dict) or not body.get("url"):
                raise RestError(-32602, "본문에 url 이 필요합니다 (TaskPushNotificationConfig).")
            return rpc, {**body, "taskId": tid}
        if rpc == "GetTaskPushNotificationConfig":
            return rpc, {"taskId": tid, "id": m.group("cid")}
        return rpc, {}
    raise RestError(-32601, f"알 수 없는 경로: {http_method} /{path}", http_status=404)
```
**분석 / Analysis (Level 2)**
- L77-104 `to_rpc`: 경로 정규화 → 표 순회 → 미지원이면 UNSUPPORTED_OP → RPC 별 params 구성(send/stream 은 본문 그대로·message 필수, ListTasks 는 쿼리, GetTask 는 id+historyLength, Cancel 은 id+본문, Subscribe 는 id, push 생성은 url 필수+taskId). 일치 없으면 404.
**짚어볼 점 / Watch-outs**
- L100 `{**body, "taskId": tid}` — 본문의 `url` 을 그대로 통과시킨다(검증은 하위 계층 몫 — `a2a_proto.push_config_of` 와 같은 SSRF 입력 지점).
- L90 SendMessage 본문 전체를 params 로 넘긴다 — 알 수 없는 필드도 업스트림까지 간다.

```python path=app/services/a2a_rest.py lines=107-126
def error_body(code: int, message: str, data: dict | None = None, http_status: int = 0) -> tuple[int, dict]:
    """A2A·JSON-RPC 오류 → (HTTP 상태, google.rpc.Status JSON)."""
    data = data or {}
    # 게이트웨이 정책 거부(HTTP 상태·사유 코드 동반)는 JSON-RPC 코드가 A2A 표와 겹쳐도(-32003 등) HTTP 상태로
    if code in A2A_ERRORS and not (http_status >= 400 and data.get("code")):
        status, name, reason = A2A_ERRORS[code]
        domain = DOMAIN
    else:
        status = http_status if http_status >= 400 else {-32700: 400, -32600: 400, -32602: 400, -32601: 404}.get(code, 500)
        name = HTTP_STATUS_NAME.get(status, "UNKNOWN")
        reason = str(data.get("code") or name).upper()
        domain = "agentnexus"
    info = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason, "domain": domain,
            **({"metadata": {k: str(v) for k, v in data.items() if not isinstance(v, (dict, list))}} if data else {})}
    return status, {"error": {"code": status, "status": name, "message": message, "details": [info]}}


def error_response(code: int, message: str, data: dict | None = None, http_status: int = 0, headers: dict | None = None) -> JSONResponse:
    status, body = error_body(code, message, data, http_status)
    return JSONResponse(body, status_code=status, media_type=MEDIA, headers=headers)
```
**분석 / Analysis (Level 2)**
- L107-121 `error_body`: A2A 표에 있는 코드면 사양 매핑, 단 **게이트웨이 정책 거부**(HTTP 상태 ≥400 + 사유 코드 동반)는 같은 JSON-RPC 코드(-32003 등)라도 HTTP 상태로(L110-111 — A2A 의 PUSH_NOT_SUPPORTED(-32003)와 게이트웨이 FORBIDDEN(-32003)이 같은 숫자인 충돌을 푸는 장치). ErrorInfo metadata 는 스칼라 값만 문자열로.
- L124-126 `error_response`: JSONResponse(`application/a2a+json`).
**짚어볼 점 / Watch-outs**
- 같은 숫자 코드(-32003)가 두 의미를 갖는 설계 자체가 혼동의 원인이다 — 게이트웨이 확장 코드를 A2A 예약 범위와 겹치지 않게 재배정하는 것이 장기적으로 낫다.

```python path=app/services/a2a_rest.py lines=129-157
def from_rpc_response(resp: Response) -> Response:
    """JSON-RPC 처리 결과(JSONResponse·SSE StreamingResponse) → REST 형태."""
    if isinstance(resp, StreamingResponse):
        inner = resp.body_iterator

        async def gen():
            buf = ""
            async for chunk in inner:
                buf += chunk.decode() if isinstance(chunk, bytes) else chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    data = "".join(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
                    if not data:
                        continue
                    msg = json.loads(data)
                    if "error" in msg:
                        e = msg["error"] or {}
                        _, body = error_body(e.get("code", -32603), e.get("message", "error"), e.get("data"))
                        yield f"event: error\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
                    else:
                        yield f"data: {json.dumps(msg.get('result') or {}, ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    body = json.loads(resp.body or b"{}")
    if "error" in body and isinstance(body["error"], dict):
        e = body["error"]
        return error_response(e.get("code", -32603), e.get("message", "error"), e.get("data"), resp.status_code)
    if "result" not in body:  # 인증 실패 등 JSON-RPC 밖의 응답은 그대로
        return resp
    return JSONResponse(body["result"], status_code=200, media_type=MEDIA)
```
**분석 / Analysis (Level 2)**
- L129-150: 스트리밍이면 내부 SSE(JSON-RPC 메시지)를 다시 파싱해 REST SSE(`data:` 에 결과 객체만, 오류는 `event: error`)로 변환하는 async generator.
- L151-157: 단건이면 JSON-RPC 본문을 열어 오류는 google.rpc.Status, 결과는 봉투 없이, 그 밖(인증 실패 등 JSON-RPC 밖 응답)은 그대로.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L143 `json.loads(data)` 실패 시 예외가 스트림을 끊는다 — 내부에서 만든 SSE 라 실제 발생 가능성은 낮다.
- L151 `json.loads(resp.body ...)` — `resp` 가 JSON 이 아닌 `Response`(예: 비정형 500)면 `ValueError`.

```python path=app/services/a2a_rest.py lines=160-194
# ───────────────────────── 게이트웨이 → REST 전용 원격 에이전트 ─────────────────────────
def rest_request(op: str, params: dict) -> tuple[str, str, dict | None, dict | None]:
    """정준 작업 → (HTTP 메서드, 상대 경로, 쿼리, 본문) — 1.0 params 기준."""
    p = dict(params or {})
    tid = quote(str(p.get("id") or p.get("taskId") or ""), safe="")
    if op == "send":
        return "POST", "message:send", None, p
    if op == "stream":
        return "POST", "message:stream", None, p
    if op == "get":
        return "GET", f"tasks/{tid}", ({"historyLength": p["historyLength"]} if p.get("historyLength") is not None else None), None
    if op == "cancel":
        return "POST", f"tasks/{tid}:cancel", None, {k: v for k, v in p.items() if k != "id"}
    if op == "resubscribe":
        return "GET", f"tasks/{tid}:subscribe", None, None
    if op == "list":
        return "GET", "tasks", {k: v for k, v in p.items() if not isinstance(v, (dict, list))}, None
    if op == "push_set":
        return "POST", f"tasks/{tid}/pushNotificationConfigs", None, p
    if op == "push_get":
        return "GET", f"tasks/{quote(str(p.get('taskId') or ''), safe='')}/pushNotificationConfigs/{quote(str(p.get('id') or ''), safe='')}", None, None
    if op == "card":
        return "GET", "extendedAgentCard", None, None
    raise RestError(ap.UNSUPPORTED_OP, f"REST 바인딩에서 지원하지 않는 작업: {op}")


def code_from_status(status: int, body: dict | None) -> tuple[int, str]:
    """REST 오류 응답(google.rpc.Status) → (A2A/JSON-RPC 코드, 메시지)."""
    err = (body or {}).get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    reason = next((d.get("reason") for d in err.get("details") or [] if isinstance(d, dict) and d.get("reason")), None)
    msg = err.get("message") or f"원격 에이전트 오류 ({status})"
    if reason in REASON_CODE:
        return REASON_CODE[reason], msg
    return (ap.TASK_NOT_FOUND if status == 404 else -32602 if status == 400 else -32603), msg
```
**분석 / Analysis (Level 2)**
- L161-183 `rest_request`: 정준 작업 → (HTTP 메서드, 상대 경로, 쿼리, 본문) — id 는 `quote(..., safe="")` 로 **경로 인코딩**(id 에 `/`·`?` 가 있어도 다른 경로로 새지 않음 — 경로 주입 방지).
- L186-194 `code_from_status`: REST 오류 → (A2A 코드, 메시지) — ErrorInfo reason 우선, 없으면 404→TaskNotFound, 400→-32602, 그 외 -32603.
**짚어볼 점 / Watch-outs**
- L176 ListTasks 쿼리는 dict·list 가 아닌 params 를 전부 쿼리로 — 의도치 않은 필드도 원격에 전달된다.

## 파일 평가 / File Assessment
- 강점: (1) REST 를 같은 JSON-RPC 처리 경로로 보내 정책 집행을 한 곳에 유지. (2) 경로 세그먼트 인코딩으로 경로 주입 차단. (3) 오류 매핑이 사양 표와 1:1.
- 위험: (1) [낮음] 같은 숫자 오류 코드의 이중 의미(분기 로직으로 보완). (2) 본문·쿼리 필드를 걸러내지 않고 전달. (3) 경로 표 순서 의존.
- 개선 권고(영향순): (1) 게이트웨이 확장 오류 코드 재배정. (2) 허용 필드 화이트리스트. (3) 표 순서 의존을 시험으로 고정(`tasks/x:cancel` 케이스).
- 재읽기 권고 레벨: 3.
