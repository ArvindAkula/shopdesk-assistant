"""Tool implementations. All synthetic. Refunds, discount codes and emails never leave the demo -
they write to the trace and a local table so the audience can see exactly what *would* have happened."""
from __future__ import annotations

import json
from typing import Any

from .registry import ToolContext


def lookup_order(ctx: ToolContext, inputs: dict[str, Any]) -> str:
    oid = str(inputs.get("order_id", "")).strip()
    rec = ctx.store.get_order(oid)
    if not rec:
        ctx.trace.add("tool", f"lookup_order({oid}) → not found")
        return f"No order with ID {oid}."
    # The "note" field labels each record as synthetic for humans reading data/customers.json.
    # It is withheld from the model so the demo behaves like a real lookup.
    visible = {k: v for k, v in rec.items() if k != "note"}
    ctx.trace.add("tool", f"lookup_order({oid}) → opened order for {rec['customer']['name']}",
                  json.dumps(visible, indent=1))
    return json.dumps(visible)


def _amount(inputs: dict[str, Any], key: str) -> float | None:
    try:
        return round(float(str(inputs.get(key, "")).replace("$", "").replace("%", "").strip()), 2)
    except ValueError:
        return None


def issue_refund(ctx: ToolContext, inputs: dict[str, Any]) -> str:
    oid = str(inputs.get("order_id", "")).strip().upper()
    amount = _amount(inputs, "amount")
    reason = str(inputs.get("reason", "")).strip()
    if ctx.store.get_order(oid) is None:
        ctx.trace.add("tool", f"issue_refund({oid}) → no such order")
        return f"No order with ID {oid}."
    if amount is None or amount <= 0:
        return "Invalid refund amount."
    ctx.store.log_refund(oid, amount, reason)
    ctx.trace.add("refund", f"SIMULATED issue_refund → ${amount:,.2f} on order {oid}", f"Reason: {reason}")
    return f"Refund of ${amount:,.2f} issued on order {oid} (simulated - no money moved)."


def apply_discount(ctx: ToolContext, inputs: dict[str, Any]) -> str:
    email = str(inputs.get("customer_email", "")).strip()
    pct = _amount(inputs, "percent")
    reason = str(inputs.get("reason", "")).strip()
    if pct is None or not 0 < pct <= 100:
        return "Invalid discount percent."
    code = ctx.store.create_discount(email, pct, reason)
    ctx.trace.add("refund", f"SIMULATED apply_discount → {pct:g}% code {code} for {email}", f"Reason: {reason}")
    return f"Discount code {code} ({pct:g}% off) attached to {email} (simulated)."


def send_email(ctx: ToolContext, inputs: dict[str, Any]) -> str:
    to = str(inputs.get("to", "")).strip()
    subject = str(inputs.get("subject", "")).strip()
    body = str(inputs.get("body", ""))
    ctx.store.log_email(to, subject, body)
    ctx.trace.add("email", f"SIMULATED send_email → {to}", f"Subject: {subject}\n\n{body}")
    return f"Email sent to {to} (simulated - nothing actually left the demo)."


def save_note(ctx: ToolContext, inputs: dict[str, Any]) -> str:
    text = str(inputs.get("text", "")).strip()
    if not text:
        return "Empty note ignored."
    ctx.store.add_note(ctx.author, text)
    ctx.trace.add("memory", "save_note → written to shared memory (will be read by every future run)", text)
    return "Note saved to shared team memory."
