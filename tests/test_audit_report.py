"""The audit report (PLAN.md step 15): scores from scoring.py, ranges from a seeded bootstrap over questions,
no AI, no per-question findings, no fix plan. Runs are built straight into the test database, so every
number below can be checked by hand."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aeo import cli
from audit import report_data, store
from audit.question_set import Question, QuestionSet
from audit.report import ReportError, make_report
from audit.report_render import render_markdown
from core import client_files
from core.db import get_connection
from core.journal import write_journal
from scoring import bootstrap, scoring
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)

TODAY = date(2026, 10, 15)
CLIENT = "Acme Co"


def nb(name, *, client=False, rec=False, pos=1, reason=None, desc=()):
    return {"name": name, "domain": None, "is_client": client, "recommended": rec, "position": pos,
            "reason": reason, "descriptors": list(desc)}


def link(domain, path="/x"):
    return {"url": f"https://{domain}{path}", "domain": domain, "origin": "pasted"}


@pytest.fixture
def conn():
    c = get_connection()
    yield c
    c.close()


def build_run(biz, specs, *, questions, targets=(), engines=("chatgpt", "perplexity"), repeats=1, status="complete",
              checkpoint="before", operator="Sam", expected=None, tags=None):
    """Writes one audit run straight into the database. Each spec is one answer:
    {q: question index, e: engine, r: repeat, named: list or None (None = extraction failed), claims, searched, text, sources}."""
    conn = get_connection()
    try:
        profile = store.load_business_and_profile(conn, biz["id"])
        qs = QuestionSet(version=f"rep{uuid.uuid4().hex[:8]}_v1", fingerprint=uuid.uuid4().hex,
                         items=[Question(text=t, intent="commercial", is_target=i in targets, **(tags or {}).get(i, {}))
                                for i, t in enumerate(questions)])
        run_id = store.start_run(conn, business=profile, qs=qs, checkpoint=checkpoint, engines=list(engines),
                                 answers_expected=expected or len(questions) * len(engines) * repeats,
                                 how="fill_in_file", extra={"repeats": repeats})
        for s in specs:
            rid, _ = store.add_result(
                conn, probe_run_id=run_id, question=questions[s["q"]], engine=s["e"],
                raw_answer=s.get("text", f"answer to {s['q']} by {s['e']}"), retrieval_activated=s.get("searched"),
                engine_version=s.get("mode", "gpt-5-thinking" if s["e"] == "chatgpt" else "sonar"),
                logged_in_state="logged_out", location_context="Montreal", question_set_version=qs.version,
                sources_cited=s.get("sources"), repeat_number=s.get("r", 1))
            named = s["named"]
            if named is not None:
                store.save_extraction(conn, rid, businesses=named, named_any=bool(named),
                                      recognized=any(b["is_client"] for b in named),
                                      recommended=any(b["is_client"] and b["recommended"] for b in named))
            if s.get("claims") is not None:
                store.save_claims(conn, rid, s["claims"])
        write_journal(business_id=biz["id"], entry_type="note", actor="aeo audit", conn=conn,
                      description="Imported answers", related_table="probe_runs", related_id=run_id,
                      details={"probe_run_id": str(run_id), "operator": operator})
        if status == "complete":
            store.complete_run(conn, business_id=biz["id"], run_id=run_id, answers=len(specs))
        return str(run_id)
    finally:
        conn.close()


def claim(text, field, verdict):
    return {"claim": text, "field": field, "verdict": verdict, "note": f"because {verdict}"}


SQ = ["which supplier covers events?", "who rents water stations in town?", "best refill option for festivals",
      "cheapest water service for venues"]


def snapshot_specs(biz):
    d = biz["domain"]
    return [
        dict(q=0, e="chatgpt", named=[nb(CLIENT, client=True, rec=True, pos=1, reason="serves all of Canada", desc=["premium", "Montreal-based"]),
                                       nb("Rival Co", pos=2, reason="cheapest")], searched=True,
             sources=[link(d, "/p1"), link("news.example", "/y")],
             claims=[claim("Phone is 514-555-0100", "Phone", "correct"),
                     claim("Only serves Ontario", "Areas we serve", "incorrect"),
                     claim("Founded in 2015", None, "unverifiable")]),
        dict(q=0, e="perplexity", named=[nb("Rival Co", rec=True, pos=1, reason="cheapest"), nb("Other Inc", pos=2, reason="local")],
             searched=True, sources=[link("rival.example"), link("news.example", "/y")]),
        dict(q=1, e="chatgpt", named=[nb("Rival Co", rec=True, pos=1), nb(CLIENT, client=True, pos=2, desc=["premium"])],
             searched=True, claims=[claim("Offers delivery", None, "unverifiable"), claim("Phone is 514-555-0100", "Phone", "correct")]),
        dict(q=1, e="perplexity", named=[], searched=None),
        dict(q=2, e="chatgpt", named=[nb("Rival Co", pos=1)], searched=True),
        dict(q=2, e="perplexity", named=[], searched=None),
        dict(q=3, e="chatgpt", named=[nb(CLIENT, client=True, rec=True, pos=1, reason="serves all of Canada", desc=["premium"])],
             searched=False, sources=[link(f"shop.{d}", "/q")]),
        dict(q=3, e="perplexity", named=[nb("rival co", pos=1)], searched=None),
    ]


@pytest.fixture
def snap(conn, biz):
    """The snapshot used by most tests: 4 questions (the first two are targets) x 2 engines x 1 run, with confirmed facts."""
    confirm_facts(biz, Phone="514-555-0100", Areas_we_serve="Montreal")
    biz["run_id"] = build_run(biz, snapshot_specs(biz), questions=SQ, targets=(0, 1))
    return biz


def report(biz, tmp_path, **kw):
    return make_report(biz, clients_root=tmp_path / "clients", today=TODAY, **kw)


def measures(res, block="headline"):
    return res.data[block]["measures"]


# ---- every score comes from scoring.py -------------------------------------------------------------------------------------

def test_each_score_matches_scoring_py(snap, tmp_path):
    m = measures(report(snap, tmp_path))
    assert m["visibility"]["value"] == scoring.visibility_score(3, 8) == 37.5
    assert m["recommendation"]["value"] == scoring.recommendation_rate(2, 8) == 25.0
    assert m["gap"]["value"] == scoring.gap_score(3, 8) == 37.5
    assert m["brand_free"]["value"] == scoring.brand_free_rate(2, 8) == 25.0
    assert m["retrieval"]["value"] == scoring.retrieval_activation_rate(4, 5) == 80.0
    assert m["accuracy"]["value"] == scoring.accuracy_score(2, 1)
    assert (m["visibility"]["num"], m["visibility"]["den"]) == (3, 8)


def test_the_tier_comes_from_the_lower_edge_rule_in_scoring_py(snap, tmp_path):
    res = report(snap, tmp_path)
    assert res.data["headline"]["tier"] == scoring.visibility_tier(37.5) == "Barely Visible"


def test_the_new_scoring_functions(conn):
    assert scoring.recommendation_rate(1, 4) == 25.0 and scoring.brand_free_rate(1, 8) == 12.5
    assert scoring.retrieval_activation_rate(3, 4) == 75.0 and scoring.formula_version() == "v1"
    for fn in (scoring.recommendation_rate, scoring.brand_free_rate, scoring.retrieval_activation_rate):
        with pytest.raises(ValueError):
            fn(0, 0)


def test_the_scores_are_saved_with_their_formula_version(conn, snap, tmp_path):
    report(snap, tmp_path)
    rows = conn.execute("SELECT formula_version, visibility_score, gap_score, accuracy_score, tier, opportunity_score "
                        "FROM visibility_scores WHERE probe_run_id = %s", (snap["run_id"],)).fetchall()
    assert len(rows) == 1
    version, vis, gap, acc, tier, opp = rows[0]
    assert (version, float(vis), float(gap), tier, opp) == ("v1", 37.5, 37.5, "Barely Visible", None)
    assert float(acc) == pytest.approx(scoring.accuracy_score(2, 1))


# ---- ranges --------------------------------------------------------------------------------------------------------------------

def test_the_same_data_and_seed_always_give_the_same_range(snap, tmp_path):
    a, b = report(snap, tmp_path), report(snap, tmp_path)
    assert measures(a) == measures(b)                                   # values AND ranges, every measure
    units = [{"n": 3, "named": k} for k in (1, 3, 0, 2, 2, 1, 3, 3, 0, 1, 2, 0)]
    stat = {"v": lambda c: scoring.visibility_score(c["named"], c["n"])}
    assert bootstrap.ranges(units, stat) == bootstrap.ranges(units, stat)
    assert bootstrap.ranges(units, stat, seed=1) != bootstrap.ranges(units, stat, seed=2)


def test_every_score_shows_a_range_that_holds_its_value(snap, tmp_path):
    for key, m in measures(report(snap, tmp_path)).items():
        assert m["low"] is not None and m["low"] <= m["value"] <= m["high"] or key == "accuracy", key
        assert m["low"] is not None and m["high"] is not None, key


def test_the_bootstrap_resamples_questions_not_answers():
    # Two questions: one always named, one never. Resampling QUESTIONS can only give 0%, 50% or 100%, so the range is
    # 0-100. Resampling the 20 answers instead would give a tight range around 50 and hide how little 2 questions prove.
    units = [{"n": 10, "named": 10}, {"n": 10, "named": 0}]
    low, high = bootstrap.ranges(units, {"v": lambda c: scoring.visibility_score(c["named"], c["n"])})["v"]
    assert (low, high) == (0.0, 100.0)


def test_one_question_cannot_give_a_range():
    assert bootstrap.ranges([{"n": 3, "named": 1}], {"v": lambda c: 1.0}) == {"v": None}


def test_a_group_where_everything_is_the_same_has_a_flat_range():
    units = [{"n": 3, "named": 3}] * 5
    assert bootstrap.ranges(units, {"v": lambda c: scoring.visibility_score(c["named"], c["n"])})["v"] == (100.0, 100.0)


def test_a_statistic_that_is_usually_undefined_gets_no_range():
    units = [{"n": 3, "x": 0}, {"n": 3, "x": 0}]
    assert bootstrap.ranges(units, {"v": lambda c: None})["v"] is None


# ---- labelling: snapshot vs before/after -------------------------------------------------------------------------------------

@pytest.mark.parametrize("q, e, r, label", [
    (32, 2, 3, "Before/after measurement"), (32, 2, 2, "Snapshot"), (31, 2, 3, "Snapshot"),
    (32, 3, 3, "Snapshot"), (12, 5, 1, "Snapshot"), (33, 2, 3, "Snapshot")])
def test_only_32_questions_by_2_engines_by_3_runs_is_before_after(q, e, r, label):
    assert report_data.design_label(q, e, r) == label


def ba_specs(biz, *, named_up_to=16):
    out = []
    for q in range(32):
        for e in ("chatgpt", "perplexity"):
            for r in (1, 2, 3):
                named = ([nb(CLIENT, client=True, rec=(e == "chatgpt"), pos=1)] if q < named_up_to
                         else [nb("Rival Co", pos=1)])
                out.append(dict(q=q, e=e, r=r, named=named, searched=True))
    return out


BQ = [f"question number {i} about buying water service" for i in range(32)]


@pytest.fixture
def ba(conn, biz):
    biz["run_id"] = build_run(biz, ba_specs(biz), questions=BQ, targets=range(10), repeats=3)
    return biz


def test_a_32_by_2_by_3_run_is_labelled_before_after(ba, tmp_path):
    res = report(ba, tmp_path)
    assert res.data["design"]["label"] == "Before/after measurement"
    md = res.md_path.read_text()
    assert "**Before/after measurement.** 32 questions × 2 engines × 3 runs" in md
    assert "detects roughly a 20-point change, and not less" in md


def test_the_snapshot_is_labelled_a_snapshot(snap, tmp_path):
    res = report(snap, tmp_path)
    assert res.data["design"]["label"] == "Snapshot"
    assert "**Snapshot.** 4 question(s) × 2 engine(s) × 1 run(s)" in res.md_path.read_text()


def test_a_snapshot_shows_only_overall_figures_and_nothing_per_question(snap, tmp_path):
    res = report(snap, tmp_path)
    assert res.data["by_engine"] is None and res.data["targets_vs_control"] is None
    md = res.md_path.read_text()
    before_appendix = md.split("## Appendix")[0]
    assert "Not shown for a snapshot" in before_appendix
    # No question is discussed outside the wrong-facts list (which names the question an incorrect claim came from)
    # and the raw appendix.
    scored_sections = before_appendix.split("## Wrong facts")[0] + before_appendix.split("## Sources")[1]
    for question in SQ:
        assert question not in scored_sections
    assert not re.search(r"\bQ\d", before_appendix.split("## Wrong facts")[0])
    assert "per_question" not in json.dumps(res.data)
    assert "### ChatGPT" not in before_appendix and "### Targets" not in before_appendix


def test_before_after_also_shows_no_figure_for_any_single_question(ba, tmp_path):
    md = report(ba, tmp_path).md_path.read_text()
    before_appendix = md.split("## Appendix")[0]
    assert not any(q in before_appendix for q in BQ)


# ---- unfinished runs -------------------------------------------------------------------------------------------------------------

@pytest.fixture
def unfinished(conn, biz):
    biz["run_id"] = build_run(biz, snapshot_specs(biz)[:4], questions=SQ, status="in_progress", expected=8)
    return biz


def test_an_unfinished_run_is_refused_without_partial(unfinished, tmp_path):
    with pytest.raises(ReportError, match=r"not finished.*4 of 8 answers saved.*--partial"):
        report(unfinished, tmp_path)
    assert not (tmp_path / "clients" / unfinished["domain"]).exists()


def test_partial_makes_a_report_with_an_incomplete_banner_at_the_very_top(conn, unfinished, tmp_path):
    res = report(unfinished, tmp_path, partial=True)
    md = res.md_path.read_text()
    assert md.startswith("> **INCOMPLETE.**") and "4 of 8 expected answers saved" in md
    assert res.data["incomplete"] == {"status": "in_progress", "answers_saved": 4, "answers_expected": 8}
    assert res.scores_saved is False
    assert conn.execute("SELECT count(*) FROM visibility_scores WHERE probe_run_id = %s", (unfinished["run_id"],)).fetchone()[0] == 0


def test_a_finished_report_has_no_banner(snap, tmp_path):
    md = report(snap, tmp_path).md_path.read_text()
    assert "INCOMPLETE" not in md and md.startswith("# Audit report:")


def test_a_failed_run_is_also_refused(conn, biz, tmp_path):
    run_id = build_run(biz, snapshot_specs(biz)[:2], questions=SQ, status="in_progress", expected=8)
    store.finish_run(conn, run_id, complete=False)
    with pytest.raises(ReportError, match="status failed"):
        report(biz, tmp_path)


# ---- answers the model could not read ------------------------------------------------------------------------------------------

def test_failed_extractions_are_counted_listed_and_left_out_of_the_scores(conn, biz, tmp_path):
    specs = snapshot_specs(biz) + [dict(q=2, e="perplexity", r=2, named=None, text="an answer nobody could read")]
    confirm_facts(biz, Phone="514-555-0100")
    biz["run_id"] = build_run(biz, specs, questions=SQ)
    res = report(biz, tmp_path)
    assert res.data["extraction"]["answers"] == 9 and res.data["extraction"]["scored"] == 8
    assert res.data["extraction"]["failed"] == [{"question_number": 3, "question": SQ[2], "engine": "perplexity", "repeat": 2}]
    m = measures(res)
    assert (m["visibility"]["num"], m["visibility"]["den"]) == (3, 8)       # not 3 of 9
    assert m["brand_free"]["num"] == 2                                       # the unreadable answer is NOT "named nobody"
    md = res.md_path.read_text()
    assert "1 answer(s) could not be read by the model and are left out of every figure above" in md
    assert "- Q3 · perplexity · run 2" in md
    assert "NOT READ by the model: left out of the scores" in md             # still in the appendix, flagged
    assert "an answer nobody could read" in md


def test_a_run_where_nothing_has_been_read_cannot_be_scored(conn, biz, tmp_path):
    build_run(biz, [dict(q=0, e="chatgpt", named=None)], questions=SQ[:1], engines=("chatgpt",))
    with pytest.raises(ReportError, match="nothing to score"):
        report(biz, tmp_path)


# ---- the headline ----------------------------------------------------------------------------------------------------------------

def test_brand_free_is_reported_with_both_denominators(snap, tmp_path):
    res = report(snap, tmp_path)
    m = measures(res)
    assert (m["brand_free"]["num"], m["brand_free"]["den"], m["brand_free"]["value"]) == (2, 8, 25.0)       # of all answers
    assert (m["visibility_among_named"]["num"], m["visibility_among_named"]["den"]) == (3, 6)               # of answers naming anyone
    assert m["visibility_among_named"]["value"] == scoring.visibility_score(3, 6) == 50.0
    assert m["recommendation_among_named"]["value"] == scoring.recommendation_rate(2, 6)
    md = res.md_path.read_text()
    assert "Brand-free answers (named no business at all) | **25.0%**" in md
    assert "counting only answers that named at least one business | **50.0%**" in md


def test_mentioned_versus_recommended_is_shown(snap, tmp_path):
    res = report(snap, tmp_path)
    assert res.data["headline"]["mentioned_only"] == 1
    assert "1 of those only mentioned it without proposing it as the answer" in res.md_path.read_text()


def test_retrieval_activation_leaves_out_answers_where_the_operator_could_not_tell(snap, tmp_path):
    res = report(snap, tmp_path)
    assert res.data["headline"]["retrieval_unknown"] == 3
    assert "could not tell whether the engine searched on 3 answer(s)" in res.md_path.read_text()


def test_accuracy_is_only_scored_when_confirmed_facts_exist(conn, biz, tmp_path):
    biz["run_id"] = build_run(biz, snapshot_specs(biz), questions=SQ)         # no truth document imported
    res = report(biz, tmp_path)
    assert "accuracy" not in measures(res) and res.data["wrong_facts"]["confirmed_facts"] == 0
    md = res.md_path.read_text()
    assert "Accuracy is not scored: no facts had been confirmed" in md and "Accuracy score" not in md.split("## By engine")[0]
    assert "No facts had been confirmed when this run started, so no claim was checked." in md
    row = conn.execute("SELECT accuracy_score FROM visibility_scores WHERE probe_run_id = %s", (biz["run_id"],)).fetchone()
    assert row == (None,)


def test_the_tier_is_not_claimed_more_firmly_than_the_range_allows(snap, tmp_path):
    h = report(snap, tmp_path).data["headline"]
    assert h["tier_low"] is not None
    text = report(snap, tmp_path).md_path.read_text()
    if h["tier_low"] != h["tier_high"]:
        assert f"The range reaches from {h['tier_low']} to {h['tier_high']}" in text


# ---- targets vs control --------------------------------------------------------------------------------------------------------------

def test_targets_and_control_are_split_by_the_tags_recorded_when_the_run_started(ba, tmp_path):
    t = report(ba, tmp_path).data["targets_vs_control"]
    assert t["available"] and t["targets"]["questions"] == 10 and t["control"]["questions"] == 22
    assert t["targets"]["answers"] == 60 and t["control"]["answers"] == 132
    tv, cv = t["targets"]["measures"], t["control"]["measures"]
    assert tv["visibility"]["value"] == 100.0 and (tv["visibility"]["low"], tv["visibility"]["high"]) == (100.0, 100.0)
    assert cv["visibility"]["value"] == pytest.approx(scoring.visibility_score(36, 132)) and cv["visibility"]["num"] == 36
    assert tv["recommendation"]["value"] == 50.0 and cv["recommendation"]["num"] == 18


def test_the_split_text_names_the_control(ba, tmp_path):
    md = report(ba, tmp_path).md_path.read_text()
    assert "### Targets (10 questions)" in md and "### Control (22 questions)" in md
    assert "The questions we leave untouched are the control" in md


def test_with_no_targets_marked_the_section_says_so(conn, biz, tmp_path):
    build_run(biz, ba_specs(biz), questions=BQ, targets=(), repeats=3)
    t = report(biz, tmp_path).data["targets_vs_control"]
    assert t == {"available": False, "reason": "no question in this run is marked as a target"}


def test_by_engine_has_the_same_numbers_for_each_engine(ba, tmp_path):
    engines = {e["engine"]: e["measures"] for e in report(ba, tmp_path).data["by_engine"]}
    assert set(engines) == {"chatgpt", "perplexity"}
    assert engines["chatgpt"]["visibility"]["value"] == 50.0 and engines["chatgpt"]["recommendation"]["value"] == 50.0
    assert engines["perplexity"]["visibility"]["value"] == 50.0 and engines["perplexity"]["recommendation"]["value"] == 0.0
    assert all(m["low"] is not None for m in engines["chatgpt"].values())


# ---- share of voice, reasons, descriptors ----------------------------------------------------------------------------------------------

def test_share_of_voice_counts_answers_recommendations_and_average_position(snap, tmp_path):
    sov = report(snap, tmp_path).data["share_of_voice"]
    rows = {r["name"]: r for r in sov["rows"]}
    assert sov["businesses_named"] == 3
    assert [r["name"] for r in sov["rows"]] == ["Rival Co", CLIENT, "Other Inc"]           # most answers first; "rival co" merged in
    assert (rows["Rival Co"]["answers"], rows["Rival Co"]["recommended"], rows["Rival Co"]["average_position"]) == (5, 2, 1.2)
    assert (rows[CLIENT]["answers"], rows[CLIENT]["recommended"]) == (3, 2)
    assert rows[CLIENT]["average_position"] == pytest.approx(4 / 3) and rows[CLIENT]["is_client"]
    assert rows["Other Inc"]["average_position"] == 2


def test_the_client_is_highlighted_in_the_table(snap, tmp_path):
    md = report(snap, tmp_path).md_path.read_text()
    assert f"**{CLIENT} ← the client**" in md and "| 1 | Rival Co | 5 | 2 | 1.2 |" in md


def test_only_the_top_15_are_listed_and_the_rest_are_counted_but_the_client_is_never_hidden(conn, biz, tmp_path):
    specs = []
    for i in range(20):                                    # 20 rivals, each named in a different number of answers
        for j in range(21 - i):
            specs.append(dict(q=j % 4, e="chatgpt", r=1 + j // 4, named=[nb(f"Rival {i:02d}", pos=1)]))
    specs.append(dict(q=0, e="perplexity", named=[nb(CLIENT, client=True, pos=1)]))       # the client is named only once
    build_run(biz, specs, questions=SQ, repeats=6)
    sov = report(biz, tmp_path).data["share_of_voice"]
    assert len(sov["rows"]) == 15 and sov["rows"][0]["name"] == "Rival 00"
    assert sov["others"] == {"businesses": 5, "mentions": 6 + 5 + 4 + 3 + 2}      # rivals 15-19 were named in 6,5,4,3,2 answers
    assert [r["name"] for r in sov["client_outside_top"]] == [CLIENT] and sov["client_outside_top"][0]["rank"] == 21


def test_reasons_are_quoted_and_counted(snap, tmp_path):
    reasons = {r["name"]: r for r in report(snap, tmp_path).data["reasons"]}
    assert reasons["Rival Co"]["reasons"] == [{"quote": "cheapest", "answers": 2}]
    assert reasons[CLIENT]["reasons"] == [{"quote": "serves all of Canada", "answers": 2}] and reasons[CLIENT]["is_client"]
    md = report(snap, tmp_path).md_path.read_text()
    assert "- “serves all of Canada” — 2 answer(s)" in md


def test_descriptor_words_come_from_brand_questions_only(snap, tmp_path):
    # Every question in this snapshot is a discovery question, so no word is collected even though the client was
    # described in some answers. The counting of words from brand answers is tested in test_question_types.py.
    res = report(snap, tmp_path)
    assert res.data["descriptors"]["words"] == []
    assert "because this run asked no brand questions" in res.md_path.read_text()


# ---- wrong facts -------------------------------------------------------------------------------------------------------------------------

def test_a_wrong_fact_shows_the_quote_the_fact_it_contradicts_the_engine_and_the_question(snap, tmp_path):
    res = report(snap, tmp_path)
    (wrong,) = res.data["wrong_facts"]["wrong"]
    assert wrong == {"claim": "Only serves Ontario", "note": "because incorrect", "field": "Areas we serve",
                     "confirmed_fact": "Montreal", "engine": "chatgpt", "repeat": 1, "question": SQ[0]}
    md = res.md_path.read_text()
    assert "**“Only serves Ontario”**" in md and "The confirmed fact it contradicts: Areas we serve: Montreal" in md
    assert f"chatgpt · run 1 · Q1: “{SQ[0]}”" in md


def test_unverifiable_claims_are_counted_separately_and_never_called_wrong(snap, tmp_path):
    res = report(snap, tmp_path)
    wf = res.data["wrong_facts"]
    assert (wf["correct"], wf["incorrect"], wf["unverifiable"]) == (2, 1, 2)
    assert [w["claim"] for w in wf["wrong"]] == ["Only serves Ontario"]                    # not "Founded in 2015", not "Offers delivery"
    assert measures(res)["accuracy"]["den"] == 3                                            # correct + incorrect only
    md = res.md_path.read_text()
    assert "2 correct, **1 incorrect**, 2 unverifiable" in md
    assert "It is not counted as wrong, and not as right." in md
    assert "Founded in 2015" not in md.split("## Sources")[0]


def test_an_answer_with_only_unverifiable_claims_gives_no_wrong_facts_and_no_accuracy_score(conn, biz, tmp_path):
    confirm_facts(biz, Phone="514-555-0100")
    specs = [dict(q=0, e="chatgpt", named=[nb(CLIENT, client=True, pos=1)], claims=[claim("Founded in 2015", None, "unverifiable")]),
             dict(q=1, e="chatgpt", named=[nb(CLIENT, client=True, pos=1)], claims=[claim("Open on Sundays", None, "unverifiable")])]
    build_run(biz, specs, questions=SQ[:2], engines=("chatgpt",))
    res = report(biz, tmp_path)
    assert res.data["wrong_facts"]["wrong"] == [] and res.data["wrong_facts"]["unverifiable"] == 2
    assert measures(res)["accuracy"]["value"] is None
    md = res.md_path.read_text()
    assert "No incorrect claim was found." in md and "Accuracy is not scored: the answers made no claim" in md


def test_wrong_facts_are_checked_against_the_facts_of_the_run_not_the_newest_ones(conn, biz, tmp_path):
    confirm_facts(biz, Areas_we_serve="Montreal")                                   # version 2, current when the run starts
    specs = [dict(q=0, e="chatgpt", named=[nb(CLIENT, client=True, pos=1)],
                  claims=[claim("Only serves Ontario", "Areas we serve", "incorrect")]),
             dict(q=1, e="chatgpt", named=[], claims=None)]
    build_run(biz, specs, questions=SQ[:2], engines=("chatgpt",))
    confirm_facts(biz, Areas_we_serve="Everywhere in Canada")                       # version 3, after the run
    (wrong,) = report(biz, tmp_path).data["wrong_facts"]["wrong"]
    assert wrong["confirmed_fact"] == "Montreal"


# ---- sources ---------------------------------------------------------------------------------------------------------------------------------

def test_sources_count_answers_per_domain_and_how_often_the_clients_own_domain_was_cited(snap, tmp_path):
    s = report(snap, tmp_path).data["sources"]
    assert s["answers"] == 8 and s["answers_with_sources"] == 3
    assert s["top"][0] == {"domain": "news.example", "answers": 2}
    assert s["client_domain"] == snap["domain"] and s["client_cited_in"] == 2           # the bare domain and a subdomain of it
    assert {u["url"] for u in s["client_urls"]} == {f"https://{snap['domain']}/p1", f"https://shop.{snap['domain']}/q"}


# ---- header ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_header_has_everything_a_reader_needs(conn, snap, tmp_path):
    h = report(snap, tmp_path).data["header"]
    assert h["business"]["domain"] == snap["domain"] and h["checkpoint"] == "before"
    assert h["question_set_version"].startswith("rep") and h["question_set_version"].endswith("_v1")
    assert [(e["label"], e["modes"]) for e in h["engines"]] == [("ChatGPT", ["gpt-5-thinking"]), ("Perplexity", ["sonar"])]
    assert (h["repeats"], h["answers_saved"], h["answers_expected"]) == (1, 8, 8)
    assert h["operators"] == ["Sam"] and h["logged_in_states"] == ["logged_out"] and h["locations"] == ["Montreal"]
    assert h["confirmed_facts"] == 2 and h["profile_version"] == 2
    assert h["started_at"] and h["completed_at"] and h["first_answer_on"] == h["last_answer_on"]
    md = report(snap, tmp_path).md_path.read_text()
    for needle in ("| Checkpoint | before |", "| Operator | Sam |", "| Logged in or out | logged out |",
                   "| Asked from | Montreal |", "| Engines | ChatGPT (gpt-5-thinking); Perplexity (sonar) |"):
        assert needle in md


def test_a_run_remembers_which_questions_were_targets_and_who_started_it(conn, snap):
    started = store.started_details(conn, snap["run_id"])
    assert [(q["text"], q["is_target"]) for q in started["questions"]] == [(SQ[0], True), (SQ[1], True), (SQ[2], False), (SQ[3], False)]
    assert started["started_by"]


# ---- the appendix and what is NOT in the report ---------------------------------------------------------------------------------------------------

def test_the_appendix_holds_every_question_and_every_answer_in_full(conn, biz, tmp_path):
    tricky = "Line one\n\n```python\nprint('code in an answer')\n```\n\n# A heading inside an answer\n- bullet"
    specs = snapshot_specs(biz)
    specs[0]["text"] = tricky
    build_run(biz, specs, questions=SQ, targets=(0,))
    res = report(biz, tmp_path)
    md = res.md_path.read_text()
    assert "## Appendix: every question and every answer (raw answers, not findings)" in md
    assert tricky in md and "````" in md                                      # a longer fence, so the answer cannot break out of its box
    appendix = md.split("## Appendix")[1]
    for q in SQ:
        assert q in appendix
    assert appendix.count("#### ChatGPT") == 4 and appendix.count("#### Perplexity") == 4
    assert "(target)" in appendix
    assert [a["text"] for a in res.data["appendix"][0]["answers"]][0] == tricky


def test_there_is_no_fix_plan_anywhere(snap, tmp_path):
    md = report(snap, tmp_path).md_path.read_text()
    headings = re.findall(r"^#+ (.*)$", md, flags=re.M)
    assert not any(re.search(r"\b(fix|fixes|recommendations?|next steps|action)\b", h, flags=re.I) for h in headings
                   if not h.startswith("Q"))
    assert "It contains no fix plan." in md and "`aeo recommend`" in md


# ---- the files ------------------------------------------------------------------------------------------------------------------------------------------

def test_the_report_and_its_data_are_dated_numbered_files_in_generated(snap, tmp_path):
    res = report(snap, tmp_path)
    folder = tmp_path / "clients" / snap["domain"] / "generated"
    assert res.md_path == folder / "2026-10-15_audit_report_run1.md" and res.json_path == folder / "2026-10-15_audit_report_run1.json"


def test_the_json_is_exactly_the_data_the_markdown_was_made_from(snap, tmp_path):
    res = report(snap, tmp_path)
    data = json.loads(res.json_path.read_text(encoding="utf-8"))
    assert data == res.data
    assert render_markdown(data) == res.md_path.read_text(encoding="utf-8")
    for key in ("design", "header", "extraction", "headline", "share_of_voice", "reasons", "descriptors",
                "wrong_facts", "sources", "appendix", "tool"):
        assert key in data
    assert data["tool"]["bootstrap"] == {"resamples": 2000, "seed": 20261014, "level": 0.95}


def test_running_it_again_gives_the_same_file_content_and_overwrites_nothing(conn, snap, tmp_path):
    first, second = report(snap, tmp_path), report(snap, tmp_path)
    assert first.md_path != second.md_path and second.md_path.name == "2026-10-15_audit_report_run2.md"
    assert first.md_path.read_bytes() == second.md_path.read_bytes()
    assert first.json_path.read_bytes() == second.json_path.read_bytes()
    assert conn.execute("SELECT count(*) FROM visibility_scores WHERE probe_run_id = %s", (snap["run_id"],)).fetchone()[0] == 1


def test_a_journal_entry_records_the_report(conn, snap, tmp_path):
    res = report(snap, tmp_path)
    ((description, table, details),) = conn.execute(
        "SELECT description, related_table, details FROM client_journal WHERE business_id = %s AND entry_type = 'audit_generated'",
        (snap["id"],)).fetchall()
    assert "Snapshot" in description and table == "probe_runs"
    assert details["report"] == f"generated/{res.md_path.name}" and details["scores_saved"] is True and details["formula_version"] == "v1"


# ---- choosing the run -------------------------------------------------------------------------------------------------------------------------------------

def test_with_no_option_the_latest_finished_run_is_used_and_other_runs_are_mentioned(conn, biz, tmp_path):
    old = build_run(biz, snapshot_specs(biz), questions=SQ)
    new = build_run(biz, snapshot_specs(biz), questions=SQ)
    res = report(biz, tmp_path)
    assert res.run_id == new and res.other_runs == 1 and old != new
    assert report(biz, tmp_path, run=old).run_id == old


def test_checkpoint_picks_the_latest_finished_run_at_that_checkpoint(conn, biz, tmp_path):
    before = build_run(biz, snapshot_specs(biz), questions=SQ, checkpoint="before")
    build_run(biz, snapshot_specs(biz), questions=SQ, checkpoint="after_aeo")
    assert report(biz, tmp_path, checkpoint="before").run_id == before
    with pytest.raises(ReportError, match="no visibility audit run at checkpoint 'check'"):
        report(biz, tmp_path, checkpoint="check")


def test_run_and_checkpoint_together_are_refused(snap, tmp_path):
    with pytest.raises(ReportError, match="not both"):
        report(snap, tmp_path, run=snap["run_id"], checkpoint="before")


def test_a_run_of_another_business_or_a_bad_id_is_refused(conn, snap, tmp_path):
    other = {"id": str(uuid.uuid4()), "name": "X", "domain": "x.example"}
    with pytest.raises(ReportError, match="does not exist for this business"):
        report(other, tmp_path, run=snap["run_id"])
    with pytest.raises(ReportError, match="is not a run id"):
        report(snap, tmp_path, run="nope")


def test_a_business_with_no_run_is_refused(biz, tmp_path):
    with pytest.raises(ReportError, match="no visibility audit run"):
        report(biz, tmp_path)


# ---- the command -------------------------------------------------------------------------------------------------------------------------------------------

def run_cli(*args):
    return CliRunner().invoke(cli.app, ["audit", "report", *args])


def test_cli_writes_the_report_and_prints_the_headline(snap):
    result = run_cli("--business", snap["domain"], "--checkpoint", "before")
    assert result.exit_code == 0, result.output
    assert "Snapshot report for run" in result.output and "Visibility score: 37.5%" in result.output
    assert "tier Barely Visible" in result.output and "Scores saved to visibility_scores." in result.output
    assert "_audit_report_run1.md" in result.output and "_audit_report_run1.json" in result.output
    assert next(client_files.CLIENTS_ROOT.rglob("*_audit_report_run1.md")).exists()


def test_cli_refuses_an_unfinished_run_and_partial_allows_it(unfinished):
    refused = run_cli("--business", unfinished["id"])
    assert refused.exit_code == 1 and "not finished" in refused.output and "--partial" in refused.output
    ok = run_cli("--business", unfinished["id"], "--partial")
    assert ok.exit_code == 0, ok.output
    assert "INCOMPLETE: 4 of 8 answers" in ok.output and "Scores were not saved" in ok.output


def test_cli_refuses_a_bad_checkpoint(snap):
    result = run_cli("--business", snap["id"], "--checkpoint", "old_site")
    assert result.exit_code == 1 and "Valid labels" in result.output
