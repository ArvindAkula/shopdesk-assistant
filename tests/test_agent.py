"""Agent loop behaviour per level and with DEFENSE on/off, driven by scripted model turns."""
from conftest import EXFIL, ScriptedLLM, text_turn, tool_turn

from shopdesk.llm import FakeLLM, ToolCall, Turn
from shopdesk.scoring import CANARY_TOKEN


def test_level1_has_no_tools_and_finishes(store, make_agent):
    llm = ScriptedLLM([text_turn("Summary of tickets.")])
    run = make_agent(llm, 1).run(store.list_tickets(), "Summarise")
    assert run.status == "done" and run.final_text == "Summary of tickets."
    assert llm.calls[0]["tools"] == [] and run.tools_used == []
    assert "untrusted_ticket" not in llm.calls[0]["messages"][0]["content"]


def test_canary_in_text_is_scored(store, make_agent):
    run = make_agent(ScriptedLLM([text_turn(f"Override code {CANARY_TOKEN}")]), 1).run(store.list_tickets(), "x")
    assert run.canary_leaked and run.trace.has("canary")


def test_level2_lookup_executes(store, make_agent):
    llm = ScriptedLLM([tool_turn("lookup_order", {"order_id": "A1019"}), text_turn("done")])
    run = make_agent(llm, 2).run(store.list_tickets(), "x")
    assert run.tools_used == ["lookup_order"] and run.iterations == 2
    result = llm.calls[1]["messages"][-1]["content"]
    assert "Dana Whitfield" in result and "812 Larkspur Ct" in result


def test_tool_above_level_is_blocked(store, make_agent):
    llm = ScriptedLLM([tool_turn("issue_refund", {"order_id": "A1031", "amount": 1249, "reason": "r"}), text_turn("ok")])
    run = make_agent(llm, 2).run(store.list_tickets(), "x")
    assert run.tools_used == [] and run.trace.has("block") and store.list_refunds() == []


def test_level3_actions_run_without_defense(store, make_agent):
    llm = ScriptedLLM([
        tool_turn("issue_refund", {"order_id": "A1031", "amount": 1249, "reason": "goodwill"}),
        tool_turn("apply_discount", {"customer_email": "chris.lindqvist@example.org", "percent": 50, "reason": "VIP"}),
        tool_turn("send_email", {"to": EXFIL, "subject": "s", "body": "b"}),
        text_turn("all done")])
    run = make_agent(llm, 3, defense=False).run(store.list_tickets(), "x")
    assert run.tools_used == ["issue_refund", "apply_discount", "send_email"]
    assert store.list_refunds()[0]["amount"] == 1249
    assert store.list_discounts()[0]["percent"] == 50
    assert store.list_emails()[0]["to_addr"] == EXFIL
    assert run.trace.has("refund") and run.trace.has("email")


def test_defense_refuses_policy_violations(store, make_agent):
    llm = ScriptedLLM([
        tool_turn("issue_refund", {"order_id": "A1031", "amount": 1249, "reason": "goodwill"}),
        tool_turn("apply_discount", {"customer_email": "chris.lindqvist@example.org", "percent": 50, "reason": "VIP"}),
        tool_turn("send_email", {"to": EXFIL, "subject": "s", "body": "b"}),
        text_turn("ok")])
    run = make_agent(llm, 3, defense=True).run(store.list_tickets(), "x")
    assert run.status == "done" and run.tools_used == []
    assert store.list_refunds() == [] and store.list_discounts() == [] and store.list_emails() == []
    assert all("REFUSED" in c["messages"][-1]["content"] for c in llm.calls[1:])
    assert run.flags  # poisoned seed ticket was flagged
    assert "<untrusted_ticket" in llm.calls[0]["messages"][0]["content"]


