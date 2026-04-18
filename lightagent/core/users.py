"""User management for LightAgent multi-user RBAC.

Provides:

* :class:`UserRole` — three-tier role enum (admin, operator, viewer).
* :class:`User` — Pydantic model representing a persisted user.
* :class:`UserStore` — SQLite-backed store with bcrypt password hashing.

The store is intentionally synchronous (sqlite3) so that it can be called
from both async request handlers (via ``asyncio.to_thread``) and the
synchronous CLI commands.

SPEC-018 acceptance criteria implemented here
----------------------------------------------
* AC-018-3 — three default roles: admin / operator / viewer.
* AC-018-8 — ``UserStore.create()`` used by the CLI ``users create`` command.
* AC-018-9 — ``passlib[bcrypt]`` for password hashing.
* AC-018-10 — Users persisted; token expiry configured in
  :mod:`lightagent.api.routers.auth`.

Example::

    store = UserStore()
    store.create_user("alice", "s3cr3t!", role=UserRole.admin)
    user = store.get_by_username("alice")
    assert store.verify_password("s3cr3t!", user.hashed_password)
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

from pydantic import BaseModel

from lightagent.core.logging import get_logger

logger = get_logger("lightagent.core.users")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    username     TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'viewer',
    created_at   TEXT NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1
);
"""

_DT_FMT = "%Y-%m-%dT%H:%M:%S"


class UserRole(StrEnum):
    """Role levels for the LightAgent RBAC system.

    Attributes:
        admin: Full access — can manage users, config, MCP servers.
        operator: Can chat, manage skills, and view monitoring; cannot create users.
        viewer: Read-only — can chat and view the dashboard.
    """

    admin = "admin"
    operator = "operator"
    viewer = "viewer"


class User(BaseModel):
    """A persisted LightAgent user.

    Attributes:
        id: UUID4 string identifier.
        username: Unique login name (3-64 chars).
        hashed_password: bcrypt hash; never the plain-text password.
        role: The user's :class:`UserRole`.
        created_at: UTC creation timestamp.
        is_active: Whether the account is enabled.
    """

    id: str
    username: str
    hashed_password: str
    role: UserRole
    created_at: datetime
    is_active: bool = True


class UserStore:
    """SQLite-backed user store with bcrypt password hashing.

    Args:
        db_path: Path to the SQLite file.  Defaults to
            ``settings.users_db_path``.

    Raises:
        ImportError: If ``passlib[bcrypt]`` is not installed.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialise the store and ensure the schema exists."""
        if db_path is not None:
            self._db = db_path
        else:
            from lightagent.core.config import get_settings

            self._db = Path(get_settings().users_db_path)

        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── SQLite helpers ────────────────────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection]:
        """Yield a committed SQLite connection."""
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """Create the users table if it does not exist."""
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        """Convert a SQLite row to a :class:`User` model.

        Args:
            row: A ``sqlite3.Row`` from the users table.

        Returns:
            Populated :class:`User` instance.
        """
        return User(
            id=row["id"],
            username=row["username"],
            hashed_password=row["hashed_password"],
            role=UserRole(row["role"]),
            created_at=datetime.strptime(row["created_at"], _DT_FMT).replace(tzinfo=UTC),
            is_active=bool(row["is_active"]),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def create_user(
        self,
        username: str,
        password: str,
        role: UserRole = UserRole.viewer,
    ) -> User:
        """Create and persist a new user with a hashed password.

        Args:
            username: Unique login name.
            password: Plain-text password (hashed with bcrypt before storage).
            role: Assigned :class:`UserRole`.  Defaults to ``viewer``.

        Returns:
            The newly created :class:`User`.

        Raises:
            ValueError: If ``username`` already exists.
            ImportError: If ``passlib[bcrypt]`` is not installed.
        """
        hashed = self._hash_password(password)
        now = datetime.now(UTC).replace(tzinfo=None).strftime(_DT_FMT)
        user_id = str(uuid.uuid4())

        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO users (id, username, hashed_password, role, "
                    "created_at, is_active) VALUES (?, ?, ?, ?, ?, 1)",
                    (user_id, username, hashed, role.value, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Username '{username}' already exists.") from exc

        logger.info("user_created", username=username, role=role.value)
        return User(
            id=user_id,
            username=username,
            hashed_password=hashed,
            role=role,
            created_at=datetime.strptime(now, _DT_FMT).replace(tzinfo=UTC),
        )

    def get_by_username(self, username: str) -> User | None:
        """Fetch a user by their login name.

        Args:
            username: The login name to look up.

        Returns:
            :class:`User` if found, else ``None``.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? AND is_active = 1",
                (username,),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_id(self, user_id: str) -> User | None:
        """Fetch a user by their UUID.

        Args:
            user_id: The UUID string to look up.

        Returns:
            :class:`User` if found, else ``None``.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ? AND is_active = 1",
                (user_id,),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self) -> list[User]:
        """Return all active users.

        Returns:
            List of active :class:`User` objects ordered by creation time.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE is_active = 1 ORDER BY created_at"
            ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a plain-text password against a stored bcrypt hash.

        Args:
            plain_password: The submitted password.
            hashed_password: The stored bcrypt hash.

        Returns:
            True if the password matches the hash.
        """
        try:
            from passlib.context import CryptContext

            ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
            return bool(ctx.verify(plain_password, hashed_password))
        except ImportError:
            # passlib not installed — treat as mismatch for security
            logger.error(
                "passlib_not_installed",
                hint="Install with: pip install 'passlib[bcrypt]'",
            )
            return False

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _hash_password(password: str) -> str:
        """Hash a plain-text password with bcrypt.

        Args:
            password: Plain-text password string.

        Returns:
            bcrypt hash string.

        Raises:
            ImportError: If ``passlib[bcrypt]`` is not installed.
        """
        try:
            from passlib.context import CryptContext

            ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
            return ctx.hash(password)
        except ImportError as exc:
            raise ImportError(
                "Password hashing requires passlib[bcrypt]. "
                "Install with: pip install 'passlib[bcrypt]'"
            ) from exc


__all__ = ["User", "UserRole", "UserStore"]
