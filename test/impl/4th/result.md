# 시험 결과 / Test Result — impl 4th (Epic 4 게이트웨이 정책 코어)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite+Valkey(+OPA 프로파일) / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 16, 통과 Pass: 16, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 42 passed · PostgreSQL 42 passed

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E4-01 ~ E4-14 | PASS | `test/test_gateway_policy.py` (14 케이스) |
| E4-15 | PASS | `logs/make-test-pg.txt` |
| E4-16 | PASS (round 2) | `logs/smoke-local.txt` 7/7 — Valkey `RedisBackend [True, True, False]`, Rego `False domain-local True True` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | S4-04 `Cannot index boolean` | 스모크 스크립트 jq 연산자 우선순위(`a|tostring + ...`) | 괄호 추가 `(.decision.allow|tostring) + ...` (앱 변경 없음) | round 2 PASS |
| 설계 | allow 정책의 args_schema 위반이 기본 allow 로 새어 나갈 수 있음 | "불일치"로 처리하면 하위 우선순위 기본 허용이 적용됨 | 같은 우선순위에서 **명시적 거부(인자 제한 위반)** 로 판정 | E4-03 PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-local) · `smoke.sh`
