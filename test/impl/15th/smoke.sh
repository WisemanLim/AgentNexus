#!/bin/sh
# impl 15th — 업스트림 mTLS live (local 스택): 인증서 시크릿 비노출 · 정책 검증 · required 시 평문 업스트림 fail closed · 복구
# 실제 TLS 핸드셰이크(클라이언트 인증서 필수 서버·다른 CA·서버 미신뢰)는 test/test_mtls.py 의 실소켓 TLS 서버로 검증한다.
set -u
BASE="http://127.0.0.1:8800"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
A=$(tok admin); S=$(tok security); U=$(tok user); O=$(tok operator)
J='Content-Type: application/json'
MCP='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_issues","arguments":{"limit":1}}}'
mcp() { curl -s -H "Authorization: Bearer $U" -H "$J" -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' -d "$MCP" "$BASE/mcp/jira"; }
PEM=$(openssl req -x509 -newkey rsa:2048 -nodes -keyout /dev/stdout -subj /CN=smoke-client -days 1 2>/dev/null | tr '\n' '|' | sed 's/|/\\n/g')
R=$(curl -s -X POST -H "Authorization: Bearer $A" -H "$J" -d "{\"name\":\"tls/smoke-client-$(date +%s)\",\"kind\":\"certificate\",\"value\":\"$PEM\"}" "$BASE/api/v1/gateway/secrets")
check S15-01-cert-secret-masked "$(printf '%s' "$R" | jq -r .kind):$(printf '%s' "$R" | grep -c 'PRIVATE KEY')" "certificate:0"
check S15-02-invalid-policy "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Authorization: Bearer $S" -H "$J" -d '{"mtls":{"upstream":"maybe"}}' "$BASE/api/v1/gateway/security")" "400"
curl -s -o /dev/null -X PUT -H "Authorization: Bearer $S" -H "$J" -d '{"mtls":{"upstream":"required","client":"disabled"}}' "$BASE/api/v1/gateway/security"
check S15-03-required-fail-closed "$(mcp | jq -r '.error.data.code // .result.isError')" "mtls_required"
check S15-04-mock-model-unaffected "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $U" -H "$J" -H 'X-AgentNexus-Data-Class: public' -d '{"model":"fast-commercial","messages":[{"role":"user","content":"x"}]}' "$BASE/v1/chat/completions")" "200"
curl -s -o /dev/null -X PUT -H "Authorization: Bearer $S" -H "$J" -d '{"mtls":{"upstream":"optional","client":"disabled"}}' "$BASE/api/v1/gateway/security"
curl -s -o /dev/null -X POST -H "Authorization: Bearer $O" "$BASE/api/v1/mcp-servers/jira/refresh"
check S15-05-restored "$(mcp | jq -r '.result.isError')" "false"
check S15-06-audit "$(curl -s -H "Authorization: Bearer $S" "$BASE/api/v1/audit/events?action=security.update&limit=5" | jq -r '[.items[].data.mtls.upstream] | index("required") != null')" "true"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
