#!/bin/sh
# impl 37th — prod 리허설 (PostgreSQL · 2 workers): A2A gRPC 바인딩 — a2a-grpc 서비스 헬스 · SendMessage·스트림·GetTask ·
# NOT_FOUND 트레일러 · 익명 거부 · platform tenant · 카드 GRPC 인터페이스 · 감사 체인 (클라이언트는 api 컨테이너 안 grpcio).
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 쓰고(출력·저장 안 함) 끝나면 비활성화.
# 사전: make prod-build && make db-migrate ENV=prod
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/37th/logs
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
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
CODE=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=s37 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
A="Authorization: Bearer $(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)"
unset PW

AG=observability-analyst
check P37-01-service-healthy "$($DC ps a2a-grpc --format '{{.Health}}')" "healthy"
TOK=$(printf '%s' "$A" | sed 's/^Authorization: Bearer //')
$DC exec -T -e TOK="$TOK" api python - > $LOG/grpc-client.txt 2>&1 <<'PYEOF'
import asyncio, os, uuid, grpc
from a2a.types import a2a_pb2 as pb, a2a_pb2_grpc as pbg
from google.rpc import status_pb2, error_details_pb2
def req(text, tenant="observability-analyst"):
    return pb.SendMessageRequest(tenant=tenant, message=pb.Message(message_id=uuid.uuid4().hex, role=pb.ROLE_USER, parts=[pb.Part(text=text)]))
async def main():
    md = [("authorization", f"Bearer {os.environ['TOK']}")]
    async with grpc.aio.insecure_channel("a2a-grpc:50051") as ch:
        s = pbg.A2AServiceStub(ch)
        r = await s.SendMessage(req("orders-api 장애 분석"), metadata=md)
        print("send", pb.TaskState.Name(r.task.status.state))
        g = await s.GetTask(pb.GetTaskRequest(tenant="observability-analyst", id=r.task.id), metadata=md)
        print("get", g.id == r.task.id)
        evs = [e async for e in s.SendStreamingMessage(req("orders-api 장애 분석"), metadata=md)]
        last = evs[-1]
        print("stream", pb.TaskState.Name(last.status_update.status.state if last.HasField("status_update") else last.task.status.state))
        try:
            await s.GetTask(pb.GetTaskRequest(tenant="observability-analyst", id="no-such"), metadata=md)
        except grpc.aio.AioRpcError as e:
            st = status_pb2.Status.FromString(dict(e.trailing_metadata())["grpc-status-details-bin"])
            info = error_details_pb2.ErrorInfo(); st.details[0].Unpack(info)
            print("missing", e.code().name, info.reason)
        try:
            await s.SendMessage(req("x"))
        except grpc.aio.AioRpcError as e:
            print("anon", e.code().name)
        p = await s.SendMessage(req("휴가 정책", tenant="platform/tpl-qa"), metadata=md)
        print("platform", pb.TaskState.Name(p.task.status.state))
asyncio.run(main())
PYEOF
v() { sed -n "s/^$1 //p" $LOG/grpc-client.txt; }
check P37-02-grpc-send "$(v send)" "TASK_STATE_COMPLETED"
check P37-03-grpc-get "$(v get)" "True"
check P37-04-grpc-stream "$(v stream)" "TASK_STATE_COMPLETED"
check P37-05-grpc-not-found "$(v missing)" "NOT_FOUND TASK_NOT_FOUND"
check P37-06-grpc-anon "$(v anon)" "UNAUTHENTICATED"
check P37-07-grpc-platform "$(v platform)" "TASK_STATE_COMPLETED"
check P37-08-card-grpc "$(curl -s "$BASE/a2a/observability-analyst/.well-known/agent-card.json" | jq -c '[.supportedInterfaces[] | select(.protocolBinding=="GRPC")][0] | [.url, .tenant]')" '["127.0.0.1:8851","observability-analyst"]'
check P37-09-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db:
    u = db.query(User).filter(User.username == 'rehearsal').first()
    if u: u.status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
