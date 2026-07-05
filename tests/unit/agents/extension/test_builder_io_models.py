"""Tests for add_node(input_model=, output_model=) (Phase NTS — SPEC-NTS-BLD-001)."""

from __future__ import annotations

from pydantic import BaseModel

from prismal.agents.extension.builder import PrismalStateGraphBuilder
from prismal.agents.extension.decorators import prismal_node


class _In(BaseModel):
    session_id: str


class _Out(BaseModel):
    current_agent: str


async def _fn(state):  # type: ignore[no-untyped-def]
    return {"current_agent": "x"}


def test_add_node_forwards_io_models_on_auto_wrap() -> None:
    builder = PrismalStateGraphBuilder("p")
    builder.add_node("classify", _fn, input_model=_In, output_model=_Out)
    wrapped = builder._nodes["classify"]
    meta = wrapped.__prismal_node__
    assert meta.input_model is _In
    assert meta.output_model is _Out


def test_add_node_io_models_default_none() -> None:
    builder = PrismalStateGraphBuilder("p")
    builder.add_node("plain", _fn)
    meta = builder._nodes["plain"].__prismal_node__
    assert meta.input_model is None
    assert meta.output_model is None


def test_already_decorated_ignores_io_kwargs() -> None:
    @prismal_node(name="pre")
    async def pre(state):  # type: ignore[no-untyped-def]
        return {"current_agent": "x"}

    builder = PrismalStateGraphBuilder("p")
    builder.add_node("pre", pre, input_model=_In, output_model=_Out)
    # kwargs ignored — the pre-decorated node keeps its original (None) models.
    meta = builder._nodes["pre"].__prismal_node__
    assert meta.input_model is None
    assert meta.output_model is None
