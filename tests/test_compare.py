"""Tests for `aeo compare` (PLAN.md step 20). Real Postgres (aeo_test); runs and diagnoses are built straight into it. No AI."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from aeo import cli
from audit import store
from audit.question_set import Question, QuestionSet
from compare.audit import NOISE, REAL, label_for
from compare.render import render_markdown
from compare.run import CompareError, make_comparison
from core import evidence as ev
from core.db import get_connection
from diagnose import store as dstore
from diagnose.codes import make_finding
from scoring import bootstrap
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_audit_report import CLIENT, claim, link, nb

TODAY = date(2026, 10, 20)
NOW = datetime.now(timezone.utc)
CLIENT_REC = lambda: [nb(CLIENT, client=True, rec=True, pos=1), nb("Rival Co", pos=2)]
CLIENT_LISTED = lambda: [nb(CLIENT, client=True, rec=False, pos=2), nb("Rival Co", pos=1)]
RIVAL = lambda: [nb("Rival Co")]
NOBODY = lambda: []


def make_qs(n=10, targets=()):
    return QuestionSet(version=f"cmp{uuid.uuid4().hex[:8]}_v1", fingerprint=uuid.uuid4().hex,
                       items=[Question(text=f"question number {i} about refills?", intent="commercial", is_target=i in targets)
                              for i in range(n)])


def add_run(biz, qs, answer, *, checkpoint, engines=("chatgpt", "perplexity"), repeats=1, status="complete"):
    """One audit run. `answer(q, engine, repeat)` returns {named, claims?, sources?, searched?}."""
    conn = get_connection()
    try:
        profile = store.load_business_and_profile(conn, biz["id"])
        run_id = store.start_run(conn, business=profile, qs=qs, checkpoint=checkpoint, engines=list(engines),
                                 answers_expected=len(qs.items) * len(engines) * repeats, how="fill_in_file",
                                 extra={"repeats": repeats})
        count = 0
        for qi, q in enumerate(qs.items):
            for e in engines:
                for r in range(1, repeats + 1):
                    spec = answer(qi, e, r)
                    if spec is None:
                        continue
                    rid, _ = store.add_result(
                        conn, probe_run_id=run_id, question=q.text, engine=e, raw_answer=f"answer {qi} {e} {r}",
                        retrieval_activated=spec.get("searched", True), engine_version="m1", logged_in_state="logged_out",
                        location_context="Montreal", question_set_version=qs.version, sources_cited=spec.get("sources"),
                        repeat_number=r)
                    named = spec["named"]()
                    store.save_extraction(conn, rid, businesses=named, named_any=bool(named),
                                          recognized=any(b["is_client"] for b in named),
                                          recommended=any(b["is_client"] and b["recommended"] for b in named))
                    if spec.get("claims") is not None:
                        store.save_claims(conn, rid, spec["claims"])
                    count += 1
        if status == "complete":
            store.complete_run(conn, business_id=biz["id"], run_id=run_id, answers=count)
        return str(run_id)
    finally:
        conn.close()


def finding(code, severity):
    return make_finding(code, what=f"{code} what", evidence="e", fixable_on_platform=True, where_to_fix="w", source="s", severity=severity)


def add_diagnosis(biz, label, findings, *, when=None, platform="wordpress", verdict="serve", plan=None):
    conn = get_connection()
    try:
        return str(dstore.insert_diagnosis(
            conn, business_id=biz["id"], checkpoint=label, url=f"https://{biz['domain']}/", checked_at=when or NOW,
            platform=platform, platform_version=None, platform_plan=plan, robots_txt=None, existing_jsonld=[],
            crawler_access={}, render=None, findings=findings, verdict=verdict, verdict_reason="r", readiness_score=None))
    finally:
        conn.close()


def journal(biz, kind, text, when, *, finding=None, actor="Sam"):
    """A work/event entry with a chosen date (the test database is a throwaway; the real write path always uses now())."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO client_journal (business_id, entry_type, description, actor, details, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (biz["id"], kind, text, actor, json.dumps({"finding": finding}), when))
    finally:
        conn.close()


def compare(biz, tmp_path, a="before", b="after_aeo", **kw):
    return make_comparison(biz, a, b, clients_root=tmp_path / "clients", today=TODAY, **kw)


def measure(res, name="visibility"):
    return res.data["audit"]["headline"]["measures"][name]


