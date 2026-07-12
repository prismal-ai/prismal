"""End-to-end memory subsystem demo with offline, deterministic stores.

Walks through the three public memory layers:

* ``ShortTermMemory`` — bounded, thread-safe in-session message buffer
  (FIFO eviction when full).
* ``ConversationHistory`` — append-only Markdown transcript per session,
  with YAML front-matter and multi-channel tracking.
* ``LongTermMemory`` — cross-session facts persisted to SQLite plus a
  vector store, with automatic secret redaction before persistence
  (AC-011-5) and retention-based expiry (AC-011-6).

Everything runs offline: SQLite lives in a temporary directory and the
vector store is a tiny in-memory keyword index conforming to
``VectorStorePort`` — no ChromaDB, no embeddings, no network, no LLM.

Run::

    python examples/memory_management.py
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage

if TYPE_CHECKING:
    from langchain_core.documents import Document

from prismal.memory import LongTermMemory, ShortTermMemory
from prismal.memory.conversation_history import ConversationHistory

SESSION_A = "sess-demo-a"
SESSION_B = "sess-demo-b"


# ── In-memory VectorStorePort double (keyword-overlap similarity) ─────────────


class KeywordVectorStore:
    """Minimal ``VectorStorePort`` implementation: token-overlap scoring.

    ``similarity_search`` scores each stored document by the fraction of
    query tokens it contains (deterministic, in ``[0, 1]``, higher = more
    relevant — the port's score contract). Good enough to exercise
    ``LongTermMemory.recall`` without ChromaDB or embeddings.
    """

    def __init__(self, collection_name: str = "memory_demo") -> None:
        self._collection_name = collection_name
        self._docs: list[Document] = []

    @property
    def collection_name(self) -> str:
        """Return the configured collection name."""
        return self._collection_name

    def add_documents(self, documents: list[Document]) -> list[str]:
        """Store documents in memory and return synthetic IDs."""
        start = len(self._docs)
        self._docs.extend(documents)
        return [f"kw-{i}" for i in range(start, start + len(documents))]

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        """Rank stored documents by query-token overlap, descending."""
        q_tokens = self._tokens(query)
        if not q_tokens:
            return []
        scored = []
        for doc in self._docs:
            overlap = len(q_tokens & self._tokens(doc.page_content))
            score = round(overlap / len(q_tokens), 4)
            if score > 0.0:
                scored.append((doc, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    def delete_by_source(self, source: str) -> None:
        """Drop documents whose ``source`` metadata matches."""
        self._docs = [d for d in self._docs if d.metadata.get("source") != source]

    def delete_collection(self) -> None:
        """Drop every stored document."""
        self._docs = []

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t for t in re.findall(r"\w+", text.lower()) if len(t) > 2}


# ── 1. Short-term memory: bounded in-session buffer ───────────────────────────


def demo_short_term() -> None:
    """Add six turns to a four-slot buffer and show FIFO eviction."""
    print("=== 1. ShortTermMemory (bounded session buffer) ===\n")

    mem = ShortTermMemory(max_messages=4)
    turns = [
        HumanMessage(content="Hi, I'm setting up the RAG pipeline."),
        AIMessage(content="Great — which vector store backend do you want?"),
        HumanMessage(content="Let's start with the Chroma default."),
        AIMessage(content="Chroma it is. Collection name?"),
        HumanMessage(content="Use 'docs_v1' as the collection name."),
        AIMessage(content="Done: collection 'docs_v1' is configured."),
    ]
    for msg in turns:
        mem.add(msg)

    print(f"Added {len(turns)} messages to a buffer with max_messages=4")
    print(f"Buffer now holds {len(mem)} messages (2 oldest evicted, FIFO):\n")
    for msg in mem.get_all():
        print(f"  [{type(msg).__name__}] {msg.content}")

    print("\nget_recent(2) — the latest exchange:")
    for msg in mem.get_recent(2):
        print(f"  [{type(msg).__name__}] {msg.content}")

    mem.clear()
    print(f"\nAfter clear(): {len(mem)} messages\n")


# ── 2. Conversation history: append-only Markdown transcript ─────────────────


def demo_conversation_history(base_dir: Path) -> None:
    """Append turns from two channels and read back the Markdown file."""
    print("=== 2. ConversationHistory (append-only Markdown) ===\n")

    history = ConversationHistory(SESSION_A, base_dir=base_dir)
    history.append("User", "Remember that I prefer dark mode.", channel="cli")
    history.append("Agent", "Noted — dark mode preference saved.", channel="cli")
    history.append("User", "Also enable compact layout.", channel="telegram")

    print(f"Transcript file: {history.path().name} (in a temp dir)")
    print("Contents (front-matter tracks both channels):\n")
    for line in history.read().splitlines():
        print(f"  {line}")
    print()


# ── 3. Long-term memory: SQLite + vector store, with redaction ────────────────


async def demo_long_term(base_dir: Path) -> None:
    """Save facts (one with a secret), recall them, then clear."""
    print("=== 3. LongTermMemory (SQLite + vector store round-trip) ===\n")

    store = KeywordVectorStore()
    mem = LongTermMemory(
        db_path=base_dir / "memory.db",
        vector_store=store,
        retention_days=30,
    )

    # A fact containing a credential — redacted before it touches disk.
    fake_key = "sk-" + "a1B2c3D4" * 6  # matches the OpenAI-style pattern
    facts = [
        (SESSION_A, "User prefers dark mode and compact layouts in every dashboard."),
        (SESSION_A, f"The staging deploy credential is {fake_key} — rotate monthly."),
        (SESSION_B, "User is a Python developer building LangGraph supervisor agents."),
    ]
    for session_id, content in facts:
        entry_id = await mem.save(content, session_id=session_id)
        print(f"Saved [{session_id}] -> {entry_id[:8]}…")

    # Redaction check (AC-011-5): the secret never reaches SQLite.
    recalled = await mem.recall("staging deploy credential", session_id=SESSION_A)
    print("\nRecall 'staging deploy credential' (session A):")
    for entry in recalled:
        print(f"  {entry.content}")
        assert fake_key not in entry.content, "secret must be redacted"
    print("  -> secret was redacted before persistence: OK")

    # Session-scoped vs cross-session recall (AC-011-2).
    query = "python developer langgraph agents"
    only_a = await mem.recall(query, session_id=SESSION_A)
    everywhere = await mem.recall(query, session_id=None)
    print(f"\nRecall '{query}':")
    print(f"  scoped to session A : {len(only_a)} hit(s)")
    print(f"  across all sessions : {len(everywhere)} hit(s)")
    for entry in everywhere:
        print(f"    [{entry.session_id}] {entry.content}")
        print(f"    expires {entry.expires_at.date()} (retention_days=30)")

    # Expiry + cleanup (AC-011-6 / AC-011-4).
    expired = await mem.expire()
    print(f"\nexpire() removed {expired} stale entries (all are fresh)")
    cleared = await mem.clear(session_id=SESSION_A)
    print(f"clear(session_id={SESSION_A!r}) removed {cleared} entries")
    remaining = await mem.recall(query, session_id=None)
    print(f"Entries still recallable after clear: {len(remaining)} (session B only)")


async def main() -> None:
    """Run the three memory demos against a temporary directory."""
    demo_short_term()
    with tempfile.TemporaryDirectory(prefix="prismal-memory-") as tmp:
        base_dir = Path(tmp)
        demo_conversation_history(base_dir / "conversations")
        await demo_long_term(base_dir)


if __name__ == "__main__":
    asyncio.run(main())
