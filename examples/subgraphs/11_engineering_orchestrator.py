"""
Engineering Orchestrator — Orquestador Jerárquico del Dominio de Ingeniería
=============================================================================
Subgraph: prismal.agents.subgraphs.engineering_orchestrator

Dataset: GitHub Issues (LangChain) — peticiones reales de ingeniería
  • `Langgraph_tutorials/data/github-issues/github_issues.csv` contiene
    issues abiertos del repo `langchain-ai/langchain`. Filtramos issues
    con `body` no vacío y los reclasificamos a las 5 hojas del dominio:
      - coder           : "fix the bug in", "implement feature"
      - codeact         : "this code throws", "run this script and"
      - planner         : "design a new", "what's the architecture for"
      - file_manager    : "rename / move / locate file"
      - skill_manager   : "install / activate / build skill"
  • Por qué: GitHub Issues son peticiones de ingeniería reales con
    fraseo natural — perfecto para probar el routing del
    `engineering_supervisor` (SPEC-042 / Phase 40).

Topología:
    engineering_supervisor (domain LLM router)
      ├──► coder           (ReAct + tools)
      ├──► codeact         (genera y ejecuta Python en sandbox)
      ├──► planner         (descomposición SDD / specs)
      ├──► file_manager    (operaciones filesystem)
      └──► skill_manager   (install/activate skills)
    cada hoja → engineering_supervisor (loop-breaker → END)

Modos de la demo:
  1. demo_simulation()      — enrutamiento por keywords (sin LLM)
  2. demo_real_langgraph()  — grafo real con stubs y MemorySaver

Uso:
    uv run python examples/subgraphs/11_engineering_orchestrator.py
"""

from __future__ import annotations

import asyncio
import csv
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Dataset: GitHub Issues reclasificadas a las 5 hojas del dominio ──────────
GH_ISSUES = (
    Path(__file__).resolve().parents[3]
    / "Langgraph_tutorials"
    / "data"
    / "github-issues"
    / "github_issues.csv"
)

ENGINEERING_LEAVES = ("coder", "codeact", "planner", "file_manager", "skill_manager")


@dataclass
class EngTask:
    """Petición de ingeniería derivada de un GitHub Issue."""

    id: str
    title: str
    request: str
    expected_leaf: str
    complexity: str = "MEDIUM"


def _classify(title: str, body: str) -> str:
    """Heurística de demostración para asignar leaf esperado por contenido."""
    text = f"{title} {body}".lower()
    if re.search(r"\b(spec|design|architecture|prd|plan)\b", text):
        return "planner"
    if re.search(r"\b(file|path|rename|move|directory|workspace)\b", text):
        return "file_manager"
    if re.search(r"\b(skill|install|activate|register|plugin)\b", text):
        return "skill_manager"
    if re.search(r"\b(traceback|run this|execute|script|notebook|stdout)\b", text):
        return "codeact"
    return "coder"


def _embedded_tasks() -> list[EngTask]:
    """Plan B si el CSV no está disponible."""
    return [
        EngTask(
            "ENG-01",
            "Fix TypeError in chat_models.py",
            "There's a TypeError when calling .invoke() with None input. "
            "Need to add a guard and a regression test.",
            "coder",
            "MEDIUM",
        ),
        EngTask(
            "ENG-02",
            "Implement new YAML parser",
            "Run this script and capture stdout — the parser should accept multi-document streams.",
            "codeact",
            "HIGH",
        ),
        EngTask(
            "ENG-03",
            "Design new retrieval architecture",
            "We need a spec for the new hybrid retrieval architecture with "
            "BM25 + dense + reranking. Write a PRD.",
            "planner",
            "HIGH",
        ),
        EngTask(
            "ENG-04",
            "Rename `utils.py` to `helpers.py`",
            "Move every reference and update imports across the package.",
            "file_manager",
            "LOW",
        ),
        EngTask(
            "ENG-05",
            "Install pdf-export skill",
            "We need to register and activate the pdf-export skill for the docs team.",
            "skill_manager",
            "LOW",
        ),
        EngTask(
            "ENG-06",
            "Bug: race condition in cache layer",
            "The cache returns stale entries under concurrent writes. "
            "Reproduce, isolate, and patch.",
            "coder",
            "HIGH",
        ),
    ]


def _load_tasks(limit: int = 8) -> list[EngTask]:
    """Cargar issues del CSV y mapearlos a EngTask con leaf esperado."""
    if not GH_ISSUES.exists():
        return _embedded_tasks()

    out: list[EngTask] = []
    try:
        with GH_ISSUES.open(encoding="utf-8") as fh:
            for i, row in enumerate(csv.DictReader(fh)):
                body = (row.get("body") or "").strip()
                title = (row.get("title") or "").strip()
                if not body or len(body) < 30:
                    continue
                out.append(
                    EngTask(
                        id=f"GH-{i:04d}",
                        title=title[:80],
                        request=textwrap.shorten(body, 220),
                        expected_leaf=_classify(title, body),
                    )
                )
                if len(out) >= limit:
                    break
    except Exception:
        return _embedded_tasks()
    return out or _embedded_tasks()


