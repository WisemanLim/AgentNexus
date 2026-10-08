# 보안 리뷰 (4차, security-review 폴백)

> **대상:** wise-AgentNexus (FastAPI + SQLAlchemy 2, Python 3.12) — MCP/AI/A2A Gateway
> **날짜:** 2026-10-08
> **방법:** 정적 읽기 (소스 코드 분석, 런타임/네트워크 접근 없음). Claude Code `/security-review` 가 이 세션에서 호출 불가하여 `oh-my-claudecode:security-reviewer` 서브에이전트로 폴백.
> **범위:** D22-①~④⑥ 표기가 있는 코드 변경과 그 영향 경로. 선행 보고(`round3/delegated/security-review.md`, `round3/VERIFY.md`)에서 수정 완료 또는 기존 결정으로 판정된 항목(SEC3-01~04 등)은 새 코드가 다시 열지 않는 한 재보고하지 않는다.

---

## 심각도별 건수

| 심각도 | 건수 |
|--------|------|
| Critical | 0 |
| High | 0 |
| Medium | 1 |
| Low | 1 |
| **합계** | **2** |

**전체 위험 수준: LOW** -- Critical/High 없음. D22 변경의 핵심 보안 메커니즘(env 2단계 키 회전, generation 기반 캐시 무효화, flock 회전 잠금, 분 창 슬롯 예약/정산, 주체별 동시 요청 상한, 익명 발견 API 한도, LIKE 이스케이프, 내장 issuer 거부, IdP 세션 종료 하위 질의, artifacts cap, JWKS 강제 갱신 30초 제한, gRPC 프로브 호스트 바인딩, CLI fchmod, OpenAPI 시크릿 정리)은 모두 올바르게 구현되어 있다. 3차 보고(SEC3-01~04)의 수정도 확인했다.

---

## 발견 사항

### SEC4-01 | MEDIUM | 익명 `/v0/servers` IP 기반 한도가 기본 Helm `forwardedAllowIps: "*"` 에서 우회 가능하다

**위치:** `app/api/catalog.py:517`, `deploy/helm/agentnexus/values.yaml:29`, `deploy/helm/agentnexus/templates/api.yaml:47`
**카테고리:** A05 Security Misconfiguration · A04 Insecure Design
**확신도:** Confirmed
**악용 가능성:** 원격, 인증 불필요, 클러스터 내부 또는 ingress 미설정 시 외부에서도 가능
**폭발 반경:** 익명 카탈로그 스크래핑 부하 → 가용성 저하 (기밀성·무결성 영향 없음)

**근거:**

D22-④ 에서 익명 발견 API(`GET /v0/servers`)에 IP 별 요청 한도(5 rps, burst 30)를 추가했다:

```python
# catalog.py:517
ok, wait = ratelimit.backend().take(
    f"rl:v0anon:{request.client.host if request.client else '-'}",
    V0_ANON_RPS, V0_ANON_BURST)
```

Helm 차트의 uvicorn 기동 인자(`api.yaml:47`)는 `--proxy-headers --forwarded-allow-ips {{ .Values.api.forwardedAllowIps }}` 를 쓰고, 기본값은:

```yaml
# values.yaml:29
forwardedAllowIps: "*"
```

`forwardedAllowIps: "*"` 은 모든 네트워크 피어의 `X-Forwarded-For` 헤더를 신뢰한다. 이 기본값 아래에서:

1. 같은 클러스터 안의 다른 파드가 `X-Forwarded-For: <임의 IP>` 를 넣어 요청하면 `request.client.host` 가 그 IP 로 바뀐다.
2. Ingress 컨트롤러 없이 Service 가 직접 노출되면(NodePort·LoadBalancer) 외부 클라이언트도 헤더를 조작할 수 있다.
3. 매 요청마다 다른 IP 를 넣으면 각 IP 가 독립 버킷으로 잡혀 한도가 무력화된다.

`values.yaml:29` 주석은 "운영은 Ingress 컨트롤러 파드 CIDR 로 좁힌다" 라고 경고하지만, 기본 배포는 넓은 상태다. D22-④ 가 이 한도를 보안 조치로 도입한 만큼, 기본 설정에서 무력화되는 점은 보고한다.

