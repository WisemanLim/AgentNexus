# impl 51st — 2차 품질 검토 (Epic 51 · D19-④)

## 범위

| 항목 | 내용 |
|------|------|
| 대상 | 1차 검토(`.review/REVIEW-InDepth.md`·`CODE-REVIEW-Lv2`·`delegated/`) 이후 바뀐 파일 52개 — 7~10차(Epic 36~50) 변경분 |
| 위임 검토 | 보안(`/security-review` 폴백 — security-reviewer 서브에이전트) · 결함(`/code-review` 폴백 — code-reviewer 서브에이전트) → `.review/round2/delegated/` 원문 |
| 라인 리뷰 | Lv2(3년 차 인수인계) — `.review/round2/CODE-REVIEW-Lv2/` (INDEX·파일별·INFRA) |
| 검증 | 지적마다 코드 경로를 따라가 판정 — `.review/round2/VERIFY.md`. **확인된 결함만** 고친다 |
| 1차 보존 | `.review/` 1차 산출물은 고치지 않는다 |

## 수용 기준 → 시험 케이스 (확인된 결함)

| # | 결함 (출처) | 수용 기준 | 시험 | 방식 |
|---|-------------|-----------|------|------|
| C51-01 | hosted-openapi 클라이언트에 요청 단위 egress 가드 없음 (SEC2-01) | 시험 마운트 호출은 정상 · 같은 클라이언트로 `169.254.169.254` 요청은 거부 | `test_review2_fixes.py::test_hosted_openapi_guard_each_request` | pytest |
| C51-02 | 보안 응답 헤더 없음 — 로그인 화면 클릭재킹 (SEC2-05) | `/health`·`/idp/authorize`·401·200 응답 모두 `X-Frame-Options: DENY`·`frame-ancestors 'none'`·`nosniff`·`no-referrer` · SSE·listen 스트림 회귀 0 | `test_security_headers` + `test_mcp_modern_ext.py`·`test_multiworker.py` | pytest |
| C51-03 | 운영 모드 푸시 URL 검사가 이벤트 루프에서 DNS 해석 (CR2-01) | 게이트웨이·플랫폼 `pushNotificationConfig/set` 의 이름 해석이 루프 스레드 밖 · 사설 주소 거부는 그대로 | `test_push_check_off_event_loop` | pytest |
| C51-04 | 원격 에이전트에 넘기는 푸시 콜백이 내부 주소 (INFRA 4) | 사설망 에이전트 → `GATEWAY_URL`, 공인 주소 에이전트 → `PUBLIC_BASE_URL` | `test_push_callback_base` | pytest |
| C51-05 | 업스트림 세션을 지워도 유휴 시각이 남음 (CR2-04) | 한 곳(`_drop_upstream`)에서 둘 다 지움 · 유휴 만료 재초기화 동작 그대로 | `test_upstream_seen_dropped_with_session` | pytest |
| C51-06 | Helm — NetworkPolicy 가 같은 릴리스 내부 호출자를 막음 · `SEED_DEMO` 없음 · 프록시 헤더 신뢰 범위 고정 (INFRA 1·2·3) | `ingressFrom` 지정 시 api·mcp-servers·a2a-demo·a2a-grpc 포함 · `SEED_DEMO` = 데모 서비스 유무 · `api.forwardedAllowIps` 값 | `make helm-check`(4 시나리오 + 불변식 음성 확인) | helm |
| C51-07 | 푸시·폴링 결과 무소독 · 차단 에이전트 푸시 수락 · 비활성 사용자·차단 에이전트 폴링 (Lv2 a2a_gateway) | 푸시·폴링 저장분에 지시문·이메일 없음 · 차단 에이전트 푸시 404 · 비활성 사용자·blocked 에이전트 Task 는 폴링 안 함 | `test_push_and_poll_governed` | pytest |
| C51-08 | MCP 인증 설정의 전송처(인가 서버·토큰 엔드포인트·mode) 변경이 시크릿 관리 권한 밖 (Lv2 mcp_gateway·api/mcp [높음]) | 개발자 변경 403 `secret_destination_forbidden`(PATCH·test-config) · 전송처 아닌 수정은 200 · 운영자는 가능 | `test_mcp_auth_destination_change_needs_secret_manage` | pytest |
| C51-09 | Alibaba IMDS 미차단 · 플랫폼 푸시 전달 재검사 없음 (Lv2 core/http·a2a_server) | `100.100.100.200` 차단 · 사설 주소 푸시는 전달 안 함 | `test_egress_alibaba_and_platform_push_recheck` | pytest |
| C51-10 | 샘플링 중계가 서버 deny 정책 우회 · MCP 헬스 루프가 DB 오류 한 번에 종료 (Lv2 mcp_consent·main) | 중계 전 deny → 사용자 거부 오류 · 첫 리스 오류 뒤에도 주기 계속 | `test_relayed_sampling_respects_policy_and_health_loop_survives` | pytest |
| C51-11 | 모델 `data_classes` 미적용 · 한도 폴백이 정책·허용 등급 우회 (Lv2 llm [높음]) | 모델 허용 등급 밖 → 403 · internal 요청의 상용 폴백 → 429 · public 은 폴백 | `test_model_data_classes_and_limit_fallback_checked` | pytest |
| C51-12 | 내장 IdP — 동기 scrypt 루프 차단 · IP 정책 경로 누락 · MFA 무제한 (Lv2 api/idp) | MFA 실패 3회 뒤 맞는 OTP 도 잠금 · 디바이스 승인 IP 거부 · 토큰 발급은 루프 밖 | `test_idp_mfa_lockout_ip_policy_and_token_off_loop` | pytest |
| C51-13 | 기계 주체 자기 승인 · 등록 대기 에이전트 PATCH 활성화 · MCP·A2A 모르는 데이터 등급 (Lv2 gateway_admin [높음]·api/a2a·policy) | 워크로드·토큰·위임 주체는 결정 불가 · `pending → active` 403 · `secret`/`top-secret` -32602 · `Confidential` 정규화 | `test_approval_and_data_class_boundaries` | pytest |
| C51-14 | 토큰으로 토큰 발급(승격) · 클라이언트 키 팀 자유 · 무기한 PAT (Lv2 api/identity [높음]) | 스코프 PAT 로 발급 403 · 다른 팀 403(운영자는 허용) · `ttl_days: null` 422 | `test_token_issuance_boundaries` | pytest |
| C51-15 | 외부 IdP 표시 이름으로 user 정책 매치 (Lv2 policy) | 이름 매치는 내장 IdP 사용자만, sub 매치 그대로 | `test_user_subject_name_match_builtin_only` | pytest |
| C51-16 | 게시자 `_anx_review` 주입 · secret_ref 떼었다 붙이기 · `catalog:write` 토큰 재등록 전송처 (INDEX H2·H4·M16 [높음]) | 예약 키 422 · 재부착 403(새 값 직접 입력은 200) · 재등록 엔드포인트 변경은 시크릿 관리 권한 | `test_publish_reserved_and_secret_reattach` | pytest |
| C51-17 | 만료 승인 결정 · 레지스트리 동기화 격리 · Inspector 등급 · 선택 밖 operation · 모르는 Task 푸시 · 로컬 취소 · 인증 형태 변경 (INDEX M14·M18·M23·M25·M41·M45·M47) | 상태 유지 → 예약 작업이 만료 · 형식 오류에도 작업 계속 · 400 · operation 제거 · -32001 · 실행 취소 · 403 | `test_index_medium_findings` | pytest |
| C51-18 | 플랫폼 `tasks/list` 가 요청 받은 워커 메모리만 (prod 리허설 44th P44-01) | 메모리에 없는(다른 워커) 내 Task 도 목록에 · 남에게는 없음 | `test_platform_list_includes_other_worker_tasks` | pytest |
| 회귀 | A2A·MCP·게이트웨이·배포 | 관련 시험 전부 | `test_a2a*.py`·`test_mcp*.py`·`test_task_ownership.py`·`test_lv2_hardening.py`·`test_deploy.py`·`test_review_fixes.py`·`test_design_gaps.py` | pytest |
| P51 | prod(PostgreSQL·2 workers) — 보안 헤더 · 푸시 설정(공인 주소) · 회귀 | 완료 게이트 리허설 | live |

## 환경 가정

- 검토 서브에이전트가 읽는 동안 소스를 고치지 않는다 — 각 파일의 Lv2 보고가 나온 뒤에만 그 파일을 수정한다(라인 근거가 스냅샷과 맞도록).
