"""Prismal custom exception hierarchy.

All framework-specific errors inherit from ``PrismalError``, allowing
callers to catch the entire hierarchy with a single ``except PrismalError``.

Hierarchy::

    PrismalError
    ├── SecurityError
    │   ├── InjectionDetectedError
    │   ├── PermissionDeniedError
    │   └── CanaryLeakError
    ├── ProviderError
    │   ├── ModelNotFoundError
    │   └── ProviderTimeoutError
    ├── SkillError
    │   ├── SkillLoadError
    │   └── SkillValidationError
    ├── MCPError
    │   ├── MCPConnectionError
    │   └── MCPToolError
    └── RAGError
        ├── DocumentLoadError
        └── RAGIndexError
"""

from __future__ import annotations

# ── Base ─────────────────────────────────────────────────────────────────────


class PrismalError(Exception):
    """Base class for all Prismal-specific errors."""


# ── Security ──────────────────────────────────────────────────────────────────


class SecurityError(PrismalError):
    """Raised when a security policy is violated."""


class InjectionDetectedError(SecurityError):
    """Raised when prompt injection is detected in user input.

    Args:
        text: The original input text (stored in full; message display is truncated).
        risk_score: Computed risk score 0-100.
        patterns: Names of the injection patterns matched.
    """

    def __init__(self, text: str, risk_score: int, patterns: list[str]) -> None:
        """Initialize InjectionDetectedError."""
        self.text = text
        self.risk_score = risk_score
        self.patterns = patterns
        preview = text[:80] + "..." if len(text) > 80 else text
        super().__init__(
            f"Injection detected (risk={risk_score}): '{preview}' matched patterns: {patterns}"
        )


class PermissionDeniedError(SecurityError):
    """Raised when an action is denied by the permission manager.

    Args:
        resource: The resource path/identifier that was accessed.
        action: The attempted action (read, write, execute, network, shell).
    """

    def __init__(self, resource: str, action: str) -> None:
        """Initialize PermissionDeniedError."""
        self.resource = resource
        self.action = action
        super().__init__(f"Permission denied: action='{action}' on resource='{resource}'")


class CanaryLeakError(SecurityError):
    """Raised when a canary token is detected in LLM output.

    Indicates the system prompt was leaked in the response.

    Args:
        token: The detected canary token value.
    """

    def __init__(self, token: str) -> None:
        """Initialize CanaryLeakError."""
        self.token = token
        super().__init__(f"Canary token leaked in LLM output: '{token}'")


# ── Provider ──────────────────────────────────────────────────────────────────


class ProviderError(PrismalError):
    """Raised when an LLM provider call fails."""


class ModelNotFoundError(ProviderError):
    """Raised when the requested model is not configured or reachable.

    Args:
        model_id: The model identifier that was requested.
    """

    def __init__(self, model_id: str) -> None:
        """Initialize ModelNotFoundError."""
        self.model_id = model_id
        super().__init__(f"Model not found or not configured: '{model_id}'")


class ProviderTimeoutError(ProviderError):
    """Raised when an LLM provider call times out.

    Args:
        model_id: The model that timed out.
        timeout_seconds: The configured timeout that was exceeded.
    """

    def __init__(self, model_id: str, timeout_seconds: int) -> None:
        """Initialize ProviderTimeoutError."""
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Provider timeout after {timeout_seconds}s for model '{model_id}'")


# ── Skill ─────────────────────────────────────────────────────────────────────


class SkillError(PrismalError):
    """Raised when a skill operation fails."""


class SkillLoadError(SkillError):
    """Raised when a skill module fails to load.

    Args:
        skill_name: The name of the skill that failed to load.
        reason: Human-readable reason for the failure.
    """

    def __init__(self, skill_name: str, reason: str) -> None:
        """Initialize SkillLoadError."""
        self.skill_name = skill_name
        self.reason = reason
        super().__init__(f"Failed to load skill '{skill_name}': {reason}")


