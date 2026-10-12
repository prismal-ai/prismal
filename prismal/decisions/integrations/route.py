"""DEC-ROUTE — supervisor tier-1 routing (Phase DEC — SPEC-DEC-INT-001, ARCHITECTURE §3.4).

Runs *inside* ``supervisor_node`` after ``match_intent()`` returned ``None``:

* ``off``     → nothing (pre-DEC LLM supervisor, byte-for-byte).
* ``shadow``  → a Choice over the valid routes runs alongside the LLM supervisor;
  the LLM route is returned and recorded as the reference.
* ``enforce`` → an accepted route skips the routing LLM call; anything else
  falls through to the unchanged LLM path.

≤ 20 options are served by Laya (its ``supports()`` cap), > 20 by Jev. Option
descriptions are developer-authored constants; the user's last message and a
bounded recent-turn summary are untrusted state fields.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any

from prismal.decisions.integrations._common import (
    ShadowHandle,
    accepted,
    active_runtime,
    launch_shadow,
    safe_decide,
)
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    DecisionMode,
    OptionSpec,
    QuestionSpec,
    RiskTier,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from prismal.core.config import Settings
    from prismal.decisions.cascade import DecisionCascade
    from prismal.decisions.state import DecisionState
    from prismal.decisions.types import GatePolicy

INTEGRATION = "route"
ROUTE_DECISION_ID = "route.supervisor"
_HISTORY_MESSAGES = 6
_HISTORY_CHARS_PER_MESSAGE = 300
_HISTORY_MAX_CHARS = 2000

_INSTRUCTIONS = (
    "Choose which agent should handle the next step of the conversation in the state. "
    "Pick END when the request is small talk, a greeting or can be answered directly "
    "without a specialist."
)

#: Developer-authored route descriptions (never user text).
ROUTE_DESCRIPTIONS: dict[str, str] = {
    "END": "No specialist needed: greetings, small talk or a direct answer.",
    "researcher": "Searches the web and gathers up-to-date public information.",
    "coder": "Writes, edits, explains or debugs source code.",
    "codeact": "Writes and executes Python code in a sandbox to act on a task.",
    "cua": "Operates a browser or desktop user interface step by step.",
    "rag_agent": "Answers from the internal knowledge base and indexed documents.",
    "planner": "Breaks a large goal into an ordered plan of tasks.",
    "critic": "Reviews a draft answer and points out weaknesses.",
    "data_analyst": "Queries tabular data with SQL or DataFrames and draws charts.",
    "file_manager": "Reads, writes, lists or moves files in the workspace.",
    "skill_manager": "Installs, lists or manages agent skills.",
    "cron_manager": "Schedules, lists, pauses or removes recurring jobs.",
    "dev_pipeline": "Runs a full software delivery pipeline from spec to review.",
    "ml_pipeline": "Trains and evaluates a machine-learning model end to end.",
    "financial_analyst": "Analyses stocks, crypto or forex markets (read-only).",
    "parallel_researcher": "Researches several independent questions in parallel.",
    "tot_agent": "Explores alternative lines of reasoning as a tree of thoughts.",
    "debate_agent": "Has several agents argue opposing positions.",
    "constitutional_filter": "Revises an answer against a set of principles.",
    "lats_agent": "Searches over candidate action sequences with tree search.",
    "llm_compiler": "Executes a dependency graph of tool tasks in parallel waves.",
    "mixture_agent": "Combines proposals from several models into one answer.",
    "customer_service": "Handles customer support questions and tickets.",
    "document_generation": "Plans, writes and formats a long document.",
    "data_etl": "Extracts, validates, transforms and loads datasets.",
    "code_review": "Reviews code for style, security and logic issues.",
    "debate_consensus": "Runs a debate that ends in a moderated consensus.",
    "multimodal_pipeline": "Understands images, audio or video attachments.",
    "kokoro": "Deliberates between three perspectives and renders a judgement.",
    "skynet": "Fans a goal out to a swarm of worker agents.",
    "blind_review_pipeline": "Implements a spec and has it blind-reviewed twice.",
}


@lru_cache(maxsize=32)
def build_route_question(valid_routes: frozenset[str]) -> QuestionSpec:
    """Choice over ``valid_routes`` (sorted for a deterministic option order)."""
    options = tuple(
        OptionSpec(route, ROUTE_DESCRIPTIONS.get(route, f"The {route} agent."))
        for route in sorted(valid_routes)
    )
    return QuestionSpec(
        id=ROUTE_DECISION_ID,
        kind=DecisionKind.CHOICE,
        instructions=_INSTRUCTIONS,
        options=options,
        risk_tier=RiskTier.MEDIUM,
    )


def route_rule_hook(
    valid_routes: frozenset[str],
) -> Callable[[Mapping[str, str], QuestionSpec], Decision | None]:
    """Rule tier: the deterministic ``match_intent`` regex over the user's message."""
    from prismal.agents.intent_router import match_intent

    def hook(state: Mapping[str, str], question: QuestionSpec) -> Decision | None:
        """Certain decision for the ``match_intent`` route when valid, else ``None``."""
        text = _unfence(state.get("user", ""))
        intent = match_intent(text)
        if intent is None or intent not in valid_routes or intent not in question.labels():
            return None
        return Decision.from_probabilities(
            question,
            {lab: (1.0 if lab == intent else 0.0) for lab in question.labels()},
            backend="rule",
            model="intent_router",
            spread_confidence=1.0,
        )

    return hook


