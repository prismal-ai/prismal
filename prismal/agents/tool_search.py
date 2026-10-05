"""Deferred tool loading + BM25 tool search (Phase TKE — SPEC-TKE-DEF-001).

When an agent has more tools than a threshold, only a small *core* set is
exposed up front; the rest are *deferred* and discoverable through a
``search_tools`` meta-tool backed by a pure-Python, deterministic BM25 index
over each tool's name + description. Activated tools are appended to the
exposed list (append-only, so the serialized tool prefix stays byte-stable
for provider prompt caching).

Providers that support server-side tool search natively (Anthropic) can
instead receive every schema marked ``defer_loading`` — see
:func:`supports_native_tool_search` and :func:`mark_deferred_native`.

This module is dependency-free beyond ``langchain_core``: no provider SDK,
no ``litellm``, no ``rank_bm25``, and no ``prismal.mcp`` / ``prismal.skills``.
"""

from __future__ import annotations

import contextlib
import copy
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from prismal.core.config import Settings

_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
_MAX_DESC_CHARS = 200
_NO_MATCH = "no matching tools"


def _tokenize(text: str) -> list[str]:
    """Lowercase tokens split on non-alphanumerics, underscores and camelCase (SPEC-TKE-DEF-001)."""
    tokens: list[str] = []
    for chunk in _SPLIT_RE.split(text):
        if chunk:
            tokens.extend(part.lower() for part in _CAMEL_RE.findall(chunk))
    return tokens


def _otel() -> Any:
    """Return the OTel manager (seam for tests) (Phase TKE — SPEC-TKE-DEF-001)."""
    from prismal.monitoring.otel import OTelManager

    return OTelManager()


def _count(metric: str, value: int, agent: str) -> None:
    """Best-effort counter increment; never raises (Phase TKE — SPEC-TKE-DEF-001)."""
    with contextlib.suppress(Exception):
        _otel().increment_counter(metric, value=value, attributes={"agent": agent})


@dataclass(frozen=True)
class ToolSearchHit:
    """One ranked search result (Phase TKE — SPEC-TKE-DEF-001).

    ``schema`` is the OpenAI-format tool dict from ``convert_to_openai_tool``.
    """

    name: str
    score: float
    schema: dict[str, Any]


