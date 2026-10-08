# COMPLIANCE — 정보통신·SaaS (ICT / SaaS — 기본 베이스라인) (KSIC J)

> 자동 생성 (wds-scaffold). 근거: `domains/ict-saas.yaml`. 규제는 변하므로 아래 출처로 재확인.
> 프로젝트: AgentNexus — MCP + AI Gateway + A2A 통합 에이전트 플랫폼 (`.docs/AgentNexus_PRD.pdf` v0.1)

## 분류 — KSIC · ISIC/NACE/NAICS

| 체계 | 코드 |
|---|---|
| KSIC | J (중분류 58·59·60·61·62·63) |
| ISIC | J |
| NACE | J |
| NAICS | 51 |

정보통신/SW개발/SaaS 는 소프트웨어 기본 도메인이다. 다른 업종 오버레이가 필요하면(예: 공공 납품 → `govtech`) 이 베이스라인 위에 합집합으로 얹는다.

## 한국 규제 (1순위)

| 규제 | 시행 | 영향 |
|---|---|---|
| 정보통신망법 | 상시 | 네트워크·정보보호 의무(§47 = ISMS 근거), 침해사고 신고 |
| 개인정보보호법(PIPA) | 상시 | PII 동의·최소화·안전조치(§29)·유출 통지(§34) — 조항별 매핑은 아래 「개인정보보호법 매핑」(NFR-PRV1) |
| ISMS-P | 상시 | 대형 ICT 서비스 의무, 접근로그·변경관리·암호화 증적, 3년 주기 |
| CSAP(클라우드) | 2025 온라인 발급 | 공공/정부 클라우드 판매 시 IaaS/SaaS/DaaS 인증 |
| 장애인차별금지법 §21 · KWCAG 2.2 | 상시 (공공·법인 웹) | 웹 콘텐츠 접근성 — 콘솔·채팅은 KWCAG 2.2 + WCAG 2.2 AA 를 기준으로 자동 점검 위반 0 (D13-③, `test/impl/26th`) · 어두운 테마도 위반 0 (D14-①, `28th`) · 시간 조절(2.2.1) — 동의·승인 기한을 사용자가 10회까지 연장 (D14-③, `30th`) |

## 국제 기준

- SOC 2 Type II (US SaaS, 6~12개월 통제)
- ISO/IEC 27001 (SOC2와 40~85% 통제 중첩)
- GDPR (EU 고객/테넌트)

## 데이터 등급

| 데이터 | 등급 | 비고 |
|---|---|---|
| 테넌트 데이터 | 민감 | 고객별 격리(RLS/스키마/DB) — AgentNexus 는 팀 단위(예산·정책) 격리 |
| 고객 PII | 민감 | PIPA/GDPR — 프롬프트·도구 응답·트레이스에 포함될 수 있음(PRD NFR-PRV1 마스킹) |
| 인증/아이덴티티(RBAC) | **규제대상** | 접근통제 — Keycloak·토큰 교환(RFC 8693)·Access Token |
| 감사로그/텔레메트리 | 내부 | SOC2/ISO 보관·삭제정책 |
| MCP 동의·중계 내용(sampling 프롬프트·elicitation 입력, 14th) | 민감 | 사람 검토를 위해 승인 레코드·실행 이벤트에 원문 보관(도구 호출 승인과 같은 방식), 감사에는 승인 id·결정만. 생성 응답은 출력 가드 적용, sampling 은 데이터 등급 라우팅(내부 → 도메인 로컬 모델) |
| mTLS 인증서·클라이언트 키(15th) | **규제대상** | 시크릿 저장소(Fernet, kind=certificate)에만 보관. 키는 컨텍스트 적재 순간만 0600 임시 파일 후 즉시 삭제, API·로그에 평문 없음 |

**규제대상 등급은 local/prod 리허설을 포함한 비운영 환경에 실데이터 반입 금지.** 실제 IdP 사용자·토큰·클라이언트 시크릿은 `.env.local` 에 넣지 않는다.

## 인프라 패턴

`infra_patterns`

