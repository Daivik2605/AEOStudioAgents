"""Tests for `aeo client-report` (PLAN.md step 22). Real Postgres (aeo_test); the model is always a fake. Nothing is sent."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aeo import cli
from audit import cost_gate
from audit.cost_gate import CostGateAborted
from audit.report_data import build_report, select_run
from client_report import summaries
from client_report.data import ClientReportError
from client_report.render import render_html
from client_report.run import make_client_report, rebuild_from_json
from client_report.strings import STRINGS, t
from client_report.summaries import allowed_numbers, check_summary, planned_sections
from compare.run import build_comparison
from conftest import FakeAnthropicClient, text_response
from core import evidence as ev
from core.db import get_connection
from recommend.run import make_recommendations
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_audit_report import CLIENT, claim, link, nb
from tests.test_compare import (CLIENT_REC, NOW, RIVAL, add_diagnosis, add_run, finding, journal, make_qs)
from tests.test_recommend import fake_site

TODAY = date(2026, 10, 25)
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082")
PLAIN = "The check and the measurements are shown in this section."          # no numbers, no promises


def make(biz, tmp_path, stage, **kw):
    kw.setdefault("no_ai", True)
    return make_client_report(biz, stage, clients_root=tmp_path / "clients", today=TODAY, **kw)


def keys(res):
    return [s["key"] for s in res.data["sections"]]


def section(res, key):
    return next(s for s in res.data["sections"] if s["key"] == key)


def html_of(res):
    return res.html_path.read_text(encoding="utf-8")


def count_runs(biz):
    c = get_connection()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT count(*) FROM runs WHERE business_id = %s AND agent = 'client_report'", (biz["id"],))
            return cur.fetchone()[0]
    finally:
        c.close()


def add_visible(biz, tmp_path, name, content, *, internal=False, evidence_type="ai_answer_screenshot", note="note", checkpoint="before"):
    src = tmp_path / "incoming" / name
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(content)
    conn = get_connection()
    try:
        return ev.add_evidence(conn, business=biz, checkpoint=checkpoint, file=src, note=note, evidence_type=evidence_type,
                               source="us", internal=internal, operator="Sam", clients_root=tmp_path / "clients", today=TODAY)
    finally:
        conn.close()


def snapshot_answer(q, e, r):
    d = {"named": CLIENT_REC if q % 2 == 0 else RIVAL, "sources": [link("news.example")]}
    if q == 0:
        d["claims"] = [claim("Only serves Ontario", "Areas we serve", "incorrect"), claim("Phone is 514-555-0100", "Phone", "correct")]
    return d


@pytest.fixture
def starting(biz, tmp_path):
    """A client at the start: two diagnoses, a snapshot audit with a wrong fact, recommendations, and evidence."""
    confirm_facts(biz, Phone="514-555-0100", Areas_we_serve="Montreal")
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium"), finding("LLMS_TXT_MISSING", "low"),
                                  finding("CRAWLER_BLOCKED_OAI_SEARCHBOT", "high")], when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [finding("LLMS_TXT_MISSING", "low"), finding("RENDER_PHONE_MISSING", "medium")], when=NOW - timedelta(days=3))
    qs = make_qs(10, targets=(0, 1, 2))
    biz["qs"] = qs
    biz["run_id"] = add_run(biz, qs, snapshot_answer, checkpoint="before")
    make_recommendations(biz, fetcher=fake_site(), no_audit=False, clients_root=tmp_path / "clients", today=TODAY)
    biz["visible_png"] = add_visible(biz, tmp_path, "visible_shot.png", PNG, note="ChatGPT answer on 14 October")
    biz["internal_png"] = add_visible(biz, tmp_path, "secret_internal.png", PNG + b"x", internal=True, note="INTERNAL ONLY NOTE")
    biz["pdf"] = add_visible(biz, tmp_path, "cdn_report.pdf", b"%PDF-1.4 fake", evidence_type="cdn_report",
                             note="Cloudflare report, see https://dash.example/report")
    return biz


@pytest.fixture
def ba_pair(biz):
    """The real before/after design: 32 questions x 2 engines x 3 runs, twice. Ten targets; the rest are the control."""
    qs = make_qs(32, targets=tuple(range(10)))
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before", repeats=3)
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q < 10 else RIVAL), checkpoint="after_aeo", repeats=3)
    biz["qs"] = qs
    return biz


@pytest.fixture
def small_pair(biz):
    qs = make_qs(10, targets=(0, 1, 2, 3, 4))
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q % 3 else RIVAL), checkpoint="after_aeo")
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium"), finding("LLMS_TXT_MISSING", "low")], when=NOW - timedelta(days=10))
    add_diagnosis(biz, "after_aeo", [finding("LLMS_TXT_MISSING", "low")], when=NOW + timedelta(days=10))
    biz["qs"] = qs
    return biz


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cost_gate, "stdin_is_tty", lambda: True)


@pytest.fixture
def no_tty(monkeypatch):
    monkeypatch.setattr(cost_gate, "stdin_is_tty", lambda: False)


# ---- each stage's sections -----------------------------------------------------------------------------------------------------

def test_starting_point_sections(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    assert keys(res) == ["health", "ai_today", "questions", "recommend", "evidence", "promise"]
    page = html_of(res)
    for h in ("How healthy your website is for AI tools", "Where AI stands today", "The questions we asked", "What we recommend", "Evidence", "What we can and cannot do"):
        assert f"<h2>{h}</h2>" in page
    health = section(res, "health")["data"]
    assert health["comparison"]["labels"] == ["before", "after_site"] and health["comparison"]["headline"] == "2 of 3 findings closed"
    assert "2 of 3 findings closed" in page
    assert section(res, "ai_today")["data"]["available"] and section(res, "recommend")["data"]["available"]


def test_starting_point_includes_the_sentence_we_use_exactly(starting, tmp_path):
    page = html_of(make(starting, tmp_path, "starting_point"))
    plan_text = Path(__file__).parent.parent.joinpath("PLAN.md").read_text()
    sentence = re.search(r"### The sentence we use\s+> \*\"(.+?)\"\*", plan_text, re.S).group(1)
    from html import unescape
    assert " ".join(sentence.split()) in " ".join(unescape(re.sub(r"<[^>]+>", " ", page)).split())


def test_starting_point_with_nothing_yet_says_so_instead_of_failing(biz, tmp_path):
    res = make(biz, tmp_path, "starting_point")
    page = html_of(res)
    assert "No website check has been run yet." in page and "No finished measurement of what AI says exists yet." in page
    assert "No recommendations have been prepared yet." in page and "No evidence has been shared with you yet." in page


def test_recommendation_titles_come_from_the_recommend_table(starting, tmp_path):
    from html import escape
    res = make(starting, tmp_path, "starting_point")
    rec = section(res, "recommend")["data"]
    page = html_of(res)
    assert rec["available"] and rec["fixes"]
    for fix in rec["fixes"]:
        assert escape(fix["title"]) in page
    assert {f["section"] for f in rec["fixes"]} >= {"wrong_fact", "finding", "hygiene"}
    assert "Correct what AI says wrongly" in page and "Small extras (low effort, unproven)" in page
    assert "AI says: " in page                                  # the wrong-fact fix, in plain words
    # same fixes as the table `aeo recommend` filled, in the same ids
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT priority_fixes, fact_fixes FROM recommendations WHERE business_id = %s", (starting["id"],))
            priority, facts = cur.fetchone()
    finally:
        conn.close()
    assert {f["id"] for f in rec["fixes"]} == {f["id"] for f in priority + facts}


def test_a_fix_that_cannot_be_done_on_the_platform_is_flagged_in_plain_words(biz, tmp_path):
    set_up = add_diagnosis(biz, "before", [finding("ROBOTS_DISALLOWS_OAI_SEARCHBOT", "high")], platform="squarespace", plan="core")
    confirm_facts(biz, Phone="514-555-0100")
    make_recommendations(biz, fetcher=fake_site(), no_audit=True, clients_root=tmp_path / "clients", today=TODAY)
    page = html_of(make(biz, tmp_path, "starting_point"))
    assert "cannot be done on your current website platform" in page and "Let AI search tools reach your site" in page
    assert "Not prioritised" not in page and "were not prioritised by a measurement" in page


def test_work_done_sections(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium")], when=NOW - timedelta(days=5))
    journal(biz, "work", "Added a heading to the home page", NOW - timedelta(days=2), finding="RENDER_H1_MISSING", actor="Priya")
    journal(biz, "work", "Reviewed the sitemap", NOW - timedelta(days=1))
    add_visible(biz, tmp_path, "proof.png", PNG, note="Heading now visible")
    res = make(biz, tmp_path, "work_done")
    assert keys(res) == ["work", "evidence"]
    items = section(res, "work")["data"]["items"]
    assert [w["text"] for w in items] == ["Added a heading to the home page", "Reviewed the sitemap"]
    assert items[0]["finding_code"] == "RENDER_H1_MISSING" and items[0]["finding"] == "RENDER_H1_MISSING what"
    page = html_of(res)
    assert "Added a heading to the home page" in page and "<code>RENDER_H1_MISSING</code>" in page and "General work" in page
    assert page.index("Added a heading") < page.index("Reviewed the sitemap")
    assert "Priya" in page and "data:image/png;base64," in page


def test_results_sections(small_pair, tmp_path):
    journal(small_pair, "event", "The client changed their own opening hours", NOW, actor="Jean")
    journal(small_pair, "work", "Rewrote the services page", NOW, finding="RENDER_H1_MISSING")
    res = make(small_pair, tmp_path, "results")
    assert keys(res) == ["r_health", "r_ai", "r_targets", "r_questions", "r_voice", "r_facts", "r_sources", "r_between"]
    assert res.data["labels"] == {"a": "before", "b": "after_aeo"}
    page = html_of(res)
    assert "1 of 2 findings closed" in page and "Rewrote the services page" in page
    assert "<span class='tag event'>Outside our control</span>" in page and "outside our control happened in this period" in page


def test_results_with_other_labels_and_missing_data(biz, tmp_path):
    add_diagnosis(biz, "before", [])
    with pytest.raises(ClientReportError, match="nothing to compare"):
        make(biz, tmp_path, "results")
    add_diagnosis(biz, "after_site", [finding("RENDER_H1_MISSING", "low")])
    res = make(biz, tmp_path, "results", a="before", b="after_site")
    assert res.data["labels"] == {"a": "before", "b": "after_site"}
    assert "No finished measurement of what AI says exists yet." in html_of(res)          # the audit half is simply absent


def test_check_compares_the_latest_check_with_the_one_before_it(biz, tmp_path):
    qs = make_qs(10, targets=(0, 1, 2))
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="after_aeo")
    first = add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q < 5 else RIVAL), checkpoint="check")
    last = add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q < 7 else RIVAL), checkpoint="check")
    add_diagnosis(biz, "check", [], when=NOW - timedelta(days=1))
    res = make(biz, tmp_path, "check")
    ai = section(res, "r_ai")["data"]
    assert ai["a"]["run_id"] == first and ai["b"]["run_id"] == last                  # the two checks, not the first measurement
    assert res.data["labels"] == {"a": "check", "b": "check"}
    assert "latest check" in html_of(res)


def test_check_uses_the_measurement_before_when_there_is_only_one_check(biz, tmp_path):
    qs = make_qs(10)
    before = add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="after_aeo")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC), checkpoint="check")
    res = make(biz, tmp_path, "check")
    assert section(res, "r_ai")["data"]["a"]["run_id"] == before and res.data["labels"]["a"] == "after_aeo"


def test_check_without_a_check_is_refused(biz, tmp_path):
    add_diagnosis(biz, "before", [])
    with pytest.raises(ClientReportError, match="no 'check'"):
        make(biz, tmp_path, "check")


def test_bad_arguments_are_refused(biz, tmp_path):
    with pytest.raises(ClientReportError, match="--stage must be"):
        make(biz, tmp_path, "yearly")
    with pytest.raises(ClientReportError, match="together"):
        make(biz, tmp_path, "results", a="before")
    with pytest.raises(ClientReportError, match="not 'work_done'"):
        make(biz, tmp_path, "work_done", a="before", b="after_aeo")
    with pytest.raises(ClientReportError, match="not a checkpoint"):
        make(biz, tmp_path, "results", a="old_site", b="after_aeo")


# ---- evidence ---------------------------------------------------------------------------------------------------------------------

def test_only_client_visible_evidence_is_included(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    page, raw = html_of(res), res.json_path.read_text()
    for blob in (page, raw):
        assert "secret_internal" not in blob and "INTERNAL ONLY NOTE" not in blob
    files = [e["file"] for e in section(res, "evidence")["data"]["items"]]
    assert any(f.endswith("visible_shot.png") for f in files) and any(f.endswith("cdn_report.pdf") for f in files)
    assert not any("secret_internal" in f for f in files)
    other = make(starting, tmp_path, "work_done")
    assert "secret_internal" not in html_of(other)


def test_screenshots_are_embedded_and_pdfs_are_listed_with_date_source_and_note(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    page = html_of(res)
    assert page.count("data:image/png;base64,") == 1 and "ChatGPT answer on 14 October" in page
    items = {e["file"].split("_", 1)[-1]: e for e in section(res, "evidence")["data"]["items"]}
    pdf = next(e for e in section(res, "evidence")["data"]["items"] if e["file"].endswith(".pdf"))
    assert pdf["embedded"] is False and pdf["data_uri"] is None and pdf["source"] == "us"
    assert pdf["captured_on"] == datetime.now(timezone.utc).date().isoformat() and "Cloudflare report" in pdf["note"]
    assert "cdn_report.pdf" in page and "JVBER" not in page and "%PDF" not in page          # listed, never embedded
    assert "Not shown here (listed only)" in page and "AEO Studio</td>" in page


def test_a_missing_screenshot_file_is_listed_not_crashed_on(starting, tmp_path):
    for f in (tmp_path / "clients" / starting["domain"] / "evidence").glob("*visible_shot.png"):
        f.unlink()
    res = make(starting, tmp_path, "starting_point")
    assert "The file could not be shown here" in html_of(res) and "data:image/png" not in html_of(res)


# ---- honesty rules ------------------------------------------------------------------------------------------------------------------

def test_every_score_shows_its_range(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    page = html_of(res)
    h = section(res, "ai_today")["data"]["headline"]
    assert "95% range" in page
    with_range = 0
    for m in h["measures"].values():
        if m["low"] is not None:
            with_range += 1
            assert f"{m['low']:.1f}–{m['high']:.1f}" in page
    assert with_range >= 3
    assert "no range: too few questions" not in page


def test_a_score_with_no_range_says_why_instead_of_showing_a_bare_number(biz, tmp_path):
    qs = make_qs(1)
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC), checkpoint="before")
    page = html_of(make(biz, tmp_path, "starting_point"))
    assert "no range: too few questions" in page


def test_changes_carry_the_labels_aeo_compare_gives(small_pair, tmp_path):
    res = make(small_pair, tmp_path, "results")
    page = html_of(res)
    cmp = build_comparison(small_pair, "before", "after_aeo", today=TODAY)
    labels = {m["change"]["label"] for m in cmp["audit"]["headline"]["measures"].values()}
    assert labels <= {"real change", "within the noise"} and "real change" in labels
    for label in labels:
        assert label in page
    assert "real change" in page
    # each measure's change cell matches compare's own number
    for key, m in cmp["audit"]["headline"]["measures"].items():
        assert f"{m['change']['value']:+.1f} points" in page
    assert section(res, "r_ai")["data"]["headline"] == cmp["audit"]["headline"]          # the very same data, not a recomputation


def test_the_detection_floor_is_printed_with_the_changes(small_pair, tmp_path):
    assert "roughly 20 points or more" in html_of(make(small_pair, tmp_path, "results"))


def test_the_numbers_are_exactly_the_audit_reports(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    conn = get_connection()
    try:
        run, _ = select_run(conn, starting["id"])
        own = build_report(conn, starting, run, partial=False)
    finally:
        conn.close()
    got = section(res, "ai_today")["data"]
    assert got["headline"] == own["headline"] and got["share_of_voice"] == own["share_of_voice"] and got["wrong_facts"] == own["wrong_facts"]


def test_the_full_question_set_is_shown_with_targets_marked(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    page = html_of(res)
    texts = [q.text for q in starting["qs"].items]
    assert all(txt in page for txt in texts) and len(texts) == 10
    assert page.count("Target: we work on this") == 3 and page.count("Not a target") == 7           # a snapshot: no "control" claim
    assert "Control: we leave this alone" not in page


def test_the_control_group_is_shown_in_the_before_after_design(ba_pair, tmp_path):
    res = make(ba_pair, tmp_path, "results")
    page = html_of(res)
    assert all(q.text in page for q in ba_pair["qs"].items)
    assert page.count("Target: we work on this") == 10 and page.count("Control: we leave this alone") == 22
    assert "Questions we worked on, compared with questions we left alone" in page
    assert "Change on questions we left alone" in page and "10 question(s) were ones we worked on" in page and "22 were ones we deliberately left alone" in page
    tv = section(res, "r_targets")["data"]
    assert tv["available"] and tv["measures"]["visibility"]["difference"]["value"] is not None


def test_snapshots_are_labelled_as_snapshots(starting, tmp_path):
    page = html_of(make(starting, tmp_path, "starting_point"))
    assert "This is a snapshot" in page and "overall figures only" in page
    assert "before/after measurement" not in page.lower().replace("<", " ")


def test_the_before_after_design_is_labelled_and_snapshot_is_not(ba_pair, tmp_path):
    page = html_of(make(ba_pair, tmp_path, "results"))
    assert "This is a before/after measurement: 32 fixed questions, 2 AI tools, 3 runs each" in page
    assert "This is a snapshot" not in page


def test_targets_are_never_shown_alone_in_the_results_questions(small_pair, tmp_path):
    q = section(make(small_pair, tmp_path, "results"), "r_questions")["data"]
    assert len(q["items"]) == 10 and sum(1 for i in q["items"] if i["is_target"]) == 5


def test_no_sentence_promises_an_outcome(starting, small_pair, tmp_path):
    for biz_, stage in ((starting, "starting_point"), (small_pair, "results")):
        text = " ".join(re.sub(r"<[^>]+>", " ", html_of(make(biz_, tmp_path, stage))).split()).lower()
        assert not re.search(r"\bwill (increase|improve|boost|rank|appear|be named|be recommended|double|grow|lead to|result in|drive)\b", text)
        assert not re.search(r"\bwe guarantee\b|\bguaranteed\b|\bis certain\b", text)
        assert "nobody can guarantee" in text


def test_every_fixed_sentence_is_in_the_strings_dictionary(starting, tmp_path, monkeypatch):
    """A second language is a translated copy of the dictionary: nothing in the HTML is hard-wired to English."""
    monkeypatch.setitem(STRINGS, "xx", {k: "«" + v for k, v in STRINGS["en"].items()})
    res = make(starting, tmp_path, "starting_point")
    data = json.loads(res.json_path.read_text())
    data["tool"]["language"] = "xx"
    page = render_html(data)
    assert "<h2>«How healthy your website is for AI tools</h2>" in page and "<h2>«What we recommend</h2>" in page
    assert page.count("«") > 30
    assert t("stage.results", "xx") == "«Results"


def test_the_header_names_the_client_stage_date_and_version(starting, tmp_path):
    page = html_of(make(starting, tmp_path, "starting_point"))
    for fragment in ("AEO Studio", starting["name"], "Starting point", TODAY.isoformat(), "v1"):
        assert fragment in page
    second = html_of(make(starting, tmp_path, "starting_point"))
    assert "<dd>v2</dd>" in second


# ---- format ----------------------------------------------------------------------------------------------------------------------------

def test_no_external_urls_anywhere(starting, small_pair, tmp_path):
    for biz_, stage in ((starting, "starting_point"), (small_pair, "results")):
        page = html_of(make(biz_, tmp_path, stage))
        for attr, value in re.findall(r"""\b(src|href|action|data)\s*=\s*["']([^"']*)["']""", page):
            assert not re.match(r"(?i)\s*(https?:)?//", value), f"{attr}={value[:60]}"
        for banned in ("<script", "<link", "<iframe", "@import", "url(http", "url(//", "xmlns="):
            assert banned not in page, banned
        assert not re.search(r"""(src|href)\s*=\s*["'](?!data:)""", page)                 # the only src values are embedded data


