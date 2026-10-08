# impl 17th — 보안·정합성 결함 (Epic 17 · FR-G02·G04·L03·I04 · NFR-SEC1·SEC2·PRV1 · PRD D12-①)

## 범위 (2026-10-06 PRD·UI 설계서 대조 감사에서 확인된 결함)

| 결함 | 수정 |
|------|------|
| 도구 단위 한도(`target_type=tool`·`any`)가 MCP 호출에 적용되지 않음 (CONTRARY) | `mcp_gateway.call_tool` 이 허용된 호출에 `ratelimit.check(kind="tool", target="<서버>.<도구>")` — 초과 시 JSON-RPC -32005 + HTTP 429 + `Retry-After`, 감사 `gateway.tool.limit` |
| 워크로드·Access Token 스코프 미적용, admin PAT 는 스코프 무시 | `Principal.has` = 역할 권한 ∩ 스코프 (`gateway:invoke` → chat:use·models:read·catalog:read 묶음). 워크로드·Access Token 은 스코프 없으면 기본 거부 |
| A2A 경로에 입출력 가드 없음, 외부 에이전트로 PII 전송 | 송신 텍스트 `scan_input`(인젝션 차단 설정 시 403 `input_blocked`, PII 마스킹)·data 파트 마스킹 / 수신 Task·Message·Artifact·스트림 이벤트를 도구 응답과 같은 소독 규칙으로 정리. 감사 `gateway.a2a.guard` |
| trace 스팬 속성 PII 비마스킹 (OTLP 수신 포함) | `tracing.span()` 속성 설정 시 + `store_spans` 저장 전 마스킹 (외부 OTLP 내보내기에도 마스킹된 값) |
| 에어갭 프로파일이 존재하지 않는 `ollama` 서비스 참조 | `compose/ollama.yml`(profile `ollama`, `ollama/ollama:0.31.1`, 모델 볼륨·헬스체크), 배포 프로파일 명령에 `COMPOSE_PROFILES=demo,ollama` |
| OWASP Agentic AI 위협·개인정보보호법 매핑 문서 없음 (NFR-SEC2·PRV1) | COMPLIANCE.md 에 T1~T15 매핑·조항별 매핑 (기존 "유출통지 §32-2" 오기 → §34) |
| 삭제·폐기 확인(MSG-002)·거부 사유(MSG-003) 누락 | I05 폐기·가상 서버 삭제·소독 규칙 삭제(+권한 Gate)·C04 거부 확인, H12·E02·E05 거부 사유 필수(API `reason_required`) |
| F02 도구 허용 토글이 인자 스키마·소독 설정을 지움 | 서버 상세 rule 에 args_schema·sanitize·description 포함 + 콘솔이 전체 규칙을 PUT |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C17-01 | 도구 한도 초과 429·-32005·Retry-After·limit_id, 다른 주체 무영향, 감사 | `test_tool_rate_limit_enforced_on_mcp_calls` | pytest |
| C17-02 | `any` 대상 한도가 도구 호출에도 적용 | `test_any_target_limit_covers_tools` | pytest |
| C17-03 | Access Token·워크로드: 스코프 밖 403, `gateway:invoke` 로 게이트웨이 호출, 스코프 없음 기본 거부 | `test_access_token_and_workload_scopes_enforced` | pytest |
| C17-04 | admin PAT 라도 스코프 밖 403, 스코프 없는 PAT 는 역할 권한 | `test_admin_pat_scopes_are_not_bypassed` | pytest |
| C17-05 | A2A 송신 PII(텍스트·data) 마스킹, 수신 지시문·시크릿·PII 소독, 감사 양방향 | `test_a2a_outbound_pii_masked_and_inbound_sanitized` | pytest |
| C17-06 | 인젝션 차단 설정 시 원격 에이전트로 나가지 않고 403 | `test_a2a_outbound_injection_blocked_when_configured` | pytest |
| C17-07 | 자체 스팬·OTLP 수신 스팬 속성 마스킹 | `test_span_attributes_masked_in_store_and_export` | pytest |
| C17-08 | 승인자 대기 거부·카탈로그 거부는 사유 필수 | `test_reject_requires_reason` | pytest |
| C17-09 | F02 토글이 인자 스키마·소독 보존 | `test_tool_toggle_keeps_args_schema` | pytest |
| C17-10 | ollama 서비스가 local·prod compose 구성에 유효 | `docker compose config --services` | live |
| C17-11 | 매핑 문서 T1~T15·PIPA 조항 존재 | grep | 문서 |
| E17-01~03 | I05 폐기 확인·H11 삭제 확인·E05 거부 사유 필수(취소 시 대기 유지) | `web/e2e/hardening.spec.ts` | Playwright |
| R | 전체 회귀 SQLite·PG·Vitest·빌드·Playwright 전체 | logs | make |

## 환경 가정

- 원격 에이전트 가드는 시험 안 가짜 A2A 에이전트(`http://guarded.test`, in-process mount)로 송신 내용·수신 응답을 확인한다.
- ollama 이미지는 compose 구성 검증만 한다(모델 가중치 다운로드·기동은 하지 않음 — 네트워크 설치는 이미지 빌드 범위만).
