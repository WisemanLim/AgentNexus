#!/bin/sh
# impl 29th — prod 리허설 (PostgreSQL · 2 workers): MCP 2026-07-28 확장 — modern 업스트림 발견·stateless 호출(세션 0) ·
# x-mcp-header(Mcp-Param-*) 검증·미러링 · subscriptions/listen 승인·도구 규칙 변경 알림 · progress SSE · MRTR(워커 간) · 강제 legacy 회귀.
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/29th/logs
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'
V=2026-07-28

PW=$(openssl rand -hex 16)
$DC exec -T -e REH_PW="$PW" api python - <<'PYEOF' >/dev/null
import os
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
with session_scope() as db:
    u = db.scalars(select(User).where(User.username == "rehearsal")).first()
    if u is None:
        u = User(id="usr_rehearsal", sub="usr_rehearsal", issuer=security.issuer(), username="rehearsal", name="rehearsal",
                 email="rehearsal@agentnexus.local", roles=["admin"], team="team-platform", password_hash="")
        db.add(u)
    u.roles, u.password_hash, u.status, u.tokens_not_before = ["admin"], security.hash_password(os.environ["REH_PW"]), "active", None
PYEOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
CODE=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=s29 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
A="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW
meta() {  # $1 caps json(기본 {}) $2 progressToken
  caps=${1:-}; [ -n "$caps" ] || caps='{}'
  tok=""; [ -n "${2:-}" ] && tok=",\"progressToken\":\"$2\""
  printf '"_meta":{"io.modelcontextprotocol/protocolVersion":"%s","io.modelcontextprotocol/clientCapabilities":%s%s}' "$V" "$caps" "$tok"
}
mcp() {  # $1 server $2 method $3 name(or -) $4 params-json(without _meta) [$5 extra header] [$6 caps json] [$7 progress token]
  NH=""; [ "$3" != "-" ] && NH="Mcp-Name: $3"
  curl -sN --max-time 60 -w '\n%{http_code}' -H "$A" -H "$J" -H 'Accept: application/json, text/event-stream' -H "MCP-Protocol-Version: $V" \
    -H "Mcp-Method: $2" ${NH:+-H "$NH"} ${5:+-H "$5"} -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$2\",\"params\":{$4${4:+,}$(meta "${6:-}" "${7:-}")}}" "$BASE/mcp/$1"
}
body() { printf '%s' "$1" | sed '$d'; }
code() { printf '%s' "$1" | tail -1; }
sse() { printf '%s' "$1" | sed -n 's/^data: //p'; }  # SSE → JSON 줄
legacy() {  # $1 server $2 tool-call params json → 결과 JSON (initialize → tools/call)
  SID=$(curl -s -D - -o /dev/null -H "$A" -H "$J" -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' \
    -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"legacy","version":"1"}}}' \
    "$BASE/mcp/$1" | tr -d '\r' | grep -i '^mcp-session-id:' | cut -d' ' -f2)
  curl -s -H "$A" -H "$J" -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' -H "Mcp-Session-Id: $SID" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":$2}" "$BASE/mcp/$1"
}
ups() { $DC exec -T api python -c "from app.db import session_scope; from app.models import McpSession
with session_scope() as db: print(db.query(McpSession).filter(McpSession.kind == 'upstream', McpSession.server_id == '$1').count())"; }
rule() { $DC exec -T api python -c "from app.db import session_scope; from app.models import ToolRule
with session_scope() as db: db.query(ToolRule).filter(ToolRule.server_id == 'jira', ToolRule.tool == 'get_issue').first().allowed = $1" >/dev/null; }

for s in jira dynatrace internal-orders; do curl -s -X POST -H "$A" "$BASE/api/v1/mcp-servers/$s/refresh" | jq -r '"\(.server_id) \(.protocol)"'; done > $LOG/refresh.log
check P29-01-upstream-detected "$(awk '{print $2}' $LOG/refresh.log | sort -u | tr '\n' ' ')" "$V "
BEFORE=$(ups jira)
check P29-02-legacy-call-modern-upstream "$(legacy jira '{"name":"search_issues","arguments":{"query":"결제"}}' | jq -r '.result.isError // false')" "false"
check P29-03-no-upstream-session "$(ups jira)" "$BEFORE"
R=$(mcp dynatrace tools/call query_metrics '"name":"query_metrics","arguments":{"service":"payment-api"}')
check P29-04-mcp-param-missing "$(code "$R"):$(body "$R" | jq -r .error.code)" "400:-32020"
R=$(mcp dynatrace tools/call query_metrics '"name":"query_metrics","arguments":{"service":"payment-api"}' 'Mcp-Param-Service: payment-api')
check P29-05-mcp-param-ok "$(code "$R"):$(body "$R" | jq -r .result.structuredContent.service)" "200:payment-api"
check P29-06-legacy-mirrored "$(legacy dynatrace '{"name":"query_metrics","arguments":{"service":"payment-api"}}' | jq -r .result.structuredContent.service)" "payment-api"

# listen — 승인 후 도구 규칙 변경 → tools/list_changed (폴링 5초, 다른 워커의 변경도 DB 로 본다)
curl -sN --max-time 20 -H "$A" -H "$J" -H 'Accept: text/event-stream' -H "MCP-Protocol-Version: $V" -H 'Mcp-Method: subscriptions/listen' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":\"s29\",\"method\":\"subscriptions/listen\",\"params\":{\"notifications\":{\"toolsListChanged\":true,\"resourceSubscriptions\":[\"x\"]},$(meta)}}" \
  "$BASE/mcp/jira" > $LOG/listen.sse &
L=$!
sleep 7; rule False; sleep 9; rule True; wait $L
check P29-07-listen-ack "$(sse "$(cat $LOG/listen.sse)" | head -1 | jq -c '[.method, .params._meta["io.modelcontextprotocol/subscriptionId"], .params.notifications]')" \
  '["notifications/subscriptions/acknowledged","s29",{"toolsListChanged":true}]'
check P29-08-listen-list-changed "$(sse "$(cat $LOG/listen.sse)" | jq -r 'select(.method == "notifications/tools/list_changed") | .params._meta["io.modelcontextprotocol/subscriptionId"]' | head -1)" "s29"

# progress — modern 요청 단위 SSE (첫 왕복은 같은 워커에서 업스트림 호출 → progress 중계 → InputRequiredResult)
R=$(mcp internal-orders tools/call classify_orders '"name":"classify_orders","arguments":{}' "" '{"sampling":{}}' pp1)
sse "$(body "$R")" > $LOG/progress-1.jsonl
check P29-09-progress-sse "$(jq -s -c '[(map(select(.method == "notifications/progress")) | .[0].params | [.progressToken, (.progress == 1), (.total == 3)]), (.[-1].result.resultType)]' $LOG/progress-1.jsonl)" '[["pp1",true,true],"input_required"]'
KEY=$(jq -s -r '.[-1].result.inputRequests | keys[0]' $LOG/progress-1.jsonl); ST=$(jq -s -r '.[-1].result.requestState' $LOG/progress-1.jsonl)
R=$(mcp internal-orders tools/call classify_orders "\"name\":\"classify_orders\",\"arguments\":{},\"requestState\":\"$ST\",\"inputResponses\":{\"$KEY\":{\"role\":\"assistant\",\"content\":{\"type\":\"text\",\"text\":\"prod 분류\"},\"model\":\"client-model\",\"stopReason\":\"endTurn\"}}" "" '{"sampling":{}}' pp2)
sse "$(body "$R")" > $LOG/progress-2.jsonl
check P29-10-mrtr-complete "$(jq -s -r '.[-1].result.structuredContent.classification' $LOG/progress-2.jsonl)" "prod 분류"

# 강제 legacy — initialize·업스트림 세션으로 (레거시 회귀)
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d '{"protocol":"legacy"}' "$BASE/api/v1/mcp-servers/jira"
P=$(curl -s -X POST -H "$A" "$BASE/api/v1/mcp-servers/jira/refresh" | jq -r .protocol)
OK=$(legacy jira '{"name":"search_issues","arguments":{"query":"결제"}}' | jq -r '.result.isError // false')
check P29-11-forced-legacy "$P:$OK:$([ "$(ups jira)" -gt "$BEFORE" ] && echo session || echo none)" "2025-11-25:false:session"
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d '{"protocol":"auto"}' "$BASE/api/v1/mcp-servers/jira"
check P29-12-auto-restored "$(curl -s -X POST -H "$A" "$BASE/api/v1/mcp-servers/jira/refresh" | jq -r .protocol)" "$V"
check P29-13-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
