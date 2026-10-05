"""Cache-stable prompt layout (Phase TKE — SPEC-TKE-LAY-001).

Security-critical: the per-request canary moves from the system prompt to a
``<turn_context>`` tail block; a tenant-scoped HMAC prefix canary keeps the
system prompt guarded *and* byte-stable. Both canaries must be detectable.
"""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.security.prompt_builder import (
    LeakVerdict,
    PromptLayout,
    SecurePromptBuilder,
    TurnContext,
)


def _stable(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "token_efficiency_enabled": True,
        "prompt_layout": "cache_stable",
        "prompt_canary_secret": "unit-test-secret",
    }
    base.update(overrides)
    return Settings(**base)


def _builder(settings: Settings | None = None, now: float = 1_000_000.0) -> SecurePromptBuilder:
    return SecurePromptBuilder(settings=settings or _stable(), clock=lambda: now)


def test_legacy_is_default_and_unchanged_with_master_off() -> None:
    builder = SecurePromptBuilder(settings=Settings())
    messages = builder.build("Be helpful.", "Hi")
    assert messages[0]["content"] == f"Be helpful.\n\n<!-- canary:{builder.canary} -->"
    assert messages[1]["content"] == "<user_input>Hi</user_input>"
    assert builder.layout_used is PromptLayout.LEGACY


def test_cache_stable_system_prompt_is_byte_identical_across_requests() -> None:
    b1, b2 = _builder(), _builder()
    m1 = b1.build("Static instructions.", "first", tenant="acme")
    m2 = b2.build("Static instructions.", "second", tenant="acme")
    assert m1[0]["content"] == m2[0]["content"]
    assert b1.canary != b2.canary  # per-request canary still unique
    assert b1.canary not in m1[0]["content"]


def test_cache_stable_puts_turn_context_at_tail_of_user_turn() -> None:
    builder = _builder()
    messages = builder.build("S.", "question", tenant="acme")
    user = messages[1]["content"]
    assert user.startswith("<user_input>question</user_input>")
    assert user.rstrip().endswith("</turn_context>")
    assert f"canary:{builder.canary}" in user
    assert builder.layout_used is PromptLayout.CACHE_STABLE


def test_explicit_turn_context_is_rendered() -> None:
    builder = _builder()
    ctx = TurnContext(
        canary="c-1", request_id="r-9", now_iso="2026-10-05T00:00:00Z", extra={"phase": "plan"}
    )
    user = builder.build("S.", "q", layout=PromptLayout.CACHE_STABLE, turn_context=ctx)[1][
        "content"
    ]
    assert "canary:c-1" in user and "request_id:r-9" in user
    assert "2026-10-05T00:00:00Z" in user and "phase:plan" in user
    assert builder.canary == "c-1"


def test_prefix_canary_is_tenant_scoped_and_bucketed() -> None:
    builder = _builder(now=1_000_000.0)
    assert builder.prefix_canary("acme") == builder.prefix_canary("acme")
    assert builder.prefix_canary("acme") != builder.prefix_canary("globex")
    later = _builder(now=1_000_000.0 + 2 * 86_400)
    assert later.prefix_canary("acme") != builder.prefix_canary("acme")


def test_prefix_canary_depends_on_secret() -> None:
    a = _builder(_stable(prompt_canary_secret="one"))
    b = _builder(_stable(prompt_canary_secret="two"))
    assert a.prefix_canary("t") != b.prefix_canary("t")


@pytest.mark.parametrize("which", ["request", "prefix"])
def test_detect_leak_catches_both_canaries(which: str) -> None:
    builder = _builder()
    builder.build("S.", "q", tenant="acme")
    leaked = builder.canary if which == "request" else builder.prefix_canary("acme")
    verdict = builder.detect_leak(f"sure, here it is: {leaked}")
    assert verdict == LeakVerdict(leaked=True, which=(which,))


def test_detect_leak_clean_output() -> None:
    builder = _builder()
    builder.build("S.", "q", tenant="acme")
    assert builder.detect_leak("nothing to see").leaked is False
    assert set(builder.canaries) == {builder.canary, builder.prefix_canary("acme")}


def test_legacy_detect_leak_uses_request_canary() -> None:
    builder = SecurePromptBuilder(settings=Settings())
    builder.build("S.", "q")
    assert builder.detect_leak(builder.canary).leaked is True
    assert builder.canaries == (builder.canary,)


def test_fallback_to_legacy_never_drops_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    builder = _builder()

    def boom(_tenant: str | None) -> str:
        raise RuntimeError("hmac failure")

    monkeypatch.setattr(builder, "prefix_canary", boom)
    messages = builder.build("S.", "q", tenant="acme")
    assert builder.layout_used is PromptLayout.LEGACY
    assert f"<!-- canary:{builder.canary} -->" in messages[0]["content"]


def test_empty_secret_still_produces_stable_process_canary() -> None:
    a = _builder(_stable(prompt_canary_secret=""))
    b = _builder(_stable(prompt_canary_secret=""))
    assert a.prefix_canary("t") == b.prefix_canary("t")
    assert a.prefix_canary("t")


async def test_guardrails_validate_output_accepts_all_canaries() -> None:
    from prismal.security.guardrails import GuardrailsEngine

    builder = _builder()
    builder.build("S.", "q", tenant="acme")
    result = await GuardrailsEngine().validate_output(
        f"leak {builder.prefix_canary('acme')}", canary=builder.canaries
    )
    assert "output:canary_leak" in result.reasons
