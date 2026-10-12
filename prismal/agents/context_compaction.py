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

Phase TKE deltas (gated by ``token_efficiency_enabled``; master off ⇒ LH exactly):

* SPEC-TKE-MSK-001 — ``MASK_OBSERVATIONS`` strategy: old ``ToolMessage``
  payloads are moved to the artifact store and replaced *in place* by an
  :data:`OBSERVATION_STUB` (same ``id``/``tool_call_id`` so ``add_messages``
  substitutes them at their original position — no ``RemoveMessage``).
* SPEC-TKE-MSK-002 — hysteresis: when ``context_compaction_high_water > 0`` the
  trigger is the estimated token size of the history; compaction proceeds down
  to ``context_compaction_low_water`` (compact once per high/low band).
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict, cast

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from prismal.agents.artifacts import anonymous_run_id, shared_artifact_store
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.utils.token_estimate import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from langchain_core.messages import BaseMessage

    from prismal.agents.artifacts import ArtifactStorePort
    from prismal.agents.state import AgentState
    from prismal.budget.guard import BudgetGuard
    from prismal.core.config import Settings

logger = get_logger("prismal.agents.context_compaction")


class CompactionStrategy(StrEnum):
    """Compaction strategy — truncate (no LLM call), summarize, or mask observations."""

    TRUNCATE = "truncate"
    SUMMARIZE = "summarize"
    MASK_OBSERVATIONS = "mask_observations"  # SPEC-TKE-MSK-001


#: Replacement content for a masked observation (SPEC-TKE-MSK-001).
OBSERVATION_STUB = "[observation elided: tool={tool} artifact={artifact_id} tokens≈{tokens}]"
_MASKED_PREFIX = "[observation elided:"

#: Optional relevance hook (SPEC-TKE-MSK-001): ``(observation, history) -> score``.
type RelevanceFn = Callable[[ToolMessage, Sequence[BaseMessage]], float]


def _estimate(messages: Sequence[BaseMessage]) -> int:
    """Estimated token size of *messages* (SPEC-TKE-MSK-002)."""
    return sum(estimate_tokens(str(m.content)) for m in messages)


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
    replaced: tuple[BaseMessage, ...] = ()  # SPEC-TKE-MSK-001 in-place replacements


