"""Permission manager — manages capability grants with TTL expiry.

Persists permissions in SQLite using the shared AsyncEngine from
prismal.core.database. Supports time-limited (TTL) and permanent grants.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Index, String, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from prismal.core.database import Base, get_db_session
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = get_logger("prismal.security.permissions")


class PermissionType(enum.StrEnum):
    """Categories of permissions that can be granted to agents."""

    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    NETWORK = "network"
    SHELL = "shell"
    DATABASE_WRITE = "database_write"


class PermissionRecord(Base):
    """SQLAlchemy ORM model for a granted permission entry."""

    __tablename__ = "permissions"
    __table_args__ = (Index("ix_perm_type_resource", "permission_type", "resource"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    permission_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str] = mapped_column(String(512), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(256), default="", nullable=False)


class PermissionManager:
    """TTL-based permission store backed by SQLite.

    Usage::

        pm = PermissionManager()
        await pm.grant(PermissionType.FILESYSTEM_WRITE, "/tmp/out", ttl_seconds=300)
        if await pm.check(PermissionType.FILESYSTEM_WRITE, "/tmp/out"):
            # proceed
    """

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        """Initialize the manager.

        Args:
            engine: Optional engine override for testing. None = use default
                engine.
        """
        self._engine = engine

    async def grant(
        self,
        permission_type: PermissionType,
        resource: str,
        ttl_seconds: int | None = 3600,
        reason: str = "",
    ) -> None:
        """Persist a new permission grant.

        Args:
            permission_type: Category of capability to grant.
            resource: Resource identifier (path, hostname, command, etc.).
            ttl_seconds: Seconds until expiry. None means no expiry.
            reason: Human-readable justification for the grant.
        """
        now = datetime.now(UTC)
        expires: datetime | None = (
            now + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
        )
        record = PermissionRecord(
            permission_type=permission_type.value,
            resource=resource,
            granted_at=now,
            expires_at=expires,
            reason=reason,
        )
        # Consume the full generator so commit() executes after yield.
        # Using `return` inside `async for` would close the generator early,
        # skipping the commit() call inside get_db_session.
        async for session in get_db_session(self._engine):
            session.add(record)

    async def check(self, permission_type: PermissionType, resource: str) -> bool:
        """Check if a valid (non-expired) permission exists.

        Args:
            permission_type: Category of capability to check.
            resource: Resource identifier.

        Returns:
            True if a non-expired permission exists, False otherwise.
        """
        now = datetime.now(UTC)
        # found defaults to False — deny-by-default if session yields nothing
        found: bool = False
        async for session in get_db_session(self._engine):
            result = await session.execute(
                select(PermissionRecord).where(
                    PermissionRecord.permission_type == permission_type.value,
                    PermissionRecord.resource == resource,
                    (PermissionRecord.expires_at.is_(None)) | (PermissionRecord.expires_at > now),
                )
            )
            found = result.scalars().first() is not None
        return found

    async def revoke(self, permission_type: PermissionType, resource: str) -> None:
        """Delete all grants matching the given type and resource.

        Args:
            permission_type: Category of capability.
            resource: Resource identifier.
        """
        async for session in get_db_session(self._engine):
            await session.execute(
                delete(PermissionRecord).where(
                    PermissionRecord.permission_type == permission_type.value,
                    PermissionRecord.resource == resource,
                )
            )

    async def list_permissions(self) -> list[PermissionRecord]:
        """Return all currently active (non-expired) permissions.

        Returns:
            List of active PermissionRecord ORM objects.
        """
        now = datetime.now(UTC)
        # records defaults to [] — safe empty list if session yields nothing
        records: list[PermissionRecord] = []
        async for session in get_db_session(self._engine):
            result = await session.execute(
                select(PermissionRecord).where(
                    (PermissionRecord.expires_at.is_(None)) | (PermissionRecord.expires_at > now),
                )
            )
            records = list(result.scalars().all())
        return records


__all__ = ["PermissionManager", "PermissionRecord", "PermissionType"]
