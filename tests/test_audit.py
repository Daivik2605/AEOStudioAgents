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
    # A unique name too: `aeo add` stops to ask when a name matches, and the test database keeps old rows.
    return add_business(f"https://{domain}", name=f"Acme Refill {uuid.uuid4().hex[:10]}")


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
    assert (status, name) == ("prospect", biz["name"])
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
    assert query("SELECT name FROM businesses WHERE id = %s", (biz["id"],))[0][0] == biz["name"]


def test_cli_add_duplicate_exits_with_a_clear_error(biz):
    result = CliRunner().invoke(app, ["add", biz["website_url"]])
    assert result.exit_code == 1
    assert "already added" in result.output


def test_cli_add_works_and_prints_the_id():
    domain = f"cli-add-{uuid.uuid4().hex[:12]}.example"
    name = f"Cli Co {uuid.uuid4().hex[:8]}"
    result = CliRunner().invoke(app, ["add", domain, "--name", name])
    assert result.exit_code == 0, result.output
    assert "Business id:" in result.output
    assert query("SELECT name FROM businesses WHERE domain = %s", (domain,))[0][0] == name


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


# ---- intent / is_target in the question set -----------------------------------

def write_tagged(path, entries):
    """entries: list of strings, or dicts of text/intent/is_target."""
    import yaml
    path.write_text(yaml.safe_dump({"questions": entries}, sort_keys=False, allow_unicode=True))
    return path


def test_tags_are_read_and_plain_questions_still_work(tmp_path):
    path = write_tagged(tmp_path / "t_v1.yaml", [
        {"text": Q1, "intent": "commercial", "is_target": True}, Q2, {"text": "third"}])
    qs = load_question_set(path)
    assert [(q.text, q.intent, q.is_target) for q in qs.items] == [
        (Q1, "commercial", True), (Q2, None, False), ("third", None, False)]
    assert qs.questions == [Q1, Q2, "third"]


@pytest.mark.parametrize("entry, message", [
    ({"text": "a", "intent": "shopping"}, "intent must be one of"),
    ({"text": "a", "is_target": "yes"}, "true or false"),
    ({"text": "a", "colour": "red"}, "not supported"),
    ({"intent": "commercial"}, "non-empty text"),
])
def test_bad_tags_are_refused(tmp_path, entry, message):
    with pytest.raises(QuestionSetError, match=message):
        load_question_set(write_tagged(tmp_path / "t_v1.yaml", [entry]))


def test_a_set_with_no_tags_keeps_the_fingerprint_it_had_before_tags_existed(tmp_path):
    """Sets already used in a real audit must not suddenly look 'edited'."""
    import hashlib
    path = write_tagged(tmp_path / "t_v1.yaml", [Q1, {"text": Q2}, {"text": "c", "is_target": False}])
    old_style = hashlib.sha256(json.dumps([Q1, Q2, "c"], ensure_ascii=False).encode()).hexdigest()
    assert load_question_set(path).fingerprint == old_style


def test_a_target_on_an_informational_question_warns_and_the_run_still_happens(biz, tmp_path):
    path = write_tagged(tmp_path / f"w{uuid.uuid4().hex[:8]}_v1.yaml", [
        {"text": "what is a refill station", "intent": "informational", "is_target": True},
        {"text": Q1, "intent": "commercial", "is_target": True},
        {"text": Q2, "intent": "informational", "is_target": False}])      # not a target: no warning
    # Answer the first question on both engines, then quit: the run must have started.
    op = Operator(["y", ""] + answer("a") + answer("b") + ["/quit"])
    out = audit(biz, path, op, FakeAnthropicClient([extraction(), extraction()]))
    warned = [s for s in op.said if s.startswith("WARNING")]
    assert len(warned) == 1 and "what is a refill station" in warned[0]
    assert "0.9%" in warned[0] and "86.5%" in warned[0]
    assert Q1 not in warned[0] and Q2 not in warned[0]
    assert out.warnings == [w[len("WARNING: "):] for w in warned]
    assert out.counts["answers"] == 2                                   # it ran


