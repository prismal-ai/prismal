"""Phase 32 — Prompt Quality Framework validation (SPEC-034 AC-034-4).

Verifies that every base-agent and pipeline-agent system prompt follows the
7-component framework (Purpose, Input, Output, Success Criteria, Instructions,
Background, Examples), that positive JSON examples in pipeline prompts validate
against their Pydantic artifact schemas, and that numeric thresholds quoted in
`## Success Criteria` stay in sync with the downstream gate threshold constants.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pydantic import BaseModel

from lightagent.agents.subgraphs.artifacts import (
    CodeArtifact,
    QAReport,
    ReviewResult,
    TechnicalSpec,
    UserStory,
)
from lightagent.agents.subgraphs.artifacts import (
    TestReport as _TestReport,  # aliased: pytest treats `Test*` classes as test suites
)
from lightagent.agents.subgraphs.financial.artifacts import (
    FinancialReport,
    FundamentalAnalysis,
    MarketSnapshot,
    RiskSentimentReport,
    TechnicalAnalysis,
)
from lightagent.agents.subgraphs.ml_pipeline.artifacts import (
    DatasetProfile,
    EDAReport,
    EvaluationReport,
    FeatureSet,
    ModelPackage,
    TrainedModel,
)

# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS: tuple[str, ...] = (
    "## Purpose",
    "## Input",
    "## Output",
    "## Success Criteria",
    "## Instructions",
    "## Background",
    "## Examples",
)

BASE_AGENT_MODULES: tuple[str, ...] = (
    "lightagent.agents.planner",
    "lightagent.agents.researcher",
    "lightagent.agents.coder",
    "lightagent.agents.rag_agent",
    "lightagent.agents.critic",
    "lightagent.agents.data_analyst",
    "lightagent.agents.file_manager",
    "lightagent.agents.cron_manager",
    "lightagent.agents.skill_manager",
)

# Pipeline modules mapped to their target Pydantic artifact class.
# The positive example in each prompt must validate against this schema.
PIPELINE_AGENTS: dict[str, type[BaseModel]] = {
    "lightagent.agents.subgraphs.dev_pipeline.po_agent": UserStory,
    "lightagent.agents.subgraphs.dev_pipeline.architect_agent": TechnicalSpec,
    "lightagent.agents.subgraphs.dev_pipeline.developer_agent": CodeArtifact,
    "lightagent.agents.subgraphs.dev_pipeline.unit_test_agent": _TestReport,
    "lightagent.agents.subgraphs.dev_pipeline.qa_agent": QAReport,
    "lightagent.agents.subgraphs.dev_pipeline.reviewer_agent": ReviewResult,
    "lightagent.agents.subgraphs.ml_pipeline.data_ingester": DatasetProfile,
    "lightagent.agents.subgraphs.ml_pipeline.eda_analyst": EDAReport,
    "lightagent.agents.subgraphs.ml_pipeline.feature_engineer": FeatureSet,
    "lightagent.agents.subgraphs.ml_pipeline.model_trainer": TrainedModel,
    "lightagent.agents.subgraphs.ml_pipeline.model_evaluator": EvaluationReport,
    "lightagent.agents.subgraphs.ml_pipeline.model_exporter": ModelPackage,
    "lightagent.agents.subgraphs.financial.market_data_collector": MarketSnapshot,
    "lightagent.agents.subgraphs.financial.technical_analyst": TechnicalAnalysis,
    "lightagent.agents.subgraphs.financial.fundamental_analyst": FundamentalAnalysis,
    "lightagent.agents.subgraphs.financial.risk_sentiment_analyst": (
        RiskSentimentReport
    ),
    "lightagent.agents.subgraphs.financial.report_generator": FinancialReport,
}


def _load_prompt(module_name: str) -> str:
    """Return the `_SYSTEM_PROMPT` or `_SYSTEM` string constant for *module_name*."""
    module = importlib.import_module(module_name)
    for attr in ("_SYSTEM_PROMPT", "_SYSTEM"):
        if hasattr(module, attr):
            value = getattr(module, attr)
            assert isinstance(value, str), f"{module_name}.{attr} must be str"
            return value
    raise AssertionError(
        f"{module_name} exposes neither `_SYSTEM_PROMPT` nor `_SYSTEM`"
    )


# Positive example block starts after `### Positive` (optionally followed by
# extra words) and ends at the next `### ` heading or end of string.
_POSITIVE_BLOCK_RE = re.compile(
    r"###\s*(?:Example\s*\d+\s*[-—]\s*)?Positive[^\n]*\n(?P<body>.*?)(?=\n###\s|\Z)",
    re.DOTALL,
)
# Greedy-match the first balanced-looking JSON object in the block.
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def _extract_first_positive_json(prompt: str) -> dict | None:
    """Extract the first JSON object appearing under a `### Positive` heading.

    Returns ``None`` when no positive block is found or the block contains no
    JSON-like payload.
    """
    block_match = _POSITIVE_BLOCK_RE.search(prompt)
    if not block_match:
        return None
    body = block_match.group("body")
    json_match = _JSON_OBJECT_RE.search(body)
    if not json_match:
        return None
    raw = json_match.group(0)
    # Strip single-line `//` comments — used sparingly in Output schema blocks
    # but defensively removed here so the parser never chokes on them.
    cleaned = re.sub(r"//[^\n]*", "", raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Tests — AC-034-4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", BASE_AGENT_MODULES)
def test_base_agent_prompts_have_required_sections(module_name: str) -> None:
    """Every base-agent prompt contains all 7 framework section headers.

    Enforces SPEC-034 AC-034-1.
    """
    prompt = _load_prompt(module_name)
    missing = [section for section in REQUIRED_SECTIONS if section not in prompt]
    assert not missing, (
        f"{module_name} is missing required prompt sections: {missing}"
    )


@pytest.mark.parametrize("module_name", sorted(PIPELINE_AGENTS))
def test_pipeline_agent_prompts_have_all_7_sections(module_name: str) -> None:
    """Every pipeline-agent prompt contains all 7 framework section headers.

    Enforces SPEC-034 AC-034-2.
    """
    prompt = _load_prompt(module_name)
    missing = [section for section in REQUIRED_SECTIONS if section not in prompt]
    assert not missing, (
        f"{module_name} is missing required prompt sections: {missing}"
    )


@pytest.mark.parametrize(
    ("module_name", "schema"),
    list(PIPELINE_AGENTS.items()),
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
def test_positive_json_examples_match_pydantic_schemas(
    module_name: str, schema: type[BaseModel]
) -> None:
    """The first positive JSON example in each pipeline prompt validates
    against its Pydantic artifact schema.

    Enforces SPEC-034 AC-034-5: at least one positive example matches schema.
    """
    prompt = _load_prompt(module_name)
    payload = _extract_first_positive_json(prompt)
    assert payload is not None, (
        f"{module_name} prompt has no parseable positive JSON example under "
        f"`### Positive`"
    )
    # ``model_validate`` raises ``ValidationError`` on any schema mismatch,
    # which pytest will surface with a precise field-level diagnostic.
    schema.model_validate(payload)


def test_pipeline_prompts_have_at_least_one_negative_example() -> None:
    """Every pipeline prompt must include a `### Negative` counter-example.

    Enforces SPEC-034 AC-034-2 (at least 1 positive AND 1 negative example).
    """
    offenders: list[str] = []
    for module_name in PIPELINE_AGENTS:
        prompt = _load_prompt(module_name)
        if "### Negative" not in prompt:
            offenders.append(module_name)
    assert not offenders, (
        f"Pipeline prompts without `### Negative` examples: {offenders}"
    )


def test_reviewer_success_criteria_threshold_matches_score_gate() -> None:
    """The reviewer prompt's Success Criteria threshold matches the dev_pipeline
    reviewer score_gate threshold (0.8).

    Enforces SPEC-034 AC-034-3: numeric thresholds in Success Criteria align
    with the gate constants used downstream.
    """
    builder_src = Path(
        "lightagent/agents/subgraphs/dev_pipeline/builder.py"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"_REVIEWER_GATE\s*=\s*score_gate\([^)]*threshold\s*=\s*(?P<t>[0-9.]+)",
        builder_src,
        re.DOTALL,
    )
    assert match, "Could not locate _REVIEWER_GATE threshold in dev_pipeline builder"
    gate_threshold = float(match.group("t"))

    prompt = _load_prompt("lightagent.agents.subgraphs.dev_pipeline.reviewer_agent")
    # The reviewer prompt explicitly states `score >= 0.8`. The literal must
    # equal the gate threshold for the rubric to be coherent.
    literal = f"score >= {gate_threshold}"
    assert literal in prompt, (
        f"Reviewer prompt must reference the gate threshold literal "
        f"'{literal}'; gate value={gate_threshold}"
    )


def test_model_evaluator_threshold_mentions_settings_variable() -> None:
    """The model_evaluator prompt must route via
    ``settings.ml_quality_threshold`` rather than hard-coding a number, since
    the ml_pipeline gate reads that setting at runtime.
    """
    prompt = _load_prompt(
        "lightagent.agents.subgraphs.ml_pipeline.model_evaluator"
    )
    assert "settings.ml_quality_threshold" in prompt, (
        "model_evaluator prompt must defer to settings.ml_quality_threshold "
        "so the Success Criteria stays in sync with the ml_pipeline gate"
    )
