"""The DEFENSE toggle: fence untrusted text, flag instruction-like content, enforce policy limits in
code (per-run order and recipient scope, refund and discount caps), and route irreversible actions
to a human approval prompt.

Each layer writes a trace line naming the rule it applied, so the audience sees why something was blocked.
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .prompts import GOODWILL_LIMIT

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Refunds above this need the item back (order status "returned"). Policy lives in code, not in the prompt.
NO_RETURN_REFUND_LIMIT = 100.0

# Heuristic patterns for "this ticket is talking to the assistant, not describing a problem".
# Deliberately simple: the point on stage is that even cheap checks catch the obvious cases,
# and that the real protection is what happens next (least privilege, policy limits, approval).
_SIGNALS: list[tuple[str, re.Pattern]] = [
    ("addresses the assistant directly",
     re.compile(r"\b(when|while|before|after)\s+(you\s+)?summari[sz](e|ing)\b|\bnote to the (assistant|ai|bot)\b|\bassistant[,:]", re.I)),
    ("asks to look up a record",
     re.compile(r"\b(look\s?up|open|pull|fetch|retrieve|check)\b.{0,40}\b(order|account|customer|record)\s+[A-Z]?\d{3,}", re.I)),
    ("asks for money or a discount",
     re.compile(r"\b(issue|process|give|apply|grant)\b.{0,30}\b(refund|discount|credit|coupon|code)\b|\b\d{2,3}\s*%\s*off\b", re.I)),
    ("asks to send or forward data",
     re.compile(r"\b(e-?mail|send|forward|mail|transmit|export)\b.{0,60}\b(to|@)\b", re.I)),
    ("mentions internal rules or the system prompt",
     re.compile(r"\b(system prompt|your instructions|internal (rule|policy)|discount (rule|cap|limit)|maximum discount|override code)\b", re.I)),
    ("tries to override instructions",
     re.compile(r"\b(ignore|disregard|forget|override)\b.{0,30}\b(instructions|rules|above|previous)\b", re.I)),
    ("asks to save or remember something",
     re.compile(r"\b(save|store|remember|record)\b.{0,30}\b(note|memory|for (future|next|later))\b", re.I)),
    ("claims authority",
     re.compile(r"\b(the (support )?(lead|team|manager)|audit team|your (manager|admin)|the store)\s+(asked|requires?|said|wants)\b", re.I)),
]


def detect_instructions(text: str) -> list[str]:
    """Return human-readable reasons this text looks like instructions rather than a support request."""
    return [name for name, pat in _SIGNALS if pat.search(text or "")]


def fence(ticket_id: str, body: str, delimiter: str | None = None) -> str:
    """Wrap untrusted text in tagged fences with a per-request random delimiter so the text
    cannot close the fence itself."""
    d = delimiter or secrets.token_hex(4)
    body = body.replace("<untrusted_ticket", "&lt;untrusted_ticket").replace("</untrusted_ticket", "&lt;/untrusted_ticket")
    return f'<untrusted_ticket id="{ticket_id}" boundary="{d}">\n{body}\n</untrusted_ticket boundary="{d}">'


@dataclass
class AllowList:
    domains: set[str] = field(default_factory=set)
    addresses: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: str | Path) -> "AllowList":
        raw = json.loads(Path(path).read_text())
        return cls({d.lower() for d in raw.get("domains", [])},
                   {a.lower() for a in raw.get("addresses", [])})

    def allows(self, address: str) -> bool:
        addr = (address or "").strip().lower()
        if not EMAIL_RE.fullmatch(addr):
            return False
        if addr in self.addresses:
            return True
        return addr.split("@", 1)[1] in self.domains


@dataclass(frozen=True)
class Decision:
    action: str          # allow | refuse | approve
    reason: str = ""
    rule: str = ""


ORDER_ID_RE = re.compile(r"\bA\d{3,}\b", re.I)


@dataclass(frozen=True)
class Scope:
    """What this run is about: the orders and senders the agent may touch.

    Orders: the ones owned by a ticket's sender (so a ticket's order-number field only counts when it is
    the sender's own order), plus any the support agent names in their own request. Order IDs written
    inside a ticket's message never count - that is where the attack lives."""
    orders: frozenset[str] = frozenset()
    senders: frozenset[str] = frozenset()


