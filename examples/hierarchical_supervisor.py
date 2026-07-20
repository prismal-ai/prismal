"""Hierarchical supervision demo — domain supervisors + network routing.

Two hierarchical building blocks, exercised fully OFFLINE:

1. :func:`prismal.agents.domain_supervisor.make_domain_supervisor` — the
   factory behind hierarchical mode (``PRISMAL_HIERARCHICAL_MODE=true``).
   In that mode the root supervisor routes to 3 *domain orchestrators*
   (research / engineering / analysis) instead of 12+ leaf agents, and each
   domain supervisor picks among ~3 leaf agents.  The factory does not take
   an injectable LLM, so this demo patches the module's ``ProviderRegistry``
   with a deterministic fake to show the three routing outcomes:

   * LLM picks a valid leaf agent  -> ``next_agent`` set to that agent
   * last message is a specialist's AIMessage -> loop breaker ENDs the turn
     (one-specialist-per-turn invariant, no LLM call at all)
   * LLM returns garbage -> defaults to END (graph always terminates)

2. :class:`prismal.agents.network_supervisor.NetworkSupervisorAgent` — the
   distributed variant: tasks are delegated to remote prismal nodes by
   capability tag, with graceful local fallback.  Here a subclass replaces
   the HTTP call and the local graph run with deterministic fakes so the
   capability selection + fallback logic runs offline.

Run::

    python examples/hierarchical_supervisor.py
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.domain_supervisor import make_domain_supervisor
from prismal.agents.network_supervisor import NetworkNode, NetworkSupervisorAgent
from prismal.agents.state import create_initial_state

# --------------------------------------------------------------------------
# Part 1 — domain supervisor with a fake routing LLM
# --------------------------------------------------------------------------


class FakeRoutingLLM:
    """Deterministic stand-in for the routing LLM (always one reply)."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        """Ignore the prompt and return the canned routing decision."""
        return AIMessage(content=self._reply)


class FakeProviderRegistry:
    """Stand-in for ``ProviderRegistry`` scoped to one canned reply."""

    reply: str = "researcher"

    def get_llm_with_fallback(self) -> FakeRoutingLLM:
        """Return the fake LLM instead of a real provider."""
        return FakeRoutingLLM(type(self).reply)


def _fresh_state(session_id: str) -> dict[str, Any]:
    """Initial state with one user question for the research domain."""
    state: dict[str, Any] = dict(create_initial_state(session_id=session_id))
    state["messages"] = [HumanMessage(content="Find recent papers on RAG evaluation.")]
    return state


async def demo_domain_supervisor() -> None:
    """Run the three deterministic routing outcomes of a domain supervisor."""
    research_supervisor = make_domain_supervisor(
        domain_name="research",
        agents=["researcher", "rag_agent"],
        domain_description=(
            "Handles web research, document retrieval and knowledge-base "
            "questions. Routes to the researcher for open-web queries and "
            "to the rag_agent for indexed-document questions."
        ),
    )
    print("== make_domain_supervisor('research', ['researcher', 'rag_agent']) ==")
    print(f"  node name: {research_supervisor.__name__}\n")

    with patch(
        "prismal.agents.domain_supervisor.ProviderRegistry",
        FakeProviderRegistry,
    ):
        # (a) Fake LLM answers "researcher" -> valid route.
        FakeProviderRegistry.reply = "researcher"
        update = await research_supervisor(_fresh_state("hier-demo-a"))
        print(f"  (a) fake LLM says 'researcher'  -> next_agent={update['next_agent']!r}")

        # (b) Loop breaker: the last message is an AIMessage produced by a
        # specialist (current_agent != research_supervisor), so the domain
        # supervisor ENDs the turn without invoking any LLM.
        state = _fresh_state("hier-demo-b")
        state["messages"].append(AIMessage(content="Here are three papers ..."))
        state["current_agent"] = "researcher"
        update = await research_supervisor(state)
        print(f"  (b) specialist just answered    -> next_agent={update['next_agent']!r} (END)")

        # (c) Invalid routing decision defaults to END (logged at WARNING).
        FakeProviderRegistry.reply = "banana"
        update = await research_supervisor(_fresh_state("hier-demo-c"))
        print(f"  (c) fake LLM says 'banana'      -> next_agent={update['next_agent']!r} (END)")

        meta = update["metadata"]["domain_research"]
        print(f"  per-domain iteration counter:  {meta['iteration_count']}\n")


# --------------------------------------------------------------------------
# Part 2 — network supervisor with deterministic remote/local fakes
# --------------------------------------------------------------------------


class OfflineNetworkSupervisor(NetworkSupervisorAgent):
    """NetworkSupervisorAgent with the two I/O seams replaced by fakes.

    ``delegate()`` (capability matching, remote-first ordering, fallback)
    is inherited unchanged — only the HTTP call and the local LangGraph run
    are stubbed so the example needs no server and no LLM.
    """

    async def _call_remote(
        self,
        node: NetworkNode,
        task: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Simulate a successful A2A round-trip to the remote node."""
        reply = f"[{node.name}] handled task: {task}"
        return {
            "messages": [AIMessage(content=reply)],
            "current_agent": "network_supervisor",
        }

    async def _run_local(self, task: str, session_id: str) -> dict[str, Any]:
        """Simulate the local-graph fallback instead of invoking an LLM."""
        return {
            "messages": [AIMessage(content=f"[local graph] handled task: {task}")],
            "current_agent": "network_supervisor",
        }


async def demo_network_supervisor() -> None:
    """Compose a two-node network and exercise remote + fallback routing."""
    nodes = [
        NetworkNode(
            name="research-node-eu",
            url="http://prismal-eu.internal:8080",
            capabilities=["research", "rag"],
        ),
        NetworkNode(
            name="coder-node-us",
            url="http://prismal-us.internal:8080",
            capabilities=["coding"],
        ),
    ]
    supervisor = OfflineNetworkSupervisor(nodes=nodes)

    print("== NetworkSupervisorAgent: capability-based delegation ==")
    for capability, task in [
        ("research", "Summarize the latest RAG evaluation benchmarks"),
        ("coding", "Refactor the payment service"),
        ("quantum", "Simulate a 40-qubit circuit"),  # no node -> local fallback
    ]:
        result = await supervisor.delegate(task, capability=capability, session_id="net-demo")
        print(f"  capability={capability!r:<11} -> {result['messages'][-1].content}")


async def main() -> None:
    """Run both hierarchical demonstrations."""
    await demo_domain_supervisor()
    await demo_network_supervisor()


if __name__ == "__main__":
    asyncio.run(main())
