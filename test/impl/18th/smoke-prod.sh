#!/bin/sh
# impl 18th — prod 리허설 (PostgreSQL 17 · uvicorn 2 workers): 마이그레이션 0007 · RAG · 첨부(읽기 전용 이미지) ·
# A2A input-required 재개 · 플랫폼 Task 워커 간 조회 · 예약 작업 리스 · 끊긴 실행 복구 · 동시 1,000 세션(k6).
# 사전: make prod-build && make db-migrate ENV=prod. 실행 위치: 프로젝트 루트. 사용: sh test/impl/18th/smoke-prod.sh
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
LOG=test/impl/18th/logs
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'

# 0) 리허설 전용 계정 rehearsal(admin) — 비밀번호는 실행 중 메모리에만, 종료 시 비활성화 (기존 admin·.env.prod 불변)
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
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=s18 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW")
CODE=$(printf '%s' "$LOC" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
H="Authorization: Bearer $TOKEN"

# 1) 기동·마이그레이션
check P18-01-health "$(curl -s "$BASE/health" | jq -r '.status + ":" + .db.engine')" "ok:postgres"
check P18-02-migration "$(curl -s -H "$H" "$BASE/api/v1/admin/updates" | jq -r .migration)" "0007"
# 2) RAG — 임베딩·지식 베이스 검색 (PostgreSQL JSON 벡터)
check P18-03-embeddings "$(curl -s -H "$H" -H "$J" -d '{"input":"주문 지연"}' "$BASE/v1/embeddings" | jq -r '.model + ":" + (.data[0].embedding | length | tostring)')" "embed-local:256"
check P18-04-kb-search "$(curl -s -H "$H" -H "$J" -d '{"query":"orders-api p95 지연 경보 Dynatrace 문제","top_k":3}' "$BASE/api/v1/knowledge-bases/ops-runbooks/search" | jq -r '.items[0].title')" "주문 API 지연 대응 런북"
# 3) 첨부 — 읽기 전용 이미지에서도 data 볼륨에 저장
printf 'release window: Friday 18:00\n' > /tmp/anx18-note.txt
check P18-05-attachment "$(curl -s -o /dev/null -w '%{http_code}' -H "$H" -F file=@/tmp/anx18-note.txt "$BASE/api/v1/chat/attachments")" "201"
rm -f /tmp/anx18-note.txt
# 4) A2A input-required → 답변 → 같은 Task 로 재개 (요청·답변이 서로 다른 워커로 가도 DB 로 이어짐)
SID=$(curl -s -X POST -H "$H" -H "$J" -d '{"profile_id":"tpl-incident"}' "$BASE/api/v1/sessions" | jq -r .id)
RID=$(curl -s -X POST -H "$H" -H "$J" -d '{"content":"장애 원인 분석해줘"}' "$BASE/api/v1/sessions/$SID/messages" | jq -r .run_id)
for i in $(seq 1 120); do ST=$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r .status); case "$ST" in waiting_input|succeeded|partial|failed) break;; esac; sleep 0.5; done
check P18-06-waiting-input "$ST" "waiting_input"
curl -s -o /dev/null -X POST -H "$H" -H "$J" -d '{"step":"s2","text":"orders-api"}' "$BASE/api/v1/runs/$RID/input"
for i in $(seq 1 120); do ST=$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r .status); case "$ST" in succeeded|partial|failed) break;; esac; sleep 0.5; done
check P18-07-resumed-same-task "$ST:$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r '.steps[] | select(.name=="s2") | .output.turns')" "succeeded:2"
# 5) 플랫폼 A2A Task — 받은 워커가 아닌 워커에서도 tasks/get (영속 Task, NFR-S1)
MSG='{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"kind":"message","messageId":"m18","role":"user","parts":[{"kind":"text","text":"휴가 정책"}]}}}'
TASK=$(curl -s -H "$H" -H "$J" -d "$MSG" "$BASE/a2a/platform/tpl-qa" | jq -r .result.id)
OK=0; for i in $(seq 1 12); do S=$(curl -s -H "$H" -H "$J" -d "{\"jsonrpc\":\"2.0\",\"id\":$i,\"method\":\"tasks/get\",\"params\":{\"id\":\"$TASK\"}}" "$BASE/a2a/platform/tpl-qa" | jq -r .result.status.state); [ "$S" = "completed" ] && OK=$((OK+1)); done
check P18-08-task-get-any-worker "$OK" "12"
# 6) 끊긴 실행 — running + 오래된 생존 신호를 만들고 예약 작업(다른 워커일 수 있음)이 이어 돌리는지
RID2=$(curl -s -X POST -H "$H" -H "$J" -d '{"content":"런북 알려줘"}' "$BASE/api/v1/sessions/$(curl -s -X POST -H "$H" -H "$J" -d '{"profile_id":"tpl-qa"}' "$BASE/api/v1/sessions" | jq -r .id)/messages" | jq -r .run_id)
for i in $(seq 1 120); do ST=$(curl -s -H "$H" "$BASE/api/v1/runs/$RID2" | jq -r .status); case "$ST" in succeeded|partial|failed) break;; esac; sleep 0.5; done
$DC exec -T -e RID="$RID2" api python - <<'EOF' >/dev/null
import os
from datetime import timedelta
from sqlalchemy import select
from app.db import session_scope, utcnow
from app.models import Run, RunStep
with session_scope() as db:
    r = db.get(Run, os.environ["RID"])
    r.status, r.result, r.heartbeat_at = "running", None, utcnow() - timedelta(seconds=600)
    st = db.scalars(select(RunStep).where(RunStep.run_id == r.id).order_by(RunStep.seq.desc())).first()
    st.status, st.output = "running", None
