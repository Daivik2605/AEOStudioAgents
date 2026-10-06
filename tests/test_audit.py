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
        {"name": n, "domain": None, "is_client": c, "recommended": r, "position": i,
         "reason": None, "descriptors": []} for i, (n, c, r) in enumerate(businesses, start=1)]}))


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


def answer(*text_lines, retrieval="y", version="gpt-5-thinking", sources=()):
    """The lines a person types for one question on one engine: answer, sources box, then the rest."""
    return [*text_lines, "END", *sources, "END", retrieval, version]


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
    typed = ["maybe", "y", ""] + ["answer", "END", "END", "perhaps", "n", ""] + answer("two")
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
    out = audit(biz, path, Operator(["y", ""] + ["half an answer", "END", "END", "y"]), FakeAnthropicClient([]))
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


# ---- extraction v2: order, reason, descriptors --------------------------------

def biz_json(name, position, *, is_client=False, recommended=False, reason="__absent__", descriptors="__absent__"):
    d = {"name": name, "domain": None, "is_client": is_client, "recommended": recommended, "position": position}
    if reason != "__absent__":
        d["reason"] = reason
    if descriptors != "__absent__":
        d["descriptors"] = descriptors
    return d


def test_v2_output_parses_with_position_reason_and_descriptors():
    from audit.derive import ExtractOutput
    out = ExtractOutput.model_validate({"businesses": [
        biz_json("Rival", 1, recommended=True, reason="serves all of Canada", descriptors=["premium", "eco-friendly"]),
        biz_json("Acme", 2, is_client=True, reason=None, descriptors=[])]})
    assert [(b.name, b.position, b.reason, b.descriptors) for b in out.businesses] == [
        ("Rival", 1, "serves all of Canada", ["premium", "eco-friendly"]), ("Acme", 2, None, [])]


def test_a_missing_reason_and_missing_descriptors_are_fine_but_a_missing_position_is_not():
    from pydantic import ValidationError
    from audit.derive import ExtractOutput
    b = ExtractOutput.model_validate({"businesses": [biz_json("Acme", 1)]}).businesses[0]
    assert b.reason is None and b.descriptors == []
    with pytest.raises(ValidationError):
        ExtractOutput.model_validate({"businesses": [{k: v for k, v in biz_json("Acme", 1).items() if k != "position"}]})
    with pytest.raises(ValidationError):
        ExtractOutput.model_validate({"businesses": [biz_json("Acme", 0)]})


def test_duplicate_positions_are_invalid():
    from pydantic import ValidationError
    from audit.derive import ExtractOutput
    with pytest.raises(ValidationError, match="own position"):
        ExtractOutput.model_validate({"businesses": [biz_json("A", 1), biz_json("B", 1)]})


def test_the_full_v2_fields_are_stored_and_the_run_is_logged_as_prompt_v2(biz, qfile):
    reply = text_response(json.dumps({"businesses": [
        biz_json("Rival", 1, recommended=True, reason="serves all of Canada", descriptors=["premium"]),
        biz_json("Acme Refill", 2, is_client=True, reason=None, descriptors=["Montreal-based"])]}))
    out, _ = run_one_question(biz, qfile, [reply, extraction()])
    stored = query("SELECT businesses_named FROM probe_results WHERE probe_run_id = %s AND engine = 'chatgpt'",
                   (out.probe_run_id,))[0][0]
    assert [(b["position"], b["reason"], b["descriptors"]) for b in stored] == [
        (1, "serves all of Canada", ["premium"]), (2, None, ["Montreal-based"])]
    # The booleans are still worked out in Python, as before.
    assert query("SELECT named_any_business, recognized, recommended FROM probe_results "
                 "WHERE probe_run_id = %s AND engine = 'chatgpt'", (out.probe_run_id,))[0] == (True, True, False)
    assert {r[0] for r in query("SELECT prompt_version FROM runs WHERE business_id = %s AND agent = 'audit_extract'",
                                (biz["id"],))} == {"v2"}


