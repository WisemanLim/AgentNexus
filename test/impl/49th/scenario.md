# impl 49th — 남은 Lv2 하드닝 (Epic 49 · D19-② · 2차 검토 이월분)

## 범위

| 항목 | 구현 |
|------|------|
| gRPC 수신 상한 | `a2a_grpc.SERVER_OPTIONS` — 수신 4 MiB · 송신 16 MiB · 동시 스트림 100 · keepalive 60s/20s · 최소 ping 간격 30s · 유휴 연결 15분 · `MAX_CONCURRENT_RPCS` 256 |
| 원격 grpcs 신뢰 | `tls.grpc_credentials` — 에이전트 `auth.tls.ca_secret`(사설 CA)·클라이언트 인증서(mTLS 정책)를 gRPC 채널 자격으로. `a2a_gateway.tls_kwargs` 가 `grpcs://` 에 `grpc_credentials` 를 넘기고 단건(`_rpc`)·스트림(`_stream`) 모두 사용 |
| stdio 허용 목록 | `MCP_STDIO_ALLOWED_COMMANDS` — 공백 없는 항목 = 실행 파일 이름, 공백 있는 항목 = 명령+인자 전체 일치(shlex) |
| hosted 클라이언트 | 설정 변경·캐시 비우기 때 이전 hosted MCP 클라이언트를 `aclose` (`_HOSTED_CLIENTS`·`_close_hosted`) |
| 업스트림 세션 | (서버, 주체) 업스트림 MCP 세션 — 30분 유휴면 다시 initialize, 최대 4096개(가장 오래 쓰지 않은 것부터 제거) |
| 푸시 전달 | `http.post_with_retry` — 5xx·429·전송 오류는 지수 백오프로 3회까지, 그 밖의 4xx·차단 대상은 바로 포기. `a2a_server._push`·게이트웨이 푸시 중계가 사용 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C49-01 | 5 MB 메시지 → `RESOURCE_EXHAUSTED`, 보통 메시지는 정상 완료 · 옵션 값 확인 | `test_lv2_hardening.py::test_grpc_server_limits` | pytest |
| C49-02 | 사설 CA grpcs 에이전트 — CA 없으면 실패, `auth.tls.ca_secret` 지정 뒤 성공 | `test_outbound_grpcs_private_ca` | pytest |
| C49-03 | `uvx` 는 인자 자유 · 전체 명령 항목은 인자까지 같아야 허용 · 빈 목록 = 금지 | `test_stdio_allowlist_full_command` | pytest |
| C49-04 | 하나 닫기 → 그 클라이언트만 `aclose` · 캐시 비우기 → 나머지도 | `test_hosted_clients_closed` | pytest |
| C49-05 | 유휴 시간이 지나면 새 initialize(세션 ID 바뀜) · 그 전에는 재사용 | `test_upstream_session_idle_reinit` | pytest |
| C49-06 | 503 두 번 뒤 200 → 성공(3회) · 400 → 1회로 포기 · 메타데이터 주소 → 시도 없음 | `test_post_with_retry` | pytest |
| 회귀 | A2A(JSONRPC·REST·gRPC)·MCP·mTLS·설계 보완·검토 수정·동의 | `test_a2a.py`·`test_a2a_grpc.py`·`test_a2a_rest.py`·`test_mcp.py`·`test_mtls.py`·`test_design_gaps.py`·`test_review_fixes.py`·`test_mcp_consent.py` | pytest |
| P49 | prod(PostgreSQL·2 workers) 회귀 — 완료 게이트 리허설에서 이전 차수 스모크로 | 게이트 | live |
