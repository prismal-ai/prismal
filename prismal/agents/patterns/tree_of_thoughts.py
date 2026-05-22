"""Tree of Thoughts reasoning pattern.

Implements SPEC-PAT-001. Explores a tree of reasoning steps (``Thought``
nodes). At each node, a user-provided ``generate_fn`` produces N candidate
next thoughts; ``evaluate_fn`` scores each. The search strategy decides
which branches to keep:

- **``beam``** (default): at every depth keep the top ``beam_size`` thoughts;
  bounded by ``beam_size * depth`` generate calls in the worst case.
- **``bfs``**: expand every node at every depth (no pruning). Exponential in
  breadth — use only for small search spaces.
- **``dfs``**: descend the highest-scoring branch first; backtrack only if no
  terminal is found. Pairs well with an early-termination ``threshold``.

Search always stops early when any thought scores at or above ``threshold``.

Example::

    async def generate(problem, state, path):
        return await llm_generate_N_candidates(problem, n=3)


    async def evaluate(thought_text, state):
        return await llm_score(thought_text)


    result = await tree_of_thoughts(
        problem="Plan a refactor of the supervisor node",
        generate_fn=generate,
        evaluate_fn=evaluate,
        state=current_state,
    )
    print(result.best_thought.content)
    for t in result.best_path:
        print(t.depth, t.score, t.content[:80])
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from prismal.core.exceptions import ToTError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

logger = get_logger("lightagent.agents.patterns.tree_of_thoughts")


@dataclass
class Thought:
    """One node in the reasoning tree.

    Attributes:
        content: Text of the thought.
        score: Evaluation score in ``[0, 1]`` (1 = solves the problem).
        depth: Distance from the root (root is depth 0).
        parent_id: ``id`` of the parent node; ``None`` for the root.
        children: Child thoughts produced by expanding this node.
        is_terminal: ``True`` when this node ended the search (threshold hit
            or best-of-run).
        id: Stable UUID for this thought (auto-generated).
    """

    content: str
    score: float
    depth: int
    parent_id: str | None
    children: list[Thought]
    is_terminal: bool = False
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass
class ToTResult:
    """Outcome of a :func:`tree_of_thoughts` run.

    Attributes:
        best_thought: Highest-scoring thought encountered.
        best_path: Path from the root to ``best_thought`` (inclusive).
        all_thoughts: Every :class:`Thought` created during the search.
        total_thoughts_generated: Thoughts created beyond the root.
    """

    best_thought: Thought
    best_path: list[Thought]
    all_thoughts: list[Thought]
    total_thoughts_generated: int


GenerateThoughtsFn = Callable[[str, Any, list[Thought]], Awaitable[list[str]]]
"""``(problem, state, path_so_far) -> list[thought_texts]``."""

EvaluateThoughtFn = Callable[[str, Any], Awaitable[float]]
"""``(content, state) -> score in [0, 1]``."""


async def tree_of_thoughts(
    problem: str,
    generate_fn: GenerateThoughtsFn,
    evaluate_fn: EvaluateThoughtFn,
    state: Any,
    breadth: int = 3,
    depth: int = 3,
    beam_size: int = 2,
    threshold: float = 0.9,
    search_strategy: Literal["bfs", "dfs", "beam"] = "beam",
) -> ToTResult:
    """Explore a reasoning tree rooted at *problem*.

    Args:
        problem: Problem description placed at the root.
        generate_fn: Async callable producing candidate thought texts for a
            node. Called with ``(problem, state, path_so_far)``.
        evaluate_fn: Async callable scoring a thought text.
        state: Opaque state object passed through to ``generate_fn`` /
            ``evaluate_fn``; not inspected here.
        breadth: Candidate thoughts requested per node (hint for the
            generator).
        depth: Maximum tree depth.
        beam_size: Number of top thoughts retained per level in ``beam`` mode.
        threshold: Score at or above which search stops early.
        search_strategy: ``"beam"`` (default), ``"bfs"``, or ``"dfs"``.

    Returns:
        :class:`ToTResult` with the best path and all visited thoughts.

    Raises:
        ValueError: For non-positive ``breadth``, ``depth``, or ``beam_size``.
        ToTError: If the search produces no thoughts at all.
    """
    if breadth < 1:
        raise ValueError(f"breadth must be >= 1; got {breadth}")
    if depth < 1:
        raise ValueError(f"depth must be >= 1; got {depth}")
    if beam_size < 1:
        raise ValueError(f"beam_size must be >= 1; got {beam_size}")

    otel = OTelManager()
    with otel.start_span("tot.search") as span:
        span.set_attribute("lightagent.tot.strategy", search_strategy)
        span.set_attribute("lightagent.tot.breadth", breadth)
        span.set_attribute("lightagent.tot.depth", depth)
        span.set_attribute("lightagent.tot.beam_size", beam_size)

        root = Thought(
            content=problem,
            score=0.0,
            depth=0,
            parent_id=None,
            children=[],
        )
        all_thoughts: list[Thought] = [root]
        parent_map: dict[str, Thought] = {root.id: root}

        if search_strategy == "beam":
            terminal = await _beam_search(
                root=root,
                problem=problem,
                generate_fn=generate_fn,
                evaluate_fn=evaluate_fn,
                state=state,
                breadth=breadth,
                depth=depth,
                beam_size=beam_size,
                threshold=threshold,
                all_thoughts=all_thoughts,
                parent_map=parent_map,
            )
        elif search_strategy == "bfs":
            terminal = await _bfs_search(
                root=root,
                problem=problem,
                generate_fn=generate_fn,
                evaluate_fn=evaluate_fn,
                state=state,
                breadth=breadth,
                depth=depth,
                threshold=threshold,
                all_thoughts=all_thoughts,
                parent_map=parent_map,
            )
        elif search_strategy == "dfs":
            terminal = await _dfs_search(
                root=root,
                problem=problem,
                generate_fn=generate_fn,
                evaluate_fn=evaluate_fn,
                state=state,
                breadth=breadth,
                depth=depth,
                threshold=threshold,
                all_thoughts=all_thoughts,
                parent_map=parent_map,
            )
        else:
            raise ValueError(f"Unknown search_strategy: {search_strategy!r}")

        non_root = [t for t in all_thoughts if t is not root]
        if not non_root:
            raise ToTError("Search produced no thoughts (generate_fn returned empty)")

        best = terminal or max(non_root, key=lambda t: t.score)
        best.is_terminal = True
        best_path = _path_from_root(best, parent_map)

        span.set_attribute("lightagent.tot.best_score", best.score)
        span.set_attribute("lightagent.tot.total_thoughts", len(non_root))

        logger.info(
            "tot_search_done",
            strategy=search_strategy,
            best_score=best.score,
            total_thoughts=len(non_root),
        )
        return ToTResult(
            best_thought=best,
            best_path=best_path,
            all_thoughts=all_thoughts,
            total_thoughts_generated=len(non_root),
        )


# ── Strategies ────────────────────────────────────────────────────────────────


async def _expand_node(
    node: Thought,
    problem: str,
    generate_fn: GenerateThoughtsFn,
    evaluate_fn: EvaluateThoughtFn,
    state: Any,
    parent_map: dict[str, Thought],
    all_thoughts: list[Thought],
) -> list[Thought]:
    """Generate + score one node's children. Returns the new :class:`Thought` list."""
    otel = OTelManager()
    with otel.start_span("tot.generate_thoughts"):
        path = _path_from_root(node, parent_map)
        texts = await generate_fn(problem, state, path)

    children: list[Thought] = []
    with otel.start_span("tot.evaluate_thoughts"):
        for text in texts:
            score = await evaluate_fn(text, state)
            child = Thought(
                content=text,
                score=score,
                depth=node.depth + 1,
                parent_id=node.id,
                children=[],
            )
            node.children.append(child)
            parent_map[child.id] = child  # id -> thought lookup
            all_thoughts.append(child)
            children.append(child)
    return children


