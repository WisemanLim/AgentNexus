#!/bin/sh
# impl 24th — prod 리허설 (PostgreSQL · 2 workers): 마이그레이션 0014 · 라우팅 규칙 게시·되돌리기 · 키별 허용 모델 · 구성요소 헬스·재시작 ·
# framework · Inspector 가상 사용자 · hosted OpenAPI OBO 교환 · 서명 없는 푸시 거부 · 파일 Part.
# 임시 계정(rehearsal 관리자 · rehearsal-dev 개발자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화. 만든 규칙·키·서버 설정은 정리.
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
$DC exec -T -e REH_PW="$PW" api python - <<'PYEOF' >/dev/null
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
        u.roles, u.password_hash, u.status, u.tokens_not_before = roles, security.hash_password(os.environ["REH_PW"]), "active", None
PYEOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
token() {
  CODE=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d state=s24 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
    -d "username=$1" --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
  curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token
}
A="Authorization: Bearer $(token rehearsal)"
DEV="Authorization: Bearer $(token rehearsal-dev)"
unset PW
AG=observability-analyst

check P24-01-migration "$(curl -s -H "$A" "$BASE/api/v1/admin/updates" | jq -r .migration)" "0014"
# 라우팅 규칙 게시 · 게시 안 된 변경 · 되돌리기
RULE='{"name":"e24p rule","match_model":"e24p-alias","strategy":"priority","targets":[{"model":"fast-commercial"}],"fallbacks":["domain-local"],"retries":1,"timeout_ms":30000,"priority":901,"enabled":true}'
RID=$(curl -s -H "$A" -H "$J" -d "$RULE" "$BASE/api/v1/gateway/routing/rules" | jq -r .id)
check P24-02-publish "$(curl -s -H "$A" -H "$J" -d '{"reason":"리허설 게시"}' "$BASE/api/v1/gateway/routing/rules/$RID/publish" | jq -r .version)" "1"
curl -s -o /dev/null -X PUT -H "$A" -H "$J" -d "$(printf '%s' "$RULE" | jq -c '.targets=[{"model":"smart-commercial"}]')" "$BASE/api/v1/gateway/routing/rules/$RID"
check P24-03-pending "$(curl -s -H "$A" "$BASE/api/v1/gateway/routing/rules/$RID/versions" | jq -r '.pending[0].field')" "targets"
check P24-04-rollback "$(curl -s -H "$A" -H "$J" -d '{"version":1,"reason":"리허설 되돌리기"}' "$BASE/api/v1/gateway/routing/rules/$RID/rollback" | jq -r '"\(.version):\(.targets[0].model)"')" "2:fast-commercial"
curl -s -o /dev/null -X DELETE -H "$A" "$BASE/api/v1/gateway/routing/rules/$RID"
# 키별 허용 모델
KEY=$(curl -s -H "$A" -H "$J" -d "{\"name\":\"e24p-key-$TS\",\"kind\":\"client_key\",\"allowed_models\":[\"domain-local\"]}" "$BASE/api/v1/me/tokens")
KT=$(printf '%s' "$KEY" | jq -r .token); KID=$(printf '%s' "$KEY" | jq -r .id)
chat() { curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KT" -H "$J" -H 'X-AgentNexus-Data-Class: public' \
  -d "{\"model\":\"$1\",\"messages\":[{\"role\":\"user\",\"content\":\"e24p\"}]}" "$BASE/v1/chat/completions"; }
check P24-05-key-allowed "$(chat domain-local)" "200"
check P24-06-key-denied "$(chat fast-commercial)" "403"
curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/gateway/client-keys/$KID/revoke"
# 구성요소 헬스·재시작 · framework
check P24-07-components "$(curl -s -H "$A" "$BASE/api/v1/system/components" | jq -r '[.items[] | has("health") and has("restarts_24h") and has("last_error")] | all')" "true"
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d '{"framework":"LangGraph"}' "$BASE/api/v1/a2a/agents/$AG"
check P24-08-framework "$(curl -s -H "$A" "$BASE/api/v1/a2a/agents/$AG" | jq -r '"\(.framework):\(.framework_source)"')" "LangGraph:operator"
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d '{"framework":""}' "$BASE/api/v1/a2a/agents/$AG"
# Inspector 가상 사용자
curl -s -o /dev/null -X POST -H "$A" "$BASE/api/v1/mcp-servers/jira/refresh"
check P24-09-test-principal "$(curl -s -H "$DEV" -H "$J" -d '{"method":"tools/call","params":{"name":"search_issues","arguments":{"query":"결제"}},"test_principal":{"roles":["user"]}}' "$BASE/api/v1/mcp-servers/jira/inspect" | jq -r '"\(.test_principal.sub):\(.dry_run)"')" "test:usr_rehearsal_dev:false"
check P24-10-test-roles-exceed "$(curl -s -H "$DEV" -H "$J" -d '{"method":"tools/list","test_principal":{"roles":["admin"]}}' "$BASE/api/v1/mcp-servers/jira/inspect" | jq -r .code)" "test_roles_exceed"
# hosted OpenAPI OBO — 업스트림 = api 자신(/health), 호출마다 api:{ns} 교환 기록
NS="e24p-obo-$TS"
SPEC='{"openapi":"3.1.0","info":{"title":"health","version":"1"},"paths":{"/health":{"get":{"operationId":"health.get","responses":{"200":{"description":"ok"}}}}}}'
curl -s -o /dev/null -H "$A" -H "$J" -d "{\"namespace\":\"$NS\",\"title\":\"E24 OBO\",\"openapi\":$SPEC,\"base_url\":\"http://api:8000\",\"identity\":\"obo\",\"scopes\":[\"api:call\"]}" "$BASE/api/v1/mcp-servers/openapi"
check P24-11-obo-call "$(curl -s -H "$A" -H "$J" -d '{"method":"tools/call","params":{"name":"health_get","arguments":{}}}' "$BASE/api/v1/mcp-servers/$NS/inspect" | jq -r '.error == null')" "true"
check P24-12-obo-exchange "$(curl -s -H "$A" "$BASE/api/v1/identity/token-exchanges?audience=api:$NS" | jq -r '[.items[] | select(.decision=="issued")] | length > 0')" "true"
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d '{"status":"disabled"}' "$BASE/api/v1/mcp-servers/$NS"
# 서명 없는 푸시 거부 · 파일 Part
$DC exec -T -e AG="$AG" api python - <<'PYEOF' >/dev/null
import os
from app.db import session_scope
from app.models import A2AAgent, A2ATask
with session_scope() as db:
    ag = db.get(A2AAgent, os.environ["AG"])
    ag.auth = {**(ag.auth or {}), "push_verify": {"jwks_url": "https://jwks.invalid/keys"}}
    if db.get(A2ATask, f"{ag.id}:e24p-push") is None:
        db.add(A2ATask(id=f"{ag.id}:e24p-push", remote_task_id="e24p-push", agent_id=ag.id, state="working", push_token="e24p-tok",
                       history=[], artifacts=[]))
PYEOF
check P24-13-push-unsigned "$(curl -s -o /dev/null -w '%{http_code}' -H "$J" -d '{"kind":"task","id":"e24p-push","status":{"state":"completed"}}' "$BASE/a2a/push/e24p-tok")" "401"
$DC exec -T -e AG="$AG" api python - <<'PYEOF' >/dev/null
import os
from app.db import session_scope
from app.models import A2AAgent
with session_scope() as db:
    ag = db.get(A2AAgent, os.environ["AG"])
    ag.auth = {k: v for k, v in (ag.auth or {}).items() if k != "push_verify"}
PYEOF
B64=$(printf '주문 로그 O-1 실패' | openssl base64 | tr -d '\n')
check P24-14-file-part "$(curl -s -H "$DEV" -H "$J" -d "{\"text\":\"orders-api 로그 분석\",\"files\":[{\"name\":\"log.txt\",\"mimeType\":\"text/plain\",\"bytes\":\"$B64\"}]}" "$BASE/api/v1/a2a/agents/$AG/try" | jq -r 'has("error") | not')" "true"
# 정리 — 임시 계정 비활성화
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    for n in ('rehearsal', 'rehearsal-dev'):
        u = db.query(User).filter(User.username == n).first()
        if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
