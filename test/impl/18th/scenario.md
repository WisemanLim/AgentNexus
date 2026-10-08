# impl 18th — 오케스트레이션 신뢰성·확장 (Epic 18 · FR-A01·A02·W02·R02~R04·A05·O02·O03·G01·S00 · NFR-S1 · PRD D12-②)

## 범위

| 항목 | 구현 |
|------|------|
| A2A 장기·추가 입력 (FR-R02~R04, SCR-C05) | 원격 Task 가 working 이면 `tasks/get` 으로 따라감(최대 `A2A_REMOTE_WAIT_S`) · input-required 면 단계 `waiting_input`·실행 `waiting_input` + 이벤트 `input.required` → `POST /api/v1/runs/{id}/input` 이 같은 Task·contextId 로 이어 보냄 · 위임 카드 인라인 답변 |
| 재계획 (FR-A02.1, SCR-C02) | `planner.replan {max_rounds, fallback_plan}` — 실패 단계가 남으면 LLM 대체 계획(검증 통과 시) 또는 fallback_plan 을 `r<N>_` 단계로 붙이고 대체된 단계는 `replanned`(실패로 집계 안 함), 이벤트 `plan.revised`, 계획 패널에 라운드 표시 |
| 조건 분기 (FR-W02) | 단계 `when: "<참조> <연산> <값>"` — 거짓이면 건너뜀(실패 아님), 그 분기만 의존하는 단계는 `분기 미선택` · 그래프 검증(형식·앞 단계 참조) |
| RAG 내장 벡터 색인 (FR-A02.2, PRD D12) | `POST /v1/embeddings`(입력 가드·데이터 등급 라우팅·사용량, 모델 `embed-local` 결정적 해시) · 지식 베이스 API(팀 범위) · Retriever `kb:` 단계(게이트웨이 경유 검색) · 데모 `ops-runbooks`·프로파일 `demo-rag` · `nexus kb` |
| 바이너리 첨부 (FR-A01, SCR-C01) | `POST /api/v1/chat/attachments` (pdf·docx·xlsx·이미지·텍스트, `ATTACHMENT_MAX_MB`) — 표준 라이브러리 텍스트 추출, 메시지는 `attachment_id` 로 참조(소유자만) |
| 품질 라우팅 (FR-G01) | 라우팅 전략 `quality`, 모델 `quality`(0~1) — 시드 기본값, I03·H02 |
| 영속·복구 (NFR-S1·A1, FR-A05) | 플랫폼 A2A Task 를 메모리에 없으면 DB 에서(실행 결과로 완결) · resubscribe 폴링 · 실행 생존 신호(`heartbeat_at`) + `recover_stale` |
| 예약 작업 (FR-O02·O03) | `scheduler` — 작업별 DB 리스(조건부 UPDATE), 경보 평가·실행 복구(60s)·보존 파기(`RETENTION_INTERVAL_S`) · Helm PrometheusRule |
| MCP 서버 자동 등록 (FR-S00) | Custom 템플릿 `anx_register.py` — 기동 시 `AGENTNEXUS_URL`·`AGENTNEXUS_TOKEN` 으로 `/v0/publish`(멱등·버전 충돌 409 안내) |
| 동시 1,000 세션 (NFR-S1) | `deploy/k6/sessions.js` — VU 당 MCP 세션 1개 유지, 10초마다 도구 호출 |

마이그레이션 0007: `runs.heartbeat_at` · `models.quality` · `attachments` · `knowledge_bases` · `kb_documents` · `kb_chunks`.
새 env: `SCHEDULER_INTERVAL_S` · `RETENTION_INTERVAL_S` · `RUN_STALE_S` · `A2A_REMOTE_WAIT_S` · `ATTACHMENT_MAX_MB` (local·prod·example·Helm).

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C18-01 | input-required → waiting_input → 다른 사용자 답변 404 → 답변 후 같은 Task·contextId 로 완료(turns 2) → 재답변 400 | `test_remote_input_required_then_resume_same_task` | pytest |
| C18-02 | working Task 를 다시 보내지 않고 tasks/get 으로 완료까지 | `test_remote_working_task_is_polled_until_done` | pytest |
| C18-03 | 조건 분기 — 선택/미선택 분기, 실패로 집계 안 함, 잘못된 조건 검증 오류 | `test_conditional_branch_*` · `test_condition_evaluator_unit` | pytest |
| C18-04 | 실패 → 대체 계획 r1_* 성공 → succeeded, plan.revised 1회 | `test_replan_with_fallback_plan_recovers` | pytest |
| C18-05 | 임베딩 256차원·임베딩 미지원 404 · KB 검색 순위·팀 범위 404 · demo-rag 근거 인용·조건 생략 | `test_embeddings_*` · `test_knowledge_base_*` | pytest |
| C18-06 | docx·xlsx·pdf 텍스트·이미지 메타 · 미지원 형식·크기 초과 400 · 남의 첨부 404 · 근거 인용 | `test_binary_attachments_*` | pytest |
| C18-07 | quality 전략이 점수 높은 모델 선택·점수 변경 반영·범위 검증 | `test_quality_routing_strategy` | pytest |
| C18-08 | 메모리 유실 후에도 tasks/get·resubscribe, 실행 결과로 Task 완결 | `test_platform_task_survives_lost_memory` | pytest |
| C18-09 | 오래된 생존 신호의 running 실행 복구 → succeeded, 신선한 실행은 건드리지 않음 | `test_stale_running_run_is_recovered` | pytest |
| C18-10 | 리스 단일 보유·만료 인수 · 경보 알림 · 보존 실행 · tick | `test_scheduler_lease_and_jobs` | pytest |
| C18-11 | 템플릿 자동 등록 — 게시·멱등·버전 충돌·환경 변수 없으면 무동작 · 승인 대기 노출 | `test_template_self_registers_on_startup` | pytest |
| V18 | 재계획·조건·추가 입력 리듀서 | `web/src/test/runState.test.ts` | Vitest |
| E18-01~04 | C05 인라인 답변·PDF 첨부 근거·재계획 1/1 표시·런타임 오류 없음 | `web/e2e/reliability.spec.ts` | Playwright |
| P18-01~13 | prod(PG·2 workers): 0007 · 임베딩·KB · 첨부(읽기 전용 이미지) · input-required 재개 · 워커 간 tasks/get 12/12 · 예약 복구 · **k6 1,000 세션 오류<1%·p95<1s** · 리스 · CLI | `smoke-prod.sh` | live |
| R | 전체 회귀 SQLite·PG·Vitest·빌드·Playwright 전체·helm-check | logs | make |

## 환경 가정

- RAG 임베딩은 mock `embed-local`(단어·문자 2-gram 해시, 어휘 겹침 기반)이다. 의미 검색 품질은 실공급자(vLLM·Ollama 임베딩)로 교체해야 나온다.
- PDF 추출은 Flate 스트림의 Tj/TJ 텍스트만 — 스캔 이미지 PDF·암호화 PDF·CID 폰트(한글 대부분)는 텍스트가 비어 있을 수 있다.
- k6 는 compose 네트워크 안 `grafana/k6:1.0.0` 컨테이너(호스트 설치 없음), Access Token 50개를 만들어 쓰고 끝나면 폐기한다.
