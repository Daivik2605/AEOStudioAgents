"""Step 22c: a finding whose subject is gone is not "closed". Built on the shape of O'land's real data: the old site had
six structured-data problems, the new site has no structured data at all. Real Postgres (aeo_test); no AI."""

from __future__ import annotations

import json
import re
from datetime import timedelta

from client_report.run import rebuild_from_json
from compare.diagnosis import compare_diagnoses
from core import timeline as tl
from core.db import get_connection
from diagnose.codes import DEPENDS_ON, JSONLD_CONTENT_CODES, REGISTRY, subject_removed_by
from tests.test_audit import biz  # noqa: F401  (fixture)
from tests.test_client_report import TODAY, html_of, make, section
from tests.test_client_report_wording import visible_text
from tests.test_compare import NOW, add_diagnosis, compare, finding

SIX_SCHEMA = ["SCHEMA_NAME_IS_LEGAL_ENTITY", "SCHEMA_ORG_MISSING_NAME", "SCHEMA_LOCALBUSINESS_NO_AREASERVED",
              "SCHEMA_OPENING_HOURS_EMPTY", "SCHEMA_SAMEAS_HAS_TRACKING_PARAMS", "SCHEMA_ENTITIES_UNLINKED"]
HEADLINE = "2 fixed · 6 no longer apply · 1 still open · 1 new"


def oland(biz, *, old="wix", new="nextjs"):
    """Old site: 6 schema findings + 2 real problems + 1 more. New site: the 2 are fixed, 1 remains, and there is no
    structured data at all (SCHEMA_NONE_FOUND is new). Nine findings in A: the '8 of 9 closed' of the real report."""
    add_diagnosis(biz, "before", [finding(c, "medium") for c in SIX_SCHEMA]
                  + [finding("RENDER_PHONE_MISSING", "medium"), finding("RENDER_ADDRESS_MISSING", "medium"), finding("LLMS_TXT_MISSING", "low")],
                  platform=old, when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [finding("LLMS_TXT_MISSING", "low"), finding("SCHEMA_NONE_FOUND", "medium")],
                  platform=new, when=NOW - timedelta(days=2))


# ---- the data: dependencies ------------------------------------------------------------------------------------------------------------

def test_every_code_that_reads_json_ld_declares_what_it_depends_on():
    assert set(JSONLD_CONTENT_CODES) == {c for c in REGISTRY if c.startswith("SCHEMA_") and c != "SCHEMA_NONE_FOUND"} and len(JSONLD_CONTENT_CODES) == 7
    missing = [c for c in JSONLD_CONTENT_CODES if c not in DEPENDS_ON]
    assert not missing, f"{missing} inspect JSON-LD but declare no dependency in diagnose/codes.py"
    for code in JSONLD_CONTENT_CODES:
        assert DEPENDS_ON[code]["gone_when"] == ("SCHEMA_NONE_FOUND",)
    assert "SCHEMA_NONE_FOUND" not in DEPENDS_ON                      # the finding that says there is none depends on nothing


def test_dependencies_only_name_real_codes():
    for code, dep in DEPENDS_ON.items():
        assert code in REGISTRY and dep["subject"] and dep["gone_when"]
        assert all(g in REGISTRY for g in dep["gone_when"]) and code not in dep["gone_when"]


def test_subject_removed_by():
    assert subject_removed_by("SCHEMA_ORG_MISSING_NAME", {"SCHEMA_NONE_FOUND"}) == ["SCHEMA_NONE_FOUND"]
    assert subject_removed_by("SCHEMA_ORG_MISSING_NAME", {"LLMS_TXT_MISSING"}) == []
    assert subject_removed_by("LLMS_TXT_MISSING", {"SCHEMA_NONE_FOUND"}) == []          # no declared dependency


# ---- compare -----------------------------------------------------------------------------------------------------------------------------

