#!/bin/sh
# impl 4th — 게이트웨이 정책 코어 스모크 (local 스택 + Valkey + OPA 프로파일). 사용: sh test/impl/4th/smoke.sh [BASE]
set -u
BASE="${1:-http://127.0.0.1:8800}"
DCL="${DCL:-docker compose -p wise-agentnexus-local -f docker-compose.yml -f docker-compose.local.yml -f compose/opa.yml -f compose/valkey.yml --env-file .env.local}"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
SEC=$(tok security); OPS=$(tok operator)

check S4-01-valkey-backend "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/gateway/limits" | jq -r '.backend.backend + ":" + (.backend.ok|tostring)')" "valkey:true"
# Valkey 토큰 버킷 실측 — rate 1/s burst 2 → 3번째 거부
BUCKET=$($DCL exec -T api python -c "
from app.services import ratelimit as r
b=r.backend(); k='smoke:%s' % __import__('uuid').uuid4().hex
print(type(b).__name__, [b.take(k, 1, 2)[0] for _ in range(3)])")
check S4-02-valkey-bucket "$BUCKET" "RedisBackend [True, True, False]"
check S4-03-default-policies "$(curl -s -H "Authorization: Bearer $SEC" "$BASE/api/v1/gateway/policies" | jq '.items | length >= 4')" "true"
SIM=$(curl -s -X POST -H "Authorization: Bearer $SEC" -H 'Content-Type: application/json' \
  -d '{"kind":"model","target":"gpt-4o","data_class":"internal","attrs":{"category":"commercial"}}' "$BASE/api/v1/gateway/policies/simulate")
check S4-04-internal-reroute "$(echo "$SIM" | jq -r '(.decision.allow|tostring) + ":" + .decision.reroute')" "false:domain-local"
SCAN=$(curl -s -X POST -H "Authorization: Bearer $SEC" -H 'Content-Type: application/json' \
  -d '{"text":"연락처 010-1234-5678, 키 sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX"}' "$BASE/api/v1/gateway/security/scan")
check S4-05-pii-mask "$(echo "$SCAN" | jq -r '.text | contains("[MASKED:phone]") and contains("[MASKED:openai_key]")')" "true"
SEC_ID=$(curl -s -X POST -H "Authorization: Bearer $OPS" -H 'Content-Type: application/json' \
  -d "{\"name\":\"smoke/$(date +%s)\",\"value\":\"sk-smoke-SECRET-0000-1111-2222\"}" "$BASE/api/v1/gateway/secrets" | jq -r .id)
check S4-06-secret-masked "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/gateway/secrets" | grep -c 'SECRET-0000')" "0"
# OPA(Rego) 실평가 — policies/agentnexus.rego
OPA=$($DCL exec -T api python -c "
import httpx
q=lambda i: httpx.post('http://opa:8181/v1/data/agentnexus/gateway', json={'input': i}).json()['result']
a=q({'kind':'model','model':'gpt-4o','data_class':'internal','attrs':{}})
b=q({'kind':'model','model':'domain-local','data_class':'internal','attrs':{}})
c=q({'kind':'tool','tool':'confluence.create_page','agent':'x','attrs':{'risk':'high'}})
print(a['allow'], a.get('reroute'), b['allow'], c.get('require_approval'))")
check S4-07-opa-rego "$OPA" "False domain-local True True"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
