"""Integration tests for the docker-backed sandbox isolation layer.

Gated on ``docker --version`` + a reachable daemon — skipped otherwise. Each
test verifies one hardening property end-to-end by running real Python code
inside a real ephemeral container:

* ``open("/etc/passwd")`` must fail → read-only root enforcement.
* ``socket.connect()`` must fail → ``--network=none`` enforcement.
* ``os.environ`` must be empty → env allowlist is empty by default.
* The container disappears after exit → ``docker ps -a`` does not list it.

SPEC-045 AC-045-8 + AC-045-9.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    """Return True when ``docker`` is installed AND the daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="docker binary or daemon not available on this host",
)


# ── helpers ──────────────────────────────────────────────────────────────────


async def _run_via_docker(code: str, timeout: int = 30):
    from lightagent.sandbox.isolation import DockerBackend

    return await DockerBackend().run(code, "python", timeout=timeout)


# ── tests ────────────────────────────────────────────────────────────────────


@requires_docker
async def test_docker_blocks_filesystem_write_to_host() -> None:
    """``open("/etc/passwd", "w")`` fails because root is read-only."""
    code = (
        "try:\n"
        "    open('/etc/hostname', 'w').write('pwned')\n"
        "    print('UNEXPECTED_WRITE_SUCCEEDED')\n"
        "except Exception as e:\n"
        "    print('WRITE_BLOCKED:' + type(e).__name__)\n"
    )
    result = await _run_via_docker(code)
    assert result.exit_code == 0
    assert "WRITE_BLOCKED" in result.stdout
    assert "UNEXPECTED" not in result.stdout


@requires_docker
async def test_docker_blocks_network_egress() -> None:
    """``socket.connect()`` fails because ``--network=none`` is set."""
    code = (
        "import socket\n"
        "try:\n"
        "    s = socket.socket()\n"
        "    s.settimeout(3)\n"
        "    s.connect(('8.8.8.8', 53))\n"
        "    print('UNEXPECTED_NETWORK_OK')\n"
        "except Exception as e:\n"
        "    print('NETWORK_BLOCKED:' + type(e).__name__)\n"
    )
    result = await _run_via_docker(code)
    assert result.exit_code == 0
    assert "NETWORK_BLOCKED" in result.stdout
    assert "UNEXPECTED" not in result.stdout


@requires_docker
async def test_docker_env_allowlist_is_empty_by_default(monkeypatch) -> None:
    """``os.environ`` inside the sandbox has no host secrets leaked."""
    # Deliberately set a highly-recognisable host env var. It must NOT
    # appear inside the container because the allowlist is empty.
    monkeypatch.setenv("LIGHTAGENT_HOST_SECRET_PROBE", "should-not-leak")
    code = (
        "import os\n"
        "print('PROBE_PRESENT' if 'LIGHTAGENT_HOST_SECRET_PROBE' in os.environ "
        "else 'PROBE_ABSENT')\n"
    )
    result = await _run_via_docker(code)
    assert result.exit_code == 0
    assert "PROBE_ABSENT" in result.stdout
    assert "should-not-leak" not in result.stdout


@requires_docker
async def test_docker_container_removed_after_exit() -> None:
    """After ``run()`` returns, ``docker ps -a`` does not list the container."""
    import secrets

    marker = f"lightagent-test-{secrets.token_hex(4)}"
    code = f"print('{marker}')\n"
    result = await _run_via_docker(code)
    assert marker in result.stdout

    # ``docker ps -a`` is the broadest listing. The ``--rm`` flag passed
    # by DockerBackend should have removed the ephemeral container, so
    # the marker string (which happens to match the random container
    # name prefix) must not show up in ``--format '{{.Names}}'``.
    ps = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert ps.returncode == 0
    assert "lightagent-codeact-" not in ps.stdout, (
        "Ephemeral codeact container leaked after run() — check --rm flag "
        "and kill path. docker ps -a output:\n" + ps.stdout
    )