@pytest.fixture
def pair(biz):
    """Same question set, client invisible at `before` and named in every answer at `after_aeo`."""
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC), checkpoint="after_aeo")
    biz["qs"] = qs
    return biz


# ---- 1. diagnosis comparison -------------------------------------------------------------------------------------------------

def diag_pair(biz):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium"), finding("SCHEMA_NONE_FOUND", "medium"),
                                  finding("LLMS_TXT_MISSING", "low"), finding("CRAWLER_BLOCKED_OAI_SEARCHBOT", "high")],
                  when=NOW - timedelta(days=10), platform="squarespace", verdict="conditional")
    add_diagnosis(biz, "after_aeo", [finding("SCHEMA_NONE_FOUND", "high"), finding("RENDER_PHONE_MISSING", "medium"),
                                     finding("ROBOTS_TXT_SERVED_AS_HTML", "low")],
                  when=NOW + timedelta(days=10), platform="squarespace", verdict="serve")


def test_findings_are_grouped_closed_still_open_and_new(biz, tmp_path):
    diag_pair(biz)
    d = compare(biz, tmp_path).data["diagnosis"]
    assert [c["code"] for c in d["closed"]] == ["CRAWLER_BLOCKED_OAI_SEARCHBOT", "RENDER_H1_MISSING", "LLMS_TXT_MISSING"]   # by severity
    assert [c["code"] for c in d["still_open"]] == ["SCHEMA_NONE_FOUND"]
    assert [c["code"] for c in d["new"]] == ["RENDER_PHONE_MISSING", "ROBOTS_TXT_SERVED_AS_HTML"]
    assert d["headline"] == "3 of 4 findings closed" and d["a_total"] == 4 and d["b_total"] == 3
    assert [c["severity"] for c in d["closed"]] == ["high", "medium", "low"]
    assert d["still_open"][0]["severity"] == "high" and d["still_open"][0]["severity_in_a"] == "medium"


def test_platform_and_verdict_changes_are_reported(biz, tmp_path):
    diag_pair(biz)
    d = compare(biz, tmp_path).data["diagnosis"]
    assert d["verdict"] == {"a": "conditional", "b": "serve", "changed": True}
    assert d["platform"]["changed"] is False
    md = (tmp_path / "clients" / biz["domain"] / "generated").glob("*.md")
    assert "conditional | serve **changed**" in next(md).read_text()


def test_closed_findings_show_the_work_that_was_logged(biz, tmp_path):
    diag_pair(biz)
    journal(biz, "work", "Allowed OAI-SearchBot in Cloudflare", NOW, finding="CRAWLER_BLOCKED_OAI_SEARCHBOT", actor="Priya")
    journal(biz, "work", "Added a heading to the home page", NOW, finding="RENDER_H1_MISSING")
    journal(biz, "work", "Tried to add schema", NOW, finding="SCHEMA_NONE_FOUND")
    journal(biz, "work", "Work logged after B was measured", NOW + timedelta(days=30), finding="RENDER_H1_MISSING")
    res = compare(biz, tmp_path)
    closed = {c["code"]: c for c in res.data["diagnosis"]["closed"]}
    w = closed["CRAWLER_BLOCKED_OAI_SEARCHBOT"]["work"]
    assert len(w) == 1 and w[0]["text"] == "Allowed OAI-SearchBot in Cloudflare" and w[0]["operator"] == "Priya"
    assert w[0]["date"] == NOW.date().isoformat()
    assert [x["text"] for x in closed["RENDER_H1_MISSING"]["work"]] == ["Added a heading to the home page"]   # not the later one
    assert closed["LLMS_TXT_MISSING"]["work"] == []
    assert [x["text"] for x in res.data["diagnosis"]["still_open"][0]["work"]] == ["Tried to add schema"]    # logged as done, still open
    md = res.md_path.read_text()
    assert "work logged: Allowed OAI-SearchBot in Cloudflare" in md and "by Priya" in md
    assert "no work-log entry carries this code" in md


def test_work_logged_through_aeo_log_links_back(biz, tmp_path):
    diag_pair(biz)
    conn = get_connection()
    try:
        ev.add_work(conn, business=biz, text="Fixed the heading", operator="Sam", finding="RENDER_H1_MISSING")
    finally:
        conn.close()
    closed = {c["code"]: c for c in compare(biz, tmp_path).data["diagnosis"]["closed"]}
    assert closed["RENDER_H1_MISSING"]["work"][0]["text"] == "Fixed the heading"


