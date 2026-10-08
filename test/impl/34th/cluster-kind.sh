#!/bin/sh
# impl 34th — 운영형 클러스터 검증 (D15-③): 전용 kind 다중 노드(control-plane 1 + worker 2, 별도 kubeconfig) + Calico(NetworkPolicy 집행)
# + ingress-nginx + metrics-server → helm install(ingress·HPA·PDB·NetworkPolicy ingressFrom 제한) →
# NetworkPolicy 실제 차단 · ingress 경유 로그인·도구 호출 · PDB(두 번째 퇴거 거부) · HPA 확장 → uninstall → 클러스터 삭제.
# 기본 kubeconfig(~/.kube/config)·다른 클러스터는 건드리지 않는다 — 모든 kubectl·helm·kind 에 --kubeconfig/--name 을 명시.
# 임시 계정(rehearsal 관리자)은 실행 중 만든 비밀번호만 stdin 으로 넘긴다. 시크릿은 차트가 클러스터 안에서 생성(출력 없음) — 클러스터와 함께 삭제.
# 사용: KC=<scratch>/kubeconfig-e34 sh test/impl/34th/cluster-kind.sh   (KEEP=1 이면 정리 생략)
# 사전: make prod-build (wise-agentnexus-api:prod) · kind · helm. 네트워크: kind 노드·Calico·ingress-nginx·metrics-server 매니페스트·이미지
set -u
KC=${KC:?전용 kubeconfig 경로}
CLUSTER=agentnexus-e34
NS=agentnexus
REL=agentnexus
HOST=agentnexus.local
HOSTPORT=18880
BASE="http://$HOST:$HOSTPORT"
LOG=test/impl/34th/logs
mkdir -p $LOG/manifests
K="kubectl --kubeconfig $KC"
KN="$K -n $NS"
HL="helm --kubeconfig $KC -n $NS"
C="curl -s --resolve $HOST:$HOSTPORT:127.0.0.1"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'
CALICO=https://raw.githubusercontent.com/projectcalico/calico/v3.30.3/manifests/calico.yaml
INGRESS=https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
METRICS=https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# ── 클러스터 ──
cat > $LOG/kind-config.yaml <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  disableDefaultCNI: true        # Calico 가 NetworkPolicy 를 실제로 집행
  podSubnet: 10.244.0.0/16        # kind 도커 네트워크(192.168.x)와 겹치지 않게 — Calico 기본 풀 192.168.0.0/16 이면 노드 IP 경로가 꼬인다
nodes:
  - role: control-plane
    kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
    extraPortMappings:
      - { containerPort: 80, hostPort: $HOSTPORT, listenAddress: "127.0.0.1", protocol: TCP }
  - role: worker
  - role: worker
YAML
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --kubeconfig "$KC" --config $LOG/kind-config.yaml > $LOG/kind-create.log 2>&1
check H34-01-context "$($K config current-context)" "kind-$CLUSTER"
curl -sL --max-time 120 -o $LOG/manifests/calico.yaml $CALICO
curl -sL --max-time 60 -o $LOG/manifests/ingress-nginx.yaml $INGRESS
curl -sL --max-time 60 -o $LOG/manifests/metrics-server.yaml $METRICS
# IP 풀을 podSubnet 과 같게 (매니페스트의 주석 처리된 CALICO_IPV4POOL_CIDR 를 켠다)
sed -i.bak -e 's|# - name: CALICO_IPV4POOL_CIDR|- name: CALICO_IPV4POOL_CIDR|' -e 's|#   value: "192.168.0.0/16"|  value: "10.244.0.0/16"|' $LOG/manifests/calico.yaml
check H34-00-calico-pool "$(grep -A1 'name: CALICO_IPV4POOL_CIDR' $LOG/manifests/calico.yaml | grep -c '10.244.0.0/16')" "1"
$K apply -f $LOG/manifests/calico.yaml > $LOG/calico-apply.log 2>&1
$K -n kube-system rollout status ds/calico-node --timeout=600s >> $LOG/calico-apply.log 2>&1
$K wait --for=condition=Ready nodes --all --timeout=300s > /dev/null 2>&1
check H34-02-nodes-ready "$($K get nodes -o json | jq -r '[.items[] | select(.status.conditions[] | select(.type=="Ready").status=="True")] | length')" "3"
check H34-03-calico "$($K -n kube-system get ds calico-node -o json | jq -r '.status.numberReady == .status.desiredNumberScheduled and .status.numberReady == 3')" "true"

