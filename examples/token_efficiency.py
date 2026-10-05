"""Token-efficiency demo with injected fakes (Phase TKE).

Runs offline — no LLM, no network:

1. Meter a cache-heavy response and compare the v3.12 price with the
   cache-aware price.
2. Build a cache-stable prompt: the system prompt is byte-identical across
   requests while both canaries stay detectable.
3. Apply the Anthropic prompt-cache policy and show where breakpoints land.
4. Cap a huge tool output to its budget; the full payload goes to the
   artifact store and stays retrievable.
5. Encode a uniform table as TOON.

Run::

    python examples/token_efficiency.py
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from prismal.agents.artifacts import InMemoryArtifactStore
from prismal.agents.tool_output_budget import ToolOutputBudget
from prismal.budget.meter import CostMeter
from prismal.core.config import Settings
from prismal.providers.prompt_cache import CachePolicy, apply_cache_policy
from prismal.security.prompt_builder import SecurePromptBuilder
from prismal.utils.toon import maybe_toon

MODEL = "claude-sonnet-4-5"


def main() -> None:
    off = Settings()
    on = Settings(
        token_efficiency_enabled=True,
        prompt_layout="cache_stable",
        prompt_canary_secret="demo-secret",  # noqa: S106 — offline demo value
        tool_output_budget_enabled=True,
        tool_output_max_tokens_default=200,
        tool_output_toon_enabled=True,
    )

    # 1. Cache-aware accounting.
    response = SimpleNamespace(
        usage_metadata={
            "input_tokens": 20_000,
            "output_tokens": 500,
            "input_token_details": {"cache_read": 18_000},
        },
        response_metadata={},
    )
    legacy = CostMeter(settings=off).record_response(response, MODEL)
    aware = CostMeter(settings=on).record_response(response, MODEL, agent="coder")
    print(
        f"[1] v3.12 price ${legacy.cost_usd:.4f} → cache-aware ${aware.cost_usd:.4f} "
        f"(hit ratio {aware.cache_hit_ratio:.0%})"
    )

    # 2. Cache-stable prompt layout.
    first, second = SecurePromptBuilder(settings=on), SecurePromptBuilder(settings=on)
    m1 = first.build("You are a careful assistant.", "hello", tenant="acme")
    m2 = second.build("You are a careful assistant.", "bye", tenant="acme")
    leak = first.detect_leak(f"oops {first.prefix_canary('acme')}")
    print(
        f"[2] system prompt identical: {m1[0]['content'] == m2[0]['content']}; "
        f"prefix-canary leak detected: {leak.leaked}"
    )

    # 3. Anthropic prompt-cache breakpoints.
    applied = apply_cache_policy(
        [SystemMessage(content="Static rules. " * 600), HumanMessage(content="hi")],
        [],
        model=MODEL,
        agent="coder",
        policy=CachePolicy(),
    )
    print(f"[3] family={applied.family} breakpoints={applied.breakpoints}")

    # 4. Tool-output budget + artifact store.
    store = InMemoryArtifactStore(settings=on)
    budgeted = ToolOutputBudget(settings=on, artifact_store=store).apply(
        "web_search", "lorem ipsum " * 5_000, run_id="demo", tenant="acme"
    )
    assert budgeted.artifact is not None
    full = store.get(budgeted.artifact.id, run_id="demo", tenant="acme")
    print(
        f"[4] {budgeted.original_tokens} tokens → {len(budgeted.content)} chars; "
        f"artifact holds {full.total_chars} chars"
    )

    # 5. TOON.
    rows = [{"id": 1, "city": "Caracas"}, {"id": 2, "city": "Madrid"}]
    print("[5]", maybe_toon(rows).replace("\n", " | "))


if __name__ == "__main__":
    main()
