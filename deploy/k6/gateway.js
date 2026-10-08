// NFR-P1 게이트웨이 지연 부하 시험 (k6) — Unified Model API · MCP 프록시 · 첫 토큰(SSE)
// 실행: docker run --rm --network <compose 네트워크> -e BASE=http://api:8000 -e TOKEN=<액세스 토큰> \
//         -v "$PWD/deploy/k6:/k6" grafana/k6 run /k6/gateway.js
// mock 공급자(지연 0) 기준이면 http_req_duration ≈ 게이트웨이 추가 지연. 실제 공급자에서는 업스트림 시간이 더해진다.
import http from 'k6/http'
import { check } from 'k6'
import { Trend } from 'k6/metrics'

const BASE = __ENV.BASE || 'http://127.0.0.1:8800'
const MODEL = __ENV.MODEL || 'fast-commercial'
const auth = { Authorization: `Bearer ${__ENV.TOKEN}`, 'Content-Type': 'application/json' }
const ttft = new Trend('anx_first_token_ms', true)

export const options = {
  // 기본 한도(주체별 50 rps) 안에서 측정 — 합계 32 rps. 처리량 시험은 RATE 를 올리고 한도를 조정한다.
  scenarios: {
    model: { executor: 'constant-arrival-rate', rate: Number(__ENV.RATE || 15), timeUnit: '1s', duration: __ENV.DURATION || '30s', preAllocatedVUs: 10, exec: 'model' },
    mcp: { executor: 'constant-arrival-rate', rate: Number(__ENV.RATE || 15), timeUnit: '1s', duration: __ENV.DURATION || '30s', preAllocatedVUs: 10, exec: 'mcp' },
    stream: { executor: 'constant-arrival-rate', rate: 2, timeUnit: '1s', duration: __ENV.DURATION || '30s', preAllocatedVUs: 4, exec: 'stream' },
  },
  thresholds: {
    'http_req_duration{scenario:model}': ['p(95)<30'],     // 게이트웨이 추가 지연 p95 ≤ 30ms (mock 공급자)
    'http_req_duration{scenario:mcp}': ['p(95)<60'],       // MCP 전체(업스트림 데모 서버 포함) — 순수 추가 지연은 trace 로 판정(≤ 20ms)
    anx_first_token_ms: ['p(95)<2000'],                    // 첫 토큰 p95 ≤ 2s
    'checks': ['rate>0.99'],
  },
}

export function model() {
  const res = http.post(`${BASE}/v1/chat/completions`, JSON.stringify({ model: MODEL, messages: [{ role: 'user', content: 'ping' }], max_tokens: 8 }),
    { headers: { ...auth, 'X-AgentNexus-Data-Class': 'public' } })
  check(res, { 'model 200': (r) => r.status === 200 })
}

export function mcp() {
  const body = { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'search_issues', arguments: { query: '결제' } } }
  const res = http.post(`${BASE}/mcp/jira`, JSON.stringify(body), { headers: { ...auth, Accept: 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25' } })
  check(res, { 'mcp 200': (r) => r.status === 200 && !String(r.body).includes('"error"') })
}

export function stream() {
  const res = http.post(`${BASE}/v1/chat/completions`, JSON.stringify({ model: MODEL, stream: true, messages: [{ role: 'user', content: '스트리밍 시험' }] }),
    { headers: { ...auth, 'X-AgentNexus-Data-Class': 'public', Accept: 'text/event-stream' } })
  check(res, { 'stream 200': (r) => r.status === 200 && String(r.body).includes('[DONE]') })
  ttft.add(res.timings.waiting) // TTFB = 첫 SSE 청크까지 (k6 는 본문 완료 후 반환 — waiting 이 첫 바이트)
}
