"""Tests for the @prismal_node hardening middleware (Phase H — H5-03)."""

from __future__ import annotations

import pytest

from prismal.agents.extension._middleware import hardening_middleware
from prismal.agents.extension.decorators import NodeMetadata
from prismal.core.config import Settings, get_settings
from prismal.security.taint import Provenance, TaintRegistry, mark_untrusted_active


def _meta() -> NodeMetadata:
    return NodeMetadata(
        name="n",
        capabilities=(),
        security="standard",
        audit=False,
        retry=None,
        timeout_s=None,
        raise_on_error=False,
        registered_at="t",
        source_module="m",
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_disabled_is_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prismal.core.config.get_settings", lambda: Settings(hardening_enabled=False)
    )
    monkeypatch.setattr(
        "prismal.agents.extension._middleware.get_settings",
        lambda: Settings(hardening_enabled=False),
        raising=False,
    )

    async def node(_state):
        # No active registry when disabled.
        assert mark_untrusted_active("x", Provenance.TOOL) is None
        return {"messages": [], "ok": True}

    out = await hardening_middleware(node, {"session_id": "s", "metadata": {}}, _meta())
    assert out["ok"] is True


async def test_taint_registry_active_during_node(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(hardening_enabled=True, taint_tracking_enabled=True)
    monkeypatch.setattr(
        "prismal.agents.extension._middleware.get_settings", lambda: settings, raising=False
    )
    reg = TaintRegistry()
    monkeypatch.setattr(
        "prismal.agents.extension._middleware.get_taint_registry",
        lambda _state: reg,
        raising=False,
    )

    async def node(_state):
        mark_untrusted_active("loader output", Provenance.RAG)
        return {"messages": []}

    await hardening_middleware(node, {"session_id": "s", "metadata": {}}, _meta())
    assert reg.is_untrusted("loader output")


async def test_pii_redaction_on_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.messages import AIMessage

    settings = Settings(hardening_enabled=True, hardening_pii_output=True)
    monkeypatch.setattr(
        "prismal.agents.extension._middleware.get_settings", lambda: settings, raising=False
    )
    monkeypatch.setattr(
        "prismal.agents.extension._middleware.get_taint_registry",
        lambda _state: None,
        raising=False,
    )

    async def node(_state):
        return {"messages": [AIMessage(content="reach me at jane@example.com")]}

    out = await hardening_middleware(node, {"session_id": "s", "metadata": {}}, _meta())
    assert "jane@example.com" not in str(out["messages"][0].content)
    assert "[EMAIL]" in str(out["messages"][0].content)
