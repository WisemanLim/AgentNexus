# 2차 검토 지적 검증·처리 / Verification of round-2 findings (Epic 51 · D19-④)

> 일자 2026-10-07 · 검증자 Claude (implement 단계) · 원문: `delegated/security-review.md`, `delegated/code-review.md`, `CODE-REVIEW-Lv2/`
> 규칙: 실제 코드 경로를 따라가 확인한 결함만 고친다(`test/impl/51st`). 고치지 않는 항목은 근거를 남긴다.
> 판정: **확인**(코드로 재현 경로 확인 → 수정) · **부분**(일부만 성립) · **기존 결정**(이전 차수에 근거와 함께 결정, 바뀐 사실 없음) · **기각**(성립하지 않음·효과 없음)

## 보안 (security-review 폴백)

| ID | 판정 | 근거 | 처리 |
|----|------|------|------|
| SEC2-01 hosted-openapi egress | **부분 → 수정** | 지적한 시나리오(`servers[].url` 로 다른 호스트)는 성립하지 않는다 — FastMCP 는 경로를 클라이언트 `base_url` 아래로 붙인다. 그러나 `app/services/mcp_gateway.py` `_hosted_server` 의 httpx2 클라이언트는 팩토리 밖이라 요청 단위 가드가 없고, `blocked_url(base)` 는 생성 때 1회뿐이다. 클라이언트는 설정이 바뀔 때까지 캐시되므로 등록 뒤 이름이 메타데이터 주소로 바뀌면 막지 못한다 | 요청마다 egress 가드 훅 (C51-01) |
| SEC2-02 DNS 재바인딩 TOCTOU | 기존 결정 | 39th 에서 문서화한 한계 — "사설망 업스트림은 허용 — DNS 재바인딩은 egress NetworkPolicy 로"(README 제한 사항). 연결 시점 IP 검사는 전송 계층 교체가 필요한 설계 변경 | 없음 |
| SEC2-03 discover·jwks_uri | 기존 결정 | 39th SEC-09: 같은 origin 강제는 Google(issuer `accounts.google.com`, jwks `www.googleapis.com`)을 깨뜨린다. `internal_url` 은 split DNS 용 관리자(identity:write) 입력 — 메타데이터는 egress 가드가 막음 | 없음 |
| SEC2-04 scrypt 파라미터 하한 | 기각 | DB 쓰기 권한이 있으면 강한 파라미터로 아는 비밀번호의 해시를 넣을 수 있다 — 하한 검사는 막는 것이 없다 | 없음 |
| SEC2-05 보안 응답 헤더 | **확인 → 수정** | 저장소 전체에 `X-Frame-Options`·`frame-ancestors`·`nosniff` 없음(grep). 내장 IdP 로그인 화면(`/idp/authorize`)을 다른 사이트가 iframe 으로 감쌀 수 있다(클릭재킹). 콘솔은 iframe 을 쓰지 않는다 | 순수 ASGI 미들웨어 — `X-Frame-Options: DENY`·`CSP: frame-ancestors 'none'`·`nosniff`·`Referrer-Policy: no-referrer` (C51-02). HSTS 는 TLS 를 끝내는 Ingress 몫 |
| SEC2-06 LIKE 와일드카드 | 기존 결정 | 39th SEC-08: 결과는 이미 권한으로 걸러진 범위 — 노출 증가 없음(빈 검색어도 전체) | 없음 |
| SEC2-07 로그인 CSRF | 기존 결정 | 39th SEC-06: 공격자 로그인으로 생긴 code 는 공격자 `code_challenge` 에 묶여 콘솔이 교환하지 못함 + `state` 확인 | 없음 |
| SEC2-08 redirect_uri 사전 등록 | 기존 결정 | 39th SEC-12: 같은 origin 검증 + 공개 클라이언트 PKCE. 클라이언트 등록 모델은 기능 추가 | 없음 |

## 결함 (code-review 폴백)

