"""Prismal agent evaluation & reliability harness (Phase V).

A sibling of the runtime: it observes the public compiled graph and the public
ports to run eval-sets, capture trajectories, score them, gate regressions, and
prove adversarial containment. It is additive — it changes no agent code.
"""

from __future__ import annotations
