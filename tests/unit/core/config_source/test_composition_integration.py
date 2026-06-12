"""W6 — composition-root threads a per-tenant ConfigSourcePort (Phase W / SPEC-CSI-013)."""

from __future__ import annotations

from prismal.composition.config_sources import apply_org_overrides
from prismal.core.config import build_settings
from prismal.core.config_source import MappingConfigSource, set_config_source


class TestApplyOrgOverridesWithSource:
    def teardown_method(self) -> None:
        set_config_source(None)

    def test_source_builds_tenant_settings(self) -> None:
        base = build_settings(MappingConfigSource({}))
        eff = apply_org_overrides(
            base,
            "acme",
            None,
            source=MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "acme-model"}),
        )
        assert eff.default_model == "acme-model"

    def test_source_then_overrides_applied(self) -> None:
        base = build_settings(MappingConfigSource({}))
        eff = apply_org_overrides(
            base,
            "acme",
            {"temperature": 0.123},
            source=MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "acme-model"}),
        )
        assert eff.default_model == "acme-model"
        assert eff.temperature == 0.123

    def test_no_source_preserves_existing_behaviour(self) -> None:
        base = build_settings(MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "base"}))
        # without a source, original semantics: returns base (no overrides)
        assert apply_org_overrides(base, "acme", None) is base

    def test_two_tenants_share_no_state(self) -> None:
        from prismal.core.config_source import get_config_source

        global_before = get_config_source()
        base = build_settings(MappingConfigSource({}))
        acme = apply_org_overrides(
            base,
            "acme",
            None,
            source=MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "acme"}),
        )
        globex = apply_org_overrides(
            base,
            "globex",
            None,
            source=MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "globex"}),
        )
        assert acme.default_model == "acme"
        assert globex.default_model == "globex"
        assert acme is not globex
        # per-tenant builds never touch the global config source
        assert get_config_source() is global_before
