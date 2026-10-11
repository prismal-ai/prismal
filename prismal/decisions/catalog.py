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

#: Concrete, one-fact human-review questions (SPEC-DEC-OLL-014); ``needs_human_review``
#: is derived from them by a deterministic OR — no model is asked the abstract question.
HITL_QUESTION_IDS: Final[tuple[str, ...]] = (
    "hitl.destructive",
    "hitl.rule_change",
    "hitl.prior_approval",
    "hitl.health_safety",
    "hitl.money",
)

#: Low-frequency / offline integrations → the ``accurate`` Ollama tier (SPEC-DEC-OLL-007).
LOW_FREQUENCY_INTEGRATIONS: Final[frozenset[str]] = frozenset({"eval_judge", "hitl", "swarm"})

#: Integration → the question ids it asks (each needs a calibration record to enforce).
INTEGRATION_DECISION_IDS: Final[dict[str, tuple[str, ...]]] = {
    "route": ("route.supervisor",),
    "eval_judge": ("eval.judge",),
    "hitl": HITL_QUESTION_IDS,
    "tool": ("tool.proposal",),
    "rag": ("rag.chunk_relevance",),
    "ctx": ("ctx.relevance",),
    "swarm": ("swarm.worker_output",),
    "safety": ("safety.prescreen",),
}


def integration_of(question_id: str) -> str | None:
    """Integration that asks ``question_id`` (``None`` for ad-hoc questions)."""
    for integration, ids in INTEGRATION_DECISION_IDS.items():
        if question_id in ids:
            return integration
    return None


def backend_model(settings: Settings, backend: str) -> str | None:
    """Pinned model a calibrated backend serves, or ``None`` (rule / llm need none)."""
    if backend == "laya":
        return settings.laya_checkpoint
    if backend == "jev":
        return settings.jev_model
    return None


#: Ollama sub-tiers, in the order their models are reported by :func:`backend_models`.
OLLAMA_TIERS: Final[tuple[str, ...]] = ("fast", "accurate")


def backend_models(settings: Settings, backend: str) -> tuple[str, ...]:
    """Model names a calibrated backend may serve (``()`` for rule / llm).

    Laya / Jev: their single pinned model (the exact calibration key). Ollama:
    the bare model names of the configured sub-tiers (deduplicated, so collapsing
    both tiers onto one model yields one name). The Ollama digest is only known
    after the runtime preflight, so these names are *not* calibration keys — the
    key is ``<name>@<digest12>`` (SPEC-DEC-OLL-003).
    """
    if backend == "ollama":
        models = settings.decision_ollama_models
        names = (models[tier] for tier in OLLAMA_TIERS if tier in models)
        return tuple(dict.fromkeys(names))
    model = backend_model(settings, backend)
    return (model,) if model is not None else ()


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
