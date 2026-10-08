# 시험 결과 / Test Result — impl 22nd (Epic 22 채팅·오케스트레이션 편집기 깊이)

- 일자 Date: 2026-10-07
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17 (uvicorn 2 workers)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 40 (pytest 10 · e2e 7 · prod live 23 — R 제외), 통과 Pass: 40, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 210 passed + 1 skipped · PostgreSQL 210 passed · Vitest 26 · 빌드(tsc) · Playwright 46/46(전체) · prod 23/23

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C22-01 ~ C22-09 | PASS (round 3) | `test/test_chat_editor.py` 9 |
| C22-10 | PASS (round 1) | 재개 예약이 유실된 대기 실행 복구 — prod 리허설에서 발견해 추가 |
| E22-01 ~ E22-07 | PASS (round 3) | `logs/e2e-round1~3.log` · 전체 회귀 `logs/e2e-full-round2.log` 46/46 |
| P22-01 ~ P22-23 | PASS (round 2) | `logs/smoke-prod-run1.log`(20/23) → 수정 → `logs/smoke-prod-run2.log` 23/23 |
| R | PASS | `logs/make-test.log`·`make-test-round2.log` · `logs/make-test-pg.log` · `logs/web-test.log` · `logs/web-build.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| pytest 1 | Retriever `jira.*` 와일드카드가 게시 422 (기존 시험 회귀) | 와일드카드가 쓰기 도구까지 덮는 것을 오류로 봄 | 정확한 이름·Retriever 단계의 쓰기 도구는 오류, 와일드카드는 경고 | round 2 PASS |
| pytest 1 | 응답 소독 메타가 목록에서 숫자로 바뀜 (기존 시험 회귀) | 게이트웨이가 이미 `_meta.io.agentnexus/sanitized`(목록)를 주는데 덮어씀 | 덮어쓰기 제거, 오케스트레이터가 목록 길이를 셈 | round 2 PASS |
| pytest 2 | 검색 0건 재계획이 동작하지 않음 (실제 결함) | YAML 1.1 이 `on:` 키를 `true` 로 읽음 | 키를 `replan.triggers` 로 (기존 `on`·`true` 도 받음) | round 3 PASS |
| pytest 2 | 잘못된 `depends_on`(객체)이면 검증이 500 (실제 결함) | 문자열 아닌 의존성을 집합에 넣으려다 TypeError | 형식 오류로 보고 | round 3 PASS |
| pytest 2 | 승인 시험·코드 시험 실패 | 데모에 없는 이슈 키 · YAML 덧붙임 위치 (시험 오류) | 시험 데이터 수정 | round 3 PASS |
| e2e 1 | 드로어가 Esc 로 닫히지 않음 (접근성 결함) | 모달만 Esc 처리 | 공용 Drawer 에 Esc 닫기 | round 2 PASS |
| e2e 1~2 | 시험 로케이터 중복 (결과 본문·select 옵션과 같은 문구) | 시험 로케이터 | 정확 문구·first | round 3 PASS |
| e2e 전체 1 | 기존 6건 회귀 — `trace … · succeeded` · 승인 모달 도구명 · 위임 카드 제목 | 응답 메타 줄을 칸으로 나눔 · 영향 목록이 도구명을 그대로 · 카드 제목을 표시 이름으로 바꿈 | 상태를 trace 와 같은 칸에 · 영향 문구 · 제목은 id + 이름 보조 | round 2 46/46 |
| live 1 | 승인 만료를 다른 프로세스에서 처리하면 실행이 대기 상태로 멈춤 (실제 결함) | 이벤트 루프 없는 프로세스에서 재개 예약이 실패해 유실 | 재개 예약 실패를 허용하고, 복구 작업이 "기다릴 단계 없는 대기 실행"을 재개 · 리허설은 워커 스케줄러로 만료 | round 2 PASS |
| live 1 | 드라이런 판정 2건이 값 없음 | `echo` 가 JSON 문자열의 `\n` 을 실제 줄바꿈으로 바꿔 jq 파싱 실패 (시험 스크립트) | `printf '%s'` | round 2 PASS |

## 리허설 잔여 데이터 (prod 리허설 DB)

- 리허설 대화 세션·실행(채팅) · 프로파일 `e22p-*` 3개는 보관(archived) · 임시 계정 2개 비활성화. round 1 에서 멈춘 대기 실행은 수정 배포 후 복구 작업이 재개했다.

## 첨부 / Logs

- `logs/` (pytest-round1~2 · make-test(·round2) · make-test-pg · web-test · web-build · e2e-round1~3 · e2e-full-round1~2 · local-stop · prod-build(·round2) · db-migrate-prod · db-ping-prod · smoke-prod-run1~2 · prod-stop · local-restore)
