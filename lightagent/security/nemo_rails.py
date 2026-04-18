"""NeMo Guardrails Layer 3 integration for the LightAgent security pipeline.

Wraps ``nemoguardrails.LLMRails`` and provides async input/output rail
checking with timeout protection.  Falls back gracefully when
``nemoguardrails`` is not installed or the config directory is missing.

SPEC-019 acceptance criteria
------------------------------
* AC-019-1: ``config/nemo_rails/config.yml`` loaded when enabled.
* AC-019-2: Input rails block harmful topics (5 categories).
* AC-019-3: Output rails detect unsafe LLM responses.
* AC-019-6: ≤ 500 ms P99 via ``asyncio.wait_for`` with 450 ms budget.
* AC-019-7: Zero overhead when ``nemo_guardrails_enabled=False``.

Architecture
------------
``NemoRailsLayer`` wraps ``LLMRails`` and provides two async methods:

* :meth:`check_input` — runs NeMo **input rails** on user text.
  When an input rail triggers, NeMo returns a refusal message *without*
  calling the main LLM, so the check is fast.  A short timeout (450 ms)
  ensures we never block the pipeline even if NeMo hangs.

* :meth:`check_output` — passes the LLM-generated text as an assistant
  message so NeMo's **output rails** can evaluate it.

Blocked responses carry a sentinel prefix ``[NEMO_BLOCKED:<category>]``
(set in the Colang ``main.co`` file) so the layer can reliably parse
results without fragile substring matching on the human-readable message.

Example::

    from lightagent.security.nemo_rails import get_nemo_layer

    layer = get_nemo_layer()
    if layer and layer.available:
        blocked, category = await layer.check_input(user_text)
        if blocked:
            raise ValueError(f"NeMo blocked input: {category}")
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from typing import Any

logger = get_logger("lightagent.security.nemo_rails")

# Timeout budget for a single NeMo rail check.
# Set below 500 ms to satisfy AC-019-6 (P99 ≤ 500 ms).
_NEMO_TIMEOUT_SECONDS: float = 0.45

# Sentinel prefix written by our Colang flows into every refusal message.
_NEMO_BLOCK_PREFIX = "[NEMO_BLOCKED:"
_NEMO_BLOCK_SUFFIX = "]"


def _parse_block_response(response: str) -> tuple[bool, str]:
    """Parse a NeMo response for the blocking sentinel prefix.

    Our Colang ``define bot refuse ...`` blocks prepend
    ``[NEMO_BLOCKED:<category>]`` so we can detect blocks without fragile
    matching on human-readable message text.

    Args:
        response: Text returned by ``LLMRails.generate_async``.

    Returns:
        Tuple ``(blocked, category)`` — when ``blocked`` is True,
        ``category`` is the Colang category tag (e.g. ``"violence"``).
        Both are empty/False when the response is not a NeMo block.
    """
    if not response.startswith(_NEMO_BLOCK_PREFIX):
        return False, ""
    try:
        end_idx = response.index(_NEMO_BLOCK_SUFFIX, len(_NEMO_BLOCK_PREFIX))
        category = response[len(_NEMO_BLOCK_PREFIX) : end_idx]
        return True, category
    except ValueError:
        return False, ""


def _extract_response_text(result: object) -> str:
    """Normalise the return value of ``LLMRails.generate_async``.

    NeMo 0.10.x can return a ``str``, a ``dict`` with ``content`` key, or
    another object.  This helper always returns a plain string.

    Args:
        result: Raw return value from ``generate_async``.

    Returns:
        Plain string representation.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return str(result.get("content", result))
    return str(result)


