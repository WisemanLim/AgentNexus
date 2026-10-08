> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · 2차 리뷰(round2) · 스냅샷 2026-10-07 21:03 KST · [← INDEX.md](../INDEX.md) · 디렉터리 개요: [app_Abstract.md](app_Abstract.md) · 1차 리뷰: [db.py.md](../../../CODE-REVIEW-Lv2/app/db.py.md)

### 04. app/db.py
**파일 요약 / File Summary**
- 역할 / Role: SQLAlchemy 엔진·세션 팩토리·선언 Base·UTC 시각 타입. SQLite(local)·PostgreSQL(prod) 공용, 2차에서 PostgreSQL 장애조치(HA) 대비 연결 인자 추가.
- 진입점 거리 / Distance: 1 (`app/main.py:13`)
- LOC: 85 (현재 `wc -l`) · 1차 80 → +5
- 주요 export: `Base`, `UTCDateTime`, `utcnow`, `engine`, `SessionLocal`, `ping`, `get_db`, `session_scope`
- 의존(내부): `app.core.config.settings`
- 외부 패키지: sqlalchemy 2.x, (드라이버) psycopg 3(`pyproject.toml:15` `psycopg[binary]>=3.2` — MySQL 계열 드라이버는 의존성에 없음)
- 부수효과 / Side effects: import 시점에 엔진 생성(L55), SQLite 면 상위 디렉터리 생성(L45)
- 동시성 / Concurrency: 엔진·커넥션 풀은 스레드 안전, `Session` 은 스레드·코루틴 간 공유 금지(호출부가 `with SessionLocal()` 로 짧게 사용)
- 테스트 커버리지 흔적: 전 시험이 간접 사용, `test/conftest.py:13-14`(`TEST_DATABASE_URL` 이 있으면 PostgreSQL 로 같은 시험 — `make test-pg`). 2차 엔진 인자(connect_timeout·keepalive·pool_recycle·pool_timeout)는 단위 시험이 없고 kind 장애조치 시험으로만 확인(`test/impl/46th/result.md:3-7`, `test/impl/46th/scenario.md:8`).
- 2차 변경 요약 / Changes since 1st review: L47-52 — PostgreSQL 이면 libpq 연결 인자 `connect_timeout=5`·TCP keepalive(idle 10·interval 5·count 3), 모든 비 SQLite 엔진에 `pool_recycle=1800`·`pool_timeout=10` 추가(Epic 46 D18-③ HA). 나머지(UTC 타입·세션 함수)는 1차와 동일.

## 라인 리뷰 / Line Walk (Level 2)

```python path=app/db.py lines=1-14
"""wise-agentnexus — DB 엔진 (SQLite / PostgreSQL / MySQL / MariaDB 공용, DATABASE_URL 로 결정)."""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, TypeDecorator, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass
```
**분석 / Analysis (Level 2)**
- L7-8: SQLAlchemy 2.x 스타일 — `DeclarativeBase` 상속 Base, `sessionmaker`.
- L13-14 `Base`: 모든 ORM 모델의 부모(`app/models.py`). Alembic 이 `Base.metadata` 로 스키마를 비교한다.
**짚어볼 점 / Watch-outs**
- [위험:낮음] L1 docstring 은 MySQL/MariaDB 공용이라 하지만, 의존성에 MySQL 드라이버가 없고(`pyproject.toml:15` 는 psycopg 만) 2차 HA 인자도 PostgreSQL 에만 준다(L49-50). `DATABASE_URL=mysql+pymysql://…` 로 바꾸면 드라이버 import 오류로 기동 실패하거나, 드라이버를 따로 넣어도 연결 시간 제한·keepalive 없이 동작한다. 최소 수정: docstring 을 "SQLite·PostgreSQL" 로 좁히거나 MySQL 용 `connect_timeout` 분기와 선택 의존성(extra)을 추가.

