#!/bin/sh
# impl 22nd — prod 리허설 (PostgreSQL · 2 workers): 마이그레이션 0011 · 응답 메타(노드별 비용·첫 토큰) · 피드백·다시 생성 분기 ·
# 본문 검색·내보내기 · 승인 맥락·고위험 기억 거부·만료 → 부분 결과 · 드라이런(쓰기 0)·버전 실행 · 코드만 저장 → 게시 409 → 되돌리기 ·
# 부분 결과로 계속 · 실패 시뮬레이션 · Host 세션 KPI · 프로파일 준비 상태.
# 임시 계정(rehearsal 관리자 · rehearsal-dev 개발자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'
TS=$(date +%s)

PW=$(openssl rand -hex 16)
$DC exec -T -e REH_PW="$PW" api python - <<'EOF' >/dev/null
import os
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
ROLES = {"rehearsal": ["admin"], "rehearsal-dev": ["developer"]}
with session_scope() as db:
    for name, roles in ROLES.items():
        u = db.scalars(select(User).where(User.username == name)).first()
        if u is None:
            uid = "usr_" + name.replace("-", "_")
            u = User(id=uid, sub=uid, issuer=security.issuer(), username=name, name=name, email=f"{name}@agentnexus.local",
                     roles=roles, team="team-platform", password_hash="")
            db.add(u)
        u.roles, u.password_hash, u.status = roles, security.hash_password(os.environ["REH_PW"]), "active"
EOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
token() {
  LOC=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d state=s22 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
    -d "username=$1" --data-urlencode "password=$PW")
  CODE=$(printf '%s' "$LOC" | sed -n 's/.*code=\([^&]*\).*/\1/p')
  curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token
}
A="Authorization: Bearer $(token rehearsal)"
DEV="Authorization: Bearer $(token rehearsal-dev)"
unset PW

wait_run() {  # $1 run id $2 종료 상태 정규식 → 상태
  for i in $(seq 1 120); do
    ST=$(curl -s -H "$A" "$BASE/api/v1/runs/$1" | jq -r .status)
    echo "$ST" | grep -Eq "$2" && { echo "$ST"; return; }
    sleep 0.5
  done
  echo "timeout:$ST"
}
ask() {  # $1 profile $2 text → "sid rid"
  SID=$(curl -s -H "$A" -H "$J" -d "$(jq -n --arg p "$1" '{profile_id:$p}')" "$BASE/api/v1/sessions" | jq -r .id)
  RID=$(curl -s -H "$A" -H "$J" -d "$(jq -n --arg c "$2" '{content:$c}')" "$BASE/api/v1/sessions/$SID/messages" | jq -r .run_id)
  echo "$SID $RID"
}
profile() {  # $1 id $2 yaml — 개발자가 만들고 게시
  curl -s -o /dev/null -H "$DEV" -H "$J" -d "$(jq -n --arg i "$1" --arg y "$2" '{id:$i,name:$i,yaml:$y}')" "$BASE/api/v1/agents"
  curl -s -o /dev/null -X POST -H "$DEV" -H "$J" -d '{}' "$BASE/api/v1/agents/$1/publish"
}

