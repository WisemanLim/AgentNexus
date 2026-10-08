# PRD — AgentNexus (MCP + AI Gateway + A2A 통합 에이전트 플랫폼)

> 원본 / Source of truth
> - `.docs/AgentNexus_PRD.pdf` — PRD v0.1 초안 (2026-09-16, FR 55 · NFR 11)
> - `.docs/AgentNexus_UI_Design.pdf` — 화면설계서 v1.0 (2026-09-30, 화면 100 · 12 그룹)
>
> 이 파일은 구현 기준 요약과 **결정·변경 이력**이다. 원본과 상충하면 아래 변경 이력이 우선한다.
> 구현 진행 상태는 `docs/implementation-plan.md`.

## 1. 목표

사용자가 자연어로 요청하면 로컬 오케스트레이션 에이전트(Planner → Retriever · Summarize · Executor)가 계획·검색·요약·실행을 수행하고,
필요한 도구(MCP)·원격 에이전트(A2A)·모델(LLM)은 모두 AI Gateway 를 경유해 호출한다. 관리자는 카탈로그에서
MCP 서버·A2A 에이전트·모델·에이전트 프로파일을 선택·등록하면 즉시(≤ 60s) 사용하고, 각 구성요소는 독립 또는 연계로 운영한다.

페르소나: 최종 사용자 · 에이전트 개발자 · 플랫폼 운영자 · 보안·IAM 담당 · FinOps 담당 · 외부 파트너 에이전트.

- 한 줄 요약: 모델(LLM)·도구(MCP)·다른 에이전트(A2A) 호출을 하나의 관문(AI Gateway)에서 통제하는 자체 호스팅 에이전트 플랫폼.
- 배경 / 왜 지금: 에이전트는 도구 연결·에이전트 협업·호출 통제라는 세 연결 문제를 함께 풀어야 하는데 표준(MCP·A2A)과 도구가 흩어져 있다. 하나의 그림(도식)으로 정리된 구조를 실제 운영 가능한 플랫폼으로 만든다 (출처: AgentNexus_PRD.pdf p.3).
- 문서: 원본 PRD v0.1 초안(2026-09-16, 작성자 [REDACTED]) · 화면설계서 v1.0(2026-09-30) · 이 파일은 구현 기준 요약 — 최종 갱신 2026-10-07 (wds-prd 보강).

## 2. 범위

- 포함: Agent App/MCP Host(오케스트레이션 4 에이전트, MCP Client Layer, A2A Client), AI Gateway 7 기능, LLM 공급자 어댑터,
  Remote A2A 서버/클라이언트, MCP Registry/Catalog, Identity 연동, Observability & Audit 5 기능, 참조 MCP 서버 5종 + 커스텀 템플릿,
  카탈로그·등록·즉시 사용 콘솔, CLI.
- 제외: LLM 학습, Jira·Confluence·Dynatrace 등 SaaS 자체, 클라우드 관리형 게이트웨이 계약.

## 3. 기능 요구사항 (원본 5장 요약)

| 그룹 | ID | 요구 | 우선 |
|---|---|---|---|
| 플랫폼 | FR-P01 | 통합 카탈로그·레지스트리(등록·검색·버전, 선택→등록→사용 ≤ 60s) | M |
| | FR-P02 | 독립 실행 플랫폼(단일 compose/Helm, 외부 SaaS 없이 동작) | M |
| | FR-P03 | 관리 콘솔·API(REST/OpenAPI + CLI) | M |
| | FR-P04 | SBOM·라이선스 리포트 | S |
| Host | FR-A00 | MCP Host 역할(다수 클라이언트 세션, 동의·승인 경계) | M |
| | FR-A01 | 자연어 진입(채팅 UI·API, 세션·이력, SSE, 첨부) | M |
| | FR-A02 | Local Agent Orchestration(A02.1 Planner · .2 Retriever · .3 Summarize · .4 Executor · .5 팬아웃/합류·부분 실패) | M |
| | FR-A03 | MCP Client Layer(stdio 로컬 / Streamable HTTP 원격=게이트웨이 경유) | M |
| | FR-A04 | A2A Client(Agent Card, message/send·stream, tasks/get·cancel, 푸시) | M |
| | FR-A05 | 플랫폼 에이전트의 A2A 서버 노출 | S |
| Gateway | FR-G00 | 단일 진입 게이트웨이(우회 차단, 프로토콜 인지) | M |
| | FR-G01 | Routing(모델 비용·지연·품질·가용성, 폴백·재시도·A/B, MCP 가상 서버, A2A 스킬) | M |
| | FR-G02 | Security(JWT/OAuth2, mTLS, 입력·출력 가드, 시크릿 격리) | M |
| | FR-G03 | Policies(누가 어떤 모델·도구·에이전트를 어떤 인자로, 데이터 분류별 모델 허용, 버전·감사) | M |
| | FR-G04 | Rate limits(사용자·에이전트·팀·모델·도구 요청/토큰/비용, 429·저비용 폴백) | M |
| | FR-G05 | Monitoring(QPS·지연·오류·토큰·비용, 프로토콜별 스팬 OTel) | M |
| | FR-G06 | Unified Model API(OpenAI 호환, 스트리밍·도구 호출·구조화 출력) | M |
| | FR-G07 | Tool & Agent Governance(허용/차단, 인자 스키마, 고위험 사람 승인, 신뢰 등급, 응답 소독) | M |
| | FR-G08 | A2A API 프록시(Agent Card 캐시·검증, 인증 주입·레이트리밋·트레이스) | M |
| | FR-G09 | MCP Requests 프록시(세션, 도구 목록 집계, Discover servers) | M |
| LLM | FR-L00 | 다중 공급자(어댑터, Unified Model API 경유) | M |
| | FR-L01 | 상용 공급자 어댑터(OpenAI·Anthropic·Gemini, 키는 게이트웨이만) | M |
| | FR-L02 | Domain-Specific Models(vLLM/Ollama, 내부 데이터 → 로컬 모델) | M |
| | FR-L03 | Access Tokens 관리(짧은 수명·스코프 제한 발급·폐기) | M |
| A2A | FR-R00~R06 | 원격 에이전트 등록·호출, Agent Card, Messages, Tasks(상태 머신·영속), Streaming·푸시, Artifacts, 원격 인증 | M |
| Registry | FR-C00 | 발견 전용 레지스트리(server.json, 미러, 커스텀) | M |
| | FR-C01 | Discover servers | M |
| | FR-C02 | Register Metadata(검증·서명, 승인 워크플로) | M |
| | FR-C03 | A2A Agent Card 카탈로그 | S |
| Identity | FR-I00 | IdP 연동(Entra ID·Okta OIDC, 기본 IdP) | M |
| | FR-I01 | 사용자 인증(OIDC Code + PKCE, 그룹·역할) | M |
| | FR-I02 | Access Token 전파(RFC 8693/OBO, 최소 스코프) | M |
| | FR-I03 | MCP 서버 Auth(OAuth 2.1, RFC 9728) | M |
| | FR-I04 | 에이전트·서비스 아이덴티티 | S |
| MCP Servers | FR-S00~S03 | 호스팅·헬스·자동 등록, SaaS 참조 서버, Internal API(OpenAPI → 도구), Custom 템플릿·CLI | M |
| Observe | FR-O00~O05 | OTel 파이프라인, 단일 trace_id, 구조화 로그·PII 마스킹, 메트릭, Usage & Cost, Replay & 불변 감사(해시 체인) | M |
| 연계 | FR-W01~W05 | 구성요소 독립 실행, 오케스트레이션 조합, 외부 연동, 즉시 사용(템플릿 3종), 실패 처리 | M |

NFR: P1 게이트웨이 추가 지연 p95 ≤ 30ms·MCP 프록시 ≤ 20ms·첫 토큰 ≤ 2s·등록→사용 ≤ 60s · S1 무상태 수평 확장 · A1 99.95% ·
SEC1 우회 차단·최소 스코프·키 격리 · SEC2 기본 거부·고위험 승인 · PRV1 PII 마스킹·데이터 분류 라우팅 · O1 OTel 100%·비용 ±2% ·
INT1 MCP·A2A·OpenAI·OTel GenAI·server.json 적합 · LIC1 허용적 라이선스 우선 · M1 사양 버전 고정·어댑터 · D1 compose/Helm·에어갭.

NFR-P1 측정 정의(구현): mock 공급자(업스트림 0) 기준 Unified Model API 응답 시간 = 게이트웨이 추가 지연, MCP 추가 지연 = 게이트웨이
서버 스팬 − 업스트림 MCP 서버 스팬(같은 trace), 첫 토큰 = SSE 첫 바이트. 판정은 prod 리허설(PostgreSQL·k6)로 한다.

## 4. 아키텍처 결정 (구현 기준)

