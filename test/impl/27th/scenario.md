# impl 27th — MCP 2026-07-28 수용 (Epic 27 · D13-④ · NFR-INT1·M1 · FR-A00·G09)

## 범위

| 항목 | 구현 |
|------|------|
| dual-era 게이트웨이 | `/mcp/{id}` — 본문 `_meta.io.modelcontextprotocol/protocolVersion` 이 있으면 modern(stateless), `initialize` 는 레거시(세션) 그대로 |
| 판정·검증 | 지원 안 하는 버전 400 `-32022 {supported, requested}` · `MCP-Protocol-Version`·`Mcp-Method`·`Mcp-Name`(Base64 `=?base64?…?=` 해석) 누락·불일치 400 `-32020` · `_meta` 없이 modern 헤더만 오면 `-32020` |
| 신규 메서드 | `server/discover` → `supportedVersions`(2026-07-28 · 2025-11-25 · 2025-06-18 · 2025-03-26)·`capabilities`·`_meta.serverInfo`·`instructions`·`ttlMs`·`cacheScope` |
| 결과 형식 | 모든 결과에 `resultType: "complete"`·`_meta.io.modelcontextprotocol/serverInfo` · 목록·읽기 결과에 `ttlMs`(60s)·`cacheScope: "private"`(정책으로 걸러진 목록) |
| 제거된 메서드 | `ping`·`logging/setLevel`·`resources/(un)subscribe`·`subscriptions/listen` → 404 `-32601` |
| MRTR | 업스트림(2025-11-25)의 `elicitation/create`·`sampling/createMessage` → `InputRequiredResult {inputRequests, requestState}` · 재시도의 `inputResponses` 를 대기 중인 업스트림 요청에 연결(mcp_relays — 워커 간 공유) → 최종 결과. 클라이언트가 능력을 선언하지 않으면 기존 Host 동의 경로 |
| requestState | Fernet(AEAD) — 상태 id·호출 주체·서버·메서드·인자 요약·만료(10분). 변조·다른 주체·다른 요청은 `-32602` |
| 표시 | `/api/v1/system/info` `protocols.mcp_supported` · 사이드바 "MCP 2025-11-25 · 2026-07-28" · Inspector 점검표 「MCP 2026-07-28 (server/discover)」 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C27-01 | discover·stateless 목록·호출·resultType·serverInfo·캐시 힌트·세션 헤더 없음·정책 동일 | `test_discover_and_stateless_calls` | pytest |
| C27-02 | -32020(헤더 누락·불일치·_meta 없음)·Base64 이름·-32022·ping 404·레거시 initialize 회귀 없음 | `test_header_validation_versions_and_removed_methods` | pytest |
| C27-03 | MRTR elicitation → input_required → 재시도 → complete | `test_mrtr_elicitation_bridge` | pytest |
| C27-04 | MRTR sampling — 클라이언트 모델 결과가 도구 결과에 반영 | `test_mrtr_sampling_bridge` | pytest |
| C27-05 | requestState 변조·다른 주체·다른 인자 → -32602 | `test_request_state_integrity` | pytest |
| E27-01 | Inspector 점검표에 2026-07-28 통과 · 사이드바 버전 | `web/e2e/modern.spec.ts` | Playwright |
| P27-01~11 | prod: discover·stateless·-32020·-32022·404·MRTR(두 워커)·변조 거부·레거시 initialize·info | `smoke-prod.sh` | live |
| R | 전체 회귀 + 완료 게이트 | logs | make |

## 환경 가정

- 업스트림 MCP 서버(데모·실서버)는 2025-11-25 로 호출한다 — 게이트웨이가 modern 클라이언트와 legacy 업스트림을 잇는다. 업스트림이 modern 인 경우의 클라이언트 측(게이트웨이→서버 stateless 호출)은 범위 밖.
- MRTR 진행 중 업스트림 호출은 첫 요청을 받은 워커에서 계속된다. 재시도가 다른 워커로 가도 응답·결과는 DB 로 만난다. 그 워커가 죽으면 재시도는 requestState 만 돌려받다가 만료된다.
- `subscriptions/listen`·Tasks 확장·`x-mcp-header`·`notifications/progress` 중계는 범위 밖(capabilities 에 listChanged:false).
