"""
Supervisor agent node for LangGraph multi-agent orchestration.

The supervisor is the entry point and coordinator of the LangGraph graph.
It analyses the conversation history, decides which specialist sub-agent
should handle the next step (or whether the task is complete), and returns
an updated state dict for LangGraph to act on.

Routing is performed by calling the LLM with a structured system prompt that
asks it to respond with exactly one of the known agent names or the literal
string ``"END"``.

Example::

    from prismal.agents.supervisor import supervisor_node, supervisor_router
    from prismal.agents.state import create_initial_state

    state = create_initial_state(session_id="sess-demo")
    updated = await supervisor_node(state)
    next_node = supervisor_router(updated)
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.agents.intent_router import match_intent
from prismal.core.config import get_settings
from prismal.core.logging import get_logger
from prismal.memory.long_term_store import (
    LongTermMemoryStore,
    preferences_namespace,
)
from prismal.memory.profile import ProfileManager
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.messages import BaseMessage

    from prismal.agents.state import AgentState

logger = get_logger("prismal.agents.supervisor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMBERS: list[str] = [
    "researcher",
    "coder",
    "codeact",
    "cua",
    "rag_agent",
    "planner",
    "critic",
    "data_analyst",
    "file_manager",
    "skill_manager",
    "cron_manager",
    # Phase 24: dynamic subgraph (PO → Architect → Developer → Tests → QA → Review)
    "dev_pipeline",
    # Phase 26: ML/DL pipeline (Ingester -> EDA -> Features -> Train -> Evaluate)
    "ml_pipeline",
    # Phase 27: Financial Analysis pipeline (read-only)
    "financial_analyst",
    # Phase 34: map-reduce parallel research dispatcher.
    "parallel_researcher",
]

_VALID_ROUTES: frozenset[str] = frozenset(MEMBERS) | {"END"}

# Phase D (D1-01/02): advanced-architecture nodes — 6 reasoning patterns and
# 5 domain subgraphs — wired into the supervisor opt-in via
# ``settings.enable_subgraphs``. They are appended to the valid routes and the
# system prompt ONLY when the flag is on, so the default behaviour of the base
# agents is byte-for-byte unchanged.
ADVANCED_MEMBERS: list[str] = [
    # Reasoning patterns (prismal/agents/patterns/nodes.py).
    "tot_agent",
    "debate_agent",
    "constitutional_filter",
    "lats_agent",
    "llm_compiler",
    "mixture_agent",
    # Domain subgraph pipelines (prismal/agents/subgraphs/).
    "customer_service",
    "document_generation",
    "data_etl",
    "code_review",
    "debate_consensus",
]


# Fase F (P3): multimodal pipeline route — a single member that fans out to the
# vision/audio/video modal agents internally. Appended to the valid routes and
# the system prompt ONLY when ``settings.multimodal_enabled`` is on, so the
# default behaviour is byte-for-byte unchanged.
MULTIMODAL_MEMBERS: list[str] = [
    "multimodal_pipeline",
]


# Fase K (K7-03): Kokoro deliberation route — a single member running the
# load_souls → deliberate → judge → act → output subgraph. Appended to the
# valid routes and the system prompt ONLY when ``settings.kokoro_enabled`` is
# on, so the default behaviour is byte-for-byte unchanged.
KOKORO_MEMBERS: list[str] = [
    "kokoro",
]


# Fase S (S5-03): Skynet swarm route — a single member running the
# plan → Send fan-out → reduce → evaluate → output subgraph. Appended to the
# valid routes and the system prompt ONLY when ``settings.skynet_enabled`` is
# on, so the default behaviour is byte-for-byte unchanged.
SKYNET_MEMBERS: list[str] = [
    "skynet",
]


def effective_valid_routes(
    enable_advanced: bool,
    enable_multimodal: bool = False,
    enable_kokoro: bool = False,
    enable_skynet: bool = False,
) -> frozenset[str]:
    """Return the set of routes the supervisor may select.

    Always includes the base :data:`MEMBERS` plus ``END``; includes
    :data:`ADVANCED_MEMBERS` when *enable_advanced* is ``True``,
    :data:`MULTIMODAL_MEMBERS` when *enable_multimodal* is ``True``,
    :data:`KOKORO_MEMBERS` when *enable_kokoro* is ``True``, and
    :data:`SKYNET_MEMBERS` when *enable_skynet* is ``True``.
    """
    routes = _VALID_ROUTES
    if enable_advanced:
        routes = routes | frozenset(ADVANCED_MEMBERS)
    if enable_multimodal:
        routes = routes | frozenset(MULTIMODAL_MEMBERS)
    if enable_kokoro:
        routes = routes | frozenset(KOKORO_MEMBERS)
    if enable_skynet:
        routes = routes | frozenset(SKYNET_MEMBERS)
    return routes


# Maximum number of recent messages sent to the LLM per supervisor call.
# Older messages are kept in LangGraph state but not re-sent to the provider,
# preventing token explosion in long sessions and avoiding rate-limit errors.
_HISTORY_WINDOW: int = 6

_ANSWER_SYSTEM_PROMPT: str = (
    "You are a helpful, knowledgeable AI assistant. "
    "Answer the user's question clearly and concisely."
)

_SYSTEM_PROMPT: str = """You are a supervisor coordinating a team of AI agents.
Your role is to:
1. Analyze the user's request and the conversation history
2. Route to the most appropriate specialist agent
3. Aggregate results from specialists into a coherent response
4. Return a FINAL ANSWER when the task is complete (route to END)

