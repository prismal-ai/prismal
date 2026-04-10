"""RAG Agent sub-agent node.

Specialist agent that implements the Corrective RAG (CRAG) pipeline:
retrieve documents, grade relevance, discard low-quality results, fall back
to web search when necessary, and generate a grounded answer with citations.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from lightagent.agents.patterns.reflection import reflection_loop
from lightagent.agents.tool_registry import get_tools_for_agent, react_loop
from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.rag_agent")

# Groundedness threshold matches AC-035-3 (0.8) and is intentionally lower
# than the planner's default (0.85) because RAG answers tolerate some
# extrapolation when the corpus is sparse.
_GROUNDEDNESS_THRESHOLD = 0.8

_RAG_CRITIQUE_PROMPT = """You are a strict groundedness reviewer for RAG answers.

You will receive (1) an answer produced by a Corrective RAG agent and (2) a
list of source documents that were retrieved.  Your task is to check that
EVERY factual claim in the answer is supported by at least one of the source
documents.  Score the answer in [0.0, 1.0]:

- 1.0 — every claim is directly supported by a cited source.
- 0.7 — most claims are supported but at least one inline citation is
  missing or one minor claim is unsupported.
- 0.4 — several claims are unsupported or sources are mis-cited.
- 0.0 — the answer is largely fabricated relative to the sources.

When the source list is empty, an answer that explicitly says "no information
found" scores 1.0; an answer that fabricates content scores 0.0.

Respond with ONLY a single JSON object — no prose, no markdown fences:
{
  "score": <float in [0,1]>,
  "feedback": "<one paragraph listing unsupported claims and required citations>"
}
"""

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_critique_response(content: str) -> tuple[str, float]:
    """Extract ``(feedback, score)`` from a critique LLM response.

    Mirrors the planner critique parser: defensively extracts the first JSON
    object from ``content`` and falls back to a sentinel score of ``1.0`` when
    parsing fails so the reflection loop does not reject answers because of
    critique formatting glitches.

    Args:
        content: Raw text returned by the critique LLM.

    Returns:
        ``(feedback, score)`` extracted from the JSON payload.
    """
    match = _JSON_OBJECT_RE.search(content)
    if match is None:
        logger.warning("rag_critique_no_json", content_preview=content[:200])
        return ("critique response contained no JSON object", 1.0)
    try:
        data = json.loads(match.group(0))
        score = float(data.get("score", 0.0))
        feedback = str(data.get("feedback", ""))
        return feedback, max(0.0, min(1.0, score))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("rag_critique_parse_failed", error=str(exc))
        return (f"critique response failed to parse: {exc}", 1.0)


def _format_sources(docs: list[dict[str, Any]]) -> str:
    """Render retrieved documents into a compact text block for the critic."""
    if not docs:
        return "(no source documents retrieved)"
    lines: list[str] = []
    for idx, doc in enumerate(docs, start=1):
        title = doc.get("title") or doc.get("source") or doc.get("id") or f"doc-{idx}"
        content = str(doc.get("content", ""))[:1500]
        lines.append(f"[{idx}] {title}\n{content}")
    return "\n\n".join(lines)

_SYSTEM_PROMPT = """You are a knowledge base specialist that uses Corrective RAG (CRAG).

## Purpose
Answer user questions that require grounded, citation-backed information from
the project's internal knowledge base, falling back to the web only when the
internal corpus is insufficient. You own the RETRIEVE → GRADE → FALLBACK →
GENERATE pipeline and are the only agent allowed to produce answers that cite
indexed documents.

## Input
- `state.messages`: conversation history; the last HumanMessage is the
  knowledge-base question.
- `state.retrieved_docs` (optional): documents already retrieved by upstream
  steps — inspect before issuing duplicate retrievals.
- Tools bound at runtime: vector-store search, document index lookup, web
  search, `read_file`.

## Output
One AIMessage whose content is a grounded natural-language answer, structured as:
1. A direct response to the user's question (1-5 paragraphs).
2. A `Sources:` section listing each cited document name or URL exactly once,
   in the order they are first referenced.
