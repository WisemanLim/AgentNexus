# app/ — 디렉터리 개요 / Directory Abstract
> [← INDEX.md](../INDEX.md)

애플리케이션 루트 패키지. FastAPI 앱 조립(`main.py`)·gRPC 수신 진입점(`grpc_server.py`)·ORM 모델 전체(`models.py`)·시드(`seed.py`)가 있고, 하위 패키지로 `core`(인프라)·`api`(HTTP 라우터)·`services`(도메인 로직)·`builtin`(데모 서버)·`cli`(nexus)가 있다.

## 리뷰한 파일
| 파일 | LOC | 역할 | 리뷰 |
|---|--:|---|---|
| `main.py` | 195 | FastAPI 앱 조립·lifespan 백그라운드 작업·콘솔 서빙·헬스 | [main.py.md](main.py.md) |
| `grpc_server.py` | 23 | A2A gRPC 수신 프로세스 진입점(게이트웨이로 위임) | [grpc_server.py.md](grpc_server.py.md) |
| `models.py` | 1036 | SQLAlchemy ORM 모델 전체(약 50 엔터티) | [models.py.md](models.py.md) |

## 미선택(부록) — `db.py` 는 리뷰됨(아래)·`seed.py`·`__init__.py` 는 부록
`db.py`(80) 는 엔진·세션·UTCDateTime — [db.py.md](db.py.md). `seed.py`(391) 멱등 시드는 부록 A.

## 구조 요약
`main.py` 가 모든 하위 모듈을 엮는 조립 지점이다. 의존 방향은 `api → services → core`·`models`·`db` 가 정상이며, 일부 서비스(`scheduler`·`metrics`·`tracing`)가 역방향(services→api, core→services)으로 지연 import 하는 계층 위반이 있다(종합 §4.2).

## 위험 신호
- `main.py` 의 헬스 루프·A2A 감시기가 워커마다 중복 실행(리스 없음).
- `models.py` 의 JSON 컬럼 제자리 변경 미추적(관례 의존)·정책 초안 수정자 미기록.
