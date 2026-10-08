#!/bin/sh
# impl 2nd — Identity 스모크 (local 스택, curl). 사용: sh test/impl/2nd/smoke.sh [BASE]
set -u
BASE="${1:-http://127.0.0.1:8800}"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }

ISS=$(curl -s "$BASE/idp/.well-known/openid-configuration" | jq -r .issuer)
check S2-01-discovery "$ISS" "$BASE/idp"
check S2-02-jwks-keys "$(curl -s "$BASE/idp/jwks" | jq '.keys | length')" "1"
check S2-03-no-token-401 "$(code "$BASE/api/v1/me")" "401"
DEV=$(tok dev); SEC=$(tok security); USR=$(tok user)
check S2-04-me-dev "$(curl -s -H "Authorization: Bearer $DEV" "$BASE/api/v1/me" | jq -r '.roles[0]')" "developer"
check S2-05-user-audit-403 "$(code -H "Authorization: Bearer $USR" "$BASE/api/v1/audit/verify")" "403"
check S2-06-txpolicy "$(code -X PUT -H "Authorization: Bearer $SEC" -H 'Content-Type: application/json' \
  -d '{"audience":"mcp:smoke","allowed_scopes":["r","w"],"default_scopes":["r"],"ttl_seconds":120}' \
  "$BASE/api/v1/identity/token-exchange/policies")" "200"
EX=$(curl -s -X POST "$BASE/idp/token" -d grant_type=urn:ietf:params:oauth:grant-type:token-exchange \
  -d "subject_token=$USR" -d audience=mcp:smoke -d scope=r -d client_id=gateway)
check S2-07-exchange-ttl "$(echo "$EX" | jq -r .expires_in)" "120"
check S2-08-exchange-deny "$(curl -s -X POST "$BASE/idp/token" -d grant_type=urn:ietf:params:oauth:grant-type:token-exchange \
  -d "subject_token=$USR" -d audience=mcp:smoke -d scope=admin | jq -r .error)" "invalid_request"
check S2-09-exchanged-not-api "$(code -H "Authorization: Bearer $(echo "$EX" | jq -r .access_token)" "$BASE/api/v1/me")" "401"
PAT=$(curl -s -X POST -H "Authorization: Bearer $DEV" -H 'Content-Type: application/json' -d '{"name":"smoke"}' \
  "$BASE/api/v1/me/tokens" | jq -r .token)
check S2-10-pat "$(curl -s -H "Authorization: Bearer $PAT" "$BASE/api/v1/me" | jq -r .kind)" "pat"
check S2-11-device-code "$(code -X POST -d client_id=nexus-cli "$BASE/idp/device/code")" "200"
check S2-12-audit-valid "$(curl -s -H "Authorization: Bearer $SEC" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
