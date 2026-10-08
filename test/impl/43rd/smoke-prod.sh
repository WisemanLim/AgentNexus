#!/bin/sh
# impl 43rd — prod 리허설 (PostgreSQL · 2 workers): 사설 푸시 URL 거부 · scrypt 새 형식·재해싱 · refresh 재사용 계열 폐기 · 회귀.
# 임시 계정(rehearsal 관리자 · rehearsal-legacy 사용자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'

PW=$(openssl rand -hex 16); LPW=$(openssl rand -hex 16)
$DC exec -T -e REH_PW="$PW" -e LEG_PW="$LPW" api python - <<'PYEOF' >/dev/null
import base64, hashlib, os, secrets
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
def legacy(pw):  # 43rd 이전 형식 (N=2^14·r=8·p=1) — 재해싱 확인용
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()
with session_scope() as db:
    for name, roles, h in (("rehearsal", ["admin"], security.hash_password(os.environ["REH_PW"])),
                           ("rehearsal-legacy", ["user"], legacy(os.environ["LEG_PW"]))):
        u = db.scalars(select(User).where(User.username == name)).first()
        if u is None:
            uid = "usr_" + name.replace("-", "_")
            u = User(id=uid, sub=uid, issuer=security.issuer(), username=name, name=name,
                     email=f"{name}@agentnexus.local", roles=roles, team="team-platform", password_hash="")
            db.add(u)
        u.roles, u.password_hash, u.status, u.tokens_not_before = roles, h, "active", None
PYEOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
tokens() {  # $1 사용자 $2 비밀번호 → 토큰 응답 JSON
  code=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d state=s43 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
    -d "username=$1" --data-urlencode "password=$2" | sed -n 's/.*code=\([^&]*\).*/\1/p')
  curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$code" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER"
}
A="Authorization: Bearer $(tokens rehearsal "$PW" | jq -r .access_token)"
LT=$(tokens rehearsal-legacy "$LPW")
unset PW LPW
hash_of() { $DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db: print(db.query(User).filter(User.username == '$1').first().password_hash[:22])"; }

# ── 클라이언트 푸시 URL — 사설·루프백 거부 (게이트웨이 경유 · 플랫폼) ──
SEND='{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"kind":"message","messageId":"p43s","role":"user","parts":[{"kind":"text","text":"orders-api 장애 분석"}]}}}'
TID=$(curl -s -H "$A" -H "$J" -d "$SEND" "$BASE/a2a/observability-analyst" | jq -r .result.id)
PUSH=$(printf '{"jsonrpc":"2.0","id":2,"method":"tasks/pushNotificationConfig/set","params":{"taskId":"%s","pushNotificationConfig":{"url":"http://10.1.2.3/hook"}}}' "$TID")
check P43-01-gateway-push-private "$(curl -s -H "$A" -H "$J" -d "$PUSH" "$BASE/a2a/observability-analyst" | jq -r '"\(.error.code):\(.error.data.code)"')" "-32602:push_url_rejected"
PPUSH='{"jsonrpc":"2.0","id":3,"method":"tasks/pushNotificationConfig/set","params":{"taskId":"x","pushNotificationConfig":{"url":"http://127.0.0.1/hook"}}}'
check P43-02-platform-push-loopback "$(curl -s -H "$A" -H "$J" -d "$PPUSH" "$BASE/a2a/platform" | jq -r .error.code)" "-32602"

# ── scrypt — 새 계정은 새 형식, 이전 형식은 로그인 성공 시 재해싱 ──
check P43-03-new-hash-format "$(hash_of rehearsal)" 'scrypt$n=16384,r=8,p=5'
check P43-04-legacy-rehashed "$(printf '%s' "$LT" | jq -r '.access_token | length > 0'):$(hash_of rehearsal-legacy)" 'true:scrypt$n=16384,r=8,p=5'

# ── refresh 재사용 — 유예(10초) 밖 재제시 → 계열 전부 폐기 + 감사 ──
R1=$(printf '%s' "$LT" | jq -r .refresh_token)
R2=$(curl -s -X POST "$BASE/idp/token" -d grant_type=refresh_token --data-urlencode "refresh_token=$R1" -d client_id=agentnexus-console | jq -r .refresh_token)
sleep 11
REUSE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/idp/token" -d grant_type=refresh_token --data-urlencode "refresh_token=$R1" -d client_id=agentnexus-console)
AFTER=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/idp/token" -d grant_type=refresh_token --data-urlencode "refresh_token=$R2" -d client_id=agentnexus-console)
check P43-05-refresh-reuse-family "$([ "$R2" != null ] && echo rotated):$REUSE:$AFTER" "rotated:400:400"
check P43-06-reuse-audit "$(curl -s -H "$A" "$BASE/api/v1/audit/events?action=auth.refresh_reuse&limit=5" | jq -r '[.items[] | select(.actor == "user:usr_rehearsal_legacy")] | length > 0')" "true"
unset R1 R2 LT

check P43-07-jsonrpc-regression "$(curl -s -H "$A" -H "$J" -d "$SEND" "$BASE/a2a/observability-analyst" | jq -r .result.status.state)" "completed"
check P43-08-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"

$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    for u in db.query(User).filter(User.username.in_(['rehearsal', 'rehearsal-legacy'])): u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
