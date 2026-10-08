# impl 55th — 결과: 관측·카운터 성능 (Epic 55 · D20-④) + 11차 완료 게이트

판정: **PASS** — pytest C55-01~04 **4/4** · 회귀 포함 **79/79** · prod 스모크 P55-01~05 **5/5**.

| 단계 | 결과 | 로그 |
|------|------|------|
| r1 | 64/65 — C55-01 첫 토큰 p95 불일치(120 ≠ 280) + 린트(review.py 의 쓰지 않는 `re`). 원인: "다음 시작" 하위 질의가 두 단계 위 시작 이벤트와 상관되지 않고 자기 FROM 에 테이블을 다시 넣음(SQLAlchemy 자동 상관은 바로 위 질의만) | `logs/pytest-r1.log` |
| r2 | **79/79** — `.correlate(st)` 명시 | `logs/pytest-r2.log` |

## 11차 완료 게이트

| 항목 | 결과 | 로그 |
|------|------|------|
| pytest SQLite | **376 passed + 1 skipped**(성능 포함 전부) | `logs/gate-pytest-sqlite.log` |
| pytest PostgreSQL | **377 passed** — C55 SQL 집계 동등성(UTC 날짜 포함)도 PostgreSQL 에서 | `logs/gate-pytest-pg.log` |
| Vitest | 27/27 | `logs/gate-web-test.log` |
| Playwright | **77/77** | `logs/gate-e2e.log` |
| helm-check | 4 시나리오 OK (a2a-grpc 프로브·checksum 불변식 포함) | `logs/gate-helm-check.log` |
| prod 리허설 (PostgreSQL · 2 workers · Valkey · **0020**) | `prod-build` → `db-migrate`(0020) → `db-ping`(postgres OK) → 6개 서비스 healthy(a2a-grpc = gRPC 헬스) → 55th **5/5** + 회귀 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th 7/7 · 45th 5/5(보유자 ZSET 으로 조정한 사본) · 47th 2/2(k6 제외) · 51st 5/5 | `logs/smoke-prod-r1.log`, `logs/regress/` |

### 게이트 중 처리

- 45th 회귀 사본 — listen 슬롯이 증감 카운터(`anx:c:listen:*`)에서 보유자 집합(`anx:h:listen:*` ZSET)으로 바뀌어 `GET` → `ZCARD` 로 조정(이전 차수 스크립트는 고치지 않음).
- NFR-P1(k6) 은 사용자 결정으로 생략 — 47th 사본은 P47-03·04 를 뺐다.

### 리허설 정리

임시 계정 `rehearsal*` 8개 disabled · 시험 Access Token 은 스모크가 폐기(남은 것 0) · `make prod-stop`(prod 컨테이너 0) → `make local-all`(health ok) · `~/.kube/config` 무변경 · 다른 프로젝트 컨테이너 미접촉.
