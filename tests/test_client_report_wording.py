"""Tests for step 22b: the client report reads as if it was written for the client. Real Postgres (aeo_test), fake model."""

from __future__ import annotations

import re
from datetime import timedelta
from html import escape, unescape

import pytest

from client_report import render
from client_report.render import render_html
from client_report.strings import STRINGS, t
from client_report.words import Words, schema_name
from core import businesses
from core.db import get_connection
from diagnose import platform_data as pd
from diagnose.codes import REGISTRY, make_finding
from recommend.run import make_recommendations
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_client_report import (PNG, TODAY, add_visible, ba_pair, html_of, keys,  # noqa: F401
                                      make, section, small_pair, starting, tty)
from tests.test_compare import NOW, add_diagnosis, finding, journal
from tests.test_recommend import fake_site

# What a client must never meet: markup names and file names (step 22b). Compared in lower case.
MARKUP_WORDS = ("json-ld", "jsonld", "@id", "@type", "h1", "sameas", "openinghours", "areaserved", "robots.txt", "llms.txt",
                "schema.json", "rfc ", "user agent", "http", "<", ">", "url", "cdn", "api", "html", "{", "}")
LABELS = ("before", "after_site", "after_aeo", "check")


def visible_text(page: str) -> str:
    """What a person sees in the browser: no style block, no tags, no embedded images."""
    page = re.sub(r"<style>.*?</style>", " ", page, flags=re.S)
    page = re.sub(r"<svg.*?</svg>", lambda m: " ".join(re.findall(r">([^<]+)<", m.group(0))), page, flags=re.S)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", page)).split())


def words_for(biz_, *, engagement=None, labels=None, dates=None, lang="en"):
    return Words({"tool": {"language": lang}, "business": {"name": biz_["name"], "domain": biz_["domain"]},
                  "engagement": engagement, "labels": labels or {"a": "", "b": ""}, "label_dates": dates or {}})


def set_engagement(biz_, kind):
    conn = get_connection()
    try:
        businesses.set_engagement(conn, biz_["id"], engagement_type=kind, access_level=None, actor="Sam")
    finally:
        conn.close()


# ---- 1. client wording for every finding code ---------------------------------------------------------------------------------------

def test_every_finding_code_has_client_wording_and_a_client_fix_title():
    for lang, table in STRINGS.items():
        missing = [c for c in REGISTRY if f"finding.{c}" not in table or f"fix.{c}" not in table]
        assert not missing, f"{lang}: no client wording for {missing}"


def test_client_wording_has_no_markup_names_and_no_loose_placeholders():
    checked = 0
    for lang, table in STRINGS.items():
        for key, text in table.items():
            if not key.startswith(("finding.", "fix.", "platform.", "evtype.", "label.", "ai_next.", "promise.next")):
                continue
            checked += 1
            low = text.lower()
            assert not re.search(r"\bh1\b|json-ld|@id|sameas|openinghours|robots\.txt|llms\.txt|schema\.json|areaserved", low), key
            assert not re.search(r"\b[A-Z]{3,}_[A-Z_]{2,}\b", text), f"{key} shows a raw code"
            stripped = re.sub(r"\{(business|platform|name|quote|question|date)\}", "", text)
            assert "{" not in stripped and "}" not in stripped, key
    assert checked > 90


def test_a_fallback_exists_wherever_the_sentence_needs_a_value_from_the_finding():
    for table in STRINGS.values():
        for key, text in table.items():
            if key.startswith("finding.") and "{name}" in text:
                assert key + ".fallback" in table


def test_the_legal_name_example_uses_the_values_from_the_finding(biz):
    f = make_finding("SCHEMA_NAME_IS_LEGAL_ENTITY", what="internal wording", evidence='"name": "11297775 Canada Inc"',
                     fixable_on_platform=True, where_to_fix="x", source="y")
    biz["name"] = "O’land Stations"
    text = words_for(biz).finding(f)
    assert text == "Your website told AI tools your business is called ‘11297775 Canada Inc’ instead of ‘O’land Stations’."
    assert schema_name(f) == "11297775 Canada Inc"
    f["evidence"] = "something we cannot read"                     # no value in the details: say less, never invent a name
    assert "11297775" not in words_for(biz).finding(f) and "O’land Stations" in words_for(biz).finding(f)


