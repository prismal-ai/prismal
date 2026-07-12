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

from typing import Literal

# ── Base ─────────────────────────────────────────────────────────────────────


class PrismalError(Exception):
    """Base class for all Prismal-specific errors."""


class BudgetExceeded(PrismalError):  # noqa: N818 — SPEC-CST-ERR-001 name
    """A run exceeded one dimension of its :class:`~prismal.budget.types.Budget`.

    Args:
        dimension: The breached dimension (``"tokens"``, ``"cost"``, ``"calls"``
            or ``"wall_clock"``).
        used: The accumulated value on that dimension at the time of the breach.
        limit: The configured ceiling for that dimension.
        scope: The budget scope (``"turn"``, ``"session"``, ``"tenant"`` or, for
            the unified Skynet budget, ``"skynet"``).
    """

    def __init__(self, dimension: str, used: float, limit: float, scope: str = "turn") -> None:
        """Initialize BudgetExceeded."""
        self.dimension = dimension
        self.used = used
        self.limit = limit
        self.scope = scope
        super().__init__(f"Budget exceeded on {dimension} ({scope}): {used} >= {limit}")


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


# ── Runtime hardening (Phase H — SPEC-HRD-ERR-001) ──────────────────────────────


class HardeningError(PrismalError):
    """Base class for the opt-in runtime-hardening layer (Phase H)."""


class IndirectInjectionBlocked(HardeningError):  # noqa: N818 — SPEC-HRD-ERR-001 name
    """Untrusted content was blocked by the indirect-injection detector.

    Raised only in ``enforce`` mode and caught at the ``react_loop`` seam, where
    it is converted into a neutralised/blocked tool result.
    """


class OutputValidationError(HardeningError):
    """Model output failed schema/escape validation before use."""


class ToolPolicyDenied(HardeningError):  # noqa: N818 — SPEC-HRD-ERR-001 name
    """A tool call was denied by the declarative ``ToolPolicyEngine``."""


class RunawayStopped(HardeningError):  # noqa: N818 — SPEC-HRD-ERR-001 name
    """A run hit the explicit step cap or stagnated.

    Maps to the same graceful-partial path used for a hard budget cap.
    """


class HardeningConfigError(HardeningError):
    """The tool-policy YAML or a hardening setting failed to load/validate."""


# ── Agent identity & access governance (Phase IDN — SPEC-IDN-ERR-001) ──────────


class IdentityError(PrismalError):
    """Base class for the opt-in identity & access governance layer (Phase IDN)."""


class DidVerificationError(IdentityError):
    """A DID could not be resolved/verified (tampered signature, unreachable did:web)."""


class ScopeError(IdentityError):
    """An action's resource falls outside the identity's least-privilege scopes."""


class PolicyDenied(IdentityError):  # noqa: N818 — SPEC-IDN-ERR-001 name
    """A ``PolicyEngine`` denied an action in ``enforce`` mode (caught at the seam)."""


class CredentialResolutionError(IdentityError):
    """The credential vault could not resolve a ``credential_ref`` within scope."""


class IdentityConfigError(IdentityError):
    """An identity setting or the identity-policy YAML failed to load/validate."""


class DelegationError(IdentityError):
    """An on-behalf token widened scopes or was used while expired/revoked."""


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


class VectorStoreError(RAGError):
    """Generic failure of a vector store (SPEC-VS-011).

    Generalizes the legacy ``ChromaStoreError`` (which now subclasses this) so
    that callers can catch any backend's failure uniformly. Inherits from
    :class:`RAGError` so existing ``except RAGError`` handlers keep working.
    """


class VectorStoreBackendUnavailable(VectorStoreError):  # noqa: N818 — SPEC-VS-011 name
    """The selected vector-store backend's optional extra is not installed.

    Args:
        backend: The ``settings.vector_store_backend`` value that was requested.
        extra: The pip extra that provides it (e.g. ``"lancedb"``).
    """

    def __init__(self, backend: str, extra: str) -> None:
        """Initialize VectorStoreBackendUnavailable."""
        self.backend = backend
        self.extra = extra
        super().__init__(
            f"Vector store backend '{backend}' is not available. "
            f"Install it with: pip install 'prismal[{extra}]'."
        )


# ── Runtime composition (Phase R) ───────────────────────────────────────────


