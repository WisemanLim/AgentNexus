#!/bin/sh
# impl 40th — prod 리허설 (PostgreSQL · 2 workers): 토큰 교환 워크로드 인증 · 데이터 등급 하향 신고 권한 · 회귀.
# 임시 계정(rehearsal 관리자 · rehearsal-user 사용자)은 실행 중 만든 비밀번호만 쓰고 끝나면 비활성화. 만든 워크로드는 비활성화.
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'

PW=$(openssl rand -hex 16); UPW=$(openssl rand -hex 16)
$DC exec -T -e REH_PW="$PW" -e U_PW="$UPW" api python - <<'PYEOF' >/dev/null
import os
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
with session_scope() as db:
    for name, roles, env in (("rehearsal", ["admin"], "REH_PW"), ("rehearsal-user", ["user"], "U_PW")):
        u = db.scalars(select(User).where(User.username == name)).first()
        if u is None:
            uid = "usr_" + name.replace("-", "_")
            u = User(id=uid, sub=uid, issuer=security.issuer(), username=name, name=name,
                     email=f"{name}@agentnexus.local", roles=roles, team="team-platform", password_hash="")
            db.add(u)
        u.roles, u.password_hash, u.status, u.tokens_not_before = roles, security.hash_password(os.environ[env]), "active", None
PYEOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
login() {  # $1 사용자 $2 비밀번호 → 액세스 토큰
  code=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d state=s40 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
    -d "username=$1" --data-urlencode "password=$2" | sed -n 's/.*code=\([^&]*\).*/\1/p')
  curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$code" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token
}
TA=$(login rehearsal "$PW"); TU=$(login rehearsal-user "$UPW")
A="Authorization: Bearer $TA"; U="Authorization: Bearer $TU"
unset PW UPW

# ── 토큰 교환 — 워크로드 인증 필수 ──
curl -s -o /dev/null -X PUT -H "$A" -H "$J" -d '{"audience":"mcp:p40","allowed_scopes":["r"],"default_scopes":["r"],"ttl_seconds":300}' \
  "$BASE/api/v1/identity/token-exchange/policies"
WL=$(curl -s -H "$A" -H "$J" -d "{\"name\":\"p40-tx-$(openssl rand -hex 3)\",\"kind\":\"service\",\"scopes\":[]}" "$BASE/api/v1/identity/workloads")
WID=$(printf '%s' "$WL" | jq -r .id); CID=$(printf '%s' "$WL" | jq -r .client_id); CSEC=$(printf '%s' "$WL" | jq -r .client_secret)
TX="grant_type=urn:ietf:params:oauth:grant-type:token-exchange&subject_token_type=urn:ietf:params:oauth:token-type:access_token&audience=mcp:p40"
R=$(curl -s -w '\n%{http_code}' -X POST "$BASE/idp/token" -d "$TX" -d "subject_token=$TU" -d client_id=gateway)
check P40-01-exchange-unauthenticated "$(printf '%s' "$R" | tail -1):$(printf '%s' "$R" | sed '$d' | jq -r .error)" "401:invalid_client"
TOK=$(curl -s -X POST "$BASE/idp/token" -d "$TX" -d "subject_token=$TU" -d "client_id=$CID" --data-urlencode "client_secret=$CSEC" | jq -r .access_token)
unset CSEC
ACT=$(printf '%s' "$TOK" | cut -d. -f2 | tr '_-' '/+' | awk '{l=length($0)%4; if(l==2)print $0"=="; else if(l==3)print $0"="; else print $0}' | openssl base64 -d -A 2>/dev/null | jq -r '.act.sub')
check P40-02-exchange-act-workload "$(printf '%s' "$ACT" | cut -c1-12)" "workload:p40"

# ── 데이터 등급 — 기본 권한이면 public 신고 유지, 회수하면 internal 로 올라가 상용 모델 대신 내부 모델 + 감사 ──
BODY='{"model":"fast-commercial","messages":[{"role":"user","content":"p40 등급 확인"}]}'
check P40-03-public-declared "$(curl -s -H "$U" -H "$J" -H 'X-AgentNexus-Data-Class: public' -d "$BODY" "$BASE/v1/chat/completions" | jq -r .model)" "fast-commercial"
OV=$(curl -s -H "$A" "$BASE/api/v1/identity/rbac" | jq -c '[.roles[] | select((.overrides|length)>0) | {key: .id, value: .overrides}] | from_entries')
NEW=$(printf '%s' "$OV" | jq -c '. + {"user": {"remove": ["data:declare"]}}')
curl -s -o /dev/null -X PUT -H "$A" -H "$J" -d "{\"overrides\":$NEW,\"reason\":\"p40 하향 신고 권한 회수 시험\"}" "$BASE/api/v1/identity/rbac"
sleep 6  # 다른 워커의 5초 캐시
M1=$(curl -s -H "$U" -H "$J" -H 'X-AgentNexus-Data-Class: public' -d "$BODY" "$BASE/v1/chat/completions" | jq -r .model)
M2=$(curl -s -H "$U" -H "$J" -H 'X-AgentNexus-Data-Class: public' -d "$BODY" "$BASE/v1/chat/completions" | jq -r .model)
check P40-04-raised-both-workers "$([ "$M1" != fast-commercial ] && [ "$M2" != fast-commercial ] && echo internal-route || echo "$M1/$M2")" "internal-route"
check P40-05-raise-audit "$(curl -s -H "$A" "$BASE/api/v1/audit/events?action=gateway.data_class.raise&limit=5" | jq -r '[.items[] | select(.actor == "usr_rehearsal_user")] | length > 0')" "true"
curl -s -o /dev/null -X PUT -H "$A" -H "$J" -d "{\"overrides\":$OV,\"reason\":\"p40 복원\"}" "$BASE/api/v1/identity/rbac"
check P40-06-rbac-restored "$(curl -s -H "$A" "$BASE/api/v1/identity/rbac" | jq -r '[.roles[] | select(.id=="user") | .effective | index("data:declare")] | .[0] != null')" "true"

check P40-07-jsonrpc-regression "$(curl -s -H "$A" -H "$J" -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"kind":"message","messageId":"p40j","role":"user","parts":[{"kind":"text","text":"orders-api 장애 분석"}]}}}' "$BASE/a2a/observability-analyst" | jq -r .result.status.state)" "completed"
check P40-08-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"

# 정리 — 워크로드·임시 계정 비활성화
curl -s -o /dev/null -X PATCH -H "$A" -H "$J" -d '{"enabled":false}' "$BASE/api/v1/identity/workloads/$WID"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User, Workload
with session_scope() as db:
    for u in db.query(User).filter(User.username.in_(['rehearsal', 'rehearsal-user'])): u.status = 'disabled'
    w = db.get(Workload, '$WID')
    if w: w.enabled = False" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