EOF
# 7) 동시 1,000 세션 (k6) — 주체별 한도 안에 들도록 Access Token 50개를 나눠 쓴다 (종료 후 폐기)
IDS=""; TOKS=""
for i in $(seq 1 50); do
  R=$(curl -s -H "$H" -H "$J" -d "{\"name\":\"k6-session-$i\",\"subject\":\"svc:load-$i\",\"scopes\":[\"gateway:invoke\"],\"ttl_seconds\":1800}" "$BASE/api/v1/access-tokens")
  IDS="$IDS $(echo "$R" | jq -r .id)"; TOKS="$TOKS,$(echo "$R" | jq -r .token)"
done
curl -s -o /dev/null -X POST -H "$H" "$BASE/api/v1/mcp-servers/jira/refresh"
docker run --rm --network wise-agentnexus-prod_edge -e BASE=http://api:8000 -e TOKENS="${TOKS#,}" -e SESSIONS=1000 -e RAMP=30s -e HOLD=60s \
  -v "$PWD/deploy/k6:/k6:ro" -v "$PWD/$LOG:/out" grafana/k6:1.0.0 run --quiet --summary-export /out/k6-sessions.json /k6/sessions.js \
  > $LOG/k6-sessions.log 2>&1
check P18-09-k6-1000-sessions-thresholds "$?" "0"
check P18-10-sessions-opened "$(jq -r '.metrics.anx_sessions_opened.count' $LOG/k6-sessions.json)" "1000"
for id in $IDS; do curl -s -o /dev/null -X POST -H "$H" "$BASE/api/v1/access-tokens/$id/revoke"; done
# 6) 판정 — k6 동안 예약 작업(60s 주기)이 끊긴 실행을 복구했어야 한다
for i in $(seq 1 90); do ST=$(curl -s -H "$H" "$BASE/api/v1/runs/$RID2" | jq -r .status); [ "$ST" = "succeeded" ] && break; sleep 1; done
check P18-11-stale-run-recovered "$ST" "succeeded"
# 8) 예약 작업 리스 — 워커 2개가 같은 루프를 돌아도 작업별 보유자는 1개
LEASES=$($DC exec -T api python -c "from app.db import session_scope; from app.models import Setting
with session_scope() as db:
    rows = [db.get(Setting, k) for k in ('lease.alerts', 'lease.recovery')]
    print(','.join(sorted({r.updated_by.split(':')[0] for r in rows if r})), len([r for r in rows if r]))" 2>/dev/null | tail -1)
check P18-12-scheduler-lease "$(echo "$LEASES" | awk '{print $2}')" "2"
# 9) CLI — nexus kb (컨테이너 안 CLI, RAG 검색)
check P18-13-cli-kb-search "$($DC exec -T -e NEXUS_URL=http://127.0.0.1:8000 -e NEXUS_TOKEN="$TOKEN" api nexus kb search ops-runbooks '결제 인증서 갱신' --top-k 1 2>/dev/null | awk '{print $2}' | head -1)" "결제"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db: db.get(User, 'usr_rehearsal').status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
