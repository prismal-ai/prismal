"""Supervisor graph quickstart — deterministic routing + offline graph build.

The core of prismal is a LangGraph SUPERVISOR state machine: a central
``supervisor`` node inspects the conversation, picks ONE specialist agent
(``coder``, ``researcher``, ``rag_agent``, ``planner``, …), that specialist
runs and returns control to the supervisor, which either routes again or
ends the turn (supervisor → specialist → supervisor → … → END).

Routing happens in two stages:

1. :func:`prismal.agents.intent_router.match_intent` — a deterministic,
   stdlib-only regex matcher that short-circuits high-confidence intents
   (cron management, Tree of Thoughts, debate, multimodal, kokoro, skynet)
   before any LLM is consulted.
2. LLM-based supervision — for everything ``match_intent`` returns ``None``
   on, the supervisor asks the configured LLM to pick the next agent.

This example stays fully OFFLINE: it demonstrates stage 1 on a batch of
utterances, injects a :class:`FakeToolProvider` (Fase Y — tools reach agents
only through the injected ``ToolProviderPort``), builds the real supervisor
graph with an in-memory checkpointer, and inspects its topology and the
initial :class:`AgentState`.  Actually *invoking* the graph requires an LLM
provider key, so no ``ainvoke`` is issued here.

Run::

    python examples/supervisor_quickstart.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from prismal.agents.extension.providers import FakeToolProvider
from prismal.agents.graph import build_supervisor_graph
from prismal.agents.intent_router import match_intent
from prismal.agents.state import create_initial_state
from prismal.agents.tool_registry import get_tools_for_agent, set_tool_provider

# Utterances covering every deterministic intent family plus two fall-throughs.
UTTERANCES: list[str] = [
    "Schedule a reminder every day at 9am",  # cron_manager (verb + recurrence)
    "List my cron jobs",  # cron_manager (verb + noun)
    "Use tree of thoughts to plan the rollout",  # tot_agent
    "Hold a debate about microservices",  # debate_agent
    "Reach a consensus on the API design",  # debate_consensus
    "Run an ETL over the sales exports",  # data_etl
    "Describe this image of the dashboard",  # multimodal_pipeline
    "Transcribe this voice note",  # multimodal_pipeline
    "Deliberate on the migration plan",  # kokoro
    "Run a swarm over these five repositories",  # skynet
    "Explain how cron works",  # None — educational denylist
    "What is the capital of France?",  # None — falls through to the LLM
]


@tool
def take_note(text: str) -> str:
    """Store a short research note (offline stand-in for a real tool)."""
    return f"noted: {text}"


def demo_intent_routing() -> None:
    """Stage 1 — deterministic regex routing, no LLM and no I/O involved."""
    print("== match_intent(): deterministic supervisor short-circuit ==")
    for utterance in UTTERANCES:
        target = match_intent(utterance)
        label = target if target is not None else "(none — LLM supervision)"
        print(f"  {utterance!r:<48} -> {label}")
    print()


async def demo_graph_structure() -> None:
    """Stage 2 — build the real supervisor graph offline and inspect it."""
    # Fase Y: inject a deterministic tool provider so no MCP/skills I/O runs.
    provider = FakeToolProvider(mapping={"researcher": [take_note]})
    set_tool_provider(provider)
    researcher_tools = [t.name for t in get_tools_for_agent("researcher")]
    print("== Injected FakeToolProvider ==")
    print(f"  researcher tools: {researcher_tools}")
    print(f"  critic tools:     {[t.name for t in get_tools_for_agent('critic')]}")
    print()

    # Build the compiled supervisor graph with an in-memory checkpointer.
    # (In async production code prefer ``await get_async_compiled_graph()``,
    # which wires an AsyncSqliteSaver; here we only inspect the topology.)
    with tempfile.TemporaryDirectory() as tmp:
        graph = build_supervisor_graph(
            checkpoint_path=Path(tmp) / "checkpoints.db",
            checkpointer=InMemorySaver(),
        )

        nodes = sorted(graph.get_graph().nodes)
        print(f"== Supervisor graph compiled ({len(nodes)} nodes) ==")
        for name in nodes:
            print(f"  - {name}")
        print()

    state = create_initial_state(session_id="quickstart-demo")
    print("== create_initial_state(session_id='quickstart-demo') ==")
    print(f"  current_agent: {state['current_agent']}")
    print(f"  next_agent:    {state['next_agent']}")
    print(f"  session_id:    {state['session_id']}")
    print(f"  created_at:    {state['created_at']}")
    print(f"  messages:      {state['messages']}")
    print()
    print("Every turn flows supervisor -> specialist -> supervisor until the")
    print("supervisor routes to END; with provider keys configured you would")
    print("now call `await graph.ainvoke(state, config)` with a thread_id.")


async def main() -> None:
    """Run both offline demonstrations."""
    demo_intent_routing()
    await demo_graph_structure()


if __name__ == "__main__":
    asyncio.run(main())