def test_oland_is_two_fixed_six_no_longer_apply_one_still_open_one_new(biz, tmp_path):
    oland(biz)
    d = compare(biz, tmp_path, "before", "after_site").data["diagnosis"]
    assert d["headline"] == HEADLINE
    assert sorted(c["code"] for c in d["fixed"]) == ["RENDER_ADDRESS_MISSING", "RENDER_PHONE_MISSING"]
    assert sorted(c["code"] for c in d["no_longer_applies"]) == sorted(SIX_SCHEMA)
    assert [c["code"] for c in d["still_open"]] == ["LLMS_TXT_MISSING"] and [c["code"] for c in d["new"]] == ["SCHEMA_NONE_FOUND"]
    assert (d["fixed_count"], d["no_longer_applies_count"], d["still_open_count"], d["new_count"]) == (2, 6, 1, 1)
    assert d["a_total"] == 9


def test_each_removed_finding_says_what_replaced_it_and_is_not_in_the_fixed_list(biz, tmp_path):
    oland(biz)
    d = compare(biz, tmp_path, "before", "after_site").data["diagnosis"]
    for c in d["no_longer_applies"]:
        assert c["outcome"] == "no_longer_applies"
        assert [r["code"] for r in c["replaced_by"]] == ["SCHEMA_NONE_FOUND"] and c["replaced_by"][0]["what"]
    assert all(c["outcome"] == "fixed" for c in d["fixed"])
    assert {c["code"] for c in d["closed"]} == {c["code"] for c in d["fixed"]}            # `closed` is kept, and means fixed only
    assert not {c["code"] for c in d["closed"]} & set(SIX_SCHEMA)


def test_a_schema_finding_is_fixed_when_the_structured_data_is_still_there(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("SCHEMA_NAME_IS_LEGAL_ENTITY", "high")], when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [], when=NOW - timedelta(days=2))                    # no SCHEMA_NONE_FOUND: the data exists
    d = compare(biz, tmp_path, "before", "after_site").data["diagnosis"]
    assert [c["code"] for c in d["fixed"]] == ["SCHEMA_NAME_IS_LEGAL_ENTITY"] and d["no_longer_applies"] == []
    assert d["headline"] == "1 fixed · 0 no longer apply · 0 still open · 0 new"


def test_two_main_headings_do_not_count_as_fixed_when_there_is_no_main_heading(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("RENDER_H1_MULTIPLE", "low")], when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [finding("RENDER_H1_MISSING", "medium")], when=NOW - timedelta(days=2))
    d = compare(biz, tmp_path, "before", "after_site").data["diagnosis"]
    assert [c["code"] for c in d["no_longer_applies"]] == ["RENDER_H1_MULTIPLE"] and d["fixed"] == []


def test_a_removed_finding_is_not_counted_as_work_that_worked(biz, tmp_path):
    oland(biz)
    side = {"platform": None, "plan": None, "verdict": "serve"}
    d = compare_diagnoses({**side, "findings": [finding("SCHEMA_ORG_MISSING_NAME", "high")]},
                          {**side, "findings": [finding("SCHEMA_NONE_FOUND", "medium")]}, [], "")
    assert d["fixed_count"] == 0 and d["no_longer_applies_count"] == 1


def test_the_compare_markdown_and_json_carry_the_outcome(biz, tmp_path):
    oland(biz)
    res = compare(biz, tmp_path, "before", "after_site")
    md = res.md_path.read_text()
    assert HEADLINE in md and "### No longer applies (6)" in md and "### Fixed (2)" in md
    assert "replaced by `SCHEMA_NONE_FOUND`" in md and "These were NOT fixed" in md
    raw = json.loads(res.json_path.read_text())
    assert raw["diagnosis"]["no_longer_applies_count"] == 6 and raw["diagnosis"]["headline"] == HEADLINE


# ---- the timeline ------------------------------------------------------------------------------------------------------------------------------

