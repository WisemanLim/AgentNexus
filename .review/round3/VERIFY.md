# 3차 검토 지적 검증·처리 / Verification of round-3 findings (Epic 59 · D21-④)

> 일자 2026-10-08 · 검증자 Claude (implement 단계) · 원문: `delegated/security-review.md`, `delegated/code-review.md`, `CODE-REVIEW-Lv2/INDEX.md`(+ 파일별)
> 규칙: 실제 코드 경로를 따라가 확인한 결함만 고친다(`test/impl/59th`). 고치지 않는 항목은 근거를 남긴다.
> 판정: **확인**(코드로 경로 확인 → 수정) · **부분**(일부만 성립·일부만 수정) · **사용자 결정**(이번·이전 차수 확인 질문의 답과 같음) · **기록**(성립하나 설계상 맞바꿈·운영 문서로 다룸·영향 미미) · **기각**(성립하지 않음)
> 같은 근본 원인은 한 행으로 묶었다(예: SEC3-01 = CR3-01 = Lv2 L14).

## 보안 (security-review 폴백)

| ID | 판정 | 근거 | 처리 |
|----|------|------|------|
| SEC3-01 `drop_tokens` 교환 토큰 키 불일치 (= CR3-01, Lv2 L14) | **확인 → 수정** | `app/services/a2a_gateway.py` `upstream_headers` 의 캐시 키는 `(p.sub, agent.id)`, `drop_tokens` 는 `k[1] == f"a2a:{agent_id}"` 와 비교 — 교환 토큰은 한 번도 지워지지 않았다. 60th 시험(C60-04)은 실제와 다른 합성 키 `("usr_user", "a2a:<id>", ())` 를 넣어 통과했다. Lv2 의 "교환 경로는 에이전트 인증을 읽지 않음"은 맞지만(교환 audience 는 고정), 운영자가 교환에서 다른 방식으로 바꾼 뒤 캐시 토큰이 계속 붙는 경로(`token_exchange: false` 전환 전 캐시 적중)는 남는다 | 교환 키를 종류 접두 `("x", sub, id)` 로(`("cc", id)` 와 충돌 없음), 폐기는 커밋 뒤·`secret` 변경 때도 (C59-13, C60-04 키 형식 수정) |
| SEC3-02 `secrets.prev.key` umask 생성 후 chmod (= Lv2 L3 일부) | **확인 → 수정** | `rotate_encryption` 이 `open("ab")` 로 만든 뒤 `os.chmod` — 그 사이 0644 | `_append_private`(O_APPEND·O_CREAT 0600 + fchmod) (C59-14) |
| SEC3-03 백업 서명 검증 발급자 없음 (= Lv2 L4) | **확인 → 수정** | `app/api/admin.py` 복원·`app/api/ops.py` 격리 복구 시험이 `decode_long_lived(..., algorithms)` 만 — 같은 키로 서명한 액세스 토큰도 서명 단계를 통과(`sha256` 비교가 막고 있으나 감사 보고서 검증 `app/services/audit.py` 와 불일치) | `issuer="agentnexus-backup"`·`require: [iss, sha256]` (C59-15) |
| SEC3-04 `signing.archive.pem` umask 생성 | **확인 → 수정** | 공개키라 기밀성 영향은 없으나 `keys/` 의 다른 파일과 다름 | SEC3-02 와 같은 함수 (C59-14) |

## 결함 (code-review 폴백)

