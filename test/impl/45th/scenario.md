# impl 45th — 다중 워커 일관성 (Epic 45 · D18-② · Lv2 BUG-06·07·리스 없음)

## 범위

| 항목 | 구현 |
|------|------|
| listen 한도 | 주체당 동시 구독 5 를 **워커 전체 합**으로 — ratelimit 백엔드 증감 카운터(`counter_add`: Valkey `INCRBY`+`EXPIRE`, 메모리 대체) · 확보(`listen_acquire`)는 열기 전, 반납은 스트림 종료·열기 실패 시 · TTL = 최대 구독 시간 + 60초(보유 워커가 죽어도 풀림) |
| progress 중계 | 업스트림 progress 를 받을 하류 요청이 이 워커에 없으면 공유 행(`mcp_relays`, method `notifications/progress`)으로 · MRTR 재시도는 쌓인 progress 부터 받고, 호출이 다른 워커에서 돌면 공유 행을 0.3초 주기로 이어 받는다(전달한 행은 삭제 — 한 번만) · 입력 요청 판정(`_poll`)에는 섞이지 않음 |
| 리스 | MCP 헬스 루프(`mcp_health`, TTL 2×주기)·A2A watcher(`a2a_watch`, TTL 3×주기)는 리스 보유 워커만 — 기존 scheduler 리스(조건부 UPDATE, SQLite·PG) |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C45-01 | 카운터 하한 0 · TTL 뒤 0 | `test_multiworker.py::test_counter_semantics` | pytest |
| C45-02 | 다른 워커가 5개 쥔 상태 → 429 · 이 워커 LISTENS 0 · 거절 시도는 되돌림 · 반납 후 확보 가능 | `test_listen_limit_is_global` | pytest |
| C45-03 | 대기 요청 없으면 공유 행 · 재시도(다른 워커) 쌓인 것 + 이어 받기 · 남은 행 0 | `test_progress_relayed_across_workers` | pytest |
| C45-04 | 리스 보유자만 · 리스 없는 워커의 watcher 폴링 0 | `test_background_leases` | pytest |
| C45-05 | 구독 중 클라이언트가 끊어도 카운터 반납(취소 범위 안에서는 await 없이 동기 반납) | `test_listen_released_on_disconnect` | pytest |
| 회귀 | MCP modern(listen·progress·MRTR)·Tasks | `test_mcp_modern_ext.py`·`test_mcp_tasks.py` | pytest |
| P45-01~05 | prod(2 workers·Valkey): 동시 listen 5 → Valkey 카운터 5 · 6번째 429 · 끊은 뒤 0 · `a2a_watch`·`mcp_health` 리스 보유 워커 1개 | `smoke-prod.sh` | live |