# 앱 이미지 — 노드마다 단일 플랫폼으로 가져오기 (kind load 는 다중 플랫폼 인덱스에서 실패, 31st)
loaded=0
for img in wise-agentnexus-api:prod postgres:17-alpine valkey/valkey:8-alpine; do
  plat=$(docker image inspect "$img" --format '{{.Os}}/{{.Architecture}}')
  f=$LOG/manifests/$(printf '%s' "$img" | tr '/:' '__').tar
  docker save --platform "$plat" "$img" -o "$f"
  for node in $(kind get nodes --name "$CLUSTER"); do
    docker exec -i "$node" ctr --namespace=k8s.io images import --digests --snapshotter=overlayfs - < "$f" >> $LOG/kind-load.log 2>&1 && loaded=$((loaded+1))
  done
  rm -f "$f"
done
check H34-04-images-loaded "$loaded" "9"

$K apply -f $LOG/manifests/ingress-nginx.yaml > $LOG/ingress-apply.log 2>&1
# 현재 kind 매니페스트는 컨트롤러를 ingress-ready 노드에 묶지 않는다 — 호스트 포트 매핑이 있는 control-plane 으로 고정
$K -n ingress-nginx patch deploy ingress-nginx-controller \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"ingress-ready":"true","kubernetes.io/os":"linux"}}}}}' >> $LOG/ingress-apply.log 2>&1
$K -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=300s >> $LOG/ingress-apply.log 2>&1
$K -n ingress-nginx wait --for=condition=Ready pod -l app.kubernetes.io/component=controller --timeout=300s >> $LOG/ingress-apply.log 2>&1
$K apply -f $LOG/manifests/metrics-server.yaml > $LOG/metrics-apply.log 2>&1
$K -n kube-system patch deploy metrics-server --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' >> $LOG/metrics-apply.log 2>&1
$K -n kube-system rollout status deploy/metrics-server --timeout=300s >> $LOG/metrics-apply.log 2>&1
check H34-05-addons "$($K -n ingress-nginx get deploy ingress-nginx-controller -o jsonpath='{.status.readyReplicas}'):$($K -n kube-system get deploy metrics-server -o jsonpath='{.status.readyReplicas}')" "1:1"

# ── 설치 — ingress·HPA(최소 2)·PDB·NetworkPolicy(api 는 ingress-nginx·릴리스 파드에서만) ──
cat > $LOG/values-e34.yaml <<YAML
publicBaseUrl: $BASE
ingress: { enabled: true, className: nginx, host: $HOST }
autoscaling: { enabled: true, minReplicas: 2, maxReplicas: 4, targetCPUUtilizationPercentage: 50 }
podDisruptionBudget: { enabled: true, minAvailable: 1 }
persistence: { enabled: false }        # 다중 복제 + RWO 는 차트가 거부 — kind 에 RWX 프로비저너 없음
postgresql: { storage: { size: 1Gi } }
api: { resources: { requests: { cpu: 100m, memory: 256Mi } } }
networkPolicy:
  enabled: true
  ingressFrom:
    - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: ingress-nginx } }
    - podSelector: { matchLabels: { app.kubernetes.io/instance: $REL } }
YAML
$HL install $REL deploy/helm/agentnexus --create-namespace -f $LOG/values-e34.yaml --wait --timeout 12m > $LOG/helm-install.log 2>&1
check H34-06-helm-install "$?" "0"
$KN get pods,svc,ingress,hpa,pdb,networkpolicy -o wide > $LOG/resources.txt 2>&1
check H34-07-api-replicas "$($KN get deploy $REL-api -o jsonpath='{.status.readyReplicas}')" "2"
for i in $(seq 1 60); do [ "$($C -o /dev/null -w '%{http_code}' $BASE/health)" = "200" ] && break; sleep 2; done
check H34-08-ingress-health "$($C $BASE/health | jq -r '"\(.status):\(.db.status)"')" "ok:ok"

PW=$(openssl rand -hex 16)
printf '%s' "$PW" | $KN exec -i deploy/$REL-api -c api -- python -c '
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
CODE=$($C -o /dev/null -w '%{redirect_url}' -X POST "$BASE/idp/authorize" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d state=s34 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$($C -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
A="Authorization: Bearer $TOKEN"
check H34-09-login-via-ingress "$($C -H "$A" "$BASE/api/v1/me" | jq -r .sub)" "usr_rehearsal"
check H34-10-tool-call-via-ingress "$($C -X POST -H "$A" "$BASE/api/v1/mcp-servers/jira/refresh" | jq -r .health):$($C -H "$A" -H "$J" \
  -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_issues","arguments":{"query":"결제"}}}' "$BASE/mcp/jira" | jq -r '.result.isError // false')" "healthy:false"

# ── NetworkPolicy 실제 차단 — 릴리스 라벨 없는 파드: backend·api 모두 차단 / api 파드 → backend 허용 ──
PROBE='import socket, sys
out = []
for host, port in [(h.split(":")[0], int(h.split(":")[1])) for h in sys.argv[1:]]:
    s = socket.socket(); s.settimeout(4)
    try:
        s.connect((host, port)); out.append("open")
    except OSError:
        out.append("blocked")
    finally:
        s.close()
