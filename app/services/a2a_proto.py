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


def op_of(method: str) -> str | None:
    return METHODS.get(method)


def version_of(method: str) -> str:
    return V10 if method[:1].isupper() else V03


def method_for(op: str, version: str) -> str:
    return (V10_METHOD if version == V10 else V03_METHOD)[op]


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
