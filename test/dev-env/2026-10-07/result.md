# 시험 결과 / Test Result — dev-env 재검증 2026-10-07

- 일자 Date: 2026-10-07
- 대상 Target(profile): python-fastapi (`wise-agentnexus`, local=sqlite · prod=postgres · extras=valkey)
- 커밋 Commit: — (git 저장소 아님 / not a git repository)

## 요약 / Summary

- 전체 Total: 9, 통과 Pass: 9, 실패 Fail: 0, 최종 Verdict: **PASS**

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| ENV-01 | PASS | `Preflight OK (DB_ENGINE local=sqlite, prod=postgres)` — `.env.prod` CHANGE_ME WARN 은 의도된 placeholder |
| ENV-02 | PASS | api `127.0.0.1:8800`·a2a-grpc `127.0.0.1:8851`·mcp-servers·valkey healthy |
| ENV-03 | PASS | HTTP 200 `{"status":"ok","env":"local","db":{"engine":"sqlite","status":"ok"}}` |
| ENV-04 | PASS | `sqlite OK: /app/data/app.db` |
| ENV-05 | PASS | `339 passed, 1 skipped` (89.6s) — 성능 시험 포함 전부 통과 |
| ENV-06 | PASS | a2a-demo·a2a-grpc·api·db·mcp-servers·valkey 6개 healthy, `/health` → `env=prod`, `engine=postgres` |
| ENV-07 | PASS | `postgres OK` |
| ENV-08 | PASS | `make prod-stop` 뒤 `wise-agentnexus*` 컨테이너 0 |
| ENV-09 | PASS | `make local-all` → `env=local` ok |

## 발견 오류 및 수정 / Bugs & Fixes

없음. 첫 검증 때의 포트 충돌(local·prod 같은 호스트 포트)은 절차(`local-stop` 먼저)로 피했다.

## 첨부 / Logs

`logs/` — 01-preflight · 02-local-all · 02b-local-ps · 03-health · 04-db-ping · 05-make-test · 06a-local-stop · 06-prod-all · 06b-prod-ps · 06c-prod-health · 07-db-ping-prod · 08-cleanup · 09-restore-local