class SkillValidationError(SkillError):
    """Raised when a skill fails code quality validation.

    Args:
        skill_name: The name of the skill that failed validation.
        violations: List of violation messages from linters/type checkers.
    """

    def __init__(self, skill_name: str, violations: list[str]) -> None:
        """Initialize SkillValidationError."""
        self.skill_name = skill_name
        self.violations = violations
        joined = "; ".join(violations)
        super().__init__(
            f"Skill '{skill_name}' failed validation with {len(violations)} violation(s): {joined}"
        )


# ── MCP ───────────────────────────────────────────────────────────────────────


class MCPError(PrismalError):
    """Raised when an MCP operation fails."""


class MCPConnectionError(MCPError):
    """Raised when a connection to an MCP server fails.

    Args:
        server_name: The name of the MCP server from config.
        reason: Human-readable reason for the connection failure.
    """

    def __init__(self, server_name: str, reason: str) -> None:
        """Initialize MCPConnectionError."""
        self.server_name = server_name
        self.reason = reason
        super().__init__(f"Cannot connect to MCP server '{server_name}': {reason}")


class MCPToolError(MCPError):
    """Raised when an MCP tool call fails.

    Args:
        tool_name: The name of the tool that failed.
        server_name: The MCP server that hosts the tool.
        reason: Human-readable reason for the tool failure.
    """

    def __init__(self, tool_name: str, server_name: str, reason: str = "") -> None:
        """Initialize MCPToolError."""
        self.tool_name = tool_name
        self.server_name = server_name
        self.reason = reason
        msg = f"MCP tool '{tool_name}' on server '{server_name}' failed"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


# ── RAG ───────────────────────────────────────────────────────────────────────


class RAGError(PrismalError):
    """Raised when a RAG operation fails."""


class DocumentLoadError(RAGError):  # reserved for future use by the document loader
    """Raised when a document fails to load.

    Args:
        path: The file path that failed to load.
        reason: Human-readable reason for the failure.
    """

    def __init__(self, path: str, reason: str) -> None:
        """Initialize DocumentLoadError."""
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to load document '{path}': {reason}")


class RAGIndexError(RAGError):  # reserved for future use by the indexing pipeline
    """Raised when document indexing fails.

    Args:
        reason: Human-readable reason for the indexing failure.
    """

    def __init__(self, reason: str) -> None:
        """Initialize RAGIndexError."""
        self.reason = reason
        super().__init__(f"Indexing failed: {reason}")


class HyDEError(RAGError):
    """Raised when HyDE hypothesis generation or embedding fails."""


class FusionError(RAGError):
    """Raised when RAG-Fusion query-variant generation or fusion fails."""


class HybridSearchError(RAGError):
    """Raised when hybrid BM25+semantic search fails."""


class SelfRAGError(RAGError):
    """Raised when Self-RAG decision, generation, or self-assessment fails."""


class HierarchicalRAGError(RAGError):
    """Raised when hierarchical parent-child RAG operations fail."""


class MultiVectorError(RAGError):
    """Raised when multi-vector indexing or search fails."""


class AdaptiveRAGError(RAGError):
    """Raised when adaptive RAG routing or dispatch fails."""


# ── Scheduler ─────────────────────────────────────────────────────────────────


class SchedulerError(PrismalError):
    """Raised when a scheduler operation fails."""


class CronJobNotFoundError(SchedulerError):
    """Raised when a cron job name cannot be found.

    Args:
        name: The job name that was not found.
    """

    def __init__(self, name: str) -> None:
        """Initialize CronJobNotFoundError."""
        self.name = name
        super().__init__(f"Cron job '{name}' not found")


class CronJobExistsError(SchedulerError):
    """Raised when a cron job name is already registered.

    Args:
        name: The duplicate job name.
    """

    def __init__(self, name: str) -> None:
        """Initialize CronJobExistsError."""
        self.name = name
        super().__init__(f"Cron job '{name}' already exists")


