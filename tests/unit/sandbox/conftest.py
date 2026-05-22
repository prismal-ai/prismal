"""Sandbox unit-test fixtures.

The production default is ``PRISMAL_SHELL_ENABLED=false`` (Phase 43 / L4
ActionInterceptor blocks subprocess calls). Sandbox unit tests exercise the
backend logic *through* that gate with a mocked ``_spawn_container`` — the gate
itself isn't what they're testing — so we open it for the duration of every
test in this directory. Tests that explicitly verify the block path
(``test_docker_backend_shell_gate_blocks``) re-patch ``check_shell`` inside
their own ``with`` block, which takes precedence within that scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _allow_shell_for_sandbox_tests() -> Iterator[None]:
    with patch(
        "prismal.security.action_interceptor.ActionInterceptor.check_shell",
        return_value=True,
    ):
        yield
