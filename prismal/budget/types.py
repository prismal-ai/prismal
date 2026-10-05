"""Immutable value objects for cost & budget governance (Phase C — SPEC-CST-TYP-001).

A :class:`Budget` is a spend ceiling for one :class:`BudgetScope`; ``0`` on any
dimension means *unlimited* (mirrors the existing ``skynet_token_budget``
convention). :class:`Usage` is the cumulative spend, summable with ``+``.
:class:`BudgetStatus` is the verdict of a guard check, and :class:`Degradation`
is the advice a pattern consumes on a soft cap.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class BudgetScope(StrEnum):
    """The accounting scope a budget applies to."""

    TURN = "turn"
    SESSION = "session"
    TENANT = "tenant"


@dataclass(frozen=True)
class Budget:
    """A spend ceiling. ``0`` on a dimension = unlimited for that dimension."""

    max_tokens: int = 0
    max_cost_usd: float = 0.0
    max_calls: int = 0
    max_wall_clock_s: float = 0.0
    scope: BudgetScope = BudgetScope.TURN

    @property
    def is_unlimited(self) -> bool:
        """True when no dimension is capped."""
        return (
            self.max_tokens == 0
            and self.max_cost_usd == 0.0
            and self.max_calls == 0
            and self.max_wall_clock_s == 0.0
        )


@dataclass(frozen=True)
class TokenCounts:
    """Token counts extracted from one LLM response.

    ``prompt_tokens`` is the total input *including* cached tokens and
    ``completion_tokens`` the total output *including* reasoning tokens; the
    cache/reasoning fields (Phase TKE — SPEC-TKE-ACC-001) are subsets of them.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        """Prompt + completion tokens."""
        return self.prompt_tokens + self.completion_tokens

    @property
    def uncached_prompt_tokens(self) -> int:
        """Input tokens billed at the full (uncached) rate."""
        return max(0, self.prompt_tokens - self.cache_read_tokens - self.cache_write_tokens)

    def normalized(self) -> TokenCounts:
        """Convert Anthropic-raw semantics (``input`` excludes cache) to totals.

        Detected when ``cache_read + cache_write > prompt_tokens``; otherwise
        the counts already follow the total-input convention and are returned
        unchanged.
        """
        cached = self.cache_read_tokens + self.cache_write_tokens
        if cached <= self.prompt_tokens:
            return self
        return replace(self, prompt_tokens=self.prompt_tokens + cached)


@dataclass(frozen=True)
class Usage:
    """Cumulative usage across one run.

    ``estimated`` is True when any contributing cost came from the pricing-table
    fallback rather than a provider-authoritative figure.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    wall_clock_s: float = 0.0
    estimated: bool = False
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_source: str = ""

    @property
    def total_tokens(self) -> int:
        """Prompt + completion tokens."""
        return self.prompt_tokens + self.completion_tokens

    @property
    def cache_hit_ratio(self) -> float:
        """Share of input tokens served from the provider prompt cache."""
        if self.prompt_tokens <= 0:
            return 0.0
        return self.cache_read_tokens / self.prompt_tokens

    def __add__(self, other: Usage) -> Usage:
        """Add each dimension; ``estimated`` is the logical OR of both operands.

        ``cost_source`` keeps the last non-empty source.
        """
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            calls=self.calls + other.calls,
            wall_clock_s=self.wall_clock_s + other.wall_clock_s,
            estimated=self.estimated or other.estimated,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cost_source=other.cost_source or self.cost_source,
        )


@dataclass(frozen=True)
class BudgetStatus:
    """The verdict of a :meth:`BudgetGuard.check`."""

    within: bool
    soft_exceeded: bool
    hard_exceeded: bool
    breached_dimension: str | None
    usage: Usage
    budget: Budget


@dataclass(frozen=True)
class Degradation:
    """Advice a pattern consumes when a budget is tight."""

    terminate: bool = False
    reduce: bool = False
    reason: str = ""
