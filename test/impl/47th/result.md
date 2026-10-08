# impl 47th — 결과: 성능 정리 (Epic 47 · D18-④) + 9차 완료 게이트

판정: **PASS** — pytest C47-01~04 **4/4** · prod 스모크 P47-01~04 **4/4**. NFR-P1 재측정은 호스트 부하로 임계 초과(아래, 판정 보류).

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest r1 (관측·운영·SLO·관리 회귀) | 42/42 | `logs/pytest-r1.log` |
| pytest r2 (MCP modern·Tasks·다중 워커 회귀) | 18/18 | `logs/pytest-r2.log` |
| pytest r3 (C47) | **4/4** | `logs/pytest-r3.log` |
| prod 스모크 | **4/4** | `logs/smoke-prod-r1.log` |

## NFR-P1 재측정 (k6 `deploy/k6/gateway.js` 20초 · prod 2 workers · mock 공급자)

| 지표 | 값 | 목표 | 조용한 호스트 기준(16th) |
|------|----|------|------|
| 모델 API p95 | 45.97ms | ≤ 30 | 10.4 |
| MCP 전체 p95 | 62.90ms | ≤ 60 | — |
| MCP 순수 추가 지연 p95(trace, 표본 288) | 52.2ms | ≤ 20 | 17.6 |
| 첫 토큰 p95 | 49.0ms | ≤ 2000 | 13.46 |

측정 시점 호스트 load average **14.1**, 다른 프로젝트 컨테이너 `aegismesh-local-guard-1/2` 가 CPU **525%·534%**(`logs/host-load.txt` — 건드리지 않음). 같은 부하에서 판정 로직 A/B(43rd)·가드 A/B(39th)로 AgentNexus 변경 탓이 아님을 확인했고, 조용한 호스트 재측정이 필요하다 — 판정 보류로 남긴다.

## 9차 완료 게이트

| 항목 | 결과 | 로그 |
|------|------|------|
| pytest SQLite | **323 passed + 1 skipped**, 실패 2 = `test_perf.py`(호스트 부하 — kind 클러스터 동시 실행 중) | `logs/gate-pytest-sqlite.log` |
| pytest PostgreSQL | **326 passed**(성능 포함 전부) | `logs/gate-pytest-pg.log` |
| Vitest | 27/27 | `logs/gate-web-test.log` |
| Playwright | **77/77** (r2 — r1 은 local 스택이 내려가 있어 전부 연결 실패) | `logs/gate-e2e-r2.log` |
| helm-check | 4 시나리오 OK(ha 에 존 분산·a2aGrpc) | — |
| prod 리허설 (PostgreSQL · 2 workers · Valkey · 0019) | 44th 7/7 · 45th 5/5 · 47th 4/4 · 회귀 40th 8/8 · 39th 10/10 · 36th 10/10 · 37th 9/9 · 43rd 7/8 → 조정 사본 8/8 | 각 차수 `logs/smoke-prod-r1.log`, `logs/regress-*/` |
| kind | 46th CloudNativePG 장애조치 15/15 | `test/impl/46th` |

### 게이트 중 처리

- Playwright r1 — local 스택 컨테이너가 없어 `ERR_EMPTY_RESPONSE`(prod `db` 는 2시간째 실행 — Docker 재시작 아님, 이 세션 밖에서 내려간 것으로 보임) → `make local-all` 후 77/77.
- 43rd P43-02 — 44th 소유권 검사가 URL 검사보다 먼저라 `taskId:"x"` 는 -32001(의도). 43rd 스크립트는 고치지 않고, 자기 Task 로 바꾼 scratch 사본으로 8/8.

### 리허설 정리

임시 계정(`rehearsal`·`rehearsal-a`·`rehearsal-b`) 비활성화 · listen 카운터 0 · RBAC 원값 · `make prod-stop` → `make local-all`(health ok). 이전 차수 폴더에 쓰지 않음.
