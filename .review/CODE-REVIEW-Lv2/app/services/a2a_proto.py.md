> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md)

### 23. app/services/a2a_proto.py
**파일 요약 / File Summary**
- 역할 / Role: A2A 프로토콜 코덱 — 내부 정준형(0.3 JSON: `kind` 기반·소문자 상태) ↔ 1.0(PascalCase 메서드·`TASK_STATE_*`·`ROLE_*`·kind 없는 파트) 변환, 메서드 매핑, 카드 해석·검증·해시·이중(0.3+1.0) 카드 생성.
- 진입점 거리 / Distance: 1 (`app/builtin/a2a_agent.py:23`) · 2 (`app/api/a2a.py:25`)
- LOC: 345
- 주요 export: `V03`·`V10`, `METHODS`, `op_of`, `version_of`, `method_for`, `state_to_v10`/`state_from`, `part_from`/`part_to_v10`, `message_from`/`message_to_v10`, `task_from`/`task_to_v10`, `result_from`/`result_to`, `event_from`/`event_to`, `params_to`, `push_config_of`, `text_of`, `card_info`, `validate_card`, `card_hash`, `build_card`, 오류 코드 상수
- 의존(내부): 없음(순수 함수 모듈)
- 외부 패키지: 표준 `hashlib`·`json`·`uuid`
- 부수효과 / Side effects: 없음(무작위 id 생성만)
- 동시성 / Concurrency: none
- 테스트 커버리지 흔적: `test/test_a2a.py`(0.3↔1.0 변환·레거시 에이전트), `test/test_a2a_v10.py`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/services/a2a_proto.py lines=1-37
"""A2A 프로토콜 코덱 — 0.3(PRD 9.2: message/send·stream, tasks/get·cancel) 과 1.0(SendMessage·GetTask…) 동시 수용.

내부 정준형은 0.3 JSON(kind 기반, 소문자 상태)이다. 1.0 은 경계에서 변환한다.
- 상태: submitted|working|input-required|completed|failed|canceled|rejected|auth-required ↔ TASK_STATE_*
- 역할: user|agent ↔ ROLE_USER|ROLE_AGENT · Part: {kind:text,text} ↔ {text} · {kind:data,data} ↔ {data}
  · {kind:file,file:{uri|bytes,name,mimeType}} ↔ {url|raw,filename,mediaType}
- 카드: 0.3 필드(url·protocolVersion·preferredTransport)와 1.0 supportedInterfaces 를 함께 게시(이중 카드)
"""
import hashlib
import json
import uuid

V03, V10 = "0.3", "1.0"
METHODS = {
    "message/send": "send", "SendMessage": "send",
    "message/stream": "stream", "SendStreamingMessage": "stream",
    "tasks/get": "get", "GetTask": "get",
    "tasks/cancel": "cancel", "CancelTask": "cancel",
    "tasks/list": "list", "ListTasks": "list",
    "tasks/pushNotificationConfig/set": "push_set", "CreateTaskPushNotificationConfig": "push_set",
    "tasks/pushNotificationConfig/get": "push_get", "GetTaskPushNotificationConfig": "push_get",
    "tasks/resubscribe": "resubscribe", "SubscribeToTask": "resubscribe",
    "agent/getAuthenticatedExtendedCard": "card", "GetExtendedAgentCard": "card",
}
V10_METHOD = {"send": "SendMessage", "stream": "SendStreamingMessage", "get": "GetTask", "cancel": "CancelTask",
              "list": "ListTasks", "push_set": "CreateTaskPushNotificationConfig",
              "push_get": "GetTaskPushNotificationConfig", "resubscribe": "SubscribeToTask",
              "card": "GetExtendedAgentCard"}
V03_METHOD = {"send": "message/send", "stream": "message/stream", "get": "tasks/get", "cancel": "tasks/cancel",
              "list": "tasks/list", "push_set": "tasks/pushNotificationConfig/set",
              "push_get": "tasks/pushNotificationConfig/get", "resubscribe": "tasks/resubscribe",
              "card": "agent/getAuthenticatedExtendedCard"}
