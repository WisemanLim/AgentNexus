#!/bin/sh
# impl 31st — Helm 실제 설치 검증 (D14-④, Q7): 전용 kind 클러스터(별도 kubeconfig) → 이미지 적재 → helm install --wait →
# 파드 Ready·PVC Bound → helm test → port-forward 스모크(/health · 로그인 · MCP 도구 호출 · 모델 호출 · 감사 체인) → uninstall → 클러스터 삭제.
# 기본 kubeconfig(~/.kube/config)와 다른 클러스터는 건드리지 않는다 — 모든 kubectl·helm·kind 에 --kubeconfig/--name 을 명시한다.
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 stdin 으로 넘긴다(출력·저장·인자 노출 없음). 클러스터와 함께 사라진다.
# 사용: KC=<scratch>/kubeconfig-e31 sh test/impl/31st/helm-kind.sh   (KEEP=1 이면 정리 생략)
# 사전: make prod-build (wise-agentnexus-api:prod) · kind · helm
set -u
KC=${KC:?전용 kubeconfig 경로 (kind create cluster --kubeconfig 로 만든 파일)}
CLUSTER=agentnexus-e31
NS=agentnexus
REL=agentnexus
PORT=18800
BASE="http://127.0.0.1:$PORT"
LOG=test/impl/31st/logs
mkdir -p $LOG
K="kubectl --kubeconfig $KC -n $NS"
HL="helm --kubeconfig $KC -n $NS"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'

kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --kubeconfig "$KC" --wait 180s > $LOG/kind-create.log 2>&1
check H31-01-cluster-context "$(kubectl --kubeconfig "$KC" config current-context)" "kind-$CLUSTER"
# 이미지 적재 — Docker Desktop containerd 저장소의 다중 플랫폼 인덱스는 `kind load` 의 --all-platforms 가져오기가 실패한다(로컬에 없는 플랫폼 계층)
# → 노드 플랫폼 하나만 save 해서 노드 containerd 로 직접 가져온다. 레지스트리에서 받지 않는다.
loaded=0
for img in wise-agentnexus-api:prod postgres:17-alpine valkey/valkey:8-alpine; do
  plat=$(docker image inspect "$img" --format '{{.Os}}/{{.Architecture}}')
  docker save --platform "$plat" "$img" | docker exec -i "$CLUSTER-control-plane" ctr --namespace=k8s.io images import --digests --snapshotter=overlayfs - \
    >> $LOG/kind-load.log 2>&1 && loaded=$((loaded+1))
done
check H31-02-images-loaded "$loaded" "3"

$HL install $REL deploy/helm/agentnexus --create-namespace \
  --set publicBaseUrl="$BASE" --set persistence.size=1Gi --set postgresql.storage.size=1Gi \
  --set api.resources.requests.cpu=100m --set api.resources.requests.memory=256Mi \
  --wait --timeout 10m > $LOG/helm-install.log 2>&1
check H31-03-helm-install "$?" "0"
$K get pods,svc,pvc,networkpolicy,configmap,secret -o wide > $LOG/resources.txt 2>&1
check H31-04-pods-ready "$($K get pods -o json | jq -r '[.items[] | select(.metadata.annotations["helm.sh/hook"] == null) | (.status.containerStatuses // []) | all(.ready)] | (length > 0 and all)')" "true"
check H31-05-pods-count "$($K get pods -o json | jq -r '[.items[] | select(.metadata.annotations["helm.sh/hook"] == null) | .metadata.labels["app.kubernetes.io/component"]] | sort | join(",")')" "a2a-demo,api,mcp-servers,postgresql,valkey"
check H31-06-pvc-bound "$($K get pvc -o json | jq -r '[.items[].status.phase] | (length > 0 and all(. == "Bound"))')" "true"
check H31-07-networkpolicy "$($K get networkpolicy -o json | jq -r '.items | length > 0')" "true"
$HL test $REL --logs > $LOG/helm-test.log 2>&1
check H31-08-helm-test "$?" "0"

