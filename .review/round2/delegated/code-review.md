# 결함 리뷰 2차 (code-review 폴백)
> 대상: /Users/wise/Developer/wise-AgentNexus (FastAPI + SQLAlchemy 2, Python 3.12, 2 uvicorn workers prod)
> 날짜: 2026-10-07
> 방법: 정적 읽기 (static read), oh-my-claudecode:code-reviewer 서브에이전트 폴백
> 비고: 1차 리뷰(.review/delegated/code-review.md) 이후 변경 사항 대상. 1차에서 고쳐진 항목은 재보고하지 않고 상태 테이블로 확인.
>
> 아래는 서브에이전트 결과 원문이다. 검증·처리 결과는 `../VERIFY.md` 에 따로 적는다(원문은 고치지 않음).

## 요약 — 심각도별 건수
- **High**: 1건
- **Medium**: 1건
- **Low**: 2건

---

## 발견 사항

### CR2-01 | HIGH | `push_url_problem()` 이벤트 루프 차단 — 동기 DNS 조회
**위치**: `app/services/a2a_gateway.py:568`, `app/services/a2a_gateway.py:639-644`, `app/api/a2a.py:222`
**확신도**: Confirmed

**근거**:
`_proxy()`(async 함수)에서 `push_url_problem()`을 `run_in_threadpool` 없이 직접 호출한다.

```python
# a2a_gateway.py:568 — async def _proxy() 안
if reason := push_url_problem(client_cfg.get("url")):
    raise A2AError(...)
```

`push_url_problem()` → `internal_url()` → `socket.getaddrinfo()` 경로가 동기 블로킹 DNS 조회를 수행한다.

```python
# a2a_gateway.py:641-644
def push_url_problem(url):
    from app.core.http import internal_url
    ...
    if settings.app_env not in ("local", "test") and internal_url(str(url)):
        ...
```

```python
# http.py:117-118 — socket.getaddrinfo = 동기 블로킹
return any(_internal_ip(info[4][0]) for info in socket.getaddrinfo(host, None))
```

같은 함수가 플랫폼 A2A 서버의 `push_check` 콜백으로도 전달된다(`app/api/a2a.py:222: push_check=push_url_problem`). `A2AServer.dispatch()`(async)가 `self.push_check(cfg.get("url"))` 을 직접 호출하므로(`app/services/a2a_server.py:283`) 같은 문제가 발생한다.

참고: 같은 모듈의 `_guard_async()`는 `await loop.getaddrinfo()`를 올바르게 사용하고, `blocked_url()`은 `run_in_threadpool`으로 호출된다. `internal_url()`만 동기 경로에 남아 있다.

**실패 시나리오**:
1. 운영(prod) 환경에서 사용자가 `tasks/pushNotificationConfig/set`으로 존재하지 않는 도메인(예: `https://nonexistent-domain.example/hook`)을 푸시 URL로 등록
2. `socket.getaddrinfo()`가 최대 30초(시스템 DNS 타임아웃) 동안 블로킹
3. 이벤트 루프가 30초간 정지 → 같은 워커의 모든 HTTP 요청(LLM 호출, MCP 중계, SSE 스트림)이 지연
4. 2-워커 prod에서 한 워커가 멈추면 QPS가 절반으로, 양 워커에 동시 요청되면 전체 서비스 장애

**권고 수정**:
`_proxy()` 안에서 `push_url_problem` 호출을 `run_in_threadpool`으로 감싸고, `A2AServer`에 전달하는 `push_check` 도 async 경로에서 threadpool로 호출하도록 수정. 또는 `internal_url()`을 async로 교체(`loop.getaddrinfo` 사용).
```python
# a2a_gateway.py:568
if reason := await run_in_threadpool(push_url_problem, client_cfg.get("url")):
```
`A2AServer.dispatch`에서는 `push_check`를 async 콜백으로 바꾸거나, `run_in_threadpool`로 감싼다.

---

