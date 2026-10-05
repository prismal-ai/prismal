"""Multi-agent handoff envelopes (Phase TKE — SPEC-TKE-HND-001).

Instead of forwarding raw worker transcripts between agents (Skynet workers →
reducer, MoA proposers → aggregator, Kokoro souls → judge, BRP reviewers), a
producer hands over a compact :class:`HandoffEnvelope`: a bounded summary, the
refs of any artifacts holding the full text, a status and optional usage.
Reducers read :func:`envelope_to_message_text` rather than the transcript.

The summary is a deterministic **extractive** cut (no LLM call): the leading
text, cut on a paragraph/sentence boundary so it fits ``max_summary_tokens``
including a trailing note pointing at the stored artifact.

Opt-in: :func:`maybe_envelope` returns ``None`` unless ``token_efficiency_enabled``
and ``handoff_envelope_enabled`` are both on. Envelopes are checkpoint-safe via
:meth:`HandoffEnvelope.to_dict` / :meth:`HandoffEnvelope.from_dict`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from prismal.agents.artifacts import ArtifactRef, ArtifactStorePort, build_artifact_store
from prismal.budget.types import Usage
from prismal.core.config import get_settings
from prismal.utils.token_estimate import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.core.config import Settings

HandoffStatus = Literal["ok", "partial", "failed"]
_VALID_STATUSES: frozenset[str] = frozenset({"ok", "partial", "failed"})
_CHARS_PER_TOKEN = 4
_SENTENCE_END = re.compile(r"[.!?](?=\s)")
_TRUNCATED_NOTE = " … [truncated]"


@dataclass(frozen=True)
class HandoffEnvelope:
    """Compact inter-agent handoff payload (SPEC-TKE-HND-001)."""

    summary: str
    artifacts: tuple[ArtifactRef, ...] = ()
    status: HandoffStatus = "ok"
    usage: Usage | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict for checkpointed state."""
        return {
            "summary": self.summary,
            "artifacts": [dataclasses.asdict(ref) for ref in self.artifacts],
            "status": self.status,
            "usage": dataclasses.asdict(self.usage) if self.usage is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HandoffEnvelope:
        """Inverse of :meth:`to_dict`; tolerates missing optional keys (legacy-safe)."""
        refs = tuple(_ref_from_dict(r) for r in data.get("artifacts") or ())
        usage_data = data.get("usage")
        usage = _usage_from_dict(usage_data) if usage_data is not None else None
        status = data.get("status") or "ok"
        return cls(
            summary=str(data.get("summary", "")),
            artifacts=refs,
            status=_validate_status(status),
            usage=usage,
        )


def _ref_from_dict(d: Mapping[str, Any]) -> ArtifactRef:
    return ArtifactRef(
        id=str(d["id"]),
        run_id=str(d.get("run_id", "")),
        tenant=str(d.get("tenant", "")),
        tool=d.get("tool"),
        tokens_estimate=int(d.get("tokens_estimate", 0)),
        created_at=float(d.get("created_at", 0.0)),
        kind=str(d.get("kind", "handoff")),
    )


def _usage_from_dict(d: Mapping[str, Any]) -> Usage:
    known = {f.name for f in dataclasses.fields(Usage)}
    return Usage(**{k: v for k, v in d.items() if k in known})


def _validate_status(status: str) -> HandoffStatus:
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid handoff status {status!r}; expected one of ok|partial|failed")
    return cast("HandoffStatus", status)


def _increment(metric: str, attributes: dict[str, Any] | None = None) -> None:
    with contextlib.suppress(Exception):
        from prismal.monitoring import otel

        otel.OTelManager().increment_counter(metric, value=1, attributes=attributes or {})


def _extractive_cut(text: str, max_chars: int) -> str:
    """Leading text ≤ *max_chars*, cut on the latest paragraph/sentence boundary."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text.rstrip()
    window = text[: max_chars + 1]  # +1 lets a terminator at the edge see its whitespace
    best = -1
    para = window.rfind("\n\n", 0, max_chars)
    if para > 0:
        best = para
    for m in _SENTENCE_END.finditer(window):
        if m.end() <= max_chars:
            best = max(best, m.end())
    if best <= 0:
        space = window.rfind(" ", 0, max_chars)
        best = space if space > 0 else max_chars
    return text[:best].rstrip()


def _with_note(body: str, note: str) -> str:
    return f"{body}{note}" if body else note.lstrip()


def build_envelope(
    text: str,
    *,
    store: ArtifactStorePort | None,
    run_id: str,
    tenant: str,
    max_summary_tokens: int,
    usage: Usage | None = None,
    status: str = "ok",
    pattern: str = "",
) -> HandoffEnvelope:
    """Build an envelope for *text* (SPEC-TKE-HND-001).

    ≤ ``max_summary_tokens`` ⇒ the text itself, nothing stored. Otherwise the full
    text is stored (``kind="handoff"``) and the summary is an extractive cut ending
    in ``… [full text: artifact <id>]``. No store / store failure ⇒ the cut ends in
    ``… [truncated]`` and ``artifacts=()``. Unknown *status* raises ``ValueError``
    before any I/O (programming error).
    """
    valid_status = _validate_status(status)
    _increment("handoff_envelopes", {"pattern": pattern})
    if estimate_tokens(text) <= max_summary_tokens:
        return HandoffEnvelope(summary=text, status=valid_status, usage=usage)

    max_chars = max(max_summary_tokens, 0) * _CHARS_PER_TOKEN
    ref: ArtifactRef | None = None
    if store is not None:
        try:
            ref = store.put(text, run_id=run_id, tenant=tenant, kind="handoff")
        except Exception:  # fail-open: truncated summary without an artifact
            ref = None
    note = f" … [full text: artifact {ref.id}]" if ref is not None else _TRUNCATED_NOTE
    body = _extractive_cut(text, max_chars - len(note))
    return HandoffEnvelope(
        summary=_with_note(body, note),
        artifacts=(ref,) if ref is not None else (),
        status=valid_status,
        usage=usage,
    )


def envelope_to_message_text(env: HandoffEnvelope) -> str:
    """Render the reducer-facing text: summary, artifact ids, non-ok status."""
    lines = [env.summary]
    if env.artifacts:
        lines.append("Artifacts: " + ", ".join(ref.id for ref in env.artifacts))
    if env.status != "ok":
        lines.append(f"Status: {env.status}")
    return "\n".join(lines)


def envelope_enabled(settings: Settings | None = None) -> bool:
    """True only when the TKE master and ``handoff_envelope_enabled`` are on."""
    s = settings if settings is not None else get_settings()
    # ``is True`` — a non-bool truthy value (e.g. a settings test double) never enables.
    return s.token_efficiency_enabled is True and s.handoff_envelope_enabled is True


def maybe_envelope(
    text: str,
    *,
    settings: Settings | None,
    store: ArtifactStorePort | None,
    run_id: str,
    tenant: str,
    pattern: str,
    usage: Usage | None = None,
    status: str = "ok",
) -> HandoffEnvelope | None:
    """Envelope *text* when enabled (cap = ``handoff_summary_max_tokens``), else ``None``."""
    s = settings if settings is not None else get_settings()
    if not envelope_enabled(s):
        return None
    return build_envelope(
        text,
        store=store,
        run_id=run_id,
        tenant=tenant,
        max_summary_tokens=s.handoff_summary_max_tokens,
        usage=usage,
        status=status,
        pattern=pattern,
    )


def handoff_text(raw: str, envelope: HandoffEnvelope | None) -> str:
    """Reducer-facing text: the envelope rendering when present, else *raw* (DD-TKE-012)."""
    return envelope_to_message_text(envelope) if envelope is not None else raw


def handoff_scope(state: object) -> tuple[str, str]:
    """``(run_id, tenant)`` for envelope artifacts — ``session_id`` / ``metadata.org_id``."""
    if not isinstance(state, dict):
        return "run", "default"
    raw = cast("dict[str, Any]", state)
    metadata = raw.get("metadata") or {}
    org_id = metadata.get("org_id") if isinstance(metadata, dict) else None
    return str(raw.get("session_id") or "run"), str(org_id or "default")


# ── per-run shared artifact store ────────────────────────────────────────────
#
# In-process registry keyed by run id (mirror of ``context_compaction._RUN_ENGINES``):
# producers and reducers of one run share a store so ``read_artifact`` can reach
# the full text behind an envelope. Live stores never enter checkpointed state.
_RUN_STORES: dict[str, ArtifactStorePort] = {}


def get_run_artifact_store(run_id: str, settings: Settings | None = None) -> ArtifactStorePort:
    """Return the artifact store shared by *run_id*, building it on first use."""
    store = _RUN_STORES.get(run_id)
    if store is None:
        store = build_artifact_store(settings)
        _RUN_STORES[run_id] = store
    return store


def clear_run_artifact_store(run_id: str) -> None:
    """Release the per-run store for *run_id* (idempotent)."""
    _RUN_STORES.pop(run_id, None)


def maybe_run_envelope(
    text: str,
    *,
    settings: Settings | None,
    run_id: str,
    tenant: str,
    pattern: str,
    usage: Usage | None = None,
    status: str = "ok",
) -> HandoffEnvelope | None:
    """:func:`maybe_envelope` over the per-run shared store.

    Disabled ⇒ ``None`` **without** touching the run-store registry, so the
    flag-off path has no side effect at all.
    """
    s = settings if settings is not None else get_settings()
    if not envelope_enabled(s):
        return None
    return maybe_envelope(
        text,
        settings=s,
        store=get_run_artifact_store(run_id, s),
        run_id=run_id,
        tenant=tenant,
        pattern=pattern,
        usage=usage,
        status=status,
    )


__all__ = [
    "HandoffEnvelope",
    "HandoffStatus",
    "build_envelope",
    "clear_run_artifact_store",
    "envelope_enabled",
    "envelope_to_message_text",
    "get_run_artifact_store",
    "handoff_scope",
    "handoff_text",
    "maybe_envelope",
    "maybe_run_envelope",
]
