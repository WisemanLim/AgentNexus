# app/api/ — 디렉터리 개요 / Directory Abstract
> [← INDEX.md](../../INDEX.md)

HTTP 라우터 — MCP·A2A 경로, 게이트웨이 관리, 관측, Identity/IdP, 카탈로그, 채팅, 에이전트, 운영(ops), 설정(setup), 감사, 지식, 시스템. `main.py` 가 구성요소별로 선택 마운트.

## 리뷰한 파일
| 파일 | LOC | 역할 | 리뷰 |
|---|--:|---|---|
| `mcp.py` | 760 | MCP 엔드포인트·서버 관리·게이트웨이 현황 | [mcp.py.md](mcp.py.md) |
| `a2a.py` | 719 | 플랫폼 A2A 서버·게이트웨이 프록시·원격 에이전트 관리 | [a2a.py.md](a2a.py.md) |
| `gateway_admin.py` | 835 | 정책·한도·시크릿·가드·거버넌스·승인·라우팅 로그 | [gateway_admin.py.md](gateway_admin.py.md) |
| `observe.py` | 471 | 메트릭·OTLP 수신·트레이스·로그·Usage·알림·SLO | [observe.py.md](observe.py.md) |

## 미선택(부록, 13개)
`ops.py`(1036)·`catalog.py`(737)·`chat.py`(665)·`agents.py`(613)·`admin.py`(518)·`llm.py`(520)·`identity.py`(490)·`setup.py`(461)·`idp.py`(415)·`audit.py`(301)·`knowledge.py`(136)·`system.py`(31)·`__init__.py` 는 부록 A.

## 구조 요약
각 라우터는 `core.auth.require(...)` 로 권한을 강제하고 `services.*` 에 위임한다. MCP·A2A 라우터는 클라이언트 헤더(`X-AgentNexus-Agent`·`-Data-Class`·`-Run`)를 그대로 서비스 컨텍스트로 넘기는 공통 신뢰 경계 문제가 있다.

## 위험 신호
- `mcp.py`·`a2a.py`: `patch_server`/`patch_agent`/`test_config` 가 임의 `auth`(secret_ref·endpoint·token_url)를 `mcp:write`/`a2a:write` 만으로 받아 시크릿 유출 경로(위험:높음).
- `mcp.py`·`a2a.py`: 클라이언트 헤더로 받은 agent·data_class·run_id 를 정책·기록에 그대로 사용.
- `gateway_admin.py`: 정책 초안 수정자 미기록으로 2인 검토 우회, 소독 규칙 ReDoS.
- `observe.py`: 요약·Usage·알림 평가가 기간 내 전체 행을 메모리 집계(성능).