| # | 결정 | 근거 |
|---|---|---|
| D1 | 스택: python-fastapi(FastAPI·SQLAlchemy 2·Alembic·uv) · local=SQLite / prod=PostgreSQL · docker compose | wds-recommend, PRD 8.3 |
| D2 | **내장 구현 + 어댑터**: 게이트웨이(모델·MCP·A2A 프록시)·정책·레이트리밋·토큰 교환·레지스트리·A2A·오케스트레이션·관측 저장·감사를 AgentNexus 안에 구현. 외부는 env 로 붙는 어댑터(OIDC IdP=Keycloak/Entra/Okta, OPA, OpenAI 호환 업스트림=LiteLLM/vLLM/Ollama, Valkey, OTLP Collector→Jaeger) | 2026-10-06 사용자 결정. 오프라인 시험·에어갭 |
| D3 | LangGraph·Temporal·agentgateway·Langfuse 는 미채택 → 내장 YAML 그래프 실행기(DB 영속·승인 재개), DB 영속 A2A Task 감시기, 내장 게이트웨이, 내장 Usage & Cost·리플레이로 대체 | D2 |
| D4 | 웹 콘솔 = React + TS + Vite SPA(FastAPI 정적 서빙, 폰트 로컬 번들), UI 설계서 핵심 화면 우선. 나머지 화면 기능은 API·CLI 로 제공 → D11 로 확장: 웹 95종 + 설치 CLI 5종(A01~A04·A11) | 2026-10-06 사용자 결정 |
| D5 | CLI `nexus` = Python Typer | 2026-10-06 사용자 결정 |
| D6 | 캐시/카운터 = Valkey(BSD-3) — scaffold 의 redis:7.4(RSAL/SSPL) 교체 | NFR-LIC1, FR-G04 |
| D7 | 프로토콜 버전 고정: MCP 2025-11-25(Streamable HTTP, 하위 2025-06-18·2025-03-26 협상 허용 · **D13-④ 로 게이트웨이는 2026-07-28 stateless 도 수용 — dual-era**), A2A 0.3.0(JSON-RPC: message/send·stream, tasks/get·cancel — PRD 9.2 메서드명 · **D15-② 로 1.0 기본 + 0.3 협상**), OpenAI Chat Completions 호환 | NFR-M1, UI 설계서 상태 표시("MCP 2025-11") |
| D8 | 상용 LLM 키가 없는 환경에서는 내장 `mock` 공급자·데모 참조 MCP 서버(Jira·Confluence·Dynatrace 모사)·데모 원격 A2A 에이전트로 E2E 시험. 실제 mcp-atlassian·dynatrace-mcp·공급자는 설정으로 교체 | FR-P02 "외부 SaaS 없이 동작" |
| D9 | Helm(NFR-D1·S1): api 파드 initContainer 가 마이그레이션·시드 수행, PostgreSQL advisory lock 으로 다중 파드 동시 기동 직렬화. 서명·암호화 키는 설치 시 생성해 Secret 에 보존. 데모 에이전트 워크로드 시크릿은 공유 볼륨 대신 Secret(`A2A_DEMO_CLIENT_SECRET`) 주입. audit-archive 영속 볼륨은 복제본 > 1 이면 RWX | 파드 간 RWO 볼륨 공유 불가 |
| D10 | ~~현 릴리즈 제한: SCR-C08 중계 미지원, 콘솔 약 60화면~~ → D11 로 대체 (2026-10-06 사용자 결정: 남은 공백 전부 구현) | 구현 결과 |
| D11 | 2차 범위(Epic 14~16): ① MCP 서버→클라이언트 요청(sampling/createMessage·elicitation/create·roots/list·ping, MCP 2025-11-25 SSE 역채널) — 하류 클라이언트가 능력을 선언했으면 그대로 **중계**, 아니면 Host 가 **동의 카드**(SCR-C08)로 받고 sampling 은 게이트웨이가 Unified Model API 로 실행(모델·데이터 등급 라우팅은 게이트웨이 결정), 60초 무응답 자동 거부·감사. 입력창 `/` 메뉴로 리소스·프롬프트 삽입. MRTR(2026-07-28 `InputRequiredResult`)은 D7 버전 고정으로 범위 밖 ② 업스트림 mTLS(FR-G02): 서버·공급자별 CA·클라이언트 인증서(시크릿 저장소) 적용 ③ 남은 P0 콘솔 화면 | 2026-10-06 사용자 결정 |
| D12 | 3차 범위(Epic 17~23): PRD·UI 설계서 대조 감사(2026-10-06)의 공백 전부 — ① 보안·정합성 결함(도구 한도 미적용·토큰 스코프·A2A 가드·trace PII·에어갭 로컬 모델) ② 오케스트레이션 신뢰성(A2A contextId·input-required 재개, 재계획, 조건 분기, 다중 복제 Task·실행 복구, 예약 보존·경보, 품질 라우팅, 바이너리 첨부, **RAG = 내장 벡터 색인**: 문서 → `/v1/embeddings`(mock 결정적 임베딩, 실공급자 교체 가능) → JSON 저장 + 앱 내 코사인, SQLite·PG 동일 동작) ③ 외부 IdP SSO 로그인 ④ 첫 실행·설치 마법사 ⑤ 등록→승인→사용 흐름 ⑥ 채팅·오케스트레이션 편집기 ⑦ 운영·관측 화면 깊이 | 2026-10-06 사용자 결정 |
| D13 | 4차 범위(Epic 24~27, PRD §8 Open Questions): ① Q5 남은 설계 요소 — H02 라우팅 규칙 버전·게시, F04 hosted OpenAPI OBO 전파, H13 키별 허용 모델, B05 구성요소 헬스·재시작 횟수, G01 framework·G07 파일 Part·푸시 서명 검증, F06 **시험 전용 가상 사용자**(역할·팀만 지정, 실제 사용자 대리 없음, 쓰기 도구 드라이런, 감사에 시험 표시) ② Q2 NFR 실측 — k6 로 S1 1천 동시 세션, A1 장애 주입(워커·공급자·DB 재시작) 중 가용성·복구 시간 ③ Q8 접근성 — **KWCAG 2.2 + WCAG 2.2 AA**, axe-core 자동 점검 + 키보드·포커스 시나리오 ④ Q4 MCP 2026-07-28 수용 — 2025-11-25 유지 + 2026-07-28 협상 추가(D7 고정 확장). 제외: Q3 실연동(실자격 증명 필요)·Q7 실제 클러스터(현 kubectl 컨텍스트는 다른 프로젝트)·Q1·Q6(사업·법무) | 2026-10-07 사용자 결정 (wds-implement 4차) |
| D14 | 5차 범위(Epic 28~31): ① 어두운 테마 접근성 — `data-theme=dark`·시스템 다크에서도 axe 위반 0 ② MCP 2026-07-28 확장 — `subscriptions/listen`(목록 변경 알림)·`notifications/progress` 중계·`x-mcp-header`(`Mcp-Param-*`) 검증·업스트림이 modern 이면 게이트웨이도 stateless 호출 ③ 동의·승인 시간 연장(WCAG 2.2.1) — 기본 대기의 **최대 10배**까지 사용자가 연장, 감사 기록 ④ Helm 실제 설치 검증 — 전용 kind 클러스터(별도 kubeconfig, 기존 `kind-secuardian-it` 미사용) 에서 install → 헬스 → 스모크 → 삭제 | 2026-10-07 사용자 결정 (wds-implement 5차) |
| D15 | 6차 범위(Epic 32~35): ① MCP 2026-07-28 리소스 구독·Tasks — `subscriptions/listen` 의 `resourceSubscriptions` → `notifications/resources/updated`, Tasks 확장(장기 도구 호출 작업 생성·조회·결과·취소)을 게이트웨이가 중계 ② A2A 1.0 기본화 — 게이트웨이 Agent Card·엔드포인트는 1.0 이 기본, 0.3 원격 에이전트·클라이언트는 협상으로 계속 지원(D7 A2A 0.3.0 고정 해제, 회귀 없음) ③ 운영형 클러스터 검증 — 전용 kind 다중 노드 + Calico(NetworkPolicy 집행) + ingress-nginx + HPA·PDB 동작 확인 후 삭제(별도 kubeconfig) ④ SLO·오류 예산 — NFR-A1 99.95% 를 30일 이동 창 가용성·지연 SLO 로 집계, 오류 예산 잔량·다중 창 소진율 경보·관측 화면. 제외: Q3 실연동(실자격 증명)·Q1·Q6 | 2026-10-07 사용자 결정 (wds-implement 6차) |
| D16 | 7차 범위(Epic 36~39): ① A2A HTTP+JSON(REST) 바인딩 — 게이트웨이 수신·원격 호출 양쪽, JSONRPC 와 같은 정책·감사·버전 변환 ② A2A gRPC 바인딩 — a2a.proto 서비스를 새 compose 서비스로(수신 게이트웨이 + gRPC 전용 원격 에이전트 호출), grpcio 는 이미지 빌드에서 설치 ③ 운영 클러스터 심화 — 전용 kind 에서 TLS(cert-manager 자체 서명 발급자·Ingress TLS)·외부 PostgreSQL(database.url)·RWX 볼륨(NFS)로 다중 복제 영속·워커 노드 장애 재배치·가용성 측정 후 삭제 ④ 품질 검토 반영 — wds-review 로 전체 보안·품질 검토 → 확인된 결함 수정·회귀(기능 추가 없음). 제외: Q3 실연동·SLO 월간 판정(30일 운영 트래픽) | 2026-10-07 사용자 결정 (wds-implement 7차) |
| D17 | 8차 범위(Epic 40~43, 39th 검토 결정 항목): ① 인증 경계 강화 — `/idp/token` 토큰 교환 grant 는 워크로드(confidential 클라이언트) 인증 필수·`act`=워크로드, 호출자 신고 헤더는 서버 판정(agent 는 워크로드·Access Token·위임 주체만, data class 하향 신고는 `data:declare` 권한 — 역할 기본 부여·RBAC 회수, 없으면 기본값으로 올림 + 감사(처음 고른 '상향만'은 기존 public 신고 경로와 충돌해 재확인), run 은 소유자 확인) ② a2a-grpc Helm 배포 + gRPC 수신 TLS 옵션(kind 검증) ③ 공급망·라이선스·CI — 저장소 라이선스 **Apache-2.0**, NOTICE·THIRD_PARTY·SECURITY.md·Dependabot·CI 확장·빌드 도구/베이스 이미지 고정 ④ 정책 하드닝 — `readOnlyHint` 는 trusted 등급만, 클라이언트 푸시 URL 사설망 차단(prod), scrypt 강도 상향(OWASP 동급 파라미터)+로그인 시 재해싱, refresh token 재사용 탐지(계열 폐기). 제외: Q3 실연동·SLO 월간 판정 | 2026-10-07 사용자 결정 (wds-implement 8차) |
| D18 | 9차 범위(Epic 44~47): ① A2A Task 소유권 — 플랫폼 A2A 서버의 list·get·cancel·subscribe·push·이어 보내기를 요청 주체 범위로(남의 Task 는 존재를 알리지 않음), 게이트웨이 경유 Task 도 주체 확인, 토큰별 주체 캐시 상한 ② 다중 워커 일관성 — listen 구독 한도 Valkey 전역 · progress 중계 워커 간 · MCP 헬스 루프·A2A watcher 리스(워커 1개) ③ 운영 HA 심화 — Helm topologySpreadConstraints(존) · CloudNativePG PostgreSQL HA 장애조치 kind 검증 · DB 연결 재시도 ④ 성능 정리 — 관측 집계 SQL·규칙 평가 공유 · listen 지문 공유 · NFR-P1 재측정. 저장소: `.wds/` 는 .gitignore(재생성 가능 추출물), `.docs/` 는 커밋 · NOTICE 저작권자 현행 유지 · Chart `home` 은 사용자가 검토 후 직접 수정(이 라운드에서 바꾸지 않음). 제외: Q3 실연동·SLO 월간 판정 | 2026-10-07 사용자 결정 (wds-implement 9차) |
| D19 | 10차 범위(Epic 48~51): ① 객체 수준 인가 전수 점검 — 모든 `/api/v1/*/{id}`·프로토콜 경로를 두 사용자로 훑는 시험 행렬, 확인된 IDOR 수정 ② 남은 Lv2 하드닝 — gRPC 서버 상한(메시지 크기·동시 스트림·keepalive)·원격 grpcs 사설 CA·stdio 허용 목록 명령 전체 일치·hosted 클라이언트 닫기·업스트림 세션 캐시 TTL·A2A 푸시 재시도 ③ CI·재현성 — GitHub 액션 SHA 고정·dev-env 재검증·시간 의존 시험 안정화 ④ 2차 품질 검토 — 7~9차 변경분 wds-review 재실행 → 확인 결함만 수정. NFR-P1 재측정은 이번에도 생략(사용자가 aegismesh 를 직접 정리). 제외: Q3 실연동·SLO 월간 판정 | 2026-10-07 사용자 결정 (wds-implement 10차) |
| D20 | 11차 범위(Epic 52~55): ① 모델 게이트웨이 정확성 — 라우팅 규칙 `timeout_ms` 실제 적용·동시 요청 예산 경합(예상 토큰 예약)·`any` 예산의 팀 없는 주체·클라이언트 키 예산 ② 신원·레지스트리 경계 — Access Token audience 는 등록된 대상(게이트웨이·`mcp:{id}`·`a2a:{id}`)만 · admin 역할 부여·IdP 역할 매핑의 admin 은 admin 만 · IdP 삭제 시 그 IdP 세션 종료 · 레지스트리 새 버전 소유자 확인 · 미러 자동 승인 항목의 인증 전송처 보류 · `push_verify` 변경 통제 · TOTP 재사용 거부 ③ 운영 배포 보강 — a2a-grpc gRPC health 프로브·PDB·존 분산·관측 env · Secret 변경 시 재기동 · compose prod a2a-grpc env 축소 · e2e 이미지 digest · 잠금 파일 필수 · bundle import 서명 검증 기본 ④ 관측·카운터 성능 — observe 개요·usage SQL 집계 · listen 증감 카운터 보유자 단위 · 도구 위험도 분류 휴리스틱. PRD §8 Open Questions(Q1~Q8)는 이번 범위 밖 · NFR-P1 재측정 생략 · 제외: Q3 실연동 | 2026-10-08 사용자 결정 (wds-implement 11차) |
| D21 | 12차 범위(Epic 56~60): ① 원격 A2A·푸시 경계 — 푸시 JWT jti 재전송 거부·issuer·본문 해시 필수, 원격 응답 크기 상한·비 JSON 처리·history 상한, 프록시 카드 URL 은 PUBLIC_BASE_URL ② 구성요소 단독 배포 정합성 — gateway-only·registry-only 에서도 해당 예약 작업(승인 만료·보존·레지스트리 동기화) ③ 키 회전 — 관리 명령(admin API·`nexus keys rotate`)으로 수동: 암호화 키는 이전 키로 복호화·새 키로 재암호화, 서명 키는 이전 키를 최대 토큰 수명 동안 JWKS 에 유지 ④ 3차 품질 검토 — 11~12차 변경분 wds-review → 확인 결함 수정 ⑤ 남은 기록 항목(사용자 요청 '나머지 추가') — 2차 검토 VERIFY·INDEX 의 '기록' 중 설계 결정이 아닌 결함·하드닝(레지스트리 발견 API 페이지·신뢰 어휘·mTLS required 의 verify:false·시크릿 생성 덮어쓰기·원격 에이전트 토큰 캐시 무효화·리소스/프롬프트 정책·DB PRAGMA·statement timeout·CLI 토큰 파일 권한 등). 설계 결정 항목(개발자 Task 열람 범위·세션 기억/위임·사용자 이름 잠금·app_env 기본값 등)은 문서화된 결정 유지 · NFR-P1 생략 | 2026-10-08 사용자 결정 (wds-implement 12차) |
| D22 | 13차 범위(Epic 61~66): ① 데이터 계층 견고성 — PostgreSQL statement·lock·idle-in-transaction 타임아웃(env), SQLite busy_timeout, 스팬(parent_id·trace_id)·실행 이벤트(type·ts) 인덱스(migration 0021), 표본 조회 정렬 ② 키 관리 보강 — generation 카운터(내용 기반)·회전 잠금, env 모드 다음 키(`*_NEXT` — 롤링 중 401 없이)·공개키 PEM·만료 있는 이전 키·폐기 kid 목록, 회전 감사 선기록, Helm `rotationNonce` ③ 한도 예약 정밀화 — 토큰 예약은 예약한 분 슬롯에만 반환, 출력 토큰(`max_tokens`) 예약·미사용 반환, 주체별 진행 중 예약 상한, 보유자 만료는 Valkey 시계, `any` 예산 범위 표시 ④ 경계 하드닝 4 — 직접 발급 Access Token 스코프는 대상 토큰 교환 정책의 허용 스코프 안(정책 없으면 거부, 게이트웨이 audience 는 그대로), 푸시 JWKS 모르는 kid 1회 갱신·prod https·issuer 없는 기존 설정 탐지, Task artifacts 상한, OpenAPI 시크릿 같은 이름 409·한 트랜잭션, Inspector 드라이런 서버 측 데이터 등급, 익명 검색 SQL·한도, 소유자 없는 항목 새 버전은 승인자만, gRPC 헬스 결과 캐시 ⑤ 4차 품질 검토 — 13차 변경분 wds-review → 확인 결함 수정 ⑥ 남은 기록 항목 2(사용자 '나머지 추가') — 3차 검토 VERIFY 의 '기록' 중 결함·하드닝: 프로브 리슨 주소, listen 반납 비차단, IdP 내장 issuer 거부·세션 종료 공용화, a2a-grpc TLS 인증서 오버레이 예시, readiness 완화, CLI 설정 디렉터리 0700·fchmod, 번들 반입 구성 복원 기본 끔, 헬스 요청 varint · NFR-P1 생략 | 2026-10-08 사용자 결정 (wds-implement 13차) |

