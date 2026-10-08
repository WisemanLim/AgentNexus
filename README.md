# AgentNexus

MCP + AI Gateway + A2A 통합 에이전트 플랫폼. 사용자는 자연어로 요청하고, 모든 모델·도구·원격 에이전트 호출은 하나의 AI 게이트웨이를
거쳐 정책·한도·가드·신원 전파·관측이 적용된다.

> 기준 문서: [PRD.md](PRD.md) (원본 `.docs/AgentNexus_PRD.pdf` · `.docs/AgentNexus_UI_Design.pdf`) · 구현 계획 [docs/implementation-plan.md](docs/implementation-plan.md)
> · 규제 오버레이 [COMPLIANCE.md](COMPLIANCE.md) · English: [README.en.md](README.en.md)
> 스택: Python 3.12 · FastAPI · SQLAlchemy 2 + Alembic · uv / React 19 · Vite 7 · Tailwind 4 / PostgreSQL 17 · Valkey 8 · (선택) OPA

## 구성요소

| 구성요소 (`COMPONENTS`) | 내용 | 주요 경로 |
|---|---|---|
| `identity` | 내장 OIDC IdP(Code+PKCE·Device·Client Credentials·Refresh 회전)·RBAC 6역할·PAT·RFC 8693 토큰 교환·워크로드 ID·외부 IdP 연동 | `/idp/*` · `/api/v1/identity/*` · `/api/v1/me` |
| `gateway` | Unified Model API(OpenAI 호환)·라우팅·폴백·데이터 등급 재라우팅·정책(내장/OPA)·레이트리밋·예산·가드·도구 거버넌스·사람 승인·MCP/A2A 프록시·MCP sampling/elicitation 중계·동의·업스트림 mTLS | `/v1/chat/completions` · `/v1/models` · `/mcp/{id}` · `/a2a/{id}` · `/api/v1/gateway/*` |
| `registry` | 통합 카탈로그(MCP·A2A·모델·프로파일)·MCP Registry 호환 `/v0`·게시 승인·소스 미러·SBOM | `/v1/catalog` · `/v0/servers` · `/v0/publish` |
| `host` | 자연어 세션·SSE·Planner→Retriever/Summarize/Executor/원격 A2A 오케스트레이션·승인 재개·프로파일·템플릿 3종 | `/api/v1/sessions` · `/api/v1/runs` · `/api/v1/agents` · `/a2a/platform` |
| `observe` | OTel(GenAI 규약)·내장 trace 저장소·OTLP 수신·`/metrics`·로그(PII 마스킹)·Usage & Cost·해시 체인 감사·서명 리포트 | `/api/v1/traces` · `/api/v1/logs` · `/api/v1/usage` · `/api/v1/audit/*` · `/v1/traces` |
| `admin` | 배포 프로파일·커넥터·헬스·백업/복구·설정·알림·전역 검색·첫 실행 | `/api/v1/admin/*` · `/api/v1/system/*` |
| `console` | 웹 콘솔 SPA (UI 설계서 100 화면 중 웹 95종 — 설치 A01~A04·A11 은 CLI) | `/console` · `/chat` · `/login` · `/setup` |

`COMPONENTS=all` 이 기본이며, 부분 집합으로 독립 실행한다(FR-W01) — 예: `gateway,identity`(게이트웨이 단독), `registry,identity`(레지스트리 단독).

## 실행 모델

실행은 **docker compose 전용**, 모드는 **local / prod**.

| 모드 | compose 파일 | env 파일 | DB | 이미지 스테이지 |
|------|--------------|----------|----|----------------|
| local | `docker-compose.yml` + `docker-compose.local.yml` | `.env.local` (커밋) | SQLite (`/app/data/app.db`) | `dev` (소스 바인드·핫리로드) |
| prod | `docker-compose.yml` + `docker-compose.prod.yml` | `.env.prod` (비커밋, CHANGE_ME) | PostgreSQL 17 | `runtime` (콘솔 번들 포함, uvicorn 2 workers) |

`compose/*.yml`(Valkey·OPA)은 두 모드에 자동 포함된다.

| 서비스 | 네트워크 | 호스트 포트 | 프로파일 | 역할 |
|--------|----------|-------------|----------|------|
| `api` | edge + backend | `API_PORT`(8800) → 8000 | 항상 | 게이트웨이·관리 API·IdP·레지스트리·호스트·콘솔 — **HTTP 진입점** |
| `a2a-grpc` | edge | `A2A_GRPC_PORT`(8851) → 50051 | 항상 | A2A gRPC 바인딩(`lf.a2a.v1.A2AService`) — 상태 없음, 요청을 api 의 HTTP+JSON 바인딩으로 위임(같은 인증·정책·감사). 기본 평문(앞단 TLS 종료), `A2A_GRPC_TLS_*` 면 직접 TLS |
| `mcp-servers` | backend(internal) | 없음 | `demo` | 참조 MCP 서버(가상 Jira·Confluence·Dynatrace·Internal API·Internal Orders — sampling·elicitation 데모, 상태 유지 SSE) |
| `a2a-demo` | backend(internal) | 없음 | `demo` | 데모 원격 A2A 에이전트(관측 분석, A2A 0.3·1.0) |
| `valkey` | backend | 없음 | 항상 | 레이트리밋 토큰 버킷 |
| `db` | backend | 없음 | prod | PostgreSQL |
| `opa` | backend | 없음 | `opa` | 외부 정책 엔진(선택) |
| `ollama` | backend | 없음 | `ollama` | 에어갭 로컬 모델(선택, `ollama/ollama:0.31.1`, 모델 볼륨 `ollama-models`) |
| `web` | edge | `WEB_PORT`(5173) | `web` (local) | 콘솔 Vite 개발 서버(HMR) — API 는 api 로 프록시 |
| `e2e` | edge | 없음 | `e2e` (local) | Playwright(Chromium) 브라우저 스모크 |

backend 네트워크는 internal 이라 참조 서버·DB·Valkey 는 게이트웨이(api)를 거치지 않고는 접근할 수 없다(FR-G00 우회 차단).

## 사전 요구사항

Docker Engine / Docker Desktop + Compose v2, GNU Make. 언어 툴체인(Python·Node)은 호스트에 필요 없다 — 빌드·시험·의존성 설치는 모두
이미지 빌드 안에서 한다. Helm 차트 검사(`make helm-check`)만 호스트 `helm` 이 필요하다.

## 빠른 시작

```bash
make preflight                 # Docker·Compose·env 파일 점검
make local-all                 # api + 참조 MCP 서버 + 데모 A2A + Valkey (SQLite)
make web-build                 # 콘솔 번들 → web/dist (local api 가 서빙)
open http://127.0.0.1:8800/console
make test                      # pytest (local dev 컨테이너)
make local-stop
```

local 모드 시드 계정(`APP_ENV=local|test` 에서만 생성, 비밀번호 = `<계정>-pass`):

| 계정 | 역할 | 로그인 후 첫 화면 |
|------|------|-------------------|
| `admin` | 플랫폼 관리자(전 권한) | `/console` |
| `operator` | 플랫폼 운영자 | `/console` |
| `security` | 보안·IAM 담당 | `/console` |
| `dev` | 에이전트 개발자 | `/console/agents` |
| `finops` | FinOps 담당 | `/console/observe/cost` |
| `user`, `user2` | 최종 사용자 | `/chat` |

prod 모드(로컬 리허설 또는 대상 호스트):

```bash
cp .env.example .env.prod      # 없을 때만 — CHANGE_ME 를 Secret Manager 값으로 교체 (make deploy 는 CHANGE_ME 가 남으면 거부)
make prod-build                # runtime 이미지(콘솔 번들 포함) 빌드 + 기동
make db-migrate ENV=prod && make db-ping ENV=prod
make prod-stop
```

prod 에서는 password grant 가 비활성화되고, 최초 관리자는 `BOOTSTRAP_ADMIN_PASSWORD` 로 한 번 생성된다(이미 있으면 바꾸지 않는다).
서명 키·암호화 키를 비워 두면 `data/keys` 에 생성·보존되므로 운영에서는 `SIGNING_KEY_PEM`·`ENCRYPTION_KEY` 를 주입한다. 키 회전 절차는 `SECURITY.md` "키 회전".

## 엔드포인트·포트

