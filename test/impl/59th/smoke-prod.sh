#!/bin/sh
# impl 59th — 12차 prod 리허설 (PostgreSQL · 2 workers · Valkey): Epic 56~60 — 카드 URL(PUBLIC_BASE_URL) · 푸시 검증 발급자 필수 ·
# 스케줄러 리스(구성요소 게이트 뒤에도 기동) · 키 회전(서명: 직전 키 유예·두 워커 즉시 반영 / 암호화: 재암호화·이전 키 제거) ·
# 남은 항목(OTLP 상한·형식, OpenAPI namespace 충돌, /v0/servers SQL 페이지).
# 임시 계정 rehearsal(admin)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'
PW=$(openssl rand -hex 16)
$DC exec -T -e REH_PW="$PW" api python - <<'PYEOF' >/dev/null
import os
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
with session_scope() as db:
    u = db.scalars(select(User).where(User.username == "rehearsal")).first()
    if u is None:
        u = User(id="usr_rehearsal", sub="usr_rehearsal", issuer=security.issuer(), username="rehearsal", name="rehearsal",
                 email="rehearsal@agentnexus.local", roles=["admin"], team="team-platform", password_hash="")
        db.add(u)
    u.roles, u.password_hash, u.status, u.tokens_not_before = ["admin"], security.hash_password(os.environ["REH_PW"]), "active", None
PYEOF
login() {
  VERIFIER=$(openssl rand -hex 32)
  CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
  REDIR="$BASE/login/callback"
  CODE=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d state=s59 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
    -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
  curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token
}
kid_of() { printf '%s' "$1" | cut -d. -f1 | tr '_-' '/+' | awk '{n=length($0)%4; if(n==2)$0=$0"=="; else if(n==3)$0=$0"="; print}' | base64 -d 2>/dev/null | jq -r .kid; }
TOKEN=$(login)
H="Authorization: Bearer $TOKEN"
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# P59-01 카드 URL — Host 헤더가 아니라 PUBLIC_BASE_URL (플랫폼 카드·게이트웨이 프록시 카드)
C1=$(curl -s -H 'Host: evil.p59.example' "$BASE/.well-known/agent-card.json")
C2=$(curl -s -H 'Host: evil.p59.example' -H "$H" "$BASE/a2a/observability-analyst/.well-known/agent-card.json")
check P59-01-card-url "$(printf '%s%s' "$C1" "$C2" | grep -c evil.p59):$(printf '%s' "$C1" | grep -c '127.0.0.1:8800'):$(printf '%s' "$C2" | grep -c '127.0.0.1:8800')" "0:1:1"

# P59-02 푸시 검증 — JWKS 를 쓰면 발급자 필수(설정 거부, 저장 안 됨) · 모르는 푸시 토큰 404
PV=$(curl -s -X PATCH -H "$H" -H "$J" -d '{"auth":{"push_verify":{"jwks_url":"https://jwks.p59.example/jwks"}}}' \
  "$BASE/api/v1/a2a/agents/observability-analyst" | jq -r .code)
check P59-02-push-issuer-required "$PV:$(code -X POST -H "$J" -d '{"kind":"task","id":"x"}' "$BASE/a2a/push/p59-unknown-token")" \
  "push_verify_issuer_required:404"

# P59-03 스케줄러 — 모든 작업 리스가 최근 10분 안에 갱신 (구성요소 all)
LEASES=$($DC exec -T api python - <<'PYEOF'
from datetime import timedelta
from app.db import SessionLocal, utcnow
from app.models import Setting
with SessionLocal() as db:
    fresh = [k for k in ("alerts", "recovery", "backup", "approvals", "registry")
             if (s := db.get(Setting, f"lease.{k}")) is not None and s.updated_at >= utcnow() - timedelta(minutes=10)]
print(len(fresh))
PYEOF
)
check P59-03-scheduler-leases "$(echo "$LEASES" | tr -d '\r')" "5"

# P59-04 서명 키 회전 — 새 kid · 회전 전 토큰은 두 워커 모두에서 유예 동안 유효 · JWKS 는 새 kid 먼저 + 직전 kid · 새 로그인은 새 kid
K0=$(curl -s -H "$H" "$BASE/api/v1/admin/keys")
OLDKID=$(echo "$K0" | jq -r .signing.kid)
ROT=$(curl -s -H "$H" -H "$J" -d '{"kind":"signing","confirm":"ROTATE"}' "$BASE/api/v1/admin/keys/rotate")
NEWKID=$(echo "$ROT" | jq -r .kid)
OLDOK=0; JW=0; NEWOK=0; KIDOK=0
for i in $(seq 1 20); do
  [ "$(code -H "$H" "$BASE/api/v1/me")" = 200 ] && OLDOK=$((OLDOK+1))
  curl -s "$BASE/idp/jwks" | jq -e --arg n "$NEWKID" --arg o "$OLDKID" '[.keys[].kid] | .[0] == $n and index($o) != null' >/dev/null && JW=$((JW+1))
done
for i in 1 2 3 4; do
  T2=$(login)
  [ "$(kid_of "$T2")" = "$NEWKID" ] && KIDOK=$((KIDOK+1))
  for k in 1 2 3 4 5; do [ "$(code -H "Authorization: Bearer $T2" "$BASE/api/v1/me")" = 200 ] && NEWOK=$((NEWOK+1)); done
