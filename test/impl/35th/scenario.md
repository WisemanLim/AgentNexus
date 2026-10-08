# impl 35th — SLO·오류 예산 (Epic 35 · D15-④ · NFR-A1·P1 · FR-O03)

## 범위

| 항목 | 구현 |
|------|------|
| SLI 수집 | 트레이싱 미들웨어(api 구성요소)가 데이터 경로 요청을 분류 — 모델 API(`/v1/chat·completions·embeddings·models`) · MCP(`/mcp/…`) · A2A(`/a2a/…`), 콘솔·관리 API 제외 · 분 단위로 total·5xx·첫 응답(헤더) 100/250/500/1000/2000/5000ms 초과 수를 워커 메모리에 모아 예약 작업 주기(모든 워커)·조회·종료 때 `sli_minutes` 에 워커별 행으로 누적(migration 0018) · 95일 보관 |
| SLO | `slos` — 기본 2종(시드): 데이터 경로 가용성 99.95% / 30일 (NFR-A1), 첫 응답 ≤ 2000ms 99% / 30일 (NFR-P1 첫 토큰). 범위 all·model·mcp·a2a, 창 7~90일 |
| 계산 | SLI = 1 − 나쁜/전체 · 오류 예산 = (1 − 목표) × 전체 · 잔량 = 1 − 나쁜/예산 · 소진율 = 창 오류율 ÷ (1 − 목표) — 5m·30m·1h·6h · 일별 계열 |
| 경보 | 다중 창 다중 소진율: page = 1h·5m ≥ 14.4 · ticket = 6h·30m ≥ 6 · 예산 소진 = 잔량 ≤ 0 · 짧은 창 10건 미만은 경보 안 함 · 상태가 바뀔 때만 운영자 알림(page danger · ticket/소진 warn · 회복 info), 경보 주기(리스 1 워커) |
| API | `GET /api/v1/observe/slos` (observe:read) · `PATCH /api/v1/observe/slos/{id}` (admin:write, 사유 필수, 감사 `observe.slo.update`, 지연 기준은 정해진 값만) · `POST /api/v1/observe/slos/evaluate` |
| 화면 | 관측·감사 → 「SLO·오류 예산」 — SLI·예산 잔량·요청 KPI · 잔량 막대 · 창별 소진율 표(경보 기준 표시) · 일별 나쁜 요청 막대 · 목표 수정(사유) · 지금 평가 · 경보 배너 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C35-01 | 데이터 경로만 분류 · 같은 워커·분 누적 · 워커별 행 · 지연 초과 버킷 | `test_classify_and_flush_accumulates` | pytest |
| C35-02 | SLI·예산(허용 100·잔량 0.3)·소진율(1h 20·5m 50) → page · 지연 SLO ticket · 일별 계열 · 범위 밖 분류 제외 | `test_report_budget_and_burn` | pytest |
| C35-03 | 상태 변화 때만 알림 · 재평가 조용 · 회복 알림 | `test_evaluate_notifies_on_state_change` | pytest |
| C35-04 | 기본 2종 · 권한(dev 403) · 사유 필수 422 · 잘못된 기준 400 · 창 범위 422 · 감사 before/after | `test_slo_api` | pytest |
| C35-05 | 실제 데이터 경로 요청이 기본 SLO 에 반영 | `test_real_traffic_counted` | pytest |
| E35-01 | SLO 화면 · 소진율 표 · 사유 없이 저장 불가 · 수정·되돌림 · 지금 평가 · axe 위반 0(a11y 전 화면에 포함) | `web/e2e/slo.spec.ts` · `a11y*.spec.ts` | Playwright |
| P35-01~08 | prod(2 workers): 기본 SLO · 두 워커 카운터 합산 · 업스트림 전부 장애 502 → 나쁜 요청·5분 소진율 → page 알림 · 공급자 원복 · 감사 체인 | `smoke-prod.sh` | live |

## 환경 가정

- 월간 99.95% 의 실제 판정은 운영 트래픽이 30일 쌓여야 한다 — 이 차수는 계산·경보·화면을 검증한다.
- 5xx 만 가용성 오류로 센다(4xx·429 는 정책 결과). 다른 워커의 최근 카운터는 그 워커의 다음 주기(SCHEDULER_INTERVAL_S)에 반영된다.
- Prometheus 기록 규칙·외부 SLO 도구(Sloth 등) 연동은 범위 밖 — 내장 계산·알림.
