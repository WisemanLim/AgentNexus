# impl 15th — 업스트림 mTLS (Epic 15 · FR-G02)

## 범위

- 대상별 TLS 설정: MCP 서버 `auth.tls` · 원격 A2A 에이전트 `auth.tls` · LLM 공급자 `options.tls` =
  `{ca_secret, client_cert_secret, verify, server_hostname}` — 인증서·키 PEM 은 시크릿 저장소(kind=`certificate`)에만.
- 게이트웨이 정책 `gateway.security.mtls.upstream`: `disabled`(클라이언트 인증서 미제출) · `optional`(설정된 대상만, 기본) ·
  `required`(https + 클라이언트 인증서 없으면 호출 전 거부 — fail closed, 오류 코드 `mtls_required`).
- 적용 지점: MCP JSON-RPC(스트리밍 포함)·PRM 조회 · A2A Agent Card·send·stream·Task 감시 · OpenAI 호환·Anthropic 어댑터.
  정책 위반 공급자는 라우팅 후보에서 제외. 하류(인바운드) mTLS 는 TLS 종단(Ingress)의 책임으로 둔다.

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C15-01 | 클라이언트 인증서 필수 MCP 서버: 인증서 없음·다른 CA 서명 인증서·서버 인증서 미신뢰는 실패, 올바른 설정은 도구 호출 성공, 상세 응답에 시크릿 평문 없음 | `test_mcp_server_requires_client_certificate` | pytest (실소켓 TLS 서버) |
| C15-02 | `required` 정책: 평문 업스트림(jira) 거부 `mtls_required` · mTLS 서버 허용 · mock 공급자 영향 없음 · 잘못된 정책 값 400 | `test_required_policy_fails_closed_for_plain_upstreams` | pytest |
| C15-03 | LLM 공급자 mTLS — 설정 전 실패, 설정 후 연결 시험 성공 | `test_llm_provider_over_mtls` | pytest |
| C15-04 | 원격 A2A Agent Card 조회 mTLS — 설정 전 실패, 설정 후 등록·새로고침 | `test_a2a_agent_card_over_mtls` | pytest |
| S15-01~06 | live: 인증서 시크릿 마스킹 · 정책 검증 · required fail closed · 복구 · 감사 | `smoke.sh` | live |
| R | 전체 회귀 SQLite·PG | logs | make |

## 환경 가정

- 시험용 PKI(CA·서버·클라이언트·악성 CA)는 실행마다 `cryptography` 로 생성하고 TLS 서버는 pytest 안에서 uvicorn(ssl_cert_reqs=CERT_REQUIRED)으로 띄운다.
- compose 의 데모 서버는 내부 평문 HTTP(backend 네트워크)라 live 에서는 정책 fail-closed 와 복구만 확인한다.
