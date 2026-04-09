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

from typing import TYPE_CHECKING, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger
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
- coder: Writing and executing code, reading/writing code files
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
  Route here when the user asks to:
  · Schedule something periodically (daily, weekly, every hour, etc.)
  · List, pause, resume, or remove scheduled jobs
  · Use time-based triggers: "every day", "cada hora", "programar", "agendar",
    "cron", "schedule", "recurring", "periodic", "reminder"
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

        routing_messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
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

                response_messages = [AIMessage(content=answer_content)]

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


__all__ = ["MEMBERS", "supervisor_node", "supervisor_router"]
