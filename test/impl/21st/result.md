# 시험 결과 / Test Result — impl 21st (Epic 21 등록 → 심사 → 사용)

- 일자 Date: 2026-10-07
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17 (uvicorn 2 workers)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 37 (pytest 9 · e2e 6 · prod live 22 — R 제외), 통과 Pass: 37, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 200 passed + 1 skipped · PostgreSQL 201 passed · Vitest 26 · 빌드(tsc) · Playwright 39/39(전체: round 1 37/39 → 수정 → 실패 2건 재시험 포함 13/13) · prod 22/22

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C21-01 ~ C21-09 | PASS (round 3) | `test/test_review.py` 9 |
| E21-01 ~ E21-06 | PASS (round 2) | `logs/e2e-round1.log`(37/39) · `logs/e2e-round2.log`(review·screens 13/13) · `web/e2e/results/E21-*.png` |
| P21-01 ~ P21-22 | PASS (round 1) | `logs/smoke-prod-run1.log` · P21-13 은 감사 로그로 실제 2인 승인 확인(초안 작성 rehearsal-sec → 승인 rehearsal) 후 null 거짓 통과 방지 가드 추가 |
| R | PASS | `logs/make-test.log` · `logs/make-test-pg.log` · `logs/web-test.log` · `logs/web-build.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| pytest 1 | 개발자 A2A 등록이 `database is locked` (SQLite, 실제 결함) | 승인 대기 알림을 열린 쓰기 트랜잭션 안에서 별도 세션으로 기록 | 커밋 후 알림 | round 2 |
| pytest 1 | 기존 A2A 시험 5건 실패 (`agent_inactive`) | 개발자 등록이 승인 대기로 바뀜 — 의도된 동작 (시험 갱신) | 시험에 운영자 승인 단계(`approve_agent`) 추가 · 승인 전 호출 거부 단언 추가 | round 2 PASS |
| pytest 1 | 정책 시험의 직접 쓰기 409 | 2인 검토 도입 — 의도된 동작 (시험 갱신) | 직접 쓰기 시험은 admin 비상 경로로 | round 2 PASS |
| pytest 2 | 재승인·롤백 후 신뢰 등급이 사라짐 (실제 결함) | 버전 전환이 항목 메타데이터를 통째로 바꿔 심사 기록(`_anx_review`) 유실 · 재승인이 이전 확정값을 덮음 | `_anx_*` 키 보존 · 재승인은 이전 확정값 위에 병합 | round 3 PASS |
| pytest 2 | Rego 패키지 이름에 공백·한글이 그대로 (실제 결함) | 이름 치환이 `/ - :` 만 처리 | 식별자 외 문자 → `_`, 숫자 시작 방지 | round 3 PASS |
| pytest 2 | F03 시험이 `교환 정책 없음` | 시험 DB 가 dynatrace 를 refresh 하지 않음 — 등록 시 만들어지는 기본값 (시험 환경) | 시험에서 refresh 선행 | round 3 PASS |
| pytest 2 | 삭제 초안 시험이 실패 | 정책을 지우면 user 가 다른 allow 로 허용됨 → 회귀 케이스가 **올바르게** 게시를 막음 (시험 기대 오류) | 막힘을 단언 → 케이스 제거 후 게시 | round 3 PASS |
| 구현 | 즉시 사용 ③ 의 쓰기 도구가 "차단"으로 분류되지 않음 | 기본 규칙 분류(2단계)가 쓰기를 모두 high 로 둠 | ③ 은 승인자 확정 위험도 → 없으면 3단계 분류(쓰기 = 명시 허용) | pytest PASS |
| 구현 | 즉시 사용 ② 가 아무 스코프나 허용 정책에 추가 | 요청 스코프를 그대로 합침 | 서버가 선언한 스코프(PRM·도구 스코프·기존 허용) 안에서만 — 밖이면 시험 교환 MSG-019 | pytest PASS |
| e2e 1 | E16-03 server.json 입력창 찾기 실패 (기존 시험 회귀) | 새 URL 입력의 접근성 이름 `server.json URL` 이 겹침 | `가져올 URL` 로 변경 | round 2 PASS |
| e2e 1 | E21-02 대상 입력 찾기 실패 | 필드 레이블에 힌트 문구가 포함돼 정확 일치 실패 (시험 로케이터) | role+정규식 로케이터 | round 2 PASS |

## 리허설 잔여 데이터 (prod 리허설 DB)

- 카탈로그 `io.e21prod/notes-*`(1.0.0 롤백 상태)·원격 에이전트 `e21-demo-*`(활성)·프로파일 `e21-prod-*`(A2A 게시) · 데모 원격 에이전트 즉시 사용 정책 `agent-allow/observability-analyst`·한도 `instant:observability-analyst`(600/분). 리허설 정책 `e21 prod deny *` 는 삭제, 임시 계정 3개는 비활성화.

## 첨부 / Logs

- `logs/` (pytest-round1 · make-test · make-test-pg · web-test · web-build · e2e-round1~2 · local-stop · prod-build · db-migrate-prod · db-ping-prod · smoke-prod-run1 · prod-stop · local-restore)
