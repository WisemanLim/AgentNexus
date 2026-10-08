# 시나리오 / Scenario — impl 4th · Epic 4 게이트웨이 정책 코어 (FR-G02·G03·G04·G07)

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E4-01 | 데이터 분류별 모델 허용 (FR-G03) | 시뮬레이터: internal×commercial / public / local | 차단+reroute domain-local / 허용 / 허용 | P0 |
| E4-02 | 정책 CRUD·버전·주체 매칭 | role=finops deny jira.* | dev 403, finops 거부·developer 허용, v1→v2 이력, 감사 | P0 |
| E4-03 | 인자 제한 (어떤 인자로) | args_schema limit≤10 | 5 허용, 50 "인자 제한 위반" | P0 |
| E4-04 | 요청·토큰 한도·피해 반경 격리 (FR-G04) | rps 1 burst 2, tpm 100 | 3번째 거부(retry_after), 토큰 초과 거부, 타 사용자 무영향 | P0 |
| E4-05 | 예산 알림·초과 폴백·차단 | 팀 월 $5, 80% 알림, fallback→block | 90% 알림, 초과 시 domain-local 폴백, block 전환 시 차단 | P0 |
| E4-06 | 입력 가드 (FR-G02·NFR-PRV1) | RRN·전화·메일·카드(Luhn)·API 키 / 인젝션 | 마스킹, Luhn 불일치 미마스킹, 인젝션 flag→block, PII block | P0 |
| E4-07 | 도구 응답 소독 (간접 인젝션) | 지시문·토큰·PII 포함 응답, block 규칙 | 지시문 제거, 시크릿·PII 마스킹, 규칙 차단 | P0 |
| E4-08 | 시크릿 저장소 — 키 유출 0건 | 생성·회전·목록·감사 | 평문 응답·감사 미포함, 내부 복호화만 | P0 |
| E4-09 | 기본 규칙 위험도 분류 | readOnly/destructive/이름 | search low, create_page·purge high+승인 | P1 |
| E4-10 | 도구 판정 + 요청자 승인 | 미등록·비활성·인자 위반·허용·승인 필요→승인→사용 | 각 code 정확, 승인 1회성 | P0 |
| E4-11 | 직무 분리·팀 간 누출 차단 | approver=approvers | 요청자 본인 승인 403, 타팀 조회 404, 보안 승인, 재처리 거부, 알림 | P0 |
| E4-12 | 차단목록·프로파일 허용목록·팀 정책 | allowed=false / profile.tools / team deny | tool_denied / agent_not_allowed / policy_denied | P0 |
| E4-13 | 원격 에이전트 신뢰 등급 | trusted / untrusted / blocked / 프로파일 | 허용 / 명시 정책 필요 / 차단 / 허용목록 외 거부 | P0 |
| E4-14 | OPA 어댑터·fail-closed | MockTransport / 503 | rego 결정 반영, 장애 시 거부 | P0 |
| E4-15 | PostgreSQL 회귀 | `make test-pg` | PASS | P0 |
| E4-16 | live: Valkey 토큰 버킷 · OPA Rego 실평가 · API | `smoke.sh` (COMPOSE_PROFILES=opa) | 7/7 | P0 |
