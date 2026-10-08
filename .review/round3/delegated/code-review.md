# 결함 리뷰 3차 (code-review 폴백)

> 대상: /Users/wise/Developer/wise-AgentNexus (FastAPI + SQLAlchemy 2, Python 3.12, anyio/asyncio; local SQLite, prod PostgreSQL 2 uvicorn workers + Valkey)
> 날짜: 2026-10-08
> 방법: 정적 읽기 (static read), D20-/D21- 마커 대상 코드 전수 정독, oh-my-claudecode:code-reviewer 서브에이전트 폴백
> 비고: 2차 리뷰(.review/round2/delegated/code-review.md) 이후 변경 사항 대상. CR2-01·CR2-02 수정 여부 확인 포함.

## 요약 — 심각도별 건수
- **CRITICAL**: 0건
- **HIGH**: 1건
- **MEDIUM**: 2건
- **LOW**: 1건

---

## 발견 사항

### CR3-01 | HIGH | `drop_tokens()` 가 교환 토큰(exchange token)을 무효화하지 못함 — 캐시 키 불일치

**위치**: `app/services/a2a_gateway.py:260–261`, `app/services/a2a_gateway.py:276–281`
**확신도**: Confirmed (코드 경로 끝까지 추적)

**근거**:

교환 토큰 캐시 키 등록(260–272행):

```python
key = (p.sub, agent.id)                          # 예: ("user123", "my-agent")
…
_cache_token(key, time.time() + tok["expires_in"], tok["access_token"])
```

`drop_tokens` 무효화 조건(276–278행):

```python
def drop_tokens(agent_id: str) -> int:
    gone = [k for k in list(_TOKENS)
            if k == ("cc", agent_id)                          # CC 토큰: ("cc", "my-agent") ✓ 일치
            or (len(k) > 1 and k[1] == f"a2a:{agent_id}")]   # 교환 토큰: k[1]=="my-agent" ≠ "a2a:my-agent" ✗ 불일치
```

교환 토큰의 `k[1]`은 `agent.id`(예: `"my-agent"`)인데, `drop_tokens`는 `f"a2a:{agent_id}"`(예: `"a2a:my-agent"`)와 비교한다. 따라서 **교환 토큰은 절대로 무효화되지 않는다**.

`drop_tokens`는 에이전트 인증 설정 변경 시 호출된다(`app/api/a2a.py:568`):
```python
a2a_gateway.drop_tokens(a.id)  # 바뀐 인증으로 다시 받게 (D21-⑤)
```

CC 토큰(`("cc", agent_id)`)은 정확히 일치하여 정상 무효화된다. 교환 토큰만 빠진다.

**실패 시나리오**:
1. 운영자가 원격 에이전트 `X`의 인증 설정을 변경(예: 스코프 축소, 토큰 교환 비활성화)
2. `drop_tokens("X")` 호출 — CC 토큰만 삭제되고 교환 토큰은 캐시에 남음
3. 후속 요청에서 `upstream_headers()`가 `_TOKENS.get((user_sub, "X"))`로 **이전 교환 토큰**을 계속 사용
4. 변경된 인증 정책과 다른 자격으로 원격 에이전트를 호출 — `TokenExchangePolicy` 변경이나 교환 비활성화 의도가 반영되지 않음
5. 교환 토큰 TTL(기본 600초)이 지나야 자연 만료

**권고 수정**:
교환 토큰의 캐시 키 `k[1]`이 `agent.id`이므로, `drop_tokens` 비교 조건을 맞춘다:
```python
# a2a_gateway.py:278 — "a2a:" 접두어 제거
gone = [k for k in list(_TOKENS) if k == ("cc", agent_id) or (len(k) > 1 and k[1] == agent_id)]
```

---

### CR3-02 | MEDIUM | SSE 스트리밍 중 클라이언트 연결 끊김 시 한도 예약(hold) 누수

**위치**: `app/api/llm.py:91–120` (`_sse` 비동기 제너레이터)
**확신도**: Confirmed (Python async generator 종료 프로토콜 기준)

**근거**:

`_sse` 제너레이터에 `finally` 블록이 없다:
```python
async def _sse(ctx, plan, body, t0):                     # :91
    …
    try:                                                   # :94
        async for kind, payload in llm.execute_stream(…):  # :95
            …
            yield f"data: …"                               # :108 — yield 지점
    except ApiError as exc:                                # :109
        …
    except ProviderError as exc:                           # :113
        …
    yield "data: [DONE]\n\n"                               # :116
    …
    await run_in_threadpool(_final, ctx, plan, …)          # :120 — hold 정산/반환
```

클라이언트가 스트림 도중 연결을 끊으면:
1. Starlette가 async generator의 `aclose()` 호출
2. 현재 `yield` 지점(108행)에서 `GeneratorExit` 발생
3. `GeneratorExit`는 `BaseException` — `except ApiError`·`except ProviderError`에 잡히지 않음
4. 제너레이터 즉시 종료 → 120행의 `_final` 실행 안 됨
5. `plan.holds` (토큰 창 예약 `tok:*` + 예산 카운터 `usdhold:*`) 미반환