## 5. 문제와 대상 (Why & Who)

- **해결하려는 문제**: 팀마다 LLM·사내 SaaS 도구·외부 에이전트를 제각각 연결해, 누가 어떤 모델·도구·에이전트를 어떤 데이터로 호출했는지 한곳에서 통제·감사할 수 없다.
  그 결과 게이트웨이 우회 호출·API 키 노출·비용 초과·도구 응답을 통한 간접 인젝션 위험이 커진다 (출처: AgentNexus_PRD.pdf p.3, p.31).
- **비즈니스 관점 중요성**: 사내 데이터를 쓰는 에이전트 도입의 전제 조건(보안·감사·비용 통제)이며, 상용 SaaS 종속·데이터 반출을 줄인다 (출처: AgentNexus_PRD.pdf p.4, p.28).
- **근거 데이터 / VOC / 로그**: `가정:` 원본은 도식 분석·오픈소스 조사 기반이라 정량 VOC·사고 로그가 없다 → Open Question Q1.
- **타깃 사용자 / 첫 화면** (출처: AgentNexus_PRD.pdf p.10 · AgentNexus_UI_Design.pdf p.21):

| 사용자 | 하고 싶은 일 | 첫 화면 |
|---|---|---|
| U1 최종 사용자 | 자연어로 요청하고 Jira·Confluence·내부 시스템 결과를 한 번에 받기, 도구 실행 승인 | `/chat` (C01) |
| U2 에이전트 개발자 | MCP 서버·A2A 에이전트·에이전트 프로파일을 만들어 등록·시험 | `/console/agents` (D01) |
| U3 플랫폼 운영자 | 설치·업데이트, 모델·도구·에이전트 호출 라우팅·한도·카탈로그 승인 | `/console` (B02) |
| U4 보안·IAM 담당 | IdP 신원으로 접근 통제, 토큰 교환·정책·거버넌스·감사 | `/console` (B02, 보안 위젯 강조) |
| U5 FinOps 담당 | 팀·모델·도구별 사용량·비용·예산 | `/console/observe/cost` (K06) |
| U6 외부 파트너 에이전트 | Agent Card 로 발견되고 Task 를 위임받기 (화면 없음 — A2A API) | — |

