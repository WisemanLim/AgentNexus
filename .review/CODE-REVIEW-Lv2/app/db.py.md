> 리뷰 레벨: 2 — ~3년 주니어 인수인계 수준 · [← INDEX.md](../INDEX.md) · 디렉터리 개요: [app_Abstract.md](app_Abstract.md)

### 10. app/db.py
**파일 요약 / File Summary**
- 역할 / Role: SQLAlchemy 엔진·세션 팩토리·선언 Base·UTC 시각 타입. SQLite(local)·PostgreSQL(prod) 공용.
- 진입점 거리 / Distance: 1 (`app/main.py:12`)
- LOC: 80
- 주요 export: `Base`, `UTCDateTime`, `utcnow`, `engine`, `SessionLocal`, `ping`, `get_db`, `session_scope`
- 의존(내부): `app.core.config.settings`
- 외부 패키지: sqlalchemy 2.x
- 부수효과 / Side effects: import 시점에 엔진 생성(L50), SQLite 면 상위 디렉터리 생성(L45)
- 동시성 / Concurrency: 엔진·커넥션 풀은 스레드 안전, 세션은 스레드/코루틴 간 공유 금지(각 호출부가 `with SessionLocal()` 로 짧게 사용)
- 테스트 커버리지 흔적: 전 시험이 간접 사용, `test/test_foundation.py`(마이그레이션)

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
- L13-14 `Base`: 모든 ORM 모델의 부모(`app/models.py:11`). Alembic `env.py` 가 `Base.metadata` 를 참조한다.

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
- L17-21 `UTCDateTime`: `TypeDecorator` 로 기존 `DateTime(timezone=True)` 를 감싼 사용자 정의 타입. `cache_ok = True` 는 SQL 컴파일 캐시에 안전하다고 알리는 필수 선언(없으면 경고).
- L23-28 쓰기: naive datetime 은 UTC 로 간주해 tz 부여, aware 면 UTC 로 변환 — DB 에는 항상 UTC.
- L30-35 읽기: SQLite 는 tz 정보를 저장하지 않아 naive 로 돌아오므로 UTC 를 다시 붙인다. PostgreSQL `timestamptz` 는 aware 로 온다.
- L38-39 `utcnow()`: tz-aware 현재 시각 — `datetime.utcnow()`(naive, 3.12 에서 deprecated) 대신 쓰는 올바른 방식.
**짚어볼 점 / Watch-outs**
- L26-27 의 "naive = UTC" 가정은 호출부가 로컬 시각 naive 를 넘기면 조용히 틀린 시각을 저장한다. 코드베이스는 `utcnow()` 를 일관되게 써서 실제 위험은 낮다.
- 비교 연산(`row.expires_at < utcnow()`)이 aware↔aware 로 맞춰지는 것이 이 타입의 핵심 가치다 — 다른 DateTime 컬럼 타입을 섞으면 `TypeError: can't compare offset-naive and offset-aware` 가 난다.

```python path=app/db.py lines=42-51
def _make_engine():
    url = settings.database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
```
**분석 / Analysis (Level 2)**
- L44-46 SQLite: 파일 상위 디렉터리 생성, `check_same_thread=False`(스레드풀에서 같은 연결 객체 사용 허용), `timeout=30`(잠금 대기 30초).
- L47 그 외(PostgreSQL 등): `pool_pre_ping=True`(풀에서 꺼낼 때 연결 생존 확인 — DB 재시작 후 끊긴 연결 재사용 방지), `pool_size=10`, `max_overflow=20` → 워커당 최대 30연결.
- L50-51: 엔진·세션 팩토리. `autoflush=False`(쿼리 전 자동 flush 끔 — 명시적 commit/flush 에 의존), `expire_on_commit=False`(commit 후에도 속성 접근 시 재조회하지 않음 — 세션을 닫은 뒤 객체를 반환하는 패턴(`audit.append` 의 `expunge`, 각종 `_load()`)이 가능해진다).
**짚어볼 점 / Watch-outs**
- [위험:낮음] 워커 2개 × (10+20) = 60, 파드 여러 개면 PostgreSQL `max_connections`(기본 100)에 금방 닿는다. Helm 값과 함께 조정 필요.
- SQLite 에서 WAL 모드(`PRAGMA journal_mode=WAL`)를 켜는 코드가 없다 — 기본(rollback journal) 모드에서는 쓰기 중 읽기도 막혀 `database is locked`(30초 대기 후)가 나기 쉽다. local 모드는 단일 워커(`UVICORN_WORKERS=1`)라 완화된다.
- `expire_on_commit=False` 는 commit 후 다른 트랜잭션의 변경을 보지 못하는 "오래된 객체" 를 만들 수 있다 — 같은 세션을 오래 쓰는 루프에서는 `db.refresh()` 가 필요하다.

```python path=app/db.py lines=54-80
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
- L54-57 `ping`: `with engine.connect()` 로 연결을 반환한다. `/health` 가 사용.
- L60-66 `get_db`: FastAPI `Depends` 용 제너레이터 — 요청이 끝나면 `finally` 에서 닫는다. commit 은 엔드포인트 책임.
- L69-80 `session_scope`: 백그라운드용 — 성공 시 commit, 예외 시 rollback 후 재발생, 항상 close. 표준 Unit-of-Work 패턴.
**짚어볼 점 / Watch-outs**
- `get_db` 는 예외 시 rollback 을 명시하지 않는다 — `close()` 가 미완료 트랜잭션을 롤백하므로 동작은 안전하다.
- 코드베이스 대부분은 `session_scope` 대신 `with SessionLocal() as db:` + 수동 `db.commit()` 을 쓴다(예: `app/services/mcp_gateway.py:324-328`). 예외 시 commit 이 생략되고 close 에서 롤백되므로 안전하지만, 두 패턴이 섞여 있어 "어디서 commit 하는가" 를 매번 확인해야 한다.

## 파일 평가 / File Assessment
- 강점: (1) UTC 일관성을 타입 수준에서 강제. (2) PostgreSQL `pool_pre_ping`. (3) 세션 수명이 짧은 패턴을 지원하는 `expire_on_commit=False`.
- 위험: (1) 연결 풀 크기 × 워커·파드 수 → DB 연결 고갈. (2) SQLite WAL 미설정 → 잠금 경합. (3) 세션 관리 패턴 2종 혼용.
- 개선 권고(영향순): (1) 풀 크기를 설정값으로 노출(`DB_POOL_SIZE`). (2) SQLite 연결 이벤트에서 WAL·`busy_timeout` 설정. (3) 서비스 계층은 `session_scope` 로 통일.
- 재읽기 권고 레벨: 3.
