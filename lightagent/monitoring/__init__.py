"""Monitoring package — Langfuse, OpenTelemetry, and structured logging.

All observability singletons are exposed here:

- :class:`LangfuseManager` — LLM trace management via Langfuse
- :class:`OTelManager` — distributed tracing and metrics via OpenTelemetry

Do **not** import from ``langfuse`` or ``opentelemetry`` directly anywhere
outside this package. Always use the abstractions here.
"""

from lightagent.monitoring.langfuse_client import LangfuseManager
from lightagent.monitoring.otel import OTelManager

__all__ = ["LangfuseManager", "OTelManager"]