class RuntimeCompositionError(PrismalError):
    """Failure composing the runtime (SPEC-CR-007).

    Raised by :func:`prismal.composition.build_runtime` when a sub-port cannot
    be assembled. Carries the name of the port that failed so the host can
    pinpoint the broken dependency; ``build_runtime`` first tears down whatever
    it already created before raising, so no resources leak.

    Args:
        port: The port being composed when the failure happened
            (e.g. ``"tool_provider"``, ``"vector_store"``, ``"checkpointer"``).
        cause: Human-readable description of the underlying failure.
    """

    def __init__(self, port: str, cause: str) -> None:
        """Initialize RuntimeCompositionError."""
        self.port = port
        self.cause = cause
        super().__init__(f"Failed to compose runtime port '{port}': {cause}")


# ── Configuration source injection (Fase W) ────────────────────────────────────


class ConfigSourceError(PrismalError):
    """No usable configuration source (Phase W — SPEC-CSI-012).

    Raised by :func:`prismal.core.config.build_settings` when no
    :class:`~prismal.core.config_source.ConfigSourcePort` is injected and
    ``settings.config_source_strict`` is ``True`` (a host that must never read
    the ambient environment), or when an injected source fails irrecoverably.

    Args:
        source: The failing source name (e.g. ``"none"``, ``"vault"``).
        cause: Optional human-readable description of the underlying failure.
    """

    def __init__(self, source: str, cause: str = "") -> None:
        """Initialize ConfigSourceError."""
        self.source = source
        self.cause = cause
        msg = f"Configuration source '{source}' unavailable"
        super().__init__(f"{msg}: {cause}" if cause else msg + ".")


# ── Evaluation harness (Phase V — SPEC-EVL-ERR-001) ────────────────────────────


class EvalError(PrismalError):
    """Base class for evaluation-harness failures."""


class EvalSetError(EvalError):
    """A malformed eval-set or red-team corpus (SPEC-EVL-ERR-001)."""


class RegressionGateFailed(EvalError):  # noqa: N818 — SPEC-EVL-ERR-001 name
    """The CI regression gate rejected a scorecard vs its baseline."""


# ── Observability integration (Phase OBS — SPEC-OBS-ERR-001) ───────────────────


class ObservabilityError(PrismalError):
    """Base class for the opt-in observability-integration layer (Phase OBS)."""


class ObservabilityConfigError(ObservabilityError):
    """A malformed ``observability_*`` setting value (SPEC-OBS-ERR-001)."""


class RunNotFoundError(ObservabilityError):
    """A run summary was requested for an unknown/evicted ``run_id``.

    Raised only by callers that opt into strict lookup;
    :meth:`ObservabilityPort.get_run_summary` itself returns ``None``, never
    raises (SPEC-OBS-PRT-001 / DD-OBS-006).
    """


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


# ── Extension surface (Fase X) ────────────────────────────────────────────────


class ExtensionError(PrismalError):
    """Base for errors raised by the public extension surface."""


class NodeExecutionError(ExtensionError):
    """Error captured while executing a node wrapped by ``@prismal_node``.

    Args:
        node_name: Name of the failing node.
        state_keys: Keys present in the state passed to the node (for context;
            values are never stored to avoid leaking user content).
        cause: The original exception raised by the user function.
    """

    def __init__(
        self,
        node_name: str,
        state_keys: list[str],
        cause: BaseException | None,
    ) -> None:
        """Initialize NodeExecutionError.

        ``cause`` is ``BaseException | None``: most nodes fail with a concrete
        exception, but :class:`NodeValidationError` may carry ``None`` (a
        schema rejection with only field-level messages).
        """
        self.node_name = node_name
        self.state_keys = state_keys
        self.cause = cause
        super().__init__(f"Node '{node_name}' failed: {cause!r}")


class NodeTimeoutError(NodeExecutionError):
    """Raised when a decorated node exceeds its ``timeout_s``.

    Args:
        node_name: Name of the node.
        state_keys: Keys present in the state at timeout.
        cause: Underlying ``TimeoutError`` (or the cancellation cause).
        timeout_s: The configured timeout that was exceeded.
    """

    def __init__(
        self,
        node_name: str,
        state_keys: list[str],
        cause: BaseException,
        *,
        timeout_s: float,
    ) -> None:
        """Initialize NodeTimeoutError."""
        self.timeout_s = timeout_s
        super().__init__(node_name, state_keys, cause)
        self.args = (f"Node '{node_name}' timed out after {timeout_s}s",)


