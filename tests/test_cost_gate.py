"""The cost gate: a cost card and a typed yes before the audit's AI calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aeo.cli import app
from audit import cost_gate
from audit.cost_gate import CostGateAborted, card_lines, confirm_cost, estimated_cost
from audit.derive import EXTRACT
from audit.fill_in import generate_fill_in, import_fill_in
from audit.manual import run_manual_audit
from conftest import FakeAnthropicClient
from core.db import get_connection
from core.llm import estimate_cost
from tests.test_audit import Operator, answer, audit, biz, confirm_facts, extraction, qfile, query  # noqa: F401
from tests.test_fill_in import HEADER, all_done, done, fill, generate, ids  # noqa: F401

PRICES = json.loads((Path(__file__).parent.parent / "core" / "model_prices.json").read_text())


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cost_gate, "stdin_is_tty", lambda: True)


@pytest.fixture
def no_tty(monkeypatch):
    monkeypatch.setattr(cost_gate, "stdin_is_tty", lambda: False)


def gate(calls, reply, *, model="claude-haiku-4-5-20251001", claim_checks=0):
    said, asked = [], []

    def ask(prompt):
        asked.append(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply
    confirm_cost(calls, model=model, claim_checks=claim_checks, ask=ask, say=said.append)
    return said, asked


# ---- the maths ----------------------------------------------------------------

def test_cost_is_800_in_and_400_out_per_call_at_the_rates_in_the_price_file():
    haiku = PRICES["claude-haiku-4-5-20251001"]
    per_call = (800 * haiku["input_per_million"] + 400 * haiku["output_per_million"]) / 1_000_000
    assert per_call == pytest.approx(0.0028)
    assert estimated_cost(12, "claude-haiku-4-5-20251001") == pytest.approx(12 * 0.0028)
    sonnet = PRICES["claude-sonnet-5"]
    assert estimated_cost(10, "claude-sonnet-5") == pytest.approx(
        10 * (800 * sonnet["input_per_million"] + 400 * sonnet["output_per_million"]) / 1_000_000)
    assert estimated_cost(10, "claude-sonnet-5") == pytest.approx(0.056)
    assert estimate_cost("claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(12.0)


def test_sonnet_5_5_is_priced_the_same_as_sonnet_5():
    assert PRICES["claude-sonnet-5-5"] == PRICES["claude-sonnet-5"] == {"input_per_million": 2.0, "output_per_million": 10.0}
    assert estimated_cost(5, "claude-sonnet-5-5") == estimated_cost(5, "claude-sonnet-5")


def test_the_card_has_the_agreed_format():
    assert card_lines(12, "claude-haiku-4-5-20251001") == ["  AI extraction: 12 calls · claude-haiku-4-5-20251001 · ~$0.034"]
    assert card_lines(12, "claude-sonnet-5")[0] == "  AI extraction: 12 calls · claude-sonnet-5 · ~$0.067"
    with_claims = card_lines(4, "claude-sonnet-5", claim_checks=4)
    assert len(with_claims) == 2 and "up to 4 claim checks" in with_claims[1] and "~$0.022" in with_claims[1]


def test_an_unpriced_model_stops_instead_of_guessing():
    with pytest.raises(ValueError, match="no price configured"):
        card_lines(1, "claude-made-up")


# ---- yes / anything else / no terminal ---------------------------------------

@pytest.mark.parametrize("reply", ["yes", "YES", "Yes", "  yes  "])
def test_yes_in_any_case_proceeds(tty, reply):
    said, asked = gate(3, reply)
    assert asked == ["  Type yes to continue: "] and "Aborted." not in said


@pytest.mark.parametrize("reply", ["y", "no", "", "yes please", "ok", "YEP", EOFError()])
def test_anything_else_aborts(tty, reply):
    with pytest.raises(CostGateAborted):
        said, _ = gate(3, reply)
    # (the card and "Aborted." were printed before raising)


def test_aborting_prints_the_card_then_aborted(tty):
    said = []
    with pytest.raises(CostGateAborted):
        confirm_cost(3, model="claude-sonnet-5", ask=lambda p: "no", say=said.append)
    assert said == ["  AI extraction: 3 calls · claude-sonnet-5 · ~$0.017", "Aborted."]


def test_without_a_terminal_the_card_is_printed_then_it_aborts_without_asking(no_tty):
    said = []
    with pytest.raises(CostGateAborted):
        confirm_cost(5, model="claude-sonnet-5", ask=lambda p: pytest.fail("must not ask"), say=said.append)
    assert said[0].startswith("  AI extraction: 5 calls")
    assert said[-1] == ("A person must be at the keyboard to approve this cost. "
                        "Run this again from a terminal.")


def test_zero_calls_means_no_card_and_no_question(tty):
    said, asked = gate(0, "no")
    assert said == [] and asked == []


# ---- the fill-in import route -------------------------------------------------

Q1 = "who supplies water refill stations for events?"
Q2 = "best refill station rental in Canada"


def rows(run_id):
    return query("SELECT count(*) FROM probe_results WHERE probe_run_id = %s", (run_id,))[0][0]


def test_import_shows_the_call_count_and_yes_proceeds(biz, qfile, tmp_path, tty):
    path, made = generate(biz, qfile, tmp_path)
    all_done(made.path)
    said, asked = [], []
    res = import_fill_in(made.path, client=FakeAnthropicClient([extraction()] * 4),
                         ask=lambda p: (asked.append(p), "yes")[1], say=said.append)
    assert res.saved == 4 and asked == ["  Type yes to continue: "]
    assert f"  AI extraction: 4 calls · {EXTRACT.model} · ~$0.022" in said


def test_import_abort_saves_nothing_and_the_file_can_be_imported_later(biz, qfile, tmp_path, tty):
    path, made = generate(biz, qfile, tmp_path)
    all_done(made.path)
    said = []
    client = FakeAnthropicClient([extraction()] * 4)
    with pytest.raises(CostGateAborted):
        import_fill_in(made.path, client=client, ask=lambda p: "no", say=said.append)
    assert "Aborted." in said and rows(made.probe_run_id) == 0
    assert client.messages.calls == []
    assert query("SELECT status FROM probe_runs WHERE id = %s", (made.probe_run_id,))[0][0] == "in_progress"
    assert query("SELECT count(*) FROM client_journal WHERE entry_type IN ('note','probe_completed') AND "
                 "details->>'probe_run_id' = %s", (made.probe_run_id,))[0][0] == 0
    res = import_fill_in(made.path, client=client, ask=lambda p: "yes", say=said.append)   # later, after all
    assert res.saved == 4 and res.complete


def test_the_count_leaves_out_answers_an_earlier_import_already_saved(biz, qfile, tmp_path, tty):
    path, made = generate(biz, qfile, tmp_path)
    fill(made.path, header=HEADER, Q1_chatgpt_r1=done("a"), Q1_perplexity_r1=done("b"))
    client = FakeAnthropicClient([extraction()] * 4)
    first, second = [], []
    import_fill_in(made.path, partial=True, client=client, ask=lambda p: "yes", say=first.append)
    fill(made.path, Q2_chatgpt_r1=done("c"), Q2_perplexity_r1=done("d"))
    import_fill_in(made.path, client=client, ask=lambda p: "yes", say=second.append)
    assert first[0].startswith("  AI extraction: 2 calls") and second[0].startswith("  AI extraction: 2 calls")
    third = []                                    # nothing new to read: no card, no question
    import_fill_in(made.path, client=client, ask=lambda p: pytest.fail("asked"), say=third.append)
    assert third == []


def test_import_without_a_terminal_aborts_and_saves_nothing(biz, qfile, tmp_path, no_tty):
    path, made = generate(biz, qfile, tmp_path)
    all_done(made.path)
    said = []
    client = FakeAnthropicClient([extraction()] * 4)
    with pytest.raises(CostGateAborted):
        import_fill_in(made.path, client=client,
                       ask=lambda p: pytest.fail("a piped run must not be asked"), say=said.append)
    assert said[0].startswith("  AI extraction: 4 calls")
    assert "A person must be at the keyboard to approve this cost. Run this again from a terminal." in said
    assert rows(made.probe_run_id) == 0 and client.messages.calls == []
    assert query("SELECT status FROM probe_runs WHERE id = %s", (made.probe_run_id,))[0][0] == "in_progress"


def test_the_card_mentions_claim_checks_when_the_business_has_confirmed_facts(biz, qfile, tmp_path, tty):
    confirm_facts(biz, Phone="514-555-0100")
    path, made = generate(biz, qfile, tmp_path, questions=(Q1,))
    all_done(made.path, question_count=1)
    said = []
    import_fill_in(made.path, client=FakeAnthropicClient([extraction()] * 2), ask=lambda p: "yes", say=said.append)
    assert "up to 2 claim checks" in said[1]


# ---- the terminal route -------------------------------------------------------

def test_terminal_mode_asks_first_and_abort_creates_and_saves_nothing(biz, qfile, tty):
    op = Operator(["no"], approve_cost=False)
    from audit.manual import run_manual_audit as run
    with pytest.raises(CostGateAborted):
        run(biz["id"], str(qfile()), checkpoint=None, ask=op.ask, say=op.say, client=FakeAnthropicClient([]))
    assert any("AI extraction: 4 calls" in s for s in op.said) and "Aborted." in op.said
    assert op.prompts == ["  Type yes to continue: "]                  # asked before anything else
    assert query("SELECT count(*) FROM probe_runs WHERE business_id = %s", (biz["id"],))[0][0] == 0
    assert query("SELECT count(*) FROM client_journal WHERE business_id = %s AND entry_type = 'probe_started'",
                 (biz["id"],))[0][0] == 0


def test_terminal_mode_without_a_terminal_aborts_and_creates_nothing(biz, qfile, no_tty):
    op = Operator([], approve_cost=False)
    with pytest.raises(CostGateAborted):
        run_manual_audit(biz["id"], str(qfile()), checkpoint=None, ask=op.ask, say=op.say,
                         client=FakeAnthropicClient([]))
    assert op.prompts == [] and "Aborted." not in op.said
    assert query("SELECT count(*) FROM probe_runs WHERE business_id = %s", (biz["id"],))[0][0] == 0


def test_terminal_mode_yes_carries_on_into_the_normal_flow(biz, qfile, tty):
    op = Operator(["yes", "y", ""] + answer("a") + answer("b"), approve_cost=False)
    out = audit(biz, qfile(questions=(Q1,)), op, FakeAnthropicClient([extraction(), extraction()]))
    assert out.complete and out.counts["answers"] == 2
    assert any("AI extraction: 2 calls" in s for s in op.said)


# ---- the command line ---------------------------------------------------------

def test_cli_import_abort_prints_aborted_and_exits_without_saving(biz, qfile, tmp_path, tty, monkeypatch):
    path, made = generate(biz, qfile, tmp_path)
    all_done(made.path)
    result = CliRunner().invoke(app, ["audit", "import", str(made.path)], input="nope\n")
    assert result.exit_code == 1
    assert "AI extraction: 4 calls" in result.output and "Aborted." in result.output
    assert "Error" not in result.output
    assert rows(made.probe_run_id) == 0


def test_cli_import_yes_goes_through(biz, qfile, tmp_path, tty, monkeypatch):
    path, made = generate(biz, qfile, tmp_path)
    all_done(made.path)
    fake = FakeAnthropicClient([extraction()] * 4)
    monkeypatch.setattr("core.llm.anthropic.Anthropic", lambda: fake)
    result = CliRunner().invoke(app, ["audit", "import", str(made.path)], input="YES\n")
    assert result.exit_code == 0, result.output
    assert "Audit complete: every answer is saved." in result.output


def test_cli_audit_without_a_terminal_exits_non_zero_and_saves_nothing(biz, qfile, no_tty):
    result = CliRunner().invoke(app, ["audit", "--business", biz["id"], "--question-set", str(qfile())])
    assert result.exit_code == 1
    assert "AI extraction: 4 calls" in result.output
    assert "A person must be at the keyboard to approve this cost. Run this again from a terminal." in result.output
    assert query("SELECT count(*) FROM probe_runs WHERE business_id = %s", (biz["id"],))[0][0] == 0


def test_cli_import_without_a_terminal_exits_non_zero_and_saves_nothing(biz, qfile, tmp_path, no_tty):
    path, made = generate(biz, qfile, tmp_path)
    all_done(made.path)
    result = CliRunner().invoke(app, ["audit", "import", str(made.path)])
    assert result.exit_code == 1
    assert "A person must be at the keyboard to approve this cost. Run this again from a terminal." in result.output
    assert rows(made.probe_run_id) == 0
