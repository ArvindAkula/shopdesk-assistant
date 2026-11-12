"""Structured step log. Every model call, tool call, defense decision and block lands here."""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict

ICONS = {
    "info": "ℹ️", "llm": "🧠", "tool": "🔧", "defense": "🛡️", "block": "⛔",
    "approval": "✋", "canary": "🚨", "email": "📧", "refund": "💸", "memory": "💾", "error": "❌",
}


@dataclass
class TraceEvent:
    kind: str
    title: str
    detail: str = ""
    t: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trace:
    events: list[TraceEvent] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def add(self, kind: str, title: str, detail: str = "") -> TraceEvent:
        ev = TraceEvent(kind, title, detail)
        self.events.append(ev)
        return ev

    def has(self, kind: str) -> bool:
        return any(e.kind == kind for e in self.events)

    def to_markdown(self) -> str:
        if not self.events:
            return "_No steps yet._"
        lines = []
        for i, e in enumerate(self.events, 1):
            dt = e.t - self.started
            lines.append(f"**{i}. {ICONS.get(e.kind, '•')} {e.title}** <sub>+{dt:0.1f}s</sub>")
            if e.detail:
                detail = e.detail.strip()
                if e.kind == "llm":
                    # Model text is markdown itself: render it as a quote, not a raw code block.
                    lines.append("\n".join("> " + ln if ln.strip() else ">" for ln in detail.splitlines()))
                elif "\n" in detail or len(detail) > 120:
                    lines.append("```text\n" + detail + "\n```")
                else:
                    lines.append(f"<br>{detail}")
            lines.append("")
        return "\n".join(lines)

    def to_text(self) -> str:
        return "\n".join(f"[{e.kind}] {e.title}" + (f"\n    {e.detail}" if e.detail else "") for e in self.events)

    def to_list(self) -> list[dict]:
        return [e.to_dict() for e in self.events]
