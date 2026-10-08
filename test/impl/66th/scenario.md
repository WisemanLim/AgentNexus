# impl 66th — 남은 기록 항목 2 (Epic 66 · D22-⑥)

## 범위 (사용자 '나머지 추가' — 3차 검토 `.review/round3/VERIFY.md` 의 '기록' 중 결함·하드닝)

| # | 출처 | 내용 |
|---|------|------|
| L19 | Lv2 a2a_grpc | `--probe` 가 늘 127.0.0.1 로 — 특정 주소로만 수신하면 그 주소로(와일드카드 `0.0.0.0`·`[::]` 는 루프백). TLS 의 gRPC 헬스(인증서 신뢰 설정 필요)는 기록 유지 |
| L22 | Lv2 mcp_modern | listen 반납 — Valkey 면 스레드에 넘기고 기다리지 않음(끊길 때마다 이벤트 루프 최대 0.5초 정지 제거), 메모리 백엔드는 즉시 |
| L23 | Lv2 identity | 내장 IdP issuer 로 외부 IdP 연동 거부(`issuer_reserved`) · 연동 중지·삭제 모두 `sub=ext:{id}:*` 기준(삭제는 issuer 기준이었다 — 같은 issuer 의 다른 사용자까지 끊김) · LIKE 와일드카드 이스케이프(`idp_…` 의 `_`) · 하위 질의 UPDATE(IN 목록 상한 없음) |
| L28 | Lv2 INFRA | a2a-grpc TLS 인증서 볼륨 오버레이 예시 `deploy/compose/a2a-grpc-tls.example.yml`(compose/ 자동 포함 밖) · `certs/` 커밋 금지 |
| L29 | Lv2 INFRA | Helm a2a-grpc readiness(gRPC) `failureThreshold: 6` — DB 장애조치 순간 전 파드 동시 NotReady 완화(54th 결정의 readiness = api 까지는 유지) |
| L30 | Lv2 nexus | CLI 설정 디렉터리 0700(이미 있던 디렉터리도) · 파일 권한은 연 파일에 `fchmod` |
| L33 | Lv2 nexus | `bundle import` 구성 복원 기본 **끔** — `--restore-config` 를 줄 때만 운영 데이터 덮어쓰기. 3차 VERIFY 의 "명시 동의" 판정은 기본값이 켜짐이라 틀렸다(정정) |
| CR3-04 | 위임 결함 | 헬스 요청 서비스 이름 길이 protobuf varint 인코딩·해석 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C66-01 | 리슨 127.0.0.1·0.0.0.0 → 0 · 127.0.0.9(수신 없음) → 1 | `test_remaining_items2.py::test_probe_uses_listen_host` | pytest |
| C66-02 | Valkey 반납 0.1초 안에 돌아오고 스레드에서 실행 · 메모리 즉시 0 | `test_listen_release_does_not_block_loop` | pytest |
| C66-03 | 내장 issuer 400 · 중지 1명·삭제 1명 · `_`→`Z` 닮은 id 사용자 영향 없음 | `test_idp_builtin_issuer_and_session_scope` | pytest |
| C66-04 | 템플릿 `failureThreshold: 6` · 예시 볼륨·경로 · compose/ 에 없음 · `.gitignore certs/` | `test_grpc_readiness_threshold_and_tls_overlay` + `make helm-check` | pytest·helm |
| C66-05 | 0755 디렉터리·0644 파일 → 0700·0600 | `test_cli_config_dir_private` | pytest |
| C66-06 | 기본 드라이런에 복원 없음 · `--restore-config` 면 있음 | `test_bundle_import_restore_opt_in` | pytest |
| C66-07 | 0·9·127·300 바이트 왕복 · 300 → `AC 02` · 깨진 varint → "" | `test_health_request_varint` | pytest |
| 회귀 | C53-01·C53-03(실제 sub 형식으로 갱신)·CLI 번들 왕복(`--restore-config`)·gRPC·listen | `test_identity_boundaries.py`·`test_identity.py`·`test_cli*.py`·`test_deploy_hardening.py`·`test_multiworker.py`·`test_mcp_modern_ext.py`·`test_a2a_grpc.py`·`test_remaining_items.py`·`test_review3_fixes.py` | pytest |
