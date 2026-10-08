# impl 53rd — 신원·레지스트리 경계 (Epic 53 · D20-②)

## 범위

| 항목 | 구현 |
|------|------|
| Access Token audience | 게이트웨이 API(`security.API_AUDIENCE`)·등록된 MCP 서버(`mcp:<id>`)·A2A 에이전트(`a2a:<id>`)만 — 그 밖은 400 `unknown_audience` (사용자 결정: 등록된 대상만) |
| admin 부여 | 사용자 생성·수정·IdP 역할 매핑의 `admin` 은 admin 만, 관리자 계정 수정도 admin 만 — 403 `admin_grant_forbidden` (사용자 결정) |
| IdP 삭제 | 그 IdP issuer 사용자 `tokens_not_before = now` + 갱신 토큰 폐기, 감사에 건수 |
| 새 버전 게시 | 기존 항목은 소유자 또는 `catalog:approve` 만, 미러 항목은 수동 게시 불가 — 403 `not_item_owner` |
| 미러 자동 승인 | `_meta.auth` 에 외부 인가 서버·토큰 엔드포인트·메타데이터가 있으면 자동 승인하지 않고 보류(감사 `registry.auto_approve.hold`) |
| push_verify | 바꾸거나 끄려면 시크릿 관리 권한(`gateway:write`) — 403 `push_verify_forbidden` |
| TOTP | 같은 코드는 사용자별 한 번(120초 기억, 워커 공유 Valkey) |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C53-01 | 게이트웨이·`mcp:jira`·`a2a:<에이전트>` 201 · 없는 서버·`model:`·URL 400 | `test_identity_boundaries.py::test_access_token_audience_allowlist` | pytest |
| C53-02 | 보안 담당의 admin 생성·승격·관리자 수정·admin 매핑 403 · developer 생성 201 · 관리자는 승격 가능 | `test_admin_grant_admin_only` | pytest |
| C53-03 | IdP 삭제 → 그 사용자 `tokens_not_before`·갱신 토큰 폐기 · 응답에 건수 | `test_idp_delete_signs_out_users` | pytest |
| C53-04 | 다른 소유자 항목의 새 버전 403 · 승인 권한자는 201 | `test_publish_new_version_owner_only` | pytest |
| C53-05 | 미러 자동 승인 — 일반 항목 approved · 외부 인가 항목 pending | `test_mirror_auto_approve_holds_external_auth` | pytest |
| C53-06 | 개발자 push_verify 변경 403 · 운영자 200 | `test_push_verify_change_needs_secret_manage` | pytest |
| C53-07 | 같은 TOTP 두 번째 로그인 400 | `test_totp_code_single_use` | pytest |
| 회귀 | 신원·SSO·검토 수정·카탈로그·A2A·운영·하드닝·CLI·설정·관리 | 13개 파일 | pytest |
