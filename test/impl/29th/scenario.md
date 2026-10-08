# impl 29th — MCP 2026-07-28 확장 (Epic 29 · D14-② · NFR-INT1 · FR-G09·A03)

## 범위

| 항목 | 구현 |
|------|------|
| 업스트림 modern 호출 | 서버별 `protocol` = `auto`(기본, 도구 갱신 때 `server/discover` 로 발견) · `legacy`(initialize·세션) · `2026-07-28`(고정). modern 업스트림은 세션 없이 요청마다 `_meta`(버전·능력·clientInfo)·헤더(`MCP-Protocol-Version`·`Mcp-Method`·`Mcp-Name`·`Mcp-Param-*`)로 호출. `-32022` 를 받으면 legacy 로 내려가 기록 (migration 0015: `mcp_servers.protocol`·`upstream_protocol`) |
| 업스트림 MRTR | 업스트림 `InputRequiredResult` → 각 `inputRequests` 를 레거시 서버 요청과 같은 `mcp_consent.handle`(하류 중계 또는 Host 동의)로 답해 `inputResponses`·`requestState` 로 재호출(최대 8왕복). 동의 거부·만료는 도구 오류 결과(레거시와 같은 모양) |
| x-mcp-header | 하류 modern `tools/call` 의 `Mcp-Param-*` ↔ 인자 일치(누락·불일치·인자 없는 헤더·잘못된 Base64 → 400 `-32020`, SDK `mcp.shared.inbound` 규칙) · 라우팅 헤더 중복 → `-32020` · 업스트림 호출에는 도구 스키마의 `x-mcp-header` 를 미러링 (데모 `dynatrace.query_metrics.service` → `Mcp-Param-Service`) |
| subscriptions/listen | SSE 필수(아니면 406) · 첫 메시지 `notifications/subscriptions/acknowledged {_meta.subscriptionId = 요청 id, notifications = 승인한 부분집합}` (resourceSubscriptions 는 승인 안 함 — `resources.subscribe=false`) · 주체 기준 목록 지문(도구 = 정책 적용 목록)을 5초마다 비교해 `notifications/{tools,prompts,resources}/list_changed` (subscriptionId 태그) · 900초 뒤 `{id, result:{resultType:"complete", _meta:{subscriptionId}}}` 정상 종료 · 토큰 만료·폐기 → 오류 응답으로 종료 · 주체당 동시 5개(429) · 감사 `mcp.subscribe` · discover `listChanged: true` |
| progress 중계 | modern: `_meta.progressToken` + SSE 수락 → 요청 단위 SSE(`notifications/progress` … → 결과/InputRequiredResult). MRTR 재시도가 같은 워커로 오면 그 요청 토큰으로 이어서 중계 · 레거시: 같은 조건이면 기존 중계 SSE 에 progress 를 함께 · 증가하지 않는 값은 버린다(가드 패턴 업스트림의 재실행) |
| 데모 업스트림 | internal-orders `classify_orders`·`request_return` 이 dual-era(2026-07-28 은 MRTR 가드 패턴) · `classify_orders` 진행률 1→3 |
| 화면 | 서버 상세 「업스트림 프로토콜」(발견 버전·호출 방식·선택) · 적용 전 시험 「프로토콜」 점검 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C29-01 | 갱신 → `protocol: 2026-07-28` · 레거시 하류 호출이 modern 업스트림으로(업스트림 세션 0, 결과에 modern 필드 없음) · 적용 전 시험 프로토콜 점검 · 강제 legacy → 세션 생성 · 잘못된 값 422 | `test_modern_upstream_detected_and_stateless` | pytest |
| C29-02 | x-mcp-header 노출 · Mcp-Param 누락·불일치·여분 -32020 · 일치 200(업스트림 미러링) · Base64 · 중복 헤더 · 레거시 하류 미러링 | `test_x_mcp_header_mirroring_and_validation` | pytest |
| C29-03 | listen 승인 부분집합·subscriptionId · 도구 규칙 변경 → tools/list_changed · SSE 아님 406 · 필터 없음 -32602 · discover listChanged | `test_listen_ack_and_tools_list_changed` | pytest |
| C29-04 | 정상 종료 응답 · 토큰 폐기 → 오류 종료 · 동시 구독 한도 429 · 카운터 복원 | `test_listen_graceful_close_cap_and_revoked_token` | pytest |
| C29-05 | modern progress SSE (p1: 1/3 → input_required, p2: 증가 순 · 마지막 3 "완료" → complete) | `test_modern_progress_relay_with_mrtr` | pytest |
| C29-06 | 레거시 SSE progress + sampling 중계 · 값 [1,2,3] · 업스트림 세션 0 | `test_legacy_progress_relay_through_modern_upstream` | pytest |
| R1 | 레거시 회귀 0 — 기존 MCP·동의·MRTR 시험이 modern 업스트림으로 그대로 통과 | 전체 pytest · e2e consent/review/modern/gaps | pytest·Playwright |
| E29-01·02 | 서버 상세 프로토콜 카드 전환 · 적용 전 시험 프로토콜 항목 | `web/e2e/modern-ext.spec.ts` | Playwright |
| P29-01~13 | prod(2 workers·PostgreSQL): 발견·세션 0·Mcp-Param·미러링·listen 승인/알림(DB 경유)·progress·MRTR 완료·강제 legacy·감사 체인 | `smoke-prod.sh` | live |

## 환경 가정

- 데모 업스트림(FastMCP 4.0.11)은 dual-era — 기본 `auto` 에서 modern 으로 호출된다. 레거시 전용 업스트림은 discover 실패 → legacy.
- progress 는 업스트림 호출이 도는 워커에서만 중계된다. MRTR 재시도가 다른 워커로 가면 그 요청에는 progress 없이 결과만 온다(결과·입력 응답은 DB 로 공유).
- listen 은 폴링(5초) 지문 비교 — 정책 변경·업스트림 목록 변경(도구 TTL 300초 갱신)을 같은 경로로 잡는다. `resourceSubscriptions`(리소스 내용 변경)는 범위 밖.
- 하류 hosted-openapi·stdio 업스트림의 progress 중계는 범위 밖(HTTP 업스트림만).