class NodeValidationError(NodeExecutionError):
    """Raised when a node's declared ``input_model``/``output_model`` rejects the
    state (Phase NTS — SPEC-NTS-ERR-001).

    Extends :class:`NodeExecutionError` (so ``error_mapping_middleware`` catches
    it for free) with the side of the contract that failed and field-level
    messages. Consistent with the ``state_keys``-not-values convention,
    ``schema_errors`` carries field names and pydantic's own type/constraint
    description only — never field values.

    Args:
        node_name: Name of the node whose contract failed.
        state_keys: Keys present in the state/state_update at failure time.
        cause: The underlying pydantic ``ValidationError``, or ``None``.
        direction: ``"input"`` or ``"output"`` — which side of the contract failed.
        schema_errors: Field-level messages (never field values).
    """

    def __init__(
        self,
        node_name: str,
        state_keys: list[str],
        cause: BaseException | None,
        *,
        direction: Literal["input", "output"],
        schema_errors: list[str],
    ) -> None:
        """Initialize NodeValidationError."""
        self.direction = direction
        self.schema_errors = schema_errors
        super().__init__(node_name, state_keys, cause)


class PluginLoadError(ExtensionError):
    """Error raised while loading a plugin from an entry point.

    Args:
        plugin_name: Entry-point name of the plugin.
        entry_point: The ``module:object`` reference that failed.
        cause: The original exception.
    """

    def __init__(
        self,
        plugin_name: str,
        entry_point: str,
        cause: BaseException,
    ) -> None:
        """Initialize PluginLoadError."""
        self.plugin_name = plugin_name
        self.entry_point = entry_point
        self.cause = cause
        super().__init__(f"Plugin '{plugin_name}' ({entry_point}) failed to load: {cause!r}")


class PluginConflictError(ExtensionError):
    """Raised when two plugins try to register the same name.

    Args:
        conflicting_name: The name both plugins tried to register.
        plugins: Distribution names of the conflicting plugins.
    """

    def __init__(self, conflicting_name: str, plugins: list[str]) -> None:
        """Initialize PluginConflictError."""
        self.conflicting_name = conflicting_name
        self.plugins = plugins
        super().__init__(f"Plugin name conflict on '{conflicting_name}': {plugins}")


# Name mandated by SPEC-TPI-010 (public API) — kept without the Error suffix.
class ToolProviderNotConfigured(ExtensionError):  # noqa: N818
    """No ``ToolProviderPort`` injected and ``settings.tool_provider_strict`` is True.

    Args:
        agent_name: Agent whose tool resolution found no provider.
    """

    def __init__(self, agent_name: str) -> None:
        """Initialize ToolProviderNotConfigured."""
        self.agent_name = agent_name
        super().__init__(
            f"No tool provider configured for agent '{agent_name}'. "
            "Call set_tool_provider(...) at startup, or set "
            "settings.tool_provider_strict=False to fall back to stubs."
        )


class AdapterError(ExtensionError):
    """Base for adapter errors."""


class LangChainAdapterError(AdapterError):
    """Error raised by :class:`LangChainRunnableAdapter`.

    Args:
        runnable_type: Type name of the offending Runnable.
        message: Human-readable description of the failure.
    """

    def __init__(self, runnable_type: str, message: str) -> None:
        """Initialize LangChainAdapterError."""
        self.runnable_type = runnable_type
        super().__init__(f"{message} (runnable_type={runnable_type})")


# ── Multimodal (Fase F) ───────────────────────────────────────────────────────


class MultimodalError(PrismalError):
    """Base for errors raised by the multimodal layer (Fase F)."""


class STTError(MultimodalError):
    """Speech-to-text transcription failed in the backend."""


class TTSError(MultimodalError):
    """Text-to-speech synthesis failed across every configured backend."""


class VisionAgentError(MultimodalError):
    """:class:`VisionAgent` failed (validation or VLM call)."""


class AudioAgentError(MultimodalError):
    """:class:`AudioAgent` failed at one of its pipeline stages."""


class VideoAgentError(MultimodalError):
    """:class:`VideoAgent` failed (extraction, transcription or fusion)."""


class ModalityRouterError(MultimodalError):
    """Modality classification or routing failed."""


class MultimodalFusionError(MultimodalError):
    """Fusion of modal contributions failed."""


class MultimodalRAGError(RAGError):
    """Multimodal RAG indexing or search failed.

    Inherits from :class:`RAGError` so callers catching the RAG hierarchy keep
    working when they enable the multimodal engine.
    """


class MediaValidationError(PrismalError):
    """Incoming media was rejected by :class:`MediaValidator`.

    This is intentionally *not* a :class:`MultimodalError`: rejection happens
    before any multimodal agent runs.
    """