TERMINAL = {"completed", "failed", "canceled", "rejected"}
STATES = ("submitted", "working", "input-required", "completed", "failed", "canceled", "rejected", "auth-required")

# A2A JSON-RPC 오류 코드
TASK_NOT_FOUND, TASK_NOT_CANCELABLE, PUSH_NOT_SUPPORTED, UNSUPPORTED_OP = -32001, -32002, -32003, -32004
```
**분석 / Analysis (Level 2)**
- L1-8: 설계 결정 — **내부 정준형은 0.3**, 1.0 은 경계에서 변환. 카드는 0.3 필드와 1.0 `supportedInterfaces` 를 함께 게시(이중 카드)해 두 세대 클라이언트를 모두 받는다.
- L14-24 `METHODS`: 0.3·1.0 메서드 이름 → 정준 작업(op). L25-32: 역매핑.
- L33-34: 종료 상태, 전체 상태.
- L37: A2A 오류 코드(-32001 TaskNotFound 등).
**짚어볼 점 / Watch-outs**
- L33 `TERMINAL` 에 `auth-required`·`input-required` 는 없다 — 감시기(`app/services/a2a_gateway.py:796-797`)는 submitted·working 만 폴링하므로 input-required Task 는 폴링되지 않는다(사용자 응답 대기라 의도된 동작).

```python path=app/services/a2a_proto.py lines=40-49
def op_of(method: str) -> str | None:
    return METHODS.get(method)


def version_of(method: str) -> str:
    return V10 if method[:1].isupper() else V03


def method_for(op: str, version: str) -> str:
    return (V10_METHOD if version == V10 else V03_METHOD)[op]
```
**분석 / Analysis (Level 2)**
- L40-41 `op_of`: 모르는 메서드면 `None`(호출부가 -32601 처리).
- L44-45 `version_of`: 첫 글자가 대문자면 1.0 — 1.0 메서드는 PascalCase, 0.3 은 `message/send` 처럼 소문자(간단하지만 명명 규칙에 의존).
- L48-49 `method_for`: 정준 작업 → 대상 버전 메서드명. 모르는 op 면 `KeyError`.

```python path=app/services/a2a_proto.py lines=52-98
# ── 상태·역할·파트 ──
def state_to_v10(s: str) -> str:
    return "TASK_STATE_" + s.upper().replace("-", "_")


def state_from(s: str | None) -> str:
    if not s:
        return "submitted"
    if s.startswith("TASK_STATE_"):
        return s[len("TASK_STATE_"):].lower().replace("_", "-")
    return s


def role_from(r: str | None) -> str:
    return {"ROLE_USER": "user", "ROLE_AGENT": "agent"}.get(r or "", r or "user")


def part_from(p: dict) -> dict:
    if "kind" in p:
        return p
    if "text" in p:
        out = {"kind": "text", "text": p["text"]}
    elif "data" in p:
        out = {"kind": "data", "data": p["data"]}
    else:
        f = {k2: p[k1] for k1, k2 in (("url", "uri"), ("raw", "bytes"), ("filename", "name"),
                                      ("mediaType", "mimeType")) if k1 in p}
        out = {"kind": "file", "file": f}
    if p.get("metadata"):
        out["metadata"] = p["metadata"]
    return out


def part_to_v10(p: dict) -> dict:
    p = part_from(p)
    kind = p.get("kind")
    if kind == "text":
        out = {"text": p.get("text", "")}
    elif kind == "data":
        out = {"data": p.get("data")}
    else:
        f = p.get("file") or {}
        out = {k2: f[k1] for k1, k2 in (("uri", "url"), ("bytes", "raw"), ("name", "filename"),
                                        ("mimeType", "mediaType")) if k1 in f}
    if p.get("metadata"):
        out["metadata"] = p["metadata"]
    return out
