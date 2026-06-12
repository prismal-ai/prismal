"""Deprecated shim for the legacy ``LIGHTAGENT_`` environment prefix (Phase W).

Historically this module mirrored any still-present ``LIGHTAGENT_<NAME>`` variable
onto ``PRISMAL_<NAME>`` by **mutating ``os.environ`` on import** of
:mod:`prismal.core`. Phase W removed that global, import-time side effect: the
legacy ``LIGHTAGENT_* → PRISMAL_*`` mirror now lives inside
:class:`prismal.core.config_source.EnvConfigSource.load`, where it is explicit,
local, and active only when that source is actually used (the default).

:func:`apply_legacy_env_aliases` is retained only as a backward-compatible
**no-op** shim that emits a ``DeprecationWarning`` and no longer touches
``os.environ``. It is **not** called at import time. It will be removed in a
future major release.
"""

from __future__ import annotations

import warnings


def apply_legacy_env_aliases() -> list[str]:
    """DEPRECATED no-op (Phase W). Returns ``[]`` and mutates nothing.

    The legacy ``LIGHTAGENT_* → PRISMAL_*`` mirror now happens inside
    :meth:`prismal.core.config_source.EnvConfigSource.load`. This shim no longer
    reads or writes ``os.environ`` and is not invoked at import time.

    Returns:
        An empty list (kept for signature compatibility with the old API).
    """
    warnings.warn(
        "prismal.core.env_compat.apply_legacy_env_aliases() is deprecated and is "
        "now a no-op: the LIGHTAGENT_ -> PRISMAL_ mirror moved into "
        "EnvConfigSource.load() (Phase W). It no longer mutates os.environ and "
        "will be removed in a future major release.",
        DeprecationWarning,
        stacklevel=2,
    )
    return []
