> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [core_Abstract.md](core_Abstract.md)

### 13. app/core/tracing.py
**파일 요약 / File Summary**
- 역할 / Role: OpenTelemetry 설정(TracerProvider·프로세서·내장 DB 익스포터·선택 OTLP 익스포터), 스팬 속성 PII 마스킹, OTLP 수신 토큰 관리, `span()` 컨텍스트 매니저, 저장된 trace id 로 컨텍스트 복원, ASGI 서버 스팬 미들웨어(응답 헤더 `X-AgentNexus-Trace-Id`, 요청 메트릭·SLI 기록).
- 진입점 거리 / Distance: 1 (`app/main.py:121,125`)
- LOC: 226
- 주요 export: `setup`, `setup_for_service`, `span`, `current_span_ids`, `context_from`, `TracingMiddleware`, `DbSpanExporter`, `store_spans`, `ingest_token`, `mask`, `STATS`
- 의존(내부): `app.core.config`, (지연) `app.services.guards.mask_pii`, `app.db`, `app.models.Span`, `app.core.metrics`
- 외부 패키지: opentelemetry-api/sdk, opentelemetry-exporter-otlp-proto-http
- 부수효과 / Side effects: 전역 TracerProvider 설정, DB 쓰기(spans), 키 파일 생성(`otlp-ingest.token`), 전역 `STATS` 변이
- 동시성 / Concurrency: BatchSpanProcessor 백그라운드 스레드에서 DB 익스포트, 미들웨어는 asyncio
- 테스트 커버리지 흔적: `test/test_observe.py`, `test/test_hardening.py`(마스킹), `test/test_slo.py`(SLI)

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/core/tracing.py lines=1-27
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
```
**분석 / Analysis (Level 2)**
- L1-6: 구조 설명 — api 는 자체 DB 에 스팬 저장, 다른 컨테이너는 OTLP/HTTP 로 api 수신기(`POST /v1/traces`)에 보낸다 → 단일 trace 로 조회.
- L24 `tracer`: 모듈 수준 트레이서. Provider 가 나중에(`setup`) 설정돼도 OTel API 의 프록시 트레이서가 위임하므로 동작한다.
- L25-26 `STATS`: 파이프라인 상태 화면용 카운터(`app/api/observe.py:314-322`).
- L27: 트레이싱 제외 경로 — 헬스·메트릭·수신기 자체(자기 자신을 추적하는 무한 루프 방지).
**짚어볼 점 / Watch-outs**
- L27 접두 비교(L188 `startswith`)라 `/health` 가 `/healthz` 도 포함한다(의도와 일치). 다만 `/metrics-foo` 처럼 같은 접두의 미래 경로도 제외된다.
- `STATS` 는 워커별 메모리 — 다중 워커에서 화면 값은 요청을 받은 워커 것만.

```python path=app/core/tracing.py lines=30-52
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
```
**분석 / Analysis (Level 2)**
- L30-35: id 를 고정 폭 16진수로, 나노초를 UTC datetime 으로.
- L38-45 `mask`: 문자열·리스트만 PII 마스킹(`guards.mask_pii`), 그 외 타입은 그대로. 지연 import 로 core → services 순환을 피한다.
- L48-52 `_clean_attrs`: 속성 dict 전체에 마스킹 적용.
**짚어볼 점 / Watch-outs**
- 계층 위반: core 모듈이 services 모듈(`guards`)을 import 한다 — 지연 import 로 순환은 피했지만 의존 방향이 거꾸로다(`app/core/logs.py:20` 도 동일). 마스킹 함수를 core 로 내리는 편이 깔끔하다.

```python path=app/core/tracing.py lines=55-95
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
```
**분석 / Analysis (Level 2)**
- L55-65 `store_spans`: 다른 컨테이너가 보낸 스팬도 저장 시점에 다시 마스킹(신뢰 경계에서 재적용 — 좋은 원칙). `db.merge` 는 PK(span id) 기준 upsert.
- L68-92 `DbSpanExporter.export`: OTel `ReadableSpan` → dict 변환 → 저장. 실패하면 카운트·경고 로그 후 `FAILURE`(OTel 이 재시도하지 않음).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L64 `db.merge` 는 행마다 SELECT 후 INSERT/UPDATE — 배치 512개 스팬이면 1000+ 쿼리. 대량 트래픽에서 익스포터가 병목이 된다(`bulk_insert` 또는 `INSERT ... ON CONFLICT` 고려).
- [위험:낮음] `merge` upsert 때문에 OTLP 수신기 토큰을 가진 쪽(데모 컨테이너)이 같은 span id 로 api 자신의 스팬을 **덮어쓸** 수 있다(추적 데이터 변조). 수신 스팬은 INSERT-only 가 안전하다.
- `SPAN_PROCESSOR=simple`(시험 기본, `test/conftest.py:16`)이면 L127 이 `SimpleSpanProcessor` 를 쓰고, 스팬 종료 시 **요청 스레드(또는 이벤트 루프)에서 동기 DB 쓰기**가 일어난다 — 운영에서 simple 을 쓰면 이벤트 루프를 막는다. `.env.example` 은 batch.

```python path=app/core/tracing.py lines=98-112
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
```
**분석 / Analysis (Level 2)**
- L98-112 `ingest_token`: 환경변수 우선, 없으면 공유 볼륨 파일(`data_dir/keys/otlp-ingest.token`)을 읽고, 없으면 `O_EXCL` 로 생성(다중 워커 동시 생성 방지).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 하드닝] L109 파일 권한이 `0o644` — 같은 디렉터리의 서명 키·암호화 키는 `0o600`(`app/core/keys.py:29`)인데 이 토큰만 다른 사용자도 읽을 수 있다. 모든 컨테이너가 같은 uid(10001, Dockerfile)로 실행되므로 0600 으로도 공유된다.
- [위험:낮음 · 경쟁] L105-112 다른 워커가 `O_EXCL` 생성 후 **쓰기 전**에 이 워커가 L105 `exists()` → L106 `read_text()` 를 하면 빈 문자열을 반환한다. 빈 토큰은 수신기 비교(`app/api/observe.py:97`)에서 `None != ""` 라 무해하지만, `setup_for_service`(L144) 가 빈 토큰으로 OTLP 헤더를 고정하면 그 컨테이너의 스팬은 계속 401 로 버려진다. entrypoint 시드가 먼저 생성하므로(`app/seed.py:373`) 실제 발생 확률은 낮다.
- L107 `suppress(OSError)` 는 생성 실패(권한·읽기 전용 볼륨)를 삼키고 빈 문자열을 돌려줄 수 있다 — 데모 컨테이너는 볼륨을 `:ro` 로 마운트한다(`docker-compose.yml`), 그래서 생성은 api 쪽에서만 성공해야 한다.

```python path=app/core/tracing.py lines=115-144
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
```
**분석 / Analysis (Level 2)**
- L118-123: 모듈 전역 플래그로 1회만 설정 — 시험·재import 시 Provider 중복 설정 방지(OTel 은 전역 Provider 재설정을 거부하고 경고를 낸다).
- L124: Resource 속성(`service.name`) — 스팬의 서비스 구분 기준.
- L125-127: 내장 DB 익스포터(배치 또는 단순).
- L128-133: OTLP 엔드포인트가 있으면 외부 Collector 로도 배치 전송.
- L138-144 `setup_for_service`: 별도 컨테이너는 DB 익스포트 없이 api 의 OTLP 수신기로만 보낸다(Bearer = ingest token). 같은 프로세스(`AGENTNEXUS_ROLE` 미설정·`api`)면 아무것도 안 한다.
**짚어볼 점 / Watch-outs**
- L144 대상 주소로 `idp_internal_url` 을 재사용한다 — 이름상 IdP 주소인데 수신기 주소로도 쓴다. compose 에서는 둘 다 `http://api:8000` 이라 맞지만, IdP 를 분리 배포하면 스팬이 엉뚱한 곳으로 간다(전용 설정 필요).
- L128 OTLP 헤더 `otlp_headers or {}` — 외부 Collector 인증 헤더를 설정으로 줄 방법이 없다(api 자신의 `otlp_endpoint` 경로).

