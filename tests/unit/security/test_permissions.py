"""Unit tests for PermissionManager."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from prismal.core.database import create_async_engine_from_url, init_db
from prismal.security.permissions import PermissionManager, PermissionType


@pytest.fixture
async def engine() -> AsyncEngine:
    """In-memory SQLite engine with schema initialized."""
    eng = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def pm(engine: AsyncEngine) -> PermissionManager:
    """Return a PermissionManager bound to the in-memory test engine."""
    return PermissionManager(engine=engine)


def test_permission_type_values() -> None:
    """PermissionType enum values must match spec strings."""
    assert PermissionType.FILESYSTEM_READ.value == "filesystem_read"
    assert PermissionType.SHELL.value == "shell"


async def test_grant_and_check_returns_true(pm: PermissionManager) -> None:
    """Granted permission must be found by check."""
    await pm.grant(PermissionType.FILESYSTEM_READ, "/tmp/docs")
    assert await pm.check(PermissionType.FILESYSTEM_READ, "/tmp/docs")


async def test_check_without_grant_returns_false(pm: PermissionManager) -> None:
    """Non-existent permission must return False."""
    assert not await pm.check(PermissionType.NETWORK, "example.com")


async def test_grant_different_resource_not_found(pm: PermissionManager) -> None:
    """Permission for /tmp/a must not satisfy check for /tmp/b."""
    await pm.grant(PermissionType.FILESYSTEM_READ, "/tmp/a")
    assert not await pm.check(PermissionType.FILESYSTEM_READ, "/tmp/b")


async def test_grant_different_type_not_found(pm: PermissionManager) -> None:
    """FILESYSTEM_READ grant must not satisfy FILESYSTEM_WRITE check."""
    await pm.grant(PermissionType.FILESYSTEM_READ, "/tmp/x")
    assert not await pm.check(PermissionType.FILESYSTEM_WRITE, "/tmp/x")


async def test_expired_permission_not_found(pm: PermissionManager) -> None:
    """Permission with ttl_seconds=0 expires immediately."""
    await pm.grant(PermissionType.NETWORK, "example.com", ttl_seconds=0)
    assert not await pm.check(PermissionType.NETWORK, "example.com")


async def test_no_ttl_never_expires(pm: PermissionManager) -> None:
    """Permission with ttl_seconds=None must never expire."""
    await pm.grant(PermissionType.SHELL, "bash", ttl_seconds=None)
    assert await pm.check(PermissionType.SHELL, "bash")


async def test_revoke_removes_permission(pm: PermissionManager) -> None:
    """Revoked permission must no longer be found."""
    await pm.grant(PermissionType.FILESYSTEM_WRITE, "/tmp/out")
    assert await pm.check(PermissionType.FILESYSTEM_WRITE, "/tmp/out")
    await pm.revoke(PermissionType.FILESYSTEM_WRITE, "/tmp/out")
    assert not await pm.check(PermissionType.FILESYSTEM_WRITE, "/tmp/out")


async def test_revoke_nonexistent_is_noop(pm: PermissionManager) -> None:
    """Revoking a non-existent permission must not raise."""
    await pm.revoke(PermissionType.NETWORK, "nonexistent.com")


async def test_list_permissions_returns_active(pm: PermissionManager) -> None:
    """list_permissions must return all active (non-expired) grants."""
    await pm.grant(PermissionType.FILESYSTEM_READ, "/a")
    await pm.grant(PermissionType.FILESYSTEM_READ, "/b")
    perms = await pm.list_permissions()
    assert len(perms) == 2


async def test_list_permissions_excludes_expired(pm: PermissionManager) -> None:
    """list_permissions must exclude expired grants."""
    await pm.grant(PermissionType.NETWORK, "x.com", ttl_seconds=None)
    await pm.grant(PermissionType.NETWORK, "y.com", ttl_seconds=0)
    perms = await pm.list_permissions()
    resources = [p.resource for p in perms]
    assert "x.com" in resources
    assert "y.com" not in resources
