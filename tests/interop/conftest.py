"""Opt-in interop suites (PRT6-06): official conformance/TCK runs, never in the unit gate.

Collected only with ``PRISMAL_INTEROP=1``; each suite additionally needs its own
external artifact (path or tool) and skips with a reason when it is absent.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("PRISMAL_INTEROP") == "1":
        return
    skip = pytest.mark.skip(reason="interop suites are opt-in: set PRISMAL_INTEROP=1")
    for item in items:
        if "interop" in item.keywords:
            item.add_marker(skip)