| ID | 판정 | 근거 | 처리 |
|----|------|------|------|
| CR3-01 | **확인 → 수정** | SEC3-01 과 같음 | C59-13 |
| CR3-02 SSE 끊김 시 예약 누수 (= Lv2 H2) | **확인 → 수정** | `app/api/llm.py` `_sse` 는 `_final` 을 `[DONE]` 뒤에만 부른다 — 끊기면(`GeneratorExit`·취소) 사용 기록·정산이 모두 빠진다. Lv2 지적대로 예산 예약(`usdhold`)은 `counter_add` 가 매 요청 TTL 을 300초로 다시 늘려 같은 범위에 요청이 계속 오면 누수분이 풀리지 않는다(README "300초 뒤 풀림"은 조용할 때만). 끝 직전에 끊으면 답은 받고 사용 기록은 없다(예산·한도 우회, 2차부터) | `finally` 에서 받은 만큼 추정해 `cancelled` 로 기록·정산 — 취소 중 await 대신 실행기에 넘김 · 예산 예약은 예약 단위 고정 만료(`amount_hold` — Valkey HASH + Lua) · 정산은 사용 기록 커밋 뒤(Lv2 L10) (C59-02) |
| CR3-03 재암호화 한 트랜잭션 (= Lv2 L5) | **확인 → 수정** | `yield_per(500)` 로 읽으며 고친 행이 모두 세션에 남고 마지막에 한 번 커밋 — 큰 `log_entries` 에서 메모리·잠금. `yield_per` 중간 커밋은 PostgreSQL 서버 측 커서를 닫으므로 권고안 그대로는 안 됨 | 기본키 순 500행 배치(키셋) — 배치마다 커밋·`expunge_all` (C59-06) |
| CR3-04 헬스 요청 varint 1바이트 가정 (= Lv2 L18 일부) | 기록 | 서비스 이름은 `""`·`readiness` 뿐, 결과는 SERVING/NOT_SERVING 둘 중 하나 — 영향 없음 | 없음 |

## Lv2 라인 리뷰 — High

| ID | 판정 | 근거 | 처리 |
|----|------|------|------|
| H1 admin 매핑 IdP 의 `jwks_uri` 변경으로 admin 획득 | **확인 → 수정** | `patch_idp` 는 `role_mapping` 이 본문에 있을 때만 `_check_roles` — 보안 담당(`identity:write`)이 admin 매핑 IdP 의 `jwks_uri`·`internal_url`·`audience`·클레임 이름을 바꾸면 자기 키로 서명한 JWT 의 그룹이 `_map_roles` 로 admin 이 된다(`app/core/auth.py`). 11차 사용자 결정 "admin 부여는 admin 만"의 우회 | admin 매핑 IdP 는 이름 변경·연동/로그인 중지 외 모든 변경을 admin 만 (C59-01) |
| H2 | **확인 → 수정** | CR3-02 와 같음 | C59-02 |
| H3 번들 안 `cosign.pub` 로 검증 | **확인 → 수정** | `bundle_import` 가 `path / "cosign.pub"` 로 서명 확인 — 번들을 바꿔치기하는 쪽이 키와 서명을 함께 넣으면 통과(54th 의 "서명 검증 기본"이 무결성만 보장) | `--cosign-key`(번들 밖) 필수, 번들 안을 가리키면 거부 (C59-03, C54-04 갱신) |

## Lv2 — Medium

