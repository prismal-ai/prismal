"""Tests for per-tool output budgets (Phase TKE — SPEC-TKE-OUT-001)."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from prismal.agents.artifacts import ArtifactRef, InMemoryArtifactStore
from prismal.agents.tool_output_budget import BudgetedOutput, ToolOutputBudget
from prismal.core.config import Settings
from prismal.utils.token_estimate import estimate_tokens


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "token_efficiency_enabled": True,
        "tool_output_budget_enabled": True,
        "tool_output_max_tokens_default": 100,
    }
    base.update(kw)
    return Settings(**base)


class RecordingStore:
    """Artifact store double recording put() calls."""

    def __init__(self) -> None:
        self.inner = InMemoryArtifactStore(settings=Settings())
        self.puts: list[tuple[str, dict[str, Any]]] = []

    def put(self, content: str, **kw: Any) -> ArtifactRef:
        self.puts.append((content, kw))
        return self.inner.put(content, **kw)

    def get(self, *a: Any, **kw: Any) -> Any:
        return self.inner.get(*a, **kw)

    def purge_expired(self, now: float | None = None) -> int:
        return self.inner.purge_expired(now)


class BrokenStore:
    def put(self, content: str, **kw: Any) -> ArtifactRef:
        raise RuntimeError("store down")

    def get(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise RuntimeError("store down")

    def purge_expired(self, now: float | None = None) -> int:  # pragma: no cover
        return 0


def _apply(b: ToolOutputBudget, tool: str, output: object) -> BudgetedOutput:
    return b.apply(tool, output, run_id="r1", tenant="t1")


class TestEnabledFlag:
    def test_disabled_by_default(self) -> None:
        assert ToolOutputBudget(settings=Settings()).enabled is False

    def test_sub_flag_needs_master(self) -> None:
        s = Settings(tool_output_budget_enabled=True)
        assert ToolOutputBudget(settings=s).enabled is False

    def test_enabled_with_master_and_sub_flag(self) -> None:
        assert ToolOutputBudget(settings=_settings()).enabled is True


class TestDisabledPassthrough:
    def test_str_unchanged(self) -> None:
        text = "x" * 50_000
        out = _apply(ToolOutputBudget(settings=Settings()), "search", text)
        assert out == BudgetedOutput(
            content=text, truncated=False, original_tokens=estimate_tokens(text), artifact=None
        )

    def test_non_str_uses_str(self) -> None:
        data = {"b": 1, "a": [1, 2]}
        out = _apply(ToolOutputBudget(settings=Settings()), "search", data)
        assert out.content == str(data)
        assert out.truncated is False
        assert out.artifact is None

    def test_disabled_never_touches_store(self) -> None:
        store = RecordingStore()
        _apply(ToolOutputBudget(settings=Settings(), artifact_store=store), "s", "y" * 99_999)
        assert store.puts == []


class TestMaxTokensFor:
    def test_default(self) -> None:
        assert ToolOutputBudget(settings=_settings()).max_tokens_for("any") == 100

    def test_per_tool_override(self) -> None:
        b = ToolOutputBudget(settings=_settings(tool_output_max_tokens_by_tool={"web": 10}))
        assert b.max_tokens_for("web") == 10
        assert b.max_tokens_for("other") == 100


class TestUnderBudget:
    def test_str_under_budget_unchanged(self) -> None:
        store = RecordingStore()
        b = ToolOutputBudget(settings=_settings(), artifact_store=store)
        out = _apply(b, "s", "hello world")
        assert out.content == "hello world"
        assert out.truncated is False
        assert out.artifact is None
        assert store.puts == []

    def test_dict_serialized_as_stable_json(self) -> None:
        b = ToolOutputBudget(settings=_settings(), artifact_store=RecordingStore())
        out = _apply(b, "s", {"b": 1, "a": 2})
        assert out.content == '{"a":2,"b":1}'

    def test_other_objects_use_str(self) -> None:
        b = ToolOutputBudget(settings=_settings(), artifact_store=RecordingStore())
        assert _apply(b, "s", 42).content == "42"


class TestOverBudget:
    def test_excerpt_within_budget_including_marker(self) -> None:
        store = RecordingStore()
        b = ToolOutputBudget(settings=_settings(), artifact_store=store)
        text = "".join(f"line {i}\n" for i in range(2000))
        out = _apply(b, "search", text)
        assert out.truncated is True
        assert out.original_tokens == estimate_tokens(text)
        assert estimate_tokens(out.content) <= 100
        assert "[truncated:" in out.content
        assert out.artifact is not None
        assert f"artifact={out.artifact.id}" in out.content
        assert "read_artifact" in out.content

    def test_artifact_stored_with_full_text(self) -> None:
        store = RecordingStore()
        b = ToolOutputBudget(settings=_settings(), artifact_store=store)
        text = "a" * 5000
        out = _apply(b, "search", text)
        assert len(store.puts) == 1
        content, kw = store.puts[0]
        assert content == text
        assert kw["kind"] == "tool_output"
        assert kw["tool"] == "search"
        assert kw["run_id"] == "r1"
        assert kw["tenant"] == "t1"
        assert out.artifact is not None
        sl = store.get(out.artifact.id, run_id="r1", tenant="t1")
        assert sl.content == text

    def test_head_tail_proportions(self) -> None:
        b = ToolOutputBudget(settings=_settings(), artifact_store=RecordingStore())
        text = "H" * 3000 + "T" * 3000
        out = _apply(b, "s", text)
        head, _, rest = out.content.partition("\n[truncated:")
        tail = rest.partition("]\n")[2]
        assert set(head) == {"H"}
        assert set(tail) == {"T"}
        ratio = len(head) / (len(head) + len(tail))
        assert 0.55 <= ratio <= 0.65

    def test_marker_omitted_count(self) -> None:
        b = ToolOutputBudget(settings=_settings(), artifact_store=RecordingStore())
        text = "z" * 4000
        out = _apply(b, "s", text)
        assert f"[truncated: {estimate_tokens(text)} tokens" in out.content
        assert "omitted;" in out.content

    def test_per_tool_override_applied(self) -> None:
        b = ToolOutputBudget(
            settings=_settings(tool_output_max_tokens_by_tool={"tiny": 1000}),
            artifact_store=RecordingStore(),
        )
        text = "q" * 2000  # 500 tokens: over default 100, under override 1000
        assert _apply(b, "tiny", text).truncated is False
        assert _apply(b, "other", text).truncated is True

    def test_store_failure_keeps_excerpt(self) -> None:
        b = ToolOutputBudget(settings=_settings(), artifact_store=BrokenStore())
        out = _apply(b, "s", "w" * 5000)
        assert out.truncated is True
        assert out.artifact is None
        assert "artifact=none" in out.content
        assert estimate_tokens(out.content) <= 100

    def test_budget_zero_only_marker(self) -> None:
        b = ToolOutputBudget(
            settings=_settings(tool_output_max_tokens_default=0), artifact_store=RecordingStore()
        )
        out = _apply(b, "s", "k" * 100)
        assert out.truncated is True
        assert out.content.strip().startswith("[truncated:")
        # The artifact id is random urlsafe text and may contain "k": drop it first.
        marker = re.sub(r"artifact=\S+;", "artifact=;", out.content)
        assert "k" not in marker.replace("tokens", "")

    def test_lazy_store_built_from_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import prismal.agents.tool_output_budget as mod

        built = RecordingStore()
        calls: list[Settings | None] = []

        def fake_build(settings: Settings | None = None) -> RecordingStore:
            calls.append(settings)
            return built

        monkeypatch.setattr(mod, "build_artifact_store", fake_build)
        s = _settings()
        b = ToolOutputBudget(settings=s)
        _apply(b, "s", "m" * 5000)
        _apply(b, "s", "m" * 5000)
        assert calls == [s]
        assert len(built.puts) == 2


class TestToon:
    ROWS = [{"id": i, "name": f"n{i}"} for i in range(3)]

    def test_uniform_table_toon_when_enabled(self) -> None:
        b = ToolOutputBudget(
            settings=_settings(tool_output_toon_enabled=True), artifact_store=RecordingStore()
        )
        out = _apply(b, "s", self.ROWS)
        assert out.content.startswith("items[3]{id,name}:")

    def test_uniform_table_json_when_toon_disabled(self) -> None:
        b = ToolOutputBudget(settings=_settings(), artifact_store=RecordingStore())
        out = _apply(b, "s", self.ROWS)
        assert json.loads(out.content) == self.ROWS

    def test_nested_stays_json(self) -> None:
        b = ToolOutputBudget(
            settings=_settings(tool_output_toon_enabled=True), artifact_store=RecordingStore()
        )
        nested = [{"id": 1, "tags": ["a"]}, {"id": 2, "tags": ["b"]}]
        out = _apply(b, "s", nested)
        assert json.loads(out.content) == nested


class TestCounters:
    def test_counters_emitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from prismal.monitoring import otel

        seen: list[tuple[str, dict[str, Any] | None]] = []

        def fake_inc(self: Any, metric: str, value: int = 1, attributes: Any = None) -> None:
            seen.append((metric, attributes))

        monkeypatch.setattr(otel.OTelManager, "increment_counter", fake_inc)
        b = ToolOutputBudget(
            settings=_settings(tool_output_toon_enabled=True), artifact_store=BrokenStore()
        )
        _apply(b, "web", "v" * 5000)
        _apply(b, "web", TestToon.ROWS)
        assert ("tool_output_truncated", {"tool": "web"}) in seen
        assert any(m == "toon_encoded" for m, _ in seen)

    def test_counter_failure_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from prismal.monitoring import otel

        def boom(self: Any, *a: Any, **kw: Any) -> None:
            raise RuntimeError("otel down")

        monkeypatch.setattr(otel.OTelManager, "increment_counter", boom)
        b = ToolOutputBudget(
            settings=_settings(tool_output_toon_enabled=True), artifact_store=RecordingStore()
        )
        assert _apply(b, "web", "v" * 5000).truncated is True
        assert _apply(b, "web", TestToon.ROWS).content.startswith("items[")
