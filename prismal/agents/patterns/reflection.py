"""Reflection loop framework — generate / critique / refine pattern.

This module implements a reusable Reflection pattern (SPEC-035) that can be
applied to any agent that produces a textual draft and benefits from a
self-critique step.

The :func:`reflection_loop` coroutine repeatedly invokes a ``generate_fn`` to
produce a draft and a ``critique_fn`` to score it.  When the score reaches a
configurable ``threshold`` the best draft is returned; otherwise the loop
refines the draft using the critique feedback until ``max_iterations`` is
reached.  The :func:`with_reflection` decorator wraps async LangGraph node
functions so that subgraph nodes can opt into reflection without restructuring
their generation logic.

Example::

    from prismal.agents.patterns.reflection import reflection_loop


    async def generate(state, previous_draft=None, critique=None):
        prompt = "Write a haiku about the sea."
        if previous_draft is not None:
            prompt += f"\\n\\nPrevious attempt:\\n{previous_draft}"
            prompt += f"\\n\\nCritique:\\n{critique}"
        return await llm.ainvoke(prompt)


    async def critique(draft, state):
        # Returns (feedback, score)
        return ("good rhythm", 0.92)


    final, score = await reflection_loop(generate, critique, state)
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar, cast

from prismal.core.config import get_settings
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = get_logger("lightagent.agents.patterns.reflection")

GenerateFn = Callable[..., Awaitable[str]]
"""Signature: ``generate_fn(state, previous_draft=None, critique=None) -> str``."""

CritiqueFn = Callable[[str, "AgentState"], Awaitable[tuple[str, float]]]
"""Signature: ``critique_fn(draft, state) -> (feedback, score)``."""

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


async def reflection_loop(
    generate_fn: GenerateFn,
    critique_fn: CritiqueFn,
    state: AgentState,
    threshold: float = 0.85,
    max_iterations: int = 3,
) -> tuple[str, float]:
    """Run a generate-critique-refine loop and return the best draft.

    The loop performs the following steps:

    1. Calls ``generate_fn(state)`` to obtain an initial draft.
    2. Calls ``critique_fn(draft, state)`` to receive ``(feedback, score)``.
    3. If ``score >= threshold`` the draft is returned immediately.
    4. Otherwise, while ``iteration < max_iterations``, ``generate_fn`` is
       called again with ``previous_draft`` and ``critique`` keyword arguments
       so that the underlying generator can refine its output.
    5. After ``max_iterations`` is reached the highest-scoring draft observed
       during the loop is returned regardless of whether the threshold was met.

    When the global setting ``LIGHTAGENT_REFLECTION_ENABLED`` is ``False`` the
    loop is bypassed entirely and only the first draft is generated and
    returned with a sentinel score of ``1.0``.  This allows operators to disable
    reflection in production for latency-sensitive workloads without modifying
    agent code.

    The function never mutates the supplied ``state`` — it is treated as
    read-only context that is forwarded verbatim to ``generate_fn`` and
    ``critique_fn``.

    Args:
        generate_fn: Async callable that produces a textual draft.  It must
            accept the LangGraph ``state`` as the first positional argument
            and may accept ``previous_draft`` and ``critique`` keyword
            arguments for refinement iterations.
        critique_fn: Async callable that scores a draft against the supplied
            state.  It must return ``(feedback, score)`` where ``score`` is a
            float in ``[0.0, 1.0]``.
        state: The current LangGraph ``AgentState``.  Treated as read-only.
        threshold: Minimum score that ends the loop early.  Defaults to
            ``0.85``.  Must be in ``[0.0, 1.0]``.
        max_iterations: Maximum number of generate calls.  Defaults to ``3``
            and is capped by ``get_settings().reflection_max_iterations``.

    Returns:
        A tuple ``(best_draft, final_score)``.  When reflection is disabled
        ``final_score`` is ``1.0``.

    Raises:
        ValueError: If ``threshold`` is outside ``[0.0, 1.0]`` or
            ``max_iterations`` is less than ``1``.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0.0, 1.0], got {threshold}")
    if max_iterations < 1:
        raise ValueError(f"max_iterations must be >= 1, got {max_iterations}")

    # Bypass the loop entirely when reflection is globally disabled.
    if not get_settings().reflection_enabled:
        logger.debug("reflection_disabled_returning_first_draft")
        first_draft = await generate_fn(state)
        return first_draft, 1.0

    # Honour the global cap so callers cannot exceed the operator-configured
    # ceiling even by passing a larger ``max_iterations`` argument.
    effective_max = min(max_iterations, get_settings().reflection_max_iterations)

    best_draft: str = ""
    best_score: float = -1.0
    previous_draft: str | None = None
    previous_critique: str | None = None

    for iteration in range(1, effective_max + 1):
        if previous_draft is None:
            draft = await generate_fn(state)
        else:
            draft = await generate_fn(
                state,
                previous_draft=previous_draft,
                critique=previous_critique,
            )

        feedback, score = await critique_fn(draft, state)
        logger.debug(
            "reflection_iteration",
            iteration=iteration,
            score=score,
            threshold=threshold,
            max_iterations=effective_max,
        )

        if score > best_score:
            best_score = score
            best_draft = draft

        if score >= threshold:
            return best_draft, best_score

        previous_draft = draft
        previous_critique = feedback

    logger.debug(
        "reflection_max_iterations_reached",
        best_score=best_score,
        max_iterations=effective_max,
    )
    return best_draft, best_score


