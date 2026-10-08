# 시험 결과 / Test Result — impl 29th (Epic 29 MCP 2026-07-28 확장)

- 일자 Date: 2026-10-07
- 대상 Target(profile): local(SQLite) pytest · Playwright 1.55 · prod 리허설(PostgreSQL 17 · uvicorn 2 workers · Valkey · migration 0016) · 데모 업스트림 FastMCP 4.0.11 / mcp SDK 2.3.0
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 34 (pytest 6 · e2e 2 · 회귀 묶음 R1 1 · prod 13 + 27th 회귀 11 은 게이트 기록), 통과 Pass: 34, 실패 Fail: 0, 최종 Verdict: **PASS** (pytest·e2e round 1, prod round 2)
- 업스트림 발견: 데모 5종 모두 `server/discover` → `2026-07-28` → 게이트웨이가 세션 없이 호출(업스트림 세션 0 증가). 강제 legacy 는 initialize·세션으로 되돌아감
- 레거시 회귀 0: 전체 pytest(247+1 / PG 248)·e2e 75 — 기존 동의·MRTR·중계 시험이 modern 업스트림 경로로 그대로 통과 (Epic 27 C27-03·04, consent C08 전부)
- prod: listen 승인·도구 규칙 변경 알림(다른 워커 DB 변경 감지) · Mcp-Param -32020/200 · 레거시 미러링 · progress SSE(1/3 → input_required) · MRTR 두 워커 완료 · 강제 legacy·auto 복귀 · 감사 체인

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix |
|-------|----------|------------|----------|
| 설계 | 가드 패턴 업스트림이 재시도 때 같은 progress 값을 다시 보냄 | MRTR 재호출은 도구를 처음부터 다시 실행 | `ProgressRelay` — 하류 토큰별로 증가하지 않는 값을 버림(사양 MUST) |
| 설계 | modern 업스트림에서 동의 거부·만료 시 inputResponses 에 실을 값이 없음 | MRTR 응답은 결과만 담는다 | tools/call 은 레거시와 같은 도구 오류 결과(isError, 거부 사유 포함), 그 밖은 JSON-RPC 오류 |
| prod 1 | P29-04·05·07·08 실패 (시험 결함) | 스크립트 `meta()` 의 `${1:-{\}}` 가 sh 에서 `{\}` 로 남아 본문 JSON 깨짐(-32700) | 기본값을 변수로 |
| prod 1 | P29-09 실패 (시험 결함) | 업스트림 progress 가 실수(1.0) — 비교 문자열 불일치 | 숫자 비교 |

## 첨부 / Logs

- `logs/pytest-r1.log` · `logs/pytest-full-r1.log` · `logs/e2e-r1.log` · `logs/smoke-prod-r1.log`·`r2.log` · `logs/listen.sse` · `logs/progress-*.jsonl` · `logs/refresh.log`
