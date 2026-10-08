# impl 60th — 남은 기록 항목 (Epic 60 · D21-⑤ · 사용자 요청 "나머지 추가")

2차 검토(`.review/round2/VERIFY.md`·`INDEX.md`)에서 "기록" 으로 남긴 것 중 **코드로 확인한 결함·하드닝**만. 각 항목은 이번 차수에 코드 경로를 다시 확인했다.

| # | 항목 (출처) | 확인 | 처리 |
|---|-------------|------|------|
| C60-01 | 신뢰 어휘 (governance·INDEX) | 승인 신뢰는 official·partner·community·external 인데 `== "trusted"` 비교 — 승인 경로에서 readOnlyHint 가 위험을 낮추지 못함 | official·partner(+기존 "trusted")면 신뢰 |
| C60-02 | `/v0/servers` 전체 적재 (M20) | 익명 발견 API 가 `limit=10_000` 으로 전부 읽고 자름 | 거름 조건이 없으면 SQL count + offset/limit |
| C60-03 | mTLS 필수인데 verify:false 허용 (tls Lv2) | 필수 정책에서도 서버 검증 끄기 가능 | 필수 정책이면 거부 |
| C60-04 | 원격 에이전트 토큰 캐시 (a2a 1차 이월) | 인증을 바꿔도 이전 자격으로 받은 토큰을 만료까지 사용 | PATCH 시 그 에이전트의 캐시 토큰 제거(이 워커) |
| C60-05 | OpenAPI 생성 시크릿 덮어쓰기 (secrets Lv2) | `mcp/{ns}/backend` 를 namespace 충돌 확인보다 먼저 씀 | 쓰기 전에 409 |
| C60-06 | CLI 파일 쓰고 나서 chmod (cli Lv2) | 그 사이 다른 사용자가 읽을 수 있음 | 처음부터 0600 |
| C60-07 | OTLP 수신 상한·형식 (observe Lv2) | 상한 없음, 형식 오류 500 | 8 MiB 413 · 400 |
| C60-08 | A2A 사용량 상태 (a2a 1차 이월) | A2AError 밖의 실패는 ok 로 기록 | error |
| C60-09 | 클라이언트 웹훅 토큰 평문 저장 (a2a 1차 이월) | push_config.token 평문 | 암호화 저장(`token_enc`), 이전 평문 행 호환 |
| C60-10 | 실행기 예외 원문 노출 (a2a_server Lv2) | 원격 클라이언트에 `{예외}: {메시지}` | 참조 id, 원문은 로그 |

## 이번 범위에서 뺀 것 (설계 결정·위험 대비 이득이 작은 것)

- 개발자의 전체 Task 열람·세션 기억/위임·사용자 이름 잠금·`app_env` 기본값·CGNAT 대역 — 문서화된 결정 유지.
- MCP resources/prompts 정책 — 리소스용 정책 모델이 없고(서버 단위 통제), 새 정책 모델은 기능 추가.
- SQLite WAL — 읽기 전용 마운트 컨테이너가 같은 볼륨을 쓰면 `-shm` 쓰기가 필요해 위험.
- 시크릿 생성 API 덮어쓰기 — 이미 409(`exists`)로 막혀 있음(재확인).
