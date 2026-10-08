# impl 65th — 4차 품질 검토 (Epic 65 · D22-⑤) + 13차 완료 게이트

## 범위

| 항목 | 내용 |
|------|------|
| 대상 | 13차(Epic 61~64·66) 변경분 — 코드의 `D22-` 표시 hunk(28개 파일 63곳) + 표시 없는 Helm 변경 5개 파일 |
| 위임 검토 | 보안(`/security-review` 폴백) · 결함(`/code-review` 폴백) → `.review/round4/delegated/` 원문 |
| 라인 리뷰 | Lv2(블록 모드, 하위 에이전트 없이) — `.review/round4/CODE-REVIEW-Lv2/` (INDEX·파일별 10·INFRA) |
| 검증 | `.review/round4/VERIFY.md` — 확인된 결함만 수정, 나머지 근거 기록 |

## 수용 기준 → 시험 케이스 (확인된 결함)

| # | 결함 (출처) | 수용 기준 | 시험 |
|---|-------------|-----------|------|
| C65-01 | 출력 예약이 버킷을 잠금·`max_tokens` 무검증 (Lv2 M1) | 출력 예약은 남은 여유분까지(1000 한도에 1000) · 정산 반환 · -5·0·"abc"·true·1.5 → 400 · 모델 컨텍스트로 자름 | `test_review4_fixes.py::test_output_reservation_bounded_and_validated` |
| C65-02 | 대량 작업이 세션 상한에 걸림 (Lv2 M2·L16) | PG 에서 보존 정리·감사 머리 잠금에 `SET LOCAL … = 0`(DELETE 앞) · SQLite 는 없음 | `test_bulk_jobs_unbounded` |
| C65-03 | 2단계 회전 1단계 파드 재암호화·재암호화 중 회전 (Lv2 M3·L5) | 키 지문 반환 · 세대 바뀌면 이전 키 제거 409 `keys_changed` · NEXT 남은 파드 409 `rotation_in_progress` | `test_reencrypt_guards` |
| C65-04 | IdP 정리 뒤 PAT·클라이언트 키 생존 (Lv2 M4) | 삭제 뒤 두 토큰 모두 폐기 | `test_idp_removal_revokes_pats` |
| C65-05 | 현재 kid 폐기 무효·EC 이전 키 (CR4-04·Lv2 M5·L6) | 현재 kid 폐기 → 키 적재 RuntimeError · EC 공개키 → ValueError | `test_revoked_current_key_and_non_rsa` |
| C65-06 | 실패·취소 경로 예약 누수 (CR4-01·Lv2 L1) | 비스트림 예상 밖 오류 뒤 창 0·진행 중 0 · `check` 예외에도 진행 중 0 | `test_unexpected_failures_release_holds` |
| C65-07 | 진행 중 상한 사용자 단위 (Lv2 L4) | 클라이언트 키 = `inflight:key:<id>` | `test_inflight_key_per_client_key` |
| C65-08 | 익명 한도 XFF 우회·메모리 버킷 무한 (SEC4-01·Lv2 L12) | 전체 버킷 3번째 429 · 버킷 수 ≤ 상한 | `test_anonymous_total_bucket_and_memory_cap` |
| C65-09 | JWKS 강제 갱신 워커별 (SEC4-02) | 첫 호출만 True, 이어지는 호출 False(고정 만료) | `test_forced_jwks_refresh_shared` |
| C65-10 | 등록 경로·사용 시점 push_verify (Lv2 L9) | 등록 발급자 없음 400 · 개발자 403 · 운영 http 는 검증 시 거부 | `test_push_verify_checked_on_register_and_use` |
| C65-11 | 큰 artifact 하나가 목록을 비움 (Lv2 L10) | 큰 항목만 빠지고 a0~a4 유지 | `test_oversized_artifact_skipped` |
| C65-12 | OpenAPI 실패 정리 (CR4-03·Lv2 L11) | OBO 정책 정리 · 등록된 뒤 실패면 시크릿 유지 | `test_openapi_failure_cleans_only_own_rows` |
| C65-13 | 매니페스트 밖 백업 복원 (Lv2 L17) | `--restore-config` 여도 복원 호출 없음 | `test_restore_only_manifest_backup` |
| C65-14 | 샘플링 실패 미기록 (Lv2 L19) | 라우팅 결정 1건 status=error · 진행 중 0 | `test_sampling_failure_recorded` |
| C65-15 | readiness 잠금 대기 (Lv2 L20) | 확인 중 두 번째 요청 0.3초 안 | `test_readiness_does_not_queue` |
| C65-16 | 사용자 검색 와일드카드 (CR4-05) | `q=_` 에 admin 없음 | `test_user_search_escapes_wildcards` |
| C65-17 | 빈 스코프 대상 토큰 (Lv2 L8) | 정책 기본 스코프 | `test_empty_scopes_use_policy_defaults` |
| 갱신 | C62-03(다음 키는 키 수에 넣지 않음 → 1)·C59-10(상관 하위 질의, Lv2 L13)·C64-01(정책 없는 전용 서버 — 전체 실행 순서 의존 제거) | — | `test_key_mgmt.py`·`test_review3_fixes.py`·`test_boundaries4.py` |

기록만: CR4-06(Valkey/Redis 7+ 문서)·CR4-07·L3·L14(업그레이드 노트)·L15(PgBouncer 문서) — `.review/round4/VERIFY.md`.

## 완료 게이트 (13차)

| 단계 | 내용 |
|------|------|
| 회귀 | `make test` · `make test-pg` · `make web-test` · web-build + web-e2e · `make helm-check` |
| prod 리허설 | `make prod-build` → `make db-migrate ENV=prod`(0021) → `make db-ping ENV=prod` → `smoke-prod.sh`(P65-01~05) + 59th 스모크 + 회귀 사본(36th~55th) → 정리 → `make prod-stop` → `make local-all` |
