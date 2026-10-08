# 시험 결과 / Test Result — impl 20th (Epic 20 첫 실행·설치)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17 (uvicorn 2 workers)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 30 (pytest 10 · e2e 4 · prod live 16 — R 제외), 통과 Pass: 30, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 191 passed + 1 skipped · PostgreSQL 192 passed · Vitest 26 · 빌드 · Playwright 33/33(전체) · helm-check · prod 16/16

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C20-01 ~ C20-05 | PASS (round 2) | `test/test_setup.py` 5 |
| C20-06 ~ C20-10 | PASS (round 1) | `test/test_cli_install.py` 5 · 기존 `test_cli.py` 11 유지 |
| E20-01 ~ E20-04 | PASS (round 4) | `logs/e2e-round4.log` · `web/e2e/results/E20-*.png` |
| P20-01 ~ P20-16 | PASS (round 2) | `logs/smoke-prod-run2.log` |
| R | PASS | `logs/make-test.log` · `logs/make-test-pg.log` · `logs/web-test.log` · `logs/helm-check.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 구현 | L01 배포 프로파일 탭이 열리면 오류 (실제 결함, 이전부터) | API 의 `components` 가 문자열인데 화면이 배열로 `.map` | 화면을 새로 구성(카드·미리보기·적용) | PASS |
| pytest 1 | 자가 시험 '인증 설정' 실패 | 시험 모듈이 MCP 서버를 refresh 하지 않아 교환 정책 없음 — 자가 시험이 올바르게 탐지 (시험 환경) | 다른 모듈처럼 refresh 픽스처 | round 2 |
| pytest 1 | 완료 응답에 `completed` 없음 | 설정 코드 경로는 관리자 단계가 끝나야 완료 — 시험이 단계를 건너뜀 (시험 오류) | 시험에서 ① 완료 상태로 둠 | round 2 PASS |
| e2e 1 | MFA 오류 후 사용자 이름이 비어 재입력 필요 (UX 결함) | 오류 화면이 입력을 버림 | 로그인 오류 화면에 사용자 이름 유지 | round 2 |
| e2e 2 | 재로그인 후 입력이 사라짐 (실제 결함) | 재로그인 뒤 `reload()` 가 로딩 화면으로 바꿔 페이지가 다시 마운트 | 재로그인은 조용한 갱신(`reload(true)`) | round 3 |
| e2e 3 | 잘못된 코드 안내 대신 '만료' 안내 | 유효한 코드가 없으면 만료가 먼저 — 서버 동작이 맞음 (시험 순서) | 코드를 먼저 발급하고 오답 시험 | round 4 PASS |
| live 1 | P20-08·09 가 값이 달라도 통과로 찍힘 · 관리자 단계 미완료 | 셸의 중첩 인용(`"$(… \"…$(totp)…\" …)"`)이 인자를 잘못 갈라 본문이 깨짐 (시험 스크립트) | 본문을 변수로 만든 뒤 호출 | round 2 PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · web-test · web-build · e2e·round2~4 · prod-build · db-migrate-prod · smoke-prod-run1~2 · helm-check · prod-stop)
