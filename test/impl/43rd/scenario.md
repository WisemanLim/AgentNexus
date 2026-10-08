# impl 43rd — 정책 하드닝 (Epic 43 · D17-④)

## 범위

| 항목 | 구현 |
|------|------|
| `readOnlyHint` 신뢰 | 위험을 낮추는 `readOnlyHint` 는 카탈로그 신뢰 등급 `trusted` 서버만 — 아니면 이름 규칙(쓰기 동사)으로 판정. 높이는 `destructiveHint` 는 언제나 (`governance.classify_risk`·`default_rules`) |
| 클라이언트 푸시 URL | http(s) 만 · local/test 밖에서는 사설·루프백·링크 로컬·예약 주소(이름 해석 포함) 거부 → JSON-RPC -32602 `push_url_rejected` — 게이트웨이 경유(`a2a_gateway.push_url_problem`)·플랫폼 서버(`A2AServer(push_check=…)`) · 게이트웨이가 원격 에이전트에 등록하는 자기 웹훅은 대상 아님 · 시험 마운트 통과 (`http.internal_url`) |
| scrypt 강도 | OWASP 동급 N=2^14·r=8·**p=5**(메모리 16MiB 유지, 연산 5배) · 형식 `scrypt$n=…,r=…,p=…$salt$dk` · 이전 형식 검증 유지 · 로그인 성공 시 재해싱(`needs_rehash`) |
| refresh 재사용 탐지 | `family_id`(migration **0019**) — 회전해도 계열 유지 · 회전된 토큰을 10초 유예 밖에서 다시 내밀면 같은 계열의 살아 있는 토큰 모두 폐기 + 감사 `auth.refresh_reuse` · 유예 안(탭 두 개 동시 갱신)은 거부만 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C43-01 | trusted 서버 readOnly 쓰기 이름 → low · untrusted → high(승인) · destructiveHint → high | `test_policy_hardening.py::test_readonly_hint_trusted_only` | pytest |
| C43-02 | test 환경 사설 허용 · prod: 사설·루프백·IPv6 루프백·메타데이터·localhost 거부, 공인 IP·마운트 허용 · 게이트웨이·플랫폼 push set -32602 | `test_push_url_guard` | pytest |
| C43-03 | 새 형식 파라미터 · 이전 형식 검증·재해싱 필요 · 로그인 후 새 형식으로 | `test_scrypt_upgrade_and_rehash` | pytest |
| C43-04 | 유예 안 재제시 거부(계열 유지) · 유예 밖 재제시 → 계열 전부 폐기 + 감사 · 최신 토큰도 무효 | `test_refresh_reuse_revokes_family` | pytest |
| 회귀 | migration head 0019 · 전체 pytest | `test_admin.py` · `make test` | pytest |
| P43-01~07 | prod: 게이트웨이·플랫폼 사설 푸시 거부 · 새 계정 해시 형식 · 이전 해시 로그인 재해싱 · refresh 재사용 계열 폐기 + 감사 · 회귀 | `smoke-prod.sh` | live |

## 환경 가정

- 운영 클러스터의 egress NetworkPolicy 가 최종 방어선 — 이름 해석 검사는 DNS 재바인딩을 막지 못한다(39th 와 같은 한계).
- p=5 로 로그인 1회 CPU 가 늘었다(시험 스위트 실행 시간 증가) — 워크로드 비밀(고엔트로피)도 같은 해시를 쓴다.
