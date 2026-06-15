"""Trajectory capture from the graph event stream (SPEC-EVL-TRJ-001).

``capture_trajectory`` drives ``graph.astream(..., stream_mode="updates")`` for a
single case and reconstructs the :class:`~prismal.eval.types.Trajectory` purely
from the public event stream — no agent instrumentation. Token counts reuse the
LangChain-convention reader in :mod:`prismal.budget.usage`; cost is priced
through the provider-agnostic :func:`prismal.providers.cost.compute_cost_usd`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage

from prismal.budget.usage import extract_token_usage
from prismal.eval.types import EvalCase, Trajectory, TrajectoryStep
from prismal.langgraph import AgentState, create_initial_state

if TYPE_CHECKING:
    from collections.abc import Callable


async def capture_trajectory(
    graph: Any,
    case: EvalCase,
    *,
    config: dict[str, Any] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> Trajectory:
    """Run one case through *graph* and capture its trajectory.

    Args:
        graph: A compiled graph exposing ``astream(input, config, stream_mode=)``.
        case: The case whose ``input`` seeds the run.
        config: Optional LangGraph config (e.g. ``{"configurable": {...}}``).
        clock: Monotonic clock for latency; injectable for deterministic tests.

    Returns:
        A populated :class:`Trajectory`. Never raises on stream content; a
        provider/runtime error surfaces as ``terminated=False``.
    """
    steps: list[TrajectoryStep] = []
    visited: list[str] = []
    tool_calls = 0
    tool_errors = 0
    tokens = 0
    cost_usd = 0.0
    final_answer = ""
    terminated = False

    input_state = _initial_state(case)
    start = clock()
    try:
        async for chunk in graph.astream(input_state, config, stream_mode="updates"):
            for node, update in _iter_node_updates(chunk):
                visited.append(node)
                for message in _messages_of(update):
                    new_steps, answer, calls, errors, toks, cost = _consume_message(node, message)
                    steps.extend(new_steps)
                    tool_calls += calls
                    tool_errors += errors
                    tokens += toks
                    cost_usd += cost
                    if answer is not None:
                        final_answer = answer
        terminated = True
    except Exception:  # capture must never raise; record non-termination instead
        terminated = False

    latency_ms = max(0.0, (clock() - start) * 1000.0)
    return Trajectory(
        case_id=case.id,
        final_answer=final_answer,
        steps=steps,
        visited_nodes=visited,
        tool_calls=tool_calls,
        tool_errors=tool_errors,
        cost_usd=cost_usd,
        tokens=tokens,
        latency_ms=latency_ms,
        terminated=terminated,
    )


def _initial_state(case: EvalCase) -> AgentState:
    """Build the graph input for *case* via the public state factory."""
    state = create_initial_state(f"eval-{case.id}")
    state["messages"] = [HumanMessage(content=case.input)]
    return state


def _iter_node_updates(chunk: Any) -> list[tuple[str, Any]]:
    """Yield ``(node_name, update)`` pairs from one ``updates`` stream chunk."""
    if not isinstance(chunk, dict):
        return []
    return [(node, update) for node, update in chunk.items() if isinstance(node, str)]


def _messages_of(update: Any) -> list[Any]:
    """Extract the message delta from a node update, tolerant of shapes."""
    if isinstance(update, dict):
        msgs = update.get("messages")
        if isinstance(msgs, list):
            return msgs
        if msgs is not None:
            return [msgs]
    return []


def _consume_message(
    node: str, message: Any
) -> tuple[list[TrajectoryStep], str | None, int, int, int, float]:
    """Classify one message into trajectory steps + counters.

    Returns ``(steps, final_answer_or_None, tool_calls, tool_errors, tokens, cost)``.
    """
    role = _role_of(message)
    content = _text(getattr(message, "content", ""))
    steps: list[TrajectoryStep] = []
    final_answer: str | None = None
    tool_calls = 0
    tool_errors = 0

    if role == "tool":
        ok = getattr(message, "status", "success") != "error"
        if not ok:
            tool_errors += 1
        steps.append(
            TrajectoryStep(
                node=node,
                role="tool",
                content=content,
                tool_name=getattr(message, "name", None),
                tool_ok=ok,
            )
        )
        return steps, None, tool_calls, tool_errors, 0, 0.0

    requested = getattr(message, "tool_calls", None) or []
    for call in requested:
        if not isinstance(call, dict):
            continue
        tool_calls += 1
        steps.append(
            TrajectoryStep(
                node=node,
                role="assistant",
                tool_name=call.get("name"),
                tool_args=call.get("args"),
            )
        )

    if content or not requested:
        steps.append(TrajectoryStep(node=node, role=role, content=content))
        if role == "assistant" and not requested:
            final_answer = content

    tokens, cost = _usage_of(message)
    return steps, final_answer, tool_calls, tool_errors, tokens, cost


def _role_of(message: Any) -> str:
    """Map a LangChain message ``type`` to a trajectory role."""
    mtype = getattr(message, "type", "")
    return {"ai": "assistant", "human": "user", "tool": "tool", "system": "system"}.get(
        mtype, mtype or "assistant"
    )


def _text(content: Any) -> str:
    """Coerce message content (str or content-blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        ]
        return "".join(parts)
    return str(content) if content is not None else ""


def _usage_of(message: Any) -> tuple[int, float]:
    """Return ``(tokens, cost_usd)`` for one message via budget/provider helpers."""
    counts = extract_token_usage(message)
    tokens = counts.prompt_tokens + counts.completion_tokens
    if tokens == 0:
        return 0, 0.0
    model = _model_of(message)
    if not model:
        return tokens, 0.0
    from prismal.providers.cost import compute_cost_usd

    estimate = compute_cost_usd(model, counts.prompt_tokens, counts.completion_tokens)
    return tokens, estimate.cost_usd


def _model_of(message: Any) -> str:
    """Best-effort model name from a message's response metadata."""
    meta = getattr(message, "response_metadata", None)
    if isinstance(meta, dict):
        model = meta.get("model_name") or meta.get("model")
        if isinstance(model, str):
            return model
    return ""


__all__ = ["capture_trajectory"]
