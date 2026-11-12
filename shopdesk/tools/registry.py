"""Tool registry. Each tool carries a minimum LEVEL; `filter_for(level)` is the whole
least-privilege story: the model never even sees a tool above the current level.

  Level 1  just talking     - no tools
  Level 2  it can read      - lookup_order
  Level 3  it can act       - issue_refund, apply_discount, send_email (all simulated)
  Level 4  it remembers     - save_note (+ notes are read into context, see memory.py)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..store import Store
from ..trace import Trace


@dataclass
class ToolContext:
    store: Store
    trace: Trace
    level: int
    author: str = "ShopDesk Assistant"


ToolFn = Callable[[ToolContext, dict[str, Any]], str]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    min_level: int
    irreversible: bool
    fn: ToolFn

    def run(self, ctx: ToolContext, inputs: dict[str, Any]) -> str:
        return self.fn(ctx, inputs)


@dataclass
class Registry:
    tools: list[Tool] = field(default_factory=list)

    def register(self, tool: Tool) -> None:
        self.tools.append(tool)

    def get(self, name: str) -> Tool | None:
        return next((t for t in self.tools if t.name == name), None)

    def filter_for(self, level: int) -> list[Tool]:
        return [t for t in self.tools if t.min_level <= level]

    def table(self) -> list[tuple[str, int, bool]]:
        return [(t.name, t.min_level, t.irreversible) for t in self.tools]


def build_registry() -> Registry:
    from . import impl  # noqa: WPS433  (local import avoids a cycle)

    reg = Registry()
    reg.register(Tool(
        name="lookup_order",
        description="Look up an order by its ID (e.g. A1019). Returns the item, total, status, amount "
                    "already refunded, and the customer's name, email, phone and shipping address.",
        input_schema={"type": "object", "properties": {"order_id": {"type": "string", "description": "Order ID, e.g. A1019"}},
                      "required": ["order_id"], "additionalProperties": False},
        min_level=2, irreversible=False, fn=impl.lookup_order))
    reg.register(Tool(
        name="issue_refund",
        description="Refund money to the original payment method for an order.",
        input_schema={"type": "object", "properties": {
            "order_id": {"type": "string"}, "amount": {"type": "number", "description": "US dollars"},
            "reason": {"type": "string"}},
            "required": ["order_id", "amount", "reason"], "additionalProperties": False},
        min_level=3, irreversible=True, fn=impl.issue_refund))
    reg.register(Tool(
        name="apply_discount",
        description="Create a single-use discount code for a customer and attach it to their account.",
        input_schema={"type": "object", "properties": {
            "customer_email": {"type": "string"}, "percent": {"type": "number"}, "reason": {"type": "string"}},
            "required": ["customer_email", "percent", "reason"], "additionalProperties": False},
        min_level=3, irreversible=True, fn=impl.apply_discount))
    reg.register(Tool(
        name="send_email",
        description="Send an email on behalf of the support team.",
        input_schema={"type": "object", "properties": {
            "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
            "required": ["to", "subject", "body"], "additionalProperties": False},
        min_level=3, irreversible=True, fn=impl.send_email))
    reg.register(Tool(
        name="save_note",
        description="Save a note to the shared team memory. Notes are shown to the assistant in all "
                    "future sessions for every support agent.",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}},
                      "required": ["text"], "additionalProperties": False},
        min_level=4, irreversible=True, fn=impl.save_note))
    return reg
