# AgentNexus

An integrated agent platform: MCP + AI Gateway + A2A. Users ask in natural language; every model, tool and remote-agent call
goes through one AI gateway that applies policy, limits, guards, identity propagation and observability.

> Source documents: [PRD.md](PRD.md) (originals `.docs/AgentNexus_PRD.pdf` · `.docs/AgentNexus_UI_Design.pdf`) · plan
> [docs/implementation-plan.md](docs/implementation-plan.md) · compliance overlay [COMPLIANCE.md](COMPLIANCE.md) · 한국어: [README.md](README.md)
> Stack: Python 3.12 · FastAPI · SQLAlchemy 2 + Alembic · uv / React 19 · Vite 7 · Tailwind 4 / PostgreSQL 17 · Valkey 8 · (optional) OPA

## Components

| Component (`COMPONENTS`) | What it does | Main paths |
|---|---|---|
| `identity` | Built-in OIDC IdP (Code+PKCE, Device, Client Credentials, rotating refresh), 6-role RBAC, PATs, RFC 8693 token exchange, workload identity, external IdPs | `/idp/*` · `/api/v1/identity/*` · `/api/v1/me` |
| `gateway` | Unified Model API (OpenAI-compatible), routing, fallback, data-class rerouting, policy (built-in/OPA), rate limits, budgets, guards, tool governance, human approval, MCP/A2A proxy, MCP sampling/elicitation relay and consent, upstream mTLS | `/v1/chat/completions` · `/v1/models` · `/mcp/{id}` · `/a2a/{id}` · `/api/v1/gateway/*` |
| `registry` | Unified catalog (MCP, A2A, models, profiles), MCP Registry-compatible `/v0`, publish approval, source mirrors, SBOM | `/v1/catalog` · `/v0/servers` · `/v0/publish` |
| `host` | Natural-language sessions, SSE, Planner→Retriever/Summarize/Executor/remote A2A orchestration, approval resume, profiles, 3 templates | `/api/v1/sessions` · `/api/v1/runs` · `/api/v1/agents` · `/a2a/platform` |
| `observe` | OTel (GenAI conventions), built-in trace store, OTLP receiver, `/metrics`, PII-masked logs, usage & cost, hash-chained audit, signed reports | `/api/v1/traces` · `/api/v1/logs` · `/api/v1/usage` · `/api/v1/audit/*` · `/v1/traces` |
| `admin` | Deployment profiles, connectors, health, backup/restore, settings, notifications, global search, first-run setup | `/api/v1/admin/*` · `/api/v1/system/*` |
| `console` | Web console SPA (95 of the 100 designed screens; install screens A01–A04 and A11 are CLI) | `/console` · `/chat` · `/login` · `/setup` |

`COMPONENTS=all` is the default. Subsets run independently (FR-W01), for example `gateway,identity` (gateway only) or `registry,identity`.

## Run model

Everything runs through **docker compose**, in two modes: **local** and **prod**.

| Mode | compose files | env file | DB | image stage |
|------|---------------|----------|----|-------------|
| local | `docker-compose.yml` + `docker-compose.local.yml` | `.env.local` (committed) | SQLite (`/app/data/app.db`) | `dev` (bind mount, hot reload) |
| prod | `docker-compose.yml` + `docker-compose.prod.yml` | `.env.prod` (not committed, CHANGE_ME) | PostgreSQL 17 | `runtime` (console bundle included, uvicorn 2 workers) |

`compose/*.yml` (Valkey, OPA) is included in both modes.

| Service | Network | Host port | Profile | Role |
|---------|---------|-----------|---------|------|
| `api` | edge + backend | `API_PORT` (8800) → 8000 | always | gateway, admin API, IdP, registry, host, console — **the HTTP entry point** |
| `a2a-grpc` | edge | `A2A_GRPC_PORT` (8851) → 50051 | always | A2A gRPC binding (`lf.a2a.v1.A2AService`) — stateless; forwards each RPC to the api HTTP+JSON binding (same auth, policy and audit). Plaintext by default (terminate TLS in front), or direct TLS with `A2A_GRPC_TLS_*` |
| `mcp-servers` | backend (internal) | none | `demo` | reference MCP servers (simulated Jira, Confluence, Dynatrace, Internal API, Internal Orders: a stateful SSE server that demonstrates sampling and elicitation) |
| `a2a-demo` | backend (internal) | none | `demo` | demo remote A2A agent (observability analyst, A2A 0.3 and 1.0) |
| `valkey` | backend | none | always | rate-limit token buckets |
| `db` | backend | none | prod | PostgreSQL |
| `opa` | backend | none | `opa` | external policy engine (optional) |
| `ollama` | backend | none | `ollama` | local model for air-gapped installs (optional, `ollama/ollama:0.31.1`, model volume `ollama-models`) |
| `web` | edge | `WEB_PORT` (5173) | `web` (local) | console Vite dev server (HMR), proxies API calls to api |
| `e2e` | edge | none | `e2e` (local) | Playwright (Chromium) browser smoke tests |

The backend network is internal, so reference servers, the DB and Valkey can only be reached through the gateway (FR-G00).

## Requirements

Docker Engine or Docker Desktop with Compose v2, and GNU Make. No Python or Node toolchain is needed on the host: builds, tests and
dependency installs all happen inside image builds. Only `make helm-check` needs `helm` on the host.

## Quick start

```bash
make preflight                 # check Docker, Compose and env files
make local-all                 # api + reference MCP servers + demo A2A + Valkey (SQLite)
make web-build                 # console bundle → web/dist (served by the local api)
open http://127.0.0.1:8800/console
make test                      # pytest in the local dev container
make local-stop
```

Seed accounts exist only when `APP_ENV=local|test`; the password is `<account>-pass`.

| Account | Role | First screen after login |
|---------|------|--------------------------|
| `admin` | platform admin (all permissions) | `/console` |
| `operator` | platform operator | `/console` |
| `security` | security / IAM | `/console` |
| `dev` | agent developer | `/console/agents` |
| `finops` | FinOps | `/console/observe/cost` |
| `user`, `user2` | end user | `/chat` |

prod mode (local rehearsal or the target host):

```bash
cp .env.example .env.prod      # only if missing — replace CHANGE_ME from your secret manager (make deploy refuses CHANGE_ME)
make prod-build                # build the runtime image (with console bundle) and start
make db-migrate ENV=prod && make db-ping ENV=prod
make prod-stop
```

In prod the password grant is disabled and the first admin is created once from `BOOTSTRAP_ADMIN_PASSWORD` (an existing admin is never changed).
If the signing and encryption keys are empty they are generated under `data/keys`; in production inject `SIGNING_KEY_PEM` and `ENCRYPTION_KEY`. Key rotation steps are in `SECURITY.md` ("키 회전 / Key rotation").

## Endpoints and ports

