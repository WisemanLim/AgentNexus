> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../../INDEX.md) · 디렉터리 개요: [services_Abstract.md](services_Abstract.md) · 1차 리뷰: [a2a_gateway.py.md](../../../../CODE-REVIEW-Lv2/app/services/a2a_gateway.py.md)

### 12. app/services/a2a_gateway.py
**파일 요약 / File Summary**
- 역할 / Role: A2A 프록시 핵심 — Agent Card 조회·검증·해시 캐시·ETag 재검증·변경 감지, 신뢰 등급·정책·한도 판정, 카드 보안 스킴에 맞춘 업스트림 인증(RFC 8693 교환·apiKey·OAuth2 client_credentials), 0.3↔1.0 변환·바인딩(JSON-RPC·HTTP+JSON·gRPC) 선택, SSE 중계, 입출력 가드, Task 영속. 2차에서 **Task 소유권 판정**(D18-①), **클라이언트 푸시 URL 검사**(D17-④), **푸시 토큰↔Task 묶음 검사**, **푸시 중계 백그라운드 재시도**(D19-②), **grpcs 채널 자격 증명**(D19-②), **감시기 리스·순환 폴링**(D18-②) 이 더해졌다.
- 진입점 거리 / Distance: 1 (`app/main.py:105-107` 감시기 Task 생성) · 2 (`app/api/a2a.py:283-294` 푸시 웹훅, `:297-351` JSON-RPC·REST 프록시, `:222` 플랫폼 서버 `push_check=push_url_problem`, `:686-699` 콘솔 취소)
- LOC: 886 (스냅샷 21:03 기준 `wc -l`, sha1 `9d47b24b…` = `scratchpad/snapshot-start.sha`) · 1차 819 → **+67**. 이 보고서 작성 뒤(21:30) Epic 51 패치가 적용되어 현재 디스크는 909줄이다 — **본문의 모든 줄 번호는 스냅샷 기준**이며, 현재 파일에 Epic 51 패치(`scratchpad/e51-patch-a2a.py`·`e51-patch-a2a-2.py`)를 역적용하면 sha1 이 스냅샷과 일치함을 확인했다.
- 리뷰 후 변경(참고 · 스냅샷 밖, 라인 리뷰하지 않음): 현재 디스크에서 아래 지적 일부가 고쳐졌다 — L568 DNS 해석 스레드풀화(현재 :569), 웹훅 전달 직전 URL 재검사(현재 :853 — 단 검사 시점 해석과 httpx 연결 시점 해석이 여전히 별개라 DNS rebinding·해석 실패 통과는 남는다), 푸시 수신 시 에이전트 상태 확인·`_sanitize_in`(현재 :834 부근), `_principal_for` 사용자 상태 확인(현재 :867), 감시기 에이전트 상태 확인·`_sanitize_in`(현재 :891 부근), 원격 에이전트별 푸시 콜백 기준 주소 `push_callback_base`(현재 :641). 아래 "2차 상태" 는 스냅샷 판정이며 재검증이 필요하다.
- 주요 export: `A2AError`, `AgentRef`, `CallContext`, `proxy`, `fetch_card`, `apply_card`, `refresh_card`, `gateway_card`, `card_url_for`, `ensure_exchange_policy`, `load_agent`, `record`, `route_by_skill`, `receive_push`, `PushRejected`, `verify_push_jwt`, `push_url_problem`(신규), `poll_once`, `watcher`, `upstream_headers`, `tls_kwargs`, `RESERVED`
- 의존(내부): `app.core.{logs,metrics,auth,config,context,http,tracing,ids}`, `app.db`, `app.models`, `app.services.{a2a_proto,a2a_rest,audit,governance,guards,identity,ratelimit,secrets,notify}`, 지연 import `app.services.{a2a_grpc,tls,scheduler}`·`app.core.http.{internal_url,sync_client,post_with_retry}`
- 외부 패키지: httpx, sqlalchemy(`func.coalesce` 신규), starlette, pyjwt(푸시 검증), opentelemetry
- 부수효과 / Side effects: 원격 에이전트·클라이언트 웹훅 HTTP(웹훅은 이제 백그라운드 재시도), 원격 gRPC 채널, DB 쓰기(카드·Task·`last_polled_at`·사용량·결정 로그·카드 점검 이력), 알림·감사, 워커 전역 `_TOKENS`·`_JWKS_CACHE`·`_PUSH_RELAYS`, settings 행 리스 `lease.a2a_watch`
- 동시성 / Concurrency: asyncio + `run_in_threadpool`(동기 DB). 감시기는 워커마다 돌지만 리스 보유 워커만 `poll_once`. 푸시 중계는 `asyncio.create_task` 로 요청과 분리(강참조 집합으로 GC 방지). **동기 DNS 해석이 이벤트 루프에서 실행되는 지점이 새로 생겼다**(L568).
- 테스트 커버리지 흔적: `test/test_task_ownership.py:51`(게이트웨이 소유권 C44-03), `test/test_policy_hardening.py:46`(푸시 URL C43-02), `test/test_review_fixes.py:358`(푸시↔Task 묶음 C39-18)·`:378`(감시기 순환 C39-19)·`:225`(토큰 캐시 C39-10), `test/test_multiworker.py:77`(리스 C45-04), `test/test_lv2_hardening.py:76`(grpcs 사설 CA C49-02)·`:178`(재시도 C49-06), `test/test_hardening.py:147`·`:161`(가드), `test/test_a2a.py`, `test/test_a2a_rest.py`, `test/test_a2a_grpc.py`
- 2차 변경 요약 / Changes since 1st review (1차 인용 원문과 현재 코드 대조):
  - L17 `func` import → L859 `order_by(func.coalesce(last_polled_at, created_at))` + L860-862 선택 즉시 `last_polled_at` 기록 (C39-19, 1차 기아 지적 해결).
  - L80-81 `grpcs://` 이면 `tls.grpc_credentials` → L348·L667·L699 로 gRPC 채널에 전달 (Epic 49 C49-02).
  - L559-560 `_task_id_of`·`_check_task_owner`, L606-609 `list` 결과를 내 Task 로 거름, L613-636 보조 함수 (Epic 44 D18-①).
  - L568-569·L639-646 `push_url_problem` (Epic 43 D17-④).
  - L803-808·L817-822 푸시 본문 Task id ↔ 토큰 행 일치 검사 (C39-18, 1차 무결성 지적 해결).
  - L824-837 클라이언트 웹훅 중계를 `post_with_retry` 백그라운드로 + 실패 경고 로그 (Epic 49, 1차 `suppress(Exception)` 제거).
  - L879-886 감시기 리스 (Epic 45 D18-②).
  - 그 밖의 블록(카드·인증·바인딩·가드·스트림)은 1차 원문과 동일하고 줄 번호만 이동했다.
- **스냅샷 고지 (2차 리뷰 도중 소스 변경)**: 이 보고서의 줄 번호·인용·판정은 21:03 스냅샷(sha1 `9d47b24b…`, 886줄 — `scratchpad/snapshot-start.sha:2` 와 일치)을 기준으로 한다. 리뷰 도중 **21:30:59** 에 Epic 51(`.review/round2/VERIFY.md` CR2-01·INFRA 4, C51-03·C51-04) 작업으로 디스크 파일이 바뀌었다(909줄, sha1 `4ca0285e…`). 관찰한 변경 — 3차 검증 대상이며 아래 판정에는 반영하지 않았다: `push_url_problem` 스레드풀 호출(디스크 L569), `push_callback_base`(디스크 L572·L641-646 — 사설망 에이전트는 `GATEWAY_URL`, 그 밖은 `PUBLIC_BASE_URL`), 중계 직전 URL 재검사(디스크 L853), 푸시·폴링 `_sanitize_in`(디스크 L839·L896), `_principal_for` 의 비활성 사용자 거부(디스크 L867), 푸시 수신 시 에이전트 상태·차단 확인(디스크 L815, 한 줄만 확인 — 가능성). 시험 `test/test_review2_fixes.py:81-90`(21:19 작성)은 스냅샷 코드에 없는 `a2a_gateway.push_callback_base` 를 부르므로 스냅샷 기준으로는 `AttributeError` 로 실패한다(실행하지 않음 — 코드 대조).

## 블록 리뷰 / Block Walk (Level 2)
`max_lines_per_file`(600) 초과 → **논리 블록 단위로 분석(모든 블록 커버)**. 블록 lines= 범위가 1..886 을 연속으로 덮는다. 40줄 이하 블록은 전체 인용, 더 긴 블록(L152-220, L383-430)은 핵심 줄 원문 + 생략 표시.

```python path=app/services/a2a_gateway.py lines=1-37
"""A2A API 프록시 (FR-G08·G01, FR-R00~R06, FR-A04 게이트웨이 경유).

- Agent Card: `{url}/.well-known/agent-card.json` 조회 → 검증 → 해시 캐시 → 변경 감지(알림·카탈로그 갱신)
- 호출: 신뢰 등급·정책·한도 → 카드 securitySchemes 에 맞춘 인증 주입(bearer=RFC 8693 교환, apiKey=시크릿)
  → 클라이언트 버전(0.3/1.0)과 업스트림 버전이 다르면 변환 → SSE 중계 → Task·Artifacts 영속
- 푸시: 업스트림에는 게이트웨이 웹훅을 등록하고, 수신 시 Task 갱신 후 클라이언트 웹훅으로 중계
- 장기 Task: 감시기(watcher)가 터미널 상태까지 tasks/get 폴링 (Temporal 대체, PRD D3)
"""
import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, StreamingResponse

from opentelemetry.trace import SpanKind

from app.core import logs, metrics
from app.core.auth import Principal
from app.core.config import settings
from app.core.context import current_trace_id
from app.core.http import async_client
from app.core.tracing import span
from app.core.ids import new_id, token_urlsafe
from app.db import SessionLocal, utcnow
from app.models import A2AAgent, A2ATask, CatalogItem, RoutingDecision, TokenExchangePolicy, UsageRecord, User
from app.services import a2a_proto as ap
from app.services import a2a_rest
from app.services import audit, governance, guards, identity, ratelimit, secrets
from app.services.notify import notify
```
**분석 / Analysis (Level 2)**
- L1-8: 설계 요약 — 카드 캐시·변경 감지 → 정책·한도 → 스킴별 인증 주입 → 버전 변환 → SSE 중계 → Task 영속 → 푸시 중계 → 감시기(Temporal 대신 단순 폴링). 2차 기능(소유권·푸시 URL 검사·리스)은 docstring 에 반영되지 않았다(문서 갱신 누락, 동작 영향 없음).
- L17 `func`: 2차 신규 — 감시기 정렬식 `coalesce`(L859) 용.
- L19 `run_in_threadpool`: 동기 SQLAlchemy 세션을 이벤트 루프 밖 스레드에서 돌리는 Starlette 유틸. 이 파일의 모든 DB 접근은 이 경계를 지켜야 한다 — L568 의 동기 DNS 해석이 이 원칙을 깬 첫 사례다(아래 L549-579 블록 참고).
- L33-36: `a2a_grpc`·`tls`·`scheduler` 는 함수 안에서 지연 import 한다(L76, L347, L666, L880) — `a2a_grpc` 가 반대로 `A2AError` 를 지연 import(`app/services/a2a_grpc.py:94`)하는 순환 의존을 피하기 위함.

```python path=app/services/a2a_gateway.py lines=38-67
RESERVED = {"platform", "push", "_skill"}


class A2AError(Exception):
    def __init__(self, code: int, message: str, data: dict | None = None, http_status: int = 200):
        super().__init__(message)
        self.code, self.message, self.data, self.http_status = code, message, data or {}, http_status

    def response(self, rid) -> JSONResponse:
        err = {"code": self.code, "message": self.message, **({"data": self.data} if self.data else {})}
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": err}, status_code=self.http_status)


@dataclass
class AgentRef:
    id: str
    name: str
    url: str
    card: dict
    trust_tier: str
    auth: dict
    status: str
    info: dict = field(default_factory=dict)

    @classmethod
    def of(cls, a: A2AAgent) -> "AgentRef":
        return cls(a.id, a.name, a.url, dict(a.card or {}), a.trust_tier, dict(a.auth or {}), a.status,
                   ap.card_info(a.card or {}) if a.card else {})
```
**분석 / Analysis (Level 2)**
- L38 `RESERVED`: 에이전트 id 로 쓸 수 없는 경로 세그먼트 — `/a2a/platform`·`/a2a/push/{token}`·`/a2a/_skill/{skill}` 라우트(`app/api/a2a.py:241,282,297`)와 충돌 방지.
- L41-48 `A2AError`: JSON-RPC 오류 + HTTP 상태. 기본 `http_status=200` 은 JSON-RPC 관례(전송은 성공, 본문에 error). 정책 거부·인증 실패처럼 HTTP 의미가 있는 경우만 403·502 를 준다.
- L51-65 `AgentRef`: ORM 행의 **분리 스냅샷**(1차 INDEX §4.1 패턴 1) — 세션이 닫힌 뒤 비동기 구간에서 써도 lazy load 가 일어나지 않는다. `dict(a.auth)` 는 얕은 복사라 중첩 `tls`·`push_verify` dict 는 공유되지만 `load_agent` 세션이 곧 닫히므로 실해는 없다.

