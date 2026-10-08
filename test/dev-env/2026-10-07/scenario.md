# dev-env 재검증 시나리오 — 2026-10-07 (Epic 50 · D19-③)

> 첫 검증(`../scenario.md`·`../result.md`, 2026-10-06)은 redis·호스트 포트 8000 시절이다. 그 뒤 바뀐 것: Valkey(redis 대체),
> api 호스트 포트 8800(`API_PORT` — 8000 은 이 호스트의 다른 프로젝트가 사용), a2a-grpc(8851)·mcp-servers·a2a-demo 서비스, 마이그레이션 0019.
> 첫 검증 파일은 그대로 두고 이 폴더에 다시 남긴다.

| id | 목적 / purpose | 명령 / command | 기대 / expected | 우선 |
|----|----------------|----------------|-----------------|------|
| ENV-01 | 도구·설정 점검 | `make preflight` | `Preflight OK` | P0 |
| ENV-02 | local 기동 | `make local-all` · `make local-ps` | api·a2a-grpc·mcp-servers·valkey healthy | P0 |
| ENV-03 | 헬스 체크 | `curl -fsS http://127.0.0.1:8800/health` | HTTP 200, `env=local`, `engine=sqlite` | P0 |
| ENV-04 | local DB | `make db-ping` | `sqlite OK` | P0 |
| ENV-05 | 테스트 진입점 | `make test` | exit 0 (전체 회귀) | P0 |
| ENV-06 | prod 기동 (postgres) | `make local-stop` → `make prod-all` · `make prod-ps` · `/health` | 6개 서비스 healthy, `env=prod`, `engine=postgres` | P1 |
| ENV-07 | prod DB | `make db-ping ENV=prod` | `postgres OK` | P1 |
| ENV-08 | 정리 | `make prod-stop` | 이 프로젝트 컨테이너 0 | P1 |
| ENV-09 | 복구 | `make local-all` · `/health` | `env=local` ok (작업 환경 원상태) | P1 |
