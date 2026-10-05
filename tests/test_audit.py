"""Tests for `aeo add` and `aeo audit --mode manual`.

Real test database (aeo_test), fake Anthropic client, scripted operator input.
Nothing here touches the network or a real model.
"""

from __future__ import annotations

import json
import uuid

import pytest
from typer.testing import CliRunner

from aeo.cli import app
from audit import store
from audit.derive import count_claims
from audit.manual import AuditError, run_manual_audit
from audit.question_set import QuestionSetError, load_question_set
from conftest import FakeAnthropicClient, text_response
from core.businesses import BusinessError, add_business
from core.db import get_connection

Q1 = "who supplies water refill stations for events?"
Q2 = "best refill station rental in Canada"


# ---- helpers ------------------------------------------------------------------

def query(sql, params=()):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


@pytest.fixture
def biz():
    """A real business made through `aeo add`'s own function, with a unique domain."""
    domain = f"audit-test-{uuid.uuid4().hex[:12]}.example"
    return add_business(f"https://{domain}", name="Acme Refill")


@pytest.fixture
def qfile(tmp_path):
    """Writes a question set file with a unique version name, so tests never share a version."""
    def make(questions=(Q1, Q2), name=None):
        path = tmp_path / f"{name or 't' + uuid.uuid4().hex[:10]}_v1.yaml"
        path.write_text("questions:\n" + "".join(f"  - {json.dumps(q)}\n" for q in questions))
        return path
    return make


def extraction(*businesses):
    """A queued fake model reply for the extraction job. Each item: (name, is_client, recommended)."""
    return text_response(json.dumps({"businesses": [
        {"name": n, "domain": None, "is_client": c, "recommended": r} for n, c, r in businesses]}))


def claims_reply(*claims):
    return text_response(json.dumps({"claims": [
        {"claim": c, "field": f, "verdict": v, "note": "n"} for c, f, v in claims]}))


