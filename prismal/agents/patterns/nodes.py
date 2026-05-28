"""LangGraph node factories for the advanced reasoning patterns (Phase D / D1-01).

Each ``make_*_node`` turns one pattern from :mod:`prismal.agents.patterns`
into a supervisor-compatible LangGraph node -- an ``async (state) -> state_update``
coroutine that:

1. Reads the user query from ``state["messages"][-1]`` (no-op on empty history).
2. Runs the pattern, supplying LLM-backed default callables when the caller
   does not inject its own (the same factory-injection contract the patterns
   themselves follow, so tests run without an LLM backend).
3. Returns ``{"messages": [AIMessage(...)], "metadata": {<pattern>: {...}}}``.

The LLM-backed defaults resolve the provider lazily *at call time* via
:class:`~prismal.providers.registry.ProviderRegistry`, so building the graph
never requires provider credentials. User input is always isolated through
:class:`~prismal.security.prompt_builder.SecurePromptBuilder` -- never
f-stringed into a prompt template.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.agents.patterns.constitutional import ConstitutionalFilter
from prismal.agents.patterns.debate import debate_round
from prismal.agents.patterns.lats import LATSAgent
from prismal.agents.patterns.llm_compiler import LLMCompiler, TaskNode
from prismal.agents.patterns.mixture_of_agents import MixtureOfAgents
from prismal.agents.patterns.tree_of_thoughts import make_tot_node
from prismal.core.config import get_settings
from prismal.core.exceptions import LATSError
from prismal.core.logging import get_logger
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

logger = get_logger("prismal.agents.patterns.nodes")

__all__ = [
    "make_constitutional_node",
    "make_debate_node",
    "make_lats_node",
    "make_llm_compiler_node",
    "make_mixture_node",
    "make_tot_agent_node",
]


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #


def _resolve_llm(llm: Any) -> Any:
    """Return *llm* if given, else a lazily-built default provider LLM."""
    if llm is not None:
        return llm
    from prismal.providers.registry import ProviderRegistry

    return ProviderRegistry().get_llm()


async def _complete(llm: Any, system: str, user: str) -> str:
    """Run one LLM completion with user input isolated by SecurePromptBuilder."""
    builder = SecurePromptBuilder()
    messages = builder.build(system=system, user=user)
    response = await llm.ainvoke(
        [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
    )
    return str(response.content).strip()


def _split_lines(text: str) -> list[str]:
    """Split LLM output into clean lines, stripping bullets and numbering."""
    out: list[str] = []
    for raw in str(text).splitlines():
        line = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", raw.strip())
        if line:
            out.append(line)
    return out


def _parse_score(text: str) -> float:
    """Extract the first number from *text* and clamp it to ``[0, 1]``.

    Non-numeric output is pessimistic (``0.0``) rather than raising, mirroring
    the safe-default convention used elsewhere in the patterns layer.
    """
    match = re.search(r"[-+]?\d*\.?\d+", str(text))
    if match is None:
        return 0.0
    try:
        value = float(match.group())
    except ValueError:  # pragma: no cover -- regex guarantees a parseable token
        return 0.0
    return max(0.0, min(1.0, value))


def _last_query(state: dict[str, Any]) -> str | None:
    """Return the last message content as a query, or ``None`` for empty history."""
    messages = state.get("messages") or []
    if not messages:
        return None
    return str(messages[-1].content)


def _merged_metadata(state: dict[str, Any], key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``state['metadata']`` with ``payload`` stored under *key*."""
    metadata = dict(state.get("metadata") or {})
    metadata[key] = payload
    return metadata


def _default_proposer_models() -> list[str]:
    """Default Mixture-of-Agents proposers: configured default + fallback model."""
    settings = get_settings()
    models = [settings.default_model]
    if settings.fallback_model and settings.fallback_model != settings.default_model:
        models.append(settings.fallback_model)
    return models


# --------------------------------------------------------------------------- #
# LLM-backed default callables (Tree of Thoughts)                              #
# --------------------------------------------------------------------------- #


def _default_tot_generate(
    llm: Any, breadth: int = 3
) -> Callable[[str, Any, list[Any]], Awaitable[list[str]]]:
    """Build a thought generator that asks the LLM for candidate next steps."""

    async def _gen(problem: str, _state: Any, path: list[Any]) -> list[str]:
        system = (
            f"You are exploring solutions step by step. Propose up to {breadth} "
            "distinct, concise candidate next thoughts. One per line, no numbering."
        )
        prior = "\n".join(str(getattr(node, "content", node)) for node in path)
        user = problem if not prior else f"{problem}\n\nReasoning so far:\n{prior}"
        text = await _complete(_resolve_llm(llm), system, user)
        return _split_lines(text)[:breadth]

    return _gen


def _default_tot_evaluate(llm: Any) -> Callable[[str, Any], Awaitable[float]]:
    """Build a thought scorer that asks the LLM for a ``[0, 1]`` quality score."""

    async def _score(content: str, _state: Any) -> float:
        system = (
            "Rate how promising the following reasoning step is for solving the "
            "task, as a single number between 0 and 1. Reply with only the number."
        )
        text = await _complete(_resolve_llm(llm), system, content)
        return _parse_score(text)

    return _score


