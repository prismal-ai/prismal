"""LATS — Language Agent Tree Search (MCTS over agent actions).

Implements SPEC-PAT-004. Applies Monte Carlo Tree Search to explore the
action space of an agent. Each simulation performs the four classic MCTS
steps:

1. **Select** — from the root, traverse children by max UCB1 until a leaf.
2. **Expand** — if the leaf is non-terminal and has been visited, add
   children (one per candidate action).
3. **Simulate** — score the chosen state via the user-supplied reward
   function.
4. **Backpropagate** — walk root → leaf updating visit counts and total
   reward.

The UCB1 formula is the standard Auer et al. (2002) selection rule::

    ucb1(node) = Q(node) / N(node) + C * sqrt(ln(N_parent) / N(node))

where ``Q`` is accumulated reward, ``N`` is visit count, and ``C`` is the
exploration constant (default ``sqrt(2) ≈ 1.41``). Unvisited nodes return
``+inf`` so they are always chosen first (standard "first-play-urgency"
heuristic).

To keep the tree-search logic decoupled from any particular LLM or tool
framework, the agent accepts three callables:

- ``action_generator(state, tools)``: produce candidate actions for a state.
- ``transition_fn(state, action)``: apply an action, returning the new
  state.
- ``reward_fn(state)``: score a state in ``[0, 1]``.

Example::

    async def gen(state, tools):
        return await llm_generate_actions(state, tools)


    async def step(state, action):
        return await env.apply(action)


    async def score(state):
        return 1.0 if env.is_done(state) else 0.0


    agent = LATSAgent(
        tools=my_tools,
        reward_fn=score,
        action_generator=gen,
        transition_fn=step,
    )
    result = await agent.search(initial_state=start, goal="finish task X")
    print(result.best_action_sequence)
"""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from prismal.core.config import get_settings
from prismal.core.exceptions import LATSError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger("prismal.agents.patterns.lats")


@dataclass
class LATSNode:
    """One node in the MCTS tree.

    Attributes:
        state: Opaque agent state at this node.
        action: Action taken to reach this node from its parent; ``None`` at
            the root.
        reward: Accumulated reward (Q). Averaged by ``visits`` at UCB1 time.
        visits: Visit count (N).
        children: Child nodes from this state.
        is_terminal: Whether this node's state ends the search.
        parent: Parent node; ``None`` at the root.
    """

    state: Any
    action: Any | None
    reward: float = 0.0
    visits: int = 0
    children: list[LATSNode] = field(default_factory=list)
    is_terminal: bool = False
    parent: LATSNode | None = None

    def ucb1(self, exploration_constant: float = 1.41) -> float:
        """Return UCB1 score.

        Unvisited nodes return ``+inf`` (first-play-urgency). Root returns
        its pure exploitation value.
        """
        if self.visits == 0:
            return math.inf
        exploit = self.reward / self.visits
        if self.parent is None or self.parent.visits <= 0:
            return exploit
        explore = exploration_constant * math.sqrt(math.log(self.parent.visits) / self.visits)
        return exploit + explore


@dataclass
class LATSResult:
    """Outcome of :meth:`LATSAgent.search`.

    Attributes:
        best_action_sequence: Actions from root to the best-scoring leaf
            (exploits only — picks the child with the best average reward at
            each step).
        final_state: The state reached by following ``best_action_sequence``.
        total_simulations: Simulations actually run (≤ ``max_simulations``).
        best_reward: Average reward of the best leaf on its best path.
        search_tree_depth: Maximum depth reached in the tree.
    """

    best_action_sequence: list[Any]
    final_state: Any
    total_simulations: int
    best_reward: float
    search_tree_depth: int


ActionGeneratorFn = Callable[[Any, Sequence[Any]], Awaitable[Sequence[Any]]]
"""``(state, tools) -> list of candidate actions``."""

TransitionFn = Callable[[Any, Any], Awaitable[Any]]
"""``(state, action) -> new_state`` (pure; does not mutate ``state``)."""

RewardFn = Callable[[Any], Awaitable[float]]
"""``(state) -> reward in [0, 1]``."""


