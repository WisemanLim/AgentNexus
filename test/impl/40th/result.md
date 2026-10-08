# impl 40th — 결과: 인증 경계 강화 (Epic 40 · D17-①)

판정: **PASS** — pytest C40-01~05 **5/5**(+ `test_identity.py` 갱신 12/12) · prod 스모크 P40-01~08 **8/8**.

| 단계 | 결과 | 로그 |
|------|------|------|
| pytest r1 | 15/17 — 시험 결함 2: Run 열 이름(`request`)·상용 모델 강등 판정(403 이 아니라 내부 모델로 재라우팅) | `logs/pytest-r1.log` |
| pytest r2 | **17/17** | `logs/pytest-r2.log` |
| 전체 r1 | 302/303 — `test_approval_context_remember_and_node_policy` 1건(단독·r2 통과, 시간 의존 시험) | `logs/pytest-full-r1.log` |
| 전체 r2 | **303 passed + 1 skipped** | `logs/pytest-full-r2.log` |
| prod 스모크 (PostgreSQL · 2 workers · 0019) | **8/8** | `logs/smoke-prod-r1.log` |

## 결정 경위

처음 고른 데이터 등급 '상향만'은 기본 internal 에서 public 신고(상용 모델 경로)를 막아 기존 동작과 충돌 → 구현 전에 다시 물어 **권한 기반**(`data:declare`, 역할 기본 부여·RBAC 회수)으로 확정 — PRD D17.

## prod 스모크

| # | 결과 |
|---|------|
| P40-01 | 신고 client_id 만으로 토큰 교환 → 401 `invalid_client` |
| P40-02 | 워크로드 인증 교환 → `act.sub = workload:p40-tx-…` |
| P40-03 | 기본 권한: public 신고 → `fast-commercial` |
| P40-04 | RBAC 로 user 의 `data:declare` 회수(6초 대기) → 두 번 모두 상용 모델 아님(두 워커) |
| P40-05 | 감사 `gateway.data_class.raise`(rehearsal-user) |
| P40-06 | RBAC 원복 — user 실효 권한에 `data:declare` |
| P40-07~08 | JSON-RPC 회귀 completed · 감사 체인 무결 |

정리: 임시 계정·워크로드 비활성화, RBAC 재정의 원래 값(`{}`).
