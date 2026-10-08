#!/bin/sh
# impl 11th — CLI nexus live 스모크 (local 스택 기동 상태). CLI 는 api 컨테이너 안에서 실행.
set -u
BASE="http://127.0.0.1:8800"
DC="docker compose -p wise-agentnexus-local"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
nx() { $DC exec -T -e NEXUS_URL=http://127.0.0.1:8000 -e NEXUS_CONFIG_DIR=/tmp/nx-smoke "$@"; }
nxt() { t="$1"; shift; $DC exec -T -e NEXUS_URL=http://127.0.0.1:8000 -e NEXUS_TOKEN="$t" -w /tmp api nexus "$@"; }
OUT=$(mktemp)

# 1) Device login — 백그라운드로 대기, 브라우저 승인 대신 폼 POST
nx api sh -c 'rm -rf /tmp/nx-smoke; nexus logout >/dev/null 2>&1; true'
nx api nexus login --device >"$OUT" 2>&1 &
LPID=$!
for i in $(seq 1 30); do CODE=$(grep -o '[B-Z2-9]\{4\}-[B-Z2-9]\{4\}' "$OUT" | head -1); [ -n "$CODE" ] && break; sleep 0.5; done
check S11-01-user-code "$(printf '%s' "$CODE" | grep -c '^....-....$')" "1"
curl -s -o /dev/null -X POST "$BASE/idp/device" --data-urlencode "user_code=$CODE" -d username=dev -d password=dev-pass
wait $LPID
check S11-02-login "$(grep -c '로그인 완료: .*developer' "$OUT")" "1"
check S11-03-whoami "$(nx api nexus whoami --json | jq -r .sub)" "usr_dev"

DEV=$(tok dev); USR=$(tok user); ADM=$(tok admin); FIN=$(tok finops); SEC=$(tok security)
# 2) scaffold → publish → pending / 권한 없는 게시 거부
N="smoke$(date +%s)"
nxt "$DEV" mcp scaffold "$N" --dir "/tmp/$N" --title "스모크 MCP" >/dev/null
check S11-04-scaffold "$($DC exec -T api sh -c "ls /tmp/$N | tr '\n' ' '")" "Dockerfile README.md anx_auth.py pyproject.toml server.json server.py telemetry.py "
check S11-05-scaffold-no-overwrite "$(nxt "$DEV" mcp scaffold "$N" --dir "/tmp/$N" >/dev/null 2>&1; echo $?)" "1"
check S11-06-publish-pending "$(nxt "$DEV" registry publish "/tmp/$N/server.json" --json | jq -r .version.status)" "pending"
check S11-07-publish-forbidden "$(nxt "$USR" registry publish "/tmp/$N/server.json" 2>&1 | grep -c '오류 403')" "1"
check S11-08-catalog-filter "$(nxt "$DEV" catalog list --type mcp_server --json | jq '[.items[].type] | unique | join(",")' -r)" "mcp_server"
# 3) Inspector 고위험 도구 → 승인 → 재호출
curl -s -o /dev/null -X POST -H "Authorization: Bearer $(tok operator)" "$BASE/api/v1/mcp-servers/jira/refresh"
AP=$(nxt "$DEV" mcp inspect jira transition_issue --args '{"key":"ORI-1440","to":"Done"}' | jq -r .error.data.approval_id)
check S11-09-approval-required "$(printf '%s' "$AP" | cut -c1-4)" "apr_"
check S11-10-approve "$(nxt "$DEV" approvals approve "$AP" --note smoke)" "$AP: approved"
check S11-11-retry-ok "$(nxt "$DEV" mcp inspect jira transition_issue --args '{"key":"ORI-1440","to":"Done"}' --approval "$AP" | jq -r .result.isError)" "false"
# 4) chat SSE · usage · audit
check S11-12-chat "$(nxt "$USR" chat tpl-qa '장애 대응 런북 알려줘' 2>/dev/null | grep -c '^\[succeeded\] 인용')" "1"
check S11-13-usage-audit "$(nxt "$FIN" usage --group-by kind | grep -c '^합계 \$')$(nxt "$SEC" audit-verify | grep -c '유효')" "11"
# 5) install --check — 컨테이너엔 docker 없음 → 정확히 실패 보고
check S11-14-install-check "$(nxt "$DEV" install --check --json | jq -c '[.ok, .checks.docker]')" "[false,false]"
# 6) 에어갭 번들 (구성만 실번들, 이미지 포함은 계획 확인)
check S11-15-bundle-plan "$(nxt "$ADM" bundle export --dry-run | head -1)" "docker save wise-agentnexus-api:prod"
nxt "$ADM" bundle export --out /tmp/anx-bundle --no-images >/dev/null
check S11-16-bundle-import "$(nxt "$ADM" bundle import /tmp/anx-bundle | sed 's/: [0-9]* rows/: N rows/' | tr '\n' '|')" "체크섬 확인: 1 files|구성 복원: N rows|"
$DC exec -T api sh -c 'echo x >> /tmp/anx-bundle/config-backup.json'
check S11-17-bundle-tamper "$(nxt "$ADM" bundle import /tmp/anx-bundle 2>&1 | grep -c '체크섬 불일치')" "1"
check S11-18-help "$($DC exec -T api python -m app.cli --help | grep -c 'bundle')" "1"
$DC exec -T api sh -c "rm -rf /tmp/$N /tmp/anx-bundle /tmp/nx-smoke"
rm -f "$OUT"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
