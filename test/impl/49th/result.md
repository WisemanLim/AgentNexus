# impl 49th — 결과: 남은 Lv2 하드닝 (Epic 49 · D19-②)

판정: **PASS** — pytest C49-01~06 **6/6** · 회귀 **84/84**.

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest r1 (C49) | **6/6** | `logs/pytest-r1.log` |
| pytest r2 (A2A·gRPC·REST·MCP·mTLS·설계 보완·검토 수정·동의 회귀) | **84/84** | `logs/pytest-r2.log` |

## 결과 요약

- gRPC: 4 MiB 를 넘는 요청은 서버가 `RESOURCE_EXHAUSTED` 로 끊는다. 상한이 없을 때는 gRPC 기본값(수신 4 MiB)에 기대고 있었고, 송신·동시 스트림·keepalive 는 명시값이 없었다.
- 원격 grpcs: 시스템 신뢰 저장소만으로는 사설 CA 서버에 연결 실패, 에이전트의 `ca_secret` 을 지정하면 성공 — HTTP 바인딩과 같은 TLS 설정을 gRPC 도 따른다.
- stdio: `npx` 를 통째로 허용하던 형태 대신 `npx -y @mcp/server-fs@0.6.2 /data` 처럼 전체 명령으로 좁힐 수 있다. 다른 인자(`/etc`)는 거절.
- 푸시: 받는 쪽이 잠시 5xx 를 내도 재시도로 전달. 영구 4xx·차단 대상은 재시도하지 않는다(실패는 경고 로그).

## 문서

- README KO/EN `MCP_STDIO_ALLOWED_COMMANDS` 행에 항목 규칙(실행 파일 이름 / 전체 명령)을 적었다. 새 env 키 없음.
