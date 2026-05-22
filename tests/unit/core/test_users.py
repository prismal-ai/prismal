"""Unit tests for prismal.core.users — UserRole, User, UserStore.

Note: passlib 1.7.4 + bcrypt 4.x have a known incompatibility (passlib's
wrap-bug detection uses an internal >72-byte password that bcrypt 4 rejects).
Tests that exercise password hashing mock passlib.context.CryptContext to
avoid this environment issue while still verifying the UserStore logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prismal.core.users import User, UserRole, UserStore

# ── Helpers ───────────────────────────────────────────────────────────────────

_FAKE_HASH = "$2b$12$fakehashfakehashfakehashfakehashfakehashfakehashfakehashfak"


def _mock_crypt_context() -> MagicMock:
    """Return a MagicMock that mimics CryptContext for bcrypt."""
    ctx = MagicMock()
    ctx.hash.return_value = _FAKE_HASH
    ctx.verify.return_value = True
    return ctx


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> UserStore:
    """Return a UserStore backed by a temporary SQLite file."""
    # Mock passlib to avoid passlib/bcrypt 4.x incompatibility
    with patch("passlib.context.CryptContext", return_value=_mock_crypt_context()):
        return UserStore(db_path=tmp_path / "test_users.db")


# ── UserRole ──────────────────────────────────────────────────────────────────


def test_user_role_values() -> None:
    """UserRole enum has admin, operator, viewer values."""
    assert UserRole.admin.value == "admin"
    assert UserRole.operator.value == "operator"
    assert UserRole.viewer.value == "viewer"


def test_user_role_is_str_enum() -> None:
    """UserRole is a subclass of str, comparable to plain strings."""
    assert UserRole.admin == "admin"
    assert UserRole.viewer != "admin"


def test_user_role_can_be_constructed_from_string() -> None:
    """UserRole(value) constructs enum from string."""
    assert UserRole("admin") is UserRole.admin
    assert UserRole("operator") is UserRole.operator


# ── User model ────────────────────────────────────────────────────────────────


def test_user_model_defaults() -> None:
    """User.is_active defaults to True."""
    user = User(
        id="u-001",
        username="alice",
        hashed_password=_FAKE_HASH,
        role=UserRole.admin,
        created_at=datetime.now(UTC),
    )
    assert user.is_active is True
    assert user.role == UserRole.admin


def test_user_model_inactive() -> None:
    """User.is_active can be set to False."""
    user = User(
        id="u-002",
        username="bob",
        hashed_password=_FAKE_HASH,
        role=UserRole.viewer,
        created_at=datetime.now(UTC),
        is_active=False,
    )
    assert user.is_active is False


# ── UserStore CRUD ────────────────────────────────────────────────────────────


def test_create_user_returns_user(tmp_path: Path) -> None:
    """create_user returns a User with correct username and role."""
    mock_ctx = _mock_crypt_context()
    with patch("passlib.context.CryptContext", return_value=mock_ctx):
        store = UserStore(db_path=tmp_path / "users.db")
        user = store.create_user("alice", "somepassword", role=UserRole.admin)

    assert user.username == "alice"
    assert user.role == UserRole.admin
    assert len(user.id) == 36  # UUID4
    assert user.hashed_password == _FAKE_HASH


def test_create_user_default_role_is_viewer(tmp_path: Path) -> None:
    """create_user assigns viewer role when not specified."""
    mock_ctx = _mock_crypt_context()
    with patch("passlib.context.CryptContext", return_value=mock_ctx):
        store = UserStore(db_path=tmp_path / "users.db")
        user = store.create_user("bob", "somepassword")

    assert user.role == UserRole.viewer


def test_create_user_duplicate_raises_value_error(tmp_path: Path) -> None:
    """create_user raises ValueError when username already exists."""
    mock_ctx = _mock_crypt_context()
    with patch("passlib.context.CryptContext", return_value=mock_ctx):
        store = UserStore(db_path=tmp_path / "users.db")
        store.create_user("carol", "somepassword")
        with pytest.raises(ValueError, match="carol"):
            store.create_user("carol", "anotherpassword")


def test_get_by_username_returns_user(tmp_path: Path) -> None:
    """get_by_username returns the user when found."""
    mock_ctx = _mock_crypt_context()
    with patch("passlib.context.CryptContext", return_value=mock_ctx):
        store = UserStore(db_path=tmp_path / "users.db")
        store.create_user("dave", "somepassword")
        user = store.get_by_username("dave")

    assert user is not None
    assert user.username == "dave"


def test_get_by_username_returns_none_for_missing(tmp_path: Path) -> None:
    """get_by_username returns None for an unknown username."""
    store = UserStore(db_path=tmp_path / "users.db")
    result = store.get_by_username("nobody")
    assert result is None


def test_get_by_id_returns_user(tmp_path: Path) -> None:
    """get_by_id returns the user when the UUID matches."""
    mock_ctx = _mock_crypt_context()
    with patch("passlib.context.CryptContext", return_value=mock_ctx):
        store = UserStore(db_path=tmp_path / "users.db")
        created = store.create_user("eve", "somepassword")
        found = store.get_by_id(created.id)

    assert found is not None
    assert found.id == created.id
    assert found.username == "eve"


def test_get_by_id_returns_none_for_missing(tmp_path: Path) -> None:
    """get_by_id returns None for an unknown UUID."""
    store = UserStore(db_path=tmp_path / "users.db")
    result = store.get_by_id("00000000-0000-0000-0000-000000000000")
    assert result is None


def test_list_users_returns_all_active(tmp_path: Path) -> None:
    """list_users returns all active users."""
    mock_ctx = _mock_crypt_context()
    with patch("passlib.context.CryptContext", return_value=mock_ctx):
        store = UserStore(db_path=tmp_path / "users.db")
        store.create_user("frank", "somepassword", role=UserRole.operator)
        store.create_user("grace", "somepassword2", role=UserRole.viewer)
        users = store.list_users()

    usernames = [u.username for u in users]
    assert "frank" in usernames
    assert "grace" in usernames


def test_list_users_empty_store(tmp_path: Path) -> None:
    """list_users returns an empty list when no users exist."""
    store = UserStore(db_path=tmp_path / "users.db")
    assert store.list_users() == []


# ── Password hashing and verification ─────────────────────────────────────────


def test_verify_password_calls_ctx_verify(tmp_path: Path) -> None:
    """verify_password delegates to CryptContext.verify."""
    mock_ctx = _mock_crypt_context()
    mock_ctx.verify.return_value = True

    with patch("passlib.context.CryptContext", return_value=mock_ctx):
        store = UserStore(db_path=tmp_path / "users.db")
        result = store.verify_password("plaintext", _FAKE_HASH)

    assert result is True
    mock_ctx.verify.assert_called_once_with("plaintext", _FAKE_HASH)


def test_verify_password_returns_false_on_mismatch(tmp_path: Path) -> None:
    """verify_password returns False when CryptContext.verify returns False."""
    mock_ctx = _mock_crypt_context()
    mock_ctx.verify.return_value = False

    with patch("passlib.context.CryptContext", return_value=mock_ctx):
        store = UserStore(db_path=tmp_path / "users.db")
        result = store.verify_password("wrongpassword", _FAKE_HASH)

    assert result is False


def test_verify_password_returns_false_on_import_error(tmp_path: Path) -> None:
    """verify_password returns False when passlib CryptContext raises ImportError."""
    store = UserStore(db_path=tmp_path / "users.db")
    with patch("passlib.context.CryptContext", side_effect=ImportError("no passlib")):
        result = store.verify_password("any", _FAKE_HASH)
    assert result is False


def test_hash_password_calls_ctx_hash(tmp_path: Path) -> None:
    """_hash_password calls CryptContext.hash and returns the result."""
    mock_ctx = _mock_crypt_context()
    mock_ctx.hash.return_value = "$2b$fake"

    with patch("passlib.context.CryptContext", return_value=mock_ctx):
        result = UserStore._hash_password("somepassword")

    assert result == "$2b$fake"
    mock_ctx.hash.assert_called_once_with("somepassword")
