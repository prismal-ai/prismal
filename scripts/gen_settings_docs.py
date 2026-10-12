"""Generate ``env.example`` and ``docs/settings-reference.md`` from ``Settings``.

Both files are derived from the pydantic models in ``prismal/core/config.py``
(``Settings`` and ``MaintenanceSettings``), so they can never drift from the
code. Section headings come from the ``# ── Section ──`` comments in the
source; descriptions come from each field's ``Field(description=...)`` or,
when absent, from the comment block directly above the field.

Usage::

    uv run python scripts/gen_settings_docs.py          # rewrite both files
    uv run python scripts/gen_settings_docs.py --check  # exit 1 if stale

``tests/unit/core/test_settings_docs_sync.py`` runs the ``--check`` mode in CI.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import textwrap
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import AliasChoices, SecretStr
from pydantic_core import PydanticUndefined

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PY = ROOT / "prismal" / "core" / "config.py"
ENV_EXAMPLE = ROOT / "env.example"
REFERENCE_MD = ROOT / "docs" / "settings-reference.md"

_SECTION_RE = re.compile(r"^\s*#\s*[─━-]{2,}\s*(?P<title>[^─━\s-].*?)\s*[─━-]*\s*$")
_RULE_RE = re.compile(r"^\s*#\s*[─━-]{3,}\s*$")
_SECRET_SUFFIXES = ("api_key", "secret", "secret_key", "password", "_token")

# Fields left active (uncommented) in env.example: the handful most hosts set.
_ACTIVE_FIELDS = frozenset(
    {
        "llm_provider",
        "default_model",
        "fallback_model",
        "anthropic_api_key",
        "openai_api_key",
        "google_api_key",
        "security_mode",
    }
)


@dataclass(frozen=True)
class FieldDoc:
    """Documentation record for one settings field."""

    name: str
    env: str
    aliases: tuple[str, ...]
    type_repr: str
    default: str
    description: str
    section: str
    secret: bool


def _source_sections_and_comments(cls_name: str) -> tuple[dict[str, str], dict[str, str]]:
    """Map each field of ``cls_name`` to its section heading and leading comment."""
    lines = CONFIG_PY.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    sections: dict[str, str] = {}
    comments: dict[str, str] = {}
    current = "General"
    cursor = cls.lineno
    for node in cls.body:
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            cursor = getattr(node, "end_lineno", cursor) or cursor
            continue
        block: list[str] = []
        boxed = False
        for raw in lines[cursor : node.lineno - 1]:
            match = _SECTION_RE.match(raw)
            if _RULE_RE.match(raw):
                boxed = not boxed
                block = []
            elif match or (boxed and raw.strip().startswith("#")):
                title = match.group("title") if match else raw.strip().lstrip("#")
                current = _clean_title(title)
                block = []
            elif raw.strip().startswith("#"):
                block.append(raw.strip().lstrip("#").strip())
            elif raw.strip():
                block = []
        sections[node.target.id] = current
        comments[node.target.id] = " ".join(b for b in block if b)
        cursor = node.end_lineno or node.lineno
    return sections, comments


def _clean_title(title: str) -> str:
    title = re.sub(r"\s*-{2,}\s*", " — ", title.strip(" ─━-"))
    return title.strip(" —")


def _type_repr(annotation: Any) -> str:
    text = getattr(annotation, "__name__", None) if isinstance(annotation, type) else None
    if text is None:
        text = repr(annotation)
    text = text.replace("typing.", "").replace("pydantic.types.", "")
    text = re.sub(r"<class '([\w.]+)'>", lambda m: m.group(1).rsplit(".", 1)[-1], text)
    return re.sub(r"\b(?:[a-z_]+\.)+([A-Z]\w*)", r"\1", text)


def _default_repr(info: FieldInfo, secret: bool) -> str:
    if secret:
        return ""
    if info.default_factory is not None:
        try:
            value: Any = info.default_factory()  # type: ignore[call-arg]
        except TypeError:
            return "<computed>"
    elif info.default is PydanticUndefined:
        return ""
    else:
        value = info.default
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return ""
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        if isinstance(value, (set, frozenset)):
            value = sorted(value)
        return json.dumps(value, default=str, sort_keys=isinstance(value, dict))
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _aliases(info: FieldInfo, env: str) -> tuple[str, ...]:
    alias = info.validation_alias
    names: list[str] = []
    if isinstance(alias, AliasChoices):
        names = [c for c in alias.choices if isinstance(c, str)]
    elif isinstance(alias, str):
        names = [alias]
    return tuple(n for n in names if n.upper() != env)


def collect(cls: type[Any], prefix: str) -> list[FieldDoc]:
    """Return one :class:`FieldDoc` per model field, in source order."""
    sections, comments = _source_sections_and_comments(cls.__name__)
    docs: list[FieldDoc] = []
    for name, info in cls.model_fields.items():
        env = f"{prefix}{name}".upper()
        type_repr = _type_repr(info.annotation)
        secret = "SecretStr" in type_repr or (
            name.endswith(_SECRET_SUFFIXES) and "bool" not in type_repr
        )
        description = " ".join((info.description or comments.get(name, "")).split())
        docs.append(
            FieldDoc(
                name=name,
                env=env,
                aliases=_aliases(info, env),
                type_repr=type_repr,
                default=_default_repr(info, secret),
                description=description,
                section=sections.get(name, "General"),
                secret=secret,
            )
        )
    return docs


def _grouped(docs: list[FieldDoc]) -> list[tuple[str, list[FieldDoc]]]:
    groups: list[tuple[str, list[FieldDoc]]] = []
    for doc in docs:
        if not groups or groups[-1][0] != doc.section:
            groups.append((doc.section, []))
        groups[-1][1].append(doc)
    return groups


def render_env(main: list[FieldDoc], maintenance: list[FieldDoc]) -> str:
    """Render the ``env.example`` template."""
    out = [
        "# " + "=" * 77,
        "# Prismal — Environment Variables Template",
        "# " + "=" * 77,
        "# GENERATED by scripts/gen_settings_docs.py from prismal/core/config.py.",
        "# Do not edit by hand: change the Field() description and re-run the script.",
        "#",
        "# Copy this file to `.env` and uncomment what you need. Every value shown is",
        "# the code default, so a commented line behaves exactly like an unset one.",
        "# All settings use the PRISMAL_ prefix; alternative names accepted for a",
        "# field are listed as `aliases`. Full reference: docs/settings-reference.md",
        "# " + "=" * 77,
    ]
    for title, docs in [*_grouped(main), ("Maintenance (PRISMAL_MAINTENANCE_)", maintenance)]:
        out += ["", "", "# " + "-" * 77, f"# {title}", "# " + "-" * 77]
        for doc in docs:
            if doc.description:
                out += [
                    f"# {line}"
                    for line in textwrap.wrap(doc.description, 77, break_on_hyphens=False)
                ]
            if doc.aliases:
                out.append(f"# aliases: {', '.join(doc.aliases)}")
            prefix = "" if doc.name in _ACTIVE_FIELDS and title != "Maintenance" else "# "
            out.append(f"{prefix}{doc.env}={doc.default}")
    out += [
        "",
        "",
        "# " + "-" * 77,
        "# Third-party variables read by tools (not part of Settings)",
        "# " + "-" * 77,
        "# Used by the Tavily web-search tool (prismal/agents/tools.py)",
        "# TAVILY_API_KEY=",
        "",
    ]
    return "\n".join(out)


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(main: list[FieldDoc], maintenance: list[FieldDoc]) -> str:
    """Render ``docs/settings-reference.md``."""
    groups = [*_grouped(main), ("Maintenance (PRISMAL_MAINTENANCE_)", maintenance)]
    out = [
        "# Settings reference",
        "",
        "<!-- GENERATED by scripts/gen_settings_docs.py — do not edit by hand. -->",
        "",
        "Every field of `prismal.core.config.Settings` (env prefix `PRISMAL_`) and",
        "`MaintenanceSettings` (env prefix `PRISMAL_MAINTENANCE_`), in source order.",
        f"`Settings` declares **{len(main)}** fields; `MaintenanceSettings` declares",
        f"**{len(maintenance)}**. Values are resolved through the injected",
        "`ConfigSourcePort` — see [configuration.md](./configuration.md) for how",
        "sources are composed (env, `.env`, secrets managers, per-tenant).",
        "",
        "Secret fields never show a default. Regenerate this page and",
        "[`env.example`](../env.example) with",
        "`uv run python scripts/gen_settings_docs.py`; CI fails if they drift.",
        "",
        "## Sections",
        "",
    ]
    anchors: list[str] = []
    for title, docs in groups:
        anchor = re.sub(r"[^a-z0-9 -]", "", title.lower()).strip().replace(" ", "-")
        anchors.append(anchor)
        out.append(f"- [{title}](#{anchor}) ({len(docs)})")
    for (title, docs), _anchor in zip(groups, anchors, strict=True):
        out += ["", f"## {title}", "", "| Variable | Type | Default | Description |"]
        out.append("|---|---|---|---|")
        for doc in docs:
            var = f"`{doc.env}`"
            if doc.aliases:
                var += "<br>aliases: " + ", ".join(f"`{a}`" for a in doc.aliases)
            default = "*(secret)*" if doc.secret else (f"`{doc.default}`" if doc.default else "—")
            out.append(
                f"| {var} | `{_md_escape(doc.type_repr)}` | {_md_escape(default)} | "
                f"{_md_escape(doc.description) or '—'} |"
            )
    out.append("")
    return "\n".join(out)


def build() -> dict[Path, str]:
    """Return the expected content of every generated file."""
    from prismal.core.config import MaintenanceSettings, Settings

    main = collect(Settings, "PRISMAL_")
    maintenance = collect(MaintenanceSettings, "PRISMAL_MAINTENANCE_")
    return {
        ENV_EXAMPLE: render_env(main, maintenance),
        REFERENCE_MD: render_markdown(main, maintenance),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if files are stale")
    args = parser.parse_args(argv)
    stale = []
    for path, content in build().items():
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != content:
            stale.append(path)
            if not args.check:
                path.write_text(content, encoding="utf-8")
    if args.check and stale:
        names = ", ".join(str(p.relative_to(ROOT)) for p in stale)
        print(f"stale: {names} — run scripts/gen_settings_docs.py", file=sys.stderr)
        return 1
    for path in stale:
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
