"""Deterministic eval toolkit — an I/O-free order ledger (TKE9-04).

Selected per case with ``setup: {tool_provider: eval_toolkit}``. Every agent
gets the same two tools. ``get_order`` records are verbose on purpose (line
items, shipping history, notes), so a multi-lookup task builds the long,
observation-heavy context the token-efficiency controls target. Everything is
derived from the order id: runs are reproducible and need no network.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

ORDER_IDS: tuple[str, ...] = tuple(f"ORD-{n:03d}" for n in range(1, 13))

_SKUS = ("cable", "adapter", "monitor", "keyboard", "dock", "charger", "headset", "webcam")
_ITEMS_PER_ORDER = 14
_EVENTS = ("label_created", "picked_up", "in_transit", "out_for_delivery")


def _number(order_id: str) -> int:
    return int(order_id.removeprefix("ORD-"))


def _items(order_id: str) -> list[dict[str, Any]]:
    n = _number(order_id)
    return [
        {
            "line": i + 1,
            "sku": f"{_SKUS[(n + i) % len(_SKUS)]}-{(n * 7 + i) % 100:02d}",
            "quantity": 1 + (n + i) % 3,
            "unit_price": 5 + (n * 3 + i * 11) % 40,
            "warehouse": f"WH-{(n + i) % 4 + 1}",
            "note": "packed and verified against the picking list",
        }
        for i in range(_ITEMS_PER_ORDER)
    ]


def order_total(order_id: str) -> int:
    """The order's total (sum of quantity * unit price): the expected answer."""
    return sum(item["quantity"] * item["unit_price"] for item in _items(order_id))


def _record(order_id: str) -> dict[str, Any]:
    n = _number(order_id)
    return {
        "order_id": order_id,
        "customer": f"CUST-{(n * 13) % 50:03d}",
        "status": ("shipped", "delivered", "processing")[n % 3],
        "currency": "USD",
        "items": _items(order_id),
        "shipping_history": [
            {"event": event, "hub": f"HUB-{(n + k) % 6 + 1}", "day": k + 1}
            for k, event in enumerate(_EVENTS)
        ],
        "notes": "Customer requested a single consolidated shipment; no substitutions.",
        "total": order_total(order_id),
    }


def _list_orders() -> str:
    return json.dumps(list(ORDER_IDS))


def _get_order(order_id: str) -> str:
    if order_id not in ORDER_IDS:
        return f"unknown order {order_id!r}; call list_orders for valid ids"
    return json.dumps(_record(order_id), indent=1)


# Chained ledger (``tool_provider: eval_toolkit_chain``): each record names the
# next order of its chain, so lookups cannot be batched — one loop turn per hop.
_LINKS: dict[str, str | None] = {
    "ORD-001": "ORD-005",
    "ORD-005": "ORD-009",
    "ORD-009": "ORD-002",
    "ORD-002": "ORD-011",
    "ORD-011": None,
    "ORD-003": "ORD-008",
    "ORD-008": "ORD-012",
    "ORD-012": "ORD-006",
    "ORD-006": None,
    "ORD-004": "ORD-010",
    "ORD-010": "ORD-007",
    "ORD-007": None,
}
CHAIN_STARTS: tuple[str, ...] = ("ORD-001", "ORD-003", "ORD-004")


def chain_from(start: str) -> list[str]:
    """The order ids of the chain beginning at *start*, in hop order."""
    chain: list[str] = []
    current: str | None = start
    while current is not None:
        chain.append(current)
        current = _LINKS[current]
    return chain


def _get_linked_order(order_id: str) -> str:
    if order_id not in _LINKS:
        return f"unknown order {order_id!r}; follow linked_order from a known order"
    return json.dumps({**_record(order_id), "linked_order": _LINKS[order_id]}, indent=1)


def eval_toolkit_chain_tools() -> list[BaseTool]:
    """A fresh ``get_linked_order`` tool (no state, no I/O)."""
    return [
        StructuredTool.from_function(
            _get_linked_order,
            name="get_linked_order",
            description=(
                "Get one order's full record by id. The record's linked_order field "
                "names the next order of its chain (null at the end)."
            ),
        )
    ]


def eval_toolkit_tools() -> list[BaseTool]:
    """Fresh ``list_orders`` / ``get_order`` tools (no state, no I/O)."""
    return [
        StructuredTool.from_function(
            _list_orders, name="list_orders", description="List every order id in the ledger."
        ),
        StructuredTool.from_function(
            _get_order,
            name="get_order",
            description="Get one order's full record (items, shipping history, total) by id.",
        ),
    ]
