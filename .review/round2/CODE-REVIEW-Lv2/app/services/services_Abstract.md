# app/services/ — 디렉터리 개요 / Directory Abstract (2차)
> [← INDEX.md](../../INDEX.md) · 리뷰 레벨 2 · 스냅샷 2026-10-07 21:03 KST · 1차: [../../../../CODE-REVIEW-Lv2/app/services/services_Abstract.md](../../../../CODE-REVIEW-Lv2/app/services/services_Abstract.md)

도메인 로직 계층. 2차에서 바뀐 17개 — MCP·A2A 게이트웨이와 프로토콜 어댑터, 거버넌스·정책·한도, 신원·시크릿·mTLS, Unified Model API, 감사·보존, 카탈로그. 이 중 8개(`mcp_gateway`·`a2a_gateway`·`a2a_server`·`secrets`·`policy`·`mcp_consent`·`llm`·`governance`)는 스냅샷 이후 Epic 51 로 디스크가 바뀌었다 — 보고서 줄 번호는 스냅샷 기준, 처리 결과는 [INDEX §5](../../INDEX.md).

## 리뷰한 파일
| 파일 | LOC(1차 → 2차) | 역할 | 등급 | 리뷰 |
|---|---|---|:--:|---|
| `mcp_gateway.py` | 1031/1041 → 1096 | MCP 프록시 — 세션·대상 해석·전송별 호출·업스트림 인증 주입·발견·소독 | 높음 | [mcp_gateway.py.md](mcp_gateway.py.md) |
| `mcp_modern.py` | 415 → 500 | MCP 2026-07-28 stateless 수용(검증·MRTR·listen) | 미산출(보고서 미완) | [mcp_modern.py.md](mcp_modern.py.md) |
| `mcp_tasks.py` | 231 → 231 | MCP Tasks 확장(장기 도구 호출) | 낮음 | [mcp_tasks.py.md](mcp_tasks.py.md) |
| `mcp_consent.py` | 333 → 337 | 서버→클라이언트 요청(sampling·elicitation) 중계·동의 | 중간 | [mcp_consent.py.md](mcp_consent.py.md) |
| `a2a_gateway.py` | 819 → 886 | A2A 프록시 — 카드·인증·변환·Task·푸시·감시기 | 높음 | [a2a_gateway.py.md](a2a_gateway.py.md) |
| `a2a_server.py` | 361 → 394 | A2A 서버 엔진(JSON-RPC·REST, 실행기·SSE·푸시) | 높음 | [a2a_server.py.md](a2a_server.py.md) |
| `a2a_grpc.py` | 254 → 277 | A2A gRPC 바인딩(수신·원격) | 중간 | [a2a_grpc.py.md](a2a_grpc.py.md) |
| `governance.py` | 318 → 333 | 도구·에이전트 거버넌스, 위험도·기본 규칙·승인 | 높음 | [governance.py.md](governance.py.md) |
| `policy.py` | 157 → 211 | 정책 엔진 — 호출자 신고 판정·데이터 등급·주체 매칭 | 높음 | [policy.py.md](policy.py.md) |
| `identity.py` | 196 → 217 | 토큰 발급·refresh 회전·워크로드·교환 | 낮음 | [identity.py.md](identity.py.md) |
| `secrets.py` | 67 → 77 | 시크릿 저장소(Fernet)·참조/전송처 인가 | 낮음 | [secrets.py.md](secrets.py.md) |
| `tls.py` | 94 → 122 | 업스트림 mTLS 설정·정책 | 낮음 | [tls.py.md](tls.py.md) |
| `ratelimit.py` | 277 → 301 | 요청·토큰·예산 한도, 창·증감 카운터 백엔드 | 중간 | [ratelimit.py.md](ratelimit.py.md) |
| `llm.py` | 425 → 426 | Unified Model API — 후보·정책·등급·한도·폴백·실행 | 높음 | [llm.py.md](llm.py.md) |
| `audit.py` | 242 → 243 | 감사 해시 체인·아카이브·리포트 | 낮음 | [audit.py.md](audit.py.md) |
| `retention.py` | 39 → 39 | 보존 정책 실행 | 중간 | [retention.py.md](retention.py.md) |
| `registry.py` | 569(추정) → 575 | MCP Registry·카탈로그 — 게시·서명·승인·등록·검색·미러·SBOM | 높음 | [registry.py.md](registry.py.md) |

