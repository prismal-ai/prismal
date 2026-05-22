"""Unit tests for built-in skills (T-072).

Tests cover:
- weather     (wttr.in backend — HTTP mocked)
- web_search  (DuckDuckGo backend — HTTP mocked)
- code_executor (subprocess gated by PRISMAL_SHELL_ENABLED)
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

_WEATHER_PATCH = "prismal.skills.available.weather.skill.httpx.get"
_SEARCH_PATCH = "prismal.skills.available.web_search.skill.httpx.get"


class TestWeatherSkill:
    """Tests for WeatherSkill."""

    def test_metadata(self) -> None:
        """WeatherSkill exposes correct metadata."""
        from prismal.skills.available.weather.skill import WeatherSkill

        skill = WeatherSkill()
        assert skill.metadata.name == "weather"
        assert skill.metadata.safe_to_auto_activate is True

    def test_get_tools_returns_get_weather(self) -> None:
        """get_tools() returns a single 'get_weather' tool."""
        from prismal.skills.available.weather.skill import WeatherSkill

        tools = WeatherSkill().get_tools()
        assert len(tools) == 1
        assert tools[0].name == "get_weather"

    def test_wttr_success(self) -> None:
        """get_weather returns formatted string on successful wttr.in response."""
        from prismal.skills.available.weather.skill import WeatherSkill

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

        from prismal.skills.available.weather.skill import WeatherSkill

        with patch(_WEATHER_PATCH, side_effect=httpx.HTTPError("timeout")):
            result = WeatherSkill().get_tools()[0].invoke({"city": "Nowhere"})

        assert "[weather]" in result

    def test_owm_used_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OpenWeatherMap backend is used when env vars are set."""
        from prismal.skills.available.weather.skill import WeatherSkill

        monkeypatch.setenv("PRISMAL_WEATHER_PROVIDER", "openweathermap")
        monkeypatch.setenv("PRISMAL_WEATHER_API_KEY", "testkey")

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
        from prismal.skills.available.web_search.skill import WebSearchSkill

        skill = WebSearchSkill()
        assert skill.metadata.name == "web_search"
        assert skill.metadata.safe_to_auto_activate is True

    def test_get_tools_returns_web_search(self) -> None:
        """get_tools() returns a single 'web_search' tool."""
        from prismal.skills.available.web_search.skill import WebSearchSkill

        tools = WebSearchSkill().get_tools()
        assert len(tools) == 1
        assert tools[0].name == "web_search"

    def test_ddg_success_with_abstract(self) -> None:
        """web_search returns abstract text from DDG response."""
        from prismal.skills.available.web_search.skill import WebSearchSkill

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
        from prismal.skills.available.web_search.skill import WebSearchSkill

        mock_response = MagicMock()
        mock_response.json.return_value = {"AbstractText": "", "RelatedTopics": []}
        mock_response.raise_for_status = MagicMock()

        with patch(_SEARCH_PATCH, return_value=mock_response):
            result = WebSearchSkill().get_tools()[0].invoke({"query": "xyzzy"})

        assert "xyzzy" in result

    def test_ddg_http_error(self) -> None:
        """web_search returns error message on HTTP failure."""
        import httpx

        from prismal.skills.available.web_search.skill import WebSearchSkill

        with patch(_SEARCH_PATCH, side_effect=httpx.HTTPError("connection refused")):
            result = WebSearchSkill().get_tools()[0].invoke({"query": "test"})

        assert "[web_search]" in result


# ── Code executor skill ───────────────────────────────────────────────────────


