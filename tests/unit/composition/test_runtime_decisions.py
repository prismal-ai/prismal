"""build_runtime decision composition (Phase DEC — DecisionRuntime in RuntimeContext)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from prismal.composition import build_runtime
from prismal.core.config import Settings
from prismal.decisions.adapters.systemone_http import SystemOneHttpDecisionAdapter
from prismal.decisions.runtime import DecisionRuntime, get_decision_runtime, set_decision_runtime
from prismal.identity.vault import FakeVault

from .test_composition import _reset_global_provider, patched_builders  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("patched_builders")]


@pytest.fixture(autouse=True)
def _reset() -> Any:
    set_decision_runtime(None)
    yield
    set_decision_runtime(None)


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "decisions_enabled": True,
        "decision_backend_order": ["rule", "jev"],
        "decision_calibration_dir": str(tmp_path),
    }
    base.update(kw)
    return Settings(**base)


async def test_none_when_disabled() -> None:
    ctx = await build_runtime(Settings(), mode="context")
    assert ctx.decisions is None
    await ctx.aclose()


async def test_context_mode_keeps_runtime_local(tmp_path: Path) -> None:
    vault = FakeVault()
    vault.store("jev_api_key@acme", SecretStr("acme-jev"))
    ctx = await build_runtime(
        _settings(tmp_path), org_id="acme", mode="context", decision_vault=vault
    )
    assert isinstance(ctx.decisions, DecisionRuntime)
    (backend,) = ctx.decisions.cascade.backends
    assert isinstance(backend, SystemOneHttpDecisionAdapter)
    assert get_decision_runtime() is None
    await ctx.aclose()


async def test_global_mode_installs_and_uninstalls(tmp_path: Path) -> None:
    ctx = await build_runtime(_settings(tmp_path, jev_api_key="k"), mode="global")
    assert get_decision_runtime() is ctx.decisions
    await ctx.aclose()
    assert get_decision_runtime() is None


async def test_tenant_without_key_has_no_jev_tier(tmp_path: Path) -> None:
    ctx = await build_runtime(
        _settings(tmp_path, jev_api_key="global"),
        org_id="beta",
        mode="context",
        decision_vault=FakeVault(),
    )
    assert ctx.decisions is not None
    assert ctx.decisions.cascade.backends == ()
    await ctx.aclose()
