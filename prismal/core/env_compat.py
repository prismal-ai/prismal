"""Backward-compatibility for the legacy ``LIGHTAGENT_`` environment prefix.

Prismal reads configuration from ``PRISMAL_*`` environment variables (renamed
from ``LIGHTAGENT_*`` in v3.0.0). To avoid breaking existing deployments, this
module mirrors any still-present ``LIGHTAGENT_<NAME>`` variable onto
``PRISMAL_<NAME>`` — but only when the new name is unset — and emits a single
``DeprecationWarning`` listing what was mirrored.

The mirror runs once, on first import of :mod:`prismal.core` (which every
settings consumer and built-in skill imports transitively), so it is active
before any :func:`os.getenv` call or ``Settings`` instantiation reads the
environment.

This shim is transitional and will be removed in a future major release once
users have migrated their environment to the ``PRISMAL_`` prefix.
"""

from __future__ import annotations

import os
import warnings

_LEGACY_PREFIX = "LIGHTAGENT_"
_NEW_PREFIX = "PRISMAL_"
_applied = False


def apply_legacy_env_aliases() -> list[str]:
    """Mirror ``LIGHTAGENT_*`` env vars onto ``PRISMAL_*`` (idempotent).

    For each ``LIGHTAGENT_<NAME>`` present in the environment whose
    ``PRISMAL_<NAME>`` counterpart is unset, copies the value across so legacy
    configuration keeps working. Returns the list of legacy names mirrored.
    """
    global _applied
    if _applied:
        return []
    _applied = True

    mirrored: list[str] = []
    for key, value in list(os.environ.items()):
        if not key.startswith(_LEGACY_PREFIX):
            continue
        new_key = _NEW_PREFIX + key[len(_LEGACY_PREFIX) :]
        if new_key not in os.environ:
            os.environ[new_key] = value
            mirrored.append(key)

    if mirrored:
        warnings.warn(
            "The 'LIGHTAGENT_' environment-variable prefix is deprecated; "
            "rename these to 'PRISMAL_': " + ", ".join(sorted(mirrored)) + ". "
            "Legacy names still work for now but will be removed in a future "
            "major release.",
            DeprecationWarning,
            stacklevel=2,
        )
    return mirrored


# Activate on import (see module docstring).
apply_legacy_env_aliases()
