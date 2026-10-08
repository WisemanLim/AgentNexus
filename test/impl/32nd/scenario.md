# impl 32nd — MCP 2026-07-28 리소스 구독·Tasks 확장 (Epic 32 · D15-① · NFR-INT1 · FR-G09)

## 범위

| 항목 | 구현 |
|------|------|
| 리소스 구독 | `subscriptions/listen` `notifications.resourceSubscriptions` (최대 20) — 승인 시점에 `resources/read` 가 되는 URI 만 승인(ack 에 부분집합) · 5초마다 내용 지문 비교 → `notifications/resources/updated {uri, _meta.subscriptionId}` · discover `resources.subscribe: true` · 데모 `dynatrace://live/{service}`(10초마다 바뀌는 실시간 지표) |
| Tasks 확장 | `io.modelcontextprotocol/tasks` (SEP-2663) — 요청 `_meta.clientCapabilities.extensions` 에 선언한 `tools/call` 만. 게이트웨이가 작업을 직접 실행(업스트림 세대 무관, 정책·승인·토큰 교환 동일). 작업 행을 먼저 저장 → 2초 안에 끝나면 일반 결과(작업 행 삭제), 아니면·입력이 필요하면 `CreateTaskResult {resultType:"task", taskId, status, createdAt, lastUpdatedAt, ttlMs, pollIntervalMs}` (migration 0017 `mcp_tasks`) |
| 작업 메서드 | `tasks/get` → DetailedTask(working · input_required+`inputRequests` · completed+`result` · failed+`error` · cancelled) · `tasks/update {inputResponses}` (대기 중 키만, mcp_relays 로 워커 간) · `tasks/cancel` (협조적 — 실행 워커가 2초 생존 신호 때 확인) · `Mcp-Name` = taskId · 확장 미선언 -32021 · 남의·만료·모르는 작업 -32602 · `tasks/list` 없음(사양) |
| 입력 경로 | 클라이언트가 elicitation·sampling 능력을 선언하면 inputRequests 로, 아니면 기존 Host 동의 카드(작업은 working) |
| 알림 | listen `taskIds` — 이 주체의 작업만 승인 · DetailedTask 가 바뀌면 `notifications/tasks` (subscriptionId 태그) |
| 견고성 | taskId = `task_` + 192비트 · TTL 1시간 뒤 조회 시 삭제 · 생존 신호 30초 끊긴 working 은 failed(워커 중단) |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C32-01 | 읽을 수 있는 URI 만 승인 · resources/updated(uri·subscriptionId) · 잘못된 필터 -32602 · discover 능력 | `test_resource_subscription_updates` | pytest |
| C32-02 | 빠른 호출은 확장 선언에도 일반 결과 · 작업 행 없음 | `test_fast_call_returns_inline` | pytest |
| C32-03 | 작업 생성 · input_required(elicitation 스키마) · 남의 작업 -32602 · 미선언 -32021 · Mcp-Name 불일치 -32020 · update(모르는 키 무시) → completed(result) | `test_task_input_required_update_complete` | pytest |
| C32-04 | 취소 → cancelled · 재취소 무시 | `test_task_cancel` | pytest |
| C32-05 | 능력 없는 클라이언트 → Host 동의 카드 · working 유지 → 완료 | `test_task_with_host_consent` | pytest |
| C32-06 | listen taskIds 내 작업만 승인 · notifications/tasks(완료·result) | `test_listen_task_status` | pytest |
| C32-07 | 만료 -32602·삭제 · 생존 신호 끊김 → failed | `test_task_expiry_and_stale_worker` | pytest |
| R | 29th·27th·동의 시험 회귀 (discover `subscribe` 는 D15 로 true) | pytest | pytest |
| P32-01~11 | prod(2 workers): 능력 · 리소스 구독 · 빠른 호출 · 작업 생성·입력·update·완료(워커 간) · 다른 주체 · 취소 · 감사 체인 | `smoke-prod.sh` | live |

## 환경 가정

- Tasks 는 2026-07-28 하류 전용(확장 선언 필요) — 레거시 2025-11-25 의 `task` 매개변수 방식은 범위 밖.
- 작업은 만든 워커에서 실행된다. 그 워커가 죽으면 작업은 failed 로 보이고 재실행하지 않는다(쓰기 도구 중복 실행 방지).
- 리소스 구독은 폴링(5초) 지문 비교 — 업스트림 `resources/subscribe` 를 쓰지 않는다.