def test_a_caution_when_b_could_not_run_the_crawler_checks(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("CRAWLER_BLOCKED_OAI_SEARCHBOT", "high")], when=NOW - timedelta(days=5))
    add_diagnosis(biz, "check", [finding("CRAWLER_TEST_INCONCLUSIVE", "medium")], when=NOW + timedelta(days=5))
    d = compare(biz, tmp_path, "before", "check").data["diagnosis"]
    assert d["caveats"] and "cannot be called closed" in d["caveats"][0]


def test_a_diagnosis_can_be_chosen_by_id(biz, tmp_path):
    old = add_diagnosis(biz, "check", [finding("RENDER_H1_MISSING", "medium")], when=NOW - timedelta(days=20))
    new = add_diagnosis(biz, "check", [], when=NOW + timedelta(days=1))
    d = compare(biz, tmp_path, "check", "check", a_diagnosis=old, b_diagnosis=new).data["diagnosis"]
    assert d["a"]["id"] == old and d["b"]["id"] == new and d["headline"] == "1 of 1 findings closed"


def test_the_same_label_twice_without_choosing_is_refused(biz, tmp_path):
    add_diagnosis(biz, "before", [])
    with pytest.raises(CompareError, match="same diagnosis"):
        compare(biz, tmp_path, "before", "before")


def test_a_diagnosis_of_another_business_is_refused(biz, tmp_path):
    from core.businesses import add_business
    other = add_business(f"https://o-{uuid.uuid4().hex[:8]}.example", name=f"Other {uuid.uuid4().hex[:8]}")
    foreign = add_diagnosis(other, "before", [])
    add_diagnosis(biz, "after_aeo", [])
    with pytest.raises(CompareError, match="does not exist for this business"):
        compare(biz, tmp_path, a_diagnosis=foreign)


def test_diagnosis_only_works_when_no_audit_exists(biz, tmp_path):
    diag_pair(biz)
    res = compare(biz, tmp_path)
    assert res.data["audit"]["available"] is False and "no audit run at checkpoint 'before'" in res.data["audit"]["reason"]
    assert res.data["diagnosis"]["available"] is True
    md = res.md_path.read_text()
    assert "3 of 4 findings closed" in md and "## 2. Audit comparison\n\nNot available" in md


def test_audit_only_works_when_no_diagnosis_exists(pair, tmp_path):
    res = compare(pair, tmp_path)
    assert res.data["diagnosis"]["available"] is False and "no diagnosis at checkpoint" in res.data["diagnosis"]["reason"]
    assert res.data["audit"]["available"] is True


def test_with_neither_there_is_nothing_to_compare(biz, tmp_path):
    with pytest.raises(CompareError, match="nothing to compare"):
        compare(biz, tmp_path)
    assert not (tmp_path / "clients").exists()


def test_a_bad_label_is_refused(biz, tmp_path):
    from core.checkpoints import CheckpointError
    with pytest.raises(CheckpointError):
        compare(biz, tmp_path, "yesterday", "after_aeo")


# ---- 2. audit comparison: refusals ----------------------------------------------------------------------------------------------

def test_different_question_sets_are_refused_with_a_reason(biz, tmp_path):
    add_run(biz, make_qs(10), lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, make_qs(10), lambda q, e, r: dict(named=RIVAL), checkpoint="after_aeo")     # new version and hash
    with pytest.raises(CompareError) as err:
        compare(biz, tmp_path)
    assert "different question sets" in str(err.value) and "not comparable" in str(err.value)
    assert not (tmp_path / "clients").exists()


def test_a_different_design_is_refused(biz, tmp_path):
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="after_aeo", engines=("chatgpt",))
    with pytest.raises(CompareError, match="different designs"):
        compare(biz, tmp_path)


def test_a_different_number_of_repeats_is_a_different_design(biz, tmp_path):
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="after_aeo", repeats=2)
    with pytest.raises(CompareError, match="different designs"):
        compare(biz, tmp_path)


def test_a_partial_run_chosen_by_id_is_refused(biz, tmp_path):
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    partial = add_run(biz, qs, lambda q, e, r: dict(named=RIVAL) if q < 5 else None, checkpoint="after_aeo", status="running")
    with pytest.raises(CompareError, match="not finished"):
        compare(biz, tmp_path, b_run=partial)


