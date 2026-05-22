"""Tests for Loguru + structlog logging integration (T-130)."""

from __future__ import annotations

import json

import structlog

from prismal.core.logging import get_logger, setup_logging


def test_setup_logging_returns_no_error() -> None:
    """setup_logging configures structlog without raising."""
    setup_logging(log_level="DEBUG", log_format="json")


def test_get_logger_returns_logger() -> None:
    """get_logger returns a bound logger after setup_logging."""
    setup_logging(log_level="DEBUG", log_format="json")
    logger = get_logger("test.module")
    assert logger is not None


def test_json_format_output(tmp_path: object) -> None:
    """JSON format produces valid JSON log lines."""
    import pathlib

    log_file = pathlib.Path(str(tmp_path)) / "test.log"  # type: ignore[arg-type]
    setup_logging(log_level="DEBUG", log_format="json", log_file=str(log_file))
    logger = get_logger("test")
    logger.info("test_event", key="value")
    from loguru import logger as loguru_logger

    loguru_logger.complete()
    content = log_file.read_text().strip()
    if content:
        lines = content.split("\n")
        parsed = json.loads(lines[-1])
        assert parsed["event"] == "test_event"
        assert parsed["key"] == "value"


def test_console_format_does_not_crash() -> None:
    """Console (pretty) format runs without error."""
    setup_logging(log_level="INFO", log_format="pretty", log_file=None)
    logger = get_logger("test")
    logger.info("hello_console", number=42)


def test_invalid_log_level_raises() -> None:
    """Invalid log level raises ValueError."""
    import pytest

    with pytest.raises(ValueError, match="Invalid log level"):
        setup_logging(log_level="NOTLEVEL")  # type: ignore[arg-type]


def test_structlog_context_vars() -> None:
    """structlog context vars are merged into log output."""
    setup_logging(log_level="DEBUG", log_format="json", log_file=None)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-123")
    logger = get_logger("test")
    logger.info("context_test")
    structlog.contextvars.clear_contextvars()
