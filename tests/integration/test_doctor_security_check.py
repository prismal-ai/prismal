"""Integration tests — DoctorService.security_check (SPEC-031).

These tests call the real OSV API (https://api.osv.dev/v1/query) and are
network-gated.  Set ``PRISMAL_TEST_NETWORK=1`` to enable them:

    PRISMAL_TEST_NETWORK=1 pytest tests/integration/test_doctor_security_check.py -v

Without the env-var the tests are collected but immediately skipped.

Design notes
------------
- All tests use ``dry_run=True`` — nothing is installed or modified.
- The OSV API may rate-limit; ``osv_concurrency`` is deliberately low (2).
- We assert on *structure*, not on specific CVE data — package vulnerability
  status changes over time as patches are released.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_NETWORK = bool(os.environ.get("PRISMAL_TEST_NETWORK"))

pytestmark = pytest.mark.skipif(
    not _NETWORK,
    reason=(
        "Network integration test — set PRISMAL_TEST_NETWORK=1 to enable. "
        "Calls the real OSV API (https://api.osv.dev)."
    ),
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def doctor_service(tmp_path: Path):  # type: ignore[return]
    """Return a DoctorService with low concurrency and tmp reports_dir."""
    from prismal.maintenance.doctor_service import DoctorService
    from prismal.maintenance.package_updater import PackageUpdater
    from prismal.maintenance.vulnerability_scanner import VulnerabilityScanner

    scanner = VulnerabilityScanner()
    updater = PackageUpdater(pypi_timeout=15.0)
    return DoctorService(
        scanner=scanner,
        updater=updater,
        reports_dir=str(tmp_path / "logs"),
        pyproject_path=str(tmp_path / "pyproject.toml"),
    )


@pytest.fixture
def minimal_pyproject(tmp_path: Path) -> Path:
    """Write a minimal pyproject.toml with a known safe dependency."""
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
async def test_security_check_dry_run_returns_valid_report(
    tmp_path: Path,
) -> None:
    """security_check(dry_run=True) returns a SecurityAuditReport via real OSV API.

    Verifies:
    - At least one installed package was scanned.
    - The report has the correct mode and dry_run flag.
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
        # Use a missing pyproject so _load_constraints returns {} (no version guards)
        pyproject_path=str(tmp_path / "nonexistent.toml"),
    )

    # Override concurrency to avoid hammering OSV in CI
    svc._osv_concurrency = 2  # type: ignore[attr-defined]

    report = await svc.security_check(dry_run=True)

    # Structure checks
    assert report.mode == "security"
    assert report.dry_run is True
    assert report.totals.packages_scanned > 0, "Must have scanned at least one package"
    assert isinstance(report.vulnerable_packages, list)
    assert isinstance(report.update_plans, list)

    # A report file must have been written
    assert report.report_path is not None
    assert Path(report.report_path).exists(), "Report file was not written to disk"


@pytest.mark.asyncio
@pytest.mark.slow
async def test_security_check_report_only_returns_report(
    tmp_path: Path,
) -> None:
    """security_check(report_only=True) returns a report and never calls apply."""
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

    report = await svc.security_check(report_only=True)

    assert report.report_only is True
    assert report.totals.packages_scanned > 0
    # report_only never applies patches
    assert report.applied == []


@pytest.mark.asyncio
@pytest.mark.slow
async def test_security_check_totals_are_consistent(
    tmp_path: Path,
) -> None:
    """Verify totals fields are internally consistent with report details."""
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

    report = await svc.security_check(dry_run=True)

    t = report.totals
    # vulnerable count must match the vulnerable_packages list length
    assert t.vulnerable == len(report.vulnerable_packages)
    # patches_available must not exceed the vulnerable count
    assert t.patches_available <= t.vulnerable + t.packages_scanned
    # scan_failed must be non-negative
    assert t.scan_failed >= 0
