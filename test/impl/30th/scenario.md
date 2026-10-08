# impl 30th — 동의·승인 시간 연장 (Epic 30 · D14-③ · WCAG 2.2.1 · FR-A00·G07)

## 범위

| 항목 | 구현 |
|------|------|
| API | `POST /api/v1/approvals/{id}/extend` — 만료 시각을 그 승인의 기본 시간만큼 뒤로(남은 시간에 더함): 동의(sampling·elicitation) `MCP_CONSENT_TIMEOUT` 60초 · 도구 승인 30분. 기한 없는 승인(registry) 400 `no_time_limit` |
| 상한 | 최대 10회 (WCAG 2.2.1 "최소 10번 연장" · D14 결정 "최대 10배": 늘어난 시간 합계 ≤ 기본의 10배) → 11회째 400 `extend_limit` |
| 권한·상태 | 요청자 또는 결정 권한자(`can_decide` — 직무 분리 승인자·위임받은 사람)만. pending·review 중에만, 만료·결정 후 409 `approval_closed`. 남의 요청은 404(보이지 않음) |
| 기록 | 감사 `approval.extend {approval_id, kind, extensions, step_s, expires_at}` · 실행 이벤트 `{sampling,elicitation}.extended`·`approval.extended` (채팅 카드가 새 기한을 따라감) · `approvals.extensions` (migration 0016) · 응답·목록에 `extensions`·`max_extensions`·`extend_step_s` |
| 대기 루프 | 동의 대기는 DB 기한을 매 폴링마다 읽으므로 다른 워커에서 연장해도 따른다 · 레거시 업스트림 SSE 읽기 제한을 연장 상한까지 늘림 |
| 화면 | 동의 카드(샘플링·검토·추가 입력)·채팅 승인 모달·승인 대기열 드로어에 「시간 연장 (+60초 / +30분)」(남은 연장 횟수 title) · 만료 20초 전 `role="alert"` 경고 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C30-01 | 동의 연장 +60초 · extensions/max/step 노출 · 감사 · 남의 요청 404 | `test_consent_extend_moves_expiry_and_audits` | pytest |
| C30-02 | 10회 → 합계 +10×30분 · 11회째 extend_limit · 실행 이벤트 10건 | `test_extend_limit_ten_times` | pytest |
| C30-03 | 직무 분리 승인자 연장 · 결정 후·만료 409 · 기한 없는 승인 400 | `test_extend_permissions_and_states` | pytest |
| C30-04 | 1초 기한을 연장하면 1.5초 뒤에도 pending → 허용 시 도구 완료 | `test_extension_keeps_consent_alive` | pytest |
| V30-01 | 실행 이벤트 → 카드 기한·횟수 갱신 | `runState.test.ts` | Vitest |
| E30-01 | 동의 카드 「시간 연장」 → 남은 초 +50 이상 · axe 위반 0 | `web/e2e/extend.spec.ts` | Playwright |
| E30-02 | 승인 대기열 드로어 연장 → 남은 연장 10 → 9회 | `web/e2e/extend.spec.ts` | Playwright |
| P30-01~09 | prod(2 workers): 연장 1회 후 65초에도 pending(워커 간 DB 기한) · 거부 마무리 · 10회 상한 · 감사 11건 · 체인 | `smoke-prod.sh` | live |

## 환경 가정

- 연장 1회 = 기본 시간(동의 60초 · 도구 승인 30분). `MCP_CONSENT_TIMEOUT` 를 바꾸면 연장 단위도 같이 바뀐다.
- 하류 MCP 클라이언트 중계(Relay)·MRTR 의 클라이언트 응답 대기는 사람 화면이 아니므로 대상이 아니다.