async def _beam_search(
    *,
    root: Thought,
    problem: str,
    generate_fn: GenerateThoughtsFn,
    evaluate_fn: EvaluateThoughtFn,
    state: Any,
    breadth: int,
    depth: int,
    beam_size: int,
    threshold: float,
    all_thoughts: list[Thought],
    parent_map: dict[str, Thought],
) -> Thought | None:
    """Expand the top ``beam_size`` nodes each level; bail on threshold."""
    del breadth  # honoured by the generator via the API, not enforced here
    frontier = [root]
    for _d in range(depth):
        otel = OTelManager()
        level_children: list[Thought] = []
        for node in frontier:
            level_children.extend(
                await _expand_node(
                    node=node,
                    problem=problem,
                    generate_fn=generate_fn,
                    evaluate_fn=evaluate_fn,
                    state=state,
                    parent_map=parent_map,
                    all_thoughts=all_thoughts,
                )
            )
        if not level_children:
            break
        with otel.start_span("tot.beam_select") as span:
            span.set_attribute("lightagent.tot.candidates", len(level_children))
            best_at_level = max(level_children, key=lambda t: t.score)
            if best_at_level.score >= threshold:
                return best_at_level
            # Stable top-k: sort by score descending; Python sort is stable, so
            # ties retain insertion order.
            level_children.sort(key=lambda t: t.score, reverse=True)
            frontier = level_children[:beam_size]
    return None


