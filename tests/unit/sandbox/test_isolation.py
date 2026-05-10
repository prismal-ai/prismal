"""Unit tests for the sandbox isolation layer.

Covers SPEC-045 AC-045-8 and AC-045-9:

* ``select_backend()`` priority order and production guard.
* Env-allowlist parser rejecting ``*_API_KEY`` etc.
* Each backend's ``run()`` with :func:`asyncio.create_subprocess_exec` mocked.
* Audit entry shape.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from lightagent.sandbox import isolation
from lightagent.sandbox.isolation import (
    BACKEND_PRIORITY,
    BACKENDS_BY_NAME,
    BwrapBackend,
    DockerBackend,
    FirejailBackend,
    NoneBackend,
    NsjailBackend,
    PodmanBackend,
    SandboxIsolationError,
    _build_env_args,
    _seccomp_profile_path,
    select_backend,
)

# ── helpers ──────────────────────────────────────────────────────────────────


class _FakeProc:
    """Minimal stub for ``asyncio.subprocess.Process``."""

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self, _input: bytes | None = None):
        return self._stdout, self._stderr

    async def wait(self):
        return self.returncode

    def kill(self):  # pragma: no cover - only hit on timeout path
        pass


# ── backend registry + priority ──────────────────────────────────────────────


def test_backend_priority_order() -> None:
    """Priority order is docker → podman → nsjail → bwrap → firejail → none."""
    names = [name for name, _ in BACKEND_PRIORITY]
    assert names == ["docker", "podman", "nsjail", "bwrap", "firejail", "none"]
    # Every entry is registered in BACKENDS_BY_NAME.
    assert set(BACKENDS_BY_NAME.keys()) == set(names)


def test_none_backend_is_not_isolated() -> None:
    """NoneBackend is the only backend reporting isolated=False."""
    assert NoneBackend.isolated is False
    assert NoneBackend().is_available() is True
    for cls in (
        DockerBackend,
        PodmanBackend,
        NsjailBackend,
        BwrapBackend,
        FirejailBackend,
    ):
        assert cls.isolated is True


# ── select_backend: explicit override ────────────────────────────────────────


def test_select_backend_unknown_name_raises() -> None:
    """An unknown explicit backend name raises SandboxIsolationError."""
    with pytest.raises(SandboxIsolationError, match="Unknown"):
        select_backend(override="totally-made-up")


def test_select_backend_explicit_not_available_raises() -> None:
    """Explicit backend that isn't available on the host must raise."""
    with (
        patch.object(DockerBackend, "is_available", return_value=False),
        pytest.raises(SandboxIsolationError, match="not"),
    ):
        select_backend(override="docker")


def test_select_backend_explicit_available_returns_it() -> None:
    """When the explicit backend IS available, it is returned."""
    with patch.object(DockerBackend, "is_available", return_value=True):
        backend = select_backend(override="docker")
    assert backend.name == "docker"


# ── select_backend: autoselect + priority walk ───────────────────────────────


def test_select_backend_autoselect_picks_first_available() -> None:
    """Autoselect walks BACKEND_PRIORITY and returns the first available one."""

    # Force everything to be unavailable except the nsjail backend so we can
    # verify the walk actually honours the ordering.
    def _only_nsjail(self) -> bool:
        return isinstance(self, NsjailBackend | NoneBackend)

    with (
        patch.object(isolation.IsolationBackend, "is_available", _only_nsjail),
        patch("lightagent.core.config.get_settings") as mock_settings,
    ):
        mock_settings.return_value.sandbox_isolation_backend = ""
        mock_settings.return_value.is_production = False
        backend = select_backend()
    assert backend.name == "nsjail"


def test_select_backend_none_blocked_in_production() -> None:
    """NoneBackend is rejected when is_production=True (CLAUDE.md Phase 43 #2)."""

    def _only_none(self) -> bool:
        return isinstance(self, NoneBackend)

    with (
        patch.object(isolation.IsolationBackend, "is_available", _only_none),
        patch("lightagent.core.config.get_settings") as mock_settings,
    ):
        mock_settings.return_value.sandbox_isolation_backend = ""
        mock_settings.return_value.is_production = True
        with pytest.raises(SandboxIsolationError, match="forbidden in production"):
            select_backend()


def test_select_backend_none_allowed_in_dev() -> None:
    """NoneBackend is accepted when is_production=False."""

    def _only_none(self) -> bool:
        return isinstance(self, NoneBackend)

    with (
        patch.object(isolation.IsolationBackend, "is_available", _only_none),
        patch("lightagent.core.config.get_settings") as mock_settings,
    ):
        mock_settings.return_value.sandbox_isolation_backend = ""
        mock_settings.return_value.is_production = False
        backend = select_backend()
    assert backend.name == "none"


# ── env-allowlist parser ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "DATABASE_PASSWORD",
        "MY_APP_SECRET",
        "github_credential",  # case-insensitive
    ],
)
def test_build_env_args_rejects_sensitive_keys(monkeypatch, key) -> None:
    """Sensitive env-var names are dropped at parse time (rule #5)."""
    monkeypatch.setenv(key, "value-must-not-leak")
    args = _build_env_args(key)
    assert args == []


