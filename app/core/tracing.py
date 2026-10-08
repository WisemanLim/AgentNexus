"""OpenTelemetry 트레이싱 (FR-O00·O01, FR-G05) — W3C Trace Context 전파, GenAI 시맨틱 컨벤션 속성.

- api: 스팬을 내장 저장소(spans 테이블)에 쓰고, OTLP_ENDPOINT 가 있으면 외부 Collector(Jaeger 등)로도 보낸다.
- mcp-servers·a2a-demo 등 별도 컨테이너: OTLP/HTTP 로 api 의 수신기(POST /v1/traces)에 보낸다 → 단일 trace 조회.
- 요청 미들웨어가 traceparent 를 추출해 서버 스팬을 열고, 아웃바운드 httpx 는 core.http 가 traceparent 를 주입한다.
"""
import contextlib
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Status, StatusCode

from app.core.config import settings

log = logging.getLogger("agentnexus.tracing")
tracer = trace.get_tracer("agentnexus")
STATS = {"db_exported": 0, "db_failed": 0, "otlp_configured": False, "ingested": 0, "last_ingest_at": None,
         "service": None}
_SKIP_PATHS = ("/health", "/metrics", "/v1/traces", "/healthz")


def _hex(n: int, width: int) -> str:
    return format(n, f"0{width}x")


def _ts(ns: int | None) -> datetime:
    return datetime.fromtimestamp((ns or 0) / 1e9, tz=timezone.utc)


def mask(v):
    """스팬 속성 PII·시크릿 마스킹 (NFR-PRV1) — 문자열(목록 포함)만. 로그와 같은 패턴(guards.mask_pii)."""
    if isinstance(v, str):
        from app.services.guards import mask_pii
        return mask_pii(v)[0]
    if isinstance(v, (list, tuple)):
        return [mask(x) for x in v]
    return v


def _clean_attrs(attrs) -> dict:
    out = {}
    for k, v in dict(attrs or {}).items():
        out[k] = mask(list(v) if isinstance(v, tuple) else v)
    return out


def store_spans(rows: list[dict]) -> None:
    """내장 저장소 기록 — 자체 스팬·OTLP 수신 스팬 모두 여기서 마스킹한다(다른 컨테이너가 마스킹하지 않았어도)."""
    from app.db import SessionLocal
    from app.models import Span
    with SessionLocal() as db:
        for r in rows:
            r = {**r, "attributes": _clean_attrs(r.get("attributes")),
                 "events": [{**e, **({"attributes": _clean_attrs(e["attributes"])} if e.get("attributes") else {})}
                            for e in r.get("events") or []]}
            db.merge(Span(**r))
        db.commit()


