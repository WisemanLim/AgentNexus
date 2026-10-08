# impl 24th — 남은 설계 요소 (Epic 24 · D13-① · SCR-H02·H13·F04·F06·B05·G01·G04·G07)

## 범위

| 항목 | 구현 |
|------|------|
| H02 라우팅 규칙 게시 | `POST …/routing/rules/{id}/publish {reason}` (사유 필수, 변경 없으면 `no_changes`) · `GET …/versions`(버전별 사유·게시자·직전 대비 변경 + 게시 안 된 변경) · `POST …/rollback {version, reason}`(스냅샷 적용 후 새 버전) · 목록 `version`·`unpublished_changes` |
| H13 키별 허용 모델 | `POST /api/v1/me/tokens {kind: client_key, allowed_models}` · `PATCH /api/v1/gateway/client-keys/{id}` · `/v1/chat/completions`·`/v1/embeddings` 에서 목록 밖 별칭 403 `model_not_allowed` |
| F04 hosted OpenAPI OBO | `POST /api/v1/mcp-servers/openapi {identity: "obo", scopes}` → 교환 정책 `api:{ns}` 생성 · 도구 호출마다 호출 사용자 토큰을 교환(RFC 8693)해 `Authorization: Bearer` 주입(ContextVar, 서버 객체 공유) · 서비스 시크릿 미사용 · 교환 실패 = 호출 안 함(`token_exchange_failed`) |
| F06 시험 전용 가상 사용자 | `POST …/inspect {test_principal: {roles, team}}` — sub `test:{호출자}`, 내 권한보다 넓은 역할 403 `test_roles_exceed` · 쓰기·위험 도구는 호출 없이 정책 판정만(드라이런) · 감사 `mcp.inspect.test_principal`(actor = 실제 호출자) |
| B05 구성요소 | `GET /api/v1/system/components` 에 `health`(healthy·degraded·disabled)·`errors_1h`·`restarts_24h`(워커별 시작 로그)·`last_error`·`detail`(MCP 헬스 실패·스팬 저장 실패) |
| G01 framework | `a2a_agents.framework`(운영자 지정) 또는 Agent Card 힌트(provider·문서 URL·확장) — `framework_source` |
| G07 파일 Part | `POST …/a2a/agents/{id}/try {files: [{name, mimeType, bytes|uri}]}` — base64 검증·합계 `ATTACHMENT_MAX_MB` · 콘솔 send 는 파일도 시험 API 로 |
| G07 푸시 서명 | 에이전트 `auth.push_verify {jwks_url, issuer?}` → `/a2a/push/{token}` 이 JWT 서명(JWKS)·발급 ±5분·`request_body_sha256` 확인, 실패 401 + 감사 `a2a.push.reject` |

마이그레이션 0014: `routing_rules.version` · `routing_rule_versions` · `personal_tokens.allowed_models` · `a2a_agents.framework`.

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C24-01 | 빈 사유 422 · v1 게시 · 같은 내용 재게시 `no_changes` · 수정 → 게시 안 된 변경 · v2 → v1 되돌리기 = v3(설정 복원) · 이력 3건 · 개발자 403 | `test_routing_rule_publish_versions_and_rollback` | pytest |
| C24-02 | 미지 별칭 400 · PAT 에 지정 400 · 허용 별칭 200 · 그 외 403 · 목록 표시 · 해제 후 200 · 개발자 PATCH 403 | `test_client_key_allowed_models` | pytest |
| C24-03 | OBO 생성 → 정책 · 업스트림 Authorization = 교환 토큰(aud `api:{ns}`·sub 호출자·scope) · 서비스 시크릿 미전송 · 정책 끄면 `token_exchange_failed` | `test_hosted_openapi_obo_propagates_user_identity` | pytest |
| C24-04 | ERROR 로그 → gateway degraded·last_error · 시작 로그 기록 | `test_component_health_restarts_and_last_error` | pytest |
| C24-05 | 카드 힌트 ADK · 운영자 지정 우선 · 파일 Part 전달 · 잘못된 base64·file:// 거부 | `test_a2a_framework_and_file_parts` | pytest |
| C24-06 | 서명 없음·다른 키·오래된 iat·본문 해시 불일치 401 · 올바른 서명 200 → 상태 반영 · 감사 | `test_push_notification_jwt_verification` | pytest |
| C24-07 | 가상 사용자 읽기 호출 · 쓰기 도구 드라이런 · 넓은 역할 403 · 미지 역할 400 · 감사 actor | `test_inspector_virtual_test_principal` | pytest |
| E24-01~05 | 게시·이력 · 허용 별칭 발급 · 가상 사용자 호출·OBO 선택 · 구성요소·푸시 JWKS 설정 · 런타임 오류 0 | `web/e2e/gaps.spec.ts` | Playwright |
| P24-01~ | prod: 0014 · 게시·되돌리기 · 허용 별칭 403/200 · 구성요소 헬스·재시작 · framework · 가상 사용자·넓은 역할 거부 · OBO 교환 기록 · 서명 없는 푸시 401 · 파일 Part | `smoke-prod.sh` | live |
| R | 전체 회귀 | logs | make |

## 환경 가정

- 라우팅 규칙 편집은 지금처럼 저장 즉시 적용되고, "게시"는 되돌리기 기준점(스냅샷)을 남기는 동작이다 — 별도 승인 단계는 두지 않았다(정책은 2인 검토, 라우팅은 운영자 권한).
- 허용 모델은 요청 모델 이름 기준이다(`auto` 를 허용하려면 목록에 `auto`).
- framework 는 A2A 표준 필드가 없어 카드 힌트로 추정하고 운영자 지정이 우선한다.
- 가상 사용자는 실제 사용자를 대리하지 않는다(D13-①). 쓰기 판정은 ToolRule 위험도(low 외)·승인 필요 여부 기준.
