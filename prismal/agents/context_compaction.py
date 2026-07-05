"""Persisted-context compaction (Phase LH — SPEC-LH-CTX-001/002/003).

``ContextCompactor`` trims/summarizes ``AgentState.messages`` when a
configurable message-count or (when ``budget_enabled``) cumulative-token
threshold is exceeded, keeping the most recent N messages verbatim. Compaction
is expressed as a state update using LangGraph ``RemoveMessage`` entries —
never in-place mutation of the history list (DD-LH-001).

Ordering caveat (deviation from the spec's illustrative diagram): LangGraph's
``add_messages`` reducer only ever *appends* new (non-removal) entries to the
end of the surviving history — it cannot splice a new message in at the
position of the segment it replaces. In ``summarize`` mode the summary message
therefore lands *after* the kept-recent tail in ``state["messages"]``, not
before it. This is a real constraint of the reducer, not an oversight; nodes
that build their own prompt from ``state["messages"]`` already reorder/window
it themselves (DD-LH-002), so this does not surface as a literal
out-of-chronological-order read in any LLM prompt.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, RemoveMessage

from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from langchain_core.messages import BaseMessage

    from prismal.agents.state import AgentState
    from prismal.budget.guard import BudgetGuard
    from prismal.core.config import Settings

logger = get_logger("prismal.agents.context_compaction")


class CompactionStrategy(StrEnum):
    """Compaction strategy — truncate (no LLM call) or summarize (opt-in)."""

    TRUNCATE = "truncate"
    SUMMARIZE = "summarize"


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of one compaction attempt.

    A no-op result (``compacted=False``) always carries ``messages_dropped=0``,
    ``removed_ids=()``, ``summary_message=None`` — unambiguous and cheap to check.
    """

    compacted: bool
    strategy: CompactionStrategy
    messages_dropped: int
    removed_ids: tuple[str, ...]
    summary_message: BaseMessage | None = None
    reason: str = ""


