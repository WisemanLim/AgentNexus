# impl 23rd — 운영·관측 화면 깊이 (Epic 23 · D12-⑦ · FR-I·O02·O04·O05·G00·L · SCR-B02~B07·F01~F06·G01·G04~G07·H01~H15·I01~I05·J01~J06·K01~K10·L02~L06)

## 범위

| 그룹 | 화면 | 구현 |
|------|------|------|
| B 공통 | B02 개요 · B03 ⌘K · B04 알림 · B05 구성요소 · B06 내 계정 · B07 API 탐색기 | 게이트웨이 추가 지연 p95·첫 토큰 p95·팀 예산·등록 승인 KPI · 범위 탭·명령 · 분류 탭·음소거·채널(`/notifications/preferences`) · 구성요소 버전·복제 수·재시작 명령·우회 점검 · PAT 스코프·90일 상한·내 세션·다른 세션 종료(토큰 `sid`) · 시험 호출·curl/CLI |
| F MCP | F01 목록 · F02 상세 · F04 OpenAPI · F05 템플릿 · F06 Inspector | KPI·버전·재시작 · 리소스·프롬프트·샘플링 정책(ask/deny)·로그 · 작업 선택·이름·위험도(→ ToolRule) · 포함 항목(docker/register/readme) · 집계 도구 검색 |
| G A2A | G01 · G04~G07 | KPI·프레임워크 · 인증 스킴 · Task 취소(MSG-002)·필터 · Task 상세 · 시험 콘솔 |
| H 게이트웨이 | H01~H06 · H09~H15 | 우회 점검 · 장애 주입 라우팅 시험 · 결정 로그 필터·JSONL · 401 시험·탐지 · 시크릿 사용처·D-day·유출 점검 · 한도 `warn`·429 미리보기 · 최근 차단 · 인자 스키마(strict/warn)·소독 미리보기 · 승인 위임·마감 강조 · MCP 세션 강제 종료 · A2A 통계 |
| I 모델 | I01~I05 | 공급자 어댑터 화면(I02) · 비교(최대 4) · Access Token 5–60분 |
| J Identity | J01~J04 · J06 | IdP 편집·시험 로그인 · 사용자 상세(세션·강제 종료 사유·재동기화·클레임) · 교환 시뮬레이터·15분 상한 · MCP Auth 다시 탐색 |
| K 관측 | K01~K10 | 계측 비율 · trace 사용자·도구 필터 · 마스킹 해제(사유·30분, 시크릿 제외) · CSV·드릴다운 · 예산 화면 · 리플레이 페이지 · 구간 검증 · 규제 리포트 마법사(K10) |
| L 관리 | L02~L06 | 커넥터 생성·편집 · RBAC 행렬·시뮬레이터(L03) · 업그레이드 계획·SBOM 기준 · 예약 백업·격리 복구 시험·`RESTORE` 입력 확인 · PII 규칙·시험·세션/IP/토큰 정책·시간대·이력·되돌리기 |

