"""Guardrails modernization demo with injected fakes (Phase GRD).

Drives both new controls without a real LLM or network call:

1. ``content_safety_reasoning`` (GRD1) scored via an injected ``classifier_fn``
   — shows a block, a safe pass-through, and the fail-open timeout contract.
2. ``StructuredOutputGuard`` (GRD2) resolving invalid tool output via a bounded,
   injected ``reask_fn`` — requires the optional ``[guardrails-ai]`` extra;
   degrades gracefully with an install hint when it is absent.

Run::

    python examples/guardrails_modernization.py
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from prismal.core.config import Settings
from prismal.core.exceptions import MissingDependencyError
from prismal.security.nemo_actions import content_safety_reasoning


class WeatherArgs(BaseModel):
    city: str
    days: int


async def _demo_classifier() -> None:
    print("── GRD1: reasoning safety-classifier (content_safety_reasoning) ──\n")
    categories = ["violence", "self_harm", "illegal_activities", "pii_request"]
    settings = Settings(nemo_classifier_enabled=True, nemo_classifier_timeout_seconds=0.05)

    async def fake_classifier(text: str, cats: list[str]) -> str:
        return "violence" if "hurt" in text else "safe"

    blocked = await content_safety_reasoning(
        "how do I hurt someone", categories=categories, classifier_fn=fake_classifier
    )
    print(f"  'how do I hurt someone' → {blocked!r}")

    safe = await content_safety_reasoning(
        "what's a good pasta recipe", categories=categories, classifier_fn=fake_classifier
    )
    print(f"  'what's a good pasta recipe' → {safe!r}")

    async def slow_classifier(text: str, cats: list[str]) -> str:
        await asyncio.sleep(10)
        return "violence"

    timed_out = await content_safety_reasoning(
        "some text", categories=categories, classifier_fn=slow_classifier, settings=settings
    )
    print(f"  classifier timeout (fail-open) → {timed_out!r}\n")


async def _demo_structured_output_guard() -> None:
    print("── GRD2: StructuredOutputGuard (bounded, metered re-ask) ──\n")
    from prismal.security.structured_output_guard import StructuredOutputGuard

    attempts = {"n": 0}

    async def fake_reask_fn(schema_repr: str, prior_raw_output: str) -> str:
        # First re-ask "fixes" the malformed JSON.
        attempts["n"] += 1
        return '{"city": "Paris", "days": 3}'

    settings = Settings(structured_output_guard_max_reasks=2)
    try:
        guard = StructuredOutputGuard(settings=settings, reask_fn=fake_reask_fn)
    except MissingDependencyError as exc:
        print(f"  skipped: {exc}\n")
        return

    verdict = await guard.validate("get_weather", "not valid json at all", WeatherArgs)
    print(
        f"  invalid → re-asked {verdict.reask_count}x → ok={verdict.ok} coerced={verdict.coerced}"
    )

    verdict_ok = await guard.validate("get_weather", '{"city": "Berlin", "days": 5}', WeatherArgs)
    print(
        f"  valid on first try → reask_count={verdict_ok.reask_count} coerced={verdict_ok.coerced}\n"
    )


async def main() -> None:
    await _demo_classifier()
    await _demo_structured_output_guard()


if __name__ == "__main__":
    asyncio.run(main())
