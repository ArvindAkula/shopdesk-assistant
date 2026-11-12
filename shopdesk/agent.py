"""The agent loop: build context → call model → run any tool calls → repeat, with every step traced.

Hand-written on purpose (no framework) so every step is visible on the trace panel.
An AgentRun is a plain state object; `run()` starts, `resume()` continues after a human
approval decision (the DEFENSE approval gate pauses the loop and hands control to the UI).
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from .config import Settings
from .defense import Defense, Scope
from .llm import LLM, ToolCall, ToolSpec
from .memory import Memory
from .prompts import system_prompt
from .scoring import contains_canary
from .store import Store
from .tools import Registry, ToolContext
from .trace import Trace


@dataclass
class AgentRun:
    level: int
    defense: bool
    status: str = "running"                    # running | awaiting_approval | done | error
    messages: list = field(default_factory=list)
    trace: Trace = field(default_factory=Trace)
    final_text: str = ""
    iterations: int = 0
    tools_used: list[str] = field(default_factory=list)
    canary_leaked: bool = False
    flags: dict[str, list[str]] = field(default_factory=dict)   # ticket id -> detector reasons
    scope: Scope = field(default_factory=Scope)                  # orders / senders this run may touch
    # approval-gate state
    pending_calls: list[ToolCall] = field(default_factory=list)
    pending_index: int = 0
    partial_results: list = field(default_factory=list)

    @property
    def pending(self) -> ToolCall | None:
        if self.status == "awaiting_approval" and self.pending_index < len(self.pending_calls):
            return self.pending_calls[self.pending_index]
        return None

    def summary_line(self) -> str:
        bits = [f"level {self.level}", "defense ON" if self.defense else "defense OFF", f"{self.iterations} model turn(s)"]
        if self.tools_used:
            counts = Counter(self.tools_used)
            bits.append("tools: " + ", ".join(n if k == 1 else f"{n} ×{k}" for n, k in counts.items()))
        if self.canary_leaked:
            bits.append("🚨 CANARY LEAKED")
        return " · ".join(bits)


class ShopDeskAgent:
    def __init__(self, llm: LLM, store: Store, registry: Registry, settings: Settings,
                 *, level: int, defense: Defense):
        self.llm, self.store, self.registry, self.settings = llm, store, registry, settings
        self.level = level
        self.defense = defense
        self.memory = Memory(store) if level >= 4 else None
        self.tools = registry.filter_for(level)
        self.specs = [ToolSpec(t.name, t.description, t.input_schema) for t in self.tools]

    # ---- public API ----
    def run(self, tickets: list[dict], agent_request: str) -> AgentRun:
        run = AgentRun(level=self.level, defense=self.defense.enabled)
        tr = run.trace
        prompt = "defended" if self.defense.enabled else f"{self.settings.naive_prompt} naive"
        tr.add("info", f"Run started: level {self.level}, defense {'ON' if self.defense.enabled else 'OFF'}, model {self.llm.label}, prompt {prompt}",
               "Tools visible to the model: " + (", ".join(t.name for t in self.tools) or "none"))
        notes = self.memory.recall() if self.memory else []
        if self.memory:
            tr.add("memory", f"Loaded {len(notes)} note(s) from shared memory into context",
                   "\n".join(notes) if notes else "(memory empty)")
        for t in tickets:
            reasons = self.defense.flags_for(t)
            if reasons:
                run.flags[f"T{t['id']}"] = reasons
                tr.add("defense", f"Ticket T{t['id']} flagged: instruction-like content", "\n".join("• " + r for r in reasons))
        rendered = "\n\n".join(self.defense.render_ticket(t) for t in tickets)
        run.scope = self.defense.set_scope(tickets, agent_request)
        if self.defense.enabled:
            tr.add("defense", "Ticket text fenced as untrusted data (random per-request boundary)")
            tr.add("defense", "Scope set from ticket senders and the support agent's request",
                   f"orders: {', '.join(sorted(run.scope.orders)) or 'none'}\n"
                   f"senders: {', '.join(sorted(run.scope.senders)) or 'none'}")
        user = f"{agent_request}\n\nNew support tickets ({len(tickets)}):\n\n{rendered}"
        run.messages.append(self.llm.user_message(user))
        self._system = system_prompt(tools_available=bool(self.tools), defense=self.defense.enabled, memory_notes=notes,
                                     style=self.settings.naive_prompt)
        return self._loop(run)

    def resume(self, run: AgentRun, approved: bool) -> AgentRun:
        call = run.pending
        if call is None:
            return run
        self.defense.scope = run.scope   # resume builds a fresh agent; the run carries its scope
        tr = run.trace
        if approved:
            tr.add("approval", f"Support agent APPROVED {call.name}")
            result = self._execute(run, call)
        else:
            tr.add("block", f"Support agent DENIED {call.name}", "rule: irreversible actions need a human yes")
            result = f"Action {call.name} was denied by the support agent. Do not retry it."
        run.partial_results.append((call, result))
        run.pending_index += 1
        run.status = "running"
        if not hasattr(self, "_system"):
            self._system = system_prompt(tools_available=bool(self.tools), defense=self.defense.enabled,
                                         memory_notes=self.memory.recall() if self.memory else None,
                                         style=self.settings.naive_prompt)
        self._process_calls(run)
        return self._loop(run) if run.status == "running" else run

    # ---- internals ----
    def _loop(self, run: AgentRun) -> AgentRun:
        while run.status == "running":
            if run.iterations >= self.settings.max_iterations:
                run.trace.add("block", f"Stopped: max {self.settings.max_iterations} model turns reached")
                run.status = "done"
                break
            run.iterations += 1
            try:
                turn = self.llm.complete(self._system, run.messages, self.specs, self.settings.max_tokens)
            except Exception as e:  # surfaced on the trace, never crashes the UI
                run.trace.add("error", f"Model call failed: {type(e).__name__}", str(e)[:800])
                run.status = "error"
                run.final_text = f"⚠️ Model call failed: {e}"
                break
            run.messages.append(turn.raw_assistant)
            detail = turn.text or "(no text)"
            if turn.tool_calls:
                detail += "\n\nTool calls requested: " + ", ".join(
                    f"`{c.name}({json.dumps(c.input)[:200]})`" for c in turn.tool_calls)
            run.trace.add("llm", f"Model turn {run.iterations} (stop: {turn.stop_reason or 'n/a'})", detail)
            if turn.stop_reason in ("max_tokens", "length"):
                run.trace.add("error", f"Output cut off at MAX_TOKENS={self.settings.max_tokens}",
                              "The model ran out of output budget mid-answer. Raise MAX_TOKENS.")
            if turn.stop_reason == "refusal":
                run.trace.add("block", "Model declined the request (safety stop)")
            self._check_canary(run, turn.text, "model text")
            if not turn.tool_calls:
                run.final_text = turn.text
                run.status = "done"
                break
            run.pending_calls, run.pending_index, run.partial_results = turn.tool_calls, 0, []
            self._process_calls(run)
        return run

    def _process_calls(self, run: AgentRun) -> AgentRun:
        while run.pending_index < len(run.pending_calls):
            call = run.pending_calls[run.pending_index]
            tool = self.registry.get(call.name)
            if tool is None or tool not in self.tools:
                run.trace.add("block", f"Model asked for unavailable tool {call.name!r}")
                result = f"Tool {call.name} is not available."
            else:
                self._check_canary(run, json.dumps(call.input), f"{call.name} arguments")
                decision = self.defense.check_tool_call(tool, call.input)
                if decision.action == "refuse":
                    run.trace.add("block", f"REFUSED {call.name}: {decision.reason}", f"rule: {decision.rule}")
                    result = f"REFUSED by policy: {decision.reason}."
                elif decision.action == "approve":
                    run.trace.add("approval", f"Waiting for support agent approval: {call.name}",
                                  json.dumps(call.input, indent=1)[:1500])
                    run.status = "awaiting_approval"
                    return run
                else:
                    result = self._execute(run, call)
            run.partial_results.append((call, result))
            run.pending_index += 1
        run.messages.extend(self.llm.tool_result_messages(run.partial_results))
        run.pending_calls, run.partial_results, run.pending_index = [], [], 0
        run.status = "running"
        return run

    def _execute(self, run: AgentRun, call: ToolCall) -> str:
        tool = self.registry.get(call.name)
        ctx = ToolContext(self.store, run.trace, self.level)
        try:
            result = tool.run(ctx, call.input)
        except Exception as e:
            run.trace.add("error", f"{call.name} failed: {e}")
            result = f"Tool error: {e}"
        run.tools_used.append(call.name)
        return result

    def _check_canary(self, run: AgentRun, text: str, where: str) -> None:
        if text and contains_canary(text):
            run.canary_leaked = True
            run.trace.add("canary", f"Confidential discount rule appeared in {where}")
