"""Process-level decision runtime (Phase DEC — composition seam for the integrations).

The host composes one :class:`DecisionRuntime` (``build_runtime``) or the first
integration call builds it lazily from settings. Integrations only ever read it
through :func:`get_decision_runtime`; with ``decisions_enabled=False`` nothing is
built and every integration is a no-op.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prismal.core.logging import get_logger
from prismal.decisions.shadow import ShadowRunner
from prismal.decisions.types import GatePolicy

if TYPE_CHECKING:
    from collections.abc import Iterator

    from prismal.core.config import Settings
    from prismal.decisions.cascade import DecisionCascade

logger = get_logger(__name__)


@dataclass
class DecisionRuntime:
    """Cascade + shadow runner + gate policy shared by the integrations."""

    cascade: DecisionCascade
    settings: Settings
    shadow: ShadowRunner | None = None
    policy: GatePolicy = field(init=False)

    def __post_init__(self) -> None:
        self.policy = GatePolicy.from_settings(self.settings)
        if self.shadow is None:
            self.shadow = ShadowRunner(
                self.cascade,
                sample_rate=self.settings.decision_shadow_sample_rate,
                settings=self.settings,
            )

    async def aclose(self) -> None:
        await self.cascade.aclose()


_RUNTIME: DecisionRuntime | None = None
_BOUND: ContextVar[DecisionRuntime | None] = ContextVar("prismal_decision_runtime", default=None)


def set_decision_runtime(runtime: DecisionRuntime | None) -> None:
    """Install (or clear) the process decision runtime (``global`` runtime mode)."""
    global _RUNTIME
    _RUNTIME = runtime


@contextmanager
def use_decision_runtime(runtime: DecisionRuntime | None) -> Iterator[DecisionRuntime | None]:
    """Bind a per-tenant runtime (e.g. ``RuntimeContext.decisions``) for this context."""
    token = _BOUND.set(runtime)
    try:
        yield runtime
    finally:
        _BOUND.reset(token)


def get_decision_runtime(settings: Settings | None = None) -> DecisionRuntime | None:
    """Bound runtime, else the process runtime, else (``global`` mode only) a lazy one.

    In ``context`` runtime mode nothing is built implicitly: without a bound
    runtime every integration stays on its pre-DEC path, so tenant keys and
    audit can never fall back to a process-wide composition.
    """
    global _RUNTIME
    bound = _BOUND.get()
    if bound is not None:
        return bound
    if (
        _RUNTIME is None
        and settings is not None
        and settings.decisions_enabled
        and getattr(settings, "runtime_mode", "global") == "global"
    ):
        from prismal.decisions.cascade import DecisionCascade
        from prismal.security.audit import AuditLogger

        try:
            _RUNTIME = DecisionRuntime(
                cascade=DecisionCascade.from_settings(settings, audit=AuditLogger()),
                settings=settings,
            )
        except Exception as exc:  # composition failure ⇒ integrations stay on pre-DEC paths
            logger.warning("decision_runtime_unavailable", error_type=type(exc).__name__)
            return None
    return _RUNTIME