def test_build_env_args_forwards_allowed_keys(monkeypatch) -> None:
    """Non-sensitive keys that exist in the process env are emitted as -e pairs."""
    monkeypatch.setenv("LIGHTAGENT_TEST_FOO", "bar")
    monkeypatch.setenv("LIGHTAGENT_TEST_BAZ", "qux")
    args = _build_env_args("LIGHTAGENT_TEST_FOO,LIGHTAGENT_TEST_BAZ")
    assert args == [
        "-e",
        "LIGHTAGENT_TEST_FOO=bar",
        "-e",
        "LIGHTAGENT_TEST_BAZ=qux",
    ]


def test_build_env_args_empty_string_returns_empty() -> None:
    """Empty / whitespace-only allowlist yields an empty arg list."""
    assert _build_env_args("") == []
    assert _build_env_args("   ") == []


def test_build_env_args_drops_missing_keys(monkeypatch) -> None:
    """Allowlist entries that aren't present in os.environ are silently dropped."""
    monkeypatch.delenv("LIGHTAGENT_NOT_SET", raising=False)
    args = _build_env_args("LIGHTAGENT_NOT_SET")
    assert args == []


# ── seccomp profile helper ───────────────────────────────────────────────────


def test_seccomp_profile_path_resolves_when_present() -> None:
    """The helper returns an absolute path when config/sandbox_seccomp.json exists."""
    # T-353 (SPEC-045) is intended to ship the profile but the file is not yet
    # committed. Skip until then; the helper's None branch is covered by the
    # production warning logs in DockerBackend / PodmanBackend.
    expected = Path(__file__).resolve().parents[3] / "config" / "sandbox_seccomp.json"
    if not expected.is_file():
        pytest.skip("config/sandbox_seccomp.json not shipped yet (SPEC-045 T-353)")
    p = _seccomp_profile_path()
    assert p is not None
    assert p.is_absolute()
    assert p.name == "sandbox_seccomp.json"


# ── backend.run() with mocked subprocess (docker/podman) ─────────────────────


async def test_docker_backend_rejects_non_python_language() -> None:
    """Container backends only support Python in Phase 43."""
    backend = DockerBackend()
    with pytest.raises(SandboxIsolationError, match="only Python"):
        await backend.run("puts 'hi'", "ruby", timeout=5)


async def test_docker_backend_run_mocked_success() -> None:
    """A mocked docker spawn returns an ExecutionResult with the captured stdout."""
    backend = DockerBackend()
    with patch.object(isolation, "_spawn_container", return_value=_FakeProc(b"hello\n")):
        result = await backend.run("print('hello')", "python", timeout=5)
    assert result.exit_code == 0
    assert "hello" in result.stdout


async def test_docker_backend_shell_gate_blocks() -> None:
    """When ActionInterceptor.check_shell() returns False, run is blocked."""
    backend = DockerBackend()
    with patch(
        "lightagent.security.action_interceptor.ActionInterceptor.check_shell",
        return_value=False,
    ):
        result = await backend.run("print('hi')", "python", timeout=5)
    assert result.exit_code == 126
    assert "blocked" in result.stderr.lower()


# ── backend.run() with mocked subprocess (namespace backends) ────────────────


@pytest.mark.parametrize(
    "backend_cls",
    [NsjailBackend, BwrapBackend, FirejailBackend],
)
async def test_namespace_backend_run_mocked_success(backend_cls) -> None:
    """Namespace backends decode stdout/stderr and surface the exit code."""
    backend = backend_cls()
    with patch.object(
        isolation,
        "_spawn_container",
        return_value=_FakeProc(b"ns-ok\n", b"", returncode=0),
    ):
        result = await backend.run("print('ns-ok')", "python", timeout=5)
    assert result.exit_code == 0
    assert "ns-ok" in result.stdout
    assert result.language == "python"


@pytest.mark.parametrize(
    "backend_cls",
    [NsjailBackend, BwrapBackend, FirejailBackend],
)
async def test_namespace_backend_rejects_non_python(backend_cls) -> None:
    backend = backend_cls()
    with pytest.raises(SandboxIsolationError, match="only Python"):
        await backend.run("console.log(1)", "javascript", timeout=5)


# ── audit entry shape ────────────────────────────────────────────────────────


async def test_audit_entry_shape_from_container_backend() -> None:
    """The audit row contains backend, code_hash, language, exit_code fields only."""
    calls: list[dict] = []

    def _fake_log(self, *, name, params, result, duration_ms):
        calls.append(
            {
                "name": name,
                "params": params,
                "result": result,
                "duration_ms": duration_ms,
            }
        )

    with (
        patch("lightagent.security.audit.AuditLogger.log_tool_call", new=_fake_log),
        patch.object(
            isolation,
            "_spawn_container",
            return_value=_FakeProc(b"shape", b"", 0),
        ),
    ):
        await DockerBackend().run("print('shape')", "python", timeout=5)

    assert len(calls) == 1
    row = calls[0]
    assert row["name"] == "sandbox.run"
    params = row["params"]
    # Mandatory keys per SPEC-045 AC-045-6.
    for key in (
        "backend",
        "image_digest",
        "code_hash",
        "language",
        "env_allowlist",
        "exit_code",
    ):
        assert key in params, f"missing audit key: {key}"
    assert params["code_hash"] == hashlib.sha256(b"print('shape')").hexdigest()
    assert isinstance(row["duration_ms"], int)
    assert row["duration_ms"] >= 0