```
**분석 / Analysis (Level 2)**
- L53-54: `working` → `TASK_STATE_WORKING`, `input-required` → `TASK_STATE_INPUT_REQUIRED`.
- L57-62: 역변환, 빈 값은 `submitted`.
- L65-66: 역할 변환, 기본 `user`.
- L69-82 `part_from`: 이미 정준형(`kind` 있음)이면 그대로, 1.0 형식(`text`·`data`·`url/raw/filename/mediaType`)을 0.3 형식(`kind` + `file{uri,bytes,name,mimeType}`)으로.
- L85-98 `part_to_v10`: 반대 방향.
**짚어볼 점 / Watch-outs**
- L60-61 `TASK_STATE_UNSPECIFIED` 는 `unspecified` 로 변환된다 — 정준 상태 목록(L34)에 없는 값이 저장될 수 있다.
- L72-79 `text`·`data` 둘 다 없는 파트는 file 로 간주 — 알 수 없는 파트 형식이 빈 file 파트가 된다(정보 손실 없이 실패하는 편이 명확).

```python path=app/services/a2a_proto.py lines=101-172
def message_from(m: dict | None) -> dict | None:
    if m is None:
        return None
    out = {"kind": "message", "messageId": m.get("messageId") or uuid.uuid4().hex, "role": role_from(m.get("role")),
           "parts": [part_from(p) for p in m.get("parts") or []]}
    for k in ("contextId", "taskId", "metadata", "referenceTaskIds", "extensions"):
        if m.get(k):
            out[k] = m[k]
    return out


def message_to_v10(m: dict) -> dict:
    out = {"messageId": m.get("messageId"), "role": "ROLE_AGENT" if m.get("role") == "agent" else "ROLE_USER",
           "parts": [part_to_v10(p) for p in m.get("parts") or []]}
    for k in ("contextId", "taskId", "metadata", "referenceTaskIds", "extensions"):
        if m.get(k):
            out[k] = m[k]
    return out


def artifact_from(a: dict) -> dict:
    out = {"artifactId": a.get("artifactId") or uuid.uuid4().hex, "parts": [part_from(p) for p in a.get("parts") or []]}
    for k in ("name", "description", "metadata"):
        if a.get(k):
            out[k] = a[k]
    return out


def artifact_to_v10(a: dict) -> dict:
    out = {"artifactId": a.get("artifactId"), "parts": [part_to_v10(p) for p in a.get("parts") or []]}
    for k in ("name", "description", "metadata"):
        if a.get(k):
            out[k] = a[k]
    return out


def status_from(st: dict | None) -> dict:
    st = st or {}
    out = {"state": state_from(st.get("state"))}
    if st.get("message"):
        out["message"] = message_from(st["message"])
    if st.get("timestamp"):
        out["timestamp"] = st["timestamp"]
    return out


def status_to_v10(st: dict) -> dict:
    out = {"state": state_to_v10(st.get("state", "submitted"))}
    if st.get("message"):
        out["message"] = message_to_v10(st["message"])
    if st.get("timestamp"):
        out["timestamp"] = st["timestamp"]
    return out


def task_from(t: dict) -> dict:
    out = {"kind": "task", "id": t.get("id"), "contextId": t.get("contextId"), "status": status_from(t.get("status")),
           "artifacts": [artifact_from(a) for a in t.get("artifacts") or []],
           "history": [message_from(m) for m in t.get("history") or []]}
    if t.get("metadata"):
        out["metadata"] = t["metadata"]
    return out


def task_to_v10(t: dict) -> dict:
    out = {"id": t.get("id"), "contextId": t.get("contextId"), "status": status_to_v10(t.get("status") or {}),
           "artifacts": [artifact_to_v10(a) for a in t.get("artifacts") or []]}
    if t.get("history"):
        out["history"] = [message_to_v10(m) for m in t["history"]]
    if t.get("metadata"):
        out["metadata"] = t["metadata"]
    return out
