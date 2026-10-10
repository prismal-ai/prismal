"""Fail-open OTel helpers for the decision layer (SPEC-DEC-OTEL-001).

Telemetry never breaks a decision.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def inc(metric: str, attributes: dict[str, Any] | None = None) -> None:
    with suppress(Exception):
        from prismal.monitoring.otel import OTelManager

        OTelManager().increment_counter(metric, attributes=attributes)


def hist(metric: str, value: float, attributes: dict[str, Any] | None = None) -> None:
    with suppress(Exception):
        from prismal.monitoring.otel import OTelManager

        OTelManager().record_histogram(metric, value, attributes=attributes)


def gauge(metric: str, value: float, attributes: dict[str, Any] | None = None) -> None:
    with suppress(Exception):
        from prismal.monitoring.otel import OTelManager

        OTelManager().set_gauge(metric, value, attributes=attributes)
