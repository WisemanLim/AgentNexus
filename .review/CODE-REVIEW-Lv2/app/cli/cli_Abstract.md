# app/cli/ — 디렉터리 개요 / Directory Abstract
> [← INDEX.md](../../INDEX.md)

`nexus` CLI — 설치 점검·compose 설치, 로그인(디바이스·PAT), 카탈로그·레지스트리·MCP·에이전트·KB·채팅·승인·감사·에어갭 번들.

## 리뷰한 파일
| 파일 | LOC | 역할 | 리뷰 |
|---|--:|---|---|
| `__main__.py` | 3 | `python -m app.cli` 진입점 | [__main__.py.md](__main__.py.md) |
| `nexus.py` | 752 | typer CLI 전체 | [nexus.py.md](nexus.py.md) |

## 구조 요약
`__main__.py` → `nexus.main` → `app(prog_name="nexus")`. `nexus.py` 는 `app.db`·`app.services.{setup,deploy}` 를 지연 import(`--local` 설정 코드·프로파일 컴포넌트).

## 위험 신호
- 서버 응답·번들 매니페스트의 파일 이름을 경로 검증 없이 파일 시스템에 쓰기(`mcp scaffold`·`bundle import`).
- `write_env` 가 `SIGNING_KEY_PEM=CHANGE_ME` 를 PEM 아닌 난수로 채워 운영 기동 실패 가능.
