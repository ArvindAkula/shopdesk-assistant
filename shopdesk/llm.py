"""Thin provider adapters. The agent loop only sees `Turn` objects and opaque message dicts;
each adapter owns its own wire format.

  AnthropicLLM  - Claude via the official SDK (Messages API, manual tool loop)
  OpenAILLM     - any OpenAI-compatible chat.completions endpoint
  FakeLLM       - offline stub: no key, no network. Writes a plain summary and never calls a
                  tool. Lets you click through the UI without a key.

Keys come from the environment only (ANTHROPIC_API_KEY / OPENAI_API_KEY).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall]
    raw_assistant: Any          # provider-format assistant message, appended verbatim to history
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


class LLM(Protocol):
    label: str

    def complete(self, system: str, messages: list, tools: list[ToolSpec], max_tokens: int) -> Turn: ...
    def user_message(self, text: str) -> Any: ...
    def tool_result_messages(self, results: list[tuple[ToolCall, str]]) -> list: ...


# ---------------------------------------------------------------- Anthropic
class AnthropicLLM:
    def __init__(self, model: str, effort: str = "low", api_key: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model, self.effort = model, effort
        self.label = f"anthropic:{model}"

    def complete(self, system, messages, tools, max_tokens) -> Turn:
        kwargs: dict[str, Any] = dict(model=self.model, max_tokens=max_tokens, system=system, messages=messages)
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                               for t in tools]
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        resp = self.client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        raw = {"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]}
        usage = {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens}
        return Turn(text, calls, raw, resp.stop_reason or "", usage)

    def user_message(self, text):
        return {"role": "user", "content": text}

    def tool_result_messages(self, results):
        return [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": r} for c, r in results]}]


# ---------------------------------------------------------------- OpenAI-compatible
class OpenAILLM:
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        import openai
        self.client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"),
                                    base_url=base_url or os.environ.get("OPENAI_BASE_URL") or None)
        self.model = model
        self.label = f"openai:{model}"

    def complete(self, system, messages, tools, max_tokens) -> Turn:
        kwargs: dict[str, Any] = dict(model=self.model, max_tokens=max_tokens,
                                      messages=[{"role": "system", "content": system}] + messages)
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema}} for t in tools]
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        raw = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            raw["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in msg.tool_calls]
        usage = {"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens} if resp.usage else {}
        return Turn(msg.content or "", calls, raw, choice.finish_reason or "", usage)

    def user_message(self, text):
        return {"role": "user", "content": text}

    def tool_result_messages(self, results):
        return [{"role": "tool", "tool_call_id": c.id, "content": r} for c, r in results]


# ---------------------------------------------------------------- Fake (offline stub)
_NAME_RE = re.compile(r"^Name:[ \t]*(.*)$", re.M)
_ORDER_RE = re.compile(r"^Order:[ \t]*(.*)$", re.M)


class FakeLLM:
    """Offline stand-in. Produces a plausible-looking summary from the ticket text and never calls
    tools, so the UI and scoring can be exercised without an API key."""

    label = "fake:offline-stub"

    def complete(self, system, messages, tools, max_tokens) -> Turn:
        text = "\n".join(m["content"] for m in messages
                         if m.get("role") == "user" and isinstance(m.get("content"), str))
        names = _NAME_RE.findall(text)
        orders = _ORDER_RE.findall(text)
        if not names:
            summary = "No tickets to summarise."
        else:
            lines = ["**Support ticket summary (offline stub model)**", ""]
            for i, name in enumerate(names):
                order = orders[i] if i < len(orders) else "n/a"
                lines.append(f"- **{name.strip()}** — order {order.strip()}. Next step: reply to confirm details.")
            lines.append("")
            lines.append("_This is the offline stub: it writes a plain summary and never uses tools. "
                         "Set an API key to run a real model._")
            summary = "\n".join(lines)
        return Turn(summary, [], {"role": "assistant", "content": summary}, "end_turn", {})

    def user_message(self, text):
        return {"role": "user", "content": text}

    def tool_result_messages(self, results):
        return [{"role": "tool", "tool_call_id": c.id, "content": r} for c, r in results]


def make_llm(settings) -> LLM:
    if settings.provider == "anthropic":
        return AnthropicLLM(settings.model, settings.effort)
    if settings.provider == "openai":
        return OpenAILLM(settings.model)
    return FakeLLM()
