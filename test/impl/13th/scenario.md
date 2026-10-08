# impl 13th — Helm 차트 · 에어갭 번들 · NFR-P1 지연 (Epic 13 · FR-P02 · NFR-D1·P1) + 완료 게이트(prod 회귀)

## 범위

- `deploy/helm/agentnexus` — api(initContainer 마이그레이션·시드), 데모 참조 서버, 내장 PostgreSQL StatefulSet·Valkey·OPA(선택),
  ConfigMap/Secret(lookup 으로 서명·암호화 키 유지, `resource-policy: keep`), PVC, NetworkPolicy(edge/backend 분리), HPA·PDB,
  Ingress·Gateway API HTTPRoute, ServiceMonitor, helm test, values.schema.json, 설정 검증(fail 가드).
- 다중 파드 대비 앱 변경: PostgreSQL advisory lock 으로 마이그레이션·시드 직렬화, 데모 워크로드 시크릿 env 주입(`A2A_DEMO_CLIENT_SECRET`).
- 에어갭: `nexus bundle export --include <chart.tgz>` (이미지 docker save + 서명 구성 백업 + 동봉 파일 + SHA-256), `make helm-package`.
- NFR-P1: `test/test_perf.py`(in-process, 회귀 감시) · `deploy/k6/gateway.js`(prod 리허설 실측).

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| E13-01 | `helm lint --strict` (기본·에어갭·HA·게이트웨이 단독 values) | `make helm-check` | helm |
| E13-02 | `helm template` 4 시나리오 렌더 + 오프라인 정합성(셀렉터·Service 포트·ConfigMap/Secret/PVC 참조·태그 고정·비루트·프로브·ENTRYPOINT 유지) | `deploy/helm/check-rendered.py` | helm + python |
| E13-03 | 잘못된 조합 거부: RWO+다중 복제 · 외부 DB 미지정 · 스키마 위반 · opa 엔진 without opa · redis without Valkey | `helm template --set …` 5종 | helm |
| E13-04 | 생성 시크릿 형식 (Fernet 32B urlsafe · RSA PEM · DATABASE_URL) | 렌더 결과 디코드 | helm + python |
| E13-05 | 차트 ↔ 앱 정합: appVersion · OPA 정책 사본 동기 · compose env 키 ⊂ 차트 ConfigMap/Secret | `test/test_deploy.py` | pytest |
| E13-06 | 동시 기동 3 프로세스의 마이그레이션·시드 직렬화 | `test_concurrent_migrate_and_seed_serialized` | pytest(PG) |
| E13-07 | 데모 워크로드 시크릿 env 주입 → client_credentials 발급 | `test_demo_workload_secret_from_env` | pytest |
| E13-08 | 번들 `--include` 동봉·체크섬·변조 탐지 | `test_bundle_export_import_roundtrip` | pytest |
| E13-09 | NFR-P1 in-process 회귀 감시 | `test/test_perf.py` | pytest |
| P13-01 ~ P13-11 | prod 리허설: 헬스(PG)·password grant 차단·PKCE·콘솔 번들(runtime 이미지)·템플릿 실행·교차 컨테이너 trace·감사 체인·k6 임계값·MCP 순수 추가 지연 ≤ 20ms·에어갭 번들(차트 동봉)·읽기 전용 루트 FS | `smoke-prod.sh` | live |
| G-01 | 완료 게이트: `make test` · `make test-pg` · `make prod-build` → `db-migrate ENV=prod` → `db-ping ENV=prod` → 스모크 → `prod-stop` | 로그 | make |

## 실행

```sh
make helm-check
make test && make test-pg
make local-stop && make prod-build && make db-migrate ENV=prod && make db-ping ENV=prod
sh test/impl/13th/smoke-prod.sh && make prod-stop
```

## 환경 가정

- 실제 클러스터 설치는 하지 않는다(현재 kubectl 컨텍스트는 다른 프로젝트 클러스터). kubeconform 은 실행 시 인터넷에서 스키마를 받으므로
  쓰지 않고 오프라인 검사기로 대체.
- prod 리허설 인증은 전용 `rehearsal` 계정(admin 역할)에 실행 중 생성한 임시 비밀번호를 설정해 PKCE 로 토큰을 받고, 끝나면 비활성화한다.
  비밀번호는 출력·파일에 남기지 않는다. 기존 admin 계정과 `.env.prod`(CHANGE_ME)는 건드리지 않는다.
- k6 는 `grafana/k6:1.0.0` 컨테이너로 compose 네트워크 안에서 실행(호스트 설치 없음). 기본 한도(주체별 50 rps) 안에서 32 rps.