def test_a_plain_link_in_an_evidence_note_is_text_not_a_link(starting, tmp_path):
    page = html_of(make(starting, tmp_path, "starting_point"))
    assert "https://dash.example/report" in page and 'href="https://dash.example' not in page


def test_it_is_one_self_contained_file_with_a_print_stylesheet(starting, tmp_path):
    page = html_of(make(starting, tmp_path, "starting_point"))
    assert page.startswith("<!doctype html>") and page.count("<style>") == 1
    assert "@media print" in page and "break-inside:avoid" in page and "@page" in page and "break-before:page" in page


def test_charts_are_inline_svg_and_readable_in_greyscale(starting, small_pair, tmp_path):
    now_page = html_of(make(starting, tmp_path, "starting_point"))
    results_page = html_of(make(small_pair, tmp_path, "results"))
    assert now_page.count("<svg") >= 2 and "Bar chart: how many answers named each business" in now_page      # share of voice + figures
    assert results_page.count("<svg") >= 1 and "<pattern" in results_page and "<line" in results_page         # hatched bars with whiskers
    for page in (now_page, results_page):
        for colour in re.findall(r"#[0-9a-fA-F]{3,6}\b", page):
            c = colour[1:]
            c = "".join(ch * 2 for ch in c) if len(c) == 3 else c
            assert c[0:2] == c[2:4] == c[4:6], f"{colour} is not a grey"
        assert "<img" not in page or "data:image" in page


