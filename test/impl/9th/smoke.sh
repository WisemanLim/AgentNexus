#!/bin/sh
# impl 9th — 관측·감사 스모크 (local 스택). 컨테이너 간 단일 trace(OTLP 수신) · 메트릭 · 로그 · 비용 · 리포트.
# prod 리허설: TOKEN=<admin 토큰> sh smoke.sh
set -u
BASE="${1:-http://127.0.0.1:8800}"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
USR="${TOKEN:-$(tok user)}"; OPS="${TOKEN:-$(tok operator)}"; SEC="${TOKEN:-$(tok security)}"; FIN="${TOKEN:-$(tok finops)}"
H="Authorization: Bearer $USR"
SID=$(curl -s -X POST -H "$H" -H 'Content-Type: application/json' -d '{"profile_id":"tpl-incident"}' "$BASE/api/v1/sessions" | jq -r .id)
RID=$(curl -s -X POST -H "$H" -H 'Content-Type: application/json' -d '{"content":"orders-api 장애 원인 분석"}' "$BASE/api/v1/sessions/$SID/messages" | jq -r .run_id)
for i in $(seq 1 100); do st=$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r .status); [ "$st" = "succeeded" ] && break; sleep 0.3; done
check S9-01-run "$st" "succeeded"
TID=$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r .trace_id)
for i in $(seq 1 20); do  # 스팬은 BatchSpanProcessor(기본 5s) → 내장 저장소·OTLP 수신으로 수 초 내 도착
  TR=$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/traces/$TID")
  SVCS=$(printf '%s' "$TR" | jq -r '.services | join(",")'); GEN=$(printf '%s' "$TR" | jq '.gen_ai_spans')
  case "$SVCS" in *agentnexus-a2a-demo*agentnexus-mcp-servers*) [ "$GEN" -ge 4 ] && break;; esac; sleep 1
done
check S9-02-cross-container-trace "$SVCS" "agentnexus,agentnexus-a2a-demo,agentnexus-mcp-servers"
check S9-03-genai-spans "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/traces/$TID" | jq '.gen_ai_spans >= 4')" "true"
check S9-04-ingested "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/observe/pipeline" | jq '.receiver.ingested > 0')" "true"
check S9-05-metrics-auth "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/metrics")" "401"
check S9-06-metrics "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/metrics" | grep -c '^anx_llm_tokens_total' | awk '{print ($1>0)}')" "1"
check S9-07-usage-team "$(curl -s -H "Authorization: Bearer $FIN" "$BASE/api/v1/usage?group_by=team" | jq '.scope + ":" + (.items|length>0|tostring)' -r)" "all:true"
check S9-08-summary "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/observe/summary" | jq '.calls > 0')" "true"
REP=$(curl -s -X POST -H "Authorization: Bearer $SEC" -H 'Content-Type: application/json' -d '{"actions":["token."]}' "$BASE/api/v1/audit/reports")
REP_ID=$(printf '%s' "$REP" | jq -r .id); SIG=$(printf '%s' "$REP" | jq -r .signature)
curl -s -H "Authorization: Bearer $SEC" "$BASE/api/v1/audit/reports/$REP_ID/download" -o /tmp/anx-report.jsonl
check S9-09-report-signature "$(jq -n --arg s "$SIG" --rawfile c /tmp/anx-report.jsonl '{signature:$s, content:$c}' | curl -s -X POST -H "Authorization: Bearer $SEC" -H 'Content-Type: application/json' -d @- "$BASE/api/v1/audit/reports/verify" | jq -r .valid)" "true"
check S9-10-chain-valid "$(curl -s -H "Authorization: Bearer $SEC" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
check S9-11-replay "$(curl -s -H "$H" "$BASE/api/v1/audit/replay/$SID" | jq '[.timeline[].kind] | index("a2a.task") != null')" "true"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