def test_a_partial_run_is_never_picked_by_default(biz, tmp_path):
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL) if q < 5 else None, checkpoint="after_aeo", status="running")
    add_diagnosis(biz, "before", [])
    add_diagnosis(biz, "after_aeo", [])
    res = compare(biz, tmp_path)
    assert res.data["audit"]["available"] is False and "not finished" in res.data["audit"]["reason"]
    assert res.data["diagnosis"]["available"] is True


def test_the_same_run_twice_is_refused(pair, tmp_path):
    with pytest.raises(CompareError, match="same audit run"):
        compare(pair, tmp_path, "before", "before")


# ---- 2. audit comparison: numbers -----------------------------------------------------------------------------------------------

def test_each_side_matches_the_audit_report_and_the_change_is_b_minus_a(pair, tmp_path):
    from audit.report import make_report
    res = compare(pair, tmp_path)
    v = measure(res)
    assert v["a"]["value"] == 0.0 and v["b"]["value"] == 100.0 and v["change"]["value"] == 100.0
    rep = make_report(pair, checkpoint="after_aeo", clients_root=tmp_path / "clients", today=TODAY)
    own = rep.data["headline"]["measures"]["visibility"]
    assert (v["b"]["value"], v["b"]["low"], v["b"]["high"]) == (own["value"], own["low"], own["high"])    # same counting, same seed
    assert measure(res, "recommendation")["change"]["value"] == 100.0
    assert measure(res, "brand_free")["a"]["value"] == 0.0


def test_both_denominators_are_compared(pair, tmp_path):
    m = measure(compare(pair, tmp_path), "visibility_among_named")
    assert m["a"]["den"] == 20 and m["b"]["den"] == 20 and m["change"]["value"] == 100.0


def test_real_change_when_the_range_excludes_zero(pair, tmp_path):
    c = measure(compare(pair, tmp_path))["change"]
    assert c["label"] == REAL and c["low"] > 0


def test_within_the_noise_when_the_range_includes_zero(biz, tmp_path):
    qs = make_qs(10)
    # A names the client in questions 0-4, B in questions 1-6: one question lost, two gained.
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q < 5 else RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if 1 <= q <= 6 else RIVAL), checkpoint="after_aeo")
    c = measure(compare(biz, tmp_path))["change"]
    assert c["value"] == 10.0 and c["low"] < 0 < c["high"] and c["label"] == NOISE


def test_identical_runs_are_within_the_noise_with_a_flat_range(biz, tmp_path):
    qs = make_qs(10)
    for label in ("before", "after_aeo"):
        add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q % 2 else RIVAL), checkpoint=label)
    c = measure(compare(biz, tmp_path))["change"]
    assert (c["value"], c["low"], c["high"]) == (0.0, 0.0, 0.0) and c["label"] == NOISE


def test_label_rules():
    assert label_for(0.1, 5) == REAL and label_for(-5, -0.1) == REAL
    assert label_for(-1, 5) == NOISE and label_for(0, 5) == NOISE and label_for(-5, 0) == NOISE
    assert label_for(None, None).startswith("no range")


def test_the_detection_floor_is_printed_next_to_the_labels(pair, tmp_path):
    res = compare(pair, tmp_path)
    assert res.data["detection_floor"]["points"] == 20
    md = res.md_path.read_text()
    assert "detects roughly a 20-point change, and not less" in md and "**real change**" in md


def test_the_paired_bootstrap_is_identical_on_a_rerun(pair, tmp_path):
    first, second = compare(pair, tmp_path), compare(pair, tmp_path)
    assert first.json_path != second.json_path
    assert json.loads(first.json_path.read_text())["audit"] == json.loads(second.json_path.read_text())["audit"]
    assert first.md_path.read_text() == second.md_path.read_text()
    assert second.md_path.name.endswith("_run2.md")


def test_paired_ranges_are_deterministic_and_use_the_audit_seed():
    ua = [{"n": 4, "named": i % 3} for i in range(9)]
    ub = [{"n": 4, "named": (i + 1) % 3} for i in range(9)]
    stat = {"v": lambda c: 100 * c["named"] / c["n"]}
    one = bootstrap.paired_ranges(ua, ub, stat)
    assert one == bootstrap.paired_ranges(ua, ub, stat)
    assert one != bootstrap.paired_ranges(ua, ub, stat, seed=1)


