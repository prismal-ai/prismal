"""Herramientas LangChain para que los agentes usen la sandbox multi-lenguaje.

Todas las herramientas validan que ``shell_enabled=true`` antes de ejecutar
cualquier subproceso.  Las operaciones de lectura/escritura de archivos no
requieren ese flag.
"""

from __future__ import annotations

import os
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from lightagent.core.logging import get_logger

logger = get_logger("lightagent.sandbox.tools")

__all__ = [
    "SANDBOX_TOOLS",
    "sandbox_exec",
    "sandbox_install",
    "sandbox_ls",
    "sandbox_read_file",
    "sandbox_shell",
    "sandbox_status",
    "sandbox_write_file",
]


def _check_shell_enabled() -> str | None:
    """Verifica que shell_enabled=true; devuelve mensaje de error o None."""
    from lightagent.core.config import get_settings  # noqa: PLC0415

    if not get_settings().shell_enabled:
        return (
            "Ejecución de código deshabilitada. "
            "Establece LIGHTAGENT_SHELL_ENABLED=true en tu .env para habilitarla."
        )
    return None


def _get_manager() -> object:
    """Devuelve un SandboxManager con la configuración actual."""
    from lightagent.sandbox.manager import SandboxManager  # noqa: PLC0415

    return SandboxManager()


@tool
def sandbox_exec(code: str, language: str = "python", workdir: str = "") -> str:
    """Ejecuta código en la sandbox multi-lenguaje.

    Soporta: python, javascript, typescript, go, bash, ruff-check, tsc.
    El código se escribe en un archivo temporal, se ejecuta y el archivo
    se elimina automáticamente.

    Args:
        code: Código fuente a ejecutar.
        language: Lenguaje de programación (default: ``"python"``).
        workdir: Subdirectorio dentro de sandbox/workspace/ como CWD.
            Deja vacío para usar sandbox/tmp/ como CWD temporal.

    Returns:
        Output combinado (stdout + stderr), código de salida y duración.
        Si el runtime no está instalado, indica cómo bootstrapearlo.
    """
    err = _check_shell_enabled()
    if err:
        return err

    from lightagent.sandbox.executor import SandboxExecutor  # noqa: PLC0415
    from lightagent.sandbox.manager import SandboxManager  # noqa: PLC0415

    mgr = SandboxManager()
    mgr.setup()
    executor = SandboxExecutor(mgr)

    result = executor.run_code(code, language, workdir)
    output = result.combined_output()

    status = "OK" if result.exit_code == 0 else f"exit {result.exit_code}"
    header = f"[{language} | {status} | {result.duration_ms}ms]"
    if workdir:
        header += f" workdir=workspace/{workdir}"
    return f"{header}\n{output}"


@tool
def sandbox_install(package: str, manager: str = "pip") -> str:
    """Instala un paquete en la sandbox de desarrollo.

    Gestores soportados: pip, npm, go, ruff, typescript.

    Ejemplos:
        package="fastapi uvicorn", manager="pip"
        package="express", manager="npm"
        package="github.com/gin-gonic/gin@latest", manager="go"
        package="ruff", manager="ruff"
        package="typescript", manager="typescript"

    Args:
        package: Nombre del paquete o módulo a instalar.
        manager: Gestor de paquetes (default: ``"pip"``).

    Returns:
        Resumen de la instalación o mensaje de error detallado.
    """
    err = _check_shell_enabled()
    if err:
        return err

    from lightagent.sandbox.installer import SandboxInstaller  # noqa: PLC0415
    from lightagent.sandbox.manager import SandboxManager  # noqa: PLC0415

    mgr = SandboxManager()
    mgr.setup()
    installer = SandboxInstaller(mgr)
    return installer.install(package, manager)  # type: ignore[arg-type]


@tool
def sandbox_shell(command: str, workdir: str = "") -> str:
    """Ejecuta un comando de shell arbitrario dentro de la sandbox.

    El entorno tiene PATH aislado: los binarios de node, go y python de la
    sandbox tienen prioridad sobre los del sistema.

    Ejemplos:
        "npm init -y"
        "go mod init myapp"
        "ls -la"
        "node --version"
        "python --version"

    Args:
        command: Comando de shell a ejecutar.
        workdir: Subdirectorio dentro de sandbox/workspace/ como CWD.

    Returns:
        Output combinado del comando.
    """
    err = _check_shell_enabled()
    if err:
        return err

    from lightagent.sandbox.executor import SandboxExecutor  # noqa: PLC0415
    from lightagent.sandbox.manager import SandboxManager  # noqa: PLC0415

    mgr = SandboxManager()
    mgr.setup()
    executor = SandboxExecutor(mgr)
    result = executor.run_command(command, workdir)
    output = result.combined_output()

    status = "OK" if result.exit_code == 0 else f"exit {result.exit_code}"
    return f"[shell | {status} | {result.duration_ms}ms]\n{output}"


