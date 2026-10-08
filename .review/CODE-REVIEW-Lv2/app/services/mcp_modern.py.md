> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 19. app/services/mcp_modern.py
**파일 요약 / File Summary**
- 역할 / Role: MCP 2026-07-28(stateless) 하류 수용 — 요청별 버전·헤더/본문 일치 검증, `server/discover`, 결과 `resultType`·캐시 힌트, MRTR(InputRequiredResult ↔ inputResponses/requestState) 상태 봉인(Fernet)·워커 간 공유(mcp_relays), progress 중계, `subscriptions/listen` 알림 스트림, 게이트웨이 → modern 업스트림 요청 헤더·`_meta` 생성.
- 진입점 거리 / Distance: 2 (`app/api/mcp.py:132-224` → 이 모듈)
- LOC: 415
- 주요 export: `MODERN_VERSIONS`, `SUPPORTED`, `ModernError`, `validate`, `check_params`, `progress_token`, `ProgressRelay`, `request_headers`, `request_meta`, `strip`, `client_caps`, `discover`, `complete`, `seal`/`unseal`, `start`/`resume`, `detach`, `listen_filter`, `listen_events`, `sse`, `LISTENS`·`MAX_LISTENS`, `_answer`(외부에서 사용 — `app/services/mcp_tasks.py:229`)
- 의존(내부): `app.core.{config,ids,keys}`, `app.db`, `app.models.McpRelay`, (지연) `app.services.{mcp_consent,mcp_gateway,mcp_tasks}`
- 외부 패키지: mcp(`mcp.shared.inbound` — 헤더 검증 규칙), cryptography(Fernet), starlette
- 부수효과 / Side effects: DB 쓰기(mcp_relays), 워커 로컬 전역 상태 `LISTENS`·`_RUNNING`·`_PROGRESS`, 백그라운드 Task
- 동시성 / Concurrency: asyncio Task·Queue, DB 폴링(0.2초)
- 테스트 커버리지 흔적: `test/test_mcp_modern.py`, `test/test_mcp_modern_ext.py`, `test/test_mcp_tasks.py`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/mcp_modern.py lines=1-25
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
```
**분석 / Analysis (Level 2)**
- L1-11: 이 모듈이 구현하는 사양 요소 목록 — 특히 L8 "requestState 는 Fernet(AEAD) — 주체·메서드·인자 요약·만료(10분)를 담아 재사용 거부" 가 보안 설계의 핵심.
- L19 `from mcp.shared import inbound`: SDK 의 헤더 검증 규칙을 직접 재사용 — 자체 구현 대신 사양 구현체를 따른다(중개자 간 판단 불일치 방지).

```python path=app/services/mcp_modern.py lines=27-55
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
MAX_LISTENS = 5        # 주체별 동시 구독 (워커 단위)
LISTENS: dict[str, int] = {}
STATE_TTL_S = 600
WAIT_S = 25.0
_RUNNING: dict[str, asyncio.Task] = {}
_PROGRESS: dict[str, asyncio.Queue] = {}  # state_id → 지금 이 호출을 기다리는 요청의 progress 큐 (워커 로컬)

