"""Tests for `aeo dossier` (PLAN.md step 22e). Real Postgres (aeo_test). No AI is involved anywhere."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aeo import cli
from core import businesses, evidence as ev
from core.db import get_connection
from diagnose import store as dstore
from dossier import notes as notes_mod
from dossier import questions as dq
from dossier.data import ACCESS_ITEMS, SIGNALS
from dossier.render import render_html, render_md
from dossier.run import DossierError, make_dossier
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_audit_report import claim
from tests.test_compare import CLIENT_REC, NOW, RIVAL, add_diagnosis, add_run, finding, journal, make_qs
from tests.test_honest_closed import SIX_SCHEMA, oland

TODAY = date(2026, 10, 25)


def dossier(biz_, tmp_path, **kw):
    kw.setdefault("question_dir", tmp_path / "qdir")
    return make_dossier(biz_, clients_root=tmp_path / "clients", today=TODAY, **kw)


def section(res, key):
    return next(s for s in res.data["sections"] if s["key"] == key)


def rich_diagnosis(biz_, label, *, when, platform="wix", findings=(), render=None, crawler=None, jsonld=()):
    conn = get_connection()
    try:
        return str(dstore.insert_diagnosis(
            conn, business_id=biz_["id"], checkpoint=label, url=f"https://{biz_['domain']}/", checked_at=when, platform=platform,
            platform_version=None, platform_plan=None, robots_txt=None, existing_jsonld=list(jsonld), crawler_access=crawler or {},
            render=render, findings=list(findings), verdict="serve", verdict_reason="r", readiness_score=None))
    finally:
        conn.close()


def add_evidence_file(biz_, tmp_path, name, kind, *, checkpoint="before", note="note", period=None, captured="2026-10-14"):
    src = tmp_path / "incoming" / name
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"data " + name.encode())
    conn = get_connection()
    try:
        extra = {"period_start": period[0], "period_end": period[1]} if period else {}
        return ev.add_evidence(conn, business=biz_, checkpoint=checkpoint, file=src, note=note, evidence_type=kind, source="us",
                               captured=captured, operator="Sam", clients_root=tmp_path / "clients", today=TODAY, **extra)
    finally:
        conn.close()


def set_engagement(biz_, kind):
    conn = get_connection()
    try:
        businesses.set_engagement(conn, biz_["id"], engagement_type=kind, access_level="cms", actor="Sam")
    finally:
        conn.close()


def write_questions(path: Path, items) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("questions:\n" + "".join(f"  - {json.dumps(i)}\n" for i in items))
    return path


# ---- the notes file ---------------------------------------------------------------------------------------------------------------

def test_the_notes_file_is_created_once_from_a_template_with_a_heading_per_section(biz, tmp_path):
    res = dossier(biz, tmp_path)
    assert res.notes_created and res.notes_path.name == "dossier_notes.md"
    text = res.notes_path.read_text()
    for _, heading in notes_mod.SECTIONS:
        assert f"\n## {heading}\n" in text
    assert text.count("\n## ") == len(notes_mod.SECTIONS) == 11
    assert res.warnings == []


def test_the_notes_file_is_never_overwritten(biz, tmp_path):
    first = dossier(biz, tmp_path)
    mine = first.notes_path.read_text().replace("## Stage\n", "## Stage\nThe client says the move is on 3 November.\n")
    first.notes_path.write_text(mine)
    second = dossier(biz, tmp_path)
    assert not second.notes_created and second.notes_path.read_text() == mine


def test_the_text_under_each_heading_is_copied_word_for_word_into_its_section(biz, tmp_path):
    first = dossier(biz, tmp_path)
    text = first.notes_path.read_text()
    body = "Line one, with  odd   spacing.\n\n- a bullet\n  indented *markdown* and <b>html</b>"
    first.notes_path.write_text(text.replace("## Business snapshot\n", f"## Business snapshot\n{body}\n"))
    second = dossier(biz, tmp_path)
    assert section(second, "snapshot")["notes"] == body
    md = second.md_path.read_text()
    assert "### Our notes\n\n" + body in md
    assert "No notes yet." in md                                           # the other sections have none
    assert body.replace("<b>", "&lt;b&gt;").replace("</b>", "&lt;/b&gt;") in second.html_path.read_text()


def test_a_renamed_heading_warns_and_does_not_guess(biz, tmp_path):
    first = dossier(biz, tmp_path)
    text = first.notes_path.read_text().replace("## Stage\n", "## Where they are\nSome text that belongs to the stage.\n")
    first.notes_path.write_text(text)
    second = dossier(biz, tmp_path)
    assert any("Where they are" in w and "NOT copied" in w for w in second.warnings)
    assert any("no heading \"Stage\"" in w for w in second.warnings)
    assert section(second, "stage")["notes"] == "" and "Some text that belongs" not in second.md_path.read_text()
    assert "Notes file:" in second.md_path.read_text() and "Notes file:" in second.html_path.read_text()


def test_an_added_heading_warns(biz, tmp_path):
    first = dossier(biz, tmp_path)
    first.notes_path.write_text(first.notes_path.read_text() + "\n## Extra thoughts\nhello\n")
    second = dossier(biz, tmp_path)
    assert len(second.warnings) == 1 and "Extra thoughts" in second.warnings[0]


# ---- every section, empty and filled --------------------------------------------------------------------------------------------------

def test_every_section_works_with_nothing_in_the_database(biz, tmp_path):
    res = dossier(biz, tmp_path)
    assert [s["key"] for s in res.data["sections"]] == [k for k, _ in notes_mod.SECTIONS]
    for s in res.data["sections"]:
        assert s["answer"].strip() and s["blocks"], s["key"]
    md = res.md_path.read_text()
    for n, (_, heading) in enumerate(notes_mod.SECTIONS, start=1):
        assert f"## {n}. {heading}" in md
    assert section(res, "how_to_use")["data"]["previous_version"] is None
    assert "No truth document" in section(res, "snapshot")["answer"]
    assert "No diagnosis yet" in section(res, "how_ai_sees")["answer"]
    assert "No AI crawler report yet" in section(res, "crawler")["answer"]
    assert "spot check has not been run" in section(res, "ai_says")["answer"]
    assert section(res, "problems")["data"]["items"]          # nothing is collected, so the gaps themselves are the problems
    assert "no question set yet" in section(res, "questions")["answer"]
    assert section(res, "signals")["answer"].startswith("0 of 3")
    assert "Not collected yet" in md


def filled(biz_, tmp_path):
    """A client with a diagnosis per checkpoint, facts, a snapshot run, evidence, work and a question file."""
    confirm_facts(biz_, Phone="514-555-0100", Areas_we_serve="Montreal")
    schema = json.dumps({"@context": "https://schema.org", "@type": "LocalBusiness", "name": "11297775 Canada Inc", "areaServed": "Montreal",
                         "openingHours": ["Mo-Fr 09:00-17:00"]})
    rich_diagnosis(biz_, "before", when=NOW - timedelta(days=9), platform="wix",
                   findings=[finding("SCHEMA_NAME_IS_LEGAL_ENTITY", "high"), finding("CRAWLER_BLOCKED_OAI_SEARCHBOT", "high"), finding("LLMS_TXT_MISSING", "low")],
                   render={"word_count": 412, "h1_texts": ["Welcome", ""], "verdict": "content_present"}, jsonld=[schema],
                   crawler={"control": {}, "bots": {"OAI-SearchBot": {"verdict": "challenged", "kind": "retrieval"}, "GPTBot": {"verdict": "allowed", "kind": "training"}}})
    rich_diagnosis(biz_, "after_site", when=NOW - timedelta(days=2), platform="nextjs",
                   findings=[finding("SCHEMA_NONE_FOUND", "medium")], render={"word_count": 90, "h1_texts": ["Hi"], "verdict": "content_present"},
                   crawler={"control": {}, "bots": {}})
    qs = make_qs(4, targets=(0,))
    biz_["qs"] = qs

    def answer(q, e, r):
        d = {"named": CLIENT_REC if (q + (e == "chatgpt")) % 2 == 0 else RIVAL, "sources": []}
        if q == 0:
            d["claims"] = [claim("Only serves Ontario", "Areas we serve", "incorrect"), claim("Phone is 514-555-0100", "Phone", "correct")]
        return d
    biz_["run_id"] = add_run(biz_, qs, answer, checkpoint="before")
    add_evidence_file(biz_, tmp_path, "cdn.pdf", "cdn_report", note="Bots blocked on 3 paths", period=("2026-04-14", "2026-10-14"))
    journal(biz_, "work", "Added a heading", NOW - timedelta(days=1), finding="RENDER_H1_MISSING")
    return biz_


def test_every_section_has_content_when_the_data_exists(biz, tmp_path):
    filled(biz, tmp_path)
    qpath = write_questions(tmp_path / "draft_v1.yaml", [
        {"text": "refill station rental", "type": "discovery", "language": "en"},
        {"text": "We run a 300 person festival in Toronto in July. Who rents water stations? We need six of them.", "type": "discovery", "is_target": True},
        {"text": f"What does {biz['name']} do?", "type": "brand"}])
    res = dossier(biz, tmp_path, questions=qpath)
    assert section(res, "snapshot")["data"]["total"] == 2 and "2 of 2 facts are confirmed" in section(res, "snapshot")["answer"]
    assert "2 diagnosis columns" in section(res, "how_ai_sees")["answer"]
    assert "1 AI crawler report" in section(res, "crawler")["answer"] and "Bots blocked on 3 paths" in res.md_path.read_text()
    assert section(res, "ai_says")["data"]["run"]["checkpoint"] == "before"
    assert section(res, "problems")["data"]["items"]
    assert section(res, "questions")["data"]["counts"] == {"short": 2, "medium": 0, "long": 1}
    assert "1 of 3 AI visibility signals" in section(res, "signals")["answer"]
    assert section(res, "next")["answer"].startswith("Next: ")


# ---- how AI sees the site ----------------------------------------------------------------------------------------------------------------

def test_how_ai_sees_the_site_has_a_column_per_checkpoint_and_the_listed_rows(biz, tmp_path):
    filled(biz, tmp_path)
    cols = section(dossier(biz, tmp_path), "how_ai_sees")["data"]["columns"]
    assert [c["label"] for c in cols] == ["before", "after_site"]
    before, after = cols[0]["rows"], cols[1]["rows"]
    assert before["OAI-SearchBot (retrieval bot)"] == "blocked: challenged" and before["GPTBot (training bot)"] == "allowed"
    assert after["OAI-SearchBot (retrieval bot)"] == "allowed"
    assert before["Readable words (no JavaScript)"] == "412" and before["Main headings (h1)"] == "2 (1 empty)"
    assert before["Render test"] == "content present"
    assert before["Structured data blocks"] == "LocalBusiness" and before["Name in the structured data"] == "11297775 Canada Inc"
    assert before["Areas served"] == "given" and before["Opening hours"] == "present" and before["llms.txt"] == "missing"
    assert after["Structured data blocks"] == "none found" and after["Name in the structured data"] == "no structured data"
    assert before["Sitemap and host consistency"] == "not checked yet" == after["Sitemap and host consistency"]
    assert {"Readable words (no JavaScript)", "Render test", "Main headings (h1)", "Structured data blocks", "llms.txt"} <= set(before)


def test_untested_parts_say_not_tested(biz, tmp_path):
    add_diagnosis(biz, "before", [])             # the shared test helper stores no crawler test and no render test
    col = section(dossier(biz, tmp_path), "how_ai_sees")["data"]["columns"][0]["rows"]
    assert col["OAI-SearchBot (retrieval bot)"] == "not tested" and col["Readable words (no JavaScript)"] == "not tested"


def test_the_outcomes_between_checkpoints_use_part_one_in_plain_words(biz, tmp_path):
    oland(biz)
    res = dossier(biz, tmp_path)
    step = section(res, "how_ai_sees")["data"]["steps"][0]
    assert step["headline"] == "2 fixed · 6 no longer apply · 1 still open · 1 new"
    md = res.md_path.read_text()
    assert "before → after_site: 2 fixed · 6 no longer apply · 1 still open · 1 new." in md
    assert ("No longer applies: SCHEMA_ORG_MISSING_NAME what [SCHEMA_ORG_MISSING_NAME]; replaced by: SCHEMA_NONE_FOUND what [SCHEMA_NONE_FOUND]. "
            "Not fixed, still to be redone") in md


# ---- stage ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_stage_table_is_derived_from_the_database(biz, tmp_path):
    set_engagement(biz, "web_dev_aeo")
    empty = {i["item"]: i["status"] for i in section(dossier(biz, tmp_path), "stage")["data"]["items"]}
    assert empty["Diagnoses"] == "not yet" and empty["Spot check run"] == "not yet" and empty["Site-move evidence logged"] == "not yet"
    assert empty["Access: Analytics (old site)"] == "not yet" and empty["Work items logged"] == "not yet"
    filled(biz, tmp_path)
    add_evidence_file(biz, tmp_path, "redirects.txt", "site_move", checkpoint="after_site")
    add_evidence_file(biz, tmp_path, "gsc.csv", "search_console")
    add_evidence_file(biz, tmp_path, "ga_old.csv", "analytics", checkpoint="before")
    res = dossier(biz, tmp_path)
    got = {i["item"]: i for i in section(res, "stage")["data"]["items"]}
    assert got["Diagnoses"]["detail"] == "before: 1, after_site: 1" and got["Diagnoses"]["status"] == "done"
    assert got["Site-move evidence logged"]["status"] == "done" and got["Spot check run"]["status"] == "done"
    assert got["Facts confirmed"]["detail"] == "2 of 2" and got["Work items logged"]["detail"] == "1"
    assert got["Access: Google Search Console"]["status"] == "done" and got["Access: Analytics (old site)"]["status"] == "done"
    assert got["Access: Analytics (current site)"]["status"] == "not yet" and got["Access: Google Business Profile"]["status"] == "not yet"
    assert got["Audit: before"]["status"] == "done" and got["Question set"]["status"] == "not yet"
    assert "web_dev_aeo" in section(res, "stage")["answer"]


def test_old_site_analytics_and_site_move_do_not_apply_to_an_aeo_only_client(biz, tmp_path):
    set_engagement(biz, "aeo_only")
    got = {i["item"]: i["status"] for i in section(dossier(biz, tmp_path), "stage")["data"]["items"]}
    assert got["Site-move evidence logged"] == "not applicable" and got["Access: Analytics (old site)"] == "not applicable"


# ---- What AI says today -----------------------------------------------------------------------------------------------------------------------------

def test_the_spot_check_numbers_equal_the_extraction_data(biz, tmp_path):
    filled(biz, tmp_path)
    res = dossier(biz, tmp_path)
    data = section(res, "ai_says")["data"]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT query_text, engine, businesses_named, claims_checked FROM probe_results WHERE probe_run_id = %s", (biz["run_id"],))
            rows = cur.fetchall()
    finally:
        conn.close()
    assert len(data["rows"]) == len(rows) == 8                                         # 4 questions x 2 engines, one run
    for r in data["rows"]:
        question_rows = [x for x in rows if x[0] == r["question"] and x[1].lower() in (r["engine"].lower(), "chatgpt" if "chat" in r["engine"].lower() else r["engine"].lower())]
        (_, _, named, claims), = question_rows
        assert r["client_named"] == (1 if any(b["is_client"] for b in named) else 0)
        assert r["wrong_facts"] == sum(1 for c in (claims or []) if c["verdict"] == "incorrect")
        assert r["other_names"] == sorted({b["name"] for b in named if not b["is_client"]})
    total_named = sum(1 for x in rows if any(b["is_client"] for b in x[2]))
    assert data["discovery"]["client_named"] + data["brand"]["client_named"] == total_named
    assert data["discovery"]["answers"] + data["brand"]["answers"] == len(rows)
    assert data["competitors"] == [{"name": "Rival Co", "answers": len(rows)}]
    assert sum(r["wrong_facts"] for r in data["rows"]) == 2                           # question 0, once per engine
    md = res.md_path.read_text()
    assert "SNAPSHOT" in md and f"{data['discovery']['client_named']} of {data['discovery']['answers']} discovery answers" in section(res, "ai_says")["answer"]


def test_a_brand_question_is_told_apart_from_a_discovery_question(biz, tmp_path):
    qs = make_qs(2)
    qs.items[0].text = f"What does {biz['name']} do?"
    qs.items[1].text = "refill station rental for a festival"
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q == 0 else RIVAL), checkpoint="before")
    data = section(dossier(biz, tmp_path), "ai_says")["data"]
    assert data["brand"] == {"answers": 2, "client_named": 2} and data["discovery"] == {"answers": 2, "client_named": 0}
    assert {r["question"]: r["type"] for r in data["rows"]} == {qs.items[0].text: "brand", qs.items[1].text: "discovery"}


def test_with_no_spot_check_it_says_not_run_and_lists_the_questions(biz, tmp_path):
    qpath = write_questions(tmp_path / "draft_v1.yaml", [{"text": "refill station rental"}, {"text": "water stations for events in Toronto"}])
    res = dossier(biz, tmp_path, questions=qpath)
    d = section(res, "ai_says")["data"]
    assert d["run"] is None and d["questions_to_ask"] == ["refill station rental", "water stations for events in Toronto"]
    assert "Not run yet." in res.md_path.read_text() and "- refill station rental" in res.md_path.read_text()


def test_a_run_that_is_not_finished_or_is_not_a_snapshot_is_not_a_spot_check(biz, tmp_path):
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before", status="in_progress")
    assert section(dossier(biz, tmp_path), "ai_says")["data"]["run"] is None
    big = make_qs(32)
    add_run(biz, big, lambda q, e, r: dict(named=RIVAL), checkpoint="after_aeo", repeats=3)
    d = section(dossier(biz, tmp_path), "ai_says")
    assert d["data"]["run"] is None and "not a spot check" in d["answer"]


# ---- proposed question set -----------------------------------------------------------------------------------------------------------------------

def test_a_brand_name_in_a_discovery_question_is_flagged(biz, tmp_path):
    qpath = write_questions(tmp_path / "draft_v1.yaml", [
        {"text": "refill station rental", "type": "discovery"},
        {"text": f"is {biz['name']} good for events", "type": "discovery"},                 # the name
        {"text": f"best refill stations like {biz['domain']}", "type": "discovery"},        # the domain
        {"text": f"what does {biz['name']} do", "type": "brand"},                            # fine: a brand question
        {"text": f"{biz['name'].lower()} reviews"}])                                        # no tag, names the brand
    res = dossier(biz, tmp_path, questions=qpath)
    warnings = section(res, "questions")["data"]["warnings"]
    assert len(warnings) == 3 and sum("tagged discovery" in w for w in warnings) == 2 and sum("has no type tag" in w for w in warnings) == 1
    assert not any("what does" in w for w in warnings) and not any("refill station rental" in w for w in warnings)
    assert "Warning:** Discovery question contains the brand" in res.md_path.read_text()
    assert "3 warning" in section(res, "questions")["answer"]


def test_questions_are_grouped_by_type_and_length_with_counts_and_draft_or_frozen(biz, tmp_path):
    qpath = write_questions(tmp_path / "set_v1.yaml", [
        {"text": "refill station rental", "type": "discovery", "language": "en"},
        {"text": "Who rents water stations for a summer festival in Toronto?", "type": "discovery", "language": "en", "is_target": True},
        {"text": "On 12 July we host 300 people in Laval. We need six refill stations and branding. Who can do this?", "type": "discovery", "language": "fr"},
        {"text": f"Tell me about {biz['domain']}", "type": "brand", "length": "short"}])
    res = dossier(biz, tmp_path, questions=qpath)
    d = section(res, "questions")["data"]
    assert d["counts"] == {"short": 2, "medium": 1, "long": 1} and d["targets"] == 1 and d["languages"] == {"en": 2, "fr": 1, "not tagged": 1}
    assert d["set"]["state"] == "draft" and "set_v1 is draft" in section(res, "questions")["answer"]
    md = res.md_path.read_text()
    for heading in ("### Discovery · short", "### Discovery · medium", "### Discovery · long", "### Brand · short"):
        assert heading in md
    # freezing = an audit used this version
    qs = make_qs(3)
    qs.version = "set_v1"
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    assert section(dossier(biz, tmp_path, questions=qpath), "questions")["data"]["set"]["state"] == "frozen"


def test_without_a_file_the_latest_question_set_an_audit_used_is_shown(biz, tmp_path):
    qs = make_qs(3)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    write_questions(tmp_path / "qdir" / f"{qs.version}.yaml", [{"text": q.text} for q in qs.items])
    d = section(dossier(biz, tmp_path), "questions")["data"]
    assert d["set"]["version"] == qs.version and d["set"]["state"] == "frozen" and len(d["items"]) == 3


def test_a_bad_question_file_stops_with_a_plain_message(biz, tmp_path):
    with pytest.raises(DossierError, match="not found"):
        dossier(biz, tmp_path, questions=tmp_path / "nope.yaml")
    bad = tmp_path / "bad_v1.yaml"
    bad.write_text("hello: world\n")
    with pytest.raises(DossierError, match="questions:"):
        dossier(biz, tmp_path, questions=bad)


def test_length_and_brand_helpers():
    assert dq.length_of("refill station rental") == "short"
    assert dq.length_of("Who rents water stations for a summer festival in Toronto?") == "medium"
    assert dq.length_of("We host 300 people. We need six stations.") == "long"
    terms = dq.brand_terms("O'land Stations", "www.olandstations.com")
    assert dq.contains_brand("tell me about Oland Stations", terms) and dq.contains_brand("olandstations.com hours", terms)
    assert dq.contains_brand("water stations for festivals", terms) is None


# ---- problems, signals, needs, next ----------------------------------------------------------------------------------------------------------

def test_problems_are_ranked_with_a_kind_each(biz, tmp_path):
    filled(biz, tmp_path)
    confirm_facts(biz, facts=[{"label": "Founded", "value": "2019", "status": "unconfirmed"}], Phone="514-555-0100", Areas_we_serve="Montreal")
    res = dossier(biz, tmp_path)
    items = section(res, "problems")["data"]["items"]
    assert [i["rank"] for i in items] == list(range(1, len(items) + 1))
    assert {i["kind"] for i in items} <= {"site", "AEO", "facts", "access", "off-site"}
    sev = [{"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[i["severity"]] for i in items]
    assert sev == sorted(sev)
    kinds = {i["text"]: i["kind"] for i in items}
    assert kinds["Fact not confirmed: Founded"] == "facts" and kinds["Not collected yet: Google Business Profile"] == "off-site"
    assert kinds["Not collected yet: Google Search Console"] == "access"
    assert any(i["kind"] == "AEO" and i["text"].startswith("AI says: \"Only serves Ontario\"") for i in items)
    assert any(i["source"].endswith("SCHEMA_NONE_FOUND") for i in items)                  # the LATEST diagnosis's open findings
    assert not any(i["source"].endswith("CRAWLER_BLOCKED_OAI_SEARCHBOT") for i in items)


def test_a_missing_signal_is_a_problem_named_in_plain_words(biz, tmp_path):
    texts = [i["text"] for i in section(dossier(biz, tmp_path), "problems")["data"]["items"]]
    assert "Not collected yet: Copilot citations (Bing Webmaster Tools, AI Performance report)" in texts
    assert not any("bing_wmt" in t or "cdn_report" in t for t in texts)
    assert sum("Analytics" in t for t in texts) == 1                         # listed once, as the access item


def test_the_three_ai_signals_show_their_evidence(biz, tmp_path):
    assert [s[1] for s in SIGNALS] == ["bing_wmt", "analytics", "cdn_report"]
    add_evidence_file(biz, tmp_path, "cdn.png", "cdn_report", captured="2026-09-01")
    add_evidence_file(biz, tmp_path, "cdn2.png", "cdn_report", captured="2026-10-01")
    add_evidence_file(biz, tmp_path, "bing.csv", "bing_wmt", captured="2026-10-03")
    data = {s["signal"]: s for s in section(dossier(biz, tmp_path), "signals")["data"]["signals"]}
    assert data["AI crawler visits"] == {"signal": "AI crawler visits", "evidence_type": "cdn_report", "received": True, "latest": "2026-10-01"}
    assert data["Copilot citations"]["latest"] == "2026-10-03" and not data["Visits from AI assistants"]["received"]


def test_the_new_evidence_types_are_known_and_accepted(biz, tmp_path):
    assert {"bing_wmt", "analytics", "search_console", "gbp"} <= set(ev.EVIDENCE_TYPES)
    for kind in ("bing_wmt", "analytics", "search_console", "gbp"):
        add_evidence_file(biz, tmp_path, f"{kind}.csv", kind)
    access = {a["key"]: a["received"] for a in section(dossier(biz, tmp_path), "stage")["data"]["access"]}
    assert access["search_console"] and access["analytics"] and access["gbp"]
    assert "evidence add" in CliRunner().invoke(cli.app, ["evidence", "add", "--help"]).output
    assert "bing_wmt" in CliRunner().invoke(cli.app, ["evidence", "add", "--help"]).output.replace("\n", " ").replace("│", "")


def test_what_we_need_from_the_client_comes_from_the_gaps(biz, tmp_path):
    set_engagement(biz, "web_dev_aeo")
    confirm_facts(biz, facts=[{"label": "Opening hours", "value": "9-5", "status": "unconfirmed"}], Phone="514-555-0100")
    add_evidence_file(biz, tmp_path, "gsc.csv", "search_console")
    d = section(dossier(biz, tmp_path), "client_needs")["data"]
    assert d["access"] == ["Analytics (current site)", "Analytics (old site)", "Google Business Profile"]
    assert d["unconfirmed_facts"] == ["Opening hours"] and len(d["standard_asks"]) == 4
    assert any("priority customers" in a for a in d["standard_asks"]) and any("competitors" in a for a in d["standard_asks"])


def steps_of(res):
    return [s["step"] for s in section(res, "next")["data"]["steps"]]


def test_the_next_steps_change_as_the_state_changes(biz, tmp_path):
    set_engagement(biz, "web_dev_aeo")
    s = steps_of(dossier(biz, tmp_path))
    assert s[0] == "Run the diagnosis" and "Finish the site move" in s and "Run the spot check" in s and "Send the truth document" in s
    assert "Write the question set" in s and "Ask the client for access" in s

    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium")])
    s = steps_of(dossier(biz, tmp_path))
    assert "Run the diagnosis" not in s and "Run `aeo recommend`" in s

    add_evidence_file(biz, tmp_path, "redirects.txt", "site_move", checkpoint="after_site")
    s = steps_of(dossier(biz, tmp_path))
    assert "Finish the site move" not in s

    qs = make_qs(3)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    write_questions(tmp_path / "qdir" / f"{qs.version}.yaml", [{"text": q.text} for q in qs.items])
    s = steps_of(dossier(biz, tmp_path))
    assert "Run the spot check" not in s and "Write the question set" not in s and "Freeze the questions" not in s       # it was used: frozen

    draft = write_questions(tmp_path / "draft_v1.yaml", [{"text": "refill station rental"}])
    # a draft file given on the command line is not frozen yet, and the next step says so
    assert "Freeze the questions" in steps_of(dossier(biz, tmp_path, questions=draft))

    confirm_facts(biz, Phone="514-555-0100")
    assert "Send the truth document" not in steps_of(dossier(biz, tmp_path))
    first = section(dossier(biz, tmp_path), "next")
    assert first["answer"] == "Next: " + steps_of(dossier(biz, tmp_path))[0] + "."


def test_a_paying_client_with_no_audit_gets_audit_1(biz, tmp_path):
    assert "Audit 1" not in steps_of(dossier(biz, tmp_path))              # a prospect: nothing was paid
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE businesses SET status = 'client' WHERE id = %s", (biz["id"],))
    finally:
        conn.close()
    assert "Audit 1" in steps_of(dossier(biz, tmp_path))
    add_run(biz, make_qs(3), lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    assert "Audit 1" not in steps_of(dossier(biz, tmp_path))


def test_the_answer_names_the_first_step_or_says_nothing_is_blocking(biz, tmp_path):
    d = section(dossier(biz, tmp_path), "next")
    assert d["answer"] == f"Next: {d['data']['steps'][0]['step']}."
    # a complete baseline (a fully-filled client with a frozen question set) has no step left
    set_engagement(biz, "aeo_only")
    add_diagnosis(biz, "before", [])
    confirm_facts(biz, Phone="514-555-0100")
    qs = make_qs(3)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    write_questions(tmp_path / "qdir" / f"{qs.version}.yaml", [{"text": q.text} for q in qs.items])
    for kind in ("search_console", "analytics", "gbp"):
        add_evidence_file(biz, tmp_path, f"{kind}.csv", kind)
    done = section(dossier(biz, tmp_path), "next")
    assert done["data"]["steps"] == [] and done["answer"] == "Nothing is blocking: the baseline is complete."


# ---- how to use / versions ------------------------------------------------------------------------------------------------------------------

def test_versions_count_per_client_and_nothing_is_overwritten(biz, tmp_path):
    one = dossier(biz, tmp_path)
    two = dossier(biz, tmp_path)
    three = make_dossier(biz, clients_root=tmp_path / "clients", today=TODAY + timedelta(days=3), question_dir=tmp_path / "qdir")
    assert [r.data["version"] for r in (one, two, three)] == [1, 2, 3]
    assert one.md_path.name == "2026-10-25_dossier_v1.md" and three.md_path.name == "2026-10-28_dossier_v3.md"
    for r in (one, two, three):
        assert r.md_path.exists() and r.html_path.exists() and r.json_path.exists()
        assert r.md_path.parent == tmp_path / "clients" / biz["domain"] / "generated"
    assert len({r.md_path for r in (one, two, three)}) == 3


def test_the_first_section_says_what_is_new_since_the_last_version(biz, tmp_path):
    first = dossier(biz, tmp_path)
    assert "first dossier" in section(first, "how_to_use")["answer"]
    assert "nothing new" in section(dossier(biz, tmp_path), "how_to_use")["answer"]
    add_diagnosis(biz, "before", [])
    add_evidence_file(biz, tmp_path, "cdn.pdf", "cdn_report")
    confirm_facts(biz, Phone="514-555-0100")
    add_run(biz, make_qs(3), lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    res = dossier(biz, tmp_path)
    data = section(res, "how_to_use")["data"]
    assert data["new_since_last"] == ["1 new diagnosis", "1 new evidence file", "1 new confirmed fact", "1 new audit run"]
    assert "Version 3" in section(res, "how_to_use")["answer"] and "since version 2" in section(res, "how_to_use")["answer"]


# ---- the files ------------------------------------------------------------------------------------------------------------------------------------

def test_the_json_matches_the_markdown_and_the_html(biz, tmp_path):
    filled(biz, tmp_path)
    res = dossier(biz, tmp_path)
    data = json.loads(res.json_path.read_text())
    assert data == res.data
    assert res.md_path.read_text() == render_md(data) and res.html_path.read_text() == render_html(data)
    md = res.md_path.read_text()
    for s in data["sections"]:
        assert s["answer"] in md and f"## " in md
        for blk in s["blocks"]:
            if blk["t"] == "table":
                for row in blk["rows"][:3]:
                    assert " | ".join(" ".join(str(c).split()).replace("|", "\\|") for c in row) in md
    assert render_md(json.loads(json.dumps(data))) == md                                  # deterministic


def test_the_html_is_internal_self_contained_and_prints_internal_on_every_page(biz, tmp_path):
    filled(biz, tmp_path)
    page = dossier(biz, tmp_path).html_path.read_text()
    assert page.startswith("<!doctype html>") and page.count("<style>") == 1
    for banned in ("<script", "<link", "<iframe", "@import", "url(http", "url(//", "xmlns="):
        assert banned not in page, banned
    for attr, value in re.findall(r"""\b(src|href|action|data)\s*=\s*["']([^"']*)["']""", page):
        assert not re.match(r"(?i)\s*(https?:)?//", value)
    assert "INTERNAL: not for the client" in page and "internal running" in page
    css = re.sub(r"\s+", "", re.search(r"<style>(.*?)</style>", page, re.S).group(1))
    assert re.search(r"@mediaprint\{.*\.internal\.running\{display:block;position:fixed", css)      # a fixed bar repeats on every printed page
    assert "break-before:page" not in css and "@page{" in css
    assert page.count("INTERNAL") >= 2 and "Never send this to the client" in page


def test_the_markdown_is_marked_internal(biz, tmp_path):
    assert "INTERNAL: not for the client" in dossier(biz, tmp_path).md_path.read_text().splitlines()[2]


def test_making_a_dossier_writes_nothing_to_the_database(biz, tmp_path):
    def counts():
        c = get_connection()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT (SELECT count(*) FROM client_journal WHERE business_id = %s), (SELECT count(*) FROM evidence WHERE business_id = %s),"
                            " (SELECT count(*) FROM diagnoses WHERE business_id = %s)", (biz["id"],) * 3)
                return cur.fetchone()
        finally:
            c.close()
    before = counts()
    dossier(biz, tmp_path)
    assert counts() == before


# ---- the command -----------------------------------------------------------------------------------------------------------------------------------

def test_the_command_writes_the_dossier_and_says_it_is_internal(biz):
    result = CliRunner().invoke(cli.app, ["dossier", "--business", biz["domain"]])
    assert result.exit_code == 0, result.output
    assert "INTERNAL" in result.output and "Created the notes file" in result.output and "_dossier_v1.md" in result.output
    again = CliRunner().invoke(cli.app, ["dossier", "--business", biz["domain"]])
    assert "Created the notes file" not in again.output and "_dossier_v2.md" in again.output and "What happens next: Next:" in again.output


def test_the_command_reports_a_bad_questions_file(biz, tmp_path):
    result = CliRunner().invoke(cli.app, ["dossier", "--business", biz["domain"], "--questions", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 1 and "not found" in result.output
