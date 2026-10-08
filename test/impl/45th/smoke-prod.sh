#!/bin/sh
# impl 45th — prod 리허설 (PostgreSQL · 2 workers · Valkey): listen 한도 워커 전체 합 · 백그라운드 리스 보유 워커 1개.
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/45th/logs
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s45 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
LB='{"jsonrpc":"2.0","id":7,"method":"subscriptions/listen","params":{"notifications":{"toolsListChanged":true},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"p45","version":"1"}}}}'
listen() {  # $1 최대 시간(초)  $2 출력 파일
  curl -sN --max-time "$1" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: subscriptions/listen' -d "$LB" -o "$2" -w '%{http_code}' "$BASE/mcp/jira"
}
PIDS=""
for i in 1 2 3 4 5; do listen 40 $LOG/listen-$i.sse > $LOG/listen-$i.code & PIDS="$PIDS $!"; done
sleep 4
COUNT=$($DC exec -T valkey valkey-cli GET anx:c:listen:usr_rehearsal | tr -d '\r')
check P45-01-global-counter "$COUNT" "5"
check P45-02-sixth-rejected "$(listen 5 $LOG/listen-6.sse)" "429"
check P45-03-acked-streams "$(grep -l 'subscriptions/acknowledged' $LOG/listen-[1-5].sse | wc -l | tr -d ' ')" "5"
kill $PIDS 2>/dev/null; wait 2>/dev/null
for i in $(seq 1 40); do c=$($DC exec -T valkey valkey-cli GET anx:c:listen:usr_rehearsal | tr -d '\r'); [ "$c" = 0 ] && break; sleep 2; done
check P45-04-released "$c" "0"
lease_owner() { $DC exec -T api python -c "from app.db import session_scope; from app.models import Setting
with session_scope() as db:
    r = db.get(Setting, 'lease.$1'); print((r.value or {}).get('owner') if r else 'none')"; }
for i in $(seq 1 40); do [ "$(lease_owner mcp_health)" != none ] && break; sleep 3; done  # MCP_HEALTH_INTERVAL_S 뒤 첫 리스
W1=$(lease_owner a2a_watch); H1=$(lease_owner mcp_health)
sleep 16
W2=$(lease_owner a2a_watch); H2=$(lease_owner mcp_health)
check P45-05-single-lease-holder "$([ "$W1" != none ] && [ "$W1" = "$W2" ] && echo watch-ok):$([ "$H1" != none ] && [ "$H1" = "$H2" ] && echo health-ok)" "watch-ok:health-ok"
unset TOKEN
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
