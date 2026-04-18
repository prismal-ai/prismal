"""Async database engine and session factory using SQLAlchemy 2.x.

Supports SQLite (default, via aiosqlite), PostgreSQL (via asyncpg),
and MongoDB (via motor, handled separately in providers).

Usage::

    from lightagent.core.database import get_db_session, init_db


    # In FastAPI dependency:
    async def get_db() -> AsyncGenerator[AsyncSession]:
        async for session in get_db_session():
            yield session


    # In application startup:
    await init_db()
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from lightagent.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class Base(DeclarativeBase):
    """Declarative base class for all LightAgent SQLAlchemy models.

    All ORM model classes must inherit from this ``Base``.
    Tables are created via ``init_db()`` or Alembic migrations.
    """


def create_async_engine_from_url(db_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine for the given database URL.

    Applies ``check_same_thread=False`` for SQLite to support async usage.
    Enables SQL echo when ``settings.debug`` is True.

    Args:
        db_url: SQLAlchemy-compatible async database URL, e.g.:
            - ``sqlite+aiosqlite:///data/db/lightagent.db``
            - ``postgresql+asyncpg://user:pass@host/db``

    Returns:
        A configured ``AsyncEngine`` instance.
    """
    engine_kwargs: dict[str, object] = {
        "echo": get_settings().debug,
    }
    if db_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # PostgreSQL: configure connection pool for concurrency and resilience.
        engine_kwargs["pool_size"] = 2
        engine_kwargs["max_overflow"] = 8
        engine_kwargs["pool_pre_ping"] = True

    async_engine = create_async_engine(db_url, **engine_kwargs)

    if db_url.startswith("sqlite"):
        # Enable WAL mode (better concurrent read performance) and enforce
        # foreign key constraints (SQLite disables them by default).
        @event.listens_for(async_engine.sync_engine, "connect")
        def set_sqlite_pragmas(dbapi_conn: object, _connection_record: object) -> None:
            """Set SQLite pragmas for WAL mode and foreign key enforcement."""
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return async_engine


@lru_cache(maxsize=1)
def _get_engine() -> AsyncEngine:
    """Return the cached default async engine built from settings.db_url.

    Returns:
        The singleton ``AsyncEngine`` for the configured database.
    """
    return create_async_engine_from_url(get_settings().db_url)


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The ``AsyncEngine`` to bind sessions to.

    Returns:
        An ``async_sessionmaker`` producing ``AsyncSession`` instances.
    """
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


async def get_db_session(
    engine: AsyncEngine | None = None,
) -> AsyncGenerator[AsyncSession]:
    """Yield an async database session, committing or rolling back on exit.

    Intended for use as a FastAPI dependency or async context manager.

    Args:
        engine: Optional engine override. Defaults to the singleton engine
            built from ``settings.db_url``.

    Yields:
        An ``AsyncSession`` instance. Commits on normal exit, rolls back
        on any exception and re-raises it.

    Example::

        async def my_endpoint(db: AsyncSession = Depends(get_db_session)):
            result = await db.execute(select(MyModel))
    """
    resolved = engine if engine is not None else _get_engine()
    factory = get_session_factory(resolved)

    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Create all tables registered on ``Base``.

    Should be called once at application startup before any requests.
    In production, prefer Alembic migrations for schema changes — this
    function is a convenience shortcut for development and testing.

    Args:
        engine: Optional engine override. Defaults to the singleton engine.
    """
    resolved = engine if engine is not None else _get_engine()
    async with resolved.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_backend_type() -> Literal["sqlite", "postgresql"]:
    """Return the configured database backend type.

    Returns:
        ``"sqlite"`` if ``settings.db_url`` starts with ``sqlite``,
        ``"postgresql"`` otherwise.
    """
    return "sqlite" if get_settings().db_url.startswith("sqlite") else "postgresql"


def get_checkpointer() -> object:
    """Return a LangGraph async checkpoint saver for the configured database.

    Selects ``AsyncSqliteSaver`` for SQLite URLs and ``AsyncPostgresSaver``
    for PostgreSQL URLs.  Both implement the LangGraph
    ``BaseCheckpointSaver`` interface and support use as an async context
    manager::

        async with get_checkpointer() as checkpointer:
            graph = compiled_graph.with_config(checkpointer=checkpointer)

    Returns:
        An ``AsyncSqliteSaver`` or ``AsyncPostgresSaver`` instance.
        The return type is ``object`` because these classes are optional
        dependencies — callers should use them via the context-manager
        protocol rather than relying on the concrete type.

    Raises:
        ImportError: If the required optional package is not installed.
            For SQLite: ``pip install langgraph-checkpoint-sqlite``.
            For PostgreSQL: ``pip install 'lightagent[postgres]'``.
    """
    db_url = get_settings().db_url
    if db_url.startswith("sqlite"):
        try:
            from langgraph.checkpoint.sqlite.aio import (
                AsyncSqliteSaver,
            )
        except ImportError as exc:
            raise ImportError(
                "SQLite checkpointing requires langgraph-checkpoint-sqlite. "
                "Install with: pip install langgraph-checkpoint-sqlite"
            ) from exc
        sep = ":///"
        db_path = db_url.split(sep, 1)[1] if sep in db_url else ":memory:"
        return AsyncSqliteSaver.from_conn_string(db_path)

    try:
        from langgraph.checkpoint.postgres.aio import (
            AsyncPostgresSaver,
        )
    except ImportError as exc:
        raise ImportError(
            "PostgreSQL checkpointing requires langgraph-checkpoint-postgres. "
            "Install with: pip install 'lightagent[postgres]'"
        ) from exc
    # Strip asyncpg/psycopg driver prefix — PostgresSaver uses psycopg directly.
    pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )
    return AsyncPostgresSaver.from_conn_string(pg_url)


__all__ = [
    "Base",
    "create_async_engine_from_url",
    "get_backend_type",
    "get_checkpointer",
    "get_db_session",
    "get_session_factory",
    "init_db",
]
