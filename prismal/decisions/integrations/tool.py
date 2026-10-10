"""DEC-TOOL — tool proposal (Phase DEC — SPEC-DEC-INT-004, DD-DEC-001).

A Choice over the *phase-allowed* tool names; the result is a **proposal**
only — ``ToolPolicyEngine`` and ``ActionInterceptor`` still authorize every
call. Option descriptions are built from tool names only: tool docstrings may
come from third-party MCP servers and are not developer-authored constants.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from prismal.decisions.integrations._common import (
    accepted,
    launch_shadow,
    resolve_mode,
    safe_decide,
)
from prismal.decisions.integrations.route import build_route_state
from prismal.decisions.types import (
    DecisionKind,
    DecisionMode,
    GatePolicy,
    OptionSpec,
    QuestionSpec,
    RiskTier,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from prismal.core.config import Settings
    from prismal.decisions.cascade import DecisionCascade
    from prismal.decisions.shadow import ShadowRunner

INTEGRATION = "tool"
TOOL_DECISION_ID = "tool.proposal"


@lru_cache(maxsize=64)
def _question(names: tuple[str, ...]) -> QuestionSpec:
    return QuestionSpec(
        id=TOOL_DECISION_ID,
        kind=DecisionKind.CHOICE,
        instructions="Choose the single tool best suited to the next step of the request.",
        options=tuple(OptionSpec(n, f"Use the tool named {n}.") for n in names),
        risk_tier=RiskTier.HIGH,
    )


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def build_tool_question(names: Sequence[str]) -> QuestionSpec:
    """Choice over tool names; names outside a safe charset (third-party text) are dropped."""
    return _question(tuple(sorted({n for n in names if _SAFE_NAME.match(n)})))


async def propose_tool(
    state: Mapping[str, Any],
    allowed: Sequence[Any],
    *,
    cascade: DecisionCascade,
    settings: Settings | None = None,
    shadow: ShadowRunner | None = None,
    policy: GatePolicy | None = None,
) -> str | None:
    """Proposed tool name from ``allowed`` (``BaseTool`` or names), or ``None``."""
    names = [getattr(t, "name", t) for t in allowed]
    names = [str(n) for n in names if n and _SAFE_NAME.match(str(n))]
    if len(set(names)) < 2:
        return None
    mode = resolve_mode(settings, INTEGRATION)
    if mode is DecisionMode.OFF:
        return None
    gate = policy or (GatePolicy.from_settings(settings) if settings else GatePolicy())
    question = build_tool_question(names)
    decision_state = build_route_state(list(state.get("messages", [])))
    if mode is DecisionMode.SHADOW:
        launch_shadow(shadow, decision_state, question, gate, INTEGRATION)
        return None
    d = (
        await safe_decide(
            cascade, decision_state, {question.id: question}, policy=gate, integration=INTEGRATION
        )
    ).get(question.id)
    if accepted(d) and isinstance(d.value, str) and d.value in names:
        return d.value
    return None