def test_the_internal_wording_is_kept_for_our_own_reports(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    page = html_of(res)
    stored = section(res, "health")["data"]["latest"]["findings"]
    assert stored and all(f["what"].endswith(" what") for f in stored)                    # the internal text is still in the data
    assert not any(f["what"] in page for f in stored)                                    # and never on the client's page
    assert "Your page has no main heading" in page or "Your phone number does not appear" in page


# ---- 2. no internal labels --------------------------------------------------------------------------------------------------------------

def test_checkpoint_labels_in_the_clients_words(biz):
    w = words_for(biz, engagement="web_dev_aeo")
    assert w.label("before") == "your previous website" and w.label("after_site") == "your new website"
    assert w.label("after_aeo") == "after our AI-visibility work"
    assert w.label("check", "2026-10-21") == "quarterly check on 2026-10-21"
    for other in ("aeo_only", None, "prospect", "web_dev_only"):
        expected = "your previous website" if other == "web_dev_only" else "before we started"
        assert words_for(biz, engagement=other).label("before") == expected


def test_the_before_label_depends_on_the_engagement(small_pair, tmp_path):
    page = visible_text(html_of(make(small_pair, tmp_path, "results")))
    assert "before we started" in page and "your previous website" not in page
    set_engagement(small_pair, "web_dev_aeo")
    page = visible_text(html_of(make(small_pair, tmp_path, "results")))
    assert "your previous website" in page and "after our AI-visibility work" in page and "before we started" not in page


def test_a_check_is_named_by_its_date(biz, tmp_path):
    from tests.test_compare import RIVAL, CLIENT_REC, add_run, make_qs
    qs = make_qs(10)
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="after_aeo")
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC), checkpoint="check")
    add_diagnosis(biz, "after_aeo", [finding("RENDER_H1_MISSING", "low")], when=NOW - timedelta(days=30))
    add_diagnosis(biz, "check", [], when=NOW - timedelta(days=2))
    res = make(biz, tmp_path, "check")
    day = (NOW - timedelta(days=2)).date().isoformat()
    page = visible_text(html_of(res))
    assert f"quarterly check on {day}" in page and "after our AI-visibility work" in page


def test_no_raw_label_or_code_reaches_the_page(starting, small_pair, ba_pair, tmp_path):
    set_engagement(small_pair, "web_dev_aeo")
    pages = [html_of(make(starting, tmp_path, "starting_point")), html_of(make(starting, tmp_path, "work_done")),
             html_of(make(small_pair, tmp_path, "results")), html_of(make(ba_pair, tmp_path, "results"))]
    for page in pages:
        text = visible_text(page)
        assert not re.search(r"\b[a-z]{2,}_[a-z0-9_]+\b", text), re.search(r"\b[a-z]{2,}_[a-z0-9_]+\b", text).group(0)
        assert not re.search(r"\b[A-Z][A-Z0-9]{2,}_[A-Z0-9_]{2,}\b", text), "a finding code is showing"
        assert not re.search(r"\((?:before|after_site|after_aeo|check)\)|(?:before|after_site|after_aeo|check) →|→ (?:before|after_site|after_aeo|check)\b", text)
        for code in REGISTRY:
            assert code not in page
        for label in LABELS:
            assert f">{label}<" not in page and f"({label})" not in page


def test_platforms_are_shown_by_their_proper_names(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "low")], platform="nextjs", when=NOW - timedelta(days=9))
    page = visible_text(html_of(make(biz, tmp_path, "starting_point")))
    assert "built on Next.js." in page and "nextjs" not in page.lower().replace("next.js", "")
    wanted = {"wix": "Wix", "squarespace": "Squarespace", "wordpress": "WordPress", "wordpress_com": "WordPress.com",
              "google_sites": "Google Sites", "godaddy_builder": "GoDaddy Website Builder", "hubspot": "HubSpot"}
    for key, name in wanted.items():
        assert words_for(biz).platform(key) == name