class ToolSearchIndex:
    """Okapi BM25 index over tool name + description (Phase TKE — SPEC-TKE-DEF-001).

    IDF uses the non-negative variant ``ln((N - df + 0.5) / (df + 0.5) + 1)``.
    Ranking is deterministic: score descending, then name ascending; zero
    scores are excluded.
    """

    def __init__(
        self,
        tools: Sequence[BaseTool],
        *,
        k1: float = 1.5,
        b: float = 0.75,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._k1 = k1
        self._b = b
        self._clock = clock
        self._tools: tuple[BaseTool, ...] = ()
        self._tfs: list[Counter[str]] = []
        self._lens: list[int] = []
        self._df: Counter[str] = Counter()
        self._avgdl = 0.0
        self._built_at = 0.0
        self._build(tools)

    def _build(self, tools: Sequence[BaseTool]) -> None:
        self._tools = tuple(tools)
        self._tfs = []
        self._lens = []
        self._df = Counter()
        for tool in self._tools:
            tokens = _tokenize(tool.name) + _tokenize(tool.description or "")
            tf = Counter(tokens)
            self._tfs.append(tf)
            self._lens.append(len(tokens))
            self._df.update(tf.keys())
        self._avgdl = (sum(self._lens) / len(self._lens)) if self._lens else 0.0
        self._built_at = self._clock()

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Names of the indexed tools in insertion order (Phase TKE — SPEC-TKE-DEF-001)."""
        return tuple(t.name for t in self._tools)

    def tool(self, name: str) -> BaseTool | None:
        """Return the indexed tool named *name*, if any (Phase TKE — SPEC-TKE-DEF-001)."""
        for t in self._tools:
            if t.name == name:
                return t
        return None

    def refresh(self, tools: Sequence[BaseTool], *, ttl_ms: int | None = None) -> None:
        """Rebuild the index; skip if *ttl_ms* is given and the index is still fresh.

        Seam for MCP ``ttlMs`` tool-list caching (Phase TKE — SPEC-TKE-DEF-001).
        """
        if ttl_ms is not None and (self._clock() - self._built_at) * 1000.0 < ttl_ms:
            return
        self._build(tools)

    def _score(self, doc: int, query: Sequence[str]) -> float:
        n_docs = len(self._tools)
        tf_doc = self._tfs[doc]
        norm = self._k1 * (1.0 - self._b + self._b * self._lens[doc] / self._avgdl)
        score = 0.0
        for term in query:
            tf = tf_doc.get(term, 0)
            if not tf:
                continue
            df = self._df[term]
            idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
            score += idf * tf * (self._k1 + 1.0) / (tf + norm)
        return score

    def search(self, query: str, k: int = 5) -> list[ToolSearchHit]:
        """Return the top-*k* tools for *query* (Phase TKE — SPEC-TKE-DEF-001)."""
        if k <= 0 or not self._tools:
            return []
        terms = _tokenize(query)
        if not terms:
            return []
        scored = [(self._score(i, terms), t) for i, t in enumerate(self._tools)]
        ranked = sorted(((s, t) for s, t in scored if s > 0.0), key=lambda st: (-st[0], st[1].name))
        return [
            ToolSearchHit(name=t.name, score=s, schema=dict(convert_to_openai_tool(t)))
            for s, t in ranked[:k]
        ]


def _compact_description(tool: BaseTool | None) -> str:
    text = " ".join((tool.description if tool is not None else "").split())
    return text[:_MAX_DESC_CHARS]


def make_search_tools_tool(
    index: ToolSearchIndex,
    activate: Callable[[Sequence[str]], object],
    *,
    agent: str = "",
) -> BaseTool:
    """Build the ``search_tools`` meta-tool over *index* (Phase TKE — SPEC-TKE-DEF-001).

    Each call runs a BM25 search, activates the hit names (when any), counts
    ``tool_search_calls`` and returns one ``name: description`` line per hit.
    """

    def search_tools(query: str, k: int = 5) -> str:
        """Search the deferred tool catalogue by keywords and activate matching tools."""
        hits = index.search(query, k=k)
        _count("tool_search_calls", 1, agent)
        if not hits:
            return _NO_MATCH
        activate([h.name for h in hits])
        return "\n".join(f"{h.name}: {_compact_description(index.tool(h.name))}" for h in hits)

    return StructuredTool.from_function(
        func=search_tools,
        name="search_tools",
        description=(
            "Search for additional tools by keywords (name/description). "
            "Matching tools become available for subsequent calls."
        ),
    )


def split_core_and_deferred(
    tools: Sequence[BaseTool], *, core: frozenset[str], threshold: int
) -> tuple[list[BaseTool], list[BaseTool]]:
    """Split *tools* into (core, deferred) when above *threshold* (Phase TKE — SPEC-TKE-DEF-001).

    At or below the threshold every tool is core (order unchanged); otherwise
    both lists are sorted by name.
    """
    if len(tools) <= threshold:
        return list(tools), []
    core_list = sorted((t for t in tools if t.name in core), key=lambda t: t.name)
    deferred = sorted((t for t in tools if t.name not in core), key=lambda t: t.name)
    return core_list, deferred


class DeferredToolSet:
    """Per-run exposed tool list with append-only activation (Phase TKE — SPEC-TKE-DEF-001).

    ``exposed()`` is the core tools (given order) followed by activated
    deferred tools in activation order; nothing is ever reordered or removed.
    """

    def __init__(
        self,
        core: Sequence[BaseTool],
        deferred: Sequence[BaseTool],
        *,
        agent: str = "",
    ) -> None:
        self._core: tuple[BaseTool, ...] = tuple(core)
        self._deferred: dict[str, BaseTool] = {t.name: t for t in deferred}
        self._active: list[BaseTool] = []
        self._active_names: set[str] = set()
        self._agent = agent
        self.index = ToolSearchIndex(list(deferred))

    def exposed(self) -> list[BaseTool]:
        """Core tools followed by activated deferred tools (Phase TKE — SPEC-TKE-DEF-001)."""
        return [*self._core, *self._active]

    def activate(self, names: Sequence[str]) -> list[str]:
        """Activate deferred tools by name; return newly activated names (SPEC-TKE-DEF-001)."""
        added: list[str] = []
        for name in names:
            tool = self._deferred.get(name)
            if tool is None or name in self._active_names:
                continue
            self._active.append(tool)
            self._active_names.add(name)
            added.append(name)
        if added:
            _count("tool_deferred_activated", len(added), self._agent)
        return added

    def search_tool(self) -> BaseTool:
        """The ``search_tools`` meta-tool wired to this set (Phase TKE — SPEC-TKE-DEF-001)."""
        return make_search_tools_tool(self.index, self.activate, agent=self._agent)


_NATIVE_PREFIXES: tuple[str, ...] = ("anthropic/", "openrouter/anthropic/")


def supports_native_tool_search(model: str) -> bool:
    """Whether *model* supports provider-native deferred tool search (SPEC-TKE-DEF-001).

    Capability table: the Anthropic family only — verified TKE0-04 against
    litellm 1.86.2 Anthropic transform: ``defer_loading`` +
    ``tool_search_tool_bm25_20251119``.
    """
    m = model.strip().lower()
    if not m:
        return False
    return m.startswith(_NATIVE_PREFIXES) or "claude" in m


def mark_deferred_native(schemas: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep-copy OpenAI-format tool dicts with ``defer_loading=True`` (SPEC-TKE-DEF-001)."""
    marked: list[dict[str, Any]] = []
    for schema in schemas:
        clone = copy.deepcopy(schema)
        clone["defer_loading"] = True
        marked.append(clone)
    return marked


@dataclass(frozen=True)
class DeferredPlan:
    """How an agent's tools are exposed for one run (Phase TKE — TKE3-05..07).

    ``exposed`` is what the model is bound to initially; ``deferred_set`` is set
    on the meta-tool path (``search_tools`` + append-only activation);
    ``native`` marks the provider-native ``defer_loading`` path.
    """

    exposed: list[BaseTool]
    deferred_set: DeferredToolSet | None = None
    native: bool = False
    deferred_names: frozenset[str] = frozenset()

    def bind_schemas(self) -> list[dict[str, Any]]:
        """OpenAI-format schemas; deferred tools carry ``defer_loading`` on the native path."""
        from langchain_core.utils.function_calling import convert_to_openai_tool

        core = [
            convert_to_openai_tool(t) for t in self.exposed if t.name not in self.deferred_names
        ]
        deferred = [
            convert_to_openai_tool(t) for t in self.exposed if t.name in self.deferred_names
        ]
        return [*core, *(mark_deferred_native(deferred) if self.native else deferred)]


def plan_deferred_tools(
    tools: Sequence[BaseTool], *, settings: Settings, agent: str, model: str
) -> DeferredPlan:
    """Decide the exposure plan (Phase TKE — SPEC-TKE-DEF-001).

    Disabled (master off / ``tool_defer_enabled`` off) or at/below
    ``tool_defer_threshold`` ⇒ every tool exposed (pre-TKE behaviour). Above the
    threshold: provider-native deferral when supported, else core tools plus the
    ``search_tools`` meta-tool.
    """
    all_tools = list(tools)
    if not (settings.token_efficiency_enabled and settings.tool_defer_enabled):
        return DeferredPlan(exposed=all_tools)
    core, deferred = split_core_and_deferred(
        all_tools, core=frozenset(settings.tool_defer_core), threshold=settings.tool_defer_threshold
    )
    if not deferred:
        return DeferredPlan(exposed=all_tools)
    if supports_native_tool_search(model):
        return DeferredPlan(
            exposed=[*core, *deferred],
            native=True,
            deferred_names=frozenset(t.name for t in deferred),
        )
    deferred_set = DeferredToolSet(core, deferred, agent=agent)
    return DeferredPlan(exposed=[*core, deferred_set.search_tool()], deferred_set=deferred_set)


__all__ = [
    "DeferredPlan",
    "DeferredToolSet",
    "ToolSearchHit",
    "ToolSearchIndex",
    "make_search_tools_tool",
    "mark_deferred_native",
    "plan_deferred_tools",
    "split_core_and_deferred",
    "supports_native_tool_search",
]
