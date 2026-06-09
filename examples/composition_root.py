"""Runtime composition root demo (Phase R).

Shows the three ways to compose a runtime:

1. ``build_test_runtime`` — deterministic fakes, no I/O (what tests use).
2. ``build_runtime(..., mode="context")`` — per-tenant, no global state.
3. Per-tenant isolation — two tenants get separate, suffixed collections.

The real ``build_runtime`` connects MCP, opens a checkpointer, and loads an
embeddings model, so this example uses ``build_test_runtime`` for the runnable
path and only *describes* the host lifespan (which a server would run).

Run::

    python examples/composition_root.py
"""

from __future__ import annotations

import asyncio

from prismal.agents.extension.providers import FakeToolProvider
from prismal.composition import build_test_runtime, collection_for
from prismal.rag.vector_store_factory import FakeVectorStore


async def demo_test_runtime() -> None:
    """1. Compose a runtime with fakes — five ports, no I/O."""
    ctx = build_test_runtime(
        tool_provider=FakeToolProvider({}),
        vector_store=FakeVectorStore(),
        org_id="acme",
    )
    print("ports:", ctx.tool_provider, ctx.embeddings, ctx.checkpointer, ctx.audit)
    print("collection:", ctx.config.collection_name)  # default_acme
    async with ctx:  # aclose() is a no-op for the test runtime
        pass


def demo_tenant_isolation() -> None:
    """3. The same base collection is isolated per tenant."""
    base = "docs"
    print("single tenant:", collection_for(base, None))  # docs
    print("acme        :", collection_for(base, "acme"))  # docs_acme
    print("globex      :", collection_for(base, "globex"))  # docs_globex


HOST_LIFESPAN = """
# 2. Host lifespan (what prismal-server runs) — composes everything in one call:

from contextlib import asynccontextmanager
from prismal.agents.graph import get_async_compiled_graph
from prismal.composition import build_runtime
from prismal.core.config import get_settings

@asynccontextmanager
async def lifespan(app):
    ctx = await build_runtime(get_settings())          # mode=global
    app.state.runtime = ctx
    app.state.graph = await get_async_compiled_graph()
    try:
        yield
    finally:
        await ctx.aclose()
"""


def main() -> None:
    asyncio.run(demo_test_runtime())
    print()
    demo_tenant_isolation()
    print(HOST_LIFESPAN)


if __name__ == "__main__":
    main()