- 멀티테넌트 격리(RLS/스키마/DB-per-tenant)
- 관측성(OTel 콜렉터 → 티어드 스토리지, 검증된 삭제 CC6.5/A.8.10)
- 피처플래그, API 게이트웨이, 수평 오토스케일, 시크릿 관리
- 표준 CI/CD

`stack_overrides` (선택 스택과의 차이)

| 항목 | 도메인 권장 | 이 프로젝트 | 비고 |
|---|---|---|---|
| frontend | Next.js(App Router) + TS + Tailwind/shadcn | React 19 + TS + Vite 7 SPA + Tailwind 4(shadcn 스타일) — `web/` | PRD D4: FastAPI 정적 서빙·폰트 로컬 번들(에어갭). SSR 불필요한 관리 콘솔이라 Vite SPA 채택 |
| backend | NestJS 또는 FastAPI, OpenAPI 계약(API-first) | FastAPI | 일치 |
| data | PostgreSQL + Prisma, Redis | PostgreSQL 17 + SQLAlchemy 2/Alembic, Valkey 8 | ORM 만 다름(Python 스택 표준). Redis → Valkey(BSD-3, PRD D6) |
| tenancy | subdomain/row-level(RLS) → schema/DB-per-tenant | 팀 단위 애플리케이션 필터(RLS 대체) | SQLite·PostgreSQL 공통 동작을 위해 DB RLS 대신 쿼리 범위 강제 — 팀 간 누출 시험(4th·9th) |
| observability | OpenTelemetry 티어드 보관, 피처플래그 | OTel(GenAI 규약) → 내장 trace 저장소 + OTLP 수신기, 선택 OTLP 내보내기(Jaeger 등), Prometheus `/metrics` | Langfuse 미채택 → 내장 Usage & Cost·리플레이(PRD D3). 보존 기간은 운영 설정(L06) |

`--extras redis` 로 생성됐던 `compose/redis.yml`(`redis:7.4-alpine`, RSALv2/SSPLv1)은 PRD NFR-LIC1 에 따라 `compose/valkey.yml`(`valkey/valkey:8-alpine`, BSD-3)로 교체했다(PRD D6).

## 개발환경 특수 요구

- 테넌트 격리 테스트(크로스테넌트 누출 단언, RLS 강제)
- 표준 CI/CD(lint/type/test/build/deploy)
- 텔레메트리 보관/삭제 검증(CC6.5/A.8.10), OpenAPI 계약 테스트
- SOC2/ISO 통제 증적 자동화(접근로그·변경관리)

## 추가 시험

`test/dev-env` 및 `test/impl/<Nth>` 케이스로 반영한다.

