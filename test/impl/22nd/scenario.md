# impl 22nd — 채팅·오케스트레이션 편집기 깊이 (Epic 22 · FR-A01·A02·A00·A03·W02·W05·G01·G07 · SCR-C01~C07·D01~D09 · ST-06)

## 범위

| 항목 | 구현 |
|------|------|
| C01 채팅 홈 | 프로파일 카드 준비 상태(`GET /api/v1/chat/profiles` — 쓰는 MCP 서버·원격 에이전트가 활성인지) · 미설정이면 비활성 + 사유 · 예시 요청 |
| C03 응답 | 실행 메타(`run.meta` — 토큰·비용·첫 토큰·모델·로컬 전용·노드별 지연/비용(`usage_records.step_id`)·폴백·429·응답 소독·근거 사용/미사용) · 근거 요약(MSG-025) · ⧉ 복사 · 👍👎(`messages.feedback`, 다시 누르면 취소) · ↻ 다시 생성 · ✎ 편집 후 재전송(새 분기 `messages.branch_of`, 이전 대화 보존) · 원문 링크 |
| C04 승인 모달 | `GET /api/v1/approvals/{id}` 의 `context` — 요청 출처(프로파일 › 노드 · i단계 중 n · 대화 · trace) · 실행 신원(OBO·audience·스코프) · 영향(대상·공개 범위·되돌리기) · 정책 근거 체크 · 만료 타이머 · **이 세션 동안 다시 묻지 않기**(중위험만, 고위험은 `remember_not_allowed`) · 본문 전체 보기 |
| C05 위임 카드 | Agent Card 헤더(이름·신뢰 등급·버전·스킬) · 스트림 이벤트 로그 · Artifact 내려받기 · **첨부로 추가**(`POST /api/v1/chat/attachments/from-artifact`) |
| C06 부분 실패 | 작업별 결과 표(유형·결과·사유·시도·지연, 실패 먼저) · 빠진 근거 안내 · **부분 결과로 계속**(`POST /api/v1/runs/{id}/continue` — partial 확정, on_failure=fail 로 멈춘 실행은 남은 단계를 부분 결과 모드로) · 폴백 배너(MSG-015) · 429 배너(MSG-012) |
| C07 대화 이력 | 제목·**본문** 검색(300ms) · 프로파일·기간·출처(채팅/API — `sessions.source`) 필터(URL 보존) · 메시지 수·비용 · 미리보기(`/summary` — 위임·비용·trace·첨부·Artifacts) · 첨부·Artifacts 모아보기(`/api/v1/chat/files`) · 내보내기 .md(서버) · **삭제 = 내용 제거·메타만 감사 보존** |
| D01 목록 | 상태 탭(보관함 포함)·팀·검색 필터 · 행 동작(연결 점검 `GET …/health` · 복제 `POST …/clone` · 보관 · 복원 `POST …/restore`) |
| D02 그래프 | 노드 배지(합류 ⋈ · 필수 ★ · 재시도 ×N · 타임아웃 · 조건) · 검증 오류·코드만 저장이 있으면 게시 비활성 · 참조 경고(미등록 도구·신뢰 미달 원격) |
| D03 Planner | 계획 모델·**폴백 모델**(장애 시) · **분해 프롬프트**({request}·{tools}·{agents}) · 최대 하위 작업·**최대 깊이** · 출력 스키마 plan.v1 · **배정 대상**(신뢰 미달 원격 비활성) · **재계획 조건 `replan.triggers`**: failed · empty(검색 0건) · rejected(사람 거부) |
| D04 Retriever·Summarize | **Retriever 쓰기 도구는 검증 오류**(와일드카드는 경고) · top-k·타임아웃 · 요약 최대 토큰 · **인용 필수(없으면 1회 재생성, 그래도 없으면 ‘근거 없음’)** · 인용 형식 · 로컬 전용 배지 |
| D05 Executor | 쓰기 도구 위험도·승인(high 필수 고정, medium 선택 `approval.medium`) · 승인자(본인/별도 승인자 — 강화만) · **승인 만료 ttl_s → 자동 거부**(스케줄러 `approval_jobs`) · 거부·만료 시 partial/replan/fail |
| D06 팬아웃·실패 | 병렬/순차·**최대 동시** · 합류 **all/any/required(필수 노드)/n_of_m** · **노드·전체 타임아웃** · 재시도·**지수 백오프** · **도구 429 대기(≤10초) 후 재시도 또는 제외** · 부분 결과 · 필수 노드 실패 = 전체 실패 · **실패 시뮬레이션** `POST …/simulate` · 단계별 retry·timeout_s |
| D07 코드 | 형식 YAML / **ADK(Python) 보기**(변환 경고) · 동기 배지 · **코드만 저장**(`allow_invalid`, `agent_profiles.draft_code`, MSG-001 — 게시 409 `code_only_draft`) · 되돌리기 · **게시본 비교** `GET …/diff` · 실시간 검증(오류·경고 줄 이동) · 그래프 미리보기(오류 노드 적색) |
| D08 테스트 | **쓰기 도구 드라이런**(기본 켬 — 호출·승인 0건, 인자만 기록) · **게시 버전 실행** · 노드별 지연·비용·상태·입출력 · 이전 실행 비교 · **게시 전 점검**(검증 0·최근 3회 성공·승인 정책·연결·코드만 저장 없음) → 게시 |
| D09 Host 세션 | KPI(활성·서버·재연결 중·동의 대기·테스트) · 전송·도구·상태·재연결 횟수 · 세션 이벤트(열림·재연결·종료) · capabilities · 동의 경계 · **재연결·세션 종료**(확인 MSG-002) |