**실패 시나리오:**
1. 공격자가 인증 없이 `GET /v0/servers?search=&limit=100` 을 반복 호출한다.
2. 매 요청에 `X-Forwarded-For: <랜덤 IP>` 를 넣는다.
3. 요청마다 새 버킷으로 한도를 우회하고, 전체 카탈로그를 빠르게 읽는다.
4. DB 쿼리(`registry.search`) 부하로 다른 요청의 응답이 느려진다.

**권고:**

```yaml
# BAD — 기본값
api:
  forwardedAllowIps: "*"

# GOOD — 운영에서 Ingress 컨트롤러 파드 CIDR 만 신뢰
api:
  forwardedAllowIps: "10.244.0.0/16"   # 예시: Calico 기본 CIDR
```

또는 코드 쪽에서 방어 심층을 추가한다: 전역 미들웨어에서 익명 요청의 요청 빈도를 세거나, Ingress 계층(nginx `limit_req`)에서 IP 별 한도를 건다.

---

### SEC4-02 | LOW | 푸시 JWKS 강제 갱신 한도가 워커·파드별이라 다중 인스턴스에서 증폭된다

**위치:** `app/services/a2a_gateway.py:836,841,865`
**카테고리:** A10 SSRF (증폭) · A05 Security Misconfiguration
**확신도:** Confirmed
**악용 가능성:** 원격, 인증 필요 (유효한 push token 보유), 등록된 에이전트 JWKS URL 로만 요청
**폭발 반경:** 에이전트 JWKS 엔드포인트에 대한 아웃바운드 요청 증폭 (egress guard 적용, SSRF 는 아님)

**근거:**

D22-④ 에서 모르는 kid 에 대해 JWKS 를 한 번 다시 받되, 30초에 1회로 제한하는 로직이 추가되었다:

```python
# a2a_gateway.py:836
_JWKS_FORCED: dict[str, float] = {}   # 프로세스 로컬 딕셔너리
```

`_JWKS_FORCED` 는 프로세스 메모리에만 존재하여 같은 파드의 다른 워커, 다른 파드와 공유되지 않는다. HA 프로파일(2 워커 × 2+ 파드 = 4+ 프로세스)에서는 같은 JWKS URL 에 대해 30초마다 최대 4+ 회의 강제 갱신이 발생할 수 있다.

공격 전제:
- 유효한 push token 이 필요하다 (Task 생성 시 발급, 암호화 저장).
- JWKS 요청은 등록된 에이전트의 `push_verify.jwks_url` 로만 간다 (egress guard 적용, `sync_client` → `_guard_sync`).
- JWKS URL 은 `SECRET_MANAGE` 권한을 가진 보안 담당자만 설정할 수 있다 (`a2a.py:562`).

영향은 에이전트 JWKS 엔드포인트에 대한 아웃바운드 HTTP 요청 빈도가 워커 수만큼 늘어나는 것이다. 30초에 4회(5초 타임아웃) = 초당 0.13회로, JWKS 엔드포인트(보통 CDN/IdP)에 유의미한 부하가 아니다.

**실패 시나리오:**
1. 공격자가 유효한 push token 을 알고 있다.
2. 존재하지 않는 kid 로 서명한 푸시를 보낸다 (`verify_push_jwt` 경로).
3. 각 워커가 독립적으로 30초 타이머를 적용해 JWKS 를 다시 받는다.
4. 워커 × 파드 수만큼 아웃바운드 요청이 증폭된다.

**권고:**
증폭 계수가 작고(4~6배) 전제 조건이 높아 긴급하지 않으나, 개선한다면 `_JWKS_FORCED` 타임스탬프를 Valkey 로 공유하면 된다:

```python
# BAD — 프로세스 로컬
_JWKS_FORCED: dict[str, float] = {}

# GOOD — Valkey 로 공유 (degraded 시 로컬 폴백)
def _forced_ok(url: str) -> bool:
    key = f"jwks-forced:{hashlib.sha256(url.encode()).hexdigest()[:16]}"
    return ratelimit.backend().counter_add(key, 1, 30) <= 1
```

