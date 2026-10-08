# 결함 리뷰 (code-review 폴백)
> 대상: /Users/wise/Developer/wise-AgentNexus (FastAPI + SQLAlchemy 2, 2 uvicorn workers prod)
> 날짜: 2026-10-07
> 도구: Claude Code `/code-review` 가 이 세션에서 호출 불가 → `oh-my-claudecode:code-reviewer` 서브에이전트로 폴백 (wds-review §1). 아래는 그 결과 원문.

## 요약 — 심각도별 건수
- **High**: 3건
- **Medium**: 6건
- **Low**: 3건

---

## 발견 사항

### BUG-01 | High | mcp_relays 테이블 보존 정책 없음 — 무한 증가
**위치**: `app/services/retention.py` (전체), `app/services/mcp_consent.py:297-305`, `app/services/mcp_modern.py:212-240`
**근거**:
```python
# retention.py — 정리 대상에 McpRelay 없음
for key, model, col in (("spans_days", Span, Span.start_time),
                        ("logs_days", LogEntry, LogEntry.ts),
                        ("usage_days", UsageRecord, UsageRecord.ts),
                        ("run_events_days", RunEvent, RunEvent.ts)):
```
`mcp_consent.Relay.request()`(line 299)와 `mcp_modern._answer()`(line 229)가 행을 생성하지만, retention.py 에 McpRelay 정리 로직이 전혀 없다. `_store_final`도 행을 추가만 한다.
**실패 시나리오**: 운영 서버에서 MCP 서버가 sampling/elicitation 요청을 보내거나, MRTR 재시도가 반복되면 mcp_relays 행이 쌓인다. 30일 후 수십만 행이 누적되어 DB 용량을 잡아먹고, session_id 인덱스 검색 성능이 저하된다.
**권고 수정**: retention.py 의 `run()` 함수에 McpRelay 정리를 추가. 예: `("relays_days", McpRelay, McpRelay.created_at)` — 기본 7일.
**확신도**: Confirmed

### BUG-02 | High | A2AServer 인메모리 딕셔너리 무한 증가 — 메모리 누수
**위치**: `app/services/a2a_server.py:81-85`, `132`, `134`, `147`
**근거**:
```python
# a2a_server.py:81-85 — 항목은 추가만, 제거 없음
self.tasks: dict[str, dict] = {}      # _start()에서 추가(line 132)
self.ctxs: dict[str, TaskContext] = {} # _start()에서 추가(line 134)
self.runners: dict[str, asyncio.Task] = {} # _start()에서 추가(line 147)
self.push: dict[str, dict] = {}       # push_set에서 추가(line 247)
```
`self.tasks`, `self.ctxs`, `self.runners`, `self.push` 에 대한 `del`/`pop`/`clear` 호출이 전체 파일에 없다.
**실패 시나리오**: 플랫폼 A2A 서버(`_platform_server`)가 수백 건의 task를 처리하면, 완료된 task의 전체 history/artifacts/TaskContext 객체가 메모리에 남아 OOM으로 워커가 재시작된다. `_SERVERS` 캐시로 인해 A2AServer 인스턴스는 프로세스 수명만큼 유지된다.
**권고 수정**: task가 terminal 상태에 도달하면(`_emit` 안에서 `ev.get("final")` 확인) `self.ctxs.pop(tid, None)`, `self.runners.pop(tid, None)` 정리. tasks 딕셔너리는 LRU 캐시(최대 500개)로 교체하거나, 완료 후 일정 시간 뒤 제거.
**확신도**: Confirmed

