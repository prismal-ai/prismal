"""SandboxInstaller — instala paquetes y runtimes dentro de la sandbox.

Soporta los gestores: ``pip``, ``npm``, ``go``, ``ruff``, ``typescript``.

Si Node.js o Go no están instalados en la sandbox, los métodos
:meth:`bootstrap_node` y :meth:`bootstrap_go` descargan los binarios
precompilados desde las URLs oficiales y los extraen en su lugar.
"""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Literal

import httpx

from lightagent.core.logging import get_logger
from lightagent.sandbox.manager import SandboxManager

logger = get_logger("lightagent.sandbox.installer")

__all__ = ["SandboxInstaller"]

_MANAGER_TYPE = Literal["pip", "npm", "go", "ruff", "typescript"]

# URLs de descarga de binarios precompilados (Linux x64)
_NODE_URL_TMPL = "https://nodejs.org/dist/v{version}/node-v{version}-linux-x64.tar.xz"
_GO_URL_TMPL = "https://go.dev/dl/go{version}.linux-amd64.tar.gz"


class SandboxInstaller:
    """Instala paquetes y runtimes en la sandbox.

    Args:
        manager: :class:`~lightagent.sandbox.manager.SandboxManager` configurado.
    """

    def __init__(self, manager: SandboxManager | None = None) -> None:
        """Inicializa el instalador.

        Args:
            manager: Manager de sandbox.  Si es ``None`` se crea uno con la
                configuración por defecto.
        """
        self._mgr = manager or SandboxManager()

    # ── API pública ──────────────────────────────────────────────────

    def install(
        self,
        package: str,
        manager: _MANAGER_TYPE = "pip",
        workdir: str | None = None,
    ) -> str:
        """Instala *package* en la sandbox usando el gestor especificado.

        Args:
            package: Nombre del paquete (e.g. ``"fastapi"``, ``"express"``,
                ``"github.com/gin-gonic/gin@latest"``).
            manager: Gestor de paquetes: ``pip``, ``npm``, ``go``, ``ruff``,
                ``typescript``.
            workdir: Subdirectorio de trabajo dentro de ``workspace/`` (solo
                para ``npm install`` local).

        Returns:
            Resumen de la instalación o mensaje de error.
        """
        mgr = manager.lower().strip()
        dispatch = {
            "pip": self._pip_install,
            "npm": self._npm_install,
            "go": self._go_get,
            "ruff": lambda p, _wd: self._pip_install("ruff", None),  # noqa: ARG005 — dispatch signature
            "typescript": lambda p, _wd: self._npm_global_install("typescript ts-node"),  # noqa: ARG005 — dispatch signature
        }
        fn = dispatch.get(mgr)
        if fn is None:
            return f"Gestor '{manager}' no soportado. Usa: pip, npm, go, ruff, typescript."
        return fn(package, workdir)

    # ── Bootstrap de runtimes ────────────────────────────────────────

    def bootstrap_node(self, version: str | None = None) -> str:
        """Descarga y extrae Node.js en ``sandbox/.node/``.

        Args:
            version: Versión a instalar (e.g. ``"20.11.0"``).  Por defecto
                usa ``settings.sandbox_node_version``.

        Returns:
            Mensaje de éxito con la versión instalada.
        """
        from lightagent.core.config import get_settings

        ver = version or get_settings().sandbox_node_version
        url = _NODE_URL_TMPL.format(version=ver)
        dest = self._mgr.node_dir

        logger.info("sandbox_bootstrap_node", version=ver, url=url)
        return self._download_and_extract(url, dest, strip_components=1, fmt="xz")

    def bootstrap_go(self, version: str | None = None) -> str:
        """Descarga y extrae Go en ``sandbox/.go/``.

        Args:
            version: Versión a instalar (e.g. ``"1.22.0"``).  Por defecto
                usa ``settings.sandbox_go_version``.

        Returns:
            Mensaje de éxito con la versión instalada.
        """
        from lightagent.core.config import get_settings

        ver = version or get_settings().sandbox_go_version
        url = _GO_URL_TMPL.format(version=ver)
        dest = self._mgr.go_dir

        logger.info("sandbox_bootstrap_go", version=ver, url=url)
        return self._download_and_extract(url, dest, strip_components=1, fmt="gz")

    def setup_python_venv(self) -> str:
        """Crea el venv Python aislado en ``sandbox/.python/``.

        Si el venv ya existe, solo actualiza pip.

        Returns:
            Mensaje de resultado.
        """
        venv_dir = self._mgr.python_dir
        python_bin = venv_dir / "bin" / "python"

        if not python_bin.exists():
            logger.info("sandbox_create_venv", path=str(venv_dir))
            result = subprocess.run(
                ["python3", "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                return f"Error creando venv: {result.stderr}"

        # Instalar herramientas base
        pip = str(venv_dir / "bin" / "pip")
        result = subprocess.run(
            [pip, "install", "--upgrade", "pip", "ruff", "mypy"],
            capture_output=True,
            text=True,
            timeout=120,
            env=self._mgr.get_env(),
        )
        if result.returncode != 0:
            return f"venv creado pero error instalando herramientas: {result.stderr}"

        return f"venv Python listo en {venv_dir}"

    # ── Instaladores internos ────────────────────────────────────────

    def _pip_install(self, package: str, _workdir: str | None) -> str:
        """Instala un paquete Python en el venv sandbox."""
        pip = self._mgr.python_dir / "bin" / "pip"
        if not pip.exists():
            setup_result = self.setup_python_venv()
            if "Error" in setup_result:
                return setup_result

        result = subprocess.run(
            [str(pip), "install", *package.split()],
            capture_output=True,
            text=True,
            timeout=120,
            env=self._mgr.get_env(),
            cwd=str(self._mgr.root),
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"pip install error:\n{output}"
        last = output.splitlines()[-1] if output else "OK"
        return f"pip install {package!r}: {last}"

    def _npm_install(self, package: str, workdir: str | None) -> str:
        """Instala un paquete npm localmente en workdir o globalmente."""
        node = self._mgr.node_dir / "bin" / "node"
        npm = self._mgr.node_dir / "bin" / "npm"

        if not node.exists():
            return (
                "Node.js no está instalado en la sandbox. "
                "Ejecuta sandbox_install con manager='npm' para bootstrap, "
                "o usa sandbox_shell('sandbox_bootstrap_node') primero."
            )

        cwd = str(self._mgr.resolve_workspace(workdir)) if workdir else str(self._mgr.workspace)
        cmd = [str(npm), "install", *package.split()]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=self._mgr.get_env(),
            cwd=cwd,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"npm install error:\n{output}"
        return f"npm install {package!r}: OK\n{output[-500:]}"

    def _npm_global_install(self, packages: str) -> str:
        """Instala paquetes npm globalmente en sandbox/.node/."""
        npm = self._mgr.node_dir / "bin" / "npm"
        if not npm.exists():
            return "Node.js no instalado en sandbox."

        result = subprocess.run(
            [str(npm), "install", "-g", *packages.split()],
            capture_output=True,
            text=True,
            timeout=120,
            env=self._mgr.get_env(),
            cwd=str(self._mgr.root),
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"npm install -g error:\n{output}"
        return f"npm install -g {packages!r}: OK"

    def _go_get(self, package: str, _workdir: str | None) -> str:
        """Descarga un módulo Go en la sandbox."""
        go_bin = self._mgr.go_dir / "bin" / "go"
        if not go_bin.exists():
            go_bin_path = subprocess.run(
                ["which", "go"], capture_output=True, text=True
            ).stdout.strip()
            if not go_bin_path:
                return (
                    "Go no está instalado en la sandbox ni en el sistema. "
                    "Instálalo con sandbox_install manager='go' o ejecuta setup_sandbox.sh."
                )
            go_bin = Path(go_bin_path)

        result = subprocess.run(
            [str(go_bin), "get", package],
            capture_output=True,
            text=True,
            timeout=120,
            env=self._mgr.get_env(),
            cwd=str(self._mgr.workspace),
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"go get error:\n{output}"
        return f"go get {package!r}: OK"

    # ── Descarga de binarios ─────────────────────────────────────────

    def _download_and_extract(
        self,
        url: str,
        dest: Path,
        strip_components: int,
        fmt: str,
    ) -> str:
        """Descarga un tarball y lo extrae en *dest*.

        Args:
            url: URL del archivo tar.
            dest: Directorio de destino.
            strip_components: Número de componentes de prefijo a eliminar.
            fmt: Formato de compresión (``"gz"`` o ``"xz"``).

        Returns:
            Mensaje de éxito o error.
        """
        dest.mkdir(parents=True, exist_ok=True)

        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
                resp.raise_for_status()
                with tempfile.NamedTemporaryFile(suffix=f".tar.{fmt}", delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        tmp.write(chunk)
        except Exception as exc:
            return f"Error descargando {url}: {exc}"

        try:
            mode = f"r:{fmt}"
            with tarfile.open(tmp_path, mode) as tar:
                members = tar.getmembers()
                for member in members:
                    parts = Path(member.name).parts
                    if len(parts) <= strip_components:
                        continue
                    member.name = str(Path(*parts[strip_components:]))
                    tar.extract(member, path=str(dest), filter="data")
            tmp_path.unlink(missing_ok=True)
            return f"Instalado en {dest}"
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            return f"Error extrayendo archivo: {exc}"
