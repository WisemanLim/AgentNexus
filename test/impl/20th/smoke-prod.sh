#!/bin/sh
# impl 20th — prod 리허설 (PostgreSQL · 2 workers): 마이그레이션 0009 · 설정 코드(로그·CLI, DB 로그엔 없음) · 설정 토큰 격리 ·
# ① 관리자 MFA(실제 로그인 폼) · ⑤ 자가 시험 · 배포 프로파일 에어갭 적용 → 배너·라우팅 → 되돌리기.
# 리허설 관리자(nexus-admin)·rehearsal 계정은 실행 중 만든 임시 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화한다.
# 첫 실행 완료는 하지 않는다(재실행 가능). 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'

PW=$(openssl rand -hex 16)
$DC exec -T -e REH_PW="$PW" api python - <<'EOF' >/dev/null
import os
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
with session_scope() as db:
    u = db.scalars(select(User).where(User.username == "rehearsal")).first()
    if u is None:
        u = User(id="usr_rehearsal", sub="usr_rehearsal", issuer=security.issuer(), username="rehearsal", name="리허설",
                 email="rehearsal@agentnexus.local", roles=["admin"], team="team-platform", password_hash="")
        db.add(u)
    u.password_hash, u.status = security.hash_password(os.environ["REH_PW"]), "active"
EOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
authorize() {  # $1 user $2 password $3 otp → redirect Location
  curl -s -o /tmp/anx20-page.html -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d state=s20 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
    -d "username=$1" --data-urlencode "password=$2" -d "otp=$3"
}
LOC=$(authorize rehearsal "$PW" "")
CODE=$(printf '%s' "$LOC" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
H="Authorization: Bearer $TOKEN"

check P20-01-migration "$(curl -s -H "$H" "$BASE/api/v1/admin/updates" | jq -r .migration)" "0009"
check P20-02-state-public "$(curl -s "$BASE/api/v1/setup/state" | jq -r '.requires_code')" "true"
# 설정 코드 — 기동 로그(stdout)에만, DB 로그(K04 조회 대상)에는 남기지 않는다
check P20-03-code-in-stdout "$($DC logs api 2>/dev/null | grep -c '첫 실행 일회용 설정 코드' | awk '{print ($1>0)?"yes":"no"}')" "yes"
check P20-04-code-not-in-db-logs "$($DC exec -T api python -c "from app.db import session_scope; from app.models import LogEntry
with session_scope() as db: print(db.query(LogEntry).filter(LogEntry.message.like('%설정 코드%')).count())" 2>/dev/null | tail -1)" "0"
SCODE=$($DC exec -T api nexus install code --local 2>/dev/null | sed -n 's/.*설정 코드: \([A-Z0-9-]*\).*/\1/p')
STOK=$(curl -s -H "$J" -d "{\"code\":\"$SCODE\"}" "$BASE/api/v1/setup/claim" | jq -r .token)
S="Authorization: Bearer $STOK"
check P20-05-claim-cli-code "$(curl -s -H "$S" "$BASE/api/v1/setup/progress" | jq -r 'has("steps")')" "true"
check P20-06-setup-token-isolated "$(curl -s -o /dev/null -w '%{http_code}' -H "$S" "$BASE/api/v1/me")" "401"
check P20-07-code-one-time "$(curl -s -H "$J" -d "{\"code\":\"$SCODE\"}" "$BASE/api/v1/setup/claim" | jq -r .code)" "code_expired"
# ① 관리자 — 정책·MFA, 이후 실제 로그인 폼에서 MFA 강제
ENR=$(curl -s -X POST -H "$S" "$BASE/api/v1/setup/mfa?account=nexus-admin")
SECRET=$(echo "$ENR" | jq -r .secret)
APW="$(openssl rand -hex 10)Aa1!"
totp() { $DC exec -T -e SEC="$SECRET" api python -c "import os; from app.services import totp; print(totp.code_at(os.environ['SEC']))" 2>/dev/null | tail -1; }
ADMIN_BODY() {  # $1 비밀번호 — 셸 중첩 인용을 피해 변수로 만든다
  echo "{\"admin\":{\"username\":\"nexus-admin\",\"email\":\"a@agentnexus.local\",\"password\":\"$1\"},\"mfa\":{\"secret\":\"$SECRET\",\"code\":\"$(totp)\"}}"
}
B=$(ADMIN_BODY 'Password1234!')
R=$(curl -s -H "$S" -H "$J" -d "$B" "$BASE/api/v1/setup/identity" | jq -r .code)
check P20-08-weak-password "$R" "weak_password"
B=$(ADMIN_BODY "$APW")
R=$(curl -s -H "$S" -H "$J" -d "$B" "$BASE/api/v1/setup/identity" | jq -r .steps.identity.status)
check P20-09-identity-step "$R" "done"
unset B
authorize nexus-admin "$APW" "" >/dev/null
check P20-10-login-needs-mfa "$(grep -c 'MFA 코드가 필요하거나' /tmp/anx20-page.html)" "1"
OTP=$(totp)
R=$(authorize nexus-admin "$APW" "$OTP" | grep -c 'code=')
check P20-11-login-with-mfa "$R" "1"
unset APW
# ⑤ 자가 시험 — 2 workers·PostgreSQL 에서 60초 이내
check P20-12-self-test "$(curl -s -H "$S" -H "$J" -d '{"template":"tpl-qa"}' "$BASE/api/v1/setup/templates/test" | jq -r '.passed and .total_seconds <= 60')" "true"
# 배포 프로파일 — 에어갭 적용 → 배너·상용 모델 제외 → 되돌리기
check P20-13-plan "$(curl -s -H "$H" -H "$J" -d '{"profile":"gateway-only"}' "$BASE/api/v1/admin/profiles/plan" | jq -r '.stop | index("host") != null')" "true"
curl -s -o /dev/null -H "$H" -H "$J" -d '{"profile":"full","airgap":true,"confirm":true}' "$BASE/api/v1/admin/profiles/apply"
sleep 6  # 다른 워커의 에어갭 캐시(5초)
check P20-14-airgap-banner "$(curl -s -H "$H" "$BASE/api/v1/system/banners" | jq -r '[.items[].id] | index("airgap") != null')" "true"
OK=0; for i in 1 2 3 4; do M=$(curl -s -D- -o /dev/null -H "$H" -H "$J" -H 'X-AgentNexus-Data-Class: public' -d '{"model":"auto","messages":[{"role":"user","content":"hi"}]}' "$BASE/v1/chat/completions" | tr -d '\r' | awk -F': ' 'tolower($1)=="x-agentnexus-model"{print $2}'); [ "$M" = "domain-local" ] && OK=$((OK+1)); done
check P20-15-airgap-routing-all-workers "$OK" "4"
curl -s -o /dev/null -H "$H" -H "$J" -d '{"profile":"full","airgap":false,"confirm":true}' "$BASE/api/v1/admin/profiles/apply"
sleep 6
check P20-16-airgap-off "$(curl -s -H "$H" "$BASE/api/v1/system/banners" | jq -r '[.items[].id] | index("airgap") == null')" "true"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    for n in ('rehearsal', 'nexus-admin'):
        u = db.query(User).filter(User.username == n).first()
        if u: u.status = 'disabled'" >/dev/null
rm -f /tmp/anx20-page.html
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
