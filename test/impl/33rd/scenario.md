# impl 33rd — A2A 1.0 기본화 (Epic 33 · D15-② · FR-A03 · NFR-INT1·M1)

## 범위

| 항목 | 구현 |
|------|------|
| 기본 버전 | `A2A_PROTOCOL_VERSION = "1.0"`, 지원 `["1.0", "0.3"]` — `/api/v1/system/info` `protocols.a2a`·`a2a_supported`, 업데이트 정보(L04) `current`, 사이드바 "A2A 1.0 · 0.3" (D7 의 A2A 0.3.0 고정 해제) |
| 카드 | 플랫폼·게이트웨이 경유 카드는 `supportedInterfaces` 첫 항목이 1.0, 0.3 클라이언트용 `url`·`protocolVersion: 0.3.0`·`preferredTransport` 유지(이중 카드 그대로) |
| 오케스트레이터 | 원격 위임 `SendMessage`·`GetTask`(1.0) — 1.0 업스트림이면 변환 없음, 0.3 전용이면 게이트웨이가 변환. 결과는 정준형으로 받는다 |
| 시험 콘솔 | `POST /api/v1/a2a/agents/{id}/try` 기본 `version: "1.0"` · 화면 기본 1.0, 메서드 이름을 버전에 맞게 표시 |
| 기록 | A2A Task 이벤트에 `protocol {client, upstream}` — Task 상세 이벤트에 "A2A 1.0→0.3 변환" 표시 |
| 0.3 호환 | `message/send`·`message/stream`·`tasks/*` 0.3 메서드·kind 형식 그대로 수용·응답 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C33-01 | info·업데이트 1.0 기본 · 카드 1.0 우선 + 0.3 필드 | `test_defaults_advertise_v10` | pytest |
| C33-02 | 오케스트레이션 위임 이벤트 전부 client 1.0 · upstream 1.0 | `test_orchestrator_delegates_with_v10` | pytest |
| C33-03 | 0.3 클라이언트 message/send → 0.3 형식 결과 · 0.3 그대로 | `test_v03_client_still_supported` | pytest |
| C33-04 | 0.3 전용 에이전트 — 시험 콘솔 기본(1.0) → 업스트림 message/send · 하류 TASK_STATE_* · 변환 기록 | `test_v10_default_against_v03_only_agent` | pytest |
| R | 기존 A2A·오케스트레이션·재개·폴링 시험(0.3·1.0 혼합) 회귀 없음 | `test_a2a.py`·`test_orchestration.py`·`test_reliability.py` | pytest |
| E33-01 | 시험 콘솔 기본 1.0 SendMessage 완료 · 0.3 선택 시 message/send · 사이드바 | `web/e2e/a2a-v10.spec.ts` | Playwright |
| P33 | prod: info 1.0 · 0.3/1.0 클라이언트 · 오케스트레이터 위임 1.0 | 완료 게이트 스모크 | live |

## 환경 가정

- 0.3 은 제거하지 않는다(D15-② "1.0 기본 + 0.3 호환 유지"). 카드의 0.3 필드는 0.3 클라이언트가 계속 읽는다.
- gRPC·HTTP+JSON 바인딩은 범위 밖(JSONRPC 만).
