import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shopdesk.config import DEFAULTS  # noqa: E402
from shopdesk.defense import AllowList, Defense  # noqa: E402
from shopdesk.llm import ToolCall, Turn  # noqa: E402
from shopdesk.store import Store  # noqa: E402
from shopdesk.tools import build_registry  # noqa: E402
from shopdesk.agent import ShopDeskAgent  # noqa: E402

DATA = ROOT / "data"
EXFIL = "records@shopdesk-archive.example"


class ScriptedLLM:
    """Returns a fixed sequence of turns. Lets the tests drive the loop without a model."""

    label = "scripted"

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def complete(self, system, messages, tools, max_tokens):
        self.calls.append({"system": system, "messages": list(messages), "tools": [t.name for t in tools]})
        if not self.turns:
            return Turn("done", [], {"role": "assistant", "content": "done"}, "end_turn")
        return self.turns.pop(0)

    def user_message(self, text):
        return {"role": "user", "content": text}

    def tool_result_messages(self, results):
        return [{"role": "tool", "tool_call_id": c.id, "content": r} for c, r in results]


def text_turn(text):
    return Turn(text, [], {"role": "assistant", "content": text}, "end_turn")


def tool_turn(name, inputs, text=""):
    call = ToolCall(f"id-{name}", name, inputs)
    return Turn(text, [call], {"role": "assistant", "content": text, "tool_calls": [{"id": call.id, "name": name}]}, "tool_use")


@pytest.fixture
def store():
    s = Store(":memory:")
    s.load_customers(DATA / "customers.json")
    s.seed_tickets(DATA / "seed_tickets.json", EXFIL)
    return s


@pytest.fixture
def allowlist():
    return AllowList.load(DATA / "email_allowlist.json")


@pytest.fixture
def make_agent(store, allowlist):
    def _make(llm, level, defense=False):
        return ShopDeskAgent(llm, store, build_registry(), DEFAULTS, level=level,
                             defense=Defense(defense, allowlist, store))
    return _make
