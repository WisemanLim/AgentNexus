# 구현 계획표 / Implementation Plan (wds-implement)

> 기준: `PRD.md` (원본 `.docs/AgentNexus_PRD.pdf` v0.1 · `.docs/AgentNexus_UI_Design.pdf` v1.0) · 반복 엔진: wds-implement 폴백(직접 반복, `--max-retry 5`)
> 시험 차수: `test/impl/<Nth>/` (Epic 1개 = 차수 1개). COMPLIANCE.md `testing_additions` 는 Epic 1·4·9 에 배분.

| # | Epic | 기능(FR) | 수용 기준 / 시험 케이스 | 의존 | 상태 |
|---|---|---|---|---|---|
| 1 | 기반: 9.1 엔터티 모델·Alembic, 공통 오류·ID, 감사 해시 체인, OpenAPI 계약 | P03, O05(체인) | SQLite·PG 마이그레이션, 체인 변조 탐지, OpenAPI 계약 스냅샷(contract test) | — | DONE (1st) |
| 2 | Identity: OIDC/JWKS·내장 IdP(Code+PKCE·Device·Client Credentials), RBAC, PAT, RFC 8693 교환, Access Tokens, IdP 연동, 워크로드 | I00–I04, L03 | 미인증 401, 교환 토큰 aud·scope·ttl, 폐기 후 거부, 역할별 403 | 1 | DONE (2nd) |
| 3 | 카탈로그·레지스트리: CatalogItem·버전, `/v0/servers`·`/v0/publish`, 승인, Agent Card 색인, 미러, SBOM | P01, C00–C03, P04 | 등록→승인→발견, 스키마 오류 거부, 승인 전 비노출 | 1,2 | DONE (3rd) |
| 4 | 게이트웨이 정책 코어: 정책(내장+OPA), 레이트리밋·예산(memory/Valkey), 가드, 시크릿, 도구 거버넌스, 승인 대기열, 결정 로그 | G02–G04, G07 | 미등록 도구 0건, 429, 키 노출 0, 팀 간 누출 0(RLS 대체 필터) | 1,2 | DONE (4th) |
| 5 | Unified Model API: `/v1/chat/completions`·`/v1/models`, 어댑터(OpenAI 호환·Anthropic·Gemini·mock), 라우팅·폴백·재시도·A/B, 데이터 분류 라우팅, 사용량 | G01, G05, G06, L00–L02 | 장애 폴백, 설정만으로 모델 교체, 내부 데이터→로컬 모델, 스트리밍 | 4 | DONE (5th) |
| 6 | MCP 경로: `/mcp/{id}` 프록시·세션·집계·가상 서버, MCP Authorization(RFC 9728), 참조 서버(데모), OpenAPI→도구, Custom 템플릿 | A03, G09, S00–S03, I03 | 도구 호출 E2E, 우회 차단, 미허용 도구 거부 | 2–4 | DONE (6th) — sampling 중계 제외 |
| 7 | A2A 경로: `/a2a/{id}` 프록시, Agent Card 검증·캐시, send·stream·tasks·push, Task 영속, Artifacts, 플랫폼 Agent Card, 데모 원격 에이전트 | A04, A05, G08, R00–R06 | Task 위임→Artifacts E2E, 상태 전이 | 2–4 | DONE (7th) — A2A 0.3+1.0 |
| 8 | Host·오케스트레이션: 세션·채팅 SSE, Planner→Retriever/Summarize/Executor, 승인 재개, 프로파일·템플릿 3종, 즉시 사용 | A00–A02, W02, W04, W05 | 템플릿 3종 E2E, 승인 흐름, 부분 실패, ≤ 60s | 5–7 | DONE (8th) |
| 9 | 관측·감사: OTel·trace 조회, 로그 PII 마스킹·보존, `/metrics`, Usage & Cost·예산 알림, 리플레이, 서명 리포트, 아카이브·파기 | G05, O00–O05 | trace_id 전 구간, 비용 ±2%, 보관 경과 삭제 증명 | 5–8 | DONE (9th) |
| 10 | 독립 실행·연동·운영: 구성요소 선택 기동, 커넥터, 시스템 헬스, 백업·복구, 설정, 알림, 검색 | P02, W01, W03 | 구성요소별 단독 기동, 백업→복구 | 1–9 | DONE (10th) |
| 11 | CLI `nexus` (Typer) | P03, S03 | device login, publish, scaffold, install --check | 2,3,6 | DONE (11th) |
| 12 | 웹 콘솔 (React + Vite SPA, 핵심 화면) | P03(UI) | 빌드·단위 시험·브라우저 스모크 | 1–11 | DONE (12th) |
| 13 | Helm chart · 에어갭 번들 | P02, NFR-D1 | `helm lint`·`helm template` | 10 | DONE (13th) |
| 14 | MCP 서버→클라이언트 요청: sampling·elicitation 중계/Host 동의(SCR-C08)·`/` 리소스·프롬프트 메뉴 | A00, A03, G09 | 동의 허용·편집·검토·거부·60s 만료, 하류 SSE 중계, 오케스트레이션 중 동의 카드, 브라우저 | 6, 8, 12 | DONE (14th) |
| 15 | 업스트림 mTLS (MCP·A2A·LLM 공급자) | G02 | 클라이언트 인증서 필수 업스트림 성공·미설정 거부·CA 검증 실패 거부, 시크릿 비노출 | 4–7 | DONE (15th) |
| 16 | 남은 P0 콘솔 화면 (H03·H06·H10·H11·H13~H15·I03·I05·J01·J02·J04·J06·J07·G04·E04·E06·E08·F03~F05·K01·K05·C05·C07) | P03(UI) | Vitest·빌드·Playwright | 12, 14, 15 | DONE (16th) |
| 17 | 보안·정합성 결함 (감사 D12-①): 도구·any 레이트리밋 적용, 워크로드·교환 토큰 스코프 강제, A2A 입출력 가드·외부 PII 마스킹, trace 스팬 PII 마스킹, 에어갭 로컬 모델(ollama) 서비스, OWASP Agentic·PIPA 매핑, MSG-002/003 확인·사유, F02 토글 결함 | G02, G04, L03, I04, P02, NFR-SEC1·SEC2·PRV1 | 도구 한도 429, 스코프 밖 403, A2A 주입 차단·PII 마스킹, 스팬 마스킹, 매핑 문서 | 4–9 | DONE (17th) |
| 18 | 오케스트레이션 신뢰성·확장 (D12-②): A2A contextId·input-required 재개·인라인 답변, 재계획, 조건 분기, 플랫폼 A2A Task DB 영속·실행 복구, 예약 보존·경보, 품질 라우팅, 바이너리 첨부, RAG 내장 벡터 색인, MCP 서버 자동 등록, 1,000 세션 부하 | A01, A02, W02, R02–R04, A05, O02, O03, G01, S00, NFR-S1 | 재개 E2E, 재계획 이벤트, 조건 분기, 재시작 후 tasks/get, 예약 실행, RAG 근거 인용 | 8, 9, 17 | DONE (18th) |
| 19 | 외부 IdP SSO 로그인 (D12-③): Code+PKCE 브로커, A10 IdP별 버튼·원인별 오류, J02 저장 전 시험 로그인, J01 수정·비활성 | I00, I01 | 외부 OIDC(데모 IdP)로 콘솔 로그인·그룹→역할, 실패 원인 표시 | 2, 12 | DONE (19th) |
| 20 | 첫 실행·설치 (D12-④): A05~A09 실제 마법사(설정 코드), A01·A02·A04·A11 CLI 확장, ST-09 에어갭·ST-11 재로그인·전역 배너, L01 변경 미리보기·적용 | P02, P03, W04 | 마법사 5단계 완료→60초 자가 시험, `nexus install compose`, 재로그인 후 입력 유지 | 10–13 | DONE (20th) |
| 21 | 등록→승인→사용 (D12-⑤): E01~E07·D10·G02·G03·H07·H08·F03 — 즉시 사용 마법사, 승인 검사·신뢰 등급·사유, Agent Card 편집·승인, 원격 등록 대기, 정책 diff·롤백·회귀 시험 게이트 | P01, C02, C03, A05, G03, G07 | 미승인 비노출, 사유 필수, 롤백, 회귀 실패 시 게시 차단 | 3, 4, 7, 12 | DONE (21st) |
| 22 | 채팅·오케스트레이션 편집기 깊이 (D12-⑥): C01~C07 카드·메타·피드백·부분 결과, D01~D09 노드 설정·드라이런·버전 비교·Host 세션 | A01, A02, W02 | 브라우저 시나리오, 드라이런 쓰기 0건 | 18, 21 | DONE (22nd) |
| 23 | 운영·관측 화면 깊이 (D12-⑦): B02~B07, F·G·H·I·J·K·L 남은 요소(I02·K10 신규, RBAC 행렬·PII 규칙·세션 종료·마스킹 해제·CSV·우회 점검 등) | P03(UI), O02, O04, O05, G00 | Vitest·빌드·Playwright | 17–22 | DONE (23rd) |
| 24 | 남은 설계 요소 (D13-①, Q5): H02 라우팅 규칙 버전·게시, F04 hosted OpenAPI OBO, H13 키별 허용 모델, B05 헬스·재시작 횟수, G01 framework·G07 파일 Part·푸시 JWT 서명, F06 시험 전용 가상 사용자 | G01, S02, L03, P03, R00·R04·R05, A00 | 버전 게시·롤백, OBO 토큰 교환 후 업스트림 주입, 허용 외 모델 403, 헬스·재시작 표시, 파일 Part 전달·서명 불일치 거부, 가상 사용자 정책 판정·쓰기 드라이런 | 23 | DONE (24th) |
| 25 | NFR 실측 (D13-②, Q2): S1 1천 동시 세션 재측정(18th 실측 회귀), A1 장애 주입(워커·공급자·DB·Valkey 재시작) 가용성·복구 | NFR-S1, NFR-A1 | k6 prod 리허설 — 1천 동시 세션 오류율·지연, 장애 중 성공률·복구 시간 기록 → PRD §7 갱신 | 24 | DONE (25th) |
| 26 | 접근성 (D13-③, Q8): KWCAG 2.2 + WCAG 2.2 AA | P03(UI) | axe-core wcag2a·2aa·21aa·22aa 위반 0(웹 전 화면) · 키보드 이동·포커스·건너뛰기 링크 · 대비 | 23 | DONE (26th) |
| 27 | MCP 2026-07-28 수용 (D13-④, Q4): 2025-11-25 유지 + 2026-07-28 협상 | INT1, M1, A00, G09 | 버전 협상(양쪽), 신규 사양 메시지 중계, 기존 클라이언트 회귀 0 | 23 | DONE (27th) |
| 28 | 어두운 테마 접근성 (D14-①) | P03(UI) | 다크 테마(명시·시스템) axe 위반 0 · 26th 밝은 테마 회귀 없음 | 26 | DONE (28th) |
| 29 | MCP 2026-07-28 확장 (D14-②): subscriptions/listen · progress 중계 · x-mcp-header 검증 · 업스트림 modern 호출 | INT1, G09, A03 | listen 승인·알림 태그 · progress 이벤트 SSE · Mcp-Param 불일치 -32020 · modern 업스트림 stateless 왕복 · 레거시 회귀 0 | 27 | DONE (29th) |
| 30 | 동의·승인 시간 연장 (D14-③, WCAG 2.2.1) | A00, G07 | 연장 → 만료 시각 이동 · 기본의 10배 상한 · 감사 · 화면 「시간 연장」 | 22, 23 | DONE (30th) |
| 31 | Helm 실제 설치 검증 (D14-④, Q7) | D1, S1 | 전용 kind 클러스터: helm install → 파드 Ready → /health·로그인·도구 호출 스모크 → uninstall·클러스터 삭제 | 13 | DONE (31st) |
| 32 | MCP 2026-07-28 리소스 구독·Tasks (D15-①) | INT1, G09 | resourceSubscriptions 승인 · 리소스 내용 변경 → notifications/resources/updated(subscriptionId·uri) · Tasks: 작업 생성 → 상태 조회 → 결과 · 취소 · 다른 주체 접근 거부 · 레거시 회귀 0 | 29 | DONE (32nd) |
| 33 | A2A 1.0 기본화 (D15-②) | A03, INT1 | 게이트웨이 Agent Card 1.0 기본 · 1.0 메서드·형식 수용 · 0.3 클라이언트·원격 에이전트 협상 회귀 0 · 화면·info 버전 표시 | 7 | DONE (33rd) |
| 34 | 운영형 클러스터 검증 (D15-③) | D1, S1 | 전용 kind 다중 노드 + Calico: NetworkPolicy 로 backend 직접 접근 차단 확인 · ingress-nginx 경유 접속 · HPA 확장·PDB · 삭제 | 31 | DONE (34th) |
| 35 | SLO·오류 예산 (D15-④) | A1, O | 30일 이동 창 가용성·지연 SLO · 오류 예산 잔량 · 다중 창 소진율 경보 · 관측 화면 · API | 10, 25 | DONE (35th) |
| 36 | A2A HTTP+JSON 바인딩 (D16-①) | A03, G08, INT1 | 수신 REST(message:send·stream, tasks get·list·cancel·subscribe, 푸시 설정, extendedAgentCard) · 카드 HTTP+JSON 인터페이스 · REST 전용 원격 에이전트 호출(JSONRPC 클라이언트 ↔ REST 업스트림 변환) · 정책·감사·Task 영속 동일 · JSONRPC 회귀 0 | 33 | DONE (36th) |
| 37 | A2A gRPC 바인딩 (D16-②) | A03, G08, INT1 | 새 compose 서비스 a2a-grpc(a2a.proto A2AService) · 인증·정책·감사는 게이트웨이와 같은 경로 · 카드 GRPC 인터페이스 · gRPC 전용 원격 에이전트 호출 · 스트림 · prod 2 workers | 36 | DONE (37th) |
| 38 | 운영 클러스터 심화 (D16-③) | D1, S1, A1, SEC1 | 전용 kind: cert-manager 자체 서명 → Ingress TLS(https 로그인) · 외부 PostgreSQL(database.url, 내장 끔) · NFS RWX 로 api 다중 복제 persistence · 워커 노드 정지 → 재배치·가용성 · 삭제 | 34 | DONE (38th) |
| 39 | 품질 검토 반영 (D16-④) | 전체 | wds-review 결과 중 확인된 결함 수정 · 회귀 시험 · 리뷰 보고 | 36~38 | DONE (39th) |
| 40 | 인증 경계 강화 (D17-①) | I03, G01, G04 | `/idp/token` 토큰 교환: 워크로드 인증 없으면 401 `invalid_client` · `act`=워크로드 · 신고 헤더: 사람 주체의 `X-AgentNexus-Agent` 무시(agent 정책 위조 불가) · data class 하향 신고 무효(기본값 이상) · 남의 run id 무시 · 기존 흐름 회귀 0 | 39 | DONE (40th) |
| 41 | a2a-grpc Helm·TLS (D16-② 남은 항목, D17-②) | D1, A03 | 차트 `a2aGrpc.enabled` Deployment·Service·NetworkPolicy·카드 광고 주소 · gRPC 수신 TLS(인증서 경로 env) · 전용 kind 에서 클러스터 내부·Ingress(TLS) 경유 gRPC send·get 후 삭제 | 37, 38 | DONE (41st) |
| 42 | 공급망·라이선스·CI (D17-③) | LIC1, SEC2 | LICENSE(Apache-2.0)·NOTICE·THIRD_PARTY(파이썬·JS 패키지·폰트 OFL) 생성 스크립트 · 콘솔 번들 법적 주석 보존 · SECURITY.md · Dependabot · CI(ruff·pytest·PG·Vitest·helm-check·audit·permissions) · uv·베이스 이미지 digest 고정 · e2e 잠금 파일 | 39 | DONE (42nd) |
| 43 | 정책 하드닝 (D17-④) | G07, SEC1, I01 | `readOnlyHint` 는 trusted 서버만 low · 클라이언트 푸시 URL 사설·루프백 거부(prod) · scrypt OWASP 동급 파라미터 + 로그인 성공 시 재해싱(기존 해시 호환) · 폐기된 refresh token 재제시 → 같은 계열 전부 폐기 + 감사 | 39 | DONE (43rd) |
| 44 | A2A Task 소유권 (D18-①) | A03, SEC1 | 플랫폼 A2A: 남의 Task list 에 안 보임 · get·cancel·subscribe·push·taskId 이어 보내기 → TaskNotFound(존재 비노출) · 게이트웨이 경유 Task 도 다른 주체면 -32001 · 토큰별 주체 캐시 상한 · 자기 Task 회귀 0 | 33, 36, 37 | DONE (44th) |
| 45 | 다중 워커 일관성 (D18-②) | G09, A1 | listen 동시 구독 한도 Valkey 전역(워커 합) · 다른 워커에서 resume 해도 progress 전달 · MCP 헬스 루프·A2A watcher 는 리스 보유 워커만 · Valkey 장애 시 워커 로컬로 대체 | 29, 32 | DONE (45th) |
| 46 | 운영 HA 심화 (D18-③) | D1, A1 | Helm topologySpreadConstraints(존·노드) · kind 존 라벨 3노드 + CloudNativePG 3 인스턴스 → primary 장애조치 중 api 가용성·쓰기 복구 · DB 연결 재시도(pool_pre_ping·재연결) | 38 | DONE (46th) |
| 47 | 성능 정리 (D18-④) | P1, O | 관측 요약 SQL 집계(행 전체 적재 없음)·경보 규칙 평가 시 요약 공유 · listen 지문 서버 단위 공유 캐시 · NFR-P1 재측정(호스트 부하 기록) · `.wds/` gitignore | 35, 29 | DONE (47th) |
| 48 | 객체 수준 인가 전수 점검 (D19-①) | SEC1, A01 | 라우트 목록에서 경로 매개변수가 있는 엔드포인트 전부를 소유자·다른 사용자·다른 팀으로 호출하는 시험 행렬 · 남의 객체는 404/403(존재 비노출 우선) · 확인된 결함 수정 · 새 라우트가 행렬에서 빠지면 시험 실패 | 44 | DONE (48th) |
| 49 | 남은 Lv2 하드닝 (D19-②) | A03, G02, G08 | gRPC 수신 상한·keepalive · 원격 grpcs 가 에이전트 `auth.tls.ca_secret` 신뢰 · stdio 허용 목록은 명령+인자 전체 · hosted 클라이언트 교체 시 닫힘 · 업스트림 세션 캐시 TTL·상한 · 푸시 중계 재시도(지수 백오프 3회) | 37, 41 | DONE (49th) |
| 50 | CI·재현성 (D19-③) | D1, O | 워크플로 액션을 커밋 SHA 로 고정(태그 주석) · dev-env 재검증(현행 구성) · 시간 의존 시험(성능·1초 노드 시간 초과) 안정화 | 42 | DONE (50th) |
| 51 | 2차 품질 검토 (D19-④) | 전체 | 7~9차 변경 파일 위임 보안·결함 리뷰 + Lv2 라인 리뷰 → 확인 결함 수정·회귀 · `.review/` 2차 보고 | 48~50 | DONE (51st) |
| 52 | 모델 게이트웨이 정확성 (D20-①) | FR-H, NFR-P1, K | 라우팅 규칙 `timeout_ms` 시도별 적용(전체 = 시간 × 시도 수) · 예산·토큰 한도 예약 가산(동시 요청이 한도를 넘지 않음)·실사용으로 정산 · `any` 예산은 팀 없는 주체도 · 클라이언트 키 범위 예산 | 47, 43 | DONE (52nd) |
| 53 | 신원·레지스트리 경계 (D20-②) | SEC1, A01, A07 | Access Token audience 허용 목록 · admin 부여·IdP admin 매핑은 admin 만 · IdP 삭제 → 그 IdP 사용자 세션 종료 · 새 버전 게시 소유자 확인 · 미러 자동 승인 항목의 auth 전송처 → 보류 · `push_verify` 변경은 시크릿 관리 권한 · TOTP 재사용 거부 | 51 | DONE (53rd) |
| 54 | 운영 배포 보강 (D20-③) | D1, SEC2 | a2a-grpc gRPC health 프로브·PDB·topologySpread·OTLP env · Secret checksum 재기동 · compose prod a2a-grpc env 최소 · e2e 베이스 digest · 잠금 파일 없으면 빌드 실패 · bundle import 서명 검증 기본 | 41, 42 | DONE (54th) |
| 55 | 관측·카운터 성능 (D20-④) | O, P1 | observe 개요(RunEvent·스팬)·usage SQL 집계(행 전체 적재 없음, 이전 결과와 같은 값) · listen 카운터 보유자 단위(죽은 워커 몫 TTL 해제) · 도구 위험도 분류 camelCase·동사 보강 | 47, 45 | DONE (55th) |
| 56 | 원격 A2A·푸시 경계 (D21-①) | A03, SEC1 | 푸시 JWT jti 재전송 거부·issuer 필수(JWKS 지정 시)·본문 해시 필수 · 원격 응답 크기 상한(초과 = 오류)·비 JSON 200 처리·Task history 상한 · 프록시 카드 URL = PUBLIC_BASE_URL(Host 헤더 무시) | 44, 49 | DONE (56th) |
| 57 | 구성요소 단독 배포 정합성 (D21-②) | FR-W01, O | gateway-only·registry-only 에서 승인 만료·보존·레지스트리 동기화 실행 · 구성요소 조합별 compose 스모크 | 45 | DONE (57th) |
| 58 | 키 회전 (D21-③) | SEC1, PRV1 | 암호화 키 회전(MultiFernet, 이전 키 복호화·전체 재암호화) · 서명 키 회전(새 kid, 이전 키는 최대 토큰 수명 동안 JWKS 유지 후 제거) · admin API·`nexus keys rotate`·감사 | 40 | DONE (58th) |
| 59 | 3차 품질 검토 (D21-④) | 전체 | 11~12차 변경분 위임 보안·결함 리뷰 + Lv2 라인 리뷰 → 확인 결함 수정 · `.review/round3/` | 52~58, 60 | DONE (59th) |
| 60 | 남은 기록 항목 (D21-⑤) | 전체 | 신뢰 어휘·발견 API 페이지·mTLS verify·토큰 캐시·OpenAPI 시크릿·CLI 0600·OTLP 상한·A2A 사용량·웹훅 토큰 암호화·실행기 오류 원문 | 51 | DONE (60th) |
| 61 | 데이터 계층 견고성 (D22-①) | P03, A1 | PostgreSQL `statement_timeout`·`lock_timeout`·`idle_in_transaction_session_timeout`(env) · SQLite `busy_timeout` · 인덱스 spans(parent_id, trace_id)·run_events(type, ts) (migration 0021) · 표본 조회 정렬 | 47, 55 | DONE (61st) |
| 62 | 키 관리 보강 (D22-②) | SEC1, PRV1 | generation 카운터·회전 파일 잠금 · env 모드 `*_NEXT`(검증 먼저, 롤링 중 401 없음)·공개키 PEM·만료 있는 이전 키·폐기 kid · 회전 감사 선기록 · Helm `rotationNonce` | 58, 59 | DONE (62nd) |
| 63 | 한도 예약 정밀화 (D22-③) | G03, K | 토큰 예약 분 슬롯 반환 · 출력 토큰 예약·미사용 반환 · 주체별 진행 중 예약 상한 · 보유자 만료 Valkey 시계 · `any` 예산 범위 표시 | 52, 59 | DONE (63rd) |
| 64 | 경계 하드닝 4 (D22-④) | I03, G08, P01 | 직접 발급 Access Token 스코프 ⊆ 교환 정책 허용 스코프 · 푸시 JWKS 갱신·https·issuer 없는 설정 탐지 · artifacts 상한 · OpenAPI 시크릿 409·한 트랜잭션 · Inspector 드라이런 데이터 등급 · 익명 검색 SQL·한도 · 소유자 없는 항목 · gRPC 헬스 캐시 | 53, 56, 60 | DONE (64th) |
| 65 | 4차 품질 검토 (D22-⑤) | 전체 | 13차 변경분 위임 보안·결함 리뷰 + Lv2 라인 리뷰 → 확인 결함 수정 · `.review/round4/` | 61~64, 66 | DONE (65th) |
| 66 | 남은 기록 항목 2 (D22-⑥) | 전체 | 프로브 리슨 주소 · listen 반납 비차단 · IdP 내장 issuer 거부·세션 종료 공용화 · a2a-grpc TLS 오버레이 · readiness 완화 · CLI 디렉터리 0700·fchmod · 번들 반입 구성 복원 기본 끔 · 헬스 varint | 59 | DONE (66th) |

## 가정 / Assumptions

- 상용 LLM 키·SaaS(Jira·Confluence·Dynatrace)·외부 IdP 없음 → 내장 mock 공급자·데모 참조 서버·내장 IdP 로 시험(PRD D8). 실제 연동은 어댑터 설정으로 교체.
- k6 호스트 미설치 → `grafana/k6` 컨테이너로 prod 리허설에서 실측(13th): Model API p95 15.7ms · MCP 추가 지연 p95 18.2ms · 첫 토큰 p95 12.4ms. in-process 회귀 감시는 `test/test_perf.py`.
- 브라우저 스모크는 Chrome 확장 미연결로 Playwright 컨테이너(compose `e2e` 프로파일)로 대체(12th).
- Helm 은 실제 클러스터 설치 없이 lint·렌더·오프라인 정합성 검사(13th) — 현재 kubectl 컨텍스트가 다른 프로젝트 클러스터.