| 경로 | 설명 | 인증 |
|------|------|------|
| `GET /health` | DB 실제 `SELECT 1` 포함 헬스 | 없음 |
| `/console/*` · `/chat/*` · `/login` · `/setup` | 웹 콘솔 SPA (`/_app/*` 해시 자산, `/` → `/console`) | 브라우저 로그인 |
| `/idp/.well-known/openid-configuration` · `/idp/authorize` · `/idp/token` · `/idp/device/code` · `/idp/jwks` | 내장 OIDC IdP | — |
| `GET /idp/providers` · `/idp/authorize?idp_hint=` · `/idp/broker/{id}/start` · `/idp/broker/callback` | 외부 IdP SSO 브로커 (Entra·Okta·Keycloak → 콘솔, 원인별 오류 코드) | — |
| `POST /api/v1/identity/idps/discover` · `POST …/idps/{id}/test-login` · `GET …/test-login/{state}` | IdP 저장 전 디스커버리·시험 로그인 (SCR-J02) | `identity:write` |
| `POST /v1/chat/completions` · `GET /v1/models` | Unified Model API (OpenAI 호환, SSE) — 헤더 `X-AgentNexus-Data-Class` | Bearer |
| `POST /mcp/{server_id}` · `/mcp/_all` | MCP 게이트웨이 (Streamable HTTP, 2025-11-25 · 06-18 · 03-26 협상). 서버→클라이언트 요청(sampling·elicitation)은 SSE 응답 스트림으로 중계, 클라이언트 응답은 같은 경로 POST(202) | Bearer / RFC 9728 |
| `GET /.well-known/oauth-protected-resource/mcp/{id}` | MCP Protected Resource Metadata | 없음 |
| `POST /a2a/{agent_id}` · `GET /a2a/{agent_id}/.well-known/agent-card.json` | A2A 게이트웨이 (0.3 ↔ 1.0 변환) | Bearer |
| `/a2a/{agent_id}/rest/…` · `/a2a/platform[/{profile}]/rest/…` — `POST message:send` · `message:stream`(SSE) · `GET tasks[/{id}]` · `POST tasks/{id}:cancel` · `tasks/{id}:subscribe` · `tasks/{id}/pushNotificationConfigs[/{cid}]` · `GET extendedAgentCard` | A2A 1.0 HTTP+JSON(REST) 바인딩 — `application/a2a+json`, 오류는 `google.rpc.Status`+`ErrorInfo`(domain `a2a-protocol.org`), 카드에 `HTTP+JSON` 인터페이스. REST 전용 원격 에이전트는 게이트웨이가 변환해 호출 (D16-①) | Bearer |
| gRPC `127.0.0.1:8851` `lf.a2a.v1.A2AService/*` (`tenant` = 원격 에이전트 id · `platform` · `platform/{profile}`) | A2A 1.0 gRPC 바인딩 — metadata `authorization`, 오류 트레일러 `grpc-status-details-bin`(ErrorInfo), 서버 스트림. `A2A_GRPC_PUBLIC_URL` 이 있으면 카드에 `GRPC` 인터페이스(tenant). gRPC 전용 원격 에이전트 호출 지원 (D16-②) | Bearer (metadata) |
| `GET /.well-known/agent-card.json` · `POST /a2a/platform[/{profile}]` | 플랫폼 A2A 서버 (게시 프로파일 노출) | Bearer |
| `GET /v0/servers` · `POST /v0/publish` | MCP Registry 호환 (발견·게시) | 게시만 Bearer |
| `POST /api/v1/approvals/{id}/consent` | 동의 카드(SCR-C08) — sampling 허용(요청 편집)·응답 검토 후 전송 / elicitation 수락(스키마 검증)·거절·취소 | Bearer (요청 주체) |
| `GET /api/v1/chat/mcp/context` · `POST /api/v1/chat/mcp/resource` · `POST /api/v1/chat/mcp/prompt` | 입력창 `/` 메뉴 — 프로파일 MCP 서버의 리소스·프롬프트 조회·삽입 | Bearer |
| `POST /api/v1/registry/validate` | server.json 게시 전 검증 (SCR-E04, MSG-007) | `registry:publish` |
| `POST /v1/embeddings` | OpenAI 호환 임베딩 — 입력 가드·데이터 등급 라우팅·사용량 (`embed-local` 기본) | Bearer |
| `/api/v1/knowledge-bases[/{id}[/documents\|/search\|/reindex]]` | 지식 베이스(RAG 내장 벡터 색인) — 관리 `agents:write`, 검색 `chat:use`(팀 범위) | Bearer |
| `POST /api/v1/chat/attachments` · `GET …/{id}` | 첨부 업로드(pdf·docx·xlsx·이미지·텍스트, `ATTACHMENT_MAX_MB`)·다운로드(소유자) | Bearer |
| `/api/v1/setup/{state,claim,code,progress,mfa,identity,models,registry,observability,templates,complete}` | 첫 실행 마법사 (SCR-A05~A09) — 일회용 설정 코드 → 설정 전용 토큰 | 공개(state·claim) · 설정 토큰 또는 admin |
| `POST /api/v1/admin/profiles/plan` · `POST …/profiles/apply` | 배포 프로파일 미리보기·적용 · 에어갭 (SCR-L01·ST-09) | `admin:read` · `admin:write` |
| `GET /api/v1/system/banners` | 전역 배너 (CMP-16 — 에어갭·첫 실행·감사 체인) | Bearer |
| `GET /v1/catalog?q=jira tag:… auth:… trust:…&auth=&trust=&status=` · `GET /v1/catalog/exposures` | 통합 검색(점수순·일치 힌트·유형 건수) · 등록 → 노출 현황 (SCR-E01) | `catalog:read` |
| `GET /api/v1/registry/approvals?status=` · `POST /v1/catalog/{id}/approve` `{trust, tool_risks, allowlist_group}` · `…/reject` | 등록 승인 대기열 — 점검(실패면 승인 불가 MSG-007)·도구 위험도·신뢰·허용목록 · 72시간 자동 반려 (SCR-E05) | `catalog:approve` |
| `GET /v1/catalog/{id}/diff?from&to` · `PUT …/pin` · `POST …/rollback` · `GET …/usage` · `GET …/history` | 버전 diff(재승인)·고정·롤백(확인)·사용처·변경 이력 (SCR-E07) | `catalog:read` · 변경 `catalog:approve` |
| `POST /v1/catalog/{id}/instant-use/auth` · `POST …/instant-use/policy` `{dry_run}` · `GET …/exposure` | 즉시 사용 위저드 ② 시험 교환(MSG-019) ③ 정책 기본값·Rego ④ 60초 노출 확인 (SCR-E03) | `catalog:write` · 확인 `catalog:read` |
| `GET·PUT /api/v1/registry/draft` · `POST /api/v1/registry/fetch` | server.json 임시 저장(사용자별)·URL 가져오기 (SCR-E04) | `registry:publish` |
| `/api/v1/gateway/policies/drafts[/{id}[/test·/submit·/approve·/reject]]` · `/api/v1/gateway/policy-tests` · `POST /api/v1/gateway/policies/{id}/rollback` · `GET …/{id}/diff?from` · `POST …/policies/rego` | 정책 2인 검토 — 초안 → 회귀 시험 → 게시 요청(사유) → 작성자 외 승인 · 롤백 · Rego 미리보기 (SCR-H07·H08). 직접 `POST·PUT·DELETE /policies` 는 admin 비상 경로(감사 `break_glass`) | `policy:write` · 조회 `gateway:read` |
| `POST /api/v1/mcp-servers/{id}/test-config` | MCP 설정 적용 전 시험 — 후보 엔드포인트·인증으로 PRM/AS·tools/list·스코프 점검, 저장 안 함 (SCR-F03) | `mcp:write` |
| `GET·PUT /api/v1/agents/{id}/a2a-card` · `POST …/a2a-card/publish` | 플랫폼 Agent Card 편집·검증·게시 요청 → 승인 후 공개 (SCR-D10) | `agents:write` |
| `POST /api/v1/a2a/agents` `{dry_run}` · `GET /api/v1/a2a/agents/{id}/checks` | 원격 에이전트 등록 전 점검 · 개발자 등록 = 승인 대기(MSG-008) · Agent Card 재검증 이력 (SCR-G02·G03) | `a2a:write` · `a2a:read` |
| `POST /api/v1/runs/{id}/input` | 원격 에이전트 input-required 에 답하기 — 같은 Task·contextId 로 재개 (SCR-C05) | Bearer (실행 소유자) |
| `GET /api/v1/runs/{id}` 의 `meta` · `POST /api/v1/runs/{id}/continue` | 응답 메타(토큰·비용·첫 토큰·노드별 지연/비용·폴백·429·소독·근거 사용) · 부분 결과로 계속 (SCR-C03·C06) | Bearer (실행 소유자) |
| `POST /api/v1/sessions/{sid}/messages/{mid}/feedback·/regenerate·/edit` | 👍👎 · 다시 생성 · 편집 후 재전송(새 분기) (SCR-C03) | `chat:use` |
| `GET /api/v1/sessions?q=&profile_id=&days=&source=` · `GET …/{sid}/summary·/export` · `GET /api/v1/chat/files` · `GET /api/v1/chat/profiles` | 대화 이력 본문 검색·필터·미리보기·내보내기 .md · 첨부·Artifacts 모아보기 · 프로파일 준비 상태 (SCR-C01·C07). `DELETE /api/v1/sessions/{sid}` 는 내용 제거·메타만 보존 | `chat:use` |
| `POST /api/v1/chat/attachments/from-artifact` | 원격 Artifact → 대화 첨부 (SCR-C05) | `chat:use` |
| `GET /api/v1/approvals/{id}` 의 `context` · `POST …/approve {remember}` | 승인 모달 맥락(출처·OBO 신원·영향·정책 근거) · 이 세션 다시 묻지 않기(중위험만) (SCR-C04) | Bearer |
| `GET /api/v1/agents?status=&team=&q=` · `POST /api/v1/agents/{id}/clone·/restore` · `GET …/health` | 프로파일 목록 필터·복제·복원·연결 점검 (SCR-D01) | `agents:read` · `agents:write` |
| `POST /api/v1/agents/{id}/test {dry_run, version}` · `GET …/test-runs` · `GET …/checks` | 드라이런(쓰기 0)·게시 버전 실행·이전 실행 비교·게시 전 점검 (SCR-D08) | `agents:write` · `agents:read` |
| `GET /api/v1/agents/{id}/code?format=yaml\|adk` · `PUT …/graph {allow_invalid}` · `POST …/code/revert` · `GET …/diff` · `POST …/simulate` | YAML/ADK 보기 · 코드만 저장(MSG-001) · 되돌리기 · 게시본 비교 · 실패 시뮬레이션 (SCR-D06·D07) | `agents:read` · `agents:write` |
| `GET /api/v1/host/sessions` · `POST …/{sid}/reconnect` · `DELETE …/{sid}` | MCP Host 세션 KPI·이벤트·재연결·종료 (SCR-D09) | `observe:read` · `mcp:write` |
| `GET /api/v1/identity/users/{id}/detail` · `POST …/sessions/revoke {reason}` · `POST …/resync` · `GET /api/v1/me/sessions` · `POST /api/v1/me/sessions/revoke-others` | 사용자 상세(세션·권한·클레임) · 세션 강제 종료(사유 필수, 다음 요청부터 401) · IdP 재동기화 · 내 세션·다른 세션 종료 (SCR-J03·B06) | `identity:read`·`identity:write` · Bearer |
| `GET·PUT /api/v1/identity/rbac {overrides, reason}` · `POST …/rbac/simulate` | 역할·권한 행렬(admin 고정, 다음 토큰부터 반영) · 권한 시뮬레이터(역할 또는 IdP 그룹) (SCR-L03) | `identity:read` · `identity:write` |
| `POST /api/v1/identity/token-exchange/simulate` · `POST /api/v1/identity/mcp-auth/{id}/rediscover` | 토큰 교환 시뮬레이터(MSG-019) · PRM·AS·스코프 다시 탐색 (SCR-J04·J06) | `identity:read` |
| `GET·POST /api/v1/gateway/bypass-check` · `POST …/limits/preview` · `POST …/routing/test {fail}` · `POST …/security/auth-test` · `GET …/security/detections` | 우회 방지 현황(GET, 감사 없음)·지금 점검(POST, 감사) · 401/403/429/5xx 카운터 · 429 미리보기(경고만 `warn`·`retry_after`) · 장애 주입 라우팅 시험 · 401 시험·탐지 이벤트 (SCR-H01·H02·H05·H09) | `gateway:read` |
| `GET /api/v1/gateway/governance/blocks` · `POST …/tools/args-test` · `POST …/sanitize-rules/preview` · `GET …/sanitize-events` | 최근 차단 · 인자 스키마 시험(strict/warn) · 소독 미리보기·이벤트 (SCR-H10·H11) | `gateway:read` |
| `POST /api/v1/approvals/{id}/delegate {to, note}` | 승인 위임 — 요청자에게는 불가(`delegate_forbidden`), 위임받은 사람이 결정 (SCR-H12) | `approvals:decide` |
| `GET /api/v1/gateway/secrets/{id}/usage` · `POST …/leak-check` · `DELETE /api/v1/gateway/mcp/sessions/{sid}` · `GET /api/v1/gateway/a2a/stats` | 시크릿 사용처·교체 D-day · 유출 점검(값 미노출) · MCP 세션 강제 종료 · A2A 호출 통계 (SCR-H06·H14·H15) | `gateway:read` · `gateway:write` |
| `POST /api/v1/mcp-servers/{id}/restart` · `PUT …/sampling {ask\|deny}` · `GET …/logs` · `POST /api/v1/mcp-servers/openapi {operations}` · `GET …/templates/custom?include=` | MCP 재시작 · 샘플링 정책 · 로그 · OpenAPI 노출 선택·이름·위험도 · 템플릿 포함 항목 (SCR-F01·F02·F04·F05) | `mcp:write` · `mcp:read` |
| `GET /api/v1/observe/instrumentation` · `GET /api/v1/observe/a2a` · `GET /api/v1/traces?user=&tool=` | 계측 비율 · A2A 지표 · trace 사용자·도구 필터 (SCR-K01·K02·K05) | `observe:read` |
| `POST /api/v1/logs/unmask {ids, reason}` | 로그 마스킹 해제 — 사유 필수, 30분, 시크릿은 계속 가림, 감사 `observe.log.unmask` (SCR-K04) | `audit:read` |
| `GET /api/v1/usage/export` (CSV) · `GET /api/v1/usage/drilldown?team=&user=` | 비용 CSV · 팀 → 사용자 → 대상 드릴다운 (SCR-K06) | `cost:read` |
| `GET /api/v1/audit/verify?from_seq&to_seq` · `GET /api/v1/audit/reports/templates` · `POST …/reports/preview` · `GET …/reports/{id}/download?format=md\|csv\|jsonl` | 해시 체인 구간 검증 · 규제 리포트 템플릿(개인정보보호법 접속기록 등)·미리보기·다운로드 (SCR-K09·K10) | `audit:read` |
| `GET /api/v1/admin/settings/history` · `POST …/settings/revert` · `POST …/settings/pii-test` | 설정 변경 이력·되돌리기 · PII 규칙 시험 (SCR-L06). 새 설정 키: `guards.pii_rules`·`auth.access_token_ttl_min`·`auth.session_ttl_hours`·`auth.ip_allowlist`·`ui.timezone`·`secrets.rotation_days`·`notify.channels`·`backup.schedule` | `admin:read` · `admin:write` |
| `PATCH /api/v1/admin/connectors/{id}` · `GET /api/v1/admin/updates/plan` · `POST …/updates/sbom-baseline` | 커넥터 편집 · 업그레이드 계획(마이그레이션·SBOM 차이·서명) (SCR-L02·L04) | `admin:write` · `admin:read` |
| `POST /api/v1/admin/restore/test` · `GET·POST /api/v1/admin/backups[/run]` · `GET …/backups/{name}` | 격리 복구 시험(임시 DB) · 예약 백업 목록·즉시 실행·내려받기 (SCR-L05). `POST /api/v1/admin/restore` 는 `confirm: "RESTORE"` 필수 | `admin:write` |
| `GET /api/v1/admin/keys` · `POST …/keys/rotate` · `POST …/keys/reencrypt[?drop_previous=true]` | 키 상태(kid·JWKS kid·암호화 키 수) · 회전(`{kind: signing\|encryption, confirm: "ROTATE"}`, env 주입 키는 409 `keys_env_managed`) · 재암호화·이전 암호화 키 제거 (58th) | `admin:read` · `admin:write` + admin 역할 |
| `GET·PUT /api/v1/notifications/preferences` · `GET /api/v1/notifications?category=` · `GET /api/v1/system/components` | 알림 분류·음소거·수신 채널 · 구성요소 버전·복제 수·재시작 명령 (SCR-B04·B05) | Bearer · `admin:read` |
| `POST /api/v1/gateway/client-keys/{id}/revoke` · `GET /api/v1/gateway/mcp/sessions` 의 `sid` | 클라이언트 키 관리자 폐기 · MCP 세션 해시 핸들(원 세션 ID 미노출)로 강제 종료 (SCR-H13·H14) | `gateway:write` |
| `POST /api/v1/gateway/virtual-servers {collision_policy}` | 가상 MCP 서버 도구 이름 충돌 — `prefix`(기본, server.tool) · `reject`(409 `tool_collision`) (SCR-H03) | `gateway:write` |
| `POST /api/v1/mcp-servers/openapi/fetch {url}` · `GET …/templates/custom?include=none` | OpenAPI 문서 URL 을 게이트웨이가 가져오기(CORS 무관, 2MB, JSON·YAML) · 선택 파일 없는 템플릿 (SCR-F04·F05). CLI `nexus mcp scaffold --include docker\|register\|readme\|none` | `mcp:write` |
| `PATCH /api/v1/a2a/agents/{id} {auth:{scheme:"oauth2", token_url, client_id, scopes}, secret}` · `GET /api/v1/a2a/tasks/{id}` 의 `transitions`·`events` | 원격 에이전트 OAuth2 client_credentials(게이트웨이가 발급·캐시, 만료 30초 전 갱신) · Task 상태 전이·수신 경로(send·sse·push·poll) (SCR-G04·G06) | `a2a:write` · Bearer |
| `POST·PATCH /api/v1/providers {secret_ref, options.zdr, options.data_classes}` · `PATCH /api/v1/models/{id} {price_in, price_out}` | 공급자 기존 시크릿 참조(없으면 400) · ZDR(OpenAI `store:false`) · 공급자 허용 데이터 등급(모델 정책을 좁힘) · 단가만 변경은 FinOps `cost:write` (SCR-I02·K06) | `models:write` · `cost:write` |
| `GET /api/v1/observe/timeseries` 의 `p95_latency_ms` · `GET /api/v1/me` 의 `issuer`·`idp`·`claims` · `GET /api/v1/notifications` 의 `unread_counts` | 구간별 지연 p95 · 내 IdP·그룹 클레임 · 분류별 읽지 않음 (SCR-K05·B06·B04) | `observe:read` · Bearer |
| `PATCH /api/v1/identity/idps/{id} {enabled:false}` | IdP 중지 = 그 IdP 사용자 세션 종료(`sessions_ended`) · 시험 로그인은 비활성 초안에서도 가능 (SCR-J01·J02) | `identity:write` |
| `POST /api/v1/gateway/routing/rules/{id}/publish {reason}` · `GET …/versions` · `POST …/rollback {version, reason}` | 라우팅 규칙 게시(사유 필수)·버전 이력·게시 안 된 변경·되돌리기(새 버전으로 기록) (SCR-H02) | `gateway:write` · 조회 `gateway:read` |
| `POST /api/v1/me/tokens {kind: client_key, allowed_models}` · `PATCH /api/v1/gateway/client-keys/{id}` | 클라이언트 키별 허용 모델 별칭 — 목록 밖 별칭은 403 `model_not_allowed` (SCR-H13) | `gateway:read` · `gateway:write` |
| `POST /api/v1/mcp-servers/openapi {identity: "obo", scopes}` | hosted OpenAPI 서버의 사용자 신원 전파 — 호출마다 `api:{ns}` 교환 토큰을 `Authorization` 으로 주입 (SCR-F04) | `mcp:write` |
| `POST /api/v1/mcp-servers/{id}/inspect {test_principal: {roles, team}}` | 시험 전용 가상 사용자 — 내 권한 이하 역할만, 쓰기·위험 도구는 드라이런, 감사 `mcp.inspect.test_principal` (SCR-F06) | `mcp:read` |
| `POST /api/v1/a2a/agents/{id}/try {files}` · `PATCH /api/v1/a2a/agents/{id} {framework, auth.push_verify}` | 시험 콘솔 파일 Part · 프레임워크 지정 · 푸시 JWT 서명 검증(JWKS·±5분·본문 해시, 실패 401) (SCR-G01·G04·G07) | `a2a:read` · `a2a:write` |
| `POST /mcp/{id}` (MCP 2026-07-28, `_meta.io.modelcontextprotocol/protocolVersion`) · `server/discover` | dual-era 게이트웨이 — 세션 없는 modern 요청·헤더 검증(-32020)·버전 거부(-32022)·`resultType`·캐시 힌트·MRTR(`InputRequiredResult`·`inputResponses`·AEAD `requestState`). `initialize` 는 레거시(2025-11-25) 그대로 (D13-④) | Bearer (MCP 와 같음) |
| `subscriptions/listen` · `Mcp-Param-*` · `_meta.progressToken` (MCP 2026-07-28) | 목록 변경 구독(승인 `notifications/subscriptions/acknowledged` → `notifications/{tools,prompts,resources}/list_changed`, 모두 subscriptionId 태그 · 900초 뒤 정상 종료 · 주체당 동시 5개) · `x-mcp-header` 인자 ↔ `Mcp-Param-*` 검증(-32020) · progress 요청 단위 SSE 중계 (D14-②) · `resourceSubscriptions`(읽을 수 있는 URI 만 승인 → `notifications/resources/updated`) · `taskIds`(내 작업 → `notifications/tasks`) (D15-①) | Bearer (MCP 와 같음) |
| `tools/call` + `_meta…clientCapabilities.extensions["io.modelcontextprotocol/tasks"]` · `tasks/get` · `tasks/update` · `tasks/cancel` | MCP Tasks 확장(SEP-2663) — 게이트웨이가 작업을 실행, 2초 안에 끝나면 일반 결과, 아니면 `resultType:"task"` · DetailedTask(input_required·completed·failed·cancelled) · `inputResponses` 로 입력 · 협조적 취소 · `Mcp-Name`=taskId · 남의·만료 작업 -32602 · 확장 미선언 -32021 (D15-①) | Bearer (작업은 주체에 묶임) |
| `PATCH /api/v1/mcp-servers/{id} {protocol: auto\|legacy\|2026-07-28}` | 업스트림 세대 — auto 는 도구 갱신 때 `server/discover` 로 발견, modern 업스트림은 세션 없이 호출(업스트림 `InputRequiredResult` 는 같은 중계·동의 경로로 답해 재호출). 발견 값 `upstream_protocol` (D14-②) | `mcp:write` |
| `GET /api/v1/observe/slos` · `PATCH /api/v1/observe/slos/{id} {target, threshold_ms, window_days, enabled, reason}` · `POST /api/v1/observe/slos/evaluate` | SLO·오류 예산 — 데이터 경로(모델·MCP·A2A) 가용성 99.95%·첫 응답 ≤ 2s 99% (30일 이동 창), 예산 잔량·5m/30m/1h/6h 소진율·page(14.4×)/ticket(6×) 경보 · 수정은 사유·감사 (D15-④) | `observe:read` · `admin:write` |
| `POST /api/v1/approvals/{id}/extend` | 동의·승인 시간 연장 — 기본 대기(동의 60초 · 도구 승인 30분)만큼, 최대 10회, 감사 `approval.extend` (D14-③, WCAG 2.2.1) | 요청자 · 결정 권한자 |
| `/api/v1/*` | 관리 REST (OpenAPI: `/openapi.json`, 콘솔 `/console/api`) | Bearer |
| `GET /metrics` | Prometheus (multiprocess) | `METRICS_TOKEN` 또는 observe:read |
| `GET /licenses/*` | 콘솔 번들에 포함한 서드파티 라이선스 사본(Pretendard OFL-1.1) — 저장소 `LICENSE`(Apache-2.0)·`NOTICE`·`THIRD_PARTY_LICENSES.md` | 없음 |
| `POST /v1/traces` | 내장 OTLP/HTTP 수신기(컨테이너 간 단일 trace) | `OTLP_INGEST_TOKEN` |

