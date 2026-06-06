"""Tests for the hexagonal ports (X6, SPEC-EXT-006)."""

from __future__ import annotations

from langchain_core.embeddings import Embeddings
from langchain_core.tools import BaseTool

from prismal.agents.extension.ports import (
    AuditPort,
    CheckpointPort,
    EmbeddingsPort,
    ToolPort,
    conforms_to,
)
from prismal.security.audit import AuditLogger


class _FakeEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0]


class _DemoTool(BaseTool):
    name: str = "demo_port_tool"
    description: str = "demo"

    def _run(self, *args: object, **kwargs: object) -> str:
        return "ok"


class TestConformance:
    def test_audit_logger_conforms_to_audit_port(self) -> None:
        assert conforms_to(AuditLogger(), AuditPort)

    def test_embeddings_conforms_to_embeddings_port(self) -> None:
        assert conforms_to(_FakeEmbeddings(), EmbeddingsPort)

    def test_base_tool_conforms_to_tool_port(self) -> None:
        assert conforms_to(_DemoTool(), ToolPort)

    async def test_async_sqlite_saver_conforms_to_checkpoint_port(self) -> None:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
            assert conforms_to(saver, CheckpointPort)


class TestNonConformance:
    def test_plain_object_does_not_conform(self) -> None:
        assert conforms_to(object(), AuditPort) is False
        assert conforms_to(object(), CheckpointPort) is False
        assert conforms_to(object(), EmbeddingsPort) is False
        assert conforms_to(object(), ToolPort) is False

    def test_partial_implementation_does_not_conform(self) -> None:
        class _PartialAudit:
            def log_event(self, event_type: str, payload: dict) -> None:
                pass

            # missing log_node and log_media

        assert conforms_to(_PartialAudit(), AuditPort) is False

    def test_non_runtime_checkable_protocol_returns_false(self) -> None:
        from typing import Protocol

        class _NotRuntimeCheckable(Protocol):
            def foo(self) -> None: ...

        # isinstance against a non-runtime_checkable Protocol raises TypeError;
        # conforms_to must swallow it and return False.
        assert conforms_to(object(), _NotRuntimeCheckable) is False