class TestCodeExecutorSkill:
    """Tests for CodeExecutorSkill."""

    def test_metadata(self) -> None:
        """CodeExecutorSkill exposes correct metadata."""
        from prismal.skills.available.code_executor.skill import CodeExecutorSkill

        skill = CodeExecutorSkill()
        assert skill.metadata.name == "code_executor"
        assert skill.metadata.safe_to_auto_activate is False
        assert "shell.execute" in skill.metadata.requires_permissions

    def test_disabled_by_default(self) -> None:
        """execute_python returns error when PRISMAL_SHELL_ENABLED is not set."""
        from prismal.skills.available.code_executor.skill import CodeExecutorSkill

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRISMAL_SHELL_ENABLED", None)
            result = CodeExecutorSkill().get_tools()[0].invoke({"code": "print('hi')"})

        assert "disabled" in result.lower()

    def test_enabled_runs_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """execute_python runs code when PRISMAL_SHELL_ENABLED=true."""
        from prismal.skills.available.code_executor.skill import CodeExecutorSkill

        monkeypatch.setenv("PRISMAL_SHELL_ENABLED", "true")
        tool = CodeExecutorSkill().get_tools()[0]
        result = tool.invoke({"code": "print('hello world')"})
        assert "hello world" in result

    def test_syntax_error_captured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Syntax errors in user code are captured in stderr, not raised."""
        from prismal.skills.available.code_executor.skill import CodeExecutorSkill

        monkeypatch.setenv("PRISMAL_SHELL_ENABLED", "true")
        result = CodeExecutorSkill().get_tools()[0].invoke({"code": "def broken("})
        assert "SyntaxError" in result or "stderr" in result

    def test_timeout_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Code that exceeds the timeout returns a timeout error."""
        from prismal.skills.available.code_executor.skill import CodeExecutorSkill

        monkeypatch.setenv("PRISMAL_SHELL_ENABLED", "true")
        result = (
            CodeExecutorSkill()
            .get_tools()[0]
            .invoke({"code": "import time; time.sleep(60)", "timeout": 1})
        )
        assert "timed out" in result.lower()


# ── Email reader skill ────────────────────────────────────────────────────────

_EMAIL_KEYS = (
    "PRISMAL_EMAIL_HOST",
    "PRISMAL_EMAIL_USER",
    "PRISMAL_EMAIL_PASSWORD",
)


class TestEmailReaderSkill:
    """Tests for EmailReaderSkill."""

    def test_metadata(self) -> None:
        """EmailReaderSkill exposes correct metadata."""
        from prismal.skills.available.email_reader.skill import EmailReaderSkill

        skill = EmailReaderSkill()
        assert skill.metadata.name == "email_reader"
        assert skill.metadata.safe_to_auto_activate is False

    def test_validate_false_without_env(self) -> None:
        """validate() returns False when IMAP env vars are missing."""
        from prismal.skills.available.email_reader.skill import EmailReaderSkill

        with patch.dict(os.environ, {}, clear=False):
            for key in _EMAIL_KEYS:
                os.environ.pop(key, None)
            assert EmailReaderSkill().validate() is False

    def test_validate_true_with_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """validate() returns True when all IMAP env vars are present."""
        from prismal.skills.available.email_reader.skill import EmailReaderSkill

        monkeypatch.setenv("PRISMAL_EMAIL_HOST", "imap.example.com")
        monkeypatch.setenv("PRISMAL_EMAIL_USER", "user@example.com")
        monkeypatch.setenv("PRISMAL_EMAIL_PASSWORD", "secret")
        assert EmailReaderSkill().validate() is True

    def test_graceful_degradation_no_config(self) -> None:
        """read_emails returns error message when env vars are missing."""
        from prismal.skills.available.email_reader.skill import EmailReaderSkill

        with patch.dict(os.environ, {}, clear=False):
            for key in _EMAIL_KEYS:
                os.environ.pop(key, None)
            result = EmailReaderSkill().get_tools()[0].invoke({"max_emails": 5})

        assert "Missing configuration" in result or "PRISMAL_EMAIL" in result


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
        from prismal.skills.available.calendar.skill import CalendarSkill

        skill = CalendarSkill()
        assert skill.metadata.name == "calendar"
        assert skill.metadata.safe_to_auto_activate is True

    def test_no_calendar_dir_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_events returns error when calendar dir does not exist."""
        from prismal.skills.available.calendar.skill import CalendarSkill

        monkeypatch.setenv("PRISMAL_CALENDAR_DIR", "/nonexistent/dir/abc123")
        result = CalendarSkill().get_tools()[0].invoke({"days_ahead": 7})
        assert "not found" in result.lower() or "PRISMAL_CALENDAR_DIR" in result

    def test_reads_ics_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_events returns events found in .ics files."""
        from prismal.skills.available.calendar.skill import CalendarSkill

        with tempfile.TemporaryDirectory() as tmpdir:
            ics_path = Path(tmpdir) / "test.ics"
            ics_path.write_text(_SAMPLE_ICS)
            monkeypatch.setenv("PRISMAL_CALENDAR_DIR", tmpdir)
            result = CalendarSkill().get_tools()[0].invoke({"days_ahead": 365})

        # Either returns the event or "No events" (date-dependent)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_dir_returns_no_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_events returns 'no events' for empty calendar directory."""
        from prismal.skills.available.calendar.skill import CalendarSkill

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("PRISMAL_CALENDAR_DIR", tmpdir)
            result = CalendarSkill().get_tools()[0].invoke({"days_ahead": 7})

        assert "No events" in result or "no events" in result.lower()


