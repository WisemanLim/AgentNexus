# impl 45th — 결과: 다중 워커 일관성 (Epic 45 · D18-②)

판정: **PASS** — pytest C45-01~05 **5/5**(+ MCP modern·Tasks 회귀) · prod 스모크 P45-01~05 **5/5**.

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest r1 | 17/17 (C45-01~04 + 회귀) | `logs/pytest-r1.log` |
| pytest r2 | **5/5** — C45-05 추가 | `logs/pytest-r2.log` |
| prod 스모크 (2 workers · Valkey) | **5/5** | `logs/smoke-prod-r1.log` |

## 회차 중 발견

- 구독 반납을 `finally` 안에서 `await run_in_threadpool(…)` 로 하면, 클라이언트가 끊을 때 anyio 취소 범위가 그 await 도 다시 취소해 반납이 빠진다(TTL 960초 동안 한도가 잠김) → 동기 반납으로 바꾸고 끊김 시험(C45-05)을 더했다. prod P45-04 로 실제 끊김 후 0 확인.

## prod 스모크

| # | 결과 |
|---|------|
| P45-01 | 동시 listen 5개(두 워커로 분산) → Valkey `anx:c:listen:usr_rehearsal` = 5 |
| P45-02 | 6번째 listen → 429 |
| P45-03 | 5개 모두 `notifications/subscriptions/acknowledged` |
| P45-04 | 연결을 끊은 뒤 카운터 0 |
| P45-05 | `lease.a2a_watch`·`lease.mcp_health` 보유 워커가 16초 동안 그대로(한 워커만) |

## 남은 항목

- 다른 워커의 MRTR progress 이어 받기는 pytest(C45-03)로만 확인 — prod 에서 두 워커에 걸친 MRTR 재시도를 일부러 만들기 어렵다.
