#!/bin/sh
# impl 55th — 11차 prod 리허설 (PostgreSQL · 2 workers · Valkey): Epic 52~55 — 마이그레이션 0020 · a2a-grpc gRPC 헬스·최소 env ·
# Access Token audience 허용 목록 · usage SQL 집계(UTC 날짜) · listen 보유자 집합(Valkey ZSET).
# 임시 계정 rehearsal(admin)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화, 발급한 Access Token 은 폐기.
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s55 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
H="Authorization: Bearer $TOKEN"

# P55-01 마이그레이션 0020 (usage_records.client_key)
check P55-01-migration-0020 "$(curl -s -H "$H" "$BASE/api/v1/admin/updates" | jq -r .migration)" "0020"

# P55-02 a2a-grpc — gRPC 헬스 프로브(readiness = api 까지) · 엣지 컨테이너에 서명 키·DB 비밀번호 없음
G=wise-agentnexus-prod-a2a-grpc-1
R=$(docker exec $G python -m app.grpc_server --probe readiness >/dev/null 2>&1; echo $?)
SECRETS=$(docker exec $G env | grep -cE '^(SIGNING_KEY_PEM|ENCRYPTION_KEY|DB_PASSWORD|DATABASE_URL|BOOTSTRAP_ADMIN_PASSWORD)=')
check P55-02-grpc-health-and-env "$(docker inspect -f '{{.State.Health.Status}}' $G):$R:$SECRETS" "healthy:0:0"

# P55-03 Access Token audience — 등록된 대상만
AT=$(curl -s -H "$H" -H "$J" -d '{"name":"p55","subject":"a2a:p55-svc","audience":"mcp:jira","ttl_seconds":300}' "$BASE/api/v1/access-tokens")
BAD=$(curl -s -H "$H" -H "$J" -d '{"name":"p55x","subject":"a2a:p55-svc","audience":"mcp:no-such","ttl_seconds":300}' "$BASE/api/v1/access-tokens" | jq -r .code)
check P55-03-audience-allowlist "$(echo "$AT" | jq -r '.audience'):$BAD" "mcp:jira:unknown_audience"
curl -s -o /dev/null -X POST -H "$H" "$BASE/api/v1/access-tokens/$(echo "$AT" | jq -r .id)/revoke"

# P55-04 usage — PostgreSQL 에서 SQL 집계 · day 키는 YYYY-MM-DD
U=$(curl -s -H "$H" "$BASE/api/v1/usage?group_by=day")
check P55-04-usage-day-sql "$(echo "$U" | jq -r '[.items[].key | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")] | all'):$(curl -s -o /dev/null -w '%{http_code}' -H "$H" "$BASE/api/v1/usage?group_by=team")" "true:200"

# P55-05 listen 보유자 집합 — 열면 1, 닫으면 0 (Valkey ZSET anx:h:listen:<sub>)
LB='{"jsonrpc":"2.0","id":9,"method":"subscriptions/listen","params":{"notifications":{"toolsListChanged":true},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
curl -sN --max-time 8 -H "$H" -H "$J" -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: subscriptions/listen' -d "$LB" -o /dev/null "$BASE/mcp/jira" &
P=$!
sleep 3
HELD=$($DC exec -T valkey valkey-cli ZCARD anx:h:listen:usr_rehearsal | tr -d '\r')
wait $P 2>/dev/null
for i in $(seq 1 20); do LEFT=$($DC exec -T valkey valkey-cli ZCARD anx:h:listen:usr_rehearsal | tr -d '\r'); [ "$LEFT" = 0 ] && break; sleep 1; done
check P55-05-listen-holder-set "$HELD:$LEFT" "1:0"

unset TOKEN
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
