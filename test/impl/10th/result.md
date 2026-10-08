# 시험 결과 / Test Result — impl 10th (Epic 10 독립 실행·연동·운영)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 11, 통과 Pass: 11, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 119 passed · PostgreSQL 119 passed

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E10-01 ~ E10-09 | PASS (round 3) | `test/test_admin.py` (9 케이스) |
| E10-10 | PASS | `logs/make-test-pg.txt` |
| E10-11 | PASS (round 2) | `logs/smoke-local.txt` 11/11 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | 구성요소 시험 서브프로세스 실패 | FastAPI 0.142 의 `app.routes` 에 포함 라우터 객체(`_IncludedRouter`)가 남아 `.path` 없음 (시험 코드) | OpenAPI 경로 목록으로 확인 | round 2 |
| 2 | ⌘K 검색에서 `jira` 로 `jira.search_issues` 미적중 | 도구 검색 대상 문자열에 서버 네임스페이스 누락 | `{서버}.{도구} 설명` 으로 검색 | round 3 PASS |
| smoke 1 | 서비스 목록에 `opa` 혼입 | 앞선 OPA 시험(COMPOSE_PROFILES=opa)의 컨테이너가 프로파일 밖이라 `down` 으로 제거되지 않음 (환경) | `COMPOSE_PROFILES=demo,opa make local-stop` 로 정리, 문서 주석 `demo,opa` 로 수정 | round 2 PASS |
| 기타 | 차수 폴더가 11th 로 생성 | smoke.sh 를 먼저 써서 10th 폴더가 이미 존재 (작업 순서) | 로그를 10th 로 옮기고 방금 생긴 빈 11th 정리 | — |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-local) · `smoke.sh`