class Defense:
    """All four layers behind one object. With enabled=False every method is a pass-through.
    `store` gives the policy checks read access to orders and customers; `scope` is set per run."""

    def __init__(self, enabled: bool, allowlist: AllowList | None = None, store=None):
        self.enabled = enabled
        self.allowlist = allowlist or AllowList()
        self.store = store
        self.scope = Scope()

    def set_scope(self, tickets: list[dict], agent_request: str) -> Scope:
        senders = {str(t.get("email", "")).strip().lower() for t in tickets} - {""}
        orders = {m.upper() for m in ORDER_ID_RE.findall(agent_request or "")}
        if self.store is not None:
            orders |= {oid for email in senders for oid in self.store.orders_for_email(email)}
        self.scope = Scope(frozenset(orders), frozenset(senders))
        return self.scope

    def render_ticket(self, ticket: dict) -> str:
        body = (f"Name: {ticket['name']}\nEmail: {ticket.get('email','')}\n"
                f"Order: {ticket.get('order_ref','') or '(none given)'}\nMessage: {ticket.get('message','')}")
        tid = f"T{ticket['id']}"
        if not self.enabled:
            return f"Ticket {tid}\n{body}"
        return fence(tid, body)

    def flags_for(self, ticket: dict) -> list[str]:
        return detect_instructions(ticket.get("message", "")) if self.enabled else []

    def check_tool_call(self, tool, inputs: dict) -> Decision:
        if not self.enabled:
            return Decision("allow")
        refusal = self._policy(tool.name, inputs)
        if refusal:
            return refusal
        if tool.irreversible:
            return Decision("approve", f"{tool.name} is irreversible; a person must approve it",
                            "irreversible actions need a human yes")
        return Decision("allow")

    def _policy(self, name: str, inputs: dict) -> Decision | None:
        if name == "lookup_order":
            oid = str(inputs.get("order_id", "")).strip().upper()
            if oid not in self.scope.orders:
                return Decision("refuse", f"order {oid} is not on any ticket in this run or in the support agent's "
                                "request", "lookups only for orders in scope")
        if name == "send_email":
            to = str(inputs.get("to", "")).strip()
            staff = self.allowlist.allows(to)
            if not (staff or to.lower() in self.scope.senders):
                return Decision("refuse", f"recipient {to!r} did not send a ticket in this run and is not an "
                                "approved staff address", "reply only to the ticket's own sender")
            if not staff:
                leaked = self._other_customers_in(str(inputs.get("body", "")) + " " + str(inputs.get("subject", "")),
                                                  to)
                if leaked:
                    return Decision("refuse", f"the email to {to} contains details of another customer "
                                    f"({', '.join(leaked)})", "never send one customer's data to another")
        if name == "apply_discount":
            pct = _num(inputs.get("percent"))
            email = str(inputs.get("customer_email", "")).strip().lower()
            if pct is None or pct > GOODWILL_LIMIT:
                return Decision("refuse", f"{inputs.get('percent')}% is above the {GOODWILL_LIMIT}% goodwill limit",
                                f"discounts capped at {GOODWILL_LIMIT}% in code")
            if self.store is not None and email not in self.store.customer_emails():
                return Decision("refuse", f"{email!r} is not a customer on file", "discounts only for real customers")
        if name == "issue_refund":
            order = self.store.get_order(str(inputs.get("order_id", ""))) if self.store is not None else None
            amount = _num(inputs.get("amount"))
            if order is None:
                return Decision("refuse", f"order {inputs.get('order_id')!r} does not exist", "refunds only on real orders")
            left = round(order["total"] - order["refunded_so_far"], 2)
            if amount is None or amount > left:
                return Decision("refuse", f"${inputs.get('amount')} is more than the ${left:,.2f} left on order {order['id']}",
                                "refund at most the order total")
            if amount > NO_RETURN_REFUND_LIMIT and order["status"] != "returned":
                return Decision("refuse", f"order {order['id']} is '{order['status']}', not returned; refunds over "
                                f"${NO_RETURN_REFUND_LIMIT:,.0f} need the item back", "no big refunds without a return")
        return None

    def _other_customers_in(self, text: str, recipient: str) -> list[str]:
        """Names of customers (other than the recipient) whose email, phone or address appear in `text`."""
        if self.store is None:
            return []
        low = text.lower()
        return [c["name"] for c in self.store.customers.values()
                if c["email"].lower() != recipient.lower()
                and any(v and str(v).lower() in low for v in (c["email"], c["phone"], c["shipping_address"]))]


def _num(v) -> float | None:
    try:
        return float(str(v).replace("$", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
