"""Secure prompt builder — wraps user input in tagged sections with a canary token.

Never concatenates user input directly into the system prompt. All user text
is isolated in <user_input> tags and sanitized by InputSanitizer before use.
"""

from __future__ import annotations

import uuid

from lightagent.core.logging import get_logger
from lightagent.security.sanitizer import InputSanitizer

logger = get_logger("lightagent.security.prompt_builder")


class SecurePromptBuilder:
    """Builds safe ChatML-style message lists for LLM calls.

    Features:
    - Sanitizes all user input via InputSanitizer.
    - Wraps user input in ``<user_input>...</user_input>`` tags.
    - Wraps documents in ``<documents><document>...</document></documents>`` tags.
    - Embeds a per-call UUID canary in the system prompt (detects prompt leakage).

    Usage::

        builder = SecurePromptBuilder()
        messages = builder.build(
            system="You are a helpful assistant.",
            user="What is the capital of France?",
            docs=["France is a country in Western Europe..."],
        )
        canary = builder.canary  # pass to GuardrailsEngine.validate_output()
    """

    def __init__(self) -> None:
        """Initialize with a fresh InputSanitizer."""
        self._sanitizer = InputSanitizer()
        self.canary: str = ""

    def build(
        self,
        system: str,
        user: str,
        docs: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """Build a safe message list for an LLM call.

        Args:
            system: System prompt text (trusted code — not sanitized).
            user: Raw user input (automatically sanitized).
            docs: Optional list of document strings to include as context.

        Returns:
            List of two dicts: system message followed by user message.
        """
        canary = str(uuid.uuid4())
        self.canary = canary

        system_content = f"{system}\n\n<!-- canary:{canary} -->"

        sanitized_user = self._sanitizer.sanitize(user)
        user_parts: list[str] = [f"<user_input>{sanitized_user}</user_input>"]

        if docs:
            doc_tags = "".join(f"<document>{d}</document>" for d in docs)
            user_parts.append(f"<documents>{doc_tags}</documents>")

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": "\n".join(user_parts)},
        ]


__all__ = ["SecurePromptBuilder"]