def test_the_timeline_carries_the_outcome_between_diagnoses(biz):
    oland(biz)
    conn = get_connection()
    try:
        items = [i for i in tl.collect_timeline(conn, biz["id"]) if i["kind"] == "diagnosis"]
    finally:
        conn.close()
    assert len(items) == 2 and "since the previous" not in items[0]["text"]
    assert f"since the previous diagnosis: {HEADLINE}" in items[1]["text"]


# ---- the client report ---------------------------------------------------------------------------------------------------------------------------

def test_the_client_report_separates_fixed_from_removed(biz, tmp_path):
    oland(biz)
    res = make(biz, tmp_path, "starting_point")
    text = visible_text(html_of(res))
    assert HEADLINE in text and "of 9 closed" not in text
    assert "Removed with the old website, still to be redone (6)" in text and "Fixed (2)" in text
    assert "These problems were not fixed" in text
    assert text.count("What replaced it:") == 6 and "no behind-the-scenes summary of your business" in text
    assert not re.search(r"SCHEMA_|RENDER_|LLMS_", text)


def test_fixed_by_the_new_website_only_appears_on_fixed_findings(biz, tmp_path):
    oland(biz)
    text = visible_text(html_of(make(biz, tmp_path, "starting_point")))
    assert text.count("Fixed by the new website") == 2                # the two fixed ones, not the six removed ones


def test_the_removed_findings_get_no_new_website_line_even_if_the_platform_changed(biz, tmp_path):
    add_diagnosis(biz, "before", [finding("SCHEMA_ORG_MISSING_NAME", "high")], platform="wix", when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_site", [finding("SCHEMA_NONE_FOUND", "medium")], platform="nextjs", when=NOW - timedelta(days=2))
    text = visible_text(html_of(make(biz, tmp_path, "starting_point")))
    assert "Fixed by the new website" not in text and "Removed with the old website, still to be redone (1)" in text
    assert "0 fixed · 1 no longer applies · 0 still open · 1 new" in text


def test_the_json_carries_the_outcome_and_an_old_json_still_rebuilds(biz, tmp_path):
    oland(biz)
    res = make(biz, tmp_path, "starting_point")
    comp = section(res, "health")["data"]["comparison"]
    assert len(comp["no_longer_applies"]) == 6 and all(c["outcome"] == "no_longer_applies" for c in comp["no_longer_applies"])
    again = rebuild_from_json(biz, res.json_path, clients_root=tmp_path / "clients")
    assert again.identical_to_original is True
    # a file saved before step 22c has none of the new keys: it must still rebuild, reading `closed` as fixed
    old = json.loads(res.json_path.read_text())
    cmp_ = next(s for s in old["sections"] if s["key"] == "health")["data"]["comparison"]
    for key in ("fixed", "no_longer_applies", "fixed_count", "no_longer_applies_count"):
        cmp_.pop(key, None)
    old_file = res.json_path.with_name("2026-10-01_client_report_starting_point_v9.json")
    old_file.write_text(json.dumps(old))
    rebuilt = rebuild_from_json(biz, old_file, clients_root=tmp_path / "clients")
    assert "2 fixed · 0 no longer apply · 1 still open · 1 new" in visible_text(rebuilt.html_path.read_text())


def test_the_results_report_uses_the_same_outcomes(biz, tmp_path):
    from tests.test_compare import RIVAL, add_run, make_qs
    qs = make_qs(10, targets=(0, 1))
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="before")
    add_run(biz, qs, lambda q, e, r: dict(named=RIVAL), checkpoint="after_aeo")
    add_diagnosis(biz, "before", [finding("SCHEMA_ORG_MISSING_NAME", "high"), finding("RENDER_H1_MISSING", "medium")], when=NOW - timedelta(days=9))
    add_diagnosis(biz, "after_aeo", [finding("SCHEMA_NONE_FOUND", "medium")], when=NOW + timedelta(days=3))
    text = visible_text(html_of(make(biz, tmp_path, "results")))
    assert "1 fixed · 1 no longer applies · 0 still open · 1 new" in text and "Removed with the old website, still to be redone (1)" in text
