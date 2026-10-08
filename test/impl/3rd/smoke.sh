#!/bin/sh
# impl 3rd — 카탈로그·레지스트리 스모크 (local 스택). 사용: sh test/impl/3rd/smoke.sh [BASE]
set -u
BASE="${1:-http://127.0.0.1:8800}"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
DEV=$(tok dev); OPS=$(tok operator); USR=$(tok user)
NAME="io.smoke/demo-$(date +%s)"
DOC=$(jq -n --arg n "$NAME" '{name:$n, description:"smoke server", version:"1.0.0",
  remotes:[{type:"streamable-http", url:"https://smoke.test/mcp"}], _meta:{license:"Apache-2.0", tools:["demo.echo"]}}')

check S3-01-sources-seeded "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/registry/sources" | jq '[.items[].name] | index("mcp-official") != null')" "true"
PUB=$(curl -s -X POST -H "Authorization: Bearer $DEV" -H 'Content-Type: application/json' -d "$DOC" "$BASE/v0/publish")
ID=$(echo "$PUB" | jq -r .item.id)
check S3-02-publish-pending "$(echo "$PUB" | jq -r .version.status)" "pending"
check S3-03-hidden-before-approve "$(curl -s "$BASE/v0/servers?search=smoke" | jq --arg n "$NAME" '[.servers[].server.name] | index($n)')" "null"
check S3-04-approve "$(curl -s -X POST -H "Authorization: Bearer $OPS" "$BASE/v1/catalog/$ID/approve" | jq -r .status)" "approved"
check S3-05-discover "$(curl -s "$BASE/v0/servers/$NAME" | jq -r .server.name)" "$NAME"
check S3-06-register "$(curl -s -X POST -H "Authorization: Bearer $OPS" "$BASE/v1/catalog/$ID/register" | jq -r .item.status)" "active"
check S3-07-user-search "$(curl -s -H "Authorization: Bearer $USR" "$BASE/v1/catalog?q=demo.echo" | jq --arg n "$NAME" '[.items[].name] | index($n) != null')" "true"
check S3-08-sbom "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/v1/catalog/sbom" | jq -r .bomFormat)" "CycloneDX"
check S3-09-invalid-422 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $DEV" -H 'Content-Type: application/json' -d '{"name":"bad"}' "$BASE/v0/publish")" "422"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