```
**분석 / Analysis (Level 2)**
- L101-109 `message_from`: messageId 없으면 무작위 생성, 역할·파트 변환, 선택 필드 복사.
- L112-118 `message_to_v10`: 1.0 형식.
- L121-134: 아티팩트 변환(id 없으면 생성).
- L137-153: 상태(상태값·메시지·시각) 변환.
- L156-172: Task 변환 — 1.0 에서는 history 가 비면 생략.

```python path=app/services/a2a_proto.py lines=175-192
# ── send 결과 · 스트림 이벤트 ──
def result_from(result: dict) -> dict:
    """send/get 결과(0.3: Task|Message, 1.0: {task}|{message}|Task) → 정준형 Task|Message."""
    if "task" in result and isinstance(result["task"], dict):
        return task_from(result["task"])
    if "message" in result and isinstance(result["message"], dict) and "kind" not in result:
        return message_from(result["message"])
    if result.get("kind") == "message" or ("messageId" in result and "status" not in result):
        return message_from(result)
    return task_from(result)


def result_to(obj: dict, version: str, op: str = "send") -> dict:
    if version == V03:
        return obj
    if obj.get("kind") == "message":
        return {"message": message_to_v10(obj)} if op == "send" else message_to_v10(obj)
    return {"task": task_to_v10(obj)} if op == "send" else task_to_v10(obj)
```
**분석 / Analysis (Level 2)**
- L176-184 `result_from`: send/get 결과의 여러 모양(1.0 `{task}`·`{message}`·Task 자체, 0.3 Task|Message)을 정준형으로. Message 판정: `kind=="message"` 또는 messageId 있고 status 없음.
- L187-192 `result_to`: 클라이언트 버전으로 — 1.0 send 는 `{task}`/`{message}` 봉투, get·cancel 은 Task 그대로.

```python path=app/services/a2a_proto.py lines=195-227
def event_from(ev: dict) -> dict:
    """스트림 이벤트 → 정준형 (task|message|status-update|artifact-update)."""
    if "statusUpdate" in ev:
        su = ev["statusUpdate"]
        st = status_from(su.get("status"))
        return {"kind": "status-update", "taskId": su.get("taskId"), "contextId": su.get("contextId"), "status": st,
                "final": st["state"] in TERMINAL or st["state"] == "input-required"}
    if "artifactUpdate" in ev:
        au = ev["artifactUpdate"]
        return {"kind": "artifact-update", "taskId": au.get("taskId"), "contextId": au.get("contextId"),
                "artifact": artifact_from(au.get("artifact") or {}), "append": au.get("append", False),
                "lastChunk": au.get("lastChunk", False)}
    if ev.get("kind") == "status-update":
        return {**ev, "status": status_from(ev.get("status"))}
    if ev.get("kind") == "artifact-update":
        return {**ev, "artifact": artifact_from(ev.get("artifact") or {})}
    return result_from(ev)


def event_to(ev: dict, version: str) -> dict:
    if version == V03:
        return ev
    kind = ev.get("kind")
    if kind == "status-update":
        return {"statusUpdate": {"taskId": ev.get("taskId"), "contextId": ev.get("contextId"),
                                 "status": status_to_v10(ev.get("status") or {})}}
    if kind == "artifact-update":
        return {"artifactUpdate": {"taskId": ev.get("taskId"), "contextId": ev.get("contextId"),
                                   "artifact": artifact_to_v10(ev.get("artifact") or {}),
                                   "append": ev.get("append", False), "lastChunk": ev.get("lastChunk", False)}}
    if kind == "message":
        return {"message": message_to_v10(ev)}
    return {"task": task_to_v10(ev)}