호스트 포트: api `8800`(8000 은 다른 프로젝트 점유 회피), A2A gRPC `8851`, 콘솔 개발 서버 `5173`(프로파일 `web`).

## 웹 콘솔

React 19 + TypeScript + Vite 7 SPA(`web/`). 폰트(Pretendard)까지 로컬 번들이라 에어갭에서도 외부 요청이 없다.
라우트: 운영 개요 `/console` · 채팅 `/chat` · 에이전트 `/console/agents/{id}/graph|code|test` · 카탈로그 `/console/catalog` ·
MCP `/console/mcp-servers/{id}[/inspect]` · A2A `/console/a2a[/tasks[/{id}]]` · 게이트웨이 `/console/gateway[/policies|limits|approvals|routing/models|tools|log|security|secrets|governance[/rules]|model-api|mcp|a2a]` ·
모델 `/console/models[/new|playground|tokens|providers]` · Identity `/console/identity/users|propagation|idp[/new]|token-exchange|mcp-auth|workloads` ·
레지스트리 `/console/registry/publish|sources|approvals` · SBOM `/console/catalog/sbom` · MCP 추가 `/console/mcp-servers/new/openapi|custom`·`{id}/config` ·
관측 `/console/observe/pipeline|traces|logs|metrics|cost|budgets|replay/{sid}` · 감사 `/console/audit[/reports/new]` · 운영 `/console/admin[/rbac]` · 대화 이력 `/chat/history` · 첫 실행 `/setup`. 권한 없는 메뉴는 숨기지 않고 비활성 + 사유로 보인다(ST-04). ⌘K 전역 검색.

UI 설계서 100 화면 중 웹 95종(B04 알림 센터는 상단 바 드로어, C05 위임·C08 동의 카드는 채팅 안 카드)을 구현했고, 설치 화면
A01~A04·A11 은 설계서대로 터미널(`nexus install --check`·`docker compose`·`helm install`·`nexus bundle import`·`nexus login --device`)이다(PRD D11).
접근성은 KWCAG 2.2 + WCAG 2.2 AA 기준 — 콘솔 60 화면·채팅·로그인 axe 자동 점검 위반 0(밝은·어두운 테마 모두), 첫 Tab 「본문 바로가기」, 화면별 문서 제목(`web/e2e/a11y.spec.ts`·`a11y-dark.spec.ts`).
동의 카드·승인 모달·승인 대기열의 기한은 「시간 연장」으로 늘린다(동의 60초·도구 승인 30분씩 최대 10회, 만료 20초 전 알림 — WCAG 2.2.1).
관측·감사 → 「SLO·오류 예산」은 SLI·예산 잔량·창별 소진율·일별 나쁜 요청과 목표 수정(사유)을 보여 준다. A2A 시험 콘솔·오케스트레이터 위임은 A2A 1.0 이 기본(0.3 선택 가능, D15-②).
채팅 입력창 `/` 는 리소스·프롬프트 삽입 메뉴, 실행 중 MCP 서버가 sampling·elicitation 을 요청하면 동의 카드가 뜬다(60초 무응답 자동 거부).

```bash
COMPOSE_PROFILES=demo,web make local-all   # HMR 개발 서버 → http://127.0.0.1:5173/console
make web-test                              # Vitest (27)
make web-e2e                               # Playwright 브라우저 시나리오 (77 · axe 접근성 밝은·어두운 테마 포함) — make web-build 선행
make web-lock                              # web/package.json 변경 후 pnpm-lock.yaml 갱신
```

## CLI `nexus`

`python -m app.cli` (컨테이너: `nexus`, 저장소: `bin/nexus`). 설정 `~/.nexus/config.json`(0600) · 주소 `NEXUS_URL` · 토큰 `NEXUS_TOKEN`.

```bash
make shell                                  # api 컨테이너 안
nexus login --device --url http://127.0.0.1:8000   # RFC 8628 — 브라우저 /idp/device 에서 코드 승인
nexus whoami
nexus catalog list --type mcp_server
nexus mcp scaffold weather --dir ./weather  # Custom MCP 서버 템플릿 (FastMCP + OAuth + OTel + server.json)
nexus mcp scaffold weather --include docker --include readme   # 선택 파일만 (none = 선택 파일 없음, SCR-F05)
nexus registry publish ./weather/server.json   # → 운영자 승인 대기 (SCR-E05)
nexus mcp inspect jira transition_issue --args '{"key":"ORI-1440","to":"Done"}'   # 고위험 → approval_id
nexus approvals approve apr_… && nexus mcp inspect jira transition_issue --args '…' --approval apr_…
nexus chat tpl-qa "장애 대응 런북 알려줘"     # 실행 이벤트 스트리밍
nexus usage --group-by team · nexus audit-verify · nexus install --check [--mode helm]
nexus install compose [--profile full] [--cosign-key cosign.pub]   # install.yaml → .env·시크릿 생성 → 서명 검증 → 기동 → 헬스 → 설정 코드 (SCR-A02)
nexus bundle export --out agentnexus-airgap --include dist/agentnexus-0.1.0.tgz   # docker save + 서명 백업 + SHA-256
nexus bundle import agentnexus-airgap --cosign-key ops.pub [--dry-run]   # 서명 검증 기본 — 키는 번들 밖에서(59th) · PoC 만 --allow-unsigned
nexus keys status · nexus keys rotate --kind signing|encryption --yes · nexus keys reencrypt --drop-previous   # 키 회전 (관리자, SECURITY.md, 58th)
```

## Make 타겟

| 타겟 | 설명 |
|------|------|
| `make <mode>-all` / `-build` / `-logs [SVC=]` / `-stop` / `-restart [SVC=]` / `-ps` | 스택 수명주기 (`mode` = local \| prod) |
| `make test` | pytest — local dev 컨테이너(SQLite) |
| `make test-pg` | PostgreSQL 회귀 (prod `db` 컨테이너에 `app_test` 재생성) |
| `make web-build` / `web-test` / `web-e2e` / `web-lock` / `e2e-lock` | 콘솔 빌드 · Vitest · Playwright · 콘솔·e2e 잠금 파일 갱신 |
| `make lint` / `audit` | ruff(F·E9)+주요 모듈 import / 의존성 취약점 감사(pip-audit·pnpm audit — 네트워크 필요, CI `audit` 잡) |
| `make licenses` | `THIRD_PARTY_LICENSES.md` 재생성(prod 이미지 파이썬 런타임 + 콘솔 prod JS + 폰트, 오프라인) |
| `make helm-check` | Helm lint(--strict) + 4 시나리오 렌더 오프라인 정합성 검사 |
| `make helm-package` | 차트 패키지 → `dist/agentnexus-<ver>.tgz` |
| `make shell [ENV=]` | 앱 컨테이너 셸 |
| `make db-ping` / `db-shell ENV=prod` / `db-migrate` / `db-seed` `[ENV=]` | DB 확인·접속·마이그레이션(Alembic)·시드 |
| `make db-reset` / `db-fresh` | local SQLite 초기화(+시드) — prod 거부 |
| `make deploy` | prod 롤아웃 (CHANGE_ME 남아 있으면 거부) |
| `make preflight` / `help` | 점검 / 목록 |

## 환경 변수

새 키는 `.env.local`·`.env.prod`(CHANGE_ME)·`.env.example` 세 곳에 함께 둔다.