check P22-01-migration "$(curl -s -H "$A" "$BASE/api/v1/admin/updates" | jq -r .migration)" "0011"
# 응답 메타 · 피드백 · 다시 생성 분기 · 본문 검색 · 내보내기
set -- $(ask tpl-qa "장애 대응 런북 알려줘 e22p$TS"); SID=$1; RID=$2
check P22-02-run "$(wait_run "$RID" 'succeeded|partial|failed')" "succeeded"
check P22-03-meta "$(curl -s -H "$A" "$BASE/api/v1/runs/$RID" | jq -r '[.meta.first_token_ms != null, ([.meta.nodes[] | select(.node=="summarize")][0].tokens > 0), (.meta.sources.cited >= 1)] | all')" "true"
MID=$(curl -s -H "$A" "$BASE/api/v1/sessions/$SID" | jq -r '[.messages[] | select(.role=="assistant")][0].id')
check P22-04-feedback "$(curl -s -H "$A" -H "$J" -d '{"rating":"up"}' "$BASE/api/v1/sessions/$SID/messages/$MID/feedback" | jq -r .feedback.rating)" "up"
R2=$(curl -s -X POST -H "$A" "$BASE/api/v1/sessions/$SID/messages/$MID/regenerate" | jq -r .run_id)
wait_run "$R2" 'succeeded|partial|failed' >/dev/null
check P22-05-regenerate-branch "$(curl -s -H "$A" "$BASE/api/v1/sessions/$SID" | jq -r '[.messages[] | select(.branch_of != null)] | length')" "1"
check P22-06-content-search "$(curl -s -H "$A" "$BASE/api/v1/sessions?q=e22p$TS" | jq -r --arg s "$SID" '[.items[].id] | index($s) != null')" "true"
check P22-07-export-md "$(curl -s -o /dev/null -w '%{content_type}' -H "$A" "$BASE/api/v1/sessions/$SID/export" | cut -d';' -f1)" "text/markdown"
# 승인 맥락 · 고위험 기억 거부 · 만료 → 자동 거부 → 부분 결과
set -- $(ask tpl-issue-report "ORION 이번 주 이슈 요약해서 주간 보고 페이지로 만들어줘"); RID=$2
wait_run "$RID" 'waiting_approval|succeeded|partial|failed' >/dev/null
AP=$(curl -s -H "$A" "$BASE/api/v1/runs/$RID" | jq -r '.approvals[0].id')
check P22-08-approval-context "$(curl -s -H "$A" "$BASE/api/v1/approvals/$AP" | jq -r '[(.context.identity.audience | startswith("mcp:")), (.context.source.step != null), (.context.remember_allowed == false)] | all')" "true"
check P22-09-no-remember-high "$(curl -s -H "$A" -H "$J" -d '{"remember":true}' "$BASE/api/v1/approvals/$AP/approve" | jq -r .code)" "remember_not_allowed"
$DC exec -T -e AP="$AP" api python -c "import os; from datetime import timedelta; from app.db import session_scope, utcnow; from app.models import Approval
with session_scope() as db: db.get(Approval, os.environ['AP']).expires_at = utcnow() - timedelta(seconds=1)" >/dev/null
# 만료 처리는 API 워커의 스케줄러(60초 주기)가 한다 — 별도 프로세스로 부르지 않는다
for i in $(seq 1 3); do ST=$(wait_run "$RID" 'succeeded|partial|failed'); case "$ST" in timeout*) ;; *) break ;; esac; done
check P22-10-expiry-partial "$ST" "partial"
# 드라이런(쓰기 0) · 게시 버전 실행 · 게시 전 점검
PID="e22p-dry-$TS"
profile "$PID" 'planner: {mode: template, max_steps: 3}
workers: {retriever: {tools: [jira.search_issues]}, executor: {tools: [confluence.create_page]}}
default_plan:
  - {id: s1, agent: retriever, tool: jira.search_issues, args: {query: "{{request}}"}}
  - {id: s2, agent: executor, tool: confluence.create_page, depends_on: [s1], args: {space: ORION, title: e22p, body: x}}
