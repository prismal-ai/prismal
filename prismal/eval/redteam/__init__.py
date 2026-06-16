"""Adversarial / red-team suite for the eval harness (Phase V, SPEC-EVL-RED-001).

Loads the adversarial corpus (injection, tool_abuse, exfiltration, jailbreak,
system_prompt_leak) and runs it against the real graph, asserting containment via
``assert_security``. The harness never bypasses the security layers — payloads
enter through the normal agent path.
"""

from __future__ import annotations

from prismal.eval.redteam.loader import load_redteam_corpus

__all__ = ["load_redteam_corpus"]
