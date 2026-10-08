# Round-4 정확성/결함 리뷰 — wise-AgentNexus (D22-* 변경분)

**날짜**: 2026-10-08
**방법**: 수동 코드 읽기 + 호출 경로 추적 (D22- 마커 범위)
**참고**: Claude Code `/code-review` 폴백 — `oh-my-claudecode:code-reviewer` 에이전트 실행

---

## 심각도 요약

| 심각도 | 건수 |
|--------|------|
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 3 |
| LOW | 3 |
| **합계** | **7** |

---

## 발견 사항

### CR4-01 | HIGH | 비스트리밍·샘플링 경로에서 CancelledError 발생 시 inflight 슬롯·한도 예약 누수

**위치**:
- `app/api/llm.py:72-76` (비스트리밍 chat)
- `app/services/llm.py:366-387` (`embed_texts`)
- `app/services/mcp_consent.py:191-198` (`_generate`)

**확신도**: 높음

**근거**: 스트리밍 경로(`_sse`)는 `finally` 블록으로 취소(클라이언트 연결 끊김, `GeneratorExit`) 시에도 `_final`→`finalize`→`release_inflight`를 호출한다(`app/api/llm.py:128-135`). 그러나 비스트리밍 경로는 `except ApiError`만 잡는다:

```python
# app/api/llm.py:72-76
try:
    res, tgt, attempts = await llm.execute(plan, body)
except ApiError as exc:
    await run_in_threadpool(_final, ...)
    raise
```

`llm.execute` 안의 `asyncio.wait_for`(`_bounded`)나 어댑터 호출 중 태스크가 취소되면 `asyncio.CancelledError`(BaseException)가 `ApiError` except를 건너뛴다. `plan.inflight` 슬롯과 `plan.holds`의 토큰·예산 예약이 TTL(300초) 만료까지 반환되지 않는다.

`mcp_consent.py:_generate`도 동일:
```python
except ApiError as exc:
    if plan is not None:
        llm.discard(plan)   # CancelledError 는 여기 오지 않음
```

`embed_texts`도 동일 — `except ApiError`만 처리, 비 ApiError 예외 시 `_fin` 미호출.

**실패 시나리오**: (1) 클라이언트가 비스트리밍 요청 후 응답 전 연결을 끊는다 → `CancelledError` → inflight 슬롯이 300초간 점유됨. (2) `model_max_inflight_per_subject=32`이고 같은 주체가 반복하면 32개가 다 차서 429 상태가 된다. 스트리밍은 `finally`가 있어 괜찮으나 비스트리밍·임베딩·샘플링은 빈 틈이 있다.

**권고**: 비스트리밍 경로에 `try/finally` 추가:
```python
try:
    res, tgt, attempts = await llm.execute(plan, body)
except ApiError as exc:
    await run_in_threadpool(_final, ctx, plan, None, None, t0, ...)
    raise
except BaseException:
    llm.discard(plan)
    raise
```
`embed_texts`와 `_generate`에도 동일하게 적용.

---

### CR4-02 | MEDIUM | `encryption_key_count()`가 "next" 키를 포함해 2단계 회전 중 카운트 부풀림

**위치**: `app/core/keys.py:225`

**확신도**: 높음 (로직 재현 확인)

**근거**:
```python
env = f"{_configured(settings.encryption_key_previous)},{_configured(settings.encryption_key_next)}"
return 1 + files + len([k for k in env.split(",") if k.strip()])
```

`encryption_key_next`는 다음 회전을 위해 **미리 배포**하는 복호화 전용 키로, 아직 어떤 데이터도 암호화하지 않았다. 이 키가 설정되면 `encryption_key_count()`가 2 이상이 되어 관리자 콘솔이 "재암호화 필요"로 해석할 수 있다.

검증:
```
encryption_key_previous = ""
encryption_key_next = "some-staged-key"
→ encryption_key_count() = 2  (기대값: 1)
```

**실패 시나리오**: 2단계 env 회전의 1단계(next 키 배포)에서 관리자가 아직 회전하지 않았는데도 `keys` 카운트가 2로 보여 불필요한 재암호화를 실행하거나 혼란.

**권고**: `encryption_key_count`에서 "next" 키를 제외하거나, `key_status` 응답에서 `previous_count`와 `next_count`를 분리해 반환.

---

### CR4-03 | MEDIUM | OpenAPI 서버 생성 실패 시 `TokenExchangePolicy` 고아 행 잔류

**위치**: `app/api/mcp.py:649-657` (정책 생성), `686-695` (실패 정리)

**확신도**: 높음

**근거**: `identity == "obo"`일 때 `_pol()`이 `TokenExchangePolicy`를 별도 세션으로 커밋한다(line 653-656). 이후 `_do()`(게시+등록)가 실패하면:
```python
except Exception:
    if hosted.get("secret_ref"):    # 시크릿만 정리
        ...
    raise
```
`hosted.get("secret_ref")`는 `identity == "obo"`일 때 False이므로 시크릿 정리 블록에 진입하지 않고, `TokenExchangePolicy` 행도 삭제되지 않는다.