Available agents:
- researcher: Web search, RAG queries, reading files
- coder: Writing and executing code, reading/writing code files.
  Route here for SIMPLE, narrow code tasks: "fix this line", "explain this
  function", "add a docstring", "rename a variable", "write a small helper",
  code review, quick snippets without a full iteration loop.
- codeact: Executes Python code DIRECTLY in an isolated sandbox with an
  auto-correction loop (generate → run → read stderr → fix). Uses ~30%
  fewer tokens than 'coder' on multi-step coding tasks.
  Route here for COMPLEX multi-step coding: "refactor this module",
  "implement full X", "write and test Y", "complete implementation",
  "build a script that does A, B and C", anything that benefits from
  iterative self-correction against real sandbox output.
  Prefer 'coder' when the task is a single surgical edit or a pure
  explanation; prefer 'codeact' when the task requires running code,
  handling errors, and iterating.
- cua: Computer Use Agent — drives a real browser via Playwright using a
  vision-capable LLM to perceive screenshots and execute UI actions
  (click, type, scroll, navigate). High-risk actions (submit, pay,
  delete, send) trigger a mandatory human approval gate.
  Route here when the task explicitly involves INTERACTIVE UI AUTOMATION
  that cannot be done via API or the text-based researcher tools:
  "click on the button", "fill out this form and submit", "navigate to
  X and do Y in the interface", "automate the UI", "RPA", "drive the
  web app", "interact with the canvas", "select from the dropdown".
  DO NOT route here for simple web scraping (use researcher instead).
  DO NOT route here when a direct API call is available.
- rag_agent: Internal document knowledge base Q&A
- planner: Decompose complex multi-step tasks AND create software specifications
  using Spec-Driven Design (SDD). Route here when the user asks to:
  · Plan a new feature, API, service, or architecture before coding
  · Write a PRD, API Spec, Technical Design, Data Model, or Implementation Plan
  · Validate coherence between existing specs
  · Any request mentioning: "PRD", "spec", "especificación", "diseño técnico",
    "arquitectura", "plan de implementación", "modelo de datos", "diseño de API",
    "planificar antes de codificar", "spec-driven", "SDD"
- critic: Review and improve outputs
- data_analyst: SQL queries (DuckDB), DataFrame transforms, charts
- file_manager: File read/write operations
- skill_manager: Install, activate, deactivate, list, or create **skills** ONLY.
  Skills are Python modules in ``skills/available/`` / ``skills/active/``.
  Do NOT route here for MCP server queries — MCP is a separate system.
  Also downloads skills from GitHub repositories (e.g. anthropics/skills).
  Route here when the user mentions:
  · skills, activar skill, instalar skill, desactivar skill, crear skill,
    listar skills, "add skill", "enable skill", "disable skill"
  · "install skill from <path>", "agrega skill desde /ruta"
  · "instalar desde github", "install from github", "download from github"
  · any GitHub URL (github.com/...) paired with install/add/download verbs
  · owner/repo slugs like "anthropics/skills" with install intent
  · "skill-creator de anthropics", "instala el skill-creator"
  · zip archives or folders with skill.md + scripts/ + references/ layout
  · "instala skill desde /ruta/mi_skill.zip", "install from /path/skill.zip"

  SKILL PACKAGE FORMAT (zip or folder — no skill.py required):
  A skill package can be a .zip archive or a plain directory with this layout:
    skill.md          ← YAML frontmatter (name, description, version, author,
                         tags, safe_to_auto_activate, requires_permissions)
                         followed by free-form documentation for the agent.
    scripts/          ← Python files; each @tool-decorated function becomes
    │  └─ my_tool.py    a LangChain tool exposed to the agent.
    references/       ← Optional reference documents (txt, md, pdf, …) the
       └─ doc.md        agent can load for context.

  The YAML frontmatter inside skill.md must be fenced with --- delimiters:
    ---
    name: my_skill
    description: One-line description
    version: 1.0.0
    author: your-name
    tags: [utility]
    safe_to_auto_activate: false
    requires_permissions: []
    ---

  A skill.py wrapper is generated automatically — users never write it.
  After installation the agent will confirm the skill name so the user can
  request activation: "activa el skill <name>".
- cron_manager: Schedule recurring tasks, list/pause/resume/remove cron jobs.
  CRITICAL: route here for ANY query about cron jobs, scheduled jobs, or
  recurring tasks — whether listing them, checking which are active, pausing,
  resuming, removing, or creating new ones. The cron_manager reads the live
  scheduler state via dedicated tools, so its answers are always accurate
  and up-to-date. NEVER answer cron questions from memory, and NEVER route
  cron questions to 'researcher' just because they start with "lista" or
  "list".
  Examples (Spanish):
    · "lista los crons activos"
    · "lista mis crons"
    · "qué crons tengo activos"
    · "pausa el cron de reporte diario"
    · "borra el job de las 8"
    · "programa una tarea cada hora"
    · "agenda un recordatorio diario a las 9"
    · "cada día a las 9 envíame el reporte"
  Examples (English):
    · "list scheduled jobs"
    · "list my cron jobs"
    · "pause cron daily-report"
    · "remove cron daily-report"
    · "schedule a daily summary at 8am"
    · "every day at noon remind me to drink water"
    · "set up a recurring reminder"
  Keywords: "cron", "crons", "cronjob", "scheduled", "schedule", "programar",
  "agendar", "recurring", "periodic", "reminder", "recordatorio", "every day",
  "cada hora", "daily", "weekly", "diariamente".
- dev_pipeline: for software development tasks requiring a full pipeline
  (PO → Architect → Developer → Tests → QA → Review).
  Route here when the user asks to build a complete software feature or product
  end-to-end with specification, implementation, testing and quality review.
