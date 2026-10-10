"""Agentic memory v2 (Phase MEM): typed, decision-gated, budgeted, tenant-scoped memory.

Opt-in behind ``settings.memory_v2_enabled``; legacy ``prismal.memory`` modules are
wrapped, never edited. Thin re-export — the logic lives in the submodules.
"""

from prismal.memory.v2.consolidation import Consolidator, register_consolidation_job
from prismal.memory.v2.decision_bridge import CascadeDecisionPort
from prismal.memory.v2.forget import ForgetPolicy, ForgetRequest
from prismal.memory.v2.playbooks import Playbook, PlaybookCurator, PlaybookStore, render_playbook
from prismal.memory.v2.ports import MemoryBackendPort, MemoryGraphPort, MemoryPort
from prismal.memory.v2.recall import RecallController, RecallPolicy
from prismal.memory.v2.render import memory_block_for_prompt, render_items
from prismal.memory.v2.service import MemoryService, build_memory_service
from prismal.memory.v2.types import (
    ConsolidationReport,
    ForgetActor,
    ForgetSelector,
    MemoryCandidate,
    MemoryContext,
    MemoryEdge,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Provenance,
    RecalledItem,
    RecallQuery,
    RecallResult,
    RelationKind,
    WriteOutcome,
    new_record_id,
)
from prismal.memory.v2.write_gate import WriteGate, WriteGatePolicy

__all__ = [
    "CascadeDecisionPort",
    "ConsolidationReport",
    "Consolidator",
    "ForgetActor",
    "ForgetPolicy",
    "ForgetRequest",
    "ForgetSelector",
    "MemoryBackendPort",
    "MemoryCandidate",
    "MemoryContext",
    "MemoryEdge",
    "MemoryGraphPort",
    "MemoryPort",
    "MemoryRecord",
    "MemoryScope",
    "MemoryService",
    "MemoryType",
    "Playbook",
    "PlaybookCurator",
    "PlaybookStore",
    "Provenance",
    "RecallController",
    "RecallPolicy",
    "RecallQuery",
    "RecallResult",
    "RecalledItem",
    "RelationKind",
    "WriteGate",
    "WriteGatePolicy",
    "WriteOutcome",
    "build_memory_service",
    "memory_block_for_prompt",
    "new_record_id",
    "register_consolidation_job",
    "render_items",
    "render_playbook",
]
