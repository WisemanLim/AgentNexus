"""요청 컨텍스트 — trace_id 등 (OTel 활성 스팬이 있으면 그 trace_id 우선)."""
from contextvars import ContextVar

from opentelemetry import trace

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return _trace_id.get()


def set_trace_id(value: str | None) -> None:
    _trace_id.set(value)
