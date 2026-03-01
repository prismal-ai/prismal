"""Unit tests for lightagent.monitoring.cost_tracker — CostTracker, models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lightagent.monitoring.cost_tracker import CostEntry, CostTracker, UsageSummary


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def tracker(tmp_path: Path) -> CostTracker:
    """Return a CostTracker backed by a fresh in-memory SQLite file."""
    return CostTracker(db_path=tmp_path / "cost_test.db")


# ── Pydantic models ───────────────────────────────────────────────────────────


def test_cost_entry_model_validates() -> None:
    """CostEntry model accepts valid fields."""
    entry = CostEntry(
        id="e-001",
        session_id="sess-1",
        user_id="user-1",
        model="claude-sonnet-4-5",
        tokens_in=500,
        tokens_out=200,
        cost_usd=0.003,
        created_at=datetime.now(UTC),
    )
    assert entry.tokens_in == 500
    assert entry.model == "claude-sonnet-4-5"


def test_usage_summary_model_validates() -> None:
    """UsageSummary model accepts aggregated stats."""
    summary = UsageSummary(
        total_tokens_in=1000,
        total_tokens_out=400,
        total_cost_usd=0.01,
        request_count=3,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC),
    )
    assert summary.request_count == 3
    assert summary.total_cost_usd == pytest.approx(0.01)


def test_usage_summary_nullable_dates() -> None:
    """UsageSummary period_start and period_end can be None."""
    summary = UsageSummary(
        total_tokens_in=0,
        total_tokens_out=0,
        total_cost_usd=0.0,
        request_count=0,
        period_start=None,
        period_end=None,
    )
    assert summary.period_start is None
    assert summary.period_end is None


# ── CostTracker.record ─────────────────────────────────────────────────────────


def test_record_returns_uuid(tracker: CostTracker) -> None:
    """record() returns a valid UUID4 string."""
    entry_id = tracker.record(
        session_id="sess-1",
        model="gpt-4o-mini",
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
    )
    assert len(entry_id) == 36
    assert "-" in entry_id


def test_record_with_user_id(tracker: CostTracker) -> None:
    """record() accepts an optional user_id."""
    entry_id = tracker.record(
        session_id="sess-2",
        model="gpt-4o-mini",
        tokens_in=200,
        tokens_out=80,
        cost_usd=0.002,
        user_id="user-42",
    )
    assert entry_id is not None
    entries = tracker.list_entries(user_id="user-42")
    assert len(entries) == 1
    assert entries[0].user_id == "user-42"


# ── CostTracker.get_summary ────────────────────────────────────────────────────


def test_get_summary_empty_returns_zeroes(tracker: CostTracker) -> None:
    """get_summary on empty DB returns a zero-value summary."""
    summary = tracker.get_summary()
    assert summary.request_count == 0
    assert summary.total_cost_usd == 0.0
    assert summary.period_start is None


def test_get_summary_aggregates_correctly(tracker: CostTracker) -> None:
    """get_summary sums tokens and cost across all entries."""
    tracker.record("s1", "m1", 100, 50, 0.01)
    tracker.record("s2", "m1", 200, 80, 0.02)

    summary = tracker.get_summary()
    assert summary.request_count == 2
    assert summary.total_tokens_in == 300
    assert summary.total_tokens_out == 130
    assert summary.total_cost_usd == pytest.approx(0.03)


def test_get_summary_filters_by_user(tracker: CostTracker) -> None:
    """get_summary restricts to a specific user_id when provided."""
    tracker.record("s1", "m1", 100, 50, 0.01, user_id="user-a")
    tracker.record("s2", "m1", 200, 80, 0.02, user_id="user-b")

    summary_a = tracker.get_summary(user_id="user-a")
    assert summary_a.request_count == 1
    assert summary_a.total_cost_usd == pytest.approx(0.01)


def test_get_summary_filters_by_date_range(tracker: CostTracker) -> None:
    """get_summary respects from_date and to_date filters."""
    tracker.record("s1", "m1", 100, 50, 0.01)
    summary = tracker.get_summary(
        from_date=datetime.now(UTC) - timedelta(hours=1),
        to_date=datetime.now(UTC) + timedelta(hours=1),
    )
    assert summary.request_count == 1


# ── CostTracker.get_user_cost_today ───────────────────────────────────────────


def test_get_user_cost_today_returns_zero_with_no_data(tracker: CostTracker) -> None:
    """get_user_cost_today returns 0.0 when no entries exist for the user."""
    cost = tracker.get_user_cost_today("nonexistent-user")
    assert cost == 0.0


def test_get_user_cost_today_sums_todays_entries(tracker: CostTracker) -> None:
    """get_user_cost_today sums only today's cost for the given user."""
    tracker.record("s1", "m1", 100, 50, 0.05, user_id="user-x")
    tracker.record("s2", "m1", 200, 80, 0.10, user_id="user-x")

    cost = tracker.get_user_cost_today("user-x")
    assert cost == pytest.approx(0.15)


# ── CostTracker.check_budget_alert ────────────────────────────────────────────


def test_check_budget_alert_false_when_under_budget(tracker: CostTracker) -> None:
    """check_budget_alert returns False when cost is below budget."""
    tracker.record("s1", "m1", 100, 50, 0.01, user_id="user-y")
    result = tracker.check_budget_alert("user-y", budget_usd=10.0)
    assert result is False


def test_check_budget_alert_true_when_over_budget(tracker: CostTracker) -> None:
    """check_budget_alert returns True when cost exceeds budget."""
    tracker.record("s1", "m1", 1000, 500, 5.0, user_id="user-z")
    result = tracker.check_budget_alert("user-z", budget_usd=1.0)
    assert result is True


# ── CostTracker.list_entries ──────────────────────────────────────────────────


def test_list_entries_returns_all_entries(tracker: CostTracker) -> None:
    """list_entries returns entries ordered by descending creation time."""
    tracker.record("s1", "m1", 10, 5, 0.001)
    tracker.record("s2", "m2", 20, 10, 0.002)

    entries = tracker.list_entries()
    assert len(entries) == 2


def test_list_entries_filters_by_session(tracker: CostTracker) -> None:
    """list_entries restricts by session_id when provided."""
    tracker.record("sess-alpha", "m1", 10, 5, 0.001)
    tracker.record("sess-beta", "m1", 20, 10, 0.002)

    entries = tracker.list_entries(session_id="sess-alpha")
    assert len(entries) == 1
    assert entries[0].session_id == "sess-alpha"


def test_list_entries_respects_limit(tracker: CostTracker) -> None:
    """list_entries returns at most `limit` entries."""
    for i in range(5):
        tracker.record(f"s{i}", "m1", 10, 5, 0.001)

    entries = tracker.list_entries(limit=3)
    assert len(entries) == 3