# ── Memory ────────────────────────────────────────────────────────────────────


class MemoryError(PrismalError):
    """Raised when a memory operation fails."""


class MemoryRedactionError(MemoryError):
    """Raised when sensitive-data redaction fails unexpectedly.

    Args:
        reason: Human-readable reason for the failure.
    """

    def __init__(self, reason: str) -> None:
        """Initialize MemoryRedactionError."""
        self.reason = reason
        super().__init__(f"Memory redaction failed: {reason}")


# ── Agent patterns (Fase B / SPEC-PAT-001..007) ─────────────────────────────
# Canonical definitions live in ``prismal/agents/patterns/*.py``;
# re-exported here so callers can ``from prismal.core.exceptions import
# DebateError`` without needing to know which module owns each pattern.


class ToTError(PrismalError):
    """Tree-of-Thoughts search error (SPEC-PAT-001).

    Canonical class in :mod:`prismal.agents.patterns.tree_of_thoughts`.
    """


class DebateError(PrismalError):
    """Debate pattern error (SPEC-PAT-002).

    Canonical class in :mod:`prismal.agents.patterns.debate`.
    """


class ConstitutionalError(PrismalError):
    """Constitutional-AI filter error (SPEC-PAT-003).

    Canonical class in :mod:`prismal.agents.patterns.constitutional`.
    """


class LATSError(PrismalError):
    """Language-Agent-Tree-Search / MCTS error (SPEC-PAT-004).

    Canonical class in :mod:`prismal.agents.patterns.lats`.
    """


class CompilerError(PrismalError):
    """LLM-Compiler plan / execution error (SPEC-PAT-005).

    Canonical class in :mod:`prismal.agents.patterns.llm_compiler`.
    """


class MoAError(PrismalError):
    """Mixture-of-Agents error (SPEC-PAT-006).

    Canonical class in :mod:`prismal.agents.patterns.mixture_of_agents`.
    """


class SwarmError(PrismalError):
    """Swarm handoff error (SPEC-PAT-007).

    Canonical class in :mod:`prismal.agents.patterns.swarm`.
    """


# ── Subgraph pipelines (Fase C) ──────────────────────────────────────────────


class CustomerServiceError(PrismalError):
    """Customer-service subgraph error (SPEC-SUBGRAPH-001)."""


class DocumentGenerationError(PrismalError):
    """Document-generation subgraph error (C2)."""


class DataETLError(PrismalError):
    """Data-ETL subgraph error (C3)."""


class CodeReviewError(PrismalError):
    """Code-review subgraph error (SPEC-SUBGRAPH-002)."""


class DebateConsensusError(PrismalError):
    """Debate/consensus subgraph error (C5)."""


__all__ = [
    "AdaptiveRAGError",
    "CanaryLeakError",
    "CodeReviewError",
    "CompilerError",
    "ConstitutionalError",
    "CronJobExistsError",
    "CronJobNotFoundError",
    "CustomerServiceError",
    "DataETLError",
    "DebateConsensusError",
    "DebateError",
    "DocumentGenerationError",
    "DocumentLoadError",
    "FusionError",
    "HierarchicalRAGError",
    "HyDEError",
    "HybridSearchError",
    "InjectionDetectedError",
    "LATSError",
    "PrismalError",
    "MCPConnectionError",
    "MCPError",
    "MCPToolError",
    "MemoryError",
    "MemoryRedactionError",
    "MoAError",
    "ModelNotFoundError",
    "MultiVectorError",
    "PermissionDeniedError",
    "ProviderError",
    "ProviderTimeoutError",
    "RAGError",
    "RAGIndexError",
    "SchedulerError",
    "SecurityError",
    "SelfRAGError",
    "SkillError",
    "SkillLoadError",
    "SkillValidationError",
    "SwarmError",
    "ToTError",
]
