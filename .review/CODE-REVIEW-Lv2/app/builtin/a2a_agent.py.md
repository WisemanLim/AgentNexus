> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [builtin_Abstract.md](builtin_Abstract.md)

### 06. app/builtin/a2a_agent.py
**파일 요약 / File Summary**
- 역할 / Role: 데모 원격 A2A 에이전트 '관측 분석 에이전트'. 자기 워크로드 신원(client_credentials)으로 게이트웨이를 경유해 Dynatrace MCP 도구·도메인 모델을 호출하고 장애 분석 보고서를 Artifact 로 반환. 게이트웨이에 닿지 못하면 내장 데이터로 오프라인 분석.
- 진입점 거리 / Distance: 0 (`uvicorn app.builtin.a2a_agent:app --port 8200` — compose 서비스 `a2a-demo`)
- LOC: 192
- 주요 export: `app`, `server`(A2AServer), `card()`, `analyze()`, `AGENT_ID`
- 의존(내부): `app.builtin.{demo_data,mcp_auth}`, `app.core.{tracing,config,http}`, `app.services.a2a_proto.build_card`, `app.services.a2a_server.{A2AServer,TaskContext}`(본 리뷰 미선정 — 부록 A)
- 외부 패키지: starlette
- 부수효과 / Side effects: 모듈 전역 토큰 캐시 `_TOKEN`, `data_dir/workloads/a2a-demo.json` 읽기, 게이트웨이로 아웃바운드 HTTP
- 동시성 / Concurrency: asyncio(요청별 Task 실행은 `A2AServer` 가 관리)
- 테스트 커버리지 흔적: `test/test_a2a.py`(send·stream·input-required·push·SDK 상호운용), `test/conftest.py:68-69`

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/builtin/a2a_agent.py lines=1-28
"""데모 원격 A2A 에이전트 — '관측 분석 에이전트(Observability Analyst)' (PRD 4.3 시나리오 2, FR-R00~R06).

자체 워크로드 신원(client_credentials, FR-I04)으로 AgentNexus 게이트웨이를 경유해 Dynatrace MCP 도구와 도메인 특화 모델을
호출(FR-L03 '원격 에이전트가 토큰으로 모델·API 접근')하고, 장애 분석 보고서를 Artifact 로 돌려준다.
게이트웨이에 닿지 못하면 내장 가상 데이터로 분석한다(오프라인 시연).
기동: uvicorn app.builtin.a2a_agent:app --port 8200  (compose 서비스 a2a-demo, 내부 네트워크 전용)
"""
import json
import re
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.builtin import demo_data
from app.builtin.mcp_auth import verify
from app.core import tracing
from app.core.config import settings
from app.core.http import async_client
from app.services.a2a_proto import build_card
from app.services.a2a_server import A2AServer, TaskContext

AGENT_ID = "observability-analyst"
SERVICES = sorted({p["service"] for p in demo_data.DYNATRACE_PROBLEMS} | {s for s, _ in demo_data.METRICS})
_TOKEN: dict = {}
```
**분석 / Analysis (Level 2)**
- L1-7: 이 에이전트가 PRD 시나리오 2(원격 에이전트가 토큰으로 모델·API 접근, FR-L03)를 재현하는 대역임을 설명.
- L19: `verify` — 게이트웨이 IdP JWKS 로 Bearer 토큰 검증(`app/builtin/mcp_auth.py`).
- L22: `async_client` — traceparent 를 주입하는 httpx 팩토리(`app/core/http.py:125-127`).
- L26-27: 에이전트 id, 지원 서비스 목록(데모 데이터의 서비스 이름 합집합 — set comprehension 으로 중복 제거 후 정렬).
- L28 `_TOKEN`: 게이트웨이 액세스 토큰 캐시(`token`, `exp`).

```python path=app/builtin/a2a_agent.py lines=31-40
def card() -> dict:
    return build_card(
        name="관측 분석 에이전트 (Observability Analyst)",
        description="Dynatrace 문제·메트릭을 분석해 장애 원인·영향·조치를 보고하는 원격 A2A 에이전트 (데모)",
        url=settings.a2a_demo_url.rstrip("/") + "/", version="1.0.0", rest_url=settings.a2a_demo_url.rstrip("/") + "/rest",
        skills=[{"id": "incident-analysis", "name": "장애 분석", "description": "서비스 장애의 근본 원인·영향 분석 보고서",
                 "tags": ["sre", "incident", "dynatrace", "장애"], "examples": ["orders-api 장애 원인 분석해줘"]},
                {"id": "metric-trend", "name": "메트릭 추세", "description": "서비스 지연·오류율 추세 요약",
                 "tags": ["metrics", "observability"]}],
        provider={"organization": "한결테크 SRE (가상)", "url": "https://sre.hangyeol.example"})
