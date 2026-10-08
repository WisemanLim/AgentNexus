# 시나리오 / Scenario — impl 7th · Epic 7 A2A 경로 (FR-G08·G01·A04·A05, FR-R00~R06, FR-L03·I04)

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E7-01 | Agent Card 조회·검증·캐시 (FR-R01·G08) | refresh, 게이트웨이 경유 카드 | 0.3+1.0 인터페이스, 스킬, 카드 URL=게이트웨이, 카탈로그 반영 | P0 |
| E7-02 | message/send (0.3) 위임 → Artifacts (시나리오 2) | "orders-api 장애 원인" | completed, 보고서·데이터 Artifact, 원격이 게이트웨이 경유(via=gateway) | P0 |
| E7-03 | 원격 에이전트 자체 신원·토큰 (FR-I04·L03) | 위 호출 | 사용자→a2a 교환, workload:a2a-demo→mcp:dynatrace 교환, 원격의 모델 사용량 | P0 |
| E7-04 | 1.0(SendMessage) 형식 | SendMessage | `{task}`·TASK_STATE_*·kind 없는 parts | P0 |
| E7-05 | input-required → 재개 (FR-R03 상태 머신) | 서비스 미지정 → 응답 | input-required → completed | P0 |
| E7-06 | 스트리밍 (FR-R04) | message/stream · SendStreamingMessage | task→status·artifact 이벤트→final, DB completed | P0 |
| E7-07 | 비차단·장기 Task 감시 (Temporal 대체) | blocking=false + watcher | 폴링으로 completed 회수 | P0 |
| E7-08 | 취소·푸시 수신/중계 (FR-R03·R04) | cancel 2회 / push 설정 | canceled, -32002, 게이트웨이 웹훅 수신 후 클라이언트 웹훅 중계(토큰 유지) | P0 |
| E7-09 | 신뢰 등급·미등록·미인증 | blocked / 없는 id / 토큰 없음 | 403 agent_blocked, 404, 401 | P0 |
| E7-10 | 스킬 기반 라우팅 (FR-G01) | /a2a/_skill/incident-analysis | 신뢰 등급 우선 선택, 결정 로그 | P1 |
| E7-11 | URL 등록·검증·버전 변환 (SCR-G02) | 잘못된 카드 / 0.3 전용 에이전트에 1.0 호출 | 422, 미신뢰 거부→보안 지정 후 허용, 게이트웨이 0.3↔1.0 변환 | P0 |
| E7-12 | 카드 변경 감지 | 버전 변경 | changed, 알림, 확인 처리 | P1 |
| E7-13 | Task API 격리·Artifact 다운로드 | user/user2 | 본인 것만, 타인 404, Markdown 다운로드 | P0 |
| E7-14 | 플랫폼 A2A 서버 (FR-A05) | 노출 프로파일 | 카드 스킬, 401, completed, inbound Task 영속 | P0 |
| E7-15 | 공식 a2a-sdk 1.x 상호운용 (NFR-INT1) | SDK create_client→send_message | 게이트웨이 경유 completed | P0 |
| E7-16 | PostgreSQL 회귀 | `make test-pg` | PASS | P0 |
| E7-17 | live 스모크 | `smoke.sh` | 10/10 (SDK 스트리밍 포함, edge 우회 차단) | P0 |
