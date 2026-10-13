"""``use_tool_provider``: scoped global tool provider (eval runner, tests).

``set_tool_provider`` cannot put back ``None``, so a caller that installs a
provider for one run had no public way to restore the previous state.
"""

from __future__ import annotations

import pytest

from prismal.agents import tool_registry
from prismal.agents.extension import FakeToolProvider, use_tool_provider
from prismal.agents.tool_registry import get_tool_provider, set_tool_provider


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_registry, "_provider", None)


def test_binds_for_the_block_and_restores_none() -> None:
    scoped = FakeToolProvider()
    with use_tool_provider(scoped):
        assert get_tool_provider() is scoped
    assert get_tool_provider() is None


def test_restores_the_previous_provider() -> None:
    host, scoped = FakeToolProvider(), FakeToolProvider()
    set_tool_provider(host)
    with use_tool_provider(scoped):
        assert get_tool_provider() is scoped
    assert get_tool_provider() is host


def test_restores_even_when_the_block_raises() -> None:
    with pytest.raises(RuntimeError), use_tool_provider(FakeToolProvider()):
        raise RuntimeError("boom")
    assert get_tool_provider() is None