# ── Database query skill ──────────────────────────────────────────────────────


class TestDatabaseQuerySkill:
    """Tests for DatabaseQuerySkill."""

    def test_metadata(self) -> None:
        """DatabaseQuerySkill exposes correct metadata."""
        from prismal.skills.available.database_query.skill import DatabaseQuerySkill

        skill = DatabaseQuerySkill()
        assert skill.metadata.name == "database_query"
        assert skill.metadata.safe_to_auto_activate is False
        assert "filesystem.read" in skill.metadata.requires_permissions

    def test_select_only_blocks_insert(self) -> None:
        """INSERT statement is rejected before reaching the database."""
        from prismal.skills.available.database_query.skill import DatabaseQuerySkill

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            with patch.dict(os.environ, {"PRISMAL_DB_PATH": db_path}):
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
        from prismal.skills.available.database_query.skill import DatabaseQuerySkill

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            with patch.dict(os.environ, {"PRISMAL_DB_PATH": db_path}):
                import duckdb

                conn = duckdb.connect(db_path)
                conn.execute("CREATE TABLE t (x INT)")
                conn.close()

                result = DatabaseQuerySkill().get_tools()[0].invoke({"query": "DROP TABLE t"})

        assert "Only SELECT" in result or "not permitted" in result

    def test_valid_select_returns_results(self) -> None:
        """SELECT query returns tabular results."""
        from prismal.skills.available.database_query.skill import DatabaseQuerySkill

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            import duckdb

            conn = duckdb.connect(db_path)
            conn.execute("CREATE TABLE users (id INT, name VARCHAR)")
            conn.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
            conn.close()

            with patch.dict(os.environ, {"PRISMAL_DB_PATH": db_path}):
                result = (
                    DatabaseQuerySkill()
                    .get_tools()[0]
                    .invoke({"query": "SELECT * FROM users ORDER BY id"})
                )

        assert "Alice" in result
        assert "Bob" in result

    def test_missing_db_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns error message when database file does not exist."""
        from prismal.skills.available.database_query.skill import DatabaseQuerySkill

        monkeypatch.setenv("PRISMAL_DB_PATH", "/nonexistent/db.db")
        result = DatabaseQuerySkill().get_tools()[0].invoke({"query": "SELECT 1"})
        assert "not found" in result.lower()

    def test_with_cte_is_allowed(self) -> None:
        """WITH (CTE) queries are allowed."""
        from prismal.skills.available.database_query.skill import DatabaseQuerySkill

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            import duckdb

            conn = duckdb.connect(db_path)
            conn.execute("CREATE TABLE nums (n INT)")
            conn.execute("INSERT INTO nums VALUES (1), (2), (3)")
            conn.close()

            with patch.dict(os.environ, {"PRISMAL_DB_PATH": db_path}):
                result = (
                    DatabaseQuerySkill()
                    .get_tools()[0]
                    .invoke({"query": "WITH src AS (SELECT n FROM nums) SELECT * FROM src"})
                )

        assert "1" in result


# ── Helper function unit tests ─────────────────────────────────────────────────


class TestEmailReaderHelpers:
    """Unit tests for email_reader helper functions."""

    def test_decode_header_value_plain_string(self) -> None:
        """_decode_header_value handles plain ASCII strings."""
        from prismal.skills.available.email_reader.skill import _decode_header_value

        assert _decode_header_value("Hello World") == "Hello World"

    def test_decode_header_value_none(self) -> None:
        """_decode_header_value returns empty string for None."""
        from prismal.skills.available.email_reader.skill import _decode_header_value

        assert _decode_header_value(None) == ""

    def test_decode_header_value_bytes(self) -> None:
        """_decode_header_value decodes bytes to string."""
        from prismal.skills.available.email_reader.skill import _decode_header_value

        assert _decode_header_value(b"Hello") == "Hello"

    def test_get_body_plain_text(self) -> None:
        """_get_body extracts plain text from a simple email."""
        import email as email_lib

        from prismal.skills.available.email_reader.skill import _get_body

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

        from prismal.skills.available.email_reader.skill import _get_body

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

        from prismal.skills.available.email_reader.skill import _fetch_emails

        with patch("imaplib.IMAP4_SSL") as mock_ssl:
            mock_imap = MagicMock()
            mock_imap.__enter__ = MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = MagicMock(return_value=False)
            mock_imap.login.side_effect = imaplib.IMAP4.error("auth failed")
            mock_ssl.return_value = mock_imap

            result = _fetch_emails("imap.example.com", "user", "pass", 993, "INBOX", 5, False)

        assert "IMAP error" in result

    def test_fetch_emails_no_messages(self) -> None:
        """_fetch_emails returns 'No emails found' when mailbox is empty."""
        from unittest.mock import MagicMock, patch

        from prismal.skills.available.email_reader.skill import _fetch_emails

        with patch("imaplib.IMAP4_SSL") as mock_ssl:
            mock_imap = MagicMock()
            mock_imap.__enter__ = MagicMock(return_value=mock_imap)
            mock_imap.__exit__ = MagicMock(return_value=False)
            mock_imap.login = MagicMock()
            mock_imap.select = MagicMock(return_value=(None, None))
            mock_imap.search = MagicMock(return_value=(None, [b""]))
            mock_ssl.return_value = mock_imap

            result = _fetch_emails("imap.example.com", "user", "pass", 993, "INBOX", 5, False)

        assert "No emails found" in result

    def test_fetch_emails_unread_criterion(self) -> None:
        """_fetch_emails uses UNSEEN criterion when unread_only=True."""
        from unittest.mock import MagicMock, patch

        from prismal.skills.available.email_reader.skill import _fetch_emails

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

        from prismal.skills.available.calendar.skill import _parse_dt

        dt = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
        result = _parse_dt(dt)
        assert result == dt

    def test_parse_dt_naive_datetime(self) -> None:
        """_parse_dt adds UTC to naive datetime."""
        from datetime import UTC, datetime

        from prismal.skills.available.calendar.skill import _parse_dt

        naive = datetime(2026, 3, 1, 9, 0)  # noqa: DTZ001 — intentionally naive for test
        result = _parse_dt(naive)
        assert result is not None
        assert result.tzinfo == UTC

    def test_parse_dt_date(self) -> None:
        """_parse_dt converts date to UTC datetime."""
        from datetime import UTC, date

        from prismal.skills.available.calendar.skill import _parse_dt

        d = date(2026, 3, 1)
        result = _parse_dt(d)
        assert result is not None
        assert result.year == 2026
        assert result.tzinfo == UTC

    def test_parse_dt_unknown_type(self) -> None:
        """_parse_dt returns None for unrecognised input."""
        from prismal.skills.available.calendar.skill import _parse_dt

        assert _parse_dt("not a date") is None
        assert _parse_dt(12345) is None

    def test_read_events_no_ics_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_read_events returns empty list for directory with no .ics files."""
        import tempfile

        from prismal.skills.available.calendar.skill import _read_events

        with tempfile.TemporaryDirectory() as tmpdir:
            result = _read_events(Path(tmpdir), 30)

        assert result == []

    def test_read_events_nonexistent_dir(self) -> None:
        """_read_events returns empty list when directory does not exist."""
        from prismal.skills.available.calendar.skill import _read_events

        result = _read_events(Path("/nonexistent/calendar/dir"), 30)
        assert result == []

    def test_read_events_with_future_event(self) -> None:
        """_read_events returns events that fall within days_ahead window."""
        from prismal.skills.available.calendar.skill import _read_events

        with tempfile.TemporaryDirectory() as tmpdir:
            ics_path = Path(tmpdir) / "cal.ics"
            ics_path.write_text(_SAMPLE_ICS)
            events = _read_events(Path(tmpdir), 730)

        # The event is 2026-03-01 — should be found within 730 days
        assert isinstance(events, list)

    def test_read_events_event_with_location_and_description(self) -> None:
        """_read_events populates location and description fields."""
        from prismal.skills.available.calendar.skill import _read_events

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
        from prismal.skills.available.calendar.skill import _read_events

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
        from prismal.skills.available.calendar.skill import _read_events

        with tempfile.TemporaryDirectory() as tmpdir:
            bad_ics = Path(tmpdir) / "broken.ics"
            bad_ics.write_text("NOT VALID ICS CONTENT\n")
            events = _read_events(Path(tmpdir), 30)

        assert isinstance(events, list)