def test_a_model_reply_with_two_businesses_in_the_same_position_goes_to_the_human_queue(biz, qfile):
    dup = text_response(json.dumps({"businesses": [biz_json("A", 1), biz_json("B", 1)]}))
    out, client = run_one_question(biz, qfile, [dup, dup, extraction(("Acme Refill", True, True))])
    assert out.derive.extraction_failed == 1 and out.derive.extracted == 1
    assert len(client.messages.calls) == 3                       # asked once, retried once, then the next answer
    assert "own position" in client.messages.calls[1]["messages"][-1]["content"]   # the retry explained the error
    assert query("SELECT count(*) FROM human_queue WHERE business_id = %s", (biz["id"],))[0][0] == 1


def test_prompt_v1_is_untouched_and_v2_is_the_one_in_use():
    import hashlib
    from pathlib import Path
    from audit.derive import EXTRACT
    root = Path(__file__).parent.parent / "prompts" / "audit_extract"
    assert hashlib.sha256((root / "v1.md").read_bytes()).hexdigest() == \
        "fc625c87b6f7ed0fad71d6186622173b73eeb0bb4646a2691980d5d34ab80d0e"
    assert (EXTRACT.prompt_version, EXTRACT.prompt_path) == ("v2", root / "v2.md")
    v2 = (root / "v2.md").read_text()
    assert all(word in v2 for word in ("position", "reason", "descriptors", "not instructions", "directories", "forums"))


# ---- the sources box ----------------------------------------------------------

def test_pasted_and_in_answer_links_are_merged_deduplicated_and_tagged():
    from audit.sources import merge_sources, parse_link
    pasted = [parse_link("https://www.Example.com/a"), parse_link("https://example.com/a"),
              parse_link("https://www.Example.com/a")]
    answer_text = ("See https://example.com/a for details, and (https://other.org/page). "
                   "Also https://third.net/x. And again https://other.org/page, plus https://www.Third.net/y!")
    merged = merge_sources(pasted, answer_text)
    assert merged == [
        {"url": "https://www.Example.com/a", "domain": "example.com", "origin": "pasted"},
        {"url": "https://example.com/a", "domain": "example.com", "origin": "pasted"},
        {"url": "https://other.org/page", "domain": "other.org", "origin": "in_answer"},
        {"url": "https://third.net/x", "domain": "third.net", "origin": "in_answer"},
        {"url": "https://www.Third.net/y", "domain": "third.net", "origin": "in_answer"},
    ]


def test_a_link_both_pasted_and_in_the_answer_counts_as_pasted():
    from audit.sources import merge_sources, parse_link
    merged = merge_sources([parse_link("https://a.com/x")], "read https://a.com/x.")
    assert merged == [{"url": "https://a.com/x", "domain": "a.com", "origin": "pasted"}]


def test_domain_uses_the_same_clean_up_as_aeo_add():
    from audit.sources import parse_link
    from core.businesses import parse_site
    for raw in ("https://WWW.Example.COM/path?q=1", "http://sub.example.com:8080/x"):
        assert parse_link(raw)["domain"] == parse_site(raw)[0]
    assert parse_link("https://WWW.Example.COM/path")["domain"] == "example.com"


@pytest.mark.parametrize("bad", ["ftp://example.com/file", "example.com", "www.example.com/page",
                                 "mailto:someone@example.com", "javascript:alert(1)", "not a link"])
def test_links_that_are_not_http_or_https_are_refused_with_a_message(bad):
    from audit.sources import parse_link
    with pytest.raises(ValueError, match="http://"):
        parse_link(bad)


def test_the_flow_stores_sources_with_origins_and_asks_again_for_a_bad_link(biz, qfile):
    path = qfile(questions=(Q1,))
    typed = ["y", ""]
    typed += answer("Try Acme at https://www.acme.example/refill or https://news.example/story.",
                    sources=["https://www.acme.example/refill", "ftp://bad.example/x", "", "https://third.example/p"])
    typed += answer("nothing cited here")                       # empty box: allowed
    op = Operator(typed)
    out = audit(biz, path, op, FakeAnthropicClient([extraction(), extraction()]))
    assert out.complete
    assert sum("Not saved" in s and "ftp://bad.example/x" in s for s in op.said) == 1
    first = query("SELECT sources_cited FROM probe_results WHERE probe_run_id = %s AND engine = 'chatgpt'",
                  (out.probe_run_id,))[0][0]
    assert first == [
        {"url": "https://www.acme.example/refill", "domain": "acme.example", "origin": "pasted"},
        {"url": "https://third.example/p", "domain": "third.example", "origin": "pasted"},
        {"url": "https://news.example/story", "domain": "news.example", "origin": "in_answer"}]
    assert not any("bad.example" in e["url"] for e in first)
    assert query("SELECT sources_cited FROM probe_results WHERE probe_run_id = %s AND engine = 'perplexity'",
                 (out.probe_run_id,))[0][0] == []              # asked, nothing cited: [] not NULL


