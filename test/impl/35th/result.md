# 시험 결과 / Test Result — impl 35th (Epic 35 SLO·오류 예산) + 6차 완료 게이트

- 일자 Date: 2026-10-07
- 대상 Target(profile): local(SQLite) pytest · Playwright 1.55 · prod 리허설(PostgreSQL 17 · 2 workers · migration 0018)
- 커밋 Commit: — (git 커밋 없음)

## 요약 / Summary

- 전체 Total: 14 (pytest 5 · e2e 1 · prod 8), 통과 Pass: 14, 실패 Fail: 0, 최종 Verdict: **PASS** (round 2)
- 계산: SLI·예산(허용 100 · 잔량 0.3)·소진율(1h 20 · 5m 50) → page · 지연 SLO ticket · 상태 변화 때만 알림·회복 알림 · 수정은 admin·사유·감사
- prod: 기본 SLO 2종 · 데이터 경로 요청 20건이 두 워커 합산으로 반영 · 모델 업스트림 전부 장애(502 30건) → 5분 소진율 ≥ 14.4 → `page` + 알림 · 공급자 원복 · 감사 체인

## 6차 완료 게이트 (`logs/gate/`)

| 항목 | 결과 |
|------|------|
| pytest SQLite | 263 passed + 1 skipped (r2) |
| pytest PostgreSQL | 264 passed (r2) |
| Vitest | 27 |
| Playwright | 77/77 (전체 76 + ops 12/12 재실행 — E23-09 건수 정규식) |
| prod (PostgreSQL · 2 workers · 0018) | 32nd 11/11 · 33rd 7/7 · 35th 8/8 · 회귀 29th 13/13 · 30th 9/9 · 27th 11/11 |
| 클러스터 | 34th kind 다중 노드 21/21 (별도 실행) |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix |
|-------|----------|------------|----------|
| pytest 1 | C35-02 지연 SLO 상태 단언 (시험 결함) | 30분 창에 지연 초과 데이터가 없어 ok 가 맞음 | 2분 전 행에 초과 10건 |
| pytest 1 | C35-04 operator 403 단언 (시험 결함) | operator 도 admin:write 보유 | dev 로 |
| 게이트 1 | `test_observe` 스팬 이름 (기대된 변경) | 오케스트레이터가 SendMessage(1.0) 사용 | 단언 갱신 |
| 게이트 1 | `test_continue_with_partial_results` (SQLite 만, 재실행 통과) | 호스트 부하(1회 202초 — 평소 76초)에서 node_timeout 1초 시험이 흔들림 | 변경 없음 — 재실행 263/263 |
| 게이트 1 | E23-09 `예상 이벤트 \d+건` | 감사 이벤트가 1,000건을 넘어 "1,082건"(천 단위 구분) | 정규식 `[\d,]+` |

## 첨부 / Logs

- `logs/pytest-r1.log` · `logs/e2e-r1.log` · `logs/smoke-prod-r1.log` · `logs/slos.json` · `logs/fault.log` · `logs/gate/`
