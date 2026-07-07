"""Tests for node_io_validation_middleware (Phase NTS — SPEC-NTS-MDW-001).

Covers the three-way mode dispatch (off / warn / enforce), the master
``node_typesafety_enabled=False`` passthrough guarantee, and the ``_observe``
counter/log side effect.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from prismal.agents.extension._middleware import (
    DEFAULT_MIDDLEWARE_STACK,
    node_io_validation_middleware,
)
from prismal.agents.extension.decorators import NodeMetadata
from prismal.core.config import Settings, get_settings
from prismal.core.exceptions import NodeExecutionError, NodeValidationError


class _Out(BaseModel):
    current_agent: str


class _In(BaseModel):
    session_id: str


def _meta(*, input_model=None, output_model=None) -> NodeMetadata:
    return NodeMetadata(
        name="pilot",
        capabilities=(),
        security="standard",
        audit=False,
        retry=None,
        timeout_s=None,
        raise_on_error=False,
        registered_at="t",
        source_module="m",
        input_model=input_model,
        output_model=output_model,
    )


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **kwargs) -> None:
    settings = Settings(**kwargs)
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


# ── Master flag off ⇒ complete passthrough ──────────────────────────────────


async def test_disabled_is_passthrough_even_with_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, node_typesafety_enabled=False)
    seen = {}

    async def node(state):
        seen["state"] = state
        return {"malformed": True}  # would fail _Out, but validation is off

    meta = _meta(input_model=_In, output_model=_Out)
    out = await node_io_validation_middleware(node, {"session_id": "s"}, meta)
    assert out == {"malformed": True}
    assert seen["state"] == {"session_id": "s"}  # unmodified


# ── mode="off" ⇒ no validation even when enabled ────────────────────────────


async def test_mode_off_skips_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, node_typesafety_enabled=True, node_typesafety_mode="off")

    async def node(_state):
        return {"malformed": True}

    out = await node_io_validation_middleware(node, {"session_id": "s"}, _meta(output_model=_Out))
    assert out == {"malformed": True}


# ── warn ⇒ passes malformed through, does not raise ─────────────────────────


async def test_warn_passes_malformed_output_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, node_typesafety_enabled=True, node_typesafety_mode="warn")

    async def node(_state):
        return {"not_current_agent": "x"}  # fails _Out

    out = await node_io_validation_middleware(node, {"session_id": "s"}, _meta(output_model=_Out))
    assert out == {"not_current_agent": "x"}  # unchanged, no raise


async def test_warn_valid_output_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, node_typesafety_enabled=True, node_typesafety_mode="warn")

    async def node(_state):
        return {"current_agent": "pilot"}

    out = await node_io_validation_middleware(node, {"session_id": "s"}, _meta(output_model=_Out))
    assert out == {"current_agent": "pilot"}


# ── enforce ⇒ raises NodeValidationError, caught as NodeExecutionError ───────


async def test_enforce_raises_on_bad_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, node_typesafety_enabled=True, node_typesafety_mode="enforce")

    async def node(_state):
        return {"not_current_agent": "x"}

    with pytest.raises(NodeValidationError) as exc:
        await node_io_validation_middleware(node, {"session_id": "s"}, _meta(output_model=_Out))
    assert exc.value.direction == "output"
    assert exc.value.schema_errors
    assert isinstance(exc.value, NodeExecutionError)


async def test_enforce_raises_on_bad_input_before_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, node_typesafety_enabled=True, node_typesafety_mode="enforce")
    called = {"node": False}

    async def node(_state):
        called["node"] = True
        return {"current_agent": "pilot"}

    with pytest.raises(NodeValidationError) as exc:
        # session_id missing ⇒ _In input validation fails
        await node_io_validation_middleware(node, {}, _meta(input_model=_In))
    assert exc.value.direction == "input"
    assert called["node"] is False  # node never invoked


async def test_enforce_valid_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, node_typesafety_enabled=True, node_typesafety_mode="enforce")

    async def node(_state):
        return {"current_agent": "pilot"}

    out = await node_io_validation_middleware(
        node, {"session_id": "s"}, _meta(input_model=_In, output_model=_Out)
    )
    assert out == {"current_agent": "pilot"}


# ── no declared models ⇒ passthrough even when enforce ──────────────────────


async def test_no_models_is_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, node_typesafety_enabled=True, node_typesafety_mode="enforce")

    async def node(_state):
        return {"anything": 1}

    out = await node_io_validation_middleware(node, {"session_id": "s"}, _meta())
    assert out == {"anything": 1}


# ── Stack placement: innermost ──────────────────────────────────────────────


def test_middleware_is_innermost_entry() -> None:
    assert DEFAULT_MIDDLEWARE_STACK[-1] is node_io_validation_middleware


# ── _observe increments counters ────────────────────────────────────────────


async def test_observe_increments_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, node_typesafety_enabled=True, node_typesafety_mode="warn")
    calls: list[tuple[str, dict]] = []

    class _FakeOtel:
        def increment_counter(self, metric, value=1, attributes=None):
            calls.append((metric, attributes or {}))

    monkeypatch.setattr(
        "prismal.agents.extension._middleware.OTelManager",
        lambda: _FakeOtel(),
    )

    async def node(_state):
        return {"not_current_agent": "x"}  # fails _Out

    await node_io_validation_middleware(node, {"session_id": "s"}, _meta(output_model=_Out))
    metrics = [m for m, _ in calls]
    assert "node_io_validated" in metrics
    assert "node_io_validation_failures" in metrics
    # direction label present
    assert any(attrs.get("direction") == "output" for _, attrs in calls)
