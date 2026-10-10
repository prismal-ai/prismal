"""Static catalog of DEC integrations, their flags and decision ids (SPEC-DEC-CFG-001).

Pure data, no imports beyond typing, so ``core/config.py`` can consult it from
``_validate_decisions`` without an import cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from prismal.core.config import Settings

#: Integration name (the ``decision_modes`` key) → its enable flag.
INTEGRATION_FLAGS: Final[dict[str, str]] = {
    "route": "decision_routing_enabled",
    "eval_judge": "decision_eval_judge_enabled",
    "hitl": "decision_hitl_enabled",
    "tool": "decision_tool_proposal_enabled",
    "rag": "decision_rag_relevance_enabled",
    "ctx": "decision_ctx_relevance_enabled",
    "swarm": "decision_swarm_eval_enabled",
    "safety": "decision_safety_prescreen_enabled",
}

#: Integration → the question ids it asks (each needs a calibration record to enforce).
INTEGRATION_DECISION_IDS: Final[dict[str, tuple[str, ...]]] = {
    "route": ("route.supervisor",),
    "eval_judge": ("eval.judge",),
    "hitl": ("hitl.band",),
    "tool": ("tool.proposal",),
    "rag": ("rag.chunk_relevance",),
    "ctx": ("ctx.relevance",),
    "swarm": ("swarm.worker_output",),
    "safety": ("safety.prescreen",),
}


def backend_model(settings: Settings, backend: str) -> str | None:
    """Pinned model a calibrated backend serves, or ``None`` (rule / llm need none)."""
    if backend == "laya":
        return settings.laya_checkpoint
    if backend == "jev":
        return settings.jev_model
    return None


def integration_enabled(settings: Settings, integration: str) -> bool:
    """True when the master flag and the integration's own flag are both on."""
    flag = INTEGRATION_FLAGS.get(integration)
    # ``is True`` so loose test doubles (MagicMock settings) never switch DEC on.
    return (
        flag is not None
        and getattr(settings, "decisions_enabled", False) is True
        and getattr(settings, flag, False) is True
    )


def configured_mode(settings: Settings, integration: str) -> str:
    """``off`` | ``shadow`` | ``enforce`` — enabled without an explicit entry ⇒ ``shadow``."""
    if not integration_enabled(settings, integration):
        return "off"
    return settings.decision_modes.get(integration, "shadow")
