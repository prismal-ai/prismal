"""Langfuse client manager — singleton for LLM observability tracing.

Provides a single :class:`LangfuseManager` instance that wraps the
Langfuse SDK.  All other modules import from here; never instantiate
``Langfuse()`` directly.

Graceful degradation: when ``langfuse_enabled=False`` or keys are
absent, all methods return no-op objects so callers need no guard checks.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class _NoOpTrace:
    """No-op trace object used when Langfuse is disabled."""

    def update(self, **_kwargs: object) -> None:
        """No-op update."""

    def event(self, **_kwargs: object) -> None:
        """No-op event."""

    def score(self, **_kwargs: object) -> None:
        """No-op score."""

    def generation(self, **_kwargs: object) -> _NoOpGeneration:
        """Return a no-op generation."""
        return _NoOpGeneration()


class _NoOpGeneration:
    """No-op generation object used when Langfuse is disabled."""

    def end(self, **_kwargs: object) -> None:
        """No-op end."""

    def update(self, **_kwargs: object) -> None:
        """No-op update."""


class LangfuseManager:
    """Singleton wrapper around the Langfuse SDK.

    Provides graceful degradation when Langfuse is disabled or
    credentials are not configured.  All public methods return
    real Langfuse objects when enabled, or no-op objects when disabled.

    Usage::

        langfuse = LangfuseManager()
        trace = langfuse.create_trace(name="agent.run", session_id="s1")
        handler = langfuse.get_callback_handler(trace_id=trace.id)
    """

    _instance: LangfuseManager | None = None
    _initialized: bool = False

    def __new__(cls) -> LangfuseManager:
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
        self._client: Any = None
        self.enabled: bool = False
        self._setup()

    def _setup(self) -> None:
        """Configure the Langfuse client based on current settings."""
        try:
            from lightagent.monitoring._settings_proxy import get_monitoring_settings

            s = get_monitoring_settings()
            langfuse_enabled = getattr(s, "langfuse_enabled", True)
            public_key = getattr(s, "langfuse_public_key", None)
            secret_key = getattr(s, "langfuse_secret_key", None)
            host = getattr(s, "langfuse_host", "https://cloud.langfuse.com")

            pub = public_key.get_secret_value() if public_key else ""
            sec = secret_key.get_secret_value() if secret_key else ""

            if not langfuse_enabled or not pub or not sec:
                logger.info("langfuse.disabled", reason="not_configured")
                return

            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=pub,
                secret_key=sec,
                host=host,
            )
            self.enabled = True
            logger.info("langfuse.initialized", host=host)
        except Exception as exc:
            logger.warning("langfuse.init_failed", error=str(exc))
            self.enabled = False

    def create_trace(
        self,
        name: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:  # noqa: ANN401 — returns Langfuse StatefulTraceClient or _NoOpTrace
        """Create a Langfuse trace or return a no-op.

        Args:
            name: Trace name (e.g. ``"agent.run"``).
            session_id: Optional session identifier.
            user_id: Optional user identifier.
            metadata: Optional additional metadata dict.

        Returns:
            A Langfuse ``StatefulTraceClient`` if enabled, else a
            :class:`_NoOpTrace`.
        """
        if not self.enabled or self._client is None:
            return _NoOpTrace()
        try:
            kwargs: dict[str, Any] = {"name": name}
            if session_id:
                kwargs["session_id"] = session_id
            if user_id:
                kwargs["user_id"] = user_id
            if metadata:
                kwargs["metadata"] = metadata
            return self._client.trace(**kwargs)
        except Exception as exc:
            logger.warning("langfuse.create_trace_failed", error=str(exc))
            return _NoOpTrace()

    def get_callback_handler(
        self,
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> Any | None:  # noqa: ANN401 — returns CallbackHandler or None
        """Return a LangChain/LangGraph Langfuse callback handler.

        Args:
            trace_id: Optional existing trace ID to attach to.
            session_id: Optional session identifier.

        Returns:
            A ``CallbackHandler`` if Langfuse is enabled, else ``None``.
        """
        if not self.enabled or self._client is None:
            return None
        try:
            from langfuse.langchain import CallbackHandler

            kwargs: dict[str, Any] = {}
            if trace_id:
                kwargs["trace_id"] = trace_id
            if session_id:
                kwargs["session_id"] = session_id
            return CallbackHandler(**kwargs)
        except Exception as exc:
            logger.warning("langfuse.callback_handler_failed", error=str(exc))
            return None

    def score_trace(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str | None = None,
    ) -> None:
        """Score a trace for evaluation.

        Args:
            trace_id: The trace to score.
            name: Score name (e.g. ``"relevance"``).
            value: Numeric score value.
            comment: Optional human-readable comment.
        """
        if not self.enabled or self._client is None:
            return
        try:
            self._client.score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment,
            )
        except Exception as exc:
            logger.warning("langfuse.score_failed", error=str(exc))

    def flush(self) -> None:
        """Flush all pending Langfuse events to the server.

        Safe to call when disabled — does nothing.
        """
        if self.enabled and self._client is not None:
            try:
                self._client.flush()
            except Exception as exc:
                logger.warning("langfuse.flush_failed", error=str(exc))

    def shutdown(self) -> None:
        """Shut down the Langfuse client, flushing all pending events."""
        self.flush()
        logger.info("langfuse.shutdown")
