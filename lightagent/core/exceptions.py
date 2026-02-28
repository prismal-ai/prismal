"""LightAgent custom exception hierarchy.

All framework-specific errors inherit from ``LightAgentError``, allowing
callers to catch the entire hierarchy with a single ``except LightAgentError``.

Hierarchy::

    LightAgentError
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


class LightAgentError(Exception):
    """Base class for all LightAgent-specific errors."""


# ── Security ──────────────────────────────────────────────────────────────────


class SecurityError(LightAgentError):
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
            f"Injection detected (risk={risk_score}): '{preview}' "
            f"matched patterns: {patterns}"
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
        super().__init__(
            f"Permission denied: action='{action}' on resource='{resource}'"
        )


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


class ProviderError(LightAgentError):
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
        super().__init__(
            f"Provider timeout after {timeout_seconds}s for model '{model_id}'"
        )


# ── Skill ─────────────────────────────────────────────────────────────────────


class SkillError(LightAgentError):
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
            f"Skill '{skill_name}' failed validation with {len(violations)} "
            f"violation(s): {joined}"
        )


# ── MCP ───────────────────────────────────────────────────────────────────────


class MCPError(LightAgentError):
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
        super().__init__(
            f"Cannot connect to MCP server '{server_name}': {reason}"
        )


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


class RAGError(LightAgentError):
    """Raised when a RAG operation fails."""


class DocumentLoadError(RAGError):
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


class RAGIndexError(RAGError):
    """Raised when document indexing fails.

    Args:
        reason: Human-readable reason for the indexing failure.
    """

    def __init__(self, reason: str) -> None:
        """Initialize RAGIndexError."""
        self.reason = reason
        super().__init__(f"Indexing failed: {reason}")


__all__ = [
    "CanaryLeakError",
    "DocumentLoadError",
    "InjectionDetectedError",
    "LightAgentError",
    "MCPConnectionError",
    "MCPError",
    "MCPToolError",
    "ModelNotFoundError",
    "PermissionDeniedError",
    "ProviderError",
    "ProviderTimeoutError",
    "RAGError",
    "RAGIndexError",
    "SecurityError",
    "SkillError",
    "SkillLoadError",
    "SkillValidationError",
]