class ContextCompactor:
    """Trims/summarizes a message history when a configured threshold is exceeded."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        summarizer_fn: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
        budget_guard: BudgetGuard | None = None,
    ) -> None:
        """Initialize the compactor."""
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._summarizer_fn = summarizer_fn
        self._budget_guard = budget_guard

    def should_compact(self, messages: Sequence[BaseMessage]) -> tuple[bool, str]:
        """Return ``(should_compact, reason)``. Never raises."""
        if len(messages) > self._settings.context_compaction_max_messages:
            return True, "message_count"
        with suppress(Exception):
            if (
                self._budget_guard is not None
                and self._settings.context_compaction_token_threshold > 0
                and self._budget_guard.meter.usage.total_tokens
                > self._settings.context_compaction_token_threshold
            ):
                return True, "token_threshold"
        return False, ""

    async def compact(self, messages: Sequence[BaseMessage]) -> CompactionResult:
        """Split *messages* into an older segment and a verbatim recent tail.

        ``TRUNCATE``: drops the older segment, no LLM call. ``SUMMARIZE``:
        replaces it with one LLM-generated summary message (metered via
        ``budget_guard`` when the default summarizer resolves it). On any
        summarizer failure, falls back to ``TRUNCATE`` for this call. Never raises.
        """
        strategy = CompactionStrategy(self._settings.context_compaction_strategy)
        keep_recent = self._settings.context_compaction_keep_recent

        if keep_recent >= len(messages):
            return CompactionResult(
                compacted=False, strategy=strategy, messages_dropped=0, removed_ids=()
            )

        older = messages[:-keep_recent] if keep_recent > 0 else messages
        removed_ids = tuple(str(m.id) for m in older if getattr(m, "id", None))

        if not removed_ids:
            return CompactionResult(
                compacted=False, strategy=strategy, messages_dropped=0, removed_ids=()
            )

        if strategy is CompactionStrategy.SUMMARIZE:
            summary_message = await self._try_summarize(list(older))
            if summary_message is not None:
                result = CompactionResult(
                    compacted=True,
                    strategy=CompactionStrategy.SUMMARIZE,
                    messages_dropped=len(removed_ids),
                    removed_ids=removed_ids,
                    summary_message=summary_message,
                )
                self._record_compaction(result)
                return result
            # Summarizer failed — fall back to truncate for this call.
            with suppress(Exception):
                OTelManager().increment_counter("context_compaction_summarize_errors")

        result = CompactionResult(
            compacted=True,
            strategy=CompactionStrategy.TRUNCATE,
            messages_dropped=len(removed_ids),
            removed_ids=removed_ids,
        )
        self._record_compaction(result)
        return result

    def to_state_update(self, result: CompactionResult) -> dict[str, list[BaseMessage]]:
        """Return the reducer-safe state update for *result*, or ``{}`` when a no-op."""
        if not result.compacted:
            return {}
        updates: list[BaseMessage] = [RemoveMessage(id=i) for i in result.removed_ids]
        if result.summary_message is not None:
            updates.append(result.summary_message)
        return {"messages": updates}

    async def compact_list(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """Trim/summarize a plain in-memory message list — position-based.

        For an ephemeral list that has not round-tripped through the
        ``add_messages`` reducer (e.g. ``react_loop``'s local accumulator),
        messages typically lack a stable ``id``, so this reconstructs a new
        list directly by position rather than via :meth:`to_state_update`'s
        ``RemoveMessage``-by-id mechanism. Never mutates *messages*; always
        returns a new list. Returns *messages* unchanged (as a list) when not due.
        """
        should, _reason = self.should_compact(messages)
        if not should:
            return list(messages)

        strategy = CompactionStrategy(self._settings.context_compaction_strategy)
        keep_recent = self._settings.context_compaction_keep_recent
        if keep_recent >= len(messages):
            return list(messages)

        older = messages[:-keep_recent] if keep_recent > 0 else list(messages)
        tail = list(messages[-keep_recent:]) if keep_recent > 0 else []
        if not older:
            return list(messages)

        if strategy is CompactionStrategy.SUMMARIZE:
            summary_message = await self._try_summarize(list(older))
            if summary_message is not None:
                self._record_compaction(
                    CompactionResult(
                        compacted=True,
                        strategy=CompactionStrategy.SUMMARIZE,
                        messages_dropped=len(older),
                        removed_ids=(),
                    )
                )
                return [summary_message, *tail]
            with suppress(Exception):
                OTelManager().increment_counter("context_compaction_summarize_errors")

        self._record_compaction(
            CompactionResult(
                compacted=True,
                strategy=CompactionStrategy.TRUNCATE,
                messages_dropped=len(older),
                removed_ids=(),
            )
        )
        return tail

    # ── internals ──────────────────────────────────────────────────────────────

    async def _try_summarize(self, older: list[BaseMessage]) -> BaseMessage | None:
        summarizer_fn = self._summarizer_fn or self._default_summarizer_fn
        try:
            summary_text = await summarizer_fn(older)
        except Exception as exc:
            logger.warning("context_compaction_summarize_failed", error=str(exc)[:200])
            return None
        return AIMessage(
            content=f"[Compacted summary of {len(older)} earlier messages]: {summary_text}"
        )

    async def _default_summarizer_fn(self, older: list[BaseMessage]) -> str:
        from prismal.providers.registry import ProviderRegistry
        from prismal.security.prompt_builder import SecurePromptBuilder

        system = (
            "Summarize the following conversation history concisely, "
            "preserving key facts and decisions."
        )
        text = "\n".join(f"{getattr(m, 'type', 'message')}: {m.content}" for m in older)
        messages = SecurePromptBuilder().build(system=system, user=text)

        llm = ProviderRegistry(settings=self._settings).get_llm(
            self._settings.context_compaction_summarizer_model
        )
        response = await llm.ainvoke(messages)
        if self._budget_guard is not None:
            with suppress(Exception):
                self._budget_guard.meter.record_response(
                    response,
                    self._settings.context_compaction_summarizer_model
                    or self._settings.default_model,
                    agent="context_compactor",
                )
        content = getattr(response, "content", response)
        return str(content)

    def _record_compaction(self, result: CompactionResult) -> None:
        with suppress(Exception):
            OTelManager().increment_counter(
                "context_compactions", attributes={"strategy": result.strategy.value}
            )
            OTelManager().increment_counter(
                "context_compaction_messages_dropped", value=result.messages_dropped
            )


# ---------------------------------------------------------------------------
# Per-run seeding (SPEC-LH-CTX-003) — mirrors budget/resolve.py exactly.
# ---------------------------------------------------------------------------

# session_id -> {"compactor": ContextCompactor, "turn": int, "compacted_at": int|None}.
# In-process only — never placed in checkpointed state.
_RUN_ENGINES: dict[str, dict[str, object]] = {}

_DEFAULT_KEY = "_default"


def _run_key(state: object) -> str:
    if isinstance(state, dict):
        sid = state.get("session_id")
        if sid:
            return str(sid)
    return _DEFAULT_KEY


def _turn_signature(state: object) -> int:
    messages = state.get("messages") if isinstance(state, dict) else None
    if not isinstance(messages, list):
        return 0
    return sum(1 for m in messages if getattr(m, "type", None) == "human")


def seed_context_compaction_run(state: AgentState, settings: Settings) -> None:
    """Install the per-run :class:`ContextCompactor` in the registry.

    No-op when ``settings.context_compaction_enabled`` is False, so the
    disabled path carries zero state and is byte-for-byte unchanged.
    """
    if not settings.context_compaction_enabled:
        return
    from prismal.budget.resolve import get_budget_guard

    key = _run_key(state)
    compactor = ContextCompactor(settings=settings, budget_guard=get_budget_guard(state))
    _RUN_ENGINES[key] = {
        "compactor": compactor,
        "turn": _turn_signature(state),
        "last_evaluated_len": None,
    }
    metadata = state.setdefault("metadata", {})
    loop_meta = metadata.setdefault("loop", {})
    loop_meta["compaction"] = {
        "enabled": True,
        "strategy": settings.context_compaction_strategy,
    }


def maybe_seed_context_compaction_run(state: AgentState, settings: Settings) -> None:
    """Seed once per turn — idempotent across the supervisor's hops. No-op when off."""
    if not settings.context_compaction_enabled:
        return
    existing = _RUN_ENGINES.get(_run_key(state))
    if existing is not None and existing.get("turn") == _turn_signature(state):
        return
    seed_context_compaction_run(state, settings)


def get_context_compactor(state: AgentState) -> ContextCompactor | None:
    """Return the per-run :class:`ContextCompactor`, or None when disabled/unseeded."""
    bucket = _RUN_ENGINES.get(_run_key(state))
    if not bucket:
        return None
    compactor = bucket.get("compactor")
    return compactor if isinstance(compactor, ContextCompactor) else None


def clear_context_compaction_run(state: AgentState) -> None:
    """Release the per-run entry for *state* (idempotent)."""
    _RUN_ENGINES.pop(_run_key(state), None)


def context_compaction_react_kwargs(state: AgentState) -> dict[str, ContextCompactor]:
    """Return the ``react_loop`` kwargs for *state* (mirrors ``hardening_react_kwargs``).

    Returns an **empty dict** when disabled/unseeded, so splatting this into
    ``react_loop(**context_compaction_react_kwargs(state))`` passes no extra
    kwargs at all — the disabled call is byte-for-byte identical to before.
    """
    compactor = get_context_compactor(state)
    if compactor is None:
        return {}
    return {"context_compactor": compactor}


async def maybe_compact_context(
    state: AgentState, settings: Settings
) -> dict[str, list[BaseMessage]]:
    """Compact ``state["messages"]`` if due, honoring the min-interval watermark.

    Returns ``{}`` when disabled/unseeded, below threshold, or too soon since
    the last compaction was *evaluated* against this same (still-unreduced)
    message list — this is what prevents re-compacting the same segment when
    called twice before the ``add_messages`` reducer has applied the previous
    result (e.g. two supervisor hops with no new messages in between).

    The watermark compares the *raw* list length, not a post-compaction
    count: once the reducer actually shrinks ``state["messages"]``, the new
    (smaller) length trivially falls below the last-evaluated length and the
    gate is skipped — so a real drop in size (from an applied compaction)
    never blocks a later, genuine re-compaction.
    """
    compactor = get_context_compactor(state)
    if compactor is None:
        return {}

    messages = state["messages"]
    should, _reason = compactor.should_compact(messages)
    if not should:
        return {}

    bucket = _RUN_ENGINES.get(_run_key(state))
    last_evaluated_len = bucket.get("last_evaluated_len") if bucket else None
    if (
        isinstance(last_evaluated_len, int)
        and len(messages) >= last_evaluated_len
        and len(messages) - last_evaluated_len < settings.context_compaction_min_interval_messages
    ):
        return {}

    result = await compactor.compact(messages)
    if not result.compacted:
        return {}
    if bucket is not None:
        bucket["last_evaluated_len"] = len(messages)
    return compactor.to_state_update(result)


__all__ = [
    "CompactionResult",
    "CompactionStrategy",
    "ContextCompactor",
    "clear_context_compaction_run",
    "context_compaction_react_kwargs",
    "get_context_compactor",
    "maybe_compact_context",
    "maybe_seed_context_compaction_run",
    "seed_context_compaction_run",
]