| 시험 | 구현·위치 |
|---|---|
| 크로스테넌트 데이터 누출 차단 | 팀 단위 범위 강제 — 사용량·승인·실행·A2A Task·대화 조회 누출 0 (`test/impl/4th`·`9th`·`22nd`, `test/test_gateway_policy.py`·`test_observe.py`·`test_chat_editor.py`) |
| OpenAPI 계약 적합성 | 경로·메서드 스냅샷 계약 시험 — 삭제 시 실패, 추가는 명시 갱신 (`test/test_contract.py`, 1st~) |
| 감사로그 보관기간 경과 삭제 증명 | 체인 구간 아카이브(anchor) → 파기 → 파기 증명 이벤트를 체인에 기록 (`test/impl/9th`) |
| 업스트림 mTLS·fail closed (ISMS-P 암호화 증적) | 시험 중 생성한 PKI + 클라이언트 인증서 필수 TLS 서버로 MCP·A2A·LLM 공급자 핸드셰이크, `required` 정책 위반 403 `mtls_required` (`test/test_mtls.py`, `test/impl/15th`) |
| 사람 동의 없는 모델 호출 차단 | sampling·elicitation 거부·60초 무응답 자동 거부가 감사에 남음, 동의 채널이 없으면 거부 · 서버별 샘플링 항상 거부 정책 (`test/test_mcp_consent.py`·`test_ops.py`, `test/impl/14th`·`23rd`) |
| 정책 변경 2인 검토 (직무 분리) | 초안 → 회귀 시험 → 게시 요청(사유) → 작성자 외 승인, 직접 쓰기는 admin 비상 경로(감사 `break_glass`) (`test/test_review.py`, `test/impl/21st`) |
| 접근 통제·접속기록 (안전성 확보조치) | 세션 강제 종료(다음 요청부터 401, 다중 워커) · IdP 중지 시 세션 종료 · 로그인 IP 정책 · 마스킹 해제 사유·감사 · 접속기록 리포트 (`test/test_ops.py`, `test/impl/23rd` P23-02~17) |
| 장애 중 가용성 (NFR-A1) | 일정 부하 중 워커 강제 종료·업스트림 장애·Valkey·DB 재시작 → 구간 가용성·복구 시간, 감사 체인 무결 확인 (`deploy/k6/chaos.js`, `test/impl/25th`) |
| 웹 접근성 (KWCAG 2.2 · WCAG 2.2 AA) | axe-core 자동 점검(콘솔 60 화면 + 채팅·로그인) 위반 0 — 밝은·어두운(명시·시스템) 테마 · 본문 바로가기·포커스·화면 제목 · 시간 연장(10회·만료 20초 전 알림) (`web/e2e/a11y.spec.ts`·`a11y-dark.spec.ts`·`extend.spec.ts`, `test/impl/26th`·`28th`·`30th`) |
| MRTR 상태 무결성 (MCP 2026-07-28) | requestState AEAD — 변조·다른 주체·다른 요청 재사용 거부 (`test/test_mcp_modern.py`, `test/impl/27th`) |
| MCP 헤더·본문 일치 · 구독 인가 (2026-07-28) | `Mcp-Param-*`↔인자(x-mcp-header)·라우팅 헤더 중복 → -32020 (중개자 라우팅 위조 차단) · `subscriptions/listen` 은 매 폴링 토큰 재검증(폐기·만료 시 종료)·주체당 5개 · 감사 `mcp.subscribe` (`test/test_mcp_modern_ext.py`, `test/impl/29th`) |
| 시간 연장 통제 | 요청자·결정 권한자만, 대기·검토 중에만, 10회 상한, 감사 `approval.extend` · 다른 워커의 대기도 DB 기한을 따름 (`test/test_extend.py`, `test/impl/30th` P30-03) |
| MCP 작업 소유권 (Tasks 확장) | taskId 192비트(추측 불가) · 작업은 만든 주체·서버에 묶임 — 남의·만료 작업은 존재도 알리지 않고 -32602 · `tasks/list` 없음 · listen `taskIds` 도 내 작업만 승인 · TTL 1시간 (`test/test_mcp_tasks.py`, `test/impl/32nd` P32-09) |
| NetworkPolicy 집행 (FR-G00 우회 차단) | Calico 클러스터에서 릴리스 라벨 없는 파드 → postgres·참조 MCP·Valkey·api 차단, api → backend 허용 (`test/impl/34th` H34-11·12) |
| 가용성 SLO·오류 예산 (NFR-A1) | 데이터 경로 요청 기준 30일 SLI·예산 잔량·다중 창 소진율 경보 · 목표 변경은 사유·감사 (`test/test_slo.py`, `test/impl/35th` P35-04~07) |
| 실제 클러스터 설치 (NFR-D1) | 전용 kind 클러스터 helm install → 파드 Ready·PVC·NetworkPolicy·`helm test` → 로그인·도구·모델·감사 체인 → 삭제, 시크릿은 클러스터 안 생성(출력·저장 없음), 기본 kubeconfig 무변경 (`test/impl/31st`) |
| 운영형 클러스터 TLS·외부 DB·노드 장애 (NFR-D1·A1·SEC1) | 전용 kind: cert-manager 자체 서명 CA → Ingress TLS(https 로그인·http→https 308) · 외부 PostgreSQL(`database.url`, 내장 끔) · NFS RWX 다중 복제 · 워커 노드 정지 중 가용성 1.0 · 삭제 후 기본 kubeconfig 불변 (`test/impl/38th` H38-01~26) |
| A2A 바인딩 간 통제 동일성 | HTTP+JSON·gRPC 수신이 JSON-RPC 와 같은 인증·신뢰 등급 정책·감사·Task 영속을 거침 — 차단 등급은 REST 403·gRPC PERMISSION_DENIED(게이트웨이 사유) (`test/test_a2a_rest.py`·`test_a2a_grpc.py`, `test/impl/36th`·`37th`) |
| 보안 검토 반영 (OWASP A01·A07·A10 · 직무 분리) | 외부 IdP 비대칭 alg 만 · egress 가드(클라우드 메타데이터·링크 로컬) · 로그인 실패 10회/15분 잠금(감사 `too_many_failures`) · loopback redirect local/test 만 · Access Token 대리 발급 금지 · 디바이스 승인 MFA · 남의 시크릿 참조·전송처 변경은 시크릿 관리 권한 · 인가 서버 고정 · 승인·refresh·code 1회 사용 원자화 · 정책 수정자 승인 금지 · 푸시 Task 결합 · 리포트 서명 발급자 검증 (`test/test_review_fixes.py` C39-01~26, `test/impl/39th` P39-01~10). 검토 원문 `.review/`(위임 폴백·심층·Lv2) |
| 인증 경계 (OWASP A01·A07) | 토큰 교환 grant 는 워크로드 클라이언트 인증 필수·`act`=워크로드 · 사람 주체의 agent 신고 무시 · 데이터 등급 하향 신고는 `data:declare`(회수 시 기본값으로 올림 + 감사 `gateway.data_class.raise`) · 남의 run id 무시 (`test/test_caller_trust.py`, `test/impl/40th` P40-01~06) |
| 자격 증명 저장·세션 (안전성 확보조치 — 비밀번호 일방향 암호화) | scrypt N=2^14·r=8·p=5(OWASP 동급, 이전 해시는 로그인 시 재해싱) · refresh 회전 계열 재사용 탐지 → 계열 폐기 + 감사 `auth.refresh_reuse` (`test/test_policy_hardening.py`, `test/impl/43rd` P43-03~06) |
| 오픈소스 라이선스 고지 (NFR-LIC1) | LICENSE(Apache-2.0)·NOTICE·THIRD_PARTY_LICENSES(`make licenses` — 카피레프트 검토 표: psycopg LGPL-3.0 동적 연결·certifi MPL-2.0 무수정) · 콘솔 번들 법적 주석 보존·폰트 OFL 사본 `/licenses/` (`test/test_supply_chain.py`, `test/impl/42nd`) |
| 공급망 (NFR-SEC2) | 베이스 이미지 digest·uv 버전·`uv sync --locked`·e2e `npm ci` · Dependabot(uv·npm·docker·actions) · 워크플로 액션 커밋 SHA 고정(50th, `test/test_ci_repro.py`) · CI 최소 권한 + audit 잡 · SECURITY.md 비공개 보고 경로 (`test/impl/42nd`) |
| 교차 사용자 Task 격리 (OWASP A01 · 크로스테넌트 누출) | 플랫폼 A2A·게이트웨이 경유 Task — 남의 Task 는 목록에 없고 get·cancel·resubscribe·push·이어 보내기 -32001(존재 비노출), REST 404·gRPC NOT_FOUND, 다른 워커는 영속 주체로 판정 (`test/test_task_ownership.py`, `test/impl/44th` P44-01~07) |
| DB 장애조치 가용성 (NFR-A1) | CloudNativePG 3 인스턴스·존 분산 · primary 노드 정지 → 56초 뒤 새 primary · `/health` 복구 65.5초·180초 창 0.87 · 장애조치 뒤 쓰기 성공 (`test/impl/46th` D46-09~13) |
| 객체 수준 인가 (OWASP API1 BOLA·A01) | 일반 사용자가 닿는 경로 매개변수 라우트 66개를 OWNED(두 사용자 행렬로 검사)·SHARED(조직 공유 자원 — 이유 기재)로 분류 강제 · 남의 세션·실행·메시지·첨부·개인 토큰·A2A Task·승인·동의·MCP 세션·팀 KB 29건 → 403/404 · 주인 객체 무변경 (`test/test_idor_matrix.py`, `test/impl/48th`) |
| 프로토콜 자원 상한·전송 신뢰 (OWASP A04·A05) | gRPC 수신 4 MiB·동시 스트림 100·keepalive · 원격 grpcs 는 에이전트 사설 CA(`auth.tls.ca_secret`) · stdio 허용 목록 전체 명령 일치 · 업스트림 MCP 세션 30분 유휴 TTL·상한 4096 · 푸시 전달 재시도(5xx·429만) (`test/test_lv2_hardening.py`, `test/impl/49th`) |
| 2차 품질 검토 반영 (OWASP A01·A04·A05·A07·A10 · 사람 승인·직무 분리) | 승인·동의는 사람 주체만(기계 주체 자기 승인 차단) · 토큰으로 토큰 발급 금지·PAT 최대 90일·다른 팀 키는 운영자 · MFA 실패 잠금·IP 정책 전 로그인 경로 · 데이터 등급 모르는 값 거부·모델 허용 등급·한도 폴백 검사 · 자격 증명 전송처(인가 서버·토큰 엔드포인트·mode) 변경은 시크릿 관리 권한 · 등록 승인 우회 차단 · 푸시 전달 직전 재검사·푸시/폴링 소독·비활성 주체 폴링 중단 · 보안 응답 헤더 · Helm 데모 시드·NetworkPolicy 내부 호출자 (`test/test_review2_fixes.py`, `.review/round2/VERIFY.md`, `test/impl/51st`) |
| 신원·위임 경계 (OWASP A01·A07 · 권한 상승) | Access Token audience = 게이트웨이·등록된 `mcp:`/`a2a:` 대상만 · admin 부여·IdP admin 매핑·관리자 계정 수정은 admin 만 · IdP 삭제 → 그 사용자 세션 종료 · TOTP 재사용 거부 · push_verify 변경은 시크릿 관리 권한 (`test/test_identity_boundaries.py`, `test/impl/53rd`) |
| 공급망·배포 무결성 (NFR-SEC2·D1) | 반입 번들 서명 검증 기본(매니페스트 서명) · 서명 검증 이미지 그대로 기동 · 레지스트리 새 버전 소유자·미러 자동 승인 보류 · e2e 베이스 digest·잠금 파일 필수 · 엣지 a2a-grpc 에 시크릿 없는 env (`test/test_deploy_hardening.py`, `test/impl/54th`) |
| FinOps 예산 통제 (K) | 진행 중 요청 예약으로 동시 초과 방지 · `any` 예산 팀 없는 주체 · 클라이언트 키 예산 (`test/test_model_gateway2.py`, `test/impl/52nd`) |
| 암호키 관리·회전 (안전성 확보조치 — 암호키 관리 · ISMS-P 2.7.2) | 서명·암호화 키 관리 명령 회전(관리자·`ROTATE` 확인·감사) · 직전 서명 키 유예·공개키 보관(백업·보고서 서명 장기 검증) · 저장 암호문 즉시 재암호화(웹훅 토큰 포함·500행 배치)·이전 키 제거 · 여러 워커 즉시 반영 · env 주입 키는 `*_PREVIOUS` 절차(SECURITY.md) · 키 파일 0600 · 백업 서명은 발급자까지 검증 (`test/test_key_rotation.py`·`test_review3_fixes.py`, `test/impl/58th`·`59th`) |
| 원격 에이전트·푸시 무결성 (OWASP A04·A08) | 푸시 JWT 발급자·`iat`·`jti`(재전송 거부)·본문 해시 필수 · 원격 응답·SSE 줄 8 MiB 상한·압축 응답 거부 · 인증 없는 푸시 수신 상한 · 카드 URL 은 `PUBLIC_BASE_URL` · 웹훅 토큰 암호화 저장 (`test/test_a2a_boundaries.py`·`test_remaining_items.py`·`test_review3_fixes.py`, `test/impl/56th`·`60th`·`59th`) |
| 3차 품질 검토 반영 (OWASP A01·A04·A07·A08 · FinOps) | admin 매핑 IdP 신뢰 설정·admin 부여는 admin(역할 + `admin:write` 스코프) · 스트림 끊김도 사용 기록·정산, 예산 예약 예약별 고정 만료 · 한도 폴백 모델 없으면 429 · 번들 검증 키는 번들 밖·설치는 받은 다이제스트 검증 · 미러는 고정 안 된 oauth2 보류·다른 출처 항목 보호 · TOTP 재사용 정규화 · 실행 재개는 Host 만 (`test/test_review3_fixes.py`, `test/impl/59th`, `.review/round3/VERIFY.md`) |
| 가용성·자원 상한 (NFR-A1 · OWASP A04) | PostgreSQL 문장 2분·잠금 15초 세션 상한(대량 정리·감사 아카이브·마이그레이션은 해제) · 주체별 동시 모델 요청 상한·출력 예약 상한·`max_tokens` 검증 · 익명 발견 API IP 별·전체 한도 · Task artifacts 상한 · gRPC readiness 결과 공유 (`test/test_data_layer.py`·`test_limit_precision.py`·`test_boundaries4.py`·`test_review4_fixes.py`, `test/impl/61st`·`63rd`·`64th`·`65th`) |
| 암호키 관리 2 (안전성 확보조치 — 암호키 관리 · ISMS-P 2.7.2) | env 2단계 회전(다음 키 먼저 배포 — 롤링 중 401·복호화 실패 없음)·이전 키 검증 끝·폐기 kid(현재 키 폐기는 기동 거부)·회전 잠금·generation 카운터·회전 감사 선기록·1단계 파드 재암호화 거부·키 지문 (`test/test_key_mgmt.py`·`test_review4_fixes.py`, `test/impl/62nd`·`65th`) |
| 4차 품질 검토 반영 (OWASP A01·A04·A05·A07) | 대상용 Access Token 스코프 = 교환 정책 안(사용자 결정)·빈 스코프는 정책 기본 · IdP 중지·삭제 시 그 사용자 세션·PAT·클라이언트 키 폐기·내장 issuer 연동 거부 · push_verify 등록·사용 시점 검사(운영 https) · 실패·취소 경로 예약 반환 · 서명된 매니페스트의 백업만 복원 (`test/test_review4_fixes.py`, `test/impl/65th`, `.review/round4/VERIFY.md`) |
| 백업 무결성·복구 검증 | 서명 번들 · 격리(임시 DB) 복구 시험 · 변조 감지 · 복원 RESTORE 확인·직전 스냅샷 (`test/test_ops.py`·`test_admin.py`, `test/impl/23rd` P23-37~39) |

