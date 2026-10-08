#!/bin/sh
# impl 38th — 운영 클러스터 심화 (D16-③): 전용 kind(control-plane 1 + worker 2, 별도 kubeconfig) + Calico + ingress-nginx +
# cert-manager(자체 서명 루트 → CA 발급자 → Ingress TLS) + NFS-Ganesha(RWX) + 외부 PostgreSQL(database.url, 내장 끔) →
# https 로그인·도구 호출 · api 2 복제가 다른 노드에서 같은 /app/data 공유 · 워커 노드 정지 → 재배치·가용성 → 삭제.
# 기본 kubeconfig·다른 클러스터는 건드리지 않는다(--kubeconfig/--name 명시). 임시 비밀번호(외부 DB·rehearsal)는 실행 중 만들고
# 출력하지 않으며 scratch 파일은 끝에 지운다 — 클러스터와 함께 사라진다.
# 사용: KC=<scratch>/kubeconfig-e38 SCRATCH=<scratch> sh test/impl/38th/cluster-ha.sh   (KEEP=1 이면 정리 생략)
set -u
KC=${KC:?전용 kubeconfig 경로}
SCR=${SCRATCH:?scratch 디렉터리}
CLUSTER=${CLUSTER:-agentnexus-e38}
NS=agentnexus
REL=agentnexus
HOST=agentnexus.local
HTTPS_PORT=18443
HTTP_PORT=18880
BASE="https://$HOST:$HTTPS_PORT"
LOG=test/impl/38th/logs
mkdir -p $LOG "$SCR/e38"
K="kubectl --kubeconfig $KC"
KN="$K -n $NS"
HL="helm --kubeconfig $KC -n $NS"
CA="$SCR/e38/ca.crt"
C="curl -s --max-time 8 --resolve $HOST:$HTTPS_PORT:127.0.0.1 --resolve $HOST:$HTTP_PORT:127.0.0.1 --cacert $CA"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }
J='Content-Type: application/json'
WORKER=$CLUSTER-worker; WORKER2=$CLUSTER-worker2

# ── 클러스터·CNI·애드온 (34th 와 같은 순서·수정 포함) ──
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
  - role: worker
YAML
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --kubeconfig "$KC" --config $LOG/kind-config.yaml > $LOG/kind-create.log 2>&1
check H38-01-context "$($K config current-context)" "kind-$CLUSTER"
curl -sL --max-time 120 -o $SCR/e38/calico.yaml https://raw.githubusercontent.com/projectcalico/calico/v3.30.3/manifests/calico.yaml
sed -i.bak -e 's|# - name: CALICO_IPV4POOL_CIDR|- name: CALICO_IPV4POOL_CIDR|' -e 's|#   value: "192.168.0.0/16"|  value: "10.244.0.0/16"|' $SCR/e38/calico.yaml
$K apply -f $SCR/e38/calico.yaml > $LOG/calico-apply.log 2>&1
$K -n kube-system rollout status ds/calico-node --timeout=600s >> $LOG/calico-apply.log 2>&1
$K wait --for=condition=Ready nodes --all --timeout=300s > /dev/null 2>&1
check H38-02-nodes-ready "$($K get nodes -o json | jq -r '[.items[] | select(.status.conditions[] | select(.type=="Ready").status=="True")] | length')" "3"
# r2: CoreDNS 두 복제가 한 노드에 몰려 그 노드 장애 때 클러스터 DNS 가 사라졌다(새 api 파드 init 이 DB 이름 해석 실패).
# 운영 클러스터처럼 노드 분산(필수 anti-affinity) 후 장애를 주입한다.
$K -n kube-system patch deploy coredns -p '{"spec":{"template":{"spec":{"affinity":{"podAntiAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":[{"labelSelector":{"matchLabels":{"k8s-app":"kube-dns"}},"topologyKey":"kubernetes.io/hostname"}]}}}}}}' > $LOG/coredns-spread.log 2>&1
$K -n kube-system rollout status deploy/coredns --timeout=300s >> $LOG/coredns-spread.log 2>&1
check H38-02b-dns-spread "$($K -n kube-system get pods -l k8s-app=kube-dns -o json | jq -r '[.items[] | select(.metadata.deletionTimestamp == null) | .spec.nodeName] | unique | length')" "2"
loaded=0
for img in wise-agentnexus-api:prod postgres:17-alpine valkey/valkey:8-alpine; do
  plat=$(docker image inspect "$img" --format '{{.Os}}/{{.Architecture}}')
  f=$SCR/e38/$(printf '%s' "$img" | tr '/:' '__').tar
  docker save --platform "$plat" "$img" -o "$f"
  for node in $(kind get nodes --name "$CLUSTER"); do
    docker exec -i "$node" ctr --namespace=k8s.io images import --digests --snapshotter=overlayfs - < "$f" >> $LOG/kind-load.log 2>&1 && loaded=$((loaded+1))
  done
  rm -f "$f"
