# 시나리오 / Scenario — impl 9th · Epic 9 관측·감사 (FR-O00~O05, FR-G05, COMPLIANCE)

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E9-01 | 단일 trace_id 전 구간 (FR-O01, NFR-O1) | 장애 분석 위임 실행 | invoke_agent·단계·MCP·A2A·chat 스팬, 구성요소 api·mcp-servers·a2a-demo 동일 trace, GenAI 속성(gen_ai.*) | P0 |
| E9-02 | 승인 재개도 같은 trace | 이슈 보고 승인 후 | `invoke_agent … (resume)`·executor 스팬이 최초 trace 에 합류 | P0 |
| E9-03 | OTLP/HTTP 수신기 (FR-O00·W03) | protobuf·JSON, 잘못된 토큰 | 401 / 200, 서비스명·kind·status 보존, 파이프라인 통계 | P0 |
| E9-04 | /metrics (FR-O03·G05) | 무인증·user·operator·METRICS_TOKEN | 401·401·200·200, anx_* 지표(요청·토큰·도구·승인 대기·헬스·실행) | P0 |
| E9-05 | 구조화 로그·PII 마스킹 (FR-O02, NFR-PRV1) | 도구 거부·PII 로그 | 검색 가능, 전화·메일 마스킹 저장 | P0 |
| E9-06 | 운영 개요 요약 (SCR-B02·H01) | summary | 호출·종류별 p95·오류율·비용·서버 헬스 | P1 |
| E9-07 | Usage & Cost 정확도 ±2%·범위 (FR-O04, NFR-O1) | 9회 호출 | 집계=단가×토큰(오차 0), user 는 본인만, 팀 필터, 일자별 | P0 |
| E9-08 | 알림 규칙 (SCR-K05) | qps ≥ 0 | fired + 알림 | P2 |
| E9-09 | 세션 리플레이·재실행 (FR-O05, 시나리오 4) | 리플레이·rerun | 메시지·단계·도구·모델·교환 시간순, 타인 404·감사 담당 200, 재실행 succeeded | P0 |
| E9-10 | 서명 리포트 (SCR-K10) | 생성·다운로드·검증·변조·위조 | 서명 유효, 내용 변조 탐지, 위조 거부, user 403 | P0 |
| E9-11 | 보관기간 아카이브·파기 증명 (COMPLIANCE) | retention(audit_days=0)·archive verify·purge | 텔레메트리 삭제 건수, 아카이브 후 체인 유효(앵커), 파일 검증, 파기 후 해시 매니페스트 유지, 파기 이벤트가 체인에 기록 | P0 |
| E9-12 | PostgreSQL 회귀 | `make test-pg` | PASS | P0 |
| E9-13 | live (local) | `smoke.sh` | 11/11 — 별도 컨테이너 스팬이 OTLP 로 같은 trace 에 합류 | P0 |
| E9-14 | prod 리허설 (워커 2, multiprocess 메트릭, PG) | `TOKEN=… smoke.sh` | 11/11 | P0 |