| 변수 | local | prod |
|------|-------|------|
| `APP_ENV` / `DB_ENGINE` | `local` / `sqlite` | `prod` / `postgres` |
| `DATABASE_URL` | SQLite 파일 | `db` 서비스 접속 문자열 (CHANGE_ME → 주입) |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | — | RDBMS 계정 |
| `API_PORT` / `BIND_HOST` | `8800` / `127.0.0.1` | 호스트에 맞게 |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8800` | 운영 도메인 — IdP issuer `<값>/idp`, redirect_uri 검증 기준 |
| `BOOTSTRAP_ADMIN_PASSWORD` | (비움 — 시드 계정) | 최초 `admin` 비밀번호 |
| `SIGNING_KEY_PEM_NEXT` / `ENCRYPTION_KEY_NEXT` / `SIGNING_KEY_PREVIOUS_UNTIL` / `SIGNING_REVOKED_KIDS` | (비움) | 선택 — env 2단계 회전: 다음 키(검증·복호화만, 먼저 배포) · 이전 서명 키의 토큰 검증 끝(ISO 8601) · 폐기 kid(쉼표, 어디서도 검증 안 함) (62nd, SECURITY.md) |
| `DB_STATEMENT_TIMEOUT_MS` / `DB_LOCK_TIMEOUT_MS` / `DB_IDLE_TX_TIMEOUT_MS` | `120000` / `15000` / `0` (SQLite 는 쓰지 않음) | 동일 — PostgreSQL 세션 상한(0 = 끔). idle-in-transaction 은 스트림 요청이 세션을 연 채 기다릴 수 있어 기본 끔, 켤 때는 최장 스트림보다 길게. 마이그레이션은 상한 없이 (61st) |
| `MODEL_MAX_INFLIGHT_PER_SUBJECT` | `32` | 동일 — 주체별 동시 모델 요청 상한(넘으면 429, 0 = 끔). 큰 `max_tokens` 요청을 동시에 열어 팀 예산 예약을 잠그지 않게 (63rd) |
| `SIGNING_KEY_PEM` / `ENCRYPTION_KEY` | (비움 — `data/keys` 자동 생성) | RS256 PEM / Fernet 키 |
| `SIGNING_KEY_PEM_PREVIOUS` / `ENCRYPTION_KEY_PREVIOUS` | (비움) | 선택 — env 로 키를 주입할 때 회전 직후의 이전 키(검증·복호화 전용). 서명 PEM 은 여러 개 이어 붙일 수 있고, 암호화 키는 쉼표 구분 (58th) |
| `ACCESS_TOKEN_TTL` | `3600` | `3600` |
| `UVICORN_WORKERS` | `1` | `2` — runtime 이미지 uvicorn 워커 수 (SCR-B05 복제 수) · 데모 서버(mcp-servers·a2a-demo)는 compose·Helm 에서 `1` 고정 |
| `ANX_TARGET_VERSION` / `COSIGN_PUBLIC_KEY` | (비움) | 선택 — 업그레이드 계획 대상 버전 / 이미지 서명 검증 공개키 (SCR-L04) |
| `RATE_LIMIT_BACKEND` / `VALKEY_URL` | `redis` / `redis://valkey:6379/0` | 동일 |
| `POLICY_ENGINE` / `OPA_URL` | `builtin` / `http://opa:8181` | `builtin` 또는 `opa` |
| `MCP_REFRESH_ON_START_S` / `MCP_HEALTH_INTERVAL_S` | `90` / `60` | 동일 |
| `MCP_STDIO_ALLOWED_COMMANDS` | (비움 = stdio 금지) | 쉼표 구분 허용 목록 — 공백 없는 항목은 실행 파일 이름(예 `uvx`, 인자 자유), 공백이 있는 항목은 명령+인자 **전체 일치**(예 `npx -y @mcp/server-fs@0.6.2 /data`) (D19-②) |
| `MCP_CONSENT_TIMEOUT_S` | `60` | `60` — 동의 카드 무응답 시 자동 거부(초) |
| `SCHEDULER_INTERVAL_S` / `RETENTION_INTERVAL_S` | `60` / `3600` | 동일 — 경보 평가·실행 복구 주기 / 보존 파기 점검 주기 (0 = 끔, 워커 간 리스) |
| `RUN_STALE_S` | `90` | `90` — 생존 신호가 끊긴 실행을 다른 워커가 이어받는 기준(초) |
| `A2A_REMOTE_WAIT_S` / `ATTACHMENT_MAX_MB` | `120` / `20` | 동일 — 원격 Task working 대기(초) / 첨부 최대 크기 |
| `A2A_WATCH_INTERVAL_S` | `5` | `5` |
| `A2A_GRPC_PORT` / `A2A_GRPC_PUBLIC_URL` | `8851` / `127.0.0.1:8851` | 호스트 포트 / 카드에 광고할 gRPC 주소(비우면 카드에 GRPC 없음 — Helm 은 `a2aGrpc.publicUrl`) |
| `A2A_GRPC_TLS_CERT` / `A2A_GRPC_TLS_KEY` | (비움 — 평문) | PEM 경로 — 둘 다 있으면 gRPC 를 TLS 로 수신(Helm `a2aGrpc.tls.secretName` 이 채움) |
| `A2A_DEMO_CLIENT_SECRET` | (비움 — 공유 볼륨 파일) | CHANGE_ME/비움 = 파일 방식 · Helm 은 Secret 주입 |
| `OTLP_ENDPOINT` | (비움) | 외부 OTel Collector (선택, 내장 저장소와 병행) |
| `OTLP_INGEST_TOKEN` / `METRICS_TOKEN` | (비움 — 자동 생성 / 사용자 토큰) | 수신기·스크레이프 토큰 |
| `SPAN_PROCESSOR` / `LOG_LEVEL` | `batch` / `INFO` | 동일 |
| `COMPOSE_PROFILES` | `demo` | `demo` — `COMPOSE_PROFILES=` 로 끄고 `demo,opa`·`demo,web` 로 추가 |
| `WEB_PORT` | `5173` | (미사용 — api 가 번들 서빙) |
| `AGENTNEXUS_COMPONENTS` (셸) | `all` | `gateway,identity` 등 독립 실행 |
| `MCP_SERVERS_URL` · `A2A_DEMO_URL` · `IDP_INTERNAL_URL` · `GATEWAY_URL` | compose `environment` 고정 | 동일 |

> `.env.prod` 는 커밋하지 않는다. `.env.local` 은 시크릿이 없어 커밋한다.

## API 예시 (local 실제 응답)

```bash
$ curl -s http://127.0.0.1:8800/health
{"status":"ok","env":"local","db":{"engine":"sqlite","status":"ok"}}

$ TOKEN=$(curl -s -X POST http://127.0.0.1:8800/idp/token -d grant_type=password -d username=user \
    -d password=user-pass -d client_id=demo | jq -r .access_token)      # password grant 는 local/test 전용

# 내부 데이터 → 상용 모델 요청이 로컬 모델로 재라우팅 (FR-G03·L02)
$ curl -s -D- -H "Authorization: Bearer $TOKEN" -H 'X-AgentNexus-Data-Class: internal' -H 'Content-Type: application/json' \
    -d '{"model":"fast-commercial","messages":[{"role":"user","content":"게이트웨이 한 줄 소개"}]}' \
    http://127.0.0.1:8800/v1/chat/completions
x-agentnexus-model: domain-local
x-agentnexus-fallback: true
x-agentnexus-trace-id: 77ebc4b66850607bb6dc4ea3178c9a0b
{"model":"domain-local","choices":[{"message":{"role":"assistant","content":"[domain-7b] 게이트웨이 한 줄 소개"}}],
 "usage":{"prompt_tokens":3,"completion_tokens":6,"total_tokens":9}, …}

# MCP 도구 호출 — 게이트웨이가 사용자 신원을 도구별 최소 스코프 토큰으로 교환해 전달 (dev 토큰)
$ curl -s -H "Authorization: Bearer $DEV" -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_issues","arguments":{"query":"결제","limit":2}}}' \
    http://127.0.0.1:8800/mcp/jira | jq '.result.structuredContent.result[0]'
{"key":"ORI-1440","summary":"결제 샌드박스 연동 차단","status":"In Progress","priority":"Highest","assignee":"윤도현","updated":"2026-09-29"}

$ curl -s http://127.0.0.1:8800/.well-known/agent-card.json | jq '{name, protocolVersion, url}'
{"name":"AgentNexus","protocolVersion":"0.3.0","url":"http://127.0.0.1:8800/a2a/platform"}
```

openai SDK 는 `base_url="http://127.0.0.1:8800/v1"`, `api_key=<PAT 또는 클라이언트 키 anx_key_…>` 로 그대로 쓴다.

```bash
# 입력창 / 메뉴 — 프로파일(demo-orders)의 MCP 리소스·프롬프트 (user 토큰, 응답 일부)
$ curl -s -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8800/api/v1/chat/mcp/context?profile_id=demo-orders' \
    | jq -c '{servers, resources: [.resources[].uri], prompts: [.prompts[].name]}'
{"servers":["internal-orders"],"resources":["orders://daily/2026-09-30","orders://policy/returns"],"prompts":["anomaly_triage"]}

# server.json 게시 전 검증 (dev 토큰)
$ curl -s -H "Authorization: Bearer $DEV" -H 'Content-Type: application/json' \
    -d '{"name":"io.example/weather","version":"0.1.0","description":"날씨"}' http://127.0.0.1:8800/api/v1/registry/validate
{"valid":false,"errors":["(root): remotes 또는 packages 가 1개 이상 필요합니다"],"name":"io.example/weather","version":"0.1.0","auth":null,"tools":[],"remotes":[]}

# 권한 시뮬레이터 — IdP 그룹 → 역할 → 권한 (security 토큰)
$ curl -s -H "Authorization: Bearer $SEC" -H 'Content-Type: application/json' \
    -d '{"groups":["anx-developers"],"perm":"agents:write"}' http://127.0.0.1:8800/api/v1/identity/rbac/simulate | jq -c '{roles, via, allowed}'
{"roles":["developer"],"via":[{"group":"anx-developers","idp":"데모 파트너 IdP","role":"developer"}],"allowed":true}

# 우회 방지 현황 (operator 토큰, 감사 없음 — 「지금 점검」은 POST) · local 데모의 인증 없는 등록 서버가 mcp_identity 를 실패로 표시
$ curl -s -H "Authorization: Bearer $OPS" http://127.0.0.1:8800/api/v1/gateway/bypass-check \
    | jq -c '{ok, items: [.items[] | {key, ok}][0:3], counts, http_since_start}'
{"ok":false,"items":[{"key":"provider_keys","ok":true},{"key":"mcp_via_gateway","ok":true},{"key":"mcp_identity","ok":false}],"counts":{"blocked_24h":33,"rate_limited_1h":0,"errors_1h":3,"denied_1h":0},"http_since_start":{"401":0,"403":0,"429":0,"5xx":0}}
```

## MCP sampling·elicitation (SCR-C08) · 업스트림 mTLS

- 게이트웨이는 업스트림 initialize 에 `sampling`·`elicitation` 능력을 선언하고, 도구 호출 중 서버 요청(`sampling/createMessage`·`elicitation/create`·`roots/list`·`ping`)을 받는다.
- 하류 클라이언트가 initialize 에서 같은 능력을 선언하고 `Accept: text/event-stream` 으로 호출했으면 요청을 **그대로 중계**한다(공식 MCP SDK 클라이언트 그대로 동작). 응답은 다중 워커에서도 DB(`mcp_relays`)로 만난다.
- 아니면 Host 가 **동의 카드**로 받는다. sampling 은 사용자가 요청을 편집·허용하면 게이트웨이가 Unified Model API 로 실행(모델 `auto` — 데이터 등급 라우팅 적용, 내부 데이터 → `domain-local`)하고 응답을 검토한 뒤 전송한다. elicitation 은 서버 스키마로 폼을 그리고 수락 값을 검증한다. 무응답은 `MCP_CONSENT_TIMEOUT_S` 뒤 자동 거부, 모든 단계는 감사(`mcp.consent.*`·`mcp.sampling`·`mcp.relay`).
- 업스트림이 MCP 2026-07-28 이면(서버별 `protocol`, 기본 `auto` = 도구 갱신 때 `server/discover` 로 발견) 세션 없이 요청마다 `_meta`·헤더로 호출하고, 도구 스키마의 `x-mcp-header` 는 `Mcp-Param-*` 로 미러링한다. 업스트림의 `InputRequiredResult` 는 위 중계·동의 경로로 답해 `inputResponses`·`requestState` 로 다시 부른다. 동의 카드 기한은 「시간 연장」으로 늘릴 수 있다.
- 업스트림 mTLS: MCP 서버(`auth.tls`)·원격 A2A 에이전트(`auth.tls`)·LLM 공급자(`options.tls`)별 CA·클라이언트 인증서. 인바운드 mTLS 는 TLS 종료 지점(Ingress·게이트웨이 앞단) 책임.

```bash
# 업스트림 mTLS (FR-G02) — 인증서·키 PEM 은 시크릿 저장소(kind=certificate), 대상에는 이름만 둔다
# 콘솔: H06 시크릿 · F03 서버 설정 · G04 원격 에이전트 인증 · I03 모델 등록 · H05 Security
POST /api/v1/gateway/secrets       {"name":"jira-ca","kind":"certificate","value":"-----BEGIN CERTIFICATE-----…"}
PATCH /api/v1/mcp-servers/jira     {"auth":{…,"tls":{"ca_secret":"jira-ca","client_cert_secret":"jira-client","verify":true}}}
PUT /api/v1/gateway/security       {"mtls":{"upstream":"required"}}   # disabled · optional(기본) · required(fail closed, 403 mtls_required)
```

## Kubernetes (Helm) · 에어갭

`deploy/helm/agentnexus` — api(initContainer 가 마이그레이션·시드, PostgreSQL advisory lock 으로 다중 파드 직렬화), 데모 참조 서버,
내장 PostgreSQL·Valkey·OPA(선택), NetworkPolicy(backend 는 api 에서만 수신), HPA·PDB, Ingress 또는 Gateway API HTTPRoute(kgateway),
ServiceMonitor, `helm test`. 서명·암호화 키는 설치 시 생성해 Secret 에 보존(`lookup` + `resource-policy: keep`).
잘못된 조합(다중 복제 + RWO 볼륨, 외부 DB 미지정 등)은 렌더 단계에서 거부한다.
2차 검토 반영(51st): `demo.enabled=false` 면 데모 행(참조 MCP·데모 A2A·워크로드·KB)도 시드하지 않는다(`SEED_DEMO`) · `networkPolicy.ingressFrom` 을 좁혀도 같은 릴리스 내부 호출자(api·mcp-servers·a2a-demo·a2a-grpc)는 차트가 연다 · `api.forwardedAllowIps`(기본 `*`) — 운영은 Ingress 컨트롤러 파드 CIDR 로 좁힌다(넓으면 다른 파드가 `X-Forwarded-For` 로 IP 허용 목록·감사 IP 를 속일 수 있다).
11차(54th): a2a-grpc 는 TLS 수신이 아니면 네이티브 gRPC 프로브(`grpc.health.v1`, readiness = api 까지), TLS 면 TCP · `a2aGrpc.topologySpreadConstraints` · 복제 2개 이상이면 a2a-grpc PDB · api·데모 파드는 `checksum/secret` 으로 시크릿 교체 시 재기동.