def test_pairing_by_question_is_what_makes_the_range_tight():
    # B is A plus the same fixed gain on every question; the base varies a lot from question to question.
    base = [0, 4, 1, 3, 0, 4, 2, 2]
    ua = [{"n": 4, "named": x} for x in base]
    ub = [{"n": 4, "named": x} for x in base]
    stat = {"v": lambda c: 100 * c["named"] / c["n"]}
    assert bootstrap.paired_ranges(ua, ub, stat)["v"] == (0.0, 0.0)          # same questions, same answers: no change, no spread
    assert bootstrap.ranges(ua, stat)["v"][1] - bootstrap.ranges(ua, stat)["v"][0] > 20     # a lone run's range is wide


def test_one_question_gives_no_range(biz, tmp_path):
    qs = make_qs(1)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC), checkpoint="after_aeo")
    c = measure(compare(biz, tmp_path))["change"]
    assert c["value"] == 100.0 and c["low"] is None and c["label"].startswith("no range")


def test_failed_extractions_are_left_out_and_reported(biz, tmp_path):
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    conn = get_connection()
    try:
        run_id = store.start_run(conn, business=store.load_business_and_profile(conn, biz["id"]), qs=qs, checkpoint="after_aeo",
                                 engines=["chatgpt", "perplexity"], answers_expected=20, how="fill_in_file", extra={"repeats": 1})
        n = 0
        for qi, q in enumerate(qs.items):
            for e in ("chatgpt", "perplexity"):
                rid, _ = store.add_result(conn, probe_run_id=run_id, question=q.text, engine=e, raw_answer="x", retrieval_activated=True,
                                          engine_version="m", logged_in_state="logged_out", location_context="M",
                                          question_set_version=qs.version, sources_cited=None, repeat_number=1)
                if not (qi == 0 and e == "chatgpt"):                  # this one's extraction failed: no businesses list
                    store.save_extraction(conn, rid, businesses=CLIENT_REC(), named_any=True, recognized=True, recommended=True)
                n += 1
        store.complete_run(conn, business_id=biz["id"], run_id=run_id, answers=n)
    finally:
        conn.close()
    res = compare(biz, tmp_path)
    assert res.data["audit"]["failed_extractions"] == {"a": 0, "b": 1}
    assert res.data["audit"]["b"]["scored"] == 19 and measure(res)["b"]["value"] == 100.0       # not 19/20
    assert "could not read are left out of every score: 0 in A, 1 in B" in res.md_path.read_text()


def test_accuracy_is_compared_only_when_both_runs_have_confirmed_facts(biz, tmp_path):
    qs = make_qs(10)
    claims = lambda: [claim("Phone is 514-555-0100", "Phone", "correct")]
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC, claims=claims()), checkpoint="before")        # no facts yet
    confirm_facts(biz, Phone="514-555-0100")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC, claims=claims()), checkpoint="after_aeo")
    res = compare(biz, tmp_path)
    assert "accuracy" not in res.data["audit"]["headline"]["measures"] and res.data["audit"]["accuracy_compared"] is False
    assert res.data["audit"]["wrong_facts"]["available"] is False
    assert "Accuracy is not compared" in res.md_path.read_text()


def test_accuracy_is_compared_when_both_have_them(biz, tmp_path):
    confirm_facts(biz, Phone="514-555-0100", Areas_we_serve="Montreal")
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC, claims=[claim("p", "Phone", "correct"), claim("a", "Areas we serve", "incorrect")]),
            checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC, claims=[claim("p", "Phone", "correct"), claim("a", "Areas we serve", "correct")]),
            checkpoint="after_aeo")
    m = measure(compare(biz, tmp_path), "accuracy")
    assert round(m["a"]["value"], 1) == 50.0 and m["b"]["value"] == 100.0 and m["change"]["label"] == REAL


# ---- targets vs control ----------------------------------------------------------------------------------------------------------------

def test_targets_versus_control(biz, tmp_path):
    qs = make_qs(10, targets=(0, 1, 2, 3, 4))
    # Everything is named a little more often in B (an engine update, say); the targets gain much more.
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q in (5, 6) else RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if (q < 5 or q in (5, 6, 7)) else RIVAL), checkpoint="after_aeo")
    t = compare(biz, tmp_path).data["audit"]["targets_vs_control"]
    assert t["available"] and t["target_questions"] == 5 and t["control_questions"] == 5
    v = t["measures"]["visibility"]
    assert v["target"]["value"] == 100.0 and v["control"]["value"] == 20.0 and v["difference"]["value"] == 80.0
    assert v["difference"]["low"] is not None and v["difference"]["low"] <= 80.0 <= v["difference"]["high"]
    md = (tmp_path / "clients" / biz["domain"] / "generated").glob("*.md")
    assert "the honest measure of our work" in next(md).read_text()