> PRD 매핑 메모: FR-O05 는 불변 감사 로그(해시 체인)를 요구하므로 "보관기간 경과 삭제"와 충돌한다. → 확정·구현(9th): 보관기간 만료 시
> 체인 구간 단위로 아카이브(서명 anchor)·파기하고 파기 이벤트 자체를 체인에 남긴다. 검증은 anchor 이후 구간부터 이어진다.
>
> 비운영 데이터 메모: local/test 시드 계정은 `APP_ENV=local|test` 에서만 생성된다. prod 리허설(13th·16th~23rd)은 실행 중 생성한 임시 비밀번호의
> 전용 `rehearsal`(·`rehearsal-sec`·`rehearsal-dev`·`rehearsal-lock`) 계정을 쓰고 종료 시 비활성화한다 — 실제 IdP 사용자·토큰·시크릿은 반입하지 않았다.
> 리허설이 바꾼 설정(RBAC·IP 정책·시간대·샘플링·알림 음소거)은 원래 값으로 되돌린다.

## 위협 매핑 — OWASP Agentic AI Threats and Mitigations (NFR-SEC2)

PRD 부록 B 가 지정한 OWASP GenAI 「Agentic AI — Threats and Mitigations」 T1~T15 기준. D12(Epic 17~23)로 보강을 마쳤다(2026-10-07 재검토).

