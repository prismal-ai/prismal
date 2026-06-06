"""Deprecated ``lightagent`` compatibility shim — use ``prismal`` instead.

``lightagent-agents`` was renamed to **prismal** in v3.0.0. Installing
``lightagent-agents`` (this 2.9.0 bridge) pulls in ``prismal`` and installs the
``meta_path`` finder below, which transparently redirects every
``lightagent.<sub>`` import to the real ``prismal.<sub>`` module.

Migrate at your convenience:

    pip uninstall lightagent-agents
    pip install prismal
    # then: from prismal. ... import ...

This bridge will stop being maintained in a future release.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.machinery import ModuleSpec
    from types import ModuleType

warnings.warn(
    "'lightagent' / 'lightagent-agents' is deprecated and was renamed to "
    "'prismal'. Install 'prismal' and import from 'prismal' instead.",
    DeprecationWarning,
    stacklevel=2,
)

_PREFIX = "lightagent."


class _PrismalRedirector(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Redirect ``lightagent.<sub>`` imports to the real ``prismal.<sub>``.

    The redirected module is aliased into ``sys.modules`` under both names, so
    ``lightagent.x.y`` and ``prismal.x.y`` are the same object.
    """

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if not fullname.startswith(_PREFIX):
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        target_name = "prismal." + spec.name[len(_PREFIX) :]
        module = importlib.import_module(target_name)
        sys.modules[spec.name] = module
        return module

    def exec_module(self, module: ModuleType) -> None:
        return


if not any(isinstance(_f, _PrismalRedirector) for _f in sys.meta_path):
    sys.meta_path.insert(0, _PrismalRedirector())