### BUG-03 | High | mcp_tasks watch() 예외 시 heartbeat 중단 → 거짓 "failed" 상태
**위치**: `app/services/mcp_tasks.py:164-169`
**근거**:
```python
async def watch():
    while not job.done():
        await asyncio.sleep(HEARTBEAT_S)
        if not job.done() and await run_in_threadpool(_beat, task_id):
            job.cancel()
asyncio.create_task(watch())  # fire-and-forget, 예외 처리 없음
```
`_beat()`는 DB 접근(`SessionLocal()`, `db.get()`, `db.commit()`)을 한다. DB 연결 오류·풀 소진·일시적 잠금 등으로 예외가 발생하면 `watch()` 코루틴이 즉시 종료된다.
**실패 시나리오**: 장기 도구 호출(30초 이상) 도중 DB 풀이 일시적으로 부족하면 `_beat` 예외 → watch 종료 → heartbeat_at 갱신 중단. STALE_S(30초) 후 `_view()`가 `row.heartbeat_at < utcnow() - 30s` 조건에 걸려 status="failed"로 보고하지만, 실제 작업(job)은 정상 실행 중이다. 클라이언트는 실패로 판단해 재시도하고, 같은 도구가 중복 실행된다.
**권고 수정**: watch 루프 안에 `try/except Exception: log.warning(...)` 추가하여 일시적 오류 시 다음 주기에 재시도.
```python
async def watch():
    while not job.done():
        await asyncio.sleep(HEARTBEAT_S)
        try:
            if not job.done() and await run_in_threadpool(_beat, task_id):
                job.cancel()
        except Exception:
            log.warning("heartbeat failed for %s", task_id, exc_info=True)
```
**확신도**: Confirmed

### BUG-04 | Medium | McpTask 테이블 주기적 정리 없음 — 완료된 행 무한 잔류
**위치**: `app/services/mcp_tasks.py` (전체), `app/services/retention.py` (전체)
**근거**: McpTask 행은 인라인 완료 시(`_discard`, line 186) 삭제되지만, 장기 실행 후 completed/failed 된 행은 쿼리 시 `_expired()`(line 91-92)로만 만료 확인된다. ttl_ms(기본 1시간) 이후에도 아무도 해당 task를 조회하지 않으면 행이 영구 잔류한다. retention.py 에도 McpTask 정리 없음.
**실패 시나리오**: MCP Tasks 확장을 사용하는 클라이언트가 장기 도구 호출 후 결과만 받고 다시 조회하지 않으면 행이 쌓인다.
**권고 수정**: retention.py 또는 scheduler.py 에 `McpTask` 정리 추가 (created_at + ttl_ms 이후 삭제).
**확신도**: Confirmed

### BUG-05 | Medium | _TOKENS 캐시 딕셔너리 무한 증가
**위치**: `app/services/mcp_gateway.py:86`, `app/services/a2a_gateway.py:232`
**근거**:
```python
# mcp_gateway.py:86
_TOKENS: dict[tuple, tuple[float, str]] = {}
# key = (p.sub, aud, tuple(sorted(scopes or [])))
```
만료된 토큰 항목은 같은 키로 재요청될 때만 교체된다. 한 번 요청하고 다시 오지 않는 (사용자, 서버) 조합의 항목은 영구 잔류.
**실패 시나리오**: 다수 사용자(SSO 통합)가 각각 다른 MCP 서버를 호출하면, 독립적인 키가 축적되어 워커 메모리 사용량이 점진적으로 증가.
**권고 수정**: `cachetools.TTLCache` 등으로 교체하거나, 주기적으로 만료 항목 정리.
**확신도**: Confirmed

### BUG-06 | Medium | LISTENS 동시 구독 한도가 워커 단위 — 2워커에서 2배 우회
**위치**: `app/services/mcp_modern.py:49-50`, `app/api/mcp.py:233`
**근거**:
```python
# mcp_modern.py:49-50
MAX_LISTENS = 5
LISTENS: dict[str, int] = {}  # 워커 메모리

# mcp.py:233
if mcp_modern.LISTENS.get(p.sub, 0) >= mcp_modern.MAX_LISTENS:
    raise RpcError(...)
```
`LISTENS`는 프로세스(워커) 로컬 딕셔너리다. prod 환경의 2 uvicorn 워커에서 같은 사용자가 각 워커에 5개씩, 총 10개 동시 구독을 열 수 있다.
**실패 시나리오**: 악의적(또는 버그 있는) 클라이언트가 의도된 한도(5)의 2배 구독을 열어 서버 리소스를 과도하게 소비.
**권고 수정**: DB 또는 Redis 기반 카운터로 교체. 또는 현재 동시 구독 수를 DB(mcp_sessions 또는 별도 테이블)로 추적.
**확신도**: Confirmed

