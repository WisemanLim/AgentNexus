# 시험 결과 / Test Result — impl 1st (Epic 1 기반)

- 일자 Date: 2026-10-06
- 대상 Target(profile): python-fastapi · local=SQLite / prod=PostgreSQL 17
- 커밋 Commit: — (git 저장소 아님)

## 요약 / Summary

- 전체 Total: 9, 통과 Pass: 9, 실패 Fail: 0, 최종 Verdict: **PASS**

## 케이스 / Cases

| id | 결과 result | 비고 note |
|----|-------------|-----------|
| E1-01 | PASS | pytest |
| E1-02 | PASS | pytest (주입 연결) |
| E1-03 | PASS (round 2) | pytest |
| E1-04 | PASS | pytest + local curl `{"valid":true}` |
| E1-05 | PASS | pytest |
| E1-06 | PASS | 스냅샷 `test/contract/openapi-paths.json` 최초 생성 |
| E1-07 | PASS | `logs/local-health.txt`, `logs/local-tables.txt` |
| E1-08 | PASS | `logs/prod-tables.txt` (48 · 0001) |
| E1-09 | PASS | `logs/prod-audit-concurrency.txt` |

## 발견 오류 및 수정 / Bugs & Fixes

| round | 오류 bug | 원인 cause | 수정 fix | 재시험 retest |
|-------|----------|------------|----------|----------------|
| 1 | 변조 복구 후에도 `verify.valid=false` | `expire_on_commit=False` 세션 identity map 에 변조 시점 객체가 남아 재검증이 캐시 값 사용 | `verify()` 조회에 `populate_existing=True` — 항상 DB 값으로 재계산 | round 2 PASS |
| 1 | autogenerate 마이그레이션이 `app.db.UTCDateTime` 을 import 없이 참조 | Alembic 이 TypeDecorator 를 모듈 경로로 렌더 | 마이그레이션은 `sa.DateTime(timezone=True)` 로 치환, `env.py` 에 `render_item` 훅 추가 | PASS |

## 첨부 / Logs

- `logs/` (make-test · local-health · local-tables · local-audit-verify · prod-build · prod-health · prod-migrate · prod-tables · prod-audit-verify · prod-audit-concurrency)
