"""``@prismal_node`` decorator and its public metadata types (SPEC-EXT-002).

A node is an ``async (state) -> state_update`` callable. Decorating it with
:func:`prismal_node` wraps it in the prismal middleware chain (security,
tracing, logging, retry, timeout, audit, error mapping) and registers its
metadata so the supervisor and the builder can discover it.

Example::

    @prismal_node(name="my_classifier", capabilities=["general"])
    async def my_classifier(state):
        last = state["messages"][-1].content
        return {"metadata": {"my_classifier": {"label": classify(last)}}}
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from prismal.agents.extension import _registry
from prismal.agents.extension._middleware import build_pipeline

if TYPE_CHECKING:
    from pydantic import BaseModel

    from prismal.agents.state import AgentState

SecurityLevel = Literal["off", "standard", "strict"]
"""Security middleware level.

``'off'``      — no security middleware (max performance, max risk).
``'standard'`` — InputSanitizer on the last user message before the node runs.
``'strict'``   — standard + permission check on any tool_call in the output.
"""

NodeFn = Callable[["AgentState"], Awaitable[dict[str, Any]]]
"""Signature of a node: ``async (state) -> state_update``."""

_NodeFnT = TypeVar("_NodeFnT", bound=NodeFn)
"""Type-preserving var so a decorated node keeps its concrete callable type
(lets a ``@prismal_node``-wrapped node be added to a raw ``StateGraph`` exactly
like the undecorated function)."""


@dataclass(frozen=True)
class RetryPolicy:
    """Retry configuration for a decorated node."""

    max_attempts: int = 3
    backoff_s: tuple[float, ...] = (0.1, 0.5, 1.0)
    retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)


@dataclass(frozen=True)
class NodeMetadata:
    """Immutable metadata of a node registered via :func:`prismal_node`."""

    name: str
    capabilities: tuple[str, ...]
    security: SecurityLevel
    audit: bool
    retry: RetryPolicy | None
    timeout_s: float | None
    raise_on_error: bool
    registered_at: str
    source_module: str
    extra: dict[str, Any] = field(default_factory=dict)

    # [Phase NTS — SPEC-NTS-TYP-002] Optional per-node I/O contracts. Both
    # default to None, so every pre-existing construction site is unaffected.
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None


def prismal_node(
    *,
    name: str | None = None,
    capabilities: list[str] | None = None,
    security: SecurityLevel = "standard",
    audit: bool = True,
    retry: RetryPolicy | None = None,
    timeout_s: float | None = None,
    raise_on_error: bool = False,
    input_model: type[BaseModel] | None = None,
    output_model: type[BaseModel] | None = None,
) -> Callable[[_NodeFnT], _NodeFnT]:
    """Wrap a LangGraph node with prismal cross-cutting middleware.

    The middleware chain, from outermost to innermost, is:

        error_mapping → otel span → logger bind → security → audit
        → retry → timeout → user function

    (Order chosen so retries happen *before* the error is mapped, and the
    audit duration covers all retry attempts. This intentionally clarifies
    the contradictory ordering in the original SPEC, where ``audit`` and
    ``error_mapping`` were listed after the user function while every other
    layer wrapped it.)

    Args:
        name: Node name (default: the function's ``__name__``).
        capabilities: MCP capabilities the node requires. Registered in
            ``tool_registry.DEFAULT_CAPABILITY_MAP`` as a side effect.
        security: Security middleware level.
        audit: If True, ``AuditLogger.log_node`` runs per invocation.
        retry: Retry policy. ``None`` disables retries.
        timeout_s: Per-invocation timeout in seconds. ``None`` disables it.
        raise_on_error: If True, propagate a ``NodeExecutionError`` on failure.
            If False (default), return ``{"metadata": {"error": {...}}}``.
        input_model: Optional Pydantic model describing the subset of
            ``AgentState`` this node reads (Phase NTS). ``None`` (default)
            disables input validation for this node. Only consulted when
            ``settings.node_typesafety_enabled`` is True.
        output_model: Optional Pydantic model describing the subset of the
            returned ``state_update`` this node produces. ``None`` (default)
            disables output validation.

    Returns:
        A decorator that returns the wrapped ``NodeFn`` with two attributes:
        ``__prismal_node__`` (the :class:`NodeMetadata`) and ``__wrapped__``
        (the original function).
    """

    def decorator(fn: _NodeFnT) -> _NodeFnT:
        # Idempotent: decorating an already-decorated node returns it unchanged.
        if getattr(fn, "__prismal_node__", None) is not None:
            return fn

        node_name = name or fn.__name__
        metadata = NodeMetadata(
            name=node_name,
            capabilities=tuple(capabilities or []),
            security=security,
            audit=audit,
            retry=retry,
            timeout_s=timeout_s,
            raise_on_error=raise_on_error,
            registered_at=datetime.now(UTC).isoformat(),
            source_module=fn.__module__,
            input_model=input_model,
            output_model=output_model,
        )
        pipeline = build_pipeline(fn, metadata)

        @functools.wraps(fn)
        async def wrapper(state: AgentState) -> dict[str, Any]:
            return await pipeline(state)

        wrapper.__prismal_node__ = metadata  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

        _registry.register_node(metadata)
        if metadata.capabilities:
            # Imported lazily to avoid a heavy import at decoration time.
            from prismal.agents.tool_registry import DEFAULT_CAPABILITY_MAP

            DEFAULT_CAPABILITY_MAP[node_name] = list(metadata.capabilities)

        # ``wrapper`` has the ``NodeFn`` shape; cast back to the caller's concrete
        # type so the decorated node keeps the original's static signature.
        return cast("_NodeFnT", wrapper)

    return decorator


def list_registered_nodes() -> list[NodeMetadata]:
    """Return metadata for every node registered via :func:`prismal_node`."""
    return _registry.all_nodes()


def get_node_metadata(name: str) -> NodeMetadata | None:
    """Return metadata for a registered node, or ``None`` if unknown."""
    return _registry.get_node(name)


__all__ = [
    "NodeFn",
    "NodeMetadata",
    "RetryPolicy",
    "SecurityLevel",
    "get_node_metadata",
    "list_registered_nodes",
    "prismal_node",
]