```
**분석 / Analysis (Level 2)**
- L31-40: Agent Card 생성 — 0.3·1.0 이중 카드(`app/services/a2a_proto.py:323-345`). 스킬 2개(`incident-analysis`·`metric-trend`), 태그·예시는 게이트웨이의 스킬 기반 라우팅(`/a2a/_skill/...`)과 카탈로그 검색에 쓰인다. `rest_url` 로 HTTP+JSON 바인딩도 광고.
**짚어볼 점 / Watch-outs**
- 카드 생성 함수가 요청마다 호출되면(서버 구현 의존) `settings` 변경이 즉시 반영된다 — 상수 dict 보다 유연하다.

```python path=app/builtin/a2a_agent.py lines=43-50
async def _auth(request: Request) -> dict | None:
    hdr = request.headers.get("authorization", "")
    if not hdr.lower().startswith("bearer "):
        return None
    try:
        return await verify(hdr[7:].strip(), f"a2a:{AGENT_ID}")
    except Exception:  # noqa: BLE001
        return None
```
**분석 / Analysis (Level 2)**
- L43-50: 인바운드 인증 — `Authorization: Bearer` 만 받고, 토큰의 audience 가 `a2a:observability-analyst` 인지 검증(`verify(token, audience)`). 게이트웨이가 RFC 8693 교환으로 이 audience 토큰을 만들어 준다(`app/services/a2a_gateway.py:262-270`). 실패하면 `None` → 서버가 401.
**짚어볼 점 / Watch-outs**
- L49 `except Exception: return None` — 서명 오류와 JWKS 조회 장애(네트워크)를 구분하지 않고 모두 401 로 만든다. 장애 시 원인 파악이 어렵다(로그 1줄 권장).

```python path=app/builtin/a2a_agent.py lines=53-77
# ── 게이트웨이 경유 호출 (워크로드 토큰) ──
def _credentials() -> dict | None:
    if settings.a2a_demo_client_secret not in ("", "CHANGE_ME"):
        return {"client_id": "wl-a2a-demo", "client_secret": settings.a2a_demo_client_secret}
    path = Path(settings.data_dir) / "workloads" / "a2a-demo.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


async def _gateway_token() -> str | None:
    if _TOKEN.get("exp", 0) - 30 > time.time():
        return _TOKEN["token"]
    cred = _credentials()
    if not cred:
        return None
    async with async_client(timeout=10) as http:
        res = await http.post(settings.gateway_url.rstrip("/") + "/idp/token", data={
            "grant_type": "client_credentials", "client_id": cred["client_id"], "client_secret": cred["client_secret"]})
    if res.status_code != 200:
        return None
    body = res.json()
    _TOKEN.update(token=body["access_token"], exp=time.time() + body.get("expires_in", 300))
    return _TOKEN["token"]
