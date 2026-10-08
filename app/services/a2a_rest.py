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
