// NFR-S1 동시 세션 시험 (k6) — 1,000 개의 MCP 클라이언트 세션을 동시에 유지하며 도구를 호출한다.
// 각 VU = 세션 1개(initialize 1회 → Mcp-Session-Id 유지 → 생각 시간 THINK_S 마다 tools/call). 주체별 기본 한도(50 rps)를
// 넘지 않도록 TOKENS(쉼표 구분, Access Token 여러 개)를 VU 에 나눠 준다.
// 실행: docker run --rm --network <compose 네트워크> -e BASE=http://api:8000 -e TOKENS=t1,t2,... \
//         -v "$PWD/deploy/k6:/k6:ro" grafana/k6:1.0.0 run /k6/sessions.js
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter, Trend } from 'k6/metrics'

const BASE = __ENV.BASE || 'http://127.0.0.1:8800'
const TOKENS = (__ENV.TOKENS || __ENV.TOKEN || '').split(',').filter(Boolean)
const SESSIONS = Number(__ENV.SESSIONS || 1000)
const THINK_S = Number(__ENV.THINK_S || 10)
const callMs = new Trend('anx_session_call_ms', true)
const opened = new Counter('anx_sessions_opened')

export const options = {
  scenarios: {
    sessions: {
      executor: 'ramping-vus', startVUs: 0, gracefulRampDown: '5s',
      stages: [{ duration: __ENV.RAMP || '30s', target: SESSIONS }, { duration: __ENV.HOLD || '60s', target: SESSIONS },
               { duration: '5s', target: 0 }],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],          // 오류율 1% 미만
    anx_session_call_ms: ['p(95)<1000'],     // 동시 1,000 세션 중 도구 호출 p95 1s 미만
    checks: ['rate>0.99'],
  },
}

let sid = null

function headers() {
  const token = TOKENS[(__VU - 1) % TOKENS.length]
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
           'MCP-Protocol-Version': '2025-11-25', ...(sid ? { 'Mcp-Session-Id': sid } : {}) }
}

export default function () {
  if (!sid) {
    const res = http.post(`${BASE}/mcp/jira`, JSON.stringify({ jsonrpc: '2.0', id: 0, method: 'initialize', params: {
      protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'k6-session', version: '1' } } }), { headers: headers() })
    check(res, { 'initialize 200': (r) => r.status === 200 && !!r.headers['Mcp-Session-Id'] })
    sid = res.headers['Mcp-Session-Id'] || null
    if (sid) opened.add(1)
    sleep(Math.random() * THINK_S)  // 세션마다 호출 시점을 흩뜨린다
    return
  }
  const res = http.post(`${BASE}/mcp/jira`, JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'search_issues', arguments: { query: '결제', limit: 1 } } }), { headers: headers() })
  callMs.add(res.timings.duration)
  check(res, { 'tools/call 200': (r) => r.status === 200 && !(r.json() || {}).error })
  sleep(THINK_S)
}
