#!/bin/sh
# impl 41st — a2a-grpc Helm·TLS (D17-②): 전용 kind(control-plane 1 + worker 1, 별도 kubeconfig) + Calico + ingress-nginx + cert-manager →
# 차트 a2aGrpc.enabled — ① 파드 평문 + Ingress TLS 종료(backend-protocol GRPC) ② 파드 직접 TLS(Secret) + Ingress GRPCS
# · NetworkPolicy(api 수신은 ingress-nginx·a2a-grpc 만) · 카드 GRPC 광고 → 삭제.
# 기본 kubeconfig·다른 클러스터는 건드리지 않는다. rehearsal 비밀번호는 실행 중 만들고 출력하지 않는다.
# 사용: KC=<scratch>/kubeconfig-e41 SCRATCH=<scratch> sh test/impl/41st/cluster-grpc.sh   (KEEP=1 이면 정리 생략)
set -u
KC=${KC:?전용 kubeconfig 경로}
SCR=${SCRATCH:?scratch 디렉터리}
CLUSTER=${CLUSTER:-agentnexus-e41}
NS=agentnexus
REL=agentnexus
HOST=agentnexus.local
GHOST=grpc.agentnexus.local
HTTPS_PORT=18443
HTTP_PORT=18880
BASE="https://$HOST:$HTTPS_PORT"
LOG=test/impl/41st/logs
mkdir -p $LOG "$SCR/e41"
K="kubectl --kubeconfig $KC"
KN="$K -n $NS"
HL="helm --kubeconfig $KC -n $NS"
CA="$SCR/e41/ca.crt"
C="curl -s --max-time 8 --resolve $HOST:$HTTPS_PORT:127.0.0.1 --cacert $CA"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
CLIENT=test/impl/41st/grpc-client.py
SVC="$REL-a2a-grpc"

# ── 클러스터·CNI·애드온 (38th 와 같은 수정 포함) ──
cat > $LOG/kind-config.yaml <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking: { disableDefaultCNI: true, podSubnet: 10.244.0.0/16 }
nodes:
  - role: control-plane
    kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
    extraPortMappings:
      - { containerPort: 80, hostPort: $HTTP_PORT, listenAddress: "127.0.0.1", protocol: TCP }
      - { containerPort: 443, hostPort: $HTTPS_PORT, listenAddress: "127.0.0.1", protocol: TCP }
  - role: worker
YAML
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --kubeconfig "$KC" --config $LOG/kind-config.yaml > $LOG/kind-create.log 2>&1
check G41-01-context "$($K config current-context)" "kind-$CLUSTER"
curl -sL --max-time 120 -o $SCR/e41/calico.yaml https://raw.githubusercontent.com/projectcalico/calico/v3.30.3/manifests/calico.yaml
sed -i.bak -e 's|# - name: CALICO_IPV4POOL_CIDR|- name: CALICO_IPV4POOL_CIDR|' -e 's|#   value: "192.168.0.0/16"|  value: "10.244.0.0/16"|' $SCR/e41/calico.yaml
$K apply -f $SCR/e41/calico.yaml > $LOG/calico-apply.log 2>&1
$K -n kube-system rollout status ds/calico-node --timeout=600s >> $LOG/calico-apply.log 2>&1
$K wait --for=condition=Ready nodes --all --timeout=300s > /dev/null 2>&1
check G41-02-nodes-ready "$($K get nodes -o json | jq -r '[.items[] | select(.status.conditions[] | select(.type=="Ready").status=="True")] | length')" "2"
loaded=0
for img in wise-agentnexus-api:prod postgres:17-alpine valkey/valkey:8-alpine; do
  plat=$(docker image inspect "$img" --format '{{.Os}}/{{.Architecture}}')
  f=$SCR/e41/$(printf '%s' "$img" | tr '/:' '__').tar
  docker save --platform "$plat" "$img" -o "$f"
  for node in $(kind get nodes --name "$CLUSTER"); do
    docker exec -i "$node" ctr --namespace=k8s.io images import --digests --snapshotter=overlayfs - < "$f" >> $LOG/kind-load.log 2>&1 && loaded=$((loaded+1))
  done
  rm -f "$f"