- **대표 사용 상황**: 주간 이슈 보고 · 원격 전문 에이전트에 장애 분석 위임 · 카탈로그에서 즉시 사용 · 팀 예산 초과 시 저비용 모델 폴백 (출처: AgentNexus_PRD.pdf p.10 §4.3).

## 6. 사용자 경험 (How — User Flow)

- **시나리오 1 — 자연어 요청 처리 (FL-02)**: 최종 사용자가 채팅에서 "이번 주 프로젝트 X 이슈 요약해서 주간 보고 페이지로 만들어줘" → Planner 계획(C02) →
  Retriever 가 게이트웨이 경유로 Jira 검색(허용목록·인자 검사, 토큰 교환) → Summarize(내부 데이터는 로컬 모델) → Executor 쓰기는 사람 승인(C04) →
  근거와 함께 스트리밍(C03). 하위 실패는 부분 결과·사유(MSG-014), 전 구간 단일 trace_id 로 K03·K08 에서 재현 (출처: AgentNexus_UI_Design.pdf p.16 · AgentNexus_PRD.pdf p.10).
- **시나리오 2 — MCP 서버 등록 → 승인 → 즉시 사용 (FL-03)**: 개발자가 템플릿(F05)으로 서버를 만들고 server.json 등록(E04) → 운영자 승인(E05, 신뢰 등급·허용목록 생성) →
  즉시 사용 마법사(E03: 토큰·정책 기본값) → 60초 안에 그래프(D02)·채팅(C01)에서 사용. 반려는 사유와 함께 E04 로 (출처: AgentNexus_UI_Design.pdf p.17 · AgentNexus_PRD.pdf p.10 시나리오 3).
- **시나리오 3 — 비용·한도·감사 통제 (FL-04)**: 호출량 증가 → Usage & Cost(K06) 예산 80% 알림(MSG-013) → 한도(H09) 초과 시 429 또는 저비용 모델 폴백(MSG-015) →
  결정 로그(H04)·리플레이(K08)로 근거 재현 → 감사 체인 검증(K09)·규제 리포트(K10) (출처: AgentNexus_UI_Design.pdf p.18 · AgentNexus_PRD.pdf p.10 시나리오 4).
- **설치·첫 실행 (FL-01)**: 사전 점검(A01) → Compose(A02)/Helm(A03)/에어갭 번들(A04) → 첫 실행 마법사 A05~A09(건너뛰기 가능) → 로그인(A10) 후 역할별 첫 화면 (출처: AgentNexus_UI_Design.pdf p.15).
- **수용 기준 예 (Given-When-Then)**:
  - Given 운영자가 카탈로그 항목을 승인했고, When 즉시 사용 마법사를 마치면, Then 60초 안에 채팅·그래프 도구 목록에 보이고 항목 전용 허용 정책만 생긴다.
  - Given 고위험 쓰기 도구가 계획에 있고, When 승인자가 거부하거나 기한이 지나면, Then 실행은 부분 결과와 사유로 끝나고 감사에 남는다.
  - Given 팀 예산을 넘었고 on_exceed=fallback 이면, When 같은 팀이 모델을 호출하면, Then 저비용 모델로 응답하고 결정 로그에 사유가 남는다.

## 7. 성공 지표 (Success)

원본 목표값은 MVP 기준이며 기준선은 정의되지 않았다(신규 플랫폼). `가정:` 기준선 = 플랫폼 도입 전(통합 관문 없음 — 해당 통제 0%·측정 불가). 현재값은 구현 후 시험 실측(2026-10-07).

| KPI | 기준선 | 목표값 (MVP) | 측정 방법 | 현재값 |
|---|---|---|---|---|
| 완전성 — 도식 11그룹·44컴포넌트 구현·계약 시험 | 0% (`가정:`) | 100% | CI 계약 시험 (`test/test_contract.py`) | Epic 47/47 DONE · OpenAPI 계약 스냅샷 통과 |
| 즉시 사용 — 카탈로그 선택 → 사용 가능 | `가정:` 수동 연동(측정 없음) | ≤ 60s | CI 타이밍 · 노출 확인 API | 60초 노출 확인 시험 통과 (21st) |
| 표준 적합성 — MCP·A2A·OpenAI 호환 | — | 통과 | 적합성 시험 (공식 SDK·클라이언트) | 통과 (MCP Python SDK·a2a-sdk 1.2.2·openai SDK) |
| 보안 — 우회 경로·미허용 도구 통과·백엔드 키 노출 | `가정:` 통제 없음 | 각 0건 | 네트워크·권한 시험 · 우회 점검 | 0건 (pytest·prod 리허설 bypass-check) |
| 성능 — 게이트웨이 추가 지연 p95 · MCP 프록시 p95 · 첫 토큰 p95 | — | ≤ 30ms · ≤ 20ms · ≤ 2s | k6 (prod 리허설, mock 공급자) | 10.4ms · 17.6ms · 13.46ms (16th) |
| 관측 — trace_id 전 구간 전파율 · 비용 집계 오차 | — | 100% · ±2% | OTel 검증 · 단가표 대조 | 단일 trace 시험 통과 · 계측률 KPI 표시 (운영 실측은 Q2) |
| 연계 — 템플릿 3종(사내 Q&A·이슈 보고·장애 분석 위임) E2E | 0/3 | 3/3 | E2E 시험 | 3/3 (pytest·Playwright) |
| 확장성 — 동시 MCP 세션 (NFR-S1) | — | 1,000 세션 · 오류 < 1% · 도구 호출 p95 < 1s | k6 `deploy/k6/sessions.js` (prod 리허설) | 1,000 세션 · 오류 0% · p95 80.35ms (18th) · 583~917ms (25th, 같은 VM 다른 스택 부하 중 — 단일 클라이언트 p95 24ms) |
| 가용성 — 장애 중 (NFR-A1) | `가정:` 장애 시 동작 미측정 | 장애 유형별 가용성 ≥ 99% · 복구 ≤ 30s | k6 `deploy/k6/chaos.js` + 장애 주입 `test/impl/25th/chaos-prod.sh` | 180초·장애 4건 중 99.83% · 워커 종료 99.92%/1s · 업스트림 장애 100%(폴백) · Valkey 재시작 100% · DB 재시작 99.41%/2s (25th) |
| 접근성 (D13-③·D14-①③) | 위반 84건(25th 전) | KWCAG 2.2 + WCAG 2.2 AA 자동 점검 위반 0 | axe-core (Playwright) 콘솔 60 화면 + 채팅·로그인, 밝은·어두운 테마 | 0건 (26th 밝은 · 28th 어두운 67 → 0) · 시간 조절 2.2.1 — 동의·승인 연장 10회 (30th) |