```
**분석 / Analysis (Level 2)**
- L54-61 `_credentials`: 워크로드 시크릿 — 환경변수(K8s Secret 주입) 우선, 없으면 api 가 공유 볼륨에 써 둔 `workloads/a2a-demo.json`. 읽기 실패는 `None`.
- L64-77 `_gateway_token`: 캐시된 토큰이 만료 30초 이상 남았으면 재사용, 아니면 `/idp/token` client_credentials 로 발급 후 캐시. `async with` 로 클라이언트를 닫는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L65-77 동시 요청이 같은 시점에 만료를 보면 각자 토큰을 발급한다(thundering herd) — 기능상 문제는 없고 IdP 부하만 늘어난다. `asyncio.Lock` 으로 단일 발급 가능.
- L76 `body["access_token"]` — 200 이지만 형식이 다르면 `KeyError` 가 `analyze` 까지 전파되어 Task 가 실패한다.
- L55 `"CHANGE_ME"` 를 미설정으로 취급 — `app/core/keys.py:43-46` 와 같은 규칙(일관성 좋음).

```python path=app/builtin/a2a_agent.py lines=80-107
async def _mcp(tool: str, args: dict) -> dict | list | None:
    token = await _gateway_token()
    if not token:
        return None
    async with async_client(timeout=20) as http:
        res = await http.post(settings.gateway_url.rstrip("/") + "/mcp/dynatrace", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                     "X-AgentNexus-Agent": f"a2a:{AGENT_ID}"})
    body = res.json() if res.status_code == 200 else {}
    sc = (body.get("result") or {}).get("structuredContent")
    return sc.get("result", sc) if isinstance(sc, dict) and "result" in sc else sc


