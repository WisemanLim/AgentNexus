# impl 46th — 결과: 운영 HA 심화 (Epic 46 · D18-③)

판정: **PASS** — kind `cluster-db-ha.sh` **15/15** (3회차) · helm-check 4 시나리오(ha 에 존 분산) OK · pytest 회귀(SQLite) 통과.

| 회차 | 결과 | 원인·처리 |
|------|------|-----------|
| r1 (KEEP) | 11/13 — D46-09 장애조치 없음, D46-13 쓰기 실패 | primary **파드 삭제**는 정상 종료 경로: PostgreSQL smart shutdown 이 api 풀의 유휴 연결을 기다려 파드가 Terminating 에 머물고(CNPG 는 종료를 기다림) `pg-rw` 로의 새 연결만 시간 초과(`connect_timeout` 5초가 동작). 장애조치 시험이 되지 못함 → 실제 장애(primary 노드 정지)로 바꿈 — 로그 `logs/r1-failed/` |
| r2 (KEEP) | 13/13 | 노드 정지 → 55초 뒤 새 primary · 복구 66.8초 · 가용성 0.8588 — 로그 `logs/r2-keep/` |
| r3 | **15/15** + 정리 | 아래 |

## r3 결과 (`logs/cluster-db-ha-r3.log`, `logs/failover-events.log`)

| 항목 | 값 |
|------|-----|
| 존 | zone-a·b·c (워커 3) · PostgreSQL 3 인스턴스 노드 3개 · api 2 파드 다른 존 |
| 장애 | primary `pg-1` 이 있는 `worker2` 정지 |
| 장애조치 | **56초** 뒤 `pg-2` 가 primary |
| `/health`(DB SELECT 1 포함) | 정지 후 첫 실패 → 마지막 실패 **65.5초** · 180초 창 가용성 **0.8697 (227/261)** · 끝 20회 연속 200 |
| 쓰기 | 장애조치 뒤 앱 세션 쓰기 성공 · `/health` db ok |
| 정리 | uninstall · 클러스터 삭제 · 기본 kubeconfig sha1 `b88c119b…` 불변 · CNPG 매니페스트·kubeconfig scratch 삭제 |

## 남은 항목

- 복구 시간은 대부분 장애 감지(노드 NotReady)와 CNPG 의 primary 재선출 대기 — 운영 클러스터는 노드 감지·`failoverDelay` 설정과 매니지드 DB 의 SLA 로 정한다.
- 정상 종료(primary 파드 삭제·롤링 업그레이드)는 smart shutdown 대기 때문에 느려질 수 있다 — CNPG `smartShutdownTimeout` 조정 또는 `kubectl cnpg promote` 로 사전 전환.