| 위협 | AgentNexus 통제 | 근거(코드·시험) |
|---|---|---|
| T1 Memory Poisoning | 도구·원격 에이전트 응답을 소독한 뒤에만 Summarize·세션 이력에 넣음(간접 인젝션 지시문 제거·시크릿 마스킹) · RAG 색인 입력도 같은 소독 후 저장·임베딩 | `services/guards.py` sanitize_tool_text · A2A 응답 `_sanitize_in` · `services/knowledge.py` add_document · `test_gateway_policy.py` · `test_ops.py` C23-14 |
| T2 Tool Misuse | 도구 허용목록 기본 거부 · 인자 JSON Schema · 고위험 사람 승인 · 도구 단위 레이트리밋 | `services/governance.py` · `mcp_gateway.call_tool` · `test_mcp.py` (미등록 도구·고위험 승인·도구 한도) |
| T3 Privilege Compromise | RFC 8693 도구별 최소 스코프 교환 · 워크로드·Access Token 스코프 강제 · RBAC 6역할 · 승인 직무 분리 | `services/identity.py` · `core/auth.py` Principal.has · `test_identity.py` · `test_gateway_policy.py` |
| T4 Resource Overload | 주체·팀·모델·도구·에이전트 요청/토큰/비용 한도(429·저비용 폴백) · 업스트림 타임아웃 · MCP 구독 주체당 5개 · MCP 작업 TTL 1시간 | `services/ratelimit.py` · `mcp_modern.MAX_LISTENS` · `mcp_tasks.TTL_MS` · `test_gateway_policy.py` |
| T5 Cascading Hallucination | Summarize 근거(sources) 표시·근거 요약(MSG-025) · sampling 응답 사람 검토 후 전송 · 실패·검색 0건·거부 시 재계획 · 인용 필수(없으면 1회 재생성, 그래도 없으면 '근거 없음') | `services/orchestrator.py` · `services/mcp_consent.py` · `test_orchestration.py` · `test_chat_editor.py` |
| T6 Intent Breaking & Goal Manipulation | 입력 인젝션 탐지(`injection_input` flag·block) · 간접 인젝션 소독 · 정책 판정은 모델 밖(게이트웨이) | `guards.scan_input` · `services/policy.py` |
| T7 Misaligned & Deceptive Behaviors | 쓰기·고위험 도구 사람 승인(맥락·영향·정책 근거) · 계획·단계 이벤트 공개 · 테스트 실행은 쓰기 도구 드라이런 기본 · 게시 전 점검 | `governance.create_approval`·`approval_context` · 채팅 승인 카드(C04) · `test_chat_editor.py` |
| T8 Repudiation & Untraceability | 해시 체인 불변 감사 · 단일 trace_id(게이트웨이→MCP→원격 에이전트) · 서명 리포트 · 리플레이 | `services/audit.py` · `test_observe.py` · prod 게이트 P13-06·07 |
| T9 Identity Spoofing & Impersonation | OIDC/JWKS 서명 검증 · 신뢰 발급자만 · 워크로드 ID(client_credentials) · Agent Card 검증 · 업스트림 mTLS | `core/auth.py` · `services/tls.py` · `test_mtls.py` |
| T10 Overwhelming Human in the Loop | 위험도별 승인(저위험 자동) · 승인 만료·자동 거부 · 승인 대기 경보 · 동의 60초 자동 거부 · 사람이 서두르지 않도록 기한 연장(본인·결정 권한자만 10회, 감사) | `governance.APPROVAL_TTL`·`extend` · `MCP_CONSENT_TIMEOUT_S` · 알림 규칙 `approvals_pending` |
| T11 Unexpected RCE and Code Attacks | stdio MCP 명령 허용목록(비우면 금지) · 컨테이너 읽기 전용 루트 FS · 플랫폼에 임의 코드 실행 도구 없음 | `MCP_STDIO_ALLOWED_COMMANDS` · prod 게이트 P13-11 |
| T12 Agent Communication Poisoning | A2A 송신 인젝션 차단·PII 마스킹, 수신 소독 · Agent Card 검증·캐시 · 원격 에이전트 신뢰 등급 · 푸시 알림 JWT 서명 검증(JWKS·±5분·본문 해시) | `a2a_gateway._guard_out`·`_sanitize_in`·`verify_push_jwt` · `test_a2a.py`·`test_design_gaps.py` |
| T13 Rogue Agents | 원격 에이전트 허용목록 기본 거부 · 개발자 등록은 승인 대기(MSG-008)·신뢰 등급·사유 · 비활성화 즉시 차단 · 헬스 감시 · Agent Card 재검증 이력 | `governance.check_agent` · `api/a2a.py` 등록·`/checks` · `test_review.py` |
| T14 Human Attacks on Multi-Agent Systems | 호출 에이전트(caller) 단위 정책·한도 · 위임 경로 감사 | `governance.check_agent(caller_agent)` · `ratelimit.check(agent)` |
| T15 Human Manipulation | 동의 카드에 요청 서버·모델 라우팅·데이터 등급 표시 · 출력 PII 마스킹 · 응답 소독 | `pages/chat/Consent.tsx` · `guards.scan_output` |

