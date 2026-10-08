#!/bin/sh
# impl 30th — prod 리허설 (PostgreSQL · 2 workers): 동의·승인 시간 연장 (D14-③, WCAG 2.2.1) —
# 샘플링 동의 +60초 (다른 워커가 대기 중이어도 DB 기한을 따른다: 60초가 지나도 만료되지 않음) · 도구 승인 10회 상한 · 감사 체인.
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/30th/logs
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'
M='Accept: application/json, text/event-stream'
P='MCP-Protocol-Version: 2025-11-25'

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
  --data-urlencode "redirect_uri=$REDIR" -d state=s30 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
A="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW
curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/mcp-servers/internal-orders/refresh"

# ① 샘플링 동의 — 호출은 백그라운드로 대기, 연장 1회 후 65초 기다려도 pending (기본 60초면 만료됐을 시점)
SINCE=$(date -u +%Y-%m-%dT%H:%M:%S)
curl -s -H "$A" -H "$J" -H "$M" -H "$P" -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"classify_orders","arguments":{}}}' \
  "$BASE/mcp/internal-orders" > $LOG/consent-call.json &
CALL=$!
AID=""
for i in $(seq 1 50); do
  AID=$(curl -s -H "$A" "$BASE/api/v1/approvals?status=pending" | jq -r --arg s "$SINCE" '[.items[] | select(.kind == "sampling" and .requester == "usr_rehearsal" and .created_at >= $s)][0].id // empty')
  [ -n "$AID" ] && break; sleep 0.3
done
check P30-01-consent-pending "$([ -n "$AID" ] && echo yes || echo no)" "yes"
B=$(curl -s -H "$A" "$BASE/api/v1/approvals/$AID")
E=$(curl -s -X POST -H "$A" "$BASE/api/v1/approvals/$AID/extend")
DELTA=$(python3 -c "import sys,json,datetime as d; f=lambda s: d.datetime.fromisoformat(s.replace('Z','+00:00')); b=json.loads(sys.argv[1]); e=json.loads(sys.argv[2]); print(int((f(e['expires_at'])-f(b['expires_at'])).total_seconds()))" "$B" "$E")
check P30-02-extend-plus-step "$DELTA:$(printf '%s' "$E" | jq -r '"\(.extensions)/\(.max_extensions)"')" "60:1/10"
sleep 65
check P30-03-still-pending-after-base "$(curl -s -H "$A" "$BASE/api/v1/approvals/$AID" | jq -r .status)" "pending"
curl -s -o /dev/null -X POST -H "$A" -H "$J" -d '{"decision":"deny"}' "$BASE/api/v1/approvals/$AID/consent"
wait $CALL
check P30-04-call-finished-denied "$(jq -r .result.isError $LOG/consent-call.json)" "true"

# ② 도구 승인 — 10회까지 +30분 · 11회째 extend_limit
R=$(curl -s -H "$A" -H "$J" -H "$M" -H "$P" -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"transition_issue","arguments":{"key":"ORI-1440","to":"Done"}}}' "$BASE/mcp/jira")
TID=$(printf '%s' "$R" | jq -r '.error.data.approval_id // empty')
check P30-05-tool-approval-pending "$([ -n "$TID" ] && echo yes || echo no)" "yes"
for i in $(seq 1 10); do LAST=$(curl -s -X POST -H "$A" "$BASE/api/v1/approvals/$TID/extend" | jq -r .extensions); done
check P30-06-ten-extensions "$LAST" "10"
check P30-07-limit "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$A" "$BASE/api/v1/approvals/$TID/extend"):$(curl -s -X POST -H "$A" "$BASE/api/v1/approvals/$TID/extend" | jq -r .code)" "400:extend_limit"
curl -s -o /dev/null -X POST -H "$A" -H "$J" -d '{"note":"P30 정리"}' "$BASE/api/v1/approvals/$TID/reject"
check P30-08-audit-extend "$(curl -s -H "$A" "$BASE/api/v1/audit/events?action=approval.extend&limit=50" | jq -r '[.items[] | select(.data.approval_id == "'"$TID"'" or .data.approval_id == "'"$AID"'")] | length')" "11"
check P30-09-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