(출처: AgentNexus_PRD.pdf p.28–29 §11 · 측정값 `test/impl/13th·16th·21st/result.md`)

## 8. 리스크 및 메모

| 리스크 | 영향 | 대응 (원본) | 구현 상태 |
|---|---|---|---|
| MCP·A2A 사양 급변 | 호환성 파손 | 버전 고정·어댑터·분기 업그레이드 | D7 + MCP 2026-07-28 dual-era(27th) · A2A 0.3/1.0 동시 수용 |
| 게이트웨이 단일 장애점·지연 | 전체 중단·UX 저하 | 다중 인스턴스·폴백·캐시·경로별 SLO | 무상태 다중 워커·Helm HPA·폴백 · 장애 주입 실측 99.83%·복구 ≤ 2s(25th), 월간 99.95% 는 운영 측정 — SLO·오류 예산·소진율 경보 내장(35th) |
| 도구 응답 간접 인젝션·과도한 에이전시 | 오작동·데이터 유출 | 소독·허용목록·승인·데이터 분류 라우팅 | 구현 (COMPLIANCE OWASP T1~T15) |
| 상용 SaaS 종속·비용 | 비용·반출 | OIDC 연합·로컬 모델·자체 호스팅·예산 | 구현 · 실연동 미시험(Q3) |
| 라이선스 혼입(AGPL·ee·ELv2) | 법적 리스크 | 정책·SBOM 경고 | SBOM·라이선스 리포트(E08)·Valkey 교체(D6) |
| 레지스트리 신뢰(악성 서버 등록) | 공급망 공격 | 승인·서명·출처 검증·스코프 최소화 | 승인 워크플로·서명 검증·신뢰 등급 |

(출처: AgentNexus_PRD.pdf p.30–31 §13)

- **보정 (2026-10-07)**: §7 의 S1 행 — 처음 보강 때 '미측정'으로 적었으나 18th 에서 이미 실측했다.
- **의존성**: 내부 — 없음(단일 compose/Helm, D2). 외부 — 상용 LLM 키(OpenAI·Anthropic·Gemini), 조직 IdP(Entra ID·Okta), Jira·Confluence·Dynatrace API, 실제 Kubernetes 클러스터(Helm), 이미지 서명 키(cosign). 현재는 mock·데모로 대체(D8).
- **미결정 사항 (Open Questions)**:
  - Q1 도입 근거 정량 데이터(VOC·사고 로그)와 사업 KPI(도입 팀 수·비용 절감률) 정의 — 원본에 없음.
  - Q2 NFR 수치는 원본에서도 초안이다(출처: AgentNexus_PRD.pdf p.38 부록 E-4). NFR-S1 1천 동시 세션은 18th 에서 실측(오류 0%·도구 호출 p95 80.35ms), NFR-A1 가용성 99.95%·장애 중 동작은 아직 측정하지 않았다(Chaos 시험 계획 필요). → 25th 실측(§7 A1 행). 99.95%(월간)는 운영 지표로 측정해야 한다.
  - Q3 상용 LLM·Entra ID/Okta·Jira·Confluence·Dynatrace 실계정 연동 시험의 일정·범위·계정 준비 (D8).
  - Q4 사양 업그레이드 시점: MCP 2026-07-28(MRTR, sampling 폐기 예정)·A2A 1.x 단일화 (D7 고정 해제 조건). → D13-④: MCP 2026-07-28 dual-era 구현(27th — server/discover·헤더 검증·MRTR 중계). → D14-②: 업스트림 modern 호출(서버별 auto·legacy·2026-07-28)·`subscriptions/listen`·`Mcp-Param-*` 검증·progress 중계(29th). → D15-②: A2A 1.0 기본 + 0.3 협상 유지(33rd), D15-①: 리소스 구독·Tasks 확장(32nd).
  - Q5 (→ 24th 구현 완료) 화면은 있으나 비활성으로 남긴 설계 요소 6건(라우팅 규칙 버전 게시·hosted OpenAPI OBO·키별 허용 별칭·구성요소 헬스/재시작 횟수·A2A framework·파일 Part·푸시 서명·테스트 사용자 호출)의 구현 여부.
  - Q6 원본 도식 게시 URL·저작권 확인 (출처: AgentNexus_PRD.pdf p.38 부록 E-1).
  - Q7 Helm 차트의 실제 클러스터 설치 검증 시점 (현재 lint·렌더·오프라인 정합성만). → D14-④: 전용 kind 클러스터에 설치·스모크·삭제 18/18 (31st). 다중 노드·NetworkPolicy 집행 CNI·Ingress·관리형 DB 는 운영 클러스터 몫. → D15-③: kind 다중 노드 + Calico·ingress-nginx·HPA·PDB 21/21 (34th).
  - Q8 접근성 기준: 화면설계서는 키보드 포커스·색각 대응·색+텍스트 병기만 명시 — KWCAG 2.2 / WCAG 2.2 AA 를 공식 기준으로 할지. → D13-③: KWCAG 2.2 + WCAG 2.2 AA — 자동 점검 위반 0 (26th). → D14-①③: 어두운 테마 위반 0 (28th) · 동의·승인 시간 연장(WCAG 2.2.1, 30th).
- **문서 간 차이 (해결·기록)**: 원본 §4.2 페르소나는 5종(FinOps 없음)이나 §1.4 대상 독자·화면설계서 U5 에 FinOps 가 있다 → 6종으로 정함 (출처: AgentNexus_PRD.pdf p.4·p.10 · AgentNexus_UI_Design.pdf p.21).
  원본 §8.3 1차 선정 스택(LangGraph·Temporal·agentgateway·LiteLLM·Langfuse·Keycloak·kgateway)은 D2·D3 로 내장 구현 + 어댑터로 대체 (출처: AgentNexus_PRD.pdf p.23).

## 9. 디자인 요구

- **디자인 방향 / 레퍼런스**: 공공 SW 산출물 표준(CBD D2 사용자 인터페이스 설계서) + 디지털 정부서비스 UI/UX 가이드라인(KRDS) + Material Design 3 상태 패턴. 색은 의미로만 쓰고 항상 텍스트를 함께 표시 (출처: AgentNexus_UI_Design.pdf p.2, p.131).
- **레이아웃**: 콘솔 = 좌 내비 216px(접기 64px, 11메뉴) + 상단 바 48px(⌘K·환경·승인 대기·알림·사용자), 최소 1280×800. 채팅 = 좌 대화 목록 240px + 우 실행 계획 패널 320px, 태블릿 폭(768px)까지 (출처: AgentNexus_UI_Design.pdf p.19, p.21).
- **디자인 토큰**: `--primary #3F51E5` · `--navy #1F3864` · 유형 배지 MCP/A2A/모델/프로파일 4색 · 상태 ok/warn/danger/muted · `--focus` 2px 외곽선 · Pretendard·Noto Sans KR 14/12/16/20px, 등폭 13px — 폰트는 로컬 번들(에어갭) (출처: AgentNexus_UI_Design.pdf p.19).
- **핵심 컴포넌트**: CMP-01~17 — 버튼·입력·표(1만 행 가상화)·유형/상태/신뢰 등급/위험도 배지·trace 링크·비용 칩·스테퍼·코드 블록(시크릿 자동 가림)·그래프 캔버스·실행 타임라인·승인 카드·차트·배너·마스킹 값 (출처: AgentNexus_UI_Design.pdf p.19).
- **상태·메시지**: ST-01~11(로딩·빈 상태·오류·권한 없음·부분 실패·승인 대기·한도 초과·폴백·에어갭·스트리밍·세션 만료) · MSG-001~025 · 확인 대화상자는 삭제·폐기·거부(MSG-002) (출처: AgentNexus_UI_Design.pdf p.20, 부록 B).
- **핵심 화면**: 100종(웹 95 + 설치 CLI 5) — 운영 개요 B02 · 채팅 C01~C08 · 그래프 편집기 D02 · 카탈로그·즉시 사용 E01·E03 · 게이트웨이 H01~H15 · 관측·감사 K01~K10 (D4·D11).
- **접근성**: 키보드 포커스 표시·색각 이상 대응 차트·색만으로 구분 금지 (출처: AgentNexus_UI_Design.pdf p.19). 기준 KWCAG 2.2 + WCAG 2.2 AA (D13-③, 2026-10-07 사용자 결정).

---

## 10. 기술 메모 (Technical Notes — 풀스펙)

- **주요 기술 결정**: §4 D1~D12 가 정본이다. 요약 — 6계층(사용자·클라이언트 → Agent App/MCP Host → AI Gateway → 공급자·원격 에이전트·MCP 서버, 둘레에 Registry·Identity·Observability) (출처: AgentNexus_PRD.pdf p.18 §7.1);
  게이트웨이·정책·토큰 교환·레지스트리·A2A·오케스트레이션·관측·감사는 내장 구현, 외부는 env 어댑터(D2·D3); local=SQLite / prod=PostgreSQL, docker compose·Helm (D1·D9).