- ml_pipeline: For machine learning and data science tasks — training ML models,
  AutoML, classification, regression, clustering, deep learning, model evaluation.
  Route here when the user asks to:
  · Train, build, or evaluate a machine learning model
  · Run AutoML or hyperparameter search
  · Perform EDA (exploratory data analysis) followed by modeling
  · Engineer features and prepare datasets for ML
  · Export or deploy a trained model
- financial_analyst: For financial analysis and market research — equity analysis,
  crypto analysis, technical indicators, fundamental valuation, risk assessment,
  financial reports.
  Route here when the user asks to:
  · Analyze a stock, crypto asset, or forex pair
  · Get technical analysis (RSI, MACD, Bollinger Bands, signals, trend)
  · Get fundamental analysis (P/E ratio, revenue growth, earnings, valuation)
  · Assess risk (volatility, Sharpe ratio, VaR, drawdown, correlation)
  · Generate a financial analysis report for a ticker
  · Analyze market sentiment for a ticker or asset
  · Keywords: "analiza", "análisis financiero", "stock analysis", "crypto analysis",
    "technical analysis", "fundamental", "RSI", "MACD", "P/E ratio", "volatility",
    "Sharpe", "VaR", "financial report", "market sentiment"
  IMPORTANT: financial_analyst is READ-ONLY — it never executes trades.
  Every output includes a mandatory legal disclaimer.
- END: Return final answer to the user

Routing rules:
- Route to 'END' when you have a complete answer
- Route to 'END' for greetings ("hola", "hello", "hi", "hey", "buenos días",
  "buenas tardes", "buenas noches", "qué tal", "cómo estás", etc.) and all
  casual / small-talk conversation — answer them directly without specialists.
- Route to 'END' for simple factual questions you can answer directly
- Route to 'researcher' for ANY query about MCP servers — whether listing them,
  checking which are active/enabled, asking about their capabilities, or getting
  details about a specific server. The researcher reads the live
  config/mcp_servers.yaml file and has all MCP tools bound, so its answers are
  always accurate and up-to-date. NEVER answer MCP questions from memory.
  Examples: "lista los MCPs", "qué MCPs tengo activos", "qué hace el MCP X",
  "está habilitado playwright", "list tools of X server".
- Route to 'END' only for MCP *configuration help*: explaining how to add,
  remove, or edit entries in mcp_servers.yaml (procedural knowledge, not data).
- Route to 'skill_manager' ONLY for requests about Python skills: installing
  (local or remote), activating, deactivating, listing, or creating skills
- CRITICAL: If the most recent message in the conversation is from a specialist
  agent (skill_manager, coder, researcher, etc.), route to 'END' IMMEDIATELY.
  Never route to the same agent twice in a row for the same user request.
  A specialist response — whether success or error — is always the final answer.

Respond with ONLY the agent name (e.g. "researcher" or "END"). No explanation."""

# Phase D (D1-02): advanced-architecture routing options. Appended to the
# system prompt only when ``settings.enable_subgraphs`` is True so the default
# routing prompt stays identical to the legacy behaviour.
_ADVANCED_PROMPT_SECTION: str = """

Additional advanced agents (opt-in — route here only when clearly applicable):
- tot_agent: Tree-of-Thoughts reasoning. Explores multiple solution branches
  with scoring. Route here for hard reasoning/planning puzzles that benefit
  from exploring alternatives ("think step by step over several options").
- debate_agent: Multi-agent debate that synthesises a consensus. Route here
  for contested/subjective questions where weighing opposing views helps
  ("argue both sides", "is X better than Y", "pros and cons debate").
- constitutional_filter: Revises the latest answer against safety/accuracy
  principles. Route here to sanitise or fact-check a drafted answer.
- lats_agent: Language-Agent Tree Search (MCTS) for multi-step goal-seeking
  with backtracking. Route here for sequential decision tasks.
- llm_compiler: Decomposes a goal into a DAG of subtasks executed in parallel.
  Route here for tasks naturally split into independent parallel subtasks.
- mixture_agent: Mixture-of-Agents — multiple proposers + synthesis. Route here
  when a higher-quality synthesised answer across models is worth the cost.
- customer_service: Support pipeline (classify → FAQ → escalate → reply/ticket).
- document_generation: Long-form doc pipeline (plan → research → write → edit).
- data_etl: ETL pipeline (extract → validate → transform → load → audit).
- code_review: Code review pipeline (lint → security scan → logic review → report).
- debate_consensus: Structured proponent/opponent/moderator consensus pipeline."""


# Appended only when ``settings.multimodal_enabled`` is True.
_MULTIMODAL_PROMPT_SECTION: str = """

Multimodal pipeline (opt-in — route here only when the request involves an
image, audio, or video attachment, or asks to transcribe/describe/summarise
such media):
- multimodal_pipeline: Routes the input by modality to the vision / audio /
  video agents, fuses their outputs, and returns a single answer. Route here
  for "transcribe this", "what's in this image", "summarise this video"."""


# Appended only when ``settings.kokoro_enabled`` is True.
_KOKORO_PROMPT_SECTION: str = """

Kokoro deliberation (opt-in — route here only when the user explicitly asks
for a multi-perspective deliberation or a panel-style decision):
- kokoro: Three persona souls (spirit/values, mind/logic, heart/empathy)
  deliberate toward agreement and a single judge renders the final, accountable
  decision. Route here for "deliberate on this", "weigh the perspectives",
  "have the panel decide"."""


# Appended only when ``settings.skynet_enabled`` is True.
_SKYNET_PROMPT_SECTION: str = """

