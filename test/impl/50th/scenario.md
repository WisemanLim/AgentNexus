# impl 50th — CI·재현성 (Epic 50 · D19-③)

## 범위

| 항목 | 구현 |
|------|------|
| 액션 SHA 고정 | `.github/workflows/ci.yml` 의 `uses:` 를 커밋 SHA 로 — `git ls-remote` 로 태그를 풀어 `actions/checkout@11d5960a… # v4.4.0`(= `v4`), `azure/setup-helm@1a275c3b… # v4.3.1`(= `v4` 주석 태그가 가리키는 커밋). Dependabot(github-actions)이 SHA·주석을 함께 갱신 |
| dev-env 재검증 | 첫 검증(2026-10-06, redis·8000)을 그대로 두고 `test/dev-env/2026-10-07/` 에 현재 구성(Valkey·8800·a2a-grpc·0019)으로 다시 |
| 성능 시험 | `test_perf.py` — 한도를 넘으면 `PERF_TRIES`(기본 3)회까지 다시 재고 가장 좋은 회차로 판정. 호스트 부하 잡음은 지연을 늘리기만 하므로 최솟값이 처리 지연에 가장 가깝다. 회차는 `PERF_REPORT` 에 남김 |
| 승인 뒤 대기 경합 | `test_approval_context_remember_and_node_policy` — 승인 직후 재개 전까지 실행 상태는 여전히 `waiting_approval` 이라 기본 `wait_run` 이 바로 돌아오던 경합. 종료 상태(`succeeded·partial·failed`)까지 기다린다 |
| 1초 노드 타임아웃 | `test_continue_with_partial_results` — 팬아웃 `node_timeout_s: 1` 이 빠른 단계(`fast`·`sum`)에도 걸려 부하 때 `sum` 이 실패하던 것. 느린 단계에만 단계 `timeout_s: 1` |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C50-01 | 모든 `uses:` = `owner/repo@<40자리 SHA>` + `# vX.Y.Z` 주석 | `test_ci_repro.py::test_actions_pinned_to_sha` | pytest |
| C50-02 | 한도 안이면 1회 · 넘으면 다시 · 최솟값 판정 · 한도 안에 들면 멈춤 | `test_perf_best_of` | pytest |
| C50-03 | 두 시험의 경합 제거가 소스에 남아 있음(되돌림 방지) | `test_time_dependent_tests_hardened` | pytest |
| C50-04 | CPU 경합(코어 1개에 pytest + 부하 3개)에서 이전 판 / 고친 판 A/B 6회씩 | `logs/stress-old.log`·`logs/stress-new.log` | stress |
| C50-05 | dev-env 9케이스 | `test/dev-env/2026-10-07/` | make·curl |
| 회귀 | 공급망·채팅 편집기·성능 | `test_supply_chain.py`·`test_chat_editor.py`·`test_perf.py` | pytest |

## 환경 가정

- 부하 시험은 컨테이너 안 `taskset -c 0` 으로 코어 하나에만 건다 — 호스트의 다른 프로젝트에 주는 영향을 코어 1개로 제한.
