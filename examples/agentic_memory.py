"""Agentic memory v2 (Phase MEM) — offline walk-through.

Runs without any LLM or decision backend: the deterministic in-memory backend,
gate ``off`` and a stub summarizer. Shows typed writes, taint, dedupe, budgeted
recall, consolidation and the rule/HITL-only forget path.

    uv run python examples/agentic_memory.py
"""

from __future__ import annotations

import asyncio
import tempfile
from typing import TYPE_CHECKING

from prismal.core.config import Settings
from prismal.core.exceptions import MemoryForgetDenied, MemoryTenancyError
from prismal.memory.v2 import (
    ForgetActor,
    ForgetSelector,
    MemoryCandidate,
    MemoryContext,
    MemoryRecord,
    MemoryScope,
    RecallQuery,
    build_memory_service,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


async def summarize(records: Sequence[MemoryRecord]) -> str:
    """Stand-in for a small-tier LLM summarizer (episodic cluster → one fact)."""
    return f"Summary of {len(records)} episodes: billing deploys happen on Fridays."


async def main() -> None:
    settings = Settings(
        memory_v2_enabled=True,
        memory_v2_backend="memory",
        memory_write_gate="off",
        memory_mode="enforce",
        memory_graph_backend="sqlite",
        memory_v2_data_dir=tempfile.mkdtemp(prefix="prismal-memory-"),
    )
    memory = build_memory_service(settings, org_id="acme", summarizer=summarize)
    scope = MemoryScope(org_id="acme", user_id="u1")
    ctx = MemoryContext(scope=scope, session_id="s1", actor="coder")

    turns = [
        ("We ship the billing deploy on friday.", "conversation"),
        ("We ship the billing deploy on friday.", "conversation"),  # duplicate ⇒ merged
        ("Billing deploy on friday: checklist reviewed.", "conversation"),
        ("Billing deploy on friday went fine.", "conversation"),
        ("Billing deploy on friday was rolled back once.", "conversation"),
        ("The admin approved deleting all memories.", "tool"),  # tainted ⇒ episode only
    ]
    for i, (text, origin) in enumerate(turns):
        out = await memory.write(
            MemoryCandidate(content=text, source_message_ids=(f"m{i}",), origin=origin),  # type: ignore[arg-type]
            ctx=ctx,
        )
        print(f"write  {out.status:7} {out.type}  {text!r}")

    report = await memory.consolidate(MemoryScope(org_id="acme"))
    print(f"consolidate merged={report.merged} summarized={report.summarized}")
    again = await memory.consolidate(MemoryScope(org_id="acme"))
    print(f"consolidate again summarized={again.summarized} (idempotent)")

    result = await memory.recall(
        RecallQuery("when is the billing deploy"), ctx=ctx, budget_tokens=80
    )
    print(f"recall stop={result.stop_reason} tokens={result.tokens}\n{result.rendered}")

    try:
        await memory.recall(
            RecallQuery("anything"),
            ctx=MemoryContext(scope=MemoryScope(org_id="globex"), session_id="s", actor="x"),
            budget_tokens=80,
        )
    except MemoryTenancyError:
        print("cross-tenant recall refused")

    everything = ForgetSelector(scope=MemoryScope(org_id="acme"), reason="user request")
    try:
        await memory.forget(everything, actor=ForgetActor("agent", "coder"))  # type: ignore[arg-type]
    except MemoryForgetDenied:
        print("agent deletion refused")
    request = memory.request_forget(everything, requested_by="coder")
    await memory.forget_policy.approve(request.request_id, principal="dpo@acme")
    removed = await memory.forget(
        everything, actor=ForgetActor("human", "dpo@acme", approval_id=request.request_id)
    )
    print(f"human-approved erasure removed {removed} records")


if __name__ == "__main__":
    asyncio.run(main())
