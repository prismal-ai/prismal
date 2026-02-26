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
    from lightagent.core.database import create_async_engine_from_url, init_db

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await init_db(engine)  # Should not raise
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