class KokoroError(PrismalError):
    """Base for errors raised by the Kokoro deliberation layer (Fase K)."""


class SoulValidationError(KokoroError):
    """Raised when a soul fails validation at load time.

    Args:
        soul_id: Identifier (or directory name) of the offending soul.
        reason: Human-readable reason for the failure.
    """

    def __init__(self, soul_id: str, reason: str) -> None:
        """Initialize SoulValidationError."""
        self.soul_id = soul_id
        self.reason = reason
        super().__init__(f"Soul '{soul_id}' failed validation: {reason}")


class SoulNotFoundError(KokoroError):
    """Raised when a soul id cannot be resolved in any souls tier.

    Args:
        soul_id: The soul identifier that was not found.
    """

    def __init__(self, soul_id: str) -> None:
        """Initialize SoulNotFoundError."""
        self.soul_id = soul_id
        super().__init__(f"Soul '{soul_id}' not found")


class KokoroConfigError(KokoroError):
    """Raised when the Kokoro configuration is invalid (e.g. triad arity != 3)."""


class DeliberationError(KokoroError):
    """Raised when the multi-soul deliberation fails."""


class JudgeError(KokoroError):
    """Raised when the Kokoro judge fails to render or execute a verdict."""


class SkynetError(PrismalError):
    """Base for errors raised by the Skynet swarm supervisor (Fase S)."""


class SkynetPlanError(SkynetError):
    """Raised when the supervisor fails to decompose a goal into a SwarmPlan."""


class SwarmWorkerError(SkynetError):
    """A worker failed executing its order.

    Captured per-worker as ``WorkerResult(success=False)`` — never raised out
    of the worker node, so one worker's failure does not abort the swarm.
    """


class SkynetConfigError(SkynetError):
    """Raised when the Skynet configuration is invalid.

    For example a fixed ``skynet_swarm_size`` larger than the effective swarm
    cap, or an invalid token budget.
    """


class SkynetBudgetExceeded(BudgetExceeded, SkynetError):  # noqa: N818 — SPEC-SKY-ERR-001 name
    """Raised when a Skynet run exceeds its ``skynet_token_budget``.

    Unified with the general budget engine (Phase C): now also a
    :class:`BudgetExceeded`, so general budget handlers catch it while existing
    ``except SkynetError`` handlers keep working.
    """

    def __init__(self, used: float, limit: float) -> None:
        """Initialize SkynetBudgetExceeded on the token dimension."""
        super().__init__("tokens", used, limit, scope="skynet")


class MissingDependencyError(PrismalError):
    """Raised when a backend whose optional extra is not installed is requested.

    Args:
        message: Human-readable description of what is missing.
        extra_to_install: The pip extra that provides the missing dependency
            (e.g. ``"multimodal-embed"``), surfaced so the message can suggest
            ``pip install "prismal[<extra>]"``.
    """

    def __init__(self, message: str, *, extra_to_install: str) -> None:
        """Initialize MissingDependencyError."""
        self.extra_to_install = extra_to_install
        super().__init__(f'{message} (install with: pip install "prismal[{extra_to_install}]")')


# ── A2A interop (Phase I) ──────────────────────────────────────────────────────


class A2AError(PrismalError):
    """Base for errors raised by the A2A agent-to-agent interop layer (Phase I)."""


class A2AAgentUnavailable(A2AError):  # noqa: N818 — SPEC-A2A-008 name
    """A remote A2A agent is unreachable, denied by the allowlist, or timed out.

    Args:
        agent: The remote agent identifier (Agent Card URL or name).
        reason: Human-readable reason for the failure.
    """

    def __init__(self, agent: str, reason: str) -> None:
        """Initialize A2AAgentUnavailable."""
        self.agent = agent
        self.reason = reason
        super().__init__(f"A2A agent {agent!r} unavailable: {reason}")


# ── Guardrails Modernization (Phase GRD) ───────────────────────────────────────


class GuardrailsModernizationError(PrismalError):
    """Base for errors raised by the guardrails-modernization layer (Phase GRD)."""


class NemoClassifierError(GuardrailsModernizationError):
    """Raised for reasoning safety-classifier failures surfaced to a caller.

    The classifier action itself never raises this on the hot path (timeout
    and provider errors fail open to ``"safe"``, per DD-GRD-003) — reserved
    for callers that opt out of the graceful verdict path.
    """


class NemoClassifierConfigError(NemoClassifierError):
    """Raised for a bad ``config/nemo_rails/`` config or a missing action registration."""


