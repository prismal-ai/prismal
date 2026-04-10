"""Audit + degraded-posture coverage for the sandbox isolation layer.

T-352 / SPEC-045 AC-045-6 + AC-045-7. Verifies that:

* Every backend's ``run()`` emits exactly one ``AuditLogger.log_tool_call``
  entry with ``name="sandbox.run"``, the right ``backend`` value, and an
  sha256 ``code_hash`` (never the raw code body).
* :class:`NoneBackend` logs ``sandbox_isolation_degraded`` at WARNING level
  before delegating to the host runner.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest


def _make_fake_proc(stdout: bytes = b"hi\n", stderr: bytes = b"", rc: int = 0):
    """Build a stub object compatible with the asyncio subprocess interface."""

    class _FakeProc:
        returncode = rc

        async def communicate(self, _input: bytes | None = None):
            return stdout, stderr

        async def wait(self):
            return rc

        def kill(self):  # pragma: no cover - timeout path
            pass

    return _FakeProc()


@pytest.fixture
def captured_audit():
    """Capture every ``AuditLogger.log_tool_call`` call site-wide."""
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

    with patch(
        "lightagent.security.audit.AuditLogger.log_tool_call",
        new=_fake_log,
    ):
        yield calls


# ── container backends ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("backend_cls", "backend_name"),
    [
        ("DockerBackend", "docker"),
        ("PodmanBackend", "podman"),
    ],
)
async def test_container_backend_emits_audit(
    captured_audit, backend_cls, backend_name
) -> None:
    """Container backends call AuditLogger.log_tool_call with sandbox.run."""
    from lightagent.sandbox import isolation

    cls = getattr(isolation, backend_cls)
    backend = cls()

    code = "print('hello')"
    fake_proc = _make_fake_proc(stdout=b"hello\n")

    with patch.object(isolation, "_spawn_container", return_value=fake_proc):
        result = await backend.run(code, "python", timeout=5)

    assert result.exit_code == 0
    assert len(captured_audit) == 1
    entry = captured_audit[0]
    assert entry["name"] == "sandbox.run"
    assert entry["params"]["backend"] == backend_name
    assert entry["params"]["code_hash"] == hashlib.sha256(code.encode()).hexdigest()
    # Raw code never appears in the audit log.
    assert code not in str(entry["params"])


# ── namespace backends ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("backend_cls", "backend_name"),
    [
        ("NsjailBackend", "nsjail"),
        ("BwrapBackend", "bwrap"),
        ("FirejailBackend", "firejail"),
    ],
)
async def test_namespace_backend_emits_audit(
    captured_audit, backend_cls, backend_name
) -> None:
    """Namespace backends emit a single sandbox.run audit row."""
    from lightagent.sandbox import isolation

    cls = getattr(isolation, backend_cls)
    backend = cls()

    code = "print('ns')"
    fake_proc = _make_fake_proc(stdout=b"ns\n")

    with patch.object(isolation, "_spawn_container", return_value=fake_proc):
        result = await backend.run(code, "python", timeout=5)

    assert result.exit_code == 0
    assert len(captured_audit) == 1
    entry = captured_audit[0]
    assert entry["name"] == "sandbox.run"
    assert entry["params"]["backend"] == backend_name
    assert entry["params"]["code_hash"] == hashlib.sha256(code.encode()).hexdigest()


# ── degraded posture (NoneBackend) ───────────────────────────────────────────


async def test_none_backend_logs_degraded_warning(captured_audit) -> None:
    """NoneBackend.run() logs sandbox_isolation_degraded and audits the call."""
    from lightagent.sandbox import isolation as isolation_mod
    from lightagent.sandbox.executor import ExecutionResult
    from lightagent.sandbox.isolation import NoneBackend

    backend = NoneBackend()
    fake_result = ExecutionResult(
        stdout="ok",
        stderr="",
        exit_code=0,
        language="python",
        duration_ms=1,
        workdir="/tmp"  # noqa: S108 - test-only stub path,
    )

    warnings: list[tuple[str, dict]] = []

    def _capture_warn(event, **kw):
        warnings.append((event, kw))

    with patch(
        "lightagent.sandbox.executor.SandboxExecutor._run",
        return_value=fake_result,
    ), patch(
        "lightagent.sandbox.executor.SandboxExecutor._build_command",
        return_value=(["python", "-c", "print(1)"], []),
    ), patch(
        "lightagent.sandbox.executor.SandboxExecutor._resolve_cwd",
        return_value="/tmp"  # noqa: S108 - test-only stub path,
    ), patch.object(isolation_mod.logger, "warning", side_effect=_capture_warn):
        result = await backend.run("print(1)", "python", timeout=5)

    assert result.exit_code == 0
    assert any(event == "sandbox_isolation_degraded" for event, _ in warnings)
    assert len(captured_audit) == 1
    assert captured_audit[0]["params"]["backend"] == "none"
