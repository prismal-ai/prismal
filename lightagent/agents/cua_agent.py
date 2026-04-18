"""Computer Use Agent (CUA) — SPEC-043 / Phase 41.

The CUA uses a vision-capable LLM to perceive browser screenshots and
execute UI actions in a Playwright-controlled browser. It complements
the classic ``researcher`` node which can only query the DOM via
text-based tools: CUA can see the page, click on canvas elements,
fill out legacy forms, and drive interactive flows that would
otherwise require a human.

Every action emitted by the VLM flows through a three-stage gate:

1. **Enablement check** — ``cua_enabled`` + ``cua_vision_model`` must
   both be set. Missing either one returns a graceful
   :class:`AIMessage` that explains how to enable the agent instead
   of crashing the graph.
2. **Risk classification** — :func:`classify_risk` maps each
   ``action_type`` to ``SAFE`` / ``MODERATE`` / ``HIGH_RISK``. Safe
   and moderate actions execute directly; high-risk actions pause
   the workflow via LangGraph ``interrupt()`` (SPEC-037 HITL).
3. **Audit trail** — every action (executed, skipped, rejected) is
   logged to the existing :class:`AuditLogger` with action type,
   target, risk level, HITL decision, screenshot SHA-256 hash, and
   timestamp. This satisfies AC-043-5 and means rollback
   investigations have a complete, tamper-evident trail.

The actual Playwright plumbing is deliberately thin: this module
exposes ``take_screenshot()`` and ``execute_browser_action()`` as
lazy-imported helpers that are patched in unit tests. Production
deployments wire them up to a real Playwright browser launched
elsewhere in the process (outside the scope of this phase).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry
from lightagent.security.audit import AuditLogger

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.cua")


# ---------------------------------------------------------------------------
# Risk taxonomy (AC-043-2)
# ---------------------------------------------------------------------------

#: UI actions that never require human approval. These read or
#: navigate but do not commit any state change to the remote server.
SAFE_ACTIONS: frozenset[str] = frozenset(
    {
        "navigate",
        "click_link",
        "read",
        "scroll",
        "search_field_fill",
        "finish",
    }
)

#: UI actions that write to the page but are still reversible without
#: human consequences (e.g. typing into a text field is erasable; a
#: file upload is visible to the user before submission).
MODERATE_ACTIONS: frozenset[str] = frozenset(
    {
        "type",
        "form_fill",
        "file_upload",
        "select_option",
    }
)

#: UI actions that commit state remotely or trigger irreversible side
#: effects. **Every one of these MUST go through the HITL gate** per
#: SPEC-043 AC-043-2 and CLAUDE.md Phase 41 rule #3.
HIGH_RISK_ACTIONS: frozenset[str] = frozenset(
    {
        "click_submit",
        "click_confirm",
        "click_delete",
        "click_pay",
        "click_send",
        "form_submit_final",
    }
)


class CuaAction(BaseModel):
    """Structured action emitted by the vision LLM.

    The ``action_type`` is an opaque string taxonomy shared between the
    VLM prompt, :data:`SAFE_ACTIONS`, :data:`MODERATE_ACTIONS` and
    :data:`HIGH_RISK_ACTIONS`. ``target`` identifies what to interact
    with (CSS selector, XPath, or plain-text label); ``value`` holds
    the payload for type / fill actions.

    Attributes:
        action_type: One of the strings in the action taxonomy.
        target: What to interact with.
        value: Optional payload for typing / filling / selecting.
        rationale: Short explanation from the VLM — logged for audit
            but never surfaced to the user.
    """

    action_type: str = Field(..., description="Action taxonomy string")
    target: str = Field(default="", description="Selector or label")
    value: str = Field(default="", description="Payload for type/fill")
    rationale: str = Field(default="", description="VLM reasoning")


def classify_risk(action: CuaAction) -> str:
    """Return ``'SAFE'``, ``'MODERATE'`` or ``'HIGH_RISK'`` for ``action``.

    Unknown action types are treated as ``'HIGH_RISK'`` by default
    because the safest assumption for an unclassified action is that
    it modifies server state. Callers can pre-validate by checking
    ``action.action_type`` against the three ``_ACTIONS`` frozensets
    directly if they need ternary logic without the string.
    """
    if action.action_type in HIGH_RISK_ACTIONS:
        return "HIGH_RISK"
    if action.action_type in SAFE_ACTIONS:
        return "SAFE"
    if action.action_type in MODERATE_ACTIONS:
        return "MODERATE"
    logger.warning(
        "cua.unknown_action_defaulting_high_risk",
        action_type=action.action_type,
    )
    return "HIGH_RISK"


# ---------------------------------------------------------------------------
# Playwright adapters (lazy — real impl wired at deployment time)
# ---------------------------------------------------------------------------


async def take_screenshot() -> bytes:
    """Capture a screenshot from the live Playwright browser.

    This is the default in-process implementation; tests patch it
    with an :class:`unittest.mock.AsyncMock`. Production deployments
    are expected to override it or provide their own wrapper around
    the project's Playwright manager — the call is gated by
    ``cua_enabled`` so the default path never runs unless a browser
    is actually available.

    Returns:
        Raw PNG bytes from the browser.
    """
    raise RuntimeError(
        "CUA take_screenshot is not wired to a Playwright browser. "
        "Patch this coroutine in your deployment or test fixture."
    )


async def execute_browser_action(
    action: CuaAction,  # noqa: ARG001 -- default stub, replaced at runtime
) -> str:
    """Execute a single :class:`CuaAction` against the live browser.

    Like :func:`take_screenshot`, this is a thin stub that callers
    replace at deployment time. Returns a short human-readable result
    string that the VLM reads on the next iteration.
    """
    raise RuntimeError(
        "CUA execute_browser_action is not wired to a Playwright browser. "
        "Patch this coroutine in your deployment or test fixture."
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

CUA_SYSTEM_PROMPT: str = """\
You are the Computer Use Agent (CUA). You drive a real browser via
Playwright to complete UI automation tasks for the user. You perceive
the page through screenshots and respond with ONE structured action
per turn.

