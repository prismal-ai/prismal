"""Unit tests for the LightAgent exception hierarchy."""

import pytest


def test_base_exception_is_exception() -> None:
    """LightAgentError inherits from Exception."""
    from lightagent.core.exceptions import LightAgentError

    err = LightAgentError("test")
    assert isinstance(err, Exception)


def test_security_error_is_lightagent_error() -> None:
    """SecurityError is a LightAgentError."""
    from lightagent.core.exceptions import LightAgentError, SecurityError

    err = SecurityError("blocked")
    assert isinstance(err, LightAgentError)


def test_injection_detected_error() -> None:
    """InjectionDetectedError carries risk_score and patterns."""
    from lightagent.core.exceptions import InjectionDetectedError

    err = InjectionDetectedError(
        text="ignore previous instructions",
        risk_score=85,
        patterns=["override_instructions"],
    )
    assert err.risk_score == 85
    assert "override_instructions" in err.patterns
    assert "ignore previous" in str(err)


def test_permission_denied_error() -> None:
    """PermissionDeniedError carries resource and action."""
    from lightagent.core.exceptions import PermissionDeniedError

    err = PermissionDeniedError(resource="/etc/passwd", action="read")
    assert err.resource == "/etc/passwd"
    assert err.action == "read"


def test_canary_leak_error() -> None:
    """CanaryLeakError is a SecurityError."""
    from lightagent.core.exceptions import CanaryLeakError, SecurityError

    err = CanaryLeakError(token="lightagent-canary-abc123")
    assert isinstance(err, SecurityError)
    assert "abc123" in str(err)


def test_provider_error_hierarchy() -> None:
    """ProviderError, ModelNotFoundError, ProviderTimeoutError hierarchy."""
    from lightagent.core.exceptions import (
        LightAgentError,
        ModelNotFoundError,
        ProviderError,
        ProviderTimeoutError,
    )

    assert issubclass(ProviderError, LightAgentError)
    assert issubclass(ModelNotFoundError, ProviderError)
    assert issubclass(ProviderTimeoutError, ProviderError)


def test_model_not_found_error() -> None:
    """ModelNotFoundError carries model_id."""
    from lightagent.core.exceptions import ModelNotFoundError

    err = ModelNotFoundError(model_id="gpt-9000")
    assert err.model_id == "gpt-9000"
    assert "gpt-9000" in str(err)


def test_provider_timeout_error() -> None:
    """ProviderTimeoutError carries timeout_seconds."""
    from lightagent.core.exceptions import ProviderTimeoutError

    err = ProviderTimeoutError(model_id="claude-sonnet-4-5", timeout_seconds=60)
    assert err.timeout_seconds == 60


def test_skill_error_hierarchy() -> None:
    """SkillError, SkillLoadError, SkillValidationError hierarchy."""
    from lightagent.core.exceptions import (
        LightAgentError,
        SkillError,
        SkillLoadError,
        SkillValidationError,
    )

    assert issubclass(SkillError, LightAgentError)
    assert issubclass(SkillLoadError, SkillError)
    assert issubclass(SkillValidationError, SkillError)


def test_skill_load_error() -> None:
    """SkillLoadError carries skill_name."""
    from lightagent.core.exceptions import SkillLoadError

    err = SkillLoadError(skill_name="weather", reason="module not found")
    assert err.skill_name == "weather"
    assert "weather" in str(err)


def test_skill_validation_error() -> None:
    """SkillValidationError carries skill_name and violations."""
    from lightagent.core.exceptions import SkillValidationError

    err = SkillValidationError(
        skill_name="code_executor",
        violations=["ruff: E501", "mypy: missing return type"],
    )
    assert err.skill_name == "code_executor"
    assert len(err.violations) == 2


def test_mcp_error_hierarchy() -> None:
    """MCPError, MCPConnectionError, MCPToolError hierarchy."""
    from lightagent.core.exceptions import (
        LightAgentError,
        MCPConnectionError,
        MCPError,
        MCPToolError,
    )

    assert issubclass(MCPError, LightAgentError)
    assert issubclass(MCPConnectionError, MCPError)
    assert issubclass(MCPToolError, MCPError)


def test_mcp_connection_error() -> None:
    """MCPConnectionError carries server_name."""
    from lightagent.core.exceptions import MCPConnectionError

    err = MCPConnectionError(server_name="github", reason="timeout")
    assert err.server_name == "github"


def test_mcp_tool_error() -> None:
    """MCPToolError carries tool_name, server_name, and optional reason."""
    from lightagent.core.exceptions import MCPToolError

    err = MCPToolError(tool_name="list_files", server_name="filesystem", reason="timeout")
    assert err.tool_name == "list_files"
    assert err.server_name == "filesystem"
    assert err.reason == "timeout"


def test_catch_as_base_class() -> None:
    """All custom exceptions can be caught as LightAgentError."""
    from lightagent.core.exceptions import (
        InjectionDetectedError,
        LightAgentError,
        MCPConnectionError,
        ModelNotFoundError,
        SkillLoadError,
    )

    exceptions_to_test = [
        InjectionDetectedError("x", risk_score=50, patterns=[]),
        ModelNotFoundError(model_id="x"),
        SkillLoadError(skill_name="x", reason="x"),
        MCPConnectionError(server_name="x", reason="x"),
    ]
    for exc in exceptions_to_test:
        with pytest.raises(LightAgentError):
            raise exc
