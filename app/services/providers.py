"""LLM 공급자 어댑터 (FR-L00~L02, FR-G06) — 모든 어댑터는 OpenAI Chat Completions 형식으로 정규화한다.

- openai_compat: OpenAI · vLLM · Ollama · LiteLLM · Gemini(OpenAI 호환 엔드포인트) — 그대로 전달
- anthropic: Messages API ↔ Chat Completions 변환(도구 호출·스트리밍 포함)
- mock: 오프라인·에어갭 시험용 결정적 응답(요약·인용, 구조화 출력, 도구 호출, 장애 주입)
"""
import asyncio
import json
import math
import re
import time
import uuid
from collections.abc import AsyncIterator

import httpx

from app.core.http import async_client
from app.models import Provider

DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://ollama:11434/v1",
    "vllm": "http://vllm:8000/v1",
    "litellm": "http://litellm:4000/v1",
}
OPENAI_COMPAT = {"openai", "gemini", "ollama", "vllm", "litellm", "openai_compat"}


class ProviderError(Exception):
    def __init__(self, message: str, status: int = 502, retryable: bool = True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _cid() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4)) if text else 0


def message_text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _raise_for(res: httpx.Response) -> None:
    if res.status_code < 400:
        return
    retry = res.status_code in (408, 409, 425, 429) or res.status_code >= 500
    try:
        detail = res.json()
        msg = (detail.get("error") or {}).get("message") if isinstance(detail.get("error"), dict) else detail
    except ValueError:
        msg = res.text[:300]
    raise ProviderError(f"upstream {res.status_code}: {msg}", status=res.status_code, retryable=retry)


class Adapter:
    async def chat(self, req: dict, model: str) -> dict:
        raise NotImplementedError

    def stream(self, req: dict, model: str) -> AsyncIterator[dict]:
        raise NotImplementedError

    async def embed(self, inputs: list[str], model: str) -> dict:
        """OpenAI 호환 임베딩 응답 {data:[{embedding, index}], usage} — 미지원 공급자는 400."""
        raise ProviderError("이 공급자는 임베딩을 지원하지 않습니다.", status=400, retryable=False)


MOCK_DIMS = 256


def hash_embedding(text: str, dims: int = MOCK_DIMS) -> list[float]:
    """결정적 임베딩(오프라인·에어갭 시험용) — 단어 + 단어 내 문자 2-gram(한국어 조사·어미 변형 흡수)을 해시 공간에 사상, L2 정규화.
    의미 유사도가 아닌 어휘 겹침 기반이다. 실서비스는 OpenAI 호환 임베딩 공급자(vLLM·Ollama 등)로 교체."""
    import hashlib as _h
    vec = [0.0] * dims
    for w in re.findall(r"\w+", text.lower()):
        feats = [w] + ([w[i:i + 2] for i in range(len(w) - 1)] if len(w) > 1 else [])
        for f in feats:
            d = _h.blake2b(f.encode(), digest_size=8).digest()
            vec[int.from_bytes(d[:4], "big") % dims] += 1.0 if d[4] & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


# ───────────────────────── OpenAI 호환 ─────────────────────────
class OpenAICompatAdapter(Adapter):
    def __init__(self, base_url: str, api_key: str | None, timeout: float = 60.0, headers: dict | None = None,
                 tls_kw: dict | None = None, extra: dict | None = None):
        self.base = base_url.rstrip("/")
        self.extra = extra or {}  # 공급자 옵션이 강제하는 요청 필드 (예: ZDR → store:false)
        self.tls_kw = tls_kw or {}
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self.timeout = timeout

    async def chat(self, req: dict, model: str) -> dict:
        body = {**req, **self.extra, "model": model, "stream": False}
        try:
            async with async_client(timeout=self.timeout, **self.tls_kw) as http:
                res = await http.post(f"{self.base}/chat/completions", json=body, headers=self.headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}", status=503) from exc
        _raise_for(res)
        return res.json()

    async def stream(self, req: dict, model: str) -> AsyncIterator[dict]:
        body = {**req, **self.extra, "model": model, "stream": True, "stream_options": {"include_usage": True}}
        try:
            async with async_client(timeout=self.timeout, **self.tls_kw) as http:
                async with http.stream("POST", f"{self.base}/chat/completions", json=body, headers=self.headers) as res:
                    if res.status_code >= 400:
                        await res.aread()
                        _raise_for(res)
                    async for line in res.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        yield json.loads(data)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}", status=503) from exc

    async def embed(self, inputs: list[str], model: str) -> dict:
        try:
            async with async_client(timeout=self.timeout, **self.tls_kw) as http:
                res = await http.post(f"{self.base}/embeddings", json={"model": model, "input": inputs},
                                      headers=self.headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}", status=503) from exc
        _raise_for(res)
        return res.json()


