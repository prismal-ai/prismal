"""Media ingestion contract for the entry layer (Fase F follow-up, P1).

``ingest_media`` is the single boundary where an incoming attachment becomes
part of ``AgentState``. It validates, strips metadata, **spills the bytes to a
content-addressed file**, audits the hash, and records a *path-based* descriptor
under ``state["metadata"]["mm"]["media"]``.

Why path, not bytes: LangGraph checkpoints the whole ``AgentState`` (metadata
included) on every super-step, so raw bytes in state would be persisted on each
checkpoint — DB bloat and a contradiction of the hash-only audit policy
(DD-MM-008). Modal agents accept ``bytes | Path``, so a path works everywhere.

Layout: ``<workspace>/<session_id>/<sha256>.<ext>`` — enabling cheap per-session
cleanup via :func:`cleanup_session_media`.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger
from prismal.security.action_interceptor import ActionInterceptor
from prismal.security.media_validator import MediaKind, MediaValidator
from prismal.security.sanitizer import InputSanitizer

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.security.audit import AuditLogger

logger = get_logger("prismal.agents.multimodal.ingestion")

_EXT_BY_MIME: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


def _resolve_workspace(workspace: Path | str | None, settings: Settings | None) -> Path:
    """Resolve the media workspace root (override → settings → system temp)."""
    if workspace is not None:
        return Path(workspace)
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    if settings.media_workspace:
        return Path(settings.media_workspace)
    return Path(tempfile.gettempdir()) / "prismal_media"


def ingest_media(
    state: dict[str, Any],
    media: bytes | Path,
    *,
    kind: MediaKind | None = None,
    source: str = "",
    workspace: Path | str | None = None,
    preferred_output: str | None = None,
    validator: MediaValidator | None = None,
    sanitizer: InputSanitizer | None = None,
    audit: AuditLogger | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Validate, sanitise and spill *media*, recording a descriptor in *state*.

    Args:
        state: The AgentState (mutated in place and returned for chaining).
        media: Raw bytes or a path to the incoming attachment.
        kind: Expected media kind; auto-detected from magic bytes when ``None``.
        source: Free-form origin tag (e.g. ``"telegram:photo"``).
        workspace: Spill root override (defaults to settings / system temp).
        preferred_output: Sets ``metadata.mm.preferred_output`` when given.
        validator / sanitizer / audit / settings: Injectable collaborators.

    Returns:
        The same *state*, with ``metadata.mm.media`` appended and
        ``primary_media_index`` set.

    Raises:
        MediaValidationError: If the media fails validation.
    """
    validator = validator or MediaValidator(settings=settings)
    sanitizer = sanitizer or InputSanitizer()

    blob = media.read_bytes() if isinstance(media, Path) else media

    # 1. Validate (raises MediaValidationError on rejection).
    result = validator.validate(blob, expected_kind=kind)
    if not result.ok:
        from prismal.core.exceptions import MediaValidationError

        raise MediaValidationError(result.reason or "invalid media")
    detected_kind = result.detected_kind or kind
    assert detected_kind is not None

    # 2. Sanitise (EXIF strip for images; pass-through otherwise).
    cleaned = sanitizer.sanitize_media(blob, detected_kind, validator=validator)

    # 3. Spill to a content-addressed file under the per-session dir.
    sha256 = hashlib.sha256(cleaned).hexdigest()
    ext = _EXT_BY_MIME.get(result.detected_mime or "", ".bin")
    session_id = str(state.get("session_id") or "default")
    root = _resolve_workspace(workspace, settings)
    session_dir = root / session_id
    target = session_dir / f"{sha256}{ext}"

    if not ActionInterceptor.check_media_op("write", target, workspace_root=str(root)):
        from prismal.core.exceptions import MediaValidationError

        raise MediaValidationError(f"media path outside workspace: {target}")

    session_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(cleaned)

    # 4. Audit (hash + modality, never content).
    if audit is None:
        from prismal.security.audit import AuditLogger

        audit = AuditLogger()
    audit.log_media(
        "ingested",
        sha256=sha256,
        modality=detected_kind.value,
        size_bytes=len(cleaned),
        duration_s=result.duration_s,
    )

    # 5. Record the descriptor in metadata.mm.
    descriptor = {
        "uri": str(target),
        "kind": detected_kind.value,
        "mime": result.detected_mime,
        "sha256": sha256,
        "source": source,
        "bytes_len": len(cleaned),
    }
    meta = state.setdefault("metadata", {})
    mm = meta.setdefault("mm", {})
    media_list: list[dict[str, Any]] = mm.setdefault("media", [])
    media_list.append(descriptor)
    mm["primary_media_index"] = len(media_list) - 1
    if preferred_output is not None:
        mm["preferred_output"] = preferred_output

    logger.info(
        "media_ingested",
        session_id=session_id,
        kind=detected_kind.value,
        sha256=sha256,
        source=source,
    )
    return state


def cleanup_session_media(
    session_id: str,
    *,
    workspace: Path | str | None = None,
    settings: Settings | None = None,
) -> None:
    """Remove all spilled media for *session_id*. No-op if nothing exists."""
    root = _resolve_workspace(workspace, settings)
    session_dir = root / session_id
    if session_dir.is_dir():
        shutil.rmtree(session_dir, ignore_errors=True)
        logger.info("media_session_cleaned", session_id=session_id)


__all__ = ["cleanup_session_media", "ingest_media"]
