# impl 54th — 운영 배포 보강 (Epic 54 · D20-③)

## 범위

| 항목 | 구현 |
|------|------|
| a2a-grpc 헬스 | `grpc.health.v1.Health/Check` 를 생성 코드·추가 의존성 없이 바이트로 구현 — 기본(liveness)은 프로세스 응답, `readiness` 는 위임 대상 api `/health` 200 일 때만 SERVING. 컨테이너 검사 `python -m app.grpc_server --probe [readiness]`(TLS 수신이면 TCP) |
| Helm | TLS 수신이 아니면 네이티브 gRPC 프로브(readiness = `service: readiness`), TLS 면 TCP · `a2aGrpc.topologySpreadConstraints` · 복제 2개 이상이면 a2a-grpc PDB(minAvailable 1) · api·데모 파드에 `checksum/secret`(시크릿 교체 → 재기동) |
| compose | 공통 a2a-grpc 헬스체크를 probe 로 · prod a2a-grpc 는 `.env.prod` 전체 대신 `APP_ENV`·`LOG_LEVEL`·`A2A_GRPC_TLS_*` 만(엣지 노출 서비스에 서명 키·DB 비밀번호가 없게) |
| 이미지·잠금 | e2e 베이스(Playwright) digest 고정 · `PNPM_FROZEN=1`/`NPM_LOCKED=1` 인데 잠금 파일이 없으면 빌드 실패(고정 안 된 설치로 넘어가지 않음) |
| 번들 반입 | 서명 검증이 기본 — `cosign.pub` 로 `manifest.json.sig`(파일 해시 전체) 검증, `*.tar.sig` 가 있으면 함께. 생략은 `--allow-unsigned`(경고) |
| 설치 | `--cosign-key` 로 이미지 서명을 검증했으면 `up -d`(그 이미지 그대로), 미검증(`--allow-unsigned`)만 `--build` |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C54-01 | 기본 SERVING · readiness 는 위임 대상 200 이면 SERVING, 503 이면 NOT_SERVING | `test_deploy_hardening.py::test_grpc_health_service` | pytest |
| C54-02 | prod a2a-grpc env_file 없음·키 4개 · 다른 서비스는 그대로 · 헬스체크 = probe readiness | `test_compose_a2a_grpc_minimal_env_and_health` | pytest |
| C54-03 | 외부 베이스 이미지 전부 digest · 잠금 파일 필수 | `test_dockerfile_pins_and_lockfiles` | pytest |
| C54-04 | 키 없으면 반입 중단 · 위조 매니페스트 서명 중단(적재 0) · 맞는 서명이면 적재 · `--allow-unsigned` 경고 | `test_bundle_import_verifies_by_default` | pytest |
| C54-05 | 서명 검증 설치 계획은 `up -d`, 미검증은 `up -d --build` | `test_install_runs_verified_images` | pytest |
| C54-06 | 차트 — gRPC 프로브(TLS 아니면)·TCP(TLS)·`checksum/secret` · 불변식 음성 확인 | `make helm-check` | helm |
| L54-01 | local compose — a2a-grpc healthy(probe) · api 정지 시 readiness 1·liveness 0 · 복구 뒤 0 | `logs/local-probe.log` | live |
| L54-02 | web·e2e 이미지 빌드(digest 해석) | `logs/build-web.log`·`build-e2e.log` | build |
