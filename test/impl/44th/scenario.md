# impl 44th — A2A Task 소유권 (Epic 44 · D18-① · 보안)

## 발견 (라운드 준비 중 코드 확인)

플랫폼 A2A 서버(`/a2a/platform[/{profile}]`, JSON-RPC·REST·gRPC 위임 공통)는 한 인스턴스를 모든 사용자가 공유하는데
`tasks/list` 가 메모리의 최근 50개 Task 를 **주체와 무관하게** 돌려주었다(대화 이력 포함). `get`·`cancel`·`resubscribe`·푸시 설정·
`taskId` 이어 보내기도 소유자를 확인하지 않아, 목록으로 얻은 id 로 남의 Task 를 읽고 취소하거나 입력을 끼워 넣을 수 있었다(OWASP A01).
게이트웨이 경유 원격 Task 도 게이트웨이가 주체(`A2ATask.principal_sub`)를 기록하면서 확인하지 않았다. 플랫폼 서버의 토큰별 주체 사전은
쓰기만 하고 읽지 않아 토큰마다 늘었다.

## 범위

| 항목 | 구현 |
|------|------|
| A2AServer | `owner(auth)`·`owner_lookup(tid)` 훅 — 새 Task 에 주체 기록, `list` 는 내 Task 만, `get`·`cancel`·`resubscribe`·`push_get`·`push_set`·이어 보내기는 남의 Task 면 `TaskNotFound(-32001)`(존재 비노출). 메모리에 없는 Task 는 영속 행의 주체로 · 상한 제거 시 주체 기록도 함께. 훅이 없는 서버(데모 원격 에이전트)는 그대로 |
| 플랫폼 서버 | `owner = principal.sub` · `owner_lookup = A2ATask("platform:{tid}").principal_sub` · 토큰별 주체 사전 제거 |
| 게이트웨이 경유 | `get`·`cancel`·`resubscribe`·푸시·이어 보내기 대상 Task 의 기록 주체가 다르면 -32001 · 원격 `tasks/list` 결과는 내 Task 만 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C44-01 | 플랫폼: 남의 Task list 에 없음 · get·cancel·GetTask(1.0)·push set/get·이어 보내기·resubscribe -32001 · REST 404 · 주인 get 성공 | `test_task_ownership.py::test_platform_task_owner_only` | pytest |
| C44-02 | 메모리에서 내려간 Task 도 영속 주체로 판정 | `test_platform_owner_from_store` | pytest |
| C44-03 | 게이트웨이 경유: 다른 주체 get·cancel -32001 · 목록에 남의 Task 없음 · 내 목록엔 있음 | `test_gateway_task_owner_only` | pytest |
| C44-04 | owner 훅 없는 서버 동작 불변 | `test_server_without_owner_unchanged` | pytest |
| 회귀 | A2A(0.3·1.0·REST·gRPC)·오케스트레이션 | `test_a2a*.py`·`test_orchestration.py` | pytest |
| P44-01~07 | prod(2 workers): 플랫폼 목록·JSON-RPC get·REST·gRPC(다른 워커 포함)·게이트웨이 get·목록 · 주인 get | `smoke-prod.sh` | live |
