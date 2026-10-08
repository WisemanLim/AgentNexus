#!/bin/sh
# impl 8th — 오케스트레이션 스모크. 템플릿 3종 E2E + 승인 재개 + SSE + 플랫폼 A2A.
# prod 리허설: TOKEN=<사용자 토큰> EXPECT_NAME=<표시 이름> sh smoke.sh
set -u
BASE="${1:-http://127.0.0.1:8800}"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
tok() { curl -s -X POST "$BASE/idp/token" -d grant_type=password -d "username=$1" -d "password=$1-pass" -d client_id=smoke | jq -r .access_token; }
USR="${TOKEN:-$(tok user)}"
OPS="${TOKEN:-$(tok operator)}"
NAME="${EXPECT_NAME:-사용자}"
H="Authorization: Bearer $USR"
session() { curl -s -X POST -H "$H" -H 'Content-Type: application/json' -d "{\"profile_id\":\"$1\"}" "$BASE/api/v1/sessions" | jq -r .id; }
send() { curl -s -X POST -H "$H" -H 'Content-Type: application/json' -d "{\"content\":\"$2\"}" "$BASE/api/v1/sessions/$1/messages" | jq -r .run_id; }
wait_run() { for i in $(seq 1 100); do st=$(curl -s -H "$H" "$BASE/api/v1/runs/$1" | jq -r .status); case "$st" in succeeded|partial|failed|waiting_approval|canceled) echo "$st"; return;; esac; sleep 0.3; done; echo timeout; }

# 1) 사내 Q&A — SSE 스트림
SID=$(session tpl-qa)
SSE=$(curl -s -N -X POST -H "$H" -H 'Accept: text/event-stream' -H 'Content-Type: application/json' -d '{"content":"장애 대응 런북 알려줘"}' "$BASE/api/v1/sessions/$SID/messages")
check S8-01-qa-sse-completed "$(printf '%s' "$SSE" | grep -c '^event: run.completed')" "1"
check S8-02-qa-delta-streamed "$(printf '%s' "$SSE" | grep -c '^event: delta' | awk '{print ($1>0)}')" "1"
# 2) 이슈 보고 자동화 — 승인 대기 → 승인 → 완료
SID=$(session tpl-issue-report); RID=$(send "$SID" "이번 주 ORION 이슈 보고서 작성")
check S8-03-waiting-approval "$(wait_run "$RID")" "waiting_approval"
AP=$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r '.steps[] | select(.status=="waiting_approval") | .approval_id')
curl -s -X POST -H "$H" "$BASE/api/v1/approvals/$AP/approve" >/dev/null
check S8-04-resumed-succeeded "$(wait_run "$RID")" "succeeded"
check S8-05-page-by-user "$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r '.steps[] | select(.name=="s4") | .output.raw.created_by')" "$NAME"
# 3) 장애 분석 위임 — 원격 A2A 병렬
SID=$(session tpl-incident); RID=$(send "$SID" "orders-api 장애 원인 분석")
check S8-06-incident-succeeded "$(wait_run "$RID")" "succeeded"
check S8-07-remote-step "$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r '.steps[] | select(.node=="remote") | .output.state')" "completed"
# 4) 플랫폼 A2A 서버 → 오케스트레이션
A2A=$(curl -s -X POST -H "$H" -H 'Content-Type: application/json' -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"message/send\",\"params\":{\"message\":{\"kind\":\"message\",\"messageId\":\"$(uuidgen)\",\"role\":\"user\",\"parts\":[{\"kind\":\"text\",\"text\":\"휴가 정책\"}]}}}" "$BASE/a2a/platform/tpl-qa")
check S8-08-platform-a2a "$(printf '%s' "$A2A" | jq -r .result.status.state)" "completed"
check S8-09-host-sessions-closed "$(curl -s -H "Authorization: Bearer $OPS" "$BASE/api/v1/host/sessions" | jq '[.items[] | select(.state=="closed")] | length > 0')" "true"
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
