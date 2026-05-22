"""LightAgent security package — 5-layer defense-in-depth.

L1: InputSanitizer     — strip control chars, normalize unicode, enforce length
L2: GuardrailsEngine   — regex pattern matching + risk scoring
L4: ActionInterceptor  — LangChain callback for pre-tool permission checks
L5: AuditLogger        — immutable JSONL audit log with hash chaining

Support:
    PermissionManager    — TTL-based SQLite permission grants
    SecurePromptBuilder  — user input isolation with canary tokens
"""

from prismal.security.action_interceptor import ActionInterceptor
from prismal.security.audit import AuditLogger
from prismal.security.guardrails import GuardrailResult, GuardrailsEngine
from prismal.security.permissions import (
    PermissionManager,
    PermissionRecord,
    PermissionType,
)
from prismal.security.prompt_builder import SecurePromptBuilder
from prismal.security.sanitizer import MAX_INPUT_LENGTH, InputSanitizer

__all__ = [
    "MAX_INPUT_LENGTH",
    "ActionInterceptor",
    "AuditLogger",
    "GuardrailResult",
    "GuardrailsEngine",
    "InputSanitizer",
    "PermissionManager",
    "PermissionRecord",
    "PermissionType",
    "SecurePromptBuilder",
]
