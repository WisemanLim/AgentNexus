# impl 25th — NFR 실측 (Epic 25 · D13-② · NFR-S1·NFR-A1)

## 범위

| 항목 | 방법 |
|------|------|
| NFR-S1 동시 1,000 세션 | `deploy/k6/sessions.js` — 1,000 VU 가 MCP 세션(initialize → Mcp-Session-Id 유지)을 열고 10초마다 tools/call (RAMP 30s · HOLD 60s). Access Token 50개로 주체별 한도 안 (18th 실측 회귀) |
| NFR-A1 장애 중 가용성 | `deploy/k6/chaos.js` — 모델(`auto`) 20 rps + MCP 10 rps 를 180초 유지하며 `chaos-prod.sh` 가 장애 주입: ① uvicorn 워커 1개 SIGKILL ② `auto` 1순위 모델의 업스트림 장애 20초(mock `fail_models`) ③ Valkey 재시작 ④ PostgreSQL 재시작 |
| 분석 | `analyze.py` — k6 `--out json` 의 요청별 check 와 장애 시각으로 구간 가용성·실패 건수·복구 시간(첫 실패 후 연속 3초 성공률 ≥ 99%) |
| 보강 | Valkey 가 끊기면 한도 검사가 오류를 내지 않고 5초 동안 워커 로컬 버킷으로 동작(차단 대신 느슨) — `RedisBackend._guard` · `backend_status.degraded` |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C25-01 | 닫힌 Valkey → 허용·로컬 한도 유지·5초 창 안 재연결 없음·창 후 재시도 | `test_rate_limiter_survives_valkey_outage` | pytest |
| P25-01·02 | S1 — k6 임계(오류 < 1% · 도구 호출 p95 < 1s) 통과 · 세션 1,000 | `chaos-prod.sh` | live |
| P25-03·04 | 워커 강제 종료 구간 가용성 ≥ 99% · 복구 ≤ 10초 | 〃 | live |
| P25-05·06 | 업스트림 장애 구간 가용성 ≥ 99%(폴백) · 폴백 결정 기록 | 〃 | live |
| P25-07 | Valkey 재시작 구간 가용성 ≥ 99% | 〃 | live |
| P25-08 | DB 재시작 복구 ≤ 30초 | 〃 | live |
| P25-09 | 전체 가용성 ≥ 97% (180초 안 장애 4건 압축) | 〃 | live |
| P25-10~12 | 장애 후 헬스 ok · 감사 체인 무결 · 공급자 설정 원복 | 〃 | live |
| R | 전체 회귀 | logs | make |

## 환경 가정

- 99.95%(월간)는 3분 시험으로 증명할 수 없다 — 대신 장애 유형별 가용성·복구 시간을 측정해 근거로 쓴다. 장애 4건을 180초 안에 몰아 넣었으므로 전체 가용성은 실제 운영보다 보수적이다.
- PostgreSQL·Valkey 는 단일 인스턴스(리허설). DB 재시작 중 실패는 HA(복제·장애 조치) 구성이 해결할 범위로 남긴다.
- mock 공급자(업스트림 지연 0) 기준이다.
