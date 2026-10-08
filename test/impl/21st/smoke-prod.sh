#!/bin/sh
# impl 21st — prod 리허설 (PostgreSQL · 2 workers): 마이그레이션 0010 · 등록 승인 대기열(점검·위험도·신뢰·허용목록) · 재승인 diff ·
# 롤백 · 원격 A2A 등록 요청(pending → 승인) · 정책 2인 검토 · 즉시 사용 노출 · F03 적용 전 시험 · 플랫폼 카드 게시 심사.
# 임시 계정(rehearsal 관리자 · rehearsal-sec 보안 · rehearsal-dev 개발자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
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
ROLES = {"rehearsal": ["admin"], "rehearsal-sec": ["security"], "rehearsal-dev": ["developer"]}
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
token() {  # $1 user → access token (실제 로그인 폼 + PKCE)
  LOC=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d state=s21 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
    -d "username=$1" --data-urlencode "password=$PW")
  CODE=$(printf '%s' "$LOC" | sed -n 's/.*code=\([^&]*\).*/\1/p')
  curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token
}
A="Authorization: Bearer $(token rehearsal)"
SEC="Authorization: Bearer $(token rehearsal-sec)"
DEV="Authorization: Bearer $(token rehearsal-dev)"
unset PW

check P21-01-migration "$(curl -s -H "$A" "$BASE/api/v1/admin/updates" | jq -r .migration)" "0010"
# 등록 승인 대기열 — 개발자 게시 → 점검·위험도 → 승인(신뢰·허용목록)
NAME="io.e21prod/notes-$TS"
doc() {  # $1 version $2 tools(JSON 배열)
  echo "{\"\$schema\":\"https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json\",\"name\":\"$NAME\",\"description\":\"e21 prod\",\"version\":\"$1\",\"remotes\":[{\"type\":\"streamable-http\",\"url\":\"https://mcp.example.test/e21/mcp\"}],\"_meta\":{\"auth\":{\"type\":\"oauth2\"},\"tools\":$2,\"license\":\"MIT\"}}"
}
B=$(doc 1.0.0 '["notes.search","notes.delete_note"]')
ITEM=$(curl -s -H "$DEV" -H "$J" -d "$B" "$BASE/v0/publish" | jq -r .item.id)
ROW=$(curl -s -H "$A" "$BASE/api/v1/registry/approvals" | jq -c --arg i "$ITEM" '.items[] | select(.item.id==$i) | .review')
check P21-02-queue-risks "$(echo "$ROW" | jq -r '[.approvable, ([.tools[] | .risk] | join(","))] | join(" ")')" "true low,high"
B='{"note":"prod 리허설","trust":"partner","tool_risks":{"notes.delete_note":"high"},"allowlist_group":"anx-developers"}'
check P21-03-approve-effects "$(curl -s -H "$A" -H "$J" -d "$B" "$BASE/v1/catalog/$ITEM/approve" | jq -r '[.status, (.effects | map(select(test("허용목록"))) | length)] | join(" ")')" "approved 1"
B=$(doc 1.1.0 '["notes.search","notes.delete_note","notes.purge_all"]')
curl -s -o /dev/null -H "$DEV" -H "$J" -d "$B" "$BASE/v0/publish"
check P21-04-reapproval-diff "$(curl -s -H "$A" "$BASE/api/v1/registry/approvals" | jq -r --arg i "$ITEM" '.items[] | select(.item.id==$i) | .review.diff.reapproval')" "true"
curl -s -o /dev/null -H "$A" -H "$J" -d '{"version":"1.1.0","note":"purge 고위험 확인"}' "$BASE/v1/catalog/$ITEM/approve"
check P21-05-rollback-confirm "$(curl -s -H "$A" -H "$J" -d '{"version":"1.0.0"}' "$BASE/v1/catalog/$ITEM/rollback" | jq -r .code)" "confirm_required"
R=$(curl -s -H "$A" -H "$J" -d '{"version":"1.0.0","confirm":true}' "$BASE/v1/catalog/$ITEM/rollback" | jq -r '[.version, .trust] | join(" ")')
check P21-06-rollback-keeps-review "$R" "1.0.0 partner"
# 원격 A2A — 개발자 등록은 pending, 호출 거부 → 승인 → 활성
AID="e21-demo-$TS"
B="{\"url\":\"http://a2a-demo:8200\",\"id\":\"$AID\"}"
check P21-07-a2a-pending "$(curl -s -H "$DEV" -H "$J" -d "$B" "$BASE/api/v1/a2a/agents" | jq -r .status)" "pending"
MSG='{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","messageId":"m1","parts":[{"kind":"text","text":"orders-api"}]}}}'
check P21-08-a2a-call-refused "$(curl -s -H "$A" -H "$J" -d "$MSG" "$BASE/a2a/$AID" | jq -r .error.data.code)" "agent_inactive"
CAT=$(curl -s -H "$A" "$BASE/api/v1/a2a/agents/$AID" | jq -r .catalog_item_id)
curl -s -o /dev/null -H "$A" -H "$J" -d '{"note":"prod 리허설 승인"}' "$BASE/v1/catalog/$CAT/approve"
check P21-09-a2a-active "$(curl -s -H "$A" "$BASE/api/v1/a2a/agents/$AID" | jq -r .status)" "active"
# 정책 2인 검토 — 직접 쓰기 409 → 초안 → 시험 → 요청 → 자기 승인 403 → 관리자 승인 → 두 워커에서 같은 판정
POL="{\"name\":\"e21 prod deny $TS\",\"subject_type\":\"role\",\"subject\":\"user\",\"target_type\":\"tool\",\"target\":\"e21prod$TS.*\",\"effect\":\"deny\",\"priority\":30}"
check P21-10-direct-write-409 "$(curl -s -o /dev/null -w '%{http_code}' -H "$SEC" -H "$J" -d "$POL" "$BASE/api/v1/gateway/policies")" "409"
B="{\"policy\":$POL}"
D=$(curl -s -H "$SEC" -H "$J" -d "$B" "$BASE/api/v1/gateway/policies/drafts" | jq -r .id)
check P21-11-draft-test "$(curl -s -X POST -H "$SEC" "$BASE/api/v1/gateway/policies/drafts/$D/test" | jq -r .test_result.passed)" "true"
curl -s -o /dev/null -H "$SEC" -H "$J" -d '{"reason":"prod 리허설 — 쓰기 차단"}' "$BASE/api/v1/gateway/policies/drafts/$D/submit"
check P21-12-self-approval "$(curl -s -H "$SEC" -H "$J" -d '{}' "$BASE/api/v1/gateway/policies/drafts/$D/approve" | jq -r .code)" "self_approval"
PID=$(curl -s -H "$A" -H "$J" -d '{"note":"확인"}' "$BASE/api/v1/gateway/policies/drafts/$D/approve" | jq -r .policy_id)
SIM="{\"kind\":\"tool\",\"target\":\"e21prod$TS.write\",\"subject\":{\"sub\":\"x\",\"roles\":[\"user\"]}}"
OK=0; for i in 1 2 3 4; do V=$(curl -s -H "$SEC" -H "$J" -d "$SIM" "$BASE/api/v1/gateway/policies/simulate" | jq -r .decision.policy_id); [ "$V" = "$PID" ] && OK=$((OK+1)); done
case "$PID" in pol_*) ;; *) OK="no-policy:$PID" ;; esac  # 승인 실패(null)면 기본 거부와 같아져 거짓 통과하지 않게
check P21-13-applied-all-workers "$OK" "4"
# 즉시 사용 — 데모 원격 에이전트: 시험 교환 → 미리보기 → 적용 → 노출(두 워커)
OA=$(curl -s -H "$A" "$BASE/v1/catalog?type=a2a_agent&q=observability-analyst" | jq -r '.items[0].id')
check P21-14-exchange-test "$(curl -s -H "$A" -H "$J" -d '{"scopes":[],"ttl_seconds":300}' "$BASE/v1/catalog/$OA/instant-use/auth" | jq -r .ok)" "true"
check P21-15-dry-run "$(curl -s -H "$A" -H "$J" -d '{"dry_run":true}' "$BASE/v1/catalog/$OA/instant-use/policy" | jq -r '.rego | test("package agentnexus.gateway")')" "true"
curl -s -o /dev/null -H "$A" -H "$J" -d '{"calls_per_min":600}' "$BASE/v1/catalog/$OA/instant-use/policy"
OK=0; for i in 1 2 3 4; do V=$(curl -s -H "$A" "$BASE/v1/catalog/$OA/exposure" | jq -r '.exposed and .within_slo'); [ "$V" = "true" ] && OK=$((OK+1)); done
check P21-16-exposed-all-workers "$OK" "4"
# F03 적용 전 시험 — 실패해도 저장하지 않는다
curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/mcp-servers/dynatrace/refresh"
EP=$(curl -s -H "$A" "$BASE/api/v1/mcp-servers/dynatrace" | jq -r .endpoint)
check P21-17-test-config-ok "$(curl -s -H "$A" -H "$J" -d '{}' "$BASE/api/v1/mcp-servers/dynatrace/test-config" | jq -r .ok)" "true"
B='{"endpoint":"http://mcp-servers:8100/nope/mcp"}'
check P21-18-test-config-fail "$(curl -s -H "$A" -H "$J" -d "$B" "$BASE/api/v1/mcp-servers/dynatrace/test-config" | jq -r .ok)" "false"
check P21-19-not-saved "$(curl -s -H "$A" "$BASE/api/v1/mcp-servers/dynatrace" | jq -r .endpoint)" "$EP"
# 플랫폼 Agent Card — 개발자 게시 요청 → 승인 전 비공개 → 승인 → 공개
PR="e21-prod-$TS"
YAML='planner: {mode: template, max_steps: 2}\nworkers: {summarize: {model: domain-local}}\ndefault_plan:\n  - {id: s1, agent: summarize, args: {message: \"{{request}}\"}}\n'
B="{\"id\":\"$PR\",\"name\":\"$PR\",\"yaml\":\"$YAML\"}"
curl -s -o /dev/null -H "$DEV" -H "$J" -d "$B" "$BASE/api/v1/agents"
curl -s -o /dev/null -X POST -H "$DEV" "$BASE/api/v1/agents/$PR/publish"
check P21-20-card-requested "$(curl -s -X POST -H "$DEV" "$BASE/api/v1/agents/$PR/a2a-card/publish" | jq -r .requested)" "true"
check P21-21-card-hidden "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/a2a/platform/$PR/.well-known/agent-card.json")" "404"
PC=$(curl -s -H "$A" "$BASE/api/v1/registry/approvals" | jq -r --arg n "$PR" '.items[] | select(.item.type=="agent_profile" and .item.name==$n) | .item.id')
curl -s -o /dev/null -H "$A" -H "$J" -d '{"note":"게시 승인"}' "$BASE/v1/catalog/$PC/approve"
OK=0; for i in 1 2 3 4; do V=$(curl -s "$BASE/a2a/platform/$PR/.well-known/agent-card.json" | jq -r '.url | endswith("/a2a/platform/'"$PR"'")'); [ "$V" = "true" ] && OK=$((OK+1)); done
check P21-22-card-public-all-workers "$OK" "4"
# 정리 — 리허설 정책 삭제(관리자 비상 경로), 임시 계정 비활성화
curl -s -o /dev/null -X DELETE -H "$A" "$BASE/api/v1/gateway/policies/$PID"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    for n in ('rehearsal', 'rehearsal-sec', 'rehearsal-dev'):
        u = db.query(User).filter(User.username == n).first()
        if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
