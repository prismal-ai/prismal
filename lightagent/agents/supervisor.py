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

    from lightagent.agents.supervisor import supervisor_node, supervisor_router
    from lightagent.agents.state import create_initial_state

    state = create_initial_state(session_id="sess-demo")
    updated = await supervisor_node(state)
    next_node = supervisor_router(updated)
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.agents.intent_router import match_intent
from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger
from lightagent.memory.long_term_store import (
    LongTermMemoryStore,
    preferences_namespace,
)
from lightagent.memory.profile import ProfileManager
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.supervisor")

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

    LightAgent session IDs follow the shape ``{user}-{timestamp}``; this
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
        response = await llm.ainvoke(
            [SystemMessage(content=_MEMORY_EXTRACTION_PROMPT), *recent]
        )

        raw = str(getattr(response, "content", "")).strip()
        if not raw:
            return

        candidate_lines = [
            line.lstrip("-*0123456789. ").strip() for line in raw.splitlines()
        ]
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
            "lightagent.agent": "supervisor",
            "lightagent.session_id": session_id,
            "lightagent.message_count": len(state["messages"]),
        },
    ) as span:
        llm = ProviderRegistry().get_llm_with_fallback()

        # Trim the conversation history to the most recent messages before
        # sending to the LLM.  Long sessions (message_count > 20) can easily
        # exceed provider rate limits (e.g. Anthropic's 30k tokens/min cap)
        # because every supervisor call re-sends the entire history.
        # Keeping the last _HISTORY_WINDOW messages is sufficient for routing
        # decisions — the full history is still stored in the LangGraph state.
        trimmed = state["messages"][-_HISTORY_WINDOW:]

        # Loop-breaker: if the last message is an AIMessage from a specialist
        # agent (i.e. not the user and not generated by the supervisor itself),
        # route directly to END without calling the LLM.  This guarantees that
        # a single specialist response always terminates the routing cycle,
        # regardless of what the LLM might decide.
        if trimmed:
            last_msg = trimmed[-1]
            last_is_ai = getattr(last_msg, "type", "") == "ai"
            last_agent = state.get("current_agent", "")
            if last_is_ai and last_agent != "supervisor":
                logger.debug(
                    "supervisor_loop_break",
                    last_agent=last_agent,
                    session_id=session_id,
                )
                span.set_attribute("lightagent.routing_decision", "END")
                return {
                    "current_agent": "supervisor",
                    "next_agent": None,
                    "messages": [],
                }

        # SPEC-046 AC-046-5: deterministic short-circuit for unambiguous
        # intents (currently only ``cron_manager``).  Runs AFTER the loop
        # breaker and BEFORE building the LLM routing prompt so we never
        # waste tokens — and never mis-route — on requests that a pure
        # regex matcher can recognise with high confidence.
        last_human_text = ""
        for msg in reversed(trimmed):
            if getattr(msg, "type", "") == "human":
                last_human_text = str(getattr(msg, "content", ""))
                break
        matched_intent = match_intent(last_human_text)
        if matched_intent is not None and matched_intent in _VALID_ROUTES:
            logger.info(
                "supervisor_intent_short_circuit",
                intent="cron_management",
                next_agent=matched_intent,
                session_id=session_id,
            )
            span.set_attribute("lightagent.routing_decision", matched_intent)
            span.set_attribute("lightagent.routing_source", "intent_router")
            return {
                "current_agent": "supervisor",
                "next_agent": matched_intent,
                "messages": [],
            }

        # SPEC-039 AC-039-4: recall up to ``memory_recall_limit`` durable
        # preferences for the current user and append them to the system
        # prompt. Degrades gracefully to an empty suffix on any failure.
        memory_context = await _recall_memory_context(state)

        routing_messages = [
            SystemMessage(content=_SYSTEM_PROMPT + memory_context),
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
        # Normalise: strip surrounding quotes/whitespace and match case-insensitively
        normalised = raw.strip("\"' \t\n").upper()

        # Find exact match against valid routes (case-insensitive)
        matched: str | None = None
        for valid in _VALID_ROUTES:
            if valid.upper() == normalised:
                matched = valid
                break

        if matched is None:
            logger.warning(
                "supervisor_invalid_routing",
                raw_response=raw,
                defaulting_to="END",
                session_id=session_id,
            )
            matched = "END"

        next_agent: str | None = None if matched == "END" else matched

        # Phase 38: respect the CodeAct feature flag — if the LLM picked
        # ``codeact`` while the toggle is off, downgrade to the classic
        # ``coder`` agent so the user still gets a response.
        if next_agent == "codeact" and not get_settings().codeact_enabled:
            logger.info(
                "supervisor_codeact_downgrade_to_coder",
                session_id=session_id,
            )
            next_agent = "coder"
            matched = "coder"

        # Phase 41: respect the CUA feature flag — if the LLM picked
        # ``cua`` while the toggle is off, downgrade to the
        # ``researcher`` agent which is the closest text-based
        # fallback. The CUA node itself also returns a graceful
        # disabled message, but downgrading at the routing layer
        # avoids an extra round-trip through the graph.
        if next_agent == "cua" and not get_settings().cua_enabled:
            logger.info(
                "supervisor_cua_downgrade_to_researcher",
                session_id=session_id,
            )
            next_agent = "researcher"
            matched = "researcher"

        # Phase 34: heuristic upgrade — when the LLM picked ``researcher`` and
        # the planner has already enqueued more than one independent task in
        # ``pending_tasks``, switch to the parallel research dispatcher so the
        # tasks fan out concurrently instead of running serially.
        if (
            next_agent == "researcher"
            and len(state.get("pending_tasks", [])) > 1
            and get_settings().parallel_enabled
        ):
            logger.info(
                "supervisor_routing_upgraded_to_parallel",
                pending_count=len(state.get("pending_tasks", [])),
                session_id=session_id,
            )
            next_agent = "parallel_researcher"
            matched = "parallel_researcher"

        span.set_attribute("lightagent.routing_decision", matched)

        logger.info(
            "supervisor_routing_decision",
            next_agent=next_agent,
            raw_response=raw,
            session_id=session_id,
        )

        # When routing to END and the last message is from the user, generate a
        # direct answer.  Sub-agents produce their own AIMessages, so we only
        # need this for the supervisor-answers-directly path.
        response_messages: list[AIMessage] = []
        if next_agent is None and state.get("messages"):
            last = state["messages"][-1]
            if getattr(last, "type", "") == "human":
                # Build the answer system prompt by combining:
                #   SOUL.md (persona) + CAPACITIES.md (permanent capabilities)
                #   + USER.md (user context) + PREFERENCES.md (learned prefs).
                # load_system_prompt() handles the concatenation and falls back
                # to an empty string when none of the files exist.
                profile = ProfileManager()
                answer_system = profile.load_system_prompt() or _ANSWER_SYSTEM_PROMPT

                # Bind remember_preference so the LLM can call it when the user
                # makes an explicit "remember that…" request.
                from lightagent.agents.tools import SUPERVISOR_DIRECT_TOOLS

                llm_with_tools = llm.bind_tools(SUPERVISOR_DIRECT_TOOLS)
                answer_resp = await llm_with_tools.ainvoke(
                    [SystemMessage(content=answer_system), *trimmed]
                )

                # If the LLM decided to call remember_preference, invoke it
                # and replace the response with a human-readable confirmation.
                tool_calls = getattr(answer_resp, "tool_calls", None) or []
                if tool_calls:
                    tool_results: list[str] = []
                    for tc in tool_calls:
                        if tc.get("name") == "remember_preference":
                            from lightagent.agents.tools import remember_preference

                            result = remember_preference.invoke(tc.get("args", {}))
                            tool_results.append(str(result))
                            logger.info(
                                "supervisor.remember_preference_called",
                                args=tc.get("args", {}),
                                result=result,
                                session_id=session_id,
                            )
                    if tool_results:
                        answer_content = "\n".join(tool_results)
                    else:
                        answer_content = str(answer_resp.content)
                else:
                    answer_content = str(answer_resp.content)

                # Unwrap synthetic function-call JSON (Ollama / open models)
                # so the supervisor direct-answer path never leaks the raw
                # wrapper through to the user.
                from lightagent.agents.tool_registry import (
                    _unwrap_synthetic_tool_call,
                )

                unwrapped = _unwrap_synthetic_tool_call(answer_content)
                if unwrapped is not None:
                    answer_content = unwrapped

                response_messages = [AIMessage(content=answer_content)]

        # SPEC-039 AC-039-3: when the session is ending (next_agent is
        # None) fire memory extraction as a background task so the user
        # response is returned immediately. ``create_task`` swallows its
        # own exceptions via ``_extract_and_store_memory`` which wraps
        # the entire body in try/except.
        if next_agent is None and get_settings().memory_extraction_enabled:
            try:
                task = asyncio.create_task(_extract_and_store_memory(state))
                _memory_extraction_tasks.add(task)
                task.add_done_callback(_memory_extraction_tasks.discard)
            except RuntimeError as exc:
                # No running loop (unlikely inside an async node) —
                # best-effort skip so the graph still returns cleanly.
                logger.debug("memory_extraction_task_skipped", error=str(exc))

        return {
            "current_agent": "supervisor",
            "next_agent": next_agent,
            "messages": response_messages,
        }


# ---------------------------------------------------------------------------
# Hierarchical root supervisor (Phase 40 / SPEC-042 AC-042-2)
# ---------------------------------------------------------------------------

#: Valid routing targets when ``LIGHTAGENT_HIERARCHICAL_MODE=true``. The
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
            "lightagent.agent": "hierarchical_supervisor",
            "lightagent.session_id": session_id,
            "lightagent.mode": "hierarchical",
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
                span.set_attribute("lightagent.routing_decision", "END")
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
            span.set_attribute("lightagent.routing_decision", matched_intent)
            span.set_attribute("lightagent.routing_source", "intent_router")
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
        span.set_attribute("lightagent.routing_decision", matched)

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
                answer_resp = await llm.ainvoke(
                    [SystemMessage(content=answer_system), *trimmed]
                )
                answer_content = str(answer_resp.content)
                # Same Ollama / open-model JSON-wrap defense as in the flat
                # supervisor — small talk must never leak the synthetic blob.
                from lightagent.agents.tool_registry import (
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
    "HIERARCHICAL_MEMBERS",
    "MEMBERS",
    "hierarchical_supervisor_node",
    "supervisor_node",
    "supervisor_router",
]
