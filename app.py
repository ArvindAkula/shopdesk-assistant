"""Entry point: the ShopDesk support agent view (inbox, level selector, DEFENSE toggle, trace).

Run your own copy with your own API key in .env; the inbox, memory and simulated side effects belong
to whoever runs the app.
"""
from __future__ import annotations

import os

from shopdesk.config import load_dotenv, load_settings

load_dotenv()

import gradio as gr  # noqa: E402

from shopdesk.defense import AllowList
from shopdesk.llm import make_llm
from shopdesk.store import Store
from shopdesk.tools import build_registry
from shopdesk.ui import presenter
from shopdesk.ui.common import CSS, DATA_DIR, AppContext


def build_context() -> AppContext:
    settings = load_settings()
    store = Store(settings.db_path)
    store.load_customers(DATA_DIR / "customers.json")
    store.seed_tickets(DATA_DIR / "seed_tickets.json", settings.exfil_address)
    return AppContext(
        settings=settings, store=store, llm=make_llm(settings), registry=build_registry(),
        allowlist=AllowList.load(DATA_DIR / "email_allowlist.json"),
    )


def build_app(ctx: AppContext) -> gr.Blocks:
    s = ctx.settings
    with gr.Blocks(title="ShopDesk Assistant") as demo:
        presenter.render(ctx)
    demo.queue(default_concurrency_limit=s.queue_concurrency, max_size=s.queue_max_size)
    return demo


ctx = build_context()
demo = build_app(ctx)
print(f"[shopdesk] level={ctx.settings.level} provider={ctx.llm.label} defense={ctx.settings.defense} "
      f"db={ctx.settings.db_path}")

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", "7860")), show_error=True, css=CSS)