```bash
make helm-check                                    # lint + 시나리오(기본·에어갭·HA·게이트웨이 단독) 정합성
helm install anx deploy/helm/agentnexus -n agentnexus --create-namespace \
  --set publicBaseUrl=https://agentnexus.example.com --set ingress.enabled=true --set ingress.host=agentnexus.example.com
helm test anx -n agentnexus
# 에어갭: 사설 레지스트리 + 데모 끔 — deploy/helm/agentnexus/ci/airgap-values.yaml 참고
make prod-build && make helm-package
nexus bundle export --out agentnexus-airgap --include dist/agentnexus-0.1.0.tgz   # 이미지 docker save + 차트 + 서명 백업
```

실제 설치 검증(31st): 전용 kind 클러스터(별도 kubeconfig)에 기본 values 로 설치 → 파드 5종 Ready·PVC Bound·`helm test` →
로그인·MCP 도구·모델 호출·감사 체인 스모크 → uninstall·클러스터 삭제 — `KC=<전용 kubeconfig> sh test/impl/31st/helm-kind.sh`.
Docker Desktop(containerd 이미지 저장소)에서는 `kind load docker-image` 대신 플랫폼 하나만 `docker save --platform` → 노드 `ctr images import`.

gRPC 바인딩(41st): `a2aGrpc.enabled=true` — Deployment·Service(`appProtocol: grpc`)·NetworkPolicy(api 수신에 a2a-grpc 자동 추가) · `a2aGrpc.ingress`(ingress-nginx `backend-protocol: GRPC`, 파드 TLS 면 `GRPCS`) · `a2aGrpc.tls.secretName`(cert-manager Certificate 등 kubernetes.io/tls Secret → 파드 직접 TLS) · `a2aGrpc.publicUrl`(카드 광고, 예 `grpcs://grpc.example.com:443`) — `KC=<전용 kubeconfig> SCRATCH=<scratch> sh test/impl/41st/cluster-grpc.sh`.

운영형 검증(34th): kind 다중 노드(control-plane 1 + worker 2) + Calico(NetworkPolicy 집행) + ingress-nginx + metrics-server 에서
ingress·HPA(2~4)·PDB·`networkPolicy.ingressFrom`(ingress-nginx·릴리스 파드) 로 설치 → 라벨 없는 파드의 backend·api 접근 차단 · ingress 경유 로그인·도구 호출 ·
두 번째 퇴거 거부(PDB) · 부하 시 HPA 확장 → 삭제 — `KC=<전용 kubeconfig> sh test/impl/34th/cluster-kind.sh`. 복제 2개 이상이면 `persistence.accessMode=ReadWriteMany`(또는 `existingClaim`, kind 는 `persistence.enabled=false`).

## 시험

`test/` 단일 트리 — `test/dev-env/`(환경 검증), `test/impl/<Nth>/`(Epic 1개 = 차수 1개, `scenario.md`·`result.md`·`logs/`).

| 차수 | Epic | 케이스 | 회차 | 판정 |
|------|------|--------|------|------|
| dev-env | 표준 환경 | 8/8 | 1 | PASS |
| dev-env (재검증 2026-10-07) | 현행 구성 (Valkey·8800·a2a-grpc·0019) | 9/9 | 1 | PASS |
| 1st | 기반 (엔터티·Alembic·감사 해시 체인·OpenAPI 계약) | 9/9 | 2 | PASS |
| 2nd | Identity & Authorization | 16/16 | 2 | PASS |
| 3rd | 카탈로그·레지스트리 | 9/9 | 2 | PASS |
| 4th | 게이트웨이 정책 코어 | 16/16 | 2 | PASS |
| 5th | Unified Model API | 18/18 | 2 | PASS |
| 6th | MCP 경로 | 16/16 | 4 | PASS |
| 7th | A2A 경로 (0.3 + 1.0) | 17/17 | 4 | PASS |
| 8th | Host·오케스트레이션 | 17/17 | 4 | PASS |
| 9th | 관측·감사 | 14/14 | 4 | PASS |
| 10th | 독립 실행·연동·운영 | 11/11 | 3 | PASS |
| 11th | CLI `nexus` | 14/14 | 4 | PASS |
| 12th | 웹 콘솔 (Vitest 23 · Playwright 8 — prod 번들·개발 서버) | 17/17 | 5 | PASS |
| 13th | Helm·에어갭·NFR-P1 + 완료 게이트(prod 회귀) | 21/21 | 3 | PASS |
| 14th | MCP sampling·elicitation 중계·동의 (SCR-C08) | 23/23 | 2 | PASS |
| 15th | 업스트림 mTLS (FR-G02) — 실제 TLS 핸드셰이크 | 11/11 | 2 | PASS |
| 16th | 남은 P0 콘솔 화면 26종 + 2차 완료 게이트 | 10/10 · 게이트 13/13 | 2 · 게이트 3 | PASS |
| 17th | 보안·정합성 결함 (도구 한도·토큰 스코프·A2A 가드·trace PII·에어갭 로컬 모델) | 25/25 | 3 | PASS |
| 18th | 오케스트레이션 신뢰성 (contextId·input-required 재개·재계획·조건 분기·실행 복구·품질 라우팅·첨부·내장 벡터 RAG) | 31/31 | 3 | PASS |
| 19th | 외부 IdP SSO 로그인 (브로커·PKCE·그룹 → 역할) | 22/22 | 3 | PASS |
| 20th | 첫 실행·설치 (마법사 5단계·설정 코드·CLI·에어갭·재로그인) | 30/30 | 4 | PASS |
| 21st | 등록 → 심사 → 사용 (즉시 사용·신뢰 등급·정책 2인 검토·롤백·회귀 게이트) | 37/37 | 3 | PASS |
| 22nd | 채팅·오케스트레이션 편집기 깊이 (응답 메타·승인 맥락·부분 결과·드라이런·코드만 저장) | 40/40 | 3 | PASS |
| 23rd | 운영·관측 화면 깊이 (B·F·G·H·I·J·K·L — 세션 종료·RBAC·PII·마스킹 해제·복구 시험·리포트) + 3차 완료 게이트 | 70/70 | 3 | PASS |
| 24th | 남은 설계 요소 (라우팅 규칙 게시·키별 허용 모델·hosted OBO·가상 시험 사용자·구성요소 헬스·파일 Part·푸시 서명) | 26/26 | 2 | PASS |
| 25th | NFR 실측 (S1 1,000 세션 · A1 장애 주입 4종 — Valkey 장애 대체 보강) | 13/13 | 3 | PASS |
| 26th | 접근성 KWCAG 2.2 + WCAG 2.2 AA (axe 위반 84 → 0) | 3/3 | 6 | PASS |
| 27th | MCP 2026-07-28 dual-era (server/discover·헤더 검증·MRTR) + 4차 완료 게이트 | 17/17 | 1 | PASS |
| 28th | 어두운 테마 접근성 (명시·시스템 다크 axe 위반 67 → 0) | 4/4 | 4 | PASS |
| 29th | MCP 2026-07-28 확장 (업스트림 modern 호출·listen·Mcp-Param·progress 중계) | 34/34 | 2 | PASS |
| 30th | 동의·승인 시간 연장 (WCAG 2.2.1, 10회 상한) | 16/16 | 1 | PASS |
| 31st | Helm 실제 설치 (전용 kind: install → 스모크 → 삭제) + 5차 완료 게이트 | 18/18 | 4 | PASS |
| 32nd | MCP 리소스 구독·Tasks 확장(SEP-2663) | 18/18 | 4 | PASS |
| 33rd | A2A 1.0 기본화 (0.3 호환 유지) | 12/12 | 2 | PASS |
| 34th | 운영형 클러스터 (kind 다중 노드·Calico·ingress-nginx·HPA·PDB) | 21/21 | 3 | PASS |
| 35th | SLO·오류 예산 + 6차 완료 게이트 | 14/14 | 2 | PASS |
| 36th | A2A HTTP+JSON(REST) 바인딩 | 15/15 | 2 | PASS |
| 37th | A2A gRPC 바인딩 (compose `a2a-grpc`) | 13/13 | 3 | PASS |
| 38th | 운영 클러스터 심화 (cert-manager TLS·외부 PostgreSQL·NFS RWX·노드 장애) | 27/27 | 3 | PASS |
| 39th | 품질 검토 반영 (보안·결함·심층·Lv2 라인 리뷰) + 7차 완료 게이트 | 36/36 | 5 | PASS |
| 40th | 인증 경계 강화 (토큰 교환 워크로드 인증·신고 헤더 서버 판정) | 13/13 | 2 | PASS |
| 41st | a2a-grpc Helm·TLS (kind: Ingress GRPC/GRPCS·파드 TLS·NetworkPolicy) | 22/22 | 3 | PASS |
| 42nd | 공급망·라이선스(Apache-2.0)·CI | 8/8 | 1 | PASS |
| 43rd | 정책 하드닝 (readOnlyHint·푸시 URL·scrypt·refresh 재사용) + 8차 완료 게이트 | 12/12 | 2 | PASS |
| 44th | A2A Task 소유권 (플랫폼·게이트웨이 경유 — 교차 사용자 노출 수정) | 11/11 | 2 | PASS |
| 45th | 다중 워커 일관성 (listen 전역 한도·progress 공유 중계·리스) | 10/10 | 2 | PASS |
| 46th | 운영 HA (존 분산·CloudNativePG 장애조치) | 15/15 | 3 | PASS |
| 47th | 성능 정리 (SQL 집계·요약 공유·지문 공유) + 9차 완료 게이트 | 8/8 | 3 | PASS |
| 48th | 객체 수준 인가 전수 점검 (사용자 경로 66개 분류·남의 객체 29건 거절) | 5/5 | 2 | PASS |
| 49th | 남은 Lv2 하드닝 (gRPC 상한·grpcs 사설 CA·stdio 전체 명령·세션 TTL·푸시 재시도) | 6/6 | 2 | PASS |
| 50th | CI·재현성 (액션 SHA 고정·dev-env 재검증·시간 의존 시험 경합 제거) | 5/5 | 1 | PASS |
| 51st | 2차 품질 검토 (위임 보안·결함 리뷰 + Lv2 라인 리뷰 → 확인 결함 수정) + 10차 완료 게이트 | 17/17 | 16 | PASS |
| 52nd | 모델 게이트웨이 정확성 (규칙 timeout_ms·동시 요청 예약·`any`/클라이언트 키 예산) | 4/4 | 2 | PASS |
| 53rd | 신원·레지스트리 경계 (audience 허용 목록·admin 부여·IdP 삭제 세션·게시 소유자·미러 보류·push_verify·TOTP) | 7/7 | 3 | PASS |
| 54th | 운영 배포 보강 (gRPC 헬스·PDB·시크릿 롤아웃·최소 env·digest·잠금 파일·번들 서명 기본) | 5/5 | 1 | PASS |
| 55th | 관측·카운터 성능 (개요·usage SQL 집계·listen 보유자 집합·도구 위험도 단어 분류) + 11차 완료 게이트 | 4/4 | 2 | PASS |
| 56th | 원격 A2A·푸시 경계 (푸시 JWT `iss`·`jti`·본문 해시 필수·jti 재전송 거부·원격 응답 8 MiB 상한·비 JSON 502·스트림 오류 이벤트·history 200·카드 URL) | 5/5 | 2 | PASS |
| 57th | 구성요소 단독 배포 정합성 (예약 작업이 구성요소별로 — 스케줄러는 어느 조합에서든 기동) | 6/6 | 2 | PASS |
| 58th | 키 회전 (관리 명령 — 서명 키 직전 키 유예·공개키 보관 · 암호화 키 재암호화·이전 키 제거 · 다른 워커 즉시 반영) | 5/5 | 5 | PASS |
| 59th | 3차 품질 검토 (위임 보안·결함 리뷰 + Lv2 라인 리뷰 → 확인 결함 17건 수정, `.review/round3/VERIFY.md`) + 12차 완료 게이트 | 17/17 | 2 | PASS |
| 60th | 남은 기록 항목 (신뢰 어휘·`/v0/servers` SQL 페이지·mTLS 필수 + verify:false·토큰 캐시·OpenAPI namespace·CLI 0600·OTLP 상한·A2A 사용량 상태·푸시 토큰 암호화·실행기 오류 원문) | 10/10 | 2 | PASS |
| 61st | 데이터 계층 견고성 (PostgreSQL 문장 2분·잠금 15초 상한·마이그레이션 상한 없음·SQLite busy 30초·KPI 인덱스 0021·표본 정렬) | 5/5 | 1 | PASS |
| 62nd | 키 관리 보강 (generation 카운터·회전 잠금·env 2단계 `*_NEXT`·공개키 이전 키·검증 끝·폐기 kid·회전 감사 선기록·Helm rotationNonce) | 6/6 | 1 | PASS |
| 63rd | 한도 예약 정밀화 (분 슬롯 반환·출력 토큰 예약·주체별 진행 중 상한·Valkey 시계·예약 누수 4경로·예산 현황 버킷) | 6/6 | 1 | PASS |
| 64th | 경계 하드닝 4 (Access Token 스코프 ⊆ 교환 정책·푸시 JWKS 갱신·https·artifacts 상한·OpenAPI 시크릿·드라이런 등급·익명 발견 API·소유자 없는 항목·readiness 공유) | 8/8 | 2 | PASS |
| 65th | 4차 품질 검토 (위임 보안·결함 리뷰 + Lv2 라인 리뷰 → 확인 결함 21건 수정, `.review/round4/VERIFY.md`) + 13차 완료 게이트 | 17/17 | 3 | PASS |
| 66th | 남은 기록 항목 2 (프로브 주소·listen 반납 비차단·IdP 내장 issuer·세션 기준·TLS 오버레이·readiness 완화·CLI 0700·복원 기본 끔·varint) | 7/7 | 2 | PASS |