# --------------------------------------------------------------------------- #
# LLM-backed default callables (LATS)                                          #
# --------------------------------------------------------------------------- #


def _default_lats_actions(
    llm: Any, goal: str, n_actions: int
) -> Callable[[Any, Sequence[Any]], Awaitable[list[str]]]:
    """Build an action generator proposing candidate next steps toward *goal*."""

    async def _actions(state: Any, _tools: Sequence[Any]) -> list[str]:
        system = (
            f"Given the goal and the progress so far, propose up to {n_actions} "
            "distinct next actions. One per line, no numbering."
        )
        progress = str(state).strip() or "(no progress yet)"
        user = f"Goal: {goal}\n\nProgress so far:\n{progress}"
        text = await _complete(_resolve_llm(llm), system, user)
        return _split_lines(text)[:n_actions]

    return _actions


def _default_lats_transition() -> Callable[[Any, Any], Awaitable[str]]:
    """Build a pure transition that appends the action to the reasoning state."""

    async def _transition(state: Any, action: Any) -> str:
        return f"{state}\n{action}".strip()

    return _transition


def _default_lats_reward(llm: Any, goal: str) -> Callable[[Any], Awaitable[float]]:
    """Build a reward fn scoring how completely a state achieves *goal*."""

    async def _reward(state: Any) -> float:
        system = (
            "Rate from 0 to 1 how completely the following progress achieves the "
            "stated goal. Reply with only the number."
        )
        user = f"Goal: {goal}\n\nProgress:\n{state}"
        text = await _complete(_resolve_llm(llm), system, user)
        return _parse_score(text)

    return _reward


# --------------------------------------------------------------------------- #
# LLM-backed default callables (LLM-Compiler)                                  #
# --------------------------------------------------------------------------- #


def _default_compiler_plan(
    llm: Any, max_subtasks: int = 3
) -> Callable[[str, Any, dict[str, Any] | None], Awaitable[list[TaskNode]]]:
    """Build a planner that decomposes the goal into independent subtasks.

    The default plan is intentionally dependency-free (all tasks run in one
    parallel wave) so it is robust without fragile dependency parsing; inject a
    custom ``plan_fn`` to build a richer DAG.
    """

    async def _plan(goal: str, _state: Any, _prev: dict[str, Any] | None) -> list[TaskNode]:
        system = (
            f"Break the task into up to {max_subtasks} independent subtasks that "
            "can be solved separately. One subtask per line, no numbering."
        )
        text = await _complete(_resolve_llm(llm), system, goal)
        lines = _split_lines(text)[:max_subtasks] or [goal]
        return [
            TaskNode(
                id=f"T{index + 1}",
                description=line,
                tool="answer",
                args={"question": line},
                depends_on=[],
            )
            for index, line in enumerate(lines)
        ]

    return _plan


def _default_compiler_executor(
    llm: Any,
) -> Callable[[TaskNode, dict[str, Any]], Awaitable[str]]:
    """Build a tool executor that answers each subtask via the LLM."""

    async def _execute(task: TaskNode, _prior: dict[str, Any]) -> str:
        question = str(task.args.get("question") or task.description)
        return await _complete(
            _resolve_llm(llm),
            "Answer the following subtask concisely and accurately.",
            question,
        )

    return _execute


def _default_compiler_joiner(llm: Any) -> Callable[[str, list[TaskNode]], Awaitable[str]]:
    """Build a joiner that synthesises subtask outputs into a final answer."""

    async def _join(goal: str, tasks: list[TaskNode]) -> str:
        parts = "\n\n".join(
            f"[{task.description}]\n{task.output}" for task in tasks if task.output is not None
        )
        return await _complete(
            _resolve_llm(llm),
            "Synthesise the subtask results into one coherent final answer.",
            f"Goal: {goal}\n\nSubtask results:\n{parts}",
        )

    return _join


# --------------------------------------------------------------------------- #
# Node factories                                                               #
# --------------------------------------------------------------------------- #


