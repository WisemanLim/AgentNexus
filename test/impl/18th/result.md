# 시험 결과 / Test Result — impl 18th (Epic 18 오케스트레이션 신뢰성·확장)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17 (uvicorn 2 workers)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 31 (pytest 13 · Vitest 2 · e2e 4 · prod live 13 — R 제외), 통과 Pass: 31, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 173 passed + 1 skipped · PostgreSQL 174 passed · Vitest 26 · 빌드 · Playwright 25/25(전체) · helm-check(PrometheusRule 포함) · prod 13/13
- **NFR-S1 동시 1,000 세션** (k6 · PG · 2 workers): 세션 1,000 개 유지 · 요청 8,730 건 오류 0% · 도구 호출 p95 **80.35ms** (기준 < 1s)

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C18-01 ~ C18-11 | PASS (round 2) | `test/test_reliability.py` 13 — `logs/make-test.log` · `logs/make-test-pg.log` |
| V18 | PASS | `logs/web-test.log` (26) |
| E18-01 ~ E18-04 | PASS (round 3) | `logs/e2e-round3.log` · `web/e2e/results/E18-*.png` |
| P18-01 ~ P18-13 | PASS (round 1) | `logs/smoke-prod-run1.log` · `logs/k6-sessions.log` · `logs/k6-sessions.json` |
| R | PASS | SQLite·PG·Vitest·빌드(`logs/web-build.log`)·Playwright 전체·`logs/helm-check.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 구현 | `/agents/{id}/test` 가 input-required 실행에서 시간 초과까지 대기 | `orch.wait` 종료 상태에 `waiting_input` 없음 | 종료 상태에 추가 | PASS |
| pytest 1 | 조건으로 건너뛴 분기에만 의존한 단계가 "의존 단계가 모두 실패" 로 집계 → 실행 partial (실제 결함) | 조건 건너뜀과 실패를 구분하지 않음 | `분기 미선택` 으로 표시하고 실패·건너뜀 집계에서 제외 | round 2 PASS |
| pytest 1 | KB 검색 순위 시험 실패 | 어휘 해시 임베딩에서 질의어 '롤백'이 롤백 체크리스트에 더 많이 나옴 — 질의가 모호 (시험 문제) | 대상 문서에만 있는 어휘로 질의 | round 2 PASS |
| pytest 1 | 복구 시험에서 실행이 `recovering` 에 머묾 | `asyncio.run()` 의 임시 루프가 끝나며 재개 작업 취소 — 운영은 예약 작업이 앱 루프에서 호출 | 시험을 앱 루프(`client.portal`)에서 호출 | round 2 PASS |
| e2e 1 (점검) | 재계획 후 대체된 의존 단계가 화면에 "건너뜀" 으로 남음 | `plan.revised.previous` 에 실패 단계만 담김 | 대체한 단계 전체를 담음 | round 2 |
| e2e 2 | E18-03 프로파일 생성 409 | 1회차가 만든 같은 이름 — 이름도 유일해야 함 (시험 데이터) | 이름에 id 포함 | round 3 PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · web-test · web-build · e2e · e2e-round2·3 · prod-build · db-migrate-prod · smoke-prod-run1 · k6-sessions · helm-check · prod-stop)