done
unset T2
check P59-04-signing-rotation "$(echo "$K0" | jq -c .env_managed):$( [ "$NEWKID" != "$OLDKID" ] && [ -n "$NEWKID" ] && echo new):$OLDOK:$JW:$KIDOK:$NEWOK" \
  '{"signing":false,"encryption":false}:new:20:20:4:20'

# P59-05 암호화 키 회전 — 저장 시크릿 재암호화(실패 0) · 두 워커 모두 키 2개 → drop 뒤 1개 · 다른 프로세스에서 값 그대로
VAL="p59-$(openssl rand -hex 8)"
getv() { $DC exec -T api python -c "from app.db import SessionLocal; from app.services import secrets
with SessionLocal() as db: print(secrets.get_plain(db, 'p59/value'))" | tr -d '\r'; }
$DC exec -T -e P59V="$VAL" api python -c "import os
from app.db import SessionLocal; from app.services import secrets
with SessionLocal() as db: secrets.put(db, 'p59/value', os.environ['P59V'], kind='token'); db.commit()" >/dev/null
ER=$(curl -s -H "$H" -H "$J" -d '{"kind":"encryption","confirm":"ROTATE"}' "$BASE/api/v1/admin/keys/rotate")
TWO=0; for i in $(seq 1 10); do [ "$(curl -s -H "$H" "$BASE/api/v1/admin/keys" | jq -r .encryption.keys)" = 2 ] && TWO=$((TWO+1)); done
V1=$(getv)
DR=$(curl -s -X POST -H "$H" "$BASE/api/v1/admin/keys/reencrypt?drop_previous=true")
ONE=0; for i in $(seq 1 10); do [ "$(curl -s -H "$H" "$BASE/api/v1/admin/keys" | jq -r .encryption.keys)" = 1 ] && ONE=$((ONE+1)); done
V2=$(getv)
$DC exec -T api python -c "from app.db import SessionLocal; from app.models import Secret
with SessionLocal() as db: db.query(Secret).filter(Secret.name == 'p59/value').delete(); db.commit()" >/dev/null
check P59-05-encryption-rotation "$(echo "$ER" | jq -r '"\(.failed):\(.secrets >= 1)"'):$TWO:$( [ "$V1" = "$VAL" ] && echo same):$(echo "$DR" | jq -r '"\(.failed):\(.dropped)"'):$ONE:$( [ "$V2" = "$VAL" ] && echo same)" \
  "0:true:10:same:0:1:10:same"

# P59-06 남은 항목 — OTLP 형식 오류 400 · 8 MiB 초과 413 · OpenAPI 생성이 쓰는 중인 namespace 면 409 · /v0/servers 페이지
OT=$($DC exec -T api python -c "from app.core import tracing; print(tracing.ingest_token())" | tr -d '\r')
BADJ=$(code -X POST -H "Authorization: Bearer $OT" -H "$J" --data-binary '{not json' "$BASE/v1/traces")
BIG=$(head -c 9437184 /dev/zero | tr '\0' 'a' | curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $OT" -H "$J" --data-binary @- "$BASE/v1/traces")
unset OT
OAS='{"namespace":"jira","title":"p59","base_url":"https://api.p59.example","secret":"p59-not-a-real-secret","openapi":{"openapi":"3.0.0","info":{"title":"p59","version":"1"},"paths":{"/x":{"get":{"operationId":"x","responses":{"200":{"description":"ok"}}}}}}}'
NS=$(curl -s -H "$H" -H "$J" -d "$OAS" "$BASE/api/v1/mcp-servers/openapi" | jq -r .code)
V0=$(curl -s "$BASE/v0/servers?limit=2" | jq -r '"\(.servers | length <= 2):\(.metadata | has("nextCursor"))"')
check P59-06-remaining-items "$BADJ:$BIG:$NS:$V0" "400:413:namespace_taken:true:true"

# P59-07 예산 예약 고정 만료(Valkey Lua) — 읽기(활동)가 이어져도 만료 시각에 빠짐 · 반납 · 인증 없는 푸시 수신 상한 413
AMT=$($DC exec -T api python - <<'PYEOF'
import time
from app.services import ratelimit
b, k = ratelimit.backend(), "p59:amount"
a = b.amount_hold(k, "m1", 5, 2)
c = b.amount_hold(k, "", 0, 2)
time.sleep(2.5)
d = b.amount_hold(k, "", 0, 2)
b.amount_hold(k, "m2", 7, 60)
b.amount_release(k, "m2")
print(f"{type(b).__name__}:{a}:{c}:{d}:{b.amount_hold(k, '', 0, 60)}")
PYEOF
)
PUSH=$(head -c 9437184 /dev/zero | tr '\0' 'a' | curl -s -o /dev/null -w '%{http_code}' -X POST -H "$J" --data-binary @- "$BASE/a2a/push/p59-unknown-token")
check P59-07-amount-hold-and-push-cap "$(echo "$AMT" | tr -d '\r'):$PUSH" "RedisBackend:5:5:0:0:413"

unset TOKEN PW
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