def test_the_share_of_voice_chart_darkens_the_client(starting, tmp_path):
    svg = re.search(r"<svg[^>]*aria-label=\"Bar chart: how many answers named each business\".*?</svg>", html_of(make(starting, tmp_path, "starting_point")), re.S).group(0)
    assert svg.count('fill="#1b1b1b"') >= 1 and 'fill="#cfcfcf"' in svg
    assert "Rival Co" in svg and CLIENT in svg


# ---- the AI part ----------------------------------------------------------------------------------------------------------------------------

def test_no_ai_makes_no_calls_and_no_runs(starting, tmp_path):
    client = FakeAnthropicClient([])                       # any call would raise: nothing is queued
    before = count_runs(starting)
    res = make(starting, tmp_path, "starting_point", no_ai=True, client=client)
    assert client.messages.calls == [] and count_runs(starting) == before
    assert res.data["ai"] == {"used": False, "model": None, "calls": 0, "rejected": []}
    assert "In short" not in html_of(res) and all(s["summary"] is None for s in res.data["sections"])


def ai_run(biz, tmp_path, texts, *, answer="yes", stage="starting_point"):
    said = []
    client = FakeAnthropicClient([text_response(x) for x in texts])
    res = make_client_report(biz, stage, no_ai=False, ask=lambda prompt: answer, say=said.append, client=client,
                             clients_root=tmp_path / "clients", today=TODAY)
    return res, client, said


