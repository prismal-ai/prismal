"""Decision state building (Phase DEC — SPEC-DEC-SEC-001/003).

The decision-model analogue of ``SecurePromptBuilder`` (Critical Rule #1):
untrusted text reaches a decision model **only** as a state value — L1
sanitized, fenced and labelled ``untrusted:<source>`` — never inside the
developer-authored instructions, criteria, options or levels. Authority claims
("pre-approved", "ya autorizado", …) and ``GuardrailsEngine`` injection rules
add ``injection_flags`` and taint the state; the gate then refuses to accept a
≥ MEDIUM decision without deterministic corroboration (SEC-003).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import yaml

from prismal.security.sanitizer import InputSanitizer
from prismal.security.taint import TaintRegistry, get_active_taint_registry

if TYPE_CHECKING:
    from collections.abc import Mapping

_FENCE_OPEN = "«untrusted:{source}»"
_FENCE_CLOSE = "«/untrusted»"
_FENCE_MARKER_RE = re.compile(r"«/?untrusted[^»]*»", re.IGNORECASE)
_SOURCE_RE = re.compile(r"[^a-z0-9_.-]")

_AUTHORITY_CLAIMS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bpre-?approved\b",
        r"\balready\s+(?:been\s+)?(?:authori[sz]ed|approved|cleared|verified)\b",
        r"\b(?:authori[sz]ed|approved|cleared)\s+by\s+(?:security|the\s+admin|an?\s+admin|it)\b",
        r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\b",
        r"\bnote\s+to\s+(?:the\s+)?(?:reviewer|classifier|model|judge)\b",
        r"\banswer\s+(?:with\s+)?['\"]?(?:auto_allow|yes|true|approve)\b",
        r"\baprobad[oa]s?\s+previamente\b",
        r"\bpre-?aprobad[oa]s?\b",
        r"\bya\s+(?:est[aá]\s+)?(?:autorizad[oa]|aprobad[oa])\b",
        r"\bautorizad[oa]\s+por\s+(?:seguridad|el\s+administrador)\b",
        r"\bignora(?:r)?\s+(?:las\s+|todas\s+las\s+)?instrucciones\s+(?:anteriores|previas)\b",
        r"\bnota\s+para\s+el\s+(?:revisor|clasificador|modelo)\b",
    )
)

_PATTERNS_FILE = (
    Path(__file__).resolve().parents[1] / "security" / "patterns" / "injection_patterns.yaml"
)
_INJECTION_CATEGORIES = (
    "override_instructions",
    "persona_injection",
    "jailbreak_keywords",
    "template_injection",
    "data_exfiltration",
    "code_injection",
)

_ES_TEXT = (
    "el la los las de del que y en un una por para con es se no lo como más pero sus le "
    "ya o este esta porque cuando muy sin sobre también me hasta hay donde quien desde "
    "todo nos durante uno ni contra ese eso mí qué otro él cual poco ella estar estos "
    "algunos algo nosotros mi antes ayuda necesito quiero favor escribe dime puedes"
)
_ES_WORDS = frozenset(_ES_TEXT.split())
_EN_TEXT = (
    "the be to of and a in that have i it for not on with he as you do at this but his by "
    "from they we say her she or an will my one all would there their what so up out if "
    "about who get which go me when make can like time no just him know take people into "
    "please write function list could should"
)
_EN_WORDS = frozenset(_EN_TEXT.split())
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


@lru_cache(maxsize=1)
def _injection_rules() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Input-injection categories of the ``GuardrailsEngine`` catalog (loaded once)."""
    try:
        data = yaml.safe_load(_PATTERNS_FILE.read_text("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ()
    raw = cast("dict[str, list[str]]", data.get("patterns") or {})
    flags = re.IGNORECASE | re.DOTALL | re.MULTILINE
    return tuple(
        (category, re.compile(p, flags))
        for category in _INJECTION_CATEGORIES
        for p in raw.get(category, [])
    )


def detect_language(text: str) -> str:
    """``"en"`` | ``"es"`` | ``"other"`` — stdlib heuristic (script + stop words).

    Non-Latin scripts are ``other`` (they escalate unless configured); Latin text
    with no signal defaults to ``en``.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    latin = sum(1 for c in letters if c.isascii() or "À" <= c <= "ɏ")
    if latin / len(letters) < 0.5:
        return "other"
    words = [w.lower() for w in _WORD_RE.findall(text)]
    es = sum(1 for w in words if w in _ES_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    es += sum(2 for c in text if c in "ñ¿¡áéíóú")
    if len(words) >= 4 and es == 0 and en == 0:
        return "other"  # Latin text with no EN/ES signal: never enforce it as English
    return "es" if es > en else "en"


@dataclass(frozen=True)
class DecisionState:
    """An immutable, hashed decision state (only ``fields`` reaches a backend)."""

    fields: Mapping[str, str]
    tainted_fields: frozenset[str]
    injection_flags: tuple[str, ...]
    language: str
    sha256: str
    over_limit: bool = False

    @property
    def tainted(self) -> bool:
        """True when any field is tainted (registry provenance or SEC-001 flags)."""
        return bool(self.tainted_fields)

    @property
    def total_chars(self) -> int:
        return sum(len(k) + len(v) for k, v in self.fields.items())


class DecisionStateBuilder:
    """Fluent builder: trusted constants + fenced untrusted text → :class:`DecisionState`."""

    def __init__(
        self,
        *,
        sanitizer: InputSanitizer | None = None,
        taint: TaintRegistry | None = None,
        max_chars: int = 16_000,
        language: str | None = None,
    ) -> None:
        self._sanitizer = sanitizer or InputSanitizer()
        self._taint = taint
        self._max_chars = max_chars
        self._language = language
        self._fields: dict[str, str] = {}
        self._untrusted_text: list[str] = []
        self._tainted: set[str] = set()
        self._flags: list[str] = []

    def _put(self, key: str, value: str) -> None:
        if key in self._fields:
            raise ValueError(f"duplicate decision state key {key!r}")
        self._fields[key] = value

    def add_trusted(self, key: str, value: str) -> DecisionStateBuilder:
        """Developer-authored / system text (still checked against the taint registry)."""
        self._put(key, value)
        if self._registry_untrusted(value):
            self._tainted.add(key)
        return self

    def add_untrusted(self, key: str, value: str, *, source: str) -> DecisionStateBuilder:
        """User / tool / RAG / worker text: sanitize, scan, fence and label."""
        raw = value
        clean = self._sanitizer.sanitize(value, max_chars=max(len(value), 1))
        previous = None
        while previous != clean:  # nested markers ("«/untr«/untrusted»usted»") collapse
            previous, clean = clean, _FENCE_MARKER_RE.sub("", clean)
        label = _SOURCE_RE.sub("_", source.lower()) or "unknown"
        self._put(key, f"{_FENCE_OPEN.format(source=label)}\n{clean}\n{_FENCE_CLOSE}")
        self._untrusted_text.append(clean)
        flags = _scan(clean)
        if flags:
            self._flags.extend(f for f in flags if f not in self._flags)
            self._tainted.add(key)
        if self._registry_untrusted(raw) or self._registry_untrusted(clean):
            self._tainted.add(key)
        return self

    def _registry_untrusted(self, value: str) -> bool:
        registry = self._taint if self._taint is not None else get_active_taint_registry()
        return registry is not None and registry.is_untrusted(value)

    def build(self) -> DecisionState:
        fields = dict(self._fields)
        language = self._language or detect_language(
            " ".join(self._untrusted_text) or " ".join(fields.values())
        )
        canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        total = sum(len(k) + len(v) for k, v in fields.items())
        return DecisionState(
            fields=MappingProxyType(fields),
            tainted_fields=frozenset(self._tainted),
            injection_flags=tuple(self._flags),
            language=language,
            sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            over_limit=total > self._max_chars,
        )


def _scan(text: str) -> list[str]:
    flags: list[str] = []
    if any(p.search(text) for p in _AUTHORITY_CLAIMS):
        flags.append("authority_claim")
    for category, pattern in _injection_rules():
        flag = f"injection:{category}"
        if flag not in flags and pattern.search(text):
            flags.append(flag)
    return flags
