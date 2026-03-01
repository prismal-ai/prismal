"""Settings proxy for monitoring package.

Avoids circular imports by lazily importing settings only when needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightagent.core.config import Settings


def get_monitoring_settings() -> Settings:
    """Return the application settings, imported lazily to avoid circular deps.

    Returns:
        The singleton Settings instance.
    """
    from lightagent.core.config import get_settings

    return get_settings()
