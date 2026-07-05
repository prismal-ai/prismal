"""Node I/O schema validation helpers (Phase NTS — SPEC-NTS-TYP-001).

An additive, opt-in contract layer for ``@prismal_node``: a node may declare a
narrow ``input_model``/``output_model`` (Pydantic ``BaseModel``) describing the
subset of ``AgentState`` it reads/writes. These helpers validate that subset.

Both helpers are **pure and never raise** — a ``None`` model is a trivial
``ok=True`` shortcut, which is what makes the whole feature a true no-op for
un-annotated nodes. A declared model's field names are a literal 1:1 subset of
``AgentState``'s keys (a *narrow projection*, not an exhaustive mirror): only
the keys the model declares are read; keys the mapping has that the model does
not declare are never inspected.

Security convention (mirrors ``NodeExecutionError``'s ``state_keys``-not-values
rule): error messages carry field *names* and pydantic's own type/constraint
description only — never the offending field *value* — to avoid leaking
sensitive state content into logs/metrics/audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

NodeIOMode = Literal["off", "warn", "enforce"]
"""Control mode for node I/O validation.

'off'     — feature disabled for this call; the helpers are not invoked at all
            by ``node_io_validation_middleware`` (equivalent to no model declared).
'warn'    — validation runs; a failure is logged + counted but the original
            state / state_update passes through unmodified.
'enforce' — validation runs; a failure raises ``NodeValidationError``.
"""

NodeIODirection = Literal["input", "output"]


@dataclass(frozen=True)
class NodeIOValidationResult:
    """Outcome of validating one side of a node's declared I/O contract.

    Attributes:
        ok: True if validation passed or no model was declared (trivial success).
        node_name: Name of the node being validated (for log/audit correlation).
        direction: Which side of the contract was checked.
        errors: Human-readable, field-level messages. Never contains field
            *values* — only field names and pydantic's own type/constraint
            description.
    """

    ok: bool
    node_name: str
    direction: NodeIODirection
    errors: list[str]


def _format_errors(exc: ValidationError) -> list[str]:
    """Render a pydantic ``ValidationError`` as field-name-only messages.

    Deliberately omits pydantic's ``input`` value (``err["input"]``) so no
    field value leaks into logs/metrics/audit.
    """
    messages: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
        msg = err.get("msg", "invalid")
        messages.append(f"{loc}: {msg}")
    return messages


def _validate(
    mapping: Mapping[str, Any],
    model: type[BaseModel] | None,
    *,
    node_name: str,
    direction: NodeIODirection,
) -> NodeIOValidationResult:
    """Shared core for input/output validation. Never raises."""
    if model is None:
        return NodeIOValidationResult(ok=True, node_name=node_name, direction=direction, errors=[])
    # Narrow projection: only the keys the model declares are read.
    declared = model.model_fields.keys()
    payload = {key: mapping[key] for key in declared if key in mapping}
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        return NodeIOValidationResult(
            ok=False,
            node_name=node_name,
            direction=direction,
            errors=_format_errors(exc),
        )
    return NodeIOValidationResult(ok=True, node_name=node_name, direction=direction, errors=[])


def validate_node_input(
    state: Mapping[str, Any],
    model: type[BaseModel] | None,
    *,
    node_name: str,
) -> NodeIOValidationResult:
    """Validate the subset of ``state`` a node's declared ``input_model`` describes.

    Never raises. If ``model`` is ``None``, returns a trivial ``ok=True`` result.
    Only the keys present in ``model``'s fields are read from ``state``; keys
    ``state`` has that the model does not declare are ignored.

    Args:
        state: The AgentState (a ``dict`` at runtime) the node is about to receive.
        model: The node's declared input model, or ``None``.
        node_name: Name of the node (for correlation and logs).

    Returns:
        A :class:`NodeIOValidationResult`; ``errors`` is empty iff ``ok`` is True.
    """
    return _validate(state, model, node_name=node_name, direction="input")


def validate_node_output(
    state_update: Mapping[str, Any],
    model: type[BaseModel] | None,
    *,
    node_name: str,
) -> NodeIOValidationResult:
    """Validate the subset of a node's ``state_update`` its ``output_model`` describes.

    Same no-raise, no-op-when-``None``, narrow-projection semantics as
    :func:`validate_node_input`, applied to the dict a node returns.

    Args:
        state_update: The dict returned by the node's user function.
        model: The node's declared output model, or ``None``.
        node_name: Name of the node.

    Returns:
        A :class:`NodeIOValidationResult`.
    """
    return _validate(state_update, model, node_name=node_name, direction="output")


__all__ = [
    "NodeIODirection",
    "NodeIOMode",
    "NodeIOValidationResult",
    "validate_node_input",
    "validate_node_output",
]
