# impl 38th — 결과: 운영 클러스터 심화 (Epic 38 · D16-③)

판정: **PASS** — `cluster-ha.sh` 27/27 (3회차). 이미지는 Epic 39 수정이 들어간 `wise-agentnexus-api:prod`(liveness TCP 차트 포함).

## 회차

| 회차 | 결과 | 원인 | 수정 |
|------|------|------|------|
| r1 | 중단 (H38-06) | NFS-Ganesha 매니페스트의 이미지 태그(v6.5)가 레지스트리에 없음 | `registry.k8s.io/sig-storage/nfs-provisioner:v4.0.8`(다중 아키텍처)로 고정 — 로그 `logs/r1-failed/` |
| r2 | 23/24 (H38-20 FAIL) | ① 프로비저너 export 가 노드 `/srv`(overlayfs) — Ganesha VFS 가 내보내지 못함(`AddExport … invalid param value`), 설치 대기 중 export 를 노드 `/var`(ext4)로 옮겨 진행 ② CoreDNS 두 복제가 장애 노드(worker2)에 몰려, 재배치된 api 파드의 init(마이그레이션)이 외부 DB 이름 해석 실패 → `Init:CrashLoopBackOff` | ① hostPath `/var/lib/nfs-export` 패치 ② CoreDNS 필수 anti-affinity(H38-02b) — 로그 `logs/r2-failed/` |
| r2 정리 | 노드 컨테이너가 죽지 않음 | KEEP 클러스터를 NFS 마운트가 살아 있는 채 삭제 → 커널 NFSv4 `destroy_session` RPC 가 사라진 netns 에서 재시도(약 30분 뒤 스스로 풀림, 이후 삭제) | 정리 순서: helm uninstall → 앱 파드 소멸(언마운트) 대기 → NFS 서버 삭제 → kind 삭제. 클러스터 이름 `CLUSTER` 덮어쓰기 지원 |
| r3 | **27/27** | — | `CLUSTER=agentnexus-e38b` 로 전체 실행 + 정리 |

## r3 결과 (`logs/cluster-ha-r3.log`)

| # | 결과 |
|---|------|
| H38-01~04 | 전용 컨텍스트 `kind-agentnexus-e38b` · 3노드 Ready · CoreDNS 2노드 분산 · 이미지 9개 적재 · ingress·cert-manager |
| H38-05~07 | CA 발급자 Ready · NFS 프로비저너(example.com/nfs) · 외부 PostgreSQL Ready |
| H38-08~10 | helm install 0 · 내장 PostgreSQL 0 · Ingress 인증서 Ready |
| H38-11~14 | https 헬스 `ok:postgres:ok`(CA 검증) · 발급자 `agentnexus-e38-ca` · http→https 308 · api DB 호스트 `postgres.extdb.svc.cluster.local` |
| H38-15~16 | https issuer 로그인 `usr_rehearsal` · MCP 도구 서버 healthy |
| H38-17~19 | PVC `Bound:ReadWriteMany:example-nfs` · api 파드 서로 다른 노드 · 파드 간 파일 공유 |
| H38-20~24 | worker2 정지 → 82초 만에 api 2 복제 다른 노드에서 Ready · 가용성 **1.0000 (360/360)** · 장애 중 로그인 · RWX 유지 · Traceback 0 |
| H38-25~26 | helm uninstall · 클러스터 삭제 |

정리 확인: `kind get clusters` 비어 있음 · scratch 의 kubeconfig·CA·매니페스트·DB values 삭제 · `~/.kube/config` sha1 `b88c119b…` 불변.

## 남은 항목 (운영 클러스터 몫)

- 단일 복제 구성요소(valkey·mcp-servers·a2a-demo)는 장애 노드에 있으면 그동안 쓸 수 없다 — api 는 Valkey 단절 시 워커 로컬 한도로 동작(NFR-A1).
- 외부 DB 자체의 HA(매니지드 PostgreSQL)·존 분산·a2a-grpc 의 Helm 배포는 범위 밖.
