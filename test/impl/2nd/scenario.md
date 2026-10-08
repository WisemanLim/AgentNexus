# 시나리오 / Scenario — impl 2nd · Epic 2 Identity & Authorization (FR-I00~I04, L03)

| id | 목적 | 입력 | 기대 | 우선 |
|----|------|------|------|------|
| E2-01 | 미인증 차단 | 토큰 없이 관리 API | 401 + `WWW-Authenticate: Bearer` | P0 |
| E2-02 | 역할별 인가(RBAC) | user/security/dev/admin 토큰 | user→감사 403, security→200, dev→사용자목록 403 | P0 |
| E2-03 | OIDC 디스커버리·JWKS | discovery → jwks 로 토큰 서명 검증 | issuer 일치, 서명 유효 | P0 |
| E2-04 | Authorization Code + PKCE (FR-I01) | 콘솔 공개 클라이언트 | PKCE 없음·잘못된 redirect 400, 잘못된 verifier/코드 재사용 invalid_grant, id_token nonce | P0 |
| E2-05 | Refresh 회전 | refresh 2회 | 새 토큰, 이전 refresh 재사용 거부 | P1 |
| E2-06 | Device Flow (CLI, SCR-A11) | device/code → pending → slow_down → 승인 → 토큰 | 토큰 발급, `/me` sub 일치 | P0 |
| E2-07 | PAT (SCR-B06) | 발급 → 사용 → 폐기 | 평문 1회 노출, 폐기 후 401 | P0 |
| E2-08 | 워크로드 아이덴티티 (FR-I04) | client_credentials | 잘못된 secret 401, 허용 밖 스코프 미부여, 비활성 후 401, SPIFFE ID | P1 |
| E2-09 | RFC 8693 토큰 교환 (FR-I02) | user 토큰 → mcp:jira-test | aud·scope·ttl(300)·act 정확, 비허용 스코프·정책 없음 거부, 교환 이력 issued/denied, 교환 토큰은 관리 API 401 | P0 |
| E2-10 | Access Tokens (FR-L03) | 발급(600s) → 사용 → 폐기 | user 403, TTL 정확, 폐기 후 401, 상태 revoked | P0 |
| E2-11 | 외부 OIDC 연합 (FR-I00, Entra 모사) | issuer·JWKS MockTransport, 그룹→역할 매핑 | 연결 시험 OK, JIT 사용자, 역할 매핑, 잘못된 aud 401, 시크릿 마스킹 | P0 |
| E2-12 | 사용자·역할 관리 (SCR-J03) | 생성·역할 변경·비활성 | 잘못된 역할 400, 역할 변경 즉시 반영, 비활성 401 | P1 |
| E2-13 | 감사 연계 | 교환·로그인 후 체인 검증 | token.exchange 이벤트 존재, chain valid | P0 |
| E2-14 | PostgreSQL 회귀 | `make test-pg` | 전 케이스 PASS | P0 |
| E2-15 | local 스택 curl 스모크 | `smoke.sh` | 12/12 | P0 |
| E2-16 | prod 리허설 | `smoke-prod.sh` | password grant 비활성, Code+PKCE 로 admin 로그인, 체인 valid | P0 |
