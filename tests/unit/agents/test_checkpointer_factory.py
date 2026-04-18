"""Tests for :func:`lightagent.agents.graph.build_checkpointer`.

Covers SPEC-038 AC-038-1, AC-038-2, AC-038-3, AC-038-4 and AC-038-6: the
factory selects the correct checkpointer backend based on the DB URL,
calls ``setup()`` for Postgres, and :class:`SubgraphFactory` falls back to
it when ``checkpointer_path`` is ``None``.

The ``langgraph-checkpoint-postgres`` package is intentionally not a hard
dependency of LightAgent, so the Postgres branch is exercised by
injecting a fake module into ``sys.modules``.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from lightagent.agents.graph import (
    _extract_sqlite_path,
    build_checkpointer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_fake_postgres_module() -> tuple[types.ModuleType, MagicMock, MagicMock]:
    """Install a fake ``langgraph.checkpoint.postgres.aio`` module.

    Returns the injected module plus the mocked ``AsyncPostgresSaver`` class
    and the saver instance that ``from_conn_string`` yields, so tests can
    assert how the factory interacted with them.
    """
    saver_instance = MagicMock(name="AsyncPostgresSaverInstance")
    saver_instance.setup = AsyncMock(return_value=None)

    cm = MagicMock(name="from_conn_string_cm")
    cm.__aenter__ = AsyncMock(return_value=saver_instance)
    cm.__aexit__ = AsyncMock(return_value=None)

    saver_cls = MagicMock(name="AsyncPostgresSaver")
    saver_cls.from_conn_string = MagicMock(return_value=cm)

    # Build the parent package chain so ``from langgraph.checkpoint.postgres.aio
    # import AsyncPostgresSaver`` resolves against our fakes.
    pkg = types.ModuleType("langgraph.checkpoint.postgres")
    aio_mod = types.ModuleType("langgraph.checkpoint.postgres.aio")
    aio_mod.AsyncPostgresSaver = saver_cls  # type: ignore[attr-defined]
    sys.modules["langgraph.checkpoint.postgres"] = pkg
    sys.modules["langgraph.checkpoint.postgres.aio"] = aio_mod
    return aio_mod, saver_cls, saver_instance


@pytest.fixture
def fake_postgres_module() -> Any:
    """Provide a fake Postgres saver module; clean up ``sys.modules`` on exit."""
    aio_mod, saver_cls, saver_instance = _install_fake_postgres_module()
    # Also reset the module-level CM list so assertions are deterministic
    from lightagent.agents import graph as graph_module

    original_cms = list(graph_module._checkpointer_cms)
    graph_module._checkpointer_cms.clear()
    try:
        yield aio_mod, saver_cls, saver_instance
    finally:
        graph_module._checkpointer_cms.clear()
        graph_module._checkpointer_cms.extend(original_cms)
        sys.modules.pop("langgraph.checkpoint.postgres.aio", None)
        sys.modules.pop("langgraph.checkpoint.postgres", None)


# ---------------------------------------------------------------------------
# _extract_sqlite_path
# ---------------------------------------------------------------------------


class TestExtractSqlitePath:
    """Unit tests for the internal URL-parsing helper."""

    def test_sqlite_plain_prefix(self) -> None:
        assert _extract_sqlite_path("sqlite:///data/db/test.db") == "data/db/test.db"

    def test_sqlite_aiosqlite_prefix(self) -> None:
        assert _extract_sqlite_path("sqlite+aiosqlite:///data/db/test.db") == "data/db/test.db"

    def test_empty_url_returns_memory(self) -> None:
        assert _extract_sqlite_path("") == ":memory:"

    def test_non_sqlite_url_returns_memory(self) -> None:
        assert _extract_sqlite_path("postgresql://user@host/db") == ":memory:"

    def test_sqlite_without_path_returns_memory(self) -> None:
        assert _extract_sqlite_path("sqlite:///") == ":memory:"


# ---------------------------------------------------------------------------
# build_checkpointer — SQLite branch
# ---------------------------------------------------------------------------


class TestBuildCheckpointerSqlite:
    """SQLite backend selection covers AC-038-1 (sqlite branch)."""

    async def test_none_url_returns_memory_saver(self) -> None:
        """``db_url=None`` falls back to ``settings.db_url`` or ``:memory:``."""
        with patch("lightagent.agents.graph.get_settings") as mock_settings:
            mock_settings.return_value.db_url = ""
            saver = await build_checkpointer(None)
        assert isinstance(saver, AsyncSqliteSaver)

    async def test_empty_url_returns_memory_saver(self) -> None:
        saver = await build_checkpointer("")
        assert isinstance(saver, AsyncSqliteSaver)

    async def test_unknown_scheme_returns_memory_saver(self) -> None:
        """Any non-postgres/non-sqlite URL falls back to in-memory SQLite."""
        saver = await build_checkpointer("mysql://host/db")
        assert isinstance(saver, AsyncSqliteSaver)

    async def test_sqlite_url_returns_sqlite_saver_with_path(self, tmp_path: Any) -> None:
        """``sqlite:///path`` opens an aiosqlite connection on ``path``."""
        db_file = tmp_path / "nested" / "checkpoints.db"
        url = f"sqlite:///{db_file}"
        saver = await build_checkpointer(url)
        assert isinstance(saver, AsyncSqliteSaver)
        # The parent directory should have been created automatically.
        assert db_file.parent.exists()
        # And the SQLite file is created by ``aiosqlite.connect()``.
        assert db_file.exists()

    async def test_sqlite_aiosqlite_url_accepted(self, tmp_path: Any) -> None:
        """``sqlite+aiosqlite:///path`` is equivalent to ``sqlite:///path``."""
        db_file = tmp_path / "checkpoints2.db"
        url = f"sqlite+aiosqlite:///{db_file}"
        saver = await build_checkpointer(url)
        assert isinstance(saver, AsyncSqliteSaver)
        assert db_file.exists()

    async def test_explicit_url_wins_over_settings(self) -> None:
        """An explicit ``db_url`` argument takes precedence over settings."""
        with patch("lightagent.agents.graph.get_settings") as mock_settings:
            mock_settings.return_value.db_url = "postgresql://should-not-be-used"
            # Empty string is an explicit value: factory should NOT read settings.
            saver = await build_checkpointer("")
        assert isinstance(saver, AsyncSqliteSaver)


