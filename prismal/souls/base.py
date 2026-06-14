"""Base soul model and parsing for Prismal's Kokoro deliberation layer.

A *soul* is a Markdown-authored persona (``SOUL.md`` = YAML frontmatter +
instructional body) that conditions a :class:`SoulAgent` during a Kokoro
deliberation.  The tier layout and parsing rules deliberately mirror the
three-tier skills system (``skills/base.py``).

Security note: the ``SOUL.md`` body is **user-controlled content**.  It is
length-capped here at load time (``settings.soul_max_body_chars``) and must
only reach a model through ``SecurePromptBuilder`` — never f-stringed into a
prompt template.

Example::

    from pathlib import Path
    from prismal.souls.base import load_soul

    soul = load_soul(Path("prismal/souls/available/spirit"))
    print(soul.metadata.name, soul.metadata.role)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError

from prismal.core.exceptions import SoulValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from prismal.core.config import Settings


class SoulMetadata(BaseModel):
    """Metadata describing a Kokoro soul (persona), parsed from SOUL.md frontmatter."""

    name: str = Field(..., description="Unique english id slug (snake_case), e.g. 'spirit'")
    alias_jp: str = Field(default="", description="Japanese alias, e.g. '魂' / 'tamashii'")
    description: str = Field(..., description="One-line description of the persona")
    role: str = Field(..., description="Deliberation lens, e.g. 'values', 'logic', 'empathy'")
    temperament: str = Field(default="balanced", description="Tone/voice hint for the persona")
    values: list[str] = Field(
        default_factory=list, description="Guiding priorities the soul argues from"
    )
    version: str = Field(default="1.0.0", description="Semantic version of the soul")
    author: str = Field(default="unknown", description="Author handle")
    tags: list[str] = Field(default_factory=list, description="Categorisation tags")
    model: str = Field(default="", description="Optional per-soul model override (empty = default)")


@dataclass(frozen=True)
class Soul:
    """A fully-loaded soul: parsed metadata + the Markdown persona body.

    Attributes:
        metadata: Validated frontmatter metadata.
        body: The instructional persona text (everything after the frontmatter).
        source_dir: Directory the soul was loaded from.
    """

    metadata: SoulMetadata
    body: str
    source_dir: Path


def _find_soul_md(soul_dir: Path) -> Path | None:
    """Return the ``soul.md`` file inside *soul_dir*, case-insensitively.

    Accepts any capitalisation (``soul.md``, ``SOUL.md``, ``Soul.md``, …).

    Args:
        soul_dir: Directory to search in.

    Returns:
        The :class:`~pathlib.Path` to the file, or ``None`` if not found.
    """
    if not soul_dir.is_dir():
        return None
    for entry in soul_dir.iterdir():
        if entry.is_file() and entry.name.lower() == "soul.md":
            return entry
    return None


def parse_soul_md(soul_dir: Path) -> dict[str, object]:
    """Parse YAML frontmatter from ``SOUL.md`` inside *soul_dir*.

    The frontmatter must be enclosed in ``---`` fences as the very first block
    of the file.  Accepts any capitalisation of the filename.

    Args:
        soul_dir: Directory that contains ``SOUL.md`` (any case).

    Returns:
        Parsed frontmatter as a plain :class:`dict`; empty dict on any error.
    """
    import yaml

    md_path = _find_soul_md(soul_dir)
    if md_path is None:
        return {}
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end].strip()) or {}
    except Exception:
        return {}


def _soul_md_body(soul_dir: Path) -> str:
    """Return the body of ``SOUL.md`` (everything after the YAML frontmatter).

    Strips the ``--- ... ---`` block so only the instructional persona content
    is returned.  This is what conditions the :class:`SoulAgent`.

    Args:
        soul_dir: Directory that contains ``SOUL.md`` (any case).

    Returns:
        Body text as a string; empty string if not found or no body.
    """
    md_path = _find_soul_md(soul_dir)
    if md_path is None:
        return ""
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3 :].lstrip("\n")


def default_souls_root(settings: Settings | None = None) -> Path:
    """Return the configured souls root directory.

    Args:
        settings: Prismal settings; ``None`` resolves via ``get_settings()``.

    Returns:
        ``settings.souls_dir`` when set, otherwise the packaged
        ``prismal/souls`` directory next to this module.
    """
    from pathlib import Path as _Path

    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    if settings.souls_dir:
        return _Path(settings.souls_dir).expanduser().resolve()
    return _Path(__file__).parent.resolve()


def load_soul(
    soul_dir: Path,
    *,
    souls_root: Path | None = None,
    settings: Settings | None = None,
) -> Soul:
    """Load and validate a soul from a directory.

    Args:
        soul_dir: Directory containing a ``SOUL.md`` (any case).
        souls_root: Path-confinement root.  ``None`` resolves via
            :func:`default_souls_root`; *soul_dir* must live underneath it.
        settings: Prismal settings; ``None`` resolves via ``get_settings()``.

    Returns:
        The fully-loaded :class:`Soul`.

    Raises:
        SoulValidationError: missing ``SOUL.md``, missing required metadata,
            body too large (> ``settings.soul_max_body_chars``), or path
            outside the souls root.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()

    soul_id = soul_dir.name
    resolved_root = (souls_root or default_souls_root(settings)).resolve()
    resolved_dir = soul_dir.resolve()
    if not resolved_dir.is_relative_to(resolved_root):
        raise SoulValidationError(
            soul_id, f"path '{resolved_dir}' is outside the souls root '{resolved_root}'"
        )

    if _find_soul_md(resolved_dir) is None:
        raise SoulValidationError(soul_id, "SOUL.md not found")

    meta_dict = parse_soul_md(resolved_dir)
    if not meta_dict:
        raise SoulValidationError(soul_id, "missing or invalid YAML frontmatter")

    try:
        metadata = SoulMetadata.model_validate(meta_dict)
    except (ValidationError, TypeError) as exc:
        raise SoulValidationError(soul_id, f"invalid metadata: {exc}") from exc

    body = _soul_md_body(resolved_dir)
    max_chars = settings.soul_max_body_chars
    if len(body) > max_chars:
        raise SoulValidationError(soul_id, f"body too large: {len(body)} chars (max {max_chars})")

    # Phase H — the SOUL.md body is user-controlled content; tag it untrusted so
    # the indirect-injection detector scores it before it reaches a model.
    from prismal.security.taint import Provenance, mark_untrusted_active

    mark_untrusted_active(body, Provenance.SOUL)

    return Soul(metadata=metadata, body=body, source_dir=resolved_dir)


__all__ = [
    "Soul",
    "SoulMetadata",
    "_find_soul_md",
    "_soul_md_body",
    "default_souls_root",
    "load_soul",
    "parse_soul_md",
]
