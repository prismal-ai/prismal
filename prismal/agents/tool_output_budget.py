"""Per-tool output budgets (Phase TKE — SPEC-TKE-OUT-001).

Caps how many (estimated) tokens a single tool result may contribute to
``state["messages"]``. An over-budget result is parked in full in the
artifact store (SPEC-TKE-ART-001) and replaced by a head (60 %) + tail (40 %)
excerpt joined by a marker line naming the artifact id, so the model can page
through the rest with ``read_artifact``. Uniform tabular results may be encoded
as TOON (SPEC-TKE-TOON-001) when ``tool_output_toon_enabled``.

Ordering contract: callers apply the budget **after** the Phase H
sanitize/taint step, so the artifact holds the sanitized payload (the same
content the model would otherwise have seen).

Opt-in: effective only when ``token_efficiency_enabled`` and
``tool_output_budget_enabled`` are both on; otherwise :meth:`ToolOutputBudget.apply`
returns the pre-TKE text unchanged. Hot-path helper — never raises on
telemetry or artifact-store failures (fail-open: the excerpt is kept and
``artifact`` is ``None``).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from prismal.agents.artifacts import ArtifactRef, ArtifactStorePort, build_artifact_store
from prismal.core.config import get_settings
from prismal.utils.stable_json import stable_dumps
from prismal.utils.token_estimate import estimate_tokens
from prismal.utils.toon import is_uniform_tabular, maybe_toon

if TYPE_CHECKING:
    from prismal.core.config import Settings

#: Share of the excerpt budget given to the head (the rest goes to the tail).
HEAD_RATIO = 0.6
#: Characters per estimated token (mirror of ``estimate_tokens``).
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class BudgetedOutput:
    """Result of applying a tool-output budget (SPEC-TKE-OUT-001)."""

    content: str
    truncated: bool
    original_tokens: int
    artifact: ArtifactRef | None


def _increment(metric: str, attributes: dict[str, Any] | None = None) -> None:
    with contextlib.suppress(Exception):
        from prismal.monitoring import otel

        otel.OTelManager().increment_counter(metric, value=1, attributes=attributes or {})


def _marker(original_tokens: int, omitted: int, artifact_id: str) -> str:
    return (
        f"\n[truncated: {original_tokens} tokens ≈ {omitted} omitted; "
        f"full output artifact={artifact_id}; use read_artifact to fetch]\n"
    )


class ToolOutputBudget:
    """Apply per-tool output budgets with artifact spill-over (SPEC-TKE-OUT-001)."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        artifact_store: ArtifactStorePort | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._store = artifact_store

    @property
    def enabled(self) -> bool:
        """True only when the TKE master and ``tool_output_budget_enabled`` are on."""
        s = self._settings
        return bool(s.token_efficiency_enabled and s.tool_output_budget_enabled)

    def max_tokens_for(self, tool_name: str) -> int:
        """Per-tool override from ``tool_output_max_tokens_by_tool``, else the default."""
        overrides = self._settings.tool_output_max_tokens_by_tool
        if tool_name in overrides:
            return int(overrides[tool_name])
        return int(self._settings.tool_output_max_tokens_default)

    # -- internals ---------------------------------------------------------
    def _render(self, output: object) -> str:
        if isinstance(output, str):
            return output
        if self._settings.tool_output_toon_enabled and is_uniform_tabular(output):
            text = maybe_toon(output)
            _increment("toon_encoded")
            return text
        if isinstance(output, (dict, list)):
            return stable_dumps(output)
        return str(output)

    def _get_store(self) -> ArtifactStorePort:
        if self._store is None:
            self._store = build_artifact_store(self._settings)
        return self._store

    def _spill(self, text: str, *, tool_name: str, run_id: str, tenant: str) -> ArtifactRef | None:
        try:
            return self._get_store().put(
                text, run_id=run_id, tenant=tenant, kind="tool_output", tool=tool_name
            )
        except Exception:  # fail-open: keep the excerpt without an artifact
            return None

    # -- public --------------------------------------------------------------
    def apply(self, tool_name: str, output: object, *, run_id: str, tenant: str) -> BudgetedOutput:
        """Return *output* as message text, excerpted + spilled when over budget."""
        if not self.enabled:
            text = output if isinstance(output, str) else str(output)
            return BudgetedOutput(
                content=text, truncated=False, original_tokens=estimate_tokens(text), artifact=None
            )

        text = self._render(output)
        original = estimate_tokens(text)
        budget = max(self.max_tokens_for(tool_name), 0)
        if original <= budget:
            return BudgetedOutput(
                content=text, truncated=False, original_tokens=original, artifact=None
            )

        # Store the FULL text first so the excerpt can name the artifact.
        ref = self._spill(text, tool_name=tool_name, run_id=run_id, tenant=tenant)
        artifact_id = ref.id if ref is not None else "none"
        content = self._excerpt(text, budget=budget, original=original, artifact_id=artifact_id)
        _increment("tool_output_truncated", {"tool": tool_name})
        return BudgetedOutput(
            content=content, truncated=True, original_tokens=original, artifact=ref
        )

    @staticmethod
    def _excerpt(text: str, *, budget: int, original: int, artifact_id: str) -> str:
        """Head+tail excerpt whose length INCLUDING the marker fits ``budget`` tokens."""
        max_chars = budget * _CHARS_PER_TOKEN
        marker = _marker(original, original, artifact_id)
        # The marker length depends on the omitted count; iterate to a fixed point
        # (the count only shrinks, so its digit width never grows past the first guess).
        for _ in range(3):
            avail = max_chars - len(marker)
            if avail <= 0:
                return _marker(original, original, artifact_id)
            head_len = int(avail * HEAD_RATIO)
            tail_len = avail - head_len
            head = text[:head_len]
            tail = text[len(text) - tail_len :] if tail_len > 0 else ""
            omitted = max(original - estimate_tokens(head) - estimate_tokens(tail), 0)
            new_marker = _marker(original, omitted, artifact_id)
            if len(new_marker) <= len(marker):
                return head + new_marker + tail
            marker = new_marker
        return _marker(original, original, artifact_id)  # pragma: no cover - defensive


__all__ = ["HEAD_RATIO", "BudgetedOutput", "ToolOutputBudget"]
