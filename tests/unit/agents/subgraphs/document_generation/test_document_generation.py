"""Unit tests for document_generation subgraph.

Pipeline::

    planner → researcher → writer → editor → formatter

- planner: LLM produces a numbered outline of sections.
- researcher: gathers notes per section (LLM + optional RAG).
- writer: LLM composes a full draft from outline + notes.
- editor: LLM polishes the draft.
- formatter: applies the output format (markdown / plain / html).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.subgraphs.document_generation import (
    DocumentGenerationError,
    make_editor_node,
    make_formatter_node,
    make_planner_node,
    make_researcher_node,
    make_writer_node,
)
from prismal.agents.subgraphs.document_generation.builder import (
    build_document_generation_subgraph,
)
from prismal.core.exceptions import LightAgentError

# ── Helpers ───────────────────────────────────────────────────────────────────


def _state(topic: str = "Write an intro to LangGraph", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messages": [HumanMessage(content=topic)],
        "metadata": {},
    }
    base.update(overrides)
    return base


def _llm_returning(*texts: str) -> MagicMock:
    llm = MagicMock()
    responses = [MagicMock(content=t) for t in texts]
    llm.ainvoke = AsyncMock(side_effect=responses)
    return llm


# ── Exception / exports ──────────────────────────────────────────────────────


def test_document_generation_error_inherits_from_lightagent_error() -> None:
    assert issubclass(DocumentGenerationError, LightAgentError)


# ── planner_node ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_planner_node_parses_numbered_outline() -> None:
    llm = _llm_returning("1. Introduction\n2. Core concepts\n3. Example\n4. Conclusion")
    node = make_planner_node(llm=llm)
    update = await node(_state("Intro to LangGraph"))
    outline = update["metadata"]["document_generation"]["outline"]
    assert outline == ["Introduction", "Core concepts", "Example", "Conclusion"]


@pytest.mark.asyncio
async def test_planner_node_handles_empty_messages() -> None:
    llm = _llm_returning("1. X")
    node = make_planner_node(llm=llm)
    update = await node({"messages": [], "metadata": {}})
    assert update == {}
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_planner_node_tolerates_malformed_output() -> None:
    """If LLM returns no numbered lines, fall back to single-section outline."""
    llm = _llm_returning("Just a blob of prose without numbering.")
    node = make_planner_node(llm=llm)
    update = await node(_state("topic"))
    outline = update["metadata"]["document_generation"]["outline"]
    assert len(outline) >= 1


# ── researcher_node ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_researcher_node_gathers_notes_per_section() -> None:
    # One LLM call per section → 3 sections = 3 calls.
    llm = _llm_returning("notes for section 1", "notes for section 2", "notes for 3")
    node = make_researcher_node(llm=llm, rag_engine=None)
    state = _state("topic")
    state["metadata"]["document_generation"] = {"outline": ["S1", "S2", "S3"]}
    update = await node(state)
    research = update["metadata"]["document_generation"]["research"]
    assert set(research.keys()) == {"S1", "S2", "S3"}
    assert "notes for section 1" in research["S1"]


@pytest.mark.asyncio
async def test_researcher_node_uses_rag_when_available() -> None:
    """When rag_engine is provided, its search() is called per section."""
    rag = MagicMock()
    chunks = [MagicMock(content="RAG snippet", source="doc.md")]
    rag.search = MagicMock(return_value=chunks)
    llm = _llm_returning("llm notes")
    node = make_researcher_node(llm=llm, rag_engine=rag)
    state = _state()
    state["metadata"]["document_generation"] = {"outline": ["S1"]}
    update = await node(state)
    assert rag.search.called
    assert "S1" in update["metadata"]["document_generation"]["research"]


@pytest.mark.asyncio
async def test_researcher_node_no_outline_returns_empty_update() -> None:
    """Without an outline in state, researcher cannot do anything."""
    llm = _llm_returning("x")
    node = make_researcher_node(llm=llm, rag_engine=None)
    state = _state()
    state["metadata"]["document_generation"] = {}
    update = await node(state)
    assert update == {}


# ── writer_node ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_writer_node_composes_draft_from_outline_and_research() -> None:
    llm = _llm_returning("# Full draft document\n\nContent...")
    node = make_writer_node(llm=llm)
    state = _state("topic")
    state["metadata"]["document_generation"] = {
        "outline": ["S1", "S2"],
        "research": {"S1": "notes 1", "S2": "notes 2"},
    }
    update = await node(state)
    draft = update["metadata"]["document_generation"]["draft"]
    assert "Full draft" in draft


@pytest.mark.asyncio
async def test_writer_prompt_includes_outline_and_notes() -> None:
    llm = _llm_returning("draft text")
    node = make_writer_node(llm=llm)
    state = _state("topic")
    state["metadata"]["document_generation"] = {
        "outline": ["OutlineItemA", "OutlineItemB"],
        "research": {"OutlineItemA": "NoteA", "OutlineItemB": "NoteB"},
    }
    await node(state)
    call_args = llm.ainvoke.call_args.args[0]
    user_content = call_args[1].content
    assert "OutlineItemA" in user_content
    assert "NoteA" in user_content


# ── editor_node ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_editor_node_polishes_draft() -> None:
    llm = _llm_returning("Polished version of the draft.")
    node = make_editor_node(llm=llm)
    state = _state()
    state["metadata"]["document_generation"] = {"draft": "rough draft"}
    update = await node(state)
    edited = update["metadata"]["document_generation"]["edited"]
    assert "Polished" in edited


@pytest.mark.asyncio
async def test_editor_node_no_draft_returns_empty() -> None:
    llm = _llm_returning("x")
    node = make_editor_node(llm=llm)
    state = _state()
    state["metadata"]["document_generation"] = {}
    update = await node(state)
    assert update == {}


# ── formatter_node ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_formatter_node_markdown_default() -> None:
    node = make_formatter_node(format="markdown")
    state = _state()
    state["metadata"]["document_generation"] = {"edited": "Final content."}
    update = await node(state)
    final = update["metadata"]["document_generation"]["final"]
    assert "Final content." in final


@pytest.mark.asyncio
async def test_formatter_appends_ai_message_with_final_document() -> None:
    node = make_formatter_node(format="markdown")
    state = _state()
    state["metadata"]["document_generation"] = {"edited": "Document body"}
    update = await node(state)
    assert "messages" in update
    assert isinstance(update["messages"][0], AIMessage)
    assert "Document body" in update["messages"][0].content


@pytest.mark.asyncio
async def test_formatter_falls_back_to_draft_when_no_edited() -> None:
    """If editor step skipped, format the raw draft."""
    node = make_formatter_node(format="markdown")
    state = _state()
    state["metadata"]["document_generation"] = {"draft": "uneditado"}
    update = await node(state)
    assert "uneditado" in update["metadata"]["document_generation"]["final"]


@pytest.mark.asyncio
async def test_formatter_plain_strips_markdown_headings() -> None:
    node = make_formatter_node(format="plain")
    state = _state()
    state["metadata"]["document_generation"] = {"edited": "# Title\n\nBody text."}
    update = await node(state)
    final = update["metadata"]["document_generation"]["final"]
    assert "# " not in final
    assert "Title" in final
    assert "Body text." in final


@pytest.mark.asyncio
async def test_formatter_html_replaces_newlines_with_br() -> None:
    node = make_formatter_node(format="html")
    state = _state()
    state["metadata"]["document_generation"] = {"edited": "line 1\nline 2"}
    update = await node(state)
    final = update["metadata"]["document_generation"]["final"]
    assert "<br>" in final


@pytest.mark.asyncio
async def test_formatter_returns_empty_when_no_body() -> None:
    node = make_formatter_node(format="markdown")
    state = _state()
    state["metadata"]["document_generation"] = {}
    update = await node(state)
    assert update == {}


@pytest.mark.asyncio
async def test_formatter_rejects_invalid_format() -> None:
    with pytest.raises(ValueError):
        make_formatter_node(format="nonexistent-format")


# ── builder ──────────────────────────────────────────────────────────────────


def test_build_document_generation_subgraph_has_five_nodes() -> None:
    defn = build_document_generation_subgraph(
        llm=_llm_returning("x"),
        rag_engine=None,
    )
    assert set(defn.nodes.keys()) == {
        "planner",
        "researcher",
        "writer",
        "editor",
        "formatter",
    }


def test_build_document_generation_subgraph_entry_point_is_planner() -> None:
    defn = build_document_generation_subgraph(llm=_llm_returning("x"))
    assert defn.entry_point == "planner"


def test_build_document_generation_subgraph_linear_edges() -> None:
    defn = build_document_generation_subgraph(llm=_llm_returning("x"))
    edges = set(defn.edges)
    expected = {
        ("planner", "researcher"),
        ("researcher", "writer"),
        ("writer", "editor"),
        ("editor", "formatter"),
    }
    assert expected.issubset(edges)
