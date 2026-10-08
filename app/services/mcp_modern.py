"""MCP 2026-07-28 (modern, stateless) 수용 — 게이트웨이 `/mcp/{id}` 를 dual-era 서버로 (D13-④, Epic 27).

- 요청마다 `_meta` 의 `io.modelcontextprotocol/protocolVersion`·`clientCapabilities` 로 판정, 세션·initialize 없음.
- `MCP-Protocol-Version`·`Mcp-Method`·`Mcp-Name` 헤더가 본문과 다르거나 빠지면 400 HeaderMismatch(-32020).
- `server/discover` · 모든 결과에 `resultType` · `_meta.io.modelcontextprotocol/serverInfo` · 목록·읽기 결과에 `ttlMs`·`cacheScope`.
- MRTR(SEP-2322): 업스트림(2025-11-25)이 sampling·elicitation 을 요청하면 `InputRequiredResult` 로 돌려주고, 클라이언트가
  `inputResponses`+`requestState` 로 재시도하면 같은 업스트림 호출에 응답을 넘겨 최종 결과를 돌려준다(응답·결과는 mcp_relays 로 워커 간 공유).
  requestState 는 Fernet(AEAD) — 호출 주체·메서드·인자 요약·만료(10분)를 담아 다른 주체·다른 요청의 재사용을 거부한다.
- 확장(D14-②, Epic 29): `subscriptions/listen`(승인 알림 → 목록 변경 알림, 모두 subscriptionId 태그) · `x-mcp-header` 의
  `Mcp-Param-*` 검증(-32020, SDK `mcp.shared.inbound` 규칙 그대로) · `_meta.progressToken` 이 있으면 요청 단위 SSE 로
  progress 중계 · 게이트웨이 → modern 업스트림 호출용 헤더·`_meta` 생성.
"""
import asyncio
import base64
import hashlib
import json
import time

from mcp.shared import inbound
from starlette.concurrency import run_in_threadpool

from app.core.config import APP_VERSION, MCP_SUPPORTED_VERSIONS
from app.core.ids import new_id
from app.db import SessionLocal, utcnow
from app.models import McpRelay

MODERN_VERSIONS = ("2026-07-28",)
SUPPORTED = MODERN_VERSIONS + MCP_SUPPORTED_VERSIONS
K_VERSION = "io.modelcontextprotocol/protocolVersion"
K_CAPS = "io.modelcontextprotocol/clientCapabilities"
K_CLIENT = "io.modelcontextprotocol/clientInfo"
K_SERVER = "io.modelcontextprotocol/serverInfo"
K_SUB = "io.modelcontextprotocol/subscriptionId"
HEADER_MISMATCH, UNSUPPORTED_VERSION = -32020, -32022
NAMED = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri",
         "tasks/get": "taskId", "tasks/update": "taskId", "tasks/cancel": "taskId"}  # Tasks 확장: Mcp-Name = taskId (라우팅)
MRTR_METHODS = {"tools/call", "prompts/get", "resources/read"}
CACHEABLE = {"tools/list", "prompts/list", "resources/list", "resources/read", "resources/templates/list"}
REMOVED = {"ping", "logging/setLevel", "resources/subscribe", "resources/unsubscribe", "initialize"}
SERVER_INFO = {"name": "AgentNexus MCP Gateway", "version": APP_VERSION}
CLIENT_INFO = {"name": "agentnexus-gateway", "version": APP_VERSION}  # 게이트웨이가 modern 업스트림을 부를 때
# subscriptions/listen — 필터 키 → 알림 메서드. resourceSubscriptions 는 지원하지 않아(resources.subscribe=false) 승인에서 뺀다
LISTEN_KINDS = {"toolsListChanged": "notifications/tools/list_changed",
                "promptsListChanged": "notifications/prompts/list_changed",
                "resourcesListChanged": "notifications/resources/list_changed"}