### BUG-07 | Medium | 워커 간 progress 중계 불가 — 알림 무음 손실
**위치**: `app/services/mcp_modern.py:54`, `app/services/mcp_modern.py:279-282`, `app/api/mcp.py:302-303`
**근거**:
```python
# mcp_modern.py:54
_PROGRESS: dict[str, asyncio.Queue] = {}  # 워커 로컬
```
MRTR resume 요청이 start 를 실행한 워커와 다른 워커로 도달하면, `_PROGRESS.get(state_id)`가 None을 반환해 progress 알림이 큐에 전달되지 않는다. 최종 결과는 DB(`_store_final`)를 통해 정상 전달되지만, 중간 progress notification이 클라이언트에 전혀 도달하지 않는다.
**실패 시나리오**: 2-워커 prod에서 클라이언트가 progressToken 포함 MRTR 요청을 보낸 후, resume가 다른 워커로 라우팅되면 진행률 표시가 0%에서 멈추다가 갑자기 완료로 점프.
**권고 수정**: progress 이벤트를 DB 또는 Redis pub/sub로 전달. 또는 sticky session(워커 고정)으로 같은 state_id 요청을 같은 워커로 라우팅.
**확신도**: Confirmed

### BUG-08 | Medium | _UPSTREAM_SESSIONS 캐시 무한 증가
**위치**: `app/services/mcp_gateway.py:230`
**근거**:
```python
_UPSTREAM_SESSIONS: dict[tuple[str, str], tuple[str | None, str]] = {}
# key = (server_id, principal_sub)
```
레거시 업스트림 세션 캐시. `clear_caches()` 는 서버 설정 변경 시만 호출되고, 만료·LRU 정리가 없다.
**실패 시나리오**: 다수 사용자가 다수 MCP 서버를 사용하면 (서버 수 × 사용자 수) 항목이 축적.
**권고 수정**: TTLCache 로 교체(300초 TTL).
**확신도**: Confirmed

### BUG-09 | Medium | mcp_tasks._discard 후 고아 McpRelay 행 잔류
**위치**: `app/services/mcp_tasks.py:67-73`, `app/services/mcp_tasks.py:186`
**근거**:
```python
def _discard(task_id: str) -> None:
    with SessionLocal() as db:
        row = db.get(McpTask, task_id)
        if row is not None:
            db.delete(row)  # McpTask 행만 삭제
            db.commit()
```
인라인 완료된 task를 `_discard`하면 McpTask 행은 삭제되지만, 같은 `session_id=task_id`로 연결된 McpRelay 행은 남는다(cascade 미설정). 도구 실행 중 sampling/elicitation 요청이 있었다면 relay 행이 고아가 된다.
**실패 시나리오**: BUG-01과 결합 — 고아 relay 행이 retention 없이 영구 잔류.
**권고 수정**: `_discard`에서 `db.query(McpRelay).filter(McpRelay.session_id == task_id).delete()` 추가. 또는 모델에 cascade delete 설정.
**확신도**: Confirmed

### BUG-10 | Low | mcp_tasks watch() fire-and-forget — 종료 시 경고
**위치**: `app/services/mcp_tasks.py:169`
**근거**: `asyncio.create_task(watch())`의 반환 값을 저장하지 않는다. 프로세스 종료(graceful shutdown) 시 아직 실행 중인 watch task가 "Task was destroyed but it is pending" 경고를 발생시킨다.
**실패 시나리오**: 배포 시 워커 재시작마다 진행 중인 task 수만큼 경고 로그 발생. 기능상 문제는 없지만 로그 노이즈.
**권고 수정**: watch task 참조를 _JOBS 와 함께 저장하고, 종료 시 cancel.
**확신도**: Confirmed

