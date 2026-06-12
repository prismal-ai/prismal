"""W2 — ``Settings`` consumes the injected ``ConfigSourcePort`` (Phase W).

``build_settings(source)`` is the pure per-tenant constructor; ``get_settings()``
delegates to the injected/default source behind its ``@lru_cache``; init kwargs
still win; env/``.env`` reading is funnelled exclusively through the source.
"""

from __future__ import annotations

from prismal.core.config import Settings, build_settings, get_settings, reload_settings
from prismal.core.config_source import (
    FakeConfigSource,
    MappingConfigSource,
    set_config_source,
)


class TestBuildSettings:
    def teardown_method(self) -> None:
        set_config_source(None)

    def test_builds_from_explicit_source(self) -> None:
        s = build_settings(FakeConfigSource({"PRISMAL_DEFAULT_MODEL": "claude-test"}))
        assert s.default_model == "claude-test"

    def test_source_isolated_from_os_environ(self, monkeypatch) -> None:
        # An env var that is NOT in the source must not leak in.
        monkeypatch.setenv("PRISMAL_DEFAULT_MODEL", "from-os-env")
        s = build_settings(FakeConfigSource({"PRISMAL_TEMPERATURE": "0.1"}))
        assert s.default_model == "claude-sonnet-4-5"  # schema default, not os env
        assert s.temperature == 0.1

    def test_decodes_json_list_field(self) -> None:
        s = build_settings(FakeConfigSource({"PRISMAL_CORS_ORIGINS": '["https://a.test"]'}))
        assert s.cors_origins == ["https://a.test"]

    def test_secret_field_from_source(self) -> None:
        s = build_settings(FakeConfigSource({"PRISMAL_ANTHROPIC_API_KEY": "sk-secret"}))
        assert s.anthropic_api_key.get_secret_value() == "sk-secret"

    def test_unprefixed_provider_key_via_alias(self) -> None:
        s = build_settings(FakeConfigSource({"ANTHROPIC_API_KEY": "sk-bare"}))
        assert s.anthropic_api_key.get_secret_value() == "sk-bare"

    def test_validation_alias_model(self) -> None:
        s = build_settings(FakeConfigSource({"PRISMAL_MODEL": "aliased"}))
        assert s.default_model == "aliased"

    def test_none_source_uses_injected_global(self) -> None:
        set_config_source(MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "injected"}))
        assert build_settings().default_model == "injected"


class TestInitKwargsStillWin:
    """Existing call sites construct ``Settings(field=...)`` directly."""

    def teardown_method(self) -> None:
        set_config_source(None)

    def test_init_kwarg_beats_source(self) -> None:
        set_config_source(MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "from-source"}))
        assert Settings(default_model="from-kwarg").default_model == "from-kwarg"

    def test_bare_settings_reads_through_default_source(self, monkeypatch) -> None:
        set_config_source(None)
        # Neutralise any repo .env PRISMAL_LLM_PROVIDER (would override default_model).
        monkeypatch.setenv("PRISMAL_LLM_PROVIDER", "")
        monkeypatch.setenv("PRISMAL_DEFAULT_MODEL", "from-default-env")
        assert Settings().default_model == "from-default-env"


class TestGetSettingsDelegation:
    def teardown_method(self) -> None:
        set_config_source(None)

    def test_set_invalidates_settings_cache(self) -> None:
        set_config_source(MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "first"}))
        assert get_settings().default_model == "first"
        set_config_source(MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "second"}))
        assert get_settings().default_model == "second"

    def test_reload_settings_clears_cache(self, monkeypatch) -> None:
        set_config_source(None)
        monkeypatch.setenv("PRISMAL_LLM_PROVIDER", "")
        monkeypatch.setenv("PRISMAL_DEFAULT_MODEL", "v1")
        assert get_settings().default_model == "v1"
        monkeypatch.setenv("PRISMAL_DEFAULT_MODEL", "v2")
        reload_settings()
        assert get_settings().default_model == "v2"

    def test_reload_settings_tolerates_patched_get_settings(self, monkeypatch) -> None:
        """``reload_settings`` must not crash when ``get_settings`` is patched.

        Tests routinely ``monkeypatch.setattr(prismal.core.config, "get_settings",
        lambda: ...)`` with a plain function that has no ``cache_clear``. The
        autouse ``.env`` isolation fixture calls ``set_config_source(None)`` →
        ``reload_settings()`` during teardown *before* monkeypatch undoes that
        patch, so clearing a cache that no longer exists must be a no-op.
        """
        import prismal.core.config as cfg_module

        monkeypatch.setattr(cfg_module, "get_settings", lambda: None)
        reload_settings()  # must not raise AttributeError
