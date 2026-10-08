# impl 64th — 결과: 경계 하드닝 4 (Epic 64 · D22-④)

판정: **PASS** — pytest C64-01~08 **8/8** · 관련 회귀 포함 SQLite **91 → 15/15** · PostgreSQL **42/42**.

| 단계 | 결과 | 로그 |
|------|------|------|
| r1 | 90/91 — C64-05 가 도구 규칙이 아직 없는 상태(새로고침 전)라 드라이런이 아니라 실제 호출로 감 → 시험에 `refresh` 추가 | `logs/pytest-r1.log` |
| r2 | **15/15** | `logs/pytest-r2.log` |
| PostgreSQL r1 | **42/42** (LIKE 이스케이프 포함) | `logs/pytest-pg-r1.log` |

## 의도한 동작 변경

- 대상용(`mcp:`·`a2a:`) Access Token 의 기본 스코프 `gateway:invoke` 는 교환 정책에 없으면 거부된다 — C53-01 은 대상용에 빈 스코프로, 55th prod 스모크 P55-03 회귀 사본도 같은 조정(13차 게이트).
- `/v0/servers?search=` 는 이름만 본다(도구·설명 일치 없음 — MCP 레지스트리 사양). 콘솔 카탈로그 검색(`/v1/catalog?q=`)은 그대로 전체 색인.
- 64th 디렉터리는 수동으로 만들었다(번호 규칙 동일).