| Path | Description | Auth |
|------|-------------|------|
| `GET /health` | health including a real `SELECT 1` | none |
| `/console/*` · `/chat/*` · `/login` · `/setup` | web console SPA (`/_app/*` hashed assets, `/` → `/console`) | browser login |
| `/idp/.well-known/openid-configuration` · `/idp/authorize` · `/idp/token` · `/idp/device/code` · `/idp/jwks` | built-in OIDC IdP | — |
| `GET /idp/providers` · `/idp/authorize?idp_hint=` · `/idp/broker/{id}/start` · `/idp/broker/callback` | external IdP SSO broker (Entra, Okta, Keycloak → console, with per-cause error codes) | — |
| `POST /api/v1/identity/idps/discover` · `POST …/idps/{id}/test-login` · `GET …/test-login/{state}` | IdP discovery before saving and test login (SCR-J02) | `identity:write` |
| `POST /v1/chat/completions` · `GET /v1/models` | Unified Model API (OpenAI-compatible, SSE), header `X-AgentNexus-Data-Class` | Bearer |
| `POST /mcp/{server_id}` · `/mcp/_all` | MCP gateway (Streamable HTTP; negotiates 2025-11-25, 06-18, 03-26). Server-to-client requests (sampling, elicitation) are relayed on the SSE response stream; the client answers with a POST to the same path (202) | Bearer / RFC 9728 |
| `GET /.well-known/oauth-protected-resource/mcp/{id}` | MCP Protected Resource Metadata | none |
| `POST /a2a/{agent_id}` · `GET /a2a/{agent_id}/.well-known/agent-card.json` | A2A gateway (0.3 ↔ 1.0 translation) | Bearer |
| `/a2a/{agent_id}/rest/…` · `/a2a/platform[/{profile}]/rest/…` — `POST message:send` · `message:stream` (SSE) · `GET tasks[/{id}]` · `POST tasks/{id}:cancel` · `tasks/{id}:subscribe` · `tasks/{id}/pushNotificationConfigs[/{cid}]` · `GET extendedAgentCard` | A2A 1.0 HTTP+JSON (REST) binding — `application/a2a+json`; errors are `google.rpc.Status` + `ErrorInfo` (domain `a2a-protocol.org`); cards list an `HTTP+JSON` interface. REST-only remote agents are called through translation (D16-①) | Bearer |
| gRPC `127.0.0.1:8851` `lf.a2a.v1.A2AService/*` (`tenant` = remote agent id · `platform` · `platform/{profile}`) | A2A 1.0 gRPC binding — `authorization` metadata, error trailer `grpc-status-details-bin` (ErrorInfo), server streaming. With `A2A_GRPC_PUBLIC_URL` set, cards list a `GRPC` interface (with tenant). gRPC-only remote agents are supported (D16-②) | Bearer (metadata) |
| `GET /.well-known/agent-card.json` · `POST /a2a/platform[/{profile}]` | platform A2A server (published profiles) | Bearer |
| `GET /v0/servers` · `POST /v0/publish` | MCP Registry-compatible discovery and publishing | Bearer for publish |
| `POST /api/v1/approvals/{id}/consent` | consent card (SCR-C08): allow sampling (with request edits) and send after reviewing the response, or accept (schema-validated), decline or cancel an elicitation | Bearer (the requesting principal) |
| `GET /api/v1/chat/mcp/context` · `POST /api/v1/chat/mcp/resource` · `POST /api/v1/chat/mcp/prompt` | composer `/` menu: list and insert resources and prompts from the profile's MCP servers | Bearer |
| `POST /api/v1/registry/validate` | validate a server.json before publishing (SCR-E04, MSG-007) | `registry:publish` |
| `POST /v1/embeddings` | OpenAI-compatible embeddings with input guard, data-class routing and usage (`embed-local` by default) | Bearer |
| `/api/v1/knowledge-bases[/{id}[/documents\|/search\|/reindex]]` | knowledge bases (built-in vector index for RAG): manage with `agents:write`, search with `chat:use` (team-scoped) | Bearer |
| `POST /api/v1/chat/attachments` · `GET …/{id}` | upload attachments (pdf, docx, xlsx, images, text; `ATTACHMENT_MAX_MB`) and download them (owner only) | Bearer |
| `/api/v1/setup/{state,claim,code,progress,mfa,identity,models,registry,observability,templates,complete}` | first-run wizard (SCR-A05–A09): one-time setup code → setup-only token | public (state, claim) · setup token or admin |
| `POST /api/v1/admin/profiles/plan` · `POST …/profiles/apply` | deployment profile preview and apply, air-gap switch (SCR-L01, ST-09) | `admin:read` · `admin:write` |
| `GET /api/v1/system/banners` | global banners (CMP-16: air-gap, first run, audit chain) | Bearer |
| `GET /v1/catalog?q=jira tag:… auth:… trust:…&auth=&trust=&status=` · `GET /v1/catalog/exposures` | unified search (score order, match hints, per-type counts) · registration → exposure status (SCR-E01) | `catalog:read` |
| `GET /api/v1/registry/approvals?status=` · `POST /v1/catalog/{id}/approve` `{trust, tool_risks, allowlist_group}` · `…/reject` | registration review queue: checks (a failed check blocks approval, MSG-007), tool risk, trust tier, allowlist; auto-rejected after 72 hours (SCR-E05) | `catalog:approve` |
| `GET /v1/catalog/{id}/diff?from&to` · `PUT …/pin` · `POST …/rollback` · `GET …/usage` · `GET …/history` | version diff (re-approval), pin, rollback (confirm), usage, change history (SCR-E07) | `catalog:read` · changes `catalog:approve` |
| `POST /v1/catalog/{id}/instant-use/auth` · `POST …/instant-use/policy` `{dry_run}` · `GET …/exposure` | instant-use wizard ② test exchange (MSG-019) ③ policy defaults and Rego ④ 60-second exposure check (SCR-E03) | `catalog:write` · check `catalog:read` |
| `GET·PUT /api/v1/registry/draft` · `POST /api/v1/registry/fetch` | server.json draft (per user) and fetch from URL (SCR-E04) | `registry:publish` |
| `/api/v1/gateway/policies/drafts[/{id}[/test·/submit·/approve·/reject]]` · `/api/v1/gateway/policy-tests` · `POST /api/v1/gateway/policies/{id}/rollback` · `GET …/{id}/diff?from` · `POST …/policies/rego` | two-person policy review: draft → regression test → publish request (reason) → approval by someone other than the author · rollback · Rego preview (SCR-H07·H08). Direct `POST·PUT·DELETE /policies` is an admin break-glass path (audited as `break_glass`) | `policy:write` · read `gateway:read` |
| `POST /api/v1/mcp-servers/{id}/test-config` | test an MCP config before applying it: PRM/AS discovery, tools/list and scope checks with the candidate endpoint and auth; nothing is saved (SCR-F03) | `mcp:write` |
| `GET·PUT /api/v1/agents/{id}/a2a-card` · `POST …/a2a-card/publish` | platform Agent Card editing, validation and publish request, public after approval (SCR-D10) | `agents:write` |
| `POST /api/v1/a2a/agents` `{dry_run}` · `GET /api/v1/a2a/agents/{id}/checks` | remote agent pre-registration checks · developer registrations wait for approval (MSG-008) · Agent Card re-validation history (SCR-G02·G03) | `a2a:write` · `a2a:read` |
| `POST /api/v1/runs/{id}/input` | answer a remote agent's input-required; resumes on the same Task and contextId (SCR-C05) | Bearer (run owner) |
| `meta` on `GET /api/v1/runs/{id}` · `POST /api/v1/runs/{id}/continue` | response meta (tokens, cost, first token, per-node latency and cost, fallback, 429, sanitization, evidence used) · continue with partial results (SCR-C03·C06) | Bearer (run owner) |
| `POST /api/v1/sessions/{sid}/messages/{mid}/feedback·/regenerate·/edit` | 👍👎 · regenerate · edit and resend as a new branch (SCR-C03) | `chat:use` |
| `GET /api/v1/sessions?q=&profile_id=&days=&source=` · `GET …/{sid}/summary·/export` · `GET /api/v1/chat/files` · `GET /api/v1/chat/profiles` | history full-text search, filters, preview, .md export · attachments and Artifacts gallery · profile readiness (SCR-C01·C07). `DELETE /api/v1/sessions/{sid}` removes content and keeps metadata | `chat:use` |
| `POST /api/v1/chat/attachments/from-artifact` | remote Artifact → chat attachment (SCR-C05) | `chat:use` |
| `context` on `GET /api/v1/approvals/{id}` · `POST …/approve {remember}` | approval modal context (source, OBO identity, impact, policy evidence) · don't ask again this session (medium risk only) (SCR-C04) | Bearer |
| `GET /api/v1/agents?status=&team=&q=` · `POST /api/v1/agents/{id}/clone·/restore` · `GET …/health` | profile list filters, clone, restore, connection check (SCR-D01) | `agents:read` · `agents:write` |
| `POST /api/v1/agents/{id}/test {dry_run, version}` · `GET …/test-runs` · `GET …/checks` | dry run (zero writes), run a published version, compare previous runs, pre-publish checks (SCR-D08) | `agents:write` · `agents:read` |
| `GET /api/v1/agents/{id}/code?format=yaml\|adk` · `PUT …/graph {allow_invalid}` · `POST …/code/revert` · `GET …/diff` · `POST …/simulate` | YAML/ADK view · code-only save (MSG-001) · revert · diff against published · failure simulation (SCR-D06·D07) | `agents:read` · `agents:write` |
| `GET /api/v1/host/sessions` · `POST …/{sid}/reconnect` · `DELETE …/{sid}` | MCP Host session KPIs, events, reconnect, end (SCR-D09) | `observe:read` · `mcp:write` |
| `GET /api/v1/identity/users/{id}/detail` · `POST …/sessions/revoke {reason}` · `POST …/resync` · `GET /api/v1/me/sessions` · `POST /api/v1/me/sessions/revoke-others` | User detail (sessions, permissions, claims) · force sign-out (reason required, 401 from the next request) · IdP resync · my sessions, end other sessions (SCR-J03·B06) | `identity:read`·`identity:write` · Bearer |
| `GET·PUT /api/v1/identity/rbac {overrides, reason}` · `POST …/rbac/simulate` | Role/permission matrix (admin fixed, applies from the next token) · permission simulator (roles or IdP groups) (SCR-L03) | `identity:read` · `identity:write` |
| `POST /api/v1/identity/token-exchange/simulate` · `POST /api/v1/identity/mcp-auth/{id}/rediscover` | Token-exchange simulator (MSG-019) · rediscover PRM, AS and scopes (SCR-J04·J06) | `identity:read` |
| `GET·POST /api/v1/gateway/bypass-check` · `POST …/limits/preview` · `POST …/routing/test {fail}` · `POST …/security/auth-test` · `GET …/security/detections` | Bypass-prevention status (GET, not audited) and check-now (POST, audited) · 401/403/429/5xx counters · 429 preview (warn-only `warn`, `retry_after`) · fault-injection routing test · 401 test and detection events (SCR-H01·H02·H05·H09) | `gateway:read` |
| `GET /api/v1/gateway/governance/blocks` · `POST …/tools/args-test` · `POST …/sanitize-rules/preview` · `GET …/sanitize-events` | Recent blocks · argument-schema test (strict/warn) · sanitize preview and events (SCR-H10·H11) | `gateway:read` |
| `POST /api/v1/approvals/{id}/delegate {to, note}` | Delegate an approval — never to the requester (`delegate_forbidden`); the delegate decides (SCR-H12) | `approvals:decide` |
| `GET /api/v1/gateway/secrets/{id}/usage` · `POST …/leak-check` · `DELETE /api/v1/gateway/mcp/sessions/{sid}` · `GET /api/v1/gateway/a2a/stats` | Secret usage and rotation D-day · leak check (value never returned) · force-close MCP session · A2A call stats (SCR-H06·H14·H15) | `gateway:read` · `gateway:write` |
| `POST /api/v1/mcp-servers/{id}/restart` · `PUT …/sampling {ask\|deny}` · `GET …/logs` · `POST /api/v1/mcp-servers/openapi {operations}` · `GET …/templates/custom?include=` | MCP restart · sampling policy · logs · OpenAPI operation selection, rename, risk · template include options (SCR-F01·F02·F04·F05) | `mcp:write` · `mcp:read` |
| `GET /api/v1/observe/instrumentation` · `GET /api/v1/observe/a2a` · `GET /api/v1/traces?user=&tool=` | Instrumentation ratio · A2A metrics · trace user/tool filters (SCR-K01·K02·K05) | `observe:read` |
| `POST /api/v1/logs/unmask {ids, reason}` | Unmask logs — reason required, 30 minutes, secrets stay masked, audited `observe.log.unmask` (SCR-K04) | `audit:read` |
| `GET /api/v1/usage/export` (CSV) · `GET /api/v1/usage/drilldown?team=&user=` | Cost CSV · team → user → target drilldown (SCR-K06) | `cost:read` |
| `GET /api/v1/audit/verify?from_seq&to_seq` · `GET /api/v1/audit/reports/templates` · `POST …/reports/preview` · `GET …/reports/{id}/download?format=md\|csv\|jsonl` | Hash-chain range verify · regulatory report templates (PIPA access log, …), preview, download (SCR-K09·K10) | `audit:read` |
| `GET /api/v1/admin/settings/history` · `POST …/settings/revert` · `POST …/settings/pii-test` | Settings history and revert · PII rule test (SCR-L06). New setting keys: `guards.pii_rules`·`auth.access_token_ttl_min`·`auth.session_ttl_hours`·`auth.ip_allowlist`·`ui.timezone`·`secrets.rotation_days`·`notify.channels`·`backup.schedule` | `admin:read` · `admin:write` |
| `PATCH /api/v1/admin/connectors/{id}` · `GET /api/v1/admin/updates/plan` · `POST …/updates/sbom-baseline` | Edit connector · upgrade plan (migrations, SBOM diff, signature) (SCR-L02·L04) | `admin:write` · `admin:read` |
| `POST /api/v1/admin/restore/test` · `GET·POST /api/v1/admin/backups[/run]` · `GET …/backups/{name}` | Isolated restore test (temporary DB) · scheduled backups list, run now, download (SCR-L05). `POST /api/v1/admin/restore` requires `confirm: "RESTORE"` | `admin:write` |
| `GET /api/v1/admin/keys` · `POST …/keys/rotate` · `POST …/keys/reencrypt[?drop_previous=true]` | Key status (kid, JWKS kids, number of encryption keys) · rotation (`{kind: signing\|encryption, confirm: "ROTATE"}`; env-injected keys get 409 `keys_env_managed`) · re-encryption and removal of the previous encryption key (58th) | `admin:read` · `admin:write` + admin role |
| `GET·PUT /api/v1/notifications/preferences` · `GET /api/v1/notifications?category=` · `GET /api/v1/system/components` | Notification categories, mute, channels · component version, replicas, restart command (SCR-B04·B05) | Bearer · `admin:read` |
| `POST /api/v1/gateway/client-keys/{id}/revoke` · `sid` in `GET /api/v1/gateway/mcp/sessions` | Admin revoke of client keys · force-close MCP sessions by hashed handle (raw session id never listed) (SCR-H13·H14) | `gateway:write` |
| `POST /api/v1/gateway/virtual-servers {collision_policy}` | Virtual MCP server tool-name collisions — `prefix` (default, server.tool) · `reject` (409 `tool_collision`) (SCR-H03) | `gateway:write` |
| `POST /api/v1/mcp-servers/openapi/fetch {url}` · `GET …/templates/custom?include=none` | Gateway fetches an OpenAPI document by URL (no CORS, 2MB, JSON/YAML) · template without optional files (SCR-F04·F05). CLI `nexus mcp scaffold --include docker\|register\|readme\|none` | `mcp:write` |
| `PATCH /api/v1/a2a/agents/{id} {auth:{scheme:"oauth2", token_url, client_id, scopes}, secret}` · `transitions`·`events` in `GET /api/v1/a2a/tasks/{id}` | Remote agent OAuth2 client_credentials (gateway issues and caches, refresh 30s before expiry) · task state transitions and receive path (send·sse·push·poll) (SCR-G04·G06) | `a2a:write` · Bearer |
| `POST·PATCH /api/v1/providers {secret_ref, options.zdr, options.data_classes}` · `PATCH /api/v1/models/{id} {price_in, price_out}` | Provider uses an existing secret (400 if missing) · ZDR (OpenAI `store:false`) · provider-allowed data classes (narrows model policy) · price-only change by FinOps `cost:write` (SCR-I02·K06) | `models:write` · `cost:write` |
| `p95_latency_ms` in `GET /api/v1/observe/timeseries` · `issuer`·`idp`·`claims` in `GET /api/v1/me` · `unread_counts` in `GET /api/v1/notifications` | Per-bucket latency p95 · my IdP and group claims · unread per category (SCR-K05·B06·B04) | `observe:read` · Bearer |
| `PATCH /api/v1/identity/idps/{id} {enabled:false}` | Disabling an IdP ends its users' sessions (`sessions_ended`) · test login also works on a disabled draft (SCR-J01·J02) | `identity:write` |
| `POST /api/v1/gateway/routing/rules/{id}/publish {reason}` · `GET …/versions` · `POST …/rollback {version, reason}` | Publish a routing rule (reason required), version history, unpublished changes, rollback (recorded as a new version) (SCR-H02) | `gateway:write` · read `gateway:read` |
| `POST /api/v1/me/tokens {kind: client_key, allowed_models}` · `PATCH /api/v1/gateway/client-keys/{id}` | Allowed model aliases per client key — other aliases get 403 `model_not_allowed` (SCR-H13) | `gateway:read` · `gateway:write` |
| `POST /api/v1/mcp-servers/openapi {identity: "obo", scopes}` | User identity for hosted OpenAPI servers — each call injects an `api:{ns}` exchanged token as `Authorization` (SCR-F04) | `mcp:write` |
| `POST /api/v1/mcp-servers/{id}/inspect {test_principal: {roles, team}}` | Test-only virtual user — roles no wider than yours, write/risky tools are dry-run, audited `mcp.inspect.test_principal` (SCR-F06) | `mcp:read` |
| `POST /api/v1/a2a/agents/{id}/try {files}` · `PATCH /api/v1/a2a/agents/{id} {framework, auth.push_verify}` | Test-console file Parts · framework override · push JWT signature check (JWKS, ±5 min, body hash; failures get 401) (SCR-G01·G04·G07) | `a2a:read` · `a2a:write` |
| `POST /mcp/{id}` (MCP 2026-07-28, `_meta.io.modelcontextprotocol/protocolVersion`) · `server/discover` | Dual-era gateway — sessionless modern requests, header validation (-32020), version rejection (-32022), `resultType`, cache hints, MRTR (`InputRequiredResult`, `inputResponses`, AEAD `requestState`). `initialize` keeps the legacy 2025-11-25 path (D13-④) | Bearer (same as MCP) |
| `subscriptions/listen` · `Mcp-Param-*` · `_meta.progressToken` (MCP 2026-07-28) | List-change subscriptions (acknowledged with `notifications/subscriptions/acknowledged`, then `notifications/{tools,prompts,resources}/list_changed`, all tagged with the subscriptionId · graceful close after 900 s · 5 concurrent per principal) · `x-mcp-header` argument ↔ `Mcp-Param-*` validation (-32020) · per-request SSE progress relay (D14-②) · `resourceSubscriptions` (only URIs readable now are honoured → `notifications/resources/updated`) · `taskIds` (your own tasks → `notifications/tasks`) (D15-①) | Bearer (same as MCP) |
| `tools/call` + `_meta…clientCapabilities.extensions["io.modelcontextprotocol/tasks"]` · `tasks/get` · `tasks/update` · `tasks/cancel` | MCP Tasks extension (SEP-2663) — the gateway runs the task; calls that finish within 2 s return a normal result, otherwise `resultType:"task"` · DetailedTask (input_required, completed, failed, cancelled) · input via `inputResponses` · cooperative cancel · `Mcp-Name` = taskId · another principal's or expired task -32602 · extension not declared -32021 (D15-①) | Bearer (tasks are bound to the principal) |
| `PATCH /api/v1/mcp-servers/{id} {protocol: auto\|legacy\|2026-07-28}` | Upstream protocol era — `auto` discovers it with `server/discover` on tool refresh; modern upstreams are called without sessions, and their `InputRequiredResult` is answered through the same relay and consent path before retrying. The detected value is `upstream_protocol` (D14-②) | `mcp:write` |
| `GET /api/v1/observe/slos` · `PATCH /api/v1/observe/slos/{id} {target, threshold_ms, window_days, enabled, reason}` · `POST /api/v1/observe/slos/evaluate` | SLOs and error budgets — data-path (model, MCP, A2A) availability 99.95% and first response ≤ 2 s at 99% over a 30-day rolling window, budget remaining, 5m/30m/1h/6h burn rates, page (14.4×) / ticket (6×) alerts · edits need a reason and are audited (D15-④) | `observe:read` · `admin:write` |
| `POST /api/v1/approvals/{id}/extend` | Extend a consent or approval deadline by its base wait (60 s for consent, 30 min for tool approvals), up to 10 times, audited as `approval.extend` (D14-③, WCAG 2.2.1) | requester · anyone allowed to decide |
| `/api/v1/*` | admin REST (OpenAPI at `/openapi.json`, console `/console/api`) | Bearer |
| `GET /metrics` | Prometheus (multiprocess) | `METRICS_TOKEN` or observe:read |
| `GET /licenses/*` | third-party license copies bundled with the console (Pretendard OFL-1.1) — repository `LICENSE` (Apache-2.0), `NOTICE`, `THIRD_PARTY_LICENSES.md` | none |
| `POST /v1/traces` | built-in OTLP/HTTP receiver (single trace across containers) | `OTLP_INGEST_TOKEN` |

