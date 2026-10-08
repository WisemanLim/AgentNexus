#!/bin/sh
# impl 19th — prod 리허설 (PostgreSQL · 2 workers): 마이그레이션 0008 · 데모 외부 IdP 비노출 · 브로커 안전 실패 · 관리 API.
# 사전: make prod-build && make db-migrate ENV=prod. 사용: sh test/impl/19th/smoke-prod.sh
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'

PW=$(openssl rand -hex 16)
$DC exec -T -e REH_PW="$PW" api python - <<'EOF' >/dev/null
import os
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
with session_scope() as db:
    u = db.scalars(select(User).where(User.username == "rehearsal")).first()
    if u is None:
        u = User(id="usr_rehearsal", sub="usr_rehearsal", issuer=security.issuer(), username="rehearsal", name="리허설",
                 email="rehearsal@agentnexus.local", roles=["admin"], team="team-platform", password_hash="")
        db.add(u)
    u.password_hash, u.status = security.hash_password(os.environ["REH_PW"]), "active"
EOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
AUTHQ="response_type=code&client_id=agentnexus-console&redirect_uri=$REDIR&state=s19&scope=openid&code_challenge=$CHALLENGE&code_challenge_method=S256"
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=s19 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW")
CODE=$(printf '%s' "$LOC" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
H="Authorization: Bearer $TOKEN"

check P19-01-migration "$(curl -s -H "$H" "$BASE/api/v1/admin/updates" | jq -r .migration)" "0008"
check P19-02-demo-idp-absent "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/_demo/oidc/.well-known/openid-configuration")" "404"
check P19-03-providers-empty "$(curl -s "$BASE/idp/providers" | jq -r '.items | length')" "0"
check P19-04-no-demo-idp-seeded "$(curl -s -H "$H" "$BASE/api/v1/identity/idps" | jq -r '[.items[] | select(.id=="idp-demo-partner")] | length')" "0"
# 브로커 — 알 수 없는 IdP 힌트는 원래 redirect_uri 로 원인 코드, 모르는 state 는 오류 화면(400), 외부로 새지 않음
check P19-05-unknown-hint "$(curl -s -o /dev/null -w '%{redirect_url}' "$BASE/idp/authorize?$AUTHQ&idp_hint=nope" | sed -n 's/.*error=\([^&]*\).*/\1/p')" "idp_disabled"
check P19-06-bad-state "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/idp/broker/callback?state=nope&code=x")" "400"
check P19-07-redirect-allowlist "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/idp/authorize?response_type=code&client_id=agentnexus-console&redirect_uri=https://evil.example/cb&code_challenge=$CHALLENGE&idp_hint=nope")" "400"
# 관리 API — 저장 전 디스커버리(도달 불가 issuer 도 500 아님) · 시험 전 로그인 노출 거부
check P19-08-discover-unreachable "$(curl -s -H "$H" -H "$J" -d '{"issuer":"https://idp.invalid.example"}' "$BASE/api/v1/identity/idps/discover" | jq -r '.discovery')" "false"
IDP=$(curl -s -H "$H" -H "$J" -d '{"name":"리허설 IdP","issuer":"https://login.rehearsal.example/tenant","client_id":"c19"}' "$BASE/api/v1/identity/idps" | jq -r .id)
check P19-09-login-needs-test "$(curl -s -X PATCH -H "$H" -H "$J" -d '{"login_enabled":true}' "$BASE/api/v1/identity/idps/$IDP" | jq -r .code)" "test_login_required"
check P19-10-test-login-unreachable "$(curl -s -X POST -H "$H" "$BASE/api/v1/identity/idps/$IDP/test-login" | jq -r .code)" "idp_unavailable"
curl -s -o /dev/null -X DELETE -H "$H" "$BASE/api/v1/identity/idps/$IDP"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db: db.get(User, 'usr_rehearsal').status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
