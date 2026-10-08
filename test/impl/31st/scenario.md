# impl 31st — Helm 실제 설치 검증 (Epic 31 · D14-④ · Q7 · NFR-D1·S1)

## 범위

| 항목 | 내용 |
|------|------|
| 클러스터 | 전용 kind `agentnexus-e31` (Kubernetes v1.36.1, 단일 노드) — **별도 kubeconfig**(작업 scratch 경로). 기본 `~/.kube/config`·다른 클러스터(kind-secuardian-it)는 쓰지도 바꾸지도 않는다(전후 sha 비교) |
| 이미지 | `make prod-build` 로 만든 `wise-agentnexus-api:prod`(api·mcp-servers·a2a-demo 공용) + `postgres:17-alpine` + `valkey/valkey:8-alpine` — 노드 플랫폼 하나만 `docker save` → 노드 containerd 로 가져오기(레지스트리 받기 없음) |
| 설치 | `helm install agentnexus deploy/helm/agentnexus --wait` (기본 values + `publicBaseUrl=http://127.0.0.1:18800`·PVC 1Gi·api 요청 축소). 시크릿은 차트가 클러스터 안에서 생성(출력·저장 안 함) — 클러스터 삭제와 함께 사라진다 |
| 확인 | 파드 Ready(api·mcp-servers·a2a-demo·postgresql·valkey) · PVC Bound · NetworkPolicy 생성 · `helm test`(health·agent card) · port-forward 로 /health · PKCE 로그인 · MCP 갱신(2026-07-28 발견)·도구 호출 · 모델 호출 · 감사 체인 · api 로그 Traceback 0 |
| 정리 | `helm uninstall --wait` → 릴리스 파드 0 → `kind delete cluster` → 클러스터 목록에 없음 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 방식 |
|---|-----------|------|
| H31-01 | 전용 kubeconfig 의 current-context = `kind-agentnexus-e31` | `helm-kind.sh` |
| H31-02 | 이미지 3종 노드 적재 | 〃 |
| H31-03~08 | helm install --wait 성공 · 파드 5종 Ready · PVC Bound · NetworkPolicy · helm test | 〃 |
| H31-09~15 | /health ok·db ok · 로그인 · jira 갱신 healthy:2026-07-28 · 도구 호출 결과 · 모델 응답 · 감사 체인 · Traceback 0 | 〃 |
| H31-16~18 | uninstall · 릴리스 파드 0 · 클러스터 삭제 | 〃 |
| K | 기본 kubeconfig 변경 없음 (전후 sha) | 수동 기록 |

## 환경 가정

- kind 기본 CNI(kindnet)는 NetworkPolicy 를 집행하지 않는다 — 객체 생성만 확인(집행 시험은 Calico/Cilium 클러스터 몫).
- Ingress·HTTPRoute·HPA·ServiceMonitor 는 기본값(off) — 템플릿 정합성은 `make helm-check` 가 맡는다.
- kind 노드 이미지(kindest/node)는 클러스터 생성 때 받는다(로컬에 없으면). 앱 이미지는 받지 않는다.
