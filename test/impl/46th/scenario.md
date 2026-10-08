# impl 46th — 운영 HA 심화 (Epic 46 · D18-③)

## 범위

| 항목 | 구현 |
|------|------|
| 존 분산 | 차트 `api.topologySpreadConstraints` — 항목마다 api 파드 labelSelector 를 차트가 채움 · `ci/ha-values.yaml` 에 zone `maxSkew: 1`·`DoNotSchedule` |
| DB 연결 | PostgreSQL 엔진: `connect_timeout=5`·TCP keepalive(idle 10s·interval 5s·count 3)·`pool_recycle=1800`·`pool_timeout=10`(+ 기존 `pool_pre_ping`) — 장애조치 뒤 죽은 연결을 빨리 버리고 새 primary 로 다시 붙는다 |
| 검증 클러스터 | 전용 kind(control-plane 1 + worker 3, 워커마다 `topology.kubernetes.io/zone` zone-a/b/c) · CloudNativePG v1.30.1(인스턴스 3, 존별 필수 anti-affinity) · 차트(api 2 복제·존 분산·`database.url`=`pg-rw` 서비스, 비밀번호는 CNPG Secret → scratch values → 설치 직후 삭제) |
| 장애 주입 | primary 가 있는 워커 노드를 `docker stop`(존 장애) — 시험 파드는 다른 워커에서 0.5초마다 `/health`(DB `SELECT 1` 포함) 180초 · api 파드는 not-ready/unreachable 20초 톨러레이션 |

## 수용 기준 → 시험 케이스 (`cluster-db-ha.sh`)

| # | 수용 기준 |
|---|-----------|
| D46-01~05 | 전용 컨텍스트 · 존 3개 · 이미지 적재 8 · CNPG 운영자 Ready · PostgreSQL 3 인스턴스 Ready·노드 3개에 분산 |
| D46-06~08 | helm install · api 2 파드 서로 다른 존 · `/health` ok:ok |
| D46-09 | primary 노드 정지 → 다른 인스턴스로 장애조치 |
| D46-10~12 | 정지 이후 복구 시간 ≤ 90초 · 180초 창 가용성 ≥ 0.7 · 끝 20회 연속 200 |
| D46-13 | 장애조치 뒤 `/health` ok + 앱 세션으로 쓰기 성공 |
| D46-14~15 | uninstall · 클러스터 삭제 |

## 환경 가정

- 단일 PostgreSQL 쓰기 지점의 장애조치는 운영자(CNPG)가 primary 를 다시 뽑는 시간만큼 쓰기가 멈춘다 — 측정값은 kind·Docker Desktop 기준(실제 클러스터는 노드 감지 설정에 따라 다르다).
- primary 파드 삭제(정상 종료)는 PostgreSQL smart shutdown 이 남은 연결을 기다려 장애조치가 늦게 시작된다(r1) — 운영에서는 CNPG `smartShutdownTimeout` 을 줄이거나 `kubectl cnpg promote`(전환)를 쓴다.