LISTEN_POLL_S = 5.0    # 목록 지문 비교 주기 — 정책(도구 규칙)·업스트림 변경을 같이 잡는다
LISTEN_PING_S = 15.0   # 프록시 유휴 종료 방지 SSE 주석
LISTEN_MAX_S = 900.0   # 이후 정상 종료(resultType complete) → 클라이언트가 다시 listen
MAX_LISTENS = 5        # 주체별 동시 구독 — 워커 전체 합(ratelimit 백엔드 카운터, prod 는 Valkey 공유 · D18-②)
LISTENS: dict[str, int] = {}  # 이 워커의 구독 수 (상태 확인용)


def _listen_key(sub: str) -> str:
    return f"listen:{sub}"


def listen_acquire(sub: str) -> str | None:
    """구독 1개 확보 → 보유자 id (한도 초과면 None). 워커 전체 합은 보유자 집합(prod 는 Valkey) — 만료(최대 구독 시간 + 여유)는
    넣을 때 정해져, 보유 워커가 반납 없이 죽어도 그 주체가 계속 구독하는 동안에도 정해진 시간 뒤 풀린다 (D20-④)."""
    import uuid

    from app.services import ratelimit, scheduler
    holder = f"{scheduler.WORKER}:{uuid.uuid4().hex[:12]}"
    return holder if ratelimit.backend().holder_acquire(_listen_key(sub), holder, MAX_LISTENS, int(LISTEN_MAX_S) + 60) else None


def listen_release(sub: str, holder: str | None) -> None:
    """반납은 취소 중에도 — await 하지 않는다(45th). Valkey 면 스레드에 넘기고 기다리지 않아 끊길 때마다 이벤트 루프가
    왕복 시간(최대 0.5초)만큼 멈추지 않게, 메모리 백엔드는 즉시 (D22-⑥)."""
    from app.services import ratelimit
    if not holder:
        return
    b = ratelimit.backend()
    if isinstance(b, ratelimit.RedisBackend):
        try:
            asyncio.get_running_loop().run_in_executor(None, b.holder_release, _listen_key(sub), holder)
            return
        except RuntimeError:  # 루프 밖(스레드) — 그대로
            pass
    b.holder_release(_listen_key(sub), holder)


def listen_count(sub: str) -> int:
    from app.services import ratelimit
    return ratelimit.backend().holder_count(_listen_key(sub))
STATE_TTL_S = 600
WAIT_S = 25.0
_RUNNING: dict[str, asyncio.Task] = {}
_PROGRESS: dict[str, asyncio.Queue] = {}  # state_id → 지금 이 호출을 기다리는 요청의 progress 큐 (워커 로컬)