done
check H38-03-images-loaded "$loaded" "9"
curl -sL --max-time 60 -o $SCR/e38/ingress-nginx.yaml https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
$K apply -f $SCR/e38/ingress-nginx.yaml > $LOG/ingress-apply.log 2>&1
$K -n ingress-nginx patch deploy ingress-nginx-controller \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"ingress-ready":"true","kubernetes.io/os":"linux"}}}}}' >> $LOG/ingress-apply.log 2>&1
$K -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=300s >> $LOG/ingress-apply.log 2>&1
curl -sL --max-time 120 -o $SCR/e38/cert-manager.yaml https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
$K apply -f $SCR/e38/cert-manager.yaml > $LOG/cert-manager-apply.log 2>&1
for d in cert-manager cert-manager-cainjector cert-manager-webhook; do $K -n cert-manager rollout status deploy/$d --timeout=300s >> $LOG/cert-manager-apply.log 2>&1; done
check H38-04-addons "$($K -n ingress-nginx get deploy ingress-nginx-controller -o jsonpath='{.status.readyReplicas}'):$($K -n cert-manager get deploy cert-manager-webhook -o jsonpath='{.status.readyReplicas}')" "1:1"

# ── 발급자: 자체 서명 루트 → CA → Ingress 인증서 ──
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
  commonName: agentnexus-e38-ca
  secretName: agentnexus-ca
  privateKey: { algorithm: ECDSA, size: 256 }
  issuerRef: { name: selfsigned, kind: ClusterIssuer }
---
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: { name: agentnexus-ca }
spec: { ca: { secretName: agentnexus-ca } }
YAML
for i in $(seq 1 30); do $K apply -f $LOG/issuers.yaml > $LOG/issuers-apply.log 2>&1 && break; sleep 5; done  # 웹훅 준비 전에는 거부된다
$K -n cert-manager wait --for=condition=Ready certificate/agentnexus-ca --timeout=120s > /dev/null 2>&1
check H38-05-ca-ready "$($K -n cert-manager get certificate agentnexus-ca -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" "True"
$K -n cert-manager get secret agentnexus-ca -o jsonpath='{.data.ca\.crt}' | base64 -d > "$CA"

# ── RWX: NFS-Ganesha (사용자 공간 NFS 서버 + 외부 프로비저너) — worker 에 고정 ──
for f in rbac deployment class; do curl -sL --max-time 30 -o $SCR/e38/nfs-$f.yaml \
  https://raw.githubusercontent.com/kubernetes-sigs/nfs-ganesha-server-and-external-provisioner/master/deploy/kubernetes/$f.yaml; done
