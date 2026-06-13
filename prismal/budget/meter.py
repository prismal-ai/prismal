"""The CostMeter — per-run usage accumulator (Phase C — SPEC-CST-MET-001).

In-memory, O(1) per call, no I/O on the hot path. Extracts token counts from a
response, prices them via ``providers/cost.py``, accumulates a :class:`Usage`,
emits OTel counters tagged for attribution, and — when a :class:`CostTracker`
and ``session_id`` are injected — also persists each call for FinOps history.
Enforcement (see :mod:`prismal.budget.guard`) reads :attr:`usage`; it never
depends on the persistence bridge.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from prismal.budget.types import Usage
from prismal.budget.usage import extract_token_usage
from prismal.providers.cost import compute_cost_usd

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.monitoring.cost_tracker import CostTracker


class CostMeter:
    """Accumulates :class:`Usage` for one run and optionally persists it."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        cost_tracker: CostTracker | None = None,
        session_id: str | None = None,
        tenant: str | None = None,
    ) -> None:
        """Initialize an empty meter.

        Args:
            settings: Used for the pricing-table fallback.
            cost_tracker: Optional SQLite persistence bridge (FinOps history).
            session_id: Required for the persistence bridge.
            tenant: Recorded as ``user_id`` in the bridge and as the OTel
                ``tenant`` attribute for attribution.
        """
        self._settings = settings
        self._cost_tracker = cost_tracker
        self._session_id = session_id
        self._tenant = tenant
        self._usage = Usage()

    @property
    def usage(self) -> Usage:
        """The cumulative usage snapshot."""
        return self._usage

    def record(
        self,
        usage: Usage,
        *,
        agent: str | None = None,
        pattern: str | None = None,
        model: str | None = None,
    ) -> Usage:
        """Accumulate *usage*, emit OTel, optionally persist; return the new total."""
        self._usage = self._usage + usage
        self._emit_otel(usage, agent=agent, pattern=pattern, model=model)
        if model is not None:
            self._bridge(usage, model)
        return self._usage

    def record_response(
        self,
        message: object,
        model: str,
        *,
        wall_clock_s: float = 0.0,
        agent: str | None = None,
        pattern: str | None = None,
    ) -> Usage:
        """Meter one LLM response: extract tokens, price them, accumulate."""
        tokens = extract_token_usage(message)
        estimate = compute_cost_usd(
            model,
            tokens.prompt_tokens,
            tokens.completion_tokens,
            settings=self._settings,
        )
        usage = Usage(
            prompt_tokens=tokens.prompt_tokens,
            completion_tokens=tokens.completion_tokens,
            cost_usd=estimate.cost_usd,
            calls=1,
            wall_clock_s=wall_clock_s,
            estimated=estimate.estimated,
        )
        return self.record(usage, agent=agent, pattern=pattern, model=model)

    # ── internals ─────────────────────────────────────────────────────────────

    def _emit_otel(
        self,
        usage: Usage,
        *,
        agent: str | None,
        pattern: str | None,
        model: str | None,
    ) -> None:
        with suppress(Exception):
            from prismal.monitoring.otel import OTelManager

            otel = OTelManager()
            attrs = {
                "agent": agent or "",
                "pattern": pattern or "",
                "model": model or "",
                "tenant": self._tenant or "",
            }
            otel.increment_counter("budget_tokens", usage.total_tokens, attributes=attrs)
            if usage.cost_usd:
                # Counters take ints; cost is reported in micro-USD to stay integral.
                otel.increment_counter(
                    "budget_cost_usd", round(usage.cost_usd * 1_000_000), attributes=attrs
                )
                otel.record_histogram(
                    "cost_per_call_usd", usage.cost_usd, attributes={"model": model or ""}
                )

    def _bridge(self, usage: Usage, model: str) -> None:
        if self._cost_tracker is None or self._session_id is None:
            return
        with suppress(Exception):
            self._cost_tracker.record(
                session_id=self._session_id,
                model=model,
                tokens_in=usage.prompt_tokens,
                tokens_out=usage.completion_tokens,
                cost_usd=usage.cost_usd,
                user_id=self._tenant,
            )