def test_every_platform_the_diagnosis_knows_has_a_proper_name():
    keys_ = set(pd.PLATFORM_LABELS) | set(pd.DECLINE_TABLE) | set(pd.DELIVERY) | set(pd.PLAN_GATES) | set(pd.SERVER_RENDERING_PLATFORMS)
    for key in keys_:
        assert f"platform.{key}" in STRINGS["en"], key
    assert "_" not in " ".join(STRINGS["en"][f"platform.{k}"] for k in keys_)


def test_a_platform_change_is_shown_by_name_in_the_health_comparison(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "low")], platform="wordpress_com", when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [], platform="nextjs", when=NOW - timedelta(days=2))
    page = visible_text(html_of(make(biz, tmp_path, "starting_point")))
    assert "WordPress.com" in page and "Next.js" in page and "wordpress_com" not in page


def test_evidence_types_in_plain_words(starting, tmp_path):
    page = html_of(make(starting, tmp_path, "starting_point"))
    assert "Website traffic report (Cloudflare)" in page
    assert "cdn_report" not in page and "ai_answer_screenshot" not in page
    w = words_for(starting)
    assert w.evidence_type("cdn_report") == "Website traffic report (Cloudflare)"
    assert w.evidence_type("ai_answer_screenshot") == "Screenshot of an AI answer"
    assert w.evidence_type("meeting_notes") == "Meeting notes" and w.evidence_type(None) == "Document"


# ---- 3. closed by the new site ----------------------------------------------------------------------------------------------------------

def closed_text(res) -> str:
    return visible_text(html_of(res))