Skynet swarm (opt-in — route here only when the request decomposes into many
independent sub-tasks that should run in parallel):
- skynet: Decomposes the order into sub-orders, dispatches a bounded swarm of
  parallel workers, and reduces their outputs into one answer. Route here for
  "research these N things in parallel", "fan this out", "run a swarm over
  these items", "split this across agents"."""


def build_system_prompt(
    enable_advanced: bool,
    enable_multimodal: bool = False,
    enable_kokoro: bool = False,
    enable_skynet: bool = False,
) -> str:
    """Return the supervisor routing prompt.

    When *enable_advanced* is ``True`` the advanced-architecture agents
    (:data:`ADVANCED_MEMBERS`) are appended; when *enable_multimodal* is ``True``
    the multimodal pipeline section is appended; when *enable_kokoro* is ``True``
    the Kokoro deliberation section is appended; when *enable_skynet* is ``True``
    the Skynet swarm section is appended. With all flags ``False`` the prompt is
    byte-identical to the legacy default so base-agent routing is unchanged.
    """
    prompt = _SYSTEM_PROMPT
    if enable_advanced:
        prompt += _ADVANCED_PROMPT_SECTION
    if enable_multimodal:
        prompt += _MULTIMODAL_PROMPT_SECTION
    if enable_kokoro:
        prompt += _KOKORO_PROMPT_SECTION
    if enable_skynet:
        prompt += _SKYNET_PROMPT_SECTION
    return prompt


# ---------------------------------------------------------------------------
# Long-term memory helpers (SPEC-039 AC-039-3 / AC-039-4)
# ---------------------------------------------------------------------------

# Module-level store so recall + extraction share the same backend
# instance across supervisor invocations in a given process. In tests
# this can be replaced by monkey-patching ``_memory_store_singleton`` or
# by patching :func:`_get_memory_store`.
_memory_store_singleton: LongTermMemoryStore | None = None

# Keep strong references to in-flight extraction tasks so they are not
# garbage-collected mid-run (ruff RUF006).
_memory_extraction_tasks: set[asyncio.Task[None]] = set()

_MEMORY_EXTRACTION_PROMPT: str = (
    "You are extracting durable user preferences for long-term memory. "
    "Review the conversation and emit up to 5 SHORT facts or preferences "
    "that will still be useful in FUTURE sessions (language, tone, "
    "recurring goals, domain interests). Exclude greetings, ephemeral "
    "chat, and details specific to the current task. Respond with one "
    "fact per line — no numbering, no explanations. If there are no "
    "durable facts, respond with an empty message."
)


def _get_memory_store() -> LongTermMemoryStore:
    """Return the process-wide long-term memory store singleton."""
    global _memory_store_singleton
    if _memory_store_singleton is None:
        _memory_store_singleton = LongTermMemoryStore()
    return _memory_store_singleton


def _derive_user_id(state: AgentState) -> str:
    """Extract a stable user identifier from ``state["session_id"]``.

    Prismal session IDs follow the shape ``{user}-{timestamp}``; this
    helper returns the leading segment so long-term memory is keyed per
    user rather than per session. Falls back to ``"unknown"`` when no
    session ID is present.
    """
    session_id = str(state.get("session_id", "") or "")
    head = session_id.split("-", 1)[0]
    return head or "unknown"


async def _recall_memory_context(state: AgentState) -> str:
    """Recall user preferences and format them as a system-prompt suffix.

    Returns an empty string (not an exception) when recall is disabled,
    when the store is unavailable, or when no facts are stored yet — the
    supervisor continues without memory context per SPEC-039 AC-039-4.

    The query used for semantic ranking is the most recent human message
    in the conversation, or an empty query when no such message exists.
    """
    settings = get_settings()
    if settings.memory_recall_limit <= 0:
        return ""

    query = ""
    for msg in reversed(state.get("messages", [])):
        if getattr(msg, "type", "") == "human":
            query = str(getattr(msg, "content", ""))
            break

    try:
        store = _get_memory_store()
        facts = await store.recall_facts(
            preferences_namespace(_derive_user_id(state)),
            query=query,
            limit=settings.memory_recall_limit,
        )
    except Exception as exc:
        # Defensive: LongTermMemoryStore already swallows errors in
        # recall_facts, but the factory itself may raise if a misconfig
        # prevents instantiation. Graceful degradation always wins.
        logger.debug("memory_recall_skipped", error=str(exc))
        return ""

    if not facts:
        return ""

    bullets = "\n".join(f"- {fact['value']}" for fact in facts if fact.get("value"))
    if not bullets:
        return ""
    return (
        "\n\n## Known User Preferences (from prior sessions)\n"
        f"{bullets}\n"
        "Use these preferences to tailor your routing decision when "
        "relevant, but do not mention them unless the user asks."
    )


