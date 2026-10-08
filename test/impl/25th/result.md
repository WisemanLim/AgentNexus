# 시험 결과 / Test Result — impl 25th (Epic 25 NFR 실측 — S1·A1)

- 일자 Date: 2026-10-07
- 대상 Target(profile): prod 리허설 — PostgreSQL 17 · uvicorn 2 workers · Valkey · mock 공급자 · k6 1.0.0 (compose 네트워크 안)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 13 (pytest 1 · prod live 12), 통과 Pass: 13, 실패 Fail: 0, 최종 Verdict: **PASS** (round 2·3)
- **NFR-A1 장애 주입(180초 · 모델 20 rps + MCP 10 rps · 장애 4건)** — 전체 가용성 **99.83%** (5,401건 중 실패 9)

| 장애 | 구간 가용성 | 실패 | 복구 |
|------|------------|------|------|
| uvicorn 워커 1개 SIGKILL | 99.92% | 1 (죽은 워커의 진행 중 요청) | 1초 — 남은 워커가 처리, 감독자가 재기동 |
| `auto` 1순위 업스트림 장애 20초 | **100%** | 0 | 0초 — 폴백(결정 로그 `fallback_used`) |
| Valkey 재시작 | **100%** (round 1: 99.68%·실패 3) | 0 | 0초 — 보강 후 워커 로컬 한도로 버팀 |
| PostgreSQL 재시작 | 99.41% | 8 (재시작 중 연결 거부) | 2초 — `pool_pre_ping` 재연결 |

- **NFR-S1 동시 1,000 세션** — 오류 0~0.01% · 세션 1,000 · 도구 호출 p95 **583ms / 917ms**(임계 < 1s 통과) — round 2 첫 실행은 p95 6.12s 로 임계 초과(오류 0).
  같은 시각 단일 클라이언트 p95 **24ms**(p50 17ms) — 요청당 비용은 18th 와 같고, 꼬리 지연은 같은 Docker VM 의 다른 프로젝트 부하(`logs/host-contention.log`: 다른 스택 e2e 370% CPU) 때 커진다.
  18th(조용한 호스트) p95 80ms. 스레드풀·DB 풀 확대 실험(p95 616ms)은 효과가 분명하지 않아 되돌렸다.

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C25-01 | PASS (round 1) | `test/test_nfr.py` — Valkey 장애 시 워커 로컬 버킷 |
| P25-01·02 | PASS (round 3) | round 1 PASS(p95 583ms) · round 2 임계 초과(p95 6.12s, 호스트 경합) → 단독 재실행 `logs/s1-rerun/` PASS(p95 917ms) |
| P25-03 ~ P25-12 | PASS (round 1·2) | `logs/chaos-run1.json` · `logs/chaos.json`(round 2, Valkey 보강 후) |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| live 1 | Valkey 재시작 중 게이트웨이 요청 3건 실패 (가용성 결함) | 한도 검사가 Valkey 연결 오류를 그대로 올림 — 한도 저장소가 단일 장애점 | `RedisBackend._guard`: 연결 실패 시 5초 동안 워커 로컬 버킷(한도는 워커 단위로 느슨, 차단 안 함)·경고 로그·`backend_status.degraded` | round 2 — 100% |
| live 2 | S1 p95 6.12s (오류 0) | 같은 호스트의 다른 스택 부하(측정 환경) — 단일 클라이언트 p95 24ms·서버 스팬 p50 32ms 로 요청당 비용 회귀 아님 확인 | 코드 변경 없음(풀 확대 실험은 되돌림), 재측정 | round 3 PASS |

## 측정 메모

- 99.95%(월간) 자체는 짧은 시험으로 증명할 수 없다. 장애 4건을 3분 안에 몰아 넣은 보수적 조건에서 99.83%, 장애별 복구 0~2초.
- DB 재시작 중 실패(8건)는 단일 PostgreSQL 의 한계 — HA(복제·자동 장애 조치)로 해결할 범위.
- 측정 전후 감사 체인 무결(P25-11)·공급자 설정 원복(P25-12)·부하용 Access Token 50개 폐기·임시 계정 비활성화.

## 첨부 / Logs

- `logs/` (local-stop · prod-build(·round2~4) · db-migrate-prod(·round2) · db-ping-prod · chaos-run1~2 · k6-sessions(-run1) · k6-chaos(-run1) · chaos(-run1).json · events(-run1) · points(-run1).json.gz · s1-rerun/ · s1-tuned/ · s1-control100/ · s1-single/ · host-contention · prod-stop · local-restore)