class ModernError(Exception):
    def __init__(self, code: int, message: str, http_status: int = 400, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.http_status, self.data = code, message, http_status, data

    def body(self, rid) -> dict:
        err = {"code": self.code, "message": self.message, **({"data": self.data} if self.data else {})}
        return {"jsonrpc": "2.0", "id": rid, "error": err}


def requested_version(params: dict) -> str | None:
    meta = (params or {}).get("_meta") or {}
    v = meta.get(K_VERSION)
    return v if isinstance(v, str) else None


def _header_value(raw: str | None) -> str | None:
    """`=?base64?…?=` 표기를 풀어 비교한다 (Streamable HTTP Value Encoding)."""
    if raw is not None and raw.startswith("=?base64?") and raw.endswith("?="):
        try:
            return base64.b64decode(raw[9:-2]).decode()
        except ValueError:
            return None
    return raw


def validate(headers, method: str, params: dict, version: str) -> None:
    """버전 지원 여부 → 헤더·본문 일치 (서버 검증 규칙)."""
    if version not in MODERN_VERSIONS:
        raise ModernError(UNSUPPORTED_VERSION, "Unsupported protocol version", data={"supported": list(SUPPORTED), "requested": version})
    dup = inbound.find_duplicated_routing_header(headers.items())
    if dup is not None:  # 첫 값·끝 값을 읽는 중개자가 서로 다르게 판단하지 않도록
        raise ModernError(HEADER_MISMATCH, f"Header mismatch: {dup} 헤더가 여러 번 있습니다.")
    hv = headers.get("mcp-protocol-version")
    if hv is None or hv != version:
        raise ModernError(HEADER_MISMATCH, f"Header mismatch: MCP-Protocol-Version '{hv}' ≠ body '{version}'")
    if headers.get("mcp-method") != method:
        raise ModernError(HEADER_MISMATCH, f"Header mismatch: Mcp-Method '{headers.get('mcp-method')}' ≠ body '{method}'")
    if method in NAMED:
        want = (params or {}).get(NAMED[method])
        got = _header_value(headers.get("mcp-name"))
        if got is None or got != want:
            raise ModernError(HEADER_MISMATCH, f"Header mismatch: Mcp-Name '{got}' ≠ body '{want}'")


def check_params(schema: dict | None, arguments, headers) -> None:
    """tools/call 의 `Mcp-Param-*` ↔ 인자 일치 (x-mcp-header 가 붙은 속성) — 어긋나면 400 HeaderMismatch."""
    if not isinstance(schema, dict):
        return
    rej = inbound.validate_mcp_param_headers(schema, arguments if isinstance(arguments, dict) else {}, headers)
    if rej is not None:
        raise ModernError(HEADER_MISMATCH, f"Header mismatch: {rej.message}")


def progress_token(params: dict):
    tok = ((params or {}).get("_meta") or {}).get("progressToken")
    return tok if isinstance(tok, str | int) and not isinstance(tok, bool) else None


class ProgressRelay:
    """하류 progressToken 하나 — 업스트림 progress 를 이 토큰으로 다시 태그하고, 증가하지 않는 값은 버린다
    (사양: progress 는 알림마다 증가. MRTR 가드 패턴 업스트림은 재시도 때 도구를 처음부터 다시 돌려 같은 값을 또 보낸다)."""

    def __init__(self, token):
        self.token, self.last = token, None

    def note(self, p: dict) -> dict | None:
        value = p.get("progress")
        if not isinstance(value, int | float) or (self.last is not None and value <= self.last):
            return None
        self.last = value
        params = {"progressToken": self.token, "progress": value}
        params.update({k: p[k] for k in ("total", "message") if p.get(k) is not None})
        return {"jsonrpc": "2.0", "method": "notifications/progress", "params": params}


# ───────────────────────── 게이트웨이 → modern 업스트림 ─────────────────────────
def request_headers(method: str, params: dict, schema: dict | None = None) -> dict:
    """modern 요청 헤더 — 버전·메서드·이름(필요하면 Base64 표기) + 도구 스키마의 x-mcp-header 미러링."""
    h = {"MCP-Protocol-Version": MODERN_VERSIONS[0], "Mcp-Method": method}
    name = (params or {}).get(NAMED.get(method, ""))
    if isinstance(name, str):
        h["Mcp-Name"] = inbound.encode_header_value(name)
    if method == "tools/call" and isinstance(schema, dict) and inbound.find_invalid_x_mcp_header(schema) is None:
        h.update(inbound.mcp_param_headers(inbound.x_mcp_header_map(schema), (params or {}).get("arguments") or {}))
    return h


def request_meta(caps: dict, token=None) -> dict:
    return {K_VERSION: MODERN_VERSIONS[0], K_CAPS: caps, K_CLIENT: CLIENT_INFO, **({"progressToken": token} if token else {})}


def strip(result: dict) -> dict:
    """modern 업스트림 결과 → 세대 중립 결과 (resultType·캐시 힌트·업스트림 serverInfo 제거, 하류 세대에 맞춰 다시 붙인다)."""
    out = {k: v for k, v in (result or {}).items() if k not in ("resultType", "ttlMs", "cacheScope")}
    meta = {k: v for k, v in (out.pop("_meta", None) or {}).items() if k != K_SERVER}
    return {**out, **({"_meta": meta} if meta else {})}


def client_caps(params: dict) -> dict:
    caps = ((params or {}).get("_meta") or {}).get(K_CAPS)
    return caps if isinstance(caps, dict) else {}


def discover() -> dict:
    return {"resultType": "complete", "supportedVersions": list(SUPPORTED),
            "capabilities": {"tools": {"listChanged": True}, "resources": {"listChanged": True, "subscribe": True},
                             "prompts": {"listChanged": True}, "extensions": {"io.modelcontextprotocol/tasks": {}}},
            "_meta": {K_SERVER: SERVER_INFO},
            "instructions": "AgentNexus 게이트웨이 — 정책·승인·토큰 교환·소독을 거쳐 등록된 MCP 서버의 도구를 제공합니다.",
            "ttlMs": 3_600_000, "cacheScope": "public"}


def complete(method: str, result: dict) -> dict:
    out = {**(result or {}), "resultType": "complete"}
    out["_meta"] = {**(out.get("_meta") or {}), K_SERVER: SERVER_INFO}
    if method in CACHEABLE:  # 목록은 호출 주체의 정책으로 걸러지므로 사설 캐시만 (SEP-2549)
        out.setdefault("ttlMs", 60_000)
        out.setdefault("cacheScope", "private")
    return out


# ───────────────────────── MRTR 상태 ─────────────────────────
def _digest(method: str, params: dict) -> str:
    salient = {k: v for k, v in (params or {}).items() if k not in ("_meta", "inputResponses", "requestState")}
    return hashlib.sha256(json.dumps([method, salient], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:32]


def seal(state_id: str, sub: str, server_id: str, method: str, params: dict) -> str:
    from app.core.keys import fernet
    return fernet().encrypt(json.dumps({"s": state_id, "u": sub, "v": server_id, "m": method, "d": _digest(method, params),
                                        "x": int(time.time()) + STATE_TTL_S}).encode()).decode()


def unseal(token: str, sub: str, server_id: str, method: str, params: dict) -> str:
    from cryptography.fernet import InvalidToken

    from app.core.keys import fernet
    try:
        st = json.loads(fernet().decrypt(token.encode(), ttl=STATE_TTL_S))
    except (InvalidToken, ValueError) as exc:
        raise ModernError(-32602, "Invalid requestState — 변조되었거나 만료되었습니다.") from exc
    if st.get("u") != sub or st.get("v") != server_id:
        raise ModernError(-32602, "Invalid requestState — 다른 주체·서버의 상태입니다.")
    if st.get("m") != method or st.get("d") != _digest(method, params):
        raise ModernError(-32602, "Invalid requestState — 원래 요청과 다릅니다.")
    if int(st.get("x") or 0) < time.time():
        raise ModernError(-32602, "Invalid requestState — 만료되었습니다.")
    return st["s"]


def _final_id(state_id: str) -> str:
    return f"{state_id}:final"


def _store_final(state_id: str, server_id: str, response: dict) -> None:
    with SessionLocal() as db:
        db.merge(McpRelay(id=_final_id(state_id), session_id=state_id, server_id=server_id, method="mrtr/result", params={},
                          response=response, answered_at=utcnow()))
        db.commit()


def _poll(state_id: str) -> tuple[str, object]:
    with SessionLocal() as db:
        fin = db.get(McpRelay, _final_id(state_id))
        if fin is not None:
            return "final", fin.response
        rows = [r for r in db.query(McpRelay).filter(McpRelay.session_id == state_id, McpRelay.method != "mrtr/result")
                if r.response is None]
        return ("input", {r.id: {"method": r.method, "params": r.params} for r in rows}) if rows else ("wait", None)


def _answer(state_id: str, responses: dict) -> int:
    n = 0
    with SessionLocal() as db:
        for rid, res in (responses or {}).items():
            row = db.get(McpRelay, str(rid))
            if row is None or row.session_id != state_id or row.response is not None:
                continue  # 모르는·이미 답한 키는 무시 (MRTR 오류 처리 규칙)
            row.response = {"result": res}
            row.answered_at = utcnow()
            n += 1
        db.commit()
    return n


async def _wait(state_id: str, rid, sealed: str, task: asyncio.Task | None) -> dict:
    deadline = time.monotonic() + WAIT_S
    while True:
        kind, data = await run_in_threadpool(_poll, state_id)
        if kind == "final":
            return {"__final__": data}
        if kind == "input":
            return {"resultType": "input_required", "inputRequests": data, "requestState": sealed}
        if task is not None and task.done() and task.exception() is not None:
            raise task.exception()
        if time.monotonic() > deadline:  # 아직 처리 중 — requestState 만 주면 클라이언트가 곧바로 다시 시도한다
            return {"resultType": "input_required", "requestState": sealed}
        await asyncio.sleep(0.2)


PROGRESS_METHOD = "notifications/progress"
PROGRESS_POLL_S = 0.3


def _sink(state_id: str, server_id: str):
    """업스트림 progress → 이 워커에서 기다리는 요청의 큐, 없으면(재시도가 다른 워커로 갔거나 입력 대기 중) 공유 행으로 (D18-②)."""
    async def put(p: dict) -> None:
        q = _PROGRESS.get(state_id)
        if q is not None:
            await q.put(p)
        else:
            await run_in_threadpool(_store_progress, state_id, server_id, p)
    return put


def _store_progress(state_id: str, server_id: str, p: dict) -> None:
    with SessionLocal() as db:  # response 를 채워 두어 입력 요청(_poll)으로 보이지 않게
        db.add(McpRelay(id=new_id("prg"), session_id=state_id, server_id=server_id, method=PROGRESS_METHOD, params=p,
                        response={}, answered_at=utcnow()))
        db.commit()


def _take_progress(state_id: str) -> list[dict]:
    """쌓인 공유 progress 를 시간 순으로 꺼낸다(꺼낸 행은 지운다 — 한 번만 전달)."""
    with SessionLocal() as db:
        rows = db.query(McpRelay).filter(McpRelay.session_id == state_id, McpRelay.method == PROGRESS_METHOD) \
            .order_by(McpRelay.created_at, McpRelay.id).all()
        out = [dict(r.params or {}) for r in rows]
        for r in rows:
            db.delete(r)
        db.commit()
    return out


async def _relay_shared(state_id: str, queue: asyncio.Queue) -> None:
    """다른 워커가 돌리는 호출의 progress — 공유 행을 주기적으로 꺼내 이 요청의 큐로."""
    while True:
        for p in await run_in_threadpool(_take_progress, state_id):
            await queue.put(p)
        await asyncio.sleep(PROGRESS_POLL_S)


def detach(state_id: str | None, queue: asyncio.Queue) -> None:
    if state_id and _PROGRESS.get(state_id) is queue:
        _PROGRESS.pop(state_id, None)


async def start(server_id: str, ctx, method: str, params: dict, rid, run, progress: asyncio.Queue | None = None) -> dict:
    """MRTR 가능한 요청 — 업스트림 호출을 이 워커의 작업으로 돌리고, 입력 요청이 생기면 InputRequiredResult 로 응답.

    progress 큐가 있으면 업스트림 progress 를 그 큐로 (재시도 요청이 같은 워커로 오면 그 요청의 큐로 옮겨 단다)."""
    from app.services import mcp_consent
    state_id = new_id("mrtr")
    sealed = seal(state_id, ctx.principal.sub, server_id, method, params)
    ctx.relay = mcp_consent.Relay(state_id, server_id, client_caps(params), None)
    if progress is not None:
        _PROGRESS[state_id] = progress
        ctx.progress = _sink(state_id, server_id)
        ctx.state_id = state_id

    async def work():
        try:
            res = await run(server_id, ctx, method, {k: v for k, v in params.items() if k not in ("inputResponses", "requestState")})
            await run_in_threadpool(_store_final, state_id, server_id, {"result": res})
        except Exception as exc:  # noqa: BLE001 — 오류도 최종 응답으로 공유
            err = exc.body(None)["error"] if hasattr(exc, "body") else {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}
            await run_in_threadpool(_store_final, state_id, server_id, {"error": err})
        finally:
            _RUNNING.pop(state_id, None)
            _PROGRESS.pop(state_id, None)
    task = asyncio.create_task(work())
    _RUNNING[state_id] = task
    return await _wait(state_id, rid, sealed, task)


async def resume(server_id: str, ctx, method: str, params: dict, rid, progress: asyncio.Queue | None = None) -> dict:
    """재시도 — requestState 검증 → inputResponses 를 대기 중인 업스트림 요청에 연결 → 다음 입력 요청 또는 최종 결과."""
    state_id = unseal(str(params.get("requestState")), ctx.principal.sub, server_id, method, params)
    relay = None
    if progress is not None:
        for p in await run_in_threadpool(_take_progress, state_id):  # 입력을 기다리는 동안 쌓인 progress 부터
            await progress.put(p)
        if state_id in _RUNNING:
            _PROGRESS[state_id] = progress
            ctx.state_id = state_id
        else:  # 호출은 다른 워커에서 돈다 — 공유 행으로 이어 받는다
            relay = asyncio.create_task(_relay_shared(state_id, progress))
    try:
        await run_in_threadpool(_answer, state_id, params.get("inputResponses") or {})
        sealed = seal(state_id, ctx.principal.sub, server_id, method, params)
        return await _wait(state_id, rid, sealed, _RUNNING.get(state_id))
    finally:
        if relay is not None:
            relay.cancel()
            for p in await run_in_threadpool(_take_progress, state_id):  # 결과 직전에 들어온 것까지
                await progress.put(p)


# ───────────────────────── subscriptions/listen ─────────────────────────
MAX_RESOURCE_SUBS = 20


def listen_filter(params: dict) -> tuple[dict, list, list]:
    """요청 필터 → (승인할 목록 변경 종류, 요청한 리소스 URI, 요청한 taskIds). 모르는 키는 승인하지 않는다.
    URI·작업은 API 가 읽기 가능·소유 여부를 확인해 승인 부분집합을 정한다."""
    raw = (params or {}).get("notifications")
    if not isinstance(raw, dict):
        raise ModernError(-32602, "Invalid params: notifications 필터 객체가 필요합니다.")
    lists = []
    for key in ("resourceSubscriptions", "taskIds"):
        v = raw.get(key) or []
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ModernError(-32602, f"Invalid params: notifications.{key} 는 문자열 배열이어야 합니다.")
        lists.append(list(dict.fromkeys(v)))
    return {k: True for k in LISTEN_KINDS if raw.get(k) is True}, lists[0][:MAX_RESOURCE_SUBS], lists[1]


def sse(msg: dict) -> str:
    return f"event: message\ndata: {json.dumps(msg, ensure_ascii=False, default=str)}\n\n"


def _digest_of(items) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


_FP_CACHE: dict[tuple, tuple[float, str]] = {}  # (서버, 주체, 대상) → (시각, 지문) — 같은 주체의 여러 구독이 한 번만 조회 (D18-④)


async def _digest_cached(key: tuple, fetch) -> str:
    """지문 공유 — 폴링 주기보다 짧게(0.8배) 캐시. 주체별로만 공유한다(정책·위임 토큰에 따라 목록이 다르다)."""
    ttl = LISTEN_POLL_S * 0.8
    hit = _FP_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    digest = _digest_of(await fetch())
    if len(_FP_CACHE) > 4096:
        _FP_CACHE.clear()
    _FP_CACHE[key] = (time.monotonic(), digest)
    return digest


async def _fingerprints(server_id: str, ctx, honored: dict) -> dict:
    """구독 대상별 (지문, 바뀌면 보낼 알림 params) — 도구는 정책(도구 규칙·프로필)을 거친 목록, 프롬프트·리소스 목록은 업스트림,
    리소스 구독은 resources/read 내용, 작업은 DetailedTask(상태·입력 요청·결과)."""
    from app.services import mcp_gateway as gw
    from app.services import mcp_tasks
    out: dict[str, tuple[str, dict]] = {}

    def gone(exc) -> bool:
        return exc.code in (gw.FORBIDDEN, gw.NOT_FOUND) and exc.http_status in (403, 404)
    for k in LISTEN_KINDS:
        if honored.get(k) is not True:
            continue
        async def fetch(k=k):
            if k == "toolsListChanged":
                return await gw.list_tools(server_id, ctx)
            if k == "promptsListChanged":
                return (await gw.generic(server_id, ctx, "prompts/list", {})).get("prompts")
            return (await gw.generic(server_id, ctx, "resources/list", {})).get("resources")
        try:
            digest = await _digest_cached((server_id, ctx.principal.sub, k), fetch)
        except gw.RpcError as exc:
            if gone(exc):
                raise  # 서버 비활성·삭제 → 구독 종료
            continue  # 일시 장애 — 이전 지문 유지(알림 없음)
        out[k] = (digest, {})
    for uri in honored.get("resourceSubscriptions") or []:
        async def read(uri=uri):
            return (await gw.generic(server_id, ctx, "resources/read", {"uri": uri})).get("contents")
        try:
            digest = await _digest_cached((server_id, ctx.principal.sub, f"res:{uri}"), read)
        except gw.RpcError as exc:
            if gone(exc):
                raise
            continue
        out[f"res:{uri}"] = (digest, {"uri": uri})
    for tid in honored.get("taskIds") or []:
        view = await run_in_threadpool(mcp_tasks.peek, server_id, ctx.principal.sub, tid)
        if view is not None:
            out[f"task:{tid}"] = (_digest_of(view), view)
    return out


def _notification(key: str) -> str:
    if key.startswith("res:"):
        return "notifications/resources/updated"
    if key.startswith("task:"):
        return "notifications/tasks"
    return LISTEN_KINDS[key]


async def listen_events(server_id: str, ctx, rid, honored: dict, alive, holder: str | None = None):
    """승인 알림 → (변경 시) 목록 변경 알림 … → LISTEN_MAX_S 뒤 정상 종료 응답. 모든 메시지에 subscriptionId(=요청 id)."""
    from app.services import mcp_gateway as gw
    tag = {K_SUB: rid}
    sub = ctx.principal.sub
    LISTENS[sub] = LISTENS.get(sub, 0) + 1
    try:
        yield sse({"jsonrpc": "2.0", "method": "notifications/subscriptions/acknowledged",
                   "params": {"_meta": tag, "notifications": honored}})
        last = await _fingerprints(server_id, ctx, honored)
        until, quiet = time.monotonic() + LISTEN_MAX_S, 0.0
        while time.monotonic() < until:
            await asyncio.sleep(LISTEN_POLL_S)
            if not await alive():  # 토큰 만료·폐기 → 구독도 끝
                yield sse(gw.RpcError(gw.FORBIDDEN, "인증이 만료되었거나 폐기되어 구독을 종료합니다.").body(rid))
                return
            try:
                now = await _fingerprints(server_id, ctx, honored)
            except gw.RpcError as exc:
                yield sse(exc.body(rid))
                return
            changed = [k for k in now if k in last and now[k][0] != last[k][0]]
            last.update(now)
            for k in changed:
                yield sse({"jsonrpc": "2.0", "method": _notification(k), "params": {**now[k][1], "_meta": tag}})
            quiet = 0.0 if changed else quiet + LISTEN_POLL_S
            if quiet >= LISTEN_PING_S:
                quiet = 0.0
                yield ": ping\n\n"
        yield sse({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "_meta": tag}})
    finally:
        LISTENS[sub] = max(LISTENS.get(sub, 1) - 1, 0)
        # 동기 호출 — 연결이 끊기면 anyio 취소 범위가 finally 안의 await 도 다시 취소해 반납이 빠진다
        listen_release(sub, holder)