async def _extract_and_store_memory(state: AgentState) -> None:
    """Extract up to 5 durable facts from the session and persist them.

    Called as a fire-and-forget task at session end (supervisor routes
    to END). All exceptions are caught and logged at WARNING level so a
    memory-extraction failure never impacts the user-facing response.
    The underlying :meth:`LongTermMemoryStore.store_fact` enforces PII
    sanitization — this function intentionally does NOT log the raw fact
    values anywhere.
    """
    try:
        settings = get_settings()
        if not settings.memory_extraction_enabled:
            return

        messages = state.get("messages", [])
        if not messages:
            return

        # Only look at the tail of the conversation: older context is
        # already covered by prior extraction runs.
        recent = messages[-10:]

        llm = ProviderRegistry().get_llm_with_fallback()
        response = await llm.ainvoke([SystemMessage(content=_MEMORY_EXTRACTION_PROMPT), *recent])

        raw = str(getattr(response, "content", "")).strip()
        if not raw:
            return

        candidate_lines = [line.lstrip("-*0123456789. ").strip() for line in raw.splitlines()]
        facts = [line for line in candidate_lines if line][:5]
        if not facts:
            return

        user_id = _derive_user_id(state)
        namespace = preferences_namespace(user_id)
        store = _get_memory_store()
        ts_ms = int(time.time() * 1000)
        for idx, fact in enumerate(facts):
            await store.store_fact(
                user_id=user_id,
                namespace=namespace,
                key=f"fact_{ts_ms}_{idx}",
                value=fact,
                ttl_days=settings.memory_default_ttl_days,
            )
        logger.info(
            "memory_extraction_completed",
            user_id=user_id,
            fact_count=len(facts),
        )
    except Exception as exc:
        logger.warning("memory_extraction_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Supervisor node
# ---------------------------------------------------------------------------


def _loop_break_decision(
    state: AgentState,
    trimmed: Sequence[BaseMessage],
    session_id: str,
) -> dict[str, object] | None:
    """Return an END routing dict when a specialist already answered.

    The supervisor must never route to a specialist twice in a row for
    the same user turn. When the last message in the trimmed window is
    an ``AIMessage`` whose ``current_agent`` is not the supervisor, we
    shortcut to END without calling the LLM.
    """
    if not trimmed:
        return None
    last_msg = trimmed[-1]
    if getattr(last_msg, "type", "") != "ai":
        return None
    last_agent = state.get("current_agent", "")
    if last_agent == "supervisor":
        return None
    logger.debug("supervisor_loop_break", last_agent=last_agent, session_id=session_id)
    return {"current_agent": "supervisor", "next_agent": None, "messages": []}


def _intent_short_circuit(
    trimmed: Sequence[BaseMessage],
    session_id: str,
) -> tuple[str, dict[str, object]] | None:
    """Deterministic regex-based intent match (SPEC-046 AC-046-5).

    Returns the matched agent name plus a full supervisor return dict
    when the last human message matches a known intent, or ``None`` when
    the LLM should decide. The matched name is exposed so the caller can
    tag the OTel span with the routing source.
    """
    last_human_text = ""
    for msg in reversed(trimmed):
        if getattr(msg, "type", "") == "human":
            last_human_text = str(getattr(msg, "content", ""))
            break
    matched_intent = match_intent(last_human_text)
    _settings = get_settings()
    valid_routes = effective_valid_routes(
        _settings.enable_subgraphs,
        _settings.multimodal_enabled,
        _settings.kokoro_enabled,
        _settings.skynet_enabled,
    )
    if matched_intent is None or matched_intent not in valid_routes:
        return None
    logger.info(
        "supervisor_intent_short_circuit",
        intent=matched_intent,
        next_agent=matched_intent,
        session_id=session_id,
    )
    return matched_intent, {
        "current_agent": "supervisor",
        "next_agent": matched_intent,
        "messages": [],
    }


def _match_route(raw: str, session_id: str) -> str:
    """Normalise the LLM routing response to a valid route name.

    Returns one of the members in :data:`_VALID_ROUTES` — defaults to
    ``"END"`` with a warning log when the response is unrecognised.
    """
    normalised = raw.strip("\"' \t\n").upper()
    _settings = get_settings()
    valid_routes = effective_valid_routes(
        _settings.enable_subgraphs,
        _settings.multimodal_enabled,
        _settings.kokoro_enabled,
        _settings.skynet_enabled,
    )
    for valid in valid_routes:
        if valid.upper() == normalised:
            return valid
    logger.warning(
        "supervisor_invalid_routing",
        raw_response=raw,
        defaulting_to="END",
        session_id=session_id,
    )
    return "END"


def _apply_routing_overrides(
    next_agent: str | None,
    state: AgentState,
    session_id: str,
) -> str | None:
    """Apply feature-flag downgrades and parallel-dispatch upgrades.

    - Phase 38: ``codeact`` → ``coder`` when CodeAct is disabled.
    - Phase 41: ``cua`` → ``researcher`` when CUA is disabled.
    - Phase 34: ``researcher`` → ``parallel_researcher`` when the planner
      has enqueued more than one task and parallel mode is enabled.
    """
    settings = get_settings()
    if next_agent == "codeact" and not settings.codeact_enabled:
        logger.info("supervisor_codeact_downgrade_to_coder", session_id=session_id)
        return "coder"
    if next_agent == "cua" and not settings.cua_enabled:
        logger.info("supervisor_cua_downgrade_to_researcher", session_id=session_id)
        return "researcher"
    if (
        next_agent == "researcher"
        and len(state.get("pending_tasks", [])) > 1
        and settings.parallel_enabled
    ):
        logger.info(
            "supervisor_routing_upgraded_to_parallel",
            pending_count=len(state.get("pending_tasks", [])),
            session_id=session_id,
        )
        return "parallel_researcher"
    return next_agent


def _extract_answer_content(answer_resp: object, session_id: str) -> str:
    """Collapse an LLM answer response to plain text.

    When the LLM called ``remember_preference`` we invoke the tool and
    join all tool results; otherwise we return the raw response content.
    Synthetic function-call JSON wrappers emitted by open models are
    unwrapped so the user never sees the raw wrapper.
    """
    from prismal.agents.tool_registry import _unwrap_synthetic_tool_call

    tool_calls = getattr(answer_resp, "tool_calls", None) or []
    tool_results: list[str] = []
    for tc in tool_calls:
        if tc.get("name") == "remember_preference":
            from prismal.agents.tools import remember_preference

            result = remember_preference.invoke(tc.get("args", {}))
            tool_results.append(str(result))
            logger.info(
                "supervisor.remember_preference_called",
                args=tc.get("args", {}),
                result=result,
                session_id=session_id,
            )

    content = "\n".join(tool_results) if tool_results else str(getattr(answer_resp, "content", ""))
    unwrapped = _unwrap_synthetic_tool_call(content)
    return unwrapped if unwrapped is not None else content


async def _build_direct_answer(
    llm: object,
    state: AgentState,
    trimmed: Sequence[BaseMessage],
    session_id: str,
) -> list[AIMessage]:
    """Generate the supervisor's direct answer when routing to END.

    Returns an empty list when the END branch should not answer directly
    (no messages, or the last message isn't a human turn). The system
    prompt is built from SOUL/CAPACITIES/USER/PREFERENCES markdown files
    via :class:`ProfileManager`, falling back to ``_ANSWER_SYSTEM_PROMPT``.
    """
    messages = state.get("messages")
    if not messages:
        return []
    last = messages[-1]
    if getattr(last, "type", "") != "human":
        return []

    from prismal.agents.tools import SUPERVISOR_DIRECT_TOOLS

    answer_system = ProfileManager().load_system_prompt() or _ANSWER_SYSTEM_PROMPT
    llm_with_tools = llm.bind_tools(SUPERVISOR_DIRECT_TOOLS)  # type: ignore[attr-defined]
    answer_resp = await llm_with_tools.ainvoke([SystemMessage(content=answer_system), *trimmed])
    return [AIMessage(content=_extract_answer_content(answer_resp, session_id))]


def _spawn_memory_extraction(state: AgentState) -> None:
    """Fire-and-forget SPEC-039 AC-039-3 memory extraction at session end.

    No-op when extraction is disabled or no event loop is running; all
    errors are swallowed so they never impact the user-facing response.
    """
    if not get_settings().memory_extraction_enabled:
        return
    try:
        task = asyncio.create_task(_extract_and_store_memory(state))
        _memory_extraction_tasks.add(task)
        task.add_done_callback(_memory_extraction_tasks.discard)
    except RuntimeError as exc:
        logger.debug("memory_extraction_task_skipped", error=str(exc))


async def supervisor_node(state: AgentState) -> dict[str, object]:
    """
    Execute the supervisor node logic and decide the next routing target.

    Builds a prompt from the current conversation history, invokes the LLM,
    and normalises its response to a valid routing option.  If the LLM returns
    an unrecognised string the supervisor defaults to ``None`` (i.e. END) and
    logs a warning so the event can be investigated without crashing the graph.

    Args:
        state: Current shared agent state flowing through the LangGraph graph.

    Returns:
        A partial state dict containing:
        - ``current_agent``: always ``"supervisor"``.
        - ``next_agent``: a member name string, or ``None`` when routing to END.
        - ``messages``: a list with one new ``HumanMessage`` carrying the final
          answer when the supervisor routes to END, otherwise an empty list so
          that LangGraph's ``add_messages`` reducer appends nothing.
    """
    session_id: str = str(state.get("session_id", "unknown"))
    logger.debug(
        "supervisor_node_invoked",
        session_id=session_id,
        message_count=len(state["messages"]),
    )

    otel = OTelManager()
    with otel.start_span(
        "agent.supervisor",
        attributes={
            "prismal.agent": "supervisor",
            "prismal.session_id": session_id,
            "prismal.message_count": len(state["messages"]),
        },
    ) as span:
        # Trim history before anything else — long sessions otherwise
        # exceed provider rate limits since every supervisor call re-sends
        # the entire history. The last _HISTORY_WINDOW messages are enough
        # for routing; the full history remains in LangGraph state.
        trimmed = state["messages"][-_HISTORY_WINDOW:]

        loop_break = _loop_break_decision(state, trimmed, session_id)
        if loop_break is not None:
            span.set_attribute("prismal.routing_decision", "END")
            return loop_break

        short_circuit = _intent_short_circuit(trimmed, session_id)
        if short_circuit is not None:
            matched_intent, result = short_circuit
            span.set_attribute("prismal.routing_decision", matched_intent)
            span.set_attribute("prismal.routing_source", "intent_router")
            return result

        llm = ProviderRegistry().get_llm_with_fallback()
        memory_context = await _recall_memory_context(state)
        routing_messages = [
            SystemMessage(
                content=build_system_prompt(
                    get_settings().enable_subgraphs,
                    get_settings().multimodal_enabled,
                    get_settings().kokoro_enabled,
                    get_settings().skynet_enabled,
                )
                + memory_context
            ),
            *trimmed,
            HumanMessage(
                content=(
                    "Based on the conversation above, which agent should handle "
                    "the next step? Respond with ONLY the agent name or 'END'."
                )
            ),
        ]
        response = await llm.ainvoke(routing_messages)
        raw: str = str(response.content).strip()

        matched = _match_route(raw, session_id)
        next_agent: str | None = None if matched == "END" else matched
        next_agent = _apply_routing_overrides(next_agent, state, session_id)

        span.set_attribute("prismal.routing_decision", next_agent if next_agent else "END")
        logger.info(
            "supervisor_routing_decision",
            next_agent=next_agent,
            raw_response=raw,
            session_id=session_id,
        )

        response_messages: list[AIMessage] = []
        if next_agent is None:
            response_messages = await _build_direct_answer(llm, state, trimmed, session_id)
            _spawn_memory_extraction(state)

        return {
            "current_agent": "supervisor",
            "next_agent": next_agent,
            "messages": response_messages,
        }


# ---------------------------------------------------------------------------
# Hierarchical root supervisor (Phase 40 / SPEC-042 AC-042-2)
# ---------------------------------------------------------------------------

#: Valid routing targets when ``PRISMAL_HIERARCHICAL_MODE=true``. The
#: root supervisor only sees 3 domain orchestrators plus cron_manager,
#: keeping the routing LLM prompt focused on ~4 targets instead of 12+.
HIERARCHICAL_MEMBERS: list[str] = [
    "research_orchestrator",
    "engineering_orchestrator",
    "analysis_orchestrator",
    "cron_manager",
]

_HIERARCHICAL_VALID_ROUTES: frozenset[str] = frozenset(HIERARCHICAL_MEMBERS) | {"END"}

_HIERARCHICAL_SYSTEM_PROMPT: str = """\
You are the ROOT orchestrator of a 3-level multi-agent system.

Your only job is to decide which **domain orchestrator** should handle the
user's request. You never call leaf agents directly — each domain has its
own sub-orchestrator that picks the right specialist within its scope.

## Available routing targets (ONLY these)

- research_orchestrator: Investigation, web search, internal knowledge-base
  (RAG) queries, document lookup, literature surveys, factual research.
  Keywords: "investigate", "search", "look up", "find information about",
  "what does the documentation say", "research", "summarise the paper".

- engineering_orchestrator: Writing, modifying, planning or executing code
  and files. Implementation, refactoring, debugging, PRDs and technical
  specs, file operations, skill management.
  Keywords: "code", "implement", "write a function", "refactor", "build
  a script", "plan the architecture", "create the file", "install skill".

- analysis_orchestrator: Producing structured analytical output. SQL /
  DataFrame queries and charts, ML training and evaluation, full software
  development pipelines, financial analysis of stocks / crypto / forex.
  Keywords: "analyze", "SQL", "chart", "train a model", "AutoML",
  "technical analysis", "P/E ratio", "dev pipeline".

- cron_manager: Scheduling recurring tasks — add / list / pause / resume /
  remove cron jobs. Time-based triggers ("every day", "cada hora",
  "programar", "schedule", "reminder").
  CRITICAL: route here for ANY query about cron jobs, scheduled jobs, or
  recurring tasks. NEVER answer cron questions from memory, and NEVER route
  cron questions to a domain orchestrator just because they start with
  "lista" or "list".
  Examples:
    · "lista los crons activos"
    · "pausa el cron de reporte diario"
    · "borra el job de las 8"
    · "list scheduled jobs"
    · "schedule a daily summary at 8am"

## Routing rules

1. Respond with ONLY the target name or the literal 'END'. No prose, no
   explanation, no punctuation.
2. Route to 'END' for greetings ("hola", "hello", "hi", "buenos días",
   etc.) and all small-talk — answer them directly from the conversation
   history.
3. Route to 'END' for simple factual questions you can answer directly.
4. CRITICAL loop-breaker: if the most recent message in the history is
   already an AIMessage from a specialist (any domain orchestrator or
   cron_manager), route to 'END' IMMEDIATELY. One specialist response
   per turn.
5. If the user request spans multiple domains, pick the domain that owns
   the *primary* action — the other domains can be invoked on a follow-up
   turn.

Respond with ONLY the target name or 'END'. Nothing else."""


async def hierarchical_supervisor_node(
    state: AgentState,
) -> dict[str, object]:
    """Root supervisor for hierarchical mode (Phase 40).

    A trimmed version of :func:`supervisor_node` scoped to 4 routing
    targets — the 3 domain orchestrators plus ``cron_manager``. Reuses
    the exact same machinery as the flat supervisor (history trimming,
    loop breaker, case-insensitive routing match, graceful fallback
    to END on invalid responses, direct-answer path when END is
    reached with a HumanMessage at the tail) but with a smaller valid
    routes set and a domain-level system prompt.

    Returns:
        Partial state dict with ``current_agent="supervisor"``,
        ``next_agent`` set to one of :data:`HIERARCHICAL_MEMBERS` /
        ``None``, and ``messages`` containing the direct answer when
        routing to END from a human turn.
    """
    session_id: str = str(state.get("session_id", "unknown"))
    logger.debug(
        "hierarchical_supervisor_invoked",
        session_id=session_id,
        message_count=len(state["messages"]),
    )

    otel = OTelManager()
    with otel.start_span(
        "agent.hierarchical_supervisor",
        attributes={
            "prismal.agent": "hierarchical_supervisor",
            "prismal.session_id": session_id,
            "prismal.mode": "hierarchical",
        },
    ) as span:
        llm = ProviderRegistry().get_llm_with_fallback()
        trimmed = state["messages"][-_HISTORY_WINDOW:]

        # Loop breaker: an AIMessage from any non-supervisor node is a
        # terminal response. Same semantics as ``supervisor_node``.
        if trimmed:
            last_msg = trimmed[-1]
            last_is_ai = getattr(last_msg, "type", "") == "ai"
            last_agent = state.get("current_agent", "")
            if last_is_ai and last_agent != "supervisor":
                logger.debug(
                    "hierarchical_supervisor_loop_break",
                    last_agent=last_agent,
                    session_id=session_id,
                )
                span.set_attribute("prismal.routing_decision", "END")
                return {
                    "current_agent": "supervisor",
                    "next_agent": None,
                    "messages": [],
                }

        # SPEC-046 AC-046-6: deterministic short-circuit for unambiguous
        # intents, validated against the hierarchical allowed set. When
        # the matched intent is not reachable from this supervisor, fall
        # through to LLM routing instead of forcing an unknown target.
        last_human_text = ""
        for msg in reversed(trimmed):
            if getattr(msg, "type", "") == "human":
                last_human_text = str(getattr(msg, "content", ""))
                break
        matched_intent = match_intent(last_human_text)
        if matched_intent is not None and matched_intent in _HIERARCHICAL_VALID_ROUTES:
            logger.info(
                "hierarchical_supervisor_intent_short_circuit",
                intent="cron_management",
                next_agent=matched_intent,
                session_id=session_id,
            )
            span.set_attribute("prismal.routing_decision", matched_intent)
            span.set_attribute("prismal.routing_source", "intent_router")
            return {
                "current_agent": "supervisor",
                "next_agent": matched_intent,
                "messages": [],
            }

        # Optional memory recall — the hierarchical root benefits from
        # the same cross-session preferences as the flat supervisor.
        memory_context = await _recall_memory_context(state)

        routing_messages = [
            SystemMessage(content=_HIERARCHICAL_SYSTEM_PROMPT + memory_context),
            *trimmed,
            HumanMessage(
                content=(
                    "Based on the conversation above, which domain "
                    "orchestrator should handle the next step? "
                    "Respond with ONLY the target name or 'END'."
                )
            ),
        ]

        response = await llm.ainvoke(routing_messages)
        raw: str = str(response.content).strip()
        normalised = raw.strip("\"' \t\n").upper()

        matched: str | None = None
        for valid in _HIERARCHICAL_VALID_ROUTES:
            if valid.upper() == normalised:
                matched = valid
                break

        if matched is None:
            logger.warning(
                "hierarchical_supervisor_invalid_routing",
                raw_response=raw,
                defaulting_to="END",
                session_id=session_id,
            )
            matched = "END"

        next_agent: str | None = None if matched == "END" else matched
        span.set_attribute("prismal.routing_decision", matched)

        logger.info(
            "hierarchical_supervisor_routing_decision",
            next_agent=next_agent,
            raw_response=raw,
            session_id=session_id,
        )

        # Direct-answer path when routing to END with a human tail —
        # mirrors the flat supervisor's behaviour so small talk still
        # works without involving a domain orchestrator.
        response_messages: list[AIMessage] = []
        if next_agent is None and state.get("messages"):
            last = state["messages"][-1]
            if getattr(last, "type", "") == "human":
                profile = ProfileManager()
                answer_system = profile.load_system_prompt() or _ANSWER_SYSTEM_PROMPT
                answer_resp = await llm.ainvoke([SystemMessage(content=answer_system), *trimmed])
                answer_content = str(answer_resp.content)
                # Same Ollama / open-model JSON-wrap defense as in the flat
                # supervisor — small talk must never leak the synthetic blob.
                from prismal.agents.tool_registry import (
                    _unwrap_synthetic_tool_call,
                )

                unwrapped = _unwrap_synthetic_tool_call(answer_content)
                if unwrapped is not None:
                    answer_content = unwrapped
                response_messages = [AIMessage(content=answer_content)]

        # Fire memory extraction on session end (same as flat).
        if next_agent is None and get_settings().memory_extraction_enabled:
            try:
                task = asyncio.create_task(_extract_and_store_memory(state))
                _memory_extraction_tasks.add(task)
                task.add_done_callback(_memory_extraction_tasks.discard)
            except RuntimeError as exc:
                logger.debug(
                    "hierarchical_memory_extraction_task_skipped",
                    error=str(exc),
                )

        return {
            "current_agent": "supervisor",
            "next_agent": next_agent,
            "messages": response_messages,
        }


# ---------------------------------------------------------------------------
# Supervisor router
# ---------------------------------------------------------------------------

# The return type lists all valid node names plus the LangGraph END sentinel.
_RouterLiteral = Literal[
    "researcher",
    "coder",
    "codeact",
    # Phase 41 / SPEC-043 — Computer Use Agent (VLM + Playwright).
    "cua",
    # Phase 40 / SPEC-042 — hierarchical domain orchestrators.
    "research_orchestrator",
    "engineering_orchestrator",
    "analysis_orchestrator",
    "rag_agent",
    "planner",
    "critic",
    "data_analyst",
    "file_manager",
    "skill_manager",
    "cron_manager",
    "dev_pipeline",  # Phase 24: dynamic subgraph
    "ml_pipeline",  # Phase 26: ML/DL pipeline
    "financial_analyst",  # Phase 27: Financial Analysis pipeline
    "parallel_researcher",  # Phase 34: map-reduce parallel research
    # Phase D (D1-01/02): advanced patterns + domain subgraphs (opt-in).
    "tot_agent",
    "debate_agent",
    "constitutional_filter",
    "lats_agent",
    "llm_compiler",
    "mixture_agent",
    "customer_service",
    "document_generation",
    "data_etl",
    "code_review",
    "debate_consensus",
    "__end__",
]


def supervisor_router(state: AgentState) -> _RouterLiteral:
    """
    Map ``state["next_agent"]`` to a LangGraph conditional-edge target.

    This function is passed directly to ``StateGraph.add_conditional_edges``
    as the routing function.  It reads the ``next_agent`` field set by
    :func:`supervisor_node` and returns the corresponding node name string.

    Args:
        state: Current shared agent state containing the ``next_agent`` field
            populated by the most recent call to :func:`supervisor_node`.

    Returns:
        The node name to transition to, or ``"__end__"`` when ``next_agent``
        is ``None`` or the string ``"END"``.
    """
    next_agent = state.get("next_agent")
    if next_agent is None or next_agent == "END":
        return "__end__"
    # Cast is safe: supervisor_node only sets known MEMBERS values
    return next_agent  # type: ignore[return-value]


__all__ = [
    "ADVANCED_MEMBERS",
    "HIERARCHICAL_MEMBERS",
    "MEMBERS",
    "build_system_prompt",
    "effective_valid_routes",
    "hierarchical_supervisor_node",
    "supervisor_node",
    "supervisor_router",
]
