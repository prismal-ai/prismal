"""Tests for the `python -m prismal.plugins` CLI (X4, SPEC-EXT-008)."""

from __future__ import annotations

import pytest

import prismal.plugins as cli
from prismal.agents.extension.plugins import (
    DiscoveryReport,
    PluginInfo,
    PluginLoadResult,
)


def _info(name: str) -> PluginInfo:
    return PluginInfo(
        name=name,
        group="subgraphs",
        module="demo_pkg.plugin",
        object_name="register",
        dist_name="demo-pkg",
        dist_version="1.0.0",
    )


class TestList:
    def test_list_prints_names_and_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "list_plugins", lambda **kw: [_info("demo_sub")])
        rc = cli.main(["list"])
        assert rc == 0
        assert "demo_sub" in capsys.readouterr().out


class TestInfo:
    def test_info_known(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "get_plugin_info", lambda name, **kw: _info(name))
        rc = cli.main(["info", "demo_sub"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "demo_sub" in out
        assert "1.0.0" in out

    def test_info_unknown_returns_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "get_plugin_info", lambda name, **kw: None)
        assert cli.main(["info", "ghost"]) == 2


class TestDoctor:
    def test_doctor_all_ok_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            cli,
            "discover_plugins",
            lambda **kw: DiscoveryReport(loaded=[], failed=[], skipped=[]),
        )
        assert cli.main(["doctor"]) == 0

    def test_doctor_with_failure_returns_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bad = PluginLoadResult(info=_info("broken"), status="error", error="boom")
        monkeypatch.setattr(
            cli,
            "discover_plugins",
            lambda **kw: DiscoveryReport(loaded=[], failed=[bad], skipped=[]),
        )
        assert cli.main(["doctor"]) == 3


class TestEnableDisable:
    def test_enable_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["enable", "demo_sub"]) == 0
        assert "demo_sub" in capsys.readouterr().out

    def test_disable_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["disable", "demo_sub"]) == 0


class TestNoSubcommand:
    def test_no_args_returns_nonzero(self) -> None:
        assert cli.main([]) != 0