### CR2-02 | MEDIUM | `_evict()` 가 terminal 작업만 제거 — 장기 실행 작업 누적 시 MAX_TASKS 초과 가능
**위치**: `app/services/a2a_server.py:134-141`, `163-164`
**확신도**: Likely

**근거**:
1차 리뷰(BUG-02)에서 지적된 인메모리 딕셔너리 누수에 대해 `_emit()`에서 terminal 상태 도달 시 `ctxs`/`runners`를 정리하고, `_evict()`에서 `tasks`/`push`/`owners`를 MAX_TASKS(1000) 기준으로 제거하는 수정이 적용되었다. 그러나 `_evict()`는 terminal 상태의 작업만 대상으로 한다:

```python
# a2a_server.py:136
for tid in [t for t, task in self.tasks.items() if task["status"]["state"] in ap.TERMINAL]:
```

`working` 또는 `input-required` 상태의 작업이 1000개를 초과하면 eviction 대상이 없어 딕셔너리가 한도 없이 성장한다. 각 작업은 `task` dict(history + artifacts 포함) + `TaskContext`(`inbox` Queue) + `asyncio.Task`(runner)를 보유한다.

**실패 시나리오**:
1. 플랫폼 A2A 서버에서 오케스트레이터가 장기 실행 작업(LLM 체인, 사람 승인 대기 등)을 처리
2. 동시 활성 작업이 1000개를 초과(대규모 배포에서 가능)
3. `_evict()`가 terminal 작업이 없어 아무것도 제거하지 못함
4. history/artifacts가 큰 작업이 쌓이면 워커 OOM → 재시작(영속 loader가 있어 데이터 손실은 없지만 서비스 중단)

**권고 수정**:
`_evict()`에 시간 기반 정리를 추가 — terminal 작업 우선, 부족하면 생성 시간이 가장 오래된 non-terminal 작업도 대상에 포함(DB loader가 있으면 안전하게 재적재 가능).
```python
if len(self.tasks) > self.MAX_TASKS:
    # terminal 먼저, 부족하면 가장 오래된 non-terminal (loader 가 있으면 재적재 가능)
    candidates = sorted(self.tasks, key=lambda t: (0 if self.tasks[t]["status"]["state"] in TERMINAL else 1, t))
    for tid in candidates:
        if len(self.tasks) <= self.MAX_TASKS:
            break
        for store in (self.tasks, self.push, self.ctxs, self.runners, self.owners):
            store.pop(tid, None)
```

---

### CR2-03 | LOW | Redis `counter_add` 음수 보정의 TOCTOU
**위치**: `app/services/ratelimit.py:130-140`
**확신도**: Possible (타이밍 의존, 재현 어려움)

**근거**:
`INCRBY+EXPIRE` 파이프라인(원자적) 후 음수 확인 → `SET 0`(별도 round trip)이 원자적이지 않다:

```python
# ratelimit.py:133-139
pipe = self._r.pipeline()
pipe.incrby(k, delta)
pipe.expire(k, ttl_s)
val = int(pipe.execute()[0])
if val < 0:
    self._r.set(k, 0, ex=ttl_s)  # ← INCRBY 와 별도 round trip
    return 0
```

**실패 시나리오**:
1. Worker A: `counter_add(-1)` → INCRBY → val = -1 (키가 TTL 만료 후 재생성)
2. Worker B: `counter_add(+1)` → INCRBY → val = 0 (정상 획득)
3. Worker B: `counter_add(+1)` → INCRBY → val = 1 (정상 구독)
4. Worker A: `SET 0` → val = 0 (Worker B의 +1이 지워짐)
5. 카운터가 0인데 실제 활성 구독은 1 → 한도(5)보다 1개 많은 구독 허용 가능

실제 발생 확률은 매우 낮다: 키 TTL 만료 후 release가 오는 경우에만 음수가 되며, race window는 마이크로초 수준.