| ID | 판정 | 근거 | 처리 |
|----|------|------|------|
| M1 TOTP 1회 키 정규화 | **확인 → 수정** | `totp.verify` 는 안쪽 공백 제거, 1회 키는 `strip()` 만 | 같은 정규화 (C59-04) |
| M2 단어 단위 위험도 공백 | **부분 → 일부 수정** | 목록 밖 동사(overwrite·erase·shutdown·flush·upsert·save·submit·clear)는 확인 → 추가. 붙여 쓴 소문자(`deleteuser`)·활용형(`deletes`)은 접두·어미 처리 시 `list_comments`·`list_runs`·`list_transitions` 같은 읽기 도구가 high(승인 필요)로 바뀌는 오탐과 맞바꿈 — 기본값일 뿐 규칙은 운영자가 바꾼다 | 동사 추가 (C59-17) · 나머지 기록 |
| M3 SSE 줄 버퍼 | **확인 → 수정** | httpx `aiter_lines()` 는 줄 끝까지 모은 뒤 돌려준다 — 길이 검사가 메모리를 다 쓴 다음 | 바이트 단위 줄 읽기(미완성 줄 상한) + `Accept-Encoding: identity`·압축 응답 거부(L34) (C59-05) |
| M4 재암호화 대상 누락 | **확인 → 수정** | 60th 에서 암호화한 `A2ATask.push_config.token_enc` 가 58th `reencrypt` 목록에 없다 → `drop_previous` 뒤 중계 복호화 실패. MCP 요청 상태 토큰(`mcp_modern`, 최대 600초)은 저장하지 않으므로 대상 아님 | 웹훅 토큰 포함 (C59-06) · 이전 키 제거는 회전 10분 뒤 권장(README) |
| M5 미러 자동 승인 — 고정 안 된 oauth2 | **확인 → 수정** | `_external_auth` 는 메타데이터에 인가 서버가 적혀 있을 때만 외부로 본다. `type: oauth2` 만 있으면 등록 때 `discover_auth` 가 업스트림 보호 리소스 메타데이터에서 인가 서버를 가져와(`mcp_gateway.py`) 외부일 수 있다 — 53rd 의 보류 의도 우회 | 플랫폼 issuer 로 고정되지 않은 oauth2 도 보류 (C59-07, C53-05·미러 동기화 시험 자료 갱신) |
| M6 미러가 수동 게시 항목에 버전 추가 | **확인 → 수정** | `import_entries` → `publish` 는 같은 이름의 기존 항목에 출처 확인 없이 버전을 더하고, auto_approve 면 승인 | 다른 출처 항목은 `name_owned_elsewhere` 로 건너뜀 (C59-08) |
| M7 게이트웨이 단독 프로세스의 실행 재개 | **확인 → 수정** | 57th 에서 승인 만료를 gateway·host 어느 쪽이든 돌게 했는데, `on_approval` 이 그 프로세스에서 `schedule(resume=True)` | Host 가 아니면 예약하지 않고 Host `recover_stale` 이 이어받음 (C59-09) |
| M8 보안 담당이 자신에게 operator 부여 | 사용자 결정 | 11차 확인 질문 "admin 부여는 admin 만 (Recommended)" — 다른 역할 부여는 `identity:write` 그대로. 바뀐 사실 없음 | 없음 |
| M9 토큰 예약이 입력만 | 기록 | 출력 상한(`max_tokens`)을 예약하면 정산이 "더 쓴 만큼만 더함"이라 대부분의 요청이 과대 계상된다(분 창 안에서 반환 불가 — L8). 예약은 입력 추정, 출력은 정산 때 — 52nd 설계 | 없음 |
| M10 자식 스팬 집계 범위 | **확인 → 수정** | `_overview_extra` 하위 질의가 시간 조건 없이 전체 스팬 GROUP BY (경보 경로는 호출하지 않음 — 개요 API 만) | `kid.start_time >= since` (자식은 부모 뒤에 시작) (C59-10) |
| M11 설치가 태그로 검증 | **확인 → 수정** | `cosign verify <tag>` 뒤 `up -d` — 같은 태그의 로컬 이미지나 `build:` 로 기동될 수 있다 | `pull` → 받은 다이제스트로 검증 → `up -d --no-build --pull never`, 다이제스트 없으면 중단 (C59-11) |
| M12 직접 발급 `mcp:` Access Token 스코프 | 기록 | Access Token 발급(`tokens:issue`)은 보안·관리자 권한이고 audience 는 11차 결정대로 등록 대상만. MCP 서버는 backend 망에서 api 만 수신(NetworkPolicy·compose internal) — 게이트웨이를 건너뛴 직접 제시는 망 경계 밖 | 없음 |
| M13 폴백 모델 없음 → 한도 없이 진행 | **확인 → 수정** | `prepare` 의 두 분기 모두 `tgt is not None` 조건 — 폴백 모델이 없거나 꺼져 있으면 원래 모델로 통과 | 429 (C59-12) |

## Lv2 — Low