- **비기능 요구사항**: §3 NFR 줄(P1·S1·A1·SEC1·SEC2·PRV1·O1·INT1·LIC1·M1·D1, 출처: AgentNexus_PRD.pdf p.17) + `COMPLIANCE.md`(ict-saas, KSIC J — ISMS-P·CSAP·개인정보보호법 매핑, OWASP Agentic T1~T15, 추가 시험: 크로스테넌트 누출 차단·OpenAPI 계약·감사 보관기간 파기 증명).
- **데이터 / 로깅 (지표 측정 관점)**: 사용 기록(토큰·비용·지연·단계 step_id) → 비용·성능 KPI · 라우팅 결정 로그 → 폴백·정책 근거 · 감사 이벤트(해시 체인) → 보안·규제 · OTel 스팬(단일 trace_id) → 관측 KPI ·
  구조화 로그(PII 마스킹, 원문은 암호화 보관·사유 기반 해제) (출처: AgentNexus_PRD.pdf p.26 §9.1 핵심 엔터티).
- **외부 연동 / API 스펙 요약**: MCP 2025-11-25 Streamable HTTP(+stdio) · A2A 0.3/1.0 JSON-RPC(message/send·stream, tasks/get·cancel, 푸시) · OpenAI 호환 `/v1/chat/completions`·`/v1/embeddings` ·
  OIDC Code+PKCE·OAuth 2.1·RFC 8693·9728·8628 · OTLP/HTTP·Prometheus · server.json · 관리 REST `/api/v1/*`(OpenAPI `/openapi.json`, README 엔드포인트 표) (출처: AgentNexus_PRD.pdf p.26 §9.2).
- **리스크 및 트레이드오프**: 내장 구현은 오프라인·에어갭·단일 운영에 유리하나 LangGraph·Temporal 생태계 확장성을 포기 · 워커별 5초 캐시(RBAC·PII·mTLS)는 지연을 줄이는 대신 최대 5초 반영 지연 ·
  RAG 는 결정적 mock 임베딩(실모델은 공급자 교체) · local SQLite ↔ prod PostgreSQL 차이는 양쪽 pytest 와 prod 리허설로 관리.

## 11. 실행 연결 (Epic / Story)

- 구현 계획표 `docs/implementation-plan.md` — Epic 66/66 DONE (10~13차 추가는 아래): 1~13 1차(기반·Identity·카탈로그·게이트웨이·모델·MCP·A2A·오케스트레이션·관측·운영·CLI·콘솔·Helm), 14~16 2차(D11), 17~23 3차(D12), 24~27 4차(D13), 28~31 5차(D14), 32~35 6차(D15), 36~39 7차(D16), 40~43 8차(D17), 44~47 9차(D18). 차수별 판정 `test/impl/<Nth>/result.md`.
- 4차(D13) Epic 24~27 DONE — Q5 설계 요소(24th) · Q2 NFR 실측(25th) · Q8 접근성(26th) · Q4 MCP 2026-07-28(27th).
- 5차(D14) Epic 28~31 DONE — 어두운 테마 접근성(28th) · MCP 2026-07-28 확장(29th) · 시간 연장(30th) · Helm kind 설치(31st).
- 6차(D15) Epic 32~35 DONE — MCP 리소스 구독·Tasks(32nd) · A2A 1.0 기본(33rd) · 운영형 클러스터(34th) · SLO·오류 예산(35th).
- 7차(D16) Epic 36~39 DONE — A2A HTTP+JSON(36th) · A2A gRPC(37th) · 운영 클러스터 심화(38th) · 품질 검토 반영(39th, `.review/`).
- 8차(D17) Epic 40~43 DONE — 인증 경계(40th) · a2a-grpc Helm·TLS(41st) · 공급망·라이선스 Apache-2.0·CI(42nd) · 정책 하드닝(43rd).
- 9차(D18) Epic 44~47 DONE — A2A Task 소유권(44th) · 다중 워커 일관성(45th) · 운영 HA·PostgreSQL 장애조치(46th) · 성능 정리(47th).
- 10차(D19) Epic 48~51 DONE — 객체 수준 인가(48th) · 남은 Lv2 하드닝(49th) · CI·재현성(50th) · 2차 품질 검토(51st, `.review/round2/`).
- 11차(D20) Epic 52~55 DONE — 모델 게이트웨이 정확성(52nd) · 신원·레지스트리 경계(53rd) · 운영 배포 보강(54th) · 관측·카운터 성능(55th).
- 12차(D21) Epic 56~60 DONE — 원격 A2A·푸시 경계(56th) · 구성요소 단독 배포(57th) · 키 회전(58th) · 3차 품질 검토(59th, `.review/round3/`) · 남은 기록 항목(60th).
- 13차(D22) Epic 61~66 DONE — 데이터 계층 견고성(61st) · 키 관리 보강(62nd) · 한도 예약 정밀화(63rd) · 경계 하드닝 4(64th) · 4차 품질 검토(65th, `.review/round4/`) · 남은 기록 항목 2(66th).
- 남은 후보: Q3 실연동 시험(실자격 증명 필요) · SLO 월간 판정(운영 트래픽 30일) · 조용한 호스트에서 NFR-P1 재측정(9차 측정은 다른 프로젝트 부하로 판정 보류) · 매니지드 DB HA SLA · CI 실가동(원격 저장소)·액션 SHA 고정 · Chart `home`(사용자 검토 후 직접 수정).

## 출처 문서 / Sources

| # | 문서 | 역할 | 반영 섹션 |
|---|------|------|-----------|
| 1 | `.docs/AgentNexus_PRD.pdf` (v0.1 초안, 38p) | main | §1 개요·§2 범위·§3 FR·NFR · §5 문제와 대상 · §6 흐름 · §7 지표 · §8 리스크 · §10 기술 메모 |
| 2 | `.docs/AgentNexus_UI_Design.pdf` (v1.0, 131p) | design | §5 사용자 유형·첫 화면 · §6 화면 흐름 FL-01~04 · §9 디자인 요구 |
| 3 | `docs/implementation-plan.md` · `test/impl/*/result.md` · `README.md` | 구현 결과 | §7 현재값 · §8 구현 상태 · §11 |
| 4 | `COMPLIANCE.md` (ict-saas 오버레이) | 업종·규제 | §10 비기능 |

## 12. 변경 이력

