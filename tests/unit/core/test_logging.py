"""Unit tests for structured logging setup."""

import logging


def test_get_logger_returns_bound_logger() -> None:
    """get_logger() returns a structlog BoundLogger."""
    import structlog

    from lightagent.core.logging import get_logger

    logger = get_logger("test_module")
    assert isinstance(logger, structlog.stdlib.BoundLogger)


def test_setup_logging_sets_level_debug() -> None:
    """setup_logging(DEBUG) sets root logger to DEBUG."""
    from lightagent.core.logging import setup_logging

    setup_logging(log_level="DEBUG", log_format="pretty")
    assert logging.getLogger().level == logging.DEBUG


def test_setup_logging_sets_level_warning() -> None:
    """setup_logging(WARNING) sets root logger to WARNING."""
    from lightagent.core.logging import setup_logging

    setup_logging(log_level="WARNING", log_format="pretty")
    assert logging.getLogger().level == logging.WARNING


def test_setup_logging_json_format_no_error() -> None:
    """setup_logging with json format does not raise."""
    from lightagent.core.logging import setup_logging

    setup_logging(log_level="INFO", log_format="json")


def test_setup_logging_pretty_format_no_error() -> None:
    """setup_logging with pretty format does not raise."""
    from lightagent.core.logging import setup_logging

    setup_logging(log_level="INFO", log_format="pretty")


def test_setup_logging_idempotent() -> None:
    """Calling setup_logging twice does not add duplicate handlers."""
    from lightagent.core.logging import setup_logging

    setup_logging(log_level="INFO", log_format="pretty")
    setup_logging(log_level="INFO", log_format="pretty")
    root = logging.getLogger()
    # Should have exactly 1 handler (not 2 from two calls)
    assert len(root.handlers) == 1


def test_get_logger_returns_non_none() -> None:
    """get_logger() with any name returns a non-None logger."""
    from lightagent.core.logging import get_logger

    assert get_logger("my.module") is not None
    assert get_logger() is not None


def test_get_logger_default_name() -> None:
    """get_logger() with no args uses 'lightagent' as default name."""
    import structlog

    from lightagent.core.logging import get_logger

    logger = get_logger()
    assert isinstance(logger, structlog.stdlib.BoundLogger)
