#!/bin/sh
# impl 47th — prod 리허설 (PostgreSQL · 2 workers): SQL 요약·경보 평가 · NFR-P1 k6 재측정(호스트 부하 기록).
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/47th/logs
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s47 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
H="Authorization: Bearer $TOKEN"
{ echo "# 측정 시점 호스트 부하 $(date '+%F %T')"; uptime; docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' | sort -k2 -rn | head -5; } > $LOG/host-load.txt
docker run --rm --network wise-agentnexus-prod_edge -e BASE=http://api:8000 -e TOKEN="$TOKEN" -e DURATION=20s \
  -v "$PWD/deploy/k6:/k6:ro" -v "$PWD/$LOG:/out" grafana/k6:1.0.0 run --quiet --summary-export /out/k6-summary.json /k6/gateway.js > $LOG/k6.log 2>&1
echo "k6 exit $?" >> $LOG/k6.log
S=$(curl -s -H "$H" "$BASE/api/v1/observe/summary?window_minutes=10")
check P47-01-summary-sql "$(printf '%s' "$S" | jq -r '[(.calls > 0), (.by_kind.model.p95_latency_ms != null), (.top_models | length > 0)] | map(tostring) | join(":")')" "true:true:true"
check P47-02-alerts-evaluate "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$H" "$BASE/api/v1/observe/alerts/evaluate")" "200"
check P47-03-k6-measured "$(test -s $LOG/k6-summary.json && jq -r '.metrics | has("http_req_duration")' $LOG/k6-summary.json)" "true"
OVH=$($DC exec -T api python - <<'PYEOF'
import statistics
from datetime import timedelta
from app.db import SessionLocal, utcnow
from app.models import Span
with SessionLocal() as db:
    roots = db.query(Span).filter(Span.name == "POST /mcp/{server_id}", Span.parent_id.is_(None),
                                  Span.start_time >= utcnow() - timedelta(minutes=3)).all()
    out = []
    for r in roots:
        spans = db.query(Span).filter(Span.trace_id == r.trace_id).all()
        calls = {s.id for s in spans if s.kind == "client" and s.name.startswith("mcp ")}
        up = sum(float(s.duration_ms) for s in spans if s.kind == "server" and s.parent_id in calls)
        if up:
            out.append(float(r.duration_ms) - up)
    print(f"{statistics.quantiles(out, n=100)[94]:.1f} {len(out)}" if len(out) > 20 else f"na {len(out)}")
PYEOF
)
echo "MCP 추가 지연 p95(ms) 표본: $OVH" >> $LOG/k6.log
check P47-04-mcp-overhead-sampled "$(echo "$OVH" | awk '{print ($1 != "na") ? "sampled" : "none"}')" "sampled"
unset TOKEN
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