최종 회귀(13차 완료 게이트, 65th): pytest SQLite 467 passed + 2 skipped · PostgreSQL 468 passed + 1 skipped · Vitest 27 · Playwright 77 · helm-check 4 시나리오 · prod 리허설(0021) 65th 5/5(세션 상한·인덱스·동시 회전 두 워커·Valkey 시계·예약 없음·대상용 토큰 스코프·운영 https JWKS·익명 한도·내장 issuer) + 회귀 59th 7/7 · 55th 5/5 · 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th 7/7 · 45th 5/5 · 47th 2/2(k6 제외) · 51st 5/5 (PostgreSQL · 2 workers · Valkey). NFR-P1 재측정 생략(사용자 결정).

최종 회귀(12차 완료 게이트, 59th): pytest SQLite 419 passed + 1 skipped(성능 포함 전부) · PostgreSQL 420 passed · Vitest 27 · Playwright 77(r1 에서 E30-01 1회 시간 초과 → 단독·전체 재실행 통과) · helm-check 4 시나리오 · prod 리허설(0020) 59th 7/7(카드 URL·푸시 발급자·스케줄러 리스·서명 키 회전 두 워커·암호화 키 회전·남은 항목·Valkey 예약 고정 만료) + 회귀 55th 5/5 · 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th 7/7 · 45th 5/5 · 47th 2/2(k6 제외) · 51st 5/5 (PostgreSQL · 2 workers · Valkey). NFR-P1 재측정 생략(사용자 결정).

최종 회귀(11차 완료 게이트, 55th): pytest SQLite 376 passed + 1 skipped(성능 포함 전부) · PostgreSQL 377 passed · Vitest 27 · Playwright 77 · helm-check 4 시나리오 · prod 리허설(0020) 55th 5/5 + 회귀 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th 7/7 · 45th 5/5 · 47th 2/2(k6 제외) · 51st 5/5 (PostgreSQL · 2 workers · Valkey). NFR-P1 재측정 생략(사용자 결정).

최종 회귀(10차 완료 게이트, 51st): pytest SQLite 356 passed + 1 skipped(성능 포함 전부) · PostgreSQL 357 passed · Vitest 27 · Playwright 77 · helm-check 4 시나리오 · prod 리허설 51st 5/5 + 회귀 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th 7/7 · 45th 5/5(재빌드 직후 1회 리스 인계 타이밍 실패 → 재실행 통과) · 47th 2/2(k6 제외) (PostgreSQL · 2 workers · Valkey). dev-env 재검증 9/9(2026-10-07). NFR-P1 k6 재측정은 이번 차수 생략(사용자 결정).

최종 회귀(9차 완료 게이트, 47th): pytest SQLite 323 passed + 1 skipped(성능 2건은 호스트 부하) · PostgreSQL 326 passed · Vitest 27 · Playwright 77 · helm-check 4 시나리오 · prod 리허설 44th 7/7 + 45th 5/5 + 47th 4/4 + 회귀 40th 8/8 · 39th 10/10 · 36th 10/10 · 37th 9/9 · 43rd 8/8(소유권 순서 조정 사본) (PostgreSQL · 2 workers · 0019) · kind CNPG 장애조치 15/15. NFR-P1 재측정(k6)은 다른 프로젝트 부하(load ≈ 14)로 임계 초과 — 판정 보류(`test/impl/47th/result.md`).
8차 게이트(43rd): pytest SQLite 310 passed + 1 skipped(성능 2건은 다른 프로젝트 부하로 임계 초과 — A/B 로 회귀 아님 확인, 재측정 필요) · PostgreSQL 313 passed(최종 정리 후 재실행 312 + 성능 1건 부하) · Vitest 27 · Playwright 77 · helm-check 4 시나리오 · prod 리허설 40th 8/8 + 42nd 4/4 + 43rd 8/8 + 회귀 36th 10/10 · 37th 9/9 · 39th 10/10 · 35th 8/8 (PostgreSQL · 2 workers · 0019) · kind a2a-grpc 20/20.
7차 게이트(39th): pytest SQLite 298 passed + 1 skipped · PostgreSQL 299 passed · Vitest 27 · Playwright 77 · helm-check 4 시나리오 · prod 리허설 36th 10/10 + 37th 9/9 + 39th 10/10 + 회귀 32nd 11/11 · 33rd 7/7 · 35th 8/8 (PostgreSQL · 2 workers · 0018) · kind 운영 클러스터 27/27(노드 장애 중 가용성 1.0).
6차 게이트(35th): pytest SQLite 263 passed + 1 skipped · PostgreSQL 264 passed · Vitest 27 · Playwright 77 · prod 리허설 32nd 11/11 + 33rd 7/7 + 35th 8/8 + 회귀 29th 13/13 · 30th 9/9 · 27th 11/11 (PostgreSQL · 2 workers · 0018) · kind 다중 노드 21/21.
5차 게이트(31st): pytest SQLite 247 passed + 1 skipped(PG 전용) · PostgreSQL 248 passed · Vitest 27 · Playwright 75(접근성 밝은 3·어두운 4 포함) · prod 리허설 29th 13/13 + 30th 9/9 + 27th 11/11 + 24th 13/14(P24-01 은 당시 head `0014` 고정 — 현재 `0016`, 기대된 차이) (PostgreSQL · 2 workers · 0016) · Helm kind 18/18. 4차 게이트(27th): 237+1 · 238 · 26 · 67 · 11/11 + 14/14. 3차 게이트(23rd): 224+1 · 225 · 58 · 44/44.

NFR-P1 실측(prod 리허설 · PostgreSQL · 2 workers · mock 공급자 · k6 32 rps): Unified Model API p95 **10.4ms**(≤ 30) ·
MCP 프록시 순수 추가 지연 p95 **17.6ms**(≤ 20) · 첫 토큰 p95 **13.46ms**(≤ 2s) — `deploy/k6/gateway.js`, `test/impl/16th/result.md`
(1차 13th: 15.7 · 18.2 · 12.4ms).

NFR-S1·A1 실측(25th, prod 리허설): 동시 1,000 MCP 세션 오류 0~0.01%·도구 호출 p95 583~917ms(임계 1s, 같은 Docker VM 의 다른 스택 부하 중 — 단일 클라이언트 p95 24ms, 18th 조용한 호스트 80ms) — `deploy/k6/sessions.js`.
장애 주입(`deploy/k6/chaos.js` 모델 20 rps + MCP 10 rps · 180초 · 장애 4건): 전체 **99.83%** · 워커 강제 종료 99.92%/복구 1s · 1순위 업스트림 장애 **100%**(폴백) · Valkey 재시작 **100%** · PostgreSQL 재시작 99.41%/복구 2s — `test/impl/25th/result.md`.

## 트러블슈팅 (구현 중 실제로 겪은 것)