Host ports: api `8800` (8000 is taken by another project on this host), A2A gRPC `8851`, console dev server `5173` (profile `web`).

## Web console

A React 19 + TypeScript + Vite 7 SPA in `web/`. Fonts (Pretendard) are bundled locally, so it makes no external requests in an air-gapped network.
Routes: overview `/console` · chat `/chat` · agents `/console/agents/{id}/graph|code|test` · catalog `/console/catalog` ·
MCP `/console/mcp-servers/{id}[/inspect]` · A2A `/console/a2a[/tasks[/{id}]]` · gateway `/console/gateway[/policies|limits|approvals|routing/models|tools|log|security|secrets|governance[/rules]|model-api|mcp|a2a]` ·
models `/console/models[/new|playground|tokens|providers]` · identity `/console/identity/users|propagation|idp[/new]|token-exchange|mcp-auth|workloads` ·
registry `/console/registry/publish|sources|approvals` · SBOM `/console/catalog/sbom` · new MCP servers `/console/mcp-servers/new/openapi|custom` and `{id}/config` ·
observability `/console/observe/pipeline|traces|logs|metrics|cost|budgets|replay/{sid}` · audit `/console/audit[/reports/new]` · operations `/console/admin[/rbac]` · chat history `/chat/history` · first run `/setup`. Menus you lack permission for are shown disabled with the reason (ST-04). ⌘K global search.

95 of the 100 designed screens are implemented in the web console (B04 notifications is the top-bar drawer; C05 delegation and C08 consent are cards inside
the chat). The install screens A01–A04 and A11 are terminal flows, as designed: `nexus install --check`, `docker compose`, `helm install`,
`nexus bundle import` and `nexus login --device` (PRD D11). Accessibility targets KWCAG 2.2 + WCAG 2.2 AA: axe reports 0 violations on 60 console screens plus chat and login, the first Tab reaches "본문 바로가기" (skip to content), and every screen sets its own document title (`web/e2e/a11y.spec.ts`); the dark theme also scans clean (`a11y-dark.spec.ts`). Consent cards, the approval modal and the approval queue offer "시간 연장" (extend time): 60 s or 30 min per press, up to 10 times, with a warning 20 s before expiry (WCAG 2.2.1). Observe → "SLO·오류 예산" shows SLI, budget remaining, burn rate per window and daily bad requests, and lets admins edit targets with a reason. The A2A test console and orchestrator delegation default to A2A 1.0 (0.3 still selectable, D15-②). Typing `/` in the chat composer opens the resource and prompt menu, and a consent card appears when
an MCP server asks for sampling or elicitation during a run (auto-denied after 60 s without an answer).

```bash
COMPOSE_PROFILES=demo,web make local-all   # HMR dev server → http://127.0.0.1:5173/console
make web-test                              # Vitest (27)
make web-e2e                               # Playwright browser scenarios (77, including axe accessibility in light and dark themes) — run make web-build first
make web-lock                              # refresh pnpm-lock.yaml after changing web/package.json
```

## CLI `nexus`

`python -m app.cli` (in containers: `nexus`; in the repo: `bin/nexus`). Config in `~/.nexus/config.json` (0600), server `NEXUS_URL`, token `NEXUS_TOKEN`.

```bash
make shell                                  # inside the api container
nexus login --device --url http://127.0.0.1:8000   # RFC 8628 — approve the code at /idp/device in a browser
nexus whoami
nexus catalog list --type mcp_server
nexus mcp scaffold weather --dir ./weather  # custom MCP server template (FastMCP + OAuth + OTel + server.json)
nexus mcp scaffold weather --include docker --include readme   # only these optional files (none = no optional files, SCR-F05)
nexus registry publish ./weather/server.json   # → waits for operator approval (SCR-E05)
nexus mcp inspect jira transition_issue --args '{"key":"ORI-1440","to":"Done"}'   # high risk → approval_id
nexus approvals approve apr_… && nexus mcp inspect jira transition_issue --args '…' --approval apr_…
nexus chat tpl-qa "Show me the incident runbook"   # streams run events
nexus usage --group-by team · nexus audit-verify · nexus install --check [--mode helm]
nexus install compose [--profile full] [--cosign-key cosign.pub]   # install.yaml → .env and secrets → signature check → start → health → setup code (SCR-A02)
nexus bundle export --out agentnexus-airgap --include dist/agentnexus-0.1.0.tgz   # docker save + signed backup + SHA-256
nexus bundle import agentnexus-airgap --cosign-key ops.pub [--dry-run]   # signature check by default — key from outside the bundle (59th); --allow-unsigned for PoCs only
nexus keys status · nexus keys rotate --kind signing|encryption --yes · nexus keys reencrypt --drop-previous   # key rotation (admins, SECURITY.md, 58th)
```

## Make targets

| Target | Description |
|--------|-------------|
| `make <mode>-all` / `-build` / `-logs [SVC=]` / `-stop` / `-restart [SVC=]` / `-ps` | stack lifecycle (`mode` = local \| prod) |
| `make test` | pytest in the local dev container (SQLite) |
| `make test-pg` | PostgreSQL regression (recreates `app_test` in the prod `db` container) |
| `make web-build` / `web-test` / `web-e2e` / `web-lock` / `e2e-lock` | console build · Vitest · Playwright · console and e2e lockfile refresh |
| `make lint` / `audit` | ruff (F, E9) plus key module imports / dependency vulnerability audit (pip-audit, pnpm audit — needs network; CI `audit` job) |
| `make licenses` | regenerate `THIRD_PARTY_LICENSES.md` (prod image Python runtime + console prod JS + fonts, offline) |
| `make helm-check` | Helm lint (--strict) plus offline consistency checks of 4 rendered scenarios |
| `make helm-package` | package the chart → `dist/agentnexus-<ver>.tgz` |
| `make shell [ENV=]` | shell into the app container |
| `make db-ping` / `db-shell ENV=prod` / `db-migrate` / `db-seed` `[ENV=]` | DB check, client, migrations (Alembic), seed |
| `make db-reset` / `db-fresh` | reset local SQLite (+ seed); refused for prod |
| `make deploy` | prod rollout (refused while CHANGE_ME remains) |
| `make preflight` / `help` | checks / target list |

