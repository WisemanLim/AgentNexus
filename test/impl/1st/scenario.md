# 시나리오 / Scenario — impl 1st · Epic 1 기반

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E1-01 | 마이그레이션이 모든 모델 테이블 생성 | `alembic upgrade head` (pytest, SQLite) | metadata 테이블 ⊆ DB 테이블 | P0 |
| E1-02 | 마이그레이션 왕복 | upgrade → downgrade base → upgrade | 오류 없음 | P0 |
| E1-03 | 감사 해시 체인 변조 탐지 | 내용 수정 / 꼬리 삭제 | `content_tampered` / `tail_truncated`, 복구 시 valid | P0 |
| E1-04 | 감사 조회·검증 API | `GET /api/v1/audit/events`, `/verify` | 200, valid | P0 |
| E1-05 | 공통 오류 형식 | 없는 경로 | 404 `application/problem+json`, `code=not_found` | P1 |
| E1-06 | OpenAPI 계약 (COMPLIANCE) | 스냅샷 비교 | 삭제 0 · 미등록 추가 0 · 모든 op 에 tags | P0 |
| E1-07 | local 기동 시 entrypoint 자동 마이그레이션 | `make local-build` | `/health` 200, `alembic current = 0001 (head)`, 48 테이블 | P0 |
| E1-08 | prod(PostgreSQL) 마이그레이션 | `make prod-build` → `make db-migrate ENV=prod` | 48 테이블, `alembic_version=0001` | P0 |
| E1-09 | PG 동시 append 체인 무결성 | 8 스레드 × 40 append | `verify.valid=true`, checked=40 | P0 |
