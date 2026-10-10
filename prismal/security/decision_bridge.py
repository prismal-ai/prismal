"""DEC-SAFE bridge — the only security-side importer of ``prismal.decisions`` (DD-DEC-010).

Escalate-only: a ``True`` verdict adds scrutiny (a ``decision:prescreen_escalated``
reason for the full guardrail / NeMo pass and the audit trail); ``False`` means
"no opinion". Nothing here can clear, skip or relax a guardrail, and every
failure is "no opinion". With ``decisions_enabled=False`` nothing is imported
beyond the catalog and nothing is built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger(__name__)

INTEGRATION = "safety"


async def prescreen_escalates(text: str, settings: Settings) -> bool:
    """``True`` ⇒ escalate; never raises."""
    if not (
        getattr(settings, "decisions_enabled", False) is True
        and getattr(settings, "decision_safety_prescreen_enabled", False) is True
    ):
        return False
    try:
        from prismal.decisions.integrations import safety
        from prismal.decisions.integrations._common import active_runtime, launch_shadow
        from prismal.decisions.state import DecisionStateBuilder
        from prismal.decisions.types import DecisionMode

        mode, runtime = active_runtime(settings, INTEGRATION)
        if runtime is None:
            return False
        if mode is DecisionMode.SHADOW:
            state = DecisionStateBuilder().add_untrusted("text", text, source="user").build()
            launch_shadow(
                runtime.shadow, state, safety.SAFETY_QUESTION, runtime.policy, INTEGRATION
            )
            return False
        return await safety.safety_prescreen(text, cascade=runtime.cascade, policy=runtime.policy)
    except Exception as exc:
        logger.warning("decision_prescreen_failed", error_type=type(exc).__name__)
        return False
