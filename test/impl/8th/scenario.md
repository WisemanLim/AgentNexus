# 시나리오 / Scenario — impl 8th · Epic 8 Host·오케스트레이션 (FR-A00~A02, W02·W04·W05, A05)

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E8-01 | 템플릿 3종·조합 재료 (FR-W04·W02) | 템플릿·도구·원격·모델 목록 | 사내 Q&A·이슈 보고·장애 분석 위임, 위험도별 노드 제안, 카탈로그 노출 | P0 |
| E8-02 | 사내 Q&A SSE E2E (FR-A01·A02) | 채팅 SSE | run→plan(템플릿 폴백)→단계→delta→completed, 인용 [1], 어시스턴트 메시지·인용 저장 | P0 |
| E8-03 | 게이트웨이 경유·신원 전파·MCP Host 세션 (FR-A00·G00·I02) | 위 실행 | 사용량(tool·model, agent=프로파일/노드), 교환 토큰 subject=delegation, 실행 단위 MCP 세션 생성·종료 | P0 |
| E8-04 | 이슈 보고 — 승인 일시정지·재개 (FR-G07·A02.4) | 쓰기 단계 | waiting_approval, {{s3.text}} 렌더, 승인 후 succeeded, 페이지 작성자=사용자 | P0 |
| E8-05 | 승인 거부 → 부분 결과 (FR-W05) | 거부 | partial + 안내, 단계 rejected | P0 |
| E8-06 | 장애 분석 위임 — 병렬 팬아웃·합류 + A2A (시나리오 2) | 원격 단계 | s1·s2 병렬 후 s3 합류, 원격 completed, 위임 카드(parts) | P0 |
| E8-07 | 자동 재시도·부분 실패·사용자 재시도 (A02.5, SCR-C06) | 일시 장애 원격 + 장애 도구 | 원격 2회 시도 성공, 도구 실패 → partial, 복구 후 재시도 → 하위 요약 재실행 → succeeded | P0 |
| E8-08 | LLM 플래너·검증 폴백 (A02.1) | 고정 응답 mock 공급자 | 유효 계획 사용(source=llm), 허용 외 도구 계획은 템플릿 폴백(사유) | P0 |
| E8-09 | 그래프 검증·버전 (SCR-D02·D07) | 잘못된 YAML·참조 / 템플릿 복제·게시·롤백 | 오류 경로·줄 번호, 템플릿 수정 금지, v1→v2→롤백 v3, 미게시 A2A 노출 거부 | P0 |
| E8-10 | 즉시 사용 ≤ 60s (FR-W04, NFR-P1) | OpenAPI 등록 → 프로파일 → 실행 | 도구 즉시 노출, 실행 성공, 총 60s 미만 | P0 |
| E8-11 | 플랫폼 A2A → 오케스트레이션 (FR-A05) | /a2a/platform/tpl-qa | completed, answer.md, inbound Task | P0 |
| E8-12 | 실행 격리·데이터 분류 라우팅 | user2 / public·confidential | 타인 실행 404, public→fast-commercial, confidential→domain-local | P0 |
| E8-13 | 첨부 근거 (FR-A01) | 텍스트 첨부 | 첨부가 번호 근거·인용에 포함 | P1 |
| E8-14 | 취소·오류 안내 | cancel / 프로파일 없음 | canceled, 재취소 400, profile_required | P1 |
| E8-15 | PostgreSQL 회귀 | `make test-pg` | PASS | P0 |
| E8-16 | live 스모크 (local) | `smoke.sh` | 9/9 | P0 |
| E8-17 | prod 리허설 (PG · uvicorn 워커 2) | `TOKEN=… smoke.sh` | 9/9 — 승인 재개가 다른 워커에서도 동작 | P0 |
