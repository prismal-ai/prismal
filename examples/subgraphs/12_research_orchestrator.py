"""
Research Orchestrator — Orquestador Jerárquico del Dominio de Investigación
=============================================================================
Subgraph: prismal.agents.subgraphs.research_orchestrator

Dataset: arXiv (cs.CL + cs.IR) + MedQuAD — preguntas de investigación reales
  • `Langgraph_tutorials/data/arxiv/arxiv_papers.csv` — abstracts de
    papers que disparan al `researcher` (búsqueda externa, encuestas,
    síntesis de literatura).
  • `Langgraph_tutorials/data/medquad/medquad.csv` — preguntas médicas
    sobre una base de conocimiento ya curada → disparan al `rag_agent`.
  • Por qué: el `research_supervisor` (SPEC-042) decide entre estos dos
    canales, y los dos datasets son la dicotomía perfecta:
      - arXiv → "summarise / compare / find papers about" → researcher
      - MedQuAD → "what is / how does (X) work" sobre KB → rag_agent

Topología:
    research_supervisor (domain LLM router)
      ├──► researcher    (web/lit search + summary)
      └──► rag_agent     (internal vector store Q&A)
    cada hoja → research_supervisor (loop-breaker → END)

Modos de la demo:
  1. demo_simulation()      — enrutamiento por keywords (sin LLM)
  2. demo_real_langgraph()  — grafo real con stubs y MemorySaver

Uso:
    uv run python examples/subgraphs/12_research_orchestrator.py
"""

from __future__ import annotations

import asyncio
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Datasets ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[3] / "Langgraph_tutorials" / "data"
ARXIV_CSV = ROOT / "arxiv" / "arxiv_papers.csv"
MEDQUAD_CSV = ROOT / "medquad" / "medquad.csv"

RESEARCH_LEAVES = ("researcher", "rag_agent")


@dataclass
class ResearchTask:
    id: str
    request: str
    expected_leaf: str
    source: str  # "arxiv" | "medquad" | "embedded"


def _looks_like_rag_query(text: str) -> bool:
    """Heurística: preguntas factuales / definicionales → rag_agent."""
    t = text.lower()
    return bool(
        re.search(
            r"^\s*(what is|what are|how does|how do|why does|when is|"
            r"qué es|cómo funciona|cuáles? son)\b",
            t,
        )
        or " kb " in t
        or "knowledge base" in t
        or "internal docs" in t
    )


def _looks_like_research_query(text: str) -> bool:
    """Heurística: encuestas/búsquedas externas → researcher."""
    t = text.lower()
    return bool(
        re.search(
            r"\b(survey|summari[sz]e|compare|recent papers|state of the art|"
            r"benchmark|literature|review of|latest research)\b",
            t,
        )
        or "arxiv" in t
        or "papers" in t
    )


def _classify(text: str) -> str:
    if _looks_like_rag_query(text):
        return "rag_agent"
    if _looks_like_research_query(text):
        return "researcher"
    # Default: si no estamos seguros, una pregunta breve → rag, larga → researcher.
    return "rag_agent" if len(text) < 80 else "researcher"


def _embedded_tasks() -> list[ResearchTask]:
    return [
        ResearchTask(
            "R-01",
            "Summarise recent arXiv papers on retrieval-augmented generation.",
            "researcher",
            "embedded",
        ),
        ResearchTask("R-02", "What is type-2 diabetes?", "rag_agent", "embedded"),
        ResearchTask(
            "R-03", "Compare three state-of-the-art reranking benchmarks.", "researcher", "embedded"
        ),
        ResearchTask("R-04", "What are the symptoms of glaucoma?", "rag_agent", "embedded"),
    ]


def _load_tasks(limit_per_source: int = 4) -> list[ResearchTask]:
    out: list[ResearchTask] = []

    # arXiv → researcher
    if ARXIV_CSV.exists():
        try:
            with ARXIV_CSV.open(encoding="utf-8") as fh:
                for i, row in enumerate(csv.DictReader(fh)):
                    title = (row.get("title") or "").strip()
                    if not title:
                        continue
                    out.append(
                        ResearchTask(
                            id=f"AX-{i:03d}",
                            request=f"Summarise the contributions of the paper: {title!r}",
                            expected_leaf="researcher",
                            source="arxiv",
                        )
                    )
                    if sum(1 for t in out if t.source == "arxiv") >= limit_per_source:
                        break
        except Exception as exc:
            print(f"  (aviso: no se pudo leer {ARXIV_CSV.name}: {exc})")

    # MedQuAD → rag_agent
    if MEDQUAD_CSV.exists():
        try:
            with MEDQUAD_CSV.open(encoding="utf-8") as fh:
                for i, row in enumerate(csv.DictReader(fh)):
                    q = (row.get("question") or "").strip()
                    if not q:
                        continue
                    out.append(
                        ResearchTask(
                            id=f"MQ-{i:03d}",
                            request=q,
                            expected_leaf="rag_agent",
                            source="medquad",
                        )
                    )
                    if sum(1 for t in out if t.source == "medquad") >= limit_per_source:
                        break
        except Exception as exc:
            print(f"  (aviso: no se pudo leer {MEDQUAD_CSV.name}: {exc})")

    return out or _embedded_tasks()