def test_a_closed_finding_after_a_platform_change_says_fixed_by_the_new_website(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium")], platform="wordpress", when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [], platform="nextjs", when=NOW - timedelta(days=2))
    page = closed_text(make(biz, tmp_path, "starting_point"))
    assert page.count("Fixed by the new website") == 1


def test_work_that_explains_a_closed_finding_comes_before_the_new_site(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium")], platform="wordpress", when=NOW - timedelta(days=9))
    journal(biz, "work", "Added a heading", NOW - timedelta(days=5), finding="RENDER_H1_MISSING")
    add_diagnosis(biz, "after_site", [], platform="nextjs", when=NOW - timedelta(days=2))
    page = closed_text(make(biz, tmp_path, "starting_point"))
    assert "Added a heading" in page and "Fixed by the new website" not in page


def test_a_closed_finding_nobody_explains_shows_nothing_and_never_says_no_work_was_logged(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium"), finding("LLMS_TXT_MISSING", "low")],
                  platform="wordpress", when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [finding("LLMS_TXT_MISSING", "low")], platform="wordpress", when=NOW - timedelta(days=2))
    page = closed_text(make(biz, tmp_path, "starting_point"))
    assert "Fixed by the new website" not in page and "No work was logged" not in page and "no work" not in page.lower()
    assert not any("No work was logged" in v for v in STRINGS["en"].values())


def test_an_unknown_platform_does_not_count_as_a_new_website(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium")], platform=None, when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [], platform="nextjs", when=NOW - timedelta(days=2))
    assert "Fixed by the new website" not in closed_text(make(biz, tmp_path, "starting_point"))


def test_the_new_website_line_also_works_in_the_results_report(small_pair, tmp_path):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE diagnoses SET platform = 'nextjs' WHERE business_id = %s AND checkpoint = 'after_aeo'", (small_pair["id"],))
    finally:
        conn.close()
    page = closed_text(make(small_pair, tmp_path, "results"))
    assert page.count("Fixed by the new website") == 1


# ---- 4. the headline ----------------------------------------------------------------------------------------------------------------------

NINE = ["RENDER_H1_MISSING", "RENDER_H1_MULTIPLE", "RENDER_PHONE_MISSING", "RENDER_ADDRESS_MISSING", "RENDER_BUSINESS_NAME_MISSING",
        "SCHEMA_NONE_FOUND", "LLMS_TXT_MISSING", "SCHEMA_ORG_MISSING_NAME", "SCHEMA_ENTITIES_UNLINKED"]


def test_the_headline_counts_the_new_finding(biz, tmp_path):
    add_diagnosis(biz, "before", [finding(c, "low") for c in NINE], when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [finding("LLMS_TXT_MISSING", "low"), finding("SCHEMA_OPENING_HOURS_EMPTY", "low")],
                  when=NOW - timedelta(days=2))
    page = closed_text(make(biz, tmp_path, "starting_point"))
    assert "8 of 9 closed · 1 new" in page and "findings closed" not in page


def test_the_headline_has_no_new_part_when_nothing_is_new(biz):
    w = words_for(biz)
    assert w.headline({"closed": [1] * 8, "new": [], "a_total": 9}) == "8 of 9 closed"
    assert w.headline({"closed": [1] * 8, "new": [1, 2], "a_total": 9}) == "8 of 9 closed · 2 new"
    assert w.headline({"closed": [], "new": [1], "a_total": 0}) == "Nothing to close from the earlier check · 1 new"
    assert w.headline({"closed": [], "new": [], "a_total": 0}) == "Nothing to close from the earlier check"


def test_the_results_headline_counts_the_new_finding_too(small_pair, tmp_path):
    add_diagnosis(small_pair, "after_aeo", [finding("RENDER_PHONE_MISSING", "medium")], when=NOW + timedelta(days=11))
    page = closed_text(make(small_pair, tmp_path, "results"))
    assert re.search(r"\d of \d closed · 1 new", page)


# ---- 5. the sentence we use only with an audit -------------------------------------------------------------------------------------------

def test_without_an_audit_the_sentence_is_replaced_and_nothing_claims_what_ai_says(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium")])
    res = make(biz, tmp_path, "starting_point")
    page = visible_text(html_of(res))
    assert "Next, we will ask AI assistants the questions your customers ask and record exactly what they say about you." in page
    assert t("promise.sentence") not in page and "names these competitors" not in page and "specific wrong things" not in page
    assert "what AI assistants say about you now" not in page
    assert section(res, "promise")["data"] == {"audit": False}


def test_with_a_finished_audit_the_sentence_we_use_is_there(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    page = visible_text(html_of(res))
    assert t("promise.sentence") in page and "Next, we will ask AI assistants" not in page
    assert section(res, "promise")["data"] == {"audit": True} and "what AI assistants say about you now" in page


def test_an_audit_that_is_not_finished_does_not_count(starting, tmp_path):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE probe_runs SET status = 'in_progress' WHERE id = %s", (starting["run_id"],))
    finally:
        conn.close()
    page = visible_text(html_of(make(starting, tmp_path, "starting_point")))
    assert t("promise.sentence") not in page and "Next, we will ask AI assistants" in page


# ---- 6. no audit: one short section ----------------------------------------------------------------------------------------------------------

def test_no_audit_means_one_short_section_not_two_empty_ones(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MISSING", "medium")])
    res = make(biz, tmp_path, "starting_point")
    assert keys(res) == ["health", "ai_next", "recommend", "evidence", "promise"]
    page = html_of(res)
    assert "<h2>What AI says about you: measured next</h2>" in page
    assert "Where AI stands today" not in page and "The questions we asked" not in page
    assert "No finished measurement of what AI says exists yet." not in page
    assert "That measurement is the next step." in page


def test_with_an_audit_both_sections_are_still_there(starting, tmp_path):
    assert keys(make(starting, tmp_path, "starting_point")) == ["health", "ai_today", "questions", "recommend", "evidence", "promise"]


# ---- 7. recommendations in the client's words ----------------------------------------------------------------------------------------------------

def test_confirm_the_missing_facts_example(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("LLMS_TXT_MISSING", "low")])
    make_recommendations(biz, fetcher=fake_site(), no_audit=True, clients_root=tmp_path / "clients", today=TODAY)
    internal = "Confirm the missing facts so schema.json can be built"
    page = html_of(make(biz, tmp_path, "starting_point"))
    assert "Confirm your business details with us, so AI tools describe you correctly" in page
    assert internal not in page and "schema.json" not in page
    # our own recommendations file keeps the internal title
    files = list((tmp_path / "clients" / biz["domain"] / "generated").glob("*recommendations*.md"))
    assert files and any(internal in f.read_text() for f in files)


def test_every_recommended_fix_gets_a_client_title_without_markup_names(starting, tmp_path):
    res = make(starting, tmp_path, "starting_point")
    rec = section(res, "recommend")["data"]
    w = Words(res.data)
    titles = [w.fix_title(f, rec["platform"]) for f in rec["fixes"]]
    assert titles and len(set(titles)) >= 3
    for title in titles:
        low = title.lower()
        assert not re.search(r"json-ld|schema\.json|llms\.txt|robots\.txt|\bh1\b|@id|sameas", low), title
        assert escape(title) in html_of(res)


def test_the_fix_titles_use_the_quote_and_question_the_fix_is_about(biz):
    w = words_for(biz)
    assert w.fix_title({"section": "wrong_fact", "finding_code": "AUDIT_WRONG_FACT", "quote": "Only serves Ontario"}) == \
        "Correct this wrong statement AI tools make about you: “Only serves Ontario”"
    assert w.fix_title({"section": "target_question", "finding_code": "AUDIT_TARGET_QUESTION_GAP", "question": "Who refills in Montreal?"}) == \
        "Help AI tools find your business when customers ask: “Who refills in Montreal?”"
    assert "{" not in w.fix_title({"section": "finding", "finding_code": "PLATFORM_PLAN_TOO_LOW"}, None)
    assert w.fix_title({"section": "finding", "finding_code": "PLATFORM_PLAN_TOO_LOW"}, "squarespace") == \
        "Move to a higher Squarespace plan so the changes can be made"


# ---- 8. print layout -------------------------------------------------------------------------------------------------------------------------------

def css_of(page: str) -> str:
    return re.search(r"<style>(.*?)</style>", page, re.S).group(1)


def test_sections_are_not_forced_onto_new_pages(starting, small_pair, tmp_path):
    for biz_, stage in ((starting, "starting_point"), (small_pair, "results"), (starting, "work_done")):
        page = html_of(make(biz_, tmp_path, stage))
        css = re.sub(r"\s+", "", css_of(page)).lower()
        for banned in ("page-break-before:always", "break-before:page", "break-before:always", "page-break-before:left", "break-before:left"):
            assert banned not in css, banned
        assert "newpage" not in page


def test_print_css_keeps_things_together_and_sets_page_margins(starting, tmp_path):
    css = re.sub(r"\s+", "", css_of(html_of(make(starting, tmp_path, "starting_point"))))
    assert re.search(r"@page\{[^}]*margin:\d", css)
    print_block = css[css.index("@mediaprint"):]
    assert re.search(r"h2,h3\{[^}]*break-after:avoid", print_block)
    avoid = re.search(r"([^{}]+)\{[^}]*break-inside:avoid", print_block).group(1)
    for selector in ("table", "ul", "figure", ".summary", ".box", "blockquote"):
        assert selector in avoid.split(","), selector


def test_the_style_text_in_the_renderer_has_no_forced_breaks():
    assert "break-before" not in render.CSS and "page-break-before" not in render.CSS


# ---- 9. the wording is all in the dictionary --------------------------------------------------------------------------------------------------------

def test_a_second_language_changes_the_finding_sentences_too(starting, tmp_path, monkeypatch):
    monkeypatch.setitem(STRINGS, "xx", {k: "«" + v for k, v in STRINGS["en"].items()})
    res = make(starting, tmp_path, "starting_point")
    data = res.data
    data["tool"]["language"] = "xx"
    page = visible_text(render_html(data))
    assert "«Your" in page and "«before we started" in page
    assert "«Confirm your business details" in page or "«Add a behind-the-scenes summary" in page
    assert not re.search(r"(?<!«)Your page has no main heading", page)          # every English sentence carries the marker: none is hard-wired


# ---- 10. the AI summaries get the client's words, not our labels ---------------------------------------------------------------------------------

def test_the_model_is_given_the_clients_words_and_no_internal_labels(starting, tmp_path, tty):
    from tests.test_client_report import ai_run
    set_engagement(starting, "web_dev_aeo")
    res, client, _ = ai_run(starting, tmp_path, ["The check and the measurements are shown in this section."] * 8)
    sent = " ".join(str(c) for c in client.messages.calls)
    assert client.messages.calls and "your previous website" in sent and "your new website" in sent
    assert not re.search(r"\b[A-Z]{3,}_[A-Z_]{2,}\b", sent) and "after_site" not in sent and "RENDER_H1_MISSING" not in sent
