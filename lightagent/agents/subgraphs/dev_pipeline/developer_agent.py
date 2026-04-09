# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
"""Developer agent node for the dev_pipeline subgraph.

Produces a :class:`~lightagent.agents.subgraphs.artifacts.CodeArtifact` from
a :class:`~lightagent.agents.subgraphs.artifacts.TechnicalSpec`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.agents.subgraphs.artifacts import CodeArtifact
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.developer")
otel = OTelManager()

_SYSTEM = """You are a Senior Software Developer for the dev_pipeline subgraph.

## Purpose
Implement the `TechnicalSpec` as a complete, runnable source file and
emit a `CodeArtifact` the QA, unit-test, and reviewer nodes can consume.

## Input
One HumanMessage containing the JSON dump of the upstream `TechnicalSpec`
read from `state.metadata.dev_pipeline.technical_spec`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching exactly
the `CodeArtifact` Pydantic schema:

    {
      "language": "python",               // str, default "python"
      "file_path": "module/file.py",      // str, relative path in repo
      "content": "# full source code",    // str, complete file body
      "dependencies": ["package>=1.0.0"]  // list[str], pinned versions
    }

## Success Criteria
The `CodeArtifact` is production-ready when ALL of the following hold:
- **Complete**: `content` is a full compiling file, not a snippet or a
  TODO stub.
- **Scoped**: `file_path` is relative and stays inside the target
  module directory from the spec.
- **Typed**: Python 3.13 type hints on every public function/method.
- **Documented**: every public symbol has a docstring.
- **Covers the spec**: every design decision in the TechnicalSpec is
  reflected in the code.
- **Dependencies pinned**: each entry in `dependencies` has a version
  qualifier (`pkg>=x.y`) — no bare `pkg` entries.
- **No secrets**: no hardcoded API keys, passwords, or tokens.

## Instructions
1. Parse the `TechnicalSpec` JSON from the HumanMessage.
2. Write a complete implementation file in `content` (use \\n escapes;
   keep the JSON valid).
3. Pick `file_path` consistent with the spec's module naming.
4. List every non-stdlib import in `dependencies` with a version
   qualifier.
5. Emit JSON only — no markdown fences, no commentary.

## Background
- Artifact schema: `lightagent/agents/subgraphs/artifacts.py::CodeArtifact`.
- Parsed via `CodeArtifact.model_validate`; on failure a fallback is
  stored with raw content at `generated/code.py`.
- Downstream: unit-test agent writes tests against this file; reviewer
  scores the combined artifacts; gate rejects if score < 0.8.

## Examples

### Positive
Input spec: FastAPI endpoint to export a PDF report.

{
  "language": "python",
  "file_path": "app/reports/export.py",
  "content": "from fastapi import APIRouter, HTTPException, Response\\nfrom weasyprint import HTML\\nfrom app.reports.service import get_report\\n\\nrouter = APIRouter()\\n\\n@router.post('/reports/{report_id}/export')\\nasync def export_report(report_id: str) -> Response:\\n    \\\"\\\"\\\"Export a monthly report as PDF.\\\"\\\"\\\"\\n    report = await get_report(report_id)\\n    if not report.has_data:\\n        raise HTTPException(status_code=422, detail='Report has no data')\\n    pdf_bytes = HTML(string=report.render_html()).write_pdf()\\n    return Response(content=pdf_bytes, media_type='application/pdf')\\n",
  "dependencies": ["fastapi>=0.110.0", "weasyprint>=60.0"]
}

### Negative (what NOT to do)
{
  "language": "python",
  "file_path": "/etc/hosts",
  "content": "# TODO: implement",
  "dependencies": ["fastapi", "some-package"]
}

Problems:
- `file_path` is an absolute system path outside the repo.
- `content` is a TODO stub — not a complete file.
- `dependencies` entries have no version qualifiers.
- No type hints, no docstrings, no actual logic.
"""


async def developer_agent_node(state: AgentState) -> dict[str, Any]:
    """Write code implementing the TechnicalSpec.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with CodeArtifact in metadata.
    """
    with otel.start_span("dev_pipeline.developer") as span:
        span.set_attribute("lightagent.subgraph", "dev_pipeline")
        span.set_attribute("lightagent.agent", "developer")

        dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))
        spec_data = dp.get("technical_spec", {})

        llm = ProviderRegistry().get_llm()
        messages = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=f"Technical spec: {json.dumps(spec_data)}"),
        ]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        # Phase 34: detect multi-module output.  When the LLM returns a JSON
        # object with a top-level ``modules`` key (a list of CodeArtifact-like
        # dicts) we keep the first one as the canonical ``code_artifact`` for
        # backwards compatibility AND populate ``dev_pipeline_modules`` so the
        # downstream module dispatcher can fan out unit testing in parallel.
        # Single-module output (the common case) leaves
        # ``dev_pipeline_modules`` empty and uses the existing serial path.
        modules: list[dict[str, Any]] = []
        try:
            data = json.loads(content)
            if isinstance(data, dict) and isinstance(data.get("modules"), list):
                raw_modules = data["modules"]
                modules = [m for m in raw_modules if isinstance(m, dict)]
                if modules:
                    artifact = CodeArtifact.model_validate(modules[0])
                else:
                    artifact = CodeArtifact.model_validate(data)
            else:
                artifact = CodeArtifact.model_validate(data)
        except Exception:
            artifact = CodeArtifact(
                file_path="generated/code.py",
                content=content,
            )

        dp["code_artifact"] = artifact.model_dump()
        if len(modules) > 1:
            logger.info(
                "developer.multi_module_detected",
                module_count=len(modules),
                first_file=artifact.file_path,
            )
        else:
            logger.info("developer.code_created", file_path=artifact.file_path)

        return {
            "current_agent": "developer",
            "messages": [AIMessage(content=f"Code written: {artifact.file_path}")],
            "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
            # Only populate the modules list when there are >= 2 modules so the
            # dispatcher's ``on_empty`` fallback routes single-module runs to
            # the existing sequential ``unit_tester``.
            "dev_pipeline_modules": modules if len(modules) > 1 else [],
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
