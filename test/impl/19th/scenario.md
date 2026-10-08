# impl 19th — 외부 IdP SSO 로그인 (Epic 19 · FR-I00·I01 · SCR-A10·J01·J02 · PRD D12-③)

## 범위

| 항목 | 구현 |
|------|------|
| 로그인 브로커 | 내장 IdP 가 Keycloak 처럼 외부 OIDC 로 로그인 위임 — `/idp/authorize?idp_hint=` · `/idp/broker/{id}/start` → 외부 authorize(전용 PKCE S256·state·nonce) → `/idp/broker/callback` 에서 서버 측 토큰 교환(client_secret)·id_token JWKS 검증·nonce 확인 → 그룹·역할 클레임 → 콘솔 역할 동기화 → 원래 인가 요청(콘솔 PKCE)에 코드 발급 |
| 분리 DNS | IdP `internal_url` — 브라우저는 issuer 주소, 서버 측 discovery·token·jwks 는 내부 주소 |
| 원인별 오류 (A10) | `access_denied` · `invalid_state` · `idp_unavailable` · `token_invalid` · `user_disabled` · `idp_disabled` 을 원래 redirect_uri 로 돌려주고 콘솔 로그인 화면이 원인별 안내 |
| 로그인 화면 (A10) | `GET /idp/providers` — 사용·로그인 노출 IdP 버튼, 내장 IdP 로그인 페이지에도 버튼 |
| J02 마법사 | ① 종류 ② Issuer·디스커버리(저장 전 `POST /identity/idps/discover`: issuer 일치·JWKS·PKCE S256 게이트) ③ 클라이언트·클레임(등록할 Redirect URI 표시) ④ 초안 저장 → 시험 로그인(새 창, 결과만 저장) → 성공해야 로그인 노출 |
| J01 목록 | 사용·로그인 노출 토글 · 디스커버리 · 시험 로그인 · 편집(클라이언트·내부 주소·스코프·매핑) · 삭제(MSG-002) |
| 데모 외부 IdP | `/_demo/oidc` (APP_ENV local·test 전용, prod 미탑재) — Code+PKCE·client_secret_post·RS256 id_token(groups·nonce)·JWKS, 계정 `partner-kim`(anx-developers → developer)·`partner-lee`(매핑 없음 → user) |

마이그레이션 0008: `idp_connections.login_enabled·internal_url·scopes·last_test` · `broker_requests`.

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C19-01 | 외부 IdP 로그인 → 콘솔 토큰 · sub `ext:<idp>:<sub>` · 그룹 → developer · 로그인 화면 버튼 | `test_console_sso_via_external_idp_maps_groups_to_roles` | pytest |
| C19-02 | 매핑 없는 그룹은 최소 권한 user | `test_unmapped_groups_get_least_privilege_user_role` | pytest |
| C19-03 | 취소·state 재사용·비활성 사용자·비활성 IdP → 원인 코드 | `test_sso_error_causes_are_returned_to_console[*]` (4) | pytest |
| C19-04 | 저장 전 디스커버리 · 시험 로그인(대기 → 결과·역할) · 상태 저장 · state 1회용 | `test_idp_wizard_discover_save_test_login_then_enable` | pytest |
| C19-05 | 시험 성공 전 로그인 노출 거부 · 편집·비활성·삭제 | `test_login_enable_requires_successful_test` | pytest |
| E19-01~04 | 버튼 로그인 → developer 첫 화면 · 취소 안내 · 마법사 게이트 · 목록 시험 로그인(새 창) | `web/e2e/sso.spec.ts` | Playwright |
| P19-01~10 | prod: 0008 · 데모 IdP 비탑재(404·목록 0) · 알 수 없는 힌트·state·redirect_uri 안전 실패 · 도달 불가 issuer 500 없음 · 시험 전 노출 거부 | `smoke-prod.sh` | live |
| R | 전체 회귀 | logs | make |

## 환경 가정

- 실제 Entra ID·Okta 테넌트 없음(PRD D8) → 같은 프로토콜의 데모 외부 IdP 로 브로커 전 구간을 시험한다. 실 IdP 는 issuer·Client ID·Secret·그룹 매핑만 바꾼다.
- 에어갭 프로파일에서 외부 IdP 버튼 숨김은 Epic 20(ST-09) 범위.
