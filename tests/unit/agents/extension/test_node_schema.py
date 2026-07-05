"""Unit tests for node I/O schema helpers (Phase NTS — SPEC-NTS-TYP-001).

Covers ``validate_node_input`` / ``validate_node_output``: the ``None``-model
no-op shortcut, narrow-projection semantics (extra keys ignored), and the
error shape — field *names* only, never field *values* — plus the frozen
``NodeIOValidationResult`` value object.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from prismal.agents.extension.node_schema import (
    NodeIOValidationResult,
    validate_node_input,
    validate_node_output,
)


class _CriticInput(BaseModel):
    iteration_count: int
    session_id: str


class _CriticOutput(BaseModel):
    current_agent: str


# ── None-model shortcut ─────────────────────────────────────────────────────


def test_validate_node_input_none_model_is_trivial_ok() -> None:
    result = validate_node_input({"anything": 1}, None, node_name="critic")
    assert result.ok is True
    assert result.errors == []
    assert result.node_name == "critic"
    assert result.direction == "input"


def test_validate_node_output_none_model_is_trivial_ok() -> None:
    result = validate_node_output({"anything": 1}, None, node_name="critic")
    assert result.ok is True
    assert result.errors == []
    assert result.direction == "output"


# ── Happy path ──────────────────────────────────────────────────────────────


def test_validate_node_input_valid_payload() -> None:
    state = {"iteration_count": 2, "session_id": "s1", "messages": []}
    result = validate_node_input(state, _CriticInput, node_name="critic")
    assert result.ok is True
    assert result.errors == []


def test_validate_node_output_valid_payload() -> None:
    update = {"current_agent": "critic", "messages": ["x"]}
    result = validate_node_output(update, _CriticOutput, node_name="critic")
    assert result.ok is True
    assert result.errors == []


# ── Narrow projection: extra keys ignored, undeclared keys never inspected ───


def test_extra_keys_are_ignored_not_rejected() -> None:
    # session_id/iteration_count declared; the rest of AgentState is ignored.
    state = {
        "iteration_count": 5,
        "session_id": "abc",
        "risk_score": 99.0,
        "metadata": {"secret": "value"},
    }
    result = validate_node_input(state, _CriticInput, node_name="critic")
    assert result.ok is True


# ── Failure: wrong type ─────────────────────────────────────────────────────


def test_validate_node_input_type_mismatch_fails() -> None:
    state = {"iteration_count": "not-an-int", "session_id": "s1"}
    result = validate_node_input(state, _CriticInput, node_name="critic")
    assert result.ok is False
    assert result.errors  # non-empty
    # Field name appears; the offending value must NOT leak.
    joined = " ".join(result.errors)
    assert "iteration_count" in joined
    assert "not-an-int" not in joined


# ── Failure: missing required field ─────────────────────────────────────────


def test_validate_node_output_missing_required_field_fails() -> None:
    update = {"messages": ["x"]}  # current_agent missing
    result = validate_node_output(update, _CriticOutput, node_name="critic")
    assert result.ok is False
    joined = " ".join(result.errors)
    assert "current_agent" in joined


def test_missing_field_value_never_leaks() -> None:
    class _Model(BaseModel):
        session_id: str

    update = {"session_id": 12345}  # wrong type; value is sensitive-ish
    result = validate_node_output(update, _Model, node_name="n")
    assert result.ok is False
    assert "12345" not in " ".join(result.errors)


# ── Never raises ────────────────────────────────────────────────────────────


def test_validate_never_raises_on_unusual_mapping() -> None:
    # A mapping whose value can't be coerced still yields a result, not an raise.
    result = validate_node_input({}, _CriticInput, node_name="critic")
    assert isinstance(result, NodeIOValidationResult)
    assert result.ok is False


# ── Value object is frozen ──────────────────────────────────────────────────


def test_result_is_frozen() -> None:
    result = validate_node_input({}, None, node_name="n")
    with pytest.raises((AttributeError, TypeError)):
        result.ok = False  # type: ignore[misc]
