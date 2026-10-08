# impl 61st — 데이터 계층 견고성 (Epic 61 · D22-①)

## 범위

D21 ⑤ 에 적었으나 60th 에서 빠진 DB 세션 상한 + 3차 검토 Lv2 M10·L13(개요 KPI 인덱스·표본 정렬).

| 항목 | 내용 |
|------|------|
| PostgreSQL 세션 상한 | 연결 옵션 `-c statement_timeout -c lock_timeout -c idle_in_transaction_session_timeout` — env `DB_STATEMENT_TIMEOUT_MS`(120000)·`DB_LOCK_TIMEOUT_MS`(15000)·`DB_IDLE_TX_TIMEOUT_MS`(0 = 끔: 요청 세션이 스트림·원격 대기 동안 열려 있을 수 있음) |
| 마이그레이션 | `migrations/env.py` 가 `SET LOCAL statement_timeout = 0`·`lock_timeout = 0` 뒤 advisory lock — 큰 테이블 인덱스·다른 파드 대기 |
| SQLite | 이미 `timeout=30`(busy handler 30초) — 확인만 |
| 인덱스 | migration 0021: `spans(parent_id, trace_id)`·`run_events(type, ts)` (모델 `__table_args__` 같이) |
| 표본 | 개요 게이트웨이 추가 지연 표본 = 최근 500 (`ORDER BY start_time DESC`) |
| 배포 | `.env.local`·`.env.prod`·`.env.example` 3곳 · Helm `config.dbStatementTimeoutMs`·`dbLockTimeoutMs`·`dbIdleTxTimeoutMs` → ConfigMap |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C61-01 | PG `SHOW` = 2min·15s·0 · SQLite `PRAGMA busy_timeout` = 30000 | `test_data_layer.py::test_session_limits_applied` | pytest (SQLite·PG) |
| C61-02 | env 300ms 엔진에서 `pg_sleep(2)` 취소 | `test_statement_timeout_cancels` | pytest (PG) |
| C61-03 | 마이그레이션 실행 때 `SET LOCAL … = 0` 이 advisory lock 앞 | `test_migrations_run_without_session_limits` | pytest (PG, SQLite 는 실행만) |
| C61-04 | 두 인덱스 존재·열 순서 | `test_overview_indexes` | pytest |
| C61-05 | 표본 SQL 에 `ORDER BY spans.start_time DESC … LIMIT` | `test_overview_sample_ordered` | pytest |
| 회귀 | 마이그레이션 왕복·admin 마이그레이션 head(0021)·관측 집계 | `test_foundation.py`·`test_admin.py`·`test_observe_perf2.py`·`test_deploy.py` | pytest |
