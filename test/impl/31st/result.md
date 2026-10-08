# 시험 결과 / Test Result — impl 31st (Epic 31 Helm 실제 설치 검증)

- 일자 Date: 2026-10-07
- 대상 Target: 전용 kind 클러스터 `agentnexus-e31` (kind v0.32.0 · Kubernetes v1.36.1 · 단일 노드) · Helm v4.2.0 · 차트 `deploy/helm/agentnexus` 기본 values · 이미지 `wise-agentnexus-api:prod`(이 차수 빌드, migration 0016)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 18, 통과 Pass: 18, 실패 Fail: 0, 최종 Verdict: **PASS** (round 4)
- install --wait → 파드 5종 Ready(api·mcp-servers·a2a-demo·postgresql·valkey) · PVC Bound · NetworkPolicy · `helm test` 통과 → port-forward 스모크: /health ok·db ok · PKCE 로그인 · jira 갱신 `healthy:2026-07-28`(클러스터 안에서도 modern 업스트림 발견) · 도구 호출 결과 · 모델 응답 · 감사 체인 유효 · api 로그 Traceback 0 → uninstall → 파드 0 → 클러스터 삭제
- 기본 `~/.kube/config` sha1 `b88c119b…` — 실행 전후 같음(다른 클러스터 컨텍스트 미사용). 전용 kubeconfig 는 scratch 에 두었다가 삭제

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix |
|-------|----------|------------|----------|
| 1 | api·데모 파드 `ImagePullBackOff` (설치 10분 대기 초과) | `kind load docker-image` 가 `--all-platforms` 로 가져오다 실패 — Docker Desktop containerd 저장소의 다중 플랫폼 인덱스가 로컬에 없는 플랫폼 계층을 가리킴 (postgres·valkey 는 노드가 레지스트리에서 받아 기동) | 노드 플랫폼 하나만 `docker save --platform` → 노드 `ctr images import` (스크립트 반영, 레지스트리 받기 없음) |
| 2 | H31-10 로그인 확인 실패 (시험 결함) | `/api/v1/me` 응답에 `username` 이 없음 — 다른 인증 확인(갱신·호출)은 통과 | `.sub == usr_rehearsal` 로 확인 |
| 3 | H31-17 uninstall 직후 파드 5개 (시험 결함) | `helm uninstall --wait` 반환 시점에 파드가 Terminating 으로 남아 있음 | 사라질 때까지 최대 120초 폴링 |

## 첨부 / Logs

- `logs/helm-kind-r4.log`(최종) · r2·r3 · `logs/r1-failed/` · `helm-install.log` · `helm-test.log` · `resources.txt`(설치 직후 리소스) · `api.log` · `kind-*.log`
- 완료 게이트(5차) 로그: `logs/gate/`