| ID | 판정 | 근거 | 처리 |
|----|------|------|------|
| CR2-01 푸시 URL 검사의 동기 DNS | **확인 → 수정** | `a2a_gateway._proxy`(async)와 `A2AServer.dispatch`(async)가 `push_url_problem` → `http.internal_url` → `socket.getaddrinfo` 를 이벤트 루프에서 직접 부른다. 운영 모드(local·test 밖)에서만 해석하므로 시험에서 드러나지 않았다. 느린 DNS 동안 그 워커의 모든 요청이 멈춘다 | 두 곳 모두 스레드풀로 (C51-03) |
| CR2-02 `_evict` 가 끝난 Task 만 | 기각(설계) | 진행 중 Task 를 메모리에서 내리면 실행기(`runners`)·소유자(`owners`)·푸시 설정이 끊겨 실행이 깨진다. 진행 중 Task 수는 주체별 요청 한도와 실행 시간으로 묶이고, 끝나면 1000 상한으로 정리된다 | 없음(제한 사항으로 기록) |
| CR2-03 Redis `counter_add` 음수 보정 경합 | 기각(영향 미미) | 키가 TTL 로 사라진 뒤 반납이 오고, 그 마이크로초 사이 다른 워커가 획득할 때만 — 결과는 구독 1개 여유. 카운터는 TTL 로 스스로 정리된다 | 없음 |
| CR2-04 `_UPSTREAM_SEEN` 정리 누락 | **확인 → 수정** | `_UPSTREAM_SESSIONS.pop` 5곳 중 어디서도 `_UPSTREAM_SEEN` 을 지우지 않는다 — 상한 정리 때 정렬 대상만 커진다 | `_drop_upstream` 한 곳에서 둘 다 (C51-05) |

## 라인 리뷰 Lv2 — 인프라 (INFRA.md, 리뷰어가 직접 확인한 항목)

| # | 판정 | 근거 | 처리 |
|---|------|------|------|
| 1 NetworkPolicy 내부 호출자 | **확인 → 수정** | `ingressFrom` 을 지정하면 api 수신 출처는 그 목록 + a2a-grpc 뿐 — 오케스트레이터의 api→api(`GATEWAY_URL`)·참조 MCP 의 JWKS·데모 A2A 의 토큰 요청이 막힌다. 41st 는 값으로 직접 넣어 피했다(`test/impl/41st/cluster-grpc.sh` 주석 "Ingress 만 열면 데모 에이전트·시험 클라이언트도 막힌다") | 같은 릴리스의 api·mcp-servers·a2a-demo·a2a-grpc 를 차트가 넣음 + `check-rendered.py` 불변식 (C51-06). 메트릭 수집 출처는 `ingressFrom` 에 모니터링 네임스페이스를 넣는다(문서) |
| 2 `--forwarded-allow-ips "*"` | 부분 → 설정화 | 모든 파드가 api 에 닿는 기본값(`ingressFrom: []`)에서는 다른 파드가 `X-Forwarded-For` 로 IP 허용 목록·감사 IP 를 속일 수 있다. 기본값을 좁히면 Ingress 뒤의 실제 클라이언트 IP 판정이 깨지므로(모두 컨트롤러 IP) 기본값은 유지 | `api.forwardedAllowIps` 값 + 운영 권고(Ingress 컨트롤러 CIDR) (C51-06) |
| 3 Helm `SEED_DEMO` 없음 | **확인 → 수정** | `configmap.yaml` 에 `SEED_DEMO` 가 없어 `demo.enabled: false`(에어갭)에서도 참조 MCP·trusted 데모 A2A·`a2a-demo` 워크로드·KB 를 시드(`app/seed.py`) | `SEED_DEMO: demo.enabled` + 불변식 (C51-06) |
| 4 푸시 콜백이 내부 주소 | **확인 → 수정** | `a2a_gateway._proxy` 가 원격 에이전트에 넘기는 콜백이 `GATEWAY_URL`(Helm `http://<fn>-api:8000`) — 클러스터 밖 에이전트는 닿지 못해 푸시가 오지 않는다. 데모(클러스터 안)라 시험에서 가려졌다 | 사설망 에이전트는 `GATEWAY_URL`, 그 밖은 `PUBLIC_BASE_URL` (C51-04) |
| 5 인프라 기타(Low) | 기록 | a2a-grpc 준비 프로브 TCP·PDB 없음·Secret 변경 시 재기동 없음·e2e 이미지 digest·CI e2e 없음 등 — 운영 개선 항목 | 없음(남은 개선 목록) |
| 6 `httpx2` 직접 의존 미선언 | 기록 | 1차와 같음 — fastmcp 경유 설치. 버전 고정은 `uv.lock` | 없음 |
| 6b `test/test_chat_editor_old.py` 빈 파일 | 정리 | 50th 부하 A/B 때 scratch 사본을 바인드 마운트하며 Docker 가 만든 빈 자리 파일 | 삭제함 |

