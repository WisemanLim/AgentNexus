# 시험 결과 / Test Result — impl 16th (Epic 16 남은 P0 콘솔 화면)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 10, 통과 Pass: 10, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 151 passed + 1 skipped · PostgreSQL 152 passed · Vitest 24 · 빌드 · Playwright 18/18(전체) · helm-check

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C16-01 · C16-02 | PASS | `logs/make-test.log` · `logs/web-build.log` |
| E16-01 ~ E16-07 | PASS (round 2) | `logs/e2e.log` · `logs/screenshots/` |
| R | PASS | `logs/make-test-pg.log` · `logs/web-test.log` · `logs/helm-check.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 빌드 | `tsc` 미사용 import | 작성 중 남은 import | 정리 | PASS |
| e2e 1 | 원격 위임 카드가 끝까지 안 보임 | 실행이 빨리 끝나 Task 생성 전 조회 후, 종료와 함께 재조회가 멈춤 (실제 결함) | 실행 상태가 바뀌면 재조회 | round 2 |
| 점검 | 위임 카드가 거치지 않은 input-required 를 완료로 표시 · Task ID 가 내부 키 앞부분 | 진행 표시 규칙 | input-required 는 실제 상태일 때만 · 원격 Task ID 표시 | round 2 PASS |

## 2차 완료 게이트 (prod 리허설)

- `make test` · `make test-pg` · `make prod-build` → `make db-migrate ENV=prod` → `make db-ping ENV=prod`(postgres OK) → `gate-prod.sh` 13/13 → `make prod-stop`
- 13th 항목 11종 재확인 + **P16-12** prod(uvicorn 2 workers·PostgreSQL)에서 sampling 동의가 다른 워커로 들어와도 DB 폴링으로 만남 ·
  **P16-13** 공식 fastmcp Client 중계 — `logs/gate-prod.log` · `logs/k6.log`

| 게이트 회차 | 이미지 | 결과 | 비고 |
|-------------|--------|------|------|
| 1 | Epic 16 1차 | 13/13 | `logs/gate-prod.log` — MCP p95 21.86ms · 순수 추가 지연 p95 19.5ms |
| 2 | H02·L03 화면 추가 후 | **11/13** | `logs/gate-prod-final.log` — ① P13-06 3개 서비스 중 api 스팬이 아직 flush 전인데 대기 루프가 먼저 끝남(스크립트 경합) ② P13-09 순수 추가 지연 p95 22.2ms > 20ms — 설정 없는 업스트림 호출마다 mTLS 정책을 DB 에서 읽음(스레드풀 + 세션) |
| 3 | 수정 후 | **13/13** | `logs/gate-prod-final2.log` — ① 16th 사본만 대기 조건을 3개 서비스 전체로 ② `tls.mode` 5초 캐시 + 설정 없는 대상은 DB 없이 기본 연결, 정책 저장 시 즉시 무효화 · `make test` 151+1skip · `make test-pg` 152 (`logs/make-test-final2.log`) · k6 model p95 10.4ms · MCP p95 19.48ms · 첫 토큰 p95 13.46ms · **MCP 순수 추가 지연 p95 17.6ms** (≤20ms) |

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · web-test · web-build · e2e · helm-check · screenshots/)
