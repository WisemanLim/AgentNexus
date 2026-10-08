#!/bin/sh
# impl 51st — prod 리허설 (PostgreSQL · 2 workers): 2차 품질 검토 수정 — 보안 헤더 · 푸시 URL 검사 · 데이터 등급 · 토큰 발급 경계.
# 임시 계정 rehearsal-a(사용자 역할)는 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화, 시험 PAT 는 폐기.
# 공인 주소로의 실제 푸시 전달은 하지 않는다(사설 주소 거부만 확인 — 허용 경로는 pytest C51-03).
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'
APW=$(openssl rand -hex 16)
$DC exec -T -e A_PW="$APW" api python - <<'PYEOF' >/dev/null
import os
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
with session_scope() as db:
    u = db.scalars(select(User).where(User.username == "rehearsal-a")).first()
    if u is None:
        u = User(id="usr_rehearsal_a", sub="usr_rehearsal_a", issuer=security.issuer(), username="rehearsal-a", name="rehearsal-a",
                 email="rehearsal-a@agentnexus.local", roles=["user"], team="team-platform", password_hash="")
        db.add(u)
    u.roles, u.password_hash, u.status, u.tokens_not_before = ["user"], security.hash_password(os.environ["A_PW"]), "active", None
PYEOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
code=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=s51 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d "username=rehearsal-a" --data-urlencode "password=$APW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TA=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$code" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset APW
A="Authorization: Bearer $TA"
rpc() { printf '{"jsonrpc":"2.0","id":1,"method":"%s","params":%s}' "$1" "$2"; }

# P51-01 보안 응답 헤더 — 헬스·로그인 화면
H1=$(curl -s -D - -o /dev/null "$BASE/health" | tr -d '\r' | grep -ci '^x-frame-options: DENY\|^content-security-policy: frame-ancestors .none.\|^x-content-type-options: nosniff\|^referrer-policy: no-referrer')
H2=$(curl -s -D - -o /dev/null "$BASE/idp/authorize" | tr -d '\r' | grep -ci '^x-frame-options: DENY\|^content-security-policy: frame-ancestors .none.')
check P51-01-security-headers "$H1:$H2" "4:2"

# P51-02 운영 모드 푸시 URL — 사설 주소는 게이트웨이·플랫폼 모두 거부 (검사는 스레드풀에서)
GW="$BASE/a2a/observability-analyst"; PLAT="$BASE/a2a/platform/tpl-qa"
SEND='{"message":{"kind":"message","messageId":"p51g","role":"user","parts":[{"kind":"text","text":"orders-api 장애 분석"}]}}'
LOG=test/impl/51st/logs
GT=$(curl -s -H "$A" -H "$J" -d "$(rpc message/send "$SEND")" "$GW" | tee $LOG/p51-02-gw-send.json | jq -r .result.id)
PT=$(curl -s -H "$A" -H "$J" -d "$(rpc message/send "$(echo "$SEND" | sed 's/p51g/p51p/')")" "$PLAT" | jq -r .result.id)
PRIV='"pushNotificationConfig":{"url":"http://10.0.0.7/hook"}'
GB=$(rpc tasks/pushNotificationConfig/set "{\"taskId\":\"$GT\",$PRIV}"); PB=$(rpc tasks/pushNotificationConfig/set "{\"taskId\":\"$PT\",$PRIV}")  # bash 3.2 — 중첩 인용은 변수로
E1=$(curl -s -H "$A" -H "$J" -d "$GB" "$GW" | tee $LOG/p51-02-gw-push.json | jq -r .error.code)
E2=$(curl -s -H "$A" -H "$J" -d "$PB" "$PLAT" | tee $LOG/p51-02-plat-push.json | jq -r .error.code)
check P51-02-push-private-rejected "$E1:$E2" "-32602:-32602"

# P51-03 데이터 등급 — 모르는 값은 MCP·A2A 도 거부, 대소문자는 정규화
MH="Accept: application/json, text/event-stream"; MV="MCP-Protocol-Version: 2025-11-25"
LIST='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
M1=$(curl -s -H "$A" -H "$J" -H "$MH" -H "$MV" -H "X-AgentNexus-Data-Class: secret" -d "$LIST" "$BASE/mcp/jira" | jq -r .error.code)
M2=$(curl -s -H "$A" -H "$J" -H "$MH" -H "$MV" -H "X-AgentNexus-Data-Class: Confidential" -d "$LIST" "$BASE/mcp/jira" | jq -r '.result.tools | length > 0')
A1=$(curl -s -o /dev/null -w '%{http_code}' -H "$A" -H "$J" -H "X-AgentNexus-Data-Class: top-secret" -d "$(rpc message/send "$SEND")" "$GW")
check P51-03-data-class "$M1:$M2:$A1" "-32602:true:400"

# P51-04 토큰 발급 — 스코프 PAT 로 새 토큰 불가 · 무기한 불가 · 다른 팀 키 불가
RO=$(curl -s -H "$A" -H "$J" -d '{"name":"p51-ro","scopes":["catalog:read"],"ttl_days":1}' "$BASE/api/v1/me/tokens")
RO_ID=$(echo "$RO" | jq -r .id); RO_TOK=$(echo "$RO" | jq -r .token)
T1=$(curl -s -H "Authorization: Bearer $RO_TOK" -H "$J" -d '{"name":"p51-esc","ttl_days":90}' "$BASE/api/v1/me/tokens" | jq -r .code)
T2=$(curl -s -o /dev/null -w '%{http_code}' -H "$A" -H "$J" -d '{"name":"p51-forever","ttl_days":null}' "$BASE/api/v1/me/tokens")
T3=$(curl -s -H "$A" -H "$J" -d '{"name":"p51-team","team":"team-finance"}' "$BASE/api/v1/me/tokens" | jq -r .code)
check P51-04-token-boundaries "$T1:$T2:$T3" "token_from_token:422:team_forbidden"
curl -s -o /dev/null -X DELETE -H "$A" "$BASE/api/v1/me/tokens/$RO_ID"
unset RO_TOK

# P51-05 2 워커 — 같은 요청을 여러 번 보내도 헤더·거부가 워커와 무관하게 같다
R=""; for i in 1 2 3 4; do R="$R$(curl -s -H "$A" -H "$J" -H "$MH" -H "$MV" -H "X-AgentNexus-Data-Class: secret" -d "$LIST" "$BASE/mcp/jira" | jq -r .error.code)"; done
check P51-05-consistent-across-workers "$R" "-32602-32602-32602-32602"

unset TA
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    for u in db.query(User).filter(User.username == 'rehearsal-a'): u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