| 증상 | 원인 | 해결 |
|------|------|------|
| `make local-all` 후 8000 포트 충돌 | 호스트 8000 을 다른 프로젝트가 사용 | `API_PORT=8800` (기본값) |
| local·prod 를 함께 띄우면 포트 충돌 | 둘 다 `API_PORT` 사용 | 한 모드만 기동 (`make local-stop` 후 `make prod-all`) |
| 승인 직후 실행이 `waiting_approval` 로 보임(시험 경합) | 승인 API 는 단계만 되돌리고 재개를 예약 — 재개 작업이 잡을 때까지 실행 행은 대기 상태 | 종료 상태(`succeeded·partial·failed`)까지 기다린다 (50th) |
| 성능 시험이 부하 때만 실패 | 다른 프로젝트 컨테이너가 CPU 를 씀 — 지연을 늘리기만 하는 잡음 | `PERF_TRIES`(기본 3)회까지 재측정·최솟값 판정 (50th) |
| 셸 스모크에서 JSON-RPC 본문이 빈 응답 | bash 3.2 에서 `"$(f "{\"k\":…}")"` 중첩 인용이 깨짐 | 본문을 먼저 변수로 만들고 `-d "$BODY"` (51st) |
| 기계 토큰으로 승인·토큰 발급이 403 | 승인·동의는 사람(`user`·`pat`)만, 토큰 발급은 로그인 세션에서만 (51st) | 콘솔·CLI 로그인 세션으로 실행 |
| `nexus bundle import` 가 '서명 검증 불가'·'매니페스트 서명' 으로 중단 | 반입은 서명 검증이 기본(54th) — 검증 키는 번들 밖에서 받은 것만(59th, 번들 안 `cosign.pub` 는 쓰지 않음) | 운영자가 `cosign sign-blob --key … manifest.json > manifest.json.sig` 로 서명해 동봉하고 공개키는 따로 전달 → `--cosign-key <공개키>`, PoC 는 `--allow-unsigned` |
| 업그레이드 뒤 원격 에이전트 푸시가 모두 401 | 푸시 서명 JWT 는 `iss`·`jti`·`request_body_sha256` 필수, JWKS 를 쓰면 발급자도 필수(56th) — 이전에 발급자 없이 저장한 `push_verify` 는 통과하지 못함 | 에이전트 `auth.push_verify.issuer` 지정, 원격 에이전트가 세 클레임을 넣는지 확인 |
| `nexus keys rotate` 가 409 `keys_env_managed` | 키가 env(Secret)로 주입됨 — 앱은 파일 모드 키만 회전 | SECURITY.md 키 회전의 env 2단계 절차(`*_NEXT` 배포 → 현재 키로 올리고 `*_PREVIOUS` → `nexus keys reencrypt`, 62nd) |
| `nexus install compose --cosign-key` 가 '레지스트리에서 받지 못함'·다이제스트 검증 실패 | 서명 검증은 레지스트리에서 받은 다이제스트로만(59th) — 로컬 빌드 태그(`wise-agentnexus-api:prod`)는 검증 대상이 없다 | 레지스트리 이미지 이름으로 바꾸는 compose 오버레이, PoC 는 `--allow-unsigned` |
| 사용 기록에 `cancelled` 상태 | 스트리밍 중 클라이언트가 끊음(59th) — 받은 만큼 추정해 기록·정산 | 정상 동작 (오류율 집계에는 오류로 셈) |
| 서비스 목록에 `opa` 가 남음 | 프로파일 밖 컨테이너는 `down` 이 지우지 않음 | `COMPOSE_PROFILES=demo,opa make local-stop` |
| zsh 에서 `$DC run …` 이 "no such file" | zsh 는 변수 단어 분리를 하지 않음 | `make` 타겟 사용 또는 `${=DC}` |
| 이미지 안 `pnpm install --frozen-lockfile` 실패 | package.json 만 바꾸고 잠금 파일 미갱신 | `make web-lock` (설치 직후 사본을 추출) |
| 콘솔 로그인 후 `/login` 으로 되돌아감 | 번들 없음 또는 콜백 처리 전에 이동 | `make web-build`, 콜백 완료까지 대기 |
| `/console` 503 `console_not_built` | local 에서 `web/dist` 없음 | `make web-build` (prod 이미지는 빌드 시 포함) |
| `docker compose up web` 직후 첫 요청 ECONNREFUSED | depends_on 으로 api 컨테이너가 재생성되는 중 | 헬스 확인 후 재시도 |
| prod 리허설에서 BOOTSTRAP 비밀번호로 로그인 실패 | `admin` 이 이미 있으면 시드가 비밀번호를 바꾸지 않음(의도) | 별도 리허설 계정 사용 (`test/impl/13th/smoke-prod.sh`) |
| k6 에서 429 | 기본 한도 주체별 50 rps | 도착률 고정(32 rps) 또는 한도 조정 |
| MCP 서버 도구 이름 불일치 | FastMCP 가 operationId 의 `.` 를 `_` 로 바꿈 | 변환된 이름 사용 |
| SQLite 에서 아카이브 후 감사 체인 깨짐 | 삭제 후 rowid 재사용 | seq 를 `last_seq + 1` 로 명시 |
| 프로세스 내 시험에서 sampling 요청이 오지 않고 멈춤 | httpx `ASGITransport` 는 응답 전체를 버퍼링해 SSE 중간 이벤트가 안 옴 | `app.core.http.StreamingASGITransport`(청크 단위 전달) |
| fastmcp 클라이언트 elicitation 핸들러가 검증 오류 | 핸들러는 `ElicitResult` 가 아니라 데이터 dict 를 반환해야 함 | dict 반환(거절·취소만 결과 객체) |
| 관리 API 오류에서 `error.code` 가 없음 | 관리 REST 는 problem+json — `code` 가 최상위 | `.json()["code"]` 로 판별 |
| 원격 위임 카드가 안 보임 | 실행이 Task 생성보다 빨리 끝나 마지막 조회를 놓침 | 실행 상태가 바뀌면 재조회 |
| mTLS 추가 후 MCP 순수 추가 지연 p95 22.2ms(> 20) | 설정 없는 업스트림 호출마다 정책을 DB 에서 읽음 | 정책 5초 캐시 + 설정 없는 대상은 DB 없이 기본 연결 → 17.6ms |
| 개발자 A2A 등록이 `database is locked`(SQLite) | 열린 쓰기 트랜잭션 안에서 별도 세션으로 알림 기록 | 커밋 뒤 알림 |
| 그래프 YAML 의 `replan.on` 이 무시됨 | YAML 1.1 이 `on:` 키를 `true` 로 읽음 | 키는 `replan.triggers` (기존 `on` 도 받음) |
| 셸 스크립트에서 JSON 판정이 비어 있음 | `echo` 가 문자열 안 `\n` 을 줄바꿈으로 바꿔 jq 파싱 실패 | `printf '%s'` |
| 승인 만료 후 실행이 대기에 멈춤(다중 프로세스) | 이벤트 루프 없는 프로세스에서 재개 예약 실패 | 복구 작업이 "기다릴 단계 없는 대기 실행"을 재개 |
| 세션 강제 종료 직후 새 로그인도 401 | JWT `iat` 는 초 단위, 종료 시각은 마이크로초 | 초 단위로 비교 (`int(nbf.timestamp())`) |
| 권한 행렬·PII 규칙을 바꿨는데 일부 요청은 이전 값 | 워커별 5초 캐시 | 최대 5초 뒤 전 워커 반영(설계) · 시뮬레이터는 저장값을 직접 읽음 |
| '다른 세션 종료' 가 현재 브라우저를 끊음 | 가장 최근 세션을 현재로 추정 | 액세스 토큰 `sid` 로 현재 세션 판정 |
| `POST /api/v1/admin/restore` 400 `confirm_required` · `nexus bundle import` 실패 | 복원은 `confirm: "RESTORE"` 필수 | 콘솔은 RESTORE 입력, CLI 는 `--restore-config` 로 전달 (복원 직전 `-prerestore` 스냅샷 자동) |
| 구성요소 복제 수가 prod 에서 1 | 이미지 CMD 가 워커 수를 고정 | `UVICORN_WORKERS` (이미지·.env·Helm `api.workers`) |
| e2e 표 머리글을 `columnheader` 로 못 찾음 · select 라벨이 옵션 문구까지 포함 | 시험 로케이터 | `thead` 문구 · 근처 버튼 이름으로 |
| Valkey 재시작 중 게이트웨이 요청 실패 | 한도 검사가 Valkey 연결 오류를 그대로 올림 | 연결 실패 시 5초 동안 워커 로컬 버킷으로 대체(`backend_status.degraded`) — 25th 재시작 구간 100% |
| k6 동시 1,000 세션 p95 가 실행마다 크게 다름 (583ms~6.1s) | 같은 Docker VM 의 다른 프로젝트 부하 | 단일 클라이언트로 요청당 비용 확인(p95 24ms), 조용한 시간에 재측정 |
| axe 대비 위반 수백 건 | 의미 색(#2F9E44 등)을 연한 배경 위 글자로 사용 · 보조 글자 #6b7280 | 글자용 진한 토큰 `--ok-fg` 등 · `--fg-muted` #5b6472 |
| e2e 에서 `getByLabel` 이 여러 요소에 걸림 | 이전 실행이 만든 키 이름·select 라벨이 옵션 문구 포함 | `exact: true`·버튼 이름으로 |
| kind 파드가 `ImagePullBackOff` (로컬 빌드 이미지) | `kind load docker-image` 의 `--all-platforms` 가져오기가 Docker Desktop containerd 저장소의 다중 플랫폼 인덱스에서 실패 | `docker save --platform <os/arch> <이미지> \| docker exec -i <노드> ctr -n k8s.io images import -` (31st 스크립트) |
| `make prod-build` 가 `8800 port is already allocated` 로 끝남 | prod-build 는 빌드 뒤 기동까지 한다 — local 이 같은 포트 사용 중 | 빌드는 끝난 상태 — `make prod-stop` 후 리허설은 `make local-stop` 먼저 |
| 어두운 테마에서 Tailwind `dark:` 변형이 반대로 적용 | `dark:` 가 시스템 설정만 따르고 `data-theme` 를 무시 | `@custom-variant dark` 를 토큰 규칙과 같게(명시 > 시스템) |
| modern 업스트림 경유 progress 가 같은 값을 다시 보냄 | MRTR 가드 패턴은 재시도 때 도구를 처음부터 다시 실행 | 게이트웨이가 하류 토큰별로 증가하지 않는 값을 버림 |
| kind + Calico 에서 파드 → API 서비스(10.96.0.1) 시간 초과 | kind 도커 네트워크(192.168.144.0/20)가 Calico 기본 풀 192.168.0.0/16 과 겹침 | pod CIDR 10.244.0.0/16 + `CALICO_IPV4POOL_CIDR` 같은 값 |
| kind ingress 경유 "Empty reply" | 현재 ingress-nginx kind 매니페스트가 컨트롤러를 `ingress-ready` 노드에 묶지 않음 | 컨트롤러에 `nodeSelector: ingress-ready=true` 패치 |
| 스모크 스크립트가 같은 요청을 여러 번 보냄 | macOS `/bin/sh`(bash 3.2)가 `"$( … "{…,…}" … )"` 안 중괄호를 확장 | JSON 을 변수로 만든 뒤 넘김 |
| NFS-Ganesha 프로비저너 `ImagePullBackOff` | 업스트림 매니페스트의 이미지 태그(v6.5)가 레지스트리에 없음 | `registry.k8s.io/sig-storage/nfs-provisioner:v4.0.8` 고정 |
| NFS PVC `Pending`, 로그 `AddExport … invalid param value` | kind 노드 `/` 가 overlayfs — Ganesha VFS 가 내보내지 못함 | export hostPath 를 노드 `/var`(ext4) 아래로 |
| 노드 장애 뒤 새 api 파드가 `Init:CrashLoopBackOff`(DB 이름 해석 실패) | CoreDNS 두 복제가 장애 노드 한 곳에 있었음 | CoreDNS 필수 anti-affinity 로 노드 분산 |
| kind 삭제 뒤 노드 컨테이너가 죽지 않음(`did not receive an exit event`) | NFS 마운트를 쥔 채 서버·노드를 지워 커널 NFSv4 정리 RPC 가 멈춤 | helm uninstall → 앱 파드 소멸 대기 → NFS 서버 삭제 → kind 삭제 순서(멈춘 경우 수십 분 뒤 풀림) |
| 계약 시험이 새 REST 경로로 실패 | OpenAPI 스냅샷에 새 엔드포인트 | `UPDATE_CONTRACT=1 make test` 로 스냅샷 갱신(삭제는 계약 파괴로 실패) |
| 차트 env 정합 시험 실패(`A2A_GRPC_*`) | compose 전용 키가 ConfigMap 에 없음 | 호스트 포트는 host_only, `A2A_GRPC_PUBLIC_URL` 은 ConfigMap 에 빈 값 |
| prod 에서 원격 A2A GetTask 가 가끔 NOT_FOUND | runtime 이미지 env `UVICORN_WORKERS=2` 를 데모 서버(uvicorn 이 `UVICORN_*` 를 읽음)도 받아 2 워커 — 데모 Task 는 프로세스 메모리 | compose·Helm 데모 서비스에 `UVICORN_WORKERS=1` |
| kind 에서 gRPC·데모 호출이 막힘(UNAVAILABLE·FAILED_PRECONDITION) | `networkPolicy.ingressFrom` 을 Ingress 컨트롤러만으로 두면 같은 클러스터의 클라이언트·데모 서버도 api·a2a-grpc 에 못 간다 | 필요한 출처(파드 선택자)를 `ingressFrom` 에 추가 — 같은 릴리스의 api·mcp-servers·a2a-demo·a2a-grpc → api 는 차트가 자동으로 연다(51st). 클러스터의 다른 클라이언트·Prometheus 는 직접 추가 |
| 롤아웃 직후 `kubectl logs deploy/…` 가 이전 파드 로그 | 종료 중 파드가 남아 `Found 2 pods` | 새 파드 하나만 남을 때까지 기다린 뒤 확인 |
| a2a-grpc 파드 `mkdir: cannot create directory '/tmp/prometheus'` | 읽기 전용 루트 — 진입점이 메트릭 디렉터리를 만든다 | 차트에 `/tmp` emptyDir(41st) |
| 마이그레이션 `no such table: refresh_tokens` | 테이블 이름은 `oauth_refresh_tokens` | 모델 `__tablename__` 확인 후 batch_alter_table |
| 구독을 끊었는데 listen 한도가 한동안 풀리지 않음 | `finally` 안의 `await` 는 연결 끊김(anyio 취소) 때 다시 취소돼 반납이 빠짐 | 반납은 await 없이 동기로(45th) |
| CNPG primary 파드를 지워도 장애조치가 안 일어남 | smart shutdown 이 풀의 유휴 연결을 기다려 파드가 Terminating 에 머묾 | 장애 시험은 노드 정지로 · 운영은 `smartShutdownTimeout`·`kubectl cnpg promote` |
| Playwright 가 모두 `ERR_EMPTY_RESPONSE` | local 스택이 내려가 있음 | `make local-all` 후 재실행 |
| Playwright E30-01(동의 카드 거부 뒤 사라짐)이 가끔 30초 초과 | 전체 실행(7분) 중 부하로 동의 결과 반영이 늦음 — 단독·재실행은 통과(59th) | 해당 spec 재실행 |
| PgBouncer 뒤에서 연결 실패 `unsupported startup parameter: options` | 세션 상한(61st)을 libpq `options` 로 넘긴다 — 풀러가 시작 파라미터를 거부 | 풀러에 `ignore_startup_parameters = options` 를 두고 DB 쪽에 `ALTER ROLE <app> SET statement_timeout = '120s'`·`lock_timeout = '15s'` |
| 0021 업그레이드 중 실행 이벤트·스팬 기록이 15초 뒤 실패 | 인덱스 생성(쓰기 잠금) 동안 옛 파드의 INSERT 가 잠금 상한에 걸림 | `spans` 가 크면 유지보수 창에서 `make db-migrate ENV=prod` 를 먼저 |
| 기동 실패 `현재 서명 키가 SIGNING_REVOKED_KIDS 에 있습니다` | 현재 kid 를 폐기 목록에 넣음(65th — 유출 키로 계속 통과시키지 않게 멈춤) | 새 키로 회전한 뒤 이전 kid 를 폐기 |
| `nexus keys reencrypt` 409 `rotation_in_progress` | 그 파드에 아직 `ENCRYPTION_KEY_NEXT` 가 남음(2단계 회전 1단계) | 2단계 롤아웃을 마친 뒤 다시 |

## 알려진 제한 · BLOCKED

- BLOCKED 없음 (계획표 66/66 DONE).
- 3차(Epic 17~23)에서 비활성 + 사유로 남겼던 설계 요소는 4차 Epic 24 에서 모두 구현했다 — 라우팅 규칙 버전 게시(H02) · hosted OpenAPI 서버 OBO 전파(F04) · 클라이언트 키별 허용 별칭(H13) · 구성요소별 헬스·재시작 횟수(B05) · A2A `framework` 열·시험 콘솔 파일 Part·푸시 JWT 서명 검증(G01·G07) · 테스트 사용자로 도구 호출(F06).
- 401·403·429·5xx 카운터는 Prometheus 카운터(프로세스 시작 이후, 워커 합산)다 — 재시작하면 0 부터. 1시간 지표는 사용 기록 기준.
- MCP 2026-07-28 은 하류(dual-era)·업스트림(서버별 auto·legacy·2026-07-28) 모두 지원한다. `subscriptions/listen` 은 목록 변경·리소스 내용·작업 상태를 5초 지문 비교로 알린다(업스트림 `resources/subscribe` 를 쓰지 않음). Tasks 확장은 2026-07-28 하류 전용 — 작업은 만든 워커에서 돌고, 그 워커가 죽으면 failed(재실행 안 함). progress 는 업스트림 호출이 도는 워커에서만 중계된다(MRTR 재시도가 다른 워커로 가면 결과만). hosted-openapi·stdio 업스트림의 progress 는 범위 밖.
- 접근성 자동 점검은 밝은·어두운 테마 모두 위반 0(26th·28th). 동의·승인 기한은 사용자가 연장한다(최대 10회, 30th) — 하류 MCP 클라이언트 중계·MRTR 응답 대기는 사람 화면이 아니라 대상 밖.
- PostgreSQL·Valkey 는 단일 인스턴스 — DB 재시작 중 요청 실패(25th 8건)는 HA 구성 범위.
- RAG 임베딩은 시험용 결정적 mock 임베딩(내장 벡터 색인, 앱 내 코사인)이다 — 실제 임베딩 모델은 `/v1/embeddings` 공급자 교체로.
- MCP 서버→클라이언트 요청은 POST 응답 SSE 로만 중계한다 — 독립 SSE 스트림(`GET /mcp/{id}`)은 405(사양상 허용). 2026-07-28 클라이언트에는 MRTR(`InputRequiredResult`)로 보낸다(27th). 2026-07-28 사양에서 sampling 은 폐기 예정(deprecated)이다.
- 인바운드 mTLS 는 TLS 종료 지점(Ingress) 책임 — 게이트웨이는 업스트림 mTLS 만 수행. mTLS 정책 변경은 저장한 워커에 즉시, 다른 워커에는 최대 5초 뒤 반영된다.
- 상용 LLM·Jira·Confluence·Dynatrace·외부 IdP 실연동 없이 내장 mock 공급자·데모 참조 서버·내장 IdP 로 시험했다(PRD D8). 실제 연동은 공급자·커넥터 설정으로 교체한다.
- 인증 없는(`auth: none`) 외부 MCP 서버는 게이트웨이 정책·감사는 적용되지만 업스트림으로 사용자 신원이 전파되지 않는다 — OAuth(RFC 9728) 서버 사용을 권장.
- 설치 화면(A01~A04·A11)은 웹이 아니라 CLI·Helm 이다(설계서 화면 유형 그대로).
- Helm 차트는 kind 단일 노드(31st), 다중 노드 + Calico·ingress-nginx·HPA·PDB(34th), cert-manager TLS·외부 PostgreSQL·NFS RWX·워커 노드 장애(38th, 가용성 1.0)에서 설치·스모크·삭제까지 검증했다. 매니지드 DB HA·존 분산·단일 복제 구성요소(valkey·데모 서버)의 HA 는 실제 운영 클러스터 몫.
- A2A 는 1.0 이 기본이고 0.3 을 계속 받는다(D15-②). 바인딩은 JSONRPC·HTTP+JSON·GRPC 세 가지(D16) — 푸시 설정 목록·삭제(`ListTaskPushNotificationConfigs`·`DeleteTaskPushNotificationConfig`)는 UNSUPPORTED_OPERATION. gRPC 는 기본 평문 수신(앞단 TLS 종료) 또는 `A2A_GRPC_TLS_*`·Helm `a2aGrpc.tls` 로 직접 TLS, 원격 호출은 `grpcs://` 주소면 TLS.
- Task 소유권(44th): 플랫폼 A2A 서버(`/a2a/platform[/{profile}]` — JSON-RPC·REST·gRPC)와 게이트웨이 경유 원격 Task 는 만든 주체만 — 남의 Task 는 목록에서 빠지고 get·cancel·resubscribe·푸시·이어 보내기는 TaskNotFound(-32001, REST 404, gRPC NOT_FOUND). 원격 에이전트가 게이트웨이 밖에서 만든 Task 는 게이트웨이 목록에 나오지 않는다.
- 다중 워커(45th): listen 동시 구독 5 는 워커 전체 합(Valkey 카운터, TTL = 최대 구독 시간 + 60초) · MRTR 재시도가 다른 워커로 가도 progress 는 공유 행(`mcp_relays`)으로 이어진다 · MCP 헬스 루프·A2A watcher 는 리스 보유 워커만.
- 운영 HA(46th): `api.topologySpreadConstraints`(존 분산) · PostgreSQL 연결은 `connect_timeout` 5초·TCP keepalive · CloudNativePG 노드 장애 장애조치 56초·`/health` 복구 65.5초(kind 기준). primary 파드 정상 종료는 smart shutdown 대기로 늦다 — `smartShutdownTimeout` 조정 또는 사전 전환.
- 인증 경계(40th): `/idp/token` 토큰 교환은 워크로드 `client_id`·`client_secret` 인증 필수(`act`=워크로드) · `X-AgentNexus-Agent` 는 워크로드·Access Token·위임 토큰만 받는다(사람이 보낸 값은 무시) · 기본값보다 낮은 `X-AgentNexus-Data-Class` 신고는 `data:declare` 권한(모든 역할 기본, RBAC 로 회수 — 없으면 기본값으로 올리고 감사) · `X-AgentNexus-Run` 은 자기 실행만. `test/impl/2nd/smoke.sh` 의 토큰 교환 단계는 당시 동작 기준이다.
- 정책 하드닝(43rd): `readOnlyHint` 는 trusted 서버만 위험도를 낮춘다 · 클라이언트 푸시 URL 은 prod 에서 공인 주소만 · scrypt N=2^14·r=8·p=5(로그인 시 이전 해시 재해싱) · 회전된 refresh token 재제시(10초 유예 밖) → 같은 계열 전부 폐기(migration 0019).
- 라이선스(42nd): Apache-2.0 · 저작권자 표기 `AgentNexus contributors` 는 가정 · `make audit` 은 CI 에서만(네트워크) · 원격 `grpcs://` 호출은 시스템 신뢰 저장소 + 에이전트 `auth.tls.ca_secret`(사설 CA, 49th).
- 보안 검토 반영(39th): 외부 IdP 비대칭 alg 만 · egress 가드(링크 로컬·클라우드 메타데이터 차단, 사설망 업스트림은 허용 — DNS 재바인딩은 egress NetworkPolicy 로) · 로그인 실패 10회/15분 잠금(local 은 워커 단위, prod 는 Valkey 공유) · loopback redirect 는 local/test 만 · 남의 시크릿 참조는 `gateway:write` 필요. 남은 결정 항목은 8차(40th~43rd)에서 반영 — 미반영은 `test/impl/39th/scenario.md`·`test/impl/43rd/result.md`.
- 2차 검토 반영(51st): 모든 응답에 `X-Frame-Options: DENY`·`frame-ancestors 'none'`·`nosniff`·`no-referrer`(HSTS 는 Ingress) · 승인·동의 결정은 사람(`user`·`pat`)만 — 워크로드·Access Token·위임 토큰은 자기 요청도 승인 못 함 · 토큰으로 새 토큰 발급 불가(로그인 세션에서만)·PAT 최대 90일(무기한 없음)·다른 팀 클라이언트 키는 `admin:write` · MFA 실패도 로그인 잠금 창에 셈 · IP 정책은 비밀번호 폼·디바이스 승인·외부 IdP 콜백 모두 · `X-AgentNexus-Data-Class` 모르는 값은 MCP·A2A 도 거부(대소문자 무시) · 모델 `data_classes` 적용, 한도 폴백 모델도 정책·허용 등급·에어갭 검사(막히면 429) · MCP 인증 설정의 인가 서버·토큰 엔드포인트·mode 변경은 `gateway:write` · 개발자 등록 대기 에이전트는 PATCH 로 활성화 못 함 · 푸시 URL 은 전달 직전 재검사, 원격 에이전트에 주는 콜백은 사설망이면 `GATEWAY_URL`·외부면 `PUBLIC_BASE_URL` · 푸시·폴링 결과도 응답과 같은 소독, 비활성 사용자·차단 에이전트는 폴링·푸시 안 함 · 샘플링 중계에도 서버 deny 정책 · 외부 IdP 표시 이름은 정책 user 주체와 매치하지 않음(sub 로 지정) · Alibaba IMDS 차단. 게시 메타데이터의 `_anx_*` 예약 키 거부 · 저장된 시크릿을 `secret_ref` 로 다시 붙이거나 시크릿이 묶인 설정의 인증 형태(type·scheme·header·tls)를 바꾸려면 `gateway:write`(새 값을 직접 넣는 설정은 그대로) · 재등록으로 엔드포인트를 바꿀 때도 같은 규칙 · 기한 지난 승인은 결정 대신 자동 거부(예약 작업) · 게이트웨이가 모르는 Task 의 푸시 설정은 -32001 · 플랫폼 A2A 취소는 오케스트레이션 실행도 취소 · 플랫폼 `tasks/list` 는 워커와 무관하게 내 Task(영속 최근 50) 전부.
- 11차(52nd~55th): 라우팅 규칙 `timeout_ms` 가 시도마다 실제로 적용된다(시드 규칙 30초 — 넘으면 504 공급자 오류로 재시도·다음 후보) · 토큰·예산 한도는 진행 중 요청의 예상치를 예약해 동시 요청이 함께 넘지 못한다(완료 시 정산, 정산 없이 워커가 죽으면 예산 예약은 300초 뒤 풀림) · `any` 예산은 팀 없는 주체도 그 사용자 사용액으로, 클라이언트 키 범위 예산 적용(migration 0020) · Access Token audience 는 게이트웨이·등록된 `mcp:<id>`·`a2a:<id>` 만 · admin 역할 부여·IdP admin 매핑·관리자 계정 수정은 admin 만 · IdP 를 지우면 그 IdP 사용자 세션 종료 · 레지스트리 새 버전은 항목 소유자(또는 승인 권한자)만, 미러 자동 승인은 외부 인가 서버를 쓰는 항목을 보류 · `push_verify` 변경은 `gateway:write` · 같은 TOTP 코드 재사용 거부 · a2a-grpc 는 `grpc.health.v1`(readiness = api 까지) 프로브, compose prod 의 a2a-grpc 는 시크릿 없는 최소 env · Helm 시크릿 교체 시 api·데모 재기동 · listen 동시 구독 한도는 보유자 집합(만료 고정 — 죽은 워커 몫이 활동 중에도 풀림) · 도구 위험도는 단어 단위(camelCase 포함) 분류.
- 12차(56th~60th): A2A 푸시 서명 JWT 는 `iss`·`iat`·`jti`·`request_body_sha256` 필수, 같은 `jti` 재전송 거부(15분), JWKS 를 쓰면 `push_verify.issuer` 도 필수 · 원격 에이전트 응답·이벤트 8 MiB 상한(넘으면 502·오류 이벤트), 200 인데 JSON 이 아니면 502, Task history 는 최근 200 · 게이트웨이 프록시·플랫폼 카드 URL 은 `PUBLIC_BASE_URL`(Host 헤더 무시) · 예약 작업은 구성요소별(경보·SLO = observe, 실행 복구 = host, 백업 = admin, 승인 만료 = gateway·host, 레지스트리 = registry — 스케줄러는 어느 조합에서든 기동) · 키 회전은 관리 명령(`nexus keys …`, SECURITY.md 절차) · 신뢰 등급 official·partner 서버의 `readOnlyHint` 인정 · `/v0/servers` SQL 페이지 · mTLS 필수 정책에서 `verify: false` 업스트림 거부 · 에이전트 인증을 바꾸면 그 워커의 캐시 토큰 폐기 · OpenAPI 서버 생성은 쓰는 중인 namespace 면 시크릿을 쓰기 전에 409 · CLI 토큰·시크릿 파일 0600 · OTLP 수신 8 MiB(413)·형식 오류 400 · A2A 사용량은 예외도 error · 클라이언트 웹훅 토큰 암호화 저장 · 플랫폼 실행기 오류는 원문 대신 참조 id. 3차 검토 반영(59th): admin 으로 매핑된 IdP 의 신뢰 설정(jwks_uri·internal_url·audience·클레임)은 admin 만, admin 부여는 admin 역할 + `admin:write` 스코프 · 스트림 중 끊겨도 받은 만큼 사용 기록(`cancelled`)·정산, 예산 예약은 예약마다 300초 고정 만료 · 번들 반입 검증 키는 번들 밖(`--cosign-key`) · 설치는 받은 다이제스트로 서명 검증 후 그 이미지로 기동 · 미러는 인가 서버가 플랫폼으로 고정되지 않은 oauth2 항목을 보류하고 다른 출처 항목에 버전을 얹지 않음 · 한도 폴백 모델이 없거나 꺼져 있으면 429 · 실행 재개는 Host 만 · 원격 SSE 줄·인증 없는 푸시 수신도 상한, 압축 응답 거부 · 재암호화는 웹훅 토큰 포함·500행 배치 · 공백 넣은 TOTP 재사용 거부.
- 13차(61st~66th): PostgreSQL 연결 세션 상한 — 문장 2분·잠금 대기 15초(`DB_*_TIMEOUT_MS`), 마이그레이션·보존 정리·감사 체인 머리 잠금·시드는 상한 없이 · 개요 KPI 인덱스(migration 0021)·최근 500 표본 · 키 관리: `keys/generation` 카운터·회전 잠금, env 2단계 회전(`*_NEXT` → 올림 → `*_PREVIOUS`·`SIGNING_KEY_PREVIOUS_UNTIL`)·공개키 PEM 이전 키·폐기 kid(`SIGNING_REVOKED_KIDS` — 현재 키면 기동 실패, 먼저 회전)·회전 감사 선기록·Helm `secrets.rotationNonce` · 한도: 토큰 예약은 예약한 분에만 반환·출력(`max_tokens`, 없으면 256) 예약은 남은 여유분까지·안 쓴 만큼 반환, `max_tokens` 는 1 이상 정수, 주체별 동시 모델 요청 32(`MODEL_MAX_INFLIGHT_PER_SUBJECT`, 클라이언트 키는 키마다), 보유자·예약 만료는 Valkey 시계, 실행하지 않는 계획(라우팅 시험·동의 미리 보기·실패·취소)은 예약을 남기지 않음, 예산 현황은 `*`·`any` 도 버킷별 · 경계: 대상용(`mcp:`·`a2a:`) Access Token 스코프는 교환 정책 허용 스코프 안(빈 스코프 = 정책 기본), 푸시 JWKS 모르는 kid 는 30초에 한 번 다시 받음(워커 공유)·운영은 https(등록·수정·사용 시점)·`push_verify_issuer` 표시, Task artifacts 최근 50·4 MiB, OpenAPI 생성은 namespace·시크릿 선점 409·실패 시 이번에 만든 것만 정리, Inspector 드라이런도 서버 판정 등급, `/v0/servers?search=` 는 이름 부분 일치(SQL)·익명은 IP 별 5 rps + 전체 50 rps, 소유자 없는 항목 새 버전은 승인자만, gRPC readiness 결과 2초 공유 · 남은 항목: 프로브는 수신 주소로, listen 반납 비차단, 내장 issuer 의 외부 IdP 연동 거부·IdP 중지·삭제는 그 사용자 세션·PAT·클라이언트 키까지, a2a-grpc TLS 오버레이 예시, readiness 실패 허용 6회, CLI 설정 디렉터리 0700, `bundle import` 구성 복원은 `--restore-config` 를 줄 때만(서명된 매니페스트의 백업만), 2단계 회전 중 1단계 파드의 재암호화 거부(409 `rotation_in_progress`)·재암호화 응답에 키 지문.
- SLO 월간 판정(99.95%)은 운영 트래픽이 30일 쌓여야 한다 — 다른 워커의 최근 카운터는 그 워커의 다음 예약 주기(`SCHEDULER_INTERVAL_S`)에 반영된다.
