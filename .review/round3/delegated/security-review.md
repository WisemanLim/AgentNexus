# 보안 리뷰 (3차, security-review 폴백)

> **대상:** wise-AgentNexus (FastAPI + SQLAlchemy 2, Python 3.12) — MCP/AI/A2A Gateway
> **날짜:** 2026-10-08
> **방법:** 정적 읽기 (소스 코드 분석, 런타임/네트워크 접근 없음). Claude Code `/security-review` 가 이 세션에서 호출 불가하여 `oh-my-claudecode:security-reviewer` 서브에이전트로 폴백.
> **범위:** D20-①~④, D21-①~③⑤ 표기가 있는 코드 변경과 그 영향 경로. 선행 보고(`round2/delegated/security-review.md`, `round2/VERIFY.md`)에서 수정 완료 또는 기존 결정으로 판정된 항목(SEC2-01~08, CR2-01~04 등)은 새 코드가 다시 열지 않는 한 재보고하지 않는다.

---

## 심각도별 건수

| 심각도 | 건수 |
|--------|------|
| Critical | 0 |
| High | 0 |
| Medium | 2 |
| Low | 2 |
| **합계** | **4** |

**전체 위험 수준: LOW** -- Critical/High 없음. D20-D21 변경의 핵심 보안 메커니즘(키 회전, push JWT 검증, 예산 예약, TOTP 1회용, admin grant 제한, 카드 URL 고정, mTLS required+verify:false 거부, gRPC 최소 env, Helm 시크릿 체크섬, 파일 0600)은 모두 올바르게 구현되어 있다. 발견한 2건의 MEDIUM 은 제한된 시간 창 또는 제한된 조건에서만 악용 가능하다.

---

## 발견 사항

### SEC3-01 | MEDIUM | `drop_tokens` 토큰 교환 캐시가 키 접두사 불일치로 정리되지 않는다

**위치:** `app/services/a2a_gateway.py:278`
**카테고리:** A07 Authentication Failures
**확신도:** Confirmed
**악용 가능성:** 원격, 인증 필요 (a2a:write), 시간 제한 (교환 토큰 TTL, 기본 600초)
**폭발 반경:** 인증 설정 변경 후에도 이전 자격으로 원격 에이전트에 최대 ~10분간 요청

**근거:**
D21-⑤에서 추가된 `drop_tokens`는 에이전트 인증 설정 변경 시 현재 워커의 캐시 토큰을 즉시 버리도록 설계되었다. 그러나 교환 토큰(RFC 8693)의 캐시 키 형식과 삭제 조건이 일치하지 않는다.

토큰 캐시 키 생성 (261행):
```python
key = (p.sub, agent.id)   # agent.id = "my-agent"
```

삭제 조건 (278행):
```python
gone = [k for k in list(_TOKENS)
        if k == ("cc", agent_id)                        # CC 토큰: 올바르게 매치
        or (len(k) > 1 and k[1] == f"a2a:{agent_id}")]  # 교환 토큰: "a2a:my-agent" ≠ "my-agent" → 매치 안 됨
```

`_cache_token` 호출부(272행)에서 키의 두 번째 원소는 `agent.id` (예: `"my-agent"`)이지만, `drop_tokens`는 `f"a2a:{agent_id}"` (예: `"a2a:my-agent"`)와 비교한다. exchange 함수(267행)의 audience(`f"a2a:{agent.id}"`)와 캐시 키를 혼동한 것으로 보인다.

**실패 시나리오:**
1. 운영자가 에이전트 인증을 토큰 교환 → API 키로 변경(보안 사유)
2. `patch_agent` → `a2a_gateway.drop_tokens(a.id)` 호출
3. CC 토큰은 정리되지만 교환 토큰은 캐시에 남음
4. 같은 워커에서 다음 요청이 이전 교환 토큰(변경 전 자격)으로 원격 에이전트를 호출
5. 토큰 TTL(기본 600초) 만료까지 지속

**권고:**
```python
# BAD
gone = [k for k in list(_TOKENS)
        if k == ("cc", agent_id)
        or (len(k) > 1 and k[1] == f"a2a:{agent_id}")]

# GOOD — 캐시 키의 두 번째 원소는 raw agent_id
gone = [k for k in list(_TOKENS)
        if k == ("cc", agent_id)
        or (len(k) > 1 and k[1] == agent_id)]
```

---

### SEC3-02 | MEDIUM | `rotate_encryption` 이전 암호화 키 파일이 chmod 전까지 umask 권한으로 노출된다

