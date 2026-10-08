#!/bin/sh
# impl 35th — prod 리허설 (PostgreSQL · 2 workers): SLO·오류 예산 — 기본 SLO · 실제 요청 반영(두 워커 카운터 합산) ·
# 모델 업스트림 전부 장애(502) → 나쁜 요청·5분 소진율 → 평가 시 page 알림 · 공급자 원복 · 감사 체인.
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/35th/logs
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s35 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
A="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW
avail() { curl -s -H "$A" "$BASE/api/v1/observe/slos" | jq -r ".items[] | select(.id==\"gateway-availability\") | $1"; }

check P35-01-defaults "$(curl -s -H "$A" "$BASE/api/v1/observe/slos" | jq -c '[.items[] | [.id, .target, .threshold_ms]]')" '[["gateway-availability",0.9995,null],["gateway-latency",0.99,2000]]'
T0=$(avail .total); B0=$(avail .bad)
for i in $(seq 1 20); do curl -s -o /dev/null -H "$A" "$BASE/v1/models"; done
OK=no; for i in $(seq 1 45); do [ "$(avail .total)" -ge $((T0 + 20)) ] && OK=yes && break; sleep 2; done  # 두 워커 — 조회 워커는 즉시, 다른 워커는 다음 주기(≤60초)
check P35-02-traffic-counted "$OK" "yes"
OPTS=$(curl -s -H "$A" "$BASE/api/v1/providers" | jq -c '.items[] | select(.id=="mock") | .options')
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d "$(printf '%s' "$OPTS" | jq -c '{options: (. + {fail_models: ["domain-7b","mock-fast","mock-smart"]})}')" "$BASE/api/v1/providers/mock"
CODES=$(for i in $(seq 1 30); do curl -s -o /dev/null -w '%{http_code}\n' -H "$A" -H "$J" -d '{"model":"fast-commercial","messages":[{"role":"user","content":"slo"}]}' "$BASE/v1/chat/completions"; done | sort | uniq -c | tr -s ' ' | tr '\n' ';')
echo "fault codes: $CODES" > $LOG/fault.log
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d "{\"options\": $OPTS}" "$BASE/api/v1/providers/mock"
check P35-03-provider-restored "$(curl -s -H "$A" "$BASE/api/v1/providers" | jq -c '.items[] | select(.id=="mock") | .options')" "$OPTS"
OK=no; for i in $(seq 1 45); do [ "$(avail .bad)" -ge $((B0 + 30)) ] && OK=yes && break; sleep 2; done
check P35-04-errors-counted "$OK" "yes"
check P35-05-burn-5m "$(avail '.burn["5m"].burn_rate >= 14.4')" "true"
EV=$(curl -s -X POST -H "$A" "$BASE/api/v1/observe/slos/evaluate")
echo "$EV" > $LOG/evaluate.json
check P35-06-state-page "$(avail .state)" "page"
check P35-07-notification "$(curl -s -H "$A" "$BASE/api/v1/notifications?limit=50" | jq -r '[.items[] | select(.kind=="slo" and (.title | contains("데이터 경로 가용성")))] | length > 0')" "true"
check P35-08-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
curl -s -H "$A" "$BASE/api/v1/observe/slos" | jq '[.items[] | {id, total, bad, sli, state, budget, burn}]' > $LOG/slos.json
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
