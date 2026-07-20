"""Media ingestion into AgentState + multimodal pipeline registration (Fase F).

Creates a real (complete, decodable) 1x1 PNG, ingests it through
``ingest_media`` — validate magic bytes/size (``MediaValidator``) → sanitize
(EXIF strip) → spill to a content-addressed file → audit hash-only → record a
*path-based* descriptor under ``state["metadata"]["mm"]["media"]`` — and then
registers the multimodal pipeline subgraph in a fresh ``SubgraphRegistry`` with
``multimodal_enabled=True``. Everything runs offline.

Run::

    python examples/multimodal_ingestion.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path
from typing import Any

from prismal.agents.multimodal.ingestion import cleanup_session_media, ingest_media
from prismal.agents.state import create_initial_state
from prismal.agents.subgraphs.multimodal_pipeline import register_multimodal_pipeline
from prismal.agents.subgraphs.registry import SubgraphRegistry
from prismal.core.config import Settings
from prismal.security.media_validator import MediaKind

# A real, complete 1x1 transparent PNG. Its \x89PNG\r\n\x1a\n header satisfies
# MediaValidator's magic-byte sniffing, and it decodes cleanly so the EXIF-strip
# re-encode in InputSanitizer.sanitize_media works when Pillow is installed.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg=="
)

SESSION_ID = "sess-mm-demo"


class _ConsoleAudit:
    """AuditLogger stand-in — prints the hash-first record instead of JSONL I/O."""

    def log_media(self, action: str, **fields: Any) -> None:
        print(f"  [audit] media {action}: {fields}")


async def main() -> None:
    """Ingest one PNG into a state, then register the multimodal subgraph."""
    workspace = Path(tempfile.mkdtemp(prefix="prismal_media_demo_"))
    settings = Settings(multimodal_enabled=True, media_workspace=str(workspace))
    state = create_initial_state(session_id=SESSION_ID)

    # ── 1. Ingest: validate → sanitize → spill → audit → descriptor ───────────
    ingest_media(
        state,
        _PNG_1X1,
        kind=MediaKind.IMAGE,
        source="example:generated",
        workspace=workspace,
        preferred_output="text",
        audit=_ConsoleAudit(),  # type: ignore[arg-type] — duck-typed audit stand-in
        settings=settings,
    )

    mm = state["metadata"]["mm"]
    descriptor = mm["media"][mm["primary_media_index"]]
    print("\nPath-based descriptor under state['metadata']['mm']['media']:")
    print(json.dumps(descriptor, indent=2))
    spilled = Path(descriptor["uri"])
    print(f"spilled file exists: {spilled.is_file()} ({spilled.stat().st_size} bytes)")
    # Only the path travels in checkpointed state — never the raw bytes.

    # ── 2. Register the multimodal pipeline in a fresh registry ───────────────
    registry = SubgraphRegistry()
    register_multimodal_pipeline(registry, settings=settings, fusion_strategy="concat")
    definition = registry.get("multimodal_pipeline")
    if definition is None:
        raise RuntimeError("multimodal_pipeline was not registered")
    print(f"\nRegistered subgraphs: {registry.list()}")
    print(f"Entry point: {definition.entry_point}")
    print(f"Nodes: {list(definition.nodes)}")
    print(f"Description: {definition.description}")

    # ── 3. Per-session cleanup of the spilled media ────────────────────────────
    cleanup_session_media(SESSION_ID, workspace=workspace)
    print(f"\nAfter cleanup_session_media: spilled file exists = {spilled.is_file()}")


if __name__ == "__main__":
    asyncio.run(main())
