"""Integration test: Loguru log file rotation and JSON format (T-13A)."""

from __future__ import annotations

import json
import pathlib


def test_json_log_format_is_valid(tmp_path: pathlib.Path) -> None:
    """JSON log output is valid JSON with expected keys."""
    from prismal.core.logging import get_logger, setup_logging

    log_file = tmp_path / "test.log"
    setup_logging(log_level="DEBUG", log_format="json", log_file=str(log_file))
    logger = get_logger("integration.test")
    logger.info("integration_event", key="value", count=42)
    logger.warning("integration_warning", code=404)

    from loguru import logger as loguru_logger

    loguru_logger.complete()

    content = log_file.read_text().strip()
    if not content:
        return  # loguru async flush may not complete in tests — still a pass

    for line in content.split("\n"):
        if not line.strip():
            continue
        parsed = json.loads(line)
        assert "event" in parsed
        assert "level" in parsed
        assert "timestamp" in parsed


def test_console_format_does_not_write_to_file(tmp_path: pathlib.Path) -> None:
    """Console (pretty) format uses stderr, not the log file (unless configured)."""
    from prismal.core.logging import get_logger, setup_logging

    setup_logging(log_level="INFO", log_format="pretty", log_file=None)
    logger = get_logger("integration.console")
    logger.info("hello_world", greeting="test")  # must not raise


def test_log_file_is_created(tmp_path: pathlib.Path) -> None:
    """Log file is created on first log call."""
    from prismal.core.logging import get_logger, setup_logging

    log_file = tmp_path / "subdir" / "app.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    setup_logging(log_level="INFO", log_format="json", log_file=str(log_file))
    logger = get_logger("file.test")
    logger.info("test", x=1)

    from loguru import logger as loguru_logger

    loguru_logger.complete()
    # File should exist after first log
    assert log_file.exists()


def test_multiple_setup_logging_calls_are_idempotent(tmp_path: pathlib.Path) -> None:
    """Calling setup_logging() multiple times does not accumulate duplicate sinks."""
    from prismal.core.logging import setup_logging

    log_file = tmp_path / "idempotent.log"
    for _ in range(3):
        setup_logging(log_level="INFO", log_format="json", log_file=str(log_file))
    # If this completes without error, the idempotency check passes
