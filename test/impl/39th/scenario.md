# impl 39th — 품질 검토 반영 (Epic 39 · D16-④)

## 입력 — wds-review 산출물

| 산출물 | 도구 | 비고 |
|--------|------|------|
| `.review/delegated/security-review.md` | `/security-review` 불가 → `oh-my-claudecode:security-reviewer` 폴백 | SEC-01~12 (High 3 · Medium 6 · Low 3) |
| `.review/delegated/code-review.md` | `/code-review` 불가 → `oh-my-claudecode:code-reviewer` 폴백 | BUG-01~12 (High 3 · Medium 6 · Low 3) |
| `.review/REVIEW-InDepth.md` | 고유(depth-method) | 종합 🟡 · D-01~19 |
| `.review/CODE-REVIEW-Lv2/` | 고유(code-method, 레벨 2) | 라인 리뷰 — 위험:높음·중간 항목을 아래 표로 판정 |

원칙: 실제 코드로 다시 확인한 결함만 고친다(기능 추가 없음). 고치지 않는 항목은 이유를 남긴다.

## 판정 — 확인 후 수정

| ID | 결함 (실파일 근거) | 수정 |
|----|-------------------|------|
| SEC-01 | 외부 IdP 토큰 검증이 헤더 `alg` 를 그대로 `algorithms=[…]` 로 넘김 (`app/core/auth.py` `_external_verify`) | 비대칭 허용 목록(`EXTERNAL_ALGS`) · JWK 에 `alg` 가 있으면 헤더와 일치해야 함 |
| SEC-02·04 | 사용자 URL(OpenAPI 가져오기·카드·푸시·커넥터·hosted-openapi base_url)로 서버 측 요청 — 메타데이터 주소 차단 없음 | `app/core/http.py` egress 가드(요청 훅): 링크 로컬 169.254/16·fe80::/10·`fd00:ec2::254`·`metadata(.google.internal)` — 이름 해석 결과도 확인(60초 캐시), 시험 마운트 제외. 팩토리 밖 httpx2 클라이언트(hosted-openapi)는 `blocked_url` 사전 점검. 사설망은 제품 기능이라 허용 |
| SEC-03 | 로그인(브라우저·password grant·디바이스 승인) 실패 횟수 제한 없음 (`app/api/idp.py`) | 사용자 이름별 실패 10회/15분 고정 창(ratelimit 백엔드 — prod Valkey 로 워커 공유) → 한도 중엔 비밀번호 확인 없이 거부 + 감사 `too_many_failures` |
| SEC-05 | `localhost`·`127.0.0.1` redirect_uri 를 모든 환경에서 허용 (`_check_redirect`) | local/test 에서만(Vite 콘솔 :5173). prod 콘솔은 같은 origin `/console` |
| SEC-07 | Access Token `subject` 에 다른 사용자의 `sub` 지정 가능 → 그 사용자의 세션·Task 열람 | 호출자 자신이 아닌 실제 사용자 `sub` 는 400 `subject_is_user` (서비스 주체용) |
| (추가) | 디바이스 승인(`POST /idp/device`)이 MFA 를 확인하지 않음 — 폼에 OTP 칸은 있음 | `_mfa_ok` 확인 (브라우저 로그인과 같은 규칙) |
| BUG-01·04·09 | `mcp_relays`·`mcp_tasks` 행 정리 없음, 즉시 완료 작업 `_discard` 가 중계 행을 남김 | 보존 실행에 1일 경과분 삭제(작업 TTL 1시간) · `_discard` 가 중계 행도 삭제 |
| BUG-02 | A2AServer `tasks/ctxs/runners/subscribers` 무한 증가 | 종료 시 실행 문맥 해제 · 빈 구독 목록 제거 · 상한 1000 넘으면 오래된 종료 작업부터(영속 저장소 있으면 loader 재조회) |
| BUG-03·10 | 작업 생존 신호 루프가 `_beat` 예외 한 번에 종료 → STALE_S 뒤 거짓 failed · 감시 Task 참조 미보관(루프는 약한 참조) | try/except + 경고 로그 · `_WATCHERS` 강한 참조. A2AServer 푸시 Task 도 같은 방식 |
| BUG-05 | 업스트림 토큰 캐시 `_TOKENS` 무한 증가 (MCP·A2A) | 1024 넘으면 만료분 정리 |
| Lv2 시크릿 참조 | `mcp:write`·`a2a:write`(developer)만으로 `auth.secret_ref`·`tls.ca_secret`·`tls.client_cert_secret` 를 **남의 시크릿**(공급자 키 등)으로 바꾸고 엔드포인트를 외부로 돌려 값을 빼낼 수 있음 (`app/api/mcp.py` patch·test-config, `app/api/a2a.py` 등록·patch). 게시 server.json `_meta.auth` 도 그대로 복사(`app/services/registry.py` `_register_mcp`, 자동 승인 소스 포함) | 자기 네임스페이스(`mcp/{id}/`·`a2a/{id}/`) 밖 참조는 시크릿 관리 권한(`gateway:write`) 필요 → 403 `secret_ref_forbidden` · 게시 메타데이터의 남의 참조는 등록 때 제거 (`secrets.check_refs`) · 자격 증명이 묶인 대상의 전송처(MCP `endpoint`, A2A `token_url`) 변경도 새 값을 함께 넣지 않으면 `gateway:write` → 403 `secret_destination_forbidden` (`secrets.check_destination`) |
| Lv2 인가 서버 고정 | 업스트림 401 의 `WWW-Authenticate resource_metadata` 로 재탐색한 인가 서버를 그대로 써, 악성 업스트림이 사용자 **플랫폼 토큰**(token exchange subject_token)을 받는 곳을 바꿀 수 있음 (`mcp_gateway` 401 재시도 2곳) | 등록된 `authorization_server` 와 다르면 403 `authorization_server_changed` (변경은 관리자 재탐색 API) |
| Lv2 CLI env | `nexus install compose` 의 `write_env` 가 `DB_PASSWORD` 만 난수로 바꾸고 `DATABASE_URL` 안의 `CHANGE_ME` 는 그대로 → api 가 DB 에 붙지 못함 · `SIGNING_KEY_PEM` 을 PEM 이 아닌 난수로 채움 (`app/cli/nexus.py`) | 접속 문자열 비밀번호 일치 · 서명 키는 비움(자동 생성) |
| Lv2 CLI 경로 | 서버 응답 템플릿 파일명·번들 매니페스트 이름의 `../`·절대 경로로 대상 밖 쓰기·이동 (`nexus mcp scaffold`, `nexus bundle import`) | 대상 밖이면 중단(exit 1) |
| Lv2 정책 2인 검토 | 남의 초안을 고친 사람이 승인할 수 있음 — 작성자만 제외 (`app/api/gateway_admin.py`) | 수정자 목록(`_editors`, 초안 스냅샷) 도 승인 불가 |
| Lv2 경쟁 | 승인 1회 사용·refresh token 회전·authorization code 1회 교환이 읽기→쓰기라 동시 요청이 둘 다 통과 (`governance`, `identity.refresh`, `/idp/token`) | 조건부 UPDATE rowcount 로 하나만 |
| Lv2 푸시 무결성 | 푸시 토큰으로 찾은 Task 가 아니라 본문 task id 로 기록 → 같은 에이전트의 다른 Task(남의 것) 위조 (`a2a_gateway.receive_push`) | 본문 id ≠ 토큰의 Task 면 404 + 감사 `task_mismatch` |
| Lv2 폴링 기아 | 원격 Task 폴링이 `updated_at` 오래된 20개만 — 실패하는 Task 가 20개 넘으면 나머지가 영원히 폴링되지 않음 | `last_polled_at` 기준 순환(시도 시각 먼저 기록) |
| Lv2 도구 호출 기록 | `_call_tool_inner` 가 `RpcError` 만 실패로 표시 — stdio·hosted 일반 예외는 `ok` 로 사용량·지표 기록 (`mcp_gateway`) | 모든 예외를 `error` 로 |
| Lv2 동의 처리 예외 | `mcp_consent.handle` 이 `ConsentError` 만 처리 — 그 밖의 예외면 업스트림이 응답 없이 시간 초과까지 대기 | -32603 으로 즉시 응답 + 로그 |
| Lv2 A/B 가중치 | `weight` 가 숫자 아님·음수·합 0 이면 `random.choices` 가 모든 요청에서 500 — 저장 시 검증 없음 (`api/llm.py`) | 규칙 스키마 검증(422) |
| Lv2 멀티모달 마스킹 | 마스킹 때 list `content` 를 텍스트 하나로 통째 교체 → 이미지 파트 유실 (`llm._guard_messages`) | 텍스트 파트만 마스킹본 하나로, 다른 파트는 순서 유지 |
| Lv2 리포트 서명 | `verify_report_signature` 가 발급자·필수 클레임 미검증 — 같은 키로 서명된 **사용자 액세스 토큰**이 유효한 리포트 서명으로 통과 (`audit`) | `iss=agentnexus-audit` + `report_id`·`sha256` 필수 |
| Lv2 CLI context | `nexus context add <현재 컨텍스트>` 의 새 URL 이 `_save` 재동기화에 덮여 저장되지 않음 | 현재 컨텍스트면 최상위 url 도 갱신 |
| Lv2 데모 에이전트 | `next()` 기본값 없음 — 실제 Dynatrace 로 바꾸면 데모 데이터에 없는 문제 id 에서 StopIteration | 기본값 None |
| 게이트 발견 | prod 데모 서버(`a2a-demo`·`mcp-servers`)가 runtime 이미지의 `UVICORN_WORKERS=2`(uvicorn 이 `UVICORN_*` env 를 읽음)로 2 워커 — Task·세션을 프로세스 메모리에 두므로 SendMessage 직후 GetTask 가 다른 워커로 가면 NOT_FOUND (37th 스모크 r2 P37-03~07) | compose·Helm 데모 서비스 `UVICORN_WORKERS=1` |
| D-05 | `.dockerignore` 누락 → prod 이미지(에어갭 번들)에 `.docs/ .omc/ .wds/ test/ 캐시` | 제외 목록 추가 |
| D-06 | `uv sync` 가 잠금 파일 불일치를 허용 | `--locked` |
| D-07 | e2e 에 playwright-core 1.63 이 섞임(axe peer) — 러너·이미지 1.55 | `playwright-core` 1.55.0 고정 |
| D-17 | Helm liveness 가 DB 를 보는 `/health` — DB 장애 때 api 파드 재시작 | liveness = TCP, readiness = `/health` 유지 |

