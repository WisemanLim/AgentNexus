#!/bin/sh
# impl 39th — prod 리허설 (PostgreSQL · 2 workers · Valkey): 품질 검토 반영 — loopback redirect 거부 · 로그인 실패 한도(워커 공유) ·
# egress 가드 · Access Token 대리 발급 금지 · 보존(중계·작업) · 이미지 위생 · 회귀(JSON-RPC·감사 체인).
# 임시 계정(rehearsal 관리자 · rehearsal-lock 사용자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/39th/logs
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'

PW=$(openssl rand -hex 16)
LPW=$(openssl rand -hex 16)
$DC exec -T -e REH_PW="$PW" -e LOCK_PW="$LPW" api python - <<'PYEOF' >/dev/null
import os
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
with session_scope() as db:
    for name, roles, env in (("rehearsal", ["admin"], "REH_PW"), ("rehearsal-lock", ["user"], "LOCK_PW")):
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
CODE=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=s39 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
A="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW

# ── SEC-05 loopback redirect 는 prod 에서 거부, 같은 origin 콘솔은 허용 ──
Q="response_type=code&client_id=agentnexus-console&state=s&code_challenge=$CHALLENGE&code_challenge_method=S256"
check P39-01-redirect "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/idp/authorize?$Q&redirect_uri=http%3A%2F%2Flocalhost%3A5173%2Flogin%2Fcallback"):$(curl -s -o /dev/null -w '%{http_code}' "$BASE/idp/authorize?$Q&redirect_uri=http%3A%2F%2F127.0.0.1%3A8800%2Flogin%2Fcallback")" "400:200"

# ── SEC-03 로그인 실패 한도 — 10회 실패 뒤 맞는 비밀번호도 거부 (2 워커 → Valkey 공유 창이라야 10회에서 걸린다) ──
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console --data-urlencode "redirect_uri=$REDIR" \
    -d state=s -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 -d username=rehearsal-lock -d password=wrong-$i
done
R=$(curl -s -w '\n%{http_code}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console --data-urlencode "redirect_uri=$REDIR" \
  -d state=s -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 -d username=rehearsal-lock --data-urlencode "password=$LPW")
check P39-02-login-lockout "$(printf '%s' "$R" | tail -1):$(printf '%s' "$R" | grep -c '너무 많습니다')" "200:1"
unset LPW
check P39-03-lockout-audit "$(curl -s -H "$A" "$BASE/api/v1/audit/events?action=auth.login&limit=50" | jq -r '[.items[] | select(.data.reason == "too_many_failures")] | length > 0')" "true"

# ── SEC-02 egress 가드 — 메타데이터 주소로의 서버 측 요청 차단 ──
R=$(curl -s -H "$A" -H "$J" -d '{"url":"http://169.254.169.254/latest/meta-data/"}' "$BASE/api/v1/mcp-servers/openapi/fetch")
check P39-04-egress-metadata "$(printf '%s' "$R" | jq -r '.code + ":" + (.detail | contains("EgressBlocked") | tostring)')" "fetch_failed:true"

# ── SEC-07 Access Token — 다른 사람 sub 로 대리 발급 금지, 서비스 주체는 발급 ──
R=$(curl -s -H "$A" -H "$J" -d '{"name":"p39","subject":"usr_rehearsal_lock","ttl_seconds":300}' "$BASE/api/v1/access-tokens")
check P39-05-token-subject-user "$(printf '%s' "$R" | jq -r .code)" "subject_is_user"
R=$(curl -s -H "$A" -H "$J" -d '{"name":"p39","subject":"a2a:p39-service","ttl_seconds":300}' "$BASE/api/v1/access-tokens")
TID=$(printf '%s' "$R" | jq -r .id)
check P39-06-token-service-subject "$(printf '%s' "$R" | jq -r .state):$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$A" "$BASE/api/v1/access-tokens/$TID/revoke")" "active:200"

# ── BUG-01/04 보존 — 중계·작업 행도 정리 대상 ──
check P39-07-retention-mcp-state "$(curl -s -H "$A" -H "$J" -d '{}' "$BASE/api/v1/audit/retention/run" | jq -r '.deleted | has("mcp_relays") and has("mcp_tasks")')" "true"

# ── D-05 이미지 위생 — 원본 문서·세션 상태·시험이 이미지에 없다 ──
check P39-08-image-hygiene "$($DC exec -T api sh -c 'ls -d /app/.docs /app/.omc /app/.wds /app/test /app/.pytest_cache 2>/dev/null | wc -l' | tr -d ' ')" "0"

# ── 회귀 ──
check P39-09-jsonrpc-regression "$(curl -s -H "$A" -H "$J" -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"kind":"message","messageId":"p39j","role":"user","parts":[{"kind":"text","text":"orders-api 장애 분석"}]}}}' "$BASE/a2a/observability-analyst" | jq -r .result.status.state)" "completed"
check P39-10-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"

# 정리 — 실패 창 키 삭제 · 임시 계정 비활성화
$DC exec -T valkey sh -c 'valkey-cli --scan --pattern "anx:w:login-fail:rehearsal-lock:*" | xargs -r valkey-cli del' >/dev/null 2>&1
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    for u in db.query(User).filter(User.username.in_(['rehearsal', 'rehearsal-lock'])): u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
