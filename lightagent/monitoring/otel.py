"""OpenTelemetry manager — singleton for distributed tracing and metrics.

Provides a single :class:`OTelManager` instance that configures
``TracerProvider`` and ``MeterProvider``.  All modules create spans and
metrics via this manager; never call ``opentelemetry.trace.get_tracer()``
directly.

Graceful degradation: when ``otel_enabled=False`` or the SDK is
unavailable, :meth:`start_span` returns a no-op context manager.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

import structlog

logger = structlog.get_logger(__name__)


class _NoOpSpan:
    """No-op span used when OTEL is disabled."""

    def set_attribute(self, key: str, value: object) -> None:
        """No-op set_attribute."""

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """No-op add_event."""

    def record_exception(self, exc: BaseException) -> None:
        """No-op record_exception."""

    def set_status(self, status: object) -> None:
        """No-op set_status."""


class OTelManager:
    """Singleton OpenTelemetry manager for tracing and metrics.

    Configures ``TracerProvider`` and ``MeterProvider`` with the selected
    exporter backend.  Provides :meth:`start_span` as the primary API for
    creating spans.

    Usage::

        otel = OTelManager()
        with otel.start_span("rag.retrieve") as span:
            span.set_attribute("lightagent.query_len", len(query))
            docs = retriever.get(query)
    """

    _instance: OTelManager | None = None
    _initialized: bool = False

    def __new__(cls) -> OTelManager:
        """Return the singleton instance."""
        if cls._instance is None:
            obj = super().__new__(cls)
            obj._initialized = False
            cls._instance = obj
        return cls._instance

    def __init__(self) -> None:
        """Initialize the manager (only runs once per process)."""
        if self._initialized:
            return
        self._initialized = True
        self._tracer: Any = None
        self._meter: Any = None
        self.enabled: bool = False
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._setup()

    def _setup(self) -> None:
        """Configure TracerProvider and MeterProvider."""
        try:
            from lightagent.monitoring._settings_proxy import get_monitoring_settings

            s = get_monitoring_settings()
            otel_enabled = getattr(s, "otel_enabled", True)
            if not otel_enabled:
                logger.info("otel.disabled")
                return

            exporter_name = getattr(s, "otel_exporter", "otlp")
            endpoint = getattr(s, "otel_endpoint", "http://localhost:4318")
            service_name = getattr(s, "otel_service_name", "lightagent")

            from opentelemetry import metrics, trace
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create({"service.name": service_name})

            # ── Span exporter ──────────────────────────────────────────
            span_exporter = self._build_span_exporter(exporter_name, endpoint)

            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            trace.set_tracer_provider(tracer_provider)
            self._tracer = tracer_provider.get_tracer(service_name)

            # ── Metric exporter ────────────────────────────────────────
            # Most trace backends (Jaeger, Zipkin, bare OTLP collectors)
            # only expose /v1/traces and return 404 on /v1/metrics.
            # Metric export is therefore opt-in via otel_metrics_enabled.
            # When disabled we skip MeterProvider setup entirely so no
            # PeriodicExportingMetricReader is created and no 404 errors appear.
            if getattr(s, "otel_metrics_enabled", False):
                metric_reader = self._build_metric_reader(exporter_name, endpoint)
                meter_provider = MeterProvider(
                    resource=resource, metric_readers=[metric_reader]
                )
                metrics.set_meter_provider(meter_provider)
                self._meter = meter_provider.get_meter(service_name)
                self._register_standard_metrics()
            self.enabled = True
            logger.info("otel.initialized", exporter=exporter_name, endpoint=endpoint)
        except Exception as exc:
            logger.warning("otel.init_failed", error=str(exc))
            self.enabled = False

    def _build_span_exporter(self, exporter_name: str, endpoint: str) -> Any:  # noqa: ANN401
        """Build and return the appropriate span exporter.

        Args:
            exporter_name: One of ``"otlp"``, ``"jaeger"``, ``"zipkin"``,
                ``"console"``.
            endpoint: Exporter endpoint URL.

        Returns:
            An OTEL span exporter instance.
        """
        if exporter_name == "console":
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            return ConsoleSpanExporter()
        if exporter_name == "jaeger":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            return OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        if exporter_name == "zipkin":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            return OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        # Default case: otlp
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")

    def _build_metric_reader(self, exporter_name: str, endpoint: str) -> Any:  # noqa: ANN401
        """Build and return the appropriate metric reader.

        Args:
            exporter_name: One of ``"otlp"``, ``"jaeger"``, ``"zipkin"``,
                ``"console"``.
            endpoint: Exporter endpoint URL.

        Returns:
            An OTEL metric reader instance.
        """
        if exporter_name in {"console", "jaeger", "zipkin"}:
            # jaeger and zipkin only support /v1/traces, not /v1/metrics.
            # Fall back to console so metric export doesn't log 404 errors.
            from opentelemetry.sdk.metrics.export import (
                ConsoleMetricExporter,
                PeriodicExportingMetricReader,
            )

            return PeriodicExportingMetricReader(ConsoleMetricExporter())
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        return PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics")
        )

    def _register_standard_metrics(self) -> None:
        """Register standard LightAgent OTEL metrics."""
        if self._meter is None:
            return
        self._counters["llm_requests"] = self._meter.create_counter(
            "lightagent.llm_requests_total",
            description="Total LLM API requests",
        )
        self._counters["llm_tokens"] = self._meter.create_counter(
            "lightagent.llm_tokens_total",
            description="Total LLM tokens consumed",
        )
        self._counters["agent_errors"] = self._meter.create_counter(
            "lightagent.agent_errors_total",
            description="Total agent errors",
        )
        self._counters["security_blocks"] = self._meter.create_counter(
            "lightagent.security_blocks_total",
            description="Total inputs blocked by security layer",
        )
        self._counters["rag_queries"] = self._meter.create_counter(
            "lightagent.rag_queries_total",
            description="Total RAG queries processed",
        )
        self._counters["mcp_tool_calls"] = self._meter.create_counter(
            "lightagent.mcp_tool_calls_total",
            description="Total MCP tool calls",
        )
        self._histograms["agent_latency"] = self._meter.create_histogram(
            "lightagent.agent_latency_seconds",
            description="Agent execution latency in seconds",
            unit="s",
        )
        self._histograms["llm_latency"] = self._meter.create_histogram(
            "lightagent.llm_latency_seconds",
            description="LLM call latency in seconds",
            unit="s",
        )
        self._histograms["rag_latency"] = self._meter.create_histogram(
            "lightagent.rag_retrieval_latency_seconds",
            description="RAG retrieval latency in seconds",
            unit="s",
        )

    @contextmanager
    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Any]:
        """Context manager that creates and manages an OTEL span.

        Returns a :class:`_NoOpSpan` when OTEL is disabled so callers need
        no guard checks.

        Args:
            name: Span name (e.g. ``"agent.execute"``).
            attributes: Optional initial span attributes.

        Yields:
            The span object (real or no-op).
        """
        if not self.enabled or self._tracer is None:
            yield _NoOpSpan()
            return

        with self._tracer.start_as_current_span(name) as span:
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, v)
            try:
                yield span
            except Exception as exc:
                span.record_exception(exc)
                raise

    def increment_counter(
        self,
        metric: str,
        value: int = 1,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Increment a named counter metric.

        Args:
            metric: Counter name key (e.g. ``"llm_requests"``).
            value: Amount to increment.
            attributes: Optional OTEL attribute labels.
        """
        counter = self._counters.get(metric)
        if counter is not None:
            with suppress(Exception):
                counter.add(value, attributes or {})

    def record_histogram(
        self,
        metric: str,
        value: float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Record a value in a named histogram metric.

        Args:
            metric: Histogram name key (e.g. ``"agent_latency"``).
            value: Measured value.
            attributes: Optional OTEL attribute labels.
        """
        histogram = self._histograms.get(metric)
        if histogram is not None:
            with suppress(Exception):
                histogram.record(value, attributes or {})

    def shutdown(self) -> None:
        """Flush and shut down all OTEL providers."""
        if not self.enabled:
            return
        try:
            from opentelemetry import metrics, trace

            tp = trace.get_tracer_provider()
            if hasattr(tp, "shutdown"):
                tp.shutdown()
            mp = metrics.get_meter_provider()
            if hasattr(mp, "shutdown"):
                mp.shutdown()
            logger.info("otel.shutdown")
        except Exception as exc:
            logger.warning("otel.shutdown_failed", error=str(exc))
