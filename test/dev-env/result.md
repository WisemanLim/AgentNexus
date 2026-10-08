# 시험 결과 / Test Result — dev-env

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi (`wise-agentnexus`, local=sqlite · prod=postgres · extras=redis)
- 커밋 Commit: — (git 저장소 아님 / not a git repository)

## 요약 / Summary

- 전체 Total: 8, 통과 Pass: 8, 실패 Fail: 0, 최종 Verdict: **PASS**

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| ENV-01 | PASS | `Preflight OK (DB_ENGINE local=sqlite, prod=postgres)` — `.env.prod` CHANGE_ME WARN 은 의도된 placeholder |
| ENV-02 | PASS | `api` healthy, `redis` up |
| ENV-03 | PASS | `{"status":"ok","env":"local","db":{"engine":"sqlite","status":"ok"}}` |
| ENV-04 | PASS | `sqlite OK: /app/data/app.db` |
| ENV-05 | PASS | `1 passed` |
| ENV-06 | PASS (round 3) | api+db healthy, `/health` → `env=prod`, `engine=postgres`, `status=ok` |
| ENV-07 | PASS | `postgres OK` |
| ENV-08 | PASS | local·prod 컨테이너 0 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | `Bind for 127.0.0.1:6379 failed: port is already allocated` (ENV-06) | local·prod 모드가 같은 호스트 포트(8000·6379)를 쓰는데 local 스택이 기동 중이었음 | prod 리허설 전 `make local-stop` (절차 수정, 코드 변경 없음) | round 2 |
| 2 | prod api `8000/tcp` 호스트 미바인드 → `curl: (7) Failed to connect` | round 1 바인드 실패 때 생성된 컨테이너가 재사용됨 | `make prod-stop` 후 새로 `make prod-all` | round 3 PASS |

## 첨부 / Logs

- `logs/` 참조 (01-preflight · 02-local-all · 03-health · 04-db-ping · 05-make-test · 06-prod-all-round{2,3} · 07-db-ping-prod-round3 · 08-cleanup)

## 재검증 / Re-verification

- 2026-10-07 — **PASS 9/9** (Valkey · api 8800 · a2a-grpc 8851 · 0019 기준): `2026-10-07/result.md`
