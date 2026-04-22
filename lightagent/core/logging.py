"""
Structured logging with structlog API and Loguru sink backend.

All LightAgent modules obtain loggers via :func:`get_logger`.
:func:`setup_logging` must be called once at startup (CLI main or
FastAPI lifespan). Loguru handles file rotation, retention, and
compression; structlog provides the structured-event API.

Example::

    from lightagent.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("agent_started", agent_id="supervisor", session_id="abc123")
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Callable

import structlog
from loguru import logger as _loguru

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_LEVEL_MAP: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


class _LoguruBridge:
    """Routes structlog output to Loguru at the correct call depth."""

    def msg(self, message: str) -> None:
        """Emit a log message via Loguru."""
        _loguru.opt(depth=3).info(message)

    def __getattr__(self, name: str) -> Callable[..., None]:
        """Emit a level-specific message via Loguru."""

        def _emit(message: str, *_args: object, **_kw: object) -> None:
            getattr(_loguru.opt(depth=3), name)(message)

        return _emit


def _loguru_factory(*_args: object, **_kw: object) -> _LoguruBridge:
    """Return a LoguruBridge as structlog's underlying logger."""
    return _LoguruBridge()


def setup_logging(
    log_level: LogLevel = "INFO",
    log_format: Literal["json", "pretty"] = "pretty",
    log_file: str | None = "data/logs/lightagent.log",
    rotation: str = "500 MB",
    retention: str = "30 days",
    compression: str = "gz",
) -> None:
    """
    Configure structlog processors with a Loguru sink.

    Must be called once at application startup. Subsequent calls replace
    existing sinks and reconfigure structlog (idempotent).

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_format: Output format — ``"json"`` (production) or ``"pretty"``
            (development with colours).
        log_file: Path for the rotating file sink. ``None`` disables file
            logging.
        rotation: Loguru rotation policy (e.g. ``"500 MB"``, ``"1 day"``).
        retention: Loguru retention policy (e.g. ``"30 days"``).
        compression: Loguru compression format (``"gz"``, ``"zip"``, etc.).

    Raises:
        ValueError: If ``log_level`` is not a recognised level name.
    """
    level_upper = log_level.upper()
    if level_upper not in _LEVEL_MAP:
        msg = f"Invalid log level: {log_level!r}"
        raise ValueError(msg)

    # ── Reset Loguru sinks ─────────────────────────────────────────────
    _loguru.remove()

    colorize = log_format == "pretty"
    _loguru.add(sys.stderr, level=level_upper, format="{message}", colorize=colorize)

    if log_file:
        _loguru.add(
            log_file,
            level=level_upper,
            rotation=rotation,
            retention=retention,
            compression=compression,
            format="{message}",
        )

    # ── structlog processors ───────────────────────────────────────────
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(_LEVEL_MAP[level_upper]),
        context_class=dict,
        logger_factory=_loguru_factory,
        cache_logger_on_first_use=True,
    )


def setup_chat_logging(log_file: str = "data/logs/lightagent.log") -> None:
    """Configure logging for interactive chat: file-only, no console output.

    Removes ALL Loguru sinks (including stderr) and adds a single file sink so
    that INFO/DEBUG messages do not pollute the interactive chat interface.
    Works regardless of structlog's per-logger cache state.

    Args:
        log_file: Path to the rotating log file.  Pass ``""`` to disable file
            logging entirely (completely silent).
    """
    _loguru.remove()
    if log_file:
        import sys as _sys
        from pathlib import Path as _Path

        _Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        _loguru.add(
            log_file,
            level="DEBUG",
            format="{message}",
            rotation="500 MB",
            retention="30 days",
        )
        # Re-add stderr at ERROR only so critical failures are still visible.
        _loguru.add(_sys.stderr, level="ERROR", format="{message}")


def get_logger(name: str = "lightagent") -> structlog.stdlib.BoundLogger:
    """
    Return a structlog BoundLogger bound to the given module name.

    Bootstraps with default settings (INFO, pretty) if :func:`setup_logging`
    has not yet been called, so that logging always works at import time.

    Args:
        name: Logger name — typically ``__name__`` of the calling module.

    Returns:
        A bound structlog logger ready for structured log calls.
    """
    cfg = structlog.get_config()
    if not cfg.get("processors"):
        setup_logging()
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name).bind())
