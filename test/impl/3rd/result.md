# 시험 결과 / Test Result — impl 3rd (Epic 3 카탈로그·레지스트리)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 9, 통과 Pass: 9, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 28 passed · PostgreSQL 28 passed

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E3-01 ~ E3-07 | PASS | `test/test_catalog.py` (7 케이스, 1회차 통과) |
| E3-08 | PASS | `logs/make-test-pg.txt` |
| E3-09 | PASS (round 2) | `logs/smoke-local.txt` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | 스모크 전 케이스 404 | 호스트 `127.0.0.1:8000` 을 사용자의 다른 프로젝트(`aegismesh-local-api-1`)가 점유 → AgentNexus 컨테이너 포트 미바인드, 요청이 다른 앱으로 감 | `.env.local`·`.env.prod` 의 `API_PORT`·`PUBLIC_BASE_URL` 을 **8800** 으로 변경 (타 프로젝트는 건드리지 않음) | round 2 PASS 9/9 |
| 1 | 신규 엔드포인트 계약 감지 | 설계대로 | `UPDATE_CONTRACT=1` | PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-local) · `smoke.sh`