**위치:** `app/core/keys.py:212-214`
**카테고리:** A02 Cryptographic Failures
**확신도:** Confirmed
**악용 가능성:** 로컬, 파일시스템 접근 필요, 마이크로초 단위 경합 창
**폭발 반경:** Fernet 암호화 키 노출 시 DB 내 모든 암호문(시크릿, MFA 비밀, 로그 원문) 복호화 가능

**근거:**
`rotate_encryption`는 이전 암호화 키를 `secrets.prev.key` 파일에 append한다. Python 내장 `open("ab")`를 사용하므로 파일이 존재하지 않으면 프로세스 umask(일반적으로 022 → 0o644)로 생성된다. 이후 `os.chmod(..., 0o600)`으로 권한을 수정하지만, 파일 생성과 chmod 사이에 키가 group/others에게 읽힐 수 있는 창이 존재한다.

```python
# keys.py:212-214
with open(d / FERNET_PREV, "ab") as f:   # umask 022 → 0o644 로 생성
    f.write(old + b"\n")                   # ← 이 시점에 키가 world-readable
os.chmod(d / FERNET_PREV, 0o600)           # 여기서야 권한 수정
```

같은 파일의 `_read_or_create`(31행)과 `_write_atomic`(74행)은 `os.open(..., 0o600)`으로 올바르게 처리한다. `_write_private`(CLI, nexus.py:56행)도 `os.open`을 사용한다. 이 패턴만 일관성이 깨진다.

**실패 시나리오:**
1. Kubernetes pod에서 sidecar 컨테이너 또는 공유 볼륨이 있는 환경
2. 운영자가 암호화 키 회전 실행 → `rotate_encryption` 호출
3. `FERNET_PREV` 파일이 0o644로 생성되는 마이크로초 동안 다른 프로세스가 읽음
4. 이전 Fernet 키를 획득 → DB 시크릿 복호화

실제 악용은 매우 어렵다(같은 파일시스템에 접근하는 악의적 프로세스가 정확한 시점에 읽어야 함). 그러나 수정이 사소하고 주변 코드와의 일관성이 깨져 있으므로 수정을 권고한다.

**권고:**
```python
# BAD
with open(d / FERNET_PREV, "ab") as f:
    f.write(old + b"\n")
os.chmod(d / FERNET_PREV, 0o600)

# GOOD — os.open 으로 생성부터 0o600
fd = os.open(d / FERNET_PREV, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
with os.fdopen(fd, "ab") as f:
    f.write(old + b"\n")
```

---

### SEC3-03 | LOW | `decode_long_lived` 가 issuer 클레임을 검증하지 않는다 (방어 심층 공백)

**위치:** `app/core/keys.py:147-153`, 호출: `app/api/admin.py:302`, `app/api/ops.py:858`
**카테고리:** A02 Cryptographic Failures
**확신도:** Confirmed
**악용 가능성:** 원격, 인증 필요 (admin:write), 실질적 악용 불가 (sha256 검사가 차단)
**폭발 반경:** 이론적 교차 목적 JWT 수용 (실제로는 sha256 불일치로 거부)

**근거:**
백업 서명 JWT는 `iss: "agentnexus-backup"`으로 서명(admin.py:281)되지만, 복원 시 검증(`decode_long_lived`)은 `algorithms=["RS256"]`만 전달하고 `issuer` 키워드를 넘기지 않는다.

```python
# keys.py:147-153
def decode_long_lived(token: str, **kw) -> dict:
    cands = verification_keys(jwt.get_unverified_header(token).get("kid"), long_lived=True)
    if not cands:
        raise jwt.InvalidTokenError("알 수 없는 서명 키(kid)")
    return jwt.decode(token, cands[0], **kw)  # issuer 미검증

# admin.py:302 — 호출부
claims = keys.decode_long_lived(body.signature, algorithms=["RS256"])
# → issuer="agentnexus-backup" 확인 없음
```

PyJWT는 `issuer` 인자가 없으면 `iss` 클레임을 검증하지 않는다. 따라서 같은 서명 키로 서명된 다른 용도의 JWT(예: 액세스 토큰, iss=`{PUBLIC_BASE_URL}/idp`)도 `decode_long_lived`를 통과할 수 있다. 그러나 바로 뒤의 `claims.get("sha256") != digest` 검사(admin.py:305)가 실질적 보호를 제공한다 -- 액세스 토큰에는 `sha256` 클레임이 없으므로 None != digest로 항상 거부된다.

**실패 시나리오:** 현재 코드에서는 실질적 악용 경로가 없다. 미래에 `decode_long_lived`의 다른 호출부가 sha256 검사 없이 추가되면 문제가 될 수 있다(방어 심층 원칙).