def test_summaries_are_added_behind_the_gate_and_logged_in_runs(starting, tmp_path, tty):
    before = count_runs(starting)
    res, client, said = ai_run(starting, tmp_path, [PLAIN] * 5)
    planned = ["health", "ai_today", "recommend"]
    assert [s["key"] for s in res.data["sections"] if s["summary"]] == planned
    assert len(client.messages.calls) == 3 and count_runs(starting) == before + 3
    assert any("Client report summaries: 3 calls" in line for line in said)                  # the cost card was shown
    assert res.data["ai"]["used"] and res.data["ai"]["model"] == "claude-sonnet-5" and res.data["ai"]["calls"] == 3
    page = html_of(res)
    assert page.count("<h3>In short</h3>") == 3 and "checked by a person before sending" in page


def test_the_cost_gate_needs_a_typed_yes(starting, tmp_path, tty):
    before = count_runs(starting)
    with pytest.raises(CostGateAborted):
        ai_run(starting, tmp_path, [PLAIN] * 5, answer="no")
    assert count_runs(starting) == before
    assert not list((tmp_path / "clients" / starting["domain"] / "generated").glob("*client_report*"))     # nothing written


def test_with_no_terminal_the_gate_refuses(starting, tmp_path, no_tty):
    said = []
    client = FakeAnthropicClient([text_response(PLAIN)] * 5)
    with pytest.raises(CostGateAborted):
        make_client_report(starting, "starting_point", no_ai=False, ask=lambda p: "yes", say=said.append, client=client,
                           clients_root=tmp_path / "clients", today=TODAY)
    assert client.messages.calls == [] and any("A person must be at the keyboard" in s for s in said)


