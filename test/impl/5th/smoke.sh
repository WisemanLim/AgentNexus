#!/bin/sh
# impl 5th — Unified Model API 스모크 (local 스택). 사용: sh test/impl/5th/smoke.sh [BASE]
set -u
BASE="${1:-http://127.0.0.1:8800}"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
USR=$(tok user); OPS=$(tok operator)
chat() { curl -s -D /tmp/anx5.h -H "Authorization: Bearer $USR" -H 'Content-Type: application/json' "$@" "$BASE/v1/chat/completions"; }

check S5-01-models "$(curl -s -H "Authorization: Bearer $USR" "$BASE/v1/models" | jq '[.data[].id] | contains(["domain-local","fast-commercial","auto"])')" "true"
R=$(chat -H 'X-AgentNexus-Data-Class: public' -d '{"model":"fast-commercial","messages":[{"role":"user","content":"게이트웨이 스모크"}]}')
check S5-02-chat "$(echo "$R" | jq -r .model)" "fast-commercial"
check S5-03-usage "$(echo "$R" | jq '.usage.total_tokens > 0')" "true"
R=$(chat -d '{"model":"smart-commercial","messages":[{"role":"user","content":"내부 데이터 요약"}]}')
check S5-04-internal-reroute "$(echo "$R" | jq -r .model):$(grep -i '^x-agentnexus-fallback' /tmp/anx5.h | tr -d '\r' | awk '{print $2}')" "domain-local:true"
S=$(chat -H 'X-AgentNexus-Data-Class: public' -d '{"model":"auto","stream":true,"messages":[{"role":"user","content":"스트림"}]}')
check S5-05-stream-done "$(echo "$S" | grep -c '^data: \[DONE\]$')" "1"
check S5-06-stream-model "$(echo "$S" | grep '^data: {' | head -n 1 | sed 's/^data: //' | jq -r .model)" "fast-commercial"
check S5-07-provider-test "$(curl -s -X POST -H "Authorization: Bearer $OPS" "$BASE/api/v1/providers/mock/test" | jq -r .ok)" "true"
check S5-08-decisions "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/gateway/routing/decisions?kind=model&fallback=true" | jq '.items | length > 0')" "true"
check S5-09-no-auth "$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{"model":"auto","messages":[{"role":"user","content":"x"}]}' "$BASE/v1/chat/completions")" "401"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
