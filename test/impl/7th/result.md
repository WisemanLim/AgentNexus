# 시험 결과 / Test Result — impl 7th (Epic 7 A2A 경로)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite+Valkey+mcp-servers+a2a-demo / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 17, 통과 Pass: 17, 실패 Fail: 0, 최종 Verdict: **PASS**
- pytest: SQLite 86 passed · PostgreSQL 86 passed

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E7-01 ~ E7-15 | PASS (round 4) | `test/test_a2a.py` (15 케이스) |
| E7-16 | PASS | `logs/make-test-pg.txt` |
| E7-17 | PASS (round 2) | `logs/smoke-local.txt` 10/10 |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | 감시기 시험 state=working | 즉시 1회 폴링 시 원격 작업이 아직 진행 중 (시험 설계) | 주기 폴링(최대 10s)으로 시험 | round 2 |
| 2 | 푸시 중계 미수신 | 푸시는 비동기(fire-and-forget) 전달 — 응답 직후 단언 | 최대 3s 대기 후 단언 | round 3 |
| 3 | 보안 담당 등록 403 | 시험 오류 — 등록(a2a:write)과 신뢰 등급(policy:write)은 직무 분리 | "개발자 등록 → 미신뢰 거부 확인 → 보안 담당 등급 지정" 으로 시험 보강 | round 4 PASS |
| smoke 1 | S7-03/04 jq 파싱 실패 | `sh` 의 `echo` 가 JSON 내 `\n` 을 해석 | `printf '%s'` 사용 (스크립트) | round 2 |
| smoke 1 | S7-09 SDK 연결 실패 | 게이트웨이 카드 URL 이 고정 `PUBLIC_BASE_URL` 이라 컨테이너 내부 클라이언트가 도달 불가 | 카드 URL 을 **요청 base URL** 기준으로 생성 (플랫폼 카드 포함) | round 2 PASS |

## 설계 메모

- A2A 1.0(a2a-sdk 1.2.2 기본)과 0.3(PRD 9.2 명명)을 모두 수용: 내부 정준형 0.3 + 경계 변환, 이중 카드(0.3 필드 + `supportedInterfaces`).

## 첨부 / Logs

- `logs/` (make-test · make-test-pg · smoke-local) · `smoke.sh`