## 라인 리뷰 Lv2 — 파일별 보고서 (`CODE-REVIEW-Lv2/app/**`)

> [위험:높음]·[위험:중간] 만 검증 대상으로 올렸다. [위험:낮음]은 보고서에 남기고 이번 차수 수정 범위에서 뺐다.

| 파일 · 지적 | 판정 | 근거 | 처리 |
|-------------|------|------|------|
| a2a_gateway · 푸시 URL 검사가 등록 때뿐(전달 직전 재검사 없음) | **확인 → 수정** | `_relay_push` 가 `post_with_retry`(기본 가드 = 메타데이터만)로 바로 보낸다. 등록 뒤 이름이 사설 주소로 바뀌거나 등록 때 해석 실패(통과)한 이름이면 내부망 POST | 전달 직전 `push_url_problem` 재검사 (C51-03·09 와 같은 경로) |
| a2a_gateway · 푸시·폴링 결과가 소독을 건너뜀 | **확인 → 수정** | 동기 응답은 `_sanitize_in`(지시문 제거·시크릿·PII 마스킹)을 거치지만 `receive_push`·`poll_once` 는 원문을 `record` — 저장된 Task 가 실행·첨부로 이어진다(간접 인젝션 경로) | 둘 다 같은 소독 (C51-07) |
| a2a_gateway · 감시기가 비활성 사용자·차단 에이전트에도 위임 토큰으로 폴링 · 차단 에이전트 푸시 수락 | **확인 → 수정** | `_principal_for` 가 `User.status` 를 보지 않음, `poll_once`·`receive_push` 가 에이전트 상태·`trust_tier` 를 보지 않음(호출 경로는 `governance.check_agent` 가 거부) | 비활성 사용자 → 위임 주체 없음 · 비활성·blocked 에이전트 → 폴링 건너뜀·푸시 거부(감사 `agent_inactive`) (C51-07) |
| a2a_server · `push_check` 동기 DNS | 확인 → 수정 | CR2-01 과 같은 지점 | C51-03 |
| a2a_server · 플랫폼 푸시 전달 재검사 없음 | 확인 → 수정 | 게이트웨이와 같은 TOCTOU | `_push` 전달 직전 `push_check` (C51-09) |
| mcp_gateway · 인가 서버·토큰 엔드포인트 변경이 전송처 통제 밖 [높음] | **확인 → 수정** | `check_destination` 은 `endpoint` 변경만 본다(`app/api/mcp.py` PATCH·test-config). `mcp:write`(개발자)가 `auth.token_endpoint` 를 바꾸면 저장된 서비스 시크릿이, `authorization_server` 를 외부로 바꾸면 사용자 플랫폼 토큰(`subject_token`)이 그 주소로 간다 | 전송처 키(`token_endpoint`·`authorization_server`·`resource_metadata`·`token_url`) 변경도 변경으로 · 외부 인가 서버 위임 교환은 새 시크릿과 무관하게 `gateway:write` 필요 (C51-08) |
| mcp_gateway · 등록 때 외부 AS 허용 목록 없음 [높음] | 기존 결정 | 서버 등록은 카탈로그 승인(운영자)을 거친다. 등록 뒤 변경은 위 수정으로 통제, 업스트림이 바꾸는 경우는 39th `_pinned` | 없음 |
| mcp_gateway · 401 challenge 의 메타데이터 URL 선조회 · resources/prompts 통제 · stdio 단일 항목 | 기록 | 메타데이터 주소는 egress 가드, stdio 단일 항목은 문서화한 의미(실행 파일 이름) | 없음 |
| mcp_consent · 중계 경로가 서버 샘플링 deny 정책을 건너뜀 | **확인 → 수정** | 클라이언트가 sampling 을 지원하면 `relay.request` 로 바로 — `_sampling_policy` 는 게이트웨이 동의 경로에서만 | 중계 전에도 정책 (C51-10) |
| main · `_health_loop` 가 DB 오류 한 번에 종료 · 리스 예외 fail-open | **확인 → 수정** | 서버 목록 조회가 가드 밖 — 장애조치 중 모든 워커가 같은 오류를 맞으면 헬스 점검이 재기동 전까지 멈춘다. 리스 예외는 `suppress` 로 삼켜 점검을 진행 | 주기 단위 예외 처리 + 리스 예외 시 쉼, 조회는 스레드풀 (C51-10) |
| main · gateway-only 단독 배포 스케줄러 미기동 · TrustedHost | 기록 | 구성요소 조합 설계(스케줄러 = host·observe). 보안 헤더는 SEC2-05 로 수정 | 없음 |
| llm · 한도 폴백 모델이 정책·허용 등급·에어갭 검사를 건너뜀 [높음] | **확인 → 수정** | `prepare` 끝의 `lim.fallback_model` 은 `_target` 만 보고 맨 앞에 넣는다 — internal·confidential 요청이 예산 초과 시 상용 폴백으로 갈 수 있다 | 폴백도 같은 검사, 막히면 429(예산 통제 유지) (C51-11) |
| llm · 모델 `data_classes` 저장·표시만 | **확인 → 수정** | `Model.data_classes` 는 API 로 쓰고 콘솔에 "허용 데이터 등급" 으로 보이지만 라우팅이 읽지 않는다(공급자 허용 등급만 적용) | 모델·공급자 허용 등급 둘 다 (`_blocked`) (C51-11) |
| policy · 모르는 데이터 등급 문자열 통과(MCP·A2A) | **확인 → 수정** | `declared_data_class` 는 형식 오류를 그대로 돌려 호출부가 판단 — Unified Model API 만 400, MCP·A2A 는 그대로 정책에 넣어 등급 조건이 맞지 않는다 | 대소문자 정규화 + MCP(-32602)·A2A(-32602, HTTP 400)도 거부 (C51-13) |
| policy · user 주체를 표시 이름으로도 매치 | **확인 → 수정** | 외부 IdP 사용자의 `name` 은 로그인 때 클레임으로 덮인다(`app/core/auth.py` `user.name = claims.get("name")`) — 외부에서 정한 이름으로 그 정책을 얻거나 피한다. 매치를 아예 없애면 내장 사용자 이름으로 쓴 기존 deny 정책이 풀린다 | 이름 매치는 내장 IdP 발급자 사용자만, sub 매치는 그대로 (C51-15) |
| http · Alibaba Cloud IMDS(100.100.100.200) 미차단 | **확인 → 수정** | `_BLOCKED_NETS` 에 없다 — egress 가드의 목적(클라우드 메타데이터) 범위 | 차단 목록에 추가 (C51-09). 100.64/10(CGNAT) 은 사내망에 쓰일 수 있어 그대로 |
| ratelimit · 증감 카운터 TTL 이 변경마다 연장(죽은 워커 몫이 활동 중 안 풀림) | 기록 | 워커가 finally 없이 죽을 때만, 그 주체가 TTL(960초) 동안 조용하면 풀린다 — 구독 한도가 일시적으로 줄 뿐 | 없음(제한 사항) |
| ratelimit · `any` 예산이 팀 없는 주체에 미적용 | 기록 | `any` 예산은 팀 단위 합계로 정의(`st = "team"`) — 팀 없는 주체는 사용자 예산으로 묶는다 | 없음 |
| observe · `_overview_extra` 전체 스캔·`usage` 메모리 집계 | 기록 | 성능 — 47th 범위 밖 경로 | 다음 성능 정리 후보 |
| config · `app_env` 기본 local fail-open | 기존 결정 | 1차와 같음 — 배포 산출물(.env.prod·Helm)이 prod 를 고정 | 없음 |
| cli/nexus · bundle import 서명 검증 기본 꺼짐 · cosign 뒤 `--build` | 기록 | 에어갭 번들 운영 옵션 — 검증 기본값 전환은 cosign 미설치 환경을 깬다 | 다음 공급망 정리 후보 |
| a2a_grpc · `grpc://` 평문 기본 · egress 가드 미적용 | 기록 | 스킴은 운영자가 등록한 카드가 정한다(HTTP 바인딩의 `http://` 와 같은 규칙). 메타데이터 주소 gRPC 는 [낮음] | 없음 |
| policy · `caller_context` 의 동기 DB·감사가 이벤트 루프에서 (그룹 리뷰어 직접 확인) | **확인 → 수정** | `x-agentnexus-run` 헤더 하나로 요청마다 Run 조회, 기본 등급 캐시 갱신·하향 신고 감사가 async 핸들러(`api/mcp`·`api/a2a`)에서 동기 실행 | `_ctx` 를 스레드풀로 (MCP 3곳·A2A 5곳) |
| api/mcp · 전송처 검사가 `endpoint` 만 [높음] | 확인 → 수정 | mcp_gateway 행과 같은 결함의 API 쪽 — `mode`(서비스 → 위임 교환)도 사용자 토큰의 행선지를 바꾼다 | C51-08 + `mode` 를 전송처 키에 |
| api/mcp · Inspector 가상 주체 임의 팀·`fetch_openapi` 사설망·다운로드 상한 | 기록 | Inspector 는 `mcp:write` 시험 도구(드라이런), OpenAPI 가져오기 주소는 egress 가드 범위(사설망 업스트림 허용이 제품 결정) | 없음 |
| api/a2a · PATCH `status=active` 로 등록 승인 우회 | **확인 → 수정** | 개발자 등록은 `pending` 이지만 `AgentPatch.status` 가 `active` 를 받아 `a2a:write` 만으로 활성화 — 운영자 승인(E05·MSG-008) 우회 | `pending → active` 는 `catalog:approve` 만 (C51-13) |
| api/a2a · 인증 설정 잔여 전송처(`token_url` 외) | 확인 → 수정 | `token_url` 만 비교 | `destination_changed`(인가 서버·토큰 엔드포인트·메타데이터·mode 포함)로 통일 |
| api/a2a · developer 가 모든 사용자 Task 이력 열람 · 카드 조회 사설망 | 기록 | 개발자 역할의 운영 조회 범위(관측·문제 해결)는 역할 설계 결정 — 범위를 줄이려면 RBAC 재정의(SCR-L03)로 `a2a:read` 회수 가능. 카드 주소는 egress 가드 범위 | 없음 |
| gateway_admin · 승인 엔드포인트가 기계 주체의 자기 승인을 허용 [높음] | **확인 → 수정** | `governance.can_decide` 는 `approver="requester"`(새 도구 규칙 기본값)이면 `p.sub == requester_sub` 만 본다 — 워크로드·Access Token·위임 토큰이 자기 고위험 호출을 API 로 승인해 사람 승인(HITL)을 무력화 | 승인·동의 결정은 사람 주체(`user`·`pat`)만 (C51-13) |
| gateway_admin · 단독 롤백 | 기존 결정 | 설계서 H08 ⑥ "승인됐던 이전 버전으로 즉시 복원"(확인·사유·감사·새 버전 기록) | 없음 |
| governance · 새 업스트림 도구 자동 허용·이름 휴리스틱 · 신뢰 어휘 · 세션 기억·위임 | 기록 | 도구 규칙 자동 생성은 위험도 분류 + 고위험 승인(FR-G03 설계). 세션 기억은 중위험·승인자가 고른 경우만(고위험 불가), 위임은 SCR-H12 | 없음(분류 휴리스틱 보강은 다음 후보) |
| retention · overrides 무검증(감사 최소 1년 우회) | 기존 결정 | 실행은 `audit:read`+`policy:write`, 감사는 삭제가 아니라 아카이브(해시 체인 증명 유지)이고 파기는 `admin` 별도 단계 — 설정 화면은 365일 하한(`app/api/setup.py`) | 없음 |
| api/idp · `async def token` 안의 동기 scrypt·DB | **확인 → 수정** | 인증 없는 `/idp/token` 이 워크로드 시크릿 scrypt(p=5)·DB·JWKS 를 이벤트 루프에서 — 요청이 몰리면 같은 워커의 모든 요청이 멈춘다 | grant 처리를 스레드풀로 (C51-12) |
| api/idp · 로그인 IP 정책이 비밀번호 폼에만 | **확인 → 수정** | 디바이스 승인(`/idp/device`)·외부 IdP 콜백(`/idp/broker/callback`)은 IP 정책 없이 로그인 완료 | 세 경로 모두 `_ip_ok` (C51-12) |
| api/idp · MFA 실패 무제한 | **확인 → 수정** | 비밀번호가 맞으면 실패로 세지 않아 TOTP 를 제한 없이 대입 | MFA 실패도 같은 잠금 창 (폼·디바이스·password grant) (C51-12) |
| api/idp · 사용자 이름 고정 창 잠금(표적 잠금 DoS) · TOTP 재사용 | 기존 결정 | 39th 결정(사용자 이름별 10회/15분) — 잠금 DoS 와 대입 방어의 교환. 재사용 창은 30초 | 없음 |
| api/identity · 토큰으로 토큰 발급(스코프 PAT·클라이언트 키 → 무제한 PAT) [높음] | **확인 → 수정** | `create_my_token` 은 `user_id` 만 확인 — `catalog:read` 스코프 PAT 가 스코프 없는(역할 전체) 새 PAT 를 만들어 승격 | 로그인 세션(`kind=user`)에서만 발급 (C51-14) |
| api/identity · 클라이언트 키 팀 자유 지정 · `ttl_days: null` 무기한 | **확인 → 수정** | 팀은 KB·정책·한도 경계인데 본인이 아무 팀이나 지정, 설계서(SCR-B06 최대 90일)와 달리 null 이면 무기한 | 다른 팀은 `admin:write` 만 · TTL 필수(1~90) (C51-14) |
| api/identity · `identity:write` 의 admin 부여·IdP 역할 매핑 · Access Token audience · IdP 삭제 시 세션 | 기록 | 보안 담당의 신원 관리 권한 범위(역할 설계) — 권한 분리는 RBAC 재정의로. audience 는 발급 정책(SCR-L) 범위 | 없음(다음 신원 정리 후보) |

