# impl 40th — 인증 경계 강화 (Epic 40 · D17-①)

## 범위

| 항목 | 구현 |
|------|------|
| 토큰 교환 grant | `/idp/token` `urn:ietf:params:oauth:grant-type:token-exchange` 는 워크로드 `client_id`+`client_secret` 인증 필수(`identity.authenticate_workload`) — 실패 401 `invalid_client` · 발급 토큰 `act.sub` = `workload:{이름}`(신고값 아님). 게이트웨이 내부 교환(`identity.exchange`)은 그대로 |
| agent 신고 | `X-AgentNexus-Agent` 는 기계·위임 주체(`workload`·`access_token`·`delegation`)만 — 사람 주체(user·pat·client_key)가 보낸 값은 무시 → agent 주체 정책 위조 불가. 오케스트레이터(위임 토큰)·데모 에이전트(워크로드)는 그대로 |
| 데이터 등급 신고 | 기본값(`gateway.default_data_class`)보다 낮은 신고는 `data:declare` 권한 필요 — 역할 기본 부여(현 동작 유지)·관리자가 RBAC 행렬에서 회수 · 스코프 토큰은 `gateway:invoke` 묶음 또는 `data:declare` 스코프 · 권한이 없으면 기본값으로 올리고 감사 `gateway.data_class.raise`. 상향은 언제나 |
| run 신고 | `X-AgentNexus-Run` 은 그 주체(`Run.user_sub`)의 실행만 — 아니면 무시(step 도 함께) |
| 적용 경로 | MCP(`api/mcp.py`)·A2A(`api/a2a.py`, REST·gRPC 위임 포함)·Unified Model API(`api/llm.py`)·지식 검색 — `policy.caller_context`/`declared_data_class` 한 곳 |

> 결정 경위: 처음 고른 '상향만'은 기본 internal 에서 public 신고(상용 모델)가 막혀 기존 FR 과 충돌 → 재확인으로 '권한 기반' 확정(PRD D17).

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C40-01 | 신고 client_id 만·틀린 비밀 → 401 `invalid_client` · 워크로드 인증 → `act=workload:e40-tx` | `test_token_exchange_requires_workload` (+ `test_identity.py::test_token_exchange_rfc8693` 갱신) | pytest |
| C40-02 | user·pat·client_key 의 agent 신고 무시 · workload·access_token·delegation 은 수용 | `test_agent_header_only_for_machine_principals` | pytest |
| C40-03 | 기본 권한으로 public 신고 유지 · 회수 시 internal 로 + 감사 · 상향 유지 · 스코프(`chat:use` 만 불가, `gateway:invoke` 가능) | `test_lower_data_class_needs_permission` | pytest |
| C40-04 | 자기 run 수용 · 남의·없는 run 무시(step 포함) | `test_run_header_bound_to_owner` | pytest |
| C40-05 | 모델 API: 권한 회수 시 public 신고 → 상용 모델 대신 내부 모델 | `test_model_api_uses_server_data_class` | pytest |
| 회귀 | 전체 pytest · 오케스트레이션·A2A·MCP 정책 시험 회귀 0 | `make test` | pytest |
| P40-01~06 | prod: 교환 401·워크로드 교환 act · RBAC 회수 → public 신고 상용 모델 차단 + 감사 · 복원 · 회귀 | `smoke-prod.sh` | live |

## 환경 가정

- RBAC 재정의는 워커별 5초 캐시(기존 규칙) — prod 스모크는 변경 후 6초 대기.
- `test/impl/2nd/smoke.sh` 의 토큰 교환 단계는 당시 동작 기준(덮어쓰지 않음) — 지금 재실행하면 워크로드 인증 요구로 실패한다.
