# 시험 결과 / Test Result — impl 15th (Epic 15 업스트림 mTLS)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 11, 통과 Pass: 11, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 150 passed + 1 skipped · PostgreSQL 151 passed · mTLS 4/4 (`logs/test-mtls.log`) · live 6/6

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C15-01 ~ C15-04 | PASS (round 2) | `test/test_mtls.py` — 실제 TLS 핸드셰이크(클라이언트 인증서 필수) |
| S15-01 ~ S15-06 | PASS | `logs/smoke-run1.log` |
| R | PASS | `logs/make-test.log` · `logs/make-test-pg.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | `required` 정책 위반이 `mtls_required` 대신 일반 업스트림 오류(-32002)로 보임 | 도구 목록이 오래되어 갱신 경로(refresh_tools)를 탔고, 갱신 실패가 오류 코드를 버리고 메시지만 남김 | 갱신 결과에 `error_code` 보존 → FORBIDDEN + `mtls_required` 로 재발생 | round 2 |
| 1 | (시험 부작용) 정책 시험 후 jira 헬스가 unhealthy 로 남음 | 정책 위반 갱신이 헬스를 갱신(정상 동작) | 시험 finally 에서 정책 복구 후 재갱신 | round 2 PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · test-mtls · smoke-run1) · `smoke.sh`
