# 시험 결과 / Test Result — impl 6th (Epic 6 MCP 경로)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite+Valkey+mcp-servers / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 16, 통과 Pass: 16, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 71 passed · PostgreSQL 71 passed

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E6-01 ~ E6-13 | PASS (round 4) | `test/test_mcp.py` (13 케이스) |
| E6-14 | PASS | `logs/make-test-pg.txt` |
| E6-15 | PASS (round 2) | `logs/smoke-local.txt` 12/12 — edge 네트워크에서 mcp-servers 도달 불가(000), 게이트웨이만 200, fastmcp 공식 Client 로 `/mcp/dynatrace` 호출 성공 |
| E6-16 | PASS | `logs/prod-mcp.txt` — 4 healthy, 규칙 13, 교환 정책 4 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | internal-api 도구명이 `orders_search` | FastMCP `from_openapi` 가 operationId 의 `.` 를 `_` 로 정규화 | 스코프 맵·시험을 실제 도구명 기준으로 변경 | round 2 |
| 2 | tickets_create 가 approval 대신 invalid_arguments | 시험이 요청 본문을 `body` 로 감쌈 — FastMCP 는 본문 필드를 평탄화. (판정 순서: 인자 검증 → 승인, 정상) | 시험 인자 수정 | round 3 |
| 3 | 도구 응답 소독이 JSON 응답 전체를 제거 | JSON 직렬화 텍스트는 한 줄이라 줄 단위 제거가 전부 지움 | 소독을 **JSON 구조 인지**(문자열 값별) 로 변경, structuredContent 도 동일 | round 4 |
| 4 | 전체 회귀에서 ConnectError 2건 | RFC 9728 발견 단계의 네트워크 오류를 RpcError 로 감싸지 않아 refresh·집계가 예외로 중단 | 발견 오류 → RpcError(UPSTREAM) → 서버 unhealthy, 집계에선 제외 | PASS |
| smoke 1 | S6-07 호스트 8100 응답 404 | 사용자의 다른 python 프로세스가 호스트 8100 점유 (우리 컨테이너는 공개 포트 없음) | 판정을 `docker port` 결과 0줄로 변경 | round 2 PASS |

## 알려진 제한 / Known limitations

- 서버→클라이언트 요청(sampling·elicitation, SCR-C08 샘플링 승인)은 게이트웨이가 stateless JSON 응답 모드라 중계하지 않는다 (`GET /mcp/{id}` = 405). 도구·리소스·프롬프트는 지원.

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-local · prod-mcp) · `smoke.sh`