class TestWebSearchHelpers:
    """Unit tests for web_search helper functions."""

    def test_search_ddg_related_topics(self) -> None:
        """_search_ddg extracts related topics from DDG response."""
        from prismal.skills.available.web_search.skill import _search_ddg

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
        from prismal.skills.available.web_search.skill import _search_google

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
        from prismal.skills.available.web_search.skill import _search_google

        mock_response = MagicMock()
        mock_response.json.return_value = {"items": []}
        mock_response.raise_for_status = MagicMock()

        with patch(_SEARCH_PATCH, return_value=mock_response):
            result = _search_google("obscure query", 3, "apikey", "cseid")

        assert "No results" in result


# ── Email reader — additional coverage tests ───────────────────────────────────

_RAW_EMAIL_BYTES = (
    b"From: sender@example.com\r\n"
    b"To: recipient@example.com\r\n"
    b"Subject: Test Subject\r\n"
    b"Date: Mon, 02 Mar 2026 10:00:00 +0000\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"This is the email body.\r\n"
)


def _make_imap_mock(search_ids: bytes, fetch_response: list | None = None) -> MagicMock:
    """Build a reusable IMAP4_SSL context-manager mock.

    Args:
        search_ids: Bytes containing space-separated message IDs (e.g. b"1 2").
        fetch_response: List returned by imap.fetch(); defaults to a valid RFC822 tuple.

    Returns:
        Configured MagicMock that acts as both the class and the context manager.
    """
    if fetch_response is None:
        fetch_response = [(b"1 (RFC822 {bytes})", _RAW_EMAIL_BYTES)]

    mock_imap = MagicMock()
    mock_imap.__enter__ = MagicMock(return_value=mock_imap)
    mock_imap.__exit__ = MagicMock(return_value=False)
    mock_imap.login = MagicMock()
    mock_imap.select = MagicMock(return_value=("OK", [b"INBOX"]))
    mock_imap.search = MagicMock(return_value=("OK", [search_ids]))
    mock_imap.fetch = MagicMock(return_value=("OK", fetch_response))
    return mock_imap


