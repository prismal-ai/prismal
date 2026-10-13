"""Trajectory capture from the graph event stream (SPEC-EVL-TRJ-001).

``capture_trajectory`` drives ``graph.astream(..., stream_mode="updates")`` for a
single case and reconstructs the :class:`~prismal.eval.types.Trajectory` from
the public event stream — no agent instrumentation. Token and cost figures come
from a LangChain callback that observes every LLM call of the run (internal
routing calls and rebuilt answers carry no usage in the stream); when no call is
observed, the usage on streamed messages is used instead. Token counts reuse the
LangChain-convention reader in :mod:`prismal.budget.usage`; cost is priced
through the provider-agnostic :func:`prismal.providers.cost.compute_cost_usd`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage

from prismal.budget.usage import extract_token_usage
from prismal.eval.types import EvalCase, Trajectory, TrajectoryStep
from prismal.langgraph import AgentState, create_initial_state

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.outputs import LLMResult


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
    security_signals: list[str] = []
    tool_calls = 0
    tool_errors = 0
    tokens = 0
    cost_usd = 0.0
    input_usage = _InputUsage()
    final_answer = ""
    terminated = False

    llm_usage = _LlmUsageCallback()
    thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
    input_state = _initial_state(case, session_id=thread_id)
    start = clock()
    try:
        stream = graph.astream(
            input_state, _with_callback(config, llm_usage), stream_mode="updates"
        )
        async for chunk in stream:
            for node, update in _iter_node_updates(chunk):
                visited.append(node)
                security_signals.extend(_metadata_signals(update))
                for message in _messages_of(update):
                    new_steps, answer, calls, errors, toks, cost = _consume_message(node, message)
                    steps.extend(new_steps)
                    tool_calls += calls
                    tool_errors += errors
                    tokens += toks
                    cost_usd += cost
                    input_usage.add(message)
                    if answer is not None:
                        final_answer = answer
                    security_signals.extend(_message_signals(message))
        terminated = True
    except Exception:  # capture must never raise; record non-termination instead
        terminated = False

    latency_ms = max(0.0, (clock() - start) * 1000.0)
    usage_source = "messages"
    if llm_usage.input.llm_calls:
        usage_source = "callbacks"
        tokens, cost_usd, input_usage = llm_usage.tokens, llm_usage.cost_usd, llm_usage.input
    # The callback also sees tool calls run inside a node; never double-count.
    tool_calls = max(tool_calls, len(llm_usage.tool_names))
    tool_errors = max(tool_errors, llm_usage.tool_errors)
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
        security_signals=security_signals,
        prompt_tokens=input_usage.prompt_tokens,
        cache_read_tokens=input_usage.cache_read_tokens,
        llm_calls=input_usage.llm_calls,
        input_cost_usd=input_usage.input_cost_usd,
        usage_source=usage_source,
        observed_tool_names=list(llm_usage.tool_names),
    )


def _with_callback(config: dict[str, Any] | None, handler: BaseCallbackHandler) -> dict[str, Any]:
    """A copy of *config* with *handler* added to its callbacks (caller's config untouched)."""
    merged: dict[str, Any] = dict(config or {})
    callbacks = merged.get("callbacks")
    if callbacks is None:
        merged["callbacks"] = [handler]
    elif isinstance(callbacks, list):
        merged["callbacks"] = [*callbacks, handler]
    else:  # a callback manager
        manager = callbacks.copy()
        manager.add_handler(handler, inherit=True)
        merged["callbacks"] = manager
    return merged


class _LlmUsageCallback(BaseCallbackHandler):
    """Accumulates usage of every LLM call made during the run."""

    run_inline = True  # mutate the counters on the event loop, never from a worker thread

    def __init__(self) -> None:
        self.tokens = 0
        self.cost_usd = 0.0
        self.input = _InputUsage()
        self.tool_names: list[str] = []
        self.tool_errors = 0

    def on_llm_end(self, response: LLMResult, **_: Any) -> None:
        """Count the generation messages of one finished LLM call."""
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                if message is None:
                    continue
                tokens, cost = _usage_of(message)
                self.tokens += tokens
                self.cost_usd += cost
                self.input.add(message)

    def on_tool_start(
        self, serialized: dict[str, Any] | None, input_str: str, **kwargs: Any
    ) -> None:
        """Record one tool call (including calls made inside a ReAct loop)."""
        del input_str
        name = (serialized or {}).get("name") or kwargs.get("name") or "unknown"
        self.tool_names.append(str(name))

    def on_tool_error(self, error: BaseException, **_: Any) -> None:
        """Count one failed tool call."""
        del error
        self.tool_errors += 1


def _initial_state(case: EvalCase, *, session_id: str | None = None) -> AgentState:
    """Build the graph input for *case*; the session id follows the run's thread."""
    state = create_initial_state(session_id or f"eval-{case.id}")
    state["messages"] = [HumanMessage(content=case.input)]
    return state


def _iter_node_updates(chunk: Any) -> list[tuple[str, Any]]:
    """Yield ``(node_name, update)`` pairs from one ``updates`` stream chunk."""
    if not isinstance(chunk, dict):
        return []
    return [(node, update) for node, update in chunk.items() if isinstance(node, str)]


# Security-signal markers (keys in state metadata, substrings in tool results).
_METADATA_SECURITY_KEYS = ("hardening", "security", "guardrail", "injection", "blocked")
_BLOCK_CONTENT_MARKERS = (
    "[blocked",
    "blocked by",
    "injection detected",
    "policy denied",
    "neutralised",
    "neutralized",
    "request refused",
)


def _metadata_signals(update: Any) -> list[str]:
    """Security markers present in a node update's ``metadata`` block."""
    if not isinstance(update, dict):
        return []
    meta = update.get("metadata")
    if not isinstance(meta, dict):
        return []
    return [f"metadata.{key}" for key in _METADATA_SECURITY_KEYS if key in meta]


def _message_signals(message: Any) -> list[str]:
    """Security markers in a (blocked/neutralised) tool or assistant message."""
    content = _text(getattr(message, "content", "")).lower()
    if any(marker in content for marker in _BLOCK_CONTENT_MARKERS):
        name = getattr(message, "name", None) or _role_of(message)
        return [f"blocked:{name}"]
    return []


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


class _InputUsage:
    """Input-side accumulator for the TKE token report (SPEC-TKE-ACC-005)."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.cache_read_tokens = 0
        self.llm_calls = 0
        self.input_cost_usd = 0.0

    def add(self, message: Any) -> None:
        counts = extract_token_usage(message)
        if counts.total == 0:
            return
        self.llm_calls += 1
        self.prompt_tokens += counts.prompt_tokens
        self.cache_read_tokens += counts.cache_read_tokens
        model = _model_of(message)
        if model:
            from prismal.providers.cost import compute_cost_usd

            self.input_cost_usd += compute_cost_usd(
                model,
                counts.prompt_tokens,
                0,
                cache_read_tokens=counts.cache_read_tokens,
                cache_write_tokens=counts.cache_write_tokens,
            ).cost_usd


def _model_of(message: Any) -> str:
    """Best-effort model name from a message's response metadata."""
    meta = getattr(message, "response_metadata", None)
    if isinstance(meta, dict):
        model = meta.get("model_name") or meta.get("model")
        if isinstance(model, str):
            return model
    return ""


__all__ = ["capture_trajectory"]