@tool
def sandbox_write_file(path: str, content: str) -> str:
    """Escribe un archivo en sandbox/workspace/<path>.

    Crea todos los subdirectorios necesarios automáticamente.
    Sobrescribe el archivo si ya existe.

    Args:
        path: Ruta relativa dentro de sandbox/workspace/.
              Ejemplo: ``"mi-proyecto/src/main.go"``
        content: Contenido textual del archivo.

    Returns:
        Confirmación con la ruta absoluta y número de caracteres escritos.
    """
    from lightagent.sandbox.manager import SandboxManager  # noqa: PLC0415

    mgr = SandboxManager()
    mgr.setup()

    try:
        dest = mgr.resolve_workspace(path)
    except ValueError as exc:
        return f"Ruta bloqueada: {exc}"

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    logger.debug("sandbox_write_file", path=str(dest), chars=len(content))
    return f"Archivo escrito: sandbox/workspace/{path} ({len(content)} chars)"


@tool
def sandbox_read_file(path: str) -> str:
    """Lee un archivo de sandbox/workspace/<path>.

    Args:
        path: Ruta relativa dentro de sandbox/workspace/.

    Returns:
        Contenido del archivo (truncado a 20 000 chars si es muy largo),
        o mensaje de error si no existe.
    """
    from lightagent.sandbox.manager import SandboxManager  # noqa: PLC0415

    mgr = SandboxManager()

    try:
        src = mgr.resolve_workspace(path)
    except ValueError as exc:
        return f"Ruta bloqueada: {exc}"

    if not src.exists():
        return f"Archivo no encontrado: sandbox/workspace/{path}"
    if not src.is_file():
        return f"No es un archivo: sandbox/workspace/{path}"

    content = src.read_text(encoding="utf-8", errors="replace")
    if len(content) > 20_000:
        content = content[:20_000] + "\n…[truncado a 20 000 chars]"
    return content


@tool
def sandbox_ls(path: str = "") -> str:
    """Lista archivos y directorios en sandbox/workspace/<path>.

    Args:
        path: Subdirectorio dentro de sandbox/workspace/ a listar.
              Vacío para listar la raíz de workspace/.

    Returns:
        Listado con tipo (d/f), nombre, tamaño y fecha de modificación.
    """
    import datetime  # noqa: PLC0415

    from lightagent.sandbox.manager import SandboxManager  # noqa: PLC0415

    mgr = SandboxManager()

    try:
        target = mgr.resolve_workspace(path) if path else mgr.workspace
    except ValueError as exc:
        return f"Ruta bloqueada: {exc}"

    if not target.exists():
        return f"Directorio no encontrado: sandbox/workspace/{path}"
    if not target.is_dir():
        return f"No es un directorio: sandbox/workspace/{path}"

    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
    if not entries:
        return f"(vacío) sandbox/workspace/{path or ''}"

    lines = [f"sandbox/workspace/{path or ''}:"]
    for entry in entries:
        kind = "d" if entry.is_dir() else "f"
        try:
            stat = entry.stat()
            size = f"{stat.st_size:>10,}" if entry.is_file() else "         -"
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            size, mtime = "?", "?"
        lines.append(f"  [{kind}] {entry.name:<40} {size}  {mtime}")

    return "\n".join(lines)


@tool
def sandbox_status() -> str:
    """Muestra el estado de la sandbox: runtimes instalados y espacio utilizado.

    Detecta versiones de: python, node, go, ruff, tsc.
    No requiere shell_enabled.

    Returns:
        Informe de estado con versión de cada runtime y uso de disco.
    """
    from lightagent.sandbox.manager import SandboxManager  # noqa: PLC0415

    mgr = SandboxManager()
    mgr.setup()

    runtimes = mgr.get_runtime_status()
    disk_mb = mgr.disk_usage_mb()

    lines = [f"Sandbox: {mgr.root}", ""]
    lines.append("Runtimes:")
    for name, version in runtimes.items():
        icon = "✓" if "not installed" not in version and "error" not in version else "✗"
        lines.append(f"  {icon} {name:<12} {version}")
    lines.append("")
    lines.append(f"Disco utilizado: {disk_mb} MB")
    lines.append(f"Workspace: {mgr.workspace}")
    return "\n".join(lines)


# ── Lista exportable ──────────────────────────────────────────────────────

SANDBOX_TOOLS: list[BaseTool] = [
    sandbox_exec,
    sandbox_install,
    sandbox_shell,
    sandbox_write_file,
    sandbox_read_file,
    sandbox_ls,
    sandbox_status,
]