# ───────────────────────── Anthropic Messages ─────────────────────────
_STOP = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls", "stop_sequence": "stop"}


def to_anthropic(req: dict, model: str) -> dict:
    system_parts, messages = [], []
    for m in req.get("messages", []):
        role = m.get("role")
        if role in ("system", "developer"):
            system_parts.append(message_text(m))
        elif role == "tool":
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                                                          "content": message_text(m)}]})
        elif role == "assistant" and m.get("tool_calls"):
            blocks = [{"type": "text", "text": message_text(m)}] if message_text(m) else []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                blocks.append({"type": "tool_use", "id": tc.get("id"), "name": fn.get("name"),
                               "input": json.loads(fn.get("arguments") or "{}")})
            messages.append({"role": "assistant", "content": blocks})
        else:
            messages.append({"role": "assistant" if role == "assistant" else "user", "content": message_text(m)})
    rf = req.get("response_format") or {}
    if rf.get("type") == "json_schema":
        schema = (rf.get("json_schema") or {}).get("schema", {})
        system_parts.append("Respond ONLY with a JSON object matching this JSON Schema, no prose:\n" + json.dumps(schema))
    elif rf.get("type") == "json_object":
        system_parts.append("Respond ONLY with a valid JSON object.")
    body = {"model": model, "messages": messages, "max_tokens": req.get("max_tokens") or req.get(
        "max_completion_tokens") or 1024}
    if system_parts:
        body["system"] = "\n\n".join(p for p in system_parts if p)
    for k in ("temperature", "top_p"):
        if req.get(k) is not None:
            body[k] = req[k]
    if req.get("stop"):
        body["stop_sequences"] = req["stop"] if isinstance(req["stop"], list) else [req["stop"]]
    if req.get("tools"):
        body["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                          "input_schema": t["function"].get("parameters") or {"type": "object"}}
                         for t in req["tools"] if t.get("type") == "function"]
        tc = req.get("tool_choice")
        if tc == "required":
            body["tool_choice"] = {"type": "any"}
        elif isinstance(tc, dict) and tc.get("function"):
            body["tool_choice"] = {"type": "tool", "name": tc["function"]["name"]}
        elif tc == "none":
            body.pop("tools")
        else:
            body["tool_choice"] = {"type": "auto"}
    return body


