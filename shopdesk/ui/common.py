"""Shared objects handed to every page, plus small formatting helpers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..defense import AllowList, Defense
from ..llm import LLM
from ..store import Store
from ..tools import Registry
from ..agent import ShopDeskAgent

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

BANNER = ("⚠️ **Teaching demo.** Every customer, order and ticket here is invented. "
          "Refunds, discount codes and emails are only pretend. Nothing real is reachable.")

CSS = """
.trace { font-size: 1.1rem; line-height: 1.45; }
.trace pre, .trace code { font-size: 0.95rem; white-space: pre-wrap; }
.inbox td, .inbox th { font-size: 1rem; white-space: normal !important; vertical-align: top; word-break: normal !important; }
footer { display: none !important; }
"""


@dataclass
class AppContext:
    settings: Settings
    store: Store
    llm: LLM
    registry: Registry
    allowlist: AllowList

    def agent(self, level: int, defense_on: bool) -> ShopDeskAgent:
        return ShopDeskAgent(self.llm, self.store, self.registry, self.settings,
                             level=level, defense=Defense(defense_on, self.allowlist, self.store))


def snippet(text: str, n: int = 140) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"
