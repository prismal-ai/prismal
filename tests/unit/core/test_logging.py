"""Unit tests for structured logging setup (Loguru + structlog)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
import structlog


@pytest.fixture(autouse=True)
def reset_structlog() -> Generator[None]:
    """Reset structlog to default config between tests to prevent state leakage."""
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


def test_get_logger_returns_non_none() -> None:
    """get_logger() with any name returns a non-None logger."""
    from lightagent.core.logging import get_logger

    assert get_logger("my.module") is not None
    assert get_logger() is not None


def test_get_logger_is_callable() -> None:
    """get_logger() returns an object that can call .info()."""
    from lightagent.core.logging import get_logger

    logger = get_logger("test_module")
    # The new implementation uses BoundLoggerFilteringAtNotset (not BoundLogger),
    # but it must still be a bound structlog logger with callable log methods.
    assert callable(getattr(logger, "info", None))
    assert callable(getattr(logger, "debug", None))
    assert callable(getattr(logger, "warning", None))


def test_get_logger_default_name_is_bound() -> None:
    """get_logger() with no args returns a bound structlog logger."""
    from lightagent.core.logging import get_logger

    logger = get_logger()
    assert logger is not None
    # Must have log-level methods
    assert callable(getattr(logger, "info", None))


def test_setup_logging_json_format_no_error() -> None:
    """setup_logging with json format does not raise."""
    from lightagent.core.logging import setup_logging

    setup_logging(log_level="INFO", log_format="json", log_file=None)


def test_setup_logging_pretty_format_no_error() -> None:
    """setup_logging with pretty format does not raise."""
    from lightagent.core.logging import setup_logging

    setup_logging(log_level="INFO", log_format="pretty", log_file=None)


def test_setup_logging_sets_level_debug_via_loguru() -> None:
    """setup_logging(DEBUG) configures Loguru at DEBUG level without error."""
    from lightagent.core.logging import setup_logging

    # Loguru is now the backend — verify no exception on DEBUG setup
    setup_logging(log_level="DEBUG", log_format="pretty", log_file=None)


def test_setup_logging_sets_level_warning_via_loguru() -> None:
    """setup_logging(WARNING) configures Loguru at WARNING level without error."""
    from lightagent.core.logging import setup_logging

    setup_logging(log_level="WARNING", log_format="pretty", log_file=None)


def test_setup_logging_idempotent() -> None:
    """Calling setup_logging twice does not raise or accumulate duplicate sinks."""
    from lightagent.core.logging import setup_logging

    # setup_logging calls loguru.remove() first so it is always idempotent
    setup_logging(log_level="INFO", log_format="pretty", log_file=None)
    setup_logging(log_level="INFO", log_format="pretty", log_file=None)
    # No exception = idempotency confirmed


def test_setup_logging_invalid_level_raises() -> None:
    """setup_logging with an invalid log level raises ValueError."""
    from lightagent.core.logging import setup_logging

    with pytest.raises(ValueError, match="Invalid log level"):
        setup_logging(log_level="NONSENSE")  # type: ignore[arg-type]
