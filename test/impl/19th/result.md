# 시험 결과 / Test Result — impl 19th (Epic 19 외부 IdP SSO 로그인)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17 (uvicorn 2 workers)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 22 (pytest 8 · e2e 4 · prod live 10 — R 제외), 통과 Pass: 22, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 181 passed + 1 skipped · PostgreSQL 182 passed · Vitest 26 · 빌드 · Playwright 29/29(전체) · prod 10/10
- local 스택 live: 버튼 로그인 → 데모 외부 IdP → 브로커 콜백(서버 측 `internal_url` http://api:8000) → 콘솔 토큰, `ext:idp-demo-partner:partner-kim` · roles `developer`

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C19-01 ~ C19-05 | PASS (round 3) | `test/test_sso.py` 8 — `logs/make-test.log` · `logs/make-test-pg.log` |
| E19-01 ~ E19-04 | PASS (round 1) | `logs/e2e.log` · `web/e2e/results/E19-*.png` |
| P19-01 ~ P19-10 | PASS (round 1) | `logs/smoke-prod-run1.log` |
| R | PASS | SQLite·PG·Vitest(`logs/web-test.log`)·빌드·Playwright 전체 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 구현 | 시크릿 조회 함수 오기(`require_plain_ref`) | 없는 함수 | `secrets.require_plain(db, ref)` | PASS |
| pytest 1 | 브로커 디스커버리가 `AssertionError` | 시험의 서버 측 동기 호출 경로에 Starlette TestClient 전송을 재진입 사용 (시험 환경) | `SyncASGITransport` — 스레드풀에서 앱 이벤트 루프로 넘김 | round 2 |
| pytest 2 | `NameError: asyncio` | 새 전송 클래스가 쓰는 asyncio 를 모듈에서 import 안 함 | import 추가 | round 3 |
| pytest 2 | state 재사용 시험 기대값 | 원래 요청을 아는 경우 오류를 콘솔로 돌려주는 동작이 더 맞음 (시험 기대 오류) | 재사용 → `invalid_state` 리다이렉트, 모르는 state → 400 화면으로 시험 분리 | round 3 PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · web-test · web-build · e2e · prod-build · db-migrate-prod · smoke-prod-run1 · prod-stop)
