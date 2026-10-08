#!/bin/sh
# impl 46th — 운영 HA 심화 (D18-③): 전용 kind(control-plane 1 + worker 3, 워커마다 존 라벨 zone-a/b/c, 별도 kubeconfig) +
# CloudNativePG(PostgreSQL 3 인스턴스, 존 분산) → 차트(api 2 복제 · topologySpreadConstraints 존 · database.url=CNPG rw 서비스) →
# primary 파드 삭제(장애조치) 중 /health 가용성·복구 시간·쓰기 복구 → 삭제.
# 기본 kubeconfig·다른 클러스터는 건드리지 않는다. DB 비밀번호는 CNPG 가 만든 Secret 에서 읽어 scratch values 로만 넘기고 바로 지운다.
# 사용: KC=<scratch>/kubeconfig-e46 SCRATCH=<scratch> sh test/impl/46th/cluster-db-ha.sh   (KEEP=1 이면 정리 생략)
set -u
KC=${KC:?전용 kubeconfig 경로}
SCR=${SCRATCH:?scratch 디렉터리}
CLUSTER=${CLUSTER:-agentnexus-e46}
NS=agentnexus
REL=agentnexus
CNPG_VERSION=1.30.1
LOG=test/impl/46th/logs
mkdir -p $LOG "$SCR/e46"
K="kubectl --kubeconfig $KC"
KN="$K -n $NS"
HL="helm --kubeconfig $KC -n $NS"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1 (got=$2 want=$3)"; fail=$((fail+1)); fi; }

# ── 클러스터 — 워커 3개에 존 라벨 ──
cat > $LOG/kind-config.yaml <<'YAML'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
    labels: { topology.kubernetes.io/zone: zone-a }
  - role: worker
    labels: { topology.kubernetes.io/zone: zone-b }
  - role: worker
    labels: { topology.kubernetes.io/zone: zone-c }
YAML
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --kubeconfig "$KC" --config $LOG/kind-config.yaml > $LOG/kind-create.log 2>&1
$K wait --for=condition=Ready nodes --all --timeout=300s > /dev/null 2>&1
check D46-01-context "$($K config current-context)" "kind-$CLUSTER"
check D46-02-zones "$($K get nodes -o json | jq -r '[.items[].metadata.labels["topology.kubernetes.io/zone"] // empty] | unique | join(",")')" "zone-a,zone-b,zone-c"
loaded=0
for img in wise-agentnexus-api:prod valkey/valkey:8-alpine; do
  plat=$(docker image inspect "$img" --format '{{.Os}}/{{.Architecture}}')
  f=$SCR/e46/$(printf '%s' "$img" | tr '/:' '__').tar
  docker save --platform "$plat" "$img" -o "$f"
  for node in $(kind get nodes --name "$CLUSTER"); do
    docker exec -i "$node" ctr --namespace=k8s.io images import --digests --snapshotter=overlayfs - < "$f" >> $LOG/kind-load.log 2>&1 && loaded=$((loaded+1))
  done
  rm -f "$f"
done
check D46-03-images-loaded "$loaded" "8"

# ── CloudNativePG — 3 인스턴스, 존마다 하나 ──
curl -sL --max-time 120 -o $SCR/e46/cnpg.yaml "https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v$CNPG_VERSION/cnpg-$CNPG_VERSION.yaml"
$K apply --server-side -f $SCR/e46/cnpg.yaml > $LOG/cnpg-apply.log 2>&1
$K -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=300s >> $LOG/cnpg-apply.log 2>&1
check D46-04-cnpg-operator "$($K -n cnpg-system get deploy cnpg-controller-manager -o jsonpath='{.status.readyReplicas}')" "1"
$K create namespace extdb > /dev/null 2>&1
cat > $LOG/pg-cluster.yaml <<'YAML'
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: { name: pg, namespace: extdb }
spec:
  instances: 3
  storage: { size: 1Gi }
  bootstrap: { initdb: { database: app, owner: app } }
  affinity:
    topologyKey: topology.kubernetes.io/zone
    enablePodAntiAffinity: true
    podAntiAffinityType: required