def make_debate_node(
    *,
    debate_fn: Callable[..., Awaitable[Any]] = debate_round,
    n_agents: int = 3,
    n_rounds: int = 2,
    synthesis_strategy: str = "moderator",
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build a node that runs a multi-agent debate over the latest query."""

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        query = _last_query(state)
        if query is None:
            return {}
        result = await debate_fn(
            query=query,
            state=state,
            n_agents=n_agents,
            n_rounds=n_rounds,
            synthesis_strategy=synthesis_strategy,
        )
        metadata = _merged_metadata(
            state,
            "debate",
            {
                "agreement_score": result.agreement_score,
                "rounds_completed": result.rounds_completed,
            },
        )
        return {"messages": [AIMessage(content=result.consensus)], "metadata": metadata}

    return _node


def make_constitutional_node(
    *,
    filter_factory: Callable[[], Any] = ConstitutionalFilter,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build a node that revises the latest message against constitutional principles."""

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        output = _last_query(state)
        if output is None:
            return {}
        result = await filter_factory().apply(output=output)
        metadata = _merged_metadata(
            state,
            "constitutional",
            {
                "revisions": len(result.revisions),
                "all_principles_satisfied": result.all_principles_satisfied,
                "max_revisions_reached": result.max_revisions_reached,
            },
        )
        return {"messages": [AIMessage(content=result.final_output)], "metadata": metadata}

    return _node


def make_mixture_node(
    *,
    moa: Any = None,
    moa_factory: Callable[..., Any] = MixtureOfAgents,
    proposer_models: list[str] | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build a node that runs Mixture-of-Agents synthesis over the latest query."""

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        query = _last_query(state)
        if query is None:
            return {}
        agent = moa
        if agent is None:
            models = proposer_models or _default_proposer_models()
            agent = moa_factory(proposer_models=models)
        result = await agent.generate(query, state)
        metadata = _merged_metadata(state, "mixture", {"providers_used": result.providers_used})
        return {"messages": [AIMessage(content=result.final_answer)], "metadata": metadata}

    return _node


def make_tot_agent_node(
    *,
    generate_fn: Callable[[str, Any, list[Any]], Awaitable[list[str]]] | None = None,
    score_fn: Callable[[str, Any], Awaitable[float]] | None = None,
    llm: Any = None,
    breadth: int = 3,
    depth: int = 2,
    beam_size: int = 2,
    threshold: float = 0.9,
    search_strategy: str = "beam",
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build a Tree-of-Thoughts node, defaulting to LLM-backed generate/score."""
    gen = generate_fn or _default_tot_generate(llm, breadth)
    scorer = score_fn or _default_tot_evaluate(llm)
    return make_tot_node(
        gen,
        scorer,
        breadth=breadth,
        depth=depth,
        beam_size=beam_size,
        threshold=threshold,
        search_strategy=search_strategy,  # type: ignore[arg-type]
    )


def make_lats_node(
    *,
    reward_fn: Callable[[Any], Awaitable[float]] | None = None,
    action_generator: Callable[[Any, Sequence[Any]], Awaitable[Sequence[Any]]] | None = None,
    transition_fn: Callable[[Any, Any], Awaitable[Any]] | None = None,
    llm: Any = None,
    max_simulations: int = 16,
    max_depth: int = 3,
    n_actions: int = 3,
    exploration_constant: float = 1.41,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build a LATS (MCTS) node, defaulting to LLM-backed action/reward callables."""

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        query = _last_query(state)
        if query is None:
            return {}
        rf = reward_fn or _default_lats_reward(llm, query)
        ag = action_generator or _default_lats_actions(llm, query, n_actions)
        tf = transition_fn or _default_lats_transition()
        agent = LATSAgent(
            tools=[],
            reward_fn=rf,
            action_generator=ag,
            transition_fn=tf,
            max_simulations=max_simulations,
            max_depth=max_depth,
            exploration_constant=exploration_constant,
        )
        try:
            result = await agent.search(initial_state="", goal=query)
        except LATSError:
            logger.warning("lats_node_vacuous_search", query_len=len(query))
            answer = await _complete(_resolve_llm(llm), "Answer the user query concisely.", query)
            return {
                "messages": [AIMessage(content=answer)],
                "metadata": dict(state.get("metadata") or {}),
            }

        answer = str(result.final_state).strip() or "\n".join(
            str(action) for action in result.best_action_sequence
        )
        metadata = _merged_metadata(
            state,
            "lats",
            {
                "best_reward": result.best_reward,
                "total_simulations": result.total_simulations,
                "search_tree_depth": result.search_tree_depth,
            },
        )
        return {"messages": [AIMessage(content=answer)], "metadata": metadata}

    return _node


def make_llm_compiler_node(
    *,
    plan_fn: Callable[[str, Any, dict[str, Any] | None], Awaitable[Any]] | None = None,
    tool_executor: Callable[[TaskNode, dict[str, Any]], Awaitable[Any]] | None = None,
    joiner: Callable[[str, list[TaskNode]], Awaitable[str]] | None = None,
    llm: Any = None,
    max_subtasks: int = 3,
    max_replanning: int = 0,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build an LLM-Compiler node that plans and executes a task DAG."""

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        query = _last_query(state)
        if query is None:
            return {}
        pf = plan_fn or _default_compiler_plan(llm, max_subtasks)
        te = tool_executor or _default_compiler_executor(llm)
        jn = joiner or _default_compiler_joiner(llm)
        compiler = LLMCompiler(
            tools=[],
            plan_fn=pf,
            tool_executor=te,
            joiner=jn,
            max_replanning=max_replanning,
        )
        result = await compiler.compile_and_run(goal=query, state=state)
        metadata = _merged_metadata(
            state,
            "llm_compiler",
            {
                "tasks_succeeded": result.tasks_succeeded,
                "tasks_failed": result.tasks_failed,
                "replanning_count": result.replanning_count,
            },
        )
        return {"messages": [AIMessage(content=result.final_answer)], "metadata": metadata}

    return _node
