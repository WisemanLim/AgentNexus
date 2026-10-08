# impl 52nd — 모델 게이트웨이 정확성 (Epic 52 · D20-①)

## 범위

| 항목 | 구현 |
|------|------|
| 라우팅 규칙 시간 | `RoutingRule.timeout_ms` 를 시도마다 적용(`asyncio.wait_for`) — 시간 초과는 504 공급자 오류로 재시도·다음 후보. 스트리밍은 첫 청크까지. 전체 상한 = 시간 × 시도 수(후보 × (재시도+1)). 규칙이 없으면 공급자 기본 시간 |
| 예약 | 토큰 창은 호출 전 예상 토큰을 원자적으로 더하고 넘으면 되돌림, 예산은 진행 중 예상 비용(`usdhold` 카운터, 마이크로 USD)을 함께 계산 — 동시 요청이 함께 한도를 넘지 못한다. `finalize` 에서 정산(토큰은 더 쓴 만큼 추가, 예산 예약 반환 + 이 워커 spend 캐시 비움), 실패면 반환. 정산이 없으면(워커 사망) 예산 예약은 300초 뒤 풀림 |
| `any` 예산 | 주체의 팀, 팀이 없으면 그 사용자 사용액(전에는 빈 팀 = 항상 0) |
| 클라이언트 키 예산 | `usage_records.client_key`(migration 0020) — `scope_type=client_key` 예산이 키별 사용액으로 판정 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C52-01 | 느린 후보는 규칙 시간에서 끊고 다음 후보 성공 · 전부 느리면 4 시도 모두 504 로 1.5초 안에 실패 · 규칙 없으면 기다림 | `test_model_gateway2.py::test_rule_timeout_per_attempt_and_deadline` | pytest |
| C52-02 | 토큰 100/분에 진행 중 60 + 새 60 → 거절 · 정산(90) 뒤 20 거절·10 허용 · 예산 1.0 에 진행 중 0.6 + 0.6 거절 · 반환 뒤 허용 · 실제 비용 반영 뒤 0.5 거절·0.3 허용 | `test_concurrent_reservations` | pytest |
| C52-03 | 팀 없는 주체도 `any` 예산 초과 시 거절 | `test_any_budget_applies_without_team` | pytest |
| C52-04 | 사용 기록에 키 id · 키 예산 초과 키만 거절, 다른 키는 허용 | `test_client_key_budget` | pytest |
| 회귀 | 모델 API·한도·정책·검토 수정·설계 보완·성능·샘플링 | `test_llm.py`·`test_gateway_policy.py`·`test_review2_fixes.py`·`test_design_gaps.py`·`test_perf.py`·`test_mcp_consent.py` | pytest |
| P52 | prod(PostgreSQL·2 workers·Valkey) — 마이그레이션 0020 · 예산 예약이 워커 간 공유(Valkey 카운터) | 완료 게이트 리허설 | live |