done
check G41-03-images-loaded "$loaded" "6"
curl -sL --max-time 60 -o $SCR/e41/ingress-nginx.yaml https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
$K apply -f $SCR/e41/ingress-nginx.yaml > $LOG/ingress-apply.log 2>&1
$K -n ingress-nginx patch deploy ingress-nginx-controller \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"ingress-ready":"true","kubernetes.io/os":"linux"}}}}}' >> $LOG/ingress-apply.log 2>&1
$K -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=300s >> $LOG/ingress-apply.log 2>&1
curl -sL --max-time 120 -o $SCR/e41/cert-manager.yaml https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
$K apply -f $SCR/e41/cert-manager.yaml > $LOG/cert-manager-apply.log 2>&1
for d in cert-manager cert-manager-cainjector cert-manager-webhook; do $K -n cert-manager rollout status deploy/$d --timeout=300s >> $LOG/cert-manager-apply.log 2>&1; done
check G41-04-addons "$($K -n ingress-nginx get deploy ingress-nginx-controller -o jsonpath='{.status.readyReplicas}'):$($K -n cert-manager get deploy cert-manager-webhook -o jsonpath='{.status.readyReplicas}')" "1:1"
cat > $LOG/issuers.yaml <<'YAML'
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: { name: selfsigned }
spec: { selfSigned: {} }
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata: { name: agentnexus-ca, namespace: cert-manager }
spec:
  isCA: true
  commonName: agentnexus-e41-ca
  secretName: agentnexus-ca
  privateKey: { algorithm: ECDSA, size: 256 }
  issuerRef: { name: selfsigned, kind: ClusterIssuer }
---
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: { name: agentnexus-ca }
spec: { ca: { secretName: agentnexus-ca } }
YAML
for i in $(seq 1 30); do $K apply -f $LOG/issuers.yaml > $LOG/issuers-apply.log 2>&1 && break; sleep 5; done
$K -n cert-manager wait --for=condition=Ready certificate/agentnexus-ca --timeout=120s > /dev/null 2>&1
check G41-05-ca-ready "$($K -n cert-manager get certificate agentnexus-ca -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" "True"
$K -n cert-manager get secret agentnexus-ca -o jsonpath='{.data.ca\.crt}' | base64 -d > "$CA"

# ── ① 파드 평문 + Ingress TLS 종료 — api 수신 NetworkPolicy 는 ingress-nginx 만(차트가 a2a-grpc 를 덧붙인다) ──
cat > $LOG/values-e41.yaml <<YAML
image: { repository: wise-agentnexus-api, tag: prod, pullPolicy: Never }
publicBaseUrl: $BASE
ingress:
  enabled: true
  className: nginx
  host: $HOST
  annotations: { cert-manager.io/cluster-issuer: agentnexus-ca }
  tls: [{ secretName: agentnexus-tls, hosts: [$HOST] }]
a2aGrpc:
  enabled: true
  publicUrl: grpcs://$GHOST:$HTTPS_PORT
  ingress:
    enabled: true
    className: nginx
    host: $GHOST
    annotations: { cert-manager.io/cluster-issuer: agentnexus-ca }
    tls: [{ secretName: agentnexus-grpc-tls, hosts: [$GHOST] }]
networkPolicy:
  enabled: true
  ingressFrom:  # r1: Ingress 만 열면 데모 에이전트(api 로 토큰)·시험 클라이언트(api 파드)도 막힌다 — a2a-grpc 는 일부러 빼서 차트 규칙을 시험
    - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: ingress-nginx } }
    - podSelector: { matchExpressions: [{ key: app.kubernetes.io/component, operator: In, values: [api, mcp-servers, a2a-demo] }] }