class ContextCompactor:
    """Trims/summarizes/masks a message history when a configured threshold is exceeded."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        summarizer_fn: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
        budget_guard: BudgetGuard | None = None,
        artifact_store: ArtifactStorePort | None = None,
        relevance_fn: RelevanceFn | None = None,
    ) -> None:
        """Initialize the compactor (``artifact_store``/``relevance_fn``: SPEC-TKE-MSK-001)."""
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._summarizer_fn = summarizer_fn
        self._budget_guard = budget_guard
        self._artifact_store = artifact_store
        self._relevance_fn = relevance_fn

    # ── TKE gating ────────────────────────────────────────────────────────────

    def _effective_strategy(self) -> CompactionStrategy:
        """Configured strategy; ``mask_observations`` degrades to truncate with TKE off."""
        strategy = CompactionStrategy(self._settings.context_compaction_strategy)
        if (
            strategy is CompactionStrategy.MASK_OBSERVATIONS
            and not self._settings.token_efficiency_enabled
        ):
            return CompactionStrategy.TRUNCATE
        return strategy

    def _hysteresis_active(self) -> bool:
        """True when the SPEC-TKE-MSK-002 high/low-water trigger replaces the LH one."""
        return (
            self._settings.token_efficiency_enabled
            and self._settings.context_compaction_high_water > 0
        )

    def _low_water(self) -> int | None:
        return self._settings.context_compaction_low_water if self._hysteresis_active() else None

    # ── trigger ───────────────────────────────────────────────────────────────

    def should_compact(self, messages: Sequence[BaseMessage]) -> tuple[bool, str]:
        """Return ``(should_compact, reason)``. Never raises.

        SPEC-TKE-MSK-002: with hysteresis active the only trigger is the
        estimated history size exceeding ``context_compaction_high_water``;
        otherwise the LH trigger (SPEC-LH-CTX-001) is used unchanged.
        """
        if self._hysteresis_active():
            if _estimate(messages) > self._settings.context_compaction_high_water:
                return True, "high_water"
            return False, ""
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

    def _older_count(self, messages: Sequence[BaseMessage]) -> int:
        """Number of leading messages forming the "older" segment.

        LH: everything but the ``keep_recent`` tail. Hysteresis (SPEC-TKE-MSK-002):
        the shortest prefix whose removal brings the estimate to ≤ low water,
        capped so at least ``keep_recent`` messages always remain.
        """
        keep_recent = self._settings.context_compaction_keep_recent
        max_drop = len(messages) - keep_recent if keep_recent > 0 else len(messages)
        low_water = self._low_water()
        if low_water is None:
            return max_drop
        remaining = _estimate(messages)
        drop = 0
        while drop < max_drop and remaining > low_water:
            remaining -= estimate_tokens(str(messages[drop].content))
            drop += 1
        return drop

    # ── compaction ────────────────────────────────────────────────────────────

    async def compact(
        self,
        messages: Sequence[BaseMessage],
        *,
        run_id: str | None = None,
        tenant: str = "default",
    ) -> CompactionResult:
        """Split *messages* into an older segment and a verbatim recent tail.

        ``TRUNCATE``: drops the older segment, no LLM call. ``SUMMARIZE``:
        replaces it with one LLM-generated summary message (metered via
        ``budget_guard`` when the default summarizer resolves it). On any
        summarizer failure, falls back to ``TRUNCATE`` for this call.
        ``MASK_OBSERVATIONS`` (TKE on): delegates to :meth:`mask_observations`
        scoped to ``run_id``/``tenant`` (SPEC-TKE-MSK-001). Never raises.
        """
        strategy = self._effective_strategy()
        if strategy is CompactionStrategy.MASK_OBSERVATIONS:
            return await self.mask_observations(
                messages, run_id=run_id or anonymous_run_id(), tenant=tenant
            )
        keep_recent = self._settings.context_compaction_keep_recent

        if keep_recent >= len(messages):
            return CompactionResult(
                compacted=False, strategy=strategy, messages_dropped=0, removed_ids=()
            )

        older = messages[: self._older_count(messages)]
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

    async def mask_observations(
        self, messages: Sequence[BaseMessage], *, run_id: str, tenant: str
    ) -> CompactionResult:
        """Mask old tool observations in place (SPEC-TKE-MSK-001).

        Every ``ToolMessage`` before the last ``context_mask_keep_turns``
        human/AI turns has its payload stored in the artifact store first and
        is then replaced by an :data:`OBSERVATION_STUB` carrying the same
        ``id``/``tool_call_id``. With hysteresis active (SPEC-TKE-MSK-002)
        masking proceeds oldest-first only until the estimate is ≤ low water.
        Never raises.
        """
        replacements = self._mask(messages, run_id=run_id, tenant=tenant, require_id=True)
        replaced = tuple(new for _idx, new in replacements)
        result = CompactionResult(
            compacted=bool(replaced),
            strategy=CompactionStrategy.MASK_OBSERVATIONS,
            messages_dropped=0,
            removed_ids=(),
            replaced=replaced,
        )
        if replaced:
            self._record_compaction(result)
        return result

    def to_state_update(self, result: CompactionResult) -> dict[str, list[BaseMessage]]:
        """Return the reducer-safe state update for *result*, or ``{}`` when a no-op.

        Order: ``RemoveMessage`` entries, then the summary (LH), then in-place
        replacements (SPEC-TKE-MSK-001 — mask mode produces only the latter).
        """
        if not result.compacted:
            return {}
        updates: list[BaseMessage] = [RemoveMessage(id=i) for i in result.removed_ids]
        if result.summary_message is not None:
            updates.append(result.summary_message)
        updates.extend(result.replaced)
        return {"messages": updates}

    async def compact_list(
        self,
        messages: Sequence[BaseMessage],
        *,
        run_id: str | None = None,
        tenant: str = "default",
    ) -> list[BaseMessage]:
        """Trim/summarize a plain in-memory message list — position-based.

        For an ephemeral list that has not round-tripped through the
        ``add_messages`` reducer (e.g. ``react_loop``'s local accumulator),
        messages typically lack a stable ``id``, so this reconstructs a new
        list directly by position rather than via :meth:`to_state_update`'s
        ``RemoveMessage``-by-id mechanism. Never mutates *messages*; always
        returns a new list. Returns *messages* unchanged (as a list) when not due.
        In mask mode (SPEC-TKE-MSK-001) masked observations are substituted at
        their positions (no id required, since substitution is positional),
        stored under ``(tenant, run_id)``; without a ``run_id`` a fresh
        anonymous scope is used so callers never share artifacts.
        """
        should, _reason = self.should_compact(messages)
        if not should:
            return list(messages)

        strategy = self._effective_strategy()
        if strategy is CompactionStrategy.MASK_OBSERVATIONS:
            out = list(messages)
            replacements = self._mask(
                messages, run_id=run_id or anonymous_run_id(), tenant=tenant, require_id=False
            )
            for idx, new in replacements:
                out[idx] = new
            if replacements:
                self._record_compaction(
                    CompactionResult(
                        compacted=True,
                        strategy=CompactionStrategy.MASK_OBSERVATIONS,
                        messages_dropped=0,
                        removed_ids=(),
                    )
                )
            return out

        keep_recent = self._settings.context_compaction_keep_recent
        if keep_recent >= len(messages):
            return list(messages)

        drop = self._older_count(messages)
        older = list(messages[:drop])
        tail = list(messages[drop:])
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

    # ── masking internals (SPEC-TKE-MSK-001/002) ──────────────────────────────

    def _mask_boundary(self, messages: Sequence[BaseMessage]) -> int:
        """Index of the first protected position (the keep_turns-th turn from the end)."""
        keep_turns = self._settings.context_mask_keep_turns
        if keep_turns <= 0:
            return len(messages)
        seen = 0
        for idx in range(len(messages) - 1, -1, -1):
            if isinstance(messages[idx], HumanMessage | AIMessage):
                seen += 1
                if seen == keep_turns:
                    return idx
        return 0  # fewer turns than keep_turns ⇒ everything protected

    def _relevance(self, message: ToolMessage, messages: Sequence[BaseMessage]) -> float:
        if self._relevance_fn is None:
            return 0.0
        try:
            return float(self._relevance_fn(message, messages))
        except Exception:
            return 0.0

    def _store(self) -> ArtifactStorePort:
        if self._artifact_store is None:
            self._artifact_store = shared_artifact_store(self._settings)
        return self._artifact_store

    def _mask(
        self,
        messages: Sequence[BaseMessage],
        *,
        run_id: str,
        tenant: str,
        require_id: bool,
    ) -> list[tuple[int, ToolMessage]]:
        """Return ``(index, stub)`` pairs for masked observations, oldest-first."""
        boundary = self._mask_boundary(messages)
        low_water = self._low_water()
        remaining = _estimate(messages) if low_water is not None else 0
        keep_score = self._settings.context_mask_relevance_keep
        replacements: list[tuple[int, ToolMessage]] = []
        masked = errors = 0
        for idx in range(boundary):
            if low_water is not None and remaining <= low_water:
                break
            message = messages[idx]
            if not isinstance(message, ToolMessage):
                continue
            original = str(message.content)
            if original.startswith(_MASKED_PREFIX) or (require_id and not message.id):
                continue
            if self._relevance_fn is not None and self._relevance(message, messages) >= keep_score:
                continue
            tool = message.name or "unknown"
            tokens = estimate_tokens(original)
            try:
                ref = self._store().put(
                    original, run_id=run_id, tenant=tenant, kind="tool_output", tool=tool
                )
            except Exception as exc:
                errors += 1
                logger.warning("observation_mask_store_failed", error=str(exc)[:200])
                continue
            stub = OBSERVATION_STUB.format(tool=tool, artifact_id=ref.id, tokens=tokens)
            replacements.append(
                (
                    idx,
                    ToolMessage(
                        content=stub,
                        id=message.id,
                        tool_call_id=message.tool_call_id,
                        name=message.name,
                        status=message.status,
                    ),
                )
            )
            masked += 1
            remaining += estimate_tokens(stub) - tokens
        with suppress(Exception):
            if masked:
                OTelManager().increment_counter("observations_masked", value=masked)
        with suppress(Exception):
            if errors:
                OTelManager().increment_counter("observations_mask_errors", value=errors)
        return replacements

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
        sid = cast("dict[str, Any]", state).get("session_id")
        if sid:
            return str(sid)
    return _DEFAULT_KEY


def _turn_signature(state: object) -> int:
    messages = cast("dict[str, Any]", state).get("messages") if isinstance(state, dict) else None
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


class CompactionReactKwargs(TypedDict, total=False):
    """The ``react_loop`` keyword :func:`context_compaction_react_kwargs` may supply."""

    context_compactor: ContextCompactor


def context_compaction_react_kwargs(state: AgentState) -> CompactionReactKwargs:
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
    if compactor._hysteresis_active():
        # SPEC-TKE-MSK-002: the length watermark would block a genuine
        # re-trigger (masking keeps the length; truncation regrows past it), so
        # only skip when this is the *same unreduced list* already evaluated.
        # After an applied compaction the estimate is ≤ low water < high water,
        # so ``should_compact`` alone gives "once per band".
        fingerprint = _fingerprint(messages)
        if bucket is not None and bucket.get("last_evaluated_fp") == fingerprint:
            return {}
    else:
        fingerprint = None
        last_evaluated_len = bucket.get("last_evaluated_len") if bucket else None
        if (
            isinstance(last_evaluated_len, int)
            and len(messages) >= last_evaluated_len
            and len(messages) - last_evaluated_len
            < settings.context_compaction_min_interval_messages
        ):
            return {}

    if compactor._effective_strategy() is CompactionStrategy.MASK_OBSERVATIONS:
        run_id, tenant = _run_scope(state)
        result = await compactor.mask_observations(messages, run_id=run_id, tenant=tenant)
    else:
        result = await compactor.compact(messages)
    if not result.compacted:
        return {}
    if bucket is not None:
        bucket["last_evaluated_len"] = len(messages)
        if fingerprint is not None:
            bucket["last_evaluated_fp"] = fingerprint
    return compactor.to_state_update(result)


def _fingerprint(messages: Sequence[BaseMessage]) -> tuple[object, ...]:
    """Identity of an unreduced history: ids plus the masked-state of each (MSK-002)."""
    return tuple((m.id, len(str(m.content))) for m in messages)


def _run_scope(state: AgentState) -> tuple[str, str]:
    """``(run_id, tenant)`` for artifact scoping (SPEC-TKE-MSK-001)."""
    raw = cast("dict[str, Any]", state)
    run_id = str(raw.get("session_id") or anonymous_run_id())
    metadata = raw.get("metadata") or {}
    org_id = metadata.get("org_id") if isinstance(metadata, dict) else None
    return run_id, str(org_id or "default")


__all__ = [
    "OBSERVATION_STUB",
    "CompactionResult",
    "CompactionStrategy",
    "ContextCompactor",
    "RelevanceFn",
    "clear_context_compaction_run",
    "context_compaction_react_kwargs",
    "get_context_compactor",
    "maybe_compact_context",
    "maybe_seed_context_compaction_run",
    "seed_context_compaction_run",
]