async def _llm(prompt: str) -> str | None:
    token = await _gateway_token()
    if not token:
        return None
    async with async_client(timeout=30) as http:
        res = await http.post(settings.gateway_url.rstrip("/") + "/v1/chat/completions", json={
            "model": "domain-local", "messages": [
                {"role": "system", "content": "너는 SRE 장애 분석가다. 근거를 요약하라."},
                {"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {token}", "X-AgentNexus-Data-Class": "internal",
                     "X-AgentNexus-Agent": f"a2a:{AGENT_ID}"})
    if res.status_code != 200:
        return None
    return res.json()["choices"][0]["message"]["content"]
```
**분석 / Analysis (Level 2)**
- L80-91 `_mcp`: 게이트웨이 `/mcp/dynatrace` 로 JSON-RPC `tools/call`. `X-AgentNexus-Agent: a2a:observability-analyst` 헤더로 호출 에이전트를 표시. 응답의 `structuredContent.result` 를 꺼낸다(FastMCP 가 리스트 반환값을 `{"result": [...]}` 로 감싼다).
- L94-107 `_llm`: 게이트웨이 OpenAI 호환 `/v1/chat/completions` 로 `domain-local` 모델 호출, 데이터 등급 헤더 `internal`.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L87 `Accept` 에 `text/event-stream` 을 포함한다 — 게이트웨이가 SSE 로 답하면 L89 `res.json()` 이 `JSONDecodeError` 로 실패한다(현재 게이트웨이는 진행률 토큰이 없으면 JSON 으로 응답 — `app/api/mcp.py:121-125`).
- L89 4xx/5xx 면 `{}` → `None` 반환 → 호출자는 "오프라인" 으로 오인하지 않고 L150-153 에서 내장 데이터로 대체한다. 승인 필요(-32001) 같은 정책 응답도 조용히 내장 데이터로 바뀐다.
- L107 응답 구조 가정(`choices[0].message.content`) — 형식이 다르면 `KeyError`/`IndexError`.

```python path=app/builtin/a2a_agent.py lines=110-127
def _service_in(text: str, data: dict) -> str | None:
    if data.get("service") in SERVICES:
        return data["service"]
    for s in SERVICES:
        if s in text:
            return s
    lowered = text.lower()
    for key, svc in (("주문", "orders-api"), ("order", "orders-api"), ("결제", "payment-gateway"),
                     ("payment", "payment-gateway"), ("정산", "settlement-batch")):
        if key in lowered:
            return svc
    return None


def _text_and_data(message: dict) -> tuple[str, dict]:
    text = " ".join(p.get("text", "") for p in message.get("parts", []) if p.get("kind") == "text")
    data = next((p.get("data") or {} for p in message.get("parts", []) if p.get("kind") == "data"), {})
    return text, data
```
**분석 / Analysis (Level 2)**
- L110-121 `_service_in`: 구조화 데이터(`data.service`) > 본문의 서비스 이름 > 한글·영문 키워드 매핑 순으로 대상 서비스 추출.
- L124-127 `_text_and_data`: A2A 메시지 parts 중 text 는 합치고 첫 data 파트를 꺼낸다. `next(..., {})` 로 기본값 지정.

```python path=app/builtin/a2a_agent.py lines=130-169
async def analyze(ctx: TaskContext) -> None:
    text, data = _text_and_data(ctx.message)
    service = _service_in(text, data)
    if service is None:
        reply = await ctx.ask("어떤 서비스의 장애를 분석할까요? (예: " + ", ".join(SERVICES) + ")")
        if reply is None:
            await ctx.update("failed", "입력 대기 시간이 초과되었습니다.")
            return
        service = _service_in(*_text_and_data(reply))
        if service is None:
            await ctx.update("rejected", "알 수 없는 서비스입니다.")
            return
    await ctx.update("working", f"{service} 의 Dynatrace 문제·메트릭을 조회합니다.")
    via = "gateway"
    problems = await _mcp("list_problems", {"service": service, "status": "ALL"})
    if problems is None:
        via = "offline"
        problems = [p for p in demo_data.DYNATRACE_PROBLEMS if p["service"] == service]
    detail = None
    if problems:
        detail = await _mcp("get_problem", {"problem_id": problems[0]["id"]}) if via == "gateway" else None
        detail = detail or next(p for p in demo_data.DYNATRACE_PROBLEMS if p["id"] == problems[0]["id"])
    metrics = (await _mcp("query_metrics", {"service": service}) if via == "gateway" else None) or \
        {"series": demo_data.METRICS.get((service, "p95_latency_ms"), [])}
    series = metrics.get("series") or []
    trend = f"p95 {series[0]}→{series[-1]}ms" if len(series) >= 2 else "메트릭 없음"
    root = (detail or {}).get("root_cause", "근본 원인 미상")
    impacted = (detail or {}).get("impacted", [])
    summary = await _llm(f"[1] 서비스 {service} 문제 {(detail or {}).get('title', '-')}\n[2] 근본 원인: {root}\n"
                         f"[3] 영향: {', '.join(impacted) or '-'}\n[4] 추세: {trend}") if via == "gateway" else None
    report = (f"# 장애 분석 보고서 — {service}\n\n"
              f"- 문제: {(detail or {}).get('id', '-')} {(detail or {}).get('title', '열린 문제 없음')}\n"
              f"- 근본 원인: {root}\n- 영향 서비스: {', '.join(impacted) or '-'}\n- 메트릭 추세: {trend}\n"
              f"- 권고 조치: {_advice(root)}\n\n## 분석 요약\n{summary or '(오프라인 분석) ' + root}\n\n"
              f"_데이터 경로: {via} (AgentNexus 게이트웨이 경유 여부)_\n")
    await ctx.artifact("incident-report.md", [{"kind": "text", "text": report}], "장애 분석 보고서 (Markdown)")
    await ctx.artifact("incident-data", [{"kind": "data", "data": {
        "service": service, "problem_id": (detail or {}).get("id"), "root_cause": root, "impacted": impacted,
        "series": series, "via": via}}])
    await ctx.update("completed", f"{service} 분석 완료 — 근본 원인: {root}")
```
**분석 / Analysis (Level 2)**
- L131-141: 서비스를 못 찾으면 `ctx.ask(...)` 로 `input-required` 상태를 만들고 사용자 답을 기다린다(A2A multi-turn). 시간 초과면 `failed`, 답해도 모르면 `rejected`(`test/test_a2a.py:84-90`).
- L142: `working` 상태 갱신(스트리밍 클라이언트에 status-update 이벤트).
- L143-153: 게이트웨이 경유로 문제 목록·상세·메트릭 조회, 실패하면 `via="offline"` 로 내장 데이터 사용.
- L155-159: 추세 문자열, 근본 원인·영향, 게이트웨이 경로일 때만 LLM 요약.
- L160-168: Markdown 보고서 Artifact + 구조화 data Artifact 2개 → `completed`.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 버그] L151 `next(p for p in demo_data.DYNATRACE_PROBLEMS if p["id"] == problems[0]["id"])` 에 기본값이 없다. 게이트웨이 경로(`via == "gateway"`)에서 `list_problems` 는 성공했는데 `get_problem` 이 `None` 을 반환하고, 그 문제 id 가 내장 데이터에 없으면(실제 Dynatrace 로 교체한 경우) `StopIteration` 이 코루틴 안에서 `RuntimeError` 로 바뀌어 Task 가 실패한다. `next(..., None)` 로 바꿔야 한다.
- L152 `(... if via == "gateway" else None) or {...}` — 조건식과 `or` 가 섞여 읽기 어렵다. 우선순위상 의도대로 동작하지만 분리 권장.
- L163 `_advice(root)` 는 근본 원인 문자열의 한글 키워드로 조치를 고른다 — 원격 데이터가 영어면 항상 기본 문구.

```python path=app/builtin/a2a_agent.py lines=172-192
def _advice(root: str) -> str:
    if re.search("인덱스", root):
        return "누락 인덱스 생성 마이그레이션 재실행 후 p95 재측정, 필요 시 v2.8 롤백"
    if re.search("인증서", root):
        return "샌드박스 인증서 갱신 및 만료 알림(30일 전) 설정"
    if re.search("중복", root):
        return "배치 분산 락 적용 PR 머지 및 재처리"
    return "추가 로그·트레이스 확인"


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "agent": AGENT_ID})


