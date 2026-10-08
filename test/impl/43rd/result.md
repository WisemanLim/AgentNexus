# impl 43rd — 결과: 정책 하드닝 (Epic 43 · D17-④) + 8차 완료 게이트

판정: **PASS** — pytest C43-01~04 **4/4** · prod 스모크 P43-01~08 **8/8** · 완료 게이트 통과(성능 시험 2건은 호스트 부하 — 아래).

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest r1 | 오류 — migration 0019 의 테이블 이름 오기(`refresh_tokens` → 실제 `oauth_refresh_tokens`) | `logs/pytest-r1.log` |
| pytest r2 | **16/16** (+ `test_admin.py` head 0019) | `logs/pytest-r2.log` |
| prod 스모크 | **8/8** | `logs/smoke-prod-r1.log` |

## prod 스모크

| # | 결과 |
|---|------|
| P43-01 | 게이트웨이 경유 push set `http://10.1.2.3/hook` → `-32602:push_url_rejected` |
| P43-02 | 플랫폼 push set `http://127.0.0.1/hook` → `-32602` |
| P43-03 | 새 계정 해시 `scrypt$n=16384,r=8,p=5` |
| P43-04 | 이전 형식 해시 계정 로그인 성공 → 새 형식으로 재해싱 |
| P43-05 | refresh 회전 후 11초 뒤 이전 토큰 재제시 400 · 최신 토큰도 400(계열 폐기) |
| P43-06 | 감사 `auth.refresh_reuse` |
| P43-07~08 | JSON-RPC 회귀 completed · 감사 체인 무결 |

## 8차 완료 게이트

| 항목 | 결과 | 로그 |
|------|------|------|
| pytest SQLite | **310 passed + 1 skipped**, 실패 2 = `test_perf.py`(아래) | `logs/gate-pytest-sqlite-final-r2.log` |
| pytest PostgreSQL | **313 passed** · 최종 정리(캐시·지연 세션) 후 재실행 312 passed + `test_mcp_proxy_overhead_p95` 1건(호스트 부하) | `logs/gate-pytest-pg.log`·`logs/gate-pytest-pg-r2.log` |
| Vitest | 27/27 | `logs/gate-web-test.log` |
| Playwright | **77/77** (r2 — r1 은 local SQLite 미마이그레이션) | `logs/gate-e2e-r2.log` |
| lint · helm-check | ruff 통과 · lint + 4 시나리오 OK | — |
| prod 리허설 (PostgreSQL · 2 workers · Valkey · **0019**) | 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 회귀 36th 10/10 · 37th 9/9 · 39th 10/10 · 35th 8/8 | 각 차수 `logs/smoke-prod-r1.log`, `logs/regress-*/` |
| kind | 41st a2a-grpc Helm·TLS 20/20 | `test/impl/41st` |

### 게이트 중 실패와 처리

- Playwright r1 — 모든 시험이 시간 초과: 로컬 SQLite 가 0019 로 마이그레이션되지 않아 `/idp/token` 500(`no column named family_id`). 핫 리로드는 마이그레이션을 하지 않는다 → `make db-migrate`(local) 후 r2 77/77. 절차 누락(제품 결함 아님).
- `test_perf.py` 2건(모델 API p95·MCP 추가 지연 p95 임계 초과) — 측정 시점 호스트 load average ≈ 19, 다른 프로젝트 컨테이너(`aegismesh-local-guard-1/2`)가 CPU 13코어가량 사용(건드리지 않음). Epic 40 판정 로직 유무 A/B 에서 차이 없음(`logs/perf-ctx-ab.txt`). 그래도 요청마다 DB 를 여는 것을 줄였다 — 기본 데이터 등급 5초 캐시(설정 저장 시 초기화)·run 헤더가 있을 때만 세션. 조용한 호스트에서 재측정 필요.
- 마지막 두 정리(캐시·지연 세션)는 prod 스모크 이후 변경 — 동작은 같고 SQLite·PostgreSQL 재실행으로 확인(prod 이미지 재빌드는 하지 않음).

### 리허설 정리

임시 계정 `rehearsal`·`rehearsal-user`·`rehearsal-legacy` 비활성화 · 시험 워크로드 비활성화 · RBAC 재정의 원복(`{}`) · `make prod-stop` → `make local-all`(health ok, local 0019). 이전 차수 폴더(35th·36th·37th·39th)에는 쓰지 않음(스크립트 사본의 LOG 를 이 차수로).

## 남은 항목

- 조용한 호스트에서 NFR-P1 성능 재측정(`test_perf.py`·`deploy/k6/gateway.js`).
- refresh 재사용 유예 10초 — 클라이언트 동시 갱신 패턴에 따라 조정 가능.