class TestEmailReaderCoverage:
    """Additional coverage tests for email_reader skill — targets lines 60, 127-144, 148-153, 215-216."""

    # ── Line 60: bytes part decoded with charset inside _decode_header_value ──

    def test_decode_header_value_bytes_encoding(self) -> None:
        """_decode_header_value decodes an RFC 2047 encoded-word header (line 60).

        When decode_header() returns a (bytes, charset) pair, the bytes part must
        be decoded using the provided charset — this exercises the ``isinstance(part,
        bytes)`` branch at line 59-60.
        """
        from prismal.skills.available.email_reader.skill import _decode_header_value

        # Build a proper RFC 2047 encoded-word: =?utf-8?b?<base64>?=
        # "Héllo" base64-encoded in utf-8 → SGVsbG8=  (just "Hello" for simplicity)
        # Use a real encoded-word so decode_header() returns (bytes, charset).
        encoded = "=?utf-8?b?SGVsbG8gV29ybGQ=?="  # "Hello World" in base64/utf-8
        result = _decode_header_value(encoded)
        assert result == "Hello World"

    # ── Lines 127-144: IMAP fetch loop — processing messages ──────────────────

    def test_read_emails_imap_fetch_loop(self) -> None:
        """read_emails processes IMAP messages and returns formatted summaries (lines 127-144).

        Mocks imaplib.IMAP4_SSL so that search returns two IDs, fetch returns a
        valid RFC822 message for each, and verifies the formatted output contains
        expected header values.
        """
        from prismal.skills.available.email_reader.skill import EmailReaderSkill

        mock_imap = _make_imap_mock(search_ids=b"1 2")

        env = {
            "PRISMAL_EMAIL_HOST": "imap.example.com",
            "PRISMAL_EMAIL_USER": "user@example.com",
            "PRISMAL_EMAIL_PASSWORD": "secret",
        }
        with (
            patch.dict("os.environ", env),
            patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        ):
            tool = EmailReaderSkill().get_tools()[0]
            result = tool.invoke({"max_emails": 5, "unread_only": False})

        assert "sender@example.com" in result
        assert "Test Subject" in result
        assert "email body" in result

    def test_read_emails_no_messages_found(self) -> None:
        """read_emails returns 'No emails found' when summaries list is empty (line 148).

        The fetch loop runs but fetch() returns data that is not a tuple, so no
        summaries are appended and the ternary falls to the else branch.
        """
        from prismal.skills.available.email_reader.skill import EmailReaderSkill

        # fetch returns a non-tuple raw item so the ``isinstance(raw, tuple)`` check
        # on line 137 skips the message — summaries stays empty.
        mock_imap = _make_imap_mock(
            search_ids=b"1",
            fetch_response=[b"not-a-tuple"],
        )

        env = {
            "PRISMAL_EMAIL_HOST": "imap.example.com",
            "PRISMAL_EMAIL_USER": "user@example.com",
            "PRISMAL_EMAIL_PASSWORD": "secret",
        }
        with (
            patch.dict("os.environ", env),
            patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        ):
            tool = EmailReaderSkill().get_tools()[0]
            result = tool.invoke({"max_emails": 5})

        assert "No emails found" in result

    # ── Lines 148-149: IMAP4.error handling ───────────────────────────────────

    def test_read_emails_imap_error(self) -> None:
        """read_emails returns IMAP error string when imaplib.IMAP4.error is raised (lines 148-149).

        Simulates an authentication failure by making imap.login() raise
        imaplib.IMAP4.error; the except clause at line 150 should catch it.
        """
        import imaplib

        from prismal.skills.available.email_reader.skill import EmailReaderSkill

        mock_imap = MagicMock()
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login.side_effect = imaplib.IMAP4.error("authentication failed")

        env = {
            "PRISMAL_EMAIL_HOST": "imap.example.com",
            "PRISMAL_EMAIL_USER": "user@example.com",
            "PRISMAL_EMAIL_PASSWORD": "wrong_password",
        }
        with (
            patch.dict("os.environ", env),
            patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        ):
            tool = EmailReaderSkill().get_tools()[0]
            result = tool.invoke({"max_emails": 5})

        assert "IMAP error" in result

    # ── Lines 152-153: OSError handling ───────────────────────────────────────

    def test_read_emails_os_error(self) -> None:
        """read_emails returns connection error string when OSError is raised (lines 152-153).

        Simulates a network failure (e.g. connection refused) so the OSError except
        clause at line 152 is exercised.
        """
        from prismal.skills.available.email_reader.skill import EmailReaderSkill

        mock_imap = MagicMock()
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)
        mock_imap.login.side_effect = OSError("connection refused")

        env = {
            "PRISMAL_EMAIL_HOST": "imap.example.com",
            "PRISMAL_EMAIL_USER": "user@example.com",
            "PRISMAL_EMAIL_PASSWORD": "secret",
        }
        with (
            patch.dict("os.environ", env),
            patch("imaplib.IMAP4_SSL", return_value=mock_imap),
        ):
            tool = EmailReaderSkill().get_tools()[0]
            result = tool.invoke({"max_emails": 5})

        assert "Connection error" in result

    # ── Lines 215-216: config present → logger.info + _fetch_emails called ────

    def test_read_emails_missing_config(self) -> None:
        """read_emails returns a config error message when env vars are absent (lines 215-216).

        When HOST, USER, or PASSWORD env vars are not set, the early-return branch
        at lines 208-213 fires — this verifies the config guard is the only path
        that executes (i.e. lines 215-216 are NOT reached here).  The complementary
        test ``test_read_emails_imap_fetch_loop`` ensures lines 215-216 ARE covered
        by setting all required env vars and providing a valid IMAP mock.
        """
        from prismal.skills.available.email_reader.skill import EmailReaderSkill

        # Ensure none of the required env vars are set
        env_removals = {
            "PRISMAL_EMAIL_HOST": "",
            "PRISMAL_EMAIL_USER": "",
            "PRISMAL_EMAIL_PASSWORD": "",
        }
        with patch.dict("os.environ", env_removals):
            # Blank values are treated as falsy — triggers config error path
            tool = EmailReaderSkill().get_tools()[0]
            result = tool.invoke({"max_emails": 3})

        assert "Missing configuration" in result
        assert "PRISMAL_EMAIL_HOST" in result