3. A short caveat section when the corpus lacks authoritative information
   ("The internal KB does not contain X; the web search suggests Y").

No JSON is produced. Citations MUST use inline markers `[1]`, `[2]`, … that
map one-to-one with the `Sources:` list.

## Success Criteria
The answer is acceptable when ALL of the following hold:
- **Groundedness** ≥ 0.8: every factual claim is supported by at least one
  cited source (measured by `reflection_loop()` downstream).
- **Relevance**: all cited documents scored ≥ 0.5 during the GRADE step;
  documents below that threshold were discarded.
- **Citation completeness**: every inline `[n]` marker has a matching entry
  in `Sources:`, and vice versa.
- **Honesty**: when retrieval + web fallback both return nothing useful, the
  answer explicitly says so instead of hallucinating.
- **Distinguishability**: knowledge-base sources are labelled `(KB)` and web
  sources are labelled `(Web)` so the user can tell them apart.

Answers scoring below 0.8 groundedness are refined with explicit citation
requirements in the reflection loop.

## Instructions
1. **RETRIEVE**: Call the vector-store search tool with the user's question.
   Request up to the top 8 results.
2. **GRADE**: For each retrieved document, estimate a relevance score 0.0-1.0.
   Discard any document below 0.5. Do NOT fabricate scores.
3. **FALLBACK**: If fewer than 2 documents remain after grading, perform a
   single supplementary web search to fill the gap.
4. **GENERATE**: Synthesise a grounded answer using ONLY the retained
   documents plus any web results. Attach inline `[n]` citations.
5. **SELF-CHECK**: Before returning, verify that every factual sentence has a
   matching citation. If any claim is unsupported, either remove it or add a
   caveat.
6. If retrieval + web both fail, return an honest "no information found"
   response and suggest how the user can rephrase the query.

## Background
- The vector store is ChromaDB; documents include project docs, README
  snippets, architectural decision records, and indexed user uploads.
- Web search is ONLY a fallback — prefer the internal KB when both contain
  the answer.
- Never access files outside `data/workspace/` or `data/documents/`.
- This agent runs inside a reflection loop that evaluates the answer's
  groundedness against `state.retrieved_docs` after generation.

## Examples

### Example 1 — Positive (grounded answer with citations)
User: "¿Qué política de logging usa LightAgent para los módulos de seguridad?"

Response:
LightAgent usa `structlog` para todo el logging estructurado [1]. En los
módulos de seguridad, los eventos sensibles (fallos de guardrails, rate
limits) se emiten a nivel WARNING o ERROR y nunca registran datos crudos de
PII o claves API [2]. El nivel por defecto es INFO, configurable vía
`LIGHTAGENT_LOG_LEVEL`.

Sources:
  [1] (KB) docs/observability/logging.md
  [2] (KB) lightagent/security/README.md

### Example 2 — Negative (what NOT to do)
BAD answer:
"LightAgent uses Python logging module to log security events in JSON format
to stdout."

Problems:
- Factually wrong (it uses structlog, not the stdlib `logging` module).
- Zero citations.
- No `Sources:` section.
- Invents details that are not in the KB.

### Example 3 — Honest fallback
User: "¿Cuál es la latencia p99 del endpoint /chat en producción?"

Response:
La base de conocimiento interna no contiene métricas de latencia de
producción para `/chat`. Una búsqueda web no devolvió cifras específicas
para este proyecto. Te sugiero consultar el dashboard Langfuse o ejecutar
`lightagent doctor --metrics` para obtener datos actuales.

