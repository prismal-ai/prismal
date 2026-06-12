"""W1 — ConfigSourcePort + concrete sources + injection registry (Phase W).

Tests the source layer in isolation: each ``load()`` is sync, never raises, and
the registry round-trips. ``EnvConfigSource`` is the only source that touches the
environment / ``.env`` and folds in the legacy ``LIGHTAGENT_`` mirror.
"""

from __future__ import annotations

import warnings

import pytest

from prismal.core import config_source as cs
from prismal.core.config_source import (
    ChainedConfigSource,
    ConfigSourcePort,
    EnvConfigSource,
    FakeConfigSource,
    MappingConfigSource,
    get_config_source,
    set_config_source,
)


class TestPortProtocol:
    def test_concrete_sources_conform_to_port(self) -> None:
        assert isinstance(MappingConfigSource({}), ConfigSourcePort)
        assert isinstance(EnvConfigSource(env={}, dotenv_path=None), ConfigSourcePort)
        assert isinstance(ChainedConfigSource([]), ConfigSourcePort)
        assert isinstance(FakeConfigSource(), ConfigSourcePort)


class TestMappingConfigSource:
    def test_returns_values(self) -> None:
        src = MappingConfigSource({"PRISMAL_DEFAULT_MODEL": "claude-test"})
        assert src.load() == {"PRISMAL_DEFAULT_MODEL": "claude-test"}

    def test_returns_defensive_copy(self) -> None:
        original = {"PRISMAL_DEFAULT_MODEL": "a"}
        src = MappingConfigSource(original)
        loaded = dict(src.load())
        loaded["PRISMAL_DEFAULT_MODEL"] = "mutated"
        assert src.load()["PRISMAL_DEFAULT_MODEL"] == "a"
        original["PRISMAL_DEFAULT_MODEL"] = "also mutated"
        assert src.load()["PRISMAL_DEFAULT_MODEL"] == "a"


class TestFakeConfigSource:
    def test_empty_default(self) -> None:
        assert dict(FakeConfigSource().load()) == {}

    def test_passthrough(self) -> None:
        assert dict(FakeConfigSource({"K": "V"}).load()) == {"K": "V"}


class TestChainedConfigSource:
    def test_first_source_wins(self) -> None:
        chained = ChainedConfigSource(
            [
                MappingConfigSource({"K": "high", "ONLY_LOW": "x"}),
                MappingConfigSource({"K": "low", "ONLY_HIGH": "y"}),
            ]
        )
        merged = dict(chained.load())
        assert merged["K"] == "high"
        assert merged["ONLY_LOW"] == "x"
        assert merged["ONLY_HIGH"] == "y"

    def test_subsource_error_is_skipped(self) -> None:
        class Boom:
            def load(self) -> dict[str, str]:
                raise RuntimeError("backing store down")

        chained = ChainedConfigSource(
            [Boom(), MappingConfigSource({"K": "survives"})]
        )
        # must not raise; the broken source is skipped
        assert dict(chained.load()) == {"K": "survives"}

    def test_empty_chain(self) -> None:
        assert dict(ChainedConfigSource([]).load()) == {}


class TestEnvConfigSource:
    def test_reads_from_injected_env_not_os_environ(self) -> None:
        src = EnvConfigSource(env={"PRISMAL_DEFAULT_MODEL": "from-env"}, dotenv_path=None)
        assert src.load()["PRISMAL_DEFAULT_MODEL"] == "from-env"

    def test_honours_unprefixed_provider_keys(self) -> None:
        src = EnvConfigSource(env={"ANTHROPIC_API_KEY": "sk-xyz"}, dotenv_path=None)
        assert src.load()["ANTHROPIC_API_KEY"] == "sk-xyz"

    def test_env_wins_over_dotenv(self, tmp_path) -> None:
        dotenv = tmp_path / ".env"
        dotenv.write_text("PRISMAL_DEFAULT_MODEL=from-file\n")
        src = EnvConfigSource(
            env={"PRISMAL_DEFAULT_MODEL": "from-env"}, dotenv_path=dotenv
        )
        assert src.load()["PRISMAL_DEFAULT_MODEL"] == "from-env"

    def test_reads_dotenv_when_env_absent(self, tmp_path) -> None:
        dotenv = tmp_path / ".env"
        dotenv.write_text("PRISMAL_DEFAULT_MODEL=from-file\n")
        src = EnvConfigSource(env={}, dotenv_path=dotenv)
        assert src.load()["PRISMAL_DEFAULT_MODEL"] == "from-file"

    def test_missing_dotenv_is_silently_skipped(self, tmp_path) -> None:
        src = EnvConfigSource(
            env={"PRISMAL_DEFAULT_MODEL": "v"}, dotenv_path=tmp_path / "nope.env"
        )
        assert dict(src.load()) == {"PRISMAL_DEFAULT_MODEL": "v"}

    def test_legacy_alias_mirrored_when_prismal_unset(self) -> None:
        cs._reset_legacy_warning_for_tests()
        src = EnvConfigSource(env={"LIGHTAGENT_DEFAULT_MODEL": "legacy"}, dotenv_path=None)
        with pytest.warns(DeprecationWarning):
            loaded = dict(src.load())
        assert loaded["PRISMAL_DEFAULT_MODEL"] == "legacy"

    def test_legacy_alias_does_not_override_prismal(self) -> None:
        cs._reset_legacy_warning_for_tests()
        src = EnvConfigSource(
            env={
                "PRISMAL_DEFAULT_MODEL": "new",
                "LIGHTAGENT_DEFAULT_MODEL": "legacy",
            },
            dotenv_path=None,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            loaded = dict(src.load())
        assert loaded["PRISMAL_DEFAULT_MODEL"] == "new"

    def test_legacy_aliases_can_be_disabled(self) -> None:
        cs._reset_legacy_warning_for_tests()
        src = EnvConfigSource(
            env={"LIGHTAGENT_DEFAULT_MODEL": "legacy"},
            dotenv_path=None,
            include_legacy_aliases=False,
        )
        loaded = dict(src.load())  # no warning expected
        assert "PRISMAL_DEFAULT_MODEL" not in loaded

    def test_does_not_mutate_injected_env(self) -> None:
        cs._reset_legacy_warning_for_tests()
        env = {"LIGHTAGENT_DEFAULT_MODEL": "legacy"}
        src = EnvConfigSource(env=env, dotenv_path=None)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            src.load()
        assert "PRISMAL_DEFAULT_MODEL" not in env  # global/source env untouched


class TestRegistry:
    def teardown_method(self) -> None:
        set_config_source(None)  # reset global between tests

    def test_set_and_get(self) -> None:
        src = MappingConfigSource({"K": "v"})
        set_config_source(src)
        assert get_config_source() is src

    def test_default_is_none(self) -> None:
        set_config_source(None)
        assert get_config_source() is None

    def test_default_source_is_env(self) -> None:
        assert isinstance(cs._default_source(), EnvConfigSource)
