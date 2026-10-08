# impl 56th — 원격 A2A·푸시 경계 (Epic 56 · D21-①)

## 범위

| 항목 | 구현 |
|------|------|
| 푸시 JWT | `iss`(설정한 issuer 와 일치)·`iat`(±5분)·`jti`(1회 — 900초 기억, 워커 공유)·`request_body_sha256`(필수) — 전에는 issuer·해시가 선택, jti 미확인 |
| push_verify 설정 | `jwks_url` 을 지정하면 `issuer` 필수(400 `push_verify_issuer_required`) |
| 원격 응답 | JSON-RPC·REST 응답을 스트림으로 읽으며 8 MiB 상한(넘으면 502) · 200 인데 JSON 이 아니거나 객체가 아니면 502 · SSE 는 이벤트 한 줄 상한·JSON 오류를 오류 이벤트로 |
| history | 저장 Task history 최근 200 개 |
| 카드 URL | 게이트웨이 프록시 카드·플랫폼 카드(루트·프로파일)는 `PUBLIC_BASE_URL` — `Host` 헤더로 광고 주소를 바꾸지 못한다 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C56-01 | issuer 없는 설정 400 · iss 없음/다름·jti 없음·해시 없음 401 · 같은 토큰 두 번째 401 · 새 jti 200 | `test_a2a_boundaries.py::test_push_jwt_requires_iss_jti_hash` | pytest |
| C56-02 | 상한 초과 502(상한 메시지) · HTML 200 → JSON 오류 · 정상 응답 그대로 | `test_remote_response_cap_and_non_json` | pytest |
| C56-03 | 정상 이벤트 뒤 깨진 이벤트 → 마지막이 오류 이벤트 | `test_stream_bad_event_becomes_error_event` | pytest |
| C56-04 | history 300 → 저장 200(최근) | `test_history_capped` | pytest |
| C56-05 | `Host: evil…` 로 받은 카드 2종 이상 모두 PUBLIC_BASE_URL | `test_card_urls_ignore_host_header` | pytest |
| 회귀 | A2A(JSON-RPC·REST·gRPC)·설계 보완(푸시 서명 시험 갱신)·신원 경계(push_verify 시험 갱신)·검토 수정·소유권·하드닝·운영 | 11개 파일 | pytest |
