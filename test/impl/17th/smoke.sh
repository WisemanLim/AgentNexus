#!/bin/sh
# impl 17th — live 스모크 (local 스택). 사용: sh test/impl/17th/smoke.sh  (프로젝트 루트, make local-all 선행)
BASE=${BASE:-http://127.0.0.1:8800}
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=demo | jq -r .access_token; }
OP=$(tok operator); DEV=$(tok dev)
MCP_H='-H Accept:application/json,text/event-stream -H Content-Type:application/json'
call() { curl -s -o /dev/null -w "%{http_code}" $MCP_H -H "Authorization: Bearer $DEV" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_issues","arguments":{"query":"결제","limit":1}}}' "$BASE/mcp/jira"; }

# S17-01 compose — ollama 서비스가 두 모드 구성에 유효
EXTRA=$(for f in compose/*.yml; do printf -- "-f %s " "$f"; done)
for m in local prod; do
  S=$(COMPOSE_PROFILES=demo,ollama docker compose --env-file .env.$m -f docker-compose.yml -f docker-compose.$m.yml $EXTRA config --services 2>/dev/null | grep -c '^ollama$')
  check "S17-01-compose-ollama-$m" "$S" "1"
done
# S17-02 매핑 문서 — OWASP T1~T15 · PIPA 조항
check S17-02-owasp-t1-t15 "$(grep -cE '^\| T([1-9]|1[0-5]) ' COMPLIANCE.md)" "15"
check S17-03-pipa-articles "$(grep -cE '^\| 제(3|17|21|23|29|34|35|37)조' COMPLIANCE.md)" "8"
# S17-04 도구 한도 — 첫 호출 200, 초과 429, 한도 삭제 후 복구
LID=$(curl -s -H "Authorization: Bearer $OP" -H 'Content-Type: application/json' -d '{"name":"smoke17 도구","scope_type":"user","scope":"usr_dev","target_type":"tool","target":"jira.search_issues","rps":0.01,"burst":1}' "$BASE/api/v1/gateway/limits" | jq -r .id)
check S17-04-first-call "$(call)" "200"
check S17-05-over-limit-429 "$(call)" "429"
curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $OP" "$BASE/api/v1/gateway/limits/$LID"
sleep 1
check S17-06-after-delete "$(call)" "200"
# S17-07 스팬 마스킹 — 이메일이 든 요청 경로가 trace 에 마스킹되어 저장
TID=$(curl -s -D- -o /dev/null -H "Authorization: Bearer $DEV" "$BASE/v1/catalog/kim@example.com" | tr -d '\r' | awk -F': ' 'tolower($1)=="x-agentnexus-trace-id"{print $2}')
for i in $(seq 1 15); do  # BatchSpanProcessor 주기 flush 대기
  TR=$(curl -s -H "Authorization: Bearer $OP" "$BASE/api/v1/traces/$TID"); echo "$TR" | grep -q '"url.path"' && break; sleep 1
done
check S17-07-span-masked-marker "$(echo "$TR" | grep -c 'MASKED:email')" "1"
check S17-07b-span-no-raw-email "$(echo "$TR" | grep -c 'kim@example.com')" "0"
# S17-08 거부 사유 필수 (카탈로그)
ITEM=$(curl -s -H "Authorization: Bearer $OP" "$BASE/v1/catalog" | jq -r '.items[0].id')
check S17-08-reject-reason "$(curl -s -H "Authorization: Bearer $OP" -H 'Content-Type: application/json' -d '{}' "$BASE/v1/catalog/$ITEM/reject" | jq -r .code)" "reason_required"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
