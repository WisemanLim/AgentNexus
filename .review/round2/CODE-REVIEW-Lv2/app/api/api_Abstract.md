# app/api/ — 디렉터리 개요 / Directory Abstract (2차)
> [← INDEX.md](../../INDEX.md) · 리뷰 레벨 2 · 스냅샷 2026-10-07 21:03 KST · 1차: [../../../../CODE-REVIEW-Lv2/app/api/api_Abstract.md](../../../../CODE-REVIEW-Lv2/app/api/api_Abstract.md)

HTTP 라우터 계층. 2차에서 바뀐 6개 — MCP·A2A 진입점, 게이트웨이 관리, 관측, 내장 IdP, Identity 관리. 각 라우터는 `core.auth.require(...)`(또는 `current_principal`)로 권한을 확인하고 `services.*` 에 위임한다. 이 중 4개(`idp`·`mcp`·`a2a`·`identity`)는 스냅샷 이후 Epic 51 로 디스크가 바뀌었다 — 보고서 줄 번호는 스냅샷 기준.

## 리뷰한 파일
| 파일 | LOC(1차 → 2차) | 역할 | 등급 | 리뷰 |
|---|---|---|:--:|---|
| `mcp.py` | 760 → 778 | MCP 게이트웨이 엔드포인트(legacy·modern), 서버 관리·Inspector·OpenAPI 서버 | 높음 | [mcp.py.md](mcp.py.md) |
| `a2a.py` | 719 → 727 | 플랫폼 A2A 서버, 게이트웨이 A2A 프록시, 원격 에이전트 관리·Task 조회 | 높음 | [a2a.py.md](a2a.py.md) |
| `gateway_admin.py` | 835 → 836 | 정책·버전·롤백·게시 심사, 한도·시크릿·가드·승인 | 높음 | [gateway_admin.py.md](gateway_admin.py.md) |
| `identity.py` | 490 → 490 | 내 계정·PAT/클라이언트 키, 사용자·역할, 외부 IdP 연동, 워크로드, Access Token | 높음 | [identity.py.md](identity.py.md) |
| `idp.py` | 415 → 421 | 내장 IdP — 로그인 폼·브로커·토큰 엔드포인트·디바이스 코드 | 높음 | [idp.py.md](idp.py.md) |
| `observe.py` | 471 → 475 | 메트릭·OTLP 수신·트레이스·로그·Usage·알림·SLO | 중간 | [observe.py.md](observe.py.md) |

1차 부록 A 였던 `identity.py`·`idp.py` 는 이번에 처음 라인 리뷰했다. `catalog.py`(카탈로그·레지스트리 API)는 변경 목록에 없어 리뷰하지 않았지만 [../services/registry.py.md](../services/registry.py.md) 가 호출부로 많이 인용한다.

## 구조 요약
권한 검사는 라우터 데코레이터 수준(역할·스코프)에서만 이뤄지고, 객체 수준 인가(누구의 서버·에이전트·항목인가)는 엔드포인트마다 다르다. 2차의 큰 결함은 대부분 이 "권한은 있는데 대상·주체 종류를 보지 않는" 지점에 있다 — 토큰 발급(`identity.py`), 승인(`gateway_admin.py`), 에이전트 상태·auth 변경(`a2a.py`), Inspector 가상 주체(`mcp.py`).

## 주요 의존 관계
- `mcp.py` → `services.{mcp_gateway,mcp_modern,mcp_tasks,mcp_consent,mcp_template,registry,secrets,policy,audit}`; `a2a.py` → `services.{a2a_server,a2a_gateway,a2a_proto,a2a_rest,policy,registry,secrets,audit}`(지연 `orchestrator`); `gateway_admin.py` → `services.{governance,guards,mcp_consent,policy,policy_review,ratelimit,secrets,audit}`(지연 `tls`); `identity.py` → `services.{identity,broker,secrets,audit}`, `core.security`; `idp.py` → `services.{identity,broker,audit}`(지연 `ratelimit`·`totp`·`deploy`), `core.{keys,security}`; `observe.py` → `services.{llm,ratelimit,slo,audit}`·`notify`, `core.{metrics,tracing}` (각 파일 상단 import, 현재 디스크 기준).
- 역방향: `services.scheduler` → `observe.evaluate`(`app/services/scheduler.py:46`).

## 위험 신호
(처리는 `.review/round2/VERIFY.md` 2026-10-08 08:55 판 기준 — [INDEX §5](../../INDEX.md))
- `identity.py`: 토큰으로 토큰 발급(`:55-57`) [높음]·`ttl_days: null`·클라이언트 키 팀 자유 지정 [중간] → C51-14 수정. `identity:write` 의 admin 부여·Access Token audience 무제약·IdP 삭제 시 세션 유지 [중간] → 기록(VERIFY:85).
- `gateway_admin.py`: 기계 주체 자기 승인(`:746-755`) [높음] → C51-13(사람 주체 `user`·`pat` 만), 단독 롤백 [중간] → 기존 결정. `mcp.py`: 전송처 키 통제 [높음] → C51-08, Inspector 팀·OpenAPI 가져오기 [중간] → 기록, Inspector `data_class`·operationId 없는 operation [중간] → 미검증.
- `idp.py`: MFA 무제한·IP 허용 목록 우회·이벤트 루프 scrypt [중간] → C51-12, 표적 잠금 [중간] → 기존 결정. `a2a.py`: 승인 우회 PATCH → C51-13, auth 잔여 전송처 → 부분 수정(`scheme`·`tls.verify`·`push_verify` 미검증), Task 열람 범위·카드 조회 → 기록.
