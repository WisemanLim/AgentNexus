#!/bin/sh
# impl 65th — 13차 prod 리허설 (PostgreSQL · 2 workers · Valkey): Epic 61~66 — 세션 상한·인덱스(0021) · 동시 회전 잠금(두 워커) ·
# Valkey 시계 보유자·실행 없는 계획 예약 없음·예산 현황 · Access Token 스코프 ⊆ 교환 정책·운영 https JWKS·익명 발견 API 한도 · IdP 내장 issuer 거부.
# 임시 계정 rehearsal(admin)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s65 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
H="Authorization: Bearer $TOKEN"
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# P65-01 데이터 계층 — 0021 · 앱 연결 세션 상한(2min·15s·0) · KPI 인덱스 2개
DB=$($DC exec -T api python - <<'PYEOF'
from sqlalchemy import inspect, text
from app.db import engine
with engine.connect() as c:
    lim = ",".join(c.execute(text(f"SHOW {k}")).scalar() for k in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout"))
idx = {i["name"] for t in ("spans", "run_events") for i in inspect(engine).get_indexes(t)}
print(f"{lim}:{int({'ix_spans_parent_trace', 'ix_run_events_type_ts'} <= idx)}")
PYEOF
)
check P65-01-db-limits-indexes "$(curl -s -H "$H" "$BASE/api/v1/admin/updates" | jq -r .migration):$(echo "$DB" | tr -d '\r')" "0021:2min,15s,0:1"

# P65-02 동시 서명 키 회전 — 두 요청이 두 워커로 가도 잠금으로 차례대로: 직전 kid 둘 다 다르고 JWKS 에 남음
B='{"kind":"signing","confirm":"ROTATE"}'
T=$(mktemp -d)
curl -s -H "$H" -H "$J" -d "$B" "$BASE/api/v1/admin/keys/rotate" > "$T/r1.json" &
curl -s -H "$H" -H "$J" -d "$B" "$BASE/api/v1/admin/keys/rotate" > "$T/r2.json" &
wait
P1=$(jq -r .previous_kid "$T/r1.json"); P2=$(jq -r .previous_kid "$T/r2.json"); rm -rf "$T"
KIDS=$(curl -s "$BASE/idp/jwks" | jq -r '[.keys[].kid] | join(",")')
OK=0; case ",$KIDS," in *",$P1,"*) case ",$KIDS," in *",$P2,"*) OK=1;; esac;; esac
check P65-02-concurrent-rotation "$( [ "$P1" != "$P2" ] && echo distinct):$OK:$(code -H "$H" "$BASE/api/v1/me")" "distinct:1:200"

# P65-03 한도 — Valkey 시계 보유자 · 라우팅 시험(실행 없음)은 진행 중 슬롯·예약을 남기지 않음 · 예산 현황 200
HOLD=$($DC exec -T api python - <<'PYEOF'
import time
from app.services import ratelimit
b, k = ratelimit.backend(), "p65:holder"
b.holder_acquire(k, "m1", 1, 2)
real = time.time
ratelimit.time.time = lambda: real() + 10_000  # 워커 시계만 앞섬 — 만료는 Valkey 시계라 그대로
a = b.holder_count(k)
ratelimit.time.time = real
time.sleep(2.4)
print(f"{type(b).__name__}:{a}:{b.holder_count(k)}")
PYEOF
)
for i in 1 2 3; do curl -s -o /dev/null -H "$H" -H "$J" -d '{"model":"auto","fail":[]}' "$BASE/api/v1/gateway/routing/test"; done
INF=$($DC exec -T api python -c "from app.services import ratelimit; print(ratelimit.backend().holder_count('inflight:usr_rehearsal'))" | tr -d '\r')
check P65-03-holders-and-holds "$(echo "$HOLD" | tr -d '\r'):$INF:$(code -H "$H" "$BASE/api/v1/gateway/limits/status")" "RedisBackend:1:0:0:200"

# P65-04 경계 — 대상용 Access Token 은 교환 정책 밖 스코프 거부 · 운영은 https JWKS 만 · 익명 발견 API IP 한도
curl -s -o /dev/null -X POST -H "$H" "$BASE/api/v1/mcp-servers/jira/refresh"
SC=$(curl -s -H "$H" -H "$J" -d '{"name":"p65","subject":"svc:p65","audience":"mcp:jira","scopes":["p65:not-allowed"],"ttl_seconds":300}' "$BASE/api/v1/access-tokens" | jq -r .code)
JW=$(curl -s -X PATCH -H "$H" -H "$J" -d '{"auth":{"push_verify":{"jwks_url":"http://jwks.p65.example/jwks","issuer":"https://p65.example"}}}' \
  "$BASE/api/v1/a2a/agents/observability-analyst" | jq -r .code)
N429=0; for i in $(seq 1 40); do [ "$(code "$BASE/v0/servers?limit=1")" = 429 ] && N429=$((N429+1)); done
check P65-04-boundaries "$SC:$JW:$( [ "$N429" -gt 0 ] && echo limited):$(code -H "$H" "$BASE/v0/servers?limit=1")" "scope_not_allowed:invalid_jwks_url:limited:200"

# P65-05 IdP — 내장 IdP 와 같은 issuer 는 외부 IdP 로 연동 거부
ISS=$(curl -s "$BASE/idp/.well-known/openid-configuration" | jq -r .issuer)
BODY=$(jq -cn --arg i "$ISS" '{name:"p65-self",issuer:$i,client_id:"x"}')
check P65-05-idp-builtin-issuer "$(curl -s -H "$H" -H "$J" -d "$BODY" "$BASE/api/v1/identity/idps" | jq -r .code)" "issuer_reserved"

unset TOKEN
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