def from_anthropic(res: dict, model: str) -> dict:
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(
        b.get("input") or {}, ensure_ascii=False)}} for b in res.get("content", []) if b.get("type") == "tool_use"]
    msg = {"role": "assistant", "content": text or None}
    if calls:
        msg["tool_calls"] = calls
    u = res.get("usage") or {}
    return {"id": _cid(), "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": _STOP.get(res.get("stop_reason"), "stop")}],
            "usage": {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
                      "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0)}}


class AnthropicAdapter(Adapter):
    def __init__(self, base_url: str, api_key: str | None, timeout: float = 60.0, tls_kw: dict | None = None):
        self.base = base_url.rstrip("/")
        self.tls_kw = tls_kw or {}
        self.headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if api_key:
            self.headers["x-api-key"] = api_key
        self.timeout = timeout

    async def chat(self, req: dict, model: str) -> dict:
        try:
            async with async_client(timeout=self.timeout, **self.tls_kw) as http:
                res = await http.post(f"{self.base}/v1/messages", json=to_anthropic(req, model), headers=self.headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}", status=503) from exc
        _raise_for(res)
        return from_anthropic(res.json(), model)

    async def stream(self, req: dict, model: str) -> AsyncIterator[dict]:
        cid, created = _cid(), int(time.time())
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        tool_index: dict[int, int] = {}

        def chunk(delta: dict, finish: str | None = None, **extra) -> dict:
            return {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}

        body = {**to_anthropic(req, model), "stream": True}
        try:
            async with async_client(timeout=self.timeout, **self.tls_kw) as http:
                async with http.stream("POST", f"{self.base}/v1/messages", json=body, headers=self.headers) as res:
                    if res.status_code >= 400:
                        await res.aread()
                        _raise_for(res)
                    yield chunk({"role": "assistant", "content": ""})
                    async for line in res.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        ev = json.loads(line[5:].strip())
                        t = ev.get("type")
                        if t == "message_start":
                            usage["prompt_tokens"] = (ev.get("message", {}).get("usage") or {}).get("input_tokens", 0)
                        elif t == "content_block_start" and ev.get("content_block", {}).get("type") == "tool_use":
                            b = ev["content_block"]
                            idx = tool_index.setdefault(ev["index"], len(tool_index))
                            yield chunk({"tool_calls": [{"index": idx, "id": b["id"], "type": "function",
                                                         "function": {"name": b["name"], "arguments": ""}}]})
                        elif t == "content_block_delta":
                            d = ev.get("delta", {})
                            if d.get("type") == "text_delta":
                                yield chunk({"content": d.get("text", "")})
                            elif d.get("type") == "input_json_delta":
                                yield chunk({"tool_calls": [{"index": tool_index.get(ev["index"], 0),
                                                             "function": {"arguments": d.get("partial_json", "")}}]})
                        elif t == "message_delta":
                            usage["completion_tokens"] = (ev.get("usage") or {}).get("output_tokens", 0)
                            stop = _STOP.get((ev.get("delta") or {}).get("stop_reason"), "stop")
                            yield chunk({}, stop)
                        elif t == "error":
                            raise ProviderError(str(ev.get("error")), status=502)
                    yield {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                           "choices": [], "usage": {**usage, "total_tokens": usage["prompt_tokens"] +
                                                    usage["completion_tokens"]}}
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}", status=503) from exc


# ───────────────────────── Mock (오프라인) ─────────────────────────
def from_schema(schema: dict, depth: int = 0):
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        req = schema.get("required", list(props))
        return {k: from_schema(v, depth + 1) for k, v in props.items() if k in req} if depth < 6 else {}
    if t == "array":
        return []
    if t in ("integer", "number"):
        return schema.get("minimum", 0)
    if t == "boolean":
        return False
    return "mock"


_SRC = re.compile(r"^\s*\[(\d+)\]\s*(.+)$")


def mock_reply(req: dict, model: str) -> str:
    msgs = req.get("messages", [])
    system = " ".join(message_text(m) for m in msgs if m.get("role") in ("system", "developer"))
    last = next((message_text(m) for m in reversed(msgs) if m.get("role") == "user"), "")
    sources = [(_m.group(1), _m.group(2).strip()) for line in last.splitlines() if (_m := _SRC.match(line))]
    if sources and ("요약" in system or "summar" in system.lower()):
        bullets = [f"- {text[:120].rstrip()} [{n}]" for n, text in sources[:8]]
        return f"요약 ({len(sources)}건 근거):\n" + "\n".join(bullets)
    snippet = re.sub(r"\s+", " ", last).strip()[:200]
    return f"[{model}] {snippet}" if snippet else f"[{model}] 안녕하세요. 무엇을 도와드릴까요?"


