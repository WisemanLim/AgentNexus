#!/bin/sh
# impl 33rd — prod 리허설 (PostgreSQL · 2 workers): A2A 1.0 기본화 — info·카드 1.0 우선 · 0.3 클라이언트 회귀 없음 ·
# 1.0 클라이언트 · 오케스트레이터 위임이 1.0(이벤트 protocol) · 감사 체인.
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/33rd/logs
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s33 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
A="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW

AG=observability-analyst
check P33-01-info "$(curl -s -H "$A" "$BASE/api/v1/system/info" | jq -c '[.protocols.a2a, .protocols.a2a_supported]')" '["1.0",["1.0","0.3"]]'
check P33-02-card "$(curl -s "$BASE/.well-known/agent-card.json" | jq -c '[.supportedInterfaces[0].protocolVersion, (.url != null), .protocolVersion]')" '["1.0",true,"0.3.0"]'
curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/a2a/agents/$AG/refresh"
check P33-03-v03-client "$(curl -s -H "$A" -H "$J" -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"kind":"message","messageId":"p33a","role":"user","parts":[{"kind":"text","text":"orders-api 장애 분석"}]}}}' "$BASE/a2a/$AG" | jq -r '.result.kind + ":" + .result.status.state')" "task:completed"
check P33-04-v10-client "$(curl -s -H "$A" -H "$J" -d '{"jsonrpc":"2.0","id":2,"method":"SendMessage","params":{"message":{"messageId":"p33b","role":"ROLE_USER","parts":[{"text":"orders-api 장애 분석"}]}}}' "$BASE/a2a/$AG" | jq -r .result.task.status.state)" "TASK_STATE_COMPLETED"
SID=$(curl -s -H "$A" -H "$J" -d '{"profile_id":"tpl-incident"}' "$BASE/api/v1/sessions" | jq -r .id)
RID=$(curl -s -H "$A" -H "$J" -d '{"content":"orders-api 장애 원인 분석해줘"}' "$BASE/api/v1/sessions/$SID/messages" | jq -r .run_id)
for i in $(seq 1 60); do ST=$(curl -s -H "$A" "$BASE/api/v1/runs/$RID" | jq -r .status); case "$ST" in succeeded|partial|failed) break;; esac; sleep 1; done
check P33-05-run "$ST" "succeeded"
check P33-06-run-delegated-v10 "$($DC exec -T api python -c "
from app.db import session_scope
from app.models import A2ATask
with session_scope() as db:
    ps = [e.get('protocol') for t in db.query(A2ATask).filter(A2ATask.run_id == '$RID') for e in (t.events or []) if e.get('protocol')]
    print(bool(ps) and all(p == {'client': '1.0', 'upstream': '1.0'} for p in ps))")" "True"
check P33-07-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