# 저장소 master 매니페스트는 아직 게시되지 않은 태그(v6.5)를 가리킨다 — 게시된 최신(다중 아키텍처) v4.0.8 로 고정
sed -i.bak 's|registry.k8s.io/sig-storage/nfs-provisioner:v[0-9.]*|registry.k8s.io/sig-storage/nfs-provisioner:v4.0.8|' $SCR/e38/nfs-deployment.yaml
$K apply -f $SCR/e38/nfs-rbac.yaml -f $SCR/e38/nfs-deployment.yaml -f $SCR/e38/nfs-class.yaml > $LOG/nfs-apply.log 2>&1
$K patch deploy nfs-provisioner -p "{\"spec\":{\"template\":{\"spec\":{\"nodeSelector\":{\"kubernetes.io/hostname\":\"$WORKER\"}}}}}" >> $LOG/nfs-apply.log 2>&1
# kind 노드의 / 는 overlayfs — Ganesha VFS 가 내보내지 못한다(AddExport invalid param). /var 는 ext4 볼륨이라 그쪽으로
$K patch deploy nfs-provisioner --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/volumes/0/hostPath","value":{"path":"/var/lib/nfs-export","type":"DirectoryOrCreate"}}]' >> $LOG/nfs-apply.log 2>&1
$K rollout status deploy/nfs-provisioner --timeout=300s >> $LOG/nfs-apply.log 2>&1
check H38-06-nfs-provisioner "$($K get deploy nfs-provisioner -o jsonpath='{.status.readyReplicas}'):$($K get sc example-nfs -o jsonpath='{.provisioner}')" "1:example.com/nfs"

# ── 외부 PostgreSQL (차트 밖 네임스페이스, worker 고정) ──
DBPW=$(openssl rand -hex 16)
$K create namespace extdb > /dev/null 2>&1
$K -n extdb create secret generic extdb-pw --from-literal=password="$DBPW" > /dev/null 2>&1
cat > $LOG/extdb.yaml <<YAML
apiVersion: apps/v1
kind: Deployment
metadata: { name: postgres, namespace: extdb }
spec:
  replicas: 1
  selector: { matchLabels: { app: extdb-postgres } }
  template:
    metadata: { labels: { app: extdb-postgres } }
    spec:
      nodeSelector: { kubernetes.io/hostname: $WORKER }
      containers:
        - name: postgres
          image: postgres:17-alpine
          imagePullPolicy: IfNotPresent
          env:
            - { name: POSTGRES_USER, value: app }
            - { name: POSTGRES_DB, value: app }
            - { name: POSTGRES_PASSWORD, valueFrom: { secretKeyRef: { name: extdb-pw, key: password } } }
          ports: [{ containerPort: 5432 }]
          readinessProbe: { exec: { command: ["pg_isready", "-U", "app"] }, periodSeconds: 3 }
---
apiVersion: v1
kind: Service
metadata: { name: postgres, namespace: extdb }
spec: { selector: { app: extdb-postgres }, ports: [{ port: 5432, targetPort: 5432 }] }
YAML
$K apply -f $LOG/extdb.yaml > $LOG/extdb-apply.log 2>&1
$K -n extdb rollout status deploy/postgres --timeout=300s >> $LOG/extdb-apply.log 2>&1
check H38-07-extdb-ready "$($K -n extdb get deploy postgres -o jsonpath='{.status.readyReplicas}')" "1"

# ── 설치 — TLS ingress · 외부 DB · RWX persistence · api 2 복제(노드 분산 선호·노드 장애 20초 톨러레이션) ──
cat > $LOG/values-e38.yaml <<YAML
publicBaseUrl: $BASE
ingress:
  enabled: true
  className: nginx
  host: $HOST
  annotations: { cert-manager.io/cluster-issuer: agentnexus-ca }
  tls: [{ secretName: agentnexus-tls, hosts: [$HOST] }]
autoscaling: { enabled: false }
api:
  replicas: 2
  resources: { requests: { cpu: 100m, memory: 256Mi } }
  tolerations:
    - { key: node.kubernetes.io/not-ready, operator: Exists, effect: NoExecute, tolerationSeconds: 20 }
    - { key: node.kubernetes.io/unreachable, operator: Exists, effect: NoExecute, tolerationSeconds: 20 }
  affinity:
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 100
          podAffinityTerm: { topologyKey: kubernetes.io/hostname, labelSelector: { matchLabels: { app.kubernetes.io/component: api } } }
persistence: { enabled: true, accessMode: ReadWriteMany, storageClass: example-nfs, size: 1Gi }
postgresql: { enabled: false }
networkPolicy:
  enabled: true
  ingressFrom:
    - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: ingress-nginx } }
    - podSelector: { matchLabels: { app.kubernetes.io/instance: $REL } }
