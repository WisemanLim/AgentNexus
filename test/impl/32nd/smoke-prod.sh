#!/bin/sh
# impl 32nd — prod 리허설 (PostgreSQL · 2 workers): MCP 2026-07-28 리소스 구독·Tasks 확장 — discover 능력 · resourceSubscriptions → resources/updated ·
# 빠른 호출은 일반 결과 · 입력이 필요한 호출 → 작업(input_required) → tasks/update(워커가 달라도 DB 로) → completed · 취소 · 다른 주체 -32602.
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화. 다른 주체용 Access Token 은 끝나면 폐기.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/32nd/logs
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'
V=2026-07-28
TC='{"elicitation":{},"extensions":{"io.modelcontextprotocol/tasks":{}}}'

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
  --data-urlencode "redirect_uri=$REDIR" -d state=s32 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
A="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW
meta() { caps=${1:-}; [ -n "$caps" ] || caps='{}'; printf '"_meta":{"io.modelcontextprotocol/protocolVersion":"%s","io.modelcontextprotocol/clientCapabilities":%s}' "$V" "$caps"; }
mcp() {  # $1 server $2 method $3 name(or -) $4 params-json(without _meta) [$5 caps json] [$6 auth header]
  NH=""; [ "$3" != "-" ] && NH="Mcp-Name: $3"
  curl -s --max-time 60 -H "${6:-$A}" -H "$J" -H 'Accept: application/json, text/event-stream' -H "MCP-Protocol-Version: $V" \
    -H "Mcp-Method: $2" ${NH:+-H "$NH"} -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$2\",\"params\":{$4${4:+,}$(meta "${5:-}")}}" "$BASE/mcp/$1"
}
tget() { mcp internal-orders tasks/get "$1" "\"taskId\":\"$1\"" "$TC" "${2:-$A}"; }
until_status() {  # $1 taskId $2 status → 마지막 응답
  for i in $(seq 1 60); do R=$(tget "$1"); [ "$(printf '%s' "$R" | jq -r .result.status)" = "$2" ] && break; sleep 0.5; done; printf '%s' "$R"
}
for s in jira dynatrace internal-orders; do curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/mcp-servers/$s/refresh"; done

check P32-01-discover-caps "$(mcp dynatrace server/discover - "" | jq -c '[.result.capabilities.resources.subscribe, (.result.capabilities.extensions | has("io.modelcontextprotocol/tasks"))]')" "[true,true]"
# 리소스 구독 — 10초마다 바뀌는 실시간 지표, 폴링 5초
curl -sN --max-time 30 -H "$A" -H "$J" -H 'Accept: text/event-stream' -H "MCP-Protocol-Version: $V" -H 'Mcp-Method: subscriptions/listen' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":\"r32\",\"method\":\"subscriptions/listen\",\"params\":{\"notifications\":{\"resourceSubscriptions\":[\"dynatrace://live/payment-api\",\"dynatrace://nope/x\"]},$(meta)}}" \
  "$BASE/mcp/dynatrace" > $LOG/listen-resource.sse
check P32-02-resource-ack "$(sed -n 's/^data: //p' $LOG/listen-resource.sse | head -1 | jq -c .params.notifications)" '{"resourceSubscriptions":["dynatrace://live/payment-api"]}'
check P32-03-resource-updated "$(sed -n 's/^data: //p' $LOG/listen-resource.sse | jq -r 'select(.method == "notifications/resources/updated") | .params.uri + ":" + .params._meta["io.modelcontextprotocol/subscriptionId"]' | head -1)" "dynatrace://live/payment-api:r32"
check P32-04-fast-inline "$(mcp jira tools/call search_issues '"name":"search_issues","arguments":{"query":"결제"}' "$TC" | jq -r '.result.resultType + ":" + (.result.taskId // "none")')" "complete:none"
# 작업 — 입력 요청 → tasks/update → 완료
T1=$(mcp internal-orders tools/call request_return '"name":"request_return","arguments":{"order_id":"O-2404"}' "$TC" | jq -r 'if .result.resultType == "task" then .result.taskId else "none" end')
check P32-05-task-created "$(printf '%s' "$T1" | cut -c1-5)" "task_"
G=$(until_status "$T1" input_required)
check P32-06-input-required "$(printf '%s' "$G" | jq -r '[.result.inputRequests[]][0].method')" "elicitation/create"
KEY=$(printf '%s' "$G" | jq -r '.result.inputRequests | keys[0]')
# JSON 은 변수로 — macOS /bin/sh(bash 3.2)는 "$( … "{…,…}" … )" 안의 중괄호를 확장해 요청을 여러 번 보낸다
UPD="\"taskId\":\"$T1\",\"inputResponses\":{\"$KEY\":{\"action\":\"accept\",\"content\":{\"reason\":\"파손\",\"quantity\":2}}}"
check P32-07-update-ack "$(mcp internal-orders tasks/update "$T1" "$UPD" "$TC" | jq -r .result.resultType)" "complete"
check P32-08-task-completed "$(until_status "$T1" completed | jq -r '"\(.result.status):\(.result.result.structuredContent.quantity)"')" "completed:2"
# 다른 주체 — 같은 taskId 라도 -32602 (존재를 알리지 않는다)
TOK=$(curl -s -H "$A" -H "$J" -d '{"name":"p32-other","subject":"svc:p32-other","scopes":["gateway:invoke"],"ttl_seconds":600}' "$BASE/api/v1/access-tokens")
OTHER="Authorization: Bearer $(printf '%s' "$TOK" | jq -r .token)"
check P32-09-other-principal "$(tget "$T1" "$OTHER" | jq -r .error.code)" "-32602"
curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/access-tokens/$(printf '%s' "$TOK" | jq -r .id)/revoke"
# 취소 — 실행 워커가 달라도 생존 신호 때 취소
T2=$(mcp internal-orders tools/call request_return '"name":"request_return","arguments":{"order_id":"O-2401"}' "$TC" | jq -r .result.taskId)
mcp internal-orders tasks/cancel "$T2" "\"taskId\":\"$T2\"" "$TC" > /dev/null
check P32-10-task-cancelled "$(until_status "$T2" cancelled | jq -r .result.status)" "cancelled"
check P32-11-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
