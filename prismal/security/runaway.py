"""Runaway guard — explicit loop bound (Phase H — SPEC-HRD-RUN-001).

Budget caps tokens/cost/calls/wall-clock, but a loop can still thrash *within*
budget — repeating the same failing tool or oscillating between two nodes.
``RunawayGuard`` adds the missing signal: an explicit step cap plus stagnation
detection (N consecutive turns with an identical action signature). On a breach
it surfaces a stop that ``react_loop`` turns into its graceful-partial path,
exactly like a hard budget cap.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import xxhash

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger("prismal.security.runaway")


@dataclass(frozen=True)
class RunawayStatus:
    """The verdict for one runaway-guard tick."""

    stop: bool
    reason: Literal["", "step_cap", "stagnation"]
    step: int


class RunawayGuard:
    """Tracks step count + a rolling window of action signatures per run."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        """Initialize from *settings* (resolved from ``get_settings()`` if None)."""
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._max_steps = int(settings.hardening_runaway_max_steps)
        self._window = int(settings.hardening_runaway_stagnation_window)
        self._step = 0
        # Keep at most ``window`` recent signatures; deque(maxlen=0) is invalid,
        # so disabled stagnation (window=0) uses maxlen=1 and is gated below.
        self._recent: deque[str] = deque(maxlen=max(self._window, 1))

    def tick(self, *, node: str, signature: str) -> RunawayStatus:
        """Record one model/agent turn and return the resulting status.

        Args:
            node: The node/agent name for this turn.
            signature: A per-action signature (e.g. tool + args); combined with
                *node* and hashed into the stagnation window.
        """
        self._step += 1
        sig = xxhash.xxh3_64_hexdigest(f"{node}\x00{signature}".encode())
        self._recent.append(sig)

        if self._max_steps > 0 and self._step > self._max_steps:
            logger.warning("runaway.step_cap", step=self._step, max_steps=self._max_steps)
            return RunawayStatus(stop=True, reason="step_cap", step=self._step)

        if self._window > 0 and len(self._recent) >= self._window and len(set(self._recent)) == 1:
            logger.warning("runaway.stagnation", step=self._step, window=self._window)
            return RunawayStatus(stop=True, reason="stagnation", step=self._step)

        return RunawayStatus(stop=False, reason="", step=self._step)


__all__ = ["RunawayGuard", "RunawayStatus"]