server = A2AServer(card, analyze, authenticate=_auth, blocking_timeout=30)
app = Starlette(routes=[Route("/.well-known/agent-card.json", server.card_endpoint),
                        Route("/", server.handle, methods=["POST"]),
                        Route("/rest/{path:path}", server.rest, methods=["GET", "POST", "DELETE"]),  # HTTP+JSON 바인딩 (D16-①)
                        Route("/healthz", _health)])
tracing.setup_for_service("agentnexus-a2a-demo")
app = tracing.TracingMiddleware(app, component="a2a-demo")
```
**분석 / Analysis (Level 2)**
- L172-179 `_advice`: 정규식 대신 단순 포함 검사로 충분한 곳에 `re.search` 를 쓴다(동작은 같다).
- L182-183: 헬스 체크.
- L186: `A2AServer(card, analyze, authenticate=_auth, blocking_timeout=30)` — 카드 함수·실행기·인증 콜백 주입. 블로킹 send 는 최대 30초 기다린다.
- L187-190: 라우트 — 카드(`/.well-known/agent-card.json`), JSON-RPC(`POST /`), HTTP+JSON(`/rest/{path}`), 헬스.
- L191-192: 트레이싱 설정·미들웨어.
**짚어볼 점 / Watch-outs**
- L187 카드 엔드포인트는 인증이 없다 — A2A 사양상 공개 카드는 정상. 확장 카드(`GetExtendedAgentCard`)는 서버 구현에서 인증해야 한다(`app/services/a2a_server.py`, 본 리뷰 미선정).

## 파일 평가 / File Assessment
- 강점: (1) 원격 에이전트가 사용자 토큰이 아닌 자기 워크로드 신원으로 게이트웨이를 다시 경유하는 구조를 정확히 시연(감사·정책이 두 번 적용). (2) 오프라인 폴백으로 데모 안정성 확보. (3) input-required multi-turn 흐름 구현.
- 위험: (1) L151 기본값 없는 `next`(버그). (2) 게이트웨이 정책 거부·오류가 조용히 오프라인 데이터로 대체되어 결과의 출처가 흐려진다(`via` 표시는 있음). (3) 토큰 캐시 동시 갱신.
- 개선 권고(영향순): (1) `next(..., None)`. (2) `_mcp` 가 오류 코드를 반환해 정책 거부를 보고서에 표시. (3) 토큰 발급에 `asyncio.Lock`.
- 재읽기 권고 레벨: 3 — 데모 에이전트이므로 흐름만 파악하면 된다.