def test_the_warning_comes_before_the_run_starts(biz, tmp_path):
    path = write_tagged(tmp_path / f"w{uuid.uuid4().hex[:8]}_v1.yaml",
                        [{"text": "info", "intent": "informational", "is_target": True}])
    op = Operator(["y", "", "/quit"])
    seen_when_asked = []
    original_ask = op.ask
    op.ask = lambda prompt: (seen_when_asked.append(any(s.startswith("WARNING") for s in op.said)), original_ask(prompt))[1]
    audit(biz, path, op, FakeAnthropicClient([]))
    assert seen_when_asked and all(seen_when_asked)


@pytest.mark.parametrize("edit", [
    lambda e: {**e, "intent": "informational"},              # intent changed
    lambda e: {k: v for k, v in e.items() if k != "intent"},   # intent removed
    lambda e: {**e, "is_target": False},                     # target flag changed
])
def test_changing_tags_on_a_used_version_is_refused_like_changing_text(biz, tmp_path, edit):
    path = tmp_path / f"lock{uuid.uuid4().hex[:8]}_v1.yaml"
    entry = {"text": Q1, "intent": "commercial", "is_target": True}
    write_tagged(path, [entry])
    audit(biz, path, Operator(["y", ""] + answer("a") + answer("b")), FakeAnthropicClient([extraction(), extraction()]))
    write_tagged(path, [edit(entry)])
    with pytest.raises(AuditError, match="never edited.*_v2"):
        audit(biz, path, Operator([]), FakeAnthropicClient([]))
    write_tagged(path, [entry])                                  # put it back: allowed again
    audit(biz, path, Operator(["y", "", "/quit"]), FakeAnthropicClient([]))


def test_adding_tags_to_an_untagged_used_version_is_also_refused(biz, tmp_path):
    path = write_tagged(tmp_path / f"lock{uuid.uuid4().hex[:8]}_v1.yaml", [Q1])
    audit(biz, path, Operator(["y", ""] + answer("a") + answer("b")), FakeAnthropicClient([extraction(), extraction()]))
    write_tagged(path, [{"text": Q1, "intent": "commercial"}])
    with pytest.raises(AuditError, match="never edited"):
        audit(biz, path, Operator([]), FakeAnthropicClient([]))


# ---- aeo add: duplicates ------------------------------------------------------

@pytest.fixture
def named():
    """An existing business with a unique name, and a helper that runs `aeo add` against it."""
    name = f"Dup Test {uuid.uuid4().hex[:10]}"
    existing = add_business(f"https://{uuid.uuid4().hex[:10]}-old.example", name=name)

    def run(args, *, interactive, answer_text=""):
        import aeo.cli
        aeo.cli._is_interactive = lambda: interactive
        return CliRunner().invoke(app, ["add", *args], input=answer_text)
    return existing, name, run


@pytest.fixture(autouse=True)
def _restore_interactive():
    import aeo.cli
    original = aeo.cli._is_interactive
    yield
    aeo.cli._is_interactive = original


def count_with_name(name):
    return query("SELECT count(*) FROM businesses WHERE name = %s", (name,))[0][0]


def test_a_matching_domain_names_the_existing_business(named):
    existing, name, run = named
    for flags in ([], ["--force-new"], ["--same-business", existing["id"]]):
        result = run([existing["domain"], "--name", name, *flags], interactive=True)
        assert result.exit_code == 1
        assert f"{existing['domain']} is already added, as {name} (id {existing['id']}" in result.output
        assert "Nothing was changed" in result.output
    assert count_with_name(name) == 1                 # --force-new did not create a duplicate


def test_a_matching_domain_wins_even_when_the_name_is_different(named):
    existing, name, run = named
    result = run([existing["domain"], "--name", "Something Else Entirely"], interactive=True)
    assert result.exit_code == 1 and existing["id"] in result.output


