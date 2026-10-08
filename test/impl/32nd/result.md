# 시험 결과 / Test Result — impl 32nd (Epic 32 MCP 리소스 구독·Tasks 확장)

- 일자 Date: 2026-10-07
- 대상 Target(profile): local(SQLite) pytest · prod 리허설(PostgreSQL 17 · 2 workers · migration 0018) · 데모 업스트림 FastMCP 4.0.11 · 사양 SEP-2663(Tasks) · 2026-07-28(listen)
- 커밋 Commit: — (git 커밋 없음)

## 요약 / Summary

- 전체 Total: 18 (pytest 7 · prod 11), 통과 Pass: 18, 실패 Fail: 0, 최종 Verdict: **PASS** (pytest round 2 · prod round 4)
- 리소스 구독: 읽을 수 있는 URI 만 승인 → 실시간 지표 리소스가 바뀌면 `notifications/resources/updated` (prod 폴링 5초·지표 10초)
- Tasks: 빠른 호출은 일반 결과(작업 행 없음) · 입력이 필요한 호출은 `resultType:"task"` → `tasks/get` input_required → `tasks/update` → completed — prod 에서 실행 워커와 update 워커가 달라도 DB(mcp_relays)로 이어짐 · 취소(협조적) · 다른 주체 -32602
- 회귀: MCP·동의·MRTR 시험 전부 통과 (29th 의 `resources.subscribe:false` 단언은 D15-① 로 true 로 갱신)

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix |
|-------|----------|------------|----------|
| pytest 1 | C32-03 미선언 능력 단언 실패 (시험 결함) | 도우미가 `caps or TASKS` 라 빈 능력이 확장 선언으로 바뀜 | elicitation 만 선언 |
| pytest 1 | C29-03 `subscribe is False` (기대된 변경) | D15-① 리소스 구독 | true 로 |
| prod 1~3 | P32-08 작업이 input_required 에 머묾 (시험 결함) | macOS `/bin/sh`(bash 3.2)가 `"$( … "{…,…}" … )"` 안 중괄호를 확장 — tasks/update 가 깨진 JSON 으로 3번 나감(P32-07 도 잘못 PASS) | JSON 을 변수로 만들어 전달. 같은 흐름을 수동으로 재현해 제품 동작은 정상 확인 |

## 첨부 / Logs

- `logs/pytest-r1.log`·`r2.log` · `logs/smoke-prod-r1~r4.log` · `logs/listen-resource.sse`