class DbSpanExporter(SpanExporter):
    """내장 트레이스 저장소 — SCR-K02·K03 조회용."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        rows = []
        for s in spans:
            ctx = s.get_span_context()
            parent = s.parent.span_id if s.parent else None
            rows.append({"id": _hex(ctx.span_id, 16), "trace_id": _hex(ctx.trace_id, 32),
                         "parent_id": _hex(parent, 16) if parent else None, "name": s.name[:255],
                         "service": str(s.resource.attributes.get("service.name", "agentnexus"))[:80],
                         "kind": s.kind.name.lower(), "start_time": _ts(s.start_time), "end_time": _ts(s.end_time),
                         "duration_ms": round(((s.end_time or 0) - (s.start_time or 0)) / 1e6, 3),
                         "status": "error" if s.status.status_code == StatusCode.ERROR else "ok",
                         "attributes": _clean_attrs(s.attributes),
                         "events": [{"name": e.name, "ts": _ts(e.timestamp).isoformat(),
                                     "attributes": _clean_attrs(e.attributes)} for e in s.events]})
        try:
            store_spans(rows)
            STATS["db_exported"] += len(rows)
            return SpanExportResult.SUCCESS
        except Exception as exc:  # noqa: BLE001
            STATS["db_failed"] += len(rows)
            log.warning("span export failed: %s", type(exc).__name__)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        return None


def ingest_token() -> str:
    """OTLP 수신기 인증 토큰 — env OTLP_INGEST_TOKEN 또는 data_dir/keys/otlp-ingest.token (자동 생성·공유 볼륨)."""
    import os
    import secrets
    if settings.otlp_ingest_token.strip() not in ("", "CHANGE_ME"):
        return settings.otlp_ingest_token.strip()
    path = Path(settings.data_dir) / "keys" / "otlp-ingest.token"
    if path.exists():
        return path.read_text().strip()
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(24))
    return path.read_text().strip() if path.exists() else ""


_configured = False


def setup(service: str = "agentnexus", db_export: bool = True, otlp_endpoint: str | None = None,
          otlp_headers: dict | None = None) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    provider = TracerProvider(resource=Resource.create({"service.name": service, "service.namespace": "agentnexus"}))
    proc = SimpleSpanProcessor if settings.span_processor == "simple" else BatchSpanProcessor
    if db_export:
        provider.add_span_processor(proc(DbSpanExporter()))
    endpoint = otlp_endpoint if otlp_endpoint is not None else settings.otlp_endpoint
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces",
                                                                        headers=otlp_headers or {})))
        STATS["otlp_configured"] = True
    trace.set_tracer_provider(provider)
    STATS["service"] = service


def setup_for_service(service: str) -> None:
    """별도 컨테이너 서비스(mcp-servers·a2a-demo) — api 의 OTLP 수신기로 전송."""
    import os
    if os.environ.get("AGENTNEXUS_ROLE", "api") == "api":
        return  # 같은 프로세스(시험·단일 컨테이너)는 api 설정을 공유
    target = settings.idp_internal_url.rstrip("/")
    setup(service, db_export=False, otlp_endpoint=target, otlp_headers={"Authorization": f"Bearer {ingest_token()}"})


@contextlib.contextmanager
def span(name: str, kind: SpanKind = SpanKind.INTERNAL, **attrs):
    with tracer.start_as_current_span(name, kind=kind) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, mask(v))  # 외부 OTLP 내보내기에도 마스킹된 값만
        try:
            yield sp
        except Exception as exc:
            sp.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def current_span_ids() -> tuple[str | None, str | None]:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None, None
    return _hex(ctx.trace_id, 32), _hex(ctx.span_id, 16)


def context_from(trace_id: str | None, span_id: str | None):
    """저장된 trace/span id 로 부모 컨텍스트 복원 (승인 후 재개도 같은 trace 유지)."""
    if not trace_id or not span_id:
        return None
    sc = trace.SpanContext(trace_id=int(trace_id, 16), span_id=int(span_id, 16), is_remote=True,
                           trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED))
    return trace.set_span_in_context(trace.NonRecordingSpan(sc))


class TracingMiddleware:
    """ASGI 서버 스팬 — traceparent 추출, 응답에 X-AgentNexus-Trace-Id."""

    def __init__(self, app, component: str = "api"):
        self.app = app
        self.component = component

    @property
    def router(self):  # 감싼 Starlette 앱의 lifespan 을 그대로 쓰도록 위임
        return self.app.router

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").startswith(_SKIP_PATHS):
            return await self.app(scope, receive, send)
        carrier = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        parent = propagate.extract(carrier)
        method = scope.get("method", "GET")
        token = otel_context.attach(parent)
        t0 = time.perf_counter()
        status_code = 500
        first_byte: float | None = None  # 응답 시작(헤더) 시각 — SSE 도 첫 응답까지로 지연 SLI (D15-④)
        try:
            with tracer.start_as_current_span(f"{method} {scope.get('path', '')}", kind=SpanKind.SERVER) as sp:
                sp.set_attribute("http.request.method", method)
                sp.set_attribute("url.path", mask(scope.get("path", "")))
                sp.set_attribute("anx.component", self.component)
                trace_hex = _hex(sp.get_span_context().trace_id, 32)

                async def _send(message):
                    nonlocal status_code, first_byte
                    if message["type"] == "http.response.start":
                        status_code = message["status"]
                        first_byte = time.perf_counter()
                        message = {**message, "headers": [*message.get("headers", []),
                                                          (b"x-agentnexus-trace-id", trace_hex.encode())]}
                    await send(message)
                try:
                    await self.app(scope, receive, _send)
                finally:
                    route = scope.get("route")
                    if route is not None and getattr(route, "path", None):
                        sp.update_name(f"{method} {route.path}")
                        sp.set_attribute("http.route", route.path)
                    sp.set_attribute("http.response.status_code", status_code)
                    if status_code >= 500:
                        sp.set_status(Status(StatusCode.ERROR))
        finally:
            otel_context.detach(token)
            from app.core import metrics
            metrics.observe_request(self.component, scope, status_code, time.perf_counter() - t0,
                                    (first_byte - t0) if first_byte is not None else None)
