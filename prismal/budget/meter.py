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
from typing import TYPE_CHECKING, Any, Literal

from prismal.budget.types import Usage
from prismal.budget.usage import extract_gateway_meta, extract_token_usage
from prismal.providers.cost import compute_cost_usd

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.monitoring.cost_tracker import CostTracker
    from prismal.providers.gateways.base import GatewayCallMeta
    from prismal.providers.gateways.router import GatewayRouter


class CostMeter:
    """Accumulates :class:`Usage` for one run and optionally persists it."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        cost_tracker: CostTracker | None = None,
        session_id: str | None = None,
        tenant: str | None = None,
        gateway_router: GatewayRouter | None = None,
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
        self._gateway_router = gateway_router
        self.gateway_degradation: str | None = None  # last quota-downgrade window (GW5-05)
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
        self._emit_otel(
            usage,
            agent=agent,
            pattern=pattern,
            model=model,
            gateway=usage.gateway,
            served_model=usage.served_model,
        )
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
        reported_cost_usd: float | None = None,
    ) -> Usage:
        """Meter one LLM response: extract tokens, price them, accumulate.

        With ``token_efficiency_enabled`` (TKE — SPEC-TKE-ACC-004) cache tokens
        are normalized, priced at their cache rate and split into the
        ``llm_input_tokens{kind}`` metrics; otherwise pricing uses the v3.12.0
        arguments only. A gateway-reported cost always wins when given.

        A message carrying ``response_metadata["prismal_gateway"]`` (Phase GW —
        SPEC-GW-COST-001) contributes its reported cost and ``cost_mode``; a
        ``subscription`` call meters ``cost_usd=0`` with ``equivalent_usd``.
        """
        tokens = extract_token_usage(message)
        gateway_meta = extract_gateway_meta(message)
        cost_mode: Literal["metered", "subscription"] = "metered"
        if gateway_meta is not None:
            cost_mode = gateway_meta.cost_mode
            if reported_cost_usd is None:
                reported_cost_usd = gateway_meta.reported_cost_usd
        tke = bool(getattr(self._settings, "token_efficiency_enabled", False))
        if tke:
            tokens = tokens.normalized()
            estimate = compute_cost_usd(
                model,
                tokens.prompt_tokens,
                tokens.completion_tokens,
                settings=self._settings,
                cache_read_tokens=tokens.cache_read_tokens,
                cache_write_tokens=tokens.cache_write_tokens,
                reported_cost_usd=reported_cost_usd,
                cost_mode=cost_mode,
            )
        else:
            estimate = compute_cost_usd(
                model,
                tokens.prompt_tokens,
                tokens.completion_tokens,
                settings=self._settings,
                reported_cost_usd=reported_cost_usd,
                cost_mode=cost_mode,
            )
        detailed = tke or reported_cost_usd is not None or gateway_meta is not None
        usage = Usage(
            prompt_tokens=tokens.prompt_tokens,
            completion_tokens=tokens.completion_tokens,
            cost_usd=estimate.cost_usd,
            calls=1,
            wall_clock_s=wall_clock_s,
            estimated=estimate.estimated,
            cache_read_tokens=tokens.cache_read_tokens if tke else 0,
            cache_write_tokens=tokens.cache_write_tokens if tke else 0,
            reasoning_tokens=tokens.reasoning_tokens if tke else 0,
            cost_source=estimate.source if detailed else "",
            equivalent_usd=estimate.equivalent_usd,
            gateway=gateway_meta.gateway if gateway_meta is not None else "",
            served_model=(gateway_meta.served_model or "") if gateway_meta is not None else "",
        )
        if tke:
            self._emit_tke_otel(usage, agent=agent, model=model)
        if gateway_meta is not None:
            self.gateway_degradation = gateway_meta.quota_downgrade
            self._after_gateway_call(gateway_meta, estimate.equivalent_usd, estimate.cost_usd)
        return self.record(usage, agent=agent, pattern=pattern, model=model)

    def _after_gateway_call(
        self, meta: GatewayCallMeta, equivalent_usd: float, cost_usd: float
    ) -> None:
        """Ledger record + quota gauge (GW5-03/05) and reconciliation (GW5-04).

        Never raises: metering must not break a turn.
        """
        router: Any = self._gateway_router
        if router is None:
            with suppress(Exception):
                from prismal.providers.gateways.router import get_gateway_router

                router = get_gateway_router()
        if router is None:
            return
        ledger = getattr(router, "ledger", None)
        if ledger is not None:
            with suppress(Exception):
                ledger.record(
                    meta.gateway,
                    meta.requested_model,
                    equivalent_usd=equivalent_usd if meta.cost_mode == "subscription" else cost_usd,
                )
            with suppress(Exception):
                from prismal.monitoring.otel import OTelManager

                otel = OTelManager()
                for snap in ledger.snapshot(meta.gateway):
                    if snap.remaining is not None:
                        otel.set_gauge(
                            "gateway_quota_remaining",
                            snap.remaining,
                            attributes={"gateway": snap.gateway, "window": snap.window},
                        )
        reconciler = getattr(router, "reconciler", None)
        if reconciler is not None:
            with suppress(Exception):
                reconciler.schedule(meta)

    # ── internals ─────────────────────────────────────────────────────────────

    def _emit_otel(
        self,
        usage: Usage,
        *,
        agent: str | None,
        pattern: str | None,
        model: str | None,
        gateway: str = "",
        served_model: str = "",
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
            if gateway:  # Phase GW attribution; non-gateway calls keep v3.13 labels
                from prismal.providers.gateways.chat_model import bounded_model_label

                attrs["gateway"] = gateway
                attrs["served_model"] = bounded_model_label(served_model or None)
            otel.increment_counter("budget_tokens", usage.total_tokens, attributes=attrs)
            if usage.cost_usd:
                # Counters take ints; cost is reported in micro-USD to stay integral.
                otel.increment_counter(
                    "budget_cost_usd", round(usage.cost_usd * 1_000_000), attributes=attrs
                )
                otel.record_histogram(
                    "cost_per_call_usd", usage.cost_usd, attributes={"model": model or ""}
                )

    def _emit_tke_otel(self, usage: Usage, *, agent: str | None, model: str) -> None:
        with suppress(Exception):
            from prismal.monitoring.otel import OTelManager

            otel = OTelManager()
            base = {"agent": agent or "", "model": model}
            uncached = max(
                0, usage.prompt_tokens - usage.cache_read_tokens - usage.cache_write_tokens
            )
            for kind, value in (
                ("uncached", uncached),
                ("cache_read", usage.cache_read_tokens),
                ("cache_write", usage.cache_write_tokens),
            ):
                otel.increment_counter("llm_input_tokens", value, attributes={**base, "kind": kind})
            if usage.reasoning_tokens:
                otel.increment_counter(
                    "llm_reasoning_tokens", usage.reasoning_tokens, attributes=base
                )
            otel.record_histogram(
                "llm_cache_hit_ratio", usage.cache_hit_ratio, attributes={"agent": agent or ""}
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