**권고 수정**: Lua 스크립트로 원자적 처리.
```lua
local val = redis.call('INCRBY', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
if val < 0 then redis.call('SET', KEYS[1], 0, 'EX', ARGV[2]); return 0 end
return val
```

---

### CR2-04 | LOW | `_UPSTREAM_SEEN` 딕셔너리가 `_UPSTREAM_SESSIONS` 와 함께 정리되지 않음
**위치**: `app/services/mcp_gateway.py:327`, `490`, `718`, `799`, `807`
**확신도**: Confirmed

**근거**:
`_UPSTREAM_SESSIONS`을 정리하는 5개 경로(유휴 세션 만료, 업스트림 오류, 설정 시험 복원 등)에서 `_UPSTREAM_SESSIONS.pop(key, None)`만 호출하고 `_UPSTREAM_SEEN.pop(key, None)`은 호출하지 않는다:

```python
# mcp_gateway.py:327 — 유휴 세션 제거
_UPSTREAM_SESSIONS.pop(key, None)   # SEEN 은 그대로

# mcp_gateway.py:490 — 업스트림 세션 만료
_UPSTREAM_SESSIONS.pop((srv.id, p.sub), None)   # SEEN 은 그대로
```

`_UPSTREAM_SEEN`은 max-size eviction(line 330-331, `len >= 4096` 시)과 `clear_caches()`에서만 정리된다. 유휴 세션 TTL(1800초) 만료로 제거된 항목은 `_UPSTREAM_SEEN`에 영구 잔류한다.

**실패 시나리오**: 오랜 운영 후 비활성 (server_id, user_sub) 조합이 `_UPSTREAM_SEEN`에 축적. 항목당 약 100바이트로 1년간 수십만 건 ≈ 수십 MB. 메모리 문제보다 eviction 정렬(`sorted(_UPSTREAM_SEEN, key=...)`)의 성능 저하가 먼저 체감될 수 있다.

**권고 수정**: `_UPSTREAM_SESSIONS.pop(key, None)` 을 호출하는 5개 지점에 `_UPSTREAM_SEEN.pop(key, None)` 추가.

---

## 이전 지적 재확인

| # | 제목 | 상태 | 근거 |
|---|------|------|------|
| BUG-01 | McpRelay 보존 정책 없음 | **Fixed** | `retention.py:33` — `for model in (McpRelay, McpTask):` 추가, `STATE_DAYS` 기준 삭제 |
| BUG-02 | A2AServer 딕셔너리 무한 증가 | **Partial** | `_emit()`에서 terminal 시 ctxs/runners 정리, `_evict()`에서 MAX_TASKS(1000) 기준 제거. 단 non-terminal 작업은 eviction 대상 아님 → CR2-02 |
| BUG-03 | mcp_tasks watch 예외 시 heartbeat 중단 | **Fixed** | `mcp_tasks.py:170-176` — `try/except Exception` + 경고 로그 + 다음 주기 재시도 |
| BUG-04 | McpTask 보존 정책 없음 | **Fixed** | BUG-01과 동일 경로 (`retention.py:33`) |
| BUG-05 | _TOKENS 캐시 무한 증가 | **Fixed** | `mcp_gateway.py:88-93`, `a2a_gateway.py:238-242` — 1024 상한 + 만료 항목 정리 |
| BUG-06 | LISTENS 워커 단위 한도 우회 | **Fixed** | `mcp_modern.py:57-68` — `ratelimit.backend().counter_add()` 사용(prod Valkey 공유 카운터). `LISTENS` dict는 상태 표시용만 |
| BUG-07 | 워커 간 progress 중계 불가 | **Fixed** | `mcp_modern.py:280-315` — `_sink()`가 로컬 큐 없으면 DB 행(`_store_progress`)으로, `_relay_shared()`가 다른 워커의 progress를 폴링 |
| BUG-08 | _UPSTREAM_SESSIONS 캐시 무한 증가 | **Fixed** | `mcp_gateway.py:252-253,324-331` — 유휴 TTL(1800초) + 최대 크기(4096) LRU eviction. 단 `_UPSTREAM_SEEN` 정리 누락 → CR2-04 |
| BUG-09 | _discard 후 고아 McpRelay 잔류 | **Fixed** | `mcp_tasks.py:79` — `db.execute(delete(McpRelay).where(McpRelay.session_id == task_id))` 추가 |
| BUG-10 | watch fire-and-forget 종료 경고 | **Fixed** | `mcp_tasks.py:177-179` — `_WATCHERS` set + `add_done_callback`으로 참조 유지 |
| BUG-11 | _HOSTED 캐시 설정 변경 시 잔류 | **Fixed** | `mcp_gateway.py:511-523` — `_close_hosted()`가 `_HOSTED_CLIENTS` 에서 제거 후 `aclose()` 예약 |
| BUG-12 | A2AServer._push 재시도 없음 | **Fixed** | `a2a_server.py:119` — `post_with_retry()` 사용(3회 지수 백오프) |

