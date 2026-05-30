"""Prismal core package: config, logging, exceptions, database."""

# Activate the LIGHTAGENT_ -> PRISMAL_ environment fallback before any settings
# or os.getenv read occurs (side effect on import). Transitional; see env_compat.
from prismal.core import env_compat as _env_compat  # noqa: F401
from prismal.core.config import Settings, get_settings
from prismal.core.database import Base, get_db_session, init_db
from prismal.core.exceptions import (
    CanaryLeakError,
    InjectionDetectedError,
    MCPConnectionError,
    MCPError,
    MCPToolError,
    ModelNotFoundError,
    PermissionDeniedError,
    PrismalError,
    ProviderError,
    ProviderTimeoutError,
    SecurityError,
    SkillError,
    SkillLoadError,
    SkillValidationError,
)
from prismal.core.logging import get_logger, setup_logging

__all__ = [
    "Base",
    "CanaryLeakError",
    "InjectionDetectedError",
    "MCPConnectionError",
    "MCPError",
    "MCPToolError",
    "ModelNotFoundError",
    "PermissionDeniedError",
    "PrismalError",
    "ProviderError",
    "ProviderTimeoutError",
    "SecurityError",
    "Settings",
    "SkillError",
    "SkillLoadError",
    "SkillValidationError",
    "get_db_session",
    "get_logger",
    "get_settings",
    "init_db",
    "setup_logging",
]
