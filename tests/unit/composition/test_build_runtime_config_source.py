"""``build_runtime(config_source=)`` (HRS — SPEC-HRS-CMP-001, SPEC-HRS-GRD-001)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from prismal.composition import build_runtime
from prismal.core.config import Settings
from prismal.core.config_source import MappingConfigSource, get_config_source

from .test_composition import _reset_global_provider, patched_builders  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("patched_builders")]


async def test_two_tenants_get_their_own_settings() -> None:
    env_before = dict(os.environ)
    global_before = get_config_source()
    acme = MappingConfigSource({"PRISMAL_BUDGET_MAX_TOKENS": "111"})
    beta = MappingConfigSource({"PRISMAL_BUDGET_MAX_TOKENS": "222"})

    ctx_a = await build_runtime(org_id="acme", config_source=acme, mode="context")
    ctx_b = await build_runtime(org_id="beta", config_source=beta, mode="context")

    assert ctx_a.settings is not None and ctx_b.settings is not None
    assert ctx_a.settings.budget_max_tokens == 111
    assert ctx_b.settings.budget_max_tokens == 222
    assert dict(os.environ) == env_before
    assert get_config_source() is global_before
    await ctx_a.aclose()
    await ctx_b.aclose()


async def test_tenant_source_wins_and_process_source_fills_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRISMAL_BUDGET_MAX_TOKENS", "999")
    monkeypatch.setenv("PRISMAL_BUDGET_MAX_CALLS", "7")
    tenant = MappingConfigSource({"PRISMAL_BUDGET_MAX_TOKENS": "111"})
    ctx = await build_runtime(org_id="acme", config_source=tenant, mode="context")
    assert ctx.settings is not None
    assert ctx.settings.budget_max_tokens == 111  # first (tenant) source wins
    assert ctx.settings.budget_max_calls == 7  # process source fills the gap
    await ctx.aclose()


async def test_process_source_defaults_to_env_when_none_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prismal.core import config_source as cs

    real_env_source = cs.EnvConfigSource
    # Hide the developer's .env: only os.environ feeds the default process source.
    monkeypatch.setattr(cs, "EnvConfigSource", lambda: real_env_source(dotenv_path=None))
    monkeypatch.setattr(cs, "_source", None)
    monkeypatch.setenv("PRISMAL_BUDGET_MAX_CALLS", "5")
    tenant = MappingConfigSource({})
    ctx = await build_runtime(config_source=tenant, mode="context")
    assert ctx.settings is not None and ctx.settings.budget_max_calls == 5
    assert cs._source is None
    await ctx.aclose()


async def test_overrides_layer_on_top_of_config_source() -> None:
    tenant = MappingConfigSource({"PRISMAL_BUDGET_MAX_TOKENS": "111"})
    ctx = await build_runtime(
        org_id="acme",
        config_source=tenant,
        overrides={"budget_max_tokens": 5},
        mode="context",
    )
    assert ctx.settings is not None and ctx.settings.budget_max_tokens == 5
    await ctx.aclose()


async def test_config_source_none_keeps_settings_unchanged() -> None:
    settings = Settings()
    ctx = await build_runtime(settings, mode="context")
    assert ctx.settings is settings  # no rebuild, byte-for-byte 3.16 behaviour
    await ctx.aclose()


async def test_config_source_none_without_settings_uses_get_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = Settings(budget_max_tokens=42)
    monkeypatch.setattr("prismal.core.config.get_settings", lambda: sentinel)
    ctx = await build_runtime(mode="context")
    assert ctx.settings is sentinel
    await ctx.aclose()


async def test_settings_passed_through_to_sub_builders(patched_builders: dict[str, Any]) -> None:  # noqa: F811
    tenant = MappingConfigSource({"PRISMAL_BUDGET_MAX_TOKENS": "111"})
    ctx = await build_runtime(config_source=tenant, mode="context")
    eff = patched_builders["tp_mock"].call_args.args[0]
    assert eff is ctx.settings and eff.budget_max_tokens == 111
    await ctx.aclose()