YAML
$HL install $REL deploy/helm/agentnexus --create-namespace -f $LOG/values-e41.yaml --wait --timeout 12m > $LOG/helm-install.log 2>&1
check G41-06-helm-install "$?" "0"
$KN wait --for=condition=Ready certificate/agentnexus-tls certificate/agentnexus-grpc-tls --timeout=180s > /dev/null 2>&1
$KN get pods,svc,ingress,certificate,networkpolicy -o wide > $LOG/resources.txt 2>&1
check G41-07-a2a-grpc-ready "$($KN get deploy $SVC -o jsonpath='{.status.readyReplicas}'):$($KN get certificate agentnexus-grpc-tls -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" "1:True"
check G41-08-netpol-from "$($KN get networkpolicy $REL-api -o json | jq -r '[.spec.ingress[0].from[] | (.namespaceSelector.matchLabels["kubernetes.io/metadata.name"] // .podSelector.matchLabels["app.kubernetes.io/component"] // "demo-pods")] | join(",")')" "ingress-nginx,demo-pods,a2a-grpc"
for i in $(seq 1 60); do [ "$($C -o /dev/null -w '%{http_code}' $BASE/health)" = "200" ] && break; sleep 2; done
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s41 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$($C -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
A="Authorization: Bearer $TOKEN"
$C -X POST -H "$A" "$BASE/api/v1/a2a/agents/observability-analyst/refresh" > /dev/null
check G41-09-card-grpc "$($C "$BASE/.well-known/agent-card.json" | jq -r '[.supportedInterfaces[] | select(.protocolBinding=="GRPC")][0].url')" "grpcs://$GHOST:$HTTPS_PORT"
incluster() {  # $1 ADDR  $2 CA_PEM(비우면 평문)  $3 SNI  $4 MODE
  $KN exec -i deploy/$REL-api -c api -- env ADDR="$1" CA_PEM="$2" SNI="$3" MODE="$4" TOKEN="$TOKEN" python - < $CLIENT 2>&1
}
viaingress() {  # $1 MODE — kind 네트워크의 컨테이너 → control-plane:443(Ingress) TLS, SNI=gRPC 호스트
  docker run --rm --network kind -e ADDR="$CLUSTER-control-plane:443" -e CA_PEM="$(cat "$CA")" -e SNI=$GHOST -e MODE="$1" \
    -e TOKEN="$([ "$1" = anon ] || printf '%s' "$TOKEN")" -i --entrypoint python wise-agentnexus-api:prod - < $CLIENT 2>&1
}
incluster "$SVC:50051" "" "" send > $LOG/a-incluster.txt
check G41-10-incluster-plaintext "$(sed -n 's/^send \([A-Z_]*\) .*/\1/p' $LOG/a-incluster.txt):$(grep -c '^get ' $LOG/a-incluster.txt)" "TASK_STATE_COMPLETED:1"
$KN run e41-probe --image=wise-agentnexus-api:prod --image-pull-policy=Never --restart=Never --command -- \
  python -c "import socket; socket.create_connection(('$REL-api', 8000), 3); print('open')" > /dev/null 2>&1
for i in $(seq 1 40); do ph=$($KN get pod e41-probe -o jsonpath='{.status.phase}'); [ "$ph" = Succeeded ] || [ "$ph" = Failed ] && break; sleep 2; done
check G41-11-netpol-blocks-other-pods "$($KN logs e41-probe 2>/dev/null | grep -c '^open')" "0"
$KN delete pod e41-probe --wait=false > /dev/null 2>&1
viaingress send > $LOG/a-ingress.txt
check G41-12-ingress-tls "$(sed -n 's/^send \([A-Z_]*\) .*/\1/p' $LOG/a-ingress.txt):$(grep -c '^get ' $LOG/a-ingress.txt)" "TASK_STATE_COMPLETED:1"
viaingress anon > $LOG/a-anon.txt
check G41-13-ingress-anon "$(awk '/^anon /{print $2}' $LOG/a-anon.txt)" "UNAUTHENTICATED"

# ── ② 파드 직접 TLS — 서비스 DNS 인증서(Secret) → 차트 tls.secretName, Ingress 는 GRPCS ──
cat > $LOG/pod-cert.yaml <<YAML
apiVersion: cert-manager.io/v1
kind: Certificate
metadata: { name: agentnexus-grpc-pod-tls, namespace: $NS }
spec:
  secretName: agentnexus-grpc-pod-tls
  dnsNames: [$SVC, $SVC.$NS.svc, $SVC.$NS.svc.cluster.local]
  privateKey: { algorithm: ECDSA, size: 256 }
  issuerRef: { name: agentnexus-ca, kind: ClusterIssuer }
YAML
$K apply -f $LOG/pod-cert.yaml > $LOG/pod-cert-apply.log 2>&1
$KN wait --for=condition=Ready certificate/agentnexus-grpc-pod-tls --timeout=120s > /dev/null 2>&1
$HL upgrade $REL deploy/helm/agentnexus -f $LOG/values-e41.yaml --set a2aGrpc.tls.secretName=agentnexus-grpc-pod-tls --wait --timeout 8m > $LOG/helm-upgrade.log 2>&1
# r2: 롤아웃 직후 이전(평문) 파드가 남아 있으면 로그·연결이 그쪽으로 간다 — 새 파드 하나만 남을 때까지
for i in $(seq 1 60); do [ "$($KN get pods -l app.kubernetes.io/component=a2a-grpc -o name | wc -l | tr -d ' ')" = 1 ] && break; sleep 2; done
check G41-14-upgrade-pod-tls "$?:$($KN get ingress $SVC -o jsonpath='{.metadata.annotations.nginx\.ingress\.kubernetes\.io/backend-protocol}')" "0:GRPCS"
incluster "$SVC:50051" "" "" send > $LOG/b-plain.txt
check G41-15-pod-tls-rejects-plaintext "$(awk '/^error /{print $2}' $LOG/b-plain.txt)" "UNAVAILABLE"
incluster "$SVC:50051" "$(cat "$CA")" "$SVC.$NS.svc" send > $LOG/b-tls.txt
check G41-16-pod-tls "$(sed -n 's/^send \([A-Z_]*\) .*/\1/p' $LOG/b-tls.txt):$(grep -c '^get ' $LOG/b-tls.txt)" "TASK_STATE_COMPLETED:1"
sleep 5  # Ingress 설정 반영
viaingress send > $LOG/b-ingress.txt
check G41-17-ingress-grpcs "$(sed -n 's/^send \([A-Z_]*\) .*/\1/p' $LOG/b-ingress.txt)" "TASK_STATE_COMPLETED"
$KN logs deploy/$SVC --tail=500 > $LOG/a2a-grpc.log 2>&1
check G41-18-no-traceback "$(grep -c Traceback $LOG/a2a-grpc.log):$(grep -c '(TLS)' $LOG/a2a-grpc.log):$(grep -c 'Read-only file system' $LOG/a2a-grpc.log)" "0:1:0"

if [ "${KEEP:-0}" != "1" ]; then
  $HL uninstall $REL --wait --timeout 5m > $LOG/helm-uninstall.log 2>&1
  check G41-19-helm-uninstall "$?" "0"
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KC" > $LOG/kind-delete.log 2>&1
  check G41-20-cluster-deleted "$(kind get clusters 2>/dev/null | grep -cx "$CLUSTER")" "0"
  rm -f "$SCR/e41/calico.yaml" "$SCR/e41/calico.yaml.bak" "$SCR/e41/ingress-nginx.yaml" "$SCR/e41/cert-manager.yaml" "$CA" "$KC"
  rmdir "$SCR/e41" 2>/dev/null
fi
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