```
**분석 / Analysis (Level 2)**
- L195-211 `event_from`: 스트림 이벤트(1.0 `statusUpdate`·`artifactUpdate` / 0.3 `kind` 기반) → 정준형. 1.0 statusUpdate 의 `final` 은 종료 상태 또는 input-required 로 **유도**(1.0 에는 final 필드가 없음).
- L214-227 `event_to`: 반대 방향.

```python path=app/services/a2a_proto.py lines=230-250
def params_to(op: str, params: dict, version: str) -> dict:
    """요청 params 를 대상 버전으로 변환 (게이트웨이 프로토콜 변환)."""
    p = dict(params or {})
    if op in ("send", "stream") and p.get("message"):
        p["message"] = message_from(p["message"]) if version == V03 else message_to_v10(message_from(p["message"]))
    if op == "push_set":
        if version == V10 and "pushNotificationConfig" in p:
            cfg = p.pop("pushNotificationConfig")
            p.update({k: cfg[k] for k in ("url", "token", "authentication", "id") if k in cfg})
        elif version == V03 and "url" in p:
            p = {"taskId": p.get("taskId"), "pushNotificationConfig": {k: p[k] for k in ("url", "token",
                                                                                         "authentication", "id")
                                                                       if k in p}}
    return p


def push_config_of(params: dict) -> tuple[str | None, dict]:
    """push_set params → (taskId, {url, token, authentication})."""
    if "pushNotificationConfig" in params:
        return params.get("taskId"), dict(params["pushNotificationConfig"])
    return params.get("taskId"), {k: params[k] for k in ("url", "token", "authentication") if k in params}
```
**분석 / Analysis (Level 2)**
- L230-243 `params_to`: 요청 params 를 대상 버전으로 — 메시지 변환, push 설정의 위치(0.3: `pushNotificationConfig` 하위, 1.0: 평탄) 변환.
- L246-250 `push_config_of`: (taskId, {url, token, authentication}) 추출.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 입력 검증 부재] L246-250 클라이언트가 보낸 웹훅 `url` 을 **형식·스킴·대상 검증 없이** 반환한다 — 게이트웨이가 이 URL 로 POST 하므로(`app/services/a2a_gateway.py:771-775`) SSRF 의 입력 지점이다. 코덱 계층에서 최소한 `https://` 스킴 검사를 하거나, 호출부에서 egress 정책을 적용해야 한다.

```python path=app/services/a2a_proto.py lines=253-264
def text_of(obj: dict) -> str:
    """Task/Message 에서 사람이 읽을 텍스트 (상태 메시지 + 아티팩트 텍스트)."""
    parts = []
    if obj.get("kind") == "message":
        parts += [p.get("text", "") for p in obj.get("parts", []) if p.get("kind") == "text"]
    else:
        msg = (obj.get("status") or {}).get("message")
        if msg:
            parts += [p.get("text", "") for p in msg.get("parts", []) if p.get("kind") == "text"]
        for a in obj.get("artifacts") or []:
            parts += [p.get("text", "") for p in a.get("parts", []) if p.get("kind") == "text"]
    return "\n".join(x for x in parts if x)
```
**분석 / Analysis (Level 2)**
- L253-264 `text_of`: 사람이 읽을 텍스트 — 메시지면 텍스트 파트, Task 면 상태 메시지 + 아티팩트 텍스트.

