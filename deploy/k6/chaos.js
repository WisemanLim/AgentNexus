// NFR-A1 장애 주입 중 가용성 (k6) — 일정 도착률로 모델·MCP 호출을 유지하고, 같은 시간에 워커·공급자·Valkey·DB 장애를 주입한다.
// 장애 주입은 바깥 스크립트(test/impl/25th/chaos-prod.sh)가 하고, 이 스크립트는 요청별 결과(checks)를 시각과 함께 내보낸다.
// 실행: docker run --rm --network <compose 네트워크> -e BASE=http://api:8000 -e TOKENS=t1,t2,... \
//         -v "$PWD/deploy/k6:/k6:ro" grafana/k6:1.0.0 run --out json=/out/points.json /k6/chaos.js
import http from 'k6/http'
import { check } from 'k6'

const BASE = __ENV.BASE || 'http://127.0.0.1:8800'
const TOKENS = (__ENV.TOKENS || __ENV.TOKEN || '').split(',').filter(Boolean)
const DURATION = __ENV.DURATION || '180s'
const tok = () => TOKENS[Math.floor(Math.random() * TOKENS.length)]

export const options = {
  scenarios: {
    model: { executor: 'constant-arrival-rate', rate: Number(__ENV.MODEL_RPS || 20), timeUnit: '1s', duration: DURATION, preAllocatedVUs: 40, maxVUs: 200, exec: 'model' },
    mcp: { executor: 'constant-arrival-rate', rate: Number(__ENV.MCP_RPS || 10), timeUnit: '1s', duration: DURATION, preAllocatedVUs: 20, maxVUs: 100, exec: 'mcp' },
  },
  // 판정은 바깥 분석(구간별 가용성·복구 시간)으로 — 여기서는 전체 성공률 하한만
  thresholds: { checks: ['rate>0.90'] },
}

export function model() {
  const res = http.post(`${BASE}/v1/chat/completions`, JSON.stringify({ model: 'auto', messages: [{ role: 'user', content: 'chaos' }], max_tokens: 8 }),
    { headers: { Authorization: `Bearer ${tok()}`, 'Content-Type': 'application/json', 'X-AgentNexus-Data-Class': 'public' }, timeout: '10s' })
  check(res, { ok: (r) => r.status === 200 })
}

export function mcp() {
  const body = { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'search_issues', arguments: { query: '결제', limit: 1 } } }
  const res = http.post(`${BASE}/mcp/jira`, JSON.stringify(body), { headers: { Authorization: `Bearer ${tok()}`, 'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25' }, timeout: '10s' })
  check(res, { ok: (r) => r.status === 200 && !String(r.body).includes('"error"') })
}
