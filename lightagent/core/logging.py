"""Structured logging configuration using structlog.

Provides JSON format for production and pretty console output for
development. All loggers in LightAgent should be obtained via
``get_logger()`` rather than ``logging.getLogger()``.

Example::

    from lightagent.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("agent_started", agent_id="supervisor", session_id="abc123")
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def setup_logging(
    log_level: LogLevel = "INFO",
    log_format: Literal["json", "pretty"] = "pretty",
) -> None:
    """Configure structlog and stdlib logging for the application.

    Must be called once at application startup (e.g. in CLI main or
    FastAPI lifespan). Subsequent calls are safe and idempotent — existing
    handlers are replaced, not accumulated.

    Args:
        log_level: Standard logging level name (DEBUG, INFO, WARNING, ERROR,
            CRITICAL). Case-insensitive. Raises ValueError for invalid values.
        log_format: Output format. Use ``"json"`` in production/containers
            for log aggregation; ``"pretty"`` for local development.

    Raises:
        ValueError: If ``log_level`` is not a recognised logging level name.
    """
    level = logging.getLevelName(log_level.upper())
    if not isinstance(level, int):
        msg = f"Invalid log level: {log_level!r}"
        raise ValueError(msg)

    # Processors applied to every log event
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    if log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Clear existing handlers to prevent duplicate output on repeated calls.
    # NOTE: Call setup_logging() BEFORE starting Uvicorn, not in the FastAPI
    # lifespan, to avoid removing Uvicorn's own access log handlers.
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


def get_logger(name: str = "lightagent") -> structlog.stdlib.BoundLogger:
    """Return a structlog BoundLogger bound to the given module name.

    If ``setup_logging()`` has not yet been called, this function
    initialises logging with default settings (INFO level, pretty format)
    so that a correctly-typed ``BoundLogger`` is always returned.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.
            Defaults to ``"lightagent"``.

    Returns:
        A structlog BoundLogger ready for structured log calls.

    Example::

        logger = get_logger(__name__)
        logger.info("task_complete", task_id="T-011", duration_ms=42)
    """
    # Ensure structlog is configured with BoundLogger as the wrapper class.
    # If setup_logging() was already called, configure() is a no-op because
    # we detect the wrapper mismatch and re-configure. If it has not been
    # called yet, we bootstrap with safe defaults so get_logger() is usable
    # standalone (e.g. at module import time in tests).
    cfg = structlog.get_config()
    if cfg.get("wrapper_class") is not structlog.stdlib.BoundLogger:
        setup_logging()

    # .bind() on the lazy proxy triggers resolution into a real BoundLogger.
    proxy = structlog.stdlib.get_logger(name)
    return proxy.bind()
