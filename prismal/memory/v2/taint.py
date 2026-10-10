"""Memory taint rules T1-T7 (Phase MEM — SPEC-MEM-TNT-001, fail-closed).

Pure functions over the Phase H :class:`~prismal.security.taint.TaintRegistry`
(read-only: this module never marks content). They are shared by the write gate,
consolidation and playbook curation:

- **T1** :func:`candidate_taint` — a candidate is tainted when a cited source
  message (or its own content) is untrusted in the registry, or when it comes
  from a ``tool``/``rag``/``a2a`` origin that is not on an explicit allowlist.
- **T2/T4** :func:`allowed_types` — tainted or non-``trusted`` provenance may only
  be stored as ``EPISODIC`` (a decision can only pick *skip* or *store-as-episodic*).
- **T3** :func:`may_promote` — promotion to a non-episodic type needs every record
  trusted and untainted, ≥ 2 independent untainted sources that agree on the
  normalized content, or a human approval.
- **T5** is enforced by ``render.py`` (``[untrusted]`` marks, tail block only).
- **T6** :func:`may_supersede` — a tainted record never supersedes an untainted one;
  supersession also needs the *same* scope and a provenance rank ≥ the older
  record's (:func:`provenance_rank`: human > trusted > unknown > untrusted).
- **T7** :func:`may_cite` — playbook deltas cite only trusted, untainted records.
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from prismal.memory.v2.types import MemoryType
from prismal.security.taint import TaintRegistry, get_active_taint_registry

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from prismal.memory.v2.types import MemoryCandidate, MemoryRecord, Provenance

#: Origins whose content crossed a trust boundary (T1).
UNTRUSTED_ORIGINS: frozenset[str] = frozenset({"tool", "rag", "a2a"})
#: The only types a tainted / non-trusted candidate may be stored as (T2/T4).
EPISODIC_ONLY: frozenset[MemoryType] = frozenset({MemoryType.EPISODIC})
_ALL_TYPES: frozenset[MemoryType] = frozenset(MemoryType)
_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize_content(text: str) -> str:
    """Case-folded, punctuation-free, whitespace-collapsed content (T3 agreement key)."""
    return _SPACES.sub(" ", _NON_WORD.sub(" ", text.casefold())).strip()


def _is_tainted(record: MemoryRecord) -> bool:
    """A record is treated as tainted when flagged or explicitly untrusted."""
    return record.provenance.tainted or record.provenance.trust == "untrusted"


def _resolve_registry(
    registry: TaintRegistry | None, state: Mapping[str, Any] | None
) -> TaintRegistry | None:
    """Explicit registry → active per-run registry → serialized ``metadata.hardening.taint``."""
    if registry is not None:
        return registry
    active = get_active_taint_registry()
    if active is not None:
        return active
    if state is None:
        return None
    metadata = state.get("metadata")
    hardening = metadata.get("hardening") if isinstance(metadata, dict) else None
    raw = hardening.get("taint") if isinstance(hardening, dict) else None
    if isinstance(raw, dict) and raw:
        with suppress(Exception):
            return TaintRegistry.from_dict(raw)
    return None


def _message_field(message: Any, name: str) -> Any:
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _message_texts(content: Any) -> list[str]:
    """The text(s) of a message: a ``str``, or the text blocks of a content list."""
    if isinstance(content, str):
        return [content]
    texts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                texts.append(block["text"])
    return texts


def _messages_by_id(state: Mapping[str, Any] | None) -> dict[str, Any]:
    messages = state.get("messages") if state is not None else None
    if not isinstance(messages, list):
        return {}
    out: dict[str, Any] = {}
    for message in messages:
        mid = _message_field(message, "id")
        if isinstance(mid, str) and mid:
            out[mid] = message
    return out


def _registry_hit(registry: TaintRegistry, texts: Iterable[str]) -> str | None:
    """Provenance label of the first untrusted text, or ``None``."""
    for text in texts:
        tag = registry.tag_for(text)
        if tag is not None and not tag.trusted:
            return tag.provenance.value
    return None


def candidate_taint(
    candidate: MemoryCandidate,
    *,
    state: Mapping[str, Any] | None,
    registry: TaintRegistry | None = None,
    trusted_sources: frozenset[str] = frozenset(),
) -> tuple[bool, tuple[str, ...]]:
    """T1: ``(tainted, taint_sources)`` for ``candidate``.

    ``registry`` defaults to the active per-run registry, then to the serialized
    ``state["metadata"]["hardening"]["taint"]``. ``trusted_sources`` is a
    developer-configured allowlist of ``"<origin>:<source>"`` labels (e.g.
    ``"tool:calculator"``) whose output is not tainted by origin alone.
    """
    sources: list[str] = []
    reg = _resolve_registry(registry, state)
    if reg is not None:
        hit = _registry_hit(reg, [candidate.content])
        if hit is not None:
            sources.append(f"content:{hit}")
        by_id = _messages_by_id(state)
        for mid in candidate.source_message_ids:
            message = by_id.get(mid)
            if message is None:
                continue
            hit = _registry_hit(reg, _message_texts(_message_field(message, "content")))
            if hit is not None:
                sources.append(f"message:{mid}:{hit}")
    if candidate.origin in UNTRUSTED_ORIGINS:
        label = f"{candidate.origin}:{candidate.metadata.get('source') or 'unknown'}"
        if label not in trusted_sources:
            sources.append(label)
    return bool(sources), tuple(sources)


def allowed_types(provenance: Provenance) -> frozenset[MemoryType]:
    """T2/T4: tainted or non-``trusted`` provenance may only be stored as ``EPISODIC``."""
    if provenance.tainted or provenance.trust != "trusted":
        return EPISODIC_ONLY
    return _ALL_TYPES


def provenance_rank(record: MemoryRecord) -> int:
    """Provenance precedence.

    ``human`` (3) > ``trusted`` (2) > ``unknown`` (1) > ``untrusted`` (0).
    """
    if record.provenance.origin == "human":
        return 3
    return {"trusted": 2, "unknown": 1}.get(record.provenance.trust, 0)


def supersede_block_reason(newer: MemoryRecord, older: MemoryRecord) -> str | None:
    """Why ``newer`` may not supersede ``older`` (``None`` ⇒ it may; edges are kept either way).

    ``"self"`` / ``"tenant"`` — same record or a different org; ``"scope"`` — the
    scopes differ (a user/agent/thread record never supersedes a wider or sibling
    one); ``"T6"`` — a tainted record never supersedes an untainted one;
    ``"precedence"`` — ``newer`` ranks below ``older`` (:func:`provenance_rank`).
    """
    if newer.id == older.id:
        return "self"
    if newer.scope.org_id != older.scope.org_id:
        return "tenant"
    if newer.scope != older.scope:
        return "scope"
    if _is_tainted(newer) and not _is_tainted(older):
        return "T6"
    if provenance_rank(newer) < provenance_rank(older):
        return "precedence"
    return None


def may_supersede(newer: MemoryRecord, older: MemoryRecord) -> bool:
    """T6 + scope + precedence: see :func:`supersede_block_reason`."""
    return supersede_block_reason(newer, older) is None


def _independent_agreement(records: Sequence[MemoryRecord]) -> bool:
    """≥ 2 untainted records with equal normalized content and disjoint, non-empty sources."""
    groups: dict[str, list[MemoryRecord]] = {}
    for rec in records:
        if _is_tainted(rec) or not rec.provenance.source_message_ids:
            continue
        groups.setdefault(normalize_content(rec.content), []).append(rec)
    for members in groups.values():
        for i, first in enumerate(members):
            seen = set(first.provenance.source_message_ids)
            for second in members[i + 1 :]:
                if second.id != first.id and seen.isdisjoint(second.provenance.source_message_ids):
                    return True
    return False


def may_promote(
    records: Sequence[MemoryRecord], target: MemoryType, *, human_approved: bool = False
) -> bool:
    """T3: may ``records`` be promoted (consolidated) into a ``target``-typed record?"""
    if target is MemoryType.EPISODIC or human_approved:
        return True
    if not records:
        return False
    if all(not _is_tainted(r) and r.provenance.trust == "trusted" for r in records):
        return True
    return _independent_agreement(records)


def may_cite(record: MemoryRecord) -> bool:
    """T7: playbook deltas may cite only trusted, untainted records."""
    return not record.provenance.tainted and record.provenance.trust == "trusted"


__all__ = [
    "EPISODIC_ONLY",
    "UNTRUSTED_ORIGINS",
    "allowed_types",
    "candidate_taint",
    "may_cite",
    "may_promote",
    "may_supersede",
    "normalize_content",
    "provenance_rank",
    "supersede_block_reason",
]