## Environment

New keys go into `.env.local`, `.env.prod` (CHANGE_ME) and `.env.example` together.

| Variable | local | prod |
|----------|-------|------|
| `APP_ENV` / `DB_ENGINE` | `local` / `sqlite` | `prod` / `postgres` |
| `DATABASE_URL` | SQLite file | connection string to `db` (CHANGE_ME → injected) |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | — | RDBMS account |
| `API_PORT` / `BIND_HOST` | `8800` / `127.0.0.1` | per host |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8800` | public domain — IdP issuer `<value>/idp`, basis for redirect_uri checks |
| `BOOTSTRAP_ADMIN_PASSWORD` | (empty — seed accounts) | first `admin` password |
| `SIGNING_KEY_PEM_NEXT` / `ENCRYPTION_KEY_NEXT` / `SIGNING_KEY_PREVIOUS_UNTIL` / `SIGNING_REVOKED_KIDS` | (empty) | optional — two-phase env rotation: next keys (verify / decrypt only, deployed first) · end of token verification for previous signing keys (ISO 8601) · revoked kids (comma-separated, never verified) (62nd, SECURITY.md) |
| `DB_STATEMENT_TIMEOUT_MS` / `DB_LOCK_TIMEOUT_MS` / `DB_IDLE_TX_TIMEOUT_MS` | `120000` / `15000` / `0` (unused on SQLite) | same — PostgreSQL session limits (0 = off). Idle-in-transaction is off by default because streaming requests may keep their session open; if you enable it, make it longer than the longest stream. Migrations run without limits (61st) |
| `MODEL_MAX_INFLIGHT_PER_SUBJECT` | `32` | same — concurrent model requests per principal (429 beyond, 0 = off), so one principal cannot lock a team budget with many large `max_tokens` requests (63rd) |
| `SIGNING_KEY_PEM` / `ENCRYPTION_KEY` | (empty — generated in `data/keys`) | RS256 PEM / Fernet key |
| `SIGNING_KEY_PEM_PREVIOUS` / `ENCRYPTION_KEY_PREVIOUS` | (empty) | optional — previous keys right after a rotation when keys are injected via env (verify / decrypt only). Several signing PEMs can be concatenated; encryption keys are comma-separated (58th) |
| `ACCESS_TOKEN_TTL` | `3600` | `3600` |
| `UVICORN_WORKERS` | `1` | `2` — uvicorn workers in the runtime image (SCR-B05 replicas); the demo servers (mcp-servers, a2a-demo) are pinned to `1` in compose and Helm |
| `ANX_TARGET_VERSION` / `COSIGN_PUBLIC_KEY` | (empty) | Optional — upgrade-plan target version / image-signature public key (SCR-L04) |
| `RATE_LIMIT_BACKEND` / `VALKEY_URL` | `redis` / `redis://valkey:6379/0` | same |
| `POLICY_ENGINE` / `OPA_URL` | `builtin` / `http://opa:8181` | `builtin` or `opa` |
| `MCP_REFRESH_ON_START_S` / `MCP_HEALTH_INTERVAL_S` | `90` / `60` | same |
| `MCP_STDIO_ALLOWED_COMMANDS` | (empty = stdio disabled) | comma-separated allowlist — an entry without spaces is an executable name (e.g. `uvx`, any arguments); an entry with spaces must match the whole command line (e.g. `npx -y @mcp/server-fs@0.6.2 /data`) (D19-②) |
| `MCP_CONSENT_TIMEOUT_S` | `60` | `60`: seconds before an unanswered consent card is auto-denied |
| `SCHEDULER_INTERVAL_S` / `RETENTION_INTERVAL_S` | `60` / `3600` | same: alert evaluation and run recovery period / retention purge check period (0 = off; one worker holds each lease) |
| `RUN_STALE_S` | `90` | `90`: seconds without a heartbeat before another worker takes over a run |
| `A2A_REMOTE_WAIT_S` / `ATTACHMENT_MAX_MB` | `120` / `20` | same: seconds to follow a working remote Task / maximum attachment size |
| `A2A_WATCH_INTERVAL_S` | `5` | `5` |
| `A2A_GRPC_PORT` / `A2A_GRPC_PUBLIC_URL` | `8851` / `127.0.0.1:8851` | host port / gRPC address advertised in cards (empty = no GRPC interface; Helm uses `a2aGrpc.publicUrl`) |
| `A2A_GRPC_TLS_CERT` / `A2A_GRPC_TLS_KEY` | (empty — plaintext) | PEM paths — with both set, gRPC is received over TLS (Helm `a2aGrpc.tls.secretName` fills them) |
| `A2A_DEMO_CLIENT_SECRET` | (empty — shared-volume file) | CHANGE_ME/empty = file mode · Helm injects a Secret |
| `OTLP_ENDPOINT` | (empty) | external OTel Collector (optional, alongside the built-in store) |
| `OTLP_INGEST_TOKEN` / `METRICS_TOKEN` | (empty — generated / user tokens) | receiver and scrape tokens |
| `SPAN_PROCESSOR` / `LOG_LEVEL` | `batch` / `INFO` | same |
| `COMPOSE_PROFILES` | `demo` | `demo` — disable with `COMPOSE_PROFILES=`, add with `demo,opa` or `demo,web` |
| `WEB_PORT` | `5173` | (unused — api serves the bundle) |
| `AGENTNEXUS_COMPONENTS` (shell) | `all` | independent runs such as `gateway,identity` |
| `MCP_SERVERS_URL` · `A2A_DEMO_URL` · `IDP_INTERNAL_URL` · `GATEWAY_URL` | fixed in compose `environment` | same |

> Never commit `.env.prod`. `.env.local` holds no secrets and is committed.

## API examples (real local responses)

```bash
$ curl -s http://127.0.0.1:8800/health
{"status":"ok","env":"local","db":{"engine":"sqlite","status":"ok"}}

$ TOKEN=$(curl -s -X POST http://127.0.0.1:8800/idp/token -d grant_type=password -d username=user \
    -d password=user-pass -d client_id=demo | jq -r .access_token)      # password grant: local/test only

# internal data sent to a commercial model is rerouted to the local model (FR-G03, L02)
$ curl -s -D- -H "Authorization: Bearer $TOKEN" -H 'X-AgentNexus-Data-Class: internal' -H 'Content-Type: application/json' \
    -d '{"model":"fast-commercial","messages":[{"role":"user","content":"게이트웨이 한 줄 소개"}]}' \
    http://127.0.0.1:8800/v1/chat/completions
x-agentnexus-model: domain-local
x-agentnexus-fallback: true
x-agentnexus-trace-id: 77ebc4b66850607bb6dc4ea3178c9a0b
{"model":"domain-local","choices":[{"message":{"role":"assistant","content":"[domain-7b] 게이트웨이 한 줄 소개"}}],
 "usage":{"prompt_tokens":3,"completion_tokens":6,"total_tokens":9}, …}

# MCP tool call — the gateway exchanges the user's identity for a least-privilege per-tool token (dev token)
$ curl -s -H "Authorization: Bearer $DEV" -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_issues","arguments":{"query":"결제","limit":2}}}' \
    http://127.0.0.1:8800/mcp/jira | jq '.result.structuredContent.result[0]'
{"key":"ORI-1440","summary":"결제 샌드박스 연동 차단","status":"In Progress","priority":"Highest","assignee":"윤도현","updated":"2026-09-29"}

$ curl -s http://127.0.0.1:8800/.well-known/agent-card.json | jq '{name, protocolVersion, url}'
{"name":"AgentNexus","protocolVersion":"0.3.0","url":"http://127.0.0.1:8800/a2a/platform"}
```

The openai SDK works unchanged with `base_url="http://127.0.0.1:8800/v1"` and `api_key=<PAT or client key anx_key_…>`.

```bash
# Composer / menu: MCP resources and prompts of the demo-orders profile (user token, response trimmed)
$ curl -s -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8800/api/v1/chat/mcp/context?profile_id=demo-orders' \
    | jq -c '{servers, resources: [.resources[].uri], prompts: [.prompts[].name]}'
{"servers":["internal-orders"],"resources":["orders://daily/2026-09-30","orders://policy/returns"],"prompts":["anomaly_triage"]}

# Validate a server.json before publishing (dev token; the error text is Korean in this build)
$ curl -s -H "Authorization: Bearer $DEV" -H 'Content-Type: application/json' \
    -d '{"name":"io.example/weather","version":"0.1.0","description":"날씨"}' http://127.0.0.1:8800/api/v1/registry/validate
{"valid":false,"errors":["(root): remotes 또는 packages 가 1개 이상 필요합니다"],"name":"io.example/weather","version":"0.1.0","auth":null,"tools":[],"remotes":[]}

# Permission simulator — IdP group → role → permission (security token)
$ curl -s -H "Authorization: Bearer $SEC" -H 'Content-Type: application/json' \
    -d '{"groups":["anx-developers"],"perm":"agents:write"}' http://127.0.0.1:8800/api/v1/identity/rbac/simulate | jq -c '{roles, via, allowed}'
{"roles":["developer"],"via":[{"group":"anx-developers","idp":"데모 파트너 IdP","role":"developer"}],"allowed":true}

# Bypass-prevention status (operator token, not audited — "check now" is the POST). An unauthenticated registered server in the local demo fails mcp_identity
$ curl -s -H "Authorization: Bearer $OPS" http://127.0.0.1:8800/api/v1/gateway/bypass-check \
    | jq -c '{ok, items: [.items[] | {key, ok}][0:3], counts, http_since_start}'
{"ok":false,"items":[{"key":"provider_keys","ok":true},{"key":"mcp_via_gateway","ok":true},{"key":"mcp_identity","ok":false}],"counts":{"blocked_24h":33,"rate_limited_1h":0,"errors_1h":3,"denied_1h":0},"http_since_start":{"401":0,"403":0,"429":0,"5xx":0}}
```

## MCP sampling and elicitation (SCR-C08) · upstream mTLS

- The gateway declares the `sampling` and `elicitation` capabilities in its upstream initialize and accepts server requests (`sampling/createMessage`, `elicitation/create`, `roots/list`, `ping`) during tool calls.
- If the downstream client declared the same capability at initialize and called with `Accept: text/event-stream`, the request is **relayed as is**, so the official MCP SDK client works unchanged. With several workers, the answer meets its request through the DB (`mcp_relays`).
- Otherwise the Host shows a **consent card**. For sampling, once the user edits and allows the request, the gateway runs it through the Unified Model API (model `auto`, with data-class routing, so internal data goes to `domain-local`) and sends the response after the user reviews it. For elicitation, the form is drawn from the server's schema and accepted values are validated. Unanswered cards are auto-denied after `MCP_CONSENT_TIMEOUT_S`, and every step is audited (`mcp.consent.*`, `mcp.sampling`, `mcp.relay`).
- When an upstream speaks MCP 2026-07-28 (per-server `protocol`; the default `auto` discovers it with `server/discover` on tool refresh), the gateway calls it without sessions, sending `_meta` and headers on every request and mirroring `x-mcp-header` schema annotations into `Mcp-Param-*`. An upstream `InputRequiredResult` is answered through the relay and consent path above, then retried with `inputResponses` and `requestState`. Consent card deadlines can be extended with "시간 연장".
- Upstream mTLS: CA and client certificate per MCP server (`auth.tls`), remote A2A agent (`auth.tls`) and LLM provider (`options.tls`). Inbound mTLS belongs to the TLS terminator (Ingress or whatever sits in front of the gateway).

