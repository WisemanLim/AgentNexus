# 시험 결과 / Test Result — impl 2nd (Epic 2 Identity & Authorization)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 16, 통과 Pass: 16, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 21 passed · PostgreSQL(`make test-pg`) 21 passed

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E2-01 ~ E2-13 | PASS | `test/test_identity.py` (12 케이스) |
| E2-14 | PASS (round 2) | `logs/make-test-pg.txt` |
| E2-15 | PASS | `logs/smoke-local.txt` 12/12 |
| E2-16 | PASS | `logs/smoke-prod.txt` 4/4 — admin 은 리허설용 임시 비밀번호로 컨테이너 안에서 생성 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | 신규 엔드포인트로 계약 테스트 실패 | 설계대로 — 계약 변경은 명시 갱신 필요 | `UPDATE_CONTRACT=1` 로 스냅샷 갱신 | PASS |
| 1 | PG 에서 `cannot adapt type 'dict'` (감사 체인 시험) | 시험 코드가 raw SQL 재삽입에 JSON 컬럼 dict 를 그대로 바인딩 (SQLite 는 문자열 반환) | 재삽입 전 JSON 문자열화 (`_restorable`) | round 2 PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-local · smoke-prod) · 스모크 스크립트 `smoke.sh`, `smoke-prod.sh`