$K port-forward svc/$REL-api $PORT:8000 > $LOG/port-forward.log 2>&1 &
PF=$!
for i in $(seq 1 30); do curl -sf "$BASE/health" >/dev/null && break; sleep 1; done
check H31-09-health "$(curl -s "$BASE/health" | jq -r '"\(.status):\(.db.status)"')" "ok:ok"

PW=$(openssl rand -hex 16)
printf '%s' "$PW" | $K exec -i deploy/$REL-api -c api -- python -c '
import sys
from sqlalchemy import select
from app.core import security
from app.db import session_scope
from app.models import User
pw = sys.stdin.read()
with session_scope() as db:
    u = db.scalars(select(User).where(User.username == "rehearsal")).first()
    if u is None:
        u = User(id="usr_rehearsal", sub="usr_rehearsal", issuer=security.issuer(), username="rehearsal", name="rehearsal",
                 email="rehearsal@agentnexus.local", roles=["admin"], team="team-platform", password_hash="")
        db.add(u)
    u.roles, u.password_hash, u.status = ["admin"], security.hash_password(pw), "active"
' > /dev/null 2>&1
VERIFIER=$(openssl rand -hex 32)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -binary -sha256 | openssl base64 | tr '+/' '-_' | tr -d '=')
REDIR="$BASE/login/callback"
CODE=$(curl -s -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=s31 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$(curl -s -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
A="Authorization: Bearer $TOKEN"
check H31-10-login "$(curl -s -H "$A" "$BASE/api/v1/me" | jq -r .sub)" "usr_rehearsal"
check H31-11-mcp-refresh "$(curl -s -X POST -H "$A" "$BASE/api/v1/mcp-servers/jira/refresh" | jq -r '"\(.health):\(.protocol)"')" "healthy:2026-07-28"
SID=$(curl -s -D - -o /dev/null -H "$A" -H "$J" -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' \
  -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"helm-smoke","version":"1"}}}' \
  "$BASE/mcp/jira" | tr -d '\r' | grep -i '^mcp-session-id:' | cut -d' ' -f2)
R=$(curl -s -H "$A" -H "$J" -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_issues","arguments":{"query":"결제"}}}' "$BASE/mcp/jira")
check H31-12-mcp-tool-call "$(printf '%s' "$R" | jq -r '(.result.isError // false | tostring) + ":" + ((.result.structuredContent.result // []) | length > 0 | tostring)')" "false:true"
check H31-13-model-call "$(curl -s -H "$A" -H "$J" -d '{"model":"auto","messages":[{"role":"user","content":"helm 설치 확인"}]}' "$BASE/v1/chat/completions" | jq -r '.choices[0].message.content | length > 0')" "true"
check H31-14-audit-chain "$(curl -s -H "$A" "$BASE/api/v1/audit/verify" | jq -r .valid)" "true"
$K logs deploy/$REL-api -c api --tail=2000 > $LOG/api.log 2>&1
check H31-15-no-traceback "$(grep -c 'Traceback' $LOG/api.log)" "0"
kill $PF 2>/dev/null; wait $PF 2>/dev/null

if [ "${KEEP:-0}" != "1" ]; then
  $HL uninstall $REL --wait --timeout 5m > $LOG/helm-uninstall.log 2>&1
  check H31-16-helm-uninstall "$?" "0"
  left() { $K get pods -o json | jq -r '[.items[] | select(.metadata.labels["app.kubernetes.io/instance"] == "'$REL'")] | length'; }
  for i in $(seq 1 60); do [ "$(left)" = "0" ] && break; sleep 2; done  # Terminating 파드가 사라질 때까지 (최대 120초)
  check H31-17-no-release-pods "$(left)" "0"
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KC" > $LOG/kind-delete.log 2>&1
  check H31-18-cluster-deleted "$(kind get clusters 2>/dev/null | grep -cx "$CLUSTER")" "0"
fi
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
