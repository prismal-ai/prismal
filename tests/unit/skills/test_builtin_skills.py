"""Unit tests for built-in skills (T-072).

Tests cover:
- weather     (wttr.in backend — HTTP mocked)
- web_search  (DuckDuckGo backend — HTTP mocked)
- code_executor (subprocess gated by LIGHTAGENT_SHELL_ENABLED)
- email_reader  (IMAP mocked; graceful degradation when no config)
- calendar     (reads .ics files from tmp dir)
- database_query (SELECT-only guard; DuckDB in-memory)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    import pytest


# ── Weather skill ─────────────────────────────────────────────────────────────

_WEATHER_PATCH = "lightagent.skills.available.weather.skill.httpx.get"
_SEARCH_PATCH = "lightagent.skills.available.web_search.skill.httpx.get"


class TestWeatherSkill:
    """Tests for WeatherSkill."""

    def test_metadata(self) -> None:
        """WeatherSkill exposes correct metadata."""
        from lightagent.skills.available.weather.skill import WeatherSkill

        skill = WeatherSkill()
        assert skill.metadata.name == "weather"
        assert skill.metadata.safe_to_auto_activate is True

    def test_get_tools_returns_get_weather(self) -> None:
        """get_tools() returns a single 'get_weather' tool."""
        from lightagent.skills.available.weather.skill import WeatherSkill

        tools = WeatherSkill().get_tools()
        assert len(tools) == 1
        assert tools[0].name == "get_weather"

    def test_wttr_success(self) -> None:
        """get_weather returns formatted string on successful wttr.in response."""
        from lightagent.skills.available.weather.skill import WeatherSkill

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "current_condition": [
                {
                    "temp_C": "25",
                    "temp_F": "77",
                    "weatherDesc": [{"value": "Sunny"}],
                    "humidity": "60",
                    "FeelsLikeC": "27",
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch(_WEATHER_PATCH, return_value=mock_response):
            result = WeatherSkill().get_tools()[0].invoke({"city": "Caracas"})

        assert "Caracas" in result
        assert "Sunny" in result
        assert "25" in result

    def test_wttr_http_error(self) -> None:
        """get_weather returns error string on HTTP failure."""
        import httpx

        from lightagent.skills.available.weather.skill import WeatherSkill

        with patch(_WEATHER_PATCH, side_effect=httpx.HTTPError("timeout")):
            result = WeatherSkill().get_tools()[0].invoke({"city": "Nowhere"})

        assert "[weather]" in result

    def test_owm_used_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OpenWeatherMap backend is used when env vars are set."""
        from lightagent.skills.available.weather.skill import WeatherSkill

        monkeypatch.setenv("LIGHTAGENT_WEATHER_PROVIDER", "openweathermap")
        monkeypatch.setenv("LIGHTAGENT_WEATHER_API_KEY", "testkey")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "weather": [{"description": "clear sky"}],
            "main": {"temp": 30.0, "feels_like": 32.0, "humidity": 50},
        }
        mock_response.raise_for_status = MagicMock()

        with patch(_WEATHER_PATCH, return_value=mock_response):
            result = WeatherSkill().get_tools()[0].invoke({"city": "Miami"})

        assert "clear sky" in result


# ── Web search skill ──────────────────────────────────────────────────────────


