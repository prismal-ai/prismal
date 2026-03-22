"""Unit tests for financial_analyst subgraph builder."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_register_financial_analyst_creates_registry_entry() -> None:
    """register_financial_analyst populates the SubgraphRegistry."""
    import lightagent.agents.subgraphs.financial.builder as builder_mod
    builder_mod._COMPILED_GRAPHS.clear()

    from lightagent.agents.subgraphs.financial.builder import register_financial_analyst

    mock_compiled = MagicMock()
    mock_factory = AsyncMock()
    mock_factory.build = AsyncMock(return_value=mock_compiled)
    mock_registry = MagicMock()
    mock_registry.get = MagicMock(return_value=None)
    mock_registry.register = AsyncMock()

    with (
        patch(
            "lightagent.agents.subgraphs.financial.builder.SubgraphFactory",
            return_value=mock_factory,
        ),
        patch(
            "lightagent.agents.subgraphs.financial.builder.SubgraphRegistry.get_instance",
            return_value=mock_registry,
        ),
    ):
        await register_financial_analyst(checkpointer_path=":memory:")

    mock_registry.register.assert_called_once()
    call_args = mock_registry.register.call_args
    assert call_args[0][0] == "financial_analyst"


@pytest.mark.asyncio
async def test_register_financial_analyst_idempotent() -> None:
    """Calling register twice does not re-register."""
    from lightagent.agents.subgraphs.financial.builder import register_financial_analyst

    mock_registry = MagicMock()
    mock_registry.get = MagicMock(return_value=MagicMock())  # already registered
    mock_registry.register = AsyncMock()

    with patch(
        "lightagent.agents.subgraphs.financial.builder.SubgraphRegistry.get_instance",
        return_value=mock_registry,
    ):
        await register_financial_analyst()

    mock_registry.register.assert_not_called()


@pytest.mark.asyncio
async def test_get_compiled_financial_analyst_returns_graph() -> None:
    """get_compiled_financial_analyst builds and returns a compiled graph."""
    import lightagent.agents.subgraphs.financial.builder as builder_mod
    builder_mod._COMPILED_GRAPHS.clear()

    mock_compiled = MagicMock()
    mock_factory = AsyncMock()
    mock_factory.build = AsyncMock(return_value=mock_compiled)

    with patch(
        "lightagent.agents.subgraphs.financial.builder.SubgraphFactory",
        return_value=mock_factory,
    ):
        result = await builder_mod.get_compiled_financial_analyst(
            checkpointer_path=":memory:"
        )

    assert result is mock_compiled


def test_builder_exports() -> None:
    """Builder exports the two required callables."""
    from lightagent.agents.subgraphs.financial.builder import (
        get_compiled_financial_analyst,
        register_financial_analyst,
    )
    assert callable(register_financial_analyst)
    assert callable(get_compiled_financial_analyst)
