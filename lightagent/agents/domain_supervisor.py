"""Factory for domain sub-orchestrator nodes (SPEC-042 Phase 40).

A **domain supervisor** is a mini version of
:func:`lightagent.agents.supervisor.supervisor_node` scoped to a
specific subset of leaf agents — e.g. a "research" orchestrator that
only knows about ``researcher``, ``rag_agent`` and ``cua_agent``. In
hierarchical mode (``LIGHTAGENT_HIERARCHICAL_MODE=true``) the root
supervisor routes to three such domain orchestrators instead of all
12+ leaf agents directly, keeping each routing LLM prompt focused on
~3 targets and making routing accuracy much more predictable.

This module only exposes the **factory**. The domain subgraphs that
wrap the returned nodes live in
``lightagent/agents/subgraphs/{research,engineering,analysis}_orchestrator/``
(created in T-336) and the conditional graph compilation that picks
flat vs hierarchical mode lives in ``graph.py`` (T-337).

Design notes
------------

* **Isolated iteration counters** — each domain supervisor stashes its
  own counter under
  ``state["metadata"][f"domain_{domain_name}"]["iteration_count"]`` so
  two domains running in parallel never fight over a shared slot and
  loop breakers trigger at a per-domain cap rather than a global one.
* **Same loop-breaker semantics as the root supervisor** — if the most
  recent message is an ``AIMessage`` from a specialist (i.e. the
  previous node was *not* the domain supervisor itself), route
  immediately to END. This guarantees one-specialist-then-return
  progress even if the LLM gets stuck in a routing loop.
* **Invalid LLM responses default to END** — just like the root
  supervisor. A malformed or off-topic routing decision is logged at
  WARNING and mapped to END so the graph always terminates.
* **No direct access to parent state** — the domain supervisor only
  reads ``state["messages"]`` and its own namespaced metadata slot,
  complying with CLAUDE.md Phase 24+ rule "NEVER access parent
  supervisor state from within a subgraph".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.core.logging import get_logger
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.domain_supervisor")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: How many recent messages to send to the LLM per routing call. Mirrors
#: the ``_HISTORY_WINDOW`` used by the root supervisor so token
#: consumption stays comparable.
_HISTORY_WINDOW: int = 6

#: Hard cap on routing iterations inside a single domain. When the
#: counter reaches this value the supervisor force-routes to END rather
#: than invoking the LLM again. Keeps runaway loops bounded per domain.
_MAX_DOMAIN_ITERATIONS: int = 8

#: Special routing target meaning "no more agents — hand control back to
#: the root supervisor (or END, depending on wiring)". Mirrors the
#: LangGraph ``END`` sentinel used elsewhere in the project.
_END_TARGET: str = "__end__"


def _metadata_slot(domain_name: str) -> str:
    """Return the metadata key where a domain stores its loop state."""
    return f"domain_{domain_name}"


def _build_system_prompt(
    domain_name: str,
    domain_description: str,
    agents: list[str],
) -> str:
    """Render the routing system prompt for a domain supervisor.

    Kept deliberately terse compared to the root supervisor prompt —
    the factory's whole point is to narrow the routing search space, so
    we don't dump huge per-agent descriptions here. Callers that need
    richer guidance can override the prompt by passing their own
    ``domain_description``.
    """
    agent_list = "\n".join(f"  - {name}" for name in agents)
    return (
        f"You are the **{domain_name}** domain orchestrator.\n\n"
        f"## Responsibility\n{domain_description}\n\n"
        f"## Available agents (ONLY these)\n{agent_list}\n\n"
        "## Routing rules\n"
        "1. Read the conversation history and identify the single most "
        "appropriate agent from the list above for the NEXT step.\n"
        "2. Respond with ONLY the agent name or the literal 'END'. "
        "No explanation, no punctuation, no prose.\n"
        "3. Route to 'END' when the domain task is complete or when "
        "the user request does not belong to this domain at all.\n"
        "4. If the most recent message is already an AIMessage from a "
        "specialist in this domain, return 'END' — one specialist "
        "response per turn is the rule.\n"
        "5. Never route to an agent outside the list above. If the "
        "task needs a different domain, return 'END' so the root "
        "supervisor can re-route."
    )


def make_domain_supervisor(
    domain_name: str,
    agents: list[str],
    domain_description: str,
) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    """Build an async domain sub-orchestrator node.

    The returned coroutine has the exact shape LangGraph expects for a
    node function: it takes an :class:`AgentState` and returns a
    partial state update dict. It can be registered as the entry point
    of a subgraph (T-336) or dropped directly into a flat graph as an
    intermediate routing node.

    Args:
        domain_name: Stable identifier for the domain, e.g.
            ``"research"``. Used in log fields, span attributes, and
            the metadata slot key. Lowercase ASCII recommended.
        agents: Names of the leaf agents the domain is allowed to route
            to. Must match the node names registered in the enclosing
            graph / subgraph — the factory does not validate that the
            nodes actually exist (no access to the builder at factory
            time), so wiring errors surface at LangGraph compile time.
        domain_description: One-paragraph description inserted into the
            system prompt. Should answer "what kinds of requests does
            this domain handle?" in prose the LLM can reason about.

    Returns:
        An ``async`` node function usable as a LangGraph node.
    """
    if not agents:
        raise ValueError(
            f"make_domain_supervisor({domain_name!r}): ``agents`` must "
            f"be a non-empty list; got {agents!r}."
        )

    valid_routes: frozenset[str] = frozenset(agents) | {"END"}
    system_prompt = _build_system_prompt(domain_name, domain_description, agents)
    supervisor_node_name = f"{domain_name}_supervisor"

    async def domain_supervisor_node(
        state: AgentState,
    ) -> dict[str, Any]:
        """Route one turn within the ``{domain_name}`` domain.

        Implements the same ReAct-style routing contract as
        :func:`lightagent.agents.supervisor.supervisor_node` but
        scoped to ``agents``. All state mutations are performed via a
        partial-dict return value so LangGraph's reducers handle the
        merge.
        """
        session_id: str = str(state.get("session_id", "unknown"))
        otel = OTelManager()

        # Read (or initialize) the per-domain iteration counter. Kept
        # under ``metadata[domain_<name>]`` to avoid clashes between
        # parallel domains.
        slot_key = _metadata_slot(domain_name)
        metadata: dict[str, Any] = dict(state.get("metadata", {}))
        domain_slot: dict[str, Any] = dict(metadata.get(slot_key, {}))
        iteration: int = int(domain_slot.get("iteration_count", 0))

        logger.debug(
            "domain_supervisor_invoked",
            domain=domain_name,
            session_id=session_id,
            iteration=iteration,
            message_count=len(state["messages"]),
        )

        with otel.start_span(
            f"agent.domain_supervisor.{domain_name}",
            attributes={
                "lightagent.agent": supervisor_node_name,
                "lightagent.domain": domain_name,
                "lightagent.session_id": session_id,
                "lightagent.domain_iteration": iteration,
            },
        ) as span:
            # --------------------------------------------------------- #
            # Hard iteration cap — force END before touching the LLM.
            # --------------------------------------------------------- #
            if iteration >= _MAX_DOMAIN_ITERATIONS:
                logger.warning(
                    "domain_supervisor_iteration_cap_reached",
                    domain=domain_name,
                    cap=_MAX_DOMAIN_ITERATIONS,
                    session_id=session_id,
                )
                span.set_attribute("lightagent.routing_decision", "END")
                return _update_state(
                    supervisor_node_name,
                    next_agent=None,
                    metadata=metadata,
                    domain_slot=domain_slot,
                    slot_key=slot_key,
                    iteration=iteration + 1,
                )

            trimmed = state["messages"][-_HISTORY_WINDOW:]

            # --------------------------------------------------------- #
            # Loop breaker: if the previous node was a specialist from
            # this domain, END the turn immediately. Mirrors the root
            # supervisor's one-specialist-per-turn invariant.
            # --------------------------------------------------------- #
            if trimmed:
                last_msg = trimmed[-1]
                last_is_ai = getattr(last_msg, "type", "") == "ai"
                last_agent = state.get("current_agent", "")
                if last_is_ai and last_agent != supervisor_node_name:
                    logger.debug(
                        "domain_supervisor_loop_break",
                        domain=domain_name,
                        last_agent=last_agent,
                        session_id=session_id,
                    )
                    span.set_attribute("lightagent.routing_decision", "END")
                    return _update_state(
                        supervisor_node_name,
                        next_agent=None,
                        metadata=metadata,
                        domain_slot=domain_slot,
                        slot_key=slot_key,
                        iteration=iteration + 1,
                    )

            # --------------------------------------------------------- #
            # Ask the LLM to pick the next leaf agent.
            # --------------------------------------------------------- #
            llm = ProviderRegistry().get_llm_with_fallback()
            routing_messages = [
                SystemMessage(content=system_prompt),
                *trimmed,
                HumanMessage(
                    content=(
                        "Based on the conversation above, which agent "
                        "should handle the next step? Respond with ONLY "
                        "the agent name or 'END'."
                    )
                ),
            ]

            response = await llm.ainvoke(routing_messages)
            raw: str = str(getattr(response, "content", "")).strip()
            normalised = raw.strip("\"' \t\n").upper()

            matched: str | None = None
            for valid in valid_routes:
                if valid.upper() == normalised:
                    matched = valid
                    break

            if matched is None:
                logger.warning(
                    "domain_supervisor_invalid_routing",
                    domain=domain_name,
                    raw_response=raw,
                    defaulting_to="END",
                    session_id=session_id,
                )
                matched = "END"

            next_agent: str | None = None if matched == "END" else matched
            span.set_attribute("lightagent.routing_decision", matched)

            logger.info(
                "domain_supervisor_routing_decision",
                domain=domain_name,
                next_agent=next_agent,
                raw_response=raw,
                session_id=session_id,
                iteration=iteration + 1,
            )

            return _update_state(
                supervisor_node_name,
                next_agent=next_agent,
                metadata=metadata,
                domain_slot=domain_slot,
                slot_key=slot_key,
                iteration=iteration + 1,
            )

    # Annotate the node function so it shows up nicely in LangGraph
    # debug output and so two factory calls for different domains don't
    # produce indistinguishable ``domain_supervisor_node`` entries.
    domain_supervisor_node.__name__ = supervisor_node_name
    domain_supervisor_node.__qualname__ = supervisor_node_name
    return domain_supervisor_node


def _update_state(
    supervisor_node_name: str,
    *,
    next_agent: str | None,
    metadata: dict[str, Any],
    domain_slot: dict[str, Any],
    slot_key: str,
    iteration: int,
) -> dict[str, Any]:
    """Build the partial state dict a domain supervisor returns.

    Consolidated here so every code path in ``domain_supervisor_node``
    writes the metadata slot the same way — easy to audit, easy to
    extend when a new field (e.g. ``last_routed_to``) needs to be
    stashed under ``metadata[domain_<name>]``.
    """
    domain_slot["iteration_count"] = iteration
    metadata[slot_key] = domain_slot
    return {
        "current_agent": supervisor_node_name,
        "next_agent": next_agent,
        "messages": [],
        "metadata": metadata,
    }


__all__ = ["make_domain_supervisor"]
