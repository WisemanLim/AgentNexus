# impl 63rd — 한도 예약 정밀화 (Epic 63 · D22-③)

## 범위 (3차 검토 Lv2 M9·L8·L9·L35 + 구현 중 발견한 예약 누수)

| 항목 | 내용 |
|------|------|
| 분 슬롯 | 토큰 예약 = `(tok, 키, 토큰 수, 분 슬롯)`. 반환·미사용 반환은 예약한 분에만 — 지난 분이면 아무것도 안 함(전에는 다음 분 창을 음수에서 시작해 그 분 한도가 늘었다). 더 쓴 만큼은 지금 창 |
| 출력 예약 | `max_tokens`(없으면 256)도 예약 — 이 요청의 통과는 입력까지만으로 판단, 다른 요청에는 예약 전체가 보임 · 정산 때 안 쓴 만큼 반환 · 예산 예상 비용도 같은 출력 추정 |
| 진행 중 상한 | 주체별 동시 모델 요청 `MODEL_MAX_INFLIGHT_PER_SUBJECT`(32, 0 = 끔) — 보유자 집합(고정 만료 300초), finalize·discard 에서 반납 |
| Valkey 시계 | 보유자·예약 금액 Lua 가 `redis.call('TIME')` — 워커·파드 시계 차이 무관. `holder_count` 도 Lua |
| 예약 누수 (발견) | `prepare` 뒤 `finalize` 가 없는 경로 — 라우팅 시험(`/gateway/routing/test`)·샘플링 동의 미리 보기·샘플링 실행 실패·임베딩 대상 없음 — 가 토큰·예산 예약(D20 부터)과 진행 중 슬롯을 남김 → `llm.discard(plan)` |
| 예산 현황 | `/api/v1/gateway/limits/status` 가 `*`·per-subject·`any` 한도도 이 기간 사용 있는 버킷(상위 20)별로 — `bucket` = `team:…`/`user:…` (`any` 는 팀, 팀 없으면 사용자) |
| 배포 | env 3곳 · Helm `config.modelMaxInflightPerSubject` · README env 행 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C63-01 | 같은 분 미사용 반환 · 다음 분 반환·정산은 0 유지 · 초과분은 지금 창 | `test_limit_precision.py::test_release_only_into_reserved_slot` | pytest |
| C63-02 | 출력 950 예약한 첫 요청 통과·두 번째 거절 · 정산 300 → 창 400 · `max_tokens` 없으면 +256, 있으면 그 값 | `test_output_tokens_reserved` | pytest |
| C63-03 | 상한 2 → 세 번째 429 · discard·finalize(실패) 뒤 0 | `test_inflight_cap_per_subject` | pytest |
| C63-04 | 워커 시계 +10000초에도 보유자·예약 유지, 2초 TTL 뒤 0 (Valkey 있을 때) | `test_holder_expiry_uses_valkey_time` | pytest (local Valkey) |
| C63-05 | 라우팅 시험 3회 뒤 토큰 창 0·진행 중 0 | `test_dry_runs_leave_no_holds` | pytest |
| C63-06 | `any`·`*` 한도 현황에 `team:team-e63`·`user:usr_e63_solo`, 팀 있는 사용자는 사용자 버킷 없음 | `test_budget_status_buckets` | pytest |
| 회귀 | 52nd 예약·59th 정산·모델·동의·운영 | `test_model_gateway2.py`·`test_review3_fixes.py`·`test_llm.py`·`test_gateway_policy.py`·`test_mcp_consent.py`·`test_ops.py`·`test_review2_fixes.py`·`test_deploy.py` | pytest |