```bash
# Upstream mTLS (FR-G02): certificate and key PEMs live in the secrets store (kind=certificate); targets only hold names
# console: H06 secrets · F03 server config · G04 remote agent auth · I03 model registration · H05 security
POST /api/v1/gateway/secrets       {"name":"jira-ca","kind":"certificate","value":"-----BEGIN CERTIFICATE-----…"}
PATCH /api/v1/mcp-servers/jira     {"auth":{…,"tls":{"ca_secret":"jira-ca","client_cert_secret":"jira-client","verify":true}}}
PUT /api/v1/gateway/security       {"mtls":{"upstream":"required"}}   # disabled · optional (default) · required (fail closed, 403 mtls_required)
```

## Kubernetes (Helm) and air-gapped installs

`deploy/helm/agentnexus` contains: the api (an initContainer runs migrations and seed; a PostgreSQL advisory lock serializes concurrent pods),
the demo reference servers, built-in PostgreSQL, Valkey and OPA (optional), NetworkPolicies (backend only accepts traffic from api), HPA and PDB,
an Ingress or a Gateway API HTTPRoute (kgateway), a ServiceMonitor, and `helm test`. Signing and encryption keys are generated at install time
and kept in a Secret (`lookup` + `resource-policy: keep`). Invalid combinations (several replicas on an RWO volume, no external DB, and so on)
are rejected at render time.
Round-2 review fixes (51st): with `demo.enabled=false` the demo rows (reference MCP, demo A2A, workload, KB) are not seeded either (`SEED_DEMO`); narrowing `networkPolicy.ingressFrom` still lets same-release internal callers (api, mcp-servers, a2a-demo, a2a-grpc) reach api; `api.forwardedAllowIps` (default `*`) — narrow it to the Ingress controller pod CIDR in production (if left wide, other pods can spoof `X-Forwarded-For` and fool the IP allowlist and audit IPs).
Round 11 (54th): unless a2a-grpc receives TLS it uses native gRPC probes (`grpc.health.v1`, readiness includes the api), with TLS it uses TCP; `a2aGrpc.topologySpreadConstraints`; a PDB for a2a-grpc when it has 2+ replicas; api and demo pods carry `checksum/secret` so a Secret change restarts them.

```bash
make helm-check                                    # lint + scenarios (default, air-gap, HA, gateway-only)
helm install anx deploy/helm/agentnexus -n agentnexus --create-namespace \
  --set publicBaseUrl=https://agentnexus.example.com --set ingress.enabled=true --set ingress.host=agentnexus.example.com
helm test anx -n agentnexus
# air gap: private registry with demo off — see deploy/helm/agentnexus/ci/airgap-values.yaml
make prod-build && make helm-package
nexus bundle export --out agentnexus-airgap --include dist/agentnexus-0.1.0.tgz   # docker save + chart + signed backup
```

Real install check (31st): install with default values on a dedicated kind cluster (its own kubeconfig) → five pods Ready, PVCs Bound, `helm test` →
smoke test of login, MCP tool, model call and audit chain → uninstall and delete the cluster — `KC=<dedicated kubeconfig> sh test/impl/31st/helm-kind.sh`.
On Docker Desktop with the containerd image store, use `docker save --platform` for the node's platform and `ctr images import` on the node instead of `kind load docker-image`.

gRPC binding (41st): `a2aGrpc.enabled=true` adds a Deployment, a Service (`appProtocol: grpc`) and NetworkPolicy wiring (a2a-grpc is added to the api ingress automatically). `a2aGrpc.ingress` uses ingress-nginx `backend-protocol: GRPC` (`GRPCS` with pod TLS), `a2aGrpc.tls.secretName` mounts a kubernetes.io/tls Secret for direct pod TLS, and `a2aGrpc.publicUrl` is the address advertised in cards (for example `grpcs://grpc.example.com:443`) — `KC=<dedicated kubeconfig> SCRATCH=<scratch> sh test/impl/41st/cluster-grpc.sh`.

Production-style check (34th): multi-node kind (1 control plane + 2 workers) with Calico (enforced NetworkPolicy), ingress-nginx and metrics-server;
install with ingress, HPA (2–4), PDB and `networkPolicy.ingressFrom` (ingress-nginx and release pods) → unlabelled pods cannot reach the backends or the api · login and tool calls through ingress ·
a second eviction is refused (PDB) · HPA scales out under load → delete — `KC=<dedicated kubeconfig> sh test/impl/34th/cluster-kind.sh`. With 2+ replicas use `persistence.accessMode=ReadWriteMany` (or `existingClaim`; on kind, `persistence.enabled=false`).

## Tests

A single `test/` tree: `test/dev-env/` (environment check) and `test/impl/<Nth>/` (one directory per Epic with `scenario.md`, `result.md`, `logs/`).

| Round | Epic | Cases | Attempts | Verdict |
|-------|------|-------|----------|---------|
| dev-env | standard environment | 8/8 | 1 | PASS |
| dev-env (re-check 2026-10-07) | current setup (Valkey, 8800, a2a-grpc, 0019) | 9/9 | 1 | PASS |
| 1st | foundation (entities, Alembic, audit hash chain, OpenAPI contract) | 9/9 | 2 | PASS |
| 2nd | identity & authorization | 16/16 | 2 | PASS |
| 3rd | catalog and registry | 9/9 | 2 | PASS |
| 4th | gateway policy core | 16/16 | 2 | PASS |
| 5th | Unified Model API | 18/18 | 2 | PASS |
| 6th | MCP path | 16/16 | 4 | PASS |
| 7th | A2A path (0.3 + 1.0) | 17/17 | 4 | PASS |
| 8th | host and orchestration | 17/17 | 4 | PASS |
| 9th | observability and audit | 14/14 | 4 | PASS |
| 10th | independent runs, connectors, operations | 11/11 | 3 | PASS |
| 11th | CLI `nexus` | 14/14 | 4 | PASS |
| 12th | web console (Vitest 23 · Playwright 8 on the prod bundle and the dev server) | 17/17 | 5 | PASS |
| 13th | Helm, air gap, NFR-P1 + completion gate (prod regression) | 21/21 | 3 | PASS |
| 14th | MCP sampling/elicitation relay and consent (SCR-C08) | 23/23 | 2 | PASS |
| 15th | upstream mTLS (FR-G02), with real TLS handshakes | 11/11 | 2 | PASS |
| 16th | the remaining 26 P0 console screens + second completion gate | 10/10 · gate 13/13 | 2 · gate 3 | PASS |
| 17th | security and consistency defects (tool limits, token scopes, A2A guards, trace PII, air-gap local models) | 25/25 | 3 | PASS |
| 18th | orchestration reliability (contextId, input-required resume, replanning, conditions, run recovery, quality routing, attachments, built-in vector RAG) | 31/31 | 3 | PASS |
| 19th | external IdP SSO login (broker, PKCE, groups → roles) | 22/22 | 3 | PASS |
| 20th | first run and install (5-step wizard, setup code, CLI, air-gap, re-login) | 30/30 | 4 | PASS |
| 21st | register → review → use (instant use, trust tiers, two-person policy review, rollback, regression gate) | 37/37 | 3 | PASS |
| 22nd | chat and orchestration editor depth (response meta, approval context, partial results, dry run, code-only save) | 40/40 | 3 | PASS |
| 23rd | operations and observability screen depth (B·F·G·H·I·J·K·L — session revoke, RBAC, PII, unmask, restore test, reports) + third completion gate | 70/70 | 3 | PASS |
| 24th | remaining design elements (routing-rule publishing, per-key models, hosted OBO, virtual test user, component health, file Parts, push signatures) | 26/26 | 2 | PASS |
| 25th | NFR measurement (S1 1,000 sessions · A1 four fault types — Valkey outage fallback added) | 13/13 | 3 | PASS |
| 26th | accessibility KWCAG 2.2 + WCAG 2.2 AA (axe violations 84 → 0) | 3/3 | 6 | PASS |
| 27th | MCP 2026-07-28 dual-era (server/discover, header validation, MRTR) + fourth completion gate | 17/17 | 1 | PASS |
| 28th | dark-theme accessibility (explicit and system dark, axe violations 67 → 0) | 4/4 | 4 | PASS |
| 29th | MCP 2026-07-28 extensions (modern upstream calls, listen, Mcp-Param, progress relay) | 34/34 | 2 | PASS |
| 30th | consent and approval time extension (WCAG 2.2.1, 10-extension cap) | 16/16 | 1 | PASS |
| 31st | real Helm install (dedicated kind: install → smoke → delete) + fifth completion gate | 18/18 | 4 | PASS |
| 32nd | MCP resource subscriptions and Tasks extension (SEP-2663) | 18/18 | 4 | PASS |
| 33rd | A2A 1.0 by default (0.3 still accepted) | 12/12 | 2 | PASS |
| 34th | production-style cluster (multi-node kind, Calico, ingress-nginx, HPA, PDB) | 21/21 | 3 | PASS |
| 35th | SLOs and error budgets + sixth completion gate | 14/14 | 2 | PASS |
| 36th | A2A HTTP+JSON (REST) binding | 15/15 | 2 | PASS |
| 37th | A2A gRPC binding (compose `a2a-grpc`) | 13/13 | 3 | PASS |
| 38th | Production-grade cluster (cert-manager TLS, external PostgreSQL, NFS RWX, node failure) | 27/27 | 3 | PASS |
| 39th | Quality review fixes (security, defect, in-depth and Lv2 line reviews) + seventh completion gate | 36/36 | 5 | PASS |
| 40th | Authentication boundary (workload-authenticated token exchange, server-side caller headers) | 13/13 | 2 | PASS |
| 41st | a2a-grpc Helm and TLS (kind: Ingress GRPC/GRPCS, pod TLS, NetworkPolicy) | 22/22 | 3 | PASS |
| 42nd | Supply chain, licensing (Apache-2.0) and CI | 8/8 | 1 | PASS |
| 43rd | Policy hardening (readOnlyHint, push URLs, scrypt, refresh reuse) + eighth completion gate | 12/12 | 2 | PASS |
| 44th | A2A Task ownership (platform and gateway-proxied — fixes cross-user exposure) | 11/11 | 2 | PASS |
| 45th | Multi-worker consistency (global listen cap, shared progress relay, leases) | 10/10 | 2 | PASS |
| 46th | Production HA (zone spreading, CloudNativePG failover) | 15/15 | 3 | PASS |
| 47th | Performance cleanup (SQL aggregation, shared summaries and fingerprints) + ninth completion gate | 8/8 | 3 | PASS |
| 48th | Object-level authorization sweep (66 user routes classified, 29 cross-user requests rejected) | 5/5 | 2 | PASS |
| 49th | Remaining Lv2 hardening (gRPC limits, grpcs private CA, full-command stdio allowlist, session TTL, push retry) | 6/6 | 2 | PASS |
| 50th | CI and reproducibility (actions pinned to SHAs, dev-env re-verified, time-dependent test races removed) | 5/5 | 1 | PASS |
| 51st | Round-2 quality review (delegated security and defect reviews + Lv2 line review → confirmed defects fixed) + tenth completion gate | 17/17 | 16 | PASS |
| 52nd | Model gateway correctness (rule timeout_ms, in-flight reservations, `any` and client-key budgets) | 4/4 | 2 | PASS |
| 53rd | Identity and registry boundaries (audience allowlist, admin grants, IdP-delete sign-out, publish owner, mirror hold, push_verify, TOTP) | 7/7 | 3 | PASS |
| 54th | Deployment hardening (gRPC health, PDB, Secret rollouts, minimal env, digests, lockfiles, signed bundles by default) | 5/5 | 1 | PASS |
| 55th | Observability and counter performance (overview and usage SQL aggregation, listen holder sets, per-word tool risk) + eleventh completion gate | 4/4 | 2 | PASS |
| 56th | Remote A2A and push boundaries (push JWT requires `iss`, `jti` and body hash; replayed jti rejected; 8 MiB remote response cap; non-JSON 502; stream error event; history 200; card URL) | 5/5 | 2 | PASS |
| 57th | Single-component deployments (scheduled jobs per component — the scheduler starts in any combination) | 6/6 | 2 | PASS |
| 58th | Key rotation (admin command — previous signing key kept for a grace period, public-key archive; encryption key re-encryption and removal of the old key; other workers pick it up at once) | 5/5 | 5 | PASS |
| 59th | Third quality review (delegated security and defect reviews + Lv2 line review → 17 confirmed defects fixed, `.review/round3/VERIFY.md`) + twelfth completion gate | 17/17 | 2 | PASS |
| 60th | Remaining recorded items (trust vocabulary, `/v0/servers` SQL paging, mTLS required + verify:false, token cache, OpenAPI namespace, CLI 0600, OTLP caps, A2A usage status, push token encryption, executor error text) | 10/10 | 2 | PASS |
| 61st | Data layer robustness (PostgreSQL 2 min statement and 15 s lock limits, no limits for migrations, SQLite busy 30 s, KPI indexes 0021, ordered sample) | 5/5 | 1 | PASS |
| 62nd | Key management hardening (generation counter, rotation lock, two-phase env `*_NEXT`, public-key previous keys, verification end, revoked kids, rotation audited first, Helm rotationNonce) | 6/6 | 1 | PASS |
| 63rd | Limit reservation precision (per-minute slot returns, output-token reservation, per-principal in-flight cap, Valkey clock, 4 reservation leaks, budget status buckets) | 6/6 | 1 | PASS |
| 64th | Boundary hardening 4 (Access Token scopes ⊆ exchange policy, push JWKS refresh and https, artifacts cap, OpenAPI secret, dry-run data class, anonymous discovery API, ownerless items, shared readiness) | 8/8 | 2 | PASS |
| 65th | Fourth quality review (delegated security and defect reviews + Lv2 line review → 21 confirmed defects fixed, `.review/round4/VERIFY.md`) + thirteenth completion gate | 17/17 | 3 | PASS |
| 66th | Remaining recorded items 2 (probe address, non-blocking listen release, built-in issuer and session scope for IdPs, TLS overlay, relaxed readiness, CLI 0700, restore off by default, varint) | 7/7 | 2 | PASS |

