# app/core/ — 디렉터리 개요 / Directory Abstract (2차)
> [← INDEX.md](../../INDEX.md) · 리뷰 레벨 2 · 스냅샷 2026-10-07 21:03 KST · 1차: [../../../../CODE-REVIEW-Lv2/app/core/core_Abstract.md](../../../../CODE-REVIEW-Lv2/app/core/core_Abstract.md)

인프라 계층 — 설정·인증/인가·토큰 서명과 해시·아웃바운드 HTTP. 2차에서 바뀐 4개는 모두 1차 라인 리뷰 대상이었다. `http.py` 는 스냅샷 이후 Epic 51 로 바뀌었다(C51-09 Alibaba IMDS 차단) — 보고서 줄 번호는 스냅샷 기준.

## 리뷰한 파일
| 파일 | LOC(1차 → 2차) | 역할 | 등급 | 리뷰 |
|---|---|---|:--:|---|
| `http.py` | 225 → 273 | 아웃바운드 httpx 팩토리, egress 가드(링크 로컬·IMDS), `internal_url`(사설·루프백 판정), `post_with_retry`, traceparent | 높음 | [http.py.md](http.py.md) |
| `auth.py` | 295 → 295(4줄 변경) | `Principal`, RBAC 표·스코프 묶음, 토큰 유형별 해석, FastAPI 의존성 | 중간 | [auth.py.md](auth.py.md) |
| `config.py` | 90 → 92 | 환경변수 설정 싱글턴, 구성요소·기본값 | 중간 | [config.py.md](config.py.md) |
| `security.py` | 67 → 88 | RS256 서명·검증, scrypt(파라미터 내장 형식·재해싱), 불투명 토큰, PKCE | 낮음 | [security.py.md](security.py.md) |

## 구조 요약
`config.py` 가 최하위, 그 위에 `security`·`http`, 그 위에 `auth`(모델·DB 사용). 2차의 핵심 변화는 `http.py` 의 egress 정책 — 모든 아웃바운드에 링크 로컬·IMDS 차단 훅을 걸고, 사용자 지정 콜백 URL 에는 별도로 `internal_url`(사설·루프백·예약 대역 거부)을 쓰는 2단 구조다. 사설망 업스트림 허용은 제품 결정(`app/core/http.py:34-35` 주석, 39th)이다.

## 주요 의존 관계
- `http.py` ← `services.{mcp_gateway,a2a_gateway,a2a_server,a2a_grpc,providers,broker,policy,policy_review,registry,orchestrator,notify}`, `api.{mcp,identity,catalog,admin,setup}`(grep `core.http` import) — 사실상 모든 외부 호출.
- `auth.py` ← 모든 라우터(`require`·`current_principal`·`optional_principal`). `ROLE_PERMS`·`SCOPE_GRANTS`(`app/core/auth.py:20-39`)가 권한 판단의 단일 출처.
- `security.py` ← `api.idp`(로그인·토큰), `services.identity`(워크로드 시크릿).

## 위험 신호
- `http.py`: 전달 시점 재검사 없음 [중간] → C51-03·C51-09 수정, Alibaba IMDS [중간] → C51-09 수정, DNS 재바인딩 TOCTOU [중간] → 기존 결정(SEC2-02, 차트 기본에 egress NetworkPolicy 없음).
- `auth.py`: 클라이언트 키 `team` 을 키 행 값으로 신뢰(`:211`) — 발급 API 가 검증하지 않는 교차 결함 [중간] → 발급 쪽에서 C51-14 수정(VERIFY:84, 다른 팀은 `admin:write` 만).
- `config.py`: `app_env` 기본 `local`(fail-open) [중간] — 기존 결정(배포 산출물이 prod 고정). `security.py` 는 [낮음]만(워크로드 시크릿에도 scrypt p=5 적용되는 비용 문제 등).