| ID | 판정 | 처리 / 근거 |
|----|------|-------------|
| L1 generation mtime 해상도 | 기록 | 회전은 수동 관리 명령 — 같은 틱 두 번 회전은 비현실적. 호출마다 stat 1회는 의도 |
| L2 env 이전 서명 키(만료 없음·개인키 PEM) | 기록 | env 모드는 운영자가 `*_PREVIOUS` 를 직접 지운다(README 키 회전 절차). 보관 공개키 폐기 목록은 기능 추가 |
| L3 회전 잠금 없음 | 부분 | 권한 부분은 SEC3-02 로 수정. 동시 회전은 관리자 수동 명령이라 기록 |
| L4 | 수정 | SEC3-03 |
| L5 | 수정 | CR3-03 |
| L6 회전 감사 시점·README 절 없음 | 부분 | README·SECURITY 에 키 회전 절차 추가(12차 문서). 감사 시점은 기록 |
| L7 env 모드 롤링 중 옛 파드 | 기록 | 롤링 구간(파드 교체 동안)에는 옛 파드가 새 kid 토큰을 모른다 — 401 은 재시도로 풀림. 미리 알리는 "next" 키는 기능 추가. README 키 회전 절차에 명시 |
| L8 지난 분 창 반환 | 기록 | 분 경계 근처 일시 완화 — 52nd 설계(보수적 정산) |
| L9 `any` 예산 버킷 의미 | 기록 | 52nd 결정(팀 없으면 사용자) 그대로 |
| L10 정산이 커밋 전 | 수정 | C59-02 와 함께 |
| L11 최악 시간 = 규칙 시간 × 시도 | 기록 | 52nd 에서 요청 단위 마감을 넣었다가 폴백을 막아 제거(시도마다만) |
| L12 OTLP 구조 오류 500 | 수정 | C59-16 |
| L13 순서 없는 `limit(500)`·인덱스 | 기록 | 표본 p95 — 성능 범위(NFR-P1 생략 결정) |
| L14 | 수정 | SEC3-01 |
| L15 artifacts 상한 없음 | 기록 | 원격 응답은 8 MiB 상한(56th) 안 — 개수 상한은 기능 추가 |
| L16 푸시 수신 본문 무제한 | 수정 | `read_capped` (C59-16) |
| L17 D21 이전 issuer 없는 push_verify | 부분 | README 트러블슈팅에 이행 안내. 재조회·https 강제는 기록 |
| L18 | 기록 | CR3-04 |
| L19 프로브가 127.0.0.1·TLS 면 TCP | 기록 | 54th 결정(TLS 수신이면 TCP) — HA 프로파일 문서화 |
| L20 서버 없는 같은 이름 시크릿 덮어쓰기 | 기록 | `mcp/<ns>/backend` 시크릿은 이 경로만 만든다 |
| L21 Inspector 드라이런 data_class | 기록 | 드라이런은 판정만, 실행 없음 |
| L22 동기 Valkey 반납 | 기록 | 45th 결정(취소 중 await 재취소 회피) |
| L23 IdP 비활성·삭제 기준 | 기록 | 내장 issuer 와 같은 IdP 생성은 관리 실수 — 별도 검토 |
| L24 익명 `/v0/servers?search=` | 기록 | 거름 조건이 있는 경로는 파이썬 필터(60th 범위 밖) |
| L25 소유자 없는 항목 새 버전 | 기록 | 새 버전은 승인 대기(pending) — 승인 게이트 유지 |
| L26 official·partner readOnlyHint | 기록 | D17-④ 설계(승인 신뢰 등급 서버의 힌트는 믿는다) — 60th 는 그 설계를 실제로 동작하게 한 것 |
| L27 existingSecret 체크섬 상수 | 기록 | README 키 회전 절차에 `rollout restart` 안내 |
| L28 compose a2a-grpc TLS 볼륨 | 기록 | 기본 빈 값 — 오버레이로 인증서 마운트(문서) |
| L29 readiness 가 DB 에 묶임 | 기록 | 54th 결정 |
| L30 경로 chmod·디렉터리 0755 | 기록 | 파일은 0600 으로 생성 — 영향 미미 |
| L31 0020 인덱스 잠금 | 기록 | 이미 적용된 마이그레이션 |
| L32 좁힌 admin PAT 로 admin 부여 | 수정 | `_is_admin` = admin 역할 + `admin:write` 스코프 (C59-01) |
| L33 CLI 가 확인 문구를 대신 채움 | 기록 | `--yes`·`--restore-config` 가 명시 동의 |
| L34 압축 응답 팽창 | 수정 | C59-05 |
| L35 보유자 만료 워커 시계 | 기록 | 파드 시계 차이 수 초 — 300·960초 만료에 비해 작음 |

## 집계

수정 17건(시험 C59-01~17) · 부분 4 · 사용자 결정 1 · 기록 나머지 · 기각 0.
