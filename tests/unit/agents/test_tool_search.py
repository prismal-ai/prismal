"""Tests for deferred tool loading + BM25 tool search (Phase TKE — SPEC-TKE-DEF-001)."""

from __future__ import annotations

import copy
import math
from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from prismal.agents import tool_search as ts
from prismal.agents.tool_search import (
    DeferredToolSet,
    ToolSearchHit,
    ToolSearchIndex,
    make_search_tools_tool,
    mark_deferred_native,
    split_core_and_deferred,
    supports_native_tool_search,
)

pytestmark = pytest.mark.unit


def _mk(name: str, description: str) -> BaseTool:
    def _fn(arg: str = "") -> str:
        return f"{name}:{arg}"

    return StructuredTool.from_function(func=_fn, name=name, description=description)


_SPECS: list[tuple[str, str]] = [
    ("read_file", "Read the contents of a file from disk"),
    ("write_file", "Write text content to a file on disk"),
    ("web_search", "Search the web for up-to-date information"),
    ("sql_query", "Run a SQL query against the analytics database"),
    ("send_email", "Send an email message to a recipient"),
    ("list_directory", "List entries of a directory path"),
    ("create_calendar_event", "Create a calendar event with attendees"),
    ("http_get", "Perform an HTTP GET request to a URL"),
]


@pytest.fixture
def tools() -> list[BaseTool]:
    return [_mk(n, d) for n, d in _SPECS]


@pytest.fixture
def index(tools: list[BaseTool]) -> ToolSearchIndex:
    return ToolSearchIndex(tools)


# --------------------------------------------------------------------- index


class TestToolSearchIndex:
    def test_read_a_file_ranks_read_file_first(self, index: ToolSearchIndex) -> None:
        hits = index.search("read a file")
        assert hits[0].name == "read_file"

    def test_search_the_web_ranks_web_search_first(self, index: ToolSearchIndex) -> None:
        assert index.search("search the web")[0].name == "web_search"

    def test_hit_is_frozen_with_openai_schema(self, index: ToolSearchIndex) -> None:
        hit = index.search("sql database", k=1)[0]
        assert isinstance(hit, ToolSearchHit)
        assert hit.name == "sql_query"
        assert hit.score > 0
        assert hit.schema["type"] == "function"
        assert hit.schema["function"]["name"] == "sql_query"
        with pytest.raises(AttributeError):
            hit.name = "x"  # type: ignore[misc]

    def test_k_limits_results(self, index: ToolSearchIndex) -> None:
        assert len(index.search("file", k=1)) == 1
        assert len(index.search("a file disk", k=2)) == 2

    def test_k_non_positive_returns_empty(self, index: ToolSearchIndex) -> None:
        assert index.search("file", k=0) == []
        assert index.search("file", k=-3) == []

    def test_empty_query_returns_empty(self, index: ToolSearchIndex) -> None:
        assert index.search("") == []
        assert index.search("   ---  ") == []

    def test_zero_score_excluded(self, index: ToolSearchIndex) -> None:
        hits = index.search("calendar")
        assert [h.name for h in hits] == ["create_calendar_event"]
        assert index.search("zzzunknownzzz") == []

    def test_deterministic_tie_break_by_name(self) -> None:
        idx = ToolSearchIndex([_mk("bbb", "same words here"), _mk("aaa", "same words here")])
        hits = idx.search("words")
        assert [h.name for h in hits] == ["aaa", "bbb"]
        assert hits[0].score == hits[1].score

    def test_score_desc_ordering(self, index: ToolSearchIndex) -> None:
        hits = index.search("a file disk", k=10)
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    def test_camelcase_and_snake_case_tokenization(self) -> None:
        idx = ToolSearchIndex([_mk("readFile", "loads stuff"), _mk("other", "unrelated")])
        assert [h.name for h in idx.search("read file")] == ["readFile"]
        assert [h.name for h in idx.search("read_file")] == ["readFile"]
        assert ts._tokenize("read_file") == ts._tokenize("readFile") == ["read", "file"]
        assert ts._tokenize("HTTPGet XMLParser") == ["http", "get", "xml", "parser"]

    def test_tool_names(self, index: ToolSearchIndex) -> None:
        assert index.tool_names == tuple(n for n, _ in _SPECS)

    def test_bm25_matches_hand_computed_score(self) -> None:
        # docs: alpha -> [alpha, apple, banana] (len 3); beta -> [beta, apple] (len 2)
        idx = ToolSearchIndex([_mk("alpha", "apple banana"), _mk("beta", "apple")], k1=1.5, b=0.75)
        hits = idx.search("banana")
        assert [h.name for h in hits] == ["alpha"]
        n_docs, df, k1, b, avgdl, dl, tf = 2, 1, 1.5, 0.75, 2.5, 3, 1
        idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
        expected = idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
        assert hits[0].score == pytest.approx(expected)

    def test_empty_index(self) -> None:
        idx = ToolSearchIndex([])
        assert idx.search("anything") == []
        assert idx.tool_names == ()


