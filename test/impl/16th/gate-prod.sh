#!/bin/sh
# impl 16th — 2차 완료 게이트 prod 리허설 (13th 스크립트 기반 + MCP 동의·중계) + 배포 산출물: runtime 이미지(콘솔 번들 포함)·PKCE·핵심 API·교차 컨테이너 trace
# · k6 지연(NFR-P1)·MCP 순수 추가 지연·에어갭 번들(차트 동봉)·읽기 전용 루트 FS.
# 사전: make prod-build && make db-migrate ENV=prod. 실행 위치: 프로젝트 루트. 사용: sh test/impl/16th/gate-prod.sh
set -u
BASE="http://127.0.0.1:8800"
EXTRA=""; for f in compose/*.yml; do EXTRA="$EXTRA -f $f"; done
DC="docker compose -p wise-agentnexus-prod -f docker-compose.yml -f docker-compose.prod.yml $EXTRA --env-file .env.prod"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }

# 0) 리허설 전용 계정 rehearsal(admin 역할) — 비밀번호는 실행 중에만 메모리에 두고 출력·저장하지 않으며 종료 시 비활성화.
#    기존 admin 계정은 건드리지 않는다 (.env.prod 는 CHANGE_ME 유지)
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s13 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW")
CODE=$(printf '%s' "$LOC" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
H="Authorization: Bearer $TOKEN"

# 1) 기동·인증·콘솔 번들
check P13-01-health "$(curl -s "$BASE/health" | jq -r '.status + ":" + .db.engine')" "ok:postgres"
check P13-02-password-grant-off "$(curl -s -X POST "$BASE/idp/token" -d grant_type=password -d username=admin -d password=x | jq -r .error)" "unsupported_grant_type"
check P13-03-pkce-admin "$(curl -s -H "$H" "$BASE/api/v1/me" | jq -r '.roles[0]')" "admin"
ASSET=$(curl -s "$BASE/console/agents" | grep -o '/_app/index-[^"]*\.js' | head -1)
check P13-04-console-bundle "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/console/agents"):$(curl -s -o /dev/null -w '%{http_code}' "$BASE$ASSET")" "200:200"
# 2) 핵심 흐름 — 템플릿 실행(MCP·원격 A2A·요약) · 교차 컨테이너 trace · 감사 체인
SID=$(curl -s -X POST -H "$H" -H 'Content-Type: application/json' -d '{"profile_id":"tpl-incident"}' "$BASE/api/v1/sessions" | jq -r .id)
RID=$(curl -s -X POST -H "$H" -H 'Content-Type: application/json' -d '{"content":"orders-api 장애 원인 분석"}' "$BASE/api/v1/sessions/$SID/messages" | jq -r .run_id)
for i in $(seq 1 120); do ST=$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r .status); case "$ST" in succeeded|partial|failed) break;; esac; sleep 0.5; done
check P13-05-run "$ST" "succeeded"
TID=$(curl -s -H "$H" "$BASE/api/v1/runs/$RID" | jq -r .trace_id)
for i in $(seq 1 20); do SVCS=$(curl -s -H "$H" "$BASE/api/v1/traces/$TID" | jq -r '.services | join(",")'); case "$SVCS" in agentnexus,*a2a-demo*mcp-servers*) break;; esac; sleep 1; done
check P13-06-cross-container-trace "$SVCS" "agentnexus,agentnexus-a2a-demo,agentnexus-mcp-servers"
check P13-07-audit-chain "$(curl -s -H "$H" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
# 3) NFR-P1 — k6 (compose 네트워크 안, 호스트 설치 없음)
docker run --rm --network wise-agentnexus-prod_edge -e BASE=http://api:8000 -e TOKEN="$TOKEN" -e DURATION=20s \
  -v "$PWD/deploy/k6:/k6:ro" grafana/k6:1.0.0 run --quiet --summary-export /tmp/k6.json /k6/gateway.js > test/impl/16th/logs/k6.log 2>&1
check P13-08-k6-thresholds "$?" "0"
# MCP 순수 추가 지연 p95 = 게이트웨이 서버 스팬 − 업스트림 MCP 서버 스팬 (최근 k6 호출)
OVH=$($DC exec -T api python - <<'EOF'
import statistics
from datetime import timedelta
from app.db import SessionLocal, utcnow
from app.models import Span
with SessionLocal() as db:
    roots = db.query(Span).filter(Span.name == "POST /mcp/{server_id}", Span.parent_id.is_(None),
                                  Span.start_time >= utcnow() - timedelta(minutes=3)).all()
    out = []
    for r in roots:
        spans = db.query(Span).filter(Span.trace_id == r.trace_id).all()
        calls = {s.id for s in spans if s.kind == "client" and s.name.startswith("mcp ")}
        up = sum(float(s.duration_ms) for s in spans if s.kind == "server" and s.parent_id in calls)
        if up:
            out.append(float(r.duration_ms) - up)
    p95 = statistics.quantiles(out, n=100)[94] if len(out) > 20 else 999
    print(f"{p95:.1f} {len(out)}")
EOF
)
echo "MCP 추가 지연 p95(ms) 표본: $OVH" >> test/impl/16th/logs/k6.log
check P13-09-mcp-overhead-le-20ms "$(echo "$OVH" | awk '{print ($1 <= 20 && $2 > 20) ? "yes" : "no"}')" "yes"
# 4) 에어갭 번들 — Helm 차트 동봉 + 구성 백업 · 체크섬 검증 (이미지 docker save 는 호스트 CLI 에서)
helm package deploy/helm/agentnexus -d dist >/dev/null
CID=$($DC ps -q api)
docker cp dist/agentnexus-0.1.0.tgz "$CID":/tmp/agentnexus-0.1.0.tgz
NX="$DC exec -T -e NEXUS_URL=http://127.0.0.1:8000 -e NEXUS_TOKEN=$TOKEN -w /tmp api nexus"
$NX bundle export --out /tmp/anx-airgap --no-images --include /tmp/agentnexus-0.1.0.tgz >/dev/null
check P13-10-bundle "$($NX bundle import /tmp/anx-airgap --dry-run | head -2 | tr '\n' '|')" "체크섬 확인: 2 files|동봉 파일: agentnexus-0.1.0.tgz — 예: helm install agentnexus /tmp/anx-airgap/<chart>.tgz|"
$DC exec -T -u 0 api sh -c 'rm -rf /tmp/anx-airgap /tmp/agentnexus-0.1.0.tgz'
# 5) 읽기 전용 루트 FS (Helm securityContext.readOnlyRootFilesystem 과 같은 조건) — /tmp·/app/data 만 쓰기
RO=$(docker run --rm --read-only --tmpfs /tmp --tmpfs /app/data:uid=10001 -e APP_ENV=prod -e DB_ENGINE=sqlite \
  -e DATABASE_URL=sqlite:////app/data/ro.db --entrypoint sh wise-agentnexus-api:prod -c \
  'sh /app/scripts/entrypoint.sh true >/dev/null 2>&1 && (uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 2 >/tmp/u.log 2>&1 &) && sleep 8 && python -c "import urllib.request as u; print(u.urlopen(\"http://127.0.0.1:8001/health\").status, u.urlopen(\"http://127.0.0.1:8001/console\").status)"' 2>&1 | tail -1)
check P13-11-readonly-rootfs "$RO" "200 200"
# 6) MCP 서버→클라이언트 요청 — prod(2 workers·PostgreSQL)에서 동의가 다른 워커로 와도 DB 로 만나는지 + 공식 클라이언트 중계
MCPH="-H Accept:application/json,text/event-stream -H MCP-Protocol-Version:2025-11-25 -H Content-Type:application/json"
curl -s -X POST -H "$H" "$BASE/api/v1/mcp-servers/internal-orders/refresh" >/dev/null
OUT=$(mktemp)
(curl -s -H "$H" $MCPH -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"classify_orders","arguments":{}}}' "$BASE/mcp/internal-orders" > "$OUT") &
for i in $(seq 1 60); do AP=$(curl -s -H "$H" "$BASE/api/v1/approvals?status=pending" | jq -r '[.items[] | select(.kind=="sampling")][0].id // empty'); [ -n "$AP" ] && break; sleep 0.3; done
for k in 1 2 3; do curl -s -o /dev/null -H "$H" "$BASE/api/v1/approvals/$AP"; done  # 다른 워커에도 요청 분산
curl -s -o /dev/null -X POST -H "$H" -H 'Content-Type: application/json' -d '{"decision":"allow"}' "$BASE/api/v1/approvals/$AP/consent"; wait
check P16-12-consent-across-workers "$(jq -r '.result.structuredContent.model // .error.message' "$OUT")" "domain-local"
$DC cp test/impl/14th/relay_client.py api:/tmp/relay_client.py >/dev/null 2>&1
check P16-13-relay-official-client "$($DC exec -T api python /tmp/relay_client.py "$TOKEN" 2>/dev/null | tail -1 | jq -r '.classify.model')" "client-side-model"
rm -f "$OUT"
$DC exec -T api python -c "from app.db import session_scope; from app.models import User
with session_scope() as db: db.get(User, 'usr_rehearsal').status = 'disabled'" >/dev/null
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
