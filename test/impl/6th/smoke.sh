#!/bin/sh
# impl 6th — MCP 경로 스모크 (local 스택: api + mcp-servers + valkey). 사용: sh test/impl/6th/smoke.sh [BASE]
set -u
BASE="${1:-http://127.0.0.1:8800}"
DCL="docker compose -p wise-agentnexus-local -f docker-compose.yml -f docker-compose.local.yml -f compose/opa.yml -f compose/valkey.yml --env-file .env.local"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
USR=$(tok user); OPS=$(tok operator)
mcp() { path="$1"; shift; curl -s -H "Authorization: Bearer $USR" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' "$@" "$BASE/mcp/$path"; }

# 기동 후 참조 서버 자동 갱신(백그라운드) 대기
for i in $(seq 1 30); do
  n=$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/mcp-servers" | jq '[.items[] | select(.builtin and .health=="healthy")] | length')
  [ "$n" = "4" ] && break; sleep 2
done
check S6-01-builtin-healthy "$n" "4"
check S6-02-tools-list "$(mcp jira -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq '[.result.tools[].name] | index("search_issues") != null')" "true"
R=$(mcp jira -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_issues","arguments":{"query":"결제"}}}')
check S6-03-tool-call "$(echo "$R" | jq -r '.result.structuredContent.result[0].key')" "ORI-1440"
A=$(mcp confluence -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"create_page","arguments":{"space":"ORION","title":"스모크","body":"x"}}}')
check S6-04-approval-required "$(echo "$A" | jq -r .error.code)" "-32001"
AP=$(echo "$A" | jq -r .error.data.approval_id)
curl -s -X POST -H "Authorization: Bearer $USR" "$BASE/api/v1/approvals/$AP/approve" >/dev/null
C=$(mcp confluence -H "X-AgentNexus-Approval: $AP" -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"create_page","arguments":{"space":"ORION","title":"스모크","body":"x"}}}')
check S6-05-approved-identity "$(echo "$C" | jq -r .result.structuredContent.created_by)" "사용자"
check S6-06-aggregate "$(mcp _all -d '{"jsonrpc":"2.0","id":5,"method":"tools/list"}' | jq '[.result.tools[].name] | index("dynatrace.list_problems") != null')" "true"
# 우회 차단 (FR-G00): mcp-servers 는 호스트 포트 없음 + edge 네트워크에서 이름 해석 불가, api(게이트웨이)에서만 도달
check S6-07-no-host-port "$(docker port wise-agentnexus-local-mcp-servers-1 | wc -l | tr -d ' ')" "0"
EDGE=$(docker run --rm --network wise-agentnexus-local_edge curlimages/curl:8.10.1 -s -o /dev/null -w '%{http_code}' --max-time 3 http://mcp-servers:8100/healthz 2>/dev/null)
check S6-08-edge-bypass-blocked "$EDGE" "000"
check S6-09-gateway-reaches "$($DCL exec -T api python -c "import urllib.request;print(urllib.request.urlopen('http://mcp-servers:8100/healthz').status)")" "200"
# 직접 호출(토큰 없음)은 리소스 서버가 401 + RFC 9728
check S6-10-direct-401 "$($DCL exec -T api python -c "
import urllib.request, urllib.error
try: urllib.request.urlopen(urllib.request.Request('http://mcp-servers:8100/jira/mcp', data=b'{}', method='POST'))
except urllib.error.HTTPError as e: print(e.code, 'resource_metadata' in e.headers.get('www-authenticate',''))")" "401 True"
# 공식 MCP 클라이언트(fastmcp Client · Streamable HTTP) 상호운용 — 게이트웨이를 표준 MCP 서버로 사용
INTEROP=$($DCL exec -T -e TOKEN="$USR" api python -c "
import asyncio, os
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
async def main():
    t = StreamableHttpTransport('http://api:8000/mcp/dynatrace', headers={'Authorization': 'Bearer ' + os.environ['TOKEN']})
    async with Client(t) as c:
        tools = await c.list_tools()
        r = await c.call_tool('get_problem', {'problem_id': 'P-26093'})
        print(len(tools) >= 3, r.structured_content['service'])
asyncio.run(main())" 2>&1 | tail -1)
check S6-11-official-client-interop "$INTEROP" "True orders-api"
check S6-12-exchange-trace "$(curl -s -H "Authorization: Bearer $(tok security)" "$BASE/api/v1/identity/token-exchanges?subject=usr_user&audience=mcp:confluence" | jq -r '[.items[].scopes[]] | index("pages:write") != null')" "true"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