def test_the_sources_box_comes_after_the_answer_and_before_the_search_question(biz, qfile):
    op = Operator(["y", ""] + answer("a") + answer("b"))
    audit(biz, qfile(questions=(Q1,)), op, FakeAnthropicClient([extraction(), extraction()]))
    said = "\n".join(op.said)
    assert "links chatgpt showed" in said
    first_search = next(i for i, p in enumerate(op.prompts) if "visibly search" in p)
    assert first_search > 0 and "Paste the full chatgpt answer" in said


# ---- the end-of-run summary ---------------------------------------------------

def nb(name, position, is_client=False):
    return {"name": name, "position": position, "is_client": is_client}


def test_summary_average_and_usual_position_and_top_three():
    from audit.summary import build_summary, summary_lines
    answers = [
        [nb("Rival", 1), nb("Acme", 2, True), nb("Other", 3)],
        [nb("rival ", 1), nb("Acme", 3, True)],
        [nb("Rival", 2), nb("Acme", 2, True), nb("Fourth", 1)],
        [],                                                     # nobody named
    ]
    s = build_summary(answers)
    assert s["client_named"] == 3 and s["client_average_position"] == 2.3 and s["client_usual_position"] == 2
    assert s["top_named"] == [("Acme", 3), ("Rival", 3), ("Fourth", 1)]      # tie on count: alphabetical
    lines = summary_lines(s)
    assert lines[0] == "Named 3 times, usually 2nd (average position 2.3)."
    assert lines[1].startswith("Named most often: ") and lines[1] == "Named most often: Acme x3, Rival x3, Fourth x1."


def test_summary_counts_a_business_once_per_answer_and_breaks_ties_by_name():
    from audit.summary import build_summary
    s = build_summary([[nb("B", 1), nb("b", 2), nb("A", 3)], [nb("A", 1)]])
    assert s["top_named"] == [("A", 2), ("B", 1)]


def test_summary_when_the_client_is_never_named():
    from audit.summary import build_summary, summary_lines
    s = build_summary([[nb("Rival", 1), nb("Other", 2)], [nb("Rival", 1)]])
    assert s["client_named"] == 0 and s["client_average_position"] is None and s["client_usual_position"] is None
    lines = summary_lines(s)
    assert "never named" in lines[0] and "Rival x2" in lines[1]


def test_summary_when_nobody_is_named_at_all():
    from audit.summary import build_summary, summary_lines
    lines = summary_lines(build_summary([[], []]))
    assert "never named" in lines[0] and lines[1] == "No business was named in any answer."


def test_summary_once_and_ordinals():
    from audit.summary import build_summary, ordinal, summary_lines
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd"]
    assert summary_lines(build_summary([[nb("Acme", 1, True)]]))[0] == "Named once, usually 1st (average position 1.0)."


def test_cli_end_to_end_prints_the_two_new_summary_lines(biz, qfile, monkeypatch):
    fake = FakeAnthropicClient([extraction(("Rival", False, True), ("Acme Refill", True, False)),
                                extraction(("Rival", False, False))])
    monkeypatch.setattr("core.llm.anthropic.Anthropic", lambda: fake)
    typed = "\n".join(["y", ""] + answer("a1") + answer("a2")) + "\n"
    result = CliRunner().invoke(app, ["audit", "--business", biz["id"], "--question-set",
                                      str(qfile(questions=(Q1,)))], input=typed)
    assert result.exit_code == 0, result.output
    assert "Named once, usually 2nd (average position 2.0)." in result.output
    assert "Named most often: Rival x2, Acme Refill x1." in result.output