```python path=app/services/a2a_gateway.py lines=68-104
# ───────────────────────── Agent Card ─────────────────────────
def card_url_for(url: str) -> str:
    base = url.rstrip("/")
    return base if base.endswith("agent-card.json") else base + "/.well-known/agent-card.json"


async def tls_kwargs(tls_cfg: dict | None, target: str, url: str) -> dict:
    """원격 에이전트 업스트림 mTLS (FR-G02) — auth.tls + 게이트웨이 정책."""
    from app.services import tls
    if tls.fast_none(tls_cfg):
        return {}
    try:
        if url.startswith("grpcs://"):  # gRPC 채널 자격 증명 — 같은 TLS 설정·mTLS 정책 (D19-②)
            return {"grpc_credentials": await run_in_threadpool(tls.grpc_credentials, tls_cfg, target, url)}
        return tls.client_kwargs(await run_in_threadpool(tls.context_for, tls_cfg, target, url))
    except tls.MtlsError as exc:
        raise A2AError(-32003, str(exc), {"code": "mtls_required"}, http_status=403) from exc


async def fetch_card(card_url: str, tls_cfg: dict | None = None) -> dict:
    tls_kw = await tls_kwargs(tls_cfg, "a2a:card", card_url)
    try:
        async with async_client(timeout=10, **tls_kw) as http:
            res = await http.get(card_url)
    except httpx.HTTPError as exc:
        raise A2AError(-32603, f"Agent Card 조회 실패: {type(exc).__name__}", http_status=502) from exc
    if res.status_code != 200:
        raise A2AError(-32603, f"Agent Card 조회 실패 ({res.status_code})", http_status=502)
    try:
        card = res.json()
    except ValueError as exc:
        raise A2AError(-32603, "Agent Card 가 JSON 이 아닙니다.", http_status=502) from exc
    errors = ap.validate_card(card)
    if errors:
        raise A2AError(-32602, "Agent Card 검증 실패", {"errors": errors}, http_status=422)
    return card
```
**분석 / Analysis (Level 2)**
- L69-71 `card_url_for`: 기준 URL 이면 `/.well-known/agent-card.json` 을 붙인다.
- L77-78 `tls.fast_none`: TLS 설정이 없고 **캐시된** mTLS 정책이 `required` 가 아니면 DB·스레드풀 없이 `{}`(`app/services/tls.py:45-48`). 캐시가 비어 있으면(`cached_mode()` None) 느린 경로로 간다.
- L80-81 (2차 신규): `grpcs://` 대상이면 httpx kwargs 대신 `{"grpc_credentials": ...}` 를 돌려준다(`app/services/tls.py:97-121` — 사설 CA·클라이언트 인증서 PEM 분리). **반환 dict 의 모양이 대상에 따라 달라지는** 설계라, 호출부가 이 키를 골라 쓰거나(`L348`) 빼고(`L699`) httpx 에 넘겨야 한다. 흔한 실수: `async_client(**tls_kw)` 에 그대로 넘기면 httpx 가 `grpc_credentials` 인자를 몰라 `TypeError`.
- L82: 그 밖에는 `ssl.SSLContext` → `{"verify": ctx}`.
- L83-84: mTLS 정책 위반·시크릿 오류는 403 `mtls_required`.
- L87-103 `fetch_card`: 조회 → 200·JSON·스키마 검증(`app/services/a2a_proto.py:303-316`). 모든 실패를 A2AError 로 감싼다(이 파일의 다른 응답 파싱 L339·L360·L684 와 대조).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L80-82 gRPC 대상이 `grpcs://` 가 아니면(`host:port`·`grpc://`) L82 로 가고, `tls.context_for` 는 https 가 아니면 `None` 을 돌려준다(`app/services/tls.py:76-77`) → 평문 gRPC 채널(`app/services/a2a_grpc.py:59-60`). 정책이 `required` 일 때만 L74-75 에서 막힌다. 평문 기본값 자체는 a2a_grpc 보고서의 [위험:중간] 항목.
- [위험:낮음] `auth.tls.verify: false` 는 HTTP 경로에서만 효과가 있고(`app/services/tls.py:53-55`) gRPC 경로는 읽지 않는다(`:97-121`) — 같은 설정이 바인딩마다 다르게 동작한다(보안상 더 엄격한 쪽이라 문서화로 충분).
- 카드 URL 이 내부망을 가리켜도 된다(egress 가드는 링크 로컬·IMDS 만 — `app/core/http.py:34-38`) — 1차와 같은 제품 결정.

