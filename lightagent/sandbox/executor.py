"""SandboxExecutor — ejecuta código y comandos dentro de la sandbox.

Soporta Python, JavaScript, TypeScript, Go y Bash.  Cada ejecución usa
el entorno construido por :class:`~lightagent.sandbox.manager.SandboxManager`
(PATH aislado, GOPATH, HOME sandbox, etc.) y tiene un timeout configurable.

Los archivos temporales (snippets) se eliminan automáticamente tras la
ejecución.  Los proyectos persistentes deben guardarse en ``workspace/``.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Literal

from lightagent.core.logging import get_logger
from lightagent.sandbox.manager import SandboxManager

logger = get_logger("lightagent.sandbox.executor")

__all__ = ["ExecutionResult", "SandboxExecutor"]

_LangLiteral = Literal[
    "python", "python3",
    "javascript", "js", "node",
    "typescript", "ts",
    "go", "golang",
    "bash", "sh",
    "ruff-check",
    "tsc",
]

_SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    ["python", "python3", "javascript", "js", "node",
     "typescript", "ts", "go", "golang", "bash", "sh",
     "ruff-check", "tsc"]
)


@dataclass
class ExecutionResult:
    """Resultado de una ejecución en la sandbox.

    Attributes:
        stdout: Salida estándar del proceso.
        stderr: Salida de error del proceso.
        exit_code: Código de salida (0 = éxito).
        language: Lenguaje ejecutado.
        duration_ms: Duración de la ejecución en milisegundos.
        workdir: Directorio de trabajo usado.
        truncated: ``True`` si el output fue truncado.
    """

    stdout: str
    stderr: str
    exit_code: int
    language: str
    duration_ms: int
    workdir: str
    truncated: bool = field(default=False)

    def combined_output(self, max_chars: int = 8_000) -> str:
        """Devuelve stdout + stderr combinados, truncados si exceden *max_chars*.

        Args:
            max_chars: Máximo de caracteres a retornar.

        Returns:
            String combinado con indicador de truncamiento si aplica.
        """
        raw = self.stdout
        if self.stderr:
            raw = f"{raw}\n--- stderr ---\n{self.stderr}" if raw else self.stderr
        if len(raw) > max_chars:
            return raw[:max_chars] + "\n…[output truncado]"
        return raw or "(sin output)"


class SandboxExecutor:
    """Ejecuta código y comandos arbitrarios dentro de la sandbox.

    Args:
        manager: Manager de sandbox.  Si es ``None`` se instancia uno con
            la configuración por defecto.
    """

    SUPPORTED_LANGUAGES: frozenset[str] = _SUPPORTED_LANGUAGES

    def __init__(self, manager: SandboxManager | None = None) -> None:
        """Inicializa el ejecutor.

        Args:
            manager: Manager de sandbox opcional.
        """
        self._mgr = manager or SandboxManager()

    # ── API pública ──────────────────────────────────────────────────

    def run_code(
        self,
        code: str,
        language: str,
        workdir: str = "",
        filename: str | None = None,
    ) -> ExecutionResult:
        """Ejecuta *code* en el lenguaje especificado dentro de la sandbox.

        El código se escribe en un archivo temporal en ``sandbox/tmp/``,
        se ejecuta y el archivo temporal se elimina tras la ejecución.

        Args:
            code: Código fuente a ejecutar.
            language: Lenguaje: ``python``, ``javascript``, ``typescript``,
                ``go``, ``bash``, ``ruff-check``, ``tsc``.
            workdir: Subdirectorio dentro de ``workspace/`` como CWD.
                Vacío = usar ``sandbox/tmp/``.
            filename: Nombre sugerido para el archivo temporal (sin extensión).

        Returns:
            :class:`ExecutionResult` con el resultado de la ejecución.
        """
        lang = language.lower().strip()
        if lang not in _SUPPORTED_LANGUAGES:
            return ExecutionResult(
                stdout="",
                stderr=(
                    f"Lenguaje '{language}' no soportado. "
                    f"Soportados: {', '.join(sorted(_SUPPORTED_LANGUAGES))}"
                ),
                exit_code=1,
                language=lang,
                duration_ms=0,
                workdir=workdir,
            )

        cwd = self._resolve_cwd(workdir)
        run_id = filename or str(uuid.uuid4())[:8]
        tmp_files: list[Path] = []

        try:
            cmd, tmp_files = self._build_command(lang, code, run_id, cwd)
            return self._run(cmd, cwd, lang, workdir)
        finally:
            for f in tmp_files:
                f.unlink(missing_ok=True)

    def run_command(
        self,
        command: str,
        workdir: str = "",
    ) -> ExecutionResult:
        """Ejecuta un comando de shell arbitrario dentro de la sandbox.

        El comando se ejecuta con el entorno de la sandbox (PATH aislado,
        GOPATH, HOME sandbox, etc.).

        Args:
            command: Comando de shell a ejecutar (e.g. ``"npm init -y"``).
            workdir: Subdirectorio dentro de ``workspace/`` como CWD.

        Returns:
            :class:`ExecutionResult` con stdout, stderr y exit_code.
        """
        cwd = self._resolve_cwd(workdir)
        return self._run(["bash", "-c", command], cwd, "bash", workdir)

    # ── Internos ─────────────────────────────────────────────────────

    def _resolve_cwd(self, workdir: str) -> Path:
        """Resuelve el directorio de trabajo dentro de la sandbox."""
        if not workdir:
            tmp = self._mgr.resolve_tmp()
            return tmp
        cwd = self._mgr.resolve_workspace(workdir)
        cwd.mkdir(parents=True, exist_ok=True)
        return cwd

    def _build_command(
        self,
        lang: str,
        code: str,
        run_id: str,
        cwd: Path,
    ) -> tuple[list[str], list[Path]]:
        """Construye el comando a ejecutar y la lista de archivos temporales.

        Args:
            lang: Lenguaje normalizado.
            code: Código fuente.
            run_id: ID único para el nombre del archivo temporal.
            cwd: Directorio de trabajo resuelto.

        Returns:
            Tupla ``(comando, archivos_temporales)``.
        """
        tmp_dir = self._mgr.resolve_tmp()
        tmp_files: list[Path] = []

        def _write_tmp(ext: str, content: str) -> Path:
            p = tmp_dir / f"_snip_{run_id}{ext}"
            p.write_text(content, encoding="utf-8")
            tmp_files.append(p)
            return p

        def _find_binary(name: str, fallback_dir: Path) -> str:
            local = fallback_dir / name
            if local.exists():
                return str(local)
            system = shutil.which(name)
            return system if system else name

        python_bin = _find_binary("python", self._mgr.python_dir / "bin")
        node_bin = _find_binary("node", self._mgr.node_dir / "bin")
        go_bin = _find_binary("go", self._mgr.go_dir / "bin")
        npx_bin = _find_binary("npx", self._mgr.node_dir / "bin")
        ruff_bin = _find_binary("ruff", self._mgr.python_dir / "bin")

        if lang in ("python", "python3"):
            f = _write_tmp(".py", code)
            return [python_bin, str(f)], tmp_files

        if lang in ("javascript", "js", "node"):
            f = _write_tmp(".js", code)
            return [node_bin, str(f)], tmp_files

        if lang in ("typescript", "ts"):
            f = _write_tmp(".ts", code)
            return [npx_bin, "--yes", "ts-node", str(f)], tmp_files

        if lang in ("go", "golang"):
            go_dir = tmp_dir / f"_gosnip_{run_id}"
            go_dir.mkdir(exist_ok=True)
            main_file = go_dir / "main.go"
            main_file.write_text(code, encoding="utf-8")
            tmp_files.append(main_file)
            tmp_files.append(go_dir)
            return [go_bin, "run", str(main_file)], tmp_files

        if lang in ("bash", "sh"):
            f = _write_tmp(".sh", code)
            return ["bash", str(f)], tmp_files

        if lang == "ruff-check":
            f = _write_tmp(".py", code)
            return [ruff_bin, "check", str(f)], tmp_files

        if lang == "tsc":
            f = _write_tmp(".ts", code)
            return [npx_bin, "tsc", "--noEmit", str(f)], tmp_files

        # unreachable — validated above
        raise ValueError(f"Lenguaje no soportado: {lang}")

    def _run(
        self,
        cmd: list[str],
        cwd: Path,
        lang: str,
        workdir_label: str,
    ) -> ExecutionResult:
        """Ejecuta *cmd* en *cwd* con el entorno sandbox.

        Args:
            cmd: Lista de argumentos del comando.
            cwd: Directorio de trabajo.
            lang: Etiqueta de lenguaje para el resultado.
            workdir_label: Etiqueta legible del workdir.

        Returns:
            :class:`ExecutionResult` con el resultado.
        """
        from lightagent.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        timeout = settings.sandbox_exec_timeout
        max_chars = settings.sandbox_max_output_chars

        env = self._mgr.get_env()

        logger.debug(
            "sandbox_exec",
            cmd=cmd[0],
            language=lang,
            workdir=str(cwd),
        )

        t0 = monotonic()
        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_ms = int((monotonic() - t0) * 1000)

            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            truncated = False

            total = len(stdout) + len(stderr)
            if total > max_chars:
                stdout = stdout[:max_chars]
                truncated = True

            return ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
                language=lang,
                duration_ms=duration_ms,
                workdir=workdir_label,
                truncated=truncated,
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((monotonic() - t0) * 1000)
            return ExecutionResult(
                stdout="",
                stderr=f"Timeout: ejecución superó {timeout}s.",
                exit_code=124,
                language=lang,
                duration_ms=duration_ms,
                workdir=workdir_label,
            )
        except FileNotFoundError as exc:
            return ExecutionResult(
                stdout="",
                stderr=f"Runtime no encontrado: {exc}. ¿Está instalado en la sandbox?",
                exit_code=127,
                language=lang,
                duration_ms=0,
                workdir=workdir_label,
            )
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(
                stdout="",
                stderr=f"Error de ejecución: {exc}",
                exit_code=1,
                language=lang,
                duration_ms=0,
                workdir=workdir_label,
            )
