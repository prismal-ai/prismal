"""W5 — legacy shim relocation: no import-time ``os.environ`` mutation (Phase W)."""

from __future__ import annotations

import subprocess
import sys
import warnings


class TestImportPurity:
    def test_importing_prismal_core_does_not_mirror_legacy_env(self) -> None:
        """A fresh ``import prismal.core`` must not write ``PRISMAL_*`` into os.environ.

        Run in a subprocess with ``LIGHTAGENT_FOO`` set: pre-Phase-W the import-time
        ``apply_legacy_env_aliases()`` mirrored it onto ``PRISMAL_FOO``; Phase W folds
        that into ``EnvConfigSource.load()`` so importing the core mutates nothing.
        """
        code = (
            "import os; assert 'PRISMAL_FOO' not in os.environ;"
            "import prismal.core;"
            "import os as _o;"
            "print('MIRRORED' if 'PRISMAL_FOO' in _o.environ else 'CLEAN')"
        )
        env = {"LIGHTAGENT_FOO": "bar", "PATH": __import__("os").environ.get("PATH", "")}
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        assert out.stdout.strip().endswith("CLEAN"), out.stdout + out.stderr


class TestDeprecatedShim:
    def test_apply_legacy_env_aliases_is_noop_and_warns(self, monkeypatch) -> None:
        from prismal.core import env_compat

        monkeypatch.setenv("LIGHTAGENT_BAZ", "qux")
        monkeypatch.delenv("PRISMAL_BAZ", raising=False)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = env_compat.apply_legacy_env_aliases()

        assert result == []  # no-op: returns nothing mirrored
        import os

        assert "PRISMAL_BAZ" not in os.environ  # did not mutate os.environ
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)
