# 시험 결과 / Test Result — impl 14th (Epic 14 MCP 서버→클라이언트 요청 · SCR-C08)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 23, 통과 Pass: 23, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 146 passed + 1 skipped · PostgreSQL 147 passed · Vitest 24 · 빌드 · Playwright 11/11 · helm-check
- live `smoke.sh` 12/12 — 공식 fastmcp Client(하류) 의 sampling·elicitation 핸들러가 게이트웨이 중계로 호출됨

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C14-01 ~ C14-08 | PASS (round 2) | `test/test_mcp_consent.py` 8 |
| C14-09 | PASS | `logs/web-test.log` |
| E14-01 · E14-02 | PASS (round 2) | `logs/e2e.log` · `logs/screenshots/` |
| S14-01 ~ S14-12 | PASS (round 2) | `logs/smoke-run2.log` |
| R | PASS | `logs/make-test.log` · `make-test-pg.log` · `web-build.log` · `helm-check.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 설계 | 서버가 tools/call 도중 sampling 을 요청하면 교착 | 게이트웨이가 업스트림 응답 전체를 버퍼링한 뒤 파싱 | SSE 를 흘려 읽으며 서버 요청에 응답(`_post_rpc`) | PASS |
| 설계 | in-process 시험에서도 같은 교착 | httpx ASGITransport 가 응답을 모아서 반환 | `StreamingASGITransport` 추가, 데모 서버 마운트 교체 (기존 137건 무회귀) | PASS |
| 1 | elicitation 시험 KeyError | 관리 API 오류는 problem+json(`code` 최상위)인데 시험이 `error.code` 를 읽음 (시험 코드) | 단언 수정 | round 2 |
| e2e 1 | 데모 실행 답변이 도구 이름만 표시 | Retriever 가 알려진 필드가 없는 dict 결과(분류 결과)를 본문으로 못 뽑음 (실제 결함) | 스칼라 값을 본문으로 쓰는 폴백 | round 2 |
| e2e 1 | 첨부 배지 텍스트 불일치 | 배지 안에 제거 버튼 포함 (시험 코드) | 접근성 이름 `… 제거` 로 확인 | round 2 |
| smoke 1 | 공식 클라이언트 elicitation 응답 검증 오류 | 시험 클라이언트가 fastmcp 규약(수락 데이터만 반환) 대신 `ElicitResult` 반환 (시험 코드) | 데이터 dict 반환 | smoke 2 12/12 |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · web-test · web-build · e2e · helm-check · smoke-run1/2 · screenshots/) · `smoke.sh` · `relay_client.py`
