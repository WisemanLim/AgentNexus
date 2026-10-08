# impl 14th — MCP 서버→클라이언트 요청 (Epic 14 · FR-A00·A03·G09 · SCR-C08)

## 범위

- 게이트웨이 업스트림 호출을 SSE 스트리밍으로 읽고, 도중의 서버→클라이언트 요청(sampling/createMessage · elicitation/create ·
  roots/list · ping, MCP 2025-11-25 역채널)에 답한다. 업스트림 initialize 에 `sampling`·`elicitation` 능력 선언.
- **중계**: 하류 클라이언트가 initialize 에서 능력을 선언하고 SSE 로 호출하면 같은 스트림으로 요청을 전달, 응답 POST 를 받아 업스트림에
  돌려준다(워커 간 공유 = `mcp_relays` 테이블).
- **Host 동의**: 그 외에는 요청자 동의(승인 kind `sampling`·`elicitation`) — 프롬프트 편집, 게이트웨이 모델 라우팅(내부 데이터 → 로컬),
  응답 검토 후 전송, 60초(MCP_CONSENT_TIMEOUT_S) 무응답 자동 거부, 감사(`mcp.sampling`·`mcp.relay`·`approval.consent`).
  오케스트레이션 실행 중이면 run 이벤트(`sampling.required/review/resolved`·`elicitation.required/resolved`)로 채팅에 카드 표시.
- 입력창 `/` 메뉴: 프로파일이 쓰는 서버의 resources/list·prompts/list → 리소스 첨부 · 프롬프트 인자 입력 후 삽입.
- 데모 서버 `internal-orders`(stateful·SSE) · 데모 프로파일 `demo-orders` · 마이그레이션 0006.

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C14-01 | sampling 동의 — 라우팅 미리보기(domain-local)·타인 비노출·편집 프롬프트로 실행·사용자 사용량 집계·감사 | `test_sampling_consent_edit_then_allow_routes_to_local_model` | pytest |
| C14-02 | 거부 → 도구 오류 · 무응답 자동 거부(expired) | `test_sampling_deny_and_timeout_return_tool_error` | pytest |
| C14-03 | 응답 검토 → 수정 후 전송 | `test_sampling_review_before_send` | pytest |
| C14-04 | elicitation — 스키마 위반 400 · 일반 승인 차단 · 수락/거절 | `test_elicitation_accept_validates_schema_and_decline` | pytest |
| C14-05 | 하류 중계 — SSE 로 요청 전달 → 응답 POST 202 · 엉뚱한 id 404 · Host 동의 미생성 | `test_downstream_client_with_sampling_capability_gets_relayed_request` | pytest |
| C14-06 | 오케스트레이션 중 동의 카드 이벤트 → 동의 → 실행 완료 | `test_orchestrated_run_pauses_for_consent_cards` | pytest |
| C14-07 | `/` 메뉴 목록·리소스 읽기·프롬프트 펼치기·프로파일 밖 서버 거부 | `test_slash_menu_resources_and_prompts` | pytest |
| C14-08 | 동의 채널 없는 시스템 호출 거절 · ping·roots | `test_server_requests_without_consent_channel` | pytest |
| C14-09 | 동의 상태 리듀서 | `src/test/runState.test.ts` | Vitest |
| E14-01 | 브라우저: 샘플링 카드 → 허용 → 응답 검토·수정 → 추가 입력 카드 → 실행 완료 | Playwright | 브라우저 |
| E14-02 | 브라우저: `/` 메뉴 리소스 첨부 · 프롬프트 필수 인자 → 삽입 | Playwright | 브라우저 |
| S14-01~12 | live: 컨테이너 간 동의 왕복 · 공식 fastmcp Client 중계(sampling·elicitation) · 감사 체인 | `smoke.sh` · `relay_client.py` | live |
| R | 전체 회귀 SQLite·PG·Vitest·빌드·Playwright·helm-check | logs | make |

## 환경 가정

- httpx `ASGITransport` 는 응답을 모은 뒤 돌려주므로 in-process 시험의 데모 서버 마운트를 `StreamingASGITransport`(신규)로 바꿨다.
- MCP 2026-07-28 의 MRTR(`InputRequiredResult`)은 D7 버전 고정(2025-11-25)으로 범위 밖. SDK 는 sampling 을 2026-07-28 부터 deprecated 로 경고한다.
