# impl 55th — 관측·카운터 성능 (Epic 55 · D20-④) + 11차 완료 게이트

## 범위

| 항목 | 구현 |
|------|------|
| 개요 KPI | 게이트웨이 추가 지연 — 도구 호출 스팬마다 자식을 조회(N+1)하던 것을 자식 최대값 집계 조인 1회로 · 첫 토큰 — 기간의 `run.started`·`delta` 이벤트를 전부 읽던 것을 시작마다 "다음 시작 전 첫 delta" 상관 하위 질의로(이전과 같은 의미, 재시작 포함) |
| usage | 행 전체 ORM 적재 → `GROUP BY` 집계(호출·토큰·비용·지연·오류) · day 는 UTC 날짜(PostgreSQL 은 `timezone('UTC', ts)`) |
| listen 슬롯 | 증감 카운터(변경마다 TTL 연장 — 죽은 워커 몫이 활동 중에는 안 풀림) → 보유자 집합(만료는 넣을 때 고정, Valkey ZSET·Lua 원자 처리) |
| 도구 위험도 | 이름을 단어로(snake·kebab·점·camelCase·약어) 나눠 동사 목록과 비교 — `createIssue` 같은 camelCase·drop·revoke·grant·transfer 등 빠진 동사 보강, 부분 문자열 오탐(address·settings) 제거. 게이트웨이 기본 규칙(`governance.classify_risk`)과 카탈로그 심사(`review.tool_risk`) 공통 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C55-01 | 개요 KPI = 이전 구현(스팬 자식 0~2 · 재시작·delta 없는 실행 포함) | `test_observe_perf2.py::test_overview_extra_matches_reference` | pytest (SQLite·PostgreSQL) |
| C55-02 | usage 8개 그룹 기준 = 이전 구현(빈 문자열·NULL → "-", 오류 = ok 아님·NULL) | `test_usage_sql_matches_reference` | pytest (SQLite·PostgreSQL) |
| C55-03 | 죽은 워커 보유자 5개 → 활동 중에도 960초 뒤 풀림 · 비교: 증감 카운터는 안 풀림 | `test_holder_slots_expire_while_active` | pytest |
| C55-04 | camelCase·새 동사 high · 읽기 이름 low · 심사 분류 high/medium/low | `test_tool_risk_words` | pytest |
| 회귀 | 다중 워커(listen 보유자로 갱신)·MCP modern·관측·성능 경로·정책·카탈로그·운영 | 10개 파일 | pytest |
| P55 | prod — 0020 · a2a-grpc 헬스·최소 env · audience · usage day · listen ZSET | `smoke-prod.sh` | live |