## INDEX 의 미검증 항목 (2026-10-08, `CODE-REVIEW-Lv2/INDEX.md` §4 — H·M 번호)

| INDEX | 판정 | 근거 | 처리 |
|-------|------|------|------|
| H2 registry `_anx_review` 주입 [높음] | **확인 → 수정** | 첫 게시 때 `_apply_version` 이 게시 메타데이터를 그대로 항목에 넣고, 승인(`review.apply_approval`)은 기존 `_anx_review` 위에 덮는다 — 게시자가 넣은 `tool_risks`·`trust` 가 승인자가 손대지 않은 도구에 그대로 남아 등록 때 규칙이 된다 | 게시 입력의 `_anx_*` 예약 키 거부(422 `reserved_metadata`) (C51-16) |
| H4 secret_ref 떼었다 붙이기 [높음] | **확인 → 수정** | ① `secret_ref: null` + 엔드포인트 변경(묶인 시크릿 없음 → 통과) ② 자기 네임스페이스 `secret_ref` 재부착(전송처 변화 없음 → 통과) | `secret_ref` 변경도 전송처 변경 — 저장된 시크릿 다시 붙이기는 `gateway:write`, 새 값을 직접 넣는 설정은 그대로 (C51-16). 39th C39-12 의 "자기 네임스페이스 허용" 단언을 이 규칙으로 고침 |
| M16 등록 옵션 `endpoint` 로 시크릿 전송처 변경 | **확인 → 수정** | 이미 있는 인스턴스 재등록은 `options.endpoint` 와 `auth or srv.auth`(저장 시크릿 유지)를 그대로 — `catalog:write` 만 있는 토큰이 `check_destination` 없이 전송처를 바꾼다 | 재등록 전에 PATCH 와 같은 `check_destination` (C51-16) |
| M17 미러 auto_approve 의 `_meta.auth` 전송처 | 기록 | 미러 자동 승인은 운영자가 소스별로 켜는 신뢰 설정(E06) — 신뢰한 소스의 게시물을 사람 승인 없이 받는 것이 그 기능 | 없음(자동 승인 소스의 auth 전송처 표시는 다음 후보) |
| M14 기한 지난 승인 결정 → 실행 영구 대기 | **확인 → 수정** | `decide` 가 상태만 `expired` 로 커밋하고 예외 — `approval_jobs` 는 `pending` 만 처리해 재개 훅(`on_approval`)이 불리지 않는다 | 상태를 바꾸지 않고 거부, 만료 처리는 예약 작업이(감사·재개) (C51-17) |
| M18 미러 비 ApiError 가 동기화·보존 작업 중단 | **확인 → 수정** | `registry_jobs` 가 `ApiError` 만 잡아 형식 오류 하나로 `tick` 이 중단 — 같은 주기의 보존 작업이 매번 건너뛰어진다 | 소스 단위 `except Exception` 격리 (C51-17) |
| M19 새 버전 게시 소유자 확인 · M20 `/v0/servers` 전체 적재 | 기록 | 버전 게시는 `registry:publish` + 승인 흐름(새 버전은 pending) — 선점은 409 로 드러난다. 발견 API 규모는 운영 규모 의존 | 다음 레지스트리 정리 후보 |
| M23 Inspector `data_class` 서버 판정 없음 | **확인 → 수정** | 본문 값을 그대로 `RpcContext` 로 — 기본 등급 상향·`data:declare` 회수가 Inspector 에서 빠진다 | `declared_data_class` + 모르는 값 400 (C51-17) |
| M25 선택 밖 operation 노출 | **확인 → 수정** | `operationId` 없는 operation 은 선택 목록이 있어도 남아 hosted 도구로 | 선택이 있으면 제거 (C51-17) |
| M27 AS 미등록 서버의 401 AS 수용 | 기존 결정 | 39th C39-13 — "등록된 AS 와 다르면 거부 · 미고정이면 통과"(첫 발견 경로) | 없음 |
| M34 라우팅 규칙 `timeout_ms` 미적용 · M35 예산 사전 검사 경합 | 기록 | 기능 정확성(시간 상한·동시 요청 예산 초과 폭) — 보안 경계 아님 | 다음 모델 게이트웨이 정리 후보 |
| M41 모르는 taskId 푸시 설정이 행 생성 | **확인 → 수정** | `_save_push` 가 아무 id 로 `submitted` 행을 만들어 감시기가 영원히 폴링(누구나 행을 늘릴 수 있다) | 게이트웨이가 중계한 Task 만, 없으면 -32001 (C51-17) |
| M45 로컬 취소가 오케스트레이션 실행을 남김 | **확인 → 수정** | 같은 워커 취소는 실행기 Task 만 `cancel()` — `CancelledError` 가 `ctx.canceled` 확인 전에 터져 `cancel(run_id)` 가 불리지 않는다 | 실행기에서 `CancelledError` 시 실행 취소 (C51-17) |
| M47 A2A 인증 형태(`scheme`·`tls`) 변경 | **확인 → 수정** | 시크릿이 묶인 에이전트의 `scheme`(교환 → 그대로 헤더)·`tls.verify` 변경은 전송처 키가 아니라 통과 | 전송처 키에 `type`·`scheme`·`header`·`tls` 추가 (C51-17). `push_verify` 는 수신 검증 설정(기록) |
| (리허설) 플랫폼 `tasks/list` 워커 메모리 전용 — Lv2 a2a_server [낮음] "목록이 워커 메모리 전용" | **확인 → 수정** | prod 2 workers 리허설에서 44th P44-01 실패로 재현 — get·cancel 은 영속 행으로 판정하지만 목록은 메모리만 | 주체의 영속 Task(최근 50) + 메모리 병합 (C51-18) |
