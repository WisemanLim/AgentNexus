"""Alembic env — DATABASE_URL(설정) 사용, 모델 메타데이터 자동 감지."""
from alembic import context
from sqlalchemy import text

import app.models  # noqa: F401 — 메타데이터 등록
from app.db import Base, UTCDateTime, engine

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """autogenerate 시 UTCDateTime 을 표준 타입으로 렌더 (마이그레이션이 app 코드에 의존하지 않도록)."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(url=str(engine.url), target_metadata=target_metadata, literal_binds=True,
                      render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite", render_item=render_item)
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":  # 다중 파드(Helm)가 동시에 기동해도 마이그레이션은 하나씩
            # 앱 세션 상한(D22-①)은 마이그레이션에 쓰지 않는다 — 큰 테이블 인덱스·다른 파드의 잠금을 기다릴 수 있다
            connection.execute(text("SET LOCAL statement_timeout = 0"))
            connection.execute(text("SET LOCAL lock_timeout = 0"))
            connection.execute(text("SELECT pg_advisory_xact_lock(727274101)"))
        context.run_migrations()


def run_migrations_online() -> None:
    injected = context.config.attributes.get("connection")  # 테스트 등에서 주입한 연결
    if injected is not None:
        _run(injected)
        return
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