```python path=app/core/tracing.py lines=147-173
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
```
**분석 / Analysis (Level 2)**
- L147-157 `span()`: 현재 컨텍스트에 자식 스팬을 열고, None 아닌 속성만 마스킹해 설정. 예외 시 상태를 ERROR 로 표시하고 **재발생**(삼키지 않음 — 올바름).
- L160-164: 현재 스팬의 (trace_id, span_id) 16진수.
- L167-173 `context_from`: 저장된 id 로 원격 부모 컨텍스트를 만든다 — 승인 후 재개된 실행이 같은 trace 에 이어 붙도록(`NonRecordingSpan` + `is_remote=True` + SAMPLED 플래그).
**짚어볼 점 / Watch-outs**
- L171 `int(trace_id, 16)` — 저장 값이 16진수가 아니면 `ValueError`. 내부에서 만든 값만 들어오므로 수용.
- L156 `type(exc).__name__` 만 상태 설명에 남긴다 — 메시지를 넣지 않아 시크릿 유출을 피한다(의도적).

```python path=app/core/tracing.py lines=176-226
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
```
**분석 / Analysis (Level 2)**
- L176-185: 순수 ASGI 미들웨어 클래스. `router` 프로퍼티로 감싼 Starlette 앱의 lifespan 을 위임(`app/builtin/mcp_servers.py:329` 같은 감싸기에 필요).
- L187-189: http 가 아니거나(websocket·lifespan) 제외 경로면 통과.
- L190-193: 요청 헤더에서 `traceparent` 추출 → 컨텍스트 attach(이후 생성 스팬의 부모).
- L198-202: 서버 스팬(`SpanKind.SERVER`), 메서드·마스킹된 경로·구성요소 속성, trace id 계산.
- L204-211 `_send` 래퍼: 응답 시작 메시지를 가로채 상태 코드·첫 바이트 시각 기록, 응답 헤더에 trace id 추가.
- L212-221: 앱 실행 후 라우트 템플릿으로 스팬 이름 갱신(`GET /api/v1/x/{id}` — 카디널리티 억제), 5xx 면 ERROR.
- L222-226: 컨텍스트 detach, 요청 메트릭·SLI 기록(첫 바이트 기준 지연 — SSE 도 첫 응답까지).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L190 `v.decode()` 는 UTF-8 — HTTP 헤더는 Latin-1 이 허용되므로 비 UTF-8 바이트가 오면 `UnicodeDecodeError` 로 **앱 실행 전에** 예외가 나 500 이 된다(공격자가 쉽게 유발 가능). `decode("latin-1")` 이 안전하다.
- L195 `status_code = 500` 초기값: 앱이 응답을 시작하기 전에 예외가 나면 500 으로 기록(정확). 클라이언트가 중간에 끊으면(취소) 실제로는 응답 시작 후 상태가 기록된다.
- L224-226 메트릭 기록이 `finally` 안에 있어, `metrics.observe_request` 가 예외를 던지면 원래 예외를 덮는다 — `slo.observe` 는 단순 카운터라 위험은 낮다.

## 파일 평가 / File Assessment
- 강점: (1) 신뢰 경계(수신 스팬)에서 마스킹 재적용. (2) traceparent 추출·주입·재개 컨텍스트 복원까지 일관된 분산 추적. (3) 라우트 템플릿으로 스팬 이름 카디널리티 억제.
- 위험: (1) [낮음] 헤더 UTF-8 디코드 실패 → 사전 인증 500. (2) [낮음] `merge` upsert 로 스팬 덮어쓰기·성능 병목. (3) [낮음] 수신 토큰 파일 0644·생성 경쟁.
- 개선 권고(영향순): (1) `decode("latin-1")`. (2) 수신 스팬은 INSERT-only + 배치 삽입. (3) 토큰 파일 0600, 수신기 주소 전용 설정.
- 재읽기 권고 레벨: 3.