## Output format (MANDATORY)

Respond with a single JSON object — no prose, no markdown fence:

    {
      "action_type": "<one of the taxonomy strings>",
      "target": "<CSS selector, XPath, or label>",
      "value": "<text to type or option value>",
      "rationale": "<one-sentence explanation>"
    }

## Action taxonomy

SAFE (no human approval needed):
- navigate, click_link, read, scroll, search_field_fill, finish

MODERATE (executes, but reversible):
- type, form_fill, file_upload, select_option

HIGH_RISK (requires HUMAN APPROVAL before execution):
- click_submit, click_confirm, click_delete, click_pay, click_send,
  form_submit_final

## Rules

1. Emit exactly ONE action per response.
2. When the task is complete, emit ``{"action_type": "finish", ...}``.
3. Never execute a HIGH_RISK action without being certain it matches
   the user's intent — you will be asked to confirm via HITL.
4. Prefer SAFE actions when both SAFE and MODERATE reach the goal.
5. Never fabricate selectors you did not see in a screenshot.
"""


_DISABLED_MESSAGE = (
    "CUA agent is disabled. Set `LIGHTAGENT_CUA_ENABLED=true` and "
    "configure `LIGHTAGENT_CUA_VISION_MODEL` with a vision-capable "
    "model (e.g. `claude-opus-4-6`) to enable it."
)
_NO_VISION_MODEL_MESSAGE = (
    "CUA agent cannot run without a vision-capable model. Set "
    "`LIGHTAGENT_CUA_VISION_MODEL` to a model string such as "
    "`claude-opus-4-6` and restart the process."
)


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------


def _parse_action(raw: str) -> CuaAction | None:
    """Parse a JSON blob emitted by the VLM into a :class:`CuaAction`.

    Tolerant to surrounding prose, markdown fences, and trailing
    commas. Returns ``None`` (not a raised exception) on any parse
    failure — the caller logs a warning and ends the loop gracefully.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Strip fenced code block.
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    # Find the first JSON object in the string to tolerate prose.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return CuaAction.model_validate(data)
    except Exception:  # pydantic validation failure is non-fatal
        return None


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def _audit_cua_action(
    *,
    action: CuaAction,
    risk_level: str,
    hitl_decision: str | None,
    screenshot: bytes,
    result: str,
    session_id: str,
) -> None:
    """Write an audit entry for a single CUA action (AC-043-5).

    Uses :meth:`AuditLogger.log_tool_call` so the entry slots into
    the existing hash-chained log without adding a new event type.
    The screenshot hash (SHA-256 of the PNG bytes) is attached so an
    investigator can correlate the audit entry with the actual frame
    captured at the time — the bytes themselves are never logged.
    """
    screenshot_hash = hashlib.sha256(screenshot).hexdigest() if screenshot else ""
    params: dict[str, object] = {
        "action_type": action.action_type,
        "target": action.target,
        "risk_level": risk_level,
        "hitl_decision": hitl_decision or "",
        "screenshot_hash": screenshot_hash,
        "session_id": session_id,
    }
    AuditLogger().log_tool_call(
        name="cua.action",
        params=params,
        result=result[:512],
        duration_ms=0,
    )


