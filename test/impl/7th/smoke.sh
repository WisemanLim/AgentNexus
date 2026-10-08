#!/bin/sh
# impl 7th — A2A 경로 스모크 (local 스택: api + mcp-servers + a2a-demo). 사용: sh test/impl/7th/smoke.sh [BASE]
set -u
BASE="${1:-http://127.0.0.1:8800}"
DCL="docker compose -p wise-agentnexus-local -f docker-compose.yml -f docker-compose.local.yml -f compose/opa.yml -f compose/valkey.yml --env-file .env.local"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
USR=$(tok user); OPS=$(tok operator)
a2a() { curl -s -H "Authorization: Bearer $USR" -H 'Content-Type: application/json' -d "$1" "$BASE/a2a/${2:-observability-analyst}"; }
M() { printf '{"jsonrpc":"2.0","id":1,"method":"%s","params":{"message":{"kind":"message","messageId":"%s","role":"user","parts":[{"kind":"text","text":"%s"}]}}}' "$1" "$(uuidgen)" "$2"; }

for i in $(seq 1 30); do
  v=$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/a2a/agents/observability-analyst" | jq -r '.protocol_versions | join(",")')
  [ "$v" = "0.3,1.0" ] && break; sleep 2
done
check S7-01-card-cached "$v" "0.3,1.0"
check S7-02-gateway-card "$(curl -s "$BASE/a2a/observability-analyst/.well-known/agent-card.json" | jq -r .url)" "$BASE/a2a/observability-analyst"
R=$(a2a "$(M message/send 'orders-api 장애 원인 분석')")
check S7-03-send-completed "$(printf '%s' "$R" | jq -r .result.status.state)" "completed"
check S7-04-via-gateway "$(printf '%s' "$R" | jq -r '.result.artifacts[] | select(.name=="incident-data") | .parts[0].data.via')" "gateway"
S=$(a2a "$(M message/stream 'payment-gateway 스트림 분석')")
check S7-05-stream-final "$(echo "$S" | grep '^data:' | tail -n 1 | sed 's/^data: //' | jq -r '.result.status.state')" "completed"
R10=$(a2a "$(M SendMessage 'settlement-batch')" | jq -r .result.task.status.state)
check S7-06-v10 "$R10" "TASK_STATE_COMPLETED"
check S7-07-skill-route "$(a2a "$(M message/send 'orders-api')" _skill/incident-analysis | jq -r .result.status.state)" "completed"
check S7-08-edge-bypass-blocked "$(docker run --rm --network wise-agentnexus-local_edge curlimages/curl:8.10.1 -s -o /dev/null -w '%{http_code}' --max-time 3 http://a2a-demo:8200/healthz 2>/dev/null)" "000"
INTEROP=$($DCL exec -T -e TOKEN="$USR" api python -c "
import asyncio, os, uuid, httpx
from a2a.client import ClientConfig, create_client
from a2a.types import Message, Part, Role, SendMessageRequest
async def main():
    h = {'Authorization': 'Bearer ' + os.environ['TOKEN']}
    http = httpx.AsyncClient(headers=h, timeout=60)
    c = await create_client('http://api:8000/a2a/observability-analyst', client_config=ClientConfig(httpx_client=http, streaming=True), resolver_http_kwargs={'headers': h})
    last = None
    async for ev in c.send_message(SendMessageRequest(message=Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER, parts=[Part(text='orders-api')]))):
        last = ev
    st = last.task.status.state if last.HasField('task') else last.status_update.status.state
    print('sdk', st)
asyncio.run(main())" 2>&1 | tail -1)
check S7-09-a2a-sdk-streaming-interop "$INTEROP" "sdk 3"
check S7-10-tasks-api "$(curl -s -H "Authorization: Bearer $USR" "$BASE/api/v1/a2a/tasks?agent_id=observability-analyst" | jq '[.items[] | select(.state=="completed")] | length > 0')" "true"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
