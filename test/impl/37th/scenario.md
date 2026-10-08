# impl 37th — A2A gRPC 바인딩 (Epic 37 · D16-② · FR-A03·G08 · NFR-INT1)

## 범위

| 항목 | 구현 |
|------|------|
| 정의 | 정규 `lf.a2a.v1.A2AService` — a2a-sdk(Apache-2.0)가 싣는 생성 스텁(`a2a.types.a2a_pb2`·`a2a_pb2_grpc`), 런타임 의존성 `grpcio`·`a2a-sdk` (이미지 빌드에서 설치, uv.lock 갱신) |
| 수신 | 새 compose 서비스 **`a2a-grpc`**(`python -m app.grpc_server`, 컨테이너 50051 → 호스트 `${A2A_GRPC_PORT:-8851}`) — 상태 없음: RPC 를 ProtoJSON 으로 바꿔 게이트웨이 HTTP+JSON 바인딩(36th)으로 위임, `authorization`·`a2a-version`·`x-agentnexus-*` metadata 전달 → 인증·정책·가드·감사·Task 영속은 api 한 곳 · Task 이벤트 `client_binding: GRPC` |
| 라우팅 | 요청 `tenant` = 카드 `AgentInterface.tenant` — 원격 에이전트 id · `platform` · `platform/{profile}` · 형식 밖은 INVALID_ARGUMENT |
| 오류 | REST google.rpc.Status → gRPC 상태(이름 그대로: NOT_FOUND·FAILED_PRECONDITION·PERMISSION_DENIED·UNAUTHENTICATED …) + `grpc-status-details-bin` 트레일러(같은 ErrorInfo) |
| 스트림 | SendStreamingMessage·SubscribeToTask 서버 스트림 ← REST SSE |
| 카드 | `A2A_GRPC_PUBLIC_URL` 이 있으면 플랫폼·게이트웨이 경유 카드에 `GRPC` 1.0 인터페이스(`tenant` 포함) |
| 원격 호출 | 카드에 GRPC 만 있으면 게이트웨이가 스텁으로 호출(tenant·`a2a-version: 1.0`·인증 metadata) · 오류 ErrorInfo reason/상태 → A2A 코드 · 스트림 변환 · `upstream_binding: GRPC` |
| env | `A2A_GRPC_PORT` · `A2A_GRPC_PUBLIC_URL` (local·prod·example) |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C37-01 | send·get(history)·stream·cancel FAILED_PRECONDITION·없는 Task NOT_FOUND(트레일러 ErrorInfo)·익명 UNAUTHENTICATED·잘못된 tenant·미지원 · platform tenant · client_binding GRPC | `test_grpc_inbound` | pytest(포트 0 in-process 서버 → 앱 ASGI) |
| C37-02 | 차단 등급 → PERMISSION_DENIED (agentnexus 사유) | `test_grpc_policy_parity` | pytest |
| C37-03 | 카드 GRPC 인터페이스는 광고 주소가 있을 때만, tenant 포함 | `test_cards_grpc_interface` | pytest |
| C37-04 | gRPC 전용 원격 에이전트 — 0.3 send·1.0 GetTask·없는 Task -32001·스트림 · tenant·a2a-version 전달 · upstream_binding | `test_outbound_grpc_only_agent` | pytest |
| L | compose `a2a-grpc` 실행 · 실제 서비스로 send·stream·NOT_FOUND·익명·platform | 수동(로컬 스모크) | live |
| P37-01~09 | prod(2 workers): 서비스 헬스 · send·get·stream·NOT_FOUND 트레일러·익명·platform · 카드 · 감사 체인 | `smoke-prod.sh` | live |

## 환경 가정

- gRPC 는 평문(insecure) — TLS 종료는 앞단(Ingress·메시) 몫. 원격 호출은 `grpcs://` 접두사면 TLS 채널.
- Helm 차트에는 a2a-grpc 를 넣지 않았다(compose 전용) — 남은 항목.