YAML
printf 'database:\n  url: "postgresql+psycopg://app:%s@postgres.extdb.svc.cluster.local:5432/app"\n' "$DBPW" > "$SCR/e38/values-db.yaml"
unset DBPW
$HL install $REL deploy/helm/agentnexus --create-namespace -f $LOG/values-e38.yaml -f "$SCR/e38/values-db.yaml" --wait --timeout 12m > $LOG/helm-install.log 2>&1
check H38-08-helm-install "$?" "0"
rm -f "$SCR/e38/values-db.yaml"
$KN get pods,svc,ingress,pvc,certificate,networkpolicy -o wide > $LOG/resources.txt 2>&1
check H38-09-no-internal-db "$($KN get statefulset -o json | jq -r '[.items[] | select(.metadata.labels["app.kubernetes.io/component"]=="postgresql")] | length')" "0"
$KN wait --for=condition=Ready certificate/agentnexus-tls --timeout=180s > /dev/null 2>&1
check H38-10-ingress-cert "$($KN get certificate agentnexus-tls -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" "True"

# ── TLS · 외부 DB · 로그인 ──
for i in $(seq 1 60); do [ "$($C -o /dev/null -w '%{http_code}' $BASE/health)" = "200" ] && break; sleep 2; done
check H38-11-https-health-verified "$($C $BASE/health | jq -r '"\(.status):\(.db.engine):\(.db.status)"')" "ok:postgres:ok"
check H38-12-cert-issuer "$(echo | openssl s_client -connect 127.0.0.1:$HTTPS_PORT -servername $HOST 2>/dev/null | openssl x509 -noout -issuer 2>/dev/null | grep -c agentnexus-e38-ca)" "1"
check H38-13-http-redirect "$($C -o /dev/null -w '%{http_code}' http://$HOST:$HTTP_PORT/health)" "308"
check H38-14-api-uses-extdb "$($KN exec deploy/$REL-api -c api -- python -c 'from app.db import engine; print(engine.url.host)' 2>/dev/null)" "postgres.extdb.svc.cluster.local"
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
  --data-urlencode "redirect_uri=$REDIR" -d state=s38 -d "code_challenge=$CHALLENGE" -d code_challenge_method=S256 \
  -d username=rehearsal --data-urlencode "password=$PW" | sed -n 's/.*code=\([^&]*\).*/\1/p')
TOKEN=$($C -X POST "$BASE/idp/token" -d grant_type=authorization_code -d "code=$CODE" -d client_id=agentnexus-console \
  --data-urlencode "redirect_uri=$REDIR" -d "code_verifier=$VERIFIER" | jq -r .access_token)
unset PW
A="Authorization: Bearer $TOKEN"
check H38-15-https-login "$($C -H "$A" "$BASE/api/v1/me" | jq -r '"\(.sub)"'):$($C "$BASE/idp/.well-known/openid-configuration" | jq -r '.issuer | startswith("https://")')" "usr_rehearsal:true"
check H38-16-tool-call "$($C -X POST -H "$A" "$BASE/api/v1/mcp-servers/jira/refresh" | jq -r .health)" "healthy"

# ── RWX — 다른 노드의 두 api 파드가 같은 /app/data ──
PODS=$($KN get pods -l app.kubernetes.io/component=api -o jsonpath='{range .items[*]}{.metadata.name}={.spec.nodeName} {end}')
echo "$PODS" > $LOG/api-pods-before.txt
P1=$(echo $PODS | tr ' ' '\n' | sed -n 1p | cut -d= -f1); N1=$(echo $PODS | tr ' ' '\n' | sed -n 1p | cut -d= -f2)
P2=$(echo $PODS | tr ' ' '\n' | sed -n 2p | cut -d= -f1); N2=$(echo $PODS | tr ' ' '\n' | sed -n 2p | cut -d= -f2)
check H38-17-pvc-rwx "$($KN get pvc $REL-data -o jsonpath='{.status.phase}:{.spec.accessModes[0]}:{.spec.storageClassName}')" "Bound:ReadWriteMany:example-nfs"
check H38-18-pods-spread "$([ "$N1" != "$N2" ] && echo distinct || echo same)" "distinct"
STAMP=$(openssl rand -hex 6)
$KN exec "$P1" -c api -- python -c "open('/app/data/e38-probe.txt','w').write('$STAMP')" > /dev/null 2>&1
check H38-19-rwx-shared "$($KN exec "$P2" -c api -- python -c "print(open('/app/data/e38-probe.txt').read())" 2>/dev/null)" "$STAMP"