마이그레이션 0012: `users.tokens_not_before·claims` · `approvals.delegated_to` · `tool_rules.args_mode` · `log_entries.raw_enc` ·
`mcp_servers.sampling_policy` · `setting_history`. 0013: `a2a_tasks.events`(상태 전이·send/sse/push/poll 이벤트). 새 env: `UVICORN_WORKERS` · `ANX_TARGET_VERSION` · `COSIGN_PUBLIC_KEY`(선택).

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C23-01 | 내 세션 2개 중 현재 = 토큰 sid · 다른 세션 종료 1 · 사용자 상세 · 강제 종료 사유 필수 → 이전 토큰 401 → 재로그인 200 · 내부 사용자 재동기화 `not_external` · 외부 사용자 클레임으로 역할 재계산 | `test_session_revoke_resync_and_my_sessions` | pytest |
| C23-02 | RBAC 사유 필수·admin 변경 400·미지 권한 400 · 덧붙이기 → 즉시 반영 → 원복 · 시뮬레이터(역할·IdP 그룹) · PAT 365일·Access Token 120초·교환 1800초 422 · 교환 시뮬레이터 ok/MSG-019 · MCP Auth 다시 탐색 | `test_rbac_matrix_simulator_and_ttl_caps` | pytest |
| C23-03 | 잘못된 정규식 400 · PII 시험 · 사용자 규칙 마스킹 + 원문 암호화 · 해제 사유 필수 → 원문(시크릿은 가림) → 30분 창 · 감사 · 권한 없음 403 · 이력·되돌리기 · 잘못된 시간대 400 · IP 정책 차단 · 액세스 토큰 5분 | `test_pii_rules_unmask_history_ip_policy` | pytest |
| C23-04 | 우회 점검 항목·지표 · `warn` 한도 → 허용 + `limit.warn` 알림 · 미리보기 would_block=false · 인자 warn 모드 → 허용 + 감사 · 소독 미리보기 | `test_bypass_limits_warn_args_and_sanitize` | pytest |
| C23-05 | 요청자에게 위임 거부 · 위임받은 개발자가 승인 · 시크릿 사용처·D-day · 유출 점검(값 미노출) · 장애 주입 → 후보 없음 · 401 시험 3종 · 탐지 이벤트 | `test_delegate_secret_leak_routing_and_auth_test` | pytest |
| C23-06 | 재시작 · 샘플링 deny 반영 · 로그 · OpenAPI 작업 선택·이름·위험도 → ToolRule high+승인 · 템플릿 include · 미지 include 400 · 집계 도구 검색 | `test_mcp_ops_openapi_selection_and_template` | pytest |
| C23-07 | 계측 · A2A 지표 · CSV(권한 403) · 드릴다운 team/target · trace 사용자 필터 · 개요 KPI | `test_observe_depth_and_overview` | pytest |
| C23-08 | 구간 검증 · 템플릿 · 미리보기 · CSV·MD 다운로드 · 팀 범위 0건 | `test_audit_range_verify_and_report_wizard` | pytest |
| C23-09 | 커넥터 편집(config 병합) · 업그레이드 계획 · SBOM 기준 → 차이 0 · 격리 복구 시험 ok / 변조 stage=signature · 예약 백업 keep · 경로 조작 거부 · 채널 URL 검증 · 알림 음소거·분류 · 구성요소 | `test_admin_upgrade_restore_test_backups_and_notify_prefs` | pytest |
| C23-10 | IdP 중지 → 그 IdP 사용자 세션 종료(이전 토큰 401) · 비활성 초안도 시험 로그인 · PATCH 역할 매핑 검증 | `test_idp_disable_ends_sessions_and_draft_test_login` | pytest |
| C23-11 | 내 IdP·클레임(`/me`) · 분류별 읽지 않음 · 공급자 기존 시크릿 참조(없으면 400) · OpenAPI URL 서버 가져오기(스킴 제한) · 템플릿 `include=none` | `test_me_claims_unread_provider_secret_ref_openapi_fetch` | pytest |
| C23-12 | MCP 세션 해시 핸들(원 ID 미노출)로 강제 종료 · 클라이언트 키 관리자 폐기(권한 403) · 가상 서버 충돌 거부 409 · 구간 p95 · FinOps 단가만 편집(그 외 403) · 공급자 허용 등급 밖 403 | `test_session_handle_client_key_collision_p95_price_and_provider_classes` | pytest |
| C23-13 | A2A Task 전이(submitted → input-required → canceled)·수신 경로 이벤트(send·cancel·push) · OAuth2 client_credentials 주입·캐시(발급 1회) | `test_a2a_task_events_and_oauth2_client_credentials` | pytest |
| C23-14 | RAG 색인 입력 소독 — 지시문 줄 제거·PII 마스킹 후 저장·임베딩, 소독 항목 응답 (3차 정합성 재검토에서 발견한 COMPLIANCE T1 공백) | `test_knowledge_ingest_is_sanitized` | pytest |
| C23-R | 복원 확인 없음 `confirm_required` · 복원 직전 스냅샷(`-prerestore`) · CLI `bundle import` 는 명시 동의로 확인 전달 · 감사 없는 우회 현황 GET · 결정 로그 기간·내보내기 감사 · 429 미리보기 exceeded·retry_after | `test_admin.py` · `test_cli.py` · `test_ops.py` | pytest |
| E23-01~12 | 개요 KPI·⌘K 범위·알림 수신 설정 · 구성요소·우회 점검·API 시험 호출·내 세션 · MCP KPI·샘플링·로그·Inspector 점검표 · A2A 범례·Task 전이·OAuth2 · 게이트웨이 점검·로그 JSONL·429 미리보기 · 인자 스키마·소독 미리보기·장애 주입·유출 점검 · 공급자 연결 시험·비교 4 · 사용자 상세·교환 시뮬레이터·MCP Auth 점검표 · 구간 검증·리포트 마법사·리플레이·마스킹 유형 · CSV·드릴다운·예산 · RBAC·업그레이드·PII·복구 · 런타임 오류 0 | `web/e2e/ops.spec.ts` | Playwright |
| P23-01~44 | prod: 0013 · 다른 세션 종료 · 강제 종료가 두 워커 모두 401 · RBAC 변경이 두 워커 모두 반영(5초 캐시) · 토큰 상한 · 교환 시뮬레이터 · PII 시험 · 마스킹 해제 · 설정 되돌리기 · IP 정책 · 우회 점검 · warn 미리보기 · 소독 · 장애 주입 · 401 · 위임 · 유출 점검 · 샘플링 · 계측·CSV·드릴다운·KPI · 구간 검증·리포트 · 업그레이드 계획 · 복구 시험·복원 확인 · 예약 백업 · 음소거 · 복제 수 2 | `smoke-prod.sh` | live |
| R | 전체 회귀 | logs | make |

