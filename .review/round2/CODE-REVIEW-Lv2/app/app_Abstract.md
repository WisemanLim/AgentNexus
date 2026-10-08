# app/ — 디렉터리 개요 / Directory Abstract (2차)
> [← INDEX.md](../INDEX.md) · 리뷰 레벨 2 · 스냅샷 2026-10-07 21:03 KST · 1차: [../../../CODE-REVIEW-Lv2/app/app_Abstract.md](../../../CODE-REVIEW-Lv2/app/app_Abstract.md)

애플리케이션 루트 패키지. 2차 범위에서 루트 파일은 FastAPI 앱 조립(`main.py`)과 DB 엔진·세션(`db.py`) 두 개이고, 나머지 변경은 하위 패키지 `api`(HTTP 라우터)·`core`(인프라)·`services`(도메인 로직)·`cli`(`nexus`)에 있다. `migrations/versions/0019_refresh_family.py` 는 패키지 밖이지만 함께 리뷰했다.

## 리뷰한 파일
| 파일 | LOC(1차 → 2차) | 역할 | 등급 | 리뷰 |
|---|---|---|:--:|---|
| `main.py` | 195 → 203 | 구성요소별 라우터 마운트, lifespan 백그라운드 작업(헬스 루프·A2A 감시기·스케줄러), 콘솔 서빙 | 중간 | [main.py.md](main.py.md) |
| `db.py` | 80 → 85 | 엔진·세션·UTC 시각 타입, PostgreSQL 장애조치 연결 인자 | 낮음 | [db.py.md](db.py.md) |
| `cli/nexus.py` | 752 → 768 | `nexus` CLI — 설치·번들·카탈로그·채팅 | 중간 | [cli/nexus.py.md](cli/nexus.py.md) |
| (패키지 밖) `migrations/versions/0019_refresh_family.py` | 신규 26 | refresh token 회전 계열 컬럼 | 낮음 | [../migrations/versions/0019_refresh_family.py.md](../migrations/versions/0019_refresh_family.py.md) |

하위 디렉터리 개요: [api](api/api_Abstract.md) · [core](core/core_Abstract.md) · [services](services/services_Abstract.md) (`cli/cli_Abstract.md` 는 이번 범위에서 만들지 않음 — INDEX §1.4)

## 구조 요약
`main.py` 가 모든 라우터를 import 하고 구성요소 설정(`COMPONENTS`)에 따라 마운트하며(예: registry 구성요소 → `catalog_api.router`, 스냅샷 `app/main.py:151-152` — Epic 51 이후 현재 디스크는 `:186-187`), lifespan 에서 백그라운드 루프를 띄운다. 의존 방향은 `api → services → core`·`models`·`db` 가 기본이고, `services.scheduler` 가 `api.observe`·`api.ops` 를 지연 import 하는 역방향(`app/services/scheduler.py:46`, `:116`)은 1차 그대로다. `db.py` 는 거의 모든 모듈이 쓰는 세션 팩토리·`utcnow` 를 제공한다.

## 주요 의존 관계
- `main.py` → 전 라우터(`app/main.py:14-29` 무조건 import — 라우터 모듈의 `@registry.register_handler` 부수효과가 구성요소와 무관하게 실행, 1차 INDEX §4.2), `services.scheduler.loop`, `mcp_gateway` 헬스 루프, `a2a_gateway` 감시기.
- `db.py` ← 서비스·API 전반(세션 범위 커밋), `scheduler.lease`(조건부 UPDATE 리스).

## 위험 신호
- `main.py` 헬스 루프가 DB 오류 한 번에 종료·리스 예외 fail-open(스냅샷 `:82-86`) — VERIFY C51-10 수정. 스케줄러가 host·observe 일 때만 기동(`:112`) — 기록(gateway-only·registry-only 단독 배포에서 보존 정지).
- 보안 응답 헤더·TrustedHost 부재 — C51-02 로 헤더 추가, TrustedHost 는 기록.
- 예약 작업 `tick` 의 단계가 한 예외로 함께 멈춘다 — 미러 독 항목이 보존 작업까지 막는 경로([services/registry.py.md](services/registry.py.md) L457-466).