Final regression (thirteenth completion gate, 65th): pytest SQLite 467 passed + 2 skipped · PostgreSQL 468 passed + 1 skipped · Vitest 27 · Playwright 77 · helm-check 4 scenarios · prod rehearsal (0021) 65th 5/5 (session limits, indexes, concurrent rotation on two workers, Valkey clock, no leftover holds, target token scopes, prod https JWKS, anonymous limit, built-in issuer) + regressions 59th 7/7 · 55th 5/5 · 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th 7/7 · 45th 5/5 · 47th 2/2 (k6 excluded) · 51st 5/5 (PostgreSQL · 2 workers · Valkey). NFR-P1 re-measurement skipped (user decision).

Final regression (twelfth completion gate, 59th): pytest SQLite 419 passed + 1 skipped (performance included) · PostgreSQL 420 passed · Vitest 27 · Playwright 77 (E30-01 timed out once in r1, passed alone and in a full rerun) · helm-check 4 scenarios · prod rehearsal (0020) 59th 7/7 (card URL, push issuer, scheduler leases, signing key rotation across two workers, encryption key rotation, remaining items, fixed-expiry holds on Valkey) + regressions 55th 5/5 · 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th 7/7 · 45th 5/5 · 47th 2/2 (k6 excluded) · 51st 5/5 (PostgreSQL · 2 workers · Valkey). NFR-P1 re-measurement skipped (user decision).

Final regression (eleventh completion gate, 55th): pytest SQLite 376 passed + 1 skipped (performance included) · PostgreSQL 377 passed · Vitest 27 · Playwright 77 · helm-check 4 scenarios · prod rehearsal (0020) 55th 5/5 + regression 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th 7/7 · 45th 5/5 · 47th 2/2 (k6 excluded) · 51st 5/5 (PostgreSQL · 2 workers · Valkey). NFR-P1 re-measurement skipped (user decision).

Final regression (tenth completion gate, 51st): pytest SQLite 356 passed + 1 skipped (performance included) · PostgreSQL 357 passed · Vitest 27 · Playwright 77 · helm-check 4 scenarios · prod rehearsal 51st 5/5 + regression 36th 10/10 · 37th 9/9 · 39th 10/10 · 40th 8/8 · 42nd 4/4 · 43rd 8/8 · 44th 7/7 · 45th 5/5 (one lease hand-over timing failure right after a rebuild, passed on re-run) · 47th 2/2 (k6 excluded) (PostgreSQL · 2 workers · Valkey). dev-env re-check 9/9 (2026-10-07). The NFR-P1 k6 re-measurement was skipped this round (user decision).

Final regression (ninth completion gate, 47th): pytest SQLite 323 passed + 1 skipped (2 performance tests under host load) · PostgreSQL 326 passed · Vitest 27 · Playwright 77 · helm-check 4 scenarios · prod rehearsal 44th 7/7 + 45th 5/5 + 47th 4/4 + regression 40th 8/8 · 39th 10/10 · 36th 10/10 · 37th 9/9 · 43rd 8/8 (copy adjusted for the ownership check order) (PostgreSQL · 2 workers · migration 0019) · kind CNPG failover 15/15. The NFR-P1 re-measurement (k6) exceeded its budgets under another project's load (load ≈ 14) and is left undecided (`test/impl/47th/result.md`).
Eighth gate (43rd): pytest SQLite 310 passed + 1 skipped (the 2 performance tests exceeded their budgets under load from another project; an A/B run shows no regression, re-measure on a quiet host) · PostgreSQL 313 passed (re-run after the final tweaks: 312 + 1 performance test under load) · Vitest 27 · Playwright 77 · helm-check 4 scenarios · prod rehearsal 40th 8/8 + 42nd 4/4 + 43rd 8/8 + regression 36th 10/10 · 37th 9/9 · 39th 10/10 · 35th 8/8 (PostgreSQL · 2 workers · migration 0019) · kind a2a-grpc 20/20.
Seventh gate (39th): pytest SQLite 298 passed + 1 skipped · PostgreSQL 299 passed · Vitest 27 · Playwright 77 · helm-check 4 scenarios · prod rehearsal 36th 10/10 + 37th 9/9 + 39th 10/10 + regression 32nd 11/11 · 33rd 7/7 · 35th 8/8 (PostgreSQL · 2 workers · migration 0018) · production-grade kind cluster 27/27 (availability 1.0 during a node failure).
Sixth gate (35th): pytest SQLite 263 passed + 1 skipped · PostgreSQL 264 passed · Vitest 27 · Playwright 77 · prod rehearsal 32nd 11/11 + 33rd 7/7 + 35th 8/8 + regression 29th 13/13 · 30th 9/9 · 27th 11/11 (PostgreSQL · 2 workers · migration 0018) · multi-node kind 21/21.
Fifth gate (31st): pytest SQLite 247 passed + 1 skipped (PostgreSQL-only) · PostgreSQL 248 passed · Vitest 27 · Playwright 75 (3 light-theme and 4 dark-theme accessibility) · prod rehearsal 29th 13/13 + 30th 9/9 + 27th 11/11 + 24th 13/14 (P24-01 pins the head of that time, `0014`; it is now `0016`, an expected difference) (PostgreSQL · 2 workers · migration 0016) · Helm on kind 18/18. Fourth gate (27th): 237+1 · 238 · 26 · 67 · 11/11 + 14/14. Third gate (23rd): 224+1 · 225 · 58 · 44/44.

NFR-P1 measured on the prod rehearsal (PostgreSQL, 2 workers, mock provider, k6 at 32 rps): Unified Model API p95 **10.4 ms** (≤ 30) ·
MCP proxy added latency p95 **17.6 ms** (≤ 20) · first token p95 **13.46 ms** (≤ 2 s). See `deploy/k6/gateway.js` and `test/impl/16th/result.md`
(first round, 13th: 15.7 · 18.2 · 12.4 ms).

NFR-S1 and A1 measured in 25th on the prod rehearsal: 1,000 concurrent MCP sessions with 0–0.01% errors and tool-call p95 583–917 ms (threshold 1 s, measured while other stacks loaded the same Docker VM; a single client gets p95 24 ms, and 18th on a quiet host got 80 ms) — `deploy/k6/sessions.js`.
Fault injection (`deploy/k6/chaos.js`, model 20 rps + MCP 10 rps for 180 s with 4 faults): overall **99.83%** · worker kill 99.92% / 1 s recovery · first-choice upstream down **100%** (fallback) · Valkey restart **100%** · PostgreSQL restart 99.41% / 2 s recovery — `test/impl/25th/result.md`.

## Troubleshooting (issues actually hit during implementation)

