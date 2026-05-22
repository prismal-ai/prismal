"""Integration tests — DoctorService.update end-to-end (SPEC-032).

These tests call the real PyPI JSON API (https://pypi.org/pypi/{name}/json)
and the OSV API to provide a realistic full-update scenario.  They are
network-gated behind ``LIGHTAGENT_TEST_NETWORK=1``:

    LIGHTAGENT_TEST_NETWORK=1 pytest tests/integration/test_doctor_update_e2e.py -v

All tests run with ``dry_run=True`` — nothing is installed or modified.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_NETWORK = bool(os.environ.get("LIGHTAGENT_TEST_NETWORK"))

pytestmark = pytest.mark.skipif(
    not _NETWORK,
    reason=(
        "Network integration test — set LIGHTAGENT_TEST_NETWORK=1 to enable. "
        "Calls the real PyPI API (https://pypi.org) and OSV API."
    ),
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_pyproject(tmp_path: Path) -> Path:
    """Write a minimal pyproject.toml so _load_constraints has something to read."""
    p = tmp_path / "pyproject.toml"
    p.write_text(
        '[project]\nname = "test"\ndependencies = ["httpx>=0.28.0"]\n',
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.slow
async def test_update_dry_run_returns_valid_report(
    tmp_path: Path,
    tmp_pyproject: Path,
) -> None:
    """update(dry_run=True) returns a SecurityAuditReport via real PyPI + OSV APIs.

    Verifies:
    - At least one installed package was scanned.
    - The report mode is ``"full"``.
    - dry_run is True and applied list is empty.
    - A report file was written to disk.
    """
    from prismal.maintenance.doctor_service import DoctorService
    from prismal.maintenance.package_updater import PackageUpdater
    from prismal.maintenance.vulnerability_scanner import VulnerabilityScanner

    scanner = VulnerabilityScanner()
    updater = PackageUpdater(pypi_timeout=15.0)
    svc = DoctorService(
        scanner=scanner,
        updater=updater,
        reports_dir=str(tmp_path / "logs"),
        pyproject_path=str(tmp_pyproject),
    )
    # Low concurrency to be polite to public APIs in CI
    svc._osv_concurrency = 2  # type: ignore[attr-defined]

    report = await svc.update(dry_run=True)

    # Structure checks
    assert report.mode == "full"
    assert report.dry_run is True
    assert report.totals.packages_scanned > 0, "Must have scanned at least one package"
    assert report.applied == [], "dry_run must not apply any updates"
    assert isinstance(report.update_plans, list)

    # Report file must exist
    assert report.report_path is not None
    assert Path(report.report_path).exists(), "Report file was not written to disk"


@pytest.mark.asyncio
@pytest.mark.slow
async def test_update_security_only_dry_run_returns_security_mode(
    tmp_path: Path,
) -> None:
    """update(dry_run=True, security_only=True) uses security mode and skips PyPI."""
    from prismal.maintenance.doctor_service import DoctorService
    from prismal.maintenance.package_updater import PackageUpdater
    from prismal.maintenance.vulnerability_scanner import VulnerabilityScanner

    scanner = VulnerabilityScanner()
    updater = PackageUpdater(pypi_timeout=15.0)
    svc = DoctorService(
        scanner=scanner,
        updater=updater,
        reports_dir=str(tmp_path / "logs"),
        pyproject_path=str(tmp_path / "nonexistent.toml"),
    )
    svc._osv_concurrency = 2  # type: ignore[attr-defined]

    report = await svc.update(dry_run=True, security_only=True)

    assert report.mode == "security"
    assert report.dry_run is True
    assert report.totals.packages_scanned > 0
    assert report.applied == []


@pytest.mark.asyncio
@pytest.mark.slow
async def test_update_fetch_latest_detects_outdated_packages(
    tmp_path: Path,
) -> None:
    """update(dry_run=True) fetches PyPI latest versions and detects outdated packages.

    The test verifies that the PyPI API round-trip works: ``totals.packages_scanned``
    is non-zero and no exceptions are raised.  We do not assert on specific packages
    being outdated because the test environment may already have the latest versions.
    """
    from prismal.maintenance.doctor_service import DoctorService
    from prismal.maintenance.package_updater import PackageUpdater
    from prismal.maintenance.vulnerability_scanner import VulnerabilityScanner

    scanner = VulnerabilityScanner()
    updater = PackageUpdater(pypi_timeout=15.0)
    svc = DoctorService(
        scanner=scanner,
        updater=updater,
        reports_dir=str(tmp_path / "logs"),
        pyproject_path=str(tmp_path / "nonexistent.toml"),
    )
    svc._osv_concurrency = 2  # type: ignore[attr-defined]

    # Should not raise even when no packages are outdated
    report = await svc.update(dry_run=True)

    assert report.totals.packages_scanned > 0
    # update_plans list is valid (may be empty if env is fully up-to-date)
    assert isinstance(report.update_plans, list)
    for plan in report.update_plans:
        assert plan.package_name
        assert plan.from_version
        assert plan.to_version
        assert plan.reason in ("security", "outdated")


@pytest.mark.asyncio
@pytest.mark.slow
async def test_update_packages_filter_limits_scope(
    tmp_path: Path,
) -> None:
    """update(dry_run=True, packages=[...]) only resolves the named packages."""
    from prismal.maintenance.doctor_service import DoctorService
    from prismal.maintenance.package_updater import PackageUpdater
    from prismal.maintenance.vulnerability_scanner import VulnerabilityScanner

    scanner = VulnerabilityScanner()
    updater = PackageUpdater(pypi_timeout=15.0)
    svc = DoctorService(
        scanner=scanner,
        updater=updater,
        reports_dir=str(tmp_path / "logs"),
        pyproject_path=str(tmp_path / "nonexistent.toml"),
    )
    svc._osv_concurrency = 2  # type: ignore[attr-defined]

    # Filter to a single well-known package
    report = await svc.update(dry_run=True, packages=["httpx"])

    assert report.dry_run is True
    # All update plans (if any) must be for httpx only
    for plan in report.update_plans:
        assert plan.package_name.lower() == "httpx", (
            f"Expected only 'httpx' in plans, got '{plan.package_name}'"
        )