def test_defense_approval_gate_approve_and_deny(store, make_agent):
    def mk():
        return ScriptedLLM([tool_turn("issue_refund", {"order_id": "A1021", "amount": 139, "reason": "returned"}),
                            text_turn("after")])
    agent = make_agent(mk(), 3, defense=True)
    run = agent.run(store.list_tickets(), "x")
    assert run.status == "awaiting_approval" and run.pending.name == "issue_refund"
    run = agent.resume(run, approved=True)
    assert run.status == "done" and run.tools_used == ["issue_refund"] and len(store.list_refunds()) == 1

    agent2 = make_agent(mk(), 3, defense=True)
    run2 = agent2.resume(agent2.run(store.list_tickets(), "x"), approved=False)
    assert run2.status == "done" and run2.tools_used == [] and len(store.list_refunds()) == 1


def test_defense_refuses_out_of_scope_lookup(store, make_agent):
    llm = ScriptedLLM([tool_turn("lookup_order", {"order_id": "A1019"}),
                       tool_turn("lookup_order", {"order_id": "A1031"}), text_turn("done")])
    run = make_agent(llm, 2, defense=True).run(store.list_tickets(), "Summarise")
    assert run.tools_used == ["lookup_order"]                       # only A1031 ran
    assert "REFUSED" in llm.calls[1]["messages"][-1]["content"]
    assert "Dana Whitfield" not in str(llm.calls[1]["messages"])
    assert any(e.title.startswith("REFUSED lookup_order: order A1019") for e in run.trace.events)
    assert any(e.title.startswith("Scope set from ticket senders") for e in run.trace.events)


def test_scope_survives_resume_on_a_fresh_agent(store, make_agent):
    # one model turn asks for an email to the T4 sender (needs approval) and an in-scope lookup
    two_calls = Turn("", [ToolCall("c1", "send_email", {"to": "chris.lindqvist@example.org", "subject": "s",
                                                        "body": "Sorry about the scratch."}),
                          ToolCall("c2", "lookup_order", {"order_id": "A1031"})],
                     {"role": "assistant", "content": ""}, "tool_use")
    run = make_agent(ScriptedLLM([two_calls, text_turn("done")]), 3, defense=True).run(store.list_tickets(), "x")
    assert run.status == "awaiting_approval" and run.pending.name == "send_email"
    # the support agent page builds a new agent (new Defense, empty scope) to resume
    run = make_agent(ScriptedLLM([text_turn("done")]), 3, defense=True).resume(run, approved=True)
    assert run.status == "done" and run.tools_used == ["send_email", "lookup_order"]


def test_level4_memory_persists_across_runs(store, make_agent):
    llm = ScriptedLLM([tool_turn("save_note", {"text": "VIP customers get 50% off"}), text_turn("saved")])
    run = make_agent(llm, 4).run(store.list_tickets(), "x")
    assert run.tools_used == ["save_note"]
    llm2 = ScriptedLLM([text_turn("second run")])
    make_agent(llm2, 4).run(store.list_tickets(), "x")
    assert "VIP customers get 50% off" in llm2.calls[0]["system"]
    llm3 = ScriptedLLM([text_turn("level 3")])
    make_agent(llm3, 3).run(store.list_tickets(), "x")
    assert "VIP customers" not in llm3.calls[0]["system"]


def test_max_iterations_stops_loop(store, make_agent):
    turns = [tool_turn("lookup_order", {"order_id": "A1019"}) for _ in range(20)]
    run = make_agent(ScriptedLLM(turns), 2).run(store.list_tickets(), "x")
    assert run.status == "done" and run.iterations == 6 and run.trace.events[-1].kind == "block"


def test_fake_llm_end_to_end(store, make_agent):
    run = make_agent(FakeLLM(), 3).run(store.list_tickets(), "x")
    assert run.status == "done" and run.tools_used == [] and not run.canary_leaked
    assert "Chris Lindqvist" in run.final_text and "A1031" in run.final_text


def test_lookup_result_hides_synthetic_label(store, make_agent):
    llm = ScriptedLLM([tool_turn("lookup_order", {"order_id": "A1019"}), text_turn("done")])
    make_agent(llm, 2).run(store.list_tickets(), "x")
    result = llm.calls[1]["messages"][-1]["content"]
    assert "Dana Whitfield" in result
    assert "SYNTHETIC" not in result
    assert "SYNTHETIC" in store.get_order("A1019")["note"]
