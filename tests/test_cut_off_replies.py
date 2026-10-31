"""Step 25f: a reply cut off at the output cap is kept, not thrown away. The model is always the scripted fake."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from core import llm
from core.db import get_connection
from core.runner import AgentDefinition, AgentOutputCutOff, AgentOutputInvalid, run_agent
from pydantic import BaseModel
from questions_draft import plan, run as qrun
from questions_draft.run import DraftError, DraftOutput, keep_what_arrived
from questions_draft.salvage import salvage
from tests.conftest import FakeAnthropicClient, FakeResponse, FakeTextBlock, FakeUsage, text_response
from tests.test_audit import biz  # noqa: F401  (fixture)
from tests.test_question_drafts_keep_everything import figure, run_count  # noqa: F401
from tests.test_questions_draft import (Words, clean_set, diagnosed, disc, fake, file_items,  # noqa: F401
                                        person_at_keyboard, q, reply, run, sent_to_model)

ROOT = Path(__file__).resolve().parent.parent


def cut_off(text: str, tokens: int = 6000) -> FakeResponse:
    """A reply that stopped because the output cap was reached."""
    return FakeResponse(content=[FakeTextBlock(text=text)], stop_reason="max_tokens",
                        usage=FakeUsage(input_tokens=3000, output_tokens=tokens))


def cut_reply(items: list[dict], local=True) -> str:
    """The text of `reply(items)` with its last question stopped in the middle of a word, as a real cut-off looks."""
    head = f'{{"local": {json.dumps(local)}, "local_reason": "it has a street address", "questions": [\n  '
    return head + ",\n  ".join(json.dumps(i) for i in items) + ',\n  {"text": "Which dentists in North York offer financing plans for pat'


def runs_of(biz_id, since=None):
    conn = get_connection()
    try:
        return conn.execute("SELECT status, stop_reason, output_tokens, cost_usd, error, prompt_version FROM runs "
                            "WHERE business_id = %s AND agent = 'questions_draft' ORDER BY created_at, id", (biz_id,)).fetchall()
    finally:
        conn.close()


# ---- 1. keeping what arrived ----------------------------------------------------------------------------------------------------------------

def test_every_whole_question_is_kept_and_the_half_written_one_is_dropped():
    text = ('{"local": true, "local_reason": "a clinic", "questions": [\n {"text":"a","kind":"discovery","relies_on_fact_ids":[]},\n'
            ' {"text":"b","kind":"brand"},\n {"text":"Which dentists in North York offer fin')
    kept = salvage(text)
    assert [x["text"] for x in kept["questions"]] == ["a", "b"] and kept["local"] is True and kept["local_reason"] == "a clinic"


def test_the_grouped_reply_is_salvaged_group_by_group():
    text = ('{"local": false, "discovery":[{"text":"x","need":"price"},{"text":"y"}], "control":[{"text":"z"}], '
            '"brand":[{"text":"q","language":"en","fact')
    kept = salvage(text)
    assert [x["text"] for x in kept["discovery"]] == ["x", "y"] and [x["text"] for x in kept["control"]] == ["z"]
    assert "brand" not in kept and kept["local"] is False


def test_a_word_used_as_a_value_is_not_mistaken_for_a_list_key():
    text = '{"questions": [{"text":"a","kind":"brand","control":false}, {"text":"b","kind":"discovery"}, {"te'
    assert [x["text"] for x in salvage(text)["questions"]] == ["a", "b"]


def test_nothing_arrived_whole_gives_an_empty_reply_not_an_error():
    assert salvage('{"loc') == {}
    assert keep_what_arrived('{"local": true, "local_reason": "a cl').all_questions(["en"]) == []
    assert keep_what_arrived('{"local": true, "questions": [{"text": "a').all_questions(["en"]) == []


def test_the_grouped_reply_gets_its_fixed_fields_from_the_group():
    out = DraftOutput.model_validate({
        "local": True,
        "discovery": [{"text": "d", "intent": "commercial", "need": "price", "is_target": True}],
        "control": [{"text": "c"}], "brand": [{"text": "b", "fact_topic": "hours"}]})
    d, c, b = out.all_questions(["en"])
    assert (d["kind"], d["control"], d["language"]) == ("discovery", False, "en")
    assert (c["kind"], c["control"], c["intent"], c["is_target"]) == ("discovery", True, "informational", False)
    assert (b["kind"], b["control"], b["intent"], b["is_target"], b["fact_topic"]) == ("brand", False, None, False, "hours")
    # with two languages the model must say which; nothing is guessed
    assert out.all_questions(["en", "fr"])[0]["language"] is None


def test_a_reply_with_only_the_local_answer_is_valid_but_a_reply_with_nothing_is_not():
    assert DraftOutput.model_validate({"local": True, "local_reason": "x"}).all_questions(["en"]) == []
    with pytest.raises(ValueError):
        DraftOutput.model_validate({})


# ---- 2. core/llm.py: the stop reason and the thinking setting --------------------------------------------------------------------------------

def test_a_cut_off_call_is_logged_as_failed_with_its_stop_reason_and_still_returns_the_text(business_id):
    client = FakeAnthropicClient([cut_off("part of a reply", tokens=8000)])
    result = llm.call(business_id=business_id, agent="t", model="claude-sonnet-5", prompt_version="v1", system="s",
                      messages=[{"role": "user", "content": "hi"}], max_tokens=8000, client=client)
    assert result.cut_off and result.text == "part of a reply" and result.stop_reason == "max_tokens"
    conn = get_connection()
    try:
        status, stop, error, cost, out = conn.execute(
            "SELECT status, stop_reason, error, cost_usd, output FROM runs WHERE business_id = %s", (business_id,)).fetchone()
    finally:
        conn.close()
    assert status == "failed" and stop == "max_tokens" and "cut off" in error and "8,000" in error
    assert float(cost) > 0 and out == {"text": "part of a reply"}            # the money is on the books, and the raw text is kept


def test_a_normal_call_is_still_a_success_and_records_how_it_ended(business_id):
    llm.call(business_id=business_id, agent="t", model="claude-sonnet-5", prompt_version="v1", system="s",
             messages=[{"role": "user", "content": "hi"}], client=FakeAnthropicClient([text_response("fine")]))
    conn = get_connection()
    try:
        assert conn.execute("SELECT status, stop_reason, error FROM runs WHERE business_id = %s", (business_id,)).fetchone() == \
            ("success", "end_turn", None)
    finally:
        conn.close()


def test_thinking_is_sent_only_when_asked_for(business_id):
    client = FakeAnthropicClient([text_response("a"), text_response("b")])
    base = dict(business_id=business_id, agent="t", model="claude-sonnet-5", prompt_version="v1", system="s",
                messages=[{"role": "user", "content": "hi"}], client=client)
    llm.call(**base)
    llm.call(**base, thinking={"type": "disabled"})
    assert "thinking" not in client.messages.calls[0]                       # every other caller: nothing changes
    assert client.messages.calls[1]["thinking"] == {"type": "disabled"}


def test_runs_made_before_the_stop_reason_existed_keep_it_empty(business_id):
    llm._log_run(business_id=business_id, agent="old", model="claude-sonnet-5", prompt_version="v1", input_payload={},
                 output_payload=None, input_tokens=1, output_tokens=1, cost_usd=0.0, latency_ms=1, status="success", error=None)
    conn = get_connection()
    try:
        assert conn.execute("SELECT stop_reason FROM runs WHERE business_id = %s", (business_id,)).fetchone() == (None,)
    finally:
        conn.close()


# ---- 3. core/runner.py ------------------------------------------------------------------------------------------------------------------------

class Tiny(BaseModel):
    items: list[str]


def tiny_agent(tmp_path, **kw) -> AgentDefinition:
    prompt = tmp_path / "p.md"
    prompt.write_text("system")
    return AgentDefinition(name="tiny", model="claude-sonnet-5", prompt_version="v1", prompt_path=prompt, output_schema=Tiny, **kw)


def test_an_agent_that_can_keep_a_partial_reply_is_not_asked_again(business_id, tmp_path):
    agent = tiny_agent(tmp_path, on_cut_off=lambda text: text.upper())
    client = FakeAnthropicClient([cut_off('{"items": ["a", "b"')])             # a second reply is not queued: asking would fail the test
    with pytest.raises(AgentOutputCutOff) as got:
        run_agent(agent, business_id=business_id, input_data={}, client=client)
    assert got.value.partial == '{"ITEMS": ["A", "B"' and len(client.messages.calls) == 1


def test_an_agent_without_that_keeps_the_old_behaviour_but_the_error_says_cut_off(business_id, tmp_path):
    client = FakeAnthropicClient([cut_off('{"items": ["a", "b"'), text_response('{"items": ["a"]}')])
    got = run_agent(tiny_agent(tmp_path), business_id=business_id, input_data={}, client=client)
    assert got.items == ["a"] and len(client.messages.calls) == 2
    assert "cut off at the output cap" in client.messages.calls[1]["messages"][-1]["content"]
    assert "Unterminated" not in client.messages.calls[1]["messages"][-1]["content"]


def test_the_thinking_setting_of_an_agent_reaches_the_call(business_id, tmp_path):
    client = FakeAnthropicClient([text_response('{"items": []}')])
    run_agent(tiny_agent(tmp_path, thinking={"type": "disabled"}), business_id=business_id, input_data={}, client=client)
    assert client.messages.calls[0]["thinking"] == {"type": "disabled"}


# ---- 4. the draft ------------------------------------------------------------------------------------------------------------------------------

def test_the_draft_calls_the_model_with_thinking_off_and_the_new_cap(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = fake(clean_set(biz))
    run(biz, tmp_path, client)
    call = client.messages.calls[0]
    assert call["thinking"] == {"type": "disabled"} and call["max_tokens"] == qrun.OUTPUT_CAP == 6000


@pytest.fixture
def own_history(monkeypatch):
    monkeypatch.setattr(qrun, "ACTOR", f"aeo questions draft test {uuid.uuid4().hex}")


def test_a_reply_cut_off_after_twenty_questions_keeps_them_and_the_next_call_asks_for_the_rest_only(biz, tmp_path):
    diagnosed(biz, tmp_path)
    full = clean_set(biz)
    first, rest = full[:20], full[20:]
    client = FakeAnthropicClient([cut_off(cut_reply(first)), text_response(reply(rest))])
    r = run(biz, tmp_path, client)

    assert len(client.messages.calls) == 2 and r.calls == 2                      # no whole-set repeat
    retry = sent_to_model(client, 1)["retry"]
    assert retry["accepted"] == [x["text"] for x in first] and retry["cut_off"] is True and retry["rejected"] == []
    needed = sum(n["count"] for n in retry["needed"])
    assert needed == 12 == len(rest) and sum(n["count"] for n in retry["needed"] if n["group"] == "brand") == sum(
        1 for x in rest if x["kind"] == "brand")
    assert len(file_items(r)) == 32 and r.missing == {} and r.rejected == []
    assert any("Reply 1 was cut off at the output cap (6,000 tokens) after 20 complete question(s)" in w
               and "asked only for what was still missing" in w for w in r.warnings)


def test_a_cut_off_reply_is_never_reported_as_invalid_json(biz, tmp_path):
    diagnosed(biz, tmp_path)
    full = clean_set(biz)
    said: list = []
    r = run(biz, tmp_path, FakeAnthropicClient([cut_off(cut_reply(full[:20])), text_response(reply(full[20:]))]), said=said)
    shown = "\n".join(said + [w for w in r.warnings] + summary_text(r))
    assert "Unterminated" not in shown and "usable draft" not in shown and "invalid" not in shown.lower()
    assert "queue" not in shown                                                   # nothing went to human_queue
    conn = get_connection()
    try:
        assert conn.execute("SELECT count(*) FROM human_queue WHERE business_id = %s", (biz["id"],)).fetchone()[0] == 0
    finally:
        conn.close()


def summary_text(r):
    return qrun.summary_lines(r)


def test_the_cut_off_call_is_in_the_runs_table_as_failed_with_its_cost(biz, tmp_path):
    diagnosed(biz, tmp_path)
    full = clean_set(biz)
    r = run(biz, tmp_path, FakeAnthropicClient([cut_off(cut_reply(full[:20])), text_response(reply(full[20:]))]))
    rows = runs_of(biz["id"])
    assert [x[0] for x in rows] == ["failed", "success"] and [x[1] for x in rows] == ["max_tokens", "end_turn"]
    assert float(rows[0][3]) > 0 and "cut off" in rows[0][4] and {x[5] for x in rows} == {"v4"}
    assert r.cost["cost_usd"] == pytest.approx(sum(float(x[3]) for x in rows))     # the cut-off call is in the run's cost


def test_two_cut_off_replies_keep_everything_that_arrived_and_list_the_rest_as_missing(biz, tmp_path):
    diagnosed(biz, tmp_path)
    full = clean_set(biz)
    client = FakeAnthropicClient([cut_off(cut_reply(full[:14])), cut_off(cut_reply(full[14:20])), text_response(reply([]))])
    r = run(biz, tmp_path, client)
    assert len(client.messages.calls) <= 3
    assert len(file_items(r)) == 20 and sum(r.missing.values()) == 12
    assert sum("was cut off at the output cap" in w for w in r.warnings) == 2
    assert "STILL MISSING" in r.path.read_text()


def test_the_calls_stay_at_three_when_a_cut_off_is_followed_by_a_top_up(biz, tmp_path):
    from tests.test_question_drafts_keep_everything import lopsided
    diagnosed(biz, tmp_path)
    lop = lopsided(biz)
    client = FakeAnthropicClient([cut_off(cut_reply(lop[:20])), text_response(reply(lop[20:])),
                                  text_response(reply([disc(Words(11).many(3), language="en")]))])
    r = run(biz, tmp_path, client)
    assert len(client.messages.calls) <= 3 and run_count(biz) <= 3


def test_nothing_arrived_whole_still_gets_one_follow_up_for_everything(biz, tmp_path):
    diagnosed(biz, tmp_path)
    client = FakeAnthropicClient([cut_off('{"local": true, "local_reason": "a clinic", "questions": [{"text": "emergency den'),
                                  text_response(reply(clean_set(biz)))])
    r = run(biz, tmp_path, client)
    retry = sent_to_model(client, 1)["retry"]
    assert retry["accepted"] == [] and sum(n["count"] for n in retry["needed"]) == 32 and len(file_items(r)) == 32


def test_a_grouped_v4_reply_gives_the_same_file_as_the_flat_one(biz, tmp_path):
    diagnosed(biz, tmp_path)
    full = clean_set(biz)

    def grouped(items):
        out = {"local": True, "local_reason": "it has a street address", "discovery": [], "control": [], "brand": []}
        for x in items:
            if x["kind"] == "brand":
                out["brand"].append({"text": x["text"], "language": x["language"], "fact_topic": x["fact_topic"]})
            elif x["control"]:
                out["control"].append({"text": x["text"], "language": x["language"]})
            else:
                d = {"text": x["text"], "language": x["language"], "intent": x["intent"], "need": x["need"]}
                if x["is_target"]:
                    d["is_target"] = True
                out["discovery"].append(d)
        return json.dumps(out)

    a = run(biz, tmp_path, fake(full))
    b = run(biz, tmp_path, FakeAnthropicClient([text_response(grouped(full))]))
    strip = lambda r: [{k: v for k, v in i.items() if k != "spot_check"} for i in file_items(r)]     # noqa: E731
    assert strip(a) == strip(b) and b.rejected == [] and not any(i.get("needs_review") for i in file_items(b))


def test_one_language_needs_no_language_field_in_the_reply(biz, tmp_path):
    diagnosed(biz, tmp_path)
    one = [x for x in clean_set(biz, ["en"])]
    for x in one:
        x["language"] = None
    r = run(biz, tmp_path, fake(one), languages=("en",), size=16)
    assert all(i["language"] == "en" for i in file_items(r)) and not any(i.get("needs_review") for i in file_items(r))


def test_a_run_that_completes_normally_behaves_as_before(biz, tmp_path):
    diagnosed(biz, tmp_path)
    r = run(biz, tmp_path, fake(clean_set(biz)))
    assert r.calls == 1 and r.topped_up is None and len(file_items(r)) == 32
    assert not any("cut off" in w for w in r.warnings)
    assert [(x[0], x[1]) for x in runs_of(biz["id"])] == [("success", "end_turn")]


# ---- 5. the cost card ----------------------------------------------------------------------------------------------------------------------------

def test_the_cost_card_uses_the_new_cap_for_its_up_to_figure(biz, tmp_path, own_history, monkeypatch):
    diagnosed(biz, tmp_path)
    said: list = []
    run(biz, tmp_path, fake(clean_set(biz)), said=said)
    card = "\n".join(said)
    assert "output capped at 6,000 tokens per call (thinking off)" in card
    up_to = figure(said, "Up to $")
    first_in = max(1, int(sent_tokens(card)))
    expected = (llm.estimate_cost(qrun.MODEL, first_in, 6000)
                + 2 * llm.estimate_cost(qrun.MODEL, first_in + 6000 + 32 * qrun.FOLLOW_UP_TOKENS_PER_QUESTION + qrun.FOLLOW_UP_EXTRA_TOKENS, 6000))
    assert up_to == pytest.approx(expected, abs=0.001)

    monkeypatch.setattr(qrun.DRAFT_AGENT, "max_tokens", 9000)                      # a different cap gives a different figure
    said2: list = []
    run(biz, tmp_path, fake(clean_set(biz)), said=said2)
    assert figure(said2, "Up to $") > up_to and "capped at 9,000" in "\n".join(said2)


def sent_tokens(card: str) -> int:
    line = next(s for s in card.splitlines() if "Input about" in s)
    return int(line.split("Input about")[1].split("tokens")[0].replace(",", "").strip())


# ---- 6. "Typical" counts failed and cut-off drafts --------------------------------------------------------------------------------------------------

def test_a_draft_that_fails_after_spending_money_counts_in_the_typical_figure(biz, tmp_path, own_history):
    diagnosed(biz, tmp_path)
    with pytest.raises(DraftError):
        run(biz, tmp_path, FakeAnthropicClient([text_response("not json"), text_response("still not json")]))
    spent = sum(float(x[3]) for x in runs_of(biz["id"]))
    assert spent > 0
    conn = get_connection()
    try:
        notes = conn.execute("SELECT details FROM client_journal WHERE actor = %s", (qrun.ACTOR,)).fetchall()
    finally:
        conn.close()
    assert len(notes) == 1 and notes[0][0]["outcome"] == "failed" and notes[0][0]["cost_usd"] == pytest.approx(spent)
    said: list = []
    run(biz, tmp_path, fake(clean_set(biz)), said=said)
    assert f"Typical ${spent:.3f}" in "\n".join(said) and "average of the last 1 run(s)" in "\n".join(said)


def test_a_command_that_stops_before_any_model_call_writes_no_note(biz, tmp_path, own_history):
    diagnosed(biz, tmp_path)
    with pytest.raises(Exception):
        run(biz, tmp_path, fake(clean_set(biz)), ask=lambda p: "no")
    conn = get_connection()
    try:
        assert conn.execute("SELECT count(*) FROM client_journal WHERE actor = %s", (qrun.ACTOR,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_the_no_place_stop_costs_one_call_and_that_call_counts_as_typical(biz, tmp_path, own_history):
    diagnosed(biz, tmp_path)
    with pytest.raises(DraftError):
        run(biz, tmp_path, FakeAnthropicClient([text_response(json.dumps({"local": True, "local_reason": "a clinic"}))]), location=None)
    spent = sum(float(x[3]) for x in runs_of(biz["id"]))
    said: list = []
    run(biz, tmp_path, fake(clean_set(biz)), said=said)
    assert f"Typical ${spent:.3f}" in "\n".join(said)


def test_a_cut_off_draft_that_finishes_counts_with_the_cut_off_call_included(biz, tmp_path, own_history):
    diagnosed(biz, tmp_path)
    full = clean_set(biz)
    r = run(biz, tmp_path, FakeAnthropicClient([cut_off(cut_reply(full[:20])), text_response(reply(full[20:]))]))
    said: list = []
    run(biz, tmp_path, fake(clean_set(biz)), said=said)
    assert f"Typical ${r.cost['cost_usd']:.3f}" in "\n".join(said)


# ---- 7. the v4 prompt, the older ones, PLAN.md ------------------------------------------------------------------------------------------------------

def test_the_v4_reply_shape_does_not_ask_for_what_the_code_knows():
    v4 = (ROOT / "prompts/questions_draft/v4.md").read_text()
    shape = v4[v4.index("exactly this shape:"):v4.index("Write \"language\"")]
    for derived in ('"kind"', '"control": true', '"control": false', '"length"', '"relies_on_fact_ids"', '"place"', '"questions"', '"is_target": false'):
        assert derived not in shape, derived
    assert '"discovery":' in shape and '"control":' in shape and '"brand":' in shape
    assert "cut_off" in v4 and "no \"kind\"" in v4
    assert len(v4) < len((ROOT / "prompts/questions_draft/v3.md").read_text()) * 1.1


def test_plan_has_row_25f_and_the_corrected_url_wording():
    plan_text = (ROOT / "PLAN.md").read_text()
    assert "~10 brand (one asks with the URL)." in plan_text and "half with the URL" not in plan_text
    row = next(l for l in plan_text.splitlines() if l.startswith("| 25f |"))
    assert row.rstrip().endswith("✅ Done |")
    lines = plan_text.splitlines()
    assert lines.index(row) == next(i for i, l in enumerate(lines) if l.startswith("| 25e |")) + 1
