# 시험 결과 / Test Result — impl 13th (Epic 13 Helm·에어갭·NFR-P1 + 완료 게이트)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17 · Helm 4.2 (렌더만)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 21, 통과 Pass: 21, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 138 passed + 1 skipped(PG 전용) · PostgreSQL 139 passed
- prod 리허설 `smoke-prod.sh` 11/11 (최종 회차 `logs/smoke-prod-final.log`)

### NFR-P1 실측 (prod 리허설 · PostgreSQL · uvicorn 2 workers · mock 공급자 · 32 rps · 20s)

| 지표 | 목표 | 측정 p95 | 판정 |
|------|------|----------|------|
| Unified Model API (게이트웨이 전체 = 추가 지연) | ≤ 30ms | 15.72ms | PASS |
| MCP 프록시 순수 추가 지연 (게이트웨이 − 업스트림 서버 스팬, 269 표본) | ≤ 20ms | 18.2ms | PASS |
| MCP 전체(업스트림 데모 서버 포함) | 참고 | 20.57ms | — |
| 첫 토큰(SSE TTFB) | ≤ 2s | 12.38ms | PASS |
| 성공률 | > 99% | 100% | PASS |

in-process(test 모드, 동기 스팬 저장 포함) 참고값: Model p95 10.4ms · MCP 추가 지연 p95 13.0ms (`logs/perf-inprocess.log`).

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E13-01 ~ E13-04 | PASS | `logs/helm-check.log` · 가드 5종 모두 설치 전 거부 · 생성 키 형식 확인 |
| E13-05 ~ E13-09 | PASS (round 3) | `logs/make-test.log` · `logs/make-test-pg.log` |
| P13-01 ~ P13-11 | PASS (round 2) | `logs/smoke-prod-run2.log` · 코드 수정 후 재실행 `logs/smoke-prod-final.log` · `logs/k6.log` |
| G-01 | PASS | `logs/prod-build.log` · `db-migrate-prod.log` · `db-ping-prod.log`(postgres OK) · `prod-stop.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | 다른 시험(A2A) 실패 | 워크로드 시크릿 주입 시험이 공유 DB 의 해시를 바꾼 채 종료 (시험 코드) | finally 에서 파일 기반 시크릿 해시로 복원 | round 2 |
| 1 | (설계) 다중 파드 동시 기동 시 마이그레이션·시드 경합 위험 | 각 파드 initContainer 가 동시에 실행 | PostgreSQL `pg_advisory_xact_lock` 로 직렬화 + PG 동시 3 프로세스 시험 | PASS |
| 1 | (설계) 데모 에이전트 자격증명이 공유 볼륨 파일에 의존 | K8s 는 파드 간 RWO 볼륨 공유 불가 | `A2A_DEMO_CLIENT_SECRET` env 주입(시드가 같은 값으로 해시 설정) | PASS |
| smoke 1 | P13-03 이후 연쇄 실패(토큰 null) | 이전 리허설의 admin 계정이 이미 있어 BOOTSTRAP 비밀번호가 적용되지 않음 (`_ensure_user` 는 기존 계정 유지 — 의도된 동작) | 전용 `rehearsal` 계정에 임시 비밀번호 설정 → 종료 시 비활성화, 정리용 rm 은 root 로 | smoke 2 11/11 |
| 2 | `test_qa_template_sse_end_to_end` 간헐 실패 | run.completed 이벤트를 보낸 뒤에야 실행 단위 MCP 세션을 닫음 → 완료 직후 조회 시 closed_at 없음 | 완료 알림 전에 MCP 세션 종료 | 3회 연속 PASS |
| 3 | `test_perf` MCP 28ms (1회) | test 모드 SimpleSpanProcessor 가 요청 안에서 스팬을 동기 저장 — 변동 큼 | NFR 판정은 prod 실측으로, pytest 는 목표 2배 회귀 감시 | PASS |

## 비고 / Notes

- 실제 Kubernetes 설치는 하지 않았다(현재 kubectl 컨텍스트가 다른 프로젝트 클러스터). 차트는 lint·렌더·오프라인 정합성으로 검증하고,
  런타임 조건(읽기 전용 루트 FS·비루트 uid 10001·/tmp·/app/data 만 쓰기)은 같은 이미지를 `docker run --read-only` 로 재현해 확인(P13-11).
- 이미지 포함 번들의 `docker save/load` 실행은 호스트 CLI 에서 수행(경로는 11th pytest 로 검증).

## 첨부 / Logs

- `logs/` (helm-check · make-test · make-test-pg · perf-inprocess · prod-build · db-migrate-prod · db-ping-prod · smoke-prod-run1/2/final · k6 · prod-stop)
- `smoke-prod.sh`