class Operator:
    """Plays the person at the keyboard. Each entry in `lines` is one typed line."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.prompts: list[str] = []
        self.said: list[str] = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)

    def say(self, text):
        self.said.append(text)


def answer(*text_lines, retrieval="y", version="gpt-5-thinking"):
    """The lines a person types for one question on one engine."""
    return [*text_lines, "END", retrieval, version]


def audit(biz, qpath, op, client, checkpoint=None):
    return run_manual_audit(biz["id"], str(qpath), checkpoint=checkpoint, ask=op.ask, say=op.say, client=client)


# ---- aeo add ------------------------------------------------------------------

def test_add_creates_a_prospect_with_an_empty_version_1_profile(biz):
    status, name, domain = query("SELECT status, name, domain FROM businesses WHERE id = %s", (biz["id"],))[0]
    assert (status, name) == ("prospect", "Acme Refill")
    version, source, phone, industry = query(
        "SELECT version, source, phone, industry FROM business_profiles WHERE business_id = %s", (biz["id"],))[0]
    assert (version, source, phone, industry) == (1, "client", None, None)
    types = {r[0] for r in query("SELECT entry_type FROM client_journal WHERE business_id = %s", (biz["id"],))}
    assert {"business_added", "profile_created"} <= types


def test_add_refuses_a_domain_that_already_exists_and_changes_nothing(biz):
    with pytest.raises(BusinessError, match="already added"):
        add_business(biz["website_url"], name="Other Name")
    # The www. form is the same business too.
    with pytest.raises(BusinessError, match="already added"):
        add_business("https://www." + biz["domain"])
    assert query("SELECT count(*) FROM businesses WHERE domain = %s", (biz["domain"],))[0][0] == 1
    assert query("SELECT count(*) FROM business_profiles WHERE business_id = %s", (biz["id"],))[0][0] == 1
    assert query("SELECT name FROM businesses WHERE id = %s", (biz["id"],))[0][0] == "Acme Refill"


def test_cli_add_duplicate_exits_with_a_clear_error(biz):
    result = CliRunner().invoke(app, ["add", biz["website_url"]])
    assert result.exit_code == 1
    assert "already added" in result.output


def test_cli_add_works_and_prints_the_id():
    domain = f"cli-add-{uuid.uuid4().hex[:12]}.example"
    result = CliRunner().invoke(app, ["add", domain, "--name", "Cli Co"])
    assert result.exit_code == 0, result.output
    assert "Business id:" in result.output
    assert query("SELECT name FROM businesses WHERE domain = %s", (domain,))[0][0] == "Cli Co"


# ---- question set file --------------------------------------------------------

def test_question_set_loads_and_uses_the_file_name_as_its_version(qfile):
    path = qfile(name="oland-stations")
    qs = load_question_set(path)
    assert qs.version == "oland-stations_v1"
    assert qs.questions == [Q1, Q2]


@pytest.mark.parametrize("body, message", [
    ("questions: []\n", "non-empty"),
    ("questions:\n  - a\n  - a\n", "twice"),
    ("questions:\n  - a\nintent: commercial\n", "not supported yet"),
    ("- a\n- b\n", "questions:"),
])
def test_bad_question_sets_are_refused(tmp_path, body, message):
    path = tmp_path / "x_v1.yaml"
    path.write_text(body)
    with pytest.raises(QuestionSetError, match=message):
        load_question_set(path)


def test_question_set_name_must_end_in_a_version(tmp_path):
    path = tmp_path / "questions.yaml"
    path.write_text("questions:\n  - a\n")
    with pytest.raises(QuestionSetError, match="_v1"):
        load_question_set(path)


def test_a_used_question_set_cannot_be_edited(biz, qfile):
    path = qfile(questions=(Q1,), name=f"frozen{uuid.uuid4().hex[:8]}")
    audit(biz, path, Operator(["y", ""] + answer("a") + answer("b")), FakeAnthropicClient([extraction(), extraction()]))
    # Same version, one question changed -> refused, with the way out spelled out.
    path.write_text(f'questions:\n  - "{Q1} (reworded)"\n')
    with pytest.raises(AuditError, match="never edited.*_v2"):
        audit(biz, path, Operator([]), FakeAnthropicClient([]))
    # Comments and spacing are not questions, so they may change.
    path.write_text(f'# a comment\nquestions:\n  -   "{Q1}"\n')
    load_question_set(path)


def test_a_question_set_that_collected_nothing_may_still_be_fixed(biz, qfile):
    path = qfile(questions=(Q1,))
    out = audit(biz, path, Operator(["y", "", "/quit"]), FakeAnthropicClient([]))
    assert out.counts["answers"] == 0
    path.write_text('questions:\n  - "a typo-free version"\n')
    audit(biz, path, Operator(["y", "", "/quit"]), FakeAnthropicClient([]))   # not refused


# ---- the manual flow ----------------------------------------------------------

def test_the_flow_collects_every_field_for_every_question_and_engine(biz, qfile):
    path = qfile()
    typed = ["y", "Montreal"]                       # logged out? / location
    typed += answer("chatgpt says Acme Refill", retrieval="y", version="gpt-5-thinking")
    typed += answer("perplexity one", retrieval="n", version="")
    typed += answer("chatgpt two", retrieval="unclear", version="gpt-5-instant")
    typed += answer("perplexity two", retrieval="y", version="sonar")
    op = Operator(typed)
    client = FakeAnthropicClient([
        extraction(("Acme Refill", True, True), ("Rival Co", False, False)),
        extraction(("Rival Co", False, True)),
        extraction(),
        extraction(("Acme Refill", True, False)),
    ])
    out = audit(biz, path, op, client, checkpoint="old_site")

    # The operator was shown the exact question and the engine, in order.
    shown = "\n".join(op.said)
    assert Q1 in shown and Q2 in shown
    assert shown.index("CHATGPT") < shown.index("PERPLEXITY")

    rows = query("SELECT query_text, engine, repeat_number, raw_response_text, retrieval_activated, engine_version, "
                 "logged_in_state, location_context, question_set_version, named_any_business, recognized, recommended "
                 "FROM probe_results WHERE probe_run_id = %s ORDER BY created_at, id", (out.probe_run_id,))
    qv = load_question_set(path).version
    assert rows == [
        (Q1, "chatgpt", 1, "chatgpt says Acme Refill", True, "gpt-5-thinking", "logged_out", "Montreal", qv, True, True, True),
        (Q1, "perplexity", 1, "perplexity one", False, None, "logged_out", "Montreal", qv, True, False, False),
        (Q2, "chatgpt", 1, "chatgpt two", None, "gpt-5-instant", "logged_out", "Montreal", qv, False, False, False),
        (Q2, "perplexity", 1, "perplexity two", True, "sonar", "logged_out", "Montreal", qv, True, True, False),
    ]

    run = query("SELECT run_type, mode, status, checkpoint, completed_at IS NOT NULL, gate_check_id "
                "FROM probe_runs WHERE id = %s", (out.probe_run_id,))[0]
    assert run == ("visibility", "manual", "complete", "old_site", True, None)
    types = [r[0] for r in query("SELECT entry_type FROM client_journal WHERE business_id = %s "
                                 "AND entry_type IN ('probe_started','probe_completed')", (biz["id"],))]
    assert sorted(types) == ["probe_completed", "probe_started"]

    assert out.complete and out.counts == {"answers": 4, "named_business": 2, "recommended_business": 1, "not_analysed": 0}
    # Business names, not the client's own facts, went to the model: one call per answer, nothing more.
    assert len(client.messages.calls) == 4


def test_a_pasted_answer_is_stored_word_for_word_including_blank_lines(biz, qfile):
    path = qfile(questions=(Q1,))
    pasted = ["First paragraph.", "", "  - indented bullet", "Last line"]
    op = Operator(["y", ""] + answer(*pasted) + answer("x"))
    out = audit(biz, path, op, FakeAnthropicClient([extraction(), extraction()]))
    stored = query("SELECT raw_response_text FROM probe_results WHERE probe_run_id = %s AND engine = 'chatgpt'",
                   (out.probe_run_id,))[0][0]
    assert stored == "\n".join(pasted)


def test_a_bad_yes_no_reply_is_asked_again(biz, qfile):
    path = qfile(questions=(Q1,))
    typed = ["maybe", "y", ""] + ["answer", "END", "perhaps", "n", ""] + answer("two")
    op = Operator(typed)
    out = audit(biz, path, op, FakeAnthropicClient([extraction(), extraction()]))
    assert out.complete
    assert sum("Please type" in s for s in op.said) == 2
    assert query("SELECT retrieval_activated FROM probe_results WHERE probe_run_id = %s AND engine = 'chatgpt'",
                 (out.probe_run_id,))[0][0] is False


def test_repeat_number_goes_up_on_a_repeat_and_nothing_is_overwritten(biz):
    conn = get_connection()
    try:
        profile = store.load_business_and_profile(conn, biz["id"])
        run_id = store.create_run(conn, business=profile, checkpoint=None)
        kw = dict(probe_run_id=run_id, question=Q1, engine="chatgpt", retrieval_activated=True,
                  engine_version=None, logged_in_state="logged_out", location_context=None,
                  question_set_version="x_v1")
        _, first = store.add_result(conn, raw_answer="one", **kw)
        _, second = store.add_result(conn, raw_answer="two", **kw)
        _, other_engine = store.add_result(conn, raw_answer="three", **{**kw, "engine": "perplexity"})
    finally:
        conn.close()
    assert (first, second, other_engine) == (1, 2, 1)
    texts = [r[0] for r in query("SELECT raw_response_text FROM probe_results WHERE probe_run_id = %s "
                                 "AND engine = 'chatgpt' ORDER BY repeat_number", (run_id,))]
    assert texts == ["one", "two"]


def test_quitting_early_keeps_what_was_entered_and_marks_the_run_failed(biz, qfile):
    path = qfile()                                     # 2 questions x 2 engines = 4 answers
    op = Operator(["y", ""] + answer("only answer") + ["/quit"])
    client = FakeAnthropicClient([extraction()])
    out = audit(biz, path, op, client)

    assert not out.complete and len(out.missing) == 3
    assert query("SELECT status, completed_at FROM probe_runs WHERE id = %s", (out.probe_run_id,))[0] == ("failed", None)
    assert query("SELECT count(*) FROM probe_results WHERE probe_run_id = %s", (out.probe_run_id,))[0][0] == 1
    note = query("SELECT details FROM client_journal WHERE business_id = %s AND entry_type = 'note' "
                 "AND details->>'probe_run_id' = %s", (biz["id"], out.probe_run_id))[0][0]
    assert note["answers"] == 1 and note["answers_missing"] == 3 and len(note["missing"]) == 3
    assert query("SELECT count(*) FROM client_journal WHERE business_id = %s AND entry_type = 'probe_completed'",
                 (biz["id"],))[0][0] == 0
    assert len(client.messages.calls) == 1        # the one answer that exists was still read


def test_input_running_out_counts_as_quitting(biz, qfile):
    path = qfile(questions=(Q1,))
    out = audit(biz, path, Operator(["y", ""] + ["half an answer", "END", "y"]), FakeAnthropicClient([]))
    assert not out.complete and out.counts["answers"] == 0     # the half-entered one was not saved


# ---- the AI jobs --------------------------------------------------------------

def run_one_question(biz, qfile, replies):
    """One question, two engines, each answer 'x'."""
    path = qfile(questions=(Q1,))
    client = FakeAnthropicClient(replies)
    out = audit(biz, path, Operator(["y", ""] + answer("x") + answer("y")), client)
    return out, client


def agents_logged(biz):
    return [r[0] for r in query("SELECT agent FROM runs WHERE business_id = %s ORDER BY created_at, id", (biz["id"],))]


def test_claim_check_is_not_called_when_there_are_no_confirmed_facts(biz, qfile):
    # The client IS named and recommended in both answers, but the profile holds no facts.
    out, client = run_one_question(biz, qfile, [extraction(("Acme Refill", True, True)),
                                                extraction(("Acme Refill", True, True))])
    assert out.derive.claims_skipped == 2 and out.derive.claims_checked == 0
    assert len(client.messages.calls) == 2                                  # extraction only
    assert agents_logged(biz) == ["audit_extract", "audit_extract"]
    assert query("SELECT count(*) FROM probe_results WHERE probe_run_id = %s AND claims_checked IS NOT NULL",
                 (out.probe_run_id,))[0][0] == 0


def test_an_empty_list_or_empty_text_is_not_a_confirmed_fact(biz, qfile):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE business_profiles SET services = '{}', phone = '' WHERE business_id = %s", (biz["id"],))
    finally:
        conn.close()
    out, client = run_one_question(biz, qfile, [extraction(("Acme Refill", True, True))] * 2)
    assert len(client.messages.calls) == 2


def test_claim_check_runs_when_facts_exist_and_the_client_is_named(biz, qfile):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE business_profiles SET phone = '514-555-0100', location_city = 'Montreal' "
                        "WHERE business_id = %s", (biz["id"],))
    finally:
        conn.close()
    claims = claims_reply(("Phone is 514-555-0100", "phone", "correct"),
                          ("Only serves Ontario", "location_city", "incorrect"),
                          ("Founded in 2015", None, "unverifiable"))
    out, client = run_one_question(biz, qfile, [
        extraction(("Acme Refill", True, False)), claims,        # answer 1: client named -> checked
        extraction(("Rival Co", False, True)),                   # answer 2: client not named -> not checked
    ])
    assert out.derive.claims_checked == 1 and out.derive.claims_skipped == 1
    assert agents_logged(biz) == ["audit_extract", "audit_claim_check", "audit_extract"]

    sent = json.loads(client.messages.calls[1]["messages"][0]["content"])
    assert {f["field"] for f in sent["confirmed_facts"]} == {"phone", "location_city"}

    stored = query("SELECT claims_checked FROM probe_results WHERE probe_run_id = %s AND claims_checked IS NOT NULL",
                   (out.probe_run_id,))
    assert len(stored) == 1
    verdicts = [c["verdict"] for c in stored[0][0]]
    assert verdicts == ["correct", "incorrect", "unverifiable"]
    assert count_claims(stored[0][0]) == (1, 1)         # unverifiable is left out, as accuracy_score expects


def test_a_model_reply_that_is_not_valid_goes_to_the_human_queue_and_the_rest_carry_on(biz, qfile):
    bad = text_response("not json at all")
    out, client = run_one_question(biz, qfile, [bad, bad, extraction(("Acme Refill", True, True))])
    assert out.derive.extraction_failed == 1 and out.derive.extracted == 1
    assert out.counts["not_analysed"] == 1 and out.counts["answers"] == 2     # the raw answer is kept
    assert query("SELECT count(*) FROM human_queue WHERE business_id = %s", (biz["id"],))[0][0] == 1


# ---- the command line ---------------------------------------------------------

def test_batch_mode_says_it_is_not_built_yet(biz, qfile):
    result = CliRunner().invoke(app, ["audit", "--business", biz["id"], "--question-set", str(qfile()),
                                      "--mode", "batch"])
    assert result.exit_code == 1
    assert "not built yet" in result.output and "ten times" in result.output
    assert query("SELECT count(*) FROM probe_runs WHERE business_id = %s", (biz["id"],))[0][0] == 0


def test_audit_for_an_unknown_business_is_refused(qfile):
    result = CliRunner().invoke(app, ["audit", "--business", str(uuid.uuid4()), "--question-set", str(qfile())],
                                input="y\n")
    assert result.exit_code == 1 and "no business with id" in result.output


def test_cli_end_to_end_prints_the_short_summary(biz, qfile, monkeypatch):
    fake = FakeAnthropicClient([extraction(("Acme Refill", True, True)), extraction(("Rival Co", False, False))])
    monkeypatch.setattr("core.llm.anthropic.Anthropic", lambda: fake)
    typed = "\n".join(["y", ""] + answer("a1") + answer("a2")) + "\n"
    result = CliRunner().invoke(app, ["audit", "--business", biz["id"], "--question-set",
                                      str(qfile(questions=(Q1,))), "--checkpoint", "old_site"], input=typed)
    assert result.exit_code == 0, result.output
    assert "Audit complete." in result.output
    assert "Answers collected: 2" in result.output
    assert "Answers that named the business: 1" in result.output
    assert "Answers that recommended the business: 1" in result.output
    run_id = result.output.split("Run id: ")[1].strip()
    assert query("SELECT checkpoint, status FROM probe_runs WHERE id = %s", (run_id,))[0] == ("old_site", "complete")
