# app/core/ — 디렉터리 개요 / Directory Abstract
> [← INDEX.md](../../INDEX.md)

인프라 계층 — 설정·인증/인가·토큰 서명·키·오류·HTTP 팩토리·트레이싱·메트릭·로그·식별자. 거의 모든 상위 모듈이 의존(in-degree 최상위).

## 리뷰한 파일
| 파일 | LOC | 역할 | 리뷰 |
|---|--:|---|---|
| `config.py` | 90 | 환경 설정 싱글턴·프로토콜 버전 상수 | [config.py.md](config.py.md) |
| `auth.py` | 295 | Principal·RBAC·토큰 유형별 해석·FastAPI 의존성 | [auth.py.md](auth.py.md) |
| `http.py` | 225 | 아웃바운드 HTTP 팩토리·traceparent·egress 가드·시험 마운트 | [http.py.md](http.py.md) |
| `tracing.py` | 226 | OpenTelemetry 설정·스팬 저장·마스킹·ASGI 미들웨어 | [tracing.py.md](tracing.py.md) |
| `metrics.py` | 99 | Prometheus 메트릭·DB 게이지·멀티프로세스 렌더 | [metrics.py.md](metrics.py.md) |
| `errors.py` | 75 | ApiError·problem+json/OpenAI 오류 변환 | [errors.py.md](errors.py.md) |
| `security.py` | 67 | JWT 서명·검증·scrypt 비밀번호·불투명 토큰·PKCE | [security.py.md](security.py.md) |

## 미선택(부록)
`keys.py`(85)·`logs.py`(112)·`context.py`(17)·`ids.py`(30)·`__init__.py` 는 부록 A.

## 구조 요약
`config.py` 가 최하위(의존 없음), 그 위에 `keys`·`security`·`errors`·`http`·`ids`, 그 위에 `auth`(모델·DB 사용)·`tracing`·`metrics`·`logs`. `tracing`·`logs` 가 `services.guards.mask_pii` 를 지연 import 하는 역방향 의존이 있다.

## 위험 신호
- `config.py` `app_env` 기본 `local`(fail-open), `seed_demo` 기본 True.
- `auth.py` 객체 수준 인가 부재(권한만 확인) — MCP·A2A 관리 API 의 시크릿 유출 경로와 결합.
- `http.py` egress 가드가 링크 로컬·IMDS 로 좁고 DNS 재바인딩 TOCTOU.
