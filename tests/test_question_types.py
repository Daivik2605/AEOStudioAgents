"""Two kinds of question (PLAN.md step 22d): discovery questions measure visibility; brand questions measure
recognition and accuracy and never touch visibility. Real Postgres (aeo_test); runs are built straight into it."""

from __future__ import annotations

import json

import pytest

from audit import report_data, store
from audit.question_set import QuestionSetError, load_question_set, resolve_tags
from audit.report_data import ReportError
from audit.report_render import render_markdown
from core.db import get_connection
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_audit_report import CLIENT, build_run, claim, nb

DISCOVERY = ["which supplier covers events?", "who rents water stations in town?"]
CLIENT_NAMED = lambda: [nb(CLIENT, client=True, rec=True, pos=1), nb("Rival Co", pos=2)]
RIVAL_ONLY = lambda: [nb("Rival Co", pos=1)]


def write(tmp_path, body, name="qtypes_v1"):
    path = tmp_path / f"{name}.yaml"
    path.write_text(body)
    return path


# ---- the file ------------------------------------------------------------------------------------------------------

def test_loader_reads_the_new_tags(tmp_path):
    qs = load_question_set(write(tmp_path, '''
questions:
  - {text: "water station rental", type: discovery, length: short, language: en, is_target: true, spot_check: true}
  - {text: "What is Acme?", type: brand, control: false}
'''))
    first = qs.items[0]
    assert (first.type, first.length, first.language, first.is_target, first.spot_check) == ("discovery", "short", "en", True, True)
    assert qs.items[1].type == "brand"


@pytest.mark.parametrize("bad", ['type: nonsense', 'length: huge', 'language: ""', 'control: maybe'])
def test_loader_refuses_bad_tag_values(tmp_path, bad):
    with pytest.raises(QuestionSetError):
        load_question_set(write(tmp_path, f'questions:\n  - {{text: "a question", {bad}}}\n'))


def test_untagged_set_keeps_its_old_fingerprint_and_new_tags_change_it(tmp_path):
    plain = load_question_set(write(tmp_path, 'questions:\n  - "one"\n  - "two"\n', "plain_v1"))
    tagged = load_question_set(write(tmp_path, 'questions:\n  - "one"\n  - {text: "two", type: discovery}\n', "tagged_v1"))
    # sha256 of json.dumps(["one", "two"]): what a set with no tags has always had
    import hashlib
    assert plain.fingerprint == hashlib.sha256(json.dumps(["one", "two"]).encode()).hexdigest()
    assert tagged.fingerprint != plain.fingerprint


def test_the_oland_draft_loads_as_it_is():
    from pathlib import Path
    draft = Path("clients/olandstations.com/question_set_draft_v0.yaml")
    if not draft.exists():
        pytest.skip("the O'land draft is not on this machine (clients/ is gitignored)")
    qs = load_question_set(draft)
    assert sum(q.type == "brand" for q in qs.items) > 0 and sum(q.type == "discovery" for q in qs.items) > 0


# ---- settling the tags at audit start ------------------------------------------------------------------------------------

def test_discovery_question_that_names_the_business_is_refused(tmp_path):
    qs = load_question_set(write(tmp_path, 'questions:\n  - {text: "Is Acme Refill any good?", type: discovery}\n'))
    with pytest.raises(QuestionSetError, match="discovery"):
        resolve_tags(qs, "Acme Refill", "acme.example")


def test_untagged_questions_are_sorted_by_whether_they_name_the_business(tmp_path):
    qs = load_question_set(write(tmp_path, 'questions:\n  - "who rents water stations?"\n  - "Tell me about acme.example"\n'))
    tags, warnings = resolve_tags(qs, "Acme Refill", "acme.example")
    assert [t["type"] for t in tags] == ["discovery", "brand"]
    assert all(t["type_source"] == "derived from the text" for t in tags)
    assert warnings == []


def test_brand_question_that_does_not_name_the_business_is_kept_with_a_warning(tmp_path):
    qs = load_question_set(write(tmp_path, 'questions:\n  - {text: "what do they sell?", type: brand}\n'))
    tags, warnings = resolve_tags(qs, "Acme Refill", "acme.example")
    assert tags[0]["type"] == "brand" and len(warnings) == 1


