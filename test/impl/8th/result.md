# 시험 결과 / Test Result — impl 8th (Epic 8 Host·오케스트레이션)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite / prod=PostgreSQL 17 (워커 2)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 17, 통과 Pass: 17, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 99 passed · PostgreSQL 99 passed

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E8-01 ~ E8-14 | PASS (round 4) | `test/test_orchestration.py` (13 케이스) |
| E8-15 | PASS | `logs/make-test-pg.txt` |
| E8-16 | PASS | `logs/smoke-local.txt` 9/9 |
| E8-17 | PASS | `logs/smoke-prod.txt` 9/9 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | SSE 첫 이벤트 불일치 | 시험 기대값 오류 — 첫 이벤트는 설계상 `run`(run_id 안내) | 시험 수정 | round 2 |
| 2 | 승인 단계 인자 `{{s3.text}}` 가 빈 문자열 | 계획 저장 시점에 아직 없는 단계 출력으로 미리 렌더해 참조가 사라짐 | 저장 시 미해결 참조 **보존**(keep_unknown), 실행 직전 렌더 | round 3 |
| 3 | 플랫폼 A2A Task DB 상태 working | A2A 서버가 구독자 통지 후 영속 → 클라이언트 응답이 저장보다 먼저 | 이벤트 처리 순서를 **영속 → 통지** 로 변경 | round 4 PASS |
| 설계 | 사용자 재시도 시 요약이 이전 결과로 남음 | 실패 단계만 재실행하면 하위 의존이 갱신되지 않음 | 재시도 시 실패 단계의 **전이적 하위 의존**까지 pending | E8-07 PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-local · smoke-prod) · `smoke.sh`