class StructuredOutputGuardError(GuardrailsModernizationError):
    """Base for :class:`~prismal.security.structured_output_guard.StructuredOutputGuard` errors."""


class StructuredOutputReaskExhausted(StructuredOutputGuardError):  # noqa: N818 — SPEC-GRD-ERR-001 name
    """Raised only if a caller opts out of the graceful ``StructuredOutputVerdict`` path.

    ``StructuredOutputGuard.validate()`` itself never raises this — it returns
    ``ok=False, reason="reask_exhausted"`` on the hot path (DD-GRD-005).
    """


# ── Loop Hardening (Phase LH) ───────────────────────────────────────────────


class LoopHardeningError(PrismalError):
    """Base for errors raised by the loop-hardening layer (Phase LH)."""


class ContextCompactionError(LoopHardeningError):
    """Internal use / tests — ``ContextCompactor.compact()`` itself never raises this outward."""


class ToolGatingConfigError(LoopHardeningError):
    """Raised for a bad ``tool_gating_phases.yaml`` — at load time, not per-request."""


# ── Blind Review Pipeline (Phase BRP) ────────────────────────────────────────


class BlindReviewPipelineError(PrismalError):
    """Base for all Blind Review Pipeline errors (Phase BRP)."""


class BlindReviewConfigError(BlindReviewPipelineError):
    """Invalid ``blind_review_*`` settings (threshold/iterations range)."""


class BlindReviewBlindnessViolationError(BlindReviewPipelineError):
    """Raised by ``BlindnessGuard`` when a reviewer prompt appears to embed
    ``state["messages"]`` content.

    Defense-in-depth backstop — the primary control is the reviewer node's
    narrow input contract (SPEC-BRP-REV-001).
    """


__all__ = [
    "A2AAgentUnavailable",
    "A2AError",
    "AdapterError",
    "AdaptiveRAGError",
    "AudioAgentError",
    "BlindReviewBlindnessViolationError",
    "BlindReviewConfigError",
    "BlindReviewPipelineError",
    "BudgetExceeded",
    "CanaryLeakError",
    "CodeReviewError",
    "CompilerError",
    "ConstitutionalError",
    "ContextCompactionError",
    "CredentialResolutionError",
    "CronJobExistsError",
    "CronJobNotFoundError",
    "CustomerServiceError",
    "DataETLError",
    "DebateConsensusError",
    "DebateError",
    "DelegationError",
    "DeliberationError",
    "DidVerificationError",
    "DocumentGenerationError",
    "DocumentLoadError",
    "EvalError",
    "EvalSetError",
    "ExtensionError",
    "FusionError",
    "GuardrailsModernizationError",
    "HierarchicalRAGError",
    "HyDEError",
    "HybridSearchError",
    "IdentityConfigError",
    "IdentityError",
    "InjectionDetectedError",
    "JudgeError",
    "KokoroConfigError",
    "KokoroError",
    "LATSError",
    "LangChainAdapterError",
    "LoopHardeningError",
    "MCPConnectionError",
    "MCPError",
    "MCPToolError",
    "MediaValidationError",
    "MemoryError",
    "MemoryRedactionError",
    "MissingDependencyError",
    "MoAError",
    "ModalityRouterError",
    "ModelNotFoundError",
    "MultiVectorError",
    "MultimodalError",
    "MultimodalFusionError",
    "MultimodalRAGError",
    "NemoClassifierConfigError",
    "NemoClassifierError",
    "NodeExecutionError",
    "NodeTimeoutError",
    "NodeValidationError",
    "PermissionDeniedError",
    "PluginConflictError",
    "PluginLoadError",
    "PolicyDenied",
    "PrismalError",
    "ProviderError",
    "ProviderTimeoutError",
    "RAGError",
    "RAGIndexError",
    "RegressionGateFailed",
    "RuntimeCompositionError",
    "STTError",
    "SchedulerError",
    "ScopeError",
    "SecurityError",
    "SelfRAGError",
    "SkillError",
    "SkillLoadError",
    "SkillValidationError",
    "SkynetBudgetExceeded",
    "SkynetConfigError",
    "SkynetError",
    "SkynetPlanError",
    "SoulNotFoundError",
    "SoulValidationError",
    "StructuredOutputGuardError",
    "StructuredOutputReaskExhausted",
    "SwarmError",
    "SwarmWorkerError",
    "TTSError",
    "ToTError",
    "ToolGatingConfigError",
    "ToolProviderNotConfigured",
    "VectorStoreBackendUnavailable",
    "VectorStoreError",
    "VideoAgentError",
    "VisionAgentError",
]