def test_length_comes_from_the_tag_else_the_word_count(tmp_path):
    qs = load_question_set(write(tmp_path, '''
questions:
  - "water station rental"
  - {text: "a different short one", length: long}
'''))
    tags, _ = resolve_tags(qs, "Acme Refill", "acme.example")
    assert (tags[0]["length"], tags[0]["length_source"]) == ("short", "word count")
    assert (tags[1]["length"], tags[1]["length_source"]) == ("long", "tag")


def test_start_run_records_the_settled_tags(biz):
    questions = [DISCOVERY[0], f"What is {biz['name']}?"]
    run_id = build_run(biz, [], questions=questions, expected=2)
    conn = get_connection()
    try:
        started = store.started_details(conn, run_id)
    finally:
        conn.close()
    assert [(q["type"], q["type_source"]) for q in started["questions"]] == [
        ("discovery", "derived from the text"), ("brand", "derived from the text")]


# ---- the numbers ---------------------------------------------------------------------------------------------------------

def snapshot(biz, *, brand_named=True):
    """Two discovery questions (client named in one of four answers) and one brand question (named in both its answers)."""
    brand_q = f"What does {biz['name']} do?"
    questions = DISCOVERY + [brand_q]
    specs = [{"q": 0, "e": "chatgpt", "named": CLIENT_NAMED()}, {"q": 0, "e": "perplexity", "named": RIVAL_ONLY()},
             {"q": 1, "e": "chatgpt", "named": RIVAL_ONLY()}, {"q": 1, "e": "perplexity", "named": RIVAL_ONLY()},
             {"q": 2, "e": "chatgpt", "named": CLIENT_NAMED() if brand_named else []},
             {"q": 2, "e": "perplexity", "named": CLIENT_NAMED() if brand_named else []}]
    return build_run(biz, specs, questions=questions)


def test_brand_answers_do_not_count_toward_visibility(biz):
    run_id = snapshot(biz)
    conn = get_connection()
    try:
        data = report_data.build_report(conn, biz, report_data.select_run(conn, biz["id"], run=run_id)[0], partial=False)
    finally:
        conn.close()
    vis = data["headline"]["measures"]["visibility"]
    # 1 of the 4 discovery answers named the client. If the brand answers were counted it would be 3 of 6.
    assert (vis["num"], vis["den"]) == (1, 4)
    assert data["question_mix"]["discovery_answers"] == 4 and data["question_mix"]["brand_answers"] == 2


def test_recognition_is_measured_on_brand_answers_only(biz):
    run_id = snapshot(biz)
    conn = get_connection()
    try:
        data = report_data.build_report(conn, biz, report_data.select_run(conn, biz["id"], run=run_id)[0], partial=False)
    finally:
        conn.close()
    r = data["brand"]["recognition"]
    assert data["brand"]["available"] and (r["num"], r["den"], r["value"]) == (2, 2, 100.0)
    assert "recognition" not in data["headline"]["measures"]


def test_share_of_voice_and_sources_ignore_brand_answers(biz):
    run_id = snapshot(biz)
    conn = get_connection()
    try:
        data = report_data.build_report(conn, biz, report_data.select_run(conn, biz["id"], run=run_id)[0], partial=False)
    finally:
        conn.close()
    client_row = next(r for r in data["share_of_voice"]["rows"] if r["is_client"])
    assert client_row["answers"] == 1        # not 3


def test_a_run_of_only_brand_questions_has_no_visibility_to_report(biz):
    questions = [f"What is {biz['name']}?"]
    run_id = build_run(biz, [{"q": 0, "e": "chatgpt", "named": CLIENT_NAMED()}], questions=questions, engines=("chatgpt",))
    conn = get_connection()
    try:
        with pytest.raises(ReportError, match="discovery"):
            report_data.build_report(conn, biz, report_data.select_run(conn, biz["id"], run=run_id)[0], partial=False)
    finally:
        conn.close()


def test_report_says_which_questions_were_which(biz):
    run_id = snapshot(biz)
    conn = get_connection()
    try:
        data = report_data.build_report(conn, biz, report_data.select_run(conn, biz["id"], run=run_id)[0], partial=False)
    finally:
        conn.close()
    text = render_markdown(json.loads(json.dumps(data, default=str)))
    assert "## Two kinds of question" in text and "## Brand questions" in text
    assert "[brand]" in text and "[discovery]" in text
    assert "not shown for a snapshot" in text.lower()      # by length / by language
    assert data["by_length"] is None and data["by_language"] is None