마이그레이션 0011: `messages.feedback·branch_of` · `sessions.source` · `runs.dry_run` · `agent_profiles.draft_code` · `usage_records.step_id` · `mcp_sessions.events·reconnects`.

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C22-01 | 메타(토큰·첫 토큰·노드별)·근거 · 피드백 토글·타인 404 · 다시 생성·편집 분기 · 본문 검색·출처·프로파일·기간 · 미리보기·내보내기 · 삭제 후 내용 비움·404 | `test_response_meta_feedback_branches_and_history` | pytest |
| C22-02 | 중위험 + 노드 정책 → 승인 · 맥락(출처·신원·만료·체크) · remember → 같은 세션 승인 없음 · 다른 세션 다시 승인 | `test_approval_context_remember_and_node_policy` | pytest |
| C22-03 | 고위험 remember 400 · 만료 → 자동 거부 → partial | `test_high_risk_no_remember_and_expiry` | pytest |
| C22-04 | 드라이런(쓰기 0건)·버전 실행·없는 버전 404·이전 실행·게시 전 점검·연결 | `test_dry_run_versions_and_prepublish_checks` | pytest |
| C22-05 | 코드만 저장·게시 409·diff·ADK·되돌리기 · 노드 설정 검증 · Retriever 쓰기 오류/와일드카드 경고 · 필터·복제·보관·복원 | `test_code_only_diff_adk_validation_and_list` | pytest |
| C22-06 | 노드 타임아웃·재시도 · N 중 M · 필수 노드 실패 → 전체 실패 · 시뮬레이션 · 도구 429 대기 후 성공 | `test_timeouts_429_and_join_rules` | pytest |
| C22-07 | 검색 0건 재계획 · Planner 폴백 모델·프롬프트·배정 | `test_replan_on_empty_and_planner_fallback` | pytest |
| C22-08 | on_failure=fail → 계속 → partial · 타인 404 · partial → 확정 | `test_continue_with_partial_results` | pytest |
| C22-09 | Host 세션 KPI·이벤트·재연결·종료·권한 · 프로파일 준비 상태 · Artifact → 첨부·타인 404 · 모아보기 | `test_host_sessions_readiness_and_artifact_attach` | pytest |
| E22-01~07 | 근거 요약·👍·다시 생성·편집 · 승인 모달 맥락·고위험 기억 불가 · 부분 실패 카드·계속 · 이력 검색·내보내기·출처 · 준비 안 된 카드 · 드라이런·점검·코드만 저장·비교·되돌리기·ADK · Planner·팬아웃 드로어·시뮬레이션·Host KPI · 런타임 오류 0 | `web/e2e/depth.spec.ts` | Playwright |
| P22-01~ | prod: 0011 · 노드별 비용(step_id)·첫 토큰 · 피드백·다시 생성 분기 · 본문 검색·내보내기 · 승인 맥락·고위험 기억 거부 · 만료 → partial · 드라이런 쓰기 0 · 버전 실행 · 코드만 저장 409 → 되돌리기 → 게시 · 계속 → partial · 시뮬레이션 · Host KPI · 준비 상태 | `smoke-prod.sh` | live |
| R | 전체 회귀 | logs | make |

## 환경 가정

- `replan.on` 은 YAML 1.1 에서 키가 `true` 로 읽혀 `replan.triggers` 로 정의했다(기존 `on` 도 받아 줌).
- input-required 재계획 조건은 넣지 않았다 — 원격 에이전트가 되물으면 사용자 답(C05)으로 이어 가는 것이 기본이다.
- 원격 A2A 실패의 폴백은 별도 설정 없이 Planner 재계획(대체 계획)으로 처리한다. 모델 오류 폴백은 게이트웨이 라우팅·Planner `fallback_model`.
- 노드별 비용은 게이트웨이가 `X-AgentNexus-Step` 헤더로 기록한 사용 기록 기준이다(Planner 계획 호출은 단계 밖).
