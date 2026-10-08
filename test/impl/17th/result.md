# 시험 결과 / Test Result — impl 17th (Epic 17 보안·정합성 결함)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi + web · local=SQLite / PostgreSQL 회귀
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 25 (pytest 9 · live 10(S17-01 2모드 포함) · e2e 3 · 문서 1 · 회귀 R 묶음 2), 통과 Pass: 25, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 160 passed + 1 skipped · PostgreSQL 161 passed · Vitest 24 · 빌드 · Playwright 21/21(전체) · live 10/10
- in-process NFR-P1 회귀 감시: Model API p95 8.9ms · MCP 추가 지연 p95 12.4ms (변경 전 12.62ms) — prod 판정은 완료 게이트에서 재측정

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C17-01 ~ C17-08 | PASS | `test/test_hardening.py` 8 — `logs/make-test.log` · `logs/make-test-pg.log` |
| C17-09 | PASS | `test/test_mcp.py::test_tool_toggle_keeps_args_schema` |
| C17-10 · C17-11 | PASS | `logs/smoke-run3.log` S17-01~03 |
| S17-04 ~ S17-08 | PASS (round 3) | `logs/smoke-run3.log` — live 도구 한도 429·복구, 스팬 마스킹, 거부 사유 |
| E17-01 ~ E17-03 | PASS (round 2) | `logs/e2e-round2.log` · `web/e2e/results/E17-*.png` |
| R | PASS (round 2) | SQLite·PG·Vitest(`logs/web-test.log`)·빌드(`logs/web-build.log`)·Playwright 전체 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 감사 | F02 도구 허용 토글이 인자 스키마·소독 설정을 지움 (실제 결함) | 서버 상세 rule 에 args_schema 등이 없고 PUT 이 전체 대체 | 상세에 필드 포함 + 콘솔이 전체 규칙 전송, 회귀 시험 추가 | PASS |
| pytest 1 | `test_perf::test_mcp_proxy_overhead_p95` 429 | 도구 호출이 이제 `any` 기본 한도(50 rps, 모델과 공용 버킷)를 차감 — 의도된 FR-G04 동작. 연속 측정이 한도를 넘음 | 측정 중에만 기본 한도를 끄는 모듈 픽스처 (k6 prod 는 합계 32 rps 로 한도 안) | round 2 PASS |
| e2e 1 | E17-01 상태 확인 실패 | 상태 배지가 한글 라벨(활성·폐기) — 시험 기대값 오류 | 라벨·버튼 사라짐으로 판정 | round 2 PASS |
| live 1·2 | S17-07 스팬 마스킹 판정이 무의미(통과해도 근거 없음) → 마커 0건 | 쿼리 문자열은 스팬에 기록되지 않음 / BatchSpanProcessor flush 전에 조회 | 경로에 이메일을 넣고 마커 존재·원문 부재 둘 다 확인, flush 폴링 | round 3 PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · web-test · web-build · e2e · e2e-round2 · smoke-run1~3)