`ratelimit.HOLD_TTL_S = 300`(5분) 후 자동 만료되지만, 그 사이:
- 토큰 창에 예약량이 남아 실제 사용량보다 큰 값으로 잡힘
- 같은 주체/팀의 후속 요청에 거짓 429 응답 가능
- 예산 카운터가 진행 중 비용으로 부풀려져 예산 초과 판정 가능

비스트리밍 경로(`chat_completions` 72–73행)는 `except ApiError`에서 `_final`을 호출하므로 문제없다.

**실패 시나리오**:
1. 사용자가 스트리밍 LLM 호출 시작 — `prepare`에서 토큰 1000개 예약(hold)
2. 첫 청크를 받은 뒤 브라우저 탭 닫기(또는 네트워크 끊김)
3. `_sse` 제너레이터 `GeneratorExit` → `_final` 미실행 → 토큰 1000개 예약이 5분간 남음
4. 같은 사용자의 즉시 후속 요청 — 토큰 창에 예약분까지 합산되어 `tokens_per_min` 한도 초과 → 429

**권고 수정**:
`_sse`를 `try/finally`로 감싸되, `finally`에서는 (취소 중 `await` 재취소 위험을 피해) 동기적으로 hold만 반환:
```python
async def _sse(ctx, plan, body, t0):
    tgt, attempts, usage, out_chars = None, [], None, 0
    status = "ok"
    try:
        try:
            async for kind, payload in llm.execute_stream(plan, body):
                …
                yield …
        except ApiError as exc:
            …
        except ProviderError as exc:
            …
        yield "data: [DONE]\n\n"
        …
        await run_in_threadpool(_final, ctx, plan, tgt, usage, t0, attempts, status)
    except GeneratorExit:
        ratelimit.release(plan.holds)   # 동기 — 취소 안전
        plan.holds = []
        raise
```

---

### CR3-03 | MEDIUM | `reencrypt()` — 모든 테이블을 단일 트랜잭션으로 커밋

**위치**: `app/api/admin.py:539–554`
**확신도**: Likely (대규모 배포에서 발현)

**근거**:

```python
def reencrypt(db: Session) -> dict:                                         # :539
    f = keys.fernet()
    out = {"secrets": 0, "mfa": 0, "logs": 0, "failed": 0}
    for model, col, label in ((Secret, "ciphertext", "secrets"),
                               (User, "mfa_secret", "mfa"),
                               (LogEntry, "raw_enc", "logs")):
        for row in db.scalars(select(model).where(…)).yield_per(500):       # :547
            try:
                setattr(row, col, f.rotate(getattr(row, col).encode()).decode())
                out[label] += 1
            except InvalidToken:
                out["failed"] += 1
    db.commit()                                                              # :553 — 전체를 한 번에
```

`yield_per(500)`으로 500행씩 가져오지만, 변경된 모든 행이 세션 identity map에 누적된 채 마지막에 한 번 `commit()`한다.

- **메모리**: `LogEntry` 테이블에 `raw_enc`가 있는 행이 수십만~수백만 건이면 세션에 dirty 객체가 모두 쌓여 OOM 위험
- **잠금**: PostgreSQL에서 단일 거대 트랜잭션이 많은 행을 잠가 다른 쓰기 요청 차단 — 암호화 키 회전 중 서비스 지연
- **부분 실패**: 커밋 직전 프로세스 종료 시 전부 롤백 — 이미 `f.rotate()`로 연산한 시간 손실

이 경로는 관리자가 수동으로 실행(`POST /api/v1/admin/keys/rotate` → `reencrypt(db)`)하므로 빈도는 매우 낮다. 하지만 대규모 배포에서 한 번 실행 시 영향이 클 수 있다.

**권고 수정**:
테이블별 또는 N행 단위로 중간 커밋하고, 진행 상황을 반환한다:
```python
BATCH = 500
for model, col, label in …:
    batch = 0
    for row in db.scalars(…).yield_per(BATCH):
        …
        batch += 1
        if batch >= BATCH:
            db.commit()
            batch = 0
    if batch:
        db.commit()
```

---

### CR3-04 | LOW | `health_request()`·`_service_of()` — protobuf varint 단일 바이트 가정

**위치**: `app/services/a2a_grpc.py:274–280`
**확신도**: Confirmed (코드 상 명확하나 실제 영향 거의 없음)

**근거**:

```python
def health_request(service: str = "") -> bytes:
    raw = service.encode()
    return b"\x0a" + bytes([len(raw)]) + raw if raw else b""   # :276 — len(raw) ≤ 255 가정

def _service_of(req: bytes) -> str:
    return req[2:2 + req[1]].decode(…) if len(req) >= 2 and req[0] == 0x0A else ""  # :280 — 단일 바이트 길이
```