def test_same_name_other_domain_asks_and_yes_creates_nothing(named):
    existing, name, run = named
    new_domain = f"{uuid.uuid4().hex[:10]}-new.example"
    result = run([new_domain, "--name", name], interactive=True, answer_text="y\n")
    assert result.exit_code == 0, result.output
    assert f"already exists (id {existing['id']}, domain {existing['domain']})" in result.output
    assert "[y/N]" in result.output
    assert f"business id {existing['id']}" in result.output and "from now on" in result.output
    assert count_with_name(name) == 1
    assert query("SELECT count(*) FROM businesses WHERE domain = %s", (new_domain,))[0][0] == 0
    assert query("SELECT domain FROM businesses WHERE id = %s", (existing["id"],))[0][0] == existing["domain"]


@pytest.mark.parametrize("reply", ["n\n", "\n"])
def test_same_name_other_domain_no_or_enter_creates_a_new_business(named, reply):
    existing, name, run = named
    new_domain = f"{uuid.uuid4().hex[:10]}-new.example"
    result = run([new_domain, "--name", name], interactive=True, answer_text=reply)
    assert result.exit_code == 0, result.output
    assert "Business id:" in result.output
    assert count_with_name(name) == 2


def test_non_interactive_refuses_a_name_match_and_names_the_existing_id(named):
    existing, name, run = named
    new_domain = f"{uuid.uuid4().hex[:10]}-new.example"
    result = run([new_domain, "--name", name], interactive=False)
    assert result.exit_code == 1
    assert existing["id"] in result.output and "--same-business" in result.output and "--force-new" in result.output
    assert count_with_name(name) == 1


def test_same_business_flag_confirms_the_match_without_asking(named):
    existing, name, run = named
    result = run([f"{uuid.uuid4().hex[:10]}-new.example", "--name", name, "--same-business", existing["id"]],
                 interactive=False)
    assert result.exit_code == 0, result.output
    assert existing["id"] in result.output and count_with_name(name) == 1


def test_same_business_with_an_id_that_is_not_a_name_match_is_refused(named):
    existing, name, run = named
    other = add_business(f"https://{uuid.uuid4().hex[:10]}-other.example", name=f"Other {uuid.uuid4().hex[:8]}")
    result = run([f"{uuid.uuid4().hex[:10]}-new.example", "--name", name, "--same-business", other["id"]],
                 interactive=False)
    assert result.exit_code == 1 and "not one of the businesses named" in result.output
    assert count_with_name(name) == 1


def test_force_new_confirms_it_is_a_different_business_without_asking(named):
    existing, name, run = named
    result = run([f"{uuid.uuid4().hex[:10]}-new.example", "--name", name, "--force-new"], interactive=False)
    assert result.exit_code == 0, result.output
    assert count_with_name(name) == 2


def test_the_two_flags_together_are_refused(named):
    existing, name, run = named
    result = run([f"{uuid.uuid4().hex[:10]}-new.example", "--name", name, "--force-new",
                  "--same-business", existing["id"]], interactive=False)
    assert result.exit_code == 1 and "contradict" in result.output and count_with_name(name) == 1


def test_the_flags_do_nothing_when_no_name_matches(named):
    existing, name, run = named
    for flag in (["--force-new"], ["--same-business", existing["id"]]):
        fresh_name = f"Fresh {uuid.uuid4().hex[:10]}"
        result = run([f"{uuid.uuid4().hex[:10]}-new.example", "--name", fresh_name, *flag], interactive=False)
        assert result.exit_code == 0 and count_with_name(fresh_name) == 1, result.output


def test_no_name_given_means_no_name_check(named):
    existing, name, run = named
    result = run([f"{uuid.uuid4().hex[:10]}-new.example"], interactive=False)
    assert result.exit_code == 0 and "Business id:" in result.output


def test_name_matching_ignores_case_and_spaces(named):
    existing, name, run = named
    result = run([f"{uuid.uuid4().hex[:10]}-new.example", "--name", f"  {name.upper()} "], interactive=False)
    assert result.exit_code == 1 and existing["id"] in result.output