# ── Simulación del engineering_supervisor (sin LLM) ──────────────────────────
def simulate_supervisor(request: str) -> str:
    """Domain-supervisor mock: misma heurística que la usada al construir el dataset."""
    return _classify(request, request)


# ── Stubs de las hojas ───────────────────────────────────────────────────────
LEAF_ICONS = {
    "coder": "🧑‍💻",
    "codeact": "🐍",
    "planner": "📐",
    "file_manager": "📁",
    "skill_manager": "🛠 ",
}


@dataclass
class LeafResult:
    leaf: str
    summary: str
    artifacts: list[str] = field(default_factory=list)


def stub_coder(t: EngTask) -> LeafResult:
    return LeafResult(
        "coder",
        f"Patched issue {t.id}. Added regression test and updated CHANGELOG.",
        ["patch.diff", "test_regression.py"],
    )


def stub_codeact(t: EngTask) -> LeafResult:
    return LeafResult(
        "codeact",
        f"Executed Python plan for {t.id}; produced expected stdout and validated outputs.",
        ["plan.py", "stdout.log"],
    )


def stub_planner(t: EngTask) -> LeafResult:
    return LeafResult(
        "planner",
        f"Drafted PRD + SDD spec for {t.id}; identified 4 milestones and 6 risks.",
        ["spec.md", "milestones.png"],
    )


def stub_file_manager(t: EngTask) -> LeafResult:
    return LeafResult(
        "file_manager",
        f"Renamed and re-wired imports for {t.id}; 17 files changed under workspace.",
        ["rename.log"],
    )


def stub_skill_manager(t: EngTask) -> LeafResult:
    return LeafResult(
        "skill_manager",
        f"Installed + activated skill referenced by {t.id}; smoke test passed.",
        ["skills/active/*"],
    )


LEAVES = {
    "coder": stub_coder,
    "codeact": stub_codeact,
    "planner": stub_planner,
    "file_manager": stub_file_manager,
    "skill_manager": stub_skill_manager,
}


# ── Demo 1: simulación pura ──────────────────────────────────────────────────
def demo_simulation(tasks: list[EngTask]) -> None:
    print("\n" + "=" * 72)
    print(" Demo 1 · Simulación de routing (sin LLM, sin LangGraph)")
    print("=" * 72)

    hits = 0
    for t in tasks:
        routed = simulate_supervisor(t.request)
        ok = routed == t.expected_leaf
        hits += int(ok)
        result = LEAVES[routed](t)
        print(f"\n  {t.id} · {t.title[:60]}")
        print(
            f"    supervisor → {LEAF_ICONS[routed]}{routed}    "
            f"(expected={t.expected_leaf})  {'✓' if ok else '✗'}"
        )
        print(f"    {result.summary[:90]}")
    pct = 100.0 * hits / len(tasks)
    print(f"\n  Routing accuracy: {hits}/{len(tasks)} ({pct:.1f}%)")


# ── Demo 2: LangGraph real con stubs ─────────────────────────────────────────
async def demo_real_langgraph(tasks: list[EngTask]) -> None:
    print("\n" + "=" * 72)
    print(" Demo 2 · LangGraph real con SubgraphFactory + MemorySaver + stubs")
    print("=" * 72)
    try:
        from typing import Annotated, TypedDict

        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from prismal.agents.subgraphs.engineering_orchestrator.builder import (
            ENGINEERING_AGENTS,
            _engineering_router,
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
                "messages": [AIMessage(content=f"[{name}] handled {task_id}", name=name)],
                "current_agent": name,
            }

        _node.__name__ = name
        return _node

    async def demo_supervisor(state: DemoState) -> dict[str, Any]:
        msgs = list(state.get("messages") or [])
        if (
            msgs
            and getattr(msgs[-1], "type", "") == "ai"
            and state.get("current_agent") in ENGINEERING_AGENTS
        ):
            return {"current_agent": "engineering_supervisor", "next_agent": None}
        human = next((m for m in reversed(msgs) if getattr(m, "type", "") == "human"), None)
        request = human.content if human else ""
        return {
            "current_agent": "engineering_supervisor",
            "next_agent": simulate_supervisor(request),
        }

    sg = StateGraph(DemoState)
    sg.add_node("engineering_supervisor", demo_supervisor)
    for leaf in ENGINEERING_AGENTS:
        sg.add_node(leaf, _make_leaf_node(leaf))
        sg.add_edge(leaf, "engineering_supervisor")
    sg.set_entry_point("engineering_supervisor")
    sg.add_conditional_edges("engineering_supervisor", _engineering_router)
    compiled = sg.compile(checkpointer=MemorySaver())

    for t in tasks[:4]:
        config = {"configurable": {"thread_id": t.id}}
        state = {
            "messages": [HumanMessage(content=t.request)],
            "metadata": {"task_id": t.id},
        }
        final = await compiled.ainvoke(state, config=config)
        last = final["messages"][-1]
        print(f"\n  {t.id} → {last.name}: {last.content}")


async def main() -> None:
    tasks = _load_tasks(limit=10)
    print(
        f"Loaded {len(tasks)} engineering tasks ({'dataset' if GH_ISSUES.exists() else 'fallback'})"
    )
    demo_simulation(tasks)
    await demo_real_langgraph(tasks)


if __name__ == "__main__":
    asyncio.run(main())
