"""Smoke test: all public security symbols are importable from the package root."""


def test_security_package_exports() -> None:
    """All public security symbols must be importable from prismal.security."""
    from prismal.security import (  # noqa: F401
        MAX_INPUT_LENGTH,
        ActionInterceptor,
        AuditLogger,
        GuardrailResult,
        GuardrailsEngine,
        InputSanitizer,
        PermissionManager,
        PermissionRecord,
        PermissionType,
        SecurePromptBuilder,
    )

    assert GuardrailsEngine is not None
    assert InputSanitizer is not None
    assert ActionInterceptor is not None
    assert AuditLogger is not None
    assert PermissionManager is not None
    assert SecurePromptBuilder is not None
