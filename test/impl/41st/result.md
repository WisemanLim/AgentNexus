# impl 41st — 결과: a2a-grpc Helm·TLS (Epic 41 · D17-②)

판정: **PASS** — pytest C41-01·02 · kind `cluster-grpc.sh` **20/20** (3회차).

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest (`test_a2a_grpc.py` + `test_deploy.py`) | 8/8 | `logs/pytest-r1.log` |
| helm-check | lint + 4 시나리오(ha 에 a2aGrpc 복제 2·파드 TLS·Ingress) OK | 완료 게이트 |
| kind r1 | 16/20 | `logs/r1-failed/` |
| kind r2 | 17/18 (KEEP) | `logs/r2-failed/` |
| kind r3 | **20/20** + 정리 | `logs/cluster-grpc-r3.log` |

## 회차 원인

| 회차 | 실패 | 원인 | 처리 |
|------|------|------|------|
| r1 | G41-10·12·16·17 | 시험 설계 — 시험 values 의 `ingressFrom` 을 Ingress 컨트롤러만으로 두어 ① 시험 클라이언트(api 파드) → a2a-grpc 가 새 a2a-grpc NetworkPolicy 에 막힘(의도된 동작) ② 데모 에이전트가 api 로 가지 못해 원격 호출이 실패 | `ingressFrom` 에 api·데모 파드를 넣고 a2a-grpc 는 빼서 차트 자동 규칙만 시험 · 클라이언트 출력에 오류 상세 |
| r2 | G41-18 | 시험 결함 — 롤아웃 직후 이전(평문) 파드 로그를 읽음(`Found 2 pods`). 함께 **차트 결함** 발견: 읽기 전용 루트에서 진입점의 `mkdir /tmp/prometheus` 실패 | 새 파드 하나만 남을 때까지 대기 · 차트 a2a-grpc 에 `/tmp` emptyDir(api 와 같은 방식) · 확인 항목에 `Read-only file system` 0 추가 |

## r3 결과

| # | 결과 |
|---|------|
| G41-01~05 | `kind-agentnexus-e41` · 2노드 · 이미지 6 · ingress-nginx·cert-manager · CA |
| G41-06~09 | install 0 · a2a-grpc 1 Ready + `agentnexus-grpc-tls` Ready · api NetworkPolicy from `ingress-nginx,demo-pods,a2a-grpc` · 카드 GRPC `grpcs://grpc.agentnexus.local:18443` |
| G41-10~13 | 파드 평문: 클러스터 안 send `TASK_STATE_COMPLETED`·get · 릴리스 밖 파드 → api 연결 차단 · Ingress(TLS 종료, GRPC) 경유 send·get · 익명 `UNAUTHENTICATED` |
| G41-14~18 | upgrade(`tls.secretName`) → Ingress `GRPCS` · 평문 클라이언트 `UNAVAILABLE` · CA 신뢰 + 서비스 DNS 이름으로 send·get · Ingress(GRPCS) 경유 send · 로그 `(TLS)` 1·Traceback 0·읽기 전용 오류 0 |
| G41-19~20 | uninstall · 클러스터 삭제 (기본 kubeconfig sha1 `b88c119b…` 불변, scratch 정리) |
