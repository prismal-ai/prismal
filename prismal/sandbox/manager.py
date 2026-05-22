"""SandboxManager — gestión de rutas, entorno y validación de la sandbox.

La sandbox es un directorio aislado donde los agentes pueden instalar
paquetes y ejecutar código en Python, Node.js, TypeScript y Go sin afectar
al sistema anfitrión.

Estructura esperada::

    sandbox/
    ├── .python/   ← venv Python (pip, ruff, mypy, ...)
    ├── .node/     ← Node.js portátil (npm, npx, node)
    ├── .go/       ← GOPATH + binarios Go
    ├── workspace/ ← proyectos de los agentes
    └── tmp/       ← snippets temporales (limpiados tras ejecución)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from prismal.core.logging import get_logger

logger = get_logger("lightagent.sandbox.manager")

__all__ = ["SandboxManager"]


class SandboxManager:
    """Gestiona rutas, entorno y seguridad de la sandbox multi-lenguaje.

    Args:
        sandbox_root: Ruta raíz de la sandbox.  Si es relativa se resuelve
            desde el directorio de trabajo actual.
    """

    def __init__(self, sandbox_root: str | Path | None = None) -> None:
        """Inicializa el manager con la ruta raíz de la sandbox.

        Args:
            sandbox_root: Ruta raíz de la sandbox.  Por defecto usa
                ``settings.sandbox_path``.
        """
        if sandbox_root is None:
            from prismal.core.config import get_settings

            sandbox_root = get_settings().sandbox_path

        self.root: Path = Path(sandbox_root).expanduser().resolve()
        self.workspace: Path = self.root / "workspace"
        self.tmp: Path = self.root / "tmp"
        self.python_dir: Path = self.root / ".python"
        self.node_dir: Path = self.root / ".node"
        self.go_dir: Path = self.root / ".go"

    # ── Directorios ──────────────────────────────────────────────────

    def setup(self) -> None:
        """Crea todos los subdirectorios de la sandbox si no existen."""
        for d in (
            self.workspace,
            self.tmp,
            self.python_dir,
            self.node_dir,
            self.go_dir / "bin",
        ):
            d.mkdir(parents=True, exist_ok=True)
        logger.info("sandbox_setup_done", root=str(self.root))

    # ── Entorno de ejecución ─────────────────────────────────────────

    def get_env(self) -> dict[str, str]:
        """Devuelve un dict de variables de entorno para subprocesos sandbox.

        El ``PATH`` antepone los binarios locales (Python venv, Node, Go) al
        PATH del sistema, garantizando que se usen los runtimes instalados en
        la sandbox.

        Returns:
            Dict de entorno listo para pasarse a ``subprocess.run(env=...)``.
        """
        system_env = os.environ.copy()

        python_bin = str(self.python_dir / "bin")
        node_bin = str(self.node_dir / "bin")
        go_bin = str(self.go_dir / "bin")

        local_path = os.pathsep.join(p for p in (node_bin, go_bin, python_bin) if Path(p).exists())
        system_path = system_env.get("PATH", "")
        full_path = f"{local_path}{os.pathsep}{system_path}" if local_path else system_path

        return {
            **system_env,
            "PATH": full_path,
            "GOPATH": str(self.go_dir),
            "GOMODCACHE": str(self.go_dir / "pkg" / "mod"),
            "NODE_PATH": str(self.node_dir / "lib" / "node_modules"),
            "npm_config_prefix": str(self.node_dir),
            "npm_config_cache": str(self.root / ".npm-cache"),
            # Aislar HOME para que npm/pip no escriban en el HOME real
            "HOME": str(self.root),
            # Limpiar PYTHONPATH para evitar contaminación del venv del proyecto
            "PYTHONPATH": "",
            # Variables informativas
            "LIGHTAGENT_SANDBOX_ROOT": str(self.root),
        }

    # ── Validación de rutas ──────────────────────────────────────────

    def is_safe_path(self, path: Path) -> bool:
        """Verifica que *path* esté dentro de la sandbox.

        Args:
            path: Ruta absoluta a validar.

        Returns:
            ``True`` si la ruta está dentro de ``self.root``.
        """
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def resolve_workspace(self, rel: str) -> Path:
        """Resuelve *rel* dentro de ``workspace/`` y valida que sea seguro.

        Args:
            rel: Ruta relativa dentro de ``workspace/``.

        Returns:
            Ruta absoluta dentro de ``workspace/``.

        Raises:
            ValueError: Si la ruta resultante sale de la sandbox (path traversal).
        """
        resolved = (self.workspace / rel).resolve()
        if not self.is_safe_path(resolved):
            raise ValueError(f"Ruta '{rel}' sale de la sandbox (path traversal bloqueado).")
        return resolved

    def resolve_tmp(self) -> Path:
        """Devuelve el directorio ``tmp/`` creándolo si no existe.

        Returns:
            Ruta absoluta al directorio ``tmp/``.
        """
        self.tmp.mkdir(parents=True, exist_ok=True)
        return self.tmp

    # ── Estado de runtimes ───────────────────────────────────────────

    def get_runtime_status(self) -> dict[str, str]:
        """Detecta qué runtimes están instalados en la sandbox.

        Ejecuta ``--version`` de cada runtime con el entorno sandbox.
        Devuelve ``"not installed"`` para los que no se encuentran.

        Returns:
            Dict ``{runtime_name: version_string}``.
        """
        import subprocess

        env = self.get_env()
        runtimes = {
            "python": self.python_dir / "bin" / "python",
            "node": self.node_dir / "bin" / "node",
            "go": self.go_dir / "bin" / "go",
            "ruff": self.python_dir / "bin" / "ruff",
            "tsc": self.node_dir / "bin" / "tsc",
        }
        status: dict[str, str] = {}
        for name, binary in runtimes.items():
            if not binary.exists():
                # Fallback: buscar en PATH del sistema
                fallback = shutil.which(name)
                if not fallback:
                    status[name] = "not installed"
                    continue
                binary_str = fallback
            else:
                binary_str = str(binary)
            try:
                result = subprocess.run(
                    [binary_str, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=env,
                )
                version = (result.stdout or result.stderr).strip().splitlines()[0]
                status[name] = version
            except Exception as exc:
                status[name] = f"error: {exc}"
        return status

    def disk_usage_mb(self) -> float:
        """Calcula el uso de disco de la sandbox en megabytes.

        Returns:
            Tamaño total en MB.
        """
        total = sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
        return round(total / 1_048_576, 1)