### BUG-11 | Low | _HOSTED 캐시 키에 서버 설정 해시가 포함되어 설정 변경 시 이전 항목 잔류
**위치**: `app/services/mcp_gateway.py:496`
**근거**:
```python
cache_key = f"{srv.id}:{json.dumps(srv.server_json.get('_meta', {}).get('hosted', {}), sort_keys=True)}"
if cache_key in _HOSTED:
    return _HOSTED[cache_key]
```
설정이 바뀌면 새 cache_key로 새 FastMCP 인스턴스가 생성되지만, 이전 키의 인스턴스(httpx2.AsyncClient 포함)는 `_HOSTED` 딕셔너리에 남아 닫히지 않는다.
**실패 시나리오**: hosted-openapi 서버 설정을 여러 번 변경하면, 이전 httpx2.AsyncClient 인스턴스들이 메모리에 잔류하고 연결 풀이 열린 채 남을 수 있다. `clear_caches()`가 `_HOSTED.clear()`를 호출하지만 클라이언트 close()는 하지 않는다.
**권고 수정**: `clear_caches()`에서 기존 _HOSTED 값의 client 를 close 하거나, cache_key 변경 감지 시 이전 항목 제거.
**확신도**: Plausible

### BUG-12 | Low | A2AServer._push fire-and-forget — 실패 시 재시도 없음
**위치**: `app/services/a2a_server.py:96-104`
**근거**:
```python
asyncio.create_task(self._push(cfg, task))
# _push 안에서:
with contextlib.suppress(Exception):
    async with async_client(timeout=5) as http:
        await http.post(cfg["url"], ...)
```
푸시 알림 실패가 완전히 무시된다. Task 상태 변경 알림이 유실되어도 클라이언트가 알 방법이 없다.
**실패 시나리오**: 클라이언트 웹훅이 일시적으로 불안정하면 task 완료 알림을 받지 못해, 클라이언트가 영원히 polling을 계속하거나 결과를 놓친다.
**권고 수정**: 실패 시 지수 백오프 재시도 (최대 3회) 추가.
**확신도**: Plausible

---

## 확인했으나 문제 없음

1. **`_upstream_version` 반환값 분해**: 3-tuple을 반환하며, 두 호출 지점(a2a_gateway.py:549, 799) 모두 `up_v, url, binding = _upstream_version(...)` 으로 정확히 분해한다. 문제 없음.

2. **`slo._bad_col` threshold_ms 유효성**: API 계층(observe.py:432-434)에서 `threshold_ms not in slo_svc.BOUNDS` 검증이 있어, DB에 BOUNDS 밖의 값이 들어갈 수 없다. 방어적이지는 않지만 현재 코드 경로상 안전.

3. **`slo.observe()` 타임존**: `datetime.now(timezone.utc)` 사용, `utcnow()`도 동일하게 aware datetime 반환. 일관성 있음.

4. **`startswith(_MODEL_PREFIXES)` 튜플**: Python `str.startswith`는 튜플을 지원하며 정상 동작 확인.

5. **scheduler lease 경합**: `lease()` 함수가 조건부 UPDATE + INSERT 패턴으로 다중 워커 간 안전한 리스 획득을 구현. SQLite/PG 모두 정상.

6. **`_modern_progress_stream` waiter 정리**: `events()` 제너레이터의 finally 블록에서 waiter를 cancel하고, `work()` 코루틴의 finally에서 `_RUNNING`/`_PROGRESS`를 정리. 클라이언트 단절 시에도 work()가 자체 정리.

7. **SliMinute 보존 정책**: `slo.flush()` 안에서 `RETAIN_DAYS=95` 기준으로 오래된 행을 삭제한다(line 79). 정상.