```python path=app/db.py lines=17-39
class UTCDateTime(TypeDecorator):
    """항상 tz-aware UTC 로 저장·반환 (SQLite 는 naive 로 저장되므로 읽을 때 UTC 부여)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
```
**분석 / Analysis (Level 2)**
- L17-21: `TypeDecorator` 로 `DateTime(timezone=True)` 를 감싼 사용자 정의 타입. `cache_ok = True` 는 SQL 컴파일 캐시에 안전하다고 알리는 선언(없으면 경고 + 캐시 비활성).
- L23-28 쓰기: naive 는 UTC 로 간주해 tz 부여, aware 는 UTC 로 변환 — DB 에는 항상 UTC.
- L30-35 읽기: SQLite 는 tz 를 저장하지 않아 naive 로 돌아오므로 UTC 를 다시 붙인다. PostgreSQL `timestamptz` 는 aware 로 온다.
- L38-39 `utcnow()`: tz-aware 현재 시각 — `datetime.utcnow()`(naive, 3.12 deprecated) 대신 쓰는 올바른 방식.
- 이 타입의 핵심 가치: `row.expires_at < utcnow()` 같은 비교가 항상 aware↔aware 로 맞춰진다. 다른 DateTime 타입 컬럼을 섞으면 `TypeError: can't compare offset-naive and offset-aware datetimes`.
- 집계식의 결과 타입: SQLAlchemy 의 `func.min`·`func.max`·`func.sum` 은 인자 타입을 그대로 물려받는(ReturnTypeFromArgs) 함수라 보통 이 타입의 결과 처리(`process_result_value`)가 적용된다. `app/api/observe.py:128-129` 는 그래도 `type_coerce(func.min(...), UTCDateTime())` 로 명시해 둔다(의도 명시·방어적). 반면 `text()` 원문 SQL·타입 없는 `literal_column` 으로 만든 식은 타입 정보가 없어 SQLite 에서 문자열/naive 값이 그대로 올 수 있으니 직접 `type_coerce` 해야 한다(주니어가 놓치기 쉬움).
- "naive = UTC" 가정은 호출부가 로컬 시각 naive 를 넘기면 조용히 틀린 시각을 저장한다. 코드베이스는 `utcnow()` 를 일관되게 써서 실제 위험은 낮다. 예외: API 쿼리 파라미터 `since: datetime`(`app/api/observe.py:334`)에 클라이언트가 오프셋 없는 시각을 주면 UTC 로 해석된다(문서화 필요 수준).

