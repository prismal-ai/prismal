"""Prismal security package — 5-layer defense-in-depth.

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
from prismal.security.indirect_injection import (
    IndirectInjectionDetector,
    InjectionVerdict,
)
from prismal.security.media_validator import (
    MediaKind,
    MediaValidationResult,
    MediaValidator,
)
from prismal.security.output_validator import OutputValidator, OutputVerdict
from prismal.security.permissions import (
    PermissionManager,
    PermissionRecord,
    PermissionType,
)
from prismal.security.prompt_builder import SecurePromptBuilder
from prismal.security.runaway import RunawayGuard, RunawayStatus
from prismal.security.sanitizer import MAX_INPUT_LENGTH, InputSanitizer
from prismal.security.taint import Provenance, TaintRegistry, TaintTag
from prismal.security.tool_policy import (
    PolicyDecision,
    PolicyEffect,
    RunToolPolicy,
    ToolPolicy,
    ToolPolicyEngine,
    load_tool_policies,
)

__all__ = [
    "MAX_INPUT_LENGTH",
    "ActionInterceptor",
    "AuditLogger",
    "GuardrailResult",
    "GuardrailsEngine",
    "IndirectInjectionDetector",
    "InjectionVerdict",
    "InputSanitizer",
    "MediaKind",
    "MediaValidationResult",
    "MediaValidator",
    "OutputValidator",
    "OutputVerdict",
    "PermissionManager",
    "PermissionRecord",
    "PermissionType",
    "PolicyDecision",
    "PolicyEffect",
    "Provenance",
    "RunToolPolicy",
    "RunawayGuard",
    "RunawayStatus",
    "SecurePromptBuilder",
    "TaintRegistry",
    "TaintTag",
    "ToolPolicy",
    "ToolPolicyEngine",
    "load_tool_policies",
]
