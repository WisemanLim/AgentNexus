#!/bin/sh
# impl 14th — SCR-C08 live (local 스택): Host 동의(sampling 허용·거부·elicitation) · 공식 fastmcp Client 중계 · `/` 메뉴 · 감사
set -u
BASE="http://127.0.0.1:8800"
DC="docker compose -p wise-agentnexus-local"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
U=$(tok user); D=$(tok dev); A=$(tok admin); SEC=$(tok security)
MCP="-H Accept:application/json,text/event-stream -H MCP-Protocol-Version:2025-11-25 -H Content-Type:application/json"
OUT=$(mktemp)
call() { curl -s -H "Authorization: Bearer $U" $MCP -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":$2}}" "$BASE/mcp/internal-orders" > "$OUT"; }
pending() { for i in $(seq 1 60); do ID=$(curl -s -H "Authorization: Bearer $U" "$BASE/api/v1/approvals?status=pending" | jq -r "[.items[] | select(.kind==\"$1\")][0].id // empty"); [ -n "$ID" ] && { echo "$ID"; return; }; sleep 0.3; done; }
consent() { curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $U" -H 'Content-Type: application/json' -d "$2" "$BASE/api/v1/approvals/$1/consent"; }

check S14-01-migration "$(curl -s -H "Authorization: Bearer $A" "$BASE/api/v1/admin/updates" | jq -r .migration)" "0006"
# 1) Host 동의 — 허용(내부 데이터 → 로컬 모델)
call classify_orders '{}' & sleep 0.5; AP=$(pending sampling)
check S14-02-routing-preview "$(curl -s -H "Authorization: Bearer $U" "$BASE/api/v1/approvals/$AP" | jq -r .result.routing.model)" "domain-local"
check S14-03-allow "$(consent "$AP" '{"decision":"allow"}')" "200"; wait
check S14-04-sampled "$(jq -r '.result.structuredContent.model + ":" + (.result.isError|tostring)' "$OUT")" "domain-local:false"
# 2) 거부 → 도구 오류
call classify_orders '{}' & sleep 0.5; AP=$(pending sampling); consent "$AP" '{"decision":"deny"}' >/dev/null; wait
check S14-05-deny "$(jq -r '.result.isError' "$OUT")" "true"
# 3) elicitation — 스키마 검증 후 수락
call request_return '{"order_id":"O-2404"}' & sleep 0.5; AP=$(pending elicitation)
check S14-06-elicit-invalid "$(consent "$AP" '{"decision":"accept","content":{"reason":"분실","quantity":1}}')" "400"
consent "$AP" '{"decision":"accept","content":{"reason":"파손","quantity":2}}' >/dev/null; wait
check S14-07-elicit-accept "$(jq -c '.result.structuredContent | [.status, .reason, .quantity]' "$OUT")" '["requested","파손",2]'
# 4) 하류 중계 — 공식 fastmcp Client(sampling·elicitation 능력) 가 게이트웨이를 통해 직접 응답
$DC cp test/impl/14th/relay_client.py api:/tmp/relay_client.py >/dev/null 2>&1
R=$($DC exec -T api python /tmp/relay_client.py "$D" 2>/dev/null | tail -1)
check S14-08-relay-sampling "$(printf '%s' "$R" | jq -r '.classify.model + "|" + .classify.classification')" "client-side-model|외부 클라이언트 모델이 분류"
check S14-09-relay-elicitation "$(printf '%s' "$R" | jq -c '[.asked, .return.reason]')" '[["sampling","elicitation"],"오배송"]'
# 5) `/` 메뉴
check S14-10-slash-context "$(curl -s -H "Authorization: Bearer $U" "$BASE/api/v1/chat/mcp/context?profile_id=demo-orders" | jq -c '[.servers, ([.resources[].uri]|sort), [.prompts[].name]]')" '[["internal-orders"],["orders://daily/2026-09-30","orders://policy/returns"],["anomaly_triage"]]'
# 6) 감사
check S14-11-audit "$(curl -s -H "Authorization: Bearer $SEC" "$BASE/api/v1/audit/events?action=mcp.&limit=50" | jq -r '[.items[].action] | (index("mcp.sampling") != null) and (index("mcp.relay") != null)')" "true"
check S14-12-chain "$(curl -s -H "Authorization: Bearer $SEC" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
rm -f "$OUT"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
