# impl 37th — 결과: A2A gRPC 바인딩 (Epic 37 · D16-②)

판정: **PASS**

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest r1 (`test/test_a2a_grpc.py`) | 3/4 — C37-03 카드 시험 실패 | `logs/pytest-r1.log` |
| pytest r2 (A2A 스위트 전체) | **41/41** | `logs/pytest-r2.log` |
| local 실서비스 스모크 (compose `a2a-grpc`) | send COMPLETED · stream 5 이벤트 · NOT_FOUND · 익명 UNAUTHENTICATED | `logs/local-smoke.log` |
| prod 스모크 r1 (PostgreSQL · 2 workers, api 컨테이너 안 클라이언트 → `a2a-grpc:50051`) | **9/9** | `logs/smoke-prod-r1.log` |

## 회차 원인

- pytest r1 — 시험 결함: 시험 환경이 `.env.local` 을 읽어 `A2A_GRPC_PUBLIC_URL` 이 이미 있었는데 "광고 주소 없으면 GRPC 없음" 을 가정 → 시험에서 먼저 빈 값으로 고정. 제품 변경 없음.
- 완료 게이트에서 발견(39th 게이트 r1): `test_deploy.py::test_chart_config_covers_compose_env` 가 새 env 키 `A2A_GRPC_PORT`·`A2A_GRPC_PUBLIC_URL` 을 차트 ConfigMap 에서 찾지 못함 → 호스트 포트는 host_only 로, `A2A_GRPC_PUBLIC_URL` 은 ConfigMap 에 빈 값(차트는 a2a-grpc 를 배포하지 않으므로 카드에 GRPC 를 광고하지 않음). OpenAPI 계약 스냅샷도 36th REST 경로 9개로 갱신.

## prod 스모크

| # | 결과 |
|---|------|
| P37-01 | `a2a-grpc` 서비스 healthy |
| P37-02~04 | SendMessage COMPLETED · GetTask 같은 id · SendStreamingMessage 마지막 COMPLETED |
| P37-05 | 없는 Task → `NOT_FOUND` + 트레일러 ErrorInfo `TASK_NOT_FOUND` |
| P37-06 | 익명 → `UNAUTHENTICATED` |
| P37-07 | tenant `platform/tpl-qa` COMPLETED |
| P37-08 | 게이트웨이 카드 GRPC 인터페이스 `127.0.0.1:8851` · tenant `observability-analyst` |
| P37-09 | 감사 체인 무결 |

## 남은 항목

- gRPC 는 평문 수신 — TLS 는 앞단 종료. Helm 차트에 a2a-grpc 없음(compose 전용).
