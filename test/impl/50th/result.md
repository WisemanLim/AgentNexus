# impl 50th — 결과: CI·재현성 (Epic 50 · D19-③)

판정: **PASS** — pytest C50-01~03 + 회귀 **19/19** · 부하 A/B(C50-04) 고친 판 **6/6**(이전 판 5/6) · dev-env(C50-05) **9/9**.

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest r1 (C50 + 공급망·채팅 편집기·성능) | **19/19** | `logs/pytest-r1.log` |
| 부하 A/B — 이전 판 | 5/6 — run 6 `assert 'waiting_approval' == 'succeeded'` (40th 게이트에서 보던 그 실패) | `logs/stress-old.log` |
| 부하 A/B — 고친 판 | **6/6** | `logs/stress-new.log` |
| dev-env 재검증 | **9/9** | `test/dev-env/2026-10-07/result.md` |
| 전체 회귀 (`make test`, dev-env ENV-05) | **339 passed + 1 skipped** | `test/dev-env/2026-10-07/logs/05-make-test.txt` |

## 결과 요약

- 액션 SHA: `actions/checkout` v4 = v4.4.0 = `11d5960a326750d5838078e36cf38b85af677262`, `azure/setup-helm` v4(주석 태그) → v4.3.1 = `1a275c3b69536ee54be43f2070a358922e12c8d4`.
- 성능: 이번 측정은 1회차에 통과(모델 API p95 10.23ms · MCP 순수 추가 지연 p95 12.88ms — 호스트가 조용함). 재측정 로직은 C50-02 로 확인.
- 이전 판 실패 원인 확인: 승인 API 는 단계만 `pending` 으로 돌리고 재개를 예약한다(`orchestrator.on_approval`). 실행 행은 재개 작업이 잡을 때까지 `waiting_approval` — 제품 동작은 맞고 시험의 대기 조건이 틀렸다.
- 1초 타임아웃 시험(35th·39th 게이트 실패)은 이번 부하에서 이전 판도 재현되지 않았다. 원인은 코드로 확인(팬아웃 타임아웃이 모든 단계에 적용 — `orchestrator._step_timeout`)하고, 느린 단계에만 걸리도록 고쳤다.

## 남은 일

- NFR-P1 k6 재측정은 이번 차수 범위에서 생략(사용자 결정, D19).
