"""``PrismalStateGraphBuilder`` — fluent builder over ``StateGraph[AgentState]``.

Collects nodes/edges and produces a :class:`SubgraphDefinition` (registrable
in :class:`SubgraphRegistry`) via :meth:`compile`, or a raw
``CompiledStateGraph`` via :meth:`compile_raw`.

Plain callables passed to :meth:`add_node` are auto-wrapped with
:func:`prismal_node` using the builder defaults; callables that already carry
``__prismal_node__`` are used unchanged.

Example::

    builder = PrismalStateGraphBuilder("my_pipeline")
    builder.add_node("classify", classify_fn, capabilities=["general"])
    builder.add_node("respond", respond_fn)
    builder.add_edge("classify", "respond")
    builder.add_edge("respond", "__end__")
    builder.set_entry_point("classify")
    subgraph = builder.compile()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from langgraph.graph import END, StateGraph

from prismal.agents.extension.decorators import (
    NodeFn,
    RetryPolicy,
    SecurityLevel,
    prismal_node,
)
from prismal.agents.state import AgentState
from prismal.agents.subgraphs.registry import SubgraphDefinition

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.graph.state import CompiledStateGraph
    from pydantic import BaseModel

    from prismal.core.config import Settings

_END_SENTINEL = "__end__"


@dataclass(frozen=True)
class BuilderDefaults:
    """Defaults applied by :meth:`add_node` to nodes lacking ``@prismal_node``."""

    security: SecurityLevel = "standard"
    audit: bool = True
    timeout_s: float | None = None
    retry: RetryPolicy | None = None


class PrismalStateGraphBuilder:
    """Fluent builder over ``StateGraph[AgentState]`` with prismal defaults."""

    def __init__(
        self,
        name: str,
        *,
        defaults: BuilderDefaults | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Initialise an empty builder.

        Args:
            name: Subgraph name (used for the ``SubgraphDefinition`` and traces).
            defaults: Defaults applied to auto-wrapped nodes.
            settings: Optional prismal settings (reserved for future use).
        """
        self.name = name
        self._defaults = defaults or BuilderDefaults()
        self._settings = settings
        self._nodes: dict[str, NodeFn] = {}
        self._edges: list[tuple[str, str]] = []
        self._conditional: dict[str, Callable[[AgentState], str]] = {}
        self._entry_point: str | None = None
        self._security_counter = 0

    def add_node(
        self,
        name: str,
        fn: NodeFn,
        *,
        capabilities: list[str] | None = None,
        security: SecurityLevel | None = None,
        audit: bool | None = None,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
        input_model: type[BaseModel] | None = None,
        output_model: type[BaseModel] | None = None,
    ) -> PrismalStateGraphBuilder:
        """Add a node, auto-wrapping with ``@prismal_node`` when needed.

        ``input_model``/``output_model`` (Phase NTS) follow the same
        "forwarded only if ``fn`` is not already ``@prismal_node``-decorated"
        rule as every other kwarg: if ``fn`` already carries
        ``__prismal_node__``, all kwargs here — including these two — are
        ignored.

        Raises:
            ValueError: If ``name`` is already registered in this builder.
        """
        if name in self._nodes:
            raise ValueError(f"Node '{name}' already added to builder '{self.name}'")
        if getattr(fn, "__prismal_node__", None) is not None:
            self._nodes[name] = fn
            return self
        wrapped = prismal_node(
            name=name,
            capabilities=capabilities,
            security=security if security is not None else self._defaults.security,
            audit=audit if audit is not None else self._defaults.audit,
            timeout_s=timeout_s if timeout_s is not None else self._defaults.timeout_s,
            retry=retry if retry is not None else self._defaults.retry,
            input_model=input_model,
            output_model=output_model,
        )(fn)
        self._nodes[name] = wrapped
        return self

    def add_supervisor_node(
        self,
        routing_fn: Callable[[AgentState], Awaitable[str]],
        *,
        valid_next: list[str],
        name: str = "supervisor",
    ) -> PrismalStateGraphBuilder:
        """Add a supervisor node that routes to one of ``valid_next``.

        The node is intentionally *not* wrapped with ``@prismal_node`` so that
        an out-of-range routing decision surfaces as a plain ``ValueError`` at
        runtime (rather than being mapped to an error state_update).

        Raises:
            ValueError: If ``name`` is already registered in this builder.
        """
        if name in self._nodes:
            raise ValueError(f"Node '{name}' already added to builder '{self.name}'")
        allowed = list(valid_next)

        async def _supervisor(state: AgentState) -> dict[str, Any]:
            nxt = await routing_fn(state)
            if nxt not in allowed:
                raise ValueError(
                    f"Supervisor '{name}' routed to invalid node '{nxt}'; valid targets: {allowed}"
                )
            return {"next_agent": nxt}

        self._nodes[name] = _supervisor
        self._conditional[name] = lambda state: str(state.get("next_agent"))
        return self

    def add_security_layer(
        self,
        *,
        at: Literal["entry", "exit"] = "entry",
        sanitizer: Any | None = None,
    ) -> PrismalStateGraphBuilder:
        """Insert a dedicated sanitisation node at the subgraph border.

        For ``at="entry"`` the new node becomes the entry point and is wired to
        the previous entry. For ``at="exit"`` every node currently pointing to
        ``__end__`` is rerouted through the new node, which then points to
        ``__end__``.
        """
        from prismal.security.sanitizer import InputSanitizer

        san = sanitizer or InputSanitizer()
        self._security_counter += 1
        node_name = f"_security_{at}_{self._security_counter}"

        async def _security_node(state: AgentState) -> dict[str, Any]:
            from langchain_core.messages import HumanMessage

            sanitized: str | None = None
            for msg in reversed(state.get("messages") or []):
                if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
                    sanitized = san.sanitize(msg.content)
                    break
            return {"metadata": {"security_layer": {"at": at, "sanitized": sanitized is not None}}}

        self._nodes[node_name] = _security_node

        if at == "entry":
            previous_entry = self._entry_point
            self._entry_point = node_name
            if previous_entry is not None:
                self._edges.append((node_name, previous_entry))
        else:  # exit
            rerouted = [(f, t) for (f, t) in self._edges if t == _END_SENTINEL]
            self._edges = [(f, t) for (f, t) in self._edges if t != _END_SENTINEL]
            for from_node, _ in rerouted:
                self._edges.append((from_node, node_name))
            self._edges.append((node_name, _END_SENTINEL))
        return self

    def add_edge(self, from_: str, to: str) -> PrismalStateGraphBuilder:
        """Add a linear edge. Use ``"__end__"`` as ``to`` to reach ``END``."""
        self._edges.append((from_, to))
        return self

    def add_conditional_edges(
        self,
        from_: str,
        decision_fn: Callable[[AgentState], str],
        mapping: dict[str, str],
    ) -> PrismalStateGraphBuilder:
        """Add conditional routing from ``from_`` using ``decision_fn`` + ``mapping``."""

        def _route(state: AgentState) -> str:
            key = decision_fn(state)
            return mapping.get(key, key)

        self._conditional[from_] = _route
        return self

    def set_entry_point(self, name: str) -> PrismalStateGraphBuilder:
        """Set the subgraph entry node."""
        self._entry_point = name
        return self

    def _validate(self) -> None:
        if self._entry_point is None:
            raise ValueError(f"Builder '{self.name}' has no entry point set")
        if self._entry_point not in self._nodes:
            raise ValueError(f"Entry point '{self._entry_point}' is not a registered node")
        known = set(self._nodes) | {_END_SENTINEL, END}
        for from_, to in self._edges:
            if from_ not in self._nodes:
                raise ValueError(f"Edge source '{from_}' is not a registered node")
            if to not in known:
                raise ValueError(f"Edge target '{to}' is not a registered node")
        for source in self._conditional:
            if source not in self._nodes:
                raise ValueError(f"Conditional edge source '{source}' is not a registered node")

    def compile(self) -> SubgraphDefinition:
        """Validate and return a :class:`SubgraphDefinition`."""
        self._validate()
        assert self._entry_point is not None
        return SubgraphDefinition(
            name=self.name,
            description=f"{self.name} (built via PrismalStateGraphBuilder)",
            entry_point=self._entry_point,
            nodes=dict(self._nodes),
            edges=list(self._edges),
            conditional_edges=dict(self._conditional),
        )

    def compile_raw(self) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Escape hatch: validate and return a raw ``CompiledStateGraph``."""
        self._validate()
        assert self._entry_point is not None
        # ty (alpha) does not match our AgentState TypedDict against langgraph's
        # StateT bound, though mypy and the other 10 identical StateGraph(AgentState)
        # call sites accept it. Suppress the lone ty false positive.
        graph: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)  # ty: ignore[invalid-argument-type]
        for node_name, node_fn in self._nodes.items():
            graph.add_node(node_name, cast("Any", node_fn))
        graph.set_entry_point(self._entry_point)
        for from_, to in self._edges:
            graph.add_edge(from_, END if to == _END_SENTINEL else to)
        for source, route in self._conditional.items():
            graph.add_conditional_edges(source, route)
        return graph.compile()


__all__ = ["BuilderDefaults", "PrismalStateGraphBuilder"]
