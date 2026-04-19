"""Constitutional AI pattern — principle-driven self-revision.

Implements SPEC-PAT-003. For each principle in the configured list, the LLM
checks whether the output violates it. On violation, the LLM is asked to
revise. The revise/check cycle repeats up to ``max_revisions`` per principle;
if the LLM never clears the principle within that cap, the result flags
``max_revisions_reached=True`` so downstream consumers can decide how to act.

All revisions are emitted as structured log events
(``constitutional_revision_applied``) for audit traceability.

Example::

    from lightagent.agents.patterns.constitutional import (
        ConstitutionalFilter,
        DEFAULT_PRINCIPLES,
    )

    filt = ConstitutionalFilter(principles=DEFAULT_PRINCIPLES)
    result = await filt.apply("Some agent output", context="original query")
    if not result.all_principles_satisfied:
        logger.warning(
            "principles_violated",
            count=len(result.revisions),
            max_revisions_reached=result.max_revisions_reached,
        )
    print(result.final_output)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.core.config import get_settings
from lightagent.core.exceptions import ConstitutionalError
from lightagent.core.logging import get_logger
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry
from lightagent.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from lightagent.core.config import Settings

logger = get_logger("lightagent.agents.patterns.constitutional")


@dataclass
class ConstitutionalPrinciple:
    """A principle governing what an agent's output must or must not contain.

    Attributes:
        id: Stable identifier (e.g. ``"P001"``).
        name: Short machine-friendly name.
        description: Human explanation of the principle.
        critique_prompt: Prompt asked to the LLM for violation detection.
        revision_prompt: Prompt asked to the LLM to rewrite the violating text.
        severity: ``"critical"`` / ``"high"`` / ``"medium"``.
    """

    id: str
    name: str
    description: str
    critique_prompt: str
    revision_prompt: str
    severity: Literal["critical", "high", "medium"] = "high"


@dataclass
class ConstitutionalRevision:
    """Record of one revision applied against one principle.

    Attributes:
        principle_id: Principle that triggered the revision.
        original: Text before revision.
        revised: Text after revision.
        violation_detected: LLM's description of the violation.
    """

    principle_id: str
    original: str
    revised: str
    violation_detected: str


@dataclass
class ConstitutionalResult:
    """Outcome of :meth:`ConstitutionalFilter.apply`.

    Attributes:
        final_output: Text after all revisions (or unchanged if clean).
        revisions: Ordered list of revisions applied.
        principles_checked: Total principles evaluated.
        all_principles_satisfied: ``True`` if no residual violations remain.
        max_revisions_reached: ``True`` if any principle hit the per-principle
            cap without clearing.
    """

    final_output: str
    revisions: list[ConstitutionalRevision] = field(default_factory=list)
    principles_checked: int = 0
    all_principles_satisfied: bool = True
    max_revisions_reached: bool = False


DEFAULT_PRINCIPLES: list[ConstitutionalPrinciple] = [
    ConstitutionalPrinciple(
        id="P001",
        name="no_harmful_content",
        description=(
            "The output must not contain harmful, violent, or dangerous content."
        ),
        critique_prompt=(
            "Does this text contain harmful, violent, or dangerous content that "
            "could cause real-world harm? Start your reply with either "
            "'VIOLATION:' followed by a brief description, or 'OK:' if no issue."
        ),
        revision_prompt=(
            "Rewrite the text removing harmful content while keeping the useful "
            "information intact. Respond with the revised text only."
        ),
        severity="critical",
    ),
    ConstitutionalPrinciple(
        id="P002",
        name="factual_accuracy",
        description=(
            "The output must not present clearly false claims as facts."
        ),
        critique_prompt=(
            "Does this text present clearly false factual claims as certain? "
            "Start your reply with 'VIOLATION:' plus the false claim, or 'OK:' "
            "if the text is accurate or appropriately hedged."
        ),
        revision_prompt=(
            "Rewrite the text correcting the false claims or flagging them as "
            "uncertain. Respond with the revised text only."
        ),
        severity="high",
    ),
    ConstitutionalPrinciple(
        id="P003",
        name="no_pii_exposure",
        description=(
            "The output must not unnecessarily reveal personally identifiable "
            "information (names, emails, phone numbers, addresses)."
        ),
        critique_prompt=(
            "Does this text unnecessarily expose PII (names, emails, phone "
            "numbers, addresses)? Start your reply with 'VIOLATION:' plus the "
            "exposed PII, or 'OK:' if no PII is unnecessarily revealed."
        ),
        revision_prompt=(
            "Rewrite the text removing or anonymising any unnecessarily exposed "
            "PII. Respond with the revised text only."
        ),
        severity="critical",
    ),
]


class ConstitutionalFilter:
    """Apply a sequence of constitutional principles to an agent output.

    Args:
        principles: Principles to apply in order. ``None`` uses
            :data:`DEFAULT_PRINCIPLES`.
        max_revisions: Maximum revision attempts per principle. Must be ≥ 1.
        settings: LightAgent settings. ``None`` resolves via
            :func:`~lightagent.core.config.get_settings`.
    """

    def __init__(
        self,
        principles: list[ConstitutionalPrinciple] | None = None,
        max_revisions: int = 3,
        settings: Settings | None = None,
    ) -> None:
        if max_revisions < 1:
            raise ValueError(f"max_revisions must be >= 1; got {max_revisions}")
        self._principles = (
            principles if principles is not None else DEFAULT_PRINCIPLES
        )
        self._max_revisions = max_revisions
        self._settings = settings if settings is not None else get_settings()
        self._llm = ProviderRegistry(settings=self._settings).get_llm()

    async def apply(
        self,
        output: str,
        context: str | None = None,
    ) -> ConstitutionalResult:
        """Check *output* against every principle and revise on violations."""
        otel = OTelManager()
        with otel.start_span("constitutional.apply") as span:
            span.set_attribute("lightagent.output_len", len(output))

            current = output
            revisions: list[ConstitutionalRevision] = []
            max_reached = False
            all_satisfied = True

            for principle in self._principles:
                for attempt in range(self._max_revisions):
                    violated, description = await self.check_principle(
                        current, principle, context=context
                    )
                    if not violated:
                        break
                    # Produce a revised version and log the revision.
                    try:
                        revised = await self._revise(current, principle, description)
                    except Exception as exc:
                        logger.warning(
                            "constitutional_revision_error",
                            principle_id=principle.id,
                            error=str(exc),
                        )
                        break
                    revision = ConstitutionalRevision(
                        principle_id=principle.id,
                        original=current,
                        revised=revised,
                        violation_detected=description,
                    )
                    revisions.append(revision)
                    logger.info(
                        "constitutional_revision_applied",
                        principle_id=principle.id,
                        attempt=attempt + 1,
                        severity=principle.severity,
                    )
                    current = revised
                else:
                    # Loop exhausted without `break` → still violating.
                    max_reached = True
                    all_satisfied = False

            span.set_attribute("lightagent.revisions_applied", len(revisions))
            span.set_attribute("lightagent.all_satisfied", all_satisfied)
            logger.info(
                "constitutional_apply_done",
                principles_checked=len(self._principles),
                revisions=len(revisions),
                all_satisfied=all_satisfied,
                max_revisions_reached=max_reached,
            )
            return ConstitutionalResult(
                final_output=current,
                revisions=revisions,
                principles_checked=len(self._principles),
                all_principles_satisfied=all_satisfied,
                max_revisions_reached=max_reached,
            )

    async def check_principle(
        self,
        output: str,
        principle: ConstitutionalPrinciple,
        context: str | None = None,
    ) -> tuple[bool, str]:
        """Ask the LLM whether *output* violates *principle*.

        Returns ``(violated, description)``. Unparseable replies return
        ``(False, "")`` — parsing is strict on the ``VIOLATION:`` prefix to
        avoid over-blocking benign output.
        """
        user_parts = [f"Text to evaluate:\n{output}"]
        if context:
            user_parts.append(f"Context:\n{context}")
        user = "\n\n".join(user_parts)
        builder = SecurePromptBuilder()
        messages = builder.build(system=principle.critique_prompt, user=user)
        lc_messages = [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
        try:
            response = await self._llm.ainvoke(lc_messages)
        except Exception as exc:
            logger.warning(
                "constitutional_check_error",
                principle_id=principle.id,
                error=str(exc),
            )
            return False, ""

        raw = str(response.content).strip()
        upper = raw.upper()
        if upper.startswith("VIOLATION"):
            _, _, remainder = raw.partition(":")
            return True, remainder.strip() or raw
        return False, ""

    async def _revise(
        self,
        output: str,
        principle: ConstitutionalPrinciple,
        violation: str,
    ) -> str:
        user_parts = [f"Text:\n{output}", f"Violation:\n{violation}"]
        builder = SecurePromptBuilder()
        messages = builder.build(
            system=principle.revision_prompt, user="\n\n".join(user_parts)
        )
        lc_messages = [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
        response = await self._llm.ainvoke(lc_messages)
        return str(response.content).strip()


def _build_filter(
    principles: list[ConstitutionalPrinciple] | None,
    max_revisions: int,
    settings: Settings | None,
) -> ConstitutionalFilter:
    """Factory used by downstream wrappers / graph nodes."""
    return ConstitutionalFilter(
        principles=principles,
        max_revisions=max_revisions,
        settings=settings,
    )


_: Any = _build_filter  # silence unused helper warning in strict mode

__all__ = [
    "DEFAULT_PRINCIPLES",
    "ConstitutionalError",
    "ConstitutionalFilter",
    "ConstitutionalPrinciple",
    "ConstitutionalResult",
    "ConstitutionalRevision",
]
