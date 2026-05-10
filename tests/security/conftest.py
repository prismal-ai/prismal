"""Auto-tag every test under ``tests/security/`` with ``@pytest.mark.security``.

The CI job ``test:security`` selects tests via ``pytest -m security``. Without
this hook, individual test files would each need ``pytestmark = pytest.mark.security``
and any new file forgetting it would silently disappear from the suite.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config,  # noqa: ARG001
    items: list[pytest.Item],
) -> None:
    for item in items:
        item.add_marker(pytest.mark.security)
