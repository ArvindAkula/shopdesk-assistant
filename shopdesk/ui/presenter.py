"""The support agent view: the inbox, the level selector, the DEFENSE toggle, the approval prompt
and the big trace panel. This is the whole app."""
from __future__ import annotations

import json

import gradio as gr

from ..config import MAX_LEVEL
from ..prompts import DEFAULT_AGENT_REQUEST
from .common import BANNER, DATA_DIR, AppContext, snippet

LEVEL_LABELS = {1: "1 · Just talking", 2: "2 · It can read", 3: "3 · It can act", 4: "4 · It remembers"}


INBOX = "inbox"


def _ticket_rows(ctx: AppContext):
    return [[f"T{t['id']}" + (" ☠️" if t["poisoned"] else "") + (f" L{t['example_level']}" if t["example_level"] else ""),
             t["name"], t["order_ref"] or "—", t["message"], t["status"]]
            for t in ctx.store.list_tickets()]


def _run_choices(ctx: AppContext):
    """'Run on' options: the whole inbox, each one-level example, or any single ticket."""
    choices = [("Today's inbox (every ticket except the level examples)", INBOX)]
    choices += [(f"T{t['id']} · Level {t['example_level']} example — {t['name']}", str(t["id"]))
                for t in ctx.store.example_tickets()]
    choices += [(f"T{t['id']} only — {t['name']}", str(t["id"])) for t in ctx.store.inbox_tickets()]
    return choices


def _run_on_update(ctx: AppContext, current: str | None):
    values = {v for _, v in _run_choices(ctx)}
    return gr.update(choices=_run_choices(ctx), value=current if current in values else INBOX)


def _tickets_for(ctx: AppContext, target: str | None) -> list[dict]:
    if not target or target == INBOX:
        return ctx.store.inbox_tickets()
    t = ctx.store.get_ticket(int(target))
    return [t] if t else ctx.store.inbox_tickets()


def _side_panel(ctx: AppContext) -> str:
    refunds = ctx.store.list_refunds(5)
    discounts = ctx.store.list_discounts(5)
    emails = ctx.store.list_emails(5)
    notes = ctx.store.list_notes(10)
    out = ["### Simulated refunds & discount codes"]
    money = [f"- 💸 **${r['amount']:,.2f}** refunded on {r['order_id']}" for r in refunds]
    money += [f"- 🏷️ **{d['percent']:g}% off** `{d['code']}` → {d['customer_email']}" for d in discounts]
    out += money or ["_none_"]
    out += ["", "### Simulated outbox"]
    out += [f"- **→ {e['to_addr']}** · {snippet(e['subject'], 60)}" for e in emails] or ["_empty_"]
    out += ["", "### Shared memory (level 4)"]
    out += [f"- {snippet(n['text'], 160)}" for n in notes] or ["_empty_"]
    return "\n".join(out)


def _view(run):
    pending = run.pending
    approval_text = ""
    if pending:
        approval_text = (f"**The assistant wants to run `{pending.name}`:**\n\n```json\n"
                         f"{json.dumps(pending.input, indent=1)[:1500]}\n```")
    status = f"**{run.summary_line()}**"
    if run.flags:
        status += "\n\n🛡️ Flagged tickets: " + ", ".join(f"{k} ({len(v)} signals)" for k, v in run.flags.items())
    summary = run.final_text or ("_Waiting for approval…_" if pending else "_(no text)_")
    return summary, run.trace.to_markdown(), gr.update(visible=bool(pending)), approval_text, status


