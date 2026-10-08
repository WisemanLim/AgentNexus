# app/builtin/ — 디렉터리 개요 / Directory Abstract
> [← INDEX.md](../../INDEX.md)

데모·참조 구현 — 참조 MCP 서버(Jira·Confluence·Dynatrace·Internal), 데모 원격 A2A 에이전트, 데모 외부 IdP, 데모 데이터, MCP 서버 측 인가. 운영에서는 실제 SaaS·IdP 로 교체(compose 프로파일 demo).

## 리뷰한 파일
| 파일 | LOC | 역할 | 리뷰 |
|---|--:|---|---|
| `mcp_servers.py` | 329 | 참조 MCP 서버 5종(FastMCP·OAuth 리소스 서버) | [mcp_servers.py.md](mcp_servers.py.md) |
| `a2a_agent.py` | 192 | 데모 원격 A2A 에이전트(게이트웨이 경유 호출) | [a2a_agent.py.md](a2a_agent.py.md) |

## 미선택(부록)
`demo_data.py`(59)·`demo_idp.py`(112)·`internal_system.py`(41)·`mcp_auth.py`(106)·`__init__.py` 는 부록 A.

## 구조 요약
`mcp_servers.py` 와 `a2a_agent.py` 는 별도 컨테이너(mcp-servers·a2a-demo)로 뜨거나 시험에서 in-process 마운트된다. 둘 다 `mcp_auth`(토큰 검증)·`demo_data`·`core.{config,tracing,http}` 에 의존.

## 위험 신호
- 전역 가변 데모 데이터(사용자 간 공유·경쟁) — 데모 범위라 수용.
- `a2a_agent.analyze` 의 기본값 없는 `next(...)`(실 Dynatrace 교체 시 StopIteration 가능).
