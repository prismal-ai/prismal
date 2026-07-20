"""Unit tests for the Prismal exception hierarchy."""

import pytest


def test_base_exception_is_exception() -> None:
    """PrismalError inherits from Exception."""
    from prismal.core.exceptions import PrismalError

    err = PrismalError("test")
    assert isinstance(err, Exception)


def test_security_error_is_prismal_error() -> None:
    """SecurityError is a PrismalError."""
    from prismal.core.exceptions import PrismalError, SecurityError

    err = SecurityError("blocked")
    assert isinstance(err, PrismalError)


def test_injection_detected_error() -> None:
    """InjectionDetectedError carries risk_score and patterns."""
    from prismal.core.exceptions import InjectionDetectedError

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
    from prismal.core.exceptions import PermissionDeniedError

    err = PermissionDeniedError(resource="/etc/passwd", action="read")
    assert err.resource == "/etc/passwd"
    assert err.action == "read"


def test_canary_leak_error() -> None:
    """CanaryLeakError is a SecurityError."""
    from prismal.core.exceptions import CanaryLeakError, SecurityError

    err = CanaryLeakError(token="prismal-canary-abc123")
    assert isinstance(err, SecurityError)
    assert "abc123" in str(err)


def test_provider_error_hierarchy() -> None:
    """ProviderError, ModelNotFoundError, ProviderTimeoutError hierarchy."""
    from prismal.core.exceptions import (
        ModelNotFoundError,
        PrismalError,
        ProviderError,
        ProviderTimeoutError,
    )

    assert issubclass(ProviderError, PrismalError)
    assert issubclass(ModelNotFoundError, ProviderError)
    assert issubclass(ProviderTimeoutError, ProviderError)


def test_model_not_found_error() -> None:
    """ModelNotFoundError carries model_id."""
    from prismal.core.exceptions import ModelNotFoundError

    err = ModelNotFoundError(model_id="gpt-9000")
    assert err.model_id == "gpt-9000"
    assert "gpt-9000" in str(err)


def test_provider_timeout_error() -> None:
    """ProviderTimeoutError carries timeout_seconds."""
    from prismal.core.exceptions import ProviderTimeoutError

    err = ProviderTimeoutError(model_id="claude-sonnet-4-5", timeout_seconds=60)
    assert err.timeout_seconds == 60


def test_skill_error_hierarchy() -> None:
    """SkillError, SkillLoadError, SkillValidationError hierarchy."""
    from prismal.core.exceptions import (
        PrismalError,
        SkillError,
        SkillLoadError,
        SkillValidationError,
    )

    assert issubclass(SkillError, PrismalError)
    assert issubclass(SkillLoadError, SkillError)
    assert issubclass(SkillValidationError, SkillError)


def test_skill_load_error() -> None:
    """SkillLoadError carries skill_name."""
    from prismal.core.exceptions import SkillLoadError

    err = SkillLoadError(skill_name="weather", reason="module not found")
    assert err.skill_name == "weather"
    assert "weather" in str(err)


def test_skill_validation_error() -> None:
    """SkillValidationError carries skill_name and violations."""
    from prismal.core.exceptions import SkillValidationError

    err = SkillValidationError(
        skill_name="code_executor",
        violations=["ruff: E501", "mypy: missing return type"],
    )
    assert err.skill_name == "code_executor"
    assert len(err.violations) == 2


def test_mcp_error_hierarchy() -> None:
    """MCPError, MCPConnectionError, MCPToolError hierarchy."""
    from prismal.core.exceptions import (
        MCPConnectionError,
        MCPError,
        MCPToolError,
        PrismalError,
    )

    assert issubclass(MCPError, PrismalError)
    assert issubclass(MCPConnectionError, MCPError)
    assert issubclass(MCPToolError, MCPError)


def test_mcp_connection_error() -> None:
    """MCPConnectionError carries server_name."""
    from prismal.core.exceptions import MCPConnectionError

    err = MCPConnectionError(server_name="github", reason="timeout")
    assert err.server_name == "github"


def test_mcp_tool_error() -> None:
    """MCPToolError carries tool_name, server_name, and optional reason."""
    from prismal.core.exceptions import MCPToolError

    err = MCPToolError(tool_name="list_files", server_name="filesystem", reason="timeout")
    assert err.tool_name == "list_files"
    assert err.server_name == "filesystem"
    assert err.reason == "timeout"


def test_catch_as_base_class() -> None:
    """All custom exceptions can be caught as PrismalError."""
    from prismal.core.exceptions import (
        InjectionDetectedError,
        MCPConnectionError,
        ModelNotFoundError,
        PrismalError,
        SkillLoadError,
    )

    exceptions_to_test = [
        InjectionDetectedError("x", risk_score=50, patterns=[]),
        ModelNotFoundError(model_id="x"),
        SkillLoadError(skill_name="x", reason="x"),
        MCPConnectionError(server_name="x", reason="x"),
    ]
    for exc in exceptions_to_test:
        with pytest.raises(PrismalError):
            raise exc


def test_blind_review_error_hierarchy() -> None:
    """BlindReviewPipelineError, config, and blindness-violation hierarchy (SPEC-BRP-ERR-001)."""
    from prismal.core.exceptions import (
        BlindReviewBlindnessViolationError,
        BlindReviewConfigError,
        BlindReviewPipelineError,
        PrismalError,
    )

    assert issubclass(BlindReviewPipelineError, PrismalError)
    assert issubclass(BlindReviewConfigError, BlindReviewPipelineError)
    assert issubclass(BlindReviewBlindnessViolationError, BlindReviewPipelineError)


def test_skynet_role_error_hierarchy() -> None:
    """SkynetRoleError is a SkynetError (SPEC-SP-ERR-001, load-time only)."""
    from prismal.core.exceptions import PrismalError, SkynetError, SkynetRoleError

    assert issubclass(SkynetRoleError, SkynetError)
    assert issubclass(SkynetRoleError, PrismalError)
    with pytest.raises(SkynetError):
        raise SkynetRoleError("malformed skynet_roles.yaml")