YAML
for i in $(seq 1 20); do $K apply -f $LOG/pg-cluster.yaml > $LOG/pg-apply.log 2>&1 && break; sleep 5; done  # 웹훅 준비 전에는 거부
for i in $(seq 1 120); do [ "$($K -n extdb get cluster pg -o jsonpath='{.status.readyInstances}' 2>/dev/null)" = 3 ] && break; sleep 5; done
check D46-05-pg-ready "$($K -n extdb get cluster pg -o jsonpath='{.status.readyInstances}'):$($K -n extdb get pods -l cnpg.io/cluster=pg -o json | jq -r '[.items[].spec.nodeName] | unique | length')" "3:3"

# ── 차트 — api 2 복제, 존 분산, 외부 DB = CNPG rw 서비스 ──
cat > $LOG/values-e46.yaml <<'YAML'
image: { repository: wise-agentnexus-api, tag: prod, pullPolicy: Never }
api:
  replicas: 2
  resources: { requests: { cpu: 100m, memory: 256Mi } }
  topologySpreadConstraints:
    - { maxSkew: 1, topologyKey: topology.kubernetes.io/zone, whenUnsatisfiable: DoNotSchedule }
  tolerations:
    - { key: node.kubernetes.io/not-ready, operator: Exists, effect: NoExecute, tolerationSeconds: 20 }
    - { key: node.kubernetes.io/unreachable, operator: Exists, effect: NoExecute, tolerationSeconds: 20 }
persistence: { enabled: false }
postgresql: { enabled: false }
demo: { enabled: false }
YAML
DBPW=$($K -n extdb get secret pg-app -o jsonpath='{.data.password}' | base64 -d)
printf 'database:\n  url: "postgresql+psycopg://app:%s@pg-rw.extdb.svc.cluster.local:5432/app"\n' "$DBPW" > "$SCR/e46/values-db.yaml"
unset DBPW
$HL install $REL deploy/helm/agentnexus --create-namespace -f $LOG/values-e46.yaml -f "$SCR/e46/values-db.yaml" --wait --timeout 12m > $LOG/helm-install.log 2>&1
check D46-06-helm-install "$?" "0"
rm -f "$SCR/e46/values-db.yaml"
$KN get pods -o wide > $LOG/pods.txt 2>&1
check D46-07-api-zones "$($KN get pods -l app.kubernetes.io/component=api -o json | jq -r '[.items[].spec.nodeName] | unique | length')" "2"

# ── 장애조치 — primary 가 있는 노드를 정지(존 장애). 시험 파드는 다른 워커에 두고 0.5초마다 /health ──
# r1: primary 파드 삭제는 정상 종료 경로 — PostgreSQL smart shutdown 이 풀에 남은 연결을 기다려(최대 180초) 파드가 Terminating 에
# 머물고 장애조치가 일어나지 않았다(새 연결만 시간 초과). 실제 장애(노드 정지)로 바꿔 장애조치·keepalive 감지를 시험한다.
P0=$($K -n extdb get cluster pg -o jsonpath='{.status.currentPrimary}')
N0=$($K -n extdb get pod "$P0" -o jsonpath='{.spec.nodeName}')
PN=$($K get nodes -o json | jq -r --arg n "$N0" '[.items[] | select(.metadata.name != $n and (.metadata.labels["topology.kubernetes.io/zone"] // "") != "") | .metadata.name][0]')
$KN run e46-probe --image=wise-agentnexus-api:prod --image-pull-policy=Never --restart=Never \
  --overrides "{\"spec\":{\"nodeName\":\"$PN\"}}" --command -- sleep 3600 > /dev/null 2>&1