```python path=app/services/a2a_proto.py lines=267-300
# ── Agent Card ──
def card_info(card: dict) -> dict:
    """0.3/1.0 카드 → {name, description, version, url, protocol_version, interfaces, streaming, push, skills, schemes}."""
    interfaces = []
    for i in card.get("supportedInterfaces") or []:
        interfaces.append({"url": i.get("url"), "binding": i.get("protocolBinding", "JSONRPC"),
                           "version": str(i.get("protocolVersion") or V10), **({"tenant": i["tenant"]} if i.get("tenant") else {})})
    if card.get("url"):
        interfaces.append({"url": card["url"], "binding": card.get("preferredTransport", "JSONRPC"),
                           "version": str(card.get("protocolVersion") or "0.3.0")})
    jsonrpc = [i for i in interfaces if i["binding"].upper() in ("JSONRPC", "JSON-RPC")]
    rest = [i for i in interfaces if i["binding"].upper() in ("HTTP+JSON", "REST") and i["version"].startswith("1")]
    grpc = [i for i in interfaces if i["binding"].upper() == "GRPC" and i["version"].startswith("1")]
    v10 = next((i for i in jsonrpc if i["version"].startswith("1")), None)
    v03 = next((i for i in jsonrpc if i["version"].startswith("0.3")), None)
    schemes = {}
    for name, sch in (card.get("securitySchemes") or {}).items():
        if "type" in sch:
            schemes[name] = {"type": sch["type"], **{k: v for k, v in sch.items() if k != "type"}}
        else:
            kind, body = next(iter(sch.items()), ("unknown", {}))
            schemes[name] = {"type": {"apiKeySecurityScheme": "apiKey", "httpAuthSecurityScheme": "http",
                                      "oauth2SecurityScheme": "oauth2", "openIdConnectSecurityScheme": "openIdConnect",
                                      "mtlsSecurityScheme": "mutualTLS"}.get(kind, kind), **(body or {})}
    caps = card.get("capabilities") or {}
    return {"name": card.get("name"), "description": card.get("description"), "version": card.get("version"),
            "url": (v10 or v03 or (rest[0] if rest else None) or (grpc[0] if grpc else None) or {}).get("url"),
            "url_v10": (v10 or {}).get("url"), "url_v03": (v03 or {}).get("url"),
            "url_rest": rest[0]["url"] if rest else None,
            "url_grpc": (grpc[0]["url"] + (f"#{grpc[0]['tenant']}" if grpc[0].get("tenant") else "")) if grpc else None,
            "protocol_versions": sorted({i["version"][:3] for i in jsonrpc + rest + grpc}), "interfaces": interfaces,
            "bindings": sorted({"JSONRPC" for _ in jsonrpc[:1]} | {"HTTP+JSON" for _ in rest[:1]} | {"GRPC" for _ in grpc[:1]}),
            "streaming": bool(caps.get("streaming")), "push": bool(caps.get("pushNotifications")),
            "skills": card.get("skills") or [], "schemes": schemes}
```
**분석 / Analysis (Level 2)**
- L268-300 `card_info`: 0.3·1.0 카드 → 통일된 요약 — 인터페이스 목록(1.0 `supportedInterfaces` + 0.3 `url`·`preferredTransport`), 바인딩별(JSONRPC·HTTP+JSON·GRPC) 1.0/0.3 URL, gRPC tenant(`#tenant` 접미), 프로토콜 버전·바인딩 목록, 스트리밍·푸시 능력, 스킬, 보안 스킴(1.0 oneof 형식 `{apiKeySecurityScheme: {...}}` 을 0.3 형식 `{type: apiKey, ...}` 로 정규화).
**짚어볼 점 / Watch-outs**
- L285 `sch` 가 dict 가 아니면(`"type" in sch` 는 문자열에서도 동작) 예상 밖 결과 — 원격 카드가 비정상 형식이면 L287 `next(iter(sch.items()))` 에서 `AttributeError`.
- L293 우선순위 v10 > v03 > rest > grpc — 대표 URL 결정.