def test_targets_vs_control_says_so_when_a_run_has_no_tags(biz, tmp_path):
    qs = make_qs(10, targets=(0, 1, 2))
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    run_b = add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC), checkpoint="after_aeo")
    conn = get_connection()
    try:
        with conn.cursor() as cur:       # an older run, from before tags were recorded
            cur.execute("UPDATE client_journal SET details = details - 'questions' WHERE entry_type = 'probe_started' "
                        "AND details->>'probe_run_id' = %s", (run_b,))
    finally:
        conn.close()
    res = compare(biz, tmp_path)
    t = res.data["audit"]["targets_vs_control"]
    assert t["available"] is False and "run B did not record which questions are targets" in t["reason"]
    assert "Not available: run B did not record" in res.md_path.read_text()


def test_targets_vs_control_needs_both_groups(biz, tmp_path):
    qs = make_qs(10, targets=tuple(range(10)))
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC), checkpoint="after_aeo")
    assert "no control" in compare(biz, tmp_path).data["audit"]["targets_vs_control"]["reason"]


# ---- share of voice, wrong facts, sources ------------------------------------------------------------------------------------------

def test_share_of_voice_and_sources(biz, tmp_path):
    d = biz["domain"]
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=lambda: [nb("Rival Co", pos=1), nb("Fading Inc", pos=2)] + ([nb(CLIENT, client=True, pos=3)] if q < 2 else []),
                                          sources=[link("news.example")]), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=lambda: [nb(CLIENT, client=True, rec=True, pos=1), nb("Rival Co", pos=2), nb("Rising LLC", pos=3)],
                                          sources=[link(d, "/p"), link("news.example")] if q < 4 else [link("news.example")]),
            checkpoint="after_aeo")
    s = compare(biz, tmp_path).data["audit"]
    sov = s["share_of_voice"]
    assert sov["client"]["a"]["rank"] == 3 and sov["client"]["a"]["answers"] == 4
    assert sov["client"]["b"]["rank"] in (1, 2) and sov["client"]["b"]["answers"] == 20
    assert [m["name"] for m in sov["gained"]] == ["Rising LLC"] and sov["gained"][0]["change"] == 20
    assert [m["name"] for m in sov["lost"]] == ["Fading Inc"] and sov["lost"][0]["change"] == -20
    assert all(m["name"] != CLIENT for m in sov["gained"] + sov["lost"])
    assert s["sources"]["a"]["client_cited_in"] == 0 and s["sources"]["b"]["client_cited_in"] == 8       # 4 questions x 2 engines


def test_wrong_facts_fixed_still_and_new(biz, tmp_path):
    confirm_facts(biz, Phone="514-555-0100", Areas_we_serve="Montreal", Founded="2019")
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC, claims=[claim("Phone is 438-000-0000", "Phone", "incorrect"),
                                                                      claim("Only serves Ontario", "Areas we serve", "incorrect"),
                                                                      claim("Opened in 2015", "Founded", "correct")]) if q == 0 else dict(named=CLIENT_REC),
            checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC, claims=[claim("Phone is 514-555-0100", "Phone", "correct"),
                                                                      claim("Serves Ontario and Quebec", "Areas we serve", "incorrect"),
                                                                      claim("Founded in 2010", "Founded", "incorrect")]) if q == 0 else dict(named=CLIENT_REC),
            checkpoint="after_aeo")
    w = compare(biz, tmp_path).data["audit"]["wrong_facts"]
    assert w["available"]
    assert [r["fact"] for r in w["fixed"]] == ["Phone"]
    assert [r["fact"] for r in w["still"]] == ["Areas we serve"] and w["still"][0]["answers_a"] == 2 and w["still"][0]["answers_b"] == 2
    assert [r["fact"] for r in w["new"]] == ["Founded"]
    assert w["different_truth_documents"] is False
    md = (tmp_path / "clients" / biz["domain"] / "generated").glob("*.md")
    text = next(md).read_text()
    assert "**Fixed (stated in A, not in B): 1**" in text and "**New (stated in B, not in A): 1**" in text


