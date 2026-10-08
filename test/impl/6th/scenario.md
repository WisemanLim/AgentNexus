# 시나리오 / Scenario — impl 6th · Epic 6 MCP 경로 (FR-G09·G00·G07·I02·I03, FR-A03, FR-S00~S03, FR-W04)

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E6-01 | 참조 서버 등록·자동 발견·기본값 (FR-S00·S01·C01) | 4개 서버 refresh | healthy, 도구 목록, PRM 로 인가 서버 발견, 교환 정책(기본=읽기), 기본 규칙(쓰기=승인) | P0 |
| E6-02 | MCP 서버 측 인가 (FR-I03, RFC 9728) | 토큰 없음 / 읽기 스코프로 쓰기 / 타 aud | 401+resource_metadata, PRM, 403 insufficient_scope, 401 | P0 |
| E6-03 | 게이트웨이 세션·프로토콜 | initialize·버전 협상·notification·세션 탈취·405·DELETE·batch·미지원 메서드 | Mcp-Session-Id, 2025-06-18 유지/미지원→2025-11-25, 202, 404, 405, 종료 후 404, 400, -32601 | P0 |
| E6-04 | 최소 스코프 교환 (FR-I02, NFR-SEC1) | 읽기 도구 호출 | 교환 토큰 스코프 = issues:read 만, 사용량(tool) 기록 | P0 |
| E6-05 | 고위험 도구 승인 + 신원 전파 | transition_issue | -32001 approval_id → 승인 → 성공, 백엔드 응답 by=사용자, 쓰기 스코프 교환 | P0 |
| E6-06 | 미등록 도구 0건 통과 (NFR-SEC2) | 규칙 삭제 / 인자 위반 | tools/list 비노출, -32003 unregistered_tool, invalid_arguments | P0 |
| E6-07 | 집계·가상 서버 (FR-G01·G09) | /mcp/_all, 가상 서버 | 네임스페이스 도구, 집계 호출, 가상 서버 도구 제한·충돌 409 | P0 |
| E6-08 | 프롬프트·리소스 (FR-A03) | prompts/list·get, resources/templates·read | 정상 전달 | P1 |
| E6-09 | OpenAPI → MCP 호스팅 (FR-S02) + 즉시 사용 ≤ 60s | OpenAPI 3 문서 | healthy·도구 생성·호출, 쓰기 승인, < 60s | P0 |
| E6-10 | 운영자 즉시 등록·장애 서버 | 잘못된 경로 / 503 서버 | unhealthy 기록, -32002, 상태 상세 | P1 |
| E6-11 | 도구 응답 소독 (간접 인젝션) | 지시문·PII 포함 응답(JSON) | 지시문 제거·PII 마스킹·구조 보존·_meta 기록 | P0 |
| E6-12 | Inspector (SCR-F06) | inspect tools/call | 결과·지연 | P1 |
| E6-13 | Custom 템플릿 (FR-S03) | 생성 → 실행 → 게시 | tools/list, 읽기 스코프로 쓰기 403, server.json 스키마 적합 | P0 |
| E6-14 | PostgreSQL 회귀 | `make test-pg` | PASS | P0 |
| E6-15 | live: 우회 차단·공식 MCP 클라이언트 상호운용 | `smoke.sh` | 12/12 | P0 |
| E6-16 | prod 리허설 | runtime 이미지 + PG | 참조 서버 4 healthy, 규칙·교환 정책 생성 | P0 |