1차 부록 A 였던 `a2a_server`·`policy`·`secrets`·`tls`·`ratelimit`·`retention`·`registry` 는 이번에 처음 라인 리뷰했다.

## 구조 요약
`mcp_gateway`·`a2a_gateway` 가 허브로, 둘 다 `governance`(도구·에이전트 판정 — 내부에서 `policy`)·`guards`·`identity`·`ratelimit`·`secrets`·`tls`(지연)·`audit` 와 자기 프로토콜 어댑터(`mcp_modern`·`mcp_consent` / `a2a_proto`·`a2a_rest`·`a2a_grpc`)를 쓴다(`app/services/mcp_gateway.py:36`, `app/services/a2a_gateway.py:34-36`). `a2a_server` 는 게이트웨이가 아니라 `api.a2a`(플랫폼 서버)와 데모 에이전트가 쓰고, `mcp_tasks` 는 `api.mcp` 가 쓴다. `registry` 는 실행 경로 밖(발견 전용)이지만 게시 메타데이터가 등록 때 `McpServer.auth`·`server_json`·도구 규칙(`_anx_review`)으로 흘러가 게이트웨이 동작을 바꾼다. `llm` 은 별도 허브로 `policy`·`ratelimit`·공급자 어댑터를 쓴다.

## 주요 의존 관계
- `mcp_gateway` → `governance.check_tool`·`default_rules`, `identity.exchange`, `secrets.get_plain`, `tls`(지연), `mcp_consent`(지연), `review.tool_risk`(지연), `core.http`.
- `a2a_gateway` → `governance.check_agent`, `identity`, `secrets`, `a2a_proto`·`a2a_rest`·`a2a_grpc`(지연), `notify`, `core.http.internal_url`(푸시 URL).
- `registry` ← `api.catalog`·`api.mcp`·`api.a2a`·`api.llm`(핸들러 등록), `scheduler.registry_jobs`, `review`, `governance.default_rules`(`trust_of`).

## 위험 신호
(처리는 `.review/round2/VERIFY.md` 2026-10-08 08:55 판 기준 — [INDEX §5](../../INDEX.md))
- `registry`: `_anx_` 심사 기록 주입 [높음] — 게시자가 정한 도구 위험도가 등록 때 사람 승인 해제로 적용. 등록 옵션 endpoint·게시 auth 전송처 키·미러 독 항목(예약 보존까지 정지)·소유자 확인·익명 전체 스캔 [중간]. 모두 미검증(VERIFY 에 `registry` 행 없음).
- 자격 증명 전송처: `mcp_gateway` 전송처 키 [높음] → C51-08, 2단계 PATCH 우회 [높음] → 미검증, 외부 AS 원본 토큰 [높음] → 기존 결정, AS 미고정 수용 [중간] → 미검증. `governance` 기계 주체 자기 승인 [높음] → C51-13, 새 도구 자동 허용 → 기록, 기한 직후 결정 영구 대기 [중간] → 미검증.
- A2A 푸시: 동기 DNS·전달 시점 재검사·소독·감시기 [중간] → C51-03·C51-07·C51-09. 미검증: 로컬 취소 비대칭(`a2a_server`), 쓰레기 Task 행(`a2a_gateway`). `llm`: 한도 폴백 등급 우회 [높음]·모델 등급 미적용 → C51-11, 규칙 타임아웃·예산 사전 검사 [중간] → 미검증. `policy`: 모르는 등급·표시 이름 매치 → C51-13·C51-15.
