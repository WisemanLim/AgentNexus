# impl 56th — 결과: 원격 A2A·푸시 경계 (Epic 56 · D21-①)

판정: **PASS** — pytest C56-01~05 **5/5** · 회귀 포함 **113/113**.

| 단계 | 결과 | 로그 |
|------|------|------|
| r1 | **113/113** | `logs/pytest-r1.log` |
| r2 | 5/5 — C56-05 가 404 경로만 만나 헛돌지 않게 "2개 이상 확인" 단언 추가 | `logs/pytest-r2.log` |

## 결과 요약

- 푸시 서명을 쓰는 원격 에이전트는 이제 `iss`·`jti`·`request_body_sha256` 를 모두 넣어야 한다(A2A 푸시 JWT 권고 클레임). 기존 시험 2건(C24-06 설계 보완·C53-06 push_verify)은 issuer·jti 를 넣도록 갱신.
- 상한 8 MiB 는 상수(`a2a_gateway.MAX_REMOTE_BYTES`) — 큰 Artifact 를 주고받는 에이전트는 Artifact 를 URI 로 넘기는 것이 맞다.
