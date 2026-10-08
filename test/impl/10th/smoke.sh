#!/bin/sh
# impl 10th — 독립 실행(FR-W01) live: 전체 → 게이트웨이 단독 → 레지스트리 단독 → 전체 복귀 + 운영 API.
# 실행 위치: 프로젝트 루트. make 로 스택을 재기동하므로 수 분 소요.
set -u
BASE="http://127.0.0.1:8800"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
up() { for i in $(seq 1 60); do curl -fsS "$BASE/health" >/dev/null 2>&1 && return; sleep 2; done; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
services() { docker compose -p wise-agentnexus-local ps --services --status running | sort | tr '\n' ' ' | sed 's/ $//'; }

# 1) 전체 (기본: COMPOSE_PROFILES=demo)
make local-stop >/dev/null 2>&1; make local-all >/dev/null 2>&1; up
check S10-01-full-services "$(services)" "a2a-demo api mcp-servers valkey"
OPS=$(tok operator)
check S10-02-health "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/system/health" | jq -r '.components.host.enabled')" "true"
BK=$(curl -s -X POST -H "Authorization: Bearer $(tok admin)" -H 'Content-Type: application/json' -d '{}' "$BASE/api/v1/admin/backup")
check S10-03-backup-signed "$(printf '%s' "$BK" | jq -r '.signature | split(".") | length')" "3"
check S10-04-restore "$(printf '%s' "$BK" | jq '{bundle, signature}' | curl -s -X POST -H "Authorization: Bearer $(tok admin)" -H 'Content-Type: application/json' -d @- "$BASE/api/v1/admin/restore" | jq '.restored.models > 0')" "true"
# 2) AI Gateway 단독 — 참조 서버 없이 LLM 프록시로만 사용
make local-stop >/dev/null 2>&1; AGENTNEXUS_COMPONENTS=gateway,identity COMPOSE_PROFILES= make local-all >/dev/null 2>&1; up
check S10-05-gw-services "$(services)" "api valkey"
USR=$(tok user)
check S10-06-gw-chat "$(curl -s -H "Authorization: Bearer $USR" -H 'Content-Type: application/json' -H 'X-AgentNexus-Data-Class: public' -d '{"model":"fast-commercial","messages":[{"role":"user","content":"게이트웨이 단독"}]}' "$BASE/v1/chat/completions" | jq -r .model)" "fast-commercial"
check S10-07-gw-no-host "$(code -H "Authorization: Bearer $USR" "$BASE/api/v1/agents")" "404"
check S10-08-gw-no-registry "$(code "$BASE/v0/servers")" "404"
# 3) MCP Registry 단독
make local-stop >/dev/null 2>&1; AGENTNEXUS_COMPONENTS=registry,identity COMPOSE_PROFILES= make local-all >/dev/null 2>&1; up
check S10-09-registry-servers "$(curl -s "$BASE/v0/servers" | jq '.servers | length > 0')" "true"
check S10-10-registry-no-gateway "$(code -X POST -H 'Content-Type: application/json' -d '{}' "$BASE/v1/chat/completions")" "404"
# 4) 전체 복귀
make local-stop >/dev/null 2>&1; make local-all >/dev/null 2>&1; up
check S10-11-back-to-full "$(services)" "a2a-demo api mcp-servers valkey"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