def render(ctx: AppContext) -> None:
    s = ctx.settings
    gr.Markdown("# ShopDesk Assistant — support agent view")
    gr.Markdown(BANNER)
    run_state = gr.State(None)

    # Inbox spans the full width so the messages (where the attack lives) are readable from the back row.
    tickets_df = gr.Dataframe(value=_ticket_rows(ctx), headers=["ID", "Customer", "Order", "Message", "Status"],
                              interactive=False, label="Support inbox (L1–L4 marks the one-level examples)", wrap=True,
                              column_widths=["100px", "170px", "80px", "auto", "90px"], max_height=900,
                              elem_classes=["inbox"])
    with gr.Row():
        with gr.Column(scale=5):
            with gr.Accordion("Add a ticket", open=False):
                a_name = gr.Textbox(label="Name"); a_email = gr.Textbox(label="Email")
                a_order = gr.Textbox(label="Order number"); a_msg = gr.Textbox(label="Message", lines=3)
                a_btn = gr.Button("Add to inbox")
            backup_btn = gr.Button("Load backup tickets (discount-rule attempts)", size="sm")
            request = gr.Textbox(value=DEFAULT_AGENT_REQUEST, label="Support agent's request", lines=2)
            run_on = gr.Dropdown(choices=_run_choices(ctx), value=INBOX, label="Run on",
                                 info="A level example also selects its level.")
            with gr.Row():
                level = gr.Radio(choices=[(LEVEL_LABELS[i], i) for i in range(1, MAX_LEVEL + 1)],
                                 value=s.level, label="LEVEL — what the assistant is allowed to do",
                                 interactive=True)
                defense = gr.Checkbox(value=s.defense, label="DEFENSE")
            with gr.Row():
                run_btn = gr.Button("Summarise today's tickets", variant="primary", scale=3)
                reset_btn = gr.Button("Reset refunds, outbox & memory", scale=1)
            status_md = gr.Markdown("")
            side_md = gr.Markdown(_side_panel(ctx))
        with gr.Column(scale=6):
            # Both panels scroll inside a fixed height so a long run doesn't stretch the page.
            summary_md = gr.Markdown("_Summary appears here._", label="Assistant output", max_height="45vh",
                                     elem_classes=["panel"])
            with gr.Row(visible=False) as approval_row:
                with gr.Column():
                    approval_md = gr.Markdown("")
                    with gr.Row():
                        approve_btn = gr.Button("✅ Approve", variant="primary")
                        deny_btn = gr.Button("⛔ Deny", variant="stop")
            gr.Markdown("### Trace")
            trace_md = gr.Markdown("_No steps yet._", max_height="75vh", elem_classes=["trace", "panel"])

    outputs = [summary_md, trace_md, approval_row, approval_md, status_md, run_state, tickets_df, side_md]

    def do_run(req, lvl, dfn, target):
        agent = ctx.agent(int(lvl), bool(dfn))
        run = agent.run(_tickets_for(ctx, target), req or DEFAULT_AGENT_REQUEST)
        return (*_view(run), run, _ticket_rows(ctx), _side_panel(ctx))

    def on_target(target):
        t = ctx.store.get_ticket(int(target)) if target and target != INBOX else None
        return gr.update(value=t["example_level"]) if t and t["example_level"] else gr.update()

    def do_resume(run, approved: bool):
        if run is None or run.pending is None:
            return (gr.update(), gr.update(), gr.update(visible=False), "", gr.update(), run, _ticket_rows(ctx), _side_panel(ctx))
        agent = ctx.agent(run.level, run.defense)
        run = agent.resume(run, approved)
        return (*_view(run), run, _ticket_rows(ctx), _side_panel(ctx))

    def do_add(name, email, order, msg, target):
        if name.strip():
            ctx.store.add_ticket(name.strip(), email.strip(), order.strip(), msg.strip(), source="presenter")
        return _ticket_rows(ctx), "", "", "", "", _run_on_update(ctx, target)

    def do_backup(target):
        n = ctx.store.load_extra_tickets(DATA_DIR / "backup_tickets.json")
        return (_ticket_rows(ctx), (f"_Added {n} backup ticket(s)._" if n else "_Backup tickets already in the inbox._"),
                _run_on_update(ctx, target))

    def do_reset(target):
        ctx.store.reset_demo()
        return _ticket_rows(ctx), _side_panel(ctx), "_Reset done._", _run_on_update(ctx, target)

    run_on.change(on_target, [run_on], [level])
    run_btn.click(do_run, [request, level, defense, run_on], outputs)
    approve_btn.click(lambda r: do_resume(r, True), [run_state], outputs)
    deny_btn.click(lambda r: do_resume(r, False), [run_state], outputs)
    a_btn.click(do_add, [a_name, a_email, a_order, a_msg, run_on], [tickets_df, a_name, a_email, a_order, a_msg, run_on])
    reset_btn.click(do_reset, [run_on], [tickets_df, side_md, status_md, run_on])
    backup_btn.click(do_backup, [run_on], [tickets_df, status_md, run_on])