**실패 시나리오**: OpenAPI 서버 생성 반복 실패 → `api:{namespace}` audience의 `TokenExchangePolicy`가 고아로 쌓인다. 같은 namespace로 재시도 시 `_pol()` 안의 `if pol is None` 조건에 의해 중복 생성은 안 되지만, 다른 namespace라면 누적.

**권고**: `_do()` 실패 시 정리 블록에 정책 삭제 추가:
```python
except Exception:
    if hosted.get("secret_ref"):
        ...  # 기존 시크릿 정리
    if body.identity == "obo":
        def _drop_pol():
            with SessionLocal() as db:
                db.query(TokenExchangePolicy).filter(TokenExchangePolicy.audience == f"api:{body.namespace}").delete()
                db.commit()
        await run_in_threadpool(_drop_pol)
    raise
```

---

### CR4-04 | MEDIUM | 현재 서명 키 kid가 `signing_revoked_kids`에 포함돼도 JWKS·검증에서 제외되지 않음

**위치**: `app/core/keys.py:166,186-187,201`

**확신도**: 높음

**근거**: `_signing()`에서 `revoked` 필터는 `previous`, `archive`, `nxt`에만 적용된다:
```python
return (current,                                          # ← revoked 미확인
        [(p, u) for p, u in previous if keep(p)],         # ← revoked 확인
        [p for p in archive if keep(p)],                  # ← revoked 확인
        [p for p in nxt if keep(p)])                      # ← revoked 확인
```

`verification_keys()`에서도 `current.public_key()`는 `revoked` 확인 없이 후보에 포함:
```python
cands = [current.public_key(), *_active_previous(), *nxt, ...]
```

`jwks()`도 동일 — `signing_key().public_key()`가 무조건 포함.

**실패 시나리오**: 서명 키 유출 감지 후 긴급 폐기(`signing_revoked_kids`에 kid 추가) → 그 kid로 서명된 토큰이 여전히 검증 통과. 회전(`rotate_signing`)이 먼저 되어야 폐기 효과가 발생한다. 문서("폐기 kid — 토큰·백업·보고서 **어디서도** 검증하지 않음")와 동작이 불일치.

**권고**: 문서를 "현재 키를 폐기하려면 먼저 회전하세요"로 수정하거나, `verification_keys`에서 현재 키도 revoked 필터 적용 후 빈 경우 에러 로그 + 서명 거부.

---

### CR4-05 | LOW | `list_users` 검색 쿼리의 LIKE 와일드카드 미이스케이프

**위치**: `app/api/identity.py:143-144`

**확신도**: 높음

**근거**:
```python
like = f"%{q}%"
stmt = stmt.where(or_(User.username.like(like), User.email.like(like), User.name.like(like)))
```

`q`에 `%`, `_` 등 SQL LIKE 와일드카드가 그대로 전달된다. 같은 코드베이스의 `app/services/registry.py:411`과 `app/api/identity.py:267`은 이스케이프 처리가 되어 있어 일관성 부족.

**실패 시나리오**: 인증된 관리자가 `q=___`로 검색하면 사용자 이름 3자 이상 전부 매칭 — 의도보다 넓은 결과. 보안 영향은 미미(인증된 `identity:read` 권한 필요).

**권고**: `registry.py:411`과 동일하게 이스케이프:
```python
esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
like = f"%{esc}%"
stmt = stmt.where(..., escape="\\")
```

---

### CR4-06 | LOW | Lua 스크립트 `redis.call('TIME')` — Redis < 7.0 스크립트 복제와 비호환

**위치**: `app/services/ratelimit.py:130`

**확신도**: 중간

**근거**: `_LUA_NOW`가 `redis.call('TIME')`으로 서버 시각을 읽은 후 쓰기 명령(`ZADD`, `HSET`, `ZREMRANGEBYSCORE`)을 실행한다. Redis < 7.0의 스크립트 복제(script replication) 모드에서는 비결정적 명령 후 쓰기가 복제 오류를 일으킨다. Valkey(대상 백엔드)는 effects-based 복제가 기본이라 문제없으나, Redis 6.x 복제 세트에서 사용 시 `READONLY` 오류 발생 가능.

**권고**: 문서에 "Valkey 또는 Redis 7.0+ 필요"를 명시하거나, `TIME`을 ARGV로 전달(서버 시각 대신 클라이언트 시각, 다만 파드 시계 차이 우려 있음).

---

### CR4-07 | LOW | CLI `_save` 디렉터리 chmod가 fd가 아닌 경로로 수행 (TOCTOU)

**위치**: `app/cli/nexus.py:50-51`

**확신도**: 중간

**근거**:
```python
d.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(d, 0o700)  # 경로 기반 — 사이에 심링크 교체 가능
```

