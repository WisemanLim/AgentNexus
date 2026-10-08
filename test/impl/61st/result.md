# impl 61st — 결과: 데이터 계층 견고성 (Epic 61 · D22-①)

판정: **PASS** — pytest C61-01~05 SQLite 4/4(+ PG 전용 1 건너뜀) · PostgreSQL 5/5 · 관련 회귀 포함 SQLite 29 passed + 2 skipped · PG 28/28.

| 단계 | 결과 | 로그 |
|------|------|------|
| SQLite r1 | **29 passed + 2 skipped** (C61-02 PG 전용·기존 PG 전용 1) | `logs/pytest-r1.log` |
| PostgreSQL r1 | **28/28** — 300ms 상한 엔진에서 `pg_sleep(2)` 가 `statement timeout` 으로 취소 | `logs/pytest-pg-r1.log` |
| local 마이그레이션 | `make db-migrate` → 0021 | (콘솔) |

## 결과 요약

- 앱 연결은 문장 2분·잠금 대기 15초를 넘으면 오류로 끝난다(풀 연결이 묶이지 않음). 마이그레이션은 상한 없이.
- idle-in-transaction 상한은 기본 끔 — `get_db` 요청 세션이 스트림·원격 대기 동안 트랜잭션을 연 채 남을 수 있어서다. 운영에서 켤 때는 최장 스트림(listen 900초 등)보다 길게.
- 0021 인덱스 생성은 일반 `CREATE INDEX`(쓰기 잠금) — 큰 `spans` 테이블이면 업그레이드 창에서 실행.
- 61st 디렉터리는 수동으로 만들었다(번호 규칙 동일).
