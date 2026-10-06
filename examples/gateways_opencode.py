"""OpenCode Zen / Go gateway demo (Phase GW) — offline, no request is sent.

1. Per-model wire protocols: the same gateway maps Claude to the Anthropic
   Messages API, GPT to the Responses API and GLM to Chat Completions.
2. The OpenCode Go ToS guard: only coding agents may use Go; anything else is
   refused before any request.
3. The Go flat-fee quota ledger: an exhausted 5 h window downgrades the call
   to a cheaper model (or refuses it), before any network call.

Run::

    python examples/gateways_opencode.py
"""

from __future__ import annotations

from pydantic import SecretStr

from prismal.core.config import Settings
from prismal.core.exceptions import GatewayPolicyError
from prismal.providers.gateways.router import build_gateway_router


def main() -> None:
    settings = Settings(
        llm_gateways_enabled=True,
        opencode_zen_enabled=True,
        opencode_go_enabled=True,
        opencode_go_monthly_cap_usd=10.0,
        opencode_go_quota_action="downgrade",
        opencode_go_downgrade_model="gw:opencode-go/glm-5.3-flash",
    )
    router = build_gateway_router(
        settings, org_id="acme", secret_resolver=lambda ref: SecretStr("oc-demo-key")
    )
    assert router is not None

    print("1. Per-model wire protocols (Zen)")
    for ref in ("gw:opencode/claude-sonnet-4-5", "gw:opencode/gpt-5.5", "gw:opencode/glm-5.3"):
        resolved = router.resolve(ref, agent="researcher", session_id="sess-1")
        assert resolved is not None
        print(f"   {ref:32} -> {resolved.litellm_model:34} [{resolved.wire_protocol}]")

    print("\n2. OpenCode Go ToS guard")
    resolved = router.resolve("gw:opencode-go/glm-5.3", agent="coder", session_id="sess-1")
    assert resolved is not None
    print("   coder      ->", resolved.litellm_model, "| cost_mode =", resolved.cost_mode)
    print("   headers    :", sorted(resolved.extra_headers))
    try:
        router.resolve("gw:opencode-go/glm-5.3", agent="researcher", session_id="sess-1")
    except GatewayPolicyError as exc:
        print("   researcher -> refused:", exc.reason)

    print("\n3. Go quota ledger ($10/month ⇒ 5 h window = $2)")
    ledger = router.ledger
    assert ledger is not None
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=2.0)
    verdict = ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.01)
    print(f"   verdict: {verdict.action} (window {verdict.exhausted_window})")
    print("   downgrade target:", resolved.downgrade_model)
    for snap in ledger.snapshot("opencode-go"):
        print(f"   {snap.window:>5}: used ${snap.used:.2f} of ${snap.limit:.2f}")


if __name__ == "__main__":
    main()