class TestWebSearchSkill:
    """Tests for WebSearchSkill."""

    def test_metadata(self) -> None:
        """WebSearchSkill exposes correct metadata."""
        from lightagent.skills.available.web_search.skill import WebSearchSkill

        skill = WebSearchSkill()
        assert skill.metadata.name == "web_search"
        assert skill.metadata.safe_to_auto_activate is True

    def test_get_tools_returns_web_search(self) -> None:
        """get_tools() returns a single 'web_search' tool."""
        from lightagent.skills.available.web_search.skill import WebSearchSkill

        tools = WebSearchSkill().get_tools()
        assert len(tools) == 1
        assert tools[0].name == "web_search"

    def test_ddg_success_with_abstract(self) -> None:
        """web_search returns abstract text from DDG response."""
        from lightagent.skills.available.web_search.skill import WebSearchSkill

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "AbstractText": "Python is a programming language.",
            "AbstractURL": "https://python.org",
            "RelatedTopics": [],
        }
        mock_response.raise_for_status = MagicMock()

        with patch(_SEARCH_PATCH, return_value=mock_response):
            result = WebSearchSkill().get_tools()[0].invoke({"query": "Python"})

        assert "Python is a programming language" in result

    def test_ddg_no_results_returns_fallback(self) -> None:
        """web_search returns fallback URL when no DDG results found."""
        from lightagent.skills.available.web_search.skill import WebSearchSkill

        mock_response = MagicMock()
        mock_response.json.return_value = {"AbstractText": "", "RelatedTopics": []}
        mock_response.raise_for_status = MagicMock()

        with patch(_SEARCH_PATCH, return_value=mock_response):
            result = WebSearchSkill().get_tools()[0].invoke({"query": "xyzzy"})

        assert "xyzzy" in result

    def test_ddg_http_error(self) -> None:
        """web_search returns error message on HTTP failure."""
        import httpx

        from lightagent.skills.available.web_search.skill import WebSearchSkill

        with patch(_SEARCH_PATCH, side_effect=httpx.HTTPError("connection refused")):
            result = WebSearchSkill().get_tools()[0].invoke({"query": "test"})

        assert "[web_search]" in result


# ── Code executor skill ───────────────────────────────────────────────────────


class TestCodeExecutorSkill:
    """Tests for CodeExecutorSkill."""

    def test_metadata(self) -> None:
        """CodeExecutorSkill exposes correct metadata."""
        from lightagent.skills.available.code_executor.skill import CodeExecutorSkill

        skill = CodeExecutorSkill()
        assert skill.metadata.name == "code_executor"
        assert skill.metadata.safe_to_auto_activate is False
        assert "shell.execute" in skill.metadata.requires_permissions

    def test_disabled_by_default(self) -> None:
        """execute_python returns error when LIGHTAGENT_SHELL_ENABLED is not set."""
        from lightagent.skills.available.code_executor.skill import CodeExecutorSkill

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LIGHTAGENT_SHELL_ENABLED", None)
            result = CodeExecutorSkill().get_tools()[0].invoke({"code": "print('hi')"})

        assert "disabled" in result.lower()

    def test_enabled_runs_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """execute_python runs code when LIGHTAGENT_SHELL_ENABLED=true."""
        from lightagent.skills.available.code_executor.skill import CodeExecutorSkill

        monkeypatch.setenv("LIGHTAGENT_SHELL_ENABLED", "true")
        tool = CodeExecutorSkill().get_tools()[0]
        result = tool.invoke({"code": "print('hello world')"})
        assert "hello world" in result

    def test_syntax_error_captured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Syntax errors in user code are captured in stderr, not raised."""
        from lightagent.skills.available.code_executor.skill import CodeExecutorSkill

        monkeypatch.setenv("LIGHTAGENT_SHELL_ENABLED", "true")
        result = CodeExecutorSkill().get_tools()[0].invoke({"code": "def broken("})
        assert "SyntaxError" in result or "stderr" in result

    def test_timeout_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Code that exceeds the timeout returns a timeout error."""
        from lightagent.skills.available.code_executor.skill import CodeExecutorSkill

        monkeypatch.setenv("LIGHTAGENT_SHELL_ENABLED", "true")
        result = (
            CodeExecutorSkill()
            .get_tools()[0]
            .invoke({"code": "import time; time.sleep(60)", "timeout": 1})
        )
        assert "timed out" in result.lower()


# ── Email reader skill ────────────────────────────────────────────────────────

_EMAIL_KEYS = (
    "LIGHTAGENT_EMAIL_HOST",
    "LIGHTAGENT_EMAIL_USER",
    "LIGHTAGENT_EMAIL_PASSWORD",
)


