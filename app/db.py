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


def _make_engine():
    url = settings.database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    # 장애조치(HA primary 교체)·네트워크 단절 대비 (D18-③): 연결 시도 5초 제한 · TCP keepalive 로 죽은 연결을 빨리 감지 ·
    # 풀 연결은 30분마다 교체 · 체크아웃 때 pre_ping. (psycopg/libpq 연결 인자)
    # 문장·잠금 대기 상한(D22-①)은 세션 옵션으로 — 요청마다 SET 하지 않는다
    args = {"connect_timeout": 5, "keepalives": 1, "keepalives_idle": 10, "keepalives_interval": 5, "keepalives_count": 3,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms} -c lock_timeout={settings.db_lock_timeout_ms} "
                       f"-c idle_in_transaction_session_timeout={settings.db_idle_tx_timeout_ms}"} if url.startswith("postgresql") else {}
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20, pool_recycle=1800, pool_timeout=10,
                         connect_args=args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def unbounded(db: Session) -> None:
    """이 트랜잭션만 세션 상한(D22-①) 없이 — 보존 정리·감사 아카이브·체인 머리 잠금·시드처럼 오래 걸리거나 잠금을 기다리는 작업.
    상한에 걸려 롤백되면 같은 작업이 다음 주기에 더 커져 반복된다 (PostgreSQL, 65th)."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET LOCAL statement_timeout = 0"))
        db.execute(text("SET LOCAL lock_timeout = 0"))


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
