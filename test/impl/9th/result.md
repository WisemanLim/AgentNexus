# 시험 결과 / Test Result — impl 9th (Epic 9 관측·감사)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite / prod=PostgreSQL 17 (워커 2)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 14, 통과 Pass: 14, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 110 passed · PostgreSQL 110 passed

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E9-01 ~ E9-11 | PASS (round 4) | `test/test_observe.py` (12 케이스) |
| E9-12 | PASS | `logs/make-test-pg.txt` |
| E9-13 | PASS (round 2) | `logs/smoke-local.txt` 11/11 — trace 서비스 `agentnexus, agentnexus-a2a-demo, agentnexus-mcp-servers`, 스팬 76·GenAI 12 |
| E9-14 | PASS | `logs/smoke-prod.txt` 11/11 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | 기존 A2A 시험 다수 `TypeError: event() got …` | 로그 헬퍼 인자명 `message` 와 키워드 `message=` 충돌 | 키를 `detail` 로 변경 | PASS |
| 2 | 리플레이에 token.exchange 없음 | 게이트웨이 교환 토큰 캐시 재사용 — 새 교환이 이 trace 에 없음(정상 동작) | 시험에서 캐시를 비워 해당 trace 안 교환 보장 | round 3 |
| 3 | 아카이브 후 체인 invalid (`tail_truncated`) | **SQLite 가 삭제 후 rowid 를 재사용** → 아카이브 직후 이벤트가 seq 1 을 다시 받아 앵커 이전으로 취급 | audit seq 를 체인 머리(잠금 보유) 기준 `last_seq+1` 로 명시 할당 — DB 자동 증가 비의존 | round 4 PASS |
| 4 | 전체 회귀에서 비용 정확도 실패 | 다른 시험이 넣은 합성 사용량(토큰 없이 비용만)까지 합산 | 시험 안에서 호출을 만들고 그 구간(since)만 비교 | PASS |
| smoke 1 | S9-03 GenAI 스팬 부족 | api 스팬 BatchSpanProcessor(5s) 플러시 전 조회 | 대기 조건에 GenAI 스팬 수 포함 | round 2 PASS |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-local · smoke-prod) · `smoke.sh`
