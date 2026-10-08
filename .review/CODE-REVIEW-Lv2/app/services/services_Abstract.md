# app/services/ — 디렉터리 개요 / Directory Abstract
> [← INDEX.md](../../INDEX.md)

도메인 로직 — 게이트웨이 프록시(MCP·A2A), 프로토콜 코덱·변환, 거버넌스·정책·한도, 신원·토큰 교환, Unified Model API, 오케스트레이션, 감사·관측·SLO·보존, 가드·시크릿·mTLS 등 24개 모듈. API 라우터가 호출하는 핵심 계층.

## 리뷰한 파일
| 파일 | LOC | 역할 | 리뷰 |
|---|--:|---|---|
| `mcp_gateway.py` | 1031/1041* | MCP 프록시(세션·전송·인증 주입·발견·거버넌스·소독) | [mcp_gateway.py.md](mcp_gateway.py.md) |
| `mcp_modern.py` | 415 | MCP 2026-07-28 수용(검증·MRTR·listen·progress) | [mcp_modern.py.md](mcp_modern.py.md) |
| `mcp_tasks.py` | 221/231* | MCP Tasks 확장(장기 도구 호출·생존 신호) | [mcp_tasks.py.md](mcp_tasks.py.md) |
| `mcp_consent.py` | 333 | 서버→클라이언트 요청(sampling·elicitation·중계·동의) | [mcp_consent.py.md](mcp_consent.py.md) |
| `a2a_gateway.py` | 811/819* | A2A 프록시(카드·인증·변환·SSE·Task·푸시·감시기) | [a2a_gateway.py.md](a2a_gateway.py.md) |
| `a2a_proto.py` | 345 | A2A 0.3↔1.0 코덱·카드 | [a2a_proto.py.md](a2a_proto.py.md) |
| `a2a_rest.py` | 194 | A2A HTTP+JSON 바인딩 | [a2a_rest.py.md](a2a_rest.py.md) |
| `a2a_grpc.py` | 254 | A2A gRPC 바인딩(수신·원격) | [a2a_grpc.py.md](a2a_grpc.py.md) |
| `governance.py` | 318 | 도구·에이전트 거버넌스·승인 | [governance.py.md](governance.py.md) |
| `identity.py` | 196 | 토큰 발급·RFC 8693 교환·세션 정책 | [identity.py.md](identity.py.md) |
| `llm.py` | 425 | Unified Model API 실행·라우팅·폴백 | [llm.py.md](llm.py.md) |
| `slo.py` | 156 | SLO·오류 예산 | [slo.py.md](slo.py.md) |
| `scheduler.py` | 141 | 예약 작업 루프·리스 | [scheduler.py.md](scheduler.py.md) |
| `audit.py` | 242 | 감사 해시 체인·아카이브·리포트 | [audit.py.md](audit.py.md) |

\* 리뷰 도중 동시 수정으로 커진 파일은 14:03 스냅샷 기준(INDEX §1.6).

## 미선택(부록, 10개)
`orchestrator.py`(1137)·`registry.py`(575)·`graph.py`(433)·`providers.py`(409)·`a2a_server.py`(361)·`guards.py`(280)·`ratelimit.py`(277)·`setup.py`(219)·`review.py`(201)·`knowledge.py`·`policy.py`·`policy_review.py`·`broker.py`·`deploy.py`·`tls.py`·`attachments.py`·`notify.py`·`secrets.py`·`totp.py`·`runmeta.py`·`profiles.py`·`retention.py` 는 부록 A. (일부는 INDEX §4 종합에서 교차 참조.)

## 구조 요약
`mcp_gateway`·`a2a_gateway` 가 허브다 — 둘 다 `governance`·`identity`·`guards`·`ratelimit`·`secrets`·`audit`·`tls` 와 각자의 코덱(`a2a_proto`·`mcp_modern`)을 쓴다. `scheduler` 가 `api.observe`·`api.ops` 를 지연 import 하는 계층 위반(종합 §4.2).

## 위험 신호
- `mcp_gateway`·`a2a_gateway`: 사용자 병합 가능한 `auth.secret_ref`·`endpoint`·`token_url` → 임의 시크릿 외부 전송(위험:높음, INDEX §4.3 핫스팟 #1).
- `mcp_gateway`: 업스트림 메타데이터로 발견한 외부 AS 에 사용자 원본 토큰 전송 + 업스트림 지정 URL 조회(SSRF).
- `governance`: 승인 소비 TOCTOU(이중 실행), 새 도구·annotations 자동 허용.
- `identity`: 교환 함수에 주체 권한 검사 없음 + 공개 token-exchange grant.
- `a2a_gateway`: 클라이언트 웹훅 SSRF·감시기 기아.