```python path=app/services/a2a_gateway.py lines=105-131
def apply_card(db: Session, agent: A2AAgent, card: dict) -> bool:
    h = ap.card_hash(card)
    changed = bool(agent.card_hash) and agent.card_hash != h
    info = ap.card_info(card)
    agent.card, agent.card_hash, agent.version = card, h, card.get("version")
    agent.skills = [{"id": s.get("id"), "name": s.get("name"), "tags": s.get("tags") or []} for s in info["skills"]]
    agent.last_fetched_at = utcnow()
    agent.card_changed = agent.card_changed or changed
    if not agent.auth.get("scheme"):
        scheme = next(iter(info["schemes"].values()), {"type": "none"})
        agent.auth = {**(agent.auth or {}), "scheme": {"http": "bearer", "oauth2": "bearer", "openIdConnect": "bearer"}
                      .get(scheme.get("type"), scheme.get("type", "none")),
                      **({"header": scheme.get("name", "X-API-Key")} if scheme.get("type") == "apiKey" else {})}
    item = db.get(CatalogItem, agent.catalog_item_id) if agent.catalog_item_id else None
    if item is not None:
        item.item_metadata, item.version, item.description = card, str(card.get("version")), card.get("description")
    return changed


def ensure_exchange_policy(db: Session, agent_id: str) -> None:
    aud = f"a2a:{agent_id}"
    if not db.scalars(select(TokenExchangePolicy).where(TokenExchangePolicy.audience == aud)).first():
        db.add(TokenExchangePolicy(id=new_id("txp"), audience=aud, audience_type="a2a_agent",
                                   allowed_scopes=["a2a:invoke"], default_scopes=["a2a:invoke"], ttl_seconds=600))
```
**분석 / Analysis (Level 2)**
- L106-113: 해시 비교로 변경 감지(이전 해시가 있을 때만), 스킬 요약·조회 시각 저장. `card_changed` 는 운영자가 확인할 때까지 유지(OR).
- L114-118: 인증 스킴이 비어 있으면 카드의 첫 보안 스킴으로 자동 설정.
- L119-121: 연결된 카탈로그 항목 메타데이터 동기화.
- L125-129: `a2a:{id}` 교환 정책 기본값(`a2a:invoke`, TTL 600초) — `identity.exchange` 는 정책이 없으면 거부한다(`app/services/identity.py:189-190`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L114-118 원격 카드(신뢰하지 않는 입력)가 게이트웨이 인증 방식·헤더 이름을 정한다(1차 지적, 코드 동일). 최초 설정 시에만 적용.
- [위험:낮음] L119-121 카드 변경이 카탈로그 메타데이터·인터페이스 URL·바인딩을 **즉시** 바꾼다 — "재검토 필요" 알림(L211-215)보다 적용이 먼저다(1차 지적, 코드 동일). 예: 원격이 카드의 JSON-RPC https 인터페이스를 빼고 `grpc://` 만 남기면 다음 호출부터 교환 토큰이 평문 gRPC 로 간다(L80-82 참고).
- L114 `agent.auth.get` 은 `agent.auth` 가 None 이면 `AttributeError`(L116 은 `or {}` 로 방어) — 모델 기본값이 dict 라 실해는 낮다.

```python path=app/services/a2a_gateway.py lines=132-151
def _card_changes(old: dict, new: dict) -> list[str]:
    so = {s.get("id") for s in (old or {}).get("skills") or []}
    sn = {s.get("id") for s in (new or {}).get("skills") or []}
    out = [f"+ skill {x}" for x in sorted(sn - so)] + [f"- skill {x}" for x in sorted(so - sn)]
    if (old or {}).get("version") != new.get("version") and old:
        out.append(f"~ version {old.get('version')} → {new.get('version')}")
    if set((old or {}).get("securitySchemes") or {}) != set(new.get("securitySchemes") or {}) and old:
        out.append("~ securitySchemes")
    return out


def _record_check(agent_id: str, status: str, http_status: int | None = None, etag: str | None = None,
                  changes: list | None = None, error: str | None = None) -> None:
    from app.models import A2ACardCheck
    with SessionLocal() as db:
        db.add(A2ACardCheck(id=new_id("acc"), agent_id=agent_id, status=status, http_status=http_status, etag=etag,
                            changes=changes or [], error=error))
        db.commit()
```
**분석 / Analysis (Level 2)**
- L132-140 `_card_changes`: 사람이 읽는 diff — 스킬 추가·삭제, 버전, 보안 스킴 **이름 집합** 변화. 인터페이스 URL·바인딩 변화는 diff 에 나오지 않는다(위 L119-121 의 즉시 적용과 결합하면 운영자가 전송 경로 변경을 놓칠 수 있다 — [위험:낮음], `supportedInterfaces` 비교 추가 권고).
- L143-149 `_record_check`: 재검증 이력 행(SCR-G03 ⑦).

```python path=app/services/a2a_gateway.py lines=152-220
async def refresh_card(agent_id: str, actor: str = "system:gateway") -> dict:
    """카드 재검증 (SCR-G03 ⑥⑦) — ETag(If-None-Match)로 변경 없으면 304, 실패해도 이전 캐시를 유지하고 이력에 남긴다."""
    def _load():
        with SessionLocal() as db:
            a = db.get(A2AAgent, agent_id)
            if a is None:
                raise A2AError(-32001, f"원격 에이전트 없음: {agent_id}", http_status=404)
            return a.card_url, (a.auth or {}).get("tls"), a.etag, dict(a.card or {})
    card_url, tls_cfg, etag, old = await run_in_threadpool(_load)
    tls_kw = await tls_kwargs(tls_cfg, "a2a:card", card_url)
    try:
        async with async_client(timeout=10, **tls_kw) as http:
            res = await http.get(card_url, headers={"If-None-Match": etag} if etag and old else {})
    except httpx.HTTPError as exc:
        await run_in_threadpool(_record_check, agent_id, "error", None, etag, None, f"{type(exc).__name__}")
        raise A2AError(-32603, f"Agent Card 조회 실패: {type(exc).__name__} — 이전 캐시 유지", http_status=502) from exc
    max_age = None
    import re as _re
    if m := _re.search(r"max-age=(\d+)", res.headers.get("cache-control", "")):
        max_age = int(m.group(1))
    # … (L172-182 생략 — 304: _touch() 가 `a = db.get(A2AAgent, agent_id)` 뒤 None 확인 없이 last_fetched_at·cache_ttl_s 갱신, 이력 not_modified, 이전 카드 반환)
    try:
        if res.status_code != 200:
            raise A2AError(-32603, f"Agent Card 조회 실패 ({res.status_code}) — 이전 캐시 유지", http_status=502)
        card = res.json()
        errors = ap.validate_card(card)
        if errors:
            raise A2AError(-32602, "Agent Card 검증 실패 — 이전 캐시 유지", {"errors": errors}, http_status=422)
    except (A2AError, ValueError) as exc:
        msg = exc.message if isinstance(exc, A2AError) else "JSON 아님"
        await run_in_threadpool(_record_check, agent_id, "error", res.status_code, etag, None, msg)
        if isinstance(exc, A2AError):
            raise
        raise A2AError(-32603, "Agent Card 가 JSON 이 아닙니다 — 이전 캐시 유지", http_status=502) from exc
    new_etag = res.headers.get("etag")
    # … (L197-218 생략 — _save(): apply_card·ETag·cache_ttl_s·교환 정책 저장 후 커밋(L200-201 도 None 확인 없음) → 이력 changed/ok → 변경이면 보안 담당 알림(dedupe = 카드 해시 앞 12자)·감사 → {changed, card, info} 반환)
```
**분석 / Analysis (Level 2)**
- L154-160: 카드 URL·TLS·ETag·이전 카드를 스레드풀에서 읽는다(없으면 404 -32001).
- L162-167: `If-None-Match` 조건부 GET. 네트워크 오류면 이력 기록 후 502 — **이전 캐시를 유지**해 카드 서버 장애가 호출 중단으로 번지지 않게 한다.
- L168-171: `Cache-Control: max-age` → 카드 캐시 TTL.
- L172-182: 304 면 조회 시각만 갱신.
- L183-195: 200·JSON·스키마 검증 실패는 이력 후 오류(이전 캐시 유지). `ValueError` 를 따로 잡아 JSON 아님을 502 로 바꾼다.
- L196-218: 적용·ETag·TTL·교환 정책 저장 → 이력 → 변경이면 보안 담당 알림 + 감사.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L169 함수 안 `import re as _re`(1차 지적, 코드 동일) — 모듈 상단으로.
- [위험:낮음] L175-176·L200-201 `db.get(...)` 결과를 None 확인 없이 쓴다 — 조회와 저장 사이 에이전트가 삭제되면 `AttributeError`(스레드풀 예외 → 500). 1차 지적, 코드 동일.
- 동시 `refresh_card` 두 번은 둘 다 적용되고 마지막 쓰기가 이긴다(같은 카드라면 무해).

```python path=app/services/a2a_gateway.py lines=221-232
def gateway_card(agent: AgentRef, base_url: str | None = None) -> dict:
    """게이트웨이 경유용 카드 — 인터페이스 URL 을 (요청이 들어온) 게이트웨이로, 보안 스킴을 AgentNexus OIDC 로 치환."""
    url = f"{(base_url or settings.public_base_url).rstrip('/')}/a2a/{agent.id}"
    info = agent.info or ap.card_info(agent.card)
    out = ap.build_card(name=agent.card.get("name", agent.name), description=agent.card.get("description", ""),
                        url=url, version=str(agent.card.get("version", "1")), skills=agent.card.get("skills", []),
                        streaming=info.get("streaming", False), push=True, rest_url=f"{url}/rest",
                        grpc_url=settings.a2a_grpc_public_url or None, grpc_tenant=agent.id)
    out["provider"] = {"organization": "AgentNexus Gateway (proxy)", "url": settings.public_base_url}
    return out
```
**분석 / Analysis (Level 2)**
- L221-230: 클라이언트가 원격 에이전트에 직접 붙지 않게 하는 핵심 — 인터페이스 URL 을 게이트웨이로, 보안 스킴을 AgentNexus OIDC(bearer, `build_card` 기본값 `app/services/a2a_proto.py:328-329`)로 치환, gRPC 공개 주소·tenant(=에이전트 id) 광고.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L223 `base_url` 은 `str(request.base_url)`(Host 헤더 기반, `app/api/a2a.py:318`)이다. `TrustedHostMiddleware`·proxy headers 설정은 `app/`·compose·Helm 템플릿 어디에도 없다(grep 0건) — Host 헤더 조작으로 카드 URL 을 바꿀 수 있다(중간 캐시가 있을 때만 영향). 1차 지적, 미해결.

```python path=app/services/a2a_gateway.py lines=233-244
# ───────────────────────── 업스트림 호출 ─────────────────────────
_TOKENS: dict[tuple, tuple[float, str]] = {}


def _cache_token(key: tuple, expires: float, token: str) -> None:
    if len(_TOKENS) >= 1024:  # 만료분 정리 — 한 번 왔다 다시 오지 않는 주체·대상 조합이 쌓이지 않게
        now = time.time()
        for k in [k for k, (exp, _) in _TOKENS.items() if exp <= now]:
            _TOKENS.pop(k, None)
    _TOKENS[key] = (expires, token)
```
**분석 / Analysis (Level 2)**
- L234 `_TOKENS`: `(주체 sub, 에이전트)` 또는 `("cc", 에이전트)` → `(만료 epoch, 토큰)`. 워커(프로세스) 로컬.
- L237-242: 1024 개 이상이면 만료분만 정리 후 저장(C39-10, `test/test_review_fixes.py:225`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] 만료 전 항목만 많으면 상한이 없고, `mcp_gateway` 와 같은 함수가 복붙되어 있으며(1차 INDEX §4.4), `patch_agent` 가 이 캐시를 비우지 않는다 — `_TOKENS` 참조는 이 파일 L234-277 뿐(grep). single-flight 가 없어 캐시가 빈 순간 같은 사용자의 동시 호출이 교환을 여러 번 한다(TokenExchange 행·감사 중복).

```python path=app/services/a2a_gateway.py lines=245-274
async def upstream_headers(agent: AgentRef, p: Principal) -> dict:
    scheme = agent.auth.get("scheme", "none")
    if scheme in ("none", None, ""):
        return {}
    if scheme == "apiKey":
        value = await run_in_threadpool(_secret, agent.auth.get("secret_ref"))
        if not value:
            raise A2AError(-32603, f"{agent.id}: API 키 미설정")
        return {agent.auth.get("header", "X-API-Key"): value}
    if scheme == "oauth2":  # OAuth2 client_credentials (SCR-G04 ②) — 게이트웨이가 발급·캐시(만료 30초 전 갱신)
        return {"Authorization": f"Bearer {await _client_credentials(agent)}"}
    if agent.auth.get("secret_ref") and not agent.auth.get("token_exchange", True):
        value = await run_in_threadpool(_secret, agent.auth["secret_ref"])
        return {"Authorization": f"Bearer {value}"}
    key = (p.sub, agent.id)
    hit = _TOKENS.get(key)
    if hit and hit[0] - 30 > time.time():
        return {"Authorization": f"Bearer {hit[1]}"}

    def _exchange():
        with SessionLocal() as db:
            return identity.exchange(db, p, f"a2a:{agent.id}", None, actor="gateway")
    try:
        tok = await run_in_threadpool(_exchange)
    except Exception as exc:  # noqa: BLE001 — token_exchange_failed
        raise A2AError(-32003, getattr(exc, "detail", str(exc)), {"code": "token_exchange_failed"}) from exc
    _cache_token(key, time.time() + tok["expires_in"], tok["access_token"])
    return {"Authorization": f"Bearer {tok['access_token']}"}
```
**분석 / Analysis (Level 2)**
- L246-248: 스킴 없음 → 헤더 없음.
- L249-253 apiKey: 시크릿 저장소 값 → 설정된 헤더 이름. 이 헤더는 gRPC 바인딩에서 **전달되지 않는다**(`app/services/a2a_grpc.py:88-90` `_FORWARD` 화이트리스트 — a2a_grpc 보고서 참고).
- L254-255 oauth2: 게이트웨이가 client_credentials 로 발급·캐시.
- L256-258: 시크릿이 있고 교환을 끈 경우 고정 Bearer.
- L259-272 기본: 사용자 → `a2a:{id}` audience 교환 토큰(사용자 신원 전파, 만료 30초 전 갱신).
**짚어볼 점 / Watch-outs**
- [위험:낮음 · 1차 핫스팟 #1 잔여] L249-258·L283-289 **싱크는 1차와 동일**하게 `agent.auth` 의 `secret_ref`·`token_url` 을 그대로 믿는다. 1차 [높음] 시나리오(developer 가 남의 `secret_ref`+외부 `token_url` 로 바꿔 시크릿 외부 전송)는 이제 API 계층에서 막힌다: `secrets.check_refs`(자기 네임스페이스 `a2a/{id}/` 밖 참조는 `gateway:write` 필요, `app/services/secrets.py:17-28`)와 `check_destination`(저장 시크릿이 있는 대상의 `token_url` 변경은 새 시크릿 동반 또는 `gateway:write`, `:31-38`)을 `app/api/a2a.py:457`(등록)·`:535-537`(수정)에서 호출(C39-12, `test/test_review_fixes.py:247`). 남은 것은 **심층 방어 부재** — `A2AAgent.auth` 를 쓰는 다른 경로(카탈로그 핸들러 `app/api/a2a.py:713-727`, 시드, 향후 코드)가 API 검사를 거치지 않으면 다시 열린다. 최소 수정: `_secret` 호출 전에 `ref.startswith(f"a2a/{agent.id}/")` 를 확인(운영자 예외 플래그만 허용).
- [위험:낮음] L268-270 `except Exception` — DB 오류 등 예상 밖 예외도 `token_exchange_failed`(-32003, HTTP 200)로 바뀌고 `str(exc)` 원문이 클라이언트 메시지로 나간다(내부 정보 노출 소지).
- L259 캐시 키에 역할·팀이 없다 — 교환 토큰의 `roles` 클레임(`app/services/identity.py:199-202`)이 역할 변경 뒤에도 최대 TTL(600초)까지 남는다.

```python path=app/services/a2a_gateway.py lines=275-303
async def _client_credentials(agent: AgentRef) -> str:
    key = ("cc", agent.id)
    hit = _TOKENS.get(key)
    if hit and hit[0] - 30 > time.time():
        return hit[1]
    url, cid = agent.auth.get("token_url"), agent.auth.get("client_id")
    if not url or not cid:
        raise A2AError(-32603, f"{agent.id}: OAuth2 token_url·client_id 미설정", {"code": "auth_config"})
    form = {"grant_type": "client_credentials", "client_id": cid,
            "client_secret": await run_in_threadpool(_secret, agent.auth.get("secret_ref")) or ""}
    if agent.auth.get("scopes"):
        form["scope"] = " ".join(agent.auth["scopes"])
    try:
        async with async_client(timeout=10, **await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url)) as http:
            res = await http.post(url, data=form)
        tok = res.json() if res.status_code < 400 else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise A2AError(-32003, f"OAuth2 토큰 발급 실패: {type(exc).__name__}", {"code": "token_exchange_failed"}) from exc
    if not tok.get("access_token"):
        raise A2AError(-32003, f"OAuth2 토큰 발급 실패: HTTP {res.status_code}", {"code": "token_exchange_failed"})
    _cache_token(key, time.time() + int(tok.get("expires_in") or 300), tok["access_token"])
    return tok["access_token"]


def _secret(ref: str | None) -> str | None:
    with SessionLocal() as db:
        return secrets.get_plain(db, ref)
```
**분석 / Analysis (Level 2)**
- L276-279: `("cc", 에이전트)` 캐시 적중이면 재사용.
- L280-286: `token_url`·`client_id` 필수, 시크릿 저장소의 client_secret, 선택 scope.
- L287-292: mTLS 적용 POST. `res.json()` 이 try 안에 있어 비 JSON 도 A2AError 로 감싼다(올바름).
- L299-301 `_secret`: 스레드풀용 시크릿 조회(복호화 — `app/services/secrets.py:58-64`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L276-279 에이전트 `auth` 를 바꿔도 `("cc", agent.id)` 캐시가 남는다(1차 지적, 미해결) — 설정 변경이 토큰 만료까지(기본 300초 또는 IdP 값) 반영되지 않는다. 워커마다 캐시가 따로라 워커별로 다른 토큰을 쓰는 시간도 생긴다.
- `token_url` 은 `check_destination` 이 지키는 유일한 전송처 필드다(`app/services/secrets.py:36`) — 같은 사람이 `tls` 를 바꾸는 것은 막지 않지만 시크릿 유출 경로는 아니다.

```python path=app/services/a2a_gateway.py lines=304-319
def _upstream_version(agent: AgentRef, client_version: str) -> tuple[str, str, str]:
    """(업스트림 버전, URL, 바인딩) — JSONRPC 를 우선, 카드에 HTTP+JSON(·GRPC)만 있으면 그 바인딩으로 (D16)."""
    info = agent.info
    if client_version == ap.V10 and info.get("url_v10"):
        return ap.V10, info["url_v10"], "JSONRPC"
    if client_version == ap.V03 and info.get("url_v03"):
        return ap.V03, info["url_v03"], "JSONRPC"
    if info.get("url_v10"):
        return ap.V10, info["url_v10"], "JSONRPC"
    if not info.get("url_v03") and info.get("url_rest"):
        return ap.V10, info["url_rest"], "HTTP+JSON"
    if not info.get("url_v03") and info.get("url_grpc"):
        return ap.V10, info["url_grpc"], "GRPC"
    return ap.V03, info.get("url_v03") or agent.url, "JSONRPC"
```
**분석 / Analysis (Level 2)**
- L304-317: 같은 버전 JSON-RPC 우선 → 1.0 JSON-RPC → (0.3 이 없으면) HTTP+JSON → gRPC → 0.3/기본 URL. gRPC URL 은 `url#tenant` 형태로 합쳐 온다(`app/services/a2a_proto.py:296`). 호출 지점: L561·L871.

```python path=app/services/a2a_gateway.py lines=320-341
async def _rest_rpc(url: str, op: str, params: dict, headers: dict, tls_kw: dict | None = None) -> dict:
    """HTTP+JSON 전용 원격 에이전트 호출 — 정준 작업 → REST 경로, google.rpc.Status 오류 → A2A 코드."""
    method, path, query, body = a2a_rest.rest_request(op, params)
    hdr = {"Content-Type": a2a_rest.MEDIA, "Accept": f"{a2a_rest.MEDIA}, application/json", "A2A-Version": ap.V10, **headers}
    try:
        async with async_client(timeout=settings.upstream_timeout, **(tls_kw or {})) as http:
            res = await http.request(method, f"{url.rstrip('/')}/{path}", params=query, headers=hdr,
                                     **({"json": body} if body is not None else {}))
    except httpx.HTTPError as exc:
        raise A2AError(-32603, f"원격 에이전트 연결 실패: {type(exc).__name__}", http_status=502) from exc
    if res.status_code in (401, 403):
        raise A2AError(-32003, f"원격 에이전트 인증 실패 ({res.status_code})", http_status=502)
    if res.status_code >= 400:
        try:
            err = res.json()
        except ValueError:
            err = None
        code, msg = a2a_rest.code_from_status(res.status_code, err)
        raise A2AError(code, msg, http_status=200 if code in a2a_rest.A2A_ERRORS else 502)
    return res.json() if res.content else {}
```
**분석 / Analysis (Level 2)**
- L322-327: 정준 작업 → REST 메서드·경로·쿼리·본문(`a2a_rest.rest_request`), ProtoJSON 미디어 타입.
- L330-338: 401/403 은 인증 실패(502 — 게이트웨이 입장에서는 업스트림 문제), 그 밖 4xx/5xx 는 google.rpc.Status → A2A 코드.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L339 200 인데 비 JSON 이면 `ValueError` 가 A2AError 로 감싸지지 않고 `proxy` 를 지나 500(비 JSON-RPC 본문)이 된다(1차 지적, 미해결). 응답 크기 상한도 없다.

```python path=app/services/a2a_gateway.py lines=342-366
async def _rpc(url: str, method: str, params: dict, headers: dict, version: str, tls_kw: dict | None = None,
               binding: str = "JSONRPC", op: str | None = None) -> dict:
    if binding == "HTTP+JSON":
        return await _rest_rpc(url, op or ap.op_of(method), params, headers, tls_kw)
    if binding == "GRPC":
        from app.services import a2a_grpc
        return await a2a_grpc.call(url, op or ap.op_of(method), params, headers, (tls_kw or {}).get("grpc_credentials"))
    hdr = {"Content-Type": "application/json", "A2A-Version": version, **headers}
    try:
        async with async_client(timeout=settings.upstream_timeout, **(tls_kw or {})) as http:
            res = await http.post(url, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method,
                                             "params": params}, headers=hdr)
    except httpx.HTTPError as exc:
        raise A2AError(-32603, f"원격 에이전트 연결 실패: {type(exc).__name__}", http_status=502) from exc
    if res.status_code in (401, 403):
        raise A2AError(-32003, f"원격 에이전트 인증 실패 ({res.status_code})", http_status=502)
    if res.status_code >= 400:
        raise A2AError(-32603, f"원격 에이전트 오류 ({res.status_code})", http_status=502)
    body = res.json()
    if "error" in body:
        e = body["error"]
        raise A2AError(e.get("code", -32603), e.get("message", "remote error"), e.get("data"))
    return body.get("result") or {}
```
**분석 / Analysis (Level 2)**
- L344-345: HTTP+JSON 바인딩 위임.
- L346-348 (2차 변경): gRPC 바인딩 — `tls_kwargs` 가 만든 `grpc_credentials` 를 꺼내 넘긴다(`app/services/a2a_grpc.py:93-110`). gRPC 가 아닌 대상의 `tls_kw` 에는 이 키가 없어 `.get` 이 None — 안전.
- L349-364: JSON-RPC — 무작위 id 로 POST, 오류 객체는 원격 code·message·data 를 그대로 A2AError 로.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L360 `res.json()` 비 JSON → `ValueError` 미처리(1차 지적, 미해결). `body` 가 dict 가 아니면(`[]`·문자열) L361 `"error" in body` 는 동작하지만 L364 `body.get` 에서 `AttributeError`.
- [위험:낮음] L363 원격 `e.get("message")` 를 그대로 클라이언트에 전달 — 원격 내부 정보가 게이트웨이 응답에 실린다(원격 책임이지만 길이 제한 정도는 둘 만하다).
- L348 `a2a_grpc.call` 은 `json_format.ParseError`(ValueError 아님)를 그대로 올릴 수 있다 — a2a_grpc 보고서 참고.

```python path=app/services/a2a_gateway.py lines=367-382
# ───────────────────────── Task 영속 ─────────────────────────
@dataclass
class CallContext:
    principal: Principal
    agent: str | None = None  # 호출 에이전트 프로파일
    data_class: str = "internal"
    session_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    binding: str = "JSONRPC"  # 하류 바인딩 — JSONRPC | HTTP+JSON | GRPC (D16)


def _tid(agent_id: str, remote_id: str) -> str:
    return f"{agent_id}:{remote_id}"
```
**분석 / Analysis (Level 2)**
- L369-376 `CallContext`: 주체·호출 에이전트·데이터 등급·세션/실행/단계·하류 바인딩. 2차에서 생성부가 `policy.caller_context` 로 바뀌었다(`app/api/a2a.py:47-48` → `app/services/policy.py:56-73`): `agent` 는 기계·위임 주체만, `run_id` 는 그 주체의 실행만, 데이터 등급은 서버 판정. `session_id` 는 여전히 헤더 값 그대로(`policy.py:72`).
- L379-380 `_tid`: 게이트웨이 Task id = `{에이전트}:{원격 Task id}` — 소유권 판정·푸시·폴링의 공통 키.

```python path=app/services/a2a_gateway.py lines=383-430
def record(agent_id: str, obj: dict, ctx: CallContext | None = None, push_token: str | None = None,
           source: str = "send", protocol: tuple | None = None) -> None:
    """정준형 Task 또는 이벤트를 A2ATask 로 반영 — 수신 경로(send·sse·push·poll)와 상태 전이를 이벤트로 남긴다."""
    kind = obj.get("kind")
    remote = obj.get("id") if kind == "task" else obj.get("taskId")
    if not remote:
        return
    with SessionLocal() as db:
        row = db.get(A2ATask, _tid(agent_id, remote))
        if row is None:
            row = A2ATask(id=_tid(agent_id, remote), remote_task_id=remote, agent_id=agent_id, direction="outbound",
                          context_id=obj.get("contextId"), principal_sub=ctx.principal.sub if ctx else None,
                          session_id=ctx.session_id if ctx else None, run_id=ctx.run_id if ctx else None,
                          trace_id=current_trace_id(), history=[], artifacts=[])
            db.add(row)
        before = row.state  # 새 행은 None — 첫 상태도 전이로 기록
        if kind == "task":
            row.state = obj["status"]["state"]
            row.history = obj.get("history") or row.history
            row.artifacts = obj.get("artifacts") or row.artifacts
            row.status_message = ap.text_of({"kind": "task", "status": obj["status"]})[:2000] or row.status_message
        elif kind == "status-update":
            row.state = obj["status"]["state"]
            text = ap.text_of({"kind": "task", "status": obj["status"]})
            if text:
                row.status_message = text[:2000]
                row.history = [*(row.history or []), obj["status"]["message"]]
        elif kind == "artifact-update":
            arts = [a for a in (row.artifacts or []) if a.get("artifactId") != obj["artifact"].get("artifactId")]
            row.artifacts = [*arts, obj["artifact"]]
    # … (L413-428 생략 — push_token 갱신 · 이벤트{at·source·kind·protocol(client/upstream 버전·바인딩)·state·transition·artifact} 추가 후 최근 100개로 재할당 · updated_at · commit)
```
**분석 / Analysis (Level 2)**
- L386-389: Task 면 `id`, 이벤트면 `taskId` — 없으면 기록하지 않는다(Message 응답 등).
- L390-397: 행이 없으면 생성 — 주체(`ctx` 가 있을 때만)·세션·실행·trace. **이 생성 시점의 `principal_sub` 가 Epic 44 소유권 판정의 근거**(L632-636, L613-617).
- L398-412: task 면 상태·이력·아티팩트·메시지 교체, status-update 면 상태 + 이력 추가, artifact-update 면 같은 id 교체. JSON 컬럼을 **재할당**하므로 SQLAlchemy 변경 추적 문제(제자리 변경 미감지)가 없다.
- L413-428: 이벤트 로그(최근 100개 상한).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L400·L405 `obj["status"]["state"]` — 정준화(`task_from`·`event_from` → `status_from`, `app/services/a2a_proto.py:137-144,156-162,195-212`)가 status 를 항상 채우므로 1차가 걱정한 `KeyError` 는 실제로는 나기 어렵다. 다만 원격이 `status` 를 dict 가 아닌 값으로 보내면 `status_from` 의 `st.get` 에서 `AttributeError`, 상태 문자열이 20자를 넘으면 PostgreSQL 에서 `String(20)`(`app/models.py:389`) 길이 오류로 커밋 실패한다.
- [위험:낮음] L401·L409 `history` 상한 없음(1차 지적, 미해결) — `events` 만 100개 상한.
- [위험:낮음] L387-391 행 키가 **응답 본문의 id** 다 — 호출자가 요청한 id 와 일치하는지 확인하지 않는다. 푸시는 2차에서 묶음 검사가 생겼지만(L818-822) `get`·`cancel`·`send`(L591-594)와 감시기(L874)는 원격이 다른 Task id 를 돌려주면 같은 에이전트의 **다른 사용자 행**을 갱신하고, 없던 id 면 `principal_sub=None`(감시기 경로) 행을 만든다. 원격 에이전트 자신의 데이터이긴 하지만 푸시와 같은 규칙을 적용하는 편이 일관적이다(L580-612 블록 참고).

```python path=app/services/a2a_gateway.py lines=431-465
def _usage(ctx: CallContext, agent_id: str, ms: int, status: str) -> None:
    with SessionLocal() as db:
        db.add(UsageRecord(id=new_id("use"), trace_id=current_trace_id(), session_id=ctx.session_id, run_id=ctx.run_id,
                           step_id=ctx.step_id, user_sub=ctx.principal.sub, team=ctx.principal.team, agent=ctx.agent,
                           kind="remote_agent",
                           target=agent_id, provider=agent_id, latency_ms=ms, status=status))
        db.commit()


# ───────────────────────── 프록시 ─────────────────────────
def load_agent(agent_id: str) -> AgentRef:
    with SessionLocal() as db:
        a = db.get(A2AAgent, agent_id)
        if a is None:
            raise A2AError(-32001, f"원격 에이전트를 찾을 수 없습니다: {agent_id}", http_status=404)
        return AgentRef.of(a)


async def _check(agent: AgentRef, ctx: CallContext) -> None:
    def _do():
        with SessionLocal() as db:
            a = db.get(A2AAgent, agent.id)
            dec = governance.check_agent(db, ctx.principal, a, caller_agent=ctx.agent, data_class=ctx.data_class)
            lim = ratelimit.check(db, ctx.principal, "agent", agent.id, ctx.agent) if dec.allow else None
            return dec, lim
    dec, lim = await run_in_threadpool(_do)
    if not dec.allow:
        await run_in_threadpool(lambda: audit.append("gateway.a2a.deny", actor=ctx.principal.sub, target=agent.id,
                                                     decision=dec.code, data={"reason": dec.reason}))
        raise A2AError(-32003, dec.reason, {"code": dec.code}, http_status=403)
    if lim is not None and not lim.allowed:
        raise A2AError(-32005, lim.reason or "rate limited", {"code": "rate_limited", "retry_after": lim.retry_after},
                       http_status=429)
```
**분석 / Analysis (Level 2)**
- L431-437 `_usage`: 원격 에이전트 호출 사용량 행(`session_id`·`agent` 는 서버 판정 뒤 값).
- L441-446 `load_agent`: 없으면 404(-32001). **상태(active/disabled)·신뢰 등급은 여기서 보지 않는다** — `_check` 가 담당(호출자가 `_check` 를 빠뜨리면 우회된다 → 감시기 L853-878 블록).
- L449-463 `_check`: `governance.check_agent`(비활성·차단·프로파일 허용 목록·정책·미신뢰 기본 거부 — `app/services/governance.py:314-333`) + 허용이면 에이전트 한도 차감. 거부는 감사 후 403, 한도 초과는 429.
**짚어볼 점 / Watch-outs**
- 1차 [L451 `caller_agent` 헤더 위조] → **해결**: `ctx.agent` 는 이제 기계·위임 주체가 보낸 경우에만 채워진다(`app/services/policy.py:70`, `test/test_caller_trust.py:38`).
- [위험:낮음] L452 `a` 가 None(로드와 판정 사이 삭제)이면 `check_agent` 에서 `AttributeError` → 500.

```python path=app/services/a2a_gateway.py lines=466-481
async def proxy(agent_id: str, ctx: CallContext, rid, method: str, params: dict):
    """클라이언트 JSON-RPC → 업스트림. 반환: JSONResponse | StreamingResponse."""
    with span(f"a2a {method} {agent_id}", SpanKind.CLIENT, **{"gen_ai.operation.name": "invoke_agent",
                                                              "gen_ai.agent.name": agent_id, "a2a.method": method,
                                                              "anx.agent": ctx.agent}):
        try:
            res = await _proxy(agent_id, ctx, rid, method, params)
            metrics.A2A_CALLS.labels(agent_id, ap.op_of(method) or "unknown", "ok").inc()
            return res
        except A2AError as exc:
            metrics.A2A_CALLS.labels(agent_id, ap.op_of(method) or "unknown", "error").inc()
            logs.event("warning", "gateway", f"A2A 호출 실패/거부: {agent_id} {method}", code=exc.code,
                       detail=exc.message)
            raise
```
**분석 / Analysis (Level 2)**
- L466-479: CLIENT 스팬(GenAI `invoke_agent`) 안에서 처리, 성공·실패 메트릭, 실패는 이벤트 로그 후 재발생. `logs.event` 는 큐에 넣기만 해(`app/core/logs.py:98-102`) 루프를 막지 않는다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L472-474 스트림은 `StreamingResponse` 반환 순간 "ok"(1차 지적, 미해결). A2AError 가 아닌 예외(L339·L360 `ValueError`, gRPC `ParseError`)는 메트릭·로그 어디에도 남지 않고 500 이 된다.

```python path=app/services/a2a_gateway.py lines=482-497
# ── 입출력 가드 (FR-G02·G07, NFR-SEC1·PRV1) ──
def _each_part(obj, fn) -> None:
    """정준형·업스트림형 어디서든 'parts' 목록의 각 파트에 fn 적용 (Task·Message·Artifact·이벤트 공통)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "parts" and isinstance(v, list):
                for i, part in enumerate(v):
                    if isinstance(part, dict):
                        v[i] = fn(part)
            else:
                _each_part(v, fn)
    elif isinstance(obj, list):
        for v in obj:
            _each_part(v, fn)
```
**분석 / Analysis (Level 2)**
- L483-495: 어떤 중첩 구조든 `parts` 목록 원소를 제자리 교체. 재귀 깊이 상한이 없다 — 아주 깊은 JSON 은 `RecursionError`(원격·클라이언트 입력, JSON 파서가 먼저 막는 경우가 많아 낮음).

```python path=app/services/a2a_gateway.py lines=498-524
def _guard_out(agent_id: str, ctx: CallContext, msg: dict) -> None:
    """원격(외부) 에이전트로 나가는 메시지 — 인젝션 차단·PII/시크릿 마스킹(pii_input 설정)."""
    findings: list = []

    def fix(part: dict) -> dict:
        if isinstance(part.get("text"), str):
            r = guards.scan_input(db, part["text"])
            findings.extend(r.findings)
            if r.blocked:
                raise A2AError(-32003, r.reason or "입력 가드 차단", {"code": "input_blocked", "findings": r.types},
                               http_status=403)
            return {**part, "text": r.text}
        if part.get("data") is not None and guards.security_settings(db)["pii_input"] != "allow":
            masked, f = guards.mask_pii(json.dumps(part["data"], ensure_ascii=False))
            if f:
                findings.extend(f)
                return {**part, "data": json.loads(masked)}
        return part
    with SessionLocal() as db:
        try:
            _each_part(msg, fix)
        finally:
            if findings:
                audit.append("gateway.a2a.guard", actor=ctx.principal.sub, target=agent_id, data={
                    "direction": "outbound", "findings": sorted({f"{f.kind}:{f.type}" for f in findings})})
```
**분석 / Analysis (Level 2)**
- L498-522: 나가는 메시지 — 텍스트는 입력 가드(인젝션 차단·PII 마스킹), data 파트는 설정에 따라 PII 마스킹. 차단이어도 `finally` 에서 발견 내역을 감사한다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L502-516 `fix` 가 아래 `with` 블록의 `db` 를 클로저로 늦게 참조(1차 지적, 스타일). 파일 파트(`kind: file`)는 검사 대상이 아니다(1차 지적, 미해결).

```python path=app/services/a2a_gateway.py lines=525-548
def _sanitize_in(agent_id: str, ctx: CallContext, obj: dict) -> dict:
    """원격 에이전트 응답(간접 인젝션 경로) — 지시문 제거·시크릿·PII 마스킹·소독 규칙. 도구 응답과 같은 규칙."""
    findings: list = []

    def fix(part: dict) -> dict:
        if isinstance(part.get("text"), str):
            r = guards.sanitize_tool_text(db, part["text"])
            findings.extend(r.findings)
            return {**part, "text": r.text}
        if part.get("data") is not None:
            clean, f, blocked = guards.sanitize_structured(db, part["data"])
            findings.extend(f)
            return {"kind": "text", "text": guards._BLOCKED} if blocked else {**part, "data": clean}
        return part
    with SessionLocal() as db:
        if not guards.security_settings(db).get("tool_response_sanitize", True):
            return obj
        _each_part(obj, fix)
        if findings:
            audit.append("gateway.a2a.guard", actor=ctx.principal.sub, target=agent_id, data={
                "direction": "inbound", "findings": sorted({f"{f.kind}:{f.type}" for f in findings})})
    return obj
```
**분석 / Analysis (Level 2)**
- L525-546: 원격 응답(간접 프롬프트 인젝션 경로) 소독 — 텍스트 지시문 제거·마스킹, 구조화 데이터 소독(차단이면 텍스트로 대체). 호출 지점은 **L592(단건)·L706(스트림) 두 곳뿐**이다(grep `_sanitize_in`).
**짚어볼 점 / Watch-outs**
- 푸시 수신(L817-827)과 감시기 폴링(L874)은 이 함수를 거치지 않는다 — 상세는 L795-829 블록의 [위험:중간] 가드 우회 항목. `ctx.principal.sub`(L544)를 요구하는 시그니처가 `ctx` 없는 두 경로에서 호출을 어렵게 만든 것으로 보인다(가능성) — `actor` 만 받도록 바꾸면 재사용할 수 있다.

```python path=app/services/a2a_gateway.py lines=549-579
async def _proxy(agent_id: str, ctx: CallContext, rid, method: str, params: dict):
    op = ap.op_of(method)
    if op is None:
        raise A2AError(-32601, f"Method not found: {method}")
    client_v = ap.version_of(method)
    agent = await run_in_threadpool(load_agent, agent_id)
    if not agent.card:
        await refresh_card(agent_id)
        agent = await run_in_threadpool(load_agent, agent_id)
    await _check(agent, ctx)
    if tid := _task_id_of(op, params):  # 게이트웨이를 거친 Task 는 만든 주체만 (D18-①) — 남의 Task 는 없는 것처럼
        await run_in_threadpool(_check_task_owner, agent_id, tid, ctx.principal.sub)
    up_v, url, binding = _upstream_version(agent, client_v)
    up_method = ap.method_for(op, up_v)
    up_params = ap.params_to(op, params, up_v)
    headers = await upstream_headers(agent, ctx.principal)
    push_token = None
    if op == "push_set":
        tid, client_cfg = ap.push_config_of(params)
        if reason := push_url_problem(client_cfg.get("url")):
            raise A2AError(-32602, reason, {"code": "push_url_rejected"}, http_status=400)
        push_token = token_urlsafe(18)
        gw_cfg = {"url": f"{settings.gateway_url.rstrip('/')}/a2a/push/{push_token}", "token": push_token}
        up_params = ap.params_to("push_set", {"taskId": tid, "pushNotificationConfig": gw_cfg}, up_v)
        await run_in_threadpool(_save_push, agent_id, tid, push_token, client_cfg, ctx)
    if op in ("send", "stream") and (msg := up_params.get("message")) is not None:
        if not msg.get("messageId"):
            msg["messageId"] = uuid.uuid4().hex
        await run_in_threadpool(_guard_out, agent_id, ctx, msg)
    if op in ("stream", "resubscribe"):
        return _stream(agent, ctx, rid, url, up_method, up_params, headers, up_v, client_v, binding, op)
```
**분석 / Analysis (Level 2)**
- L550-558: 메서드 → 정준 작업(모르면 -32601), 에이전트 로드(카드 없으면 조회), 정책·한도 판정.
- L559-560 (2차 신규): 요청이 가리키는 Task id(`_task_id_of`, L620-629)가 있으면 **영속 행의 `principal_sub`** 로 소유자 판정 → 남의 것이면 -32001(TaskNotFound — 존재 자체를 숨김). DB 가 진실 원천이라 워커·바인딩(JSON-RPC·REST·gRPC → 모두 이 함수로 수렴, `app/api/a2a.py:304,335,349`)과 무관하게 같은 결과(`test/test_task_ownership.py:51`). 판정이 `_check`(한도 차감) 뒤라 남의 Task 조회도 한도를 소비한다(의도로 보임).
- L561-564: 업스트림 버전·URL·바인딩, 메서드·params 변환, 인증 헤더.
- L566-573 push_set: 클라이언트 URL 검사(L568, 2차) → 업스트림에는 **게이트웨이 웹훅**(`/a2a/push/{무작위 144비트 토큰}`) 등록, 클라이언트 웹훅은 Task 행에 저장.
- L574-577: 나가는 메시지에 messageId 보장 + 출력 가드.
- L578-579: 스트림 작업은 SSE 응답으로.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 가용성] L568 `push_url_problem` → `internal_url` → **동기 `socket.getaddrinfo`**(`app/core/http.py:118`)가 이벤트 루프 스레드에서 실행된다(`_proxy` 는 async, 스레드풀 위임 없음). 시나리오: `chat:use` 사용자가 자기 Task 에 `pushNotificationConfig.url=https://<공격자가 권한 DNS 를 쥔 이름>/h` 를 보내고 그 권한 서버가 응답을 끌면, 리졸버 타임아웃(glibc 기본 5초 × 재시도) 동안 **그 워커의 모든 요청·SSE 가 멈춘다**. 반복하면 지속 DoS. prod(`app_env` 가 local·test 아님, L644)에서만 발생. 최소 수정: `await run_in_threadpool(push_url_problem, url)` 또는 `_guard_async` 처럼 `loop.getaddrinfo` 사용(`app/core/http.py:128-137`). 같은 함수가 플랫폼 서버에서도 동기 호출된다(`app/services/a2a_server.py:283`).
- [위험:낮음] L566-573 `push_set` 에서 `tid` 가 None 이면 L559 소유권 검사를 건너뛰고 L573 이 `"{agent}:None"` 행을 만든다(여러 사용자가 같은 행을 덮어씀). 업스트림이 거절해도 `_save_push` 는 이미 커밋됐다(L573 이 L583 보다 먼저) — 기존 푸시 토큰이 덮여 옛 등록의 푸시는 404. 최소 수정: `tid` 필수 검증, 업스트림 성공 뒤 저장.
- [위험:중간 · 기능 결함·정보 노출 — 조정자 후보, 확인됨] L571 원격 에이전트에 등록하는 푸시 콜백의 기준 주소가 `settings.gateway_url` 이다. 이 설정은 "Host → Gateway 호출 대상(내부 주소)" 용도이고(`app/core/config.py:26-27` 주석), 배포에서 **내부 이름으로 고정**된다 — compose `GATEWAY_URL: http://api:8000`(`docker-compose.yml:17`, a2a-demo `:50`·a2a-grpc `:68` 도 같은 값), Helm `GATEWAY_URL: http://{{ $fn }}-api:{{ .Values.service.port }}`(`deploy/helm/agentnexus/templates/configmap.yaml:14`, 21:20 수정본 기준). 외부 공개 주소는 `public_base_url`(`app/core/config.py:24-25`, Helm `configmap.yaml:11` `.Values.publicBaseUrl`)인데 이 파일은 카드 URL(L223·L229)에만 쓴다. 시나리오: 클러스터 밖 파트너 에이전트(`https://agent.partner.example`)를 등록한 사용자가 push_set → 게이트웨이가 `{"url": "http://api:8000/a2a/push/<token>", "token": "<token>"}` 를 등록(L571-572) → 원격은 이 이름을 풀지 못해 푸시가 오지 않고(클라이언트 웹훅 중계 L824-827 도 일어나지 않음 — 감시기 폴링 L853-876 만 남는다), 내부 서비스 이름·포트·평문 http 사용 사실과 푸시 토큰이 외부 조직에 전달된다. 시험은 `GATEWAY_URL=http://testserver`(`test/conftest.py:19`)·in-process 마운트·클러스터 안 데모 에이전트(`test/test_a2a.py:127-155`)라 드러나지 않았다. `.review/round2/VERIFY.md` 표 "4 푸시 콜백이 내부 주소" 도 같은 결론(C51-04 — 위 스냅샷 고지의 `push_callback_base`). 최소 수정: 콜백 기준을 `public_base_url` 로 하고 사설망 에이전트만 `gateway_url` 예외(또는 에이전트별 콜백 기준 설정).
- [위험:중간 · 가용성 — 위 [낮음] 항목의 확장] L573 `_save_push` 는 행이 없으면 **아무 taskId 로나** 새 A2ATask 를 만든다(L651-655). 새 행은 모델 기본값으로 `direction="outbound"`·`state="submitted"`(`app/models.py:384`, `:389`)라 곧바로 감시기 대상(L857-858)이 된다. 존재하지 않는 원격 Task 는 `tasks/get` 이 매번 실패(L869 에서 삼킴)해 터미널이 되지 않고, A2ATask 는 보존 정리 대상도 아니다(`app/services/retention.py:13`, `:33` — McpRelay·McpTask 등만). 시나리오: `chat:use` 사용자가 push_set 을 무작위 taskId 로 반복(L558 `_check` 의 주체·에이전트 한도만큼) → 쓰레기 행 N개 → 감시기는 주기(5초)마다 20개씩 순환(L859-862)하므로 실제 Task 의 폴링 간격이 약 N/20×5초로 늘고(1만 행이면 약 42분), 쓰레기 행마다 그 사용자 명의 교환·원격 `tasks/get` 이 영구히 반복된다. 최소 수정: 업스트림 push_set 성공 뒤 저장, 행이 없으면 만들지 말고 -32001, 감시기는 연속 실패 N회면 `failed` 로 전환, A2ATask 보존 정리 추가.

```python path=app/services/a2a_gateway.py lines=580-612
    t0 = time.perf_counter()
    status = "ok"
    try:
        result = await _rpc(url, up_method, up_params, headers, up_v,
                            await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url), binding, op)
    except A2AError:
        status = "error"
        raise
    finally:
        if op in ("send", "cancel"):
            await run_in_threadpool(_usage, ctx, agent_id, int((time.perf_counter() - t0) * 1000), status)
    if op in ("send", "get", "cancel"):
        canon = await run_in_threadpool(_sanitize_in, agent_id, ctx, ap.result_from(result))
        await run_in_threadpool(record, agent_id, canon, ctx, None, "poll" if op == "get" else op,
                                (client_v, up_v, ctx.binding, binding))
        if op in ("send", "cancel"):
            await run_in_threadpool(lambda: audit.append(f"a2a.{op}", actor=ctx.principal.sub, target=agent_id,
                                                         data={"task": canon.get("id"), "state": (canon.get("status")
                                                                                                  or {}).get("state"),
                                                               "agent": ctx.agent}))
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": ap.result_to(canon, client_v, op)})
    if op == "push_set":
        tid, client_cfg = ap.push_config_of(params)
        out = {"taskId": tid, "pushNotificationConfig": client_cfg} if client_v == ap.V03 else {"taskId": tid,
                                                                                                  **client_cfg}
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": out})
    if op == "list" and isinstance(result, dict) and isinstance(result.get("tasks"), list):  # 원격 목록 중 내 Task 만
        mine = await run_in_threadpool(_owned_ids, agent_id, [t.get("id") for t in result["tasks"] if isinstance(t, dict)],
                                       ctx.principal.sub)
        result = {**result, "tasks": [t for t in result["tasks"] if isinstance(t, dict) and t.get("id") in mine]}
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})
```
**분석 / Analysis (Level 2)**
- L580-590: 단건 호출. send·cancel 은 `finally` 에서 사용량 기록. 주의: `finally` 안의 `await` 는 클라이언트 끊김(취소) 시 다시 취소될 수 있다(45th 결과에서 같은 현상 기록 — `test/impl/45th/result.md` "회차 중 발견") → 사용량 누락 가능성(추가 확인 필요).
- L591-600: 결과 소독 → 영속(`record`) → send·cancel 감사 → 클라이언트 버전으로 변환.
- L601-605 push_set: 클라이언트가 준 설정을 그대로 돌려준다(게이트웨이 토큰 숨김).
- L606-609 (2차 신규) list: 원격 목록에서 **내 소유 행이 있는 Task 만** 남긴다(`_owned_ids`, `principal_sub == sub`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L585-587 `A2AError` 가 아닌 예외는 `status="ok"` 로 사용량 기록(1차 지적, 미해결).
- [위험:낮음 · fail-open] L606 결과가 `{"tasks": [...]}` 모양이 아니면(원격이 목록을 다른 키·리스트로 반환) **거르지 않고** L610 으로 원문을 돌려준다. `nextPageToken`·`totalSize` 같은 필드도 그대로라 남의 Task 수가 드러난다. 최소 수정: `op == "list"` 이면 거를 수 없는 결과는 `{"tasks": []}` 로.
- [위험:낮음 · 정보 노출] L610 `push_get` 은 가로채지 않아 업스트림이 저장한 **게이트웨이 웹훅 설정**(`{gateway_url}/a2a/push/{push_token}`, `token`, L571)이 그대로 클라이언트에 간다 — Task 주인이 게이트웨이 내부 주소와 푸시 토큰을 알게 되고, 에이전트에 `push_verify` 가 없으면(L809) 자기 Task 상태를 위조 푸시로 바꿀 수 있다. 최소 수정: push_set(L601-605)처럼 저장된 클라이언트 설정으로 응답.
- [위험:낮음] L591-594 응답 Task id 를 요청 id(`_task_id_of`)와 비교하지 않는다(L383-430 블록 참고).
- [위험:낮음 · 정보 노출] L610 `card`(확장 카드 — `GetExtendedAgentCard`·`agent/getAuthenticatedExtendedCard`, `app/services/a2a_proto.py:24`)도 가로채지 않아 원격의 확장 카드가 그대로 간다 — `gateway_card`(L221-230)가 숨긴 원격의 **직접 인터페이스 URL·보안 스킴**이 클라이언트에 드러나고 `_sanitize_in` 도 거치지 않는다. 최소 수정: 확장 카드도 `gateway_card` 와 같은 치환(인터페이스 URL·securitySchemes)을 거쳐 반환.

```python path=app/services/a2a_gateway.py lines=613-638
def _owned_ids(agent_id: str, ids: list, sub: str) -> set:
    with SessionLocal() as db:
        rows = db.scalars(select(A2ATask).where(A2ATask.id.in_([_tid(agent_id, i) for i in ids if i]),
                                                A2ATask.principal_sub == sub))
        return {r.remote_task_id for r in rows}


def _task_id_of(op: str, params: dict) -> str | None:
    if op in ("get", "cancel", "resubscribe"):
        return params.get("id")
    if op == "push_get":
        return params.get("taskId") or params.get("id")
    if op == "push_set":
        return ap.push_config_of(params)[0]
    if op in ("send", "stream"):
        return (params.get("message") or {}).get("taskId")
    return None


def _check_task_owner(agent_id: str, tid: str, sub: str) -> None:
    with SessionLocal() as db:
        row = db.get(A2ATask, _tid(agent_id, tid))
        if row is not None and row.principal_sub and row.principal_sub != sub:
            raise A2AError(ap.TASK_NOT_FOUND, f"Task not found: {tid}")
```
**분석 / Analysis (Level 2)**
- L613-617 `_owned_ids`: 원격 목록 id 들 중 이 주체 소유 행만. `principal_sub` 가 None 인 행은 제외(fail-closed).
- L620-629 `_task_id_of`: 작업별 Task id 위치 — get·cancel·resubscribe 는 `id`, push_get 은 1.0 `taskId`(+`id`=설정 id) 또는 0.3 `id`, push_set 은 설정의 `taskId`, send·stream 은 `message.taskId`(이어 보내기). `list`·`card` 는 None.
- L632-636 `_check_task_owner`: 행이 있고 주체가 있으며 다르면 -32001.
**짚어볼 점 / Watch-outs**
- [위험:낮음 · fail-open] L635 **행이 없거나 `principal_sub` 가 None 이면 통과**한다(`_owned_ids` 의 fail-closed 와 반대). 그 뒤 `record`(L593)·`_save_push`(L653)가 호출자를 주인으로 행을 만든다 — 게이트웨이에 기록되지 않은 원격 Task id(다른 경로로 원격에 직접 만든 Task, 또는 감시기 경로로 생긴 주체 없는 행)를 아는 사람은 그 Task 의 주인이 된다. 또 L560 판정과 L573 저장 사이 경쟁(두 사용자가 같은 미기록 id 로 동시에 push_set)에서는 `_save_push` 가 기존 행 주체를 확인하지 않아 뒤 요청이 웹훅을 덮는다. Task id 는 원격이 만든 무작위 값이라 추측은 어렵다. 최소 수정: `principal_sub` None 행은 거부, `_save_push` 에서 주체 재확인.
- [위험:낮음] L615 원격이 거대한 목록을 주면 `IN (...)` 절이 그만큼 커진다 — 상한(예: 500) 권장.

```python path=app/services/a2a_gateway.py lines=639-648
def push_url_problem(url: str | None) -> str | None:
    """클라이언트 푸시 URL 검사 — http(s) 만, 운영(local·test 밖)에서는 사설·루프백·링크 로컬 수신처 거부 (D17-④)."""
    from app.core.http import internal_url
    if not url or not str(url).startswith(("http://", "https://")):
        return "pushNotificationConfig.url 은 http(s) URL 이어야 합니다."
    if settings.app_env not in ("local", "test") and internal_url(str(url)):
        return "푸시 URL 은 공인 수신처여야 합니다 — 사설·루프백·링크 로컬 주소는 받지 않습니다."
    return None
```
**분석 / Analysis (Level 2)**
- L639-646 (2차 신규, Epic 43): 클라이언트 푸시 URL — http(s) 만, local·test 밖에서는 `internal_url`(사설·루프백·링크 로컬·예약·미지정·멀티캐스트, IPv4-mapped 정규화, 이름은 해석해서 판정 — `app/core/http.py:92-120`)이면 거부. 호출 지점: L568(게이트웨이), `app/api/a2a.py:222`(플랫폼 서버 `push_check`). 시험: `test/test_policy_hardening.py:46`.
**짚어볼 점 / Watch-outs**
- [위험:중간 · SSRF(TOCTOU·DNS rebinding)] 검사는 **등록 시점 한 번**이고, 실제 전달(L834-836 → `post_with_retry` → `async_client`)은 링크 로컬·IMDS 만 막는 egress 가드(`app/core/http.py:36-38,128-137`)만 거친다. 또 `internal_url` 은 이름 해석 실패를 **통과**로 본다(`app/core/http.py:119-120`). 시나리오: 사용자가 TTL 0 인 자기 도메인 `https://hook.attacker.example/x` 로 push_set(그 순간은 공인 IP 응답, 또는 NXDOMAIN) → 이후 DNS 를 `10.x`·클러스터 서비스 IP 로 바꿈 → 원격 에이전트가 상태를 푸시하면 게이트웨이가 Task JSON 을 **내부 서비스로 POST**(최대 3회). 리다이렉트는 따라가지 않는다(httpx 기본 `follow_redirects=False`, `async_client` 에 지정 없음 — `app/core/http.py:173-175`). 최소 수정: 전달 직전 `internal_url` 재검사(스레드풀) + 해석한 IP 로 직접 연결(Host·SNI 유지)하거나 연결된 피어 주소를 검증하는 transport, prod 에서 해석 실패는 거부.
- [위험:낮음] L642 prod 에서도 `http://` 를 받는다 — 클라이언트 토큰(`X-A2A-Notification-Token`)과 Task 본문이 평문으로 간다. prod 는 https 만 권장.
- [위험:낮음] L644 `app_env` 기본값이 `local`(`app/core/config.py:17`)이라 환경변수를 빠뜨린 배포는 검사가 꺼진다(fail-open). 배포 파일은 `prod` 를 넣는다(`.env.prod:3`, `deploy/helm/agentnexus/templates/configmap.yaml:9`).
- 가능성(추가 확인 필요): `internal_url` 은 `ip.is_private` 등으로 판정하는데(`app/core/http.py:99`), Python `ipaddress` 는 공유 주소 공간 100.64.0.0/10 을 `is_private` 로 보지 않는다 — 이 대역을 파드·노드망으로 쓰는 클러스터에서는 내부 주소가 통과할 수 있다. `not ip.is_global` 판정이 더 넓다(core/http.py 담당 검증 항목).

```python path=app/services/a2a_gateway.py lines=649-660
def _save_push(agent_id: str, tid: str, token: str, client_cfg: dict, ctx: CallContext) -> None:
    with SessionLocal() as db:
        row = db.get(A2ATask, _tid(agent_id, tid))
        if row is None:
            row = A2ATask(id=_tid(agent_id, tid), remote_task_id=tid, agent_id=agent_id, principal_sub=ctx.principal.sub,
                          history=[], artifacts=[])
            db.add(row)
        row.push_token = token
        row.push_config = {k: client_cfg[k] for k in ("url", "token") if client_cfg.get(k)}
        db.commit()
```
**분석 / Analysis (Level 2)**
- L649-658: Task 행(없으면 호출자 소유로 생성)에 게이트웨이 푸시 토큰과 클라이언트 웹훅 `{url, token}` 저장. `authentication` 은 버린다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L657 클라이언트 웹훅 토큰을 평문 JSON 으로 저장(1차 지적, 미해결 — `app/models.py:394`). L656 은 같은 Task 의 이전 게이트웨이 토큰을 덮는다(1차 지적, 정상 흐름에서는 업스트림 설정도 갱신되어 무해). Epic 43 이전에 저장된 내부 주소 웹훅 행은 재검사 없이 계속 중계된다(마이그레이션 공백, 가능성).

```python path=app/services/a2a_gateway.py lines=661-693
def _stream(agent: AgentRef, ctx: CallContext, rid, url: str, method: str, params: dict, headers: dict,
            up_v: str, client_v: str, binding: str = "JSONRPC", op: str = "stream") -> StreamingResponse:
    async def upstream_events(http, grpc_credentials=None):
        """업스트림 스트림 → (JSON-RPC 형태 메시지) — REST 는 data 가 StreamResponse 그대로, gRPC 는 서버 스트림."""
        if binding == "GRPC":
            from app.services import a2a_grpc
            async for ev in a2a_grpc.stream(url, op, params, headers, grpc_credentials):
                yield ev
            return
        if binding == "HTTP+JSON":
            m, path, query, body = a2a_rest.rest_request(op, params)
            hdr = {"Content-Type": a2a_rest.MEDIA, "Accept": "text/event-stream", "A2A-Version": up_v, **headers}
            req = http.stream(m, f"{url.rstrip('/')}/{path}", params=query, headers=hdr, **({"json": body} if body is not None else {}))
        else:
            hdr = {"Content-Type": "application/json", "Accept": "text/event-stream", "A2A-Version": up_v, **headers}
            req = http.stream("POST", url, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method,
                                                 "params": params}, headers=hdr)
        async with req as res:
            if res.status_code >= 400:
                raise A2AError(-32603, f"원격 에이전트 오류 ({res.status_code})")
            async for line in res.aiter_lines():
                if not line.startswith("data:"):
                    continue
                msg = json.loads(line[5:].strip())
                if binding == "HTTP+JSON":
                    if isinstance(msg.get("error"), dict) and "status" in msg["error"]:
                        code, text = a2a_rest.code_from_status(int(msg["error"].get("code") or 500), msg)
                        yield {"error": {"code": code, "message": text}}
                    else:
                        yield {"result": msg}
                else:
                    yield msg
```
**분석 / Analysis (Level 2)**
- L663-669: gRPC 서버 스트림 — 2차에서 자격 증명 인자 추가(L667).
- L670-677: REST(SSE `data:` 에 StreamResponse 객체) / JSON-RPC(SSE `data:` 에 JSON-RPC 메시지) 요청 준비. `http.stream(...)` 은 컨텍스트 매니저라 `async with` 를 빠져나갈 때 연결이 닫힌다(L678).
- L678-692: SSE 줄 단위 파싱, REST 의 google.rpc.Status 오류는 A2A 오류로.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L684 `json.loads` 실패(`ValueError`)는 아래 `gen` 의 `except` 두 개(L711·L714)에 걸리지 않는다 — 스트림이 오류 이벤트 없이 끊기고 사용량은 "ok"(1차 지적, 미해결). gRPC 의 `ParseError` 도 같다. 여러 줄 `data:` 이벤트 미지원(1차 지적, 미해결). 원격 소유의 이벤트 크기·개수 상한 없음.

```python path=app/services/a2a_gateway.py lines=694-726
    async def gen():
        t0 = time.perf_counter()
        status = "ok"
        try:
            tls_kw = await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url)
            creds = tls_kw.pop("grpc_credentials", None)
            async with async_client(timeout=settings.upstream_timeout, **tls_kw) as http:
                async for msg in upstream_events(http, creds):
                    if "error" in msg:
                        yield _sse(rid, error=msg["error"])
                        status = "error"
                        break
                    canon = await run_in_threadpool(_sanitize_in, agent.id, ctx,
                                                    ap.event_from(msg.get("result") or {}))
                    await run_in_threadpool(record, agent.id, canon, ctx, None, "sse",
                                            (client_v, up_v, ctx.binding, binding))
                    yield _sse(rid, ap.event_to(canon, client_v))
        except A2AError as exc:
            status = "error"
            yield _sse(rid, error={"code": exc.code, "message": exc.message})
        except httpx.HTTPError as exc:
            status = "error"
            yield _sse(rid, error={"code": -32603, "message": f"연결 실패: {type(exc).__name__}"})
        finally:
            await run_in_threadpool(_usage, ctx, agent.id, int((time.perf_counter() - t0) * 1000), status)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _sse(rid, result=None, error=None) -> str:
    body = {"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
```
**분석 / Analysis (Level 2)**
- L698-699 (2차 변경): TLS kwargs 를 만들고 `grpc_credentials` 를 **빼서**(httpx 는 모르는 인자) gRPC 스트림에만 넘긴다. gRPC 바인딩에서도 쓰지 않는 httpx 클라이언트(L700)를 하나 연다(무해, 약간의 낭비).
- L701-710: 이벤트마다 소독 → 영속 → 클라이언트 버전 변환 → SSE. 오류 이벤트면 1건 내고 종료.
- L711-718: A2AError·연결 오류는 SSE 오류 이벤트, `finally` 에서 사용량.
- L722-724 `_sse`: JSON-RPC 응답 SSE 포맷.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L717-718 클라이언트가 끊으면 Starlette 가 제너레이터를 취소하고, `finally` 안의 `await run_in_threadpool(...)` 도 다시 취소되어 사용량이 빠질 수 있다 — 45th 에서 같은 원인을 동기 반납으로 고친 기록이 있다(`test/impl/45th/result.md` "회차 중 발견"). 가능성(추가 확인 필요).
- [위험:낮음] L700 읽기 타임아웃이 이벤트 간격 기준(30초, 1차 지적) — gRPC 는 전체 스트림 기한이라 의미가 다르다(a2a_grpc 보고서).

```python path=app/services/a2a_gateway.py lines=727-746
# ───────────────────────── 스킬 기반 라우팅 (FR-G01) ─────────────────────────
TIER_RANK = {"verified": 0, "trusted": 1, "untrusted": 2, "blocked": 9}


def route_by_skill(skill: str, p: Principal) -> str:
    with SessionLocal() as db:
        rows = db.scalars(select(A2AAgent).where(A2AAgent.status == "active")).all()
        cands = [a for a in rows if a.trust_tier != "blocked" and any(
            s.get("id") == skill or skill in (s.get("tags") or []) for s in a.skills or [])]
        cands.sort(key=lambda a: (TIER_RANK.get(a.trust_tier, 5), a.id))
        chosen = cands[0].id if cands else None
        db.add(RoutingDecision(id=new_id("rd"), trace_id=current_trace_id(), kind="a2a", subject=p.sub,
                               requested=f"skill:{skill}", chosen=chosen, candidates=[a.id for a in cands],
                               reason="스킬 기반 라우팅 (신뢰 등급 우선)", status="ok" if chosen else "no_match"))
        db.commit()
    if chosen is None:
        raise A2AError(-32001, f"스킬 '{skill}' 을 가진 원격 에이전트가 없습니다.", http_status=404)
    return chosen
```
**분석 / Analysis (Level 2)**
- L731-744: 활성·비차단 에이전트 중 스킬 id·태그 일치 → 신뢰 등급 → id 순 첫 번째, 결정 로그. 호출처: `app/api/a2a.py:303`(스레드풀).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L734 `untrusted` 도 후보(1차 지적, 미해결) — 실제 호출은 `_check` 의 미신뢰 기본 거부(`app/services/governance.py:326-330`)로 막혀, 더 나은 후보가 있어도 미신뢰가 선택되면 실패한다. 모든 활성 에이전트를 메모리로 읽는 것도 동일.

```python path=app/services/a2a_gateway.py lines=747-765
# ───────────────────────── 푸시 수신 · 감시기 ─────────────────────────
class PushRejected(Exception):
    """푸시 서명 검증 실패 (SCR-G07 ⑧) — 401."""


_JWKS_CACHE: dict[str, tuple[float, dict]] = {}


def _push_jwks(url: str) -> dict:
    hit = _JWKS_CACHE.get(url)
    if hit and hit[0] > time.time():
        return hit[1]
    from app.core.http import sync_client
    with sync_client(timeout=5) as http:
        doc = http.get(url).raise_for_status().json()
    _JWKS_CACHE[url] = (time.time() + 300, doc)
    return doc
```
**분석 / Analysis (Level 2)**
- L748-749 `PushRejected`: 서명 검증 실패 → `app/api/a2a.py:292-293` 에서 401.
- L752-763 `_push_jwks`: 에이전트별 푸시 서명 JWKS 5분 캐시. 동기 클라이언트지만 호출부가 스레드풀(L811)이라 루프를 막지 않는다. `jwks_url` 은 `a2a:write` 가 바꿀 수 있는 `auth.push_verify` 값(`app/api/a2a.py:538-540` — http(s) 형식만 검사).
**짚어볼 점 / Watch-outs**
- [위험:낮음] 캐시 키 = URL, 상한 없음(운영자 설정 값이라 수가 적다). kid 를 못 찾아도 캐시를 갱신하지 않아 원격 키 회전 직후 최대 5분 거부된다.

```python path=app/services/a2a_gateway.py lines=766-794
def verify_push_jwt(cfg: dict, authorization: str | None, raw: bytes) -> dict:
    """원격 에이전트가 서명한 푸시 JWT 검증 — JWKS 서명·발급자·발급 시각(±5분)·본문 해시(request_body_sha256, 있으면)."""
    import hashlib

    import jwt as pyjwt
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PushRejected("서명 토큰 없음")
    tok = authorization.split(" ", 1)[1]
    try:
        kid = pyjwt.get_unverified_header(tok).get("kid")
        keys = _push_jwks(cfg["jwks_url"]).get("keys") or []
        jwk = next((k for k in keys if not kid or k.get("kid") == kid), None)
        if jwk is None:
            raise PushRejected("서명 키(kid)를 JWKS 에서 찾지 못함")
        key = pyjwt.PyJWK(jwk).key
        claims = pyjwt.decode(tok, key, algorithms=["RS256", "ES256", "EdDSA"], options={"verify_aud": False},
                              issuer=cfg.get("issuer") or None)
    except PushRejected:
        raise
    except Exception as exc:  # noqa: BLE001 — 서명·형식 오류는 모두 거부
        raise PushRejected(f"서명 검증 실패: {type(exc).__name__}") from exc
    if abs(time.time() - float(claims.get("iat") or 0)) > 300:
        raise PushRejected("발급 시각이 5분 범위를 벗어남 (재전송 의심)")
    want = claims.get("request_body_sha256")
    if want and want != hashlib.sha256(raw).hexdigest():
        raise PushRejected("본문 해시 불일치")
    return claims
```
**분석 / Analysis (Level 2)**
- L771-786: Bearer JWT → kid 로 JWKS 키 선택 → **비대칭 알고리즘 허용 목록 고정**(L781 — HS256·none 혼동 공격 차단), 발급자(설정 시) 확인. 형식·서명 오류는 모두 거부(fail-closed).
- L787-791: `iat` ±5분, 본문 SHA-256 클레임이 있으면 일치.
**짚어볼 점 / Watch-outs**
- [위험:낮음] `jti` 재사용 확인 없음 — 5분 창 안 재전송 가능(1차 지적, 미해결). `issuer` 미설정이면 아무 발급자나 통과, `verify_aud=False`(1차 지적, 미해결). `request_body_sha256` 가 **선택**이라 해시 없는 토큰은 5분 동안 임의 본문에 재사용할 수 있다 — 해시 필수화를 권장.

```python path=app/services/a2a_gateway.py lines=795-829
async def receive_push(token: str, body: dict, header_token: str | None, authorization: str | None = None,
                       raw: bytes = b"") -> bool:
    def _find():
        with SessionLocal() as db:
            row = db.scalars(select(A2ATask).where(A2ATask.push_token == token)).first()
            if row is None:
                return None
            agent = db.get(A2AAgent, row.agent_id)
            return (row.agent_id, row.remote_task_id, dict(row.push_config or {}),
                    dict(((agent.auth or {}) if agent else {}).get("push_verify") or {}))
    found = await run_in_threadpool(_find)
    if found is None or (header_token and header_token != token):
        return False
    agent_id, remote_id, client, verify = found
    if verify.get("jwks_url"):
        try:
            await run_in_threadpool(verify_push_jwt, verify, authorization, raw)
        except PushRejected as exc:
            reason = str(exc)
            await run_in_threadpool(lambda: audit.append("a2a.push.reject", actor=f"agent:{agent_id}", target=agent_id,
                                                         decision="deny", data={"reason": reason}))
            raise
    canon = ap.result_from(body) if "status" in body or "task" in body else ap.event_from(body)
    pushed = canon.get("id") if canon.get("kind") == "task" else canon.get("taskId")
    if pushed != remote_id:  # 푸시 토큰은 Task 하나에 묶인다 — 같은 에이전트의 다른 Task(남의 것 포함)를 고치지 못하게
        await run_in_threadpool(lambda: audit.append("a2a.push.reject", actor=f"agent:{agent_id}", target=agent_id,
                                                     decision="deny", data={"reason": "task_mismatch"}))
        return False
    await run_in_threadpool(record, agent_id, canon, None, None, "push")
    if client and client.get("url"):  # 클라이언트 웹훅 중계 — 백그라운드로(원격 푸시 요청을 붙잡지 않게), 일시 장애는 재시도 (D19-②)
        task = asyncio.create_task(_relay_push(client, canon, agent_id, remote_id))
        _PUSH_RELAYS.add(task)
        task.add_done_callback(_PUSH_RELAYS.discard)
    return True
```
**분석 / Analysis (Level 2)**
- L797-805: 푸시 토큰으로 Task 행 + 에이전트 `push_verify` 설정 조회(2차: `remote_task_id` 도 함께 — L803).
- L806-807: 토큰 행 없음·헤더 토큰 불일치면 404.
- L809-816: 검증 설정이 있으면 JWT 검증, 실패는 감사 후 401.
- L817-822 (2차 신규): 본문 → 정준형 → **본문 Task id 가 토큰에 묶인 Task 와 같아야** 기록(1차 [위험:중간] 해결, `test/test_review_fixes.py:358`). 불일치는 감사 후 404.
- L823: 영속(`ctx=None` — 행이 이미 있으므로 주체는 바뀌지 않는다).
- L824-827 (2차 신규): 클라이언트 웹훅 중계를 **백그라운드 Task** 로 — 원격의 푸시 요청을 붙잡지 않는다. `asyncio.create_task` 결과는 이벤트 루프가 약하게만 참조하므로 집합에 넣어 GC 로 사라지지 않게 한다(흔한 실수를 피한 올바른 패턴).
**짚어볼 점 / Watch-outs**
- [위험:중간 · 가드 우회] L817-827 푸시 본문은 **`_sanitize_in` 을 거치지 않고** `A2ATask.history·artifacts` 에 저장되고(L823) 그대로 클라이언트 웹훅으로 나간다(L825). 저장된 내용은 콘솔·채팅에 제공된다(`app/api/a2a.py:617-629` Task 상세, `:662-675` 아티팩트 본문, `:655-658` 실행별 이벤트 텍스트). 시나리오: 승인된 원격 에이전트(또는 `push_verify` 없는 에이전트의 푸시 토큰을 아는 자 — L580-612 블록의 push_get 노출 참고)가 상태 푸시의 메시지·아티팩트에 지시문 주입·시크릿 형태 문자열을 넣으면, 단건·스트림 경로에서는 지워졌을 내용이 그대로 저장·표시·중계된다(`tool_response_sanitize` 설정 무력화). 최소 수정: `record` 전에 `_sanitize_in`(행위자 `agent:{id}`)을 스레드풀로 적용하고 그 결과를 중계.
- [위험:낮음] L801-809 에이전트 상태를 보지 않는다 — 비활성·차단 에이전트의 푸시도 기록·중계된다. `push_verify` 가 없으면 URL 토큰(144비트)만으로 인증(1차 지적, 설계).
- [위험:낮음] L817 `body` 가 dict 가 아니면(`[]`·숫자 JSON — `app/api/a2a.py:286` 은 형식 검사 없음) `"status" in body` 또는 정준화에서 `TypeError`/`AttributeError` → 500.
- [위험:낮음] L824-827 동시 중계 수 상한이 없다 — 한 중계는 최대 약 16.5초(5초 타임아웃 × 3 + 0.5·1초 백오프, `app/core/http.py:156-170`). 원격이 푸시를 몰아 보내면 그 수만큼 동시 POST 가 생긴다. 또 재시도 중인 이전 이벤트가 나중 이벤트보다 늦게 도착할 수 있어(순서 미보장) 클라이언트가 마지막으로 받은 상태가 과거 상태일 수 있다. 최소 수정: 목적지별 세마포어·순차 큐.

```python path=app/services/a2a_gateway.py lines=830-839
_PUSH_RELAYS: set = set()  # 루프는 Task 를 약하게만 참조 — 끝날 때까지 붙잡아 둔다


async def _relay_push(client: dict, canon: dict, agent_id: str, remote_id: str) -> None:
    from app.core.http import post_with_retry
    if not await post_with_retry(client["url"], json=canon, headers={"X-A2A-Notification-Token": client.get("token", "")}):
        logs.event("warning", "gateway", "클라이언트 푸시 중계 실패(재시도 후)", agent=agent_id, task=remote_id)
```
**분석 / Analysis (Level 2)**
- L831: 진행 중 중계 Task 강참조 집합(L827 완료 콜백으로 제거).
- L834-837: `post_with_retry` — 연결 오류·5xx·429 는 0.5·1초 지수 백오프로 최대 3회, 그 밖 4xx 와 egress 차단은 즉시 포기(`app/core/http.py:156-170`, `test/test_lv2_hardening.py:178`). 실패는 경고 로그 — 1차의 `contextlib.suppress(Exception)` 조용한 삼킴 해소. 3xx 는 따라가지 않고 성공으로 친다(`res.status_code < 400`).
**짚어볼 점 / Watch-outs**
- [위험:낮음] 실패가 로그에만 남고 감사·메트릭·Task 이벤트에는 없다 — 클라이언트 웹훅 장애를 콘솔에서 볼 수 없다. 429 의 `Retry-After` 를 존중하지 않는다(core/http.py 담당).
- 전달 시점 URL 재검사 없음 → L639-648 블록 [위험:중간] SSRF 의 실제 싱크.

```python path=app/services/a2a_gateway.py lines=840-852
def _principal_for(sub: str | None) -> Principal | None:
    if not sub:
        return None
    if sub.startswith("workload:"):
        return Principal(sub=sub, kind="workload", roles=["user"])
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.sub == sub)).first()
        if u is None:
            return None
        return Principal(sub=u.sub, kind="delegation", name=u.name or u.username, email=u.email,
                         roles=list(u.roles or ["user"]), team=u.team, user_id=u.id, actor="gateway-watcher")
```
**분석 / Analysis (Level 2)**
- L840-850: 감시기가 사용자 대신 원격을 조회할 주체 — 워크로드면 고정 역할 `["user"]`, 사용자면 DB 정보로 `delegation` 주체(행위자 `gateway-watcher`).
**짚어볼 점 / Watch-outs**
- 1차 정정: 1차는 "사용자가 비활성화되면 L785 조회로 막힌다"고 했지만, 조회는 **존재만** 확인하고 `u.status` 를 보지 않는다(L846-848). `identity.exchange` 도 주체 상태를 확인하지 않는다(`app/services/identity.py:173-210`). → 비활성 사용자 명의로 계속 교환 토큰이 발급된다. 상세·수정은 L853-878 블록의 [위험:중간] 거버넌스 우회 항목.
- [위험:낮음] L844 워크로드는 실제 역할·스코프 대신 `["user"]` 로 재구성된다 — 교환 토큰의 `roles` 클레임이 실제와 다를 수 있다. 또 `Workload.enabled` 를 보지 않는다 — 일반 인증은 비활성 워크로드를 거부하지만(`app/core/auth.py:236`) 감시기는 비활성화된 워크로드 명의로도 교환·폴링을 계속한다(사용자 `status` 미확인과 같은 계열).

```python path=app/services/a2a_gateway.py lines=853-878
async def poll_once(limit: int = 20) -> int:
    """비터미널 outbound Task 를 tasks/get 으로 갱신 (푸시 미사용·유실 대비)."""
    def _due():
        with SessionLocal() as db:
            rows = db.scalars(select(A2ATask).where(A2ATask.direction == "outbound",
                                                    A2ATask.state.in_(("submitted", "working")))
                              .order_by(func.coalesce(A2ATask.last_polled_at, A2ATask.created_at)).limit(limit)).all()
            for r in rows:  # 시도 시각을 먼저 남겨 계속 실패하는 Task 가 앞자리를 차지하지 않게(돌아가며 폴링)
                r.last_polled_at = utcnow()
            db.commit()
            return [(r.agent_id, r.remote_task_id, r.principal_sub) for r in rows]
    n = 0
    for agent_id, remote, sub in await run_in_threadpool(_due):
        p = await run_in_threadpool(_principal_for, sub)
        if p is None:
            continue
        with contextlib.suppress(A2AError, httpx.HTTPError):
            agent = await run_in_threadpool(load_agent, agent_id)
            up_v, url, binding = _upstream_version(agent, ap.V10)
            result = await _rpc(url, ap.method_for("get", up_v), {"id": remote}, await upstream_headers(agent, p),
                                up_v, await tls_kwargs(agent.auth.get("tls"), f"a2a:{agent.id}", url), binding, "get")
            await run_in_threadpool(record, agent_id, ap.result_from(result), None, None, "poll")
            n += 1
    return n
```
**분석 / Analysis (Level 2)**
- L855-863 (2차 변경): submitted·working outbound Task 를 `coalesce(last_polled_at, created_at)` 오름차순 20개 — **시도 전에** `last_polled_at` 을 기록·커밋하므로, 주체가 없거나(`continue`) 실패한 Task 도 다음 주기에는 뒤로 밀린다(1차 [위험:중간] 기아 해결, `test/test_review_fixes.py:378`). 커밋 후 `rows` 속성 접근은 `expire_on_commit` 설정에 따라 재조회가 일어날 수 있지만 같은 세션 안이라 안전하다.
- L865-875: 각 Task 를 사용자 신원 교환 토큰으로 `tasks/get` → 영속.
**짚어볼 점 / Watch-outs**
- [위험:중간 · 거버넌스 우회] L869-874 감시기는 `_check`(에이전트 `status`·`trust_tier`·정책 — `app/services/governance.py:316-333`)도, 사용자 활성 여부(L846-848)도 보지 않는다. `load_agent` 는 상태와 무관하게 돌려준다(L441-446). 시나리오: 보안 담당이 침해 의심 에이전트를 `blocked` 로 바꾸거나 사용자를 비활성화해도, 그 에이전트의 working Task 들은 5초마다(리스 보유 워커) 계속 폴링되고, 매번 해당 사용자 명의 교환 토큰(`a2a:{id}`, TTL 600초 — L125-129)이 **차단된 에이전트로 전송**되며 응답이 기록된다. 원격이 working 을 계속 돌려주면 끝나지 않는다. 최소 수정: 루프에서 에이전트 행의 `status == "active"`·`trust_tier != "blocked"` 확인(또는 `governance.check_agent` 재사용), `_principal_for` 에서 `u.status == "active"` 확인, 거부면 Task 를 `failed`/`rejected` 로 표시해 폴링 대상에서 뺀다.
- L874 폴링 결과도 `_sanitize_in` 없이 저장된다 — L795-829 블록의 [위험:중간] 가드 우회와 같은 항목(같은 수정으로 함께 해결).
- [위험:낮음] L869 `suppress(A2AError, httpx.HTTPError)` 는 실패를 조용히 삼키고, 그 밖 예외(L360 `ValueError`, `status_from` 의 `AttributeError`, gRPC `ParseError`, DB 오류)는 루프를 빠져나가 **그 주기의 나머지 Task 를 건너뛴다** — 순환 덕에 기아로 번지지는 않는다. 최소 수정: Task 단위 `try/except Exception` + `logs.event`.
- [위험:낮음] L874 응답 Task id 를 `remote` 와 비교하지 않는다(L383-430 블록) — 다른 id 면 주체 없는 행이 생기고 그 행은 L635 에서 누구에게나 열린다.
- [위험:낮음] 순차 처리라 한 주기 최악 시간은 20 × (교환 + `upstream_timeout` 30초) ≈ 10분인데 리스 TTL 은 15초(L885) — 아래 L879-886 블록.

```python path=app/services/a2a_gateway.py lines=879-886
async def watcher(interval: float = 5.0) -> None:
    from app.services import scheduler
    while True:
        await asyncio.sleep(interval)
        with contextlib.suppress(Exception):
            # 리스를 가진 워커만 폴링 — 같은 Task 를 워커마다 조회하지 않게 (D18-②)
            if await run_in_threadpool(scheduler.lease, "a2a_watch", interval * 3):
                await poll_once()
```
**분석 / Analysis (Level 2)**
- L879-886 (2차 변경): 주기마다 settings 행 조건부 UPDATE 리스(`app/services/scheduler.py:24-43` — 보유자가 나이거나 만료면 1행 갱신, 없으면 INSERT·IntegrityError 경합 처리)를 잡은 워커만 `poll_once`. 보유자는 매 주기 갱신한다(`test/test_multiworker.py:77`, prod 스모크 P45-05 `test/impl/45th/result.md`). 감시기 Task 생성은 `app/main.py:105-107`.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L883 `suppress(Exception)` — 리스 DB 오류·`poll_once` 예외가 흔적 없이 사라진다(1차 INDEX §4.1 패턴 6, 미해결).
- [위험:낮음] L885 리스 TTL(`interval*3` = 15초)이 `poll_once` 최악 시간(위 ≈10분)보다 훨씬 짧다 — 느린 원격이 몰리면 실행 중 리스가 만료되어 다른 워커도 폴링을 시작한다. `_due` 의 선택→갱신이 원자적이지 않아(`SELECT … FOR UPDATE SKIP LOCKED` 없음) 두 워커가 같은 순간 고르면 같은 Task 를 중복 폴링한다. 최소 수정: Task 마다 리스 갱신 또는 조건부 UPDATE 로 행 점유.

## 파일 평가 / File Assessment
- 강점: (1) Task 소유권이 영속 행 주체로 판정되어 JSON-RPC·REST·gRPC·다중 워커에서 같은 결과(L559-560, L606-609, L613-636). (2) 푸시 토큰↔Task 묶음(L818-822)과 백그라운드 재시도 중계·실패 로그(L824-837)로 1차 무결성·조용한 실패 지적 해소. (3) 감시기 순환(L859-862)·리스(L885)로 기아·워커 중복 폴링 해소, grpcs 가 HTTP 와 같은 TLS 설정·mTLS 정책을 따름(L80-81).
- 위험: (1) [위험:중간] 푸시 URL 검사-사용 시점 차이·해석 실패 통과로 전달 시 내부망 POST 가능(`app/services/a2a_gateway.py:639-646`, `:834-836`). (2) [위험:중간] 등록 시 동기 DNS 해석이 이벤트 루프를 막음(`app/services/a2a_gateway.py:568`). (3) [위험:중간] 푸시·폴링 경로가 입력 소독을 건너뛰고(`:817-827`, `:874`), 감시기가 에이전트 차단·사용자 비활성을 무시한 채 사용자 명의 토큰을 계속 보냄(`:840-876`). (4) [위험:중간] 원격에 넘기는 푸시 콜백이 내부 `GATEWAY_URL`(compose `http://api:8000`·Helm `http://<fn>-api:…`) — 외부 에이전트는 푸시를 보낼 수 없고 내부 이름·푸시 토큰이 외부로 나감(`app/services/a2a_gateway.py:571`). (5) [위험:중간] push_set 이 업스트림 확인 전에 임의 taskId 행을 `submitted` 로 만들어 감시기를 희석·영구 폴링(`:573`, `:649-658`, `app/models.py:389`).
- 개선 권고(영향순): (0) 푸시 콜백 기준을 `public_base_url` 로(사설망 에이전트만 예외), push_set 은 업스트림 성공 뒤·기존 행에만 저장. (1) 웹훅 전달 직전 재검사 + 해석 IP 고정 + 비동기 해석(스레드풀), prod 해석 실패 거부. (2) 푸시·폴링에 `_sanitize_in`·에이전트 상태/정책·사용자 상태 확인을 넣고, 거부 시 Task 를 터미널로. (3) 응답 Task id 묶음(get·cancel·poll), `list` fail-closed, `push_get` 을 저장된 클라이언트 설정으로 응답, `_check_task_owner` 의 주체 없는 행 거부. (4) 1차 잔여 낮음(비 JSON 응답·사용량 status·history 상한·jti·Host 헤더·캐시 무효화) 정리.
- 파일 위험 등급 / Risk grade: **높음** — 보안 경계 핵심 모듈(외부 에이전트·클라이언트 웹훅·사용자 토큰 교차점)에 미해결 [위험:중간] 6건(SSRF TOCTOU·루프 차단·가드 우회·감시기 거버넌스 우회·내부 콜백 주소·쓰레기 행 감시기 희석). 1차 [높음](시크릿 외부 전송)은 API 계층에서 차단되어 이 파일의 잔여는 [낮음].
- 재읽기 권고 레벨: 2 — 기능은 늘었지만 신뢰 경계(푸시·폴링·소유권)가 함수 여러 곳에 흩어져 있어, 경로별로 같은 검사(소독·정책·id 묶음)가 적용되는지 한 번 더 따라 읽어야 한다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| [높음] L243-294 사용자 병합 `auth` 의 `secret_ref`·`token_url` 로 시크릿 외부 전송(INDEX §4.3 #1) | 부분 해결 | API 차단: `app/api/a2a.py:457`·`:535-537` → `app/services/secrets.py:17-38`(C39-12 `test/test_review_fixes.py:247`). 싱크는 동일: `app/services/a2a_gateway.py:249-258`, `:283-289` |
| L273-294 `("cc", agent.id)` 캐시가 auth 변경 후에도 유지 | 미해결 | `app/services/a2a_gateway.py:276-279`; `_TOKENS` 참조는 L234-277 뿐 |
| [낮음] L337·L358 200 비 JSON → ValueError 미처리 | 미해결 | `app/services/a2a_gateway.py:339`, `:360` |
| L342-358 원격 응답 크기 제한 없음 | 미해결 | `app/services/a2a_gateway.py:325-327`, `:351-353` |
| [중간] L563 클라이언트 웹훅 URL 검증 없이 저장(SSRF) | 부분 해결 | 등록 시 검사 `app/services/a2a_gateway.py:568-569`, `:639-646`(`test/test_policy_hardening.py:46`). 전달 시 재검사 없음·해석 실패 통과 `:834-836`, `app/core/http.py:119-120` |
| L582-584 A2AError 외 예외는 사용량 ok | 미해결 | `app/services/a2a_gateway.py:585-587` |
| L611 클라이언트 웹훅 토큰 평문 저장 | 미해결 | `app/services/a2a_gateway.py:657` |
| push_set 재설정 시 이전 게이트웨이 토큰 덮어씀 | 미해결 | `app/services/a2a_gateway.py:656` (정상 흐름 영향 없음) |
| [낮음] L638 SSE `json.loads` ValueError → 오류 이벤트 없이 끊김·사용량 ok | 미해결 | `app/services/a2a_gateway.py:684`, `:711-718` |
| L635 여러 줄 `data:` 미지원 | 미해결 | `app/services/a2a_gateway.py:681-684` |
| L653 읽기 타임아웃 = 이벤트 간격 | 미해결 | `app/services/a2a_gateway.py:700` |
| [낮음] 푸시 JWT `jti` 재전송 확인 없음 | 미해결 | `app/services/a2a_gateway.py:787-791` |
| L734 issuer 미설정 시 아무 발급자 통과 | 미해결 | `app/services/a2a_gateway.py:782` |
| [중간] L769-770 푸시 본문 task id 미검증(다른 Task 조작) | 해결 | `app/services/a2a_gateway.py:803-808`, `:817-822`; `test/test_review_fixes.py:358` |
| [중간] L771-775 웹훅 중계 SSRF + `suppress(Exception)` 조용한 실패 | 부분 해결 | 조용한 실패 해소 `app/services/a2a_gateway.py:834-837`(경고 로그·재시도). SSRF 는 위 웹훅 행과 같음 |
| 검증 설정 없으면 URL 토큰만으로 인증 | 미해결 | `app/services/a2a_gateway.py:809` (설계) |
| [중간] L792-812 감시기 기아(가장 오래된 20개만 재시도) | 해결 | `app/services/a2a_gateway.py:859-862`; `test/test_review_fixes.py:378` |
| [낮음] L805·L818 조용한 예외 삼킴, 비 A2A 예외가 주기 중단 | 부분 해결 | 기아는 순환으로 해소. 삼킴·중단은 그대로 `app/services/a2a_gateway.py:869`, `:883` |
| L815-819 감시기가 워커마다 실행(리스 없음) | 해결 | `app/services/a2a_gateway.py:884-886`; `test/test_multiworker.py:77` |
| L784-789 위임 폴링("비활성 사용자는 조회로 막힘") | 미해결 (1차 판단 정정) | 상태 미확인 `app/services/a2a_gateway.py:846-850`; 교환도 미확인 `app/services/identity.py:173-210` |
| L451 `caller_agent` 가 클라이언트 헤더(정책 위조) | 해결 | `app/api/a2a.py:47-48` → `app/services/policy.py:56-73`; `test/test_caller_trust.py:38` |
| L112-116 원격 카드가 인증 스킴·헤더 결정 | 미해결 | `app/services/a2a_gateway.py:114-118` |
| L119 카드 변경이 카탈로그를 재검토 전 즉시 덮어씀 | 미해결 | `app/services/a2a_gateway.py:119-121` |
| L167 함수 안 `import re` | 미해결 | `app/services/a2a_gateway.py:169` |
| L173 `db.get` None 이면 AttributeError | 미해결 | `app/services/a2a_gateway.py:175-176`, `:200-201` |
| L159-165 카드 URL 내부망 조회 가능 | 미해결 | egress 가드 범위 `app/core/http.py:34-38` (제품 결정) |
| gateway_card Host 헤더 기반 URL | 미해결 | `app/services/a2a_gateway.py:223`; `app/api/a2a.py:318` |
| `_cache_token` 중복 코드·만료 전 항목 상한 없음 | 미해결 | `app/services/a2a_gateway.py:237-242` |
| [낮음] L398·L403 `obj["status"]["state"]` KeyError | 미해결 (코드 동일, 실질 위험 낮음) | `app/services/a2a_gateway.py:400`, `:405`; 정준화가 status 를 채움 `app/services/a2a_proto.py:137-144,156-162` |
| `history` 상한 없음 | 미해결 | `app/services/a2a_gateway.py:401`, `:409` |
| 스트림은 반환 순간 ok 로 집계 | 미해결 | `app/services/a2a_gateway.py:472-474` |
| `fix` 클로저가 바깥 `db` 늦은 참조 | 미해결 (스타일) | `app/services/a2a_gateway.py:502-516` |
| 파일 파트 가드 미검사 | 미해결 | `app/services/a2a_gateway.py:503-515`, `:530-538` |
| `untrusted` 도 스킬 라우팅 후보 | 미해결 | `app/services/a2a_gateway.py:734` |