protobuf varint 인코딩에서 길이가 128 이상이면 2바이트 이상을 쓴다. `health_request`의 `bytes([len(raw)])` 는 255까지 동작하지만 protobuf 규격과 다르고, `_service_of`는 `req[1]`을 단일 바이트 길이로 읽는다.

실제 gRPC 헬스 체크에서 서비스 이름은 `""` 또는 `"readiness"`(9바이트)이므로 문제가 발생하지 않는다. 외부 gRPC 클라이언트가 128자 이상의 서비스 이름으로 `Health/Check`를 호출하면 잘못된 응답을 받지만, 보안 위험은 아니다(결과는 항상 SERVING 또는 NOT_SERVING).

**권고 수정**:
영향이 없으므로 주석으로 가정을 명시하면 충분:
```python
# 주의: service name < 128 bytes 가정 (grpc.health.v1 표준 사용에서는 항상 참)
```

---

## 확인했으나 문제 없음

| 영역 | 경로:행 | 확인 내용 |
|---|---|---|
| CR2-01 수정 확인 | `a2a_gateway.py:601`, `a2a_server.py:119,291` | `push_url_problem` 호출이 `run_in_threadpool` / `asyncio.to_thread`로 감싸져 이벤트 루프 차단 해소됨 |
| `_bounded` per-attempt timeout | `llm.py:265–273` | `asyncio.wait_for` 사용. 스트림 첫 청크 타임아웃 시 `except` 블록에서 `agen.aclose()` 호출하여 제너레이터 정리 완료 |
| 비스트리밍 hold 반환 | `llm.py:70–73` | `except ApiError`에서 `_final(tgt=None)` → `finalize`의 `plan.holds` 분기에서 `release()` 호출 — 정상 |
| `settle` 토큰 창 정산 | `ratelimit.py:381–391` | `tokens > n`이면 차이만 추가, 아니면 유지(보수적). `record_tokens`는 예약 안 된 창만 기록 — 이중 계산 없음 |
| keys.py 워커 간 캐시 무효화 | `keys.py:58–69` | `_generation()`이 매 호출 `stat()` → 파일 mtime 변경 감지 → `lru_cache` 새 항목 생성. `_bump()`이 `cache_clear()` 호출. 동일 워커 즉시, 타 워커 다음 호출 시 반영 |
| MultiFernet 키 순서 | `keys.py:174` | `[Fernet(primary), *older]` — 암호화는 첫(현재) 키, 복호화는 순서대로 시도. 회전 후 이전 키가 뒤에 오므로 정상 |
| `verify_local` kid 검색 | `security.py:29–38` | `kid` 있으면 해당 키만, 없으면 전체 후보. 내장 발급 토큰은 항상 `kid` 포함(`sign` 함수). 회전 유예 중 직전 키도 `verification_keys`에 포함 |
| `_budget_scope` 팀 없는 주체 | `ratelimit.py:300–304` | `scope_type=="any"`이면 팀 있으면 팀, 없으면 사용자로 폴백 — D20-① 의도대로 |
| observe.py day 그룹 SQL | `observe.py:362–363` | PostgreSQL: `func.timezone("UTC", ts)` → `func.date()`, SQLite: `func.date(ts)` — UTC 기준 날짜 추출 양쪽 정상 |
| `_overview_extra` 상관 하위질의 | `observe.py:270–274` | `.correlate(st)` 명시로 2단계 상관 정상. `2**62`는 `bigint` 범위 안. SQLite·PostgreSQL 양쪽 동일 결과 |
| scheduler 구성요소 게이팅 | `scheduler.py:108`, `main.py:118–119` | `scheduler.loop()`은 항상 시작, `tick` 안에서 `settings.enabled(component)`로 작업별 판정 — D21-② 단독 배포 정상 |
| `listen_release` 동기 호출 | `mcp_modern.py:506–507` | 코드 주석에 의도 명시: 취소 중 `await` 재취소 방지. Redis 최대 0.5초 블록이나 TTL(LISTEN_MAX_S+60)이 백업. 문서화된 트레이드오프 |
| `_write_private` TOCTOU | `cli/nexus.py:54–58` | 새 파일: `O_CREAT` + `0o600` 원자 생성. 기존 파일: `O_TRUNC` 후 즉시 `chmod` — 빈 파일 상태에서만 짧은 창, 데이터 노출 없음 |
| `_write_atomic` 원자성 | `keys.py:72–77` | `os.replace`로 원자 교체. 키 회전은 수동 관리 명령이므로 동시 호출 거의 없음 |
| registry search fast path | `registry.py:408–410` | 파이썬 필터 없을 때 SQL `count` + `offset/limit` — 전체 읽기 회피. `order_by(None)`으로 count 최적화 정상 |
