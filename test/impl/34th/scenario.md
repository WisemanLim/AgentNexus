# impl 34th — 운영형 클러스터 검증 (Epic 34 · D15-③ · NFR-D1·S1·SEC1 · FR-G00)

## 범위

| 항목 | 내용 |
|------|------|
| 클러스터 | 전용 kind `agentnexus-e34` — control-plane 1 + worker 2 (Kubernetes v1.36.1), **별도 kubeconfig**(scratch). 기본 CNI 끄고 **Calico v3.30.3**(NetworkPolicy 집행) · pod CIDR 10.244.0.0/16 · **ingress-nginx**(kind 매니페스트, 호스트 127.0.0.1:18880) · **metrics-server**(HPA 지표, kind kubelet 자체 서명이라 `--kubelet-insecure-tls`) |
| 이미지 | 앱 이미지 3종(`wise-agentnexus-api:prod`·postgres·valkey)은 노드마다 단일 플랫폼 `docker save` → `ctr import` (레지스트리 받기 없음). Calico·ingress-nginx·metrics-server 이미지와 매니페스트는 받는다(D15-③ 승인 범위) |
| 설치 값 | ingress(nginx, `agentnexus.local`) · HPA min 2 / max 4 / CPU 50% · PDB minAvailable 1 · persistence 끔(다중 복제 + RWO 는 차트가 거부, kind 에 RWX 없음) · NetworkPolicy `ingressFrom` = ingress-nginx 네임스페이스 + 릴리스 파드 |
| 확인 | 노드·Calico Ready · 애드온 · install --wait · api 2 복제 · ingress 경유 /health · PKCE 로그인 · MCP 도구 호출 · **NetworkPolicy 실제 차단**(릴리스 라벨 없는 파드 → postgres·mcp-servers·valkey·api 모두 차단, api 파드 → backend 허용) · **PDB**(Eviction API — 첫 퇴거 허용, 대체 준비 전 두 번째 퇴거 429, 그동안 ingress /health ≥ 99%) · **HPA**(지표 수신 → ingress 경유 부하로 desiredReplicas > 2) · api 로그 Traceback 0 |
| 정리 | `helm uninstall --wait` → `kind delete cluster` |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 방식 |
|---|-----------|------|
| H34-00~05 | Calico 풀 = pod CIDR · 전용 컨텍스트 · 노드 3 Ready · Calico 3/3 · 이미지 9(3×3) · ingress·metrics-server Ready | `cluster-kind.sh` |
| H34-06~10 | install · api 복제 2 · ingress /health ok·db ok · 로그인 · 도구 호출 healthy·결과 | 〃 |
| H34-11·12 | 라벨 없는 파드 → 4 대상 blocked · api → mcp-servers·postgres open | 〃 |
| H34-13~15 | PDB 첫 퇴거 허용 · 두 번째 거부 · 퇴거 중 가용성 ≥ 99% | 〃 |
| H34-16·17 | HPA 지표 · 부하 시 확장 > 2 | 〃 |
| H34-18~20 | Traceback 0 · uninstall · 클러스터 삭제 | 〃 |
| K | 기본 kubeconfig sha 무변경 | 수동 기록 |

## 환경 가정

- Docker Desktop 한 대 위의 노드 3개 — 노드 장애·존 분산은 다루지 않는다(운영 클러스터 몫).
- 관리형 DB·외부 Valkey·TLS(Ingress 인증서)·cert-manager 는 범위 밖 — 내장 StatefulSet·평문 HTTP(로컬 호스트 전용).
- HPA 부하는 가벼운 공개 경로(agent-card)로 CPU 를 올린다 — 처리량 측정이 아니라 확장 동작 확인.