## 판정 — 수정하지 않음 (이유)

| ID | 이유 |
|----|------|
| SEC-06 로그인 CSRF | 공격자가 보낸 로그인으로 생긴 code 는 공격자의 `code_challenge` 에 묶여 콘솔(공개 클라이언트, PKCE 필수)이 자기 verifier 로 교환하지 못한다 + `state` 확인 |
| SEC-08 LIKE 와일드카드 | 검색 결과는 이미 권한으로 걸러진 범위 — 노출 없음, `%`/`_` 가 넓게 맞는 검색 동작 차이일 뿐 |
| SEC-09 JWKS 같은 origin | 권고(issuer 와 같은 origin)는 Google(issuer `accounts.google.com`, jwks `www.googleapis.com`)을 깨뜨린다. issuer 는 관리자 등록 값, 메타데이터 주소는 egress 가드가 막음 |
| SEC-10 scrypt N | 2^14·r8 은 OWASP 최소선 아래이나 결함이 아닌 강도 조정 — N 상향은 로그인 지연·maxmem·재해싱 설계가 필요해 결정 사항으로 남김 |
| SEC-11 password grant | 이미 `app_env in (local,test)` 로만 동작 — 문제 없음 |
| SEC-12 redirect 사전 등록 | 같은 origin 검증 + 공개 클라이언트 PKCE 로 충분 — 클라이언트 등록 모델은 기능 추가 |
| BUG-06·07 LISTENS·progress 워커 단위 | 문서화된 한계(2 워커에서 구독 한도 느슨, 다른 워커 resume 의 중간 progress 누락 — 최종 결과는 DB 공유). Valkey 카운터·pub/sub 는 기능 추가 |
| BUG-08 `_UPSTREAM_SESSIONS` | 키 = 서버 × 주체(값 수십 바이트), 서버 설정 변경 때 비움 — 실질 위험 낮음 |
| BUG-11 `_HOSTED` 이전 클라이언트 | Plausible — 설정 변경 빈도 낮음, 기록만 |
| BUG-12 푸시 재시도 | A2A 사양이 재시도를 요구하지 않음 — 기능 추가 |
| D-01~04 LICENSE·NOTICE·SECURITY.md·Dependabot | 라이선스 선택·보안 연락처는 소유자 결정 — 보고서 결정 항목 |
| D-08 `make prod-all` CHANGE_ME | 로컬 prod 리허설이 CHANGE_ME 로 돈다(설계) — 실제 배포는 `make deploy` 가 거부 |
| D-09 prod 데모 서버 기본 켬 | 리허설 스모크가 데모에 의존 — 운영 값은 배포자가 끔(문서화) |
| D-10 `.env.example` 8000 | 키 집합 동일, example 은 기본값(8000), 이 호스트 prod 는 8000 점유로 8800 |
| D-11~14 문서·결과 표류 | 완료 게이트 문서 현행화에서 처리 |
| D-15 Chart home | 소유 도메인 미확인 — 결정 항목 |
| D-16 Helm a2a-grpc·평문 gRPC | 알려진 한계(37th) — TLS 는 앞단 종료 |
| D-18 CI | 저장소·CI 미가동 상태 — 결정 항목 |
| Lv2 `/idp/token` 토큰 교환 | 클라이언트 인증 없이 사용자 토큰으로 업스트림 audience 토큰을 받고 `act` 를 스스로 정함 — 업스트림이 사용자에게 직접 열려 있으면 게이트웨이 정책 우회. RFC 8693 공개 grant 의 동작을 바꾸는 일(기존 시험·2nd 스모크가 사용)이라 결정 항목: confidential 클라이언트(워크로드) 인증 요구 여부 |
| Lv2 refresh 재사용 탐지 | 폐기된 refresh token 재제시 때 계열 전체 폐기(OAuth BCP)는 세션 계열 모델이 필요한 기능 — 회전 경쟁만 수정 |
| Lv2 헤더 신뢰(`X-AgentNexus-Agent`·`Data-Class`·`Run`) | 설계상 호출자 신고 값(문서화) — agent 주체 allow 정책·데이터 등급 하향 신고의 위험은 결정 항목(인증 주체에서 도출) |
| Lv2 `readOnlyHint` 신뢰 | 업스트림 주석으로 위험도 low — trusted 등급에서만 믿도록 바꾸는 것은 정책 변경(결정 항목) |
| Lv2 사설망 SSRF(푸시·OpenAPI·업스트림 PRM) | 사설망 업스트림이 제품 기능 — 푸시 URL 만 사설 차단할지 결정 항목. 메타데이터 대역은 차단됨 |
| Lv2 `APP_ENV` 기본 `local` | env 파일·Helm·시험이 항상 지정 — 기본값을 바꾸면 env 없는 보조 서비스 동작이 달라져 기록만 |
| Lv2 health 루프·watcher 리스 없음 · listen 부하 | 성능 항목(워커 수만큼 호출) — 기능 결함 아님 |
| Lv2 stdio 허용 목록 | 실행 파일 이름만 비교 — 인자 고정 허용 목록은 운영 정책(기본값 stdio 금지) |
| Lv2 gRPC 평문 | 알려진 한계(37th) |
| Lv2 `web/src/lib/api.ts:71` 비 JSON 500 | 확인 결과 아님 — `request()` 가 실패 응답을 `toError` 로 보내고 거기서 파싱 실패를 잡는다. 성공 응답이 JSON 이 아닌 경우만 남는데 API 계약상 없음 |
| Lv2 관측 집계 성능 | 사용 기록 전체를 메모리로 읽어 집계·규칙마다 재집계 — 성능 항목(데이터가 늘면 SQL 집계로) |
| D-19 `.docs/`·`.wds/` gitignore | 커밋 의도 확인 필요 — 결정 항목 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C39-01 | RS256 정상 · HS256(공개키 PEM 을 비밀로)·none 위조 거부 · JWK alg 불일치 거부 | `test_external_idp_alg_allowlist` | pytest |
| C39-02 | 169.254·IPv4-mapped·fe80·IMDS IPv6·metadata 이름 차단(동기·비동기) · 이름이 메타데이터로 해석돼도 차단 · 사설망 허용 · 마운트 통과 · OpenAPI 가져오기 400 | `test_egress_guard` | pytest |
| C39-03 | 실패 한도 → 맞는 비밀번호도 거부(대소문자·공백 무시) · password grant 도 · 창 초기화 후 로그인 | `test_login_failure_lockout` | pytest |
| C39-04 | loopback redirect: test 200 · prod 400 · prod 같은 origin 200 | `test_loopback_redirect_only_local` | pytest |
| C39-05 | MFA 사용자의 디바이스 승인: OTP 없으면 거부 · 맞으면 승인 | `test_device_approval_requires_mfa` | pytest |
| C39-06 | Access Token: 남의 sub 400 · 서비스 주체·자기 sub 201 | `test_access_token_subject_not_other_user` | pytest |
| C39-07 | 보존: 1일 경과 중계·작업 삭제 · 최근 유지 | `test_retention_mcp_state` | pytest |
| C39-08 | 생존 신호 첫 실패 뒤에도 계속 · 감시 Task 보관 · 작업 completed | `test_task_heartbeat_survives_errors` | pytest |
| C39-09 | A2AServer 상한 · 종료 작업 문맥 해제 | `test_a2a_server_bounded` | pytest |
| C39-10 | 토큰 캐시 만료분 정리 | `test_token_cache_prunes_expired` | pytest |
| C39-11 | Helm liveness TCP · readiness `/health` | `test_helm_liveness_tcp` (+ 38th 클러스터 실행) | pytest |
| C39-12 | 남의 시크릿 참조 403(MCP patch·test-config·TLS·A2A) · 자기 네임스페이스 허용 · 전송처 변경 403(patch·test-config) · 게시 메타데이터 참조 제거 | `test_secret_ref_requires_secret_manager` | pytest |
| C39-13 | 401 재탐색 AS 가 다르면 거부 · 같으면·미고정이면 통과 | `test_authorization_server_pinned` | pytest |
| C39-14 | env 생성: DATABASE_URL 비밀번호 = DB_PASSWORD · SIGNING_KEY_PEM 비움 | `test_write_env_consistent` | pytest |
| C39-15 | scaffold `../`·번들 매니페스트 `../` → exit 1, 대상 밖 파일 불변 | `test_cli_rejects_path_traversal` | pytest |
| C39-16 | 남의 초안을 고친 사람의 승인 403 `self_approval` | `test_policy_editor_cannot_approve` | pytest |
| C39-17 | 승인 소비 두 번 → (True, False) | `test_approval_consumed_once` | pytest |
| C39-18 | 다른 task id 푸시 404 · 행 생성 없음 · 같은 id 200 | `test_push_bound_to_task` | pytest |
| C39-19 | 폴링 2회(limit 2)로 3개 모두 시도 | `test_watcher_rotates` | pytest |
| C39-20 | refresh 회전 경쟁 — 늦은 요청 invalid_grant | `test_refresh_rotation_single_winner` | pytest |
| C39-21 | stdio·hosted 일반 예외 → 기록 status `error` | `test_tool_call_generic_error_recorded` | pytest |
| C39-22 | 동의 처리 중 예외 → -32603 응답 | `test_consent_handle_unexpected_error` | pytest |
| C39-23 | 가중치 문자열·음수·합 0 → 422 · 정상 201 | `test_ab_weights_validated` | pytest |
| C39-24 | 이미지 파트 유지 + 텍스트는 마스킹본 하나 | `test_masking_keeps_image_parts` | pytest |
| C39-25 | 사용자 토큰을 리포트 서명으로 검증 → valid false | `test_report_signature_rejects_user_token` | pytest |
| C39-26 | 현재 컨텍스트 URL 갱신 | `test_cli_context_add_current` | pytest |
| 회귀 | 전체 pytest(SQLite·PG) · Vitest · Playwright | 완료 게이트 | — |
| P39-01~10 | prod(2 workers·Valkey): redirect · 실패 한도(워커 공유) · 감사 · egress · 대리 발급 · 보존 · 이미지 위생 · JSON-RPC · 감사 체인 | `smoke-prod.sh` | live |

## 환경 가정

- 로그인 실패 창은 ratelimit 백엔드를 쓴다 — local(memory)은 워커 단위, prod(Valkey)는 공유. Valkey 가 끊기면 5초간 워커 로컬로 동작(기존 NFR-A1 규칙).
- egress 가드는 DNS 재바인딩(검사 뒤 다른 주소로 연결)까지 막지 않는다 — 클러스터 egress NetworkPolicy 가 최종 방어선.
