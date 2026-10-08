# impl 47th — 성능 정리 (Epic 47 · D18-④ · Lv2 관측 집계·listen 부하)

## 범위

| 항목 | 구현 |
|------|------|
| 관측 요약 | `summary_data` 를 SQL 집계로 — 종류별 `count`·오류 `sum(case)`·토큰·비용 `GROUP BY`, p95 는 종류별 `ORDER BY latency LIMIT 1 OFFSET idx`(이전과 같은 위치 규칙), 상위 모델·팀 비용 `GROUP BY … ORDER BY` (동률은 이름 순으로 결정), 폴백률 SQL — 사용 기록 행을 메모리로 읽지 않는다 |
| 경보 평가 | 같은 창(window)의 규칙은 요약을 한 번만 계산해 공유 |
| listen 지문 | (서버, 주체, 대상) 지문을 폴링 주기의 0.8 배 동안 공유 — 같은 주체의 여러 구독이 업스트림을 한 번만 조회(주체가 다르면 따로 — 정책·위임 토큰마다 목록이 다르다) |
| 저장소 | `.gitignore` 에 `.wds/`(재생성 가능 추출물), `.docs/` 는 커밋 대상 유지 |
| NFR-P1 | prod 리허설에서 k6(`deploy/k6/gateway.js`) 재측정 + MCP 순수 추가 지연(trace) · 측정 시점 호스트 부하 기록 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C47-01 | SQL 요약 = 이전 파이썬 집계(종류별·호출 수·폴백률·상위 모델·팀·비용) | `test_perf_paths.py::test_summary_sql_matches_reference` | pytest |
| C47-02 | 같은 창 규칙 3개 → 요약 1회 | `test_alert_rules_share_summary` | pytest |
| C47-03 | 같은 주체 두 번 조회 → 업스트림 1회 · 다른 주체 → 따로 | `test_fingerprint_shared_per_principal` | pytest |
| C47-04 | `.wds/` 무시 · `.docs/` 무시 안 함 | `test_gitignore_wds_only` | pytest |
| 회귀 | 관측·운영·SLO·관리 · MCP modern | `test_observe.py`·`test_ops.py`·`test_slo.py`·`test_admin.py`·`test_mcp_modern_ext.py` | pytest |
| P47-01~04 | prod(PostgreSQL): 요약 API(종류별 p95 포함) · 경보 평가 · k6 측정 완료 · MCP 추가 지연 표본 | `smoke-prod.sh` | live |

## 환경 가정

- 같은 Docker VM 에 다른 프로젝트(aegismesh-local-guard-*)가 CPU 를 크게 쓰는 시간대가 있다 — k6 결과는 load average·상위 컨테이너 CPU 와 함께 기록하고, 임계 초과는 부하 기록과 함께 판단한다.
