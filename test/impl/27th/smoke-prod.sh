#!/bin/sh
# impl 27th — prod 리허설 (PostgreSQL · 2 workers): MCP 2026-07-28 dual-era — server/discover · stateless tools/list·call ·
# 헤더 불일치 -32020 · 지원 안 하는 버전 -32022 · 제거된 메서드 404 · MRTR elicitation(워커 간 재시도) · requestState 변조 거부 · 레거시 initialize 회귀 없음.
# 임시 계정(rehearsal-dev 개발자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
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
    u = db.scalars(select(User).where(User.username == "rehearsal-dev")).first()
    if u is None:
        u = User(id="usr_rehearsal_dev", sub="usr_rehearsal_dev", issuer=security.issuer(), username="rehearsal-dev", name="rehearsal-dev",
                 email="rehearsal-dev@agentnexus.local", roles=["developer"], team="team-platform", password_hash="")
        db.add(u)
    u.roles, u.password_hash, u.status, u.tokens_not_before = ["developer"], security.hash_password(os.environ["REH_PW"]), "active", None
PYEOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
CODE=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=s27 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal-dev --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
DEV="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW
META="\"_meta\":{\"io.modelcontextprotocol/protocolVersion\":\"$V\",\"io.modelcontextprotocol/clientCapabilities\":{\"elicitation\":{}}}"
mcp() {  # $1 server $2 method $3 name(or -) $4 params-json(without _meta) [$5 version header]
  NH=""; [ "$3" != "-" ] && NH="Mcp-Name: $3"
  curl -s -w '\n%{http_code}' -H "$DEV" -H "$J" -H 'Accept: application/json, text/event-stream' -H "MCP-Protocol-Version: ${5:-$V}" \
    -H "Mcp-Method: $2" ${NH:+-H "$NH"} -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$2\",\"params\":{$4${4:+,}$META}}" "$BASE/mcp/$1"
}
body() { printf '%s' "$1" | sed '$d'; }
code() { printf '%s' "$1" | tail -1; }
curl -s -o /dev/null -X POST -H "$DEV" "$BASE/api/v1/mcp-servers/jira/refresh"

R=$(mcp jira server/discover - "")
check P27-01-discover "$(code "$R"):$(body "$R" | jq -r '.result.resultType + ":" + (.result.supportedVersions[0])')" "200:complete:$V"
R=$(mcp jira tools/list - "")
check P27-02-stateless-list "$(body "$R" | jq -r '"\(.result.resultType):\(.result.cacheScope):\(.result.tools | length > 0)"')" "complete:private:true"
R=$(mcp jira tools/call search_issues '"name":"search_issues","arguments":{"query":"결제"}')
check P27-03-stateless-call "$(body "$R" | jq -r '.result.resultType')" "complete"
R=$(mcp jira tools/call get_issue '"name":"search_issues","arguments":{"query":"x"}')
check P27-04-header-mismatch "$(code "$R"):$(body "$R" | jq -r .error.code)" "400:-32020"
R=$(curl -s -w '\n%{http_code}' -H "$DEV" -H "$J" -H 'MCP-Protocol-Version: 2099-01-01' -H 'Mcp-Method: tools/list' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2099-01-01","io.modelcontextprotocol/clientCapabilities":{}}}}' "$BASE/mcp/jira")
check P27-05-unsupported-version "$(code "$R"):$(body "$R" | jq -r '.error.code')" "400:-32022"
R=$(mcp jira ping - "")
check P27-06-removed-method "$(code "$R"):$(body "$R" | jq -r .error.code)" "404:-32601"
# MRTR — elicitation 을 InputRequiredResult 로 받고, 재시도(두 워커 중 어느 쪽이든)로 완료
curl -s -o /dev/null -X POST -H "$DEV" "$BASE/api/v1/mcp-servers/internal-orders/refresh"
R=$(mcp internal-orders tools/call request_return '"name":"request_return","arguments":{"order_id":"O-2404"}')
FIRST=$(body "$R")
check P27-07-input-required "$(printf '%s' "$FIRST" | jq -r '.result.resultType + ":" + ([.result.inputRequests[]][0].method)')" "input_required:elicitation/create"
KEY=$(printf '%s' "$FIRST" | jq -r '.result.inputRequests | keys[0]'); ST=$(printf '%s' "$FIRST" | jq -r .result.requestState)
R=$(mcp internal-orders tools/call request_return "\"name\":\"request_return\",\"arguments\":{\"order_id\":\"O-2404\"},\"requestState\":\"${ST%????}AAAA\"")
check P27-08-state-tamper "$(body "$R" | jq -r .error.code)" "-32602"
R=$(mcp internal-orders tools/call request_return "\"name\":\"request_return\",\"arguments\":{\"order_id\":\"O-2404\"},\"requestState\":\"$ST\",\"inputResponses\":{\"$KEY\":{\"action\":\"accept\",\"content\":{\"reason\":\"파손\",\"quantity\":2}}}")
check P27-09-mrtr-complete "$(body "$R" | jq -r '"\(.result.resultType):\(.result.structuredContent.quantity)"')" "complete:2"
# 레거시 회귀 없음
INIT=$(curl -s -D - -o /dev/null -H "$DEV" -H "$J" -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' \
  -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"legacy","version":"1"}}}' "$BASE/mcp/jira" | tr -d '\r' | grep -i '^mcp-session-id:' | cut -d' ' -f2)
check P27-10-legacy-initialize "$([ -n "$INIT" ] && echo session || echo none)" "session"
check P27-11-info-supported "$(curl -s -H "$DEV" "$BASE/api/v1/system/info" | jq -r '.protocols.mcp_supported | index("2026-07-28") != null')" "true"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal-dev').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