def test_a_summary_with_an_unknown_number_is_rejected_and_the_section_prints_without_it(starting, tmp_path, tty):
    res, client, said = ai_run(starting, tmp_path, [PLAIN, "AI named the business in 73.4% of answers.", PLAIN])
    assert section(res, "health")["summary"] == PLAIN and section(res, "recommend")["summary"] == PLAIN
    assert section(res, "ai_today")["summary"] is None
    rejected = res.data["ai"]["rejected"]
    assert len(rejected) == 1 and rejected[0]["section"] == "ai_today" and "73.4" in rejected[0]["reason"]
    assert "73.4" not in html_of(res)                                       # the rejected text never reaches the report
    saved = json.loads(res.json_path.read_text())
    assert all("73.4" not in json.dumps(s_) for s_ in saved["sections"])    # nor its section; only the reason names it
    assert any("rejected" in s and "ai_today" in s for s in said)
    assert len(client.messages.calls) == 3                                                    # never retried


def test_a_rejected_summary_is_not_retried_with_looser_rules(starting, tmp_path, tty):
    res, client, _ = ai_run(starting, tmp_path, ["The score is 99.9%.", "The score is 99.9%.", "The score is 99.9%."])
    assert all(s["summary"] is None for s in res.data["sections"]) and len(res.data["ai"]["rejected"]) == 3
    assert len(client.messages.calls) == 3


