"""Memory v2 end to end with fakes (Phase MEM — MEM6-06, SPEC-MEM-OTEL-001).

Multi-turn conversation → consolidation → recall answers within budget, composed
through ``build_memory_service(org_id)`` (what ``build_runtime`` calls), with the
OTel instruments asserted. No external services.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.core.config import Settings
from prismal.memory.v2.service import build_memory_service
from prismal.memory.v2.types import (
    MemoryCandidate,
    MemoryContext,
    MemoryScope,
    MemoryType,
    RecallQuery,
)
from prismal.monitoring.otel import OTelManager

pytestmark = [pytest.mark.integration]

TURNS = (
    "We deploy the billing service on Fridays.",
    "We deploy the billing service on Fridays.",  # duplicate ⇒ merged
    "The billing service is written in Go.",
    "My favourite editor is Helix.",
    "Billing deploy on friday: checklist reviewed.",
    "Billing deploy on friday went fine.",
    "Billing deploy on friday was rolled back once.",
)


async def test_conversation_consolidation_and_recall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counters: list[tuple[str, dict[str, Any]]] = []
    histograms: list[tuple[str, float]] = []

    def _count(self: Any, key: str, value: int = 1, attributes: Any = None) -> None:
        counters.append((key, dict(attributes or {})))

    def _hist(self: Any, key: str, value: float, attributes: Any = None) -> None:
        histograms.append((key, value))

    monkeypatch.setattr(OTelManager, "increment_counter", _count)
    monkeypatch.setattr(OTelManager, "record_histogram", _hist)

    async def summarizer(records: Any) -> str:
        return "Billing deploys happen on Fridays"

    settings = Settings(
        memory_v2_enabled=True,
        memory_v2_backend="memory",
        memory_write_gate="off",
        memory_mode="enforce",
        memory_graph_backend="sqlite",
        memory_v2_data_dir=str(tmp_path),
    )
    memory = build_memory_service(settings, org_id="acme", summarizer=summarizer)

    ctx = MemoryContext(
        scope=MemoryScope(org_id="acme", user_id="u1"), session_id="s1", actor="coder"
    )
    statuses = [
        (
            await memory.write(
                MemoryCandidate(content=text, source_message_ids=(f"m{i}",)), ctx=ctx
            )
        ).status
        for i, text in enumerate(TURNS)
    ]
    assert statuses.count("merged") == 1

    first = await memory.consolidate(MemoryScope(org_id="acme"))
    again = await memory.consolidate(MemoryScope(org_id="acme"))
    assert (again.merged, again.superseded, again.summarized) == (0, 0, 0)

    result = await memory.recall(
        RecallQuery(text="when do we deploy billing"), ctx=ctx, budget_tokens=60
    )
    assert result.tokens <= 60
    assert "Friday" in result.rendered
    assert first.summarized == 1
    records = await memory.backend.list(scope=MemoryScope(org_id="acme"))
    assert any(r.type is MemoryType.SEMANTIC for r in records)

    keys = {k for k, _ in counters}
    assert {"memory_writes", "memory_recall_stops", "memory_consolidations"} <= keys
    assert ("memory_recall_tokens", float(result.tokens)) in histograms