## 개인정보보호법 매핑 (NFR-PRV1)

플랫폼이 제공하는 기술 통제만 적는다. 처리방침·동의서·유출 신고 절차는 운영 조직의 책임이다.

| 조항 | 요구 요지 | AgentNexus 통제 | 근거 |
|---|---|---|---|
| 제3조 (처리 원칙) | 목적 범위 최소 처리 | 저장 전 PII 마스킹(로그·trace 스팬·대화) · 도구별 최소 스코프 | `core/logs.py` · `core/tracing.py` mask · `guards.mask_pii` |
| 제17조·제26조·제28조의8 (제3자 제공·처리 위탁·국외 이전) | 외부 제공·위탁·국외 이전 통제 | 데이터 등급 라우팅(내부 데이터 → 로컬 모델) · 상용 공급자 허용 등급 정책 · 외부 A2A 송신 PII 마스킹 | `services/llm.py` · `services/policy.py` · `a2a_gateway._guard_out` |
| 제21조 (파기) | 보유 기간 경과 시 지체 없이 파기 | 보존 기간 만료 구간 아카이브·파기·파기 증명 이벤트 · 대화 삭제(내용 제거·메타만 감사 보존) · 예약 실행(스케줄러 리스, `RETENTION_INTERVAL_S`) | `test/impl/9th` · `DELETE /api/v1/sessions/{id}` · `services/scheduler.py` |
| 제23조·제24조·제24조의2 (민감정보·고유식별정보·주민등록번호) | 별도 근거 없는 처리 제한 | 주민·외국인등록번호·카드번호 패턴 마스킹, `pii_input=block` 으로 입력 차단 | `guards.PII_PATTERNS` · H05 Security |
| 제29조 (안전조치의무) · 안전성 확보조치 기준 | 접근통제·접속기록·암호화 | RBAC(역할·권한 행렬, 변경 사유 필수)·외부 IdP SSO·TOTP MFA · 세션 강제 종료·로그인 IP 허용목록·토큰 수명 상한 · 해시 체인 접속·처리 기록 · 시크릿 Fernet·mTLS · 로그 마스킹 해제는 `audit:read`·사유·30분·감사(원문 암호화 보관, 시크릿은 계속 가림) · 접속기록 점검 리포트(K10 `pipa_access`) | `core/auth.py` · `api/ops.py` · `services/audit.py` · `services/secrets.py` · `test_ops.py` C23-01~03·08 |
| 제34조 (유출 등의 통지·신고) | 유출 인지 시 통지·신고 | 감사·알림 센터·경보 규칙으로 인지 지원(통지·신고 자체는 운영 절차) | `services/notify.py` · `observe/alerts` |
| 제35조~제37조 (열람·정정·삭제·처리정지) | 정보주체 권리 보장 | 대화 이력 내보내기·삭제(C07) · 사용자 비활성화 | `pages/chat/Delegation.tsx` ChatHistory · J03 |
| 제37조의2 (자동화된 결정, 2024-03-15 시행) | 설명·거부·검토 요구권 | 고위험 실행 사람 승인 · 정책 결정 로그·trace 로 결정 근거 설명 | H04 라우팅 결정 로그 · H12 승인 · K03 trace |

## 출처

- https://genai.owasp.org (OWASP Agentic AI — Threats and Mitigations, T1~T15)
- https://www.law.go.kr (개인정보 보호법 — 제29조·제34조·제37조의2 등)

- https://isms-p.or.kr/sysm/intro/selectSysmCertDetail.do (ISMS-P)
- https://isms.kisa.or.kr/main/csap/notice/ (CSAP 2025)
- https://secureframe.com/blog/soc-2-vs-iso-27001
- https://oneuptime.com/blog/post/2026-02-06-telemetry-retention-soc2-iso27001/view
- https://www.ideadope.com/roadmaps/how-to-build-multi-tenant-saas-2025
