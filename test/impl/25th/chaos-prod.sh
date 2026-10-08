#!/bin/sh
# impl 25th — NFR 실측 (prod 리허설: PostgreSQL · 2 workers · Valkey)
#  S1: 동시 1,000 MCP 세션 (k6 sessions.js) — 18th 실측 회귀
#  A1: 일정 부하(모델 20 rps + MCP 10 rps, 180초) 중 장애 주입 — ① uvicorn 워커 1개 강제 종료 ② 1순위 모델 업스트림 장애(20초)
#      ③ Valkey 재시작 ④ PostgreSQL 재시작 → 구간별 가용성·복구 시간 (analyze.py)
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화. 부하용 Access Token 은 끝나면 폐기.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/25th/logs
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s25 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
A="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW

# 부하용 Access Token 50개 — 주체별 기본 한도(50 rps) 안에 들도록 나눠 쓴다
TOKS=""; IDS=""
for i in $(seq 1 50); do
  R=$(curl -s -H "$A" -H "$J" -d "{\"name\":\"k6-nfr-$i\",\"subject\":\"svc:nfr-$i\",\"scopes\":[\"gateway:invoke\"],\"ttl_seconds\":3600}" "$BASE/api/v1/access-tokens")
  TOKS="$TOKS,$(printf '%s' "$R" | jq -r .token)"; IDS="$IDS $(printf '%s' "$R" | jq -r .id)"
done
TOKS=${TOKS#,}
curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/mcp-servers/jira/refresh"

# ── S1 동시 1,000 세션 ──
docker run --rm --network wise-agentnexus-prod_edge -e BASE=http://api:8000 -e TOKENS="$TOKS" -e SESSIONS=1000 -e RAMP=30s -e HOLD=60s \
  -v "$PWD/deploy/k6:/k6:ro" -v "$PWD/$LOG:/out" grafana/k6:1.0.0 run --quiet --summary-export /out/k6-sessions.json /k6/sessions.js \
  > $LOG/k6-sessions.log 2>&1
check P25-01-s1-thresholds "$?" "0"
check P25-02-s1-sessions-opened "$(jq -r '.metrics.anx_sessions_opened.count' $LOG/k6-sessions.json)" "1000"

# ── A1 장애 주입 ──
FIRST=$(curl -s -H "$A" -H "$J" -d '{"model":"auto","data_class":"public","fail":[]}' "$BASE/api/v1/gateway/routing/test" | jq -r .chosen)
UP=$(curl -s -H "$A" "$BASE/api/v1/models" | jq -r --arg m "$FIRST" '.items[] | select(.id==$m) | .upstream_model')
OPTS=$(curl -s -H "$A" "$BASE/api/v1/providers" | jq -c '.items[] | select(.id=="mock") | .options')
echo "auto 1순위 $FIRST (upstream $UP)" > $LOG/chaos-plan.log
: > $LOG/events.log
docker run --rm --network wise-agentnexus-prod_edge -e BASE=http://api:8000 -e TOKENS="$TOKS" -e DURATION=180s \
  -v "$PWD/deploy/k6:/k6:ro" -v "$PWD/$LOG:/out" grafana/k6:1.0.0 run --quiet --out json=/out/points.json --summary-export /out/k6-chaos.json /k6/chaos.js \
  > $LOG/k6-chaos.log 2>&1 &
K6=$!
sleep 25
echo "$(date +%s) worker_kill" >> $LOG/events.log
$DC exec -T api python -c "
import os, signal
kids = [int(p) for p in os.listdir('/proc') if p.isdigit() and int(p) != 1 and open(f'/proc/{p}/stat').read().split()[3] == '1'
        and 'spawn_main' in open(f'/proc/{p}/cmdline').read()]
os.kill(sorted(kids)[0], signal.SIGKILL)
print('killed', sorted(kids)[0], 'of', kids)" >> $LOG/chaos-plan.log 2>&1
sleep 40
echo "$(date +%s) provider_fault" >> $LOG/events.log
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d "$(printf '%s' "$OPTS" | jq -c --arg u "$UP" '{options: (. + {fail_models: [$u]})}')" "$BASE/api/v1/providers/mock"
sleep 20
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d "{\"options\": $OPTS}" "$BASE/api/v1/providers/mock"
sleep 20
echo "$(date +%s) valkey_restart" >> $LOG/events.log
$DC restart valkey >> $LOG/chaos-plan.log 2>&1
sleep 30
echo "$(date +%s) db_restart" >> $LOG/events.log
$DC restart db >> $LOG/chaos-plan.log 2>&1
wait $K6
python3 test/impl/25th/analyze.py $LOG/points.json $LOG/events.log > $LOG/chaos.json
gzip -f $LOG/points.json
cat $LOG/chaos.json
fault() { jq -r --arg f "$1" ".faults[] | select(.fault==\$f) | $2" $LOG/chaos.json; }
check P25-03-worker-kill-availability "$(fault worker_kill '.availability >= 0.99')" "true"
check P25-04-worker-kill-recovery "$(fault worker_kill '(.recovery_s // 999) <= 10')" "true"
check P25-05-provider-fault-availability "$(fault provider_fault '.availability >= 0.99')" "true"
check P25-06-provider-fallback-used "$(curl -s -H "$A" "$BASE/api/v1/gateway/routing/decisions?fallback=true&window_minutes=10&limit=1000" | jq -r '.items | length > 0')" "true"
check P25-07-valkey-restart-availability "$(fault valkey_restart '.availability >= 0.99')" "true"
check P25-08-db-restart-recovery "$(fault db_restart '(.recovery_s // 999) <= 30')" "true"
check P25-09-overall-availability "$(jq -r '.availability >= 0.97' $LOG/chaos.json)" "true"
for i in $(seq 1 30); do curl -sf "$BASE/health" >/dev/null && break; sleep 2; done
check P25-10-health-after "$(curl -s "$BASE/health" | jq -r .status)" "ok"
check P25-11-audit-chain-after "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
check P25-12-provider-restored "$(curl -s -H "$A" "$BASE/api/v1/providers" | jq -c '.items[] | select(.id=="mock") | .options')" "$OPTS"
# 정리 — 부하용 토큰 폐기 · 임시 계정 비활성화
for id in $IDS; do curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/access-tokens/$id/revoke"; done
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
