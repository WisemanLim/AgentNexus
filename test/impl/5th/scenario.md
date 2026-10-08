# 시나리오 / Scenario — impl 5th · Epic 5 Unified Model API (FR-G06·G01·G04·G05, FR-L00~L02)

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E5-01 | 모델 목록·카탈로그 노출 | `/v1/models`, `/v1/catalog?type=model` | 활성 별칭 + `auto` 라우트, 비활성 공급자 모델 제외, 카탈로그 active | P0 |
| E5-02 | 기본 호출·사용량·비용 (FR-O04 연계) | public · fast-commercial | 200, `X-AgentNexus-Model`, UsageRecord 비용 = 토큰×단가(±1e-6) | P0 |
| E5-03 | 데이터 분류 라우팅 (FR-G03·L02) | internal · smart-commercial | domain-local 로 재라우팅, fallback 헤더·결정 로그 | P0 |
| E5-04 | auto 비용 전략 (FR-G01) | model=auto | 저가 모델 우선 | P1 |
| E5-05 | 공급자 장애 폴백·재시도·서킷 (FR-G01 수용) | 실패 mock 공급자 + fallbacks | 재시도 2회 후 폴백 성공, 서킷 오픈 시 후순위, 전원 실패 502 + attempts | P0 |
| E5-06 | 스트리밍 SSE (FR-A01·G06) | stream=true / 첫 청크 전 실패 | delta 누적 = 응답, `[DONE]`, usage 청크, 사용량 기록, 첫 청크 전 폴백 | P0 |
| E5-07 | 도구 호출·구조화 출력 정규화 | tools+tool_choice / json_schema | tool_calls 형식, 스키마 필수 키 JSON | P0 |
| E5-08 | 설정만으로 모델 교체 (FR-G06 수용) | 별칭 domain-local 의 provider·upstream 변경 | 클라이언트 요청 동일·응답 공급자 교체 | P0 |
| E5-09 | 입력 가드 (FR-G02) | PII 포함 / 인젝션(block) | 업스트림 전 마스킹, 400 input_blocked | P0 |
| E5-10 | 한도 429 · 예산 저비용 폴백 (FR-G04, 시나리오 4) | rps / 팀 예산 fallback | 429 + Retry-After, domain-local 폴백 | P0 |
| E5-11 | 정책 거부(재라우팅 없음) | role=user deny smart | 403 model_denied | P1 |
| E5-12 | 게이트웨이 클라이언트 키 (FR-W01, SCR-H13) | anx_key_ | 모델 호출 OK, 관리 API 403, 사용 시각 기록 | P0 |
| E5-13 | 공급자 연결 시험 (SCR-A06) | mock / 실패 mock | ok true / false | P1 |
| E5-14 | OpenAI 호환 어댑터 | MockTransport | Bearer 키·업스트림 모델명 전달, include_usage 스트림 | P0 |
| E5-15 | Anthropic 변환 (FR-L01) | system·tools·tool_result·SSE | Messages 형식 변환, tool_use→tool_calls, 스트림 텍스트·인자·usage 복원 | P0 |
| E5-16 | 공식 openai SDK 호환 (NFR-INT1) | openai 3.24 클라이언트 | models.list·create·stream·tools, 404/401 예외 매핑 | P0 |
| E5-17 | PostgreSQL 회귀 | `make test-pg` | PASS | P0 |
| E5-18 | live curl 스모크 | `smoke.sh` | 9/9 | P0 |