async def _bfs_search(
    *,
    root: Thought,
    problem: str,
    generate_fn: GenerateThoughtsFn,
    evaluate_fn: EvaluateThoughtFn,
    state: Any,
    breadth: int,
    depth: int,
    threshold: float,
    all_thoughts: list[Thought],
    parent_map: dict[str, Thought],
) -> Thought | None:
    """Expand every node at every level — unbounded fanout, use with care."""
    del breadth  # honoured by the generator
    frontier = [root]
    for _d in range(depth):
        next_frontier: list[Thought] = []
        for node in frontier:
            children = await _expand_node(
                node=node,
                problem=problem,
                generate_fn=generate_fn,
                evaluate_fn=evaluate_fn,
                state=state,
                parent_map=parent_map,
                all_thoughts=all_thoughts,
            )
            next_frontier.extend(children)
            for child in children:
                if child.score >= threshold:
                    return child
        if not next_frontier:
            break
        frontier = next_frontier
    return None


async def _dfs_search(
    *,
    root: Thought,
    problem: str,
    generate_fn: GenerateThoughtsFn,
    evaluate_fn: EvaluateThoughtFn,
    state: Any,
    breadth: int,
    depth: int,
    threshold: float,
    all_thoughts: list[Thought],
    parent_map: dict[str, Thought],
) -> Thought | None:
    """Descend the highest-scoring branch first, backtrack only on dead ends."""
    del breadth

    async def recurse(node: Thought, remaining: int) -> Thought | None:
        if remaining == 0:
            return None
        children = await _expand_node(
            node=node,
            problem=problem,
            generate_fn=generate_fn,
            evaluate_fn=evaluate_fn,
            state=state,
            parent_map=parent_map,
            all_thoughts=all_thoughts,
        )
        if not children:
            return None
        # Explore highest-scoring children first
        for child in sorted(children, key=lambda t: t.score, reverse=True):
            if child.score >= threshold:
                return child
            deeper = await recurse(child, remaining - 1)
            if deeper is not None:
                return deeper
        return None

    return await recurse(root, depth)


# ── Utilities ─────────────────────────────────────────────────────────────────


def _path_from_root(node: Thought, parent_map: dict[str, Thought]) -> list[Thought]:
    """Walk ``parent_map`` up from *node* and return the list root → node."""
    path: list[Thought] = [node]
    current = node
    while current.parent_id is not None and current.parent_id in parent_map:
        parent = parent_map[current.parent_id]
        if parent is current:  # defensive guard against cycles
            break
        path.append(parent)
        current = parent
    path.reverse()
    return path


def make_tot_node(
    generate_fn: GenerateThoughtsFn,
    evaluate_fn: EvaluateThoughtFn,
    breadth: int = 3,
    depth: int = 3,
    beam_size: int = 2,
    threshold: float = 0.9,
    search_strategy: Literal["bfs", "dfs", "beam"] = "beam",
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build a LangGraph-compatible async node wrapping :func:`tree_of_thoughts`.

    The returned coroutine takes a state dict (with at least ``messages`` and
    ``metadata`` keys) and returns a partial state update: the best thought's
    text is appended as an ``AIMessage``, and a ``metadata["tot"]`` summary
    records ``best_score`` and ``total_thoughts`` for downstream consumers.

    When ``state["messages"]`` is empty the node is a no-op (returns ``{}``)
    so an empty supervisor routing does not raise.

    Args:
        generate_fn: Thought generator (see :data:`GenerateThoughtsFn`).
        evaluate_fn: Thought scorer (see :data:`EvaluateThoughtFn`).
        breadth: ToT breadth hint.
        depth: ToT depth cap.
        beam_size: ToT beam size.
        threshold: Early-stop score threshold.
        search_strategy: ``"beam"`` / ``"bfs"`` / ``"dfs"``.

    Returns:
        An async callable ``node(state) -> state_update``.
    """
    from langchain_core.messages import AIMessage

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages", []) or []
        if not messages:
            return {}
        problem = str(messages[-1].content)
        result = await tree_of_thoughts(
            problem=problem,
            generate_fn=generate_fn,
            evaluate_fn=evaluate_fn,
            state=state,
            breadth=breadth,
            depth=depth,
            beam_size=beam_size,
            threshold=threshold,
            search_strategy=search_strategy,
        )
        new_metadata = dict(state.get("metadata") or {})
        new_metadata["tot"] = {
            "best_score": result.best_thought.score,
            "total_thoughts": result.total_thoughts_generated,
            "strategy": search_strategy,
        }
        return {
            "messages": [AIMessage(content=result.best_thought.content)],
            "metadata": new_metadata,
        }

    return _node


__all__ = [
    "EvaluateThoughtFn",
    "GenerateThoughtsFn",
    "Thought",
    "ToTError",
    "ToTResult",
    "make_tot_node",
    "tree_of_thoughts",
]
