"""End-to-end node typesafety through the full @prismal_node stack (Phase NTS — NTS4-07).

Exercises the whole ``DEFAULT_MIDDLEWARE_STACK`` (error_mapping outermost →
node_io_validation innermost): ``warn`` passes a malformed output through;
``enforce`` raises ``NodeValidationError``, which ``error_mapping_middleware``
maps to a ``metadata.error`` update — unless ``raise_on_error=True``.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from prismal.agents.extension.decorators import prismal_node
from prismal.core.config import Settings, get_settings
from prismal.core.exceptions import NodeValidationError


class _Out(BaseModel):
    current_agent: str


def _patch_settings(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    settings = Settings(node_typesafety_enabled=True, node_typesafety_mode=mode)
    monkeypatch.setattr(
        "prismal.agents.extension._middleware.get_settings",
        lambda: settings,
        raising=False,
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_warn_passes_malformed_output_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, "warn")

    @prismal_node(name="e2e_warn", security="off", audit=False, output_model=_Out)
    async def node(_state):
        return {"wrong_key": "x"}  # violates _Out

    out = await node({"session_id": "s", "messages": []})
    assert out == {"wrong_key": "x"}  # passed through unchanged, no error mapped


async def test_enforce_maps_error_via_error_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, "enforce")

    @prismal_node(name="e2e_enforce", security="off", audit=False, output_model=_Out)
    async def node(_state):
        return {"wrong_key": "x"}

    out = await node({"session_id": "s", "messages": []})
    # error_mapping_middleware caught NodeValidationError → metadata.error update
    # (graceful degradation, not a raise). The mapped payload identifies the
    # failing node and preserves the schema-error text (field name, no value).
    assert "metadata" in out
    err = out["metadata"]["error"]
    assert err["node"] == "e2e_enforce"
    assert err["timeout"] is False
    assert "current_agent" in err["message"]


async def test_enforce_raises_when_raise_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, "enforce")

    @prismal_node(
        name="e2e_raise",
        security="off",
        audit=False,
        raise_on_error=True,
        output_model=_Out,
    )
    async def node(_state):
        return {"wrong_key": "x"}

    with pytest.raises(NodeValidationError) as exc:
        await node({"session_id": "s", "messages": []})
    assert exc.value.direction == "output"


async def test_valid_output_passes_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, "enforce")

    @prismal_node(name="e2e_ok", security="off", audit=False, output_model=_Out)
    async def node(_state):
        return {"current_agent": "e2e_ok"}

    out = await node({"session_id": "s", "messages": []})
    assert out == {"current_agent": "e2e_ok"}
