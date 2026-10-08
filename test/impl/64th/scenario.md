# impl 64th — 경계 하드닝 4 (Epic 64 · D22-④)

## 범위 (3차 검토 Lv2 M12·L15·L17·L18·L20·L21·L24·L25 — 59th 에서 '기록'으로 둔 것, M12 는 13차 사용자 결정)

| 항목 | 내용 |
|------|------|
| Access Token 스코프 (M12, 사용자 결정 "정책 허용 스코프로 제한") | `mcp:`·`a2a:` audience 는 그 대상 토큰 교환 정책의 `allowed_scopes` 안만 — 정책 없으면 400 `no_exchange_policy`, 밖 스코프 400 `scope_not_allowed`(+`allowed`). 게이트웨이 audience 는 그대로 |
| 푸시 JWKS (L17) | 모르는 kid → JWKS 를 한 번 다시 받음(같은 URL 30초에 1회 — 위조 kid 로 계속 부르지 못함) · 운영(`APP_ENV=prod`)은 `jwks_url` https 만 · 발급자 확인을 JWKS 조회 앞으로 · 에이전트 응답에 `push_verify_issuer`(D21 이전 설정 식별) |
| artifacts (L15) | Task artifacts 최근 50 개·직렬화 4 MiB 안 |
| OpenAPI 생성 (L20) | 쓰기 전에 namespace·같은 이름 시크릿(`mcp/<ns>/backend`) 확인 → 409 `namespace_taken`·`secret_exists` (교환 정책도 그 뒤에 생성) · 게시·등록 실패 시 이번에 쓴 시크릿 삭제 |
| Inspector (L21) | 드라이런도 `policy.declared_data_class` 로 서버 판정 등급 |
| 발견 API (L24) | `/v0/servers?search=` = 이름 부분 일치(MCP 레지스트리 사양) — SQL `LIKE`(와일드카드 이스케이프)·SQL 페이지 · 익명 호출은 IP 별 5 rps·버스트 30 (429), 인증 호출은 한도 밖 |
| 소유자 없는 항목 (L25) | 새 버전 게시는 승인 권한자만 (전에는 누구나) |
| gRPC readiness (L18) | 위임 대상 `/health` 결과를 2초 공유(잠금) — 인증 없는 공개 readiness 가 DB 로 증폭되지 않음 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C64-01 | 정책 없음 400 · 밖 스코프 400(+allowed) · 정책 안 201 · 게이트웨이 audience 임의 스코프 201 | `test_boundaries4.py::test_access_token_scopes_follow_exchange_policy` | pytest |
| C64-02 | 회전한 kid 로 통과(조회 2회) · 30초 안 위조 kid 는 재조회 없이 거부 · prod http 400 · 발급자 없는 설정 `push_verify_issuer=false` | `test_push_jwks_refresh_https_and_issuer_flag` | pytest |
| C64-03 | 80 개 → ≤50, 마지막 = 새 artifact, 총량 상한에서 오래된 것부터 제외 | `test_artifacts_capped` | pytest |
| C64-04 | 기존 시크릿 409·값 그대로 · 게시 실패 뒤 시크릿 없음 | `test_openapi_secret_preexisting_and_cleanup` | pytest |
| C64-05 | 드라이런 판정 등급 = 서버 판정 값 | `test_inspector_dry_run_uses_server_data_class` | pytest |
| C64-06 | `search=JIRA` → 이름에 jira 만 · `name_contains` 로 SQL 경로 · 익명 3번째 429 · 인증 200 | `test_v0_anonymous_search_sql_and_limit` | pytest |
| C64-07 | 개발자 403 `not_item_owner` · 운영자 201 | `test_ownerless_item_new_version_approver_only` | pytest |
| C64-08 | readiness 5번 → `/health` 1번, 공유 시간 지나면 1번 더 | `test_grpc_readiness_cached` | pytest |
| 회귀 | C53-01(대상용 토큰은 빈 스코프로 갱신)·신원·카탈로그·A2A·설계 보완 | `test_identity_boundaries.py`·`test_identity.py`·`test_hardening.py`·`test_catalog.py`·`test_a2a*.py`·`test_design_gaps.py`·`test_remaining_items.py`·`test_mcp_modern_ext.py`·`test_deploy_hardening.py` | pytest |
