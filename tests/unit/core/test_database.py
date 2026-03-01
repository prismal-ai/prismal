"""Unit tests for async database engine and session factory."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


@pytest.mark.asyncio
async def test_engine_created_for_sqlite() -> None:
    """create_async_engine_from_url returns AsyncEngine for SQLite URL."""
    from lightagent.core.database import create_async_engine_from_url

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    assert isinstance(engine, AsyncEngine)
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_session_factory_returns_sessionmaker() -> None:
    """get_session_factory returns an async_sessionmaker."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from lightagent.core.database import create_async_engine_from_url, get_session_factory

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    factory = get_session_factory(engine)
    assert isinstance(factory, async_sessionmaker)
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_db_session_yields_async_session() -> None:
    """get_db_session yields an AsyncSession."""
    from lightagent.core.database import create_async_engine_from_url, get_db_session

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    async for session in get_db_session(engine):
        assert isinstance(session, AsyncSession)
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_creates_tables_without_error() -> None:
    """init_db() runs without error on a clean in-memory database."""
    from lightagent.core.database import Base, create_async_engine_from_url, init_db

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await init_db(engine)

    # Verify tables can be inspected (confirms DB connection works post-init)
    async with engine.connect() as conn:
        table_names = await conn.run_sync(
            lambda sync_conn: sync_conn.dialect.get_table_names(sync_conn)
        )
        assert isinstance(table_names, list)  # No models yet, so empty list is correct

    await engine.dispose()


def test_base_is_declarative_base() -> None:
    """Base is a SQLAlchemy DeclarativeBase subclass."""
    from sqlalchemy.orm import DeclarativeBase

    from lightagent.core.database import Base

    assert issubclass(Base, DeclarativeBase)


@pytest.mark.asyncio
async def test_session_rolls_back_on_exception() -> None:
    """get_db_session rolls back and re-raises on exception."""
    from lightagent.core.database import create_async_engine_from_url, get_db_session

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")

    with pytest.raises(ValueError, match="test error"):
        async for session in get_db_session(engine):
            assert isinstance(session, AsyncSession)
            raise ValueError("test error")

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_db_session_commits_on_success() -> None:
    """get_db_session commits when no exception is raised."""
    from lightagent.core.database import create_async_engine_from_url, get_db_session

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    # If commit fails it raises; this test simply ensures no exception occurs
    async for session in get_db_session(engine):
        assert isinstance(session, AsyncSession)
    await engine.dispose()


# ── create_async_engine_from_url — PostgreSQL URL ─────────────────────────────


def test_create_async_engine_postgresql_url() -> None:
    """create_async_engine_from_url configures pool settings for PostgreSQL."""
    from unittest.mock import MagicMock, patch

    from lightagent.core.database import create_async_engine_from_url

    mock_engine = MagicMock()
    with patch("lightagent.core.database.create_async_engine", return_value=mock_engine) as mock_create:
        engine = create_async_engine_from_url(
            "postgresql+asyncpg://user:pass@localhost/testdb"
        )

    assert engine is mock_engine
    call_kwargs = mock_create.call_args[1]
    assert call_kwargs.get("pool_size") == 2
    assert call_kwargs.get("max_overflow") == 8
    assert call_kwargs.get("pool_pre_ping") is True
    assert "connect_args" not in call_kwargs


# ── get_backend_type ──────────────────────────────────────────────────────────


def test_get_backend_type_returns_sqlite() -> None:
    """get_backend_type() returns 'sqlite' for SQLite URLs."""
    from unittest.mock import patch

    from lightagent.core.database import get_backend_type

    with patch("lightagent.core.database.get_settings") as mock_settings:
        mock_settings.return_value.db_url = "sqlite:///data/db/lightagent.db"
        result = get_backend_type()

    assert result == "sqlite"


def test_get_backend_type_returns_postgresql() -> None:
    """get_backend_type() returns 'postgresql' for PostgreSQL URLs."""
    from unittest.mock import patch

    from lightagent.core.database import get_backend_type

    with patch("lightagent.core.database.get_settings") as mock_settings:
        mock_settings.return_value.db_url = "postgresql+asyncpg://localhost/db"
        result = get_backend_type()

    assert result == "postgresql"


# ── get_checkpointer ─────────────────────────────────────────────────────────


def test_get_checkpointer_sqlite_returns_saver() -> None:
    """get_checkpointer() returns an AsyncSqliteSaver for SQLite URLs."""
    import sys
    from unittest.mock import MagicMock, patch

    from lightagent.core.database import get_checkpointer

    mock_saver_instance = MagicMock()
    mock_saver_cls = MagicMock()
    mock_saver_cls.from_conn_string.return_value = mock_saver_instance

    mock_aio = MagicMock()
    mock_aio.AsyncSqliteSaver = mock_saver_cls

    with (
        patch("lightagent.core.database.get_settings") as mock_settings,
        patch.dict(
            sys.modules,
            {
                "langgraph.checkpoint.sqlite.aio": mock_aio,
            },
        ),
    ):
        mock_settings.return_value.db_url = "sqlite:///data/db/lightagent.db"
        checkpointer = get_checkpointer()

    assert checkpointer is mock_saver_instance
    mock_saver_cls.from_conn_string.assert_called_once_with(
        "data/db/lightagent.db"
    )


def test_get_checkpointer_sqlite_import_error_raises() -> None:
    """get_checkpointer() raises ImportError when checkpoint package is missing."""
    import sys
    from unittest.mock import patch

    import pytest

    from lightagent.core.database import get_checkpointer

    with (
        patch("lightagent.core.database.get_settings") as mock_settings,
        patch.dict(sys.modules, {"langgraph.checkpoint.sqlite.aio": None}),
    ):
        mock_settings.return_value.db_url = "sqlite:///data/db/lightagent.db"
        with pytest.raises(ImportError, match="langgraph-checkpoint-sqlite"):
            get_checkpointer()


def test_get_checkpointer_postgresql_import_error_raises() -> None:
    """get_checkpointer() raises ImportError for PostgreSQL when package missing."""
    import sys
    from unittest.mock import patch

    import pytest

    from lightagent.core.database import get_checkpointer

    with (
        patch("lightagent.core.database.get_settings") as mock_settings,
        patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": None}),
    ):
        mock_settings.return_value.db_url = "postgresql+asyncpg://localhost/db"
        with pytest.raises(ImportError, match="langgraph-checkpoint-postgres"):
            get_checkpointer()