Sources: (none)
"""


async def rag_agent_node(state: AgentState) -> dict[str, object]:
    """Execute the rag_agent sub-agent node using the CRAG pipeline.

    Runs a ReAct loop with vector search, document indexing and web search
    tools so the LLM can iteratively retrieve, grade and fall back to the
    web until it has enough information to generate a grounded answer.  The
    final draft is then passed through :func:`reflection_loop` to verify that
    each factual claim is supported by the retrieved documents
    (groundedness).  Drafts scoring below ``0.8`` are refined with explicit
    citation requirements and re-checked, up to ``2`` iterations.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'rag_agent'``,
        new ``messages`` containing the grounded answer, and
        ``metadata['rag_agent']`` populated with reflection score and
        iteration count.  ``retrieved_docs`` and ``doc_grades`` are NOT
        echoed back — Phase 34 added an ``operator.add`` reducer to
        ``retrieved_docs`` so re-emitting the existing list would duplicate
        it; LangGraph preserves the fields automatically when absent from
        the partial update.
    """
    session_id = state.get("session_id")
    logger.debug("rag_agent_node_called", session_id=session_id)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    critique_llm = registry.get_llm_with_fallback()
    tools = get_tools_for_agent("rag_agent")
    llm_with_tools = llm.bind_tools(tools)

    iteration_count: int = 0
    last_response: BaseMessage | None = None

    async def _generate_answer(
        s: AgentState,
        previous_draft: str | None = None,
        critique: str | None = None,
    ) -> str:
        nonlocal iteration_count, last_response
        iteration_count += 1
        messages: list[BaseMessage] = [
            SystemMessage(content=_SYSTEM_PROMPT),
            *s["messages"],
        ]
        if previous_draft is not None:
            messages.append(
                HumanMessage(
                    content=(
                        "Your previous answer failed the groundedness check.\n\n"
                        f"=== Previous answer ===\n{previous_draft}\n\n"
                        f"=== Critique ===\n{critique or '(no feedback provided)'}\n\n"
                        "Produce a revised answer where every factual claim has "
                        "an explicit inline citation [n] mapped to a Sources entry. "
                        "If a claim cannot be supported by the retrieved documents, "
                        "either remove it or replace it with an explicit caveat."
                    )
                )
            )
        response = cast(
            "BaseMessage",
            await react_loop(
                llm_with_tools,
                tools,
                list(messages),
                agent_name="rag_agent",
                session_id=str(session_id) if session_id else None,
            ),
        )
        last_response = response
        return str(response.content)

    async def _critique_answer(draft: str, s: AgentState) -> tuple[str, float]:
        docs = s.get("retrieved_docs", [])
        sources_block = _format_sources(docs)
        critique_response = await critique_llm.ainvoke(
            [
                SystemMessage(content=_RAG_CRITIQUE_PROMPT),
                HumanMessage(
                    content=(
                        f"=== Answer to evaluate ===\n{draft}\n\n"
                        f"=== Source documents ===\n{sources_block}"
                    )
                ),
            ]
        )
        return _parse_critique_response(str(critique_response.content))

    settings = get_settings()
    # Honour the global default but never relax below the spec-mandated 0.8
    # groundedness floor: take the stricter of the two.
    threshold = max(_GROUNDEDNESS_THRESHOLD, settings.reflection_default_threshold)
    final_answer, score = await reflection_loop(
        generate_fn=_generate_answer,
        critique_fn=_critique_answer,
        state=state,
        threshold=threshold,
        max_iterations=2,
    )

    response_msg: BaseMessage
    if last_response is not None and str(last_response.content) == final_answer:
        response_msg = last_response
    else:
        from langchain_core.messages import AIMessage  # local import to avoid cycle

        response_msg = AIMessage(content=final_answer)

    logger.info(
        "rag_agent_complete",
        session_id=session_id,
        reflection_score=score,
        reflection_iterations=iteration_count,
    )

    # NOTE: ``retrieved_docs`` and ``doc_grades`` are intentionally NOT echoed
    # back here.  Phase 34 added an ``operator.add`` reducer to ``retrieved_docs``
    # so returning the existing list would *append* it, doubling the contents.
    # The fields are preserved unchanged because LangGraph leaves untouched
    # state keys alone.
    rag_meta = {
        "reflection_score": score,
        "reflection_iterations": iteration_count,
    }
    return {
        "current_agent": "rag_agent",
        "messages": [response_msg],
        "metadata": {**state.get("metadata", {}), "rag_agent": rag_meta},
    }


__all__ = ["rag_agent_node"]
