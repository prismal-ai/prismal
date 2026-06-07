"""Bounded, agreement-seeking deliberation among three SoulAgents (SPEC-KOK-AGT-002).

Reuses the ``debate`` pattern primitives (DD-KOK-002): positions are
:class:`~prismal.agents.patterns.debate.DebatePosition` value objects and the
default agreement metric is
:func:`~prismal.agents.patterns.debate.pairwise_jaccard`.

Round 1: each soul produces an independent position (concurrently).
Round r>1: each soul revises given the *other* souls' previous positions.
After each round the agreement score is computed over that round's positions;
deliberation stops early when it reaches the threshold (DD-KOK-008).  A hard
``max_rounds`` cap guarantees termination — if the souls never converge, the
judge still decides and records dissent.

Example::

    from prismal.agents.kokoro.deliberation import deliberate

    result = await deliberate("Should we ship now?", [spirit, mind, heart])
    print(result.agreement_score, result.converged)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prismal.agents.patterns.debate import DebatePosition, pairwise_jaccard
from prismal.core.exceptions import DeliberationError, KokoroConfigError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.agents.kokoro.soul_agent import SoulAgent
    from prismal.core.config import Settings

#: Agreement metric over the final positions' contents -> score in [0, 1].
AgreementFn = Callable[[list[str]], float]

logger = get_logger("prismal.agents.kokoro.deliberation")

_TRIAD_SIZE = 3


@dataclass(frozen=True)
class DeliberationResult:
    """Outcome of a :func:`deliberate` run.

    Attributes:
        positions: All positions produced across every round.
        final_positions: The last round's positions, one per soul.
        agreement_score: Agreement over ``final_positions`` content in [0, 1].
        rounds_completed: How many rounds actually ran (early stop included).
        converged: ``True`` when ``agreement_score >= threshold``.
    """

    positions: list[DebatePosition]
    final_positions: list[DebatePosition]
    agreement_score: float
    rounds_completed: int
    converged: bool


async def deliberate(
    query: str,
    souls: list[SoulAgent],
    *,
    max_rounds: int | None = None,
    agreement_threshold: float | None = None,
    agreement_fn: AgreementFn | None = None,
    settings: Settings | None = None,
) -> DeliberationResult:
    """Run deliberation rounds until convergence or ``max_rounds``.

    Args:
        query: The question or claim under deliberation.
        souls: Exactly three :class:`SoulAgent` instances.
        max_rounds: Hard cap on rounds.  ``None`` uses
            ``settings.kokoro_max_rounds`` (default 2).
        agreement_threshold: Early-stop agreement score in [0, 1].  ``None``
            uses ``settings.kokoro_agreement_threshold`` (default 0.6).
        agreement_fn: Injected agreement metric.  ``None`` uses
            :func:`pairwise_jaccard`.
        settings: Prismal settings; ``None`` resolves via ``get_settings()``.

    Returns:
        A :class:`DeliberationResult` with every position, the final round,
        the agreement score, and the convergence flag.

    Raises:
        KokoroConfigError: if ``len(souls) != 3`` or the resolved
            ``max_rounds`` is < 1.
        DeliberationError: when a soul fails in the independent first round
            (later rounds degrade gracefully to the soul's previous position).
    """
    if len(souls) != _TRIAD_SIZE:
        raise KokoroConfigError(
            f"Kokoro deliberation requires exactly {_TRIAD_SIZE} souls; got {len(souls)}"
        )

    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()

    resolved_max_rounds = max_rounds if max_rounds is not None else settings.kokoro_max_rounds
    if resolved_max_rounds < 1:
        raise KokoroConfigError(f"max_rounds must be >= 1; got {resolved_max_rounds}")
    resolved_threshold = (
        agreement_threshold
        if agreement_threshold is not None
        else settings.kokoro_agreement_threshold
    )
    resolved_agreement: AgreementFn = agreement_fn if agreement_fn is not None else pairwise_jaccard

    otel = OTelManager()
    with otel.start_span("kokoro.deliberate") as span:
        span.set_attribute("prismal.kokoro.max_rounds", resolved_max_rounds)
        span.set_attribute("prismal.kokoro.threshold", resolved_threshold)

        all_positions: list[DebatePosition] = []
        last_round: list[DebatePosition] = []
        agreement = 0.0
        converged = False
        rounds_completed = 0

        for round_idx in range(1, resolved_max_rounds + 1):
            round_positions = await _run_round(query, souls, last_round)
            all_positions.extend(round_positions)
            last_round = round_positions
            rounds_completed = round_idx

            agreement = resolved_agreement([p.content for p in round_positions])
            converged = agreement >= resolved_threshold
            logger.info(
                "kokoro_round_done",
                round=round_idx,
                agreement=agreement,
                converged=converged,
            )
            if converged:
                break

        span.set_attribute("prismal.kokoro.rounds", rounds_completed)
        span.set_attribute("prismal.kokoro.agreement", agreement)
        span.set_attribute("prismal.kokoro.converged", converged)

        return DeliberationResult(
            positions=all_positions,
            final_positions=last_round,
            agreement_score=agreement,
            rounds_completed=rounds_completed,
            converged=converged,
        )


async def _run_round(
    query: str,
    souls: list[SoulAgent],
    prior: list[DebatePosition],
) -> list[DebatePosition]:
    """Run one deliberation round concurrently and return one position per soul.

    Round 1 (``prior`` empty): independent positions; any failure aborts the
    round (there is nothing to fall back to) and the :class:`DeliberationError`
    raised by the failing :class:`SoulAgent` propagates.

    Round r>1: each soul sees only the *other* souls' previous positions.  A
    failing soul degrades gracefully to its own previous position so the round
    always yields one entry per soul.
    """
    if not prior:
        return list(await asyncio.gather(*(soul.position(query) for soul in souls)))

    by_agent = {p.agent_id: p for p in prior}
    tasks = [
        soul.position(query, prior=[p for p in prior if p.agent_id != soul.agent_id])
        for soul in souls
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    round_positions: list[DebatePosition] = []
    for soul, result in zip(souls, results, strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result  # never swallow CancelledError/KeyboardInterrupt
            previous = by_agent.get(soul.agent_id)
            if previous is None:  # pragma: no cover — prior always has one per soul
                raise DeliberationError(
                    f"Soul '{soul.agent_id}' failed with no previous position to fall back to"
                ) from result
            logger.warning(
                "kokoro_revision_fallback",
                agent_id=soul.agent_id,
                error=str(result),
            )
            round_positions.append(
                DebatePosition(
                    agent_id=previous.agent_id,
                    role=previous.role,
                    content=previous.content,
                    round=previous.round + 1,
                )
            )
        else:
            round_positions.append(result)
    return round_positions


__all__ = [
    "AgreementFn",
    "DeliberationResult",
    "deliberate",
]