---

## 3차 보고 수정 확인

| 3차 ID | D22 코드에서 확인 | 판정 |
|--------|-------------------|------|
| SEC3-01 `drop_tokens` 교환 토큰 키 불일치 | `a2a_gateway.py:260` 캐시 키 `("x", p.sub, agent.id)`, `drop_tokens` (278행) `k[0] == "x" and k[2] == agent_id` — 올바르게 매치 | **수정 완료** (C59-13) |
| SEC3-02 `secrets.prev.key` umask 생성 | `keys.py:99-104` `_append_private` 가 `os.open(..., O_CREAT, 0o600)` + `os.fchmod` 사용. `rotate_encryption` (258행)이 호출 | **수정 완료** (C59-14) |
| SEC3-03 백업 서명 검증 issuer 미검증 | `admin.py:302`, `ops.py:859` 모두 `issuer="agentnexus-backup"` 전달 | **수정 완료** (C59-15) |
| SEC3-04 `signing.archive.pem` umask | `rotate_signing` (242행)이 `_append_private` 사용 | **수정 완료** (C59-14) |

---

## 확인했으나 문제 없음

D22 범위의 다음 메커니즘은 코드 경로를 추적하여 올바르게 구현된 것을 확인했다:

| 항목 | 확인 위치 | 판정 |
|------|-----------|------|
| **DB 세션 상한 연결 옵션 (D22-①)** — `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout` 이 psycopg 연결 인자(`-c`)로 주입. 마이그레이션은 `SET LOCAL ... = 0` 으로 상한 해제 | `db.py:49-54`, `migrations/env.py:30-32` | 정상 -- 값은 Pydantic `int` 으로 검증되어 옵션 주입 불가. 마이그레이션이 상한에 걸리지 않음 |
| **인덱스 마이그레이션 (D22-①)** — `ix_spans_parent_trace`, `ix_run_events_type_ts` 생성. `pg_advisory_xact_lock` 으로 동시 마이그레이션 직렬화 | `migrations/versions/0021_span_event_indexes.py`, `migrations/env.py:33` | 정상 -- DDL 잠금 충돌을 advisory lock 으로 방지. `SET LOCAL lock_timeout = 0` 으로 무한 대기 |
| **observe 표본 정렬 (D22-①)** — `order_by(Span.start_time.desc()).limit(500)` 로 최근 500개만 (이전: 순서 없이 `limit(500)`) | `observe.py:270` | 정상 -- p95 가 일관되게 계산됨 |
| **generation 카운터 파일 내용 (D22-②)** — mtime 대신 파일 내용(`int`)으로 세대 판별. 같은 틱의 두 회전도 구분 | `keys.py:66-70` | 정상 -- `_bump` 이 카운터를 증가시키고 `_signing.cache_clear()` 호출 |
| **flock 회전 잠금 (D22-②)** — `fcntl.flock(fd, LOCK_EX)` 로 동시 회전 방지. env 모드(`EnvManaged`)에서는 파일 회전을 거부하므로 잠금 불필요 | `keys.py:80-88` | 정상 -- HA Helm(`existingSecret`)은 env 모드이므로 NFS flock 비호환 문제 해당 없음. 단일 노드(data_dir/keys)에서는 flock 동작 |
| **env NEXT 서명 키 — JWKS·검증만 (D22-②)** — `_signing()` 이 `_public(b)` 로 공개키만 추출. `signing_key()` 는 `[0]`(현재 개인키)만 반환. JWKS(`jwks()`) 에 NEXT 공개키 노출 | `keys.py:138-141,150-166,169-170,199-204` | 정상 -- NEXT 개인키가 있어도 `_signing` 캐시에는 공개키만 저장. `sign()` 은 `signing_key()` 만 사용하므로 NEXT 로 서명 불가 |
| **env NEXT 암호화 키 — 복호화만 (D22-②)** — `_fernet()` 이 NEXT 키를 `older` 목록에 추가. `MultiFernet` 은 첫 키(현재)로만 암호화, 나머지로는 복호화만 | `keys.py:214,218` | 정상 -- NEXT 로 암호화된 데이터는 없음(아직 현재 키가 아니므로). 복호화만 가능 |
| **서명 키 폐기 kid (D22-②)** — `signing_revoked_kids` 설정의 kid 는 모든 곳(이전·보관·NEXT)에서 제외 | `keys.py:154-155,166` | 정상 -- `keep = lambda pub: _kid_of(pub) not in revoked` 로 필터 |
| **SIGNING_KEY_PREVIOUS_UNTIL (D22-②)** — env 이전 서명 키의 토큰 검증 끝 시각(ISO 8601). 지나면 `_active_previous()` 에서 빠짐 (백업·보고서 서명은 보관본으로 계속 검증) | `keys.py:144-146,177-179` | 정상 -- `until > now` 검사. None 이면 무기한(env 운영자 수동 관리) |
| **회전 감사 기록 선행 (D22-②)** — `rotate_keys` 에서 회전 완료 뒤, 재암호화 전에 감사 기록 | `admin.py:601-602` | 정상 -- 재암호화 실패해도 회전 기록은 남음 |
| **Helm rotationNonce (D22-②)** — `existingSecret` 로 키를 교체할 때 `rotationNonce` 값을 바꿔 파드 재기동. api·데모 모두 annotation 에 포함 | `api.yaml:22`, `demo.yaml:23-24`, `ha-values.yaml:6` | 정상 -- `check-rendered.py:121-123` 이 검증 |
| **Helm NEXT 키 시크릿 (D22-②)** — `SIGNING_KEY_PEM_NEXT`, `ENCRYPTION_KEY_NEXT` 는 값이 있을 때만 시크릿에 포함 | `secret.yaml:14-15` | 정상 -- `{{- with ... }}` 블록으로 빈 값 제외 |
| **출력 토큰 예약 분리 (D22-③)** — `check()` 가 `output_tokens` 를 예약에 포함하되, 통과 판정은 입력까지만(`now_used - output_tokens > lim.tokens_per_min`) | `ratelimit.py:386,403-405` | 정상 -- 출력 추정 때문에 혼자 거절되지 않음. 다른 요청에는 보임 |
| **분 창 슬롯 예약/반환 (D22-③)** — 예약한 분 슬롯을 `held` 에 기록, 반환은 그 슬롯에만(`slot` 인자). 다음 분으로 넘어가면 반환하지 않음 | `ratelimit.py:50-53,204,411,451-454,467` | 정상 -- `if slot is not None and slot < now_slot: return 0` 으로 지난 창 반환 차단 |
| **예산 예약 단위 만료 (D22-③, 59th)** — `amount_hold` 가 넣을 때 만료를 정하고(sliding 없음), `amount_release` 로 정산. 워커 사망 시 TTL(300초) 후 자동 해제 | `ratelimit.py:91-104,145-160,372` | 정상 -- Lua(`_LUA_AMOUNT`)도 만료 검사 후 합산. 증감 카운터의 TTL 연장 문제(59th H2) 해결 |
| **Valkey TIME 사용 (D22-③)** — 보유자·예약 만료는 Valkey 서버 시계(`TIME`)로 판정. 워커·파드 시계 차이가 만료에 영향하지 않음 | `ratelimit.py:129-130` | 정상 -- `_LUA_NOW` 가 모든 보유자/예약 Lua 스크립트에 선행 |
| **주체별 동시 요청 상한 (D22-③)** — `model_max_inflight_per_subject` 로 한 주체가 큰 요청을 동시에 많이 열어 예산 예약을 잠그지 않게. `discard` 와 `release_inflight` 가 실패·거절 시 슬롯 반환 | `llm.py:246-251,273-276,280-283` | 정상 -- `holder_acquire` 원자적, `finalize` 의 `release_inflight` 로 정상 반환. 프로세스 사망 시 HOLD_TTL_S(300초) 후 자동 해제 |
| **라우팅 시험 예약 반환 (D22-③)** — `routing_test` 가 `llm.discard(plan)` 으로 예약·인플라이트 반환 | `ops.py:568` | 정상 |
| **미리 보기·샘플링 예약 반환 (D22-③)** — `_preview` 에서 `llm.discard(plan)`, `_generate` 의 `except` 에서 `llm.discard(plan)` | `mcp_consent.py:113,196` | 정상 |
| **budget_buckets 실제 버킷 (D22-③)** — `*`·`per-subject`·`any` 한도도 사용 기록이 있는 실제 버킷별 현황. `any` = 팀 있으면 팀, 없으면 사용자 | `ratelimit.py:350-365`, `gateway_admin.py:496-497` | 정상 -- `gateway:read` 보호 |
| **익명 발견 API SQL 이름 검색 (D22-④)** — `name_contains` 의 LIKE 와일드카드(`%`, `_`, `\`) 글자 그대로 이스케이프 | `registry.py:410-412` | 정상 -- `replace` 순서 올바름(\ 먼저), `escape="\\"` 지정 |
| **소유자 없는 항목 새 버전 선점 방지 (D22-④)** — `v0_publish` 에서 기존 항목 게시자·미러 확인. 승인 권한자가 아니면 거부 | `catalog.py:541-544` | 정상 |
| **JWKS 강제 갱신 30초 제한 (D22-④)** — `_JWKS_FORCED` 에 URL 별 마지막 강제 갱신 시각 기록. 30초 안이면 캐시 사용 | `a2a_gateway.py:841,844` | 정상 -- 위조 kid 로 반복 JWKS 요청 방지. 증폭은 SEC4-02 로 보고 |
| **push_verify issuer 필수 (D22-④)** — JWKS 지정 시 issuer 도 필수. 없으면 400 거부 | `a2a.py:569-570` | 정상 -- D21-① 재확인 |
| **prod push_verify.jwks_url https 전용 (D22-④)** — `app_env == "prod"` 이면 https 만 허용 | `a2a.py:565-568` | 정상 |
| **D21 이전 issuer 없는 push_verify 표시 (D22-④)** — `push_verify_issuer: false` 로 목록에 노출 | `a2a.py:399-400` | 정상 -- 운영자가 이행 필요 항목을 식별 가능 |
| **artifacts 개수·크기 상한 (D22-④)** — `_cap_artifacts` 가 최근 50개, 직렬화 4MiB 안에서만 저장 | `a2a_gateway.py:353-364,470,480` | 정상 -- 원격 에이전트가 artifact-update 를 대량 전송해도 행 크기 증가 방지 |
| **gRPC readiness 캐시 (D22-④)** — `asyncio.Lock` + 2초 TTL 로 인증 없는 readiness 요청이 api `/health`(DB) 를 증폭하지 않게 | `a2a_grpc.py:314-322` | 정상 |
| **Access Token 스코프 교환 정책 준수 (D22-④)** — 대상용 토큰(`audience != API_AUDIENCE`) 스코프가 교환 정책의 `allowed_scopes` 안에만 | `identity.py:513-523` | 정상 -- 교환과 같은 규칙 |
| **OpenAPI 서버 생성 namespace 충돌 (D22-④)** — 같은 namespace 의 기존 MCP 서버·시크릿 확인 후 409 | `mcp.py:637-643` | 정상 |
| **OpenAPI 서버 생성 실패 시크릿 정리 (D22-④)** — 게시·등록 실패 시 이번에 만든 시크릿 삭제 | `mcp.py:689-694` | 정상 |
| **Inspector dry-run 서버 판정 등급 (D22-④)** — 클라이언트 신고 data_class 를 그대로 쓰지 않고 `policy.declared_data_class` 로 판정 | `mcp.py:499-502` | 정상 |
| **gRPC 프로브 호스트 바인딩 (D22-⑥)** — `0.0.0.0`/`*`/`::` 수신이면 루프백(`127.0.0.1`/`::1`)으로 프로브. 특정 주소면 그 주소로 | `grpc_server.py:28` | 정상 |
| **mcp_modern listen 반납 executor (D22-⑥)** — Valkey 반납을 `run_in_executor` 로 넘겨 await 하지 않음. 취소 중 이벤트 루프 차단 방지. 실패 시 TTL 만료로 자동 해제 | `mcp_modern.py:67-80` | 정상 |
| **내장 issuer 거부 (D22-⑥)** — 외부 IdP 생성 시 내장 IdP 와 같은 issuer 면 400 거부. `patch_idp` 에서 issuer 변경 불가(`allowed` 세트에 미포함) | `identity.py:219-220,236-237` | 정상 -- issuer 위조로 내장 토큰과 혼동 불가 |
| **IdP 세션 종료 하위 질의 (D22-⑥)** — `_end_idp_sessions` 가 `User.sub.like(f"ext:{esc}:%", escape="\\")` 로 LIKE 와일드카드 이스케이프. `synchronize_session=False` 로 대량 UPDATE. 호출부(`patch_idp`, `delete_idp`) 모두 직후 커밋 | `identity.py:265-274` | 정상 -- `\` → `\\` → `%` → `\%` → `_` → `\_` 순서 올바름. IdP ID 의 `_` 가 와일드카드로 작동하지 않음 |
| **CLI 디렉터리 0700 · fchmod (D22-⑥)** — `_save` 가 `d.mkdir(mode=0o700)` + `os.chmod(d, 0o700)` 로 기존 디렉터리도 권한 조정. `_write_private` 가 `os.open(..., 0o600)` + `os.fchmod(fd, 0o600)` 으로 TOCTOU 없이 기존 파일 권한도 조정 | `nexus.py:50-58` | 정상 |
| **CLI --restore-config 기본 끔 (D22-⑥)** — 번들 복원 시 운영 데이터를 구성 백업으로 덮어쓰려면 명시 동의(`--restore-config`) 필요 | `nexus.py:734` | 정상 |
| **gRPC varint 인코딩 (D22-⑥)** — `health_request` 가 `_varint(len(raw))` 로 128 바이트 이상 서비스 이름도 처리. `_service_of` 는 최대 5 바이트 varint 까지 디코딩 | `a2a_grpc.py:279-303` | 정상 -- 실제 서비스 이름은 `""`·`"readiness"` 뿐 |
| **Helm readiness failureThreshold 6 (D22-⑥)** — DB 장애조치(수십 초) 동안 a2a-grpc 파드가 함께 NotReady 로 빠지지 않게 여유 확보 | `deploy/helm/agentnexus/templates/a2a-grpc.yaml:51` | 정상 |
| **compose a2a-grpc TLS 예시 (D22-⑥)** — 볼륨 마운트 + 환경 변수로 인증서 경로 주입. 기본값 빈 문자열(TLS 끔) | `deploy/compose/a2a-grpc-tls.example.yml` | 정상 |
| **check-rendered.py existingSecret 검증 (D22-②)** — `existingSecret` 사용 시 `rotation-nonce` annotation 존재 확인. 내장 Secret 사용 시 `checksum/secret` annotation 확인 | `deploy/helm/check-rendered.py:118-123` | 정상 |
| **.gitignore** — `*.pem`, `*.key`, `secrets.*`, `.secrets/`, `.env` (`.env.local` 제외) 비커밋. `.env.prod` 도 비커밋 | `.gitignore` | 정상 |

---

## 보안 체크리스트

- [x] 하드코딩된 시크릿 없음 — `grep -rn "api[_-]?key\|password\|secret\|token" app/ .env.example` 로 확인. `.env.example` 은 placeholder(`CHANGE_ME`)만 포함
- [x] 모든 입력 검증됨 — Pydantic 모델, `int` 설정, LIKE 이스케이프, regex `Field(pattern=...)`, 크기 상한
- [x] 인젝션 방지 확인 — SQLAlchemy 파라미터화 쿼리, LIKE `escape` 지정, 명령 실행 없음
- [x] 인증/인가 확인 — `require()` 의존성, `_is_admin` 이중 검사(역할 + 스코프), push_verify issuer 필수
- [x] 의존성 감사 — 이번 라운드에 의존성 변경 없음 (D22 는 기능·설정 변경)
