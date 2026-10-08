#!/bin/sh
# impl 44th — prod 리허설 (PostgreSQL · 2 workers): A2A Task 소유권 — 사용자 A 의 Task 를 사용자 B 가 보거나 바꾸지 못한다.
# 임시 계정 rehearsal-a·rehearsal-b(사용자 역할)는 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/44th/logs
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'
APW=$(openssl rand -hex 16); BPW=$(openssl rand -hex 16)
$DC exec -T -e A_PW="$APW" -e B_PW="$BPW" api python - <<'PYEOF' >/dev/null
import os
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
with session_scope() as db:
    for name, env in (("rehearsal-a", "A_PW"), ("rehearsal-b", "B_PW")):
        u = db.scalars(select(User).where(User.username == name)).first()
        if u is None:
            uid = "usr_" + name.replace("-", "_")
            u = User(id=uid, sub=uid, issuer=security.issuer(), username=name, name=name,
                     email=f"{name}@agentnexus.local", roles=["user"], team="team-platform", password_hash="")
            db.add(u)
        u.roles, u.password_hash, u.status, u.tokens_not_before = ["user"], security.hash_password(os.environ[env]), "active", None
PYEOF
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
login() {
  code=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d state=s44 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
    -d "username=$1" --data-urlencode "password=$2" | sed -n 's/.*code=\([^&]*\).*/\1/p')
  curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$code" -d client_id=agentnexus-console \
    --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token
}
TA=$(login rehearsal-a "$APW"); TB=$(login rehearsal-b "$BPW"); unset APW BPW
A="Authorization: Bearer $TA"; B="Authorization: Bearer $TB"
PLAT="$BASE/a2a/platform/tpl-qa"
rpc() { printf '{"jsonrpc":"2.0","id":1,"method":"%s","params":%s}' "$1" "$2"; }
SENDP='{"message":{"kind":"message","messageId":"p44a","role":"user","parts":[{"kind":"text","text":"휴가 정책"}]}}'
TID=$(curl -s -H "$A" -H "$J" -d "$(rpc message/send "$SENDP")" "$PLAT" | jq -r .result.id)
GETP=$(printf '{"id":"%s"}' "$TID")
check P44-01-platform-list-scoped "$(curl -s -H "$B" -H "$J" -d "$(rpc tasks/list '{}')" "$PLAT" | jq -r --arg t "$TID" '[.result.tasks[].id] | index($t) == null'):$(curl -s -H "$A" -H "$J" -d "$(rpc tasks/list '{}')" "$PLAT" | jq -r --arg t "$TID" '[.result.tasks[].id] | index($t) != null')" "true:true"
G1=$(curl -s -H "$B" -H "$J" -d "$(rpc tasks/get "$GETP")" "$PLAT" | jq -r .error.code)
G2=$(curl -s -H "$B" -H "$J" -d "$(rpc tasks/get "$GETP")" "$PLAT" | jq -r .error.code)  # 2 워커 — 다른 워커는 영속 행으로 판정
check P44-02-platform-get-hidden "$G1:$G2" "-32001:-32001"
check P44-03-platform-rest-404 "$(curl -s -o /dev/null -w '%{http_code}' -H "$B" "$PLAT/rest/tasks/$TID")" "404"
CANP=$(printf '{"id":"%s"}' "$TID")
check P44-04-platform-cancel-hidden "$(curl -s -H "$B" -H "$J" -d "$(rpc tasks/cancel "$CANP")" "$PLAT" | jq -r .error.code)" "-32001"
$DC exec -T -e TOK="$TB" -e TID="$TID" api python - > $LOG/grpc-other.txt 2>&1 <<'PYEOF'
import asyncio, os
import grpc
from a2a.types import a2a_pb2 as pb
from a2a.types import a2a_pb2_grpc as pbg
async def main():
    async with grpc.aio.insecure_channel("a2a-grpc:50051") as ch:
        try:
            await pbg.A2AServiceStub(ch).GetTask(pb.GetTaskRequest(tenant="platform/tpl-qa", id=os.environ["TID"]),
                                                 metadata=[("authorization", "Bearer " + os.environ["TOK"])])
            print("grpc OK")
        except grpc.aio.AioRpcError as exc:
            print("grpc", exc.code().name)
asyncio.run(main())
PYEOF
check P44-05-grpc-hidden "$(sed -n 's/^grpc //p' $LOG/grpc-other.txt)" "NOT_FOUND"
GW="$BASE/a2a/observability-analyst"
GSEND='{"message":{"kind":"message","messageId":"p44g","role":"user","parts":[{"kind":"text","text":"orders-api 장애 분석"}]}}'
GTID=$(curl -s -H "$A" -H "$J" -d "$(rpc message/send "$GSEND")" "$GW" | jq -r .result.id)
GGET=$(printf '{"id":"%s"}' "$GTID")
check P44-06-gateway-get-hidden "$(curl -s -H "$B" -H "$J" -d "$(rpc tasks/get "$GGET")" "$GW" | jq -r .error.code):$(curl -s -H "$B" -H "$J" -d "$(rpc tasks/list '{}')" "$GW" | jq -r --arg t "$GTID" '[.result.tasks[]?.id] | index($t) == null')" "-32001:true"
check P44-07-owner-still-works "$(curl -s -H "$A" -H "$J" -d "$(rpc tasks/get "$GETP")" "$PLAT" | jq -r .result.id | grep -c "$TID"):$(curl -s -H "$A" -H "$J" -d "$(rpc tasks/get "$GGET")" "$GW" | jq -r .result.id | grep -c "$GTID")" "1:1"
unset TA TB
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    for u in db.query(User).filter(User.username.in_(['rehearsal-a', 'rehearsal-b'])): u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