# ── 워커 노드 정지 — ingress 경유 가용성 · api 재배치(20초 톨러레이션) ──
DOWN=$WORKER2
(for i in $(seq 1 360); do printf '%s %s\n' "$(date +%s)" "$($C -o /dev/null -w '%{http_code}' $BASE/health)"; sleep 0.5; done) > $LOG/node-failure-health.log &
H=$!
sleep 10
T0=$(date +%s); echo "$T0 stop $DOWN" > $LOG/node-failure-events.log
docker stop "$DOWN" > /dev/null
OK=no
for i in $(seq 1 90); do
  R=$($KN get pods -l app.kubernetes.io/component=api -o json | jq -r --arg d "$DOWN" '[.items[] | select(.status.phase=="Running" and .spec.nodeName != $d and ((.status.conditions // [])[] | select(.type=="Ready").status=="True"))] | length')
  [ "$R" -ge 2 ] && OK=yes && break; sleep 3
done
echo "$(date +%s) api-rescheduled=$OK" >> $LOG/node-failure-events.log
wait $H
$KN get pods -o wide > $LOG/pods-after-failure.txt 2>&1
check H38-20-api-rescheduled "$OK" "yes"
check H38-21-availability-during-failure "$(awk '{n++; if ($2=="200") ok++} END {printf "%s", (ok/n >= 0.95) ? "true" : "false"}' $LOG/node-failure-health.log)" "true"
awk '{n++; if ($2=="200") ok++} END {printf "availability %.4f (%d/%d)\n", ok/n, ok, n}' $LOG/node-failure-health.log >> $LOG/node-failure-events.log
check H38-22-login-after-failure "$($C -H "$A" "$BASE/api/v1/me" | jq -r .sub)" "usr_rehearsal"
check H38-23-rwx-after-failure "$($KN exec deploy/$REL-api -c api -- python -c "print(open('/app/data/e38-probe.txt').read())" 2>/dev/null)" "$STAMP"
docker start "$DOWN" > /dev/null; $K wait --for=condition=Ready node/$DOWN --timeout=300s > /dev/null 2>&1
$KN logs deploy/$REL-api -c api --tail=3000 > $LOG/api.log 2>&1
check H38-24-no-traceback "$(grep -c 'Traceback' $LOG/api.log)" "0"

if [ "${KEEP:-0}" != "1" ]; then
  $HL uninstall $REL --wait --timeout 5m > $LOG/helm-uninstall.log 2>&1
  check H38-25-helm-uninstall "$?" "0"
  # r2: NFS 클라이언트(api 파드)가 마운트를 쥔 채 서버·노드를 지우면 커널 NFS 정리 RPC 가 멈춰 노드 컨테이너가 죽지 않았다
  # → 앱 파드가 모두 내려가(언마운트) 뒤 NFS 서버를 지우고 클러스터를 삭제한다
  for i in $(seq 1 100); do [ "$($KN get pods -o name 2>/dev/null | wc -l | tr -d ' ')" = "0" ] && break; sleep 3; done
  $K -n default delete deploy nfs-provisioner --wait --timeout=180s > /dev/null 2>&1
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KC" > $LOG/kind-delete.log 2>&1
  check H38-26-cluster-deleted "$(kind get clusters 2>/dev/null | grep -cx "$CLUSTER")" "0"
  rm -f "$SCR/e38/calico.yaml" "$SCR/e38/calico.yaml.bak" "$SCR/e38/ingress-nginx.yaml" "$SCR/e38/cert-manager.yaml" \
    "$SCR/e38/nfs-rbac.yaml" "$SCR/e38/nfs-deployment.yaml" "$SCR/e38/nfs-deployment.yaml.bak" "$SCR/e38/nfs-class.yaml" "$CA" "$KC"
  rmdir "$SCR/e38" 2>/dev/null
fi
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
