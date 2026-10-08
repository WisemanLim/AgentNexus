# 시험 결과 / Test Result — impl 23rd (Epic 23 운영·관측 화면 깊이)

- 일자 Date: 2026-10-07
- 대상 Target(profile): python-fastapi + web · local=SQLite / prod=PostgreSQL 17 (uvicorn 2 workers)
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 70 (pytest 14 · e2e 12 · prod live 44 — R 제외), 통과 Pass: 70, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest SQLite 224 passed + 1 skipped · PostgreSQL 225 passed · Vitest 26 · 빌드(tsc) · Playwright 58/58(전체) · prod 44/44 (게이트 재실행 round 3)
- 화면: 병렬 UI 작업 9묶음(B·F·G·H01/04/09/12·H02~H15·I·J·K·L) → 백엔드 보강 요청을 모아 반영 → 통합 시험

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| C23-01 ~ C23-09 | PASS (round 2) | `test/test_ops.py` — round 1 에서 위임받은 승인자 404 (실제 결함) |
| C23-10 ~ C23-13 | PASS (round 1~2) | UI 작업 보고로 드러난 공백 반영 후 추가 (IdP 중지 세션 종료 · 내 클레임 · 세션 핸들 · A2A 전이/OAuth2) |
| C23-14 | PASS (round 1) | 3차 정합성 재검토(문서)에서 발견 — RAG 색인 입력 소독 공백 (COMPLIANCE T1) |
| C23-R | PASS | `test_admin.py`(복원 확인·0013) · `test_cli.py`(bundle import 확인 전달) · `test_deploy.py`(차트 ConfigMap 새 키) |
| E23-01 ~ E23-12 | PASS (round 2) | `web/e2e/ops.spec.ts` · 전체 회귀 `logs/web-e2e.log` 58/58 |
| P23-01 ~ P23-44 | PASS (round 2·3) | `logs/smoke-prod-run1.log`(41/44) → 수정 → `smoke-prod-run2.log` 44/44 → RAG 소독 반영 후 `smoke-prod-run3.log` 44/44 |
| R | PASS | `logs/make-test.log`·`make-test-round2~3.log` · `logs/make-test-pg.log`·`make-test-pg-round2~3.log` · `logs/web-test.log` · `logs/web-e2e.log` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| pytest 1 | 위임받은 승인자가 승인하면 404 (실제 결함) | 조회 권한이 요청자·`approvals:decide` 만 허용 | 목록·조회에 `delegated_to` 포함 | round 2 PASS |
| pytest 전체 1 | CLI `bundle import` 실패 (회귀) | 복원 API 가 `confirm: "RESTORE"` 를 요구하게 됨 | `--restore-config` 를 명시 동의로 보고 확인 값 전달 | PASS |
| pytest 전체 2 | 차트 ConfigMap 에 새 설정 없음 (배포 정합성) | `UVICORN_WORKERS`·`ANX_TARGET_VERSION`·`COSIGN_PUBLIC_KEY` 추가 | ConfigMap·values 반영, `make helm-check` OK | PASS |
| UI 보고 | "현재 세션"을 가장 최근 세션으로 추정 — 다른 기기 로그인 후 '다른 세션 종료'가 내 세션을 끊음 (실제 결함) | 토큰에 세션 식별자 없음 | 액세스 토큰 `sid`(refresh id) → 현재 세션 정확 판정 | C23-01 PASS |
| UI 보고 | 구성요소 복제 수가 prod 에서 1 (실제 결함) | Dockerfile 이 `--workers 2` 고정, env 없음 | `UVICORN_WORKERS` env (이미지·.env·Helm) | P23-41 PASS |
| UI 보고 | 화면을 열 때마다 우회 점검 감사 행 생성 | 카운터가 POST 응답에만 있음 | `GET`(감사 없음) 현황 + 「지금 점검」만 `POST` · 401 카운터(Prometheus) 추가 | C23-04·P23-42 PASS |
| UI 보고 | MCP 세션 강제 종료 불가 · 원 ID 를 내보내면 세션 자격 노출 (보안) | 목록이 ID 를 잘라 줌 | SHA-256 핸들로 목록·종료 | C23-12 PASS |
| UI 보고 | IdP 를 꺼도 그 IdP 사용자 세션 유지 · 마법사가 시험 전 IdP 를 활성화(토큰 수용) (보안) | 중지 시 세션 처리 없음 · 시험 로그인이 활성 IdP 만 | 중지 = 세션 종료(`sessions_ended`) · 비활성 초안도 시험 로그인 · PATCH 역할 검증 | C23-10 PASS |
| UI 보고 | 공급자 ZDR·허용 등급이 저장만 되고 적용 안 됨 | 필드만 있고 라우팅·어댑터 미반영 | 허용 등급 밖 후보 제외(403) · OpenAI `store:false` | C23-12 PASS |
| UI 보고 | 복원 직전 상태가 브라우저 다운로드에만 남음 | 서버 스냅샷 없음 | 복원 전 `backup-*-prerestore.json` 서버 저장 · 마지막 복구 시험 표시 | C23-09 PASS |
| UI 보고 | A2A Task 전이 시각·수신 이벤트 없음 · OAuth2 원격 인증 미지원 | 기록 열 없음 · 주입 방식 없음 | 0013 `a2a_tasks.events` · client_credentials 발급·캐시 | C23-13·P23-44 PASS |
| e2e 1 | 표 머리글·라벨 로케이터 2건 | 시험 로케이터(머리글 role · label 이 option 문구 포함) | thead 문구 · 제거 버튼으로 | round 2 PASS |
| live 1 | 권한 시뮬레이터가 방금 되돌린 행렬을 반영하지 않음 (실제 결함 — 다중 워커) | 시뮬레이터가 워커별 5초 캐시를 읽음 | 시뮬레이터는 저장된 행렬을 직접 읽음 | round 2 PASS |
| 문서 재검토 | COMPLIANCE T1 'RAG 색인 입력 소독'이 구현되지 않음 (실제 공백) | Epic 18 RAG 가 원문을 그대로 색인 | 색인 전 `sanitize_tool_text`(지시문 제거·PII·시크릿 마스킹), 차단 규칙이면 거부 | C23-14 PASS · 게이트 round 3 |
| live 1 | 설정 되돌리기·리포트 제목 판정 2건 | 스크립트가 `history_id` 를 따옴표 없이 보냄 · 제목 뒤 리포트 ID | 시험 스크립트 수정 | round 2 PASS |

## 리허설 잔여 데이터 (prod 리허설 DB)

- 바꾼 설정(RBAC 행렬·IP 정책·시간대·샘플링·알림 음소거)은 원래 값으로 되돌림. 리허설 한도는 삭제, 임시 계정 3개 비활성화.
- 남은 것: 리허설 시크릿 `e23p/leak-*`(유출 점검용 임의 값) · 리허설 로그·승인·A2A Task · 데이터 볼륨 `backups/` 의 리허설 백업 파일.

## 첨부 / Logs

- `logs/` (make-test(·round2~3) · make-test-pg(·round2~3) · web-test · web-e2e · local-stop(·round3) · prod-build(·round2~3) · db-migrate-prod(·round3) · db-ping-prod(·round3) · smoke-prod-run1~3 · prod-stop(·round3) · local-restore(·round3))