def test_a_failed_model_call_does_not_lose_the_report(starting, tmp_path, tty):
    client = FakeAnthropicClient([text_response(PLAIN)])              # only one answer queued: the 2nd call raises
    said = []
    res = make_client_report(starting, "starting_point", no_ai=False, ask=lambda p: "yes", say=said.append, client=client,
                             clients_root=tmp_path / "clients", today=TODAY)
    assert section(res, "health")["summary"] == PLAIN and section(res, "ai_today")["summary"] is None
    assert any("model call failed" in r["reason"] for r in res.data["ai"]["rejected"]) and res.html_path.exists()


def test_a_summary_that_promises_an_outcome_is_rejected(starting, tmp_path, tty):
    res, _, _ = ai_run(starting, tmp_path, ["This work will increase how often AI names you.", PLAIN, PLAIN])
    assert section(res, "health")["summary"] is None and "promises or predicts" in res.data["ai"]["rejected"][0]["reason"]


def test_the_number_check_itself():
    allowed = {"62.5", "10", "2026", "0", "100"}
    assert check_summary("The figure is 62.5% (10 to 100).", allowed) is None
    assert check_summary("The figure is 62.50%.", allowed) is None                       # same number, written differently
    assert "63" in check_summary("About 63% of answers.", allowed)                       # no rounding of someone else's number
    assert "two" in check_summary("It happened in two answers.", allowed)                 # spelled-out numbers are numbers too
    assert check_summary("It happened in two answers.", allowed | {"2"}) is None
    assert check_summary("", allowed) == "the summary was empty"
    assert check_summary("We guarantee AI will name you.", allowed).startswith("it promises")
    assert check_summary("It is no one's fault.", allowed) is None                       # the word 'one' is not treated as a number


