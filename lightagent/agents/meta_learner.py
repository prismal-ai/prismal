"""Self-Improving Meta-Learning Agent (T-212).

Reviews past Langfuse traces, scores agent performance, and proposes
improvements to the system prompt or skill selection strategy.

Workflow
--------
1. ``fetch_traces()``   — query Langfuse HTTP API for the last N days.
2. ``score_traces()``   — use the LLM to self-score each trace
   (task_completion, accuracy, efficiency).
3. ``generate_proposals()`` — ask the LLM to propose improvements for
   low-scoring traces (overall < ``score_threshold``).
4. ``review()``         — orchestrates steps 1-3 and saves output to
   ``skills/custom/meta_proposals/`` with a human-review sentinel.

Acceptance criteria (T-212):
- Agent queries Langfuse for traces from the last 7 days.
- Self-scores on task completion, accuracy, and efficiency.
- Proposes system prompt edits or skill additions.
- Proposed changes go through the human-review gate.
- ``lightagent meta review`` triggers the cycle.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

logger = get_logger("lightagent.agents.meta_learner")

_DEFAULT_PROPOSALS_DIR = (
    Path(__file__).parent.parent / "skills" / "custom" / "meta_proposals"
)

_SCORE_THRESHOLD = 0.6  # traces with overall < this trigger proposals


class TraceScore(BaseModel):
    """Self-assessment score for a single Langfuse trace.

    Attributes:
        trace_id: Langfuse trace identifier.
        task_completion: 0.0-1.0 how fully the task was completed.
        accuracy: 0.0-1.0 factual accuracy of the response.
        efficiency: 0.0-1.0 token efficiency (low waste = high score).
        overall: Weighted average of the three dimensions.
        issues: Identified problems (empty list when score is high).
    """

    trace_id: str
    task_completion: float = Field(ge=0.0, le=1.0)
    accuracy: float = Field(ge=0.0, le=1.0)
    efficiency: float = Field(ge=0.0, le=1.0)
    overall: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class MetaLearner:
    """Orchestrates the meta-learning review cycle.

    Args:
        proposals_dir: Directory where proposals are saved.  Defaults to
            ``skills/custom/meta_proposals/``.
        score_threshold: Traces with ``overall`` below this are flagged.
    """

    def __init__(
        self,
        proposals_dir: Path | None = None,
        score_threshold: float = _SCORE_THRESHOLD,
    ) -> None:
        """Initialise the meta-learner."""
        self._proposals_dir = proposals_dir or _DEFAULT_PROPOSALS_DIR
        self._proposals_dir.mkdir(parents=True, exist_ok=True)
        self._score_threshold = score_threshold

    async def fetch_traces(self, days: int = 7) -> list[dict[str, Any]]:
        """Fetch recent traces from Langfuse.

        Uses the Langfuse SDK when enabled.  Returns an empty list when
        Langfuse is disabled or credentials are not configured.

        Args:
            days: Look-back window in days.

        Returns:
            List of raw trace dicts from the Langfuse API.
        """
        import lightagent.core.config as _cfg

        settings = _cfg.get_settings()
        if not settings.langfuse_enabled:
            logger.info("meta_learner_langfuse_disabled")
            return []

        try:
            from langfuse import Langfuse

            client = Langfuse(
                public_key=settings.langfuse_public_key.get_secret_value(),
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                host=settings.langfuse_host,
            )
            since = datetime.now(UTC) - timedelta(days=days)
            traces_page = client.fetch_traces(from_timestamp=since)
            raw: list[dict[str, Any]] = [
                t.__dict__ for t in (traces_page.data or [])
            ]
            logger.info("meta_learner_traces_fetched", count=len(raw))
            return raw
        except Exception as exc:
            logger.warning("meta_learner_fetch_error", error=str(exc))
            return []

    async def score_traces(
        self, traces: list[dict[str, Any]]
    ) -> list[TraceScore]:
        """Self-score traces using the LLM.

        Args:
            traces: Raw trace dicts from Langfuse.

        Returns:
            List of :class:`TraceScore` objects, one per trace.
        """
        if not traces:
            return []

        llm = ProviderRegistry().get_llm()
        scores: list[TraceScore] = []

        for trace in traces:
            trace_id = str(trace.get("id", "unknown"))
            input_text = str(trace.get("input", ""))[:500]
            output_text = str(trace.get("output", ""))[:500]

            prompt = (
                "You are a quality evaluator for an AI agent. "
                "Score this trace 0.0-1.0 on three dimensions.\n\n"
                f"TRACE ID: {trace_id}\n"
                f"INPUT: {input_text}\n"
                f"OUTPUT: {output_text}\n\n"
                "Respond ONLY with JSON:\n"
                '{"task_completion": 0.0, "accuracy": 0.0, '
                '"efficiency": 0.0, "issues": []}'
            )

            try:
                from langchain_core.messages import HumanMessage

                response = await llm.ainvoke([HumanMessage(content=prompt)])
                content = (
                    response.content
                    if hasattr(response, "content")
                    else str(response)
                )
                content_str: str = (
                    content if isinstance(content, str) else str(content)
                )

                # Extract JSON from response
                start = content_str.find("{")
                end = content_str.rfind("}") + 1
                parsed: dict[str, Any] = json.loads(content_str[start:end])

                tc = float(parsed.get("task_completion", 0.5))
                acc = float(parsed.get("accuracy", 0.5))
                eff = float(parsed.get("efficiency", 0.5))
                overall = round((tc + acc + eff) / 3, 3)
                issues: list[str] = parsed.get("issues", [])

                scores.append(
                    TraceScore(
                        trace_id=trace_id,
                        task_completion=tc,
                        accuracy=acc,
                        efficiency=eff,
                        overall=overall,
                        issues=issues,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "meta_learner_score_error",
                    trace_id=trace_id,
                    error=str(exc),
                )

        return scores

    async def generate_proposals(
        self, low_scores: list[TraceScore]
    ) -> str:
        """Ask the LLM to propose improvements for low-scoring traces.

        Args:
            low_scores: Traces with overall score below threshold.

        Returns:
            Proposed improvement text (Markdown).
        """
        if not low_scores:
            return ""

        llm = ProviderRegistry().get_llm()

        issues_summary = "\n".join(
            f"- Trace {s.trace_id}: overall={s.overall:.2f}, "
            f"issues={', '.join(s.issues) or 'none'}"
            for s in low_scores
        )

        prompt = (
            "You are a meta-learning agent reviewing your own performance.\n"
            "Based on these low-scoring traces, propose concrete improvements:\n\n"
            f"{issues_summary}\n\n"
            "Write a Markdown report with:\n"
            "1. **Root causes** of poor performance\n"
            "2. **System prompt improvements** (provide exact text changes)\n"
            "3. **New skills to add** (describe the skill in one sentence)\n"
            "4. **Routing improvements** for the supervisor\n\n"
            "Be specific and actionable."
        )

        try:
            from langchain_core.messages import HumanMessage

            response = await llm.ainvoke([HumanMessage(content=prompt)])
            content = (
                response.content
                if hasattr(response, "content")
                else str(response)
            )
            return content if isinstance(content, str) else str(content)
        except Exception as exc:
            logger.warning("meta_learner_proposals_error", error=str(exc))
            return ""

    async def review(self, days: int = 7) -> str:
        """Run the full meta-learning review cycle.

        1. Fetch traces from Langfuse.
        2. Score each trace.
        3. If low scores exist, generate proposals.
        4. Save proposals to disk with a human-review sentinel.

        Args:
            days: Look-back window for Langfuse traces.

        Returns:
            Summary string describing the outcome.
        """
        logger.info("meta_learner_review_start", days=days)

        traces = await self.fetch_traces(days=days)
        scores = await self.score_traces(traces)

        low_scores = [s for s in scores if s.overall < self._score_threshold]

        if not low_scores:
            msg = (
                f"Meta-review complete: {len(scores)} trace(s) scored. "
                "No issues detected — agent is performing well."
            )
            logger.info("meta_learner_no_issues", scored=len(scores))
            return msg

        proposals = await self.generate_proposals(low_scores)

        # Save to disk with human-review sentinel
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        proposal_file = self._proposals_dir / f"proposals_{timestamp}.md"
        sentinel_file = self._proposals_dir / "human_review_required.txt"

        report = (
            f"# Meta-Learning Proposals — {timestamp}\n\n"
            f"**Traces reviewed:** {len(scores)}\n"
            f"**Low-scoring traces:** {len(low_scores)} "
            f"(threshold: {self._score_threshold})\n\n"
            "## Low-Scoring Traces\n\n"
        )
        for s in low_scores:
            report += (
                f"- `{s.trace_id}`: overall={s.overall:.2f}, "
                f"issues: {', '.join(s.issues) or 'none'}\n"
            )
        report += f"\n## Proposals\n\n{proposals}\n"

        proposal_file.write_text(report, encoding="utf-8")
        sentinel_file.write_text(
            "AI-generated meta-learning proposals require human review.\n"
            "Review the proposals_*.md files and apply approved changes manually.\n"
            "Delete this file after review.\n",
            encoding="utf-8",
        )

        summary = (
            f"Meta-review complete: {len(scores)} trace(s) scored, "
            f"{len(low_scores)} low-scoring.\n"
            f"Proposals saved to: {proposal_file}\n"
            "ACTION REQUIRED: Review human_review_required.txt "
            f"in {self._proposals_dir}"
        )
        logger.info(
            "meta_learner_proposals_saved",
            proposal_file=str(proposal_file),
            low_scores=len(low_scores),
        )
        return summary


__all__ = ["MetaLearner", "TraceScore"]