**권고:**
```python
# admin.py:302 — issuer 도 검증
claims = keys.decode_long_lived(body.signature, algorithms=["RS256"],
                                 issuer="agentnexus-backup")
```

---

### SEC3-04 | LOW | `signing.archive.pem` 이 umask 의존 권한으로 생성된다

**위치:** `app/core/keys.py:196`
**카테고리:** A05 Security Misconfiguration
**확신도:** Confirmed
**악용 가능성:** 로컬, 파일시스템 접근 필요
**폭발 반경:** 공개키만 포함 (기밀성 영향 없음), 키 회전 이력 노출

**근거:**
`rotate_signing`은 이전 서명 키의 공개키를 보관본 파일에 추가한다.

```python
# keys.py:196
with open(d / SIGNING_ARCHIVE, "ab") as f:  # Python 내장 open → umask 022 시 0o644
    f.write(pub_pem)
```

같은 `keys/` 디렉터리의 다른 파일들(`signing.pem`, `secrets.key`, `secrets.prev.key`, `signing.prev.json`)은 모두 `os.open(..., 0o600)` 또는 `_write_atomic`(0o600)으로 생성된다. `signing.archive.pem`만 예외로 `open("ab")`를 사용한다.

파일 내용이 공개키(RSA public key PEM)이므로 기밀성 위험은 없다. 그러나 `keys/` 디렉터리의 일관된 보안 자세가 깨지고, 키 회전 시점과 kid 이력이 노출된다.

**권고:** SEC3-02와 같은 패턴으로 `os.open(..., 0o600)` 사용.

---

## 확인했으나 문제 없음

D20-D21 범위의 다음 메커니즘은 코드 경로를 추적하여 올바르게 구현된 것을 확인했다:

| 항목 | 확인 위치 | 판정 |
|------|-----------|------|
| **키 회전 워커 간 일관성** — `_generation()` stat 기반 캐시 무효화, `_write_atomic` + `os.replace` 원자적 교체, `_signing` LRU 캐시 키로 generation 사용 | `keys.py:58-77, 110-123` | 정상 -- `_bump()` 이전에는 모든 워커가 이전 generation 캐시 사용, 이후에는 재적재. 사이 창에서도 이전 키로 서명된 토큰은 previous 목록에서 검증 가능 |
| **JWKS 에 공개키만 노출** — `jwks()` 가 `public_numbers().n`, `.e` 만 출력. `d`, `p`, `q` 미포함 | `keys.py:156-161` | 정상 |
| **`key_status` 엔드포인트** — kid 값과 키 개수만 반환, `admin:read` 보호 | `admin.py:557-560` | 정상 |
| **Push JWT 검증** — iss/iat/jti 필수, issuer 일치, iat ±5분, body hash 필수+검증, jti 1회(TTL 900초 > iat 창 600초 + 여유), algorithms 화이트리스트(RS256/ES256/EdDSA, none/HS256 불포함) | `a2a_gateway.py:815-845` | 정상 |
| **Push JWT issuer 필수** — `push_verify.jwks_url` 지정 시 `push_verify.issuer` 도 필수 (a2a.py:562-563). 없으면 400 거부 | `a2a.py:562-563` | 정상 -- D21-① 신규 검증. issuer 없이 JWKS만으로는 아무 발급자의 서명도 통과하는 문제 방지 |
| **push_verify 변경 통제** — SECRET_MANAGE 권한 필요 (a2a.py:556-558) | `a2a.py:556-558` | 정상 -- D20-② |
| **TOTP 1회 사용** — `counter_add(f"totp-used:{user.id}:{otp}", 1, 120)` 결과 > 1이면 거부 | `idp.py:148-149` | 정상 -- D20-②. TTL 120초는 TOTP 유효 창(±1단계 = ~90초)을 포함 |
| **Admin grant 제한** — admin 역할 부여/변경 모두 admin만 | `identity.py:129-131, 163-164` | 정상 -- D20-② |
| **IdP 삭제 시 세션 종료** — `tokens_not_before` + refresh 폐기 | `identity.py:266-277` | 정상 -- D20-② |
| **게시 소유자 확인** — 다른 게시자/미러 항목 버전 선점 방지 | `catalog.py:532-534` | 정상 -- D20-② |
| **미러 auto-approve 보류** — 외부 인가 서버가 있는 항목은 자동 승인 차단 | `registry.py:209-211` | 정상 -- D20-② |
| **Access Token audience 허용 목록** — 게이트웨이 또는 등록된 MCP/A2A 대상만 | `identity.py:474-481` | 정상 -- D20-② |
| **카드 URL 고정** — `platform_card()` 가 `settings.public_base_url` 사용, Host 헤더 무시 | `a2a.py:254` | 정상 -- D21-① |
| **원격 응답 크기 상한** — `_fetch` 스트리밍 + 8MiB 누적 검사 | `a2a_gateway.py:351-362` | 정상 -- D21-① |
| **Task history 상한** — `HISTORY_MAX = 200` 적용 | `a2a_gateway.py:352, 434` | 정상 -- D21-① |
| **SSE 오류 이벤트** — 파싱 실패/원격 오류를 SSE 로 전달 (이전: 스트림 침묵 종료) | `a2a_gateway.py:731-732, 759-764` | 정상 -- D21-① |
| **Scheduler 구성요소 게이팅** — `on = settings.enabled` 로 작업별 구성요소 확인 | `scheduler.py:108-133` | 정상 -- D21-② |
| **MultiFernet 회전** — 첫 키로 암호화, 모든 키로 복호화, `reencrypt` 가 `f.rotate()` 사용 | `keys.py:164-174`, `admin.py:539-554` | 정상 -- D21-③ |
| **서명 키 유예 기간** — `max(access_token_ttl, configured_ttl*60, 3600) + 600` | `admin.py:533-536` | 정상 -- D21-③. 발급 가능한 최대 토큰 수명 + 10분 여유 |
| **mTLS required + verify:false 거부** | `tls.py:76-77` | 정상 -- D21-⑤ |
| **Compose prod a2a-grpc 최소 env** — `SIGNING_KEY_PEM`, `ENCRYPTION_KEY`, `DB_PASSWORD` 미노출 | `docker-compose.prod.yml:14-20` | 정상 -- D20-③ |
| **Helm 시크릿 체크섬** — `checksum/secret` annotation 으로 시크릿 교체 시 pod 재기동 | `deploy/helm/.../api.yaml:20` | 정상 -- D20-③ |
| **Dockerfile 다이제스트 고정 + 잠금 파일 강제** | `Dockerfile:3, 25-27` | 정상 -- D20-③ |
| **gRPC 헬스 검사** — TLS 수신이면 TCP, 평문이면 `grpc.health.v1` (readiness = 위임 대상까지) | `a2a_grpc.py:268-295`, `a2a-grpc.yaml:39-55` | 정상 -- D20-③ |
| **CLI 0600 파일** — `_write_private` 가 `os.open(..., 0o600)` + `os.chmod` 사용 | `nexus.py:54-58` | 정상 -- D21-⑤ |
| **OpenAPI create namespace 충돌 검사** — 기존 MCP 서버와 이름 충돌 시 409 | `mcp.py:646-647` | 정상 -- D21-⑤ |
| **OTLP 수신 크기 상한** — 8MiB, 413 반환 | `observe.py:103-104` | 정상 -- D21-⑤ |
| **푸시 토큰 암호화 저장** — Fernet 으로 암호화, 전달 시 복호화 | `a2a_gateway.py:698-700, 900-902` | 정상 -- D21-⑤ |
| **`/v0/servers` SQL 페이징** — cursor 기반 offset/limit, `registry.search` 가 SQL 집계로 전체 적재 방지 | `catalog.py:509-517`, `registry.py:408-410` | 정상 -- D21-⑤ |
| **Trust vocabulary 수정** — `trust_of` 가 `official/partner/trusted` 를 구분 (이전: `"trusted"` 만 비교) | `governance.py:71-72` | 정상 -- D21-⑤ |
| **토큰/예산 예약 흐름** — `check` 예약 → `settle` 정산 → `release` 반환. 워커 사망 시 `HOLD_TTL_S=300` 후 만료 | `ratelimit.py:297-392` | 정상 -- D20-① |
| **`any` 예산 범위** — 팀이 없는 주체는 사용자 범위로 (이전: 미적용) | `ratelimit.py:300-304` | 정상 -- D20-① |
| **단어 기반 도구 위험도** — camelCase 분할 후 단어 단위 매치, 부분 문자열 오탐 방지 | `governance.py:25-37`, `review.py:29` | 정상 -- D20-④ |
| **`_push_jwks` SSRF 보호** — `sync_client` 사용 → `_inject_sync` → `_guard_sync` → egress guard 적용 | `a2a_gateway.py:803-811`, `http.py:178-180` | 정상 |
| **A2A 사용량 기록에 오류 상태 포함** — `except BaseException` 에서 `status = "error"` | `a2a_gateway.py:619-620` | 정상 -- D21-⑤ |
