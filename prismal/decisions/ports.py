"""``DecisionPort`` — the typed-decision hexagonal port (Phase DEC — SPEC-DEC-PRT-001).

Mirrors ``ToolProviderPort`` / ``PolicyPort``: a ``@runtime_checkable`` Protocol
whose failures are absorbed into a typed result. ``decide()`` never raises for a
backend failure — errors, timeouts and over-limit inputs become abstentions.
:func:`guarded_decide` is the shared wrapper every adapter uses to honour that.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from prismal.core.logging import get_logger
from prismal.decisions.types import Decision

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from prismal.decisions.types import GatePolicy, QuestionSpec, Reason

logger = get_logger(__name__)


@runtime_checkable
class DecisionPort(Protocol):
    """A backend that answers closed questions with probability distributions."""

    name: str  # backend id: "laya" | "jev" | "llm" | "fake" | …
    model: str  # pinned model version

    async def decide(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, QuestionSpec],
        *,
        policy: GatePolicy,
    ) -> Mapping[str, Decision]:
        """One Decision per question id. Must not raise: failures → abstained."""
        ...

    def supports(self, question: QuestionSpec, *, language: str) -> bool:
        """False for too many Choice options or an uncovered language."""
        ...

    async def aclose(self) -> None:
        """Release resources; idempotent."""
        ...


def abstain_all(
    questions: Mapping[str, QuestionSpec],
    *,
    backend: str,
    model: str,
    reason: Reason,
    latency_ms: float = 0.0,
) -> dict[str, Decision]:
    """One abstention per question id."""
    return {
        qid: Decision.abstain(q, backend=backend, model=model, reason=reason, latency_ms=latency_ms)
        for qid, q in questions.items()
    }


async def guarded_decide(
    port: DecisionPort,
    questions: Mapping[str, QuestionSpec],
    policy: GatePolicy,
    call: Callable[[Mapping[str, QuestionSpec]], Awaitable[Mapping[str, Decision]]],
    *,
    language: str = "en",
) -> dict[str, Decision]:
    """Run ``call`` for the supported questions within ``policy.timeout_ms``; never raise.

    Unsupported questions abstain with ``reason="unsupported"``; a timeout or any
    exception abstains every remaining question; a missing answer abstains that
    question with ``reason="backend_error"``.
    """
    if not questions:
        return {}
    supported = {qid: q for qid, q in questions.items() if _supports(port, q, language)}
    out = abstain_all(
        {qid: q for qid, q in questions.items() if qid not in supported},
        backend=port.name,
        model=port.model,
        reason="unsupported",
    )
    if not supported:
        return out
    start = time.perf_counter()
    try:
        answered = await asyncio.wait_for(call(supported), timeout=policy.timeout_ms / 1000.0)
    except TimeoutError:
        elapsed = (time.perf_counter() - start) * 1000.0
        out.update(
            abstain_all(
                supported, backend=port.name, model=port.model, reason="timeout", latency_ms=elapsed
            )
        )
        return out
    except Exception as exc:
        logger.warning("decision_backend_failed", backend=port.name, error_type=type(exc).__name__)
        elapsed = (time.perf_counter() - start) * 1000.0
        out.update(
            abstain_all(
                supported,
                backend=port.name,
                model=port.model,
                reason="backend_error",
                latency_ms=elapsed,
            )
        )
        return out
    for qid, q in supported.items():
        d = answered.get(qid)
        out[qid] = (
            d
            if isinstance(d, Decision) and d.question_id == qid
            else Decision.abstain(q, backend=port.name, model=port.model, reason="backend_error")
        )
    return out


def _supports(port: DecisionPort, q: QuestionSpec, language: str) -> bool:
    try:
        return bool(port.supports(q, language=language))
    except Exception:
        return False
