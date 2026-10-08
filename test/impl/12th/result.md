# 시험 결과 / Test Result — impl 12th (Epic 12 웹 콘솔)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi + web(React 19 · Vite 7) · local=SQLite / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 17, 통과 Pass: 17, 실패 Fail: 0, 최종 Verdict: **PASS**
- Vitest 23 passed (5 files) · `make web-build` 성공(tsc 포함) · pytest 132 passed
- Playwright 8/8 — prod 번들(`/console` via api) · vite 개발 서버(StrictMode) 양쪽

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E12-U1 ~ E12-U5 | PASS (round 5) | `logs/web-test.log` 23/23 |
| E12-B1 | PASS (round 2) | `logs/web-build.log` — 초기 JS 393KB(gzip 125KB), 페이지별 lazy 청크, Pretendard 2.0MB 로컬 |
| E12-S1 · E12-S2 | PASS | `logs/make-test.log` 132/132 |
| E12-01 ~ E12-08 | PASS (round 4) | `logs/e2e-prod-bundle.log` · `logs/e2e-vite-dev.log` · `logs/screenshots/` |
| E12-R | PASS | `logs/make-test.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | 셀·오류 상자 한국어 라벨 미발견 | 기본 언어를 `navigator.language` 로 정해 jsdom(en-US)에서 영어 | 기본 ko(운영 설정 `ui.language` 기본값과 일치), 사용자가 바꾸면 localStorage | round 2 |
| 1 | `ResizeObserver is not defined` | jsdom 미구현(cmdk) | 시험 setup 폴리필 | round 2 |
| 1 | PKCE 시험 실패 | 시험 벡터 기억 오류 — RFC 7636 B 의 verifier 는 `…mB92K27…` | 정확한 벡터 + 백엔드 `pkce_s256` 와 같은 결과 확인 | round 2 |
| 2 | `tsc` 오류 12건 | optional 체이닝 누락·Recharts formatter 타입·`@types/node` 부재 | 타입 보정, `@types/node` 추가 | round 3 |
| 2 | 잠금 파일 갱신 후에도 frozen 설치 실패 | Dockerfile `COPY web/ ./` 가 설치 후 갱신된 lockfile 을 호스트 구본으로 덮어씀 → 추출본이 구본 | 설치 직후 `/tmp/pnpm-lock.resolved.yaml` 사본, `make web-lock` 은 사본 추출 + `PNPM_FROZEN=0` | round 3 |
| 3 | e2e 로그인 직후 이동이 /login 으로 튕김 | 콜백의 토큰 교환 전에 다음 `goto` (시험 경합) | 로그인 헬퍼가 콜백 완료 URL 대기 | round 4 |
| 3 | 최종 사용자가 `/` 로 들어오면 /chat 대신 /console | 서버 `/`→`/console` 후 RequireAuth 가 next=/console 로 복귀 — 역할별 첫 화면 무시 (실제 UX 결함) | 콜백에서 복귀 경로가 `/`·`/console` 이면 역할별 첫 화면 | round 4 |
| 3 | 선택자 strict 위반 3건 | 같은 텍스트가 모달·요약에 중복 (시험 코드) | exact 매칭 | round 4 PASS |
| 4 | (dev 점검) 개발 모드 로그인 콜백·첫 메시지 전송 중단 위험 | StrictMode effect 이중 실행 → 인가 코드 재교환 실패, 언마운트 abort 가 첫 스트림 중단 | 인가 코드 교환 promise 단일화, 모의 언마운트 무시(지연 abort), 세션별 key 재마운트 | dev e2e 8/8 |
| 4 | dev e2e 첫 케이스 빈 화면 | `up web` 이 depends_on 으로 api 를 재생성하는 순간 프록시 ECONNREFUSED (환경) | 재실행 | 8/8 PASS |
| 5 | `make web-test` 종료코드 1 (23 passed) | Vitest 가 `e2e/*.spec.ts`(Playwright) 까지 수집 | `test.include = src/**/*.test.*` | PASS |

## 비고 / Notes

- 브라우저 스모크는 Chrome 확장 미연결로 Playwright 컨테이너로 대체(wds-test §4). 스크린샷 `logs/screenshots/`.
- 화면 ID 기준 약 60종 구현, 나머지는 REST·CLI(PRD D4).

## 첨부 / Logs

- `logs/` (make-test · web-test · web-build · e2e-prod-bundle · e2e-vite-dev · screenshots/)