def test_allowed_numbers_come_from_the_report_data_not_from_summaries(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    allowed = allowed_numbers(res.data)
    vis = section(res, "ai_today")["data"]["headline"]["measures"]["visibility"]
    assert f"{vis['value']:.1f}".rstrip("0").rstrip(".") in allowed or summaries.norm(f"{vis['value']:.1f}") in allowed
    assert "73.4" not in allowed
    res.data["sections"][0]["summary"] = "There were 4444 problems."
    assert "4444" not in allowed_numbers(res.data)


def test_planned_sections_are_only_those_with_something_to_say(biz, tmp_path):
    res = make(biz, tmp_path, "starting_point")
    assert planned_sections(res.data) == []                       # an empty client: nothing to summarise, no cost card, no calls
    said = []
    out = summaries.add_summaries(res.data, ask=lambda p: "yes", say=said.append, client=FakeAnthropicClient([]))
    assert out["ai"]["used"] is False and said == []


# ---- rebuild and files -------------------------------------------------------------------------------------------------------------------------

def test_from_json_rebuilds_byte_identical_html(starting, tmp_path, tty):
    res, _, _ = ai_run(starting, tmp_path, [PLAIN] * 3)             # with AI summaries and an embedded screenshot
    original = res.html_path.read_bytes()
    assert b"data:image/png" in original and b"In short" in original
    same = rebuild_from_json(starting, res.json_path, clients_root=tmp_path / "clients")
    assert same.identical_to_original is True and same.html_path == res.html_path
    res.html_path.unlink()
    for f in (tmp_path / "clients" / starting["domain"] / "evidence").glob("*"):
        f.unlink()                                                  # even with the evidence files gone, the data holds the screenshot
    again = rebuild_from_json(starting, res.json_path, clients_root=tmp_path / "clients")
    assert again.html_path.name == res.html_path.name and again.html_path.read_bytes() == original and again.identical_to_original is None


def test_from_json_never_overwrites_a_changed_original(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    res.html_path.write_text("hand edited")
    made = rebuild_from_json(starting, res.json_path, clients_root=tmp_path / "clients")
    assert made.identical_to_original is False and made.html_path != res.html_path and res.html_path.read_text() == "hand edited"
    assert made.html_path.read_text() == render_html(json.loads(res.json_path.read_text()))


def test_from_json_refuses_other_files_and_other_businesses(starting, tmp_path):
    from core.businesses import add_business
    import uuid
    stranger = add_business(f"https://s-{uuid.uuid4().hex[:8]}.example", name=f"Stranger {uuid.uuid4().hex[:8]}")
    other = make(stranger, tmp_path, "work_done")
    with pytest.raises(ClientReportError, match="different business"):
        rebuild_from_json(starting, other.json_path, clients_root=tmp_path / "clients")
    junk = tmp_path / "junk.json"
    junk.write_text('{"a": 1}')
    with pytest.raises(ClientReportError, match="not a client-report data file"):
        rebuild_from_json(starting, junk)
    with pytest.raises(ClientReportError, match="cannot read"):
        rebuild_from_json(starting, tmp_path / "missing.json")


def test_the_json_holds_every_piece_of_data_and_matches_the_html(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    assert json.loads(res.json_path.read_text()) == res.data
    assert render_html(res.data) == html_of(res)
    assert render_html(res.data) == render_html(json.loads(json.dumps(res.data)))           # deterministic


def test_files_are_named_by_date_stage_and_version(starting, tmp_path):
    first, second = make(starting, tmp_path, "starting_point"), make(starting, tmp_path, "starting_point")
    assert first.html_path.name == "2026-10-25_client_report_starting_point_v1.html"
    assert first.json_path.name == "2026-10-25_client_report_starting_point_v1.json"
    assert second.html_path.name.endswith("_v2.html") and first.html_path.exists()
    assert make(starting, tmp_path, "work_done").html_path.name.endswith("work_done_v1.html")        # versions count per stage
    assert first.html_path.parent.name == "generated"


def test_it_writes_nothing_to_the_journal_and_sends_nothing(starting, tmp_path):
    def journal_count():
        c = get_connection()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT count(*) FROM client_journal WHERE business_id = %s", (starting["id"],))
                return cur.fetchone()[0]
        finally:
            c.close()
    before = journal_count()
    make(starting, tmp_path, "starting_point")
    assert journal_count() == before


# ---- the command ----------------------------------------------------------------------------------------------------------------------------------

def test_the_command_writes_the_report_without_ai(starting):
    result = CliRunner().invoke(cli.app, ["client-report", "--business", starting["domain"], "--stage", "starting_point", "--no-ai"])
    assert result.exit_code == 0, result.output
    assert "none (no AI used)" in result.output and "Nothing was sent" in result.output and "_client_report_starting_point_v1.html" in result.output


def test_the_command_needs_a_stage(starting):
    result = CliRunner().invoke(cli.app, ["client-report", "--business", starting["domain"], "--no-ai"])
    assert result.exit_code == 1 and "--stage is required" in result.output


def test_the_command_aborts_cleanly_when_the_gate_is_not_passed(starting, no_tty):
    result = CliRunner().invoke(cli.app, ["client-report", "--business", starting["domain"], "--stage", "starting_point"])
    assert result.exit_code == 1 and "Nothing was written" in result.output and "A person must be at the keyboard" in result.output


def test_the_command_rebuilds_from_json(starting):
    runner = CliRunner()
    made = runner.invoke(cli.app, ["client-report", "--business", starting["domain"], "--stage", "work_done", "--no-ai"])
    assert made.exit_code == 0, made.output
    json_file = re.search(r"Data:\s+(\S+\.json)", made.output).group(1)
    again = runner.invoke(cli.app, ["client-report", "--business", starting["domain"], "--from-json", json_file])
    assert again.exit_code == 0 and "identical to the original" in again.output
    clash = runner.invoke(cli.app, ["client-report", "--business", starting["domain"], "--from-json", json_file, "--stage", "work_done"])
    assert clash.exit_code == 1 and "do not combine" in clash.output
