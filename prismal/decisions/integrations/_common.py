"""Shared plumbing for the DEC integrations (mode resolution, safe cascade calls)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeIs

from prismal.core.logging import get_logger
from prismal.decisions.catalog import integration_enabled
from prismal.decisions.runtime import get_decision_runtime
from prismal.decisions.shadow import effective_mode
from prismal.decisions.types import DecisionMode, Outcome

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.core.config import Settings
    from prismal.decisions.cascade import DecisionCascade
    from prismal.decisions.runtime import DecisionRuntime
    from prismal.decisions.shadow import ShadowRunner
    from prismal.decisions.state import DecisionState
    from prismal.decisions.types import Decision, GatePolicy, QuestionSpec

logger = get_logger(__name__)


def active_runtime(
    settings: Settings, integration: str
) -> tuple[DecisionMode, DecisionRuntime | None]:
    """``(mode, runtime)``; ``OFF`` (and no runtime) unless the integration is enabled."""
    if not integration_enabled(settings, integration):
        return DecisionMode.OFF, None
    mode = effective_mode(settings, integration)
    if mode is DecisionMode.OFF:
        return mode, None
    runtime = get_decision_runtime(settings)
    return (mode, runtime) if runtime is not None else (DecisionMode.OFF, None)


async def safe_decide(
    cascade: DecisionCascade,
    state: DecisionState,
    questions: Mapping[str, QuestionSpec],
    *,
    policy: GatePolicy,
    integration: str,
    mode: DecisionMode = DecisionMode.ENFORCE,
    **kwargs: Any,
) -> dict[str, Decision]:
    """``cascade.decide`` that degrades to ``{}`` (pre-DEC path) on any error."""
    try:
        return dict(
            await cascade.decide(
                state, questions, policy=policy, integration=integration, mode=mode, **kwargs
            )
        )
    except Exception as exc:
        logger.warning(
            "decision_integration_failed", integration=integration, error=type(exc).__name__
        )
        return {}


def accepted(decision: Decision | None) -> TypeIs[Decision]:
    """True for a non-abstained, accepted decision."""
    return decision is not None and decision.outcome is Outcome.ACCEPTED and not decision.abstained


class ShadowHandle:
    """A launched shadow decision whose agreement is recorded once the reference is known."""

    def __init__(
        self,
        runner: ShadowRunner | None,
        task: asyncio.Task[Mapping[str, Decision]] | None,
        *,
        integration: str,
        question_id: str,
        language: str,
    ) -> None:
        self._runner = runner
        self._task = task
        self._integration = integration
        self._question_id = question_id
        self._language = language
        self._done: asyncio.Future[None] | None = None

    def record_reference(self, reference: str | bool) -> None:
        """Attach the pre-DEC result; recorded when the task finishes (never awaits)."""
        if self._runner is None or self._task is None:
            return
        loop = asyncio.get_running_loop()
        self._done = loop.create_future()
        done = self._done
        runner, task = self._runner, self._task

        def _record(t: asyncio.Task[Mapping[str, Decision]]) -> None:
            try:
                decision = t.result().get(self._question_id) if not t.cancelled() else None
                if decision is not None:
                    runner.record_reference(
                        self._integration,
                        self._question_id,
                        reference,
                        decision,
                        language=self._language,
                    )
            except Exception as exc:  # pragma: no cover - shadow never surfaces
                logger.info("decision_shadow_record_failed", error=type(exc).__name__)
            finally:
                if not done.done():
                    done.set_result(None)

        task.add_done_callback(_record)

    async def wait(self) -> None:
        """Test / shutdown helper: wait until the reference has been recorded."""
        if self._task is not None:
            await asyncio.wait({self._task})
        if self._done is not None:
            await self._done


def resolve_mode(settings: Settings | None, integration: str) -> DecisionMode:
    """Mode for an integration called with an explicit cascade (``None`` settings ⇒ enforce)."""
    if settings is None:
        return DecisionMode.ENFORCE
    return effective_mode(settings, integration)


def launch_shadow(
    shadow: ShadowRunner | None,
    state: DecisionState,
    question: QuestionSpec,
    policy: GatePolicy,
    integration: str,
    context: Mapping[str, str | None] | None = None,
) -> ShadowHandle:
    """Launch a shadow decision (if a runner is given) and wrap it in a handle."""
    task = (
        shadow.launch(
            state, {question.id: question}, policy=policy, integration=integration, context=context
        )
        if shadow is not None
        else None
    )
    return ShadowHandle(
        shadow, task, integration=integration, question_id=question.id, language=state.language
    )