# ── Stubs de las hojas ───────────────────────────────────────────────────────
@dataclass
class LeafResult:
    leaf: str
    summary: str
    citations: list[str] = field(default_factory=list)


def stub_researcher(t: ResearchTask) -> LeafResult:
    return LeafResult(
        "researcher",
        f"Ran external search for {t.id}. Synthesised findings from 5 sources.",
        citations=["arxiv:2604.02330", "arxiv:2604.02324", "web:semantic-scholar/..."],
    )


def stub_rag_agent(t: ResearchTask) -> LeafResult:
    return LeafResult(
        "rag_agent",
        f"Looked up {t.id} in internal KB; returned 3 relevant chunks.",
        citations=["kb:medquad#g78", "kb:medquad#g79", "kb:medquad#g80"],
    )


LEAVES = {"researcher": stub_researcher, "rag_agent": stub_rag_agent}
LEAF_ICONS = {"researcher": "🔬", "rag_agent": "📚"}


# ── Demo 1: simulación ──────────────────────────────────────────────────────
def demo_simulation(tasks: list[ResearchTask]) -> None:
    print("\n" + "=" * 72)
    print(" Demo 1 · Simulación de routing (sin LLM)")
    print("=" * 72)
    hits = 0
    for t in tasks:
        routed = _classify(t.request)
        ok = routed == t.expected_leaf
        hits += int(ok)
        res = LEAVES[routed](t)
        print(f"\n  {t.id} ({t.source}) — {t.request[:60]}")
        print(
            f"    supervisor → {LEAF_ICONS[routed]}{routed}  "
            f"(expected={t.expected_leaf}) {'✓' if ok else '✗'}"
        )
        print(f"    {res.summary}")
        print(f"    citations={res.citations}")
    print(f"\n  Accuracy: {hits}/{len(tasks)} ({100 * hits / len(tasks):.1f}%)")


# ── Demo 2: LangGraph real con stubs ─────────────────────────────────────────
async def demo_real_langgraph(tasks: list[ResearchTask]) -> None:
    print("\n" + "=" * 72)
    print(" Demo 2 · LangGraph real con SubgraphFactory + MemorySaver + stubs")
    print("=" * 72)
    try:
        from typing import Annotated, TypedDict

        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from prismal.agents.subgraphs.research_orchestrator.builder import (
            RESEARCH_AGENTS,
            _research_router,
        )
        from prismal.langgraph import StateGraph, add_messages
    except ImportError as exc:
        print(f"  ⚠  dependencia faltante: {exc}")
        return

    class DemoState(TypedDict, total=False):
        messages: Annotated[list, add_messages]
        current_agent: str
        next_agent: str | None
        metadata: dict
        session_id: str

    def _make_leaf_node(name: str):
        async def _node(state: DemoState) -> dict[str, Any]:
            task_id = state.get("metadata", {}).get("task_id", "?")
            return {
                "messages": [AIMessage(content=f"[{name}] resolved {task_id}", name=name)],
                "current_agent": name,
            }

        _node.__name__ = name
        return _node

    async def demo_supervisor(state: DemoState) -> dict[str, Any]:
        msgs = list(state.get("messages") or [])
        if (
            msgs
            and getattr(msgs[-1], "type", "") == "ai"
            and state.get("current_agent") in RESEARCH_AGENTS
        ):
            return {"current_agent": "research_supervisor", "next_agent": None}
        human = next((m for m in reversed(msgs) if getattr(m, "type", "") == "human"), None)
        request = human.content if human else ""
        return {
            "current_agent": "research_supervisor",
            "next_agent": _classify(request),
        }

    sg = StateGraph(DemoState)
    sg.add_node("research_supervisor", demo_supervisor)
    for leaf in RESEARCH_AGENTS:
        sg.add_node(leaf, _make_leaf_node(leaf))
        sg.add_edge(leaf, "research_supervisor")
    sg.set_entry_point("research_supervisor")
    sg.add_conditional_edges("research_supervisor", _research_router)
    compiled = sg.compile(checkpointer=MemorySaver())

    for t in tasks[:4]:
        cfg = {"configurable": {"thread_id": t.id}}
        state = {
            "messages": [HumanMessage(content=t.request)],
            "metadata": {"task_id": t.id},
        }
        final = await compiled.ainvoke(state, config=cfg)
        last = final["messages"][-1]
        print(f"\n  {t.id} → {last.name}: {last.content}")


async def main() -> None:
    tasks = _load_tasks(limit_per_source=3)
    sources = sorted({t.source for t in tasks})
    print(f"Loaded {len(tasks)} research tasks from sources={sources}")
    demo_simulation(tasks)
    await demo_real_langgraph(tasks)


if __name__ == "__main__":
    asyncio.run(main())
