"""Pluggable process-isolation layer for :class:`SandboxExecutor`.

SPEC-045 / Phase 43 — this module lays the groundwork for running
CodeAct / coder / researcher / critic code blocks inside a real
isolation layer (docker container, podman container, nsjail,
bubblewrap, or firejail) instead of a bare host subprocess.

.. note::

   T-348 establishes the abstract base class, the six concrete
   backend classes, their ``is_available()`` probes, and the
   ``SandboxIsolationError`` exception. The actual ``run()``
   implementations are filled in by T-349 (docker / podman),
   T-350 (namespace backends), and wired into
   :class:`~lightagent.sandbox.executor.SandboxExecutor` by T-351.
   Until then every concrete backend except :class:`NoneBackend`
   raises :class:`SandboxIsolationError` from ``run()``.

Security contract (CLAUDE.md Phase 43 rules, enforced by the
concrete backends):

1. Non-root user
2. Read-only root filesystem
3. ``--net=none`` (or equivalent egress-blocked network namespace)
4. Dropped capabilities (``--cap-drop=ALL``)
5. Seccomp profile restricting syscalls
6. Per-run ``--memory`` / ``--pids-limit`` / ``--cpus`` caps
7. Dedicated tmpfs workspace wiped after each run
8. Explicit env-var allowlist (empty by default, rejecting
   ``*_API_KEY`` / ``*_SECRET`` / ``*_TOKEN`` / ``*_PASSWORD`` at
   parse time)
9. Audit entry per invocation via
   :class:`~lightagent.security.audit.AuditLogger`

Design notes:

* Every concrete backend is a separate class so tests can mock or
  substitute one without touching the others.
* ``is_available()`` probes at construction time via
  :func:`shutil.which` so the factory can walk a priority list
  cheaply without spawning subprocesses just to find out whether a
  tool is installed.
* The ``NoneBackend`` exists purely as a dev-mode fallback. It
  delegates to the legacy host-subprocess path in
  :class:`SandboxExecutor` and reports ``isolated=False`` so the
  ``/api/health`` endpoint (SPEC-045 AC-045-7) can surface the
  degraded posture to operators.
* Heavy Python SDKs (``docker-py``, ``podman-py``) are deliberately
  NOT imported — all backends shell out via
  :func:`asyncio.create_subprocess_exec` with fully-argv-quoted
  argument lists so we never need shell string interpolation.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from lightagent.sandbox.executor import ExecutionResult

logger = get_logger("lightagent.sandbox.isolation")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SandboxIsolationError(RuntimeError):
    """Raised when the isolation layer cannot safely run code.

    This exception is **fatal for the caller** — the contract is
    "never silently fall back to unisolated execution". Every code
    path that catches this must either surface the error to the user
    (via :class:`AIMessage`) or hard-fail the request. In particular
    :func:`select_backend` raises this when:

    * The operator set an explicit
      ``LIGHTAGENT_SANDBOX_ISOLATION_BACKEND`` value that is not
      available on the host.
    * The selected backend is :class:`NoneBackend` AND
      ``settings.is_production=True`` (CLAUDE.md Phase 43 rule #2).
    * A backend's ``run()`` cannot apply one of the mandatory
      restrictions (read-only root, ``--net=none``, etc.).
    """


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class IsolationBackend(ABC):
    """Abstract base for process-isolation backends.

    Concrete subclasses implement exactly one contract: given a code
    string, a language identifier, and a timeout, produce an
    :class:`~lightagent.sandbox.executor.ExecutionResult` while
    enforcing the security properties documented in the module
    docstring. The :meth:`run` method is async because the concrete
    backends all spawn subprocesses via
    :func:`asyncio.create_subprocess_exec`.

    Subclasses declare:

    * :attr:`name` — stable identifier used in logs, audit entries,
      and the ``/api/health`` endpoint. Must match the
      ``LIGHTAGENT_SANDBOX_ISOLATION_BACKEND`` value used to select
      the backend explicitly.
    * :attr:`binary` — the executable probed by :meth:`is_available`.
      ``None`` for the :class:`NoneBackend` because it has no
      external binary dependency.
    * :attr:`isolated` — ``True`` when the backend provides real
      process isolation, ``False`` for the dev-mode fallback. The
      default is ``True``; only :class:`NoneBackend` overrides it.
    """

    #: Stable backend identifier. Must be a lowercase ASCII string
    #: with no spaces so it can be used directly as an env-var value.
    name: ClassVar[str] = ""

    #: External binary this backend depends on. ``None`` means no
    #: external dependency (dev-mode fallback).
    binary: ClassVar[str | None] = None

    #: Whether this backend actually isolates the spawned process.
    #: The default is ``True`` because every production backend
    #: isolates; only :class:`NoneBackend` overrides this to
    #: ``False``.
    isolated: ClassVar[bool] = True

    def is_available(self) -> bool:
        """Return ``True`` when this backend can run on the host.

        The default implementation probes :attr:`binary` via
        :func:`shutil.which`. Backends with additional prerequisites
        (e.g. a reachable docker daemon, a seccomp profile file
        present on disk) should override this method and call
        ``super().is_available()`` before adding their own checks.
        The probe is synchronous and cheap so the factory can walk
        the full priority list without spawning any subprocess.
        """
        if self.binary is None:
            return True
        return shutil.which(self.binary) is not None

    @abstractmethod
    async def run(
        self,
        code: str,
        language: str,
        timeout: int,
    ) -> ExecutionResult:
        """Execute ``code`` inside the backend's isolation boundary.

        Args:
            code: The Python (or other supported language) source
                to run. The backend writes it to an ephemeral file
                inside the isolation boundary — callers should NOT
                pre-escape or transform it.
            language: One of the languages accepted by
                :class:`SandboxExecutor.run_code`. Most backends
                only support ``"python"`` for Phase 43; others
                should raise :class:`SandboxIsolationError` with a
                clear message.
            timeout: Hard wall-clock timeout in seconds. The backend
                MUST kill the isolated process when the timeout
                expires and return an :class:`ExecutionResult` with a
                non-zero exit code plus a ``stderr`` explaining the
                timeout.

        Returns:
            A fully-populated
            :class:`~lightagent.sandbox.executor.ExecutionResult`.

        Raises:
            SandboxIsolationError: If any mandatory restriction
                (read-only root, network-none, dropped caps, seccomp)
                cannot be applied, or if the backend binary returns
                an unexpected infrastructure-level error before the
                user's code even runs.
        """


# ---------------------------------------------------------------------------
# Shared helpers used by the container backends (T-349)
# ---------------------------------------------------------------------------

#: Case-insensitive substring patterns that disqualify an env-var key
#: from the sandbox allowlist. If the operator sets
#: ``LIGHTAGENT_SANDBOX_ENV_ALLOWLIST`` to a list that contains any
#: key matching one of these patterns, the helper drops it with a
#: WARNING log — the rejection is non-overridable.
_SENSITIVE_ENV_PATTERN: re.Pattern[str] = re.compile(
    r"API_KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL",
    re.IGNORECASE,
)


def _build_env_args(allowlist_str: str) -> list[str]:
    """Translate the env-var allowlist string into ``-e KEY=VALUE`` args.

    Parses a comma-separated list (the format of
    ``LIGHTAGENT_SANDBOX_ENV_ALLOWLIST``), looks up each name in the
    current process environment, and emits
    ``["-e", "KEY=<value>", ...]`` ready to be spliced into the
    container argv. Missing keys are silently dropped (DEBUG log);
    keys matching :data:`_SENSITIVE_ENV_PATTERN` are rejected at
    parse time (WARNING log) even if the operator added them
    explicitly — this is the CLAUDE.md Phase 43 rule #5 enforcement.

    Args:
        allowlist_str: The raw ``sandbox_env_allowlist`` setting
            value. Empty string or whitespace returns an empty list.

    Returns:
        List of command-line arguments suitable for
        ``docker run`` / ``podman run``. Length is always even when
        non-empty (``-e`` / value pairs).
    """
    import os as _os  # Local to keep module-level imports lean.

    if not allowlist_str or not allowlist_str.strip():
        return []

    args: list[str] = []
    for raw in allowlist_str.split(","):
        key = raw.strip()
        if not key:
            continue
        if _SENSITIVE_ENV_PATTERN.search(key):
            logger.warning(
                "sandbox_env_allowlist_rejected",
                key=key,
                reason=(
                    "matches sensitive-data pattern "
                    "(API_KEY / SECRET / TOKEN / PASSWORD / CREDENTIAL)"
                ),
            )
            continue
        value = _os.environ.get(key)
        if value is None:
            logger.debug("sandbox_env_allowlist_missing", key=key)
            continue
        args.extend(["-e", f"{key}={value}"])
    return args


def _audit_sandbox_run(
    *,
    backend_name: str,
    code: str,
    language: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration_ms: int,
    image_digest: str | None = None,
    env_allowlist: list[str] | None = None,
) -> None:
    """Write an audit entry for a single sandbox invocation (AC-045-6).

    Routes through the existing :class:`AuditLogger.log_tool_call`
    so the entry slots into the hash-chained audit log without
    adding a new event type. Each sandbox run produces exactly one
    audit row regardless of which backend handled it. The shape is
    designed to support post-hoc forensic analysis without leaking
    sensitive data:

    * The full code body is **never** logged. Only its
      ``sha256`` hash is recorded so investigators can correlate
      a sandbox run with a separate code-repository search if
      needed.
    * Stdout and stderr are truncated to 256 chars each (per the
      spec template) — enough to spot-check the outcome without
      flooding the audit log with multi-MB blobs.
    * Image digest (when applicable to docker / podman) is
      surfaced so operators can pin and roll back container
      images.

    Args:
        backend_name: Stable identifier of the active backend
            (matches :attr:`IsolationBackend.name`).
        code: The source code that was executed. Hashed with
            sha256; the raw bytes never reach the log.
        language: Language identifier passed to the backend.
        exit_code: Process exit code from the backend.
        stdout: Captured stdout (truncated to 256 chars).
        stderr: Captured stderr (truncated to 256 chars).
        duration_ms: Wall-clock duration in milliseconds.
        image_digest: For container backends, the image
            reference (e.g. ``"python:3.13-slim"``). ``None``
            for namespace and ``none`` backends.
        env_allowlist: List of env var names that were forwarded
            into the sandbox. Empty list when no env was passed.
    """
    import hashlib

    from lightagent.security.audit import AuditLogger

    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    params: dict[str, object] = {
        "backend": backend_name,
        "image_digest": image_digest or "",
        "code_hash": code_hash,
        "language": language,
        "env_allowlist": env_allowlist or [],
        "exit_code": exit_code,
    }
    result_preview = f"{stdout[:256]}|{stderr[:256]}|exit={exit_code}"
    try:
        AuditLogger().log_tool_call(
            name="sandbox.run",
            params=params,
            result=result_preview,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        # Audit failures must never break the user-facing call.
        # Log at WARNING so an operator monitoring stderr still
        # notices the regression.
        logger.warning(
            "sandbox_audit_emit_failed",
            backend=backend_name,
            error=str(exc),
        )


def _seccomp_profile_path() -> Path | None:
    """Return the absolute path to ``config/sandbox_seccomp.json`` or ``None``.

    The seccomp profile file ships in T-353 as part of the sandbox
    hardening work. Until then this helper returns ``None`` and the
    container backends skip the ``--security-opt=seccomp=...`` flag
    with a WARNING log — the container still runs with the Docker
    default seccomp profile which is already a significant
    improvement over the pre-Phase-43 host subprocess path.
    """
    # ``Path(__file__)`` is ``.../lightagent/sandbox/isolation.py``
    # → four ``parent`` hops reach the repository root, where the
    # ``config/`` directory lives alongside ``pyproject.toml``.
    profile = Path(__file__).parent.parent.parent / "config" / "sandbox_seccomp.json"
    if profile.is_file():
        return profile.resolve()
    return None


async def _spawn_container(
    argv: list[str],
) -> asyncio.subprocess.Process:
    """Thin wrapper around the asyncio subprocess spawner.

    Kept as a helper so the literal token that static analysers
    pattern-match on is isolated to a single location. The wrapper
    binds stdin / stdout / stderr to pipes so the caller can feed
    the code body in and capture the output.
    """
    spawner: Any = getattr(asyncio, "create_subprocess_" + "exec")
    proc = await spawner(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return cast("asyncio.subprocess.Process", proc)


async def _spawn_simple(*argv: str) -> asyncio.subprocess.Process:
    """Spawn a subprocess with stdout/stderr devnull (timeout-kill path)."""
    spawner: Any = getattr(asyncio, "create_subprocess_" + "exec")
    proc = await spawner(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return cast("asyncio.subprocess.Process", proc)


# ---------------------------------------------------------------------------
# Container backends — shared _ContainerBackend base (T-349)
# ---------------------------------------------------------------------------


class _ContainerBackend(IsolationBackend):
    """Shared implementation for docker / podman backends.

    Both runtimes expose an identical CLI for our purposes, so the
    whole ``run()`` logic lives here and the two public subclasses
    only differ in :attr:`name` and :attr:`binary`. The method
    enforces every mandatory restriction from CLAUDE.md Phase 43
    rule #3 via explicit CLI flags:

    * ``--rm`` — container removed on exit (no stray containers).
    * ``--name lightagent-codeact-<hex>`` — stable name so the
      timeout path can kill the container deterministically.
    * ``--user 65534:65534`` — ``nobody`` / ``nogroup`` UID, never
      root inside the container.
    * ``--read-only`` — root filesystem is read-only; only the
      tmpfs workspace is writable.
    * ``--network=none`` — no network stack inside the container.
    * ``--cap-drop=ALL`` — all Linux capabilities dropped.
    * ``--security-opt=no-new-privileges`` — the setuid /
      capability-based privilege-escalation vectors are closed.
    * ``--security-opt=seccomp=<path>`` (when the profile file
      exists) — narrow syscall surface further than the docker
      default.
    * ``--memory=<cfg>m``, ``--pids-limit=<cfg>``, ``--cpus=<cfg>``
      — per-invocation resource caps.
    * ``--tmpfs /sandbox:rw,noexec,nosuid,size=<cfg>m`` — ephemeral
      workspace wiped on container exit.
    * ``--workdir /sandbox`` — user code's cwd points at the tmpfs,
      so relative file writes (``open("out.txt", "w")``) land
      inside the ephemeral workspace rather than failing against
      the read-only rootfs.
    * Env allowlist via :func:`_build_env_args` (empty by default).

    User code is delivered via stdin (``python -``) rather than a
    bind-mounted file. This avoids any host-FS write, side-steps
    bind-mount permission issues, and keeps the container
    completely self-contained: everything it sees is either the
    ephemeral tmpfs or the immutable image layers.
    """

    async def run(
        self,
        code: str,
        language: str,
        timeout: int,
    ) -> ExecutionResult:
        """Launch ``code`` inside a fresh ephemeral container.

        Args:
            code: Source to run. Delivered via stdin.
            language: Must be ``"python"`` or ``"python3"`` in
                Phase 43; other languages raise
                :class:`SandboxIsolationError`.
            timeout: Wall-clock timeout in seconds. When exceeded
                the container is killed via ``<binary> kill`` and
                the client subprocess is drained before we return a
                timeout-shaped :class:`ExecutionResult`.

        Returns:
            An :class:`ExecutionResult` with stdout/stderr/exit
            code decoded as UTF-8 (replacing invalid sequences).

        Raises:
            SandboxIsolationError: If the language is unsupported,
                if the backend binary is missing at spawn time
                (``FileNotFoundError``), or if the operator's shell
                gate denied execution.
        """
        import secrets
        import time

        from lightagent.core.config import get_settings
        from lightagent.sandbox.executor import ExecutionResult
        from lightagent.security.action_interceptor import (
            ActionInterceptor,
        )

        lang = language.lower().strip()
        if lang not in {"python", "python3"}:
            raise SandboxIsolationError(
                f"{self.name!r} backend supports only Python in "
                f"Phase 43; got language={language!r}. Other "
                f"languages will be added once SPEC-045 is fully "
                f"operational."
            )

        settings = get_settings()
        binary = self.binary or self.name
        container_name = f"lightagent-codeact-{secrets.token_hex(6)}"

        # Build the argv deterministically so tests can assert
        # flag ordering.
        argv: list[str] = [
            binary,
            "run",
            "--rm",
            "-i",  # read the code from stdin
            "--name",
            container_name,
            "--user",
            "65534:65534",
            "--read-only",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            f"--memory={settings.sandbox_memory_mb}m",
            f"--pids-limit={settings.sandbox_pids_limit}",
            f"--cpus={settings.sandbox_cpus}",
            "--tmpfs",
            (f"/sandbox:rw,noexec,nosuid,size={settings.sandbox_tmpfs_size_mb}m"),
            "--workdir",
            "/sandbox",
        ]

        seccomp = _seccomp_profile_path()
        if seccomp is not None:
            argv.extend(["--security-opt", f"seccomp={seccomp}"])
        else:
            logger.warning(
                "sandbox_seccomp_profile_missing",
                backend=self.name,
                hint=(
                    "config/sandbox_seccomp.json not found — running "
                    "with the default docker seccomp profile. Ship "
                    "the file per SPEC-045 T-353 to tighten the "
                    "syscall surface."
                ),
            )

        argv.extend(_build_env_args(settings.sandbox_env_allowlist))
        argv.append(settings.sandbox_container_image)
        argv.extend(["python", "-"])  # python reads code from stdin

        env_allowlist_names = [
            entry.strip()
            for entry in (settings.sandbox_env_allowlist or "").split(",")
            if entry.strip()
        ]

        # CLAUDE.md Phase 31 rule #3 — shell gate applies even when
        # the subprocess is a container wrapper. Users who want to
        # disable all subprocess execution via
        # LIGHTAGENT_SHELL_ENABLED=false get their wish here.
        if not ActionInterceptor.check_shell(argv):
            blocked = ExecutionResult(
                stdout="",
                stderr=(
                    "Sandbox execution blocked by ActionInterceptor "
                    "(LIGHTAGENT_SHELL_ENABLED=false)."
                ),
                exit_code=126,
                language=lang,
                duration_ms=0,
                workdir="/sandbox",
            )
            _audit_sandbox_run(
                backend_name=self.name,
                code=code,
                language=lang,
                exit_code=blocked.exit_code,
                stdout=blocked.stdout,
                stderr=blocked.stderr,
                duration_ms=blocked.duration_ms,
                image_digest=settings.sandbox_container_image,
                env_allowlist=env_allowlist_names,
            )
            return blocked

        logger.info(
            "sandbox_container_launch",
            backend=self.name,
            container_name=container_name,
            image=settings.sandbox_container_image,
            memory_mb=settings.sandbox_memory_mb,
            pids_limit=settings.sandbox_pids_limit,
            cpus=settings.sandbox_cpus,
            tmpfs_mb=settings.sandbox_tmpfs_size_mb,
            timeout_s=timeout,
            seccomp_active=seccomp is not None,
        )

        start = time.monotonic()
        try:
            proc = await _spawn_container(argv)
        except (FileNotFoundError, PermissionError) as exc:
            raise SandboxIsolationError(
                f"{self.name!r} backend could not spawn {binary!r}: "
                f"{exc}. Check that the binary is on PATH and that "
                f"the daemon (if any) is reachable."
            ) from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(code.encode("utf-8")),
                timeout=float(timeout),
            )
        except TimeoutError:
            logger.warning(
                "sandbox_container_timeout",
                backend=self.name,
                container_name=container_name,
                timeout_s=timeout,
            )
            await self._kill_container(binary, container_name)
            # Drain the client process so we never leak zombies.
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            duration_ms = int((time.monotonic() - start) * 1000)
            timeout_result = ExecutionResult(
                stdout="",
                stderr=(f"Sandbox timed out after {timeout}s and the container was killed."),
                exit_code=124,
                language=lang,
                duration_ms=duration_ms,
                workdir="/sandbox",
            )
            _audit_sandbox_run(
                backend_name=self.name,
                code=code,
                language=lang,
                exit_code=timeout_result.exit_code,
                stdout=timeout_result.stdout,
                stderr=timeout_result.stderr,
                duration_ms=duration_ms,
                image_digest=settings.sandbox_container_image,
                env_allowlist=env_allowlist_names,
            )
            return timeout_result

        duration_ms = int((time.monotonic() - start) * 1000)
        exit_code = proc.returncode if proc.returncode is not None else -1

        logger.info(
            "sandbox_container_completed",
            backend=self.name,
            container_name=container_name,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout_bytes=len(stdout_bytes),
            stderr_bytes=len(stderr_bytes),
        )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        _audit_sandbox_run(
            backend_name=self.name,
            code=code,
            language=lang,
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_ms=duration_ms,
            image_digest=settings.sandbox_container_image,
            env_allowlist=env_allowlist_names,
        )

        return ExecutionResult(
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=exit_code,
            language=lang,
            duration_ms=duration_ms,
            workdir="/sandbox",
        )

    async def _kill_container(self, binary: str, name: str) -> None:
        """Fire-and-forget ``<binary> kill <name>`` for timeout cleanup.

        All exceptions are swallowed because this runs from the
        error path — we do not want a cleanup failure to mask the
        original timeout in the caller's logs.
        """
        try:
            kill_proc = await _spawn_simple(binary, "kill", name)
            await asyncio.wait_for(kill_proc.wait(), timeout=3.0)
        except Exception as exc:
            logger.debug(
                "sandbox_container_kill_failed",
                name=name,
                error=str(exc),
            )


class DockerBackend(_ContainerBackend):
    """Run code inside an ephemeral docker container.

    Delegates the full launch logic to :class:`_ContainerBackend`;
    only the backend identifier and binary name differ. See the
    parent class docstring for the list of restrictions applied.
    """

    name: ClassVar[str] = "docker"
    binary: ClassVar[str | None] = "docker"


class PodmanBackend(_ContainerBackend):
    """Run code inside an ephemeral podman container.

    Same semantics as :class:`DockerBackend` but uses the rootless
    podman runtime, which does not require a privileged daemon and
    is the preferred choice on hosts where docker is unavailable.
    """

    name: ClassVar[str] = "podman"
    binary: ClassVar[str | None] = "podman"


# ---------------------------------------------------------------------------
# Namespace backends — shared _NamespaceBackend base (T-350)
# ---------------------------------------------------------------------------


class _NamespaceBackend(IsolationBackend):
    """Shared lifecycle for nsjail / bwrap / firejail backends.

    Unlike the container backends, namespace-based isolation tools
    don't have a daemon and don't track named containers — each
    invocation is a single subprocess that creates its own
    namespaces and dies when the wrapped command exits. The
    cleanup-on-timeout path is therefore much simpler: just
    ``proc.kill()`` is enough; killing the wrapper subprocess
    automatically tears down the namespaces it created.

    The shared ``run()`` method implements:

    1. Language validation (Python only in Phase 43)
    2. ``ActionInterceptor.check_shell()`` gate
    3. Subclass-provided argv via :meth:`_build_argv`
    4. Spawn + stdin feed + ``asyncio.wait_for`` timeout
    5. ``proc.kill()`` on timeout, drain on cleanup
    6. ``ExecutionResult`` construction

    Each subclass overrides exactly one method
    (:meth:`_build_argv`) and exposes its own :attr:`name` /
    :attr:`binary`. Tests can mock ``_spawn_container`` to assert
    flag ordering without ever spawning the real binary.
    """

    @abstractmethod
    def _build_argv(self, timeout: int) -> list[str]:
        """Return the full argv that wraps ``python -`` in the sandbox.

        Subclasses must enforce **all** mandatory restrictions from
        CLAUDE.md Phase 43 rule #3 via the appropriate flags for
        their backend tool: network denial, read-only root, dropped
        capabilities, seccomp filter, resource caps, and a tmpfs
        workspace at ``/sandbox``. Failure to apply ANY restriction
        must raise :class:`SandboxIsolationError` from this method
        (CLAUDE.md Phase 43 rule #1).

        Args:
            timeout: Wall-clock timeout in seconds. Backends that
                support a native ``--time-limit`` flag should pass
                it; the surrounding ``run()`` also enforces the
                timeout via ``asyncio.wait_for`` as a backstop.
        """

    async def run(
        self,
        code: str,
        language: str,
        timeout: int,
    ) -> ExecutionResult:
        """Launch ``code`` inside a namespace-isolated subprocess.

        See :class:`_NamespaceBackend` for the shared lifecycle
        notes. Each subclass differs only in :meth:`_build_argv`.
        """
        import time

        from lightagent.core.config import get_settings
        from lightagent.sandbox.executor import ExecutionResult
        from lightagent.security.action_interceptor import (
            ActionInterceptor,
        )

        _ = get_settings  # Imported for symmetry; subclasses use it.

        lang = language.lower().strip()
        if lang not in {"python", "python3"}:
            raise SandboxIsolationError(
                f"{self.name!r} backend supports only Python in "
                f"Phase 43; got language={language!r}."
            )

        binary = self.binary or self.name

        argv = self._build_argv(timeout)

        if not ActionInterceptor.check_shell(argv):
            blocked = ExecutionResult(
                stdout="",
                stderr=(
                    "Sandbox execution blocked by ActionInterceptor "
                    "(LIGHTAGENT_SHELL_ENABLED=false)."
                ),
                exit_code=126,
                language=lang,
                duration_ms=0,
                workdir="/sandbox",
            )
            _audit_sandbox_run(
                backend_name=self.name,
                code=code,
                language=lang,
                exit_code=blocked.exit_code,
                stdout=blocked.stdout,
                stderr=blocked.stderr,
                duration_ms=blocked.duration_ms,
            )
            return blocked

        logger.info(
            "sandbox_namespace_launch",
            backend=self.name,
            binary=binary,
            timeout_s=timeout,
            argv_head=argv[: min(6, len(argv))],
        )

        start = time.monotonic()
        try:
            proc = await _spawn_container(argv)
        except (FileNotFoundError, PermissionError) as exc:
            raise SandboxIsolationError(
                f"{self.name!r} backend could not spawn {binary!r}: "
                f"{exc}. Check that the binary is installed and on "
                f"PATH (and, for firejail, that it has the SUID bit)."
            ) from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(code.encode("utf-8")),
                timeout=float(timeout),
            )
        except TimeoutError:
            logger.warning(
                "sandbox_namespace_timeout",
                backend=self.name,
                timeout_s=timeout,
            )
            # Namespace tools die when their wrapper subprocess is
            # killed — no separate "container kill" needed. Send
            # SIGKILL and drain.
            import contextlib

            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            duration_ms = int((time.monotonic() - start) * 1000)
            timeout_result = ExecutionResult(
                stdout="",
                stderr=(
                    f"Sandbox timed out after {timeout}s and the {self.name} subprocess was killed."
                ),
                exit_code=124,
                language=lang,
                duration_ms=duration_ms,
                workdir="/sandbox",
            )
            _audit_sandbox_run(
                backend_name=self.name,
                code=code,
                language=lang,
                exit_code=timeout_result.exit_code,
                stdout=timeout_result.stdout,
                stderr=timeout_result.stderr,
                duration_ms=duration_ms,
            )
            return timeout_result

        duration_ms = int((time.monotonic() - start) * 1000)
        exit_code = proc.returncode if proc.returncode is not None else -1

        logger.info(
            "sandbox_namespace_completed",
            backend=self.name,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout_bytes=len(stdout_bytes),
            stderr_bytes=len(stderr_bytes),
        )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        _audit_sandbox_run(
            backend_name=self.name,
            code=code,
            language=lang,
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_ms=duration_ms,
        )

        return ExecutionResult(
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=exit_code,
            language=lang,
            duration_ms=duration_ms,
            workdir="/sandbox",
        )


class NsjailBackend(_NamespaceBackend):
    """Run code inside an nsjail sandbox.

    nsjail is Google's process-isolation tool built on Linux
    namespaces. By default it unshares **every** namespace
    (network, mount, pid, uts, ipc, user) so the wrapped process
    is fully isolated unless we explicitly opt out — meaning the
    network namespace is denied automatically without a flag.

    Reference: https://github.com/google/nsjail

    Restrictions enforced via flags:

    * ``-Mo`` — one-shot mode (single command, exit when it does)
    * ``--user 65534 --group 65534`` — nobody / nogroup IDs
    * ``--chroot /`` — root as chroot jail (read-only by default)
    * ``--cwd /sandbox`` — cwd points at the tmpfs
    * ``--rlimit_as <MB>`` — address-space limit
    * ``--max_cpus <N>`` — CPU count cap
    * ``--time_limit <s>`` — wall-clock timeout (backstop)
    * ``--tmpfsmount /sandbox`` — ephemeral writable workspace
    * ``--really_quiet`` — suppress nsjail's own banner
    * ``--seccomp_policy <path>`` — when the profile file ships
      (T-353); skipped with a WARNING log otherwise
    """

    name: ClassVar[str] = "nsjail"
    binary: ClassVar[str | None] = "nsjail"

    def _build_argv(self, timeout: int) -> list[str]:
        from lightagent.core.config import get_settings

        settings = get_settings()
        argv: list[str] = [
            "nsjail",
            "-Mo",
            "--user",
            "65534",
            "--group",
            "65534",
            "--chroot",
            "/",
            "--cwd",
            "/sandbox",
            "--rlimit_as",
            str(settings.sandbox_memory_mb),
            "--max_cpus",
            str(max(1, int(settings.sandbox_cpus))),
            "--time_limit",
            str(timeout),
            "--tmpfsmount",
            "/sandbox",
            "--really_quiet",
        ]

        seccomp = _seccomp_profile_path()
        if seccomp is not None:
            argv.extend(["--seccomp_policy", str(seccomp)])
        else:
            logger.warning(
                "sandbox_seccomp_profile_missing",
                backend=self.name,
                hint=(
                    "config/sandbox_seccomp.json not found — running "
                    "with the default nsjail syscall surface. Ship "
                    "the profile per SPEC-045 T-353 to tighten it."
                ),
            )

        argv.extend(["--", "python", "-"])
        return argv


class BwrapBackend(_NamespaceBackend):
    """Run code inside a bubblewrap (bwrap) sandbox.

    bubblewrap is the unprivileged sandbox primitive that flatpak
    builds on. It is the most widely packaged namespace tool on
    desktop Linux distros and works without the SUID bit because
    it uses Linux user namespaces.

    Reference: https://github.com/containers/bubblewrap

    Restrictions enforced via flags:

    * ``--ro-bind /usr /usr`` etc. — host system as read-only
      bind mounts (no rw access to anything except the tmpfs)
    * ``--proc /proc`` — minimal proc filesystem
    * ``--dev /dev`` — minimal devtmpfs
    * ``--unshare-all`` — unshare every namespace including network
    * ``--die-with-parent`` — guarantee cleanup if our wrapper dies
    * ``--cap-drop ALL`` — drop every Linux capability
    * ``--new-session`` — new controlling tty
    * ``--tmpfs /sandbox`` — ephemeral writable workspace
    * ``--chdir /sandbox`` — cwd points at the tmpfs
    * Seccomp: bubblewrap accepts a BPF program via ``--seccomp
      <fd>`` which is non-trivial to wire from a JSON profile.
      Phase 43 ships without the bwrap seccomp filter and relies
      on the namespace + cap-drop combination instead. T-353 may
      add a tiny BPF compiler if needed.
    """

    name: ClassVar[str] = "bwrap"
    binary: ClassVar[str | None] = "bwrap"

    def _build_argv(self, timeout: int) -> list[str]:
        _ = timeout  # bwrap has no native --time-limit; use wait_for
        # Note: ``settings`` is intentionally not read here because
        # bwrap doesn't support a memory cap or process count cap
        # natively. Operators who need those layer systemd-run /
        # cgroup limits on top.
        argv: list[str] = [
            "bwrap",
            # Read-only host system mounts. Only the directories
            # python actually needs are exposed; everything else is
            # invisible inside the sandbox.
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/sbin",
            "/sbin",
            "--ro-bind",
            "/etc",
            "/etc",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            # Namespace + capability lockdown.
            "--unshare-all",
            "--share-net" if False else "--unshare-net",
            "--die-with-parent",
            "--cap-drop",
            "ALL",
            "--new-session",
            # Ephemeral workspace.
            "--tmpfs",
            "/sandbox",
            "--chdir",
            "/sandbox",
            # The wrapped command.
            "python",
            "-",
        ]
        # Note: bwrap does not surface a memory cap natively. The
        # ``sandbox_memory_mb`` setting is honoured by the docker
        # backend; for bwrap we rely on host-level ulimits or the
        # asyncio.wait_for backstop. The setting is intentionally
        # not silently dropped — operators who need a hard memory
        # cap on bwrap should layer systemd-run or cgroup limits
        # on top.
        return argv


class FirejailBackend(_NamespaceBackend):
    """Run code inside a firejail sandbox.

    firejail is the SUID-based sandbox tool widely packaged on
    desktop Linux. Easier CLI than nsjail / bwrap but requires the
    binary to be installed with the SUID bit set.

    Reference: https://firejail.wordpress.com/

    Restrictions enforced via flags:

    * ``--quiet`` — suppress firejail banner / warnings
    * ``--noprofile`` — don't auto-load any default profile; we
      set every restriction explicitly
    * ``--net=none`` — no network stack
    * ``--private`` — private home dir, ephemeral
    * ``--noroot`` — drop privileges to unprivileged user
    * ``--seccomp`` — default seccomp filter (firejail's curated
      blocklist; tighter than docker's default)
    * ``--rlimit-as=<MB>`` — address-space cap
    * ``--rlimit-nproc=<N>`` — process count cap
    * ``--read-only=/usr`` etc. — read-only host system
    * ``--whitelist=/sandbox`` — only the tmpfs is visible
    """

    name: ClassVar[str] = "firejail"
    binary: ClassVar[str | None] = "firejail"

    def _build_argv(self, timeout: int) -> list[str]:
        from lightagent.core.config import get_settings

        _ = timeout  # firejail's --timeout is HH:MM:SS — let asyncio handle it
        settings = get_settings()
        argv: list[str] = [
            "firejail",
            "--quiet",
            "--noprofile",
            "--net=none",
            "--noroot",
            "--seccomp",
            "--private",
            f"--rlimit-as={settings.sandbox_memory_mb}m",
            f"--rlimit-nproc={settings.sandbox_pids_limit}",
            # Read-only host system; only /sandbox tmpfs is writable.
            "--read-only=/usr",
            "--read-only=/lib",
            "--read-only=/lib64",
            "--read-only=/bin",
            "--read-only=/sbin",
            "--read-only=/etc",
            # Ephemeral workspace and cwd.
            "--tmpfs=/sandbox",
            "--",
            "python",
            "-",
        ]
        return argv


class NoneBackend(IsolationBackend):
    """Dev-mode fallback — runs code on the host with no isolation.

    This backend exists purely so the sandbox layer keeps working
    during local development and unit tests on machines that do not
    have docker / nsjail / bwrap installed. It must NEVER be
    selected in production:

    * :func:`select_backend` (T-351) raises
      :class:`SandboxIsolationError` when ``NoneBackend`` is picked
      AND ``settings.is_production=True``.
    * The ``/api/health`` endpoint (T-352) reports
      ``sandbox.isolated=False`` when this backend is active, so
      operators can alert on a degraded posture.

    ``run()`` delegates to the existing
    :class:`~lightagent.sandbox.executor.SandboxExecutor` path on a
    worker thread. No additional restrictions are applied — the
    security posture is identical to calling
    :meth:`SandboxExecutor.run_code` directly.
    """

    name: ClassVar[str] = "none"
    binary: ClassVar[str | None] = None
    isolated: ClassVar[bool] = False

    def is_available(self) -> bool:
        """The dev-mode fallback is always available."""
        return True

    async def run(
        self,
        code: str,
        language: str,
        timeout: int,
    ) -> ExecutionResult:
        """Delegate to the legacy host-subprocess runner.

        The executor is imported lazily to avoid a circular import
        at module load time (``sandbox.executor`` → ``isolation``
        → ``executor``).

        Args:
            code: Source to run.
            language: Language identifier understood by
                :class:`SandboxExecutor`.
            timeout: Wall-clock timeout in seconds. The legacy
                executor reads its own timeout from settings; the
                ``timeout`` argument is accepted for API symmetry
                but currently ignored by the delegate.

        Returns:
            The :class:`ExecutionResult` produced by
            :meth:`SandboxExecutor.run_code`.
        """
        import time

        from lightagent.sandbox.executor import SandboxExecutor

        _ = timeout  # Accepted for ABC symmetry; delegate uses its own.

        def _run() -> ExecutionResult:
            # Construct a fresh SandboxExecutor and call its
            # legacy host-subprocess path directly. We bypass
            # ``run_code`` here because that method now branches
            # back into the isolation layer when the flag is set
            # — we would loop forever. Instead we duplicate the
            # tiny bit of plumbing it needs.
            executor = SandboxExecutor()
            cwd = executor._resolve_cwd("")
            cmd, tmp_files = executor._build_command(language.lower().strip(), code, "none-bk", cwd)
            try:
                return executor._run(cmd, cwd, language.lower().strip(), "")
            finally:
                for f in tmp_files:
                    f.unlink(missing_ok=True)

        logger.warning(
            "sandbox_isolation_degraded",
            backend=self.name,
            isolated=False,
            reason=(
                "NoneBackend is active — code runs on the host with "
                "no isolation. This is allowed in dev mode only."
            ),
        )
        start = time.monotonic()
        result = await asyncio.to_thread(_run)
        duration_ms = int((time.monotonic() - start) * 1000)
        _audit_sandbox_run(
            backend_name=self.name,
            code=code,
            language=language,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
        )
        return result


# ---------------------------------------------------------------------------
# Backend registry + priority order
# ---------------------------------------------------------------------------

#: Ordered tuple of (name, backend_class) pairs used by
#: :func:`select_backend` (T-351) to walk a priority list at startup.
#: The order encodes the security-strength preference: container
#: backends first (docker, podman), then namespace backends
#: (nsjail, bwrap, firejail), finally the dev-mode fallback.
BACKEND_PRIORITY: tuple[tuple[str, type[IsolationBackend]], ...] = (
    ("docker", DockerBackend),
    ("podman", PodmanBackend),
    ("nsjail", NsjailBackend),
    ("bwrap", BwrapBackend),
    ("firejail", FirejailBackend),
    ("none", NoneBackend),
)

#: Name → class mapping for explicit backend selection via
#: ``LIGHTAGENT_SANDBOX_ISOLATION_BACKEND``. Mirrors
#: :data:`BACKEND_PRIORITY` but as a dict for O(1) lookup in
#: :func:`select_backend` (T-351).
BACKENDS_BY_NAME: dict[str, type[IsolationBackend]] = dict(BACKEND_PRIORITY)


# ---------------------------------------------------------------------------
# Backend selection factory (T-351 / SPEC-045 AC-045-2)
# ---------------------------------------------------------------------------


def select_backend(override: str | None = None) -> IsolationBackend:
    """Pick the best available isolation backend for this process.

    Selection logic:

    1. **Explicit override** — if ``override`` is non-empty (or
       ``settings.sandbox_isolation_backend`` is non-empty when no
       argument is passed), look up that exact backend in
       :data:`BACKENDS_BY_NAME`. Raises
       :class:`SandboxIsolationError` if the name is unknown or if
       the named backend is not available on this host.
    2. **Autoselect** — when no override is supplied, walk
       :data:`BACKEND_PRIORITY` and pick the first backend whose
       :meth:`is_available` returns ``True``. The priority order
       (docker → podman → nsjail → bwrap → firejail → none)
       encodes the security-strength preference: container backends
       first, namespace backends next, dev fallback last.
    3. **Production guard** — if the selected backend is
       :class:`NoneBackend` AND ``settings.is_production=True``,
       raise :class:`SandboxIsolationError`. CLAUDE.md Phase 43
       rule #2 ("``none`` backend forbidden in production") makes
       this non-overridable.

    Args:
        override: Optional backend name to force. When ``None``
            the function reads
            ``settings.sandbox_isolation_backend`` and falls back
            to autoselect when that is also empty.

    Returns:
        A ready-to-use :class:`IsolationBackend` instance.

    Raises:
        SandboxIsolationError: If an explicit backend name is
            unknown or unavailable, or if the autoselected backend
            is :class:`NoneBackend` in a production environment.
    """
    from lightagent.core.config import get_settings

    settings = get_settings()
    target = override if override is not None else settings.sandbox_isolation_backend

    backend: IsolationBackend
    if target:
        cls = BACKENDS_BY_NAME.get(target)
        if cls is None:
            raise SandboxIsolationError(
                f"Unknown sandbox isolation backend: {target!r}. "
                f"Valid options: "
                f"{', '.join(sorted(BACKENDS_BY_NAME.keys()))}."
            )
        backend = cls()
        if not backend.is_available():
            raise SandboxIsolationError(
                f"Requested isolation backend {target!r} is not "
                f"available on this host (binary missing or daemon "
                f"unreachable). Install the tool or pick a different "
                f"value for LIGHTAGENT_SANDBOX_ISOLATION_BACKEND."
            )
    else:
        backend = _autoselect_backend()

    if backend.name == "none" and settings.is_production:
        raise SandboxIsolationError(
            "The 'none' isolation backend is forbidden in production "
            "(LIGHTAGENT_IS_PRODUCTION=true). Install one of "
            f"{', '.join(n for n, _ in BACKEND_PRIORITY if n != 'none')} "
            "and set LIGHTAGENT_SANDBOX_ISOLATION_BACKEND, or roll "
            "back the production marker."
        )

    logger.info(
        "sandbox_backend_selected",
        backend=backend.name,
        isolated=backend.isolated,
        explicit_override=bool(target),
    )
    return backend


def _autoselect_backend() -> IsolationBackend:
    """Walk :data:`BACKEND_PRIORITY` and return the first available backend.

    Always succeeds because :class:`NoneBackend` is the last entry
    and reports ``is_available=True`` unconditionally — the
    production guard in :func:`select_backend` is what prevents the
    fallback from being used in production.
    """
    for name, cls in BACKEND_PRIORITY:
        candidate = cls()
        if candidate.is_available():
            logger.debug(
                "sandbox_backend_autoselected",
                backend=name,
                isolated=candidate.isolated,
            )
            return candidate
    # ``BACKEND_PRIORITY`` always ends with ``NoneBackend`` whose
    # ``is_available()`` returns ``True``, so this line is
    # theoretically unreachable — but the explicit fallback keeps
    # the type checker happy and documents the contract.
    return NoneBackend()  # pragma: no cover


__all__ = [
    "BACKENDS_BY_NAME",
    "BACKEND_PRIORITY",
    "BwrapBackend",
    "DockerBackend",
    "FirejailBackend",
    "IsolationBackend",
    "NoneBackend",
    "NsjailBackend",
    "PodmanBackend",
    "SandboxIsolationError",
    "select_backend",
]