# ---------------------------------------------------------------------------
# Main node (AC-043-1)
# ---------------------------------------------------------------------------


async def cua_node(state: AgentState) -> dict[str, Any]:
    """Run the CUA perception → plan → (HITL) → execute → audit loop.

    The node is async and never raises — every failure mode (disabled,
    missing vision model, malformed VLM output, HITL rejection,
    Playwright crash) is mapped to a graceful :class:`AIMessage`
    describing the outcome so the supervisor can route it to END.

    Args:
        state: Current LangGraph shared state.

    Returns:
        Partial state update with the final ``AIMessage`` appended
        under ``messages`` and ``current_agent="cua"``.
    """
    settings = get_settings()
    session_id = str(state.get("session_id", "unknown"))

    # -------- Gate 1: enablement check (AC-043-3) ------------------------
    if not settings.cua_enabled:
        logger.info("cua.disabled_by_setting")
        return {
            "current_agent": "cua",
            "messages": [AIMessage(content=_DISABLED_MESSAGE)],
        }
    if not settings.cua_vision_model:
        logger.warning("cua.no_vision_model_configured")
        return {
            "current_agent": "cua",
            "messages": [AIMessage(content=_NO_VISION_MODEL_MESSAGE)],
        }

    registry = ProviderRegistry()
    get_vision_llm = getattr(registry, "get_vision_llm", None)
    if get_vision_llm is None:
        logger.warning("cua.provider_registry_missing_vision_support")
        return {
            "current_agent": "cua",
            "messages": [AIMessage(content=_NO_VISION_MODEL_MESSAGE)],
        }

    try:
        vision_llm = get_vision_llm(model=settings.cua_vision_model)
    except Exception as exc:
        logger.warning("cua.vision_llm_init_failed", error=str(exc))
        return {
            "current_agent": "cua",
            "messages": [
                AIMessage(
                    content=(
                        f"CUA could not initialise the vision model "
                        f"'{settings.cua_vision_model}': {exc}"
                    )
                )
            ],
        }

    max_actions = settings.cua_max_actions
    screenshot_delay = settings.cua_screenshot_interval_ms / 1000.0
    working_messages: list[Any] = list(state["messages"][-4:])
    final_reply: str | None = None
    executed_actions = 0

    for iteration in range(max_actions):
        # -------- Perception ---------------------------------------------
        try:
            screenshot = await take_screenshot()
        except Exception as exc:
            logger.warning("cua.screenshot_failed", error=str(exc))
            final_reply = f"CUA could not capture a screenshot from the browser: {exc}"
            break

        # -------- Plan ----------------------------------------------------
        perception_messages = [
            SystemMessage(content=CUA_SYSTEM_PROMPT),
            *working_messages,
            HumanMessage(
                content=(
                    f"Screenshot captured (PNG, {len(screenshot)} bytes). "
                    f"Iteration {iteration + 1}/{max_actions}. "
                    f"Emit the next action as JSON."
                )
            ),
        ]
        response = await vision_llm.ainvoke(perception_messages)
        raw = str(getattr(response, "content", ""))
        action = _parse_action(raw)
        if action is None:
            logger.warning(
                "cua.unparseable_vlm_response",
                iteration=iteration,
                preview=raw[:120],
            )
            final_reply = (
                "CUA received an unparseable response from the vision model and stopped the loop."
            )
            break

        working_messages.append(AIMessage(content=raw))

        # -------- Finish short-circuit -----------------------------------
        if action.action_type == "finish":
            _audit_cua_action(
                action=action,
                risk_level="SAFE",
                hitl_decision=None,
                screenshot=screenshot,
                result="finish",
                session_id=session_id,
            )
            final_reply = (
                action.rationale
                or f"CUA finished the task after {executed_actions} executed action(s)."
            )
            logger.info(
                "cua.finished",
                iteration=iteration,
                executed_actions=executed_actions,
            )
            break

        # -------- Gate 2: risk classification (AC-043-2) -----------------
        risk = classify_risk(action)
        hitl_decision: str | None = None

        if risk == "HIGH_RISK":
            # HITL pause — mandatory per CLAUDE.md Phase 41 rule #3.
            logger.info(
                "cua.hitl_requested",
                action_type=action.action_type,
                target=action.target,
            )
            decision_payload = interrupt(
                {
                    "action_type": action.action_type,
                    "target": action.target,
                    "value": action.value,
                    "rationale": action.rationale,
                    "risk_level": "HIGH_RISK",
                    "screenshot_bytes_len": len(screenshot),
                    "question": (f"Approve {action.action_type} on '{action.target}'?"),
                }
            )
            hitl_decision = str(
                (decision_payload or {}).get("action", "reject")
                if isinstance(decision_payload, dict)
                else "reject"
            )
            if hitl_decision != "approve":
                _audit_cua_action(
                    action=action,
                    risk_level=risk,
                    hitl_decision=hitl_decision,
                    screenshot=screenshot,
                    result=f"rejected_by_hitl:{hitl_decision}",
                    session_id=session_id,
                )
                final_reply = (
                    f"CUA was denied human approval for "
                    f"'{action.action_type}' on '{action.target}'. "
                    f"Loop stopped after {executed_actions} executed "
                    f"action(s)."
                )
                logger.info(
                    "cua.hitl_rejected",
                    decision=hitl_decision,
                    action_type=action.action_type,
                )
                break

        # -------- Gate 3: execute + audit (AC-043-5) ---------------------
        try:
            result = await execute_browser_action(action)
        except Exception as exc:
            _audit_cua_action(
                action=action,
                risk_level=risk,
                hitl_decision=hitl_decision,
                screenshot=screenshot,
                result=f"exception:{exc}",
                session_id=session_id,
            )
            logger.warning(
                "cua.execute_failed",
                action_type=action.action_type,
                error=str(exc),
            )
            final_reply = (
                f"CUA failed while executing '{action.action_type}' on '{action.target}': {exc}"
            )
            break

        _audit_cua_action(
            action=action,
            risk_level=risk,
            hitl_decision=hitl_decision,
            screenshot=screenshot,
            result=result,
            session_id=session_id,
        )
        executed_actions += 1
        working_messages.append(
            HumanMessage(
                content=(f"Action '{action.action_type}' executed. Result: {result[:200]}")
            )
        )

        if screenshot_delay > 0:
            await asyncio.sleep(screenshot_delay)

    if final_reply is None:
        final_reply = (
            f"CUA reached max_actions={max_actions} without emitting "
            f"a 'finish' action. Executed {executed_actions} action(s) "
            f"successfully."
        )
        logger.warning(
            "cua.max_actions_reached",
            limit=max_actions,
            executed_actions=executed_actions,
        )

    return {
        "current_agent": "cua",
        "messages": [AIMessage(content=final_reply)],
    }


__all__ = [
    "CUA_SYSTEM_PROMPT",
    "HIGH_RISK_ACTIONS",
    "MODERATE_ACTIONS",
    "SAFE_ACTIONS",
    "CuaAction",
    "_audit_cua_action",
    "_parse_action",
    "classify_risk",
    "cua_node",
    "execute_browser_action",
    "take_screenshot",
]