class TestRefresh:
    def test_refresh_rebuilds(self, index: ToolSearchIndex) -> None:
        index.refresh([_mk("weather_lookup", "Get the weather forecast")])
        assert index.tool_names == ("weather_lookup",)
        assert index.search("weather")[0].name == "weather_lookup"
        assert index.search("file") == []

    def test_ttl_skips_rebuild_when_fresh(self) -> None:
        now = [100.0]
        idx = ToolSearchIndex([_mk("a_tool", "first")], clock=lambda: now[0])
        now[0] = 100.5  # 500 ms later
        idx.refresh([_mk("b_tool", "second")], ttl_ms=1000)
        assert idx.tool_names == ("a_tool",)
        now[0] = 101.5  # 1500 ms after the build
        idx.refresh([_mk("b_tool", "second")], ttl_ms=1000)
        assert idx.tool_names == ("b_tool",)

    def test_ttl_none_always_rebuilds(self) -> None:
        idx = ToolSearchIndex([_mk("a_tool", "first")], clock=lambda: 0.0)
        idx.refresh([_mk("b_tool", "second")])
        assert idx.tool_names == ("b_tool",)


# --------------------------------------------------------------------- split


class TestSplit:
    def test_at_or_below_threshold_returns_all_unchanged(self, tools: list[BaseTool]) -> None:
        core, deferred = split_core_and_deferred(tools, core=frozenset(), threshold=len(tools))
        assert core == tools
        assert deferred == []

    def test_above_threshold_splits_and_sorts(self, tools: list[BaseTool]) -> None:
        core, deferred = split_core_and_deferred(
            tools, core=frozenset({"write_file", "read_file"}), threshold=3
        )
        assert [t.name for t in core] == ["read_file", "write_file"]
        assert [t.name for t in deferred] == sorted(
            n for n, _ in _SPECS if n not in {"read_file", "write_file"}
        )


# --------------------------------------------------------------- search tool


