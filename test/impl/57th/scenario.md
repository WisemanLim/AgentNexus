# impl 57th — 구성요소 단독 배포 정합성 (Epic 57 · D21-②)

## 범위

| 항목 | 구현 |
|------|------|
| 스케줄러 기동 | 구성요소 조합과 무관하게 시작(`SCHEDULER_INTERVAL_S > 0`) — 전에는 host·observe 가 없으면 시작하지 않아 gateway-only·registry-only 배포에서 승인 만료·보존·레지스트리 동기화가 돌지 않았다 |
| 작업 선택 | observe = 경보·SLO 판정 · host = 실행 복구 · admin = 예약 백업 · gateway 또는 host = 도구 승인 만료 · registry = 심사 만료·미러 동기화 · 언제나 = SLI 반영·보존 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C57-01 | 구성요소 조합 5종 — 해당 작업만 실행 | `test_components.py::test_tick_runs_component_jobs` | pytest |
| C57-02 | lifespan 이 구성요소 조건 없이 스케줄러 시작 | `test_scheduler_started_for_any_components` | pytest |
| L57-01 | 단독 기동(별도 SQLite·마이그레이션·시드) — gateway-only: `/mcp` 401·`/v1/catalog` 404·기한 지난 승인 expired · registry-only: 반대 라우트·승인은 그대로(게이트웨이 몫) · host+observe: 승인 expired | `logs/live-components.log` | live |
| 회귀 | 운영·SLO·관측·검토 수정 | 4개 파일 | pytest |
