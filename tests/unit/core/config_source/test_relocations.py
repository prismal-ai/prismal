"""W4 — relocate direct ``os.getenv`` config reads onto Settings / the port."""

from __future__ import annotations

import sys
import types

from prismal.core.config_source import FakeConfigSource, set_config_source


class TestTavilyRelocation:
    def teardown_method(self) -> None:
        set_config_source(None)

    def test_web_search_uses_source_key(self, monkeypatch) -> None:
        """web_search uses settings.tavily_api_key (supplied by the injected source)."""
        set_config_source(FakeConfigSource({"PRISMAL_TAVILY_API_KEY": "tvly-from-settings"}))

        calls: dict[str, str] = {}

        class _FakeTavily:
            def __init__(self, *a, **kw) -> None:
                pass

            def run(self, query: str) -> str:
                calls["query"] = query
                return "tavily-result"

        fake_mod = types.ModuleType("langchain_community.tools.tavily_search")
        fake_mod.TavilySearchResults = _FakeTavily
        monkeypatch.setitem(sys.modules, "langchain_community.tools.tavily_search", fake_mod)

        from prismal.agents.tools import web_search

        result = web_search.invoke({"query": "what is prismal"})
        assert "tavily-result" in result
        assert calls["query"] == "what is prismal"

    def test_web_search_ignores_os_environ_tavily_key(self, monkeypatch) -> None:
        """Relocation proof: a TAVILY_API_KEY in os.environ is NOT used.

        With the key absent from the injected source, web_search must NOT even
        attempt the Tavily path, even though os.environ has a (litellm-leaked)
        key — it goes straight to DuckDuckGo.
        """
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-from-os-environ")
        set_config_source(FakeConfigSource({}))  # source has no tavily key

        attempted = {"tavily": False}

        class _FakeTavily:
            def __init__(self, *a, **kw) -> None:
                attempted["tavily"] = True

            def run(self, query: str) -> str:
                return "tavily-result"

        tavily_mod = types.ModuleType("langchain_community.tools.tavily_search")
        tavily_mod.TavilySearchResults = _FakeTavily
        monkeypatch.setitem(sys.modules, "langchain_community.tools.tavily_search", tavily_mod)

        class _FakeDDG:
            def run(self, query: str) -> str:
                return "ddg-result"

        ddg_mod = types.ModuleType("langchain_community.tools")
        ddg_mod.DuckDuckGoSearchRun = lambda: _FakeDDG()
        monkeypatch.setitem(sys.modules, "langchain_community.tools", ddg_mod)

        from prismal.agents.tools import web_search

        result = web_search.invoke({"query": "x"})
        assert attempted["tavily"] is False  # relocation: os.environ key ignored
        assert "ddg-result" in result


class TestMcpResolveSecret:
    def teardown_method(self) -> None:
        set_config_source(None)

    def test_prefers_injected_source(self) -> None:
        from prismal.mcp.connection import resolve_secret

        set_config_source(FakeConfigSource({"MY_MCP_TOKEN": "from-source"}))
        assert resolve_secret("MY_MCP_TOKEN") == "from-source"

    def test_falls_back_to_os_environ(self, monkeypatch) -> None:
        from prismal.mcp.connection import resolve_secret

        set_config_source(None)
        monkeypatch.setenv("MY_MCP_TOKEN", "from-env")
        assert resolve_secret("MY_MCP_TOKEN") == "from-env"

    def test_missing_returns_empty(self, monkeypatch) -> None:
        from prismal.mcp.connection import resolve_secret

        set_config_source(FakeConfigSource({}))
        monkeypatch.delenv("MY_MCP_TOKEN", raising=False)
        assert resolve_secret("MY_MCP_TOKEN") == ""

    def test_unwraps_secretstr_from_source(self) -> None:
        from pydantic import SecretStr

        from prismal.mcp.connection import resolve_secret

        set_config_source(FakeConfigSource({"MY_MCP_TOKEN": SecretStr("secret-tok")}))
        assert resolve_secret("MY_MCP_TOKEN") == "secret-tok"
