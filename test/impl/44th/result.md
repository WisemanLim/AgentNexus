# impl 44th — 결과: A2A Task 소유권 (Epic 44 · D18-① · 보안)

판정: **PASS** — pytest C44-01~04 **4/4**(+ A2A·오케스트레이션 회귀 46/46) · prod 스모크 P44-01~07 **7/7**.

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest r1 | 4/4 | `logs/pytest-r1.log` |
| pytest r2 (게이트웨이 목록 검사를 엄격하게 + A2A·REST·gRPC·오케스트레이션) | **46/46** | `logs/pytest-r2.log` |
| prod 스모크 (PostgreSQL · 2 workers) | **7/7** | `logs/smoke-prod-r1.log` |

## prod 스모크

| # | 결과 |
|---|------|
| P44-01 | 플랫폼 `tasks/list`: B 의 목록에 A 의 Task 없음 · A 의 목록에는 있음 |
| P44-02 | B 의 `tasks/get` 두 번(두 워커) 모두 -32001 — 메모리에 없는 워커는 영속 행 주체로 판정 |
| P44-03 | B 의 REST `GET …/rest/tasks/{id}` 404 |
| P44-04 | B 의 `tasks/cancel` -32001 |
| P44-05 | B 의 gRPC `GetTask`(tenant `platform/tpl-qa`) `NOT_FOUND` |
| P44-06 | 게이트웨이 경유 원격 Task: B 의 get -32001 · B 의 목록에 없음 |
| P44-07 | A 는 플랫폼·게이트웨이 모두 자기 Task 조회 성공 |

## 영향

- 플랫폼 push 설정 순서 — 남의(또는 없는) Task 는 URL 검사보다 먼저 -32001. 43rd 스모크 P43-02(`taskId:"x"`)는 이제 -32001 이 맞다 → 회귀에서 자기 Task 로 바꾼 사본으로 확인(`test/impl/47th/logs/regress-43rd-adjusted/`, 8/8). pytest `test_push_url_guard` 도 같은 방식으로 갱신.
- 플랫폼 서버의 토큰별 주체 사전(쓰기만 하던 메모리 누수) 제거.