# ---------------------------------------------------------------------------
# build_checkpointer — PostgreSQL branch
# ---------------------------------------------------------------------------


class TestBuildCheckpointerPostgres:
    """Postgres backend selection covers AC-038-1 + AC-038-4 (setup called)."""

    async def test_postgresql_url_returns_postgres_saver(self, fake_postgres_module: Any) -> None:
        _, saver_cls, saver_instance = fake_postgres_module
        saver = await build_checkpointer("postgresql://user:pw@localhost/app")
        assert saver is saver_instance
        saver_cls.from_conn_string.assert_called_once_with("postgresql://user:pw@localhost/app")

    async def test_postgresql_asyncpg_url_is_normalized(self, fake_postgres_module: Any) -> None:
        """The SQLAlchemy ``postgresql+asyncpg://`` prefix is normalized to
        plain ``postgresql://`` before being passed to psycopg-based
        ``AsyncPostgresSaver.from_conn_string``.
        """
        _, saver_cls, _ = fake_postgres_module
        await build_checkpointer("postgresql+asyncpg://user:pw@localhost/app")
        saver_cls.from_conn_string.assert_called_once_with("postgresql://user:pw@localhost/app")

    async def test_postgres_setup_called_on_init(self, fake_postgres_module: Any) -> None:
        """AC-038-4: ``setup()`` must run to create checkpoint tables."""
        _, _, saver_instance = fake_postgres_module
        await build_checkpointer("postgresql://user@host/db")
        saver_instance.setup.assert_awaited_once()

    async def test_postgres_cm_retained_for_lifetime(self, fake_postgres_module: Any) -> None:
        """The ``from_conn_string`` context manager must be kept alive so
        the underlying connection does not close after the factory returns.
        """
        from lightagent.agents import graph as graph_module

        assert graph_module._checkpointer_cms == []
        await build_checkpointer("postgresql://user@host/db")
        assert len(graph_module._checkpointer_cms) == 1

    async def test_postgres_url_via_settings(self, fake_postgres_module: Any) -> None:
        _, _, saver_instance = fake_postgres_module
        with patch("lightagent.agents.graph.get_settings") as mock_settings:
            mock_settings.return_value.db_url = "postgresql://user@host/db"
            saver = await build_checkpointer(None)
        assert saver is saver_instance


# ---------------------------------------------------------------------------
# SubgraphFactory integration (AC-038-3)
# ---------------------------------------------------------------------------


class TestSubgraphFactoryUsesBuildCheckpointer:
    """When ``checkpointer_path`` is ``None``, the factory defers to
    ``build_checkpointer()`` so subgraphs share the global backend.
    """

    async def test_none_path_delegates_to_build_checkpointer(self) -> None:
        from lightagent.agents.subgraphs.factory import SubgraphFactory
        from lightagent.agents.subgraphs.registry import SubgraphDefinition

        async def _noop(state: Any) -> dict[str, Any]:
            return {}

        definition = SubgraphDefinition(
            name="cp_test",
            description="checkpointer factory test",
            nodes={"only": _noop},
            entry_point="only",
            edges=[],
            conditional_edges={},
        )

        # The SubgraphFactory passes the returned checkpointer into
        # ``StateGraph.compile()``, which validates it is a real
        # ``BaseCheckpointSaver``.  Use a concrete in-memory saver as the
        # stand-in for the global checkpointer.
        from langgraph.checkpoint.memory import MemorySaver

        sentinel = MemorySaver()
        with patch(
            "lightagent.agents.graph.build_checkpointer",
            new=AsyncMock(return_value=sentinel),
        ) as mock_build:
            factory = SubgraphFactory()
            compiled = await factory.build(definition, checkpointer_path=None)

        mock_build.assert_awaited_once_with()
        assert compiled is not None
        # The compiled subgraph should have received the sentinel saver,
        # proving the delegation path was taken.
        assert compiled.checkpointer is sentinel

    async def test_string_path_preserves_legacy_behaviour(self, tmp_path: Any) -> None:
        """Passing a string path still uses an isolated ``AsyncSqliteSaver``
        and does NOT invoke ``build_checkpointer``.
        """
        from lightagent.agents.subgraphs.factory import SubgraphFactory
        from lightagent.agents.subgraphs.registry import SubgraphDefinition

        async def _noop(state: Any) -> dict[str, Any]:
            return {}

        definition = SubgraphDefinition(
            name="cp_legacy",
            description="legacy string-path checkpointer test",
            nodes={"only": _noop},
            entry_point="only",
            edges=[],
            conditional_edges={},
        )

        db_file = tmp_path / "legacy.db"
        with patch(
            "lightagent.agents.graph.build_checkpointer",
            new=AsyncMock(),
        ) as mock_build:
            factory = SubgraphFactory()
            compiled = await factory.build(definition, checkpointer_path=str(db_file))

        mock_build.assert_not_awaited()
        assert compiled is not None
        assert db_file.exists()