def _unfence(value: str) -> str:
    lines = value.splitlines()
    if len(lines) >= 2 and lines[0].startswith("«untrusted:") and lines[-1] == "«/untrusted»":
        return "\n".join(lines[1:-1])
    return value


def _content(message: Any) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def build_route_state(messages: Sequence[Any]) -> DecisionState:
    """Last human message + bounded recent-turn summary, both untrusted."""
    last_index = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if getattr(messages[i], "type", "") == "human"
        ),
        None,
    )
    user = _content(messages[last_index]) if last_index is not None else ""
    earlier = messages[: last_index if last_index is not None else len(messages)]
    lines = [
        f"{getattr(m, 'type', 'msg')}: {_content(m)[:_HISTORY_CHARS_PER_MESSAGE]}"
        for m in earlier[-_HISTORY_MESSAGES:]
    ]
    history = "\n".join(lines)[-_HISTORY_MAX_CHARS:]
    builder = DecisionStateBuilder().add_untrusted("user", user, source="user")
    if history:
        builder.add_untrusted("history", history, source="history")
    return builder.build()


async def decide_route(
    state: Mapping[str, Any],
    valid_routes: Sequence[str] | frozenset[str],
    *,
    cascade: DecisionCascade,
    settings: Settings,
    mode: DecisionMode = DecisionMode.ENFORCE,
    policy: GatePolicy | None = None,
    context: Mapping[str, str | None] | None = None,
) -> str | None:
    """An accepted route from ``valid_routes``, or ``None`` ⇒ LLM supervisor. Never raises."""
    from prismal.decisions.types import GatePolicy as _GatePolicy

    routes = frozenset(valid_routes)
    question = build_route_question(routes)
    decision_state = build_route_state(list(state.get("messages", [])))
    decisions = await safe_decide(
        cascade,
        decision_state,
        {question.id: question},
        policy=policy or _GatePolicy.from_settings(settings),
        integration=INTEGRATION,
        mode=mode,
        rule_hook=route_rule_hook(routes),
        context=context,
    )
    d = decisions.get(question.id)
    if accepted(d) and isinstance(d.value, str) and d.value in routes:
        return d.value
    return None


class RouteOutcome:
    """Result of :func:`maybe_decide_route` consumed by ``supervisor_node``."""

    def __init__(
        self, accepted_route: str | None = None, shadow: ShadowHandle | None = None
    ) -> None:
        self.accepted_route = accepted_route
        self._shadow = shadow

    def record_reference(self, route: str) -> None:
        """Record the LLM supervisor's route as the shadow reference (no-op otherwise)."""
        if self._shadow is not None:
            self._shadow.record_reference(route)

    async def wait(self) -> None:
        """Wait until the shadow comparison (if any) has been recorded."""
        if self._shadow is not None:
            await self._shadow.wait()


_NOOP = RouteOutcome()


async def maybe_decide_route(
    state: Mapping[str, Any],
    valid_routes: Sequence[str] | frozenset[str],
    *,
    settings: Settings,
) -> RouteOutcome:
    """Supervisor seam: no-op unless ``decisions_enabled`` + ``decision_routing_enabled``."""
    mode, runtime = active_runtime(settings, INTEGRATION)
    if runtime is None:
        return _NOOP
    context = {"session_id": str(state.get("session_id")) if state.get("session_id") else None}
    if mode is DecisionMode.ENFORCE:
        route = await decide_route(
            state,
            valid_routes,
            cascade=runtime.cascade,
            settings=settings,
            mode=mode,
            policy=runtime.policy,
            context=context,
        )
        return RouteOutcome(accepted_route=route)
    routes = frozenset(valid_routes)
    question = build_route_question(routes)
    decision_state = build_route_state(list(state.get("messages", [])))
    return RouteOutcome(
        shadow=launch_shadow(
            runtime.shadow, decision_state, question, runtime.policy, INTEGRATION, context
        )
    )