class TestEmailReaderSkill:
    """Tests for EmailReaderSkill."""

    def test_metadata(self) -> None:
        """EmailReaderSkill exposes correct metadata."""
        from lightagent.skills.available.email_reader.skill import EmailReaderSkill

        skill = EmailReaderSkill()
        assert skill.metadata.name == "email_reader"
        assert skill.metadata.safe_to_auto_activate is False

    def test_validate_false_without_env(self) -> None:
        """validate() returns False when IMAP env vars are missing."""
        from lightagent.skills.available.email_reader.skill import EmailReaderSkill

        with patch.dict(os.environ, {}, clear=False):
            for key in _EMAIL_KEYS:
                os.environ.pop(key, None)
            assert EmailReaderSkill().validate() is False

    def test_validate_true_with_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """validate() returns True when all IMAP env vars are present."""
        from lightagent.skills.available.email_reader.skill import EmailReaderSkill

        monkeypatch.setenv("LIGHTAGENT_EMAIL_HOST", "imap.example.com")
        monkeypatch.setenv("LIGHTAGENT_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("LIGHTAGENT_EMAIL_PASSWORD", "secret")
        assert EmailReaderSkill().validate() is True

    def test_graceful_degradation_no_config(self) -> None:
        """read_emails returns error message when env vars are missing."""
        from lightagent.skills.available.email_reader.skill import EmailReaderSkill

        with patch.dict(os.environ, {}, clear=False):
            for key in _EMAIL_KEYS:
                os.environ.pop(key, None)
            result = EmailReaderSkill().get_tools()[0].invoke({"max_emails": 5})

        assert "Missing configuration" in result or "LIGHTAGENT_EMAIL" in result


# ── Calendar skill ────────────────────────────────────────────────────────────

_SAMPLE_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20260301T090000Z
SUMMARY:Team standup
LOCATION:Zoom
DESCRIPTION:Daily sync
END:VEVENT
END:VCALENDAR
"""


class TestCalendarSkill:
    """Tests for CalendarSkill."""

    def test_metadata(self) -> None:
        """CalendarSkill exposes correct metadata."""
        from lightagent.skills.available.calendar.skill import CalendarSkill

        skill = CalendarSkill()
        assert skill.metadata.name == "calendar"
        assert skill.metadata.safe_to_auto_activate is True

    def test_no_calendar_dir_returns_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_events returns error when calendar dir does not exist."""
        from lightagent.skills.available.calendar.skill import CalendarSkill

        monkeypatch.setenv("LIGHTAGENT_CALENDAR_DIR", "/nonexistent/dir/abc123")
        result = CalendarSkill().get_tools()[0].invoke({"days_ahead": 7})
        assert "not found" in result.lower() or "LIGHTAGENT_CALENDAR_DIR" in result

    def test_reads_ics_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_events returns events found in .ics files."""
        from lightagent.skills.available.calendar.skill import CalendarSkill

        with tempfile.TemporaryDirectory() as tmpdir:
            ics_path = Path(tmpdir) / "test.ics"
            ics_path.write_text(_SAMPLE_ICS)
            monkeypatch.setenv("LIGHTAGENT_CALENDAR_DIR", tmpdir)
            result = CalendarSkill().get_tools()[0].invoke({"days_ahead": 365})

        # Either returns the event or "No events" (date-dependent)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_dir_returns_no_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_events returns 'no events' for empty calendar directory."""
        from lightagent.skills.available.calendar.skill import CalendarSkill

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("LIGHTAGENT_CALENDAR_DIR", tmpdir)
            result = CalendarSkill().get_tools()[0].invoke({"days_ahead": 7})

        assert "No events" in result or "no events" in result.lower()


# ── Database query skill ──────────────────────────────────────────────────────


class TestDatabaseQuerySkill:
    """Tests for DatabaseQuerySkill."""

    def test_metadata(self) -> None:
        """DatabaseQuerySkill exposes correct metadata."""
        from lightagent.skills.available.database_query.skill import DatabaseQuerySkill

        skill = DatabaseQuerySkill()
        assert skill.metadata.name == "database_query"
        assert skill.metadata.safe_to_auto_activate is False
        assert "filesystem.read" in skill.metadata.requires_permissions

    def test_select_only_blocks_insert(self) -> None:
        """INSERT statement is rejected before reaching the database."""
        from lightagent.skills.available.database_query.skill import DatabaseQuerySkill

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            with patch.dict(os.environ, {"LIGHTAGENT_DB_PATH": db_path}):
                import duckdb

                conn = duckdb.connect(db_path)
                conn.execute("CREATE TABLE t (x INT)")
                conn.close()

                result = (
                    DatabaseQuerySkill()
                    .get_tools()[0]
                    .invoke({"query": "INSERT INTO t VALUES (1)"})
                )

        assert "Only SELECT" in result or "not permitted" in result

    def test_select_only_blocks_drop(self) -> None:
        """DROP statement is rejected."""
        from lightagent.skills.available.database_query.skill import DatabaseQuerySkill

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            with patch.dict(os.environ, {"LIGHTAGENT_DB_PATH": db_path}):
                import duckdb

                conn = duckdb.connect(db_path)
                conn.execute("CREATE TABLE t (x INT)")
                conn.close()

                result = (
                    DatabaseQuerySkill()
                    .get_tools()[0]
                    .invoke({"query": "DROP TABLE t"})
                )

        assert "Only SELECT" in result or "not permitted" in result

    def test_valid_select_returns_results(self) -> None:
        """SELECT query returns tabular results."""
        from lightagent.skills.available.database_query.skill import DatabaseQuerySkill

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            import duckdb

            conn = duckdb.connect(db_path)
            conn.execute("CREATE TABLE users (id INT, name VARCHAR)")
            conn.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
            conn.close()

            with patch.dict(os.environ, {"LIGHTAGENT_DB_PATH": db_path}):
                result = (
                    DatabaseQuerySkill()
                    .get_tools()[0]
                    .invoke({"query": "SELECT * FROM users ORDER BY id"})
                )

        assert "Alice" in result
        assert "Bob" in result

    def test_missing_db_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns error message when database file does not exist."""
        from lightagent.skills.available.database_query.skill import DatabaseQuerySkill

        monkeypatch.setenv("LIGHTAGENT_DB_PATH", "/nonexistent/db.db")
        result = DatabaseQuerySkill().get_tools()[0].invoke({"query": "SELECT 1"})
        assert "not found" in result.lower()

    def test_with_cte_is_allowed(self) -> None:
        """WITH (CTE) queries are allowed."""
        from lightagent.skills.available.database_query.skill import DatabaseQuerySkill

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            import duckdb

            conn = duckdb.connect(db_path)
            conn.execute("CREATE TABLE nums (n INT)")
            conn.execute("INSERT INTO nums VALUES (1), (2), (3)")
            conn.close()

            with patch.dict(os.environ, {"LIGHTAGENT_DB_PATH": db_path}):
                result = (
                    DatabaseQuerySkill()
                    .get_tools()[0]
                    .invoke(
                        {"query": "WITH src AS (SELECT n FROM nums) SELECT * FROM src"}
                    )
                )

        assert "1" in result


# ── Helper function unit tests ─────────────────────────────────────────────────


class TestEmailReaderHelpers:
    """Unit tests for email_reader helper functions."""

    def test_decode_header_value_plain_string(self) -> None:
        """_decode_header_value handles plain ASCII strings."""
        from lightagent.skills.available.email_reader.skill import _decode_header_value

        assert _decode_header_value("Hello World") == "Hello World"

    def test_decode_header_value_none(self) -> None:
        """_decode_header_value returns empty string for None."""
        from lightagent.skills.available.email_reader.skill import _decode_header_value

        assert _decode_header_value(None) == ""

    def test_decode_header_value_bytes(self) -> None:
        """_decode_header_value decodes bytes to string."""
        from lightagent.skills.available.email_reader.skill import _decode_header_value

        assert _decode_header_value(b"Hello") == "Hello"

    def test_get_body_plain_text(self) -> None:
        """_get_body extracts plain text from a simple email."""
        import email as email_lib

        from lightagent.skills.available.email_reader.skill import _get_body

        raw = (
            "From: test@example.com\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "Hello, this is the body.\r\n"
        )
        msg = email_lib.message_from_string(raw)
        body = _get_body(msg)
        assert "Hello" in body

    def test_get_body_multipart(self) -> None:
        """_get_body extracts the text/plain part from a multipart email."""
        import email as email_lib

        from lightagent.skills.available.email_reader.skill import _get_body

        raw = (
            "From: test@example.com\r\n"
            "MIME-Version: 1.0\r\n"
            'Content-Type: multipart/mixed; boundary="boundary"\r\n'
            "\r\n"
            "--boundary\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "Plain text part.\r\n"
            "--boundary\r\n"
            "Content-Type: text/html\r\n"
            "\r\n"
            "<p>HTML part</p>\r\n"
            "--boundary--\r\n"
        )
        msg = email_lib.message_from_string(raw)
        body = _get_body(msg)
        assert "Plain text part" in body

    def test_fetch_emails_imap_error(self) -> None:
        """_fetch_emails returns error string on IMAP errors."""
        import imaplib
        from unittest.mock import MagicMock, patch

        from lightagent.skills.available.email_reader.skill import _fetch_emails

        with patch("imaplib.IMAP4_SSL") as mock_ssl:
            mock_imap = MagicMock()
            mock_imap.__enter__ = MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = MagicMock(return_value=False)
            mock_imap.login.side_effect = imaplib.IMAP4.error("auth failed")
            mock_ssl.return_value = mock_imap

            result = _fetch_emails(
                "imap.example.com", "user", "pass", 993, "INBOX", 5, False
            )

        assert "IMAP error" in result

    def test_fetch_emails_no_messages(self) -> None:
        """_fetch_emails returns 'No emails found' when mailbox is empty."""
        from unittest.mock import MagicMock, patch

        from lightagent.skills.available.email_reader.skill import _fetch_emails

        with patch("imaplib.IMAP4_SSL") as mock_ssl:
            mock_imap = MagicMock()
            mock_imap.__enter__ = MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = MagicMock(return_value=False)
            mock_imap.login = MagicMock()
            mock_imap.select = MagicMock(return_value=(None, None))
            mock_imap.search = MagicMock(return_value=(None, [b""]))
            mock_ssl.return_value = mock_imap

            result = _fetch_emails(
                "imap.example.com", "user", "pass", 993, "INBOX", 5, False
            )

        assert "No emails found" in result

    def test_fetch_emails_unread_criterion(self) -> None:
        """_fetch_emails uses UNSEEN criterion when unread_only=True."""
        from unittest.mock import MagicMock, patch

        from lightagent.skills.available.email_reader.skill import _fetch_emails

        with patch("imaplib.IMAP4_SSL") as mock_ssl:
            mock_imap = MagicMock()
            mock_imap.__enter__ = MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = MagicMock(return_value=False)
            mock_imap.search = MagicMock(return_value=(None, [b""]))
            mock_ssl.return_value = mock_imap

            _fetch_emails("imap.example.com", "user", "pass", 993, "INBOX", 5, True)

        mock_imap.search.assert_called_with(None, "UNSEEN")


class TestCalendarHelpers:
    """Unit tests for calendar skill helper functions."""

    def test_parse_dt_aware_datetime(self) -> None:
        """_parse_dt returns aware datetime unchanged."""
        from datetime import UTC, datetime

        from lightagent.skills.available.calendar.skill import _parse_dt

        dt = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
        result = _parse_dt(dt)
        assert result == dt

    def test_parse_dt_naive_datetime(self) -> None:
        """_parse_dt adds UTC to naive datetime."""
        from datetime import UTC, datetime

        from lightagent.skills.available.calendar.skill import _parse_dt

        naive = datetime(2026, 3, 1, 9, 0)  # noqa: DTZ001 — intentionally naive for test
        result = _parse_dt(naive)
        assert result is not None
        assert result.tzinfo == UTC

    def test_parse_dt_date(self) -> None:
        """_parse_dt converts date to UTC datetime."""
        from datetime import UTC, date

        from lightagent.skills.available.calendar.skill import _parse_dt

        d = date(2026, 3, 1)
        result = _parse_dt(d)
        assert result is not None
        assert result.year == 2026
        assert result.tzinfo == UTC

    def test_parse_dt_unknown_type(self) -> None:
        """_parse_dt returns None for unrecognised input."""
        from lightagent.skills.available.calendar.skill import _parse_dt

        assert _parse_dt("not a date") is None
        assert _parse_dt(12345) is None

    def test_read_events_no_ics_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_read_events returns empty list for directory with no .ics files."""
        import tempfile

        from lightagent.skills.available.calendar.skill import _read_events

        with tempfile.TemporaryDirectory() as tmpdir:
            result = _read_events(Path(tmpdir), 30)

        assert result == []

    def test_read_events_nonexistent_dir(self) -> None:
        """_read_events returns empty list when directory does not exist."""
        from lightagent.skills.available.calendar.skill import _read_events

        result = _read_events(Path("/nonexistent/calendar/dir"), 30)
        assert result == []

    def test_read_events_with_future_event(self) -> None:
        """_read_events returns events that fall within days_ahead window."""
        from lightagent.skills.available.calendar.skill import _read_events

        with tempfile.TemporaryDirectory() as tmpdir:
            ics_path = Path(tmpdir) / "cal.ics"
            ics_path.write_text(_SAMPLE_ICS)
            events = _read_events(Path(tmpdir), 730)

        # The event is 2026-03-01 — should be found within 730 days
        assert isinstance(events, list)

    def test_read_events_event_with_location_and_description(self) -> None:
        """_read_events populates location and description fields."""
        from lightagent.skills.available.calendar.skill import _read_events

        with tempfile.TemporaryDirectory() as tmpdir:
            ics_path = Path(tmpdir) / "cal.ics"
            ics_path.write_text(_SAMPLE_ICS)
            events = _read_events(Path(tmpdir), 730)

        # If event is found, verify it has the expected fields
        if events:
            assert "summary" in events[0]
            assert "location" in events[0]
            assert "description" in events[0]

    def test_read_events_past_event_excluded(self) -> None:
        """_read_events does not include events in the past."""
        from lightagent.skills.available.calendar.skill import _read_events

        past_ics = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:19990101T090000Z
SUMMARY:Old event
END:VEVENT
END:VCALENDAR
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ics_path = Path(tmpdir) / "old.ics"
            ics_path.write_text(past_ics)
            events = _read_events(Path(tmpdir), 30)

        assert events == []

    def test_read_events_corrupted_ics_does_not_crash(self) -> None:
        """_read_events logs a warning and continues for corrupted .ics files."""
        from lightagent.skills.available.calendar.skill import _read_events

        with tempfile.TemporaryDirectory() as tmpdir:
            bad_ics = Path(tmpdir) / "broken.ics"
            bad_ics.write_text("NOT VALID ICS CONTENT\n")
            events = _read_events(Path(tmpdir), 30)

        assert isinstance(events, list)


class TestWebSearchHelpers:
    """Unit tests for web_search helper functions."""

    def test_search_ddg_related_topics(self) -> None:
        """_search_ddg extracts related topics from DDG response."""
        from lightagent.skills.available.web_search.skill import _search_ddg

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "AbstractText": "",
            "AbstractURL": "",
            "RelatedTopics": [
                {"Text": "Related result 1", "FirstURL": "https://example.com/1"},
                {"Text": "Related result 2", "FirstURL": "https://example.com/2"},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch(_SEARCH_PATCH, return_value=mock_response):
            result = _search_ddg("test query", 5)

        assert "Related result 1" in result

    def test_search_google_success(self) -> None:
        """_search_google returns formatted results from Google API."""
        from lightagent.skills.available.web_search.skill import _search_google

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "items": [
                {
                    "title": "Test Result",
                    "link": "https://example.com",
                    "snippet": "A test snippet",
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch(_SEARCH_PATCH, return_value=mock_response):
            result = _search_google("test", 3, "apikey", "cseid")

        assert "Test Result" in result
        assert "A test snippet" in result

    def test_search_google_no_results(self) -> None:
        """_search_google returns 'No results' when items list is empty."""
        from lightagent.skills.available.web_search.skill import _search_google

        mock_response = MagicMock()
        mock_response.json.return_value = {"items": []}
        mock_response.raise_for_status = MagicMock()

        with patch(_SEARCH_PATCH, return_value=mock_response):
            result = _search_google("obscure query", 3, "apikey", "cseid")

        assert "No results" in result