class NemoRailsLayer:
    """Layer 3 NeMo Guardrails wrapper for LightAgent.

    Initialised once from the ``config/nemo_rails/`` directory.  When
    ``nemoguardrails`` is not installed the layer reports ``available=False``
    and all checks pass through without blocking.

    Thread-safe: the underlying ``LLMRails`` object is created once in
    ``__init__`` and reused across all async calls.

    Args:
        config_path: Path to the directory containing ``config.yml``
            and Colang ``*.co`` files.  Typically ``config/nemo_rails/``.
    """

    def __init__(self, config_path: Path) -> None:
        """Load NeMo rails config from ``config_path``."""
        self._rails: Any | None = None
        self._config_path = config_path

        if not config_path.is_dir():
            logger.warning(
                "nemo_rails_config_dir_missing",
                path=str(config_path),
            )
            return

        try:
            from nemoguardrails import LLMRails, RailsConfig

            rails_config = RailsConfig.from_path(str(config_path))
            self._rails = LLMRails(rails_config)
            logger.info("nemo_rails_loaded", config_path=str(config_path))
        except ImportError:
            logger.warning(
                "nemoguardrails_not_installed",
                hint="Install with: pip install 'nemoguardrails>=0.10.1'",
            )
        except Exception as exc:
            logger.warning("nemo_rails_init_failed", error=str(exc))

    @property
    def available(self) -> bool:
        """True if NeMo was successfully initialised and is ready."""
        return self._rails is not None

    async def check_input(self, text: str) -> tuple[bool, str]:
        """Run NeMo input rails on sanitized user text.

        When an input rail triggers, NeMo returns a refusal message
        (beginning with ``[NEMO_BLOCKED:<category>]``) **without** calling
        the main LLM, so response time is short.  A :data:`_NEMO_TIMEOUT_SECONDS`
        timeout prevents blocking when rails do not trigger (main LLM call
        would be required — we fail open rather than wait).

        Args:
            text: Sanitized user input to check.

        Returns:
            ``(blocked, category)`` — when ``blocked`` is True, ``category``
            is the Colang category tag (e.g. ``"violence"``).
        """
        if self._rails is None:
            return False, ""

        try:
            result = await asyncio.wait_for(
                self._rails.generate_async(messages=[{"role": "user", "content": text}]),
                timeout=_NEMO_TIMEOUT_SECONDS,
            )
            response_text = _extract_response_text(result)
            blocked, category = _parse_block_response(response_text)
            if blocked:
                logger.info(
                    "nemo_input_blocked",
                    category=category,
                    text_length=len(text),
                )
            return blocked, category
        except TimeoutError:
            # Input passed all rails — NeMo is waiting for LLM; fail open.
            logger.debug("nemo_input_check_timeout", text_length=len(text))
            return False, ""
        except Exception as exc:
            logger.warning("nemo_input_check_error", error=str(exc))
            return False, ""

    async def check_output(self, text: str) -> tuple[bool, str]:
        """Run NeMo output rails on LLM-generated text.

        Passes the text as an assistant message so NeMo evaluates whether
        the output triggers any configured output rail flows.  Uses the same
        timeout and fail-open semantics as :meth:`check_input`.

        Args:
            text: LLM-generated output text to evaluate.

        Returns:
            ``(blocked, category)`` where ``blocked`` is True when an
            output rail triggered.
        """
        if self._rails is None:
            return False, ""

        try:
            result = await asyncio.wait_for(
                self._rails.generate_async(
                    messages=[
                        {
                            "role": "user",
                            "content": "__output_rail_check__",
                        },
                        {"role": "assistant", "content": text},
                    ]
                ),
                timeout=_NEMO_TIMEOUT_SECONDS,
            )
            response_text = _extract_response_text(result)
            blocked, category = _parse_block_response(response_text)
            if blocked:
                logger.info(
                    "nemo_output_blocked",
                    category=category,
                    text_length=len(text),
                )
            return blocked, category
        except TimeoutError:
            logger.debug("nemo_output_check_timeout", text_length=len(text))
            return False, ""
        except Exception as exc:
            logger.warning("nemo_output_check_error", error=str(exc))
            return False, ""


def get_nemo_layer(config_path: Path | None = None) -> NemoRailsLayer | None:
    """Return a :class:`NemoRailsLayer` if NeMo guardrails are enabled.

    Reads ``settings.nemo_guardrails_enabled``.  Returns ``None`` immediately
    (zero overhead) when the setting is False, satisfying AC-019-7.

    Args:
        config_path: Override for the NeMo config directory.  Defaults to
            ``config/nemo_rails`` relative to the current working directory.

    Returns:
        Initialised :class:`NemoRailsLayer` or ``None`` when disabled.
    """
    import lightagent.core.config as _config_module

    settings = _config_module.get_settings()
    if not settings.nemo_guardrails_enabled:
        return None

    path = config_path or Path("config/nemo_rails")
    return NemoRailsLayer(path)


__all__ = ["NemoRailsLayer", "_parse_block_response", "get_nemo_layer"]
