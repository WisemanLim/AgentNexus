#!/bin/sh
# impl 2nd — prod 리허설 Identity 스모크: password grant 비활성 + Authorization Code + PKCE 완주.
# 사전: prod 컨테이너에 admin 계정 존재 (result.md 절차 참고). 사용: sh smoke-prod.sh <admin-password> [BASE]
set -u
PW="$1"; BASE="${2:-http://127.0.0.1:8800}"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }

check P2-01-password-grant-disabled "$(curl -s -X POST "$BASE/idp/token" -d grant_type=password -d username=admin \
  -d "password=$PW" | jq -r .error)" "unsupported_grant_type"
VERIFIER="smoke-verifier-0123456789-0123456789-0123456789"
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/console/callback"
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=xyz -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=admin --data-urlencode "password=$PW")
CODE=$(printf '%s' "$LOC" | sed -n 's/.*code=\([^&]*\).*/\1/p')
check P2-02-auth-code "$( [ -n "$CODE" ] && echo yes || echo no)" "yes"
AT=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
check P2-03-me-admin "$(curl -s -H "Authorization: Bearer $AT" "$BASE/api/v1/me" | jq -r '.roles[0]')" "admin"
check P2-04-audit-valid "$(curl -s -H "Authorization: Bearer $AT" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