| Symptom | Cause | Fix |
|---------|-------|-----|
| port 8000 conflict after `make local-all` | another project on the host uses 8000 | `API_PORT=8800` (default) |
| port conflict running local and prod together | both use `API_PORT` | run one mode at a time (`make local-stop`, then `make prod-all`) |
| a run still shows `waiting_approval` right after approval (test race) | the approve API only resets the step and schedules the resume — the run row stays waiting until the resume picks it up | wait for a terminal state (`succeeded`, `partial`, `failed`) (50th) |
| performance tests fail only under load | another project's containers use the CPU — noise only ever adds latency | re-measure up to `PERF_TRIES` (default 3) times and judge the best run (50th) |
| a shell smoke gets an empty JSON-RPC response | bash 3.2 breaks nested quoting like `"$(f "{\"k\":…}")"` | build the body in a variable first and pass `-d "$BODY"` (51st) |
| approvals or token issuance return 403 for machine tokens | only humans (`user`, `pat`) decide approvals and consents, and tokens are issued from a login session only (51st) | run it from a console or CLI login session |
| `nexus bundle import` stops with 'cannot verify signature' or 'manifest signature' | import verifies signatures by default (54th), and only with a key obtained outside the bundle (59th — a `cosign.pub` inside the bundle is ignored) | sign with `cosign sign-blob --key … manifest.json > manifest.json.sig`, ship the signature in the bundle and the public key separately → `--cosign-key <public key>`; `--allow-unsigned` only for PoCs |
| After an upgrade every push from a remote agent is 401 | signed push JWTs need `iss`, `jti` and `request_body_sha256`, and a JWKS needs an issuer (56th) — a `push_verify` saved without an issuer no longer passes | set the agent's `auth.push_verify.issuer` and check the remote agent sends all three claims |
| `nexus keys rotate` returns 409 `keys_env_managed` | keys are injected via env (Secret) — the app only rotates file-mode keys | follow the two-phase env procedure in SECURITY.md (deploy `*_NEXT` → promote it and move the old key to `*_PREVIOUS` → `nexus keys reencrypt`, 62nd) |
| `nexus install compose --cosign-key` fails with 'could not pull from the registry' or a digest check | signatures are verified against pulled registry digests only (59th) — a local build tag (`wise-agentnexus-api:prod`) has nothing to verify | use a compose overlay with registry image names; `--allow-unsigned` for PoCs |
| Usage records with status `cancelled` | the client disconnected mid-stream (59th) — usage is estimated from what was sent and settled | expected (counted as an error in error rates) |
| `opa` keeps showing in the service list | `down` does not remove containers outside the active profiles | `COMPOSE_PROFILES=demo,opa make local-stop` |
| `$DC run …` fails with "no such file" in zsh | zsh does not word-split variables | use the `make` targets or `${=DC}` |
| `pnpm install --frozen-lockfile` fails in the image build | package.json changed without a lockfile refresh | `make web-lock` (extracts a copy taken right after install) |
| console login bounces back to `/login` | bundle missing, or navigating before the callback finished | `make web-build`, wait for the callback |
| `/console` returns 503 `console_not_built` | `web/dist` missing in local mode | `make web-build` (the prod image includes it) |
| first request ECONNREFUSED right after `docker compose up web` | depends_on recreated the api container | retry after the health check passes |
| prod rehearsal login with the bootstrap password fails | an existing `admin` is never changed by the seed (by design) | use a separate rehearsal account (`test/impl/13th/smoke-prod.sh`) |
| 429 under k6 | default limit of 50 rps per subject | fixed arrival rate (32 rps) or raise the limit |
| MCP tool names differ from the OpenAPI spec | FastMCP turns `.` in operationIds into `_` | use the converted names |
| audit chain broke after archiving on SQLite | rowids reused after deletion | seq is assigned explicitly as `last_seq + 1` |
| in-process tests hang waiting for a sampling request | httpx `ASGITransport` buffers the whole response, so mid-stream SSE events never arrive | `app.core.http.StreamingASGITransport` (forwards chunks as they come) |
| fastmcp client elicitation handler fails validation | the handler must return the data dict, not an `ElicitResult` | return the dict (result objects only for decline or cancel) |
| admin API errors have no `error.code` | admin REST uses problem+json, with `code` at the top level | read `.json()["code"]` |
| remote delegation card never shows | the run finished before the Task was created, so the last poll was missed | refetch when the run state changes |
| MCP added latency p95 rose to 22.2 ms (> 20) after mTLS | every upstream call with no TLS config read the policy from the DB | cache the policy for 5 s and skip the DB for targets with no TLS config → 17.6 ms |
| developer A2A registration fails with `database is locked` (SQLite) | the notification was written in a separate session inside an open write transaction | notify after commit |
| `replan.on` in graph YAML is ignored | YAML 1.1 reads the `on:` key as `true` | use `replan.triggers` (legacy `on` still accepted) |
| JSON checks in a shell script come back empty | `echo` turns `\n` inside strings into real newlines and jq fails | `printf '%s'` |
| a run stays waiting after an approval expires (multi-process) | the resume could not be scheduled from a process without an event loop | the recovery job resumes waiting runs that have nothing left to wait for |
| a fresh login right after a forced sign-out also gets 401 | JWT `iat` is whole seconds while the cutoff has microseconds | compare in seconds (`int(nbf.timestamp())`) |
| some requests still see the old RBAC matrix or PII rules | 5-second cache per worker | all workers pick it up within 5 s (by design); the simulator reads the stored value directly |
| "end other sessions" signs out the current browser | the newest session was assumed to be the current one | the access token carries `sid`, which identifies the current session |
| `POST /api/v1/admin/restore` 400 `confirm_required` · `nexus bundle import` fails | restore requires `confirm: "RESTORE"` | the console asks you to type RESTORE; the CLI passes it with `--restore-config` (a `-prerestore` snapshot is taken first) |
| component replicas show 1 in prod | the image CMD hard-coded the worker count | `UVICORN_WORKERS` (image, .env, Helm `api.workers`) |
| e2e cannot find table headers by `columnheader` · a select's label includes its option text | test locators | match text inside `thead` · use a nearby button name |
| gateway requests fail while Valkey restarts | the rate limiter raised Valkey connection errors | fall back to a per-worker local bucket for 5 s (`backend_status.degraded`) — 100% during the 25th restart window |
| k6 p95 for 1,000 sessions swings between runs (583 ms – 6.1 s) | other projects loading the same Docker VM | check per-request cost with a single client (p95 24 ms) and re-measure when the host is quiet |
| hundreds of axe contrast violations | semantic colours (#2F9E44, …) used as text on tinted backgrounds · muted text #6b7280 | darker text tokens `--ok-fg` etc. · `--fg-muted` #5b6472 |
| `getByLabel` matches several elements in e2e | key names left by earlier runs, select labels that include option text | `exact: true` or a button name |
| kind pods stuck in `ImagePullBackOff` (locally built image) | `kind load docker-image` imports with `--all-platforms`, which fails on multi-platform indexes in Docker Desktop's containerd store | `docker save --platform <os/arch> <image> \| docker exec -i <node> ctr -n k8s.io images import -` (31st script) |
| `make prod-build` ends with `8800 port is already allocated` | prod-build also starts the stack, and local holds the same port | the build has finished — run `make prod-stop`, and `make local-stop` before a rehearsal |
| Tailwind `dark:` variants applied the wrong way round | `dark:` followed only the system setting and ignored `data-theme` | `@custom-variant dark` matching the token rules (explicit choice wins over the system) |
| progress through a modern upstream repeats a value | the MRTR guard pattern re-runs the tool from the start on retry | the gateway drops non-increasing values per downstream token |
| pods time out reaching the API service (10.96.0.1) on kind + Calico | the kind Docker network (192.168.144.0/20) overlaps Calico's default pool 192.168.0.0/16 | pod CIDR 10.244.0.0/16 and the same `CALICO_IPV4POOL_CIDR` |
| "Empty reply" through kind ingress | the current ingress-nginx kind manifest no longer pins the controller to the `ingress-ready` node | patch the controller with `nodeSelector: ingress-ready=true` |
| a smoke script sends the same request several times | macOS `/bin/sh` (bash 3.2) brace-expands `{…,…}` inside `"$( … "…" … )"` | build the JSON in a variable first |
| NFS-Ganesha provisioner in `ImagePullBackOff` | the upstream manifest's image tag (v6.5) does not exist | pin `registry.k8s.io/sig-storage/nfs-provisioner:v4.0.8` |
| NFS PVC stays `Pending`; log shows `AddExport … invalid param value` | the kind node's `/` is overlayfs, which Ganesha VFS cannot export | put the export hostPath under the node's `/var` (ext4) |
| after a node failure the new api pod is in `Init:CrashLoopBackOff` (DB name does not resolve) | both CoreDNS replicas were on the failed node | spread CoreDNS with required anti-affinity |
| a kind node container will not die after delete (`did not receive an exit event`) | the NFS server and nodes were removed while mounts were still held, so the kernel NFSv4 teardown RPC hung | order: helm uninstall → wait for app pods to go → delete the NFS server → delete kind (a hung node clears itself after tens of minutes) |
| contract test fails on new REST paths | the OpenAPI snapshot lacks the new endpoints | refresh with `UPDATE_CONTRACT=1 make test` (removals still fail as contract breaks) |
| chart env coverage test fails (`A2A_GRPC_*`) | compose-only keys missing from the ConfigMap | host port goes to host_only; `A2A_GRPC_PUBLIC_URL` is an empty ConfigMap value |
| in prod a remote A2A GetTask sometimes returns NOT_FOUND | the demo servers inherit `UVICORN_WORKERS=2` from the runtime image (uvicorn reads `UVICORN_*`), and demo tasks live in process memory | set `UVICORN_WORKERS=1` on the demo services in compose and Helm |
| on kind, gRPC or demo calls are blocked (UNAVAILABLE, FAILED_PRECONDITION) | with `networkPolicy.ingressFrom` limited to the Ingress controller, in-cluster clients and the demo servers cannot reach api or a2a-grpc | add the needed sources (pod selectors) to `ingressFrom`; the chart itself opens api for same-release api, mcp-servers, a2a-demo and a2a-grpc pods (51st). Add other in-cluster clients and Prometheus yourself |
| right after a rollout `kubectl logs deploy/…` shows the old pod | a terminating pod is still there (`Found 2 pods`) | wait until only the new pod remains |
| a2a-grpc pod logs `mkdir: cannot create directory '/tmp/prometheus'` | read-only root filesystem; the entrypoint creates the metrics directory | the chart mounts a `/tmp` emptyDir (41st) |
| migration fails with `no such table: refresh_tokens` | the table is `oauth_refresh_tokens` | check the model `__tablename__` before `batch_alter_table` |
| the listen cap stays taken for a while after clients disconnect | an `await` inside `finally` is cancelled again on disconnect (anyio cancellation), so the release is skipped | release synchronously without awaiting (45th) |
| deleting the CNPG primary pod does not trigger a failover | smart shutdown waits for pooled idle connections, so the pod stays Terminating | test failures by stopping the node; in production tune `smartShutdownTimeout` or use `kubectl cnpg promote` |
| every Playwright test fails with `ERR_EMPTY_RESPONSE` | the local stack is down | `make local-all`, then rerun |
| Playwright E30-01 (consent card disappears after deny) occasionally exceeds 30 s | under load during the full run (7 min) the consent result arrives late — passes alone and on rerun (59th) | rerun that spec |
| Connections fail behind PgBouncer with `unsupported startup parameter: options` | session limits (61st) are passed as libpq `options`, which the pooler rejects | set `ignore_startup_parameters = options` on the pooler and `ALTER ROLE <app> SET statement_timeout = '120s'` / `lock_timeout = '15s'` in the database |
| Run events and spans fail to write after 15 s during the 0021 upgrade | index creation holds a write lock and old pods' INSERTs hit the lock limit | with a large `spans` table, run `make db-migrate ENV=prod` first in a maintenance window |
| Startup fails with `현재 서명 키가 SIGNING_REVOKED_KIDS 에 있습니다` | the current kid was revoked (65th — stops instead of accepting a leaked key) | rotate to a new key, then revoke the old kid |
| `nexus keys reencrypt` returns 409 `rotation_in_progress` | that pod still has `ENCRYPTION_KEY_NEXT` (phase 1 of the two-phase rotation) | finish the phase-2 rollout and retry |

## Known limitations and BLOCKED items

- Nothing is BLOCKED (plan 66/66 DONE).
- The design elements left disabled in the third round (Epics 17–23) were all built in Epic 24 of the fourth round — routing-rule version publishing (H02) · OBO propagation for hosted OpenAPI servers (F04) · per-client-key allowed aliases (H13) · per-component health and restart counts (B05) · the A2A `framework` column, file Parts in the test console and push JWT signature checks (G01·G07) · calling tools as a test user (F06).
- The 401/403/429/5xx counters are Prometheus counters (since process start, summed across workers) and reset on restart. The 1-hour figures come from usage records.
- MCP 2026-07-28 is supported downstream (dual-era) and upstream (per server: auto, legacy or 2026-07-28). `subscriptions/listen` reports list changes, resource content and task status by comparing fingerprints every 5 s (upstream `resources/subscribe` is not used). The Tasks extension is for 2026-07-28 clients only; a task runs on the worker that created it and is reported failed (not re-run) if that worker dies. Progress is relayed only by the worker running the upstream call, so an MRTR retry that lands on another worker gets the result without progress. Progress from hosted-openapi and stdio upstreams is out of scope.
- The accessibility scans report 0 violations in both light and dark themes (26th, 28th). Users can extend consent and approval deadlines (up to 10 times, 30th); relays to downstream MCP clients and MRTR answer waits are not human-facing and are not covered.
- PostgreSQL and Valkey are single instances — request failures during a DB restart (8 in 25th) belong to an HA setup.
- RAG uses deterministic mock embeddings for tests (built-in vector index, in-app cosine). Point the `/v1/embeddings` provider at a real embedding model to replace them.
- Server-to-client MCP requests are relayed only on the POST response SSE stream; the standalone SSE stream (`GET /mcp/{id}`) returns 405, which the spec allows. 2026-07-28 clients get these requests through MRTR (`InputRequiredResult`, 27th). The 2026-07-28 spec deprecates sampling.
- Inbound mTLS is the TLS terminator's job (Ingress); the gateway does upstream mTLS only. A change to the mTLS policy applies at once on the worker that saved it and within 5 s on the others.
- Tested with the built-in mock providers, demo reference servers and built-in IdP, without real commercial LLMs, Jira, Confluence, Dynatrace or external IdPs (PRD D8). Real integrations are swapped in through provider and connector settings.
- External MCP servers without authentication (`auth: none`) still get gateway policy and audit, but the user's identity is not propagated upstream. OAuth (RFC 9728) servers are recommended.
- The install screens (A01–A04, A11) are CLI and Helm flows, not web pages, matching the screen types in the design.
- The Helm chart was installed, smoke-tested and removed on single-node kind (31st); on multi-node kind with Calico, ingress-nginx, HPA and PDB (34th); and with cert-manager TLS, external PostgreSQL, NFS RWX and a worker-node failure (38th, availability 1.0). Managed-database HA, zone spreading and HA for single-replica components (valkey, demo servers) are left to a real production cluster.
- A2A defaults to 1.0 and still accepts 0.3 (D15-②). Three bindings are supported: JSONRPC, HTTP+JSON and GRPC (D16). Listing and deleting push configs (`ListTaskPushNotificationConfigs`, `DeleteTaskPushNotificationConfig`) return UNSUPPORTED_OPERATION. gRPC is received in plaintext by default (terminate TLS in front) or over direct TLS with `A2A_GRPC_TLS_*` / Helm `a2aGrpc.tls`; outbound calls use TLS for `grpcs://` addresses.
- Task ownership (44th): the platform A2A server (`/a2a/platform[/{profile}]` — JSON-RPC, REST, gRPC) and gateway-proxied remote Tasks are visible only to their creator; other users' Tasks are left out of lists, and get, cancel, resubscribe, push config and continuing a Task return TaskNotFound (-32001, REST 404, gRPC NOT_FOUND). Tasks a remote agent created outside the gateway do not appear in gateway lists.
- Multiple workers (45th): the cap of 5 concurrent listens is the sum over all workers (Valkey counter, TTL = max listen time + 60 s); progress follows an MRTR retry to another worker through shared rows (`mcp_relays`); the MCP health loop and the A2A watcher run only on the lease holder.
- Production HA (46th): `api.topologySpreadConstraints` (zone spreading); PostgreSQL connections use a 5 s `connect_timeout` and TCP keepalives; CloudNativePG failover on a node failure took 56 s, with `/health` recovering after 65.5 s (on kind). A graceful primary pod shutdown is slow because smart shutdown waits for pooled connections — tune `smartShutdownTimeout` or switch over first.
- Authentication boundary (40th): the `/idp/token` token-exchange grant requires workload `client_id`/`client_secret` authentication (`act` = the workload); `X-AgentNexus-Agent` is honored only for workloads, Access Tokens and delegation tokens (ignored from people); declaring an `X-AgentNexus-Data-Class` below the default needs the `data:declare` permission (granted to every role by default, revocable in the RBAC matrix — without it the class is raised to the default and audited); `X-AgentNexus-Run` must be the caller's own run. The token-exchange step in `test/impl/2nd/smoke.sh` reflects the behavior at that time.
- Policy hardening (43rd): `readOnlyHint` lowers risk only for trusted servers; client push URLs must be public addresses in prod; scrypt N=2^14, r=8, p=5 (older hashes are rehashed at login); re-presenting a rotated refresh token outside a 10-second grace revokes its whole family (migration 0019).
- Licensing (42nd): Apache-2.0; the copyright holder `AgentNexus contributors` is an assumption; `make audit` runs in CI only (needs network); outbound `grpcs://` calls trust the system store plus the agent's `auth.tls.ca_secret` (private CA, 49th).
- Security review fixes (39th): external IdPs accept asymmetric algorithms only; an egress guard blocks link-local and cloud-metadata targets (private upstreams stay allowed — use an egress NetworkPolicy against DNS rebinding); logins lock after 10 failures in 15 minutes (per worker in local, shared through Valkey in prod); loopback redirect URIs only in local/test; referencing another target's secret needs `gateway:write`. Open decisions were addressed in round 8 (40th–43rd); the remaining ones are in `test/impl/39th/scenario.md` and `test/impl/43rd/result.md`.
- Round-2 review fixes (51st): every response carries `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff` and `no-referrer` (HSTS belongs to the Ingress); only humans (`user`, `pat`) can decide approvals and consents — workloads, access tokens and delegated tokens cannot approve even their own requests; tokens cannot mint tokens (issue them from a login session), PATs last at most 90 days (no non-expiring tokens), and client keys for another team need `admin:write`; MFA failures count toward the login lockout; the IP policy applies to the password form, device approval and external IdP callback alike; unknown `X-AgentNexus-Data-Class` values are rejected on MCP and A2A too (case-insensitive); model `data_classes` are enforced, and the rate-limit fallback model goes through the same policy, allowed-class and air-gap checks (429 when blocked); changing an MCP server's authorization server, token endpoint or mode needs `gateway:write`; developers cannot activate a pending agent with PATCH; push URLs are re-checked right before delivery, and the callback handed to remote agents is `GATEWAY_URL` for private-network agents and `PUBLIC_BASE_URL` otherwise; push and poll results get the same sanitizing as responses, and inactive users or blocked agents are neither polled nor accepted for push; relayed sampling still honours the server's deny policy; external IdP display names no longer match `user` policy subjects (use the sub); the Alibaba Cloud metadata address is blocked. Publish metadata may not carry reserved `_anx_*` keys; re-attaching a stored secret through `secret_ref`, or changing the credential form (type, scheme, header, tls) of a config with a bound secret, needs `gateway:write` (entering a new secret value still works), and so does moving the endpoint by re-registering; overdue approvals are auto-rejected by the scheduler instead of being decided; push config for a task the gateway never relayed returns -32001; cancelling a platform A2A task also cancels its orchestration run; platform `tasks/list` returns your tasks regardless of worker (latest 50 persisted).
- Round 11 (52nd–55th): routing-rule `timeout_ms` is now enforced per attempt (seeded rules use 30 s — a timeout is a 504 provider error that retries or falls back); token and budget limits reserve the estimate of in-flight requests so concurrent requests cannot jointly overshoot (settled on completion; if a worker dies before settling, budget holds lapse after 300 s); `any` budgets cover principals without a team via their own spend, and client-key budgets apply (migration 0020); Access Token audiences must be the gateway or a registered `mcp:<id>`/`a2a:<id>`; granting `admin`, mapping an IdP role to `admin` or editing an admin account needs an admin; deleting an IdP signs its users out; a new registry version can only be published by the item owner (or an approver), and mirror auto-approve holds items that use an external authorization server; changing `push_verify` needs `gateway:write`; a TOTP code works once; a2a-grpc uses `grpc.health.v1` probes (readiness includes the api), and in compose prod it gets a minimal env without secrets; Helm restarts api and demo pods when the Secret changes; the concurrent listen limit uses a holder set with fixed expiry (a dead worker's slots lapse even while the user stays active); tool risk is classified per word (camelCase included).
- Round 12 (56th–60th): signed A2A push JWTs must carry `iss`, `iat`, `jti` and `request_body_sha256`; a replayed `jti` is rejected (15 min); with a JWKS, `push_verify.issuer` is required too; remote agent responses and events are capped at 8 MiB (502 / error event beyond that), a 200 that is not JSON is a 502, and Task history keeps the latest 200; gateway proxy and platform card URLs use `PUBLIC_BASE_URL` (the Host header is ignored); scheduled jobs run per component (alerts and SLOs = observe, run recovery = host, backups = admin, approval expiry = gateway or host, registry = registry — the scheduler starts in any combination); keys rotate through admin commands (`nexus keys …`, procedure in SECURITY.md); `readOnlyHint` is trusted for official and partner servers; `/v0/servers` pages in SQL; the mTLS-required policy rejects `verify: false` upstreams; changing an agent's auth drops that worker's cached tokens; creating an OpenAPI server on a namespace in use is a 409 before any secret is written; CLI token and secret files are 0600; OTLP ingest is capped at 8 MiB (413) and malformed bodies are 400; A2A usage records unexpected failures as error; client webhook tokens are stored encrypted; platform executor errors return a reference id instead of the raw text. Round-3 review fixes (59th): trust settings (jwks_uri, internal_url, audience, claims) of an IdP mapped to admin are admin-only, and granting admin needs the admin role plus the `admin:write` scope; a stream cut mid-way still records usage (`cancelled`) and settles, and each budget hold expires 300 s after it was taken; bundle import verifies with a key from outside the bundle (`--cosign-key`); install verifies the pulled digests and starts exactly those images; mirrors hold oauth2 items whose authorization server is not pinned to the platform and never add versions to items from another source; a missing or disabled limit fallback model is a 429; only the Host resumes runs; remote SSE lines and the unauthenticated push receiver are capped too, and compressed responses are refused; re-encryption covers webhook tokens in 500-row batches; a TOTP code with a space cannot be reused.
- Round 13 (61st–66th): PostgreSQL session limits — 2 min per statement, 15 s lock wait (`DB_*_TIMEOUT_MS`); migrations, retention cleanup, the audit chain head lock and seeding run without them · overview KPI indexes (migration 0021) and a 500-row recent sample · keys: `keys/generation` counter and rotation lock, two-phase env rotation (`*_NEXT` → promote → `*_PREVIOUS` with `SIGNING_KEY_PREVIOUS_UNTIL`), public-PEM previous keys, revoked kids (`SIGNING_REVOKED_KIDS` — revoking the current key stops startup; rotate first), rotation audited first, Helm `secrets.rotationNonce` · limits: token holds return only into the minute they were taken; output reservation (`max_tokens`, 256 if absent) is capped at the remaining headroom and unused tokens come back; `max_tokens` must be a positive integer; 32 concurrent model requests per principal (`MODEL_MAX_INFLIGHT_PER_SUBJECT`, per key for client keys); holder and hold expiry use the Valkey clock; plans that never run (routing test, consent preview, failures, cancellations) leave no holds; budget status lists `*` and `any` budgets per bucket · boundaries: target (`mcp:`/`a2a:`) Access Token scopes must be within the exchange policy (empty = policy defaults); unknown push JWKS kids trigger one refetch per 30 s (shared by workers), prod requires https on register, update and use, and `push_verify_issuer` is shown; Task artifacts keep the latest 50 / 4 MiB; OpenAPI creation returns 409 for a namespace or secret already in use and on failure removes only what it created; Inspector dry runs use the server-decided data class; `/v0/servers?search=` matches names in SQL, anonymous callers get 5 rps per IP plus 50 rps overall; new versions of ownerless items need an approver; gRPC readiness is shared for 2 s · remaining items: the probe connects to the listen address, listen release no longer blocks, an external IdP cannot use the built-in issuer, stopping or deleting an IdP also revokes its users' sessions, PATs and client keys, an a2a-grpc TLS overlay example, readiness tolerates 6 failures, the CLI config dir is 0700, `bundle import` restores config only with `--restore-config` (and only a backup listed in the signed manifest), re-encryption is refused on a pod still holding a next key (409 `rotation_in_progress`) and reports the key fingerprint.
- The monthly SLO verdict (99.95%) needs 30 days of real traffic; another worker's latest counters arrive on its next scheduler tick (`SCHEDULER_INTERVAL_S`).