```
**분석 / Analysis (Level 2)**
- L27-28: modern 버전과 전체 지원 버전(modern + legacy 3종).
- L29-33: `_meta` 키 상수(사양의 네임스페이스 키).
- L34: 오류 코드 — HeaderMismatch(-32020)·UnsupportedProtocolVersion(-32022).
- L35-36 `NAMED`: `Mcp-Name` 헤더와 대조할 본문 필드(도구 이름·URI·taskId).
- L37-39: MRTR 대상 메서드, 캐시 가능한 메서드, modern 에서 제거된 메서드(ping·initialize 등).
- L43-45: listen 필터 키 → 알림 메서드.
- L46-49: 폴링 5초, 핑 15초, 최대 15분 후 정상 종료, 주체별 동시 구독 5개(워커 단위).
- L50-54: 워커 로컬 상태 — 구독 수, 실행 중 MRTR 작업, progress 큐.
**짚어볼 점 / Watch-outs**
- L49-50 동시 구독 상한이 **워커 단위**라 워커 N 개면 실제 상한은 5N. 다중 파드에서는 더 커진다(문서 COMPLIANCE "주체당 5개" 와 차이).

```python path=app/services/mcp_modern.py lines=57-64
class ModernError(Exception):
    def __init__(self, code: int, message: str, http_status: int = 400, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.http_status, self.data = code, message, http_status, data

    def body(self, rid) -> dict:
        err = {"code": self.code, "message": self.message, **({"data": self.data} if self.data else {})}
        return {"jsonrpc": "2.0", "id": rid, "error": err}
```
**분석 / Analysis (Level 2)**
- L57-64 `ModernError`: JSON-RPC 오류 + HTTP 상태(기본 400 — modern 은 오류를 HTTP 상태로도 표현).

```python path=app/services/mcp_modern.py lines=67-80
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
```
**분석 / Analysis (Level 2)**
- L67-70 `requested_version`: `_meta` 의 버전 키(문자열만).
- L73-80 `_header_value`: `=?base64?…?=` 인코딩(비 ASCII 값을 헤더로 보내는 Streamable HTTP 규칙)을 풀어 비교. 디코드 실패(`UnicodeDecodeError` 는 `ValueError` 하위)는 `None` → 불일치로 처리.

```python path=app/services/mcp_modern.py lines=83-99
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
```
**분석 / Analysis (Level 2)**
- L85-86: 지원하지 않는 버전 → -32022 + 지원 목록.
- L87-89: 라우팅 헤더 **중복** 거부 — 앞 값·끝 값을 읽는 중개자가 서로 다르게 라우팅하는 공격(헤더 스머글링) 방지.
- L90-99: `MCP-Protocol-Version`·`Mcp-Method`·`Mcp-Name` 이 본문과 같아야 한다. 중개자(프록시·WAF)가 헤더만 보고 판단해도 본문과 어긋나지 않게 하는 사양 요구.
**짚어볼 점 / Watch-outs**
- L91 `hv != version` — 대소문자·공백 차이도 불일치로 처리(엄격, 사양 의도와 일치).

```python path=app/services/mcp_modern.py lines=102-130
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
```
**분석 / Analysis (Level 2)**
- L102-108 `check_params`: 도구 스키마의 `x-mcp-header` 속성 ↔ `Mcp-Param-*` 헤더 일치 검증(SDK 규칙).
- L111-113 `progress_token`: 문자열·정수만(불리언은 int 의 하위 클래스라 명시 제외 — 세심한 처리).
- L116-130 `ProgressRelay`: 업스트림 progress 를 하류 토큰으로 다시 태그하고 **증가하지 않는 값은 버린다**(MRTR 재시도로 업스트림이 같은 진행률을 다시 보내는 경우 — 사양의 "단조 증가" 요구 준수).

```python path=app/services/mcp_modern.py lines=133-158
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
```
**분석 / Analysis (Level 2)**
- L134-142 `request_headers`: 게이트웨이 → modern 업스트림 헤더 — 버전·메서드·이름(필요 시 base64 표기), 도구 스키마가 유효하면 `x-mcp-header` 를 `Mcp-Param-*` 로 미러링.
- L145-146 `request_meta`: 요청 `_meta`(버전·클라이언트 능력·정보·progressToken).
- L149-153 `strip`: 업스트림 결과에서 modern 전용 필드(`resultType`·캐시 힌트·업스트림 serverInfo)를 제거 → 하류 세대에 맞게 다시 붙인다.
- L156-158 `client_caps`.

```python path=app/services/mcp_modern.py lines=161-176
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
```
**분석 / Analysis (Level 2)**
- L161-167 `discover`: `server/discover` 결과 — 지원 버전·능력(tasks 확장 포함)·안내문, 1시간 public 캐시.
- L170-176 `complete`: 모든 결과에 `resultType: complete` + serverInfo. 목록·읽기 결과는 **private** 캐시 60초 — 호출 주체의 정책으로 걸러진 결과라 공유 캐시가 저장하면 안 된다(SEP-2549, 주석이 근거 명시).

```python path=app/services/mcp_modern.py lines=179-205
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
```
**분석 / Analysis (Level 2)**
- L180-182 `_digest`: 메서드 + 핵심 params(재시도용 필드 제외)의 SHA-256 앞 32자 — 같은 요청인지 판별.
- L185-188 `seal`: (상태 id·주체·서버·메서드·요약·만료)를 Fernet 으로 암호화+인증(AEAD) — 클라이언트가 읽거나 바꿀 수 없다.
- L191-205 `unseal`: 복호화(Fernet `ttl` 로 1차 만료 검사) → 주체·서버 일치 → 메서드·요약 일치 → 만료 재확인 → 상태 id.
**짚어볼 점 / Watch-outs**
- L196 Fernet `ttl` 과 L203 자체 만료(`x`)가 이중 — 의미는 같다(방어적 중복).
- 같은 주체가 같은 요청으로 상태를 **여러 번** 재사용(재전송)하는 것은 막지 않는다 — `_answer` 가 "이미 답한 키는 무시" 하므로(L234-235) 부작용은 제한된다.

```python path=app/services/mcp_modern.py lines=208-240
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
```
**분석 / Analysis (Level 2)**
- L208-216: 최종 결과를 `{state_id}:final` 행으로 저장(`merge` = upsert) — 다른 워커가 재시도를 받아도 결과를 찾을 수 있다.
- L219-226 `_poll`: 최종 결과 있으면 final, 미응답 입력 요청이 있으면 input, 아니면 wait.
- L229-240 `_answer`: 클라이언트 응답을 해당 상태의 미응답 요청에 연결(다른 상태 id·이미 답한 키는 무시 — 사양의 오류 처리 규칙).
**짚어볼 점 / Watch-outs**
- L224-225 SQL 로 `session_id` 만 거르고 `response is None` 은 Python 에서 거른다 — 응답된 행이 많으면 불필요한 로드(`McpRelay.response.is_(None)` 을 쿼리에 넣는 편이 낫다, `mcp_tasks._pending` 은 그렇게 한다).
- mcp_relays 행은 삭제되지 않는다 — 보존 정책 정리 대상이어야 한다(동시 작업이 `retention.py` 에 반영 중 — 본 리뷰 미선정, `test/test_review_fixes.py` docstring BUG-01).

```python path=app/services/mcp_modern.py lines=243-268
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


def _sink(state_id: str):
    async def put(p: dict) -> None:
        q = _PROGRESS.get(state_id)
        if q is not None:
            await q.put(p)
    return put


def detach(state_id: str | None, queue: asyncio.Queue) -> None:
    if state_id and _PROGRESS.get(state_id) is queue:
        _PROGRESS.pop(state_id, None)
```
**분석 / Analysis (Level 2)**
- L243-255 `_wait`: 0.2초 간격으로 DB 폴링 — final 이면 반환, 입력 요청이 생기면 InputRequiredResult(+봉인 상태), 작업이 예외로 끝났으면 재발생, 25초가 지나면 requestState 만 돌려 클라이언트가 곧 재시도하게 한다(긴 HTTP 요청을 피하는 롱폴링).
- L258-263 `_sink`: progress 를 지금 기다리는 요청의 큐로(재시도가 같은 워커면 큐가 교체된다).
- L266-268 `detach`: 요청이 끝나면 자기 큐만 떼어 낸다(다른 요청이 단 큐를 지우지 않게 identity 비교).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L251-252 `task.exception()` 은 작업이 **취소**된 경우 `CancelledError` 를 던진다 — 클라이언트 연결 종료로 작업이 취소되면 `_wait` 가 CancelledError 로 끝난다(상위에서 처리되지만 의도인지 주석 필요).
- 대기 요청 하나당 초당 5회 DB 조회 — 동시 대기가 많으면 DB 부하(이 프로젝트 전반의 "DB 폴링으로 워커 간 공유" 설계의 비용).

```python path=app/services/mcp_modern.py lines=271-307
async def start(server_id: str, ctx, method: str, params: dict, rid, run, progress: asyncio.Queue | None = None) -> dict:
    """MRTR 가능한 요청 — 업스트림 호출을 이 워커의 작업으로 돌리고, 입력 요청이 생기면 InputRequiredResult 로 응답.

    progress 큐가 있으면 업스트림 progress 를 그 큐로 (재시도 요청이 같은 워커로 오면 그 요청의 큐로 옮겨 단다)."""
    from app.services import mcp_consent
    state_id = new_id("mrtr")
    sealed = seal(state_id, ctx.principal.sub, server_id, method, params)
    ctx.relay = mcp_consent.Relay(state_id, server_id, client_caps(params), None)
    if progress is not None:
        _PROGRESS[state_id] = progress
        ctx.progress = _sink(state_id)
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
    if progress is not None and state_id in _RUNNING:
        _PROGRESS[state_id] = progress
        ctx.state_id = state_id
    await run_in_threadpool(_answer, state_id, params.get("inputResponses") or {})
    sealed = seal(state_id, ctx.principal.sub, server_id, method, params)
    return await _wait(state_id, rid, sealed, _RUNNING.get(state_id))
```
**분석 / Analysis (Level 2)**
- L271-296 `start`: 새 상태 id·봉인 → 이 요청의 중계(`Relay`, 큐 없음 = MRTR 모드)를 ctx 에 설치 → 업스트림 호출을 **백그라운드 Task** 로 돌리고(`work`) 결과·오류를 최종 행으로 공유 → `_wait` 로 첫 응답(최종 또는 InputRequired).
- L288-290 `work` 의 `except Exception` 은 오류를 **최종 응답으로 공유**하는 변환이다(삼킴 아님).
- L299-307 `resume`: 상태 검증 → (같은 워커면) progress 큐 교체 → 응답 연결 → 새 봉인 → 대기.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L294-295 Task 를 `_RUNNING` 에 보관해 GC 를 막는다(좋음). 하지만 클라이언트가 재시도하지 않으면 작업은 `Relay.request` 의 동의 대기 시간(기본 60초)까지 살아 있다 — 작업 수 상한이 없다.
- 워커가 재시작되면 `_RUNNING` 작업이 사라지고 최종 행이 영원히 생기지 않는다 — 재시도는 25초마다 requestState 만 받다가 10분 뒤 만료된다(명시적 실패 신호 없음).

```python path=app/services/mcp_modern.py lines=310-330
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
```
**분석 / Analysis (Level 2)**
- L314-326 `listen_filter`: 필터 객체 검증 — 리소스 URI·taskId 는 문자열 배열(중복 제거 `dict.fromkeys` — 순서 보존), 알 수 없는 키는 승인하지 않는다(허용 목록), 리소스 구독 20개 상한.
- L329-330 `sse`: SSE 메시지 포맷(`event: message` + `data:` 한 줄 JSON).
**짚어볼 점 / Watch-outs**
- L326 `taskIds` 는 여기서 상한이 없고 API 의 `mcp_tasks.owned` 가 50개로 자른다(`app/services/mcp_tasks.py:138`) — 상한이 두 곳에 흩어져 있다.

```python path=app/services/mcp_modern.py lines=333-381
def _digest_of(items) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


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
        try:
            if k == "toolsListChanged":
                items = await gw.list_tools(server_id, ctx)
            elif k == "promptsListChanged":
                items = (await gw.generic(server_id, ctx, "prompts/list", {})).get("prompts")
            else:
                items = (await gw.generic(server_id, ctx, "resources/list", {})).get("resources")
        except gw.RpcError as exc:
            if gone(exc):
                raise  # 서버 비활성·삭제 → 구독 종료
            continue  # 일시 장애 — 이전 지문 유지(알림 없음)
        out[k] = (_digest_of(items), {})
    for uri in honored.get("resourceSubscriptions") or []:
        try:
            res = await gw.generic(server_id, ctx, "resources/read", {"uri": uri})
        except gw.RpcError as exc:
            if gone(exc):
                raise
            continue
        out[f"res:{uri}"] = (_digest_of(res.get("contents")), {"uri": uri})
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
```
**분석 / Analysis (Level 2)**
- L333-334 `_digest_of`: 목록·내용의 지문(정렬된 JSON SHA-256).
- L337-373 `_fingerprints`: 구독 종류별 현재 지문 — 도구는 **정책을 거친 목록**(사용자마다 다를 수 있음), 프롬프트·리소스 목록, 구독 리소스 내용, 작업 상태. 서버 비활성·삭제(403/404)면 예외로 구독 종료, 일시 장애면 이전 지문 유지.
- L376-381 `_notification`: 키 → 알림 메서드.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 부하 증폭] 구독 하나가 5초마다 `list_tools`(TTL 만료 시 `refresh_tools`)·`prompts/list`·`resources/list`·리소스 최대 20개 `resources/read` 를 호출한다. 주체당 5개 × 워커 수 × 사용자 수만큼 업스트림 호출이 늘어난다 — 업스트림 알림(notifications/*/list_changed)을 받아 쓰거나, 서버 단위로 지문을 공유·캐시해야 한다.

```python path=app/services/mcp_modern.py lines=384-415
async def listen_events(server_id: str, ctx, rid, honored: dict, alive):
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
```
**분석 / Analysis (Level 2)**
- L384-415 `listen_events`(async generator): 승인 알림 → 5초마다 생존 확인(토큰 재검증) → 지문 비교 → 바뀐 키마다 알림 → 15초 조용하면 SSE 주석 핑 → 15분 뒤 정상 종료 응답. 모든 메시지에 `subscriptionId`.
- L389 / L414-415: 구독 수 증가/감소 — `finally` 에서 감소하므로 클라이언트가 끊어도(제너레이터 close) 정리된다.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 경쟁] 상한 확인(`app/api/mcp.py:233`)과 증가(L389)가 분리돼 있어 동시 요청이 상한을 넘길 수 있다(TOCTOU). 증가는 제너레이터가 **처음 실행될 때**(응답 스트리밍 시작) 일어나므로 간격이 더 벌어진다.
- L405 `changed` 는 이전에도 있던 키만 — 새로 생긴 키(이전 장애로 지문이 없던 것)는 알림 없이 기준값이 된다(합리적).

## 파일 평가 / File Assessment
- 강점: (1) 사양 요구(헤더·본문 일치, 중복 라우팅 헤더 거부, private 캐시, 단조 progress)를 SDK 규칙 재사용으로 정확히 구현. (2) requestState 를 AEAD 로 봉인하고 주체·서버·메서드·인자 요약에 바인딩. (3) 워커 간 공유를 DB 로 해결해 재시도가 다른 워커로 가도 동작.
- 위험: (1) [중간] 구독 폴링의 업스트림 부하 증폭. (2) [낮음] 구독 상한의 워커 단위·TOCTOU. (3) [낮음] 워커 재시작 시 MRTR 작업 유실의 명시적 실패 신호 부재, 대기 요청당 DB 폴링 비용.
- 개선 권고(영향순): (1) 서버 단위 지문 공유 캐시 또는 업스트림 list_changed 알림 사용. (2) 구독 수를 DB/Valkey 원자 카운터로. (3) 최종 행이 없고 작업도 없으면 즉시 오류를 돌려주는 경로 추가.
- 재읽기 권고 레벨: 3 — 사양 규칙 구현이 대부분이라 블록 단위로 충분하다.