| 일자 | 변경 | 사유 |
|---|---|---|
| 2026-10-06 | PRD.md 생성(원본 PDF 요약 + 아키텍처 결정 D1~D8) | wds-implement 0-3 확인 결과 |
| 2026-10-06 | D7 보강: A2A 는 0.3(PRD 명명)과 1.0(현행 a2a-sdk) 을 함께 수용 — 이중 Agent Card·메서드 별칭·게이트웨이 버전 변환 | 공식 a2a-sdk 1.2.2 상호운용 |
| 2026-10-06 | PRD 8.3 1차 선정 중 LangGraph·Temporal·agentgateway·Langfuse → 내장 구현(D3), Redis → Valkey(D6), 콘솔 Vite SPA 핵심 화면(D4) | 사용자 결정 |
| 2026-10-06 | [NF] NFR-P1 측정 정의 추가 · 실측(13th): Model API p95 15.7ms · MCP 추가 지연 p95 18.2ms · 첫 토큰 p95 12.4ms | wds-implement 완료 게이트 |
| 2026-10-06 | [SCOPE] D12: 대조 감사 공백 전부 구현(Epic 17~23) · RAG 내장 벡터 색인 · F02 도구 토글이 인자 스키마를 지우던 결함 수정 | 사용자 결정 (wds-implement 3차) |
| 2026-10-07 | [F] D12-⑤ 구현 결정: 정책 변경은 2인 검토(초안 → 회귀 시험 → 게시 요청(사유) → 작성자 외 승인), 직접 쓰기는 admin 비상 경로(감사 `break_glass`) · 개발자의 원격 A2A 등록·플랫폼 Agent Card 게시는 승인 대기(MSG-008) · 즉시 사용 ③ 은 항목 전용 허용 정책만 생성 · 재승인·롤백해도 심사 기록 유지 | FR-G03·O05·C02·A05 · SCR-H08·E05·D10 (Epic 21) |
| 2026-10-07 | [F] D12-⑥ 구현 결정: 테스트 실행은 쓰기 도구 드라이런이 기본 · 검증 오류가 있으면 '코드만 저장'(그래프 반영·게시 보류) · Retriever 단계의 쓰기 도구는 오류 · 재계획 조건 키는 `replan.triggers`(YAML 1.1 의 `on`=true 회피) · 대화 삭제는 내용 제거·메타 보존 · '다시 묻지 않기'는 같은 세션·중위험만 | FR-A01·A02·W05·G07 · SCR-C03·C04·C07·D03~D08 (Epic 22) |
| 2026-10-07 | [F] D12-⑦ 구현 결정: 세션 강제 종료 = refresh 폐기 + 사용자별 `tokens_not_before`(다음 요청부터 401, 사유 필수) · 현재 세션은 토큰 `sid` 로 식별 · 권한 행렬은 admin 외 역할에 덧붙이기/빼기만(사유 필수, 워커별 5초 캐시) · 토큰 수명 상한 PAT 90일·교환 15분·Access Token 5–60분 · 로그 마스킹 해제는 `audit:read`·사유·30분·시크릿 제외(원문은 암호화 보관) · 복원은 `RESTORE` 입력 확인 + 격리 복구 시험 · 한도 `warn`(경고만)·인자 스키마 `warn` 모드 · 승인 위임(요청자 제외) · 알림 분류별 음소거·Slack 은 warn/danger 만(에어갭 생략) · 예약 백업은 데이터 볼륨 `backups/` 에 N개 보관 | FR-I01·I02·O02·O04·O05·G00·L · SCR-B02~B07·F·G·H·I·J·K·L (Epic 23) |
| 2026-10-07 | [COMPLIANCE] 3차 정합성 재검토(wds-living-doc): COMPLIANCE 의 '예정' 통제를 구현 근거로 갱신 · 누락이던 RAG 색인 입력 소독(OWASP T1 — 지시문 제거·PII·시크릿 마스킹 후 색인) 구현 · 추가 시험(정책 2인 검토·접근 통제·백업 복구 검증) 반영 | COMPLIANCE.md · README · `services/knowledge.py` · test C23-14 |
| 2026-10-07 | [DOCS] wds-prd 보강(풀스펙, 두 PDF 재대조): 템플릿에 없던 섹션 추가 — §1 개요 줄 · §5 문제와 대상 · §6 사용자 흐름(FL-01~04·Given-When-Then) · §7 성공 지표(기준선 `가정:`·현재값) · §8 리스크·의존성·Open Questions Q1~Q8 · §9 디자인 요구 · §10 기술 메모 · §11 실행 연결 · 출처 문서. 기존 §1~§4 내용 변경 없음, 변경 이력 번호만 5 → 12 | 사용자 결정 (wds-prd: 보강 + 풀스펙) |
| 2026-10-07 | [SCOPE] D13: 4차 범위 Epic 24~27 — Q5 남은 설계 요소(F06 시험 전용 가상 사용자) · Q2 NFR 실측(S1·A1) · Q8 접근성 KWCAG 2.2 + WCAG 2.2 AA · Q4 MCP 2026-07-28 협상 추가. Q3·Q7·Q1·Q6 제외 | 사용자 결정 (wds-implement 4차) |
| 2026-10-07 | [NF·F] D13 구현 결과: 24th 남은 설계 요소 6건 · 25th NFR-S1 재측정·NFR-A1 장애 주입 실측(Valkey 장애 시 한도 검사 로컬 대체 보강) · 26th 접근성 위반 84 → 0(의미 색 글자 토큰·보조 글자 대비·본문 바로가기·화면 제목) · 27th MCP 2026-07-28 dual-era(MRTR 중계·requestState AEAD) · §7 보정(S1 은 18th 실측) | README · COMPLIANCE · test/impl/24th~27th |
| 2026-10-07 | [SCOPE] D14: 5차 범위 Epic 28~31 — 어두운 테마 접근성 · MCP 2026-07-28 확장(listen·progress·x-mcp-header·업스트림 modern) · 동의·승인 시간 연장(최대 10배) · Helm 전용 kind 클러스터 설치 검증 | 사용자 결정 (wds-implement 5차) |
| 2026-10-07 | [NF·F·DESIGN] D14 구현 결과: 28th 어두운 테마 위반 67 → 0(테마별 글자 토큰·`@custom-variant dark`) · 29th 업스트림 세대 서버별 선택(`protocol` auto 발견, migration 0015)·modern 업스트림 MRTR 을 같은 중계·동의 경로로 · listen(목록 변경, 5초 지문)·Mcp-Param 검증·progress 중계(증가하지 않는 값 버림) · 30th 연장 1회 = 기본 대기(동의 60초·도구 승인 30분), 10회 상한 = 늘어난 합계 기본의 10배(WCAG 2.2.1 "10번 이상"), migration 0016 · 31st kind 설치 18/18 | README · COMPLIANCE · test/impl/28th~31st |
| 2026-10-07 | [SCOPE] D15: 6차 범위 Epic 32~35 — MCP 리소스 구독·Tasks · A2A 1.0 기본(0.3 호환 유지) · 운영형 클러스터 검증(kind 다중 노드·Calico·ingress·HPA) · SLO·오류 예산 | 사용자 결정 (wds-implement 6차) |
| 2026-10-07 | [F·NF] D15 구현 결과: 32nd 리소스 구독(읽을 수 있는 URI 만 승인·5초 지문)·Tasks 확장(게이트웨이 실행, 2초 안이면 일반 결과, migration 0017) · 33rd A2A 1.0 기본(오케스트레이터 SendMessage·시험 콘솔 1.0·Task 이벤트에 버전 기록) · 34th kind 다중 노드+Calico·ingress-nginx·HPA·PDB 21/21 · 35th SLO 2종(가용성 99.95%·첫 응답 2s 99%, 30일)·워커별 분 단위 SLI(migration 0018)·다중 창 소진율 경보 | README · COMPLIANCE · test/impl/32nd~35th |
| 2026-10-07 | [SCOPE] D16: 7차 범위 Epic 36~39 — A2A HTTP+JSON·gRPC 바인딩 · 운영 클러스터 심화(TLS·외부 DB·RWX·노드 장애) · 품질 검토 반영 | 사용자 결정 (wds-implement 7차) |
| 2026-10-07 | [F·NF] D16 구현 결과: 36th A2A HTTP+JSON(REST) 수신·REST 전용 원격 호출·google.rpc.Status 오류(게이트웨이 정책 오류는 HTTP 매핑) · 37th A2A gRPC — 새 compose 서비스 `a2a-grpc`(8851, 상태 없음·REST 위임)·tenant 라우팅·트레일러 ErrorInfo·gRPC 전용 원격 호출(grpcio·a2a-sdk 런타임 의존성) · 38th kind: cert-manager TLS·외부 PostgreSQL·NFS RWX·워커 노드 장애 가용성 1.0 27/27 · 39th 검토 반영(아래) | README · test/impl/36th~39th |
| 2026-10-07 | [SCOPE] D17: 8차 범위 Epic 40~43 — 인증 경계 강화(토큰 교환 워크로드 인증·신고 헤더 서버 판정) · a2a-grpc Helm·TLS · 공급망·라이선스(Apache-2.0)·CI · 정책 하드닝 | 사용자 결정 (wds-implement 8차) |
| 2026-10-07 | [F·NF·COMPLIANCE] D17 구현 결과: 40th 토큰 교환 워크로드 인증·신고 헤더 서버 판정(데이터 등급 하향은 `data:declare` — '상향만'에서 기존 동작 충돌로 재확인 후 권한 기반) · 41st Helm `a2aGrpc`(Deployment·Service·NetworkPolicy·Ingress GRPC/GRPCS·파드 TLS) + gRPC 수신 TLS env, kind 20/20 · 42nd LICENSE Apache-2.0·NOTICE·THIRD_PARTY(`make licenses`)·SECURITY.md·Dependabot·CI(lint·PG·Vitest·helm-check·audit)·베이스 digest·uv 고정·e2e 잠금 · 43rd readOnlyHint trusted 만·클라이언트 푸시 공인 주소만(prod)·scrypt p=5+재해싱·refresh 재사용 계열 폐기(migration 0019) | README · COMPLIANCE · test/impl/40th~43rd |
| 2026-10-07 | [SCOPE] D18: 9차 범위 Epic 44~47 — A2A Task 소유권(보안) · 다중 워커 일관성 · 운영 HA(존 분산·PostgreSQL HA) · 성능 정리 · `.wds/` gitignore | 사용자 결정 (wds-implement 9차) |
| 2026-10-07 | [F·NF·COMPLIANCE] D18 구현 결과: 44th 플랫폼 A2A·게이트웨이 경유 Task 소유권(남의 Task 는 -32001·목록 제외, 영속 주체 판정 — 교차 사용자 노출 결함 수정) · 45th listen 한도 Valkey 전역 카운터·progress 공유 행 중계·헬스 루프·watcher 리스(끊김 시 반납 누락도 수정) · 46th Helm topologySpreadConstraints·DB 연결 timeout/keepalive, kind CNPG 노드 장애 장애조치 56초·가용성 0.87 · 47th 관측 요약 SQL 집계·경보 요약 공유·listen 지문 주체별 공유 · `.wds/` gitignore · NFR-P1 재측정은 호스트 부하로 보류 | README · COMPLIANCE · test/impl/44th~47th |
| 2026-10-07 | [SCOPE] D19: 10차 범위 Epic 48~51 — 객체 수준 인가 전수 점검 · 남은 Lv2 하드닝 · CI·재현성 · 2차 품질 검토 | 사용자 결정 (wds-implement 10차) |
| 2026-10-08 | [F·NF·COMPLIANCE] D19 구현 결과: 48th 객체 수준 인가 행렬(사용자 경로 66개 분류 강제·남의 객체 29건 403/404 — 결함 0) · 49th gRPC 수신 상한·원격 grpcs 사설 CA·stdio 전체 명령 일치·hosted 클라이언트 닫기·업스트림 세션 유휴 TTL·푸시 재시도 · 50th 액션 커밋 SHA 고정·dev-env 재검증 9/9·시간 의존 시험 경합 제거(승인 대기·1초 노드 타임아웃·성능 재측정) · 51st 2차 검토(`.review/round2/` — 위임 보안·결함 리뷰 + Lv2 라인 리뷰 31개 파일) → 확인 결함 수정: 자격 증명 전송처(인가 서버·토큰 엔드포인트·mode)·한도 폴백 정책 우회·기계 주체 자기 승인·토큰으로 토큰 발급·푸시 검사 루프 차단·보안 응답 헤더·푸시/폴링 소독·MFA 잠금·IP 정책 전 경로·데이터 등급 모르는 값 거부·모델 허용 등급·등록 승인 우회·Helm 데모 시드/NetworkPolicy · 게시 `_anx_*` 예약 키·secret_ref 재부착·재등록 전송처·만료 승인 처리·로컬 취소의 실행 취소·플랫폼 `tasks/list` 워커 무관(prod 리허설 발견) 등 (판정 근거 `VERIFY.md`) · NFR-P1 재측정은 사용자 결정으로 생략 | README · COMPLIANCE · test/impl/48th~51st · test/dev-env/2026-10-07 · .review/round2 |
| 2026-10-08 | [SCOPE] D20: 11차 범위 Epic 52~55 — 모델 게이트웨이 정확성 · 신원·레지스트리 경계(audience 허용 목록·admin 부여는 admin 만) · 운영 배포 보강 · 관측·카운터 성능 (Open Questions 생략, NFR-P1 생략) | 사용자 결정 (wds-implement 11차) |
| 2026-10-08 | [F·NF·COMPLIANCE] D20 구현 결과: 52nd 규칙 `timeout_ms` 시도별 적용·토큰/예산 진행 중 예약(동시 초과 방지)·`any` 예산 팀 없는 주체·클라이언트 키 예산(migration 0020) · 53rd Access Token audience 허용 목록·admin 부여는 admin 만·IdP 삭제 시 세션 종료·게시 소유자·미러 자동 승인 보류(외부 인가)·push_verify 통제·TOTP 1회 · 54th a2a-grpc `grpc.health.v1`(readiness = api)·PDB·존 분산·시크릿 checksum 재기동·compose prod 최소 env·e2e digest·잠금 파일 필수·번들 반입 서명 검증 기본·검증 이미지 그대로 기동 · 55th 개요·usage SQL 집계(이전 값과 동등, PostgreSQL 포함)·listen 보유자 집합·도구 위험도 단어 분류 · Open Questions·NFR-P1 생략 | README · COMPLIANCE · SECURITY · test/impl/52nd~55th |
| 2026-10-08 | [SCOPE] D21: 12차 범위 Epic 56~60 — 원격 A2A·푸시 경계 · 구성요소 단독 배포 정합성 · 키 회전(수동 관리 명령) · 3차 품질 검토 · 남은 기록 항목 (NFR-P1 생략) | 사용자 결정 (wds-implement 12차) |
| 2026-10-08 | [F·NF·COMPLIANCE] D21 구현 결과: 56th 푸시 JWT 발급자·iat·jti(재전송 거부)·본문 해시 필수·JWKS 면 issuer 필수·원격 응답 8 MiB 상한·비 JSON 502·스트림 오류 이벤트·history 200·카드 URL = PUBLIC_BASE_URL · 57th 예약 작업 구성요소별(스케줄러 항상 기동) · 58th 키 회전 관리 명령(서명 직전 키 유예·공개키 보관, 암호화 재암호화·이전 키 제거, env 모드 `*_PREVIOUS`) · 60th 남은 기록 항목 10건 · 59th 3차 검토(위임 보안·결함 + Lv2) 확인 결함 17건 수정(admin 매핑 IdP 신뢰 설정 admin 만·스트림 끊김 정산·예산 예약 고정 만료·번들 검증 키 번들 밖·설치 다이제스트 검증·미러 보류·폴백 모델 없음 429 등), 나머지 근거 기록(`.review/round3/VERIFY.md`) · NFR-P1 생략 | README · COMPLIANCE · SECURITY · test/impl/56th~60th |
| 2026-10-08 | [SCOPE] D22: 13차 범위 Epic 61~66 — 데이터 계층 견고성 · 키 관리 보강 · 한도 예약 정밀화 · 경계 하드닝 4(Access Token 스코프 = 교환 정책 허용 스코프) · 4차 품질 검토 · 남은 기록 항목 2 (NFR-P1 생략) | 사용자 결정 (wds-implement 13차) |
| 2026-10-08 | [F·NF·COMPLIANCE] D22 구현 결과: 61st PostgreSQL 문장·잠금 세션 상한(마이그레이션·대량 작업 해제)·KPI 인덱스 0021 · 62nd generation 카운터·회전 잠금·env 2단계 회전(`*_NEXT`)·공개키 이전 키·검증 끝·폐기 kid·회전 감사 선기록·Helm rotationNonce · 63rd 토큰 예약 분 슬롯 반환·출력 예약·주체별 진행 중 상한·Valkey 시계·예약 누수 4경로·예산 현황 버킷 · 64th 대상용 Access Token 스코프 ⊆ 교환 정책(사용자 결정)·푸시 JWKS 갱신·https·artifacts 상한·OpenAPI 시크릿·드라이런 등급·익명 발견 API·소유자 없는 항목·readiness 공유 · 66th 프로브 주소·listen 반납 비차단·IdP 내장 issuer·세션 기준·TLS 오버레이·readiness 완화·CLI 0700·복원 기본 끔·varint · 65th 4차 검토(위임 보안·결함 + Lv2) 확인 결함 21건 수정(출력 예약 상한·max_tokens 검증·대량 작업 상한 해제·1단계 파드 재암호화 거부·IdP 정리 시 PAT 폐기·현재 키 폐기 거부·실패 경로 예약 반환 등), 나머지 근거 기록 · NFR-P1 생략 | README · COMPLIANCE · SECURITY · test/impl/61st~66th |
| 2026-10-07 | [COMPLIANCE] D16-④ 품질 검토(wds-review: 위임 보안·결함 리뷰 폴백 + 심층 🟡 + Lv2 라인 리뷰) 확인 결함 수정: 외부 IdP alg 허용 목록 · egress 가드(메타데이터·링크 로컬) · 로그인 실패 잠금 · loopback redirect local/test 만 · Access Token 대리 발급 금지 · 디바이스 승인 MFA · 남의 시크릿 참조·전송처 변경은 `gateway:write` · 인가 서버 고정 · 승인·refresh·code 1회 사용 원자화 · 정책 수정자 승인 금지 · 푸시 Task 결합 · 폴링 순환 · 리포트 서명 발급자 검증 · 보존(중계·작업) · 메모리 상한 · Helm liveness TCP · 이미지 위생 · CLI env·경로 · 데모 서버 1 워커. 수정하지 않은 항목은 이유와 함께 결정 항목으로 | COMPLIANCE.md · test/impl/39th · `.review/` |
| 2026-10-06 | [SCOPE] D11: 남은 공백 전부 구현 — Epic 14 SCR-C08 중계·동의, Epic 15 업스트림 mTLS, Epic 16 남은 P0 콘솔 화면 (D10 대체) | 사용자 결정 (wds-implement 재실행) |
| 2026-10-06 | [DESIGN] D4 주석: 콘솔 웹 95종 + 설치 CLI 5종(UI 설계서 100 화면 전부) · [COMPLIANCE] 데이터 등급에 MCP 동의 내용·mTLS 키 추가 | wds-living-doc 정합성 재검토 (README·COMPLIANCE) |
| 2026-10-06 | [NF] NFR-P1 재측정(16th 2차 게이트, prod 2 workers): Model API p95 10.4ms · MCP 추가 지연 p95 17.6ms · 첫 토큰 p95 13.46ms. mTLS 정책 조회는 5초 캐시(같은 워커 즉시 무효화) — 업스트림 호출당 DB 조회 제거 | wds-implement 2차 완료 게이트 |
| 2026-10-06 | [NF] D9 Helm 다중 파드 결정(advisory lock·Secret 키 보존·워크로드 시크릿 주입) · [SCOPE] D10 현 릴리즈 제한(SCR-C08 중계 미지원, 콘솔 약 60화면) · D7 MCP 2025-03-26 협상 명시 | 구현 결과 반영 (README·COMPLIANCE 동기화) |
