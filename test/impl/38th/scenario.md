# impl 38th — 운영 클러스터 심화 (Epic 38 · D16-③ · NFR-OPS·NFR-A1)

## 범위

34th(kind 3노드 · 내장 PostgreSQL · local-path RWO)에서 남긴 운영 항목을 실제 구성으로 검증한다. 차트 변경 없이 values 만으로
구성되는지(운영형 values 의 유효성)와 노드 장애 때 가용성을 본다.

| 항목 | 구성 |
|------|------|
| 클러스터 | 전용 kind `agentnexus-e38` (control-plane 1 + worker 2, 별도 kubeconfig — 기본 `~/.kube/config` 불변) · Calico(파드 CIDR 10.244/16) · ingress-nginx(ingress-ready 노드) |
| TLS | cert-manager — 자체 서명 루트 → CA Certificate `agentnexus-ca` → ClusterIssuer → Ingress `cert-manager.io/cluster-issuer` 주석 → Secret `agentnexus-tls` · http → https 308 |
| 외부 DB | `extdb` 네임스페이스 PostgreSQL 17 (worker 고정, 실행 중 만든 비밀번호 Secret) · 차트 `postgresql.enabled=false` + `database.url` (scratch values, 설치 직후 삭제) |
| RWX | NFS-Ganesha 사용자 공간 서버 + 외부 프로비저너(`nfs-provisioner:v4.0.8`, worker 고정, export = 노드 `/var` ext4) · StorageClass `example-nfs` · `persistence.accessMode=ReadWriteMany` |
| HA | api 2 복제 · preferred anti-affinity(노드 분산) · not-ready/unreachable 톨러레이션 20초 · CoreDNS 노드 분산(필수 anti-affinity) |
| 장애 주입 | api 파드가 있는 worker2 `docker stop` → 재배치·가용성(0.5초 간격 /health)·로그인·RWX 확인 → 노드 복구 |
| 정리 | helm uninstall → 앱 파드 소멸(NFS 언마운트) 대기 → NFS 서버 삭제 → kind 삭제 → scratch 파일 삭제 |

## 수용 기준 → 시험 케이스 (`cluster-ha.sh`)

| # | 수용 기준 |
|---|-----------|
| H38-01~04 | 전용 컨텍스트 · 3노드 Ready · CoreDNS 2노드 분산(02b) · 이미지 적재 · 애드온(ingress·cert-manager) |
| H38-05~07 | CA 발급자 Ready · NFS 프로비저너 · 외부 PostgreSQL Ready |
| H38-08~10 | helm install(--wait) · 내장 PostgreSQL 없음 · Ingress 인증서 Ready |
| H38-11~14 | https 헬스(ok:postgres:ok, CA 검증) · 인증서 발급자 CN · http→https 308 · api 엔진 호스트 = 외부 DB |
| H38-15~16 | https issuer 로 로그인(rehearsal) · MCP 도구 서버 갱신 healthy |
| H38-17~19 | PVC Bound·RWX·example-nfs · api 파드 서로 다른 노드 · 파드 간 파일 공유 |
| H38-20~24 | 노드 장애 → api 2 복제 다른 노드에서 Ready · 가용성 ≥ 0.95 · 장애 중 로그인 · RWX 유지 · Traceback 0 |
| H38-25~26 | helm uninstall · 클러스터 삭제 |

## 환경 가정

- 네트워크 설치는 매니페스트·이미지 내려받기(Calico·ingress-nginx·cert-manager·NFS-Ganesha)만 — 시험 클러스터 범위.
- 단일 복제 구성요소(valkey·mcp-servers·a2a-demo)는 장애 노드에 있으면 그동안 쓸 수 없다 — api 는 Valkey 단절 시 워커 로컬 한도로 버틴다(NFR-A1). 운영은 매니지드 Valkey/Redis 권장.
- 외부 DB 가용성(단일 Deployment)은 이 시험 범위 밖 — 실제 운영은 매니지드 PostgreSQL(HA) 전제.
