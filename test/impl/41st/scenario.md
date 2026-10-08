# impl 41st — a2a-grpc Helm·TLS (Epic 41 · D16-② 남은 항목 · D17-②)

## 범위

| 항목 | 구현 |
|------|------|
| gRPC 수신 TLS | `A2A_GRPC_TLS_CERT`·`A2A_GRPC_TLS_KEY`(PEM 경로)가 모두 있으면 `add_secure_port`, 없으면 평문(앞단 TLS 종료) — 시작 로그에 `(TLS)`/`(평문)` · env 3곳(local·prod·example, 기본 비움) |
| Helm `a2aGrpc` | `enabled`·`replicas`·`publicUrl`(카드 광고, 켰을 때만)·`tls.secretName`(파드 직접 TLS — Secret 을 `/tls` 에 마운트, ConfigMap 이 경로를 채움)·`ingress`(ingress-nginx `backend-protocol: GRPC`, 파드 TLS 면 `GRPCS`) · Deployment(진입점 유지 args, TCP 프로브, 읽기 전용 루트 + `/tmp` emptyDir)·Service(`appProtocol: grpc`) |
| NetworkPolicy | api 수신 `from` 에 a2a-grpc 파드를 자동 추가(`ingressFrom` 이 있을 때) · a2a-grpc 수신은 `ingressFrom` 으로 제한 |
| CI 시나리오 | `ci/ha-values.yaml` 에 a2aGrpc(복제 2·파드 TLS·Ingress) — `make helm-check` 렌더 정합 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C41-01 | 파일 경로 설정으로 TLS 수신 · 신뢰한 클라이언트 COMPLETED · 평문 클라이언트 UNAVAILABLE | `test_a2a_grpc.py::test_grpc_inbound_tls` | pytest |
| C41-02 | 차트 env 정합(`A2A_GRPC_*`) · 렌더 정합 4 시나리오 | `test_deploy.py` · `make helm-check` | pytest · helm |
| G41-01~05 | 전용 kind(2노드)·Calico·ingress-nginx·cert-manager·CA | `cluster-grpc.sh` | kind |
| G41-06~09 | helm install · a2a-grpc Ready + Ingress 인증서 · api NetworkPolicy from = ingress-nginx·데모 파드·a2a-grpc · 카드 GRPC `grpcs://grpc.agentnexus.local:18443` | 〃 | kind |
| G41-10~13 | ① 파드 평문: 클러스터 안 send·get · 릴리스 밖 파드 → api 차단 · Ingress TLS 경유 send·get · 익명 UNAUTHENTICATED | 〃 | kind |
| G41-14~18 | ② 파드 TLS: upgrade → Ingress `GRPCS` · 평문 클라이언트 UNAVAILABLE · CA 신뢰 클라이언트 send·get · Ingress(GRPCS) 경유 send · 로그 `(TLS)`·Traceback 0·읽기 전용 오류 0 | 〃 | kind |
| G41-19~20 | uninstall · 클러스터 삭제 | 〃 | kind |

## 환경 가정

- 원격(아웃바운드) `grpcs://` 호출은 시스템 신뢰 저장소를 쓴다 — 사설 CA 를 쓰는 원격 gRPC 에이전트는 이미지 신뢰 저장소에 CA 를 넣어야 한다(범위 밖).
- 시험 values 의 `ingressFrom` 은 Ingress 컨트롤러 + api·데모 파드 — a2a-grpc 는 일부러 넣지 않아 차트의 자동 규칙을 검증한다.
