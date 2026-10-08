# 시험 결과 / Test Result — impl 24th (Epic 24 남은 설계 요소)

- 일자 Date: 2026-10-07
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17 (uvicorn 2 workers)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 26 (pytest 7 · e2e 5 · prod live 14 — R 제외), 통과 Pass: 26, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 231 passed + 1 skipped · PostgreSQL 232 passed · Vitest 26 · 빌드(tsc) · Playwright 63/63(전체) · prod 14/14

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C24-01 ~ C24-07 | PASS (round 2) | `test/test_design_gaps.py` — round 1 에서 C24-03 이 Inspector 권한(`mcp:read`) 없는 계정으로 호출 (시험 오류) |
| E24-01 ~ E24-05 | PASS (round 2) | `web/e2e/gaps.spec.ts` — round 1 E24-03 이 마법사 1단계에서 OBO 를 찾음 (시험 로케이터) |
| P24-01 ~ P24-14 | PASS (round 1) | `logs/smoke-prod-run1.log` 14/14 — OBO 는 api 자신의 `/health` 를 업스트림으로 교환 기록 확인 |
| R | PASS | `logs/make-test.log` · `logs/make-test-pg.log` · `logs/web-test.log` · `logs/web-e2e.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| lint 1 | 푸시 거부 감사 람다가 `exc` 를 참조 (F821) | except 블록 밖에서 사라지는 이름을 클로저가 잡음 | 사유를 먼저 문자열로 | PASS |
| pytest 1 | C24-03 응답에 `error` 없음 | 시험이 `user`(Inspector 권한 없음)로 호출 → 403 | 개발자 계정으로 (시험 오류) | round 2 PASS |
| e2e 1 | OBO 라디오를 찾지 못함 | 마법사 2단계(연결·내부 인증)에 있음 | 다음 → 선택 (시험 로케이터) | round 2 PASS |
| 문서 | PRD Q2 가 NFR-S1 을 '미측정'으로 기록 | 18th 실측을 놓침 | PRD §7·§8 보정 | — |

## 리허설 잔여 데이터 (prod 리허설 DB)

- 리허설 규칙·클라이언트 키는 삭제·폐기, 에이전트 framework·push_verify 원복, OBO 시험 서버 `e24p-obo-*` 는 비활성화, 임시 계정 2개 비활성화.
- 남은 것: 푸시 시험 Task `observability-analyst:e24p-push` · 교환 기록 · 감사 이벤트.

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · web-test · web-e2e · local-stop · prod-build · db-migrate-prod · db-ping-prod · smoke-prod-run1 · prod-stop · local-restore)