## 환경 가정

- "현재 세션" 은 액세스 토큰의 `sid`(refresh token id) 로 판정한다 — `sid` 가 없는 토큰(PAT 등)은 가장 최근 세션.
- 강제 종료는 토큰 블랙리스트 대신 사용자별 `tokens_not_before` 로 한다(JWT `iat` 초 단위 비교). 다중 워커에서도 DB 기준이라 즉시 반영된다.
- RBAC·PII 사용자 규칙은 워커별 5초 캐시 — 변경은 최대 5초 뒤 모든 워커에 반영된다.
- 구성요소 재시작은 명령을 안내만 한다(콘솔이 컨테이너를 직접 제어하지 않음 — 호스트 프로세스 매니저 금지 원칙).
- Slack 알림은 `notify.channels.slack_webhook`(https 만) 로 warn/danger 만 보낸다. 에어갭이면 보내지 않는다.
- MCP 세션 ID 는 세션 자격처럼 쓰이므로 목록에는 SHA-256 앞 24자 핸들만 내보내고 강제 종료도 그 핸들로 찾는다.
- 공급자 `options.data_classes` 는 모델 정책을 좁히기만 한다(넓히지 않음). ZDR 은 OpenAI 공급자에 `store:false` 로 적용한다.
- 화면 진입 시 우회 현황은 `GET /gateway/bypass-check`(감사 없음), 「지금 점검」만 `POST`(감사 `gateway.bypass_check`).
- 401·403·429·5xx 카운터는 Prometheus 카운터(프로세스 시작 이후, 워커 합산)다 — 401 은 사용 기록에 남지 않는다.
- 남긴 공백(설계 요소, 화면은 비활성+사유): 라우팅 규칙 버전 게시(H02), hosted OpenAPI 서버 OBO(F04), 클라이언트 키 허용 별칭(H13), 구성요소별 헬스·재시작 횟수(B05), A2A `framework`·`/try` 파일 Part(G01·G07), 푸시 JWT 서명 검증(G07).