```python path=app/db.py lines=42-56
def _make_engine():
    url = settings.database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    # 장애조치(HA primary 교체)·네트워크 단절 대비 (D18-③): 연결 시도 5초 제한 · TCP keepalive 로 죽은 연결을 빨리 감지 ·
    # 풀 연결은 30분마다 교체 · 체크아웃 때 pre_ping. (psycopg/libpq 연결 인자)
    args = {"connect_timeout": 5, "keepalives": 1, "keepalives_idle": 10, "keepalives_interval": 5,
            "keepalives_count": 3} if url.startswith("postgresql") else {}
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20, pool_recycle=1800, pool_timeout=10,
                         connect_args=args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
```
**분석 / Analysis (Level 2)**
- L44-46 SQLite: 파일 상위 디렉터리 생성(`sqlite:////app/data/app.db` → `/app/data`), `check_same_thread=False`(스레드풀의 다른 스레드가 풀에서 같은 연결을 꺼내 쓰는 것 허용), `timeout=30`(sqlite3 의 잠금 대기 = busy timeout 30초). SQLAlchemy 2.x 는 파일 SQLite 에 `QueuePool` 을 기본으로 쓴다.
- L47-50 (2차 신규, PostgreSQL 전용): `connect_args` 는 드라이버 `connect()` 에 그대로 전달되고, psycopg 3 은 이를 libpq 연결 키워드로 넘긴다. `connect_timeout=5` — 새 연결 수립 대기 상한(장애조치 중 죽은 primary 로의 연결 시도가 무한정 걸리지 않게). `keepalives*` — TCP keepalive: 10초 유휴 후 5초 간격 3회 무응답이면 소켓을 끊는다 → 쿼리 도중 primary 가 사라졌을 때 대략 25초 안에 `OperationalError` 로 깨어난다(keepalive 가 없으면 OS 기본값 수 시간까지 걸릴 수 있음). `url.startswith("postgresql")` 이라 `postgresql+psycopg://`(`.env.example:11`, Helm `templates/secret.yaml:15`) 모두 해당.
- L51-52: `pool_pre_ping=True` — 풀에서 꺼낼 때 `SELECT 1` 류 핑으로 끊긴 연결을 버리고 재연결. `pool_size=10`·`max_overflow=20` → 워커당 최대 30연결. `pool_recycle=1800`(2차) — 30분 넘은 연결은 꺼낼 때 교체(장애조치 후 오래된 연결·프록시 유휴 절단 대비). `pool_timeout=10`(2차, 기본 30) — 풀이 가득 차면 10초 기다린 뒤 `sqlalchemy.exc.TimeoutError`.
- L55-56: `autoflush=False`(쿼리 전 자동 flush 끔 — 명시적 commit/flush 에 의존, SQLite 에서 읽기 쿼리가 쓰기 잠금을 잡는 일을 줄인다), `expire_on_commit=False`(commit 후에도 속성을 재조회하지 않아 세션을 닫은 뒤 객체를 반환하는 패턴이 가능 — 대신 같은 세션을 오래 쓰면 다른 트랜잭션 변경을 못 본다).
**짚어볼 점 / Watch-outs**
- [위험:낮음] L51 풀 크기가 코드 상수이고 설정으로 노출되지 않는다(1차 지적 그대로). Helm 에서 autoscaling 을 켜면 `maxReplicas 6` × `workers 2`(`deploy/helm/agentnexus/values.yaml:19`·`:30-33`) × 30 = **최대 360 연결**로 PostgreSQL 기본 `max_connections` 100 을 넘는다 — 부하가 커서 확장된 바로 그 순간 `too many clients` 로 실패한다. 2차 `pool_timeout=10` 으로 풀 고갈 시 10초 뒤 `TimeoutError` 가 나는데, 일반 예외 처리기가 없어(`app/core/errors.py:55-75`) text/plain 500 으로 나간다. 최소 수정: `DB_POOL_SIZE`·`DB_MAX_OVERFLOW` 설정 노출 + Helm 값에서 `replicas × workers × (size+overflow) < max_connections` 계산 안내(또는 PgBouncer).
- [위험:낮음] L46 SQLite 연결에 PRAGMA 설정이 없다. (a) WAL 미설정(1차 지적 그대로) — 기본 rollback journal 에서 쓰기 중 읽기도 막혀 `database is locked`(30초 대기 후)가 나기 쉽다(local 은 단일 워커 `.env.local:15` 라 완화). (b) **`PRAGMA foreign_keys=ON` 이 없어** SQLite 는 FK 를 강제하지 않는다 — 모델에 `ondelete="CASCADE"` FK 가 8개 있다(`app/models.py:47`·`:235`·`:431`·`:457`·`:496` 등). local(SQLite)에서는 고아 행 삽입·부모 삭제가 조용히 통과하고 prod(PostgreSQL)에서만 `IntegrityError` 가 나는 개발/운영 불일치가 생긴다(실제 영향 경로는 가능성 — 현재 보존 파기 `app/services/retention.py:28-34` 는 자식 테이블만 지운다). 최소 수정: `@event.listens_for(engine, "connect")` 에서 `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=30000`.
- [위험:낮음] L49-52 문장 시간 제한(statement timeout)이 없다. 연결 수립(5초)과 죽은 TCP(약 25초)는 막았지만, 살아 있는 DB 에서 느린 쿼리(예: `app/api/observe.py:260` 의 인덱스 없는 `run_events` 전체 스캔)는 연결을 무기한 점유한다 → 풀(30) 고갈 → 다른 요청이 `pool_timeout` 10초 후 500. 최소 수정: PostgreSQL 이면 `args["options"] = "-c statement_timeout=30000"`(필요한 배치 작업은 세션 단위로 `SET LOCAL` 해제).

```python path=app/db.py lines=59-85
def ping() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


def get_db() -> Iterator[Session]:
    """FastAPI 의존성 — 요청 단위 세션."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """서비스·백그라운드 작업용 — 성공 시 commit, 예외 시 rollback."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```