class MockAdapter(Adapter):
    """provider.options: latency_ms · fail(bool) · fail_status · fail_models([...])."""

    def __init__(self, options: dict):
        self.options = options or {}

    async def _maybe_fail(self, model: str) -> None:
        lat = self.options.get("latency_ms", 0)
        if lat:
            await asyncio.sleep(lat / 1000)
        if self.options.get("fail") or model in (self.options.get("fail_models") or []) or model.endswith("-down"):
            raise ProviderError("mock upstream unavailable", status=int(self.options.get("fail_status", 503)))

    def _build(self, req: dict, model: str) -> tuple[dict, str]:
        rf = req.get("response_format") or {}
        msg: dict = {"role": "assistant"}
        fixed = (self.options.get("replies") or {}).get(model, self.options.get("fixed_reply"))
        if fixed is not None:  # 시험·시연용 고정 응답
            msg["content"] = fixed if isinstance(fixed, str) else json.dumps(fixed, ensure_ascii=False)
            return msg, "stop"
        finish = "stop"
        tc = req.get("tool_choice")
        if req.get("tools") and (tc == "required" or isinstance(tc, dict)):
            fn = tc["function"]["name"] if isinstance(tc, dict) else req["tools"][0]["function"]["name"]
            tool = next(t for t in req["tools"] if t["function"]["name"] == fn)
            args = from_schema(tool["function"].get("parameters") or {"type": "object"})
            msg.update(content=None, tool_calls=[{"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                                                  "function": {"name": fn, "arguments": json.dumps(args)}}])
            finish = "tool_calls"
            text = json.dumps(args)
        elif rf.get("type") == "json_schema":
            text = json.dumps(from_schema((rf.get("json_schema") or {}).get("schema", {})), ensure_ascii=False)
            msg["content"] = text
        elif rf.get("type") == "json_object":
            text = "{}"
            msg["content"] = text
        else:
            text = mock_reply(req, model)
            msg["content"] = text
        return msg, finish if text is not None else "stop"

    def _usage(self, req: dict, out: str) -> dict:
        pin = sum(estimate_tokens(message_text(m)) for m in req.get("messages", []))
        pout = estimate_tokens(out)
        return {"prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout}

    async def embed(self, inputs: list[str], model: str) -> dict:
        await self._maybe_fail(model)
        tokens = sum(estimate_tokens(t) for t in inputs)
        return {"object": "list", "model": model, "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
                "data": [{"object": "embedding", "index": i, "embedding": hash_embedding(t)} for i, t in enumerate(inputs)]}

    async def chat(self, req: dict, model: str) -> dict:
        await self._maybe_fail(model)
        msg, finish = self._build(req, model)
        out = msg.get("content") or json.dumps(msg.get("tool_calls"))
        return {"id": _cid(), "object": "chat.completion", "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": self._usage(req, out)}

    async def stream(self, req: dict, model: str) -> AsyncIterator[dict]:
        await self._maybe_fail(model)
        msg, finish = self._build(req, model)
        cid, created = _cid(), int(time.time())
        base = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model}
        yield {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}
        if msg.get("tool_calls"):
            yield {**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, **msg["tool_calls"][0]}]},
                                        "finish_reason": None}]}
            out = json.dumps(msg["tool_calls"])
        else:
            out = msg.get("content") or ""
            for piece in re.findall(r"\S+\s*|\s+", out):
                yield {**base, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
        yield {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
        yield {**base, "choices": [], "usage": self._usage(req, out)}


def adapter_for(provider: Provider, api_key: str | None, timeout: float = 60.0) -> Adapter:
    kind = provider.kind
    if kind == "mock":
        return MockAdapter(provider.options)
    base = provider.base_url or DEFAULT_BASE.get(kind, "")
    from app.services import tls  # 업스트림 mTLS (FR-G02) — options.tls + 게이트웨이 정책
    try:
        tls_kw = tls.client_kwargs(tls.context_for((provider.options or {}).get("tls"), f"provider:{provider.id}", base))
    except tls.MtlsError as exc:
        raise ProviderError(str(exc), status=403, retryable=False) from exc
    if kind == "anthropic":
        return AnthropicAdapter(base, api_key, timeout, tls_kw)
    if kind in OPENAI_COMPAT:
        zdr = bool((provider.options or {}).get("zdr")) and kind == "openai"  # 보존 최소화 (SCR-I02 ④) — OpenAI 는 store:false
        return OpenAICompatAdapter(base, api_key, timeout, headers=(provider.options or {}).get("headers"), tls_kw=tls_kw,
                                   extra={"store": False} if zdr else None)
    raise ProviderError(f"지원하지 않는 공급자 종류: {kind}", status=400, retryable=False)
