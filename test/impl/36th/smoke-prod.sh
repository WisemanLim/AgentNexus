#!/bin/sh
# impl 36th — prod 리허설 (PostgreSQL · 2 workers): A2A HTTP+JSON 바인딩 — 게이트웨이·플랫폼 REST 수신 · google.rpc.Status 오류 ·
# SSE StreamResponse · 카드 HTTP+JSON 인터페이스 · 차단 등급 정책 동일 · 감사 체인.
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/36th/logs
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'

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
  --data-urlencode "redirect_uri=$REDIR" -d state=s36 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
A="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW

AG=observability-analyst
AG=observability-analyst
M='Content-Type: application/a2a+json'
curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/a2a/agents/$AG/refresh"
SEND='{"message":{"messageId":"p36a","role":"ROLE_USER","parts":[{"text":"orders-api 장애 분석"}]}}'
R=$(curl -s -D $LOG/send.hdr -H "$A" -H "$M" -d "$SEND" "$BASE/a2a/$AG/rest/message:send")
check P36-01-rest-send "$(printf '%s' "$R" | jq -r '.task.status.state'):$(grep -i '^content-type' $LOG/send.hdr | grep -c 'application/a2a+json')" "TASK_STATE_COMPLETED:1"
TID=$(printf '%s' "$R" | jq -r .task.id)
check P36-02-rest-get "$(curl -s -H "$A" "$BASE/a2a/$AG/rest/tasks/$TID" | jq -r .id)" "$TID"
R=$(curl -s -w '\n%{http_code}' -H "$A" -H "$M" -d '{}' "$BASE/a2a/$AG/rest/tasks/$TID:cancel")
check P36-03-rest-error-status "$(printf '%s' "$R" | tail -1):$(printf '%s' "$R" | sed '$d' | jq -r '.error.details[0].reason + "@" + .error.details[0].domain')" "400:TASK_NOT_CANCELABLE@a2a-protocol.org"
check P36-04-rest-not-found "$(curl -s -o /dev/null -w '%{http_code}' -H "$A" "$BASE/a2a/$AG/rest/tasks/no-such")" "404"
curl -sN --max-time 60 -H "$A" -H "$M" -d "$SEND" "$BASE/a2a/$AG/rest/message:stream" > $LOG/stream.sse
check P36-05-rest-stream "$(sed -n 's/^data: //p' $LOG/stream.sse | jq -s -r '[(map(has("jsonrpc")) | any), (.[-1] | (.statusUpdate.status.state // .task.status.state))] | map(tostring) | join(":")')" "false:TASK_STATE_COMPLETED"
check P36-06-platform-rest "$(curl -s -H "$A" -H "$M" -d '{"message":{"messageId":"p36p","role":"ROLE_USER","parts":[{"text":"휴가 정책"}]}}' "$BASE/a2a/platform/tpl-qa/rest/message:send" | jq -r .task.status.state)" "TASK_STATE_COMPLETED"
check P36-07-cards-rest-interface "$(curl -s "$BASE/.well-known/agent-card.json" | jq -r '[.supportedInterfaces[] | select(.protocolBinding=="HTTP+JSON")][0].url | endswith("/a2a/platform/rest")'):$(curl -s "$BASE/a2a/$AG/.well-known/agent-card.json" | jq -r '[.supportedInterfaces[] | select(.protocolBinding=="HTTP+JSON")][0].url | endswith("/rest")')" "true:true"
R=$(curl -s -w '\n%{http_code}' -H "$M" -d "$SEND" "$BASE/a2a/$AG/rest/message:send")
check P36-08-anon-401 "$(printf '%s' "$R" | sed '$d' | jq -r .error.status):$(printf '%s' "$R" | tail -1)" "UNAUTHENTICATED:401"
check P36-09-jsonrpc-regression "$(curl -s -H "$A" -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"kind":"message","messageId":"p36j","role":"user","parts":[{"kind":"text","text":"orders-api 장애 분석"}]}}}' "$BASE/a2a/$AG" | jq -r .result.status.state)" "completed"
check P36-10-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
