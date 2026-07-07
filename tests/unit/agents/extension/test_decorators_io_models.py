"""Unit tests for input_model/output_model on @prismal_node (Phase NTS — SPEC-NTS-TYP-002).

Verifies the two new keyword-only parameters are accepted, threaded onto
``NodeMetadata``, and default to ``None`` so every pre-existing call site is
unaffected.
"""

from __future__ import annotations

from pydantic import BaseModel

from prismal.agents.extension.decorators import get_node_metadata, prismal_node


class _In(BaseModel):
    session_id: str


class _Out(BaseModel):
    current_agent: str


def test_metadata_io_models_default_none() -> None:
    @prismal_node(name="nts_default_node")
    async def node(state):  # type: ignore[no-untyped-def]
        return {"current_agent": "x"}

    meta = get_node_metadata("nts_default_node")
    assert meta is not None
    assert meta.input_model is None
    assert meta.output_model is None


def test_metadata_carries_declared_models() -> None:
    @prismal_node(name="nts_annotated_node", input_model=_In, output_model=_Out)
    async def node(state):  # type: ignore[no-untyped-def]
        return {"current_agent": "x"}

    meta = get_node_metadata("nts_annotated_node")
    assert meta is not None
    assert meta.input_model is _In
    assert meta.output_model is _Out


def test_only_input_model_declared() -> None:
    @prismal_node(name="nts_input_only", input_model=_In)
    async def node(state):  # type: ignore[no-untyped-def]
        return {"current_agent": "x"}

    meta = get_node_metadata("nts_input_only")
    assert meta is not None
    assert meta.input_model is _In
    assert meta.output_model is None
