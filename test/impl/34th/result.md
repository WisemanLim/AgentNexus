# 시험 결과 / Test Result — impl 34th (Epic 34 운영형 클러스터 검증)

- 일자 Date: 2026-10-07
- 대상 Target: 전용 kind `agentnexus-e34` (control-plane 1 + worker 2 · Kubernetes v1.36.1) · Calico v3.30.3 · ingress-nginx(kind 매니페스트, main) · metrics-server(latest) · Helm v4.2.0 · 이미지 `wise-agentnexus-api:prod`(Epic 32~35 포함, migration 0018)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 21, 통과 Pass: 21, 실패 Fail: 0, 최종 Verdict: **PASS** (round 3)
- NetworkPolicy 집행: 릴리스 라벨 없는 파드 → postgres·mcp-servers·valkey·api **모두 차단**, api 파드 → mcp-servers·postgres 허용 (Calico)
- ingress-nginx 경유 /health · PKCE 로그인 · MCP 도구 호출 정상
- PDB: 첫 퇴거 허용 → 대체 준비 전 두 번째 퇴거 거부(429 disruption budget) · 그동안 ingress /health 120/120 (100%)
- HPA: metrics-server 지표 수신 → ingress 경유 부하로 2 → **4** 복제(최대)
- 정리: uninstall → 클러스터 삭제 · 기본 `~/.kube/config` sha1 `b88c119b…` 실행 전후 같음 · 전용 kubeconfig 삭제

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix |
|-------|----------|------------|----------|
| 1 | 파드 → API 서비스(10.96.0.1:443)·노드 IP 연결 시간 초과 → ingress admission 작업 실패 | kind 도커 네트워크 `192.168.144.0/20` 이 Calico 기본 IP 풀 `192.168.0.0/16` 과 겹쳐 노드 IP 를 파드 경로로 보냄 | pod CIDR 10.244.0.0/16 + 매니페스트 `CALICO_IPV4POOL_CIDR` 를 같은 값으로 |
| 2 | ingress 경유 요청 "Empty reply" | 현재 kind 용 ingress-nginx 매니페스트가 컨트롤러를 `ingress-ready` 노드에 묶지 않아 worker 에 배치 — 호스트 포트 매핑은 control-plane 에만 | 컨트롤러 Deployment 에 `nodeSelector: ingress-ready=true` 패치 |

## 첨부 / Logs

- `logs/cluster-kind-r3.log`(최종) · `kind-config.yaml` · `values-e34.yaml` · `resources.txt` · `hpa.txt` · `pdb-evict.log` · `pdb-health.log` · `helm-install.log` · `api.log` · `r1-failed/` · `r2-failed/`
- 매니페스트 원본(받은 뒤 삭제): Calico `projectcalico/calico/v3.30.3/manifests/calico.yaml` · ingress-nginx `deploy/static/provider/kind/deploy.yaml` · metrics-server `releases/latest/download/components.yaml`