`_write_private`(line 57-58)은 `os.fchmod(fd, 0o600)`으로 fd 기반 chmod를 올바르게 사용한다. 디렉터리에 대해서는 fd 기반 방식이 적용되지 않았다. CLI 도구(사용자 로컬 실행)이므로 실질 위험은 낮음.

**권고**: `os.open(d, os.O_RDONLY | os.O_DIRECTORY)` 후 `os.fchmod`를 사용하거나, 현 수준 유지(CLI 로컬 전용이므로 수용 가능).

---

## 확인했으나 문제 없음

| 영역 | 근거 |
|------|------|
| **마이그레이션 0021 ↔ `__table_args__` 일치** | `ix_spans_parent_trace(parent_id, trace_id)`와 `ix_run_events_type_ts(type, ts)` — 마이그레이션과 모델이 같은 이름·컬럼. autogenerate drift 없음. |
| **`SET LOCAL` in `migrations/env.py`** | `SET LOCAL statement_timeout=0` / `lock_timeout=0`은 트랜잭션 범위이므로 마이그레이션 후 앱 세션 상한이 복원된다. 커넥션 풀 옵션(`options` 문자열)은 연결 수준이므로 서로 간섭하지 않는다. |
| **`_signing` 4-tuple 형태와 모든 호출자** | `signing_key()[0]`, `_active_previous()[1]`, `verification_keys()[0,1,3]`, `jwks()[0,1,3]` — 모두 4-tuple 정확히 처리. |
| **스트리밍 `finalize`/`release_inflight` 모든 경로** | `_sse`의 `finally` 블록(line 128-135)이 `GeneratorExit`·취소 시에도 fire-and-forget으로 `_final`을 호출해 inflight 반환. TTL 300초 백업. |
| **`verify_push_jwt` 블로킹 호출** | `receive_push`(a2a_gateway.py:907)에서 `await run_in_threadpool(verify_push_jwt, ...)`로 정확히 래핑. 이벤트 루프 차단 없음. |
| **`_end_idp_sessions` LIKE 이스케이프** | `idp.id`의 `\`, `%`, `_`를 이스케이프하고 `escape="\\"`를 지정(identity.py:267-268). SQLite·PG 모두 정확. |
| **`_end_idp_sessions` `synchronize_session=False`** | `patch_idp`/`delete_idp`에서 User·RefreshToken 객체가 세션에 로드되지 않은 상태이므로 동기화 불필요. 커밋은 정상 수행. |
| **`window_add` slot 처리** | MemoryBackend·RedisBackend 모두: `slot < now_slot`이면 0 반환(지난 창 보호), 그 외 `now_slot` 사용. `settle`의 반환·초과 로직과 정합. |
| **JWKS 강제 갱신 속도 제한** | `_JWKS_FORCED[url]` = 마지막 강제 갱신 시각. 30초 이내 재갱신 차단. 위조 kid로 JWKS 엔드포인트 폭주 방지. |
| **`_bump()` flock + NFS** | `_rotation_lock()`의 `fcntl.flock`은 NFS에서 불안정하나, HA 환경은 env-managed(`raise EnvManaged`)이므로 파일 회전 경로 미사용. |
| **`reencrypt` 배치 vs `statement_timeout`** | 500행씩 배치·커밋하므로 개별 문장은 120초 내 완료. PK 순서 cursor 방식으로 잠금 범위도 한 배치. |
| **gRPC probe IPv6** | `"::" → "::1"`, `"0.0.0.0" → "127.0.0.1"` 매핑 정확. 채널 주소 `[host]:port` 형식 IPv6 준수(grpc_server.py:35). |
| **Helm `secret.yaml` next 키** | `{{- with .Values.secrets.signingKeyPemNext }}`로 비어 있으면 생략. 2단계 회전 1단계 배포 의도대로. |
| **`budget_buckets` SQL** | `func.coalesce(UsageRecord.team, "") == ""` 조건으로 `any` 스코프의 팀 없는 사용자를 정확히 구분. SQLite·PG 모두 정상. |
| **`observe.py` 최근 500건 정렬 표본** | `.order_by(Span.start_time.desc()).limit(500)` — p95 안정성을 위한 최근 편향 표본. `start_time` 인덱스 활용. |
| **`check-rendered.py` Helm 검증** | 워크로드 셀렉터·포트·ConfigMap/Secret 참조·이미지 태그·보안 컨텍스트 검사 — CI 정합성 게이트로 적절. |
| **`listen_release` via executor** | fire-and-forget `run_in_executor`로 취소 중 await 없이 Valkey 호출. 메모리 백엔드는 즉시. 이벤트 루프 차단 없음. |
| **Access Token scope rule** | `body.audience != security.API_AUDIENCE`일 때 교환 정책의 `allowed_scopes` 확인(identity.py:513-523). 교환과 같은 규칙 적용. |
