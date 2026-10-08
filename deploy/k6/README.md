# k6 부하 시험 — NFR-P1

| 지표 | 목표 | 판정 |
|------|------|------|
| Unified Model API 추가 지연 p95 | ≤ 30ms | `http_req_duration{scenario:model}` (mock 공급자 = 업스트림 0) |
| MCP 프록시 추가 지연 p95 | ≤ 20ms | trace: 게이트웨이 서버 스팬 − 업스트림 MCP 서버 스팬 (`test/test_perf.py` 와 같은 정의) |
| 첫 토큰 p95 | ≤ 2s | `anx_first_token_ms` |

```sh
# prod 리허설 스택(make prod-all) 대상 — compose 네트워크 안에서 실행 (호스트 설치 불필요)
docker run --rm --network wise-agentnexus-prod_edge -e BASE=http://api:8000 -e TOKEN="$TOKEN" \
  -v "$PWD/deploy/k6:/k6" grafana/k6 run /k6/gateway.js
```

## NFR-S1 · NFR-A1

| 스크립트 | 목적 | 판정 |
|----------|------|------|
| `sessions.js` | 동시 1,000 MCP 세션(initialize → Mcp-Session-Id 유지 → 10초마다 tools/call) | 오류율 < 1% · `anx_session_call_ms` p95 < 1s |
| `chaos.js` | 일정 부하(모델 20 rps + MCP 10 rps) — 바깥 스크립트가 장애를 주입하는 동안 요청별 결과를 `--out json` 으로 | `test/impl/25th/analyze.py` 가 구간 가용성·복구 시간 계산 |

```sh
# 주체별 기본 한도(50 rps)를 넘지 않도록 Access Token 여러 개를 쉼표로 (TOKENS)
docker run --rm --network wise-agentnexus-prod_edge -e BASE=http://api:8000 -e TOKENS="$TOKS" -e SESSIONS=1000 \
  -v "$PWD/deploy/k6:/k6:ro" grafana/k6:1.0.0 run /k6/sessions.js
# 장애 주입 전체 절차(워커 종료·업스트림 장애·Valkey·DB 재시작)와 판정 — test/impl/25th/chaos-prod.sh
```

같은 Docker VM 에서 다른 부하가 돌면 꼬리 지연(p95)이 크게 흔들린다 — 단일 클라이언트(`SESSIONS=1`)로 요청당 비용을 함께 기록한다.