def test_before_after_design_splits_by_length_and_language(biz):
    # 24 discovery + 8 brand questions, 2 engines, 3 runs = the 32 x 2 x 3 design.
    discovery = [f"discovery question {i} about supplying water" + (" for a big festival with many people" * (i % 3 == 2)) for i in range(24)]
    brand = [f"What does {biz['name']} offer, variant {i}?" for i in range(8)]
    questions = discovery + brand
    tags = {i: {"language": "fr" if i % 4 == 0 else "en"} for i in range(24)}
    specs = [{"q": q, "e": e, "r": r, "named": CLIENT_NAMED() if q % 2 == 0 else RIVAL_ONLY()}
             for q in range(32) for e in ("chatgpt", "perplexity") for r in (1, 2, 3)]
    run_id = build_run(biz, specs, questions=questions, repeats=3, tags=tags)
    conn = get_connection()
    try:
        data = report_data.build_report(conn, biz, report_data.select_run(conn, biz["id"], run=run_id)[0], partial=False)
    finally:
        conn.close()
    assert data["design"]["label"] == "Before/after measurement"
    assert data["headline"]["answers"] == 24 * 6
    assert sum(g["answers"] for g in data["by_language"]["groups"]) == 24 * 6
    assert {g["group"] for g in data["by_language"]["groups"]} == {"en", "fr"}
    assert sum(g["answers"] for g in data["by_length"]["groups"]) == 24 * 6
    assert data["by_length"]["questions_without_tag"] == 0
    assert data["brand"]["answers"] == 8 * 6


def test_accuracy_is_measured_two_ways_and_descriptors_use_brand_answers_only(biz):
    confirm_facts(biz, Phone="514-555-0100")
    brand_q = f"What does {biz['name']} do?"
    right, wrong = [claim("phone is 514-555-0100", "Phone", "correct")], [claim("phone is 999", "Phone", "incorrect")]
    with_words = lambda word: [nb(CLIENT, client=True, rec=True, pos=1, desc=[word])]
    specs = [{"q": 0, "e": "chatgpt", "named": with_words("cheap"), "claims": wrong},      # discovery answer, one wrong fact
             {"q": 1, "e": "chatgpt", "named": RIVAL_ONLY()},
             {"q": 2, "e": "chatgpt", "named": with_words("premium"), "claims": right},    # brand answers
             {"q": 2, "e": "perplexity", "named": with_words("premium"), "claims": right}]
    run_id = build_run(biz, specs, questions=DISCOVERY + [brand_q], engines=("chatgpt", "perplexity"))
    conn = get_connection()
    try:
        data = report_data.build_report(conn, biz, report_data.select_run(conn, biz["id"], run=run_id, )[0], partial=True)
    finally:
        conn.close()
    m = data["headline"]["measures"]
    assert (m["accuracy"]["num"], m["accuracy"]["den"]) == (2, 3)             # overall: 2 right, 1 wrong
    assert (m["accuracy_brand"]["num"], m["accuracy_brand"]["den"]) == (2, 2)  # brand questions only
    assert [w["word"] for w in data["descriptors"]["words"]] == ["premium"]     # "cheap" came from a discovery answer
    text = render_markdown(json.loads(json.dumps(data, default=str)))
    assert "Accuracy, overall" in text and "Accuracy, brand questions only" in text


# ---- compare -------------------------------------------------------------------------------------------------------------

def test_compare_measures_visibility_on_discovery_questions_only(biz, tmp_path):
    from tests.test_compare import add_run, compare, make_qs, measure
    qs = make_qs(10)
    qs.items[9].text = f"What does {biz['name']} do?"            # one brand question; AI always names the client for it
    spec = lambda named: {"named": named, "claims": None}
    # Before: the client is named in no discovery answer. After: in all of them. The brand answer names the client both times.
    add_run(biz, qs, lambda q, e, r: spec(CLIENT_NAMED if q == 9 else RIVAL_ONLY), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: spec(CLIENT_NAMED), checkpoint="after_aeo")
    res = compare(biz, tmp_path)
    vis = measure(res)
    # 9 discovery questions x 2 engines = 18 answers behind each side. With the brand answers it would be 20, and "before" would be 10%.
    assert (vis["a"]["num"], vis["a"]["den"]) == (0, 18) and (vis["b"]["num"], vis["b"]["den"]) == (18, 18)
    assert res.data["audit"]["headline"]["discovery_questions"] == 9