def test_claims_that_cannot_be_verified_are_not_wrong_facts(biz, tmp_path):
    confirm_facts(biz, Phone="514-555-0100")
    qs = make_qs(10)
    for label in ("before", "after_aeo"):
        add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC, claims=[claim("Offers delivery", None, "unverifiable")]), checkpoint=label)
    w = compare(biz, tmp_path).data["audit"]["wrong_facts"]
    assert w["fixed"] == w["still"] == w["new"] == []


# ---- 3. in between ----------------------------------------------------------------------------------------------------------------

def test_work_and_events_between_the_dates_are_listed_and_events_are_flagged(biz, tmp_path):
    diag_pair(biz)          # A is 10 days ago, B is 10 days ahead
    journal(biz, "work", "Rewrote the home page", NOW, finding="RENDER_H1_MISSING")
    journal(biz, "event", "Client changed their own opening hours", NOW + timedelta(days=2), actor="Jean")
    journal(biz, "event", "ChatGPT shipped a new model", NOW + timedelta(days=5), actor="Sam")
    journal(biz, "work", "Work long before A", NOW - timedelta(days=60))
    journal(biz, "event", "Event long after B", NOW + timedelta(days=60))
    res = compare(biz, tmp_path)
    b = res.data["between"]
    assert [e["text"] for e in b["entries"]] == ["Rewrote the home page", "Client changed their own opening hours", "ChatGPT shipped a new model"]
    assert [e["outside_our_control"] for e in b["entries"]] == [False, True, True] and b["events"] == 2
    md = res.md_path.read_text()
    assert "**2 event(s) outside our control happened in this period.**" in md
    assert "**OUTSIDE OUR CONTROL: event, " in md and "Client changed their own opening hours" in md
    assert "Work long before A" not in md and "Event long after B" not in md


def test_an_empty_period_says_nothing_was_logged(biz, tmp_path):
    diag_pair(biz)
    assert "Nothing was logged in this period" in compare(biz, tmp_path).md_path.read_text()


def test_b_dated_before_a_lists_nothing_and_says_why(biz, tmp_path):
    add_diagnosis(biz, "before", [], when=NOW + timedelta(days=5))
    add_diagnosis(biz, "after_aeo", [], when=NOW - timedelta(days=5))
    assert "B is dated before A" in compare(biz, tmp_path).data["between"]["note"]


# ---- output files ------------------------------------------------------------------------------------------------------------------

def test_the_json_is_exactly_what_the_markdown_was_made_from(pair, tmp_path):
    diag_pair(pair)
    res = compare(pair, tmp_path)
    data = json.loads(res.json_path.read_text())
    assert data == res.data
    assert render_markdown(data) == res.md_path.read_text()


def test_files_are_named_by_date_labels_and_run(pair, tmp_path):
    res = compare(pair, tmp_path)
    assert res.md_path.name == "2026-10-20_compare_before_vs_after_aeo_run1.md"
    assert res.json_path.name == "2026-10-20_compare_before_vs_after_aeo_run1.json"
    assert res.md_path.parent.name == "generated"
    assert compare(pair, tmp_path).md_path.name.endswith("_run2.md")


def test_nothing_is_changed_in_the_database(pair, tmp_path):
    def counts():
        c = get_connection()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT (SELECT count(*) FROM client_journal WHERE business_id = %s), "
                            "(SELECT count(*) FROM probe_results r JOIN probe_runs p ON p.id = r.probe_run_id WHERE p.business_id = %s), "
                            "(SELECT count(*) FROM visibility_scores v JOIN probe_runs p ON p.id = v.probe_run_id WHERE p.business_id = %s)",
                            (pair["id"],) * 3)
                return cur.fetchone()
        finally:
            c.close()
    before = counts()
    compare(pair, tmp_path)
    assert counts() == before


def test_the_command_prints_the_headlines(pair, tmp_path):
    diag_pair(pair)
    result = CliRunner().invoke(cli.app, ["compare", "before", "after_aeo", "--business", pair["domain"]])
    assert result.exit_code == 0, result.output
    assert "3 of 4 findings closed" in result.output and "real change" in result.output
    assert "detects roughly a 20-point change" in result.output


def test_the_command_refuses_with_the_reason(biz):
    add_run(biz, make_qs(4), lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, make_qs(4), lambda q, e, r: dict(named=RIVAL), checkpoint="after_aeo")
    result = CliRunner().invoke(cli.app, ["compare", "before", "after_aeo", "--business", biz["domain"]])
    assert result.exit_code == 1 and "different question sets" in result.output