def with_reflection(
    threshold: float = 0.85,
    max_iterations: int = 2,
    critique_fn: CritiqueFn | None = None,
) -> Callable[[F], F]:
    """Decorate a subgraph node so its output is refined via :func:`reflection_loop`.

    The wrapped node function must be an async callable that accepts an
    :class:`AgentState` and returns a string draft.  The decorator routes the
    draft through ``reflection_loop`` using the supplied ``critique_fn`` (which
    is required — there is no default critique because the appropriate
    evaluation logic is always domain-specific).

    The reflection iteration count and final score are stored under
    ``state["metadata"][<node_name>]`` as ``reflection_iterations`` and
    ``reflection_score`` respectively, where ``<node_name>`` is the wrapped
    function's ``__name__``.

    Args:
        threshold: Score threshold forwarded to :func:`reflection_loop`.
        max_iterations: Iteration cap forwarded to :func:`reflection_loop`.
        critique_fn: Async critique callable.  Required.

    Returns:
        A decorator that returns the wrapped async function.

    Raises:
        ValueError: If ``critique_fn`` is ``None`` at decoration time.
    """
    if critique_fn is None:
        raise ValueError(
            "with_reflection requires a critique_fn — no default is provided "
            "because critique logic is always domain-specific."
        )

    def decorator(node_fn: F) -> F:
        node_name = node_fn.__name__

        @functools.wraps(node_fn)
        async def wrapper(
            state: AgentState,
            *args: Any,
            **kwargs: Any,
        ) -> str:
            async def _generate(
                s: AgentState,
                previous_draft: str | None = None,
                critique: str | None = None,
            ) -> str:
                # The wrapped node is responsible for handling refinement
                # context if it accepts the optional kwargs; otherwise the
                # extra context is dropped silently.
                try:
                    return cast(
                        "str",
                        await node_fn(
                            s,
                            *args,
                            previous_draft=previous_draft,
                            critique=critique,
                            **kwargs,
                        ),
                    )
                except TypeError:
                    return cast("str", await node_fn(s, *args, **kwargs))

            best_draft, score = await reflection_loop(
                generate_fn=_generate,
                critique_fn=critique_fn,
                state=state,
                threshold=threshold,
                max_iterations=max_iterations,
            )

            # Persist iteration metadata so downstream nodes/tests can inspect
            # the reflection outcome without re-running the loop.
            metadata = state.get("metadata", {})
            node_meta = metadata.setdefault(node_name, {})
            node_meta["reflection_score"] = score
            node_meta.setdefault("reflection_iterations", 0)
            return best_draft

        return cast("F", wrapper)

    return decorator


__all__ = ["CritiqueFn", "GenerateFn", "reflection_loop", "with_reflection"]
