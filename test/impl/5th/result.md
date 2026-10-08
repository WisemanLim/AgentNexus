# 시험 결과 / Test Result — impl 5th (Epic 5 Unified Model API)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite+Valkey / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 18, 통과 Pass: 18, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 58 passed · PostgreSQL 58 passed

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E5-01 ~ E5-16 | PASS | `test/test_llm.py` (16 케이스) |
| E5-17 | PASS | `logs/make-test-pg.txt` |
| E5-18 | PASS (round 2) | `logs/smoke-local.txt` 9/9 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | S5-05 `[DONE]` 미검출 | 스모크 스크립트가 `$(...)` 의 끝 개행 제거를 고려하지 않고 끝에서 2번째 줄을 비교 | `grep -c '^data: \[DONE\]$'` 로 판정 (앱 변경 없음) | round 2 PASS |
| 1 | (작업 실수) `uv lock` 일회용 컨테이너가 이미지 ENTRYPOINT(마이그레이션·시드)를 실행해 프로젝트 `data/` 에 SQLite·키 생성 | `docker run` 에 `--entrypoint` 미지정 | 생성 파일 3개 삭제, 이후 `--entrypoint uv` 사용. `/data/` 는 이미 `.gitignore` 대상 — 빈 `data/` 디렉터리는 샌드박스 권한으로 남음 | — |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-local) · `smoke.sh`
