# impl 48th — 객체 수준 인가 전수 점검 (Epic 48 · D19-① · OWASP API1 BOLA/IDOR)

## 범위

| 항목 | 내용 |
|------|------|
| 대상 | 경로 매개변수가 있는 라우트 163개 중 **일반 사용자가 닿는 66개**(권한 없음 또는 `user` 역할 권한만 요구 — `logs/routes-with-params.tsv` 의 `user` 행). 나머지 97개는 운영자·개발자·보안 권한 전용(`priv`) |
| 방식 | `user`(team-biz)가 실제로 만든 객체 — 세션·실행·메시지·첨부·개인 토큰·A2A Task·승인·MCP 세션·팀 전용 KB — 를 `user2`(team-agents)가 읽기·쓰기·삭제 시도 |
| 기대 | 모두 403 또는 404(존재 비노출 우선) · 주인은 같은 읽기 경로에서 200 · 시도 뒤에도 주인 객체 그대로 |
| 분류 강제 | 사용자 경로는 `OWNED`(행렬로 검사) 또는 `SHARED`(조직 공유 자원·다른 시험이 맡음 — 이유 기재) 중 하나여야 한다. 새 라우트가 생기면 시험이 실패해 분류를 요구 |

## 수용 기준 → 시험 케이스

| # | 수용 기준 | 시험 | 방식 |
|---|-----------|------|------|
| C48-01 | 사용자 경로 66개가 모두 OWNED(28) 또는 SHARED(38)로 분류됨 | `test_idor_matrix.py::test_every_user_route_is_classified` | pytest |
| C48-02 | 남의 객체 29개 요청(읽기·수정·삭제·재실행·승인·동의·KB 검색) → 403/404 | `test_other_user_cannot_touch` | pytest |
| C48-02b | 주인은 같은 읽기 경로에서 200 — 행렬이 헛돌지 않음 | `test_owner_reads_ok` | pytest |
| C48-03 | 남의 `Mcp-Session-Id` 로 GET(스트림)·DELETE·요청 불가 | `test_other_user_cannot_use_mcp_session` | pytest |
| C48-04 | 시도 뒤 주인 세션·토큰·승인 상태 그대로 | `test_owner_objects_intact` | pytest |

## 환경 가정

- 프로파일·MCP 서버·카탈로그·모델은 설계상 조직 공유 자원(읽기) — 사람별 객체가 아니므로 SHARED 로 분류하고 이유를 남긴다.
- A2A 게이트웨이·플랫폼 경로의 Task 소유권은 44th(`test_task_ownership.py`)가 이미 맡는다.