'
T=$(curl -s -H "$DEV" -H "$J" -d '{"request":"결제"}' "$BASE/api/v1/agents/$PID/test")
TR=$(printf '%s' "$T" | jq -r .run_id)
check P22-11-dry-run "$(printf '%s' "$T" | jq -r '[.status == "succeeded", ([.run.steps[] | select(.name=="s2")][0].output.dry_run == true)] | all')" "true"
check P22-12-dry-run-no-approval "$(curl -s -H "$A" "$BASE/api/v1/approvals?run_id=$TR" | jq -r '.items | length')" "0"
G=$(curl -s -H "$DEV" "$BASE/api/v1/agents/$PID" | jq -c '.graph | .default_plan += [{"id":"s3","agent":"retriever","tool":"jira.search_issues"}]')
curl -s -o /dev/null -X PUT -H "$DEV" -H "$J" -d "$(jq -n --argjson g "$G" '{graph:$g}')" "$BASE/api/v1/agents/$PID/graph"
check P22-13-version-run "$(curl -s -H "$DEV" -H "$J" -d '{"request":"결제","version":1}' "$BASE/api/v1/agents/$PID/test" | jq -r '[.run.profile_version, (.run.steps | length)] | join(",")')" "1,2"
check P22-14-prepublish-checks "$(curl -s -H "$DEV" "$BASE/api/v1/agents/$PID/checks" | jq -r '[.items[] | select(.key=="tests" or .key=="validation")] | map(.ok) | all')" "true"
# 코드만 저장 → 게시 409 → 되돌리기 → 게시
Y=$(curl -s -H "$DEV" "$BASE/api/v1/agents/$PID/code" | jq -r .code)
BAD=$(printf '%s\n- {id: s9, agent: summarize, depends_on: [ghost]}\n' "$Y")
check P22-15-code-only "$(curl -s -X PUT -H "$DEV" -H "$J" -d "$(jq -n --arg y "$BAD" '{yaml:$y,allow_invalid:true}')" "$BASE/api/v1/agents/$PID/graph" | jq -r .saved)" "code_only"
check P22-16-publish-blocked "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$DEV" -H "$J" -d '{}' "$BASE/api/v1/agents/$PID/publish")" "409"
curl -s -o /dev/null -X POST -H "$DEV" "$BASE/api/v1/agents/$PID/code/revert"
check P22-17-publish-after-revert "$(curl -s -X POST -H "$DEV" -H "$J" -d '{}' "$BASE/api/v1/agents/$PID/publish" | jq -r .version)" "2"
# 부분 결과로 계속 · 실패 시뮬레이션
FP="e22p-fail-$TS"
profile "$FP" 'planner: {mode: template, max_steps: 3}
workers: {retriever: {tools: [jira.search_issues, jira.get_issue]}, summarize: {model: domain-local}}
on_failure: fail
retry: {max_attempts: 1}
default_plan:
  - {id: bad, agent: retriever, tool: jira.get_issue, args: {key: NOPE-1}}
  - {id: ok, agent: retriever, tool: jira.search_issues}
  - {id: sum, agent: summarize, depends_on: [ok]}
'
set -- $(ask "$FP" "결제"); RID=$2
check P22-18-fail-stop "$(wait_run "$RID" 'succeeded|partial|failed')" "failed"
check P22-19-continue "$(curl -s -X POST -H "$A" "$BASE/api/v1/runs/$RID/continue" | jq -r .result)" "resumed"
check P22-20-continued-partial "$(wait_run "$RID" '^(succeeded|partial)$')" "partial"
SP="e22p-req-$TS"
profile "$SP" 'planner: {mode: template, max_steps: 3}
workers: {retriever: {tools: [jira.search_issues]}, summarize: {model: domain-local}}
fanout: {join: required, required: [a]}
default_plan:
  - {id: a, agent: retriever, tool: jira.search_issues}
  - {id: b, agent: retriever, tool: jira.search_issues}
  - {id: s, agent: summarize, depends_on: [a, b]}
'
check P22-21-simulate-required "$(curl -s -H "$DEV" -H "$J" -d '{"failed":["a"]}' "$BASE/api/v1/agents/$SP/simulate" | jq -r .status)" "failed"
# Host 세션 KPI · 프로파일 준비 상태
check P22-22-host-kpi "$(curl -s -H "$A" "$BASE/api/v1/host/sessions" | jq -r '[(.kpi | has("active") and has("reconnecting")), ([.items[] | select(.events[0].type == "opened")] | length > 0)] | all')" "true"
check P22-23-readiness "$(curl -s -H "$A" "$BASE/api/v1/chat/profiles" | jq -r '[.items[] | select(.id=="tpl-qa")][0].ready')" "true"
# 정리 — 리허설 프로파일 보관, 임시 계정 비활성화
for p in "$PID" "$FP" "$SP"; do curl -s -o /dev/null -X DELETE -H "$DEV" "$BASE/api/v1/agents/$p"; done
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    for n in ('rehearsal', 'rehearsal-dev'):
        u = db.query(User).filter(User.username == n).first()
        if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
