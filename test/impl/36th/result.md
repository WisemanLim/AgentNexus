# impl 36th — 결과: A2A HTTP+JSON 바인딩 (Epic 36 · D16-①)

판정: **PASS**

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest r1 (`test/test_a2a_rest.py`) | 4/5 — C36-03 정책 동일성 실패 | `logs/pytest-r1.log` |
| pytest r2 (A2A 스위트 전체) | **50/50** | `logs/pytest-r2.log` |
| prod 스모크 r1 | 9/10 — P36-09 FAIL(`input-required`) | `logs/smoke-prod-r1.log` |
| prod 스모크 r2 (PostgreSQL · 2 workers) | **10/10** | `logs/smoke-prod-r2.log` |

## 회차 원인

- pytest r1 — 게이트웨이 정책 오류(-32003, HTTP 403 + `data.code`)가 A2A 1.0 의 같은 코드(PushNotificationNotSupported)로 매핑돼 400 이 됨 → 제품 수정: HTTP 상태와 `data.code` 를 가진 게이트웨이 정책 오류는 HTTP 매핑(도메인 `agentnexus`)을 쓴다 (`app/services/a2a_rest.py` `error_body`).
- 스모크 r1 — 스크립트 결함: JSON-RPC 회귀 요청 문구 "분석" 에는 서비스 이름이 없어 데모 에이전트가 정상적으로 `input-required`(어느 서비스?)를 돌려줌 → 문구를 "orders-api 장애 분석" 으로(P36-01 과 같게). 제품 변경 없음.

## prod 스모크 r2

| # | 결과 |
|---|------|
| P36-01 | REST `message:send` → `TASK_STATE_COMPLETED`, `Content-Type: application/a2a+json` |
| P36-02 | `GET tasks/{id}` 같은 id |
| P36-03 | `tasks/{id}:cancel`(완료 Task) → 400 `TASK_NOT_CANCELABLE@a2a-protocol.org` |
| P36-04 | 없는 Task 404 |
| P36-05 | `message:stream` SSE — JSON-RPC 봉투 없음, 마지막 `TASK_STATE_COMPLETED` |
| P36-06 | 플랫폼 프로파일 REST send 완료 |
| P36-07 | 플랫폼·게이트웨이 카드에 `HTTP+JSON` 인터페이스(`…/rest`) |
| P36-08 | 익명 → 401 `UNAUTHENTICATED` |
| P36-09 | JSON-RPC `message/send` 회귀 `completed` |
| P36-10 | 감사 체인 무결 |

전체 회귀는 7차 완료 게이트(`test/impl/39th/result.md`)에 함께 기록한다.