**분석 / Analysis (Level 2)**
- L59-62 `ping`: `with engine.connect()` 로 연결을 풀에 반환. `/health`(`app/main.py:197`)가 사용. 풀이 가득 차면 `pool_timeout`(10초)까지 기다릴 수 있다.
- L65-71 `get_db`: FastAPI `Depends` 용 제너레이터 — 요청이 끝나면 `finally` 에서 닫는다. commit 은 엔드포인트 책임. 예외 시 명시적 rollback 은 없지만 `close()` 가 진행 중 트랜잭션을 롤백하므로 동작은 안전하다.
- L74-85 `session_scope`: 성공 시 commit, `Exception` 이면 rollback 후 재발생, 항상 close — 표준 Unit-of-Work. `except Exception` 은 `asyncio.CancelledError`·`KeyboardInterrupt`(BaseException)를 잡지 않지만 그 경우에도 `finally` 의 `close()` 가 롤백한다.
- 사용 현황(2차 검색): `session_scope` 사용 8곳 vs `with SessionLocal() as` 169곳 — 대부분 수동 `db.commit()` 패턴이다. 예외 시 commit 이 생략되고 close 에서 롤백되므로 안전하지만, 두 패턴이 섞여 "어디서 commit 하는가" 를 매번 확인해야 한다(1차와 동일, 결함은 아님).

## 파일 평가 / File Assessment
- 강점: (1) UTC 일관성을 타입 수준에서 강제. (2) 2차 HA 인자 — `connect_timeout`·TCP keepalive·`pool_recycle`·`pool_pre_ping` 조합으로 장애조치 후 죽은 연결을 빠르게 버린다(kind 장애조치 15/15, `test/impl/46th/result.md:3`). (3) 짧은 세션 수명 패턴을 돕는 `expire_on_commit=False`·`session_scope`.
- 위험: (1) [위험:낮음] 풀 크기 상수 × 워커·파드 → 연결 고갈 — `app/db.py:51`. (2) [위험:낮음] SQLite PRAGMA(WAL·foreign_keys) 미설정 → 잠금 경합·FK 미강제 — `app/db.py:46`. (3) [위험:낮음] statement timeout 부재 → 느린 쿼리가 풀 점유 — `app/db.py:49-52`.
- 개선 권고(영향순): (1) 풀 크기 설정 노출 + Helm 계산 안내. (2) SQLite `connect` 이벤트에서 WAL·`foreign_keys=ON`. (3) PostgreSQL `statement_timeout`. (4) 서비스 계층 세션 패턴을 `session_scope` 로 수렴.
- 파일 위험 등급 / Risk grade: 낮음 — [위험:낮음]만 있음. HA 인자 추가는 올바른 방향.
- 재읽기 권고 레벨: 3 — 짧은 기반 모듈, 엔진 인자와 세션 경계만 다시 보면 된다.

## 1차 리뷰 대비 / vs 1st Review
| 1차 지적 (1차 보고서 위치·요지) | 2차 상태 | 근거(현재 path:line) |
|---|---|---|
| L26-27 "naive = UTC" 가정 — 로컬 naive 를 넘기면 틀린 시각 | 미해결(설계, 코드베이스가 `utcnow()` 일관 사용) | L26-27 |
| [낮음] 워커 × (10+20) 연결 → PostgreSQL `max_connections` 고갈 | 미해결 | L51 값 그대로·설정 미노출; `deploy/helm/agentnexus/values.yaml:19`·`:30-33` |
| SQLite WAL 미설정 → `database is locked` | 미해결 | L46 (PRAGMA 없음) |
| `expire_on_commit=False` → 오래 쓰는 세션의 오래된 객체 | 미해결(설계) | L56 |
| `get_db` 예외 시 rollback 미명시(close 가 롤백 — 안전) | 미해결(결함 아님) | L65-71 |
| 세션 패턴 2종 혼용(`session_scope` vs 수동 commit) | 미해결 | `session_scope` 8곳 vs `with SessionLocal() as` 169곳(저장소 검색) |