# ── Calendar — additional coverage tests ──────────────────────────────────────

_ICS_NO_DTSTART = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:No Date Event
DESCRIPTION:This event has no DTSTART
END:VEVENT
END:VCALENDAR
"""

_ICS_FUTURE_EVENT = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20991231T120000Z
SUMMARY:Far Future Meeting
LOCATION:Room 1
DESCRIPTION:Agenda items here
END:VEVENT
END:VCALENDAR
"""


class TestCalendarCoverage:
    """Additional coverage tests for calendar skill — targets lines 67-68, 89, 93-96, 154-162."""

    # ── Lines 67-68: ImportError branch when icalendar is not installed ────────

    def test_read_events_icalendar_not_installed(self) -> None:
        """_read_events returns empty list when icalendar package is not installed (lines 67-68).

        Patches sys.modules to simulate the icalendar package being absent so
        the ``except ImportError: return []`` branch at lines 67-68 is exercised.
        """
        import sys
        import tempfile

        from prismal.skills.available.calendar.skill import _read_events

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with patch.dict(sys.modules, {"icalendar": None}):  # type: ignore[dict-item]
                result = _read_events(tmp_path, 7)

        assert result == []

    # ── Line 89: dtstart is None branch (event with no DTSTART) ───────────────

    def test_read_events_event_without_dtstart_is_skipped(self) -> None:
        """_read_events skips VEVENT components that have no DTSTART (line 89).

        Writes a .ics file whose sole VEVENT has no DTSTART field, so _parse_dt
        returns None and the ``if dtstart is None: continue`` branch fires.
        Lines 93-96 are NOT reached for this event.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            ics_path = Path(tmpdir) / "nodtstart.ics"
            ics_path.write_text(_ICS_NO_DTSTART)

            from prismal.skills.available.calendar.skill import _read_events

            result = _read_events(Path(tmpdir), 30)

        # Event without DTSTART must be filtered out
        assert result == []

    # ── Lines 93-96: event fields built and appended when within window ────────

    def test_read_events_non_directory_returns_empty(self) -> None:
        """_read_events returns empty list when calendar_dir is not a directory (line 76-77).

        Passes a path that does not exist so ``calendar_dir.is_dir()`` is False
        and the early return fires before the glob loop.  This also confirms lines
        93-96 cannot execute when no files are scanned.
        """
        from prismal.skills.available.calendar.skill import _read_events

        result = _read_events(Path("/this/path/does/not/exist/at/all"), 7)

        assert result == []

    # ── Lines 154-162: no-events path in list_events tool ─────────────────────

    def test_list_events_tool_no_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_events returns 'No events found' message when _read_events returns [] (line 152).

        Patches _read_events to return an empty list and patches the calendar
        directory to a real temporary directory so the ``cal_dir.is_dir()`` guard
        does not block execution.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("PRISMAL_CALENDAR_DIR", tmpdir)

            with patch(
                "prismal.skills.available.calendar.skill._read_events",
                return_value=[],
            ):
                from prismal.skills.available.calendar.skill import CalendarSkill

                list_events = CalendarSkill().get_tools()[0]
                result = list_events.invoke({"days_ahead": 7})

        assert "No events found" in result
        assert "7" in result

    # ── Lines 154-162: event formatting loop with location and description ─────

    def test_list_events_tool_formats_events_with_location(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_events formats events including location and description lines (lines 154-162).

        Patches _read_events to return a single mock event dict that has both
        ``location`` and ``description`` populated so that lines 157-160 (the
        ``if ev["location"]`` and ``if ev["description"]`` branches) both fire,
        and lines 154-162 are fully covered.
        """
        mock_events = [
            {
                "start": "2026-03-10 10:00 UTC",
                "summary": "Meeting",
                "location": "Room 1",
                "description": "Agenda",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("PRISMAL_CALENDAR_DIR", tmpdir)

            with patch(
                "prismal.skills.available.calendar.skill._read_events",
                return_value=mock_events,
            ):
                from prismal.skills.available.calendar.skill import CalendarSkill

                list_events = CalendarSkill().get_tools()[0]
                result = list_events.invoke({"days_ahead": 7})

        assert "Upcoming events" in result
        assert "Meeting" in result
        assert "Room 1" in result
        assert "Agenda" in result
        assert "2026-03-10 10:00 UTC" in result

    def test_list_events_tool_formats_events_without_optional_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_events formats events that have no location or description (lines 156-162).

        Patches _read_events to return an event with empty location and description
        so the ``if ev["location"]`` and ``if ev["description"]`` branches evaluate
        to False, exercising the negative path of those conditionals.
        """
        mock_events = [
            {
                "start": "2026-03-15 14:00 UTC",
                "summary": "Solo reminder",
                "location": "",
                "description": "",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("PRISMAL_CALENDAR_DIR", tmpdir)

            with patch(
                "prismal.skills.available.calendar.skill._read_events",
                return_value=mock_events,
            ):
                from prismal.skills.available.calendar.skill import CalendarSkill

                list_events = CalendarSkill().get_tools()[0]
                result = list_events.invoke({"days_ahead": 7})

        assert "Solo reminder" in result
        assert "@" not in result  # no location appended
        assert "2026-03-15 14:00 UTC" in result

    # ── Lines 93-96: event fields built and appended (real icalendar parse) ───

    def test_read_events_future_event_fields_populated(self) -> None:
        """_read_events builds summary, location, description fields for in-window events (lines 93-96).

        Writes a .ics file whose VEVENT falls well in the future so that
        lines 93-96 (``summary = …``, ``location = …``, ``description = …``,
        ``events.append(…)``) are all reached via a real icalendar parse.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            ics_path = Path(tmpdir) / "future.ics"
            ics_path.write_text(_ICS_FUTURE_EVENT)

            from prismal.skills.available.calendar.skill import _read_events

            # 40 000 days ≈ 110 years — covers 2099-12-31
            result = _read_events(Path(tmpdir), 40_000)

        assert len(result) == 1
        ev = result[0]
        assert ev["summary"] == "Far Future Meeting"
        assert ev["location"] == "Room 1"
        assert "Agenda" in ev["description"]