---

## 확인했으나 문제 없음

1. **`listen_release` 취소 안전성**: `mcp_modern.py:499-500` — 주석대로 동기 호출. `ratelimit.backend().counter_add()`는 동기 Redis/메모리 호출이므로 anyio 취소 범위에서도 완료된다. 이벤트 루프를 최대 0.5초(Redis socket_timeout) 차단하지만, 해제 보장을 위한 의도적 트레이드오프. `_listen`과 `listen_events` finally 간 이중 해제(double release) 가능성도 추적했으나, 두 경로는 상호 배타적(StreamingResponse 반환 전/후)으로 확인.

2. **`summary_data` p95 OFFSET 쿼리**: `observe.py:214-216` — COUNT와 OFFSET 쿼리 사이 동시 삽입으로 인덱스가 어긋날 수 있으나, 근사치(p95) 특성상 실질 영향 없음. SQLite/PostgreSQL 모두 `ORDER BY … OFFSET … LIMIT 1` 정상 동작.

3. **Refresh token family reuse detection**: `identity.py:77-100` — `REUSE_GRACE_S=10`으로 탭 두 개 동시 갱신을 허용하면서, 10초 후 도착한 재사용은 계열 전체를 폐기. 조건부 UPDATE(`revoked_at.is_(None)`)로 동시 회전 경합도 안전. 마이그레이션 0019는 nullable `family_id` 추가로 기존 토큰 첫 회전 시 계열 연결 성립 — 정상.

4. **scheduler.lease**: 조건부 UPDATE + INSERT(IntegrityError catch)로 SQLite/PG 공통 안전한 리스 획득. TTL 만료 시 다른 워커가 이어받는 구조.

5. **`_SPEND_CACHE` 크기**: `ratelimit.py:221` — 크기 제한 없으나 키가 (scope_type, scope_value, period)로 bounded(활성 사용자 × 4 × 2). 5초 TTL로 비활성 항목은 재요청 시 갱신. 실질 문제 없음.

6. **`post_with_retry` 에서 새 클라이언트 생성**: `http.py:158-170` — 시도마다 `async with async_client()` 로 새 클라이언트를 열고 닫는다. 연결 풀 재사용이 없어 비효율적이지만, 푸시 전달(빈도 낮음)에서만 사용되므로 실질 영향 미미.

7. **a2a_grpc `_channel` 자격 증명**: `a2a_grpc.py:57-61` — TLS 여부에 따라 `secure_channel`/`insecure_channel` 분기, 자격 증명 없으면 시스템 신뢰 저장소 사용. `async with` 로 채널 수명 관리. 정상.

8. **`_FP_CACHE`, `_RESOLVED` 크기 제한**: 둘 다 `if len > 4096: clear()` 패턴으로 무한 성장 방지. 전체 clear는 일시적 캐시 미스를 유발하지만 정확성에 영향 없음.
