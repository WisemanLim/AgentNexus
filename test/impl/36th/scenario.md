# impl 36th — A2A HTTP+JSON(REST) 바인딩 (Epic 36 · D16-① · FR-A03·G08 · NFR-INT1)

## 범위

| 항목 | 구현 |
|------|------|
| 경로 | 정규 a2a.proto(`lf.a2a.v1.A2AService`) google.api.http 기준, 인터페이스 URL 상대: `POST message:send` · `POST message:stream`(SSE) · `GET tasks` · `GET tasks/{id}`(historyLength) · `POST tasks/{id}:cancel` · `GET|POST tasks/{id}:subscribe` · `POST tasks/{id}/pushNotificationConfigs` · `GET …/{configId}` · `GET extendedAgentCard`. 목록·삭제 푸시 설정은 400 UNSUPPORTED_OPERATION(게이트웨이가 푸시를 대신 등록) |
| 수신 | 게이트웨이 `/a2a/{agent}/rest/…` · 플랫폼 `/a2a/platform[/{profile}]/rest/…` · 데모 에이전트 `/rest/…` — REST → 1.0 JSON-RPC (메서드, params) → **같은 처리 경로**(인증·정책·가드·감사·Task 영속·버전 변환) → 봉투 없는 결과(`application/a2a+json`) |
| 오류 | `google.rpc.Status` {code, status, message, details[ErrorInfo{reason, domain}]} — A2A 오류 §5.4 표(404 TASK_NOT_FOUND · 400 TASK_NOT_CANCELABLE …, domain `a2a-protocol.org`) · 게이트웨이 정책 거부는 HTTP 상태(403 PERMISSION_DENIED 등)·reason = 사유 코드, domain `agentnexus` · 인증 401 UNAUTHENTICATED · 429 는 Retry-After |
| 스트림 | SSE `data:` 에 StreamResponse 객체만(task·message·statusUpdate·artifactUpdate), 오류는 `event: error` |
| 카드 | 플랫폼·게이트웨이 경유·데모 카드 `supportedInterfaces` 에 `HTTP+JSON` 1.0 (`…/rest`) |
| 원격 호출 | 카드에 JSONRPC 가 없고 HTTP+JSON 만 있으면 REST 로 호출(`A2A-Version: 1.0`, `application/a2a+json`) — JSON-RPC 0.3·1.0 클라이언트와 상호 변환, REST 오류 reason → A2A 코드, 스트림 변환 · Task 이벤트 `protocol.upstream_binding`/`client_binding` |
| 화면 | 원격 에이전트 목록·요약에 바인딩 표시 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C36-01 | send(봉투 없음·a2a+json)·get(historyLength)·cancel 400 TASK_NOT_CANCELABLE·없는 Task 404·미지원 400·잘못된 본문 400·알 수 없는 경로 404·익명 401 · 이벤트 client_binding · 감사 a2a.send | `test_gateway_rest_unary` | pytest |
| C36-02 | REST 스트림 — 봉투 없음, 첫 Task, 마지막 완료 | `test_gateway_rest_stream` | pytest |
| C36-03 | 차단 등급 → 403 PERMISSION_DENIED · reason = JSON-RPC 사유 코드 | `test_gateway_rest_policy_parity` | pytest |
| C36-04 | 플랫폼 REST send·get · 비노출 404 · 카드 HTTP+JSON 인터페이스 | `test_platform_rest_and_cards` | pytest |
| C36-05 | REST 전용 원격 에이전트 — 0.3 send·get·1.0 GetTask 404→-32001·스트림 · upstream_binding | `test_outbound_rest_only_agent` | pytest |
| R | A2A·1.0·오케스트레이션·재개 시험 회귀 없음 | `test_a2a*`·`test_orchestration`·`test_reliability` | pytest |
| P36-01~10 | prod(2 workers): REST send·get·cancel 오류·404·스트림·플랫폼·카드·익명 401·JSON-RPC 회귀·감사 체인 | `smoke-prod.sh` | live |

## 환경 가정

- `tenant` 경로 변형(`/{tenant}/…`)은 받지 않는다(단일 테넌트 게이트웨이).
- REST 는 1.0 전용 — 0.3 의 `/v1/…` REST 표기는 범위 밖.