class LATSAgent:
    """MCTS-based agent over user-supplied action / transition / reward fns.

    Args:
        tools: Tools catalogue (opaque; forwarded to ``action_generator``).
        reward_fn: Scores a state in ``[0, 1]``.
        action_generator: Produces candidate actions for a state.
        transition_fn: Applies an action, returning the next state.
        max_simulations: Hard cap on MCTS iterations (default 50).
        exploration_constant: UCB1's ``C`` (default 1.41 = ``sqrt(2)``).
        max_depth: Maximum tree depth (default 10).
        timeout_seconds: Wall-clock budget; ``None`` means no timeout.
        terminal_reward: Reward threshold marking a node terminal
            (default 0.99).
        settings: Prismal settings. ``None`` resolves via
            :func:`~prismal.core.config.get_settings`.
    """

    def __init__(
        self,
        tools: Sequence[Any],
        reward_fn: RewardFn,
        action_generator: ActionGeneratorFn,
        transition_fn: TransitionFn,
        max_simulations: int = 50,
        exploration_constant: float = 1.41,
        max_depth: int = 10,
        timeout_seconds: float | None = None,
        terminal_reward: float = 0.99,
        settings: Settings | None = None,
    ) -> None:
        if max_simulations < 1:
            raise ValueError(f"max_simulations must be >= 1; got {max_simulations}")
        if max_depth < 1:
            raise ValueError(f"max_depth must be >= 1; got {max_depth}")
        if exploration_constant < 0.0:
            raise ValueError(f"exploration_constant must be >= 0; got {exploration_constant}")
        self._tools = tools
        self._reward_fn = reward_fn
        self._action_generator = action_generator
        self._transition_fn = transition_fn
        self._max_simulations = max_simulations
        self._exploration_constant = exploration_constant
        self._max_depth = max_depth
        self._timeout_seconds = timeout_seconds
        self._terminal_reward = terminal_reward
        self._settings = settings if settings is not None else get_settings()

    async def search(
        self,
        initial_state: Any,
        goal: str,
        budget_guard_fn: Callable[[dict[str, object]], Awaitable[bool]] | None = None,
    ) -> LATSResult:
        """Run MCTS from *initial_state* and return the best action path.

        Args:
            initial_state: Root state of the search.
            goal: Human-readable goal label (forwarded to logging only).

        Returns:
            :class:`LATSResult` with the best sequence of actions found.

        Raises:
            LATSError: If not a single simulation produced a non-zero reward
                AND no children were ever expanded (the search was vacuous).
        """
        otel = OTelManager()
        with otel.start_span("lats.search") as span:
            span.set_attribute("prismal.lats.max_simulations", self._max_simulations)
            span.set_attribute("prismal.lats.max_depth", self._max_depth)
            span.set_attribute("prismal.lats.exploration_constant", self._exploration_constant)

            root = LATSNode(state=initial_state, action=None)
            deadline = (
                time.monotonic() + self._timeout_seconds
                if self._timeout_seconds is not None
                else None
            )

            total_simulations = 0
            for _ in range(self._max_simulations):
                if deadline is not None and time.monotonic() >= deadline:
                    break
                # Budget pre-check; let one simulation run first so the search is
                # never vacuous, then a soft cap stops further simulations.
                if (
                    total_simulations >= 1
                    and budget_guard_fn is not None
                    and not await budget_guard_fn(
                        {"pattern": "lats", "simulations": total_simulations}
                    )
                ):
                    logger.debug("lats_budget_stop", simulations=total_simulations)
                    break
                await self._one_simulation(root)
                total_simulations += 1

            if total_simulations == 0:
                raise LATSError("No simulations executed (timeout too tight?)")

            best_path_nodes = self._best_path(root)
            if not best_path_nodes:
                raise LATSError("No children were expanded — action_generator returned empty")

            best_leaf = best_path_nodes[-1]
            best_avg_reward = best_leaf.reward / best_leaf.visits if best_leaf.visits else 0.0
            tree_depth = self._tree_depth(root)

            logger.info(
                "lats_search_done",
                goal=goal,
                total_simulations=total_simulations,
                best_reward=best_avg_reward,
                tree_depth=tree_depth,
            )
            span.set_attribute("prismal.lats.total_simulations", total_simulations)
            span.set_attribute("prismal.lats.best_reward", best_avg_reward)

            return LATSResult(
                best_action_sequence=[n.action for n in best_path_nodes if n.action is not None],
                final_state=best_leaf.state,
                total_simulations=total_simulations,
                best_reward=best_avg_reward,
                search_tree_depth=tree_depth,
            )

    # ── MCTS steps ────────────────────────────────────────────────────────────

    async def _one_simulation(self, root: LATSNode) -> None:
        """Select → expand → simulate → backpropagate."""
        # 1. Select
        node = root
        while node.children and not node.is_terminal:
            node = max(
                node.children,
                key=lambda c: c.ucb1(self._exploration_constant),
            )

        # 2. Expand (if non-terminal, visited at least once, and within depth).
        depth = self._node_depth(node)
        if not node.is_terminal and node.visits > 0 and depth < self._max_depth:
            await self._expand(node)
            if node.children:
                # Descend to the first fresh child for simulation.
                node = node.children[0]

        # 3. Simulate (evaluate reward).
        reward = await self._reward_fn(node.state)
        if reward >= self._terminal_reward:
            node.is_terminal = True

        # 4. Backpropagate.
        self._backpropagate(node, reward)

    async def _expand(self, node: LATSNode) -> None:
        actions = await self._action_generator(node.state, self._tools)
        for action in actions:
            new_state = await self._transition_fn(node.state, action)
            child = LATSNode(
                state=new_state,
                action=action,
                parent=node,
            )
            node.children.append(child)

    def _backpropagate(self, node: LATSNode, reward: float) -> None:
        current: LATSNode | None = node
        while current is not None:
            current.visits += 1
            current.reward += reward
            current = current.parent

    # ── utilities ────────────────────────────────────────────────────────────

    def _node_depth(self, node: LATSNode) -> int:
        depth = 0
        current: LATSNode | None = node.parent
        while current is not None:
            depth += 1
            current = current.parent
        return depth

    def _tree_depth(self, root: LATSNode) -> int:
        if not root.children:
            return 0
        return 1 + max(self._tree_depth(c) for c in root.children)

    def _best_path(self, root: LATSNode) -> list[LATSNode]:
        """Follow the best-average-reward child at each level."""
        path: list[LATSNode] = []
        node = root
        while node.children:
            next_node = max(
                node.children,
                key=lambda c: (c.reward / c.visits) if c.visits else -math.inf,
            )
            path.append(next_node)
            node = next_node
        return path


__all__ = [
    "ActionGeneratorFn",
    "LATSAgent",
    "LATSError",
    "LATSNode",
    "LATSResult",
    "RewardFn",
    "TransitionFn",
]