$KN wait --for=condition=Ready pod/e46-probe --timeout=120s > /dev/null 2>&1
check D46-08-health "$($KN exec e46-probe -- python -c "import json,urllib.request; b=json.load(urllib.request.urlopen('http://$REL-api:8000/health', timeout=5)); print(b['status']+':'+b['db']['status'])" 2>/dev/null)" "ok:ok"
$KN exec e46-probe -- python -c "
import time, urllib.request
end = time.time() + 180
while time.time() < end:
    t = time.time()
    try:
        code = urllib.request.urlopen('http://$REL-api:8000/health', timeout=3).status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = 0
    print(f'{t:.2f} {code}', flush=True)
    time.sleep(max(0.5 - (time.time() - t), 0))
" > $LOG/failover-health.log 2>&1 &
PROBE=$!
sleep 15
T0=$(date +%s); echo "$T0 stop node $N0 (primary $P0, probe on $PN)" > $LOG/failover-events.log
docker stop "$N0" > /dev/null
P1=$P0
for i in $(seq 1 75); do P1=$($K -n extdb get cluster pg -o jsonpath='{.status.currentPrimary}'); [ -n "$P1" ] && [ "$P1" != "$P0" ] && break; sleep 2; done
echo "$(date +%s) new primary $P1" >> $LOG/failover-events.log
check D46-09-new-primary "$([ -n "$P1" ] && [ "$P1" != "$P0" ] && echo switched || echo "$P1")" "switched"
wait $PROBE
# 복구 시간 = 정지 이후 첫 실패 → 마지막 실패까지 (실패가 없으면 0)
awk -v t0="$T0" '$1 >= t0 { if ($2 != "200") { if (!down) down = $1; last_bad = $1 } }
  END { printf "%s\n", (down ? sprintf("%.1f", last_bad - down + 0.5) : "0") }' $LOG/failover-health.log > $LOG/recovery-s.txt
awk '{n++; if ($2=="200") ok++} END {printf "availability %.4f (%d/%d)\n", ok/n, ok, n}' $LOG/failover-health.log >> $LOG/failover-events.log
echo "recovery_s $(cat $LOG/recovery-s.txt)" >> $LOG/failover-events.log
check D46-10-recovered-within-90s "$(awk '{print ($1 <= 90) ? "yes" : "no"}' $LOG/recovery-s.txt)" "yes"
check D46-11-availability-over-0.7 "$(awk '{n++; if ($2=="200") ok++} END {print (ok/n >= 0.7) ? "yes" : "no"}' $LOG/failover-health.log)" "yes"
check D46-12-tail-healthy "$(tail -20 $LOG/failover-health.log | awk '$2!="200"{bad++} END {print bad+0}')" "0"
check D46-13-write-after-failover "$($KN exec e46-probe -- python -c "
import json, urllib.request
print(json.load(urllib.request.urlopen('http://$REL-api:8000/health', timeout=5))['db']['status'])" 2>/dev/null):$($KN exec deploy/$REL-api -c api -- python -c "
from app.db import session_scope
from app.models import Setting
with session_scope() as db:
    row = db.get(Setting, 'e46.failover') or Setting(key='e46.failover', value={})
    row.value = {'ok': True}
    db.merge(row)
print('written')" 2>&1 | tail -1)" "ok:written"
docker start "$N0" > /dev/null; $K wait --for=condition=Ready node/$N0 --timeout=300s > /dev/null 2>&1
$KN logs deploy/$REL-api -c api --tail=2000 > $LOG/api.log 2>&1  # 장애조치 중 오류는 기록용 (판정은 D46-10~13)
$KN delete pod e46-probe --wait=false > /dev/null 2>&1

if [ "${KEEP:-0}" != "1" ]; then
  $HL uninstall $REL --wait --timeout 5m > $LOG/helm-uninstall.log 2>&1
  check D46-14-helm-uninstall "$?" "0"
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KC" > $LOG/kind-delete.log 2>&1
  check D46-15-cluster-deleted "$(kind get clusters 2>/dev/null | grep -cx "$CLUSTER")" "0"
  rm -f "$SCR/e46/cnpg.yaml" "$KC"
  rmdir "$SCR/e46" 2>/dev/null
fi
echo "total=$((pass+fail)) pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