class TestSearchTool:
    def test_invocation_activates_and_returns_text(
        self, index: ToolSearchIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, int, dict[str, Any] | None]] = []

        class _FakeOTel:
            def increment_counter(
                self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
            ) -> None:
                calls.append((metric, value, attributes))

        monkeypatch.setattr(ts, "_otel", lambda: _FakeOTel())
        activated: list[list[str]] = []
        tool = make_search_tools_tool(
            index, lambda names: activated.append(list(names)), agent="coder"
        )
        assert tool.name == "search_tools"
        assert set(tool.args) == {"query", "k"}
        out = tool.invoke({"query": "search the web", "k": 2})
        assert out.splitlines()[0] == "web_search: Search the web for up-to-date information"
        assert len(out.splitlines()) <= 2
        assert activated and activated[0][0] == "web_search"
        assert ("tool_search_calls", 1, {"agent": "coder"}) in calls

    def test_no_hits(self, index: ToolSearchIndex) -> None:
        activated: list[list[str]] = []
        tool = make_search_tools_tool(index, lambda names: activated.append(list(names)))
        assert tool.invoke({"query": "zzzunknownzzz"}) == "no matching tools"
        assert activated == []

    def test_description_truncated(self) -> None:
        idx = ToolSearchIndex([_mk("long_tool", "keyword " + "x" * 500)])
        tool = make_search_tools_tool(idx, lambda names: None)
        line = tool.invoke({"query": "keyword"})
        assert line.startswith("long_tool: ")
        assert len(line) == len("long_tool: ") + 200

    def test_otel_failure_suppressed(
        self, index: ToolSearchIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> Any:
            raise RuntimeError("otel down")

        monkeypatch.setattr(ts, "_otel", _boom)
        tool = make_search_tools_tool(index, lambda names: None)
        assert "read_file" in tool.invoke({"query": "read a file"})


# ------------------------------------------------------------ DeferredToolSet


class TestDeferredToolSet:
    def _set(self, tools: list[BaseTool]) -> DeferredToolSet:
        core, deferred = split_core_and_deferred(tools, core=frozenset({"read_file"}), threshold=2)
        return DeferredToolSet(core, deferred, agent="coder")

    def test_initially_exposes_core_only(self, tools: list[BaseTool]) -> None:
        ds = self._set(tools)
        assert [t.name for t in ds.exposed()] == ["read_file"]
        assert "read_file" not in ds.index.tool_names
        assert "web_search" in ds.index.tool_names

    def test_activation_order_append_only(self, tools: list[BaseTool]) -> None:
        ds = self._set(tools)
        assert ds.activate(["web_search", "http_get"]) == ["web_search", "http_get"]
        assert ds.activate(["sql_query", "web_search", "unknown", "read_file"]) == ["sql_query"]
        assert ds.activate(["sql_query"]) == []
        assert [t.name for t in ds.exposed()] == [
            "read_file",
            "web_search",
            "http_get",
            "sql_query",
        ]

    def test_duplicate_within_one_call(self, tools: list[BaseTool]) -> None:
        ds = self._set(tools)
        assert ds.activate(["http_get", "http_get"]) == ["http_get"]

    def test_activate_increments_counter(
        self, tools: list[BaseTool], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, int, dict[str, Any] | None]] = []

        class _FakeOTel:
            def increment_counter(
                self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
            ) -> None:
                calls.append((metric, value, attributes))

        monkeypatch.setattr(ts, "_otel", lambda: _FakeOTel())
        ds = self._set(tools)
        ds.activate(["web_search", "http_get"])
        ds.activate(["web_search"])
        assert calls == [("tool_deferred_activated", 2, {"agent": "coder"})]

    def test_search_tool_wires_activation(self, tools: list[BaseTool]) -> None:
        ds = self._set(tools)
        tool = ds.search_tool()
        tool.invoke({"query": "calendar event"})
        assert [t.name for t in ds.exposed()] == ["read_file", "create_calendar_event"]


# ------------------------------------------------------------------- native


class TestNative:
    @pytest.mark.parametrize(
        "model",
        [
            "claude-sonnet-4-5",
            "anthropic/claude-opus-4-1",
            "openrouter/anthropic/claude-3-7-sonnet",
            "bedrock/us.anthropic.claude-sonnet-4",
            "Claude-Haiku",
        ],
    )
    def test_anthropic_family_supported(self, model: str) -> None:
        assert supports_native_tool_search(model) is True

    @pytest.mark.parametrize("model", ["gpt-4o", "openai/gpt-5", "ollama/llama3", "gemini/2.5", ""])
    def test_others_unsupported(self, model: str) -> None:
        assert supports_native_tool_search(model) is False

    def test_mark_deferred_native_does_not_mutate(self, tools: list[BaseTool]) -> None:
        schemas = [h.schema for h in ToolSearchIndex(tools).search("file", k=2)]
        before = copy.deepcopy(schemas)
        marked = mark_deferred_native(schemas)
        assert schemas == before
        assert all(m["defer_loading"] is True for m in marked)
        assert all("defer_loading" not in s for s in schemas)
        marked[0]["function"]["name"] = "mutated"
        assert schemas[0]["function"]["name"] != "mutated"