print(",".join(out))'
$KN run np-probe --image=wise-agentnexus-api:prod --image-pull-policy=Never --restart=Never --labels=app=np-probe \
  --command -- python -c "$PROBE" $REL-postgresql:5432 $REL-mcp-servers:8100 $REL-valkey:6379 $REL-api:8000 > /dev/null 2>&1
$KN wait --for=jsonpath='{.status.phase}'=Succeeded pod/np-probe --timeout=120s > /dev/null 2>&1
check H34-11-np-probe-blocked "$($KN logs np-probe 2>/dev/null)" "blocked,blocked,blocked,blocked"
check H34-12-np-api-allowed "$($KN exec deploy/$REL-api -c api -- python -c "$PROBE" $REL-mcp-servers:8100 $REL-postgresql:5432 2>/dev/null)" "open,open"
$KN delete pod np-probe --wait=false > /dev/null 2>&1

# ── PDB — 복제 2 · minAvailable 1: 첫 퇴거 허용, 대체 파드가 준비되기 전 두 번째 퇴거는 429 ──
PODS=$($KN get pods -l app.kubernetes.io/component=api -o jsonpath='{.items[*].metadata.name}')
P1=$(echo $PODS | cut -d' ' -f1); P2=$(echo $PODS | cut -d' ' -f2)
evict() { printf '{"apiVersion":"policy/v1","kind":"Eviction","metadata":{"name":"%s","namespace":"%s"}}' "$1" "$NS" \
  | $K create --raw "/api/v1/namespaces/$NS/pods/$1/eviction" -f - 2>&1; }
(for i in $(seq 1 120); do $C -o /dev/null -w '%{http_code}\n' --max-time 3 $BASE/health; sleep 0.5; done) > $LOG/pdb-health.log &
H=$!
E1=$(evict "$P1"); E2=$(evict "$P2")
echo "evict1: $E1" > $LOG/pdb-evict.log; echo "evict2: $E2" >> $LOG/pdb-evict.log
check H34-13-pdb-first-allowed "$(printf '%s' "$E1" | grep -c 'Cannot evict\|TooManyRequests\|429')" "0"
check H34-14-pdb-second-refused "$(printf '%s' "$E2" | grep -c 'disruption budget\|TooManyRequests\|429')" "1"
wait $H
check H34-15-available-during-eviction "$(awk '$1=="200"{ok++} END{printf "%.2f", ok/NR}' $LOG/pdb-health.log | awk '{print ($1 >= 0.99) ? "true" : "false"}')" "true"
$KN rollout status deploy/$REL-api --timeout=300s > /dev/null 2>&1

# ── HPA — metrics-server 지표가 들어오면 ingress 경유 부하로 2 → 3+ ──
for i in $(seq 1 60); do [ "$($KN get hpa $REL-api -o json | jq -r '.status.currentMetrics[0].resource.current.averageUtilization // empty')" != "" ] && break; sleep 5; done
check H34-16-hpa-metrics "$($KN get hpa $REL-api -o json | jq -r '.status.currentMetrics[0].resource.current.averageUtilization != null')" "true"
END=$(( $(date +%s) + 240 ))
seq 1 24 | xargs -P 24 -I{} sh -c "while [ \$(date +%s) -lt $END ]; do curl -s -o /dev/null --resolve $HOST:$HOSTPORT:127.0.0.1 $BASE/.well-known/agent-card.json; done" &
LOAD=$!
UP=no
while [ $(date +%s) -lt $END ]; do
  [ "$($KN get hpa $REL-api -o jsonpath='{.status.desiredReplicas}')" -gt 2 ] 2>/dev/null && UP=yes && break
  sleep 10
done
$KN get hpa $REL-api -o wide > $LOG/hpa.txt 2>&1
kill $LOAD 2>/dev/null; pkill -f "while \[ \$(date +%s) -lt $END \]" 2>/dev/null
check H34-17-hpa-scaled-up "$UP" "yes"
$KN logs deploy/$REL-api -c api --tail=3000 > $LOG/api.log 2>&1
check H34-18-no-traceback "$(grep -c 'Traceback' $LOG/api.log)" "0"

if [ "${KEEP:-0}" != "1" ]; then
  $HL uninstall $REL --wait --timeout 5m > $LOG/helm-uninstall.log 2>&1
  check H34-19-helm-uninstall "$?" "0"
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KC" > $LOG/kind-delete.log 2>&1
  check H34-20-cluster-deleted "$(kind get clusters 2>/dev/null | grep -cx "$CLUSTER")" "0"
fi
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