```python path=app/services/a2a_proto.py lines=303-320
def validate_card(card: dict) -> list[str]:
    errors = []
    for k in ("name", "description", "version"):
        if not card.get(k):
            errors.append(f"{k}: 필수 항목")
    if not isinstance(card.get("skills"), list) or not card["skills"]:
        errors.append("skills: 1개 이상 필요")
    else:
        for i, s in enumerate(card["skills"]):
            if not s.get("id") or not s.get("name"):
                errors.append(f"skills/{i}: id·name 필수")
    if not card_info(card)["url"]:
        errors.append("url 또는 supportedInterfaces(JSONRPC·HTTP+JSON·GRPC) 필요")
    return errors


def card_hash(card: dict) -> str:
    return hashlib.sha256(json.dumps(card, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
```
**분석 / Analysis (Level 2)**
- L303-316 `validate_card`: 필수(name·description·version), 스킬 1개 이상·각 id/name, 호출 가능한 인터페이스 URL.
- L319-320 `card_hash`: 키 정렬 JSON 의 SHA-256 — 의미 없는 키 순서 변화로 "변경" 이 감지되지 않게.
**짚어볼 점 / Watch-outs**
- L312 `s.get(...)` — 스킬 항목이 dict 가 아니면 `AttributeError`(검증 함수가 예외를 던짐). 검증 함수는 예외 대신 오류 목록을 반환해야 일관된다.
- URL 형식(스킴·호스트) 검증이 없다 — `card_info()["url"]` 이 비어 있지 않은지만 본다.

```python path=app/services/a2a_proto.py lines=323-345
def build_card(*, name: str, description: str, url: str, version: str, skills: list[dict], streaming: bool = True,
               push: bool = True, schemes: dict | None = None, provider: dict | None = None,
               documentation_url: str | None = None, rest_url: str | None = None, grpc_url: str | None = None,
               grpc_tenant: str | None = None) -> dict:
    """이중(0.3+1.0) 카드 — 0.3 클라이언트는 url/protocolVersion, 1.0 SDK 는 supportedInterfaces 를 읽는다."""
    schemes = schemes if schemes is not None else {"bearer": {"type": "http", "scheme": "bearer",
                                                               "bearerFormat": "JWT"}}
    card = {"protocolVersion": "0.3.0", "name": name, "description": description, "url": url,
            "preferredTransport": "JSONRPC", "version": version,
            "supportedInterfaces": [{"url": url, "protocolBinding": "JSONRPC", "protocolVersion": V10},
                                    *([{"url": rest_url, "protocolBinding": "HTTP+JSON", "protocolVersion": V10}] if rest_url else []),
                                    *([{"url": grpc_url, "protocolBinding": "GRPC", "protocolVersion": V10,
                                        **({"tenant": grpc_tenant} if grpc_tenant else {})}] if grpc_url else []),
                                    {"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "0.3.0"}],
            "capabilities": {"streaming": streaming, "pushNotifications": push},
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "text/markdown", "application/json"],
            "skills": skills, "securitySchemes": schemes, "security": [{k: []} for k in schemes]}
    if provider:
        card["provider"] = provider
    if documentation_url:
        card["documentationUrl"] = documentation_url
    return card
```
**분석 / Analysis (Level 2)**
- L323-345 `build_card`: 이중 카드 — 0.3 필드(`protocolVersion`·`url`·`preferredTransport`)와 1.0 `supportedInterfaces`(JSONRPC 1.0, 선택 HTTP+JSON·GRPC(tenant), JSONRPC 0.3)를 함께. 기본 보안 스킴 bearer JWT, `security` 요구 목록 자동 생성.
**짚어볼 점 / Watch-outs**
- L328-329 `schemes if schemes is not None else {...}` — 빈 dict `{}` 를 넘기면 보안 스킴 없는 카드가 된다(명시적 None 과 구분 — 의도된 설계).

## 파일 평가 / File Assessment
- 강점: (1) 순수 함수 코덱 — 부수효과 없이 시험 가능. (2) 정준형 1개 + 경계 변환으로 버전 조합 폭발을 피했다. (3) 카드 해시를 정규화 JSON 으로.
- 위험: (1) [중간] push 웹훅 URL 무검증 반환(SSRF 입력 지점). (2) [낮음] 비정상 원격 카드에서 예외(검증 함수가 예외를 던짐). (3) 알 수 없는 파트·상태가 조용히 변환됨.
- 개선 권고(영향순): (1) `push_config_of` 에 URL 스킴·호스트 검증. (2) `validate_card`·`card_info` 의 타입 방어(dict 확인). (3) 알 수 없는 상태·파트는 오류로.
- 재읽기 권고 레벨: 3.
