"""Transitional compatibility shim — ``lightagent`` → ``prismal``.

DEPRECATED. The import namespace was renamed from ``lightagent`` to ``prismal``
in v3.0.0 (rebrand to Prismal). This shim transparently redirects every
``lightagent.<sub>`` import to the real ``prismal.<sub>`` module so that code,
tests, and examples not yet migrated keep working during the migration.

It is **temporary** and will be removed before the v3.0.0 release (migration
Fase 6). It is intentionally **not** shipped in the built wheel
(``[tool.hatch.build.targets.wheel] packages = ["prismal"]``); end-user
backward compatibility is handled separately by the deprecated distribution
package ``lightagent-agents`` (which depends on ``prismal``).
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import warnings
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.machinery import ModuleSpec

warnings.warn(
    "The 'lightagent' import namespace is deprecated; import from 'prismal' instead.",
    DeprecationWarning,
    stacklevel=2,
)

_PREFIX = "lightagent."


class _PrismalRedirector(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Redirect ``lightagent.<sub>`` imports to the real ``prismal.<sub>``.

    The redirected module object is aliased into ``sys.modules`` under *both*
    names, so ``lightagent.x.y`` and ``prismal.x.y`` are the same object — this
    keeps ``unittest.mock.patch("lightagent.x.y...")`` patching the very object
    the code under test (which now lives under ``prismal``) actually uses.
    """

    def find_spec(
        self,
        fullname: str,
        path: "Sequence[str] | None" = None,
        target: "ModuleType | None" = None,
    ) -> "ModuleSpec | None":
        if not fullname.startswith(_PREFIX):
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: "ModuleSpec") -> ModuleType:
        target_name = "prismal." + spec.name[len(_PREFIX) :]
        module = importlib.import_module(target_name)
        sys.modules[spec.name] = module
        return module

    def exec_module(self, module: ModuleType) -> None:
        # The real module was already imported and executed in create_module.
        return


# Insert at the front so 'lightagent.*' is intercepted before the path-based
# finder tries to load a *separate* module object from the prismal/ files.
# A guard keeps this idempotent across repeated imports / xdist workers.
if not any(isinstance(_f, _PrismalRedirector) for _f in sys.meta_path):
    sys.meta_path.insert(0, _PrismalRedirector())
