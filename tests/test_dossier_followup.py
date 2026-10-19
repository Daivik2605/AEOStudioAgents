"""Step 22e follow-up: fixes from the first real dossier (O'land v1)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dossier import questions as dq
from dossier.render import render_html
from dossier.run import make_dossier
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_compare import CLIENT_REC, RIVAL, add_diagnosis, add_run, finding, make_qs
from tests.test_dossier import (add_evidence_file, dossier, filled, section, set_engagement, write_questions)

PLACEHOLDERS = {"", "tag", "derived", "none", "null", "-", "?", "n/a", "undefined"}
OLAND_YAML = Path(__file__).parent.parent / "clients" / "olandstations.com" / "question_set_draft_v0.yaml"
OLAND_MD = OLAND_YAML.with_suffix(".md")


def all_cells(res):
    for s in res.data["sections"]:
        for blk in s["blocks"]:
            if blk["t"] == "table":
                yield s["key"], blk


# ---- question tags ------------------------------------------------------------------------------------------------------------------------

FULL = [
    {"text": "refill station rental", "type": "discovery", "intent": "commercial", "length": "short", "language": "en", "is_target": True},
    {"text": "How do large outdoor events reduce plastic waste?", "type": "discovery", "intent": "informational", "length": "medium", "language": "en", "control": True},
    {"text": "What is the brand?", "type": "brand", "length": "short", "language": "fr", "spot_check": True},
]


def test_the_reader_uses_the_one_schema(tmp_path):
    items = dq.read_questions(write_questions(tmp_path / "s_v1.yaml", FULL))["items"]
    assert [(i["type"], i["intent"], i["length"], i["language"], i["is_target"], i["control"], i["spot_check"]) for i in items] == [
        ("discovery", "commercial", "short", "en", True, False, False),
        ("discovery", "informational", "medium", "en", False, True, False),
        ("brand", None, "short", "fr", False, False, True)]
    assert all(i["length_from"] == "tag" for i in items)


def test_type_and_length_cells_show_values_not_the_word_tag(biz, tmp_path):
    res = dossier(biz, tmp_path, questions=write_questions(tmp_path / "s_v1.yaml", FULL))
    rows = {r[0]: r for _, blk in all_cells(res) if "Intent" in blk["headers"] for r in blk["rows"]}
    assert rows["refill station rental"][1:] == ["discovery", "commercial", "short", "en", "yes", "no"]
    assert rows["What is the brand?"][1:] == ["brand", "not tagged", "short", "fr", "no", "yes"]


def test_a_cell_never_renders_a_placeholder_instead_of_a_value(biz, tmp_path):
    filled(biz, tmp_path)
    # untagged questions too: the value is derived and says so, it is never just "derived" or "tag"
    untagged = write_questions(tmp_path / "u_v1.yaml", [{"text": "refill station rental"}, {"text": "Who rents water stations for a summer festival in Toronto?"}])
    for questions in (None, untagged, write_questions(tmp_path / "f_v1.yaml", FULL)):
        res = dossier(biz, tmp_path, questions=questions)
        for key, blk in all_cells(res):
            for row in blk["rows"]:
                for cell in row:
                    assert str(cell).strip().lower() not in PLACEHOLDERS, (key, row)
                    assert not re.search(r"\{[a-z_]+\}|\bNone\b|\bnull\b", str(cell)), (key, row)
    rows = [r for _, blk in all_cells(dossier(biz, tmp_path, questions=untagged)) if "Intent" in blk["headers"] for r in blk["rows"]]
    assert rows[0][1] == "discovery (derived)" and rows[0][3] == "short (derived)"


def test_intent_is_shown_and_controls_are_grouped_separately(biz, tmp_path):
    res = dossier(biz, tmp_path, questions=write_questions(tmp_path / "s_v1.yaml", FULL))
    md = res.md_path.read_text()
    assert "### Controls (informational; we do not work on these)" in md and "| Question | Type | Intent | Length | Language | Target | Spot check |" in md
    groups = {}
    heading = None
    for blk in section(res, "questions")["blocks"]:
        if blk["t"] == "h":
            heading = blk["text"]
        elif blk["t"] == "table":
            groups[heading] = [r[0] for r in blk["rows"]]
    assert groups["Controls (informational; we do not work on these)"] == ["How do large outdoor events reduce plastic waste?"]
    assert "Discovery · medium" not in groups                      # the control is not in the ordinary groups
    assert section(res, "questions")["data"]["controls"] == 1


@pytest.mark.skipif(not OLAND_YAML.exists() or not OLAND_MD.exists(), reason="O'land's real files are not on this machine")
def test_olands_real_file_matches_the_schema_and_the_original_wording():
    items = dq.read_questions(OLAND_YAML)["items"]
    md = OLAND_MD.read_text(encoding="utf-8")
    assert len(items) == 32 and all(i["text"] in md for i in items)
    assert sum(i["control"] for i in items) == 3 and all(i["intent"] == "informational" for i in items if i["control"])
    assert {i["type"] for i in items} == {"discovery", "brand"} and all(i["length_from"] == "tag" for i in items)
    spot = [i["text"] for i in items if i["spot_check"]]
    assert len(spot) == 8
    expect = ["What is O'land Stations?", "Does O'land Stations only work with events in Montreal?", "What are the best companies that rent water refill stations for outdoor events in Canada?"]
    assert all(e in spot for e in expect) and any(t.startswith("I'm planning a 3-day music festival in Montreal") for t in spot)
    assert sum(1 for t in spot if "O'land Stations" in t or "olandstations.com" in t) == 6


# ---- the spot-check set ------------------------------------------------------------------------------------------------------------------------

def spot_run(biz, n=6):
    qs = make_qs(n)
    add_run(biz, qs, lambda q, e, r: dict(named=CLIENT_REC if q % 2 == 0 else RIVAL), checkpoint="before")
    return qs


def test_only_the_marked_questions_are_listed_and_counted(biz, tmp_path):
    qs = spot_run(biz)
    marked = write_questions(tmp_path / "set_v1.yaml", [{"text": t.text, "type": "discovery", "spot_check": i in (0, 1)} for i, t in enumerate(qs.items)])
    data = section(dossier(biz, tmp_path, questions=marked), "ai_says")["data"]
    assert sorted({r["question"] for r in data["rows"]}) == sorted([qs.items[0].text, qs.items[1].text]) and len(data["rows"]) == 4
    assert data["discovery"]["answers"] == 4 and data["discovery"]["client_named"] == 2          # q0 named (both engines), q1 not
    assert "marked spot_check" in data["spot_set"]


def test_a_separate_spot_check_file_is_used(biz, tmp_path):
    qs = spot_run(biz)
    small = write_questions(tmp_path / "spot_v1.yaml", [{"text": qs.items[2].text}, {"text": "a question the run never asked"}])
    res = dossier(biz, tmp_path, spot_check=small)
    data = section(res, "ai_says")["data"]
    assert {r["question"] for r in data["rows"]} == {qs.items[2].text} and data["spot_set"] == "spot_v1.yaml"
    assert "Only the 2 spot-check question(s) from spot_v1.yaml are listed." in res.md_path.read_text()


def test_with_no_spot_check_set_every_question_is_listed_and_it_says_so(biz, tmp_path):
    qs = spot_run(biz)
    res = dossier(biz, tmp_path)
    assert len(section(res, "ai_says")["data"]["rows"]) == len(qs.items) * 2 and "No spot-check set was named" in res.md_path.read_text()


def test_before_the_run_only_the_spot_check_questions_are_listed(biz, tmp_path):
    marked = write_questions(tmp_path / "set_v1.yaml", [{"text": "q one", "spot_check": True}, {"text": "q two"}, {"text": "q three", "spot_check": True}])
    assert section(dossier(biz, tmp_path, questions=marked), "ai_says")["data"]["questions_to_ask"] == ["q one", "q three"]


def test_the_command_takes_spot_check(biz, tmp_path):
    from typer.testing import CliRunner
    from aeo import cli
    small = write_questions(tmp_path / "spot_v1.yaml", [{"text": "q one"}])
    result = CliRunner().invoke(cli.app, ["dossier", "--business", biz["domain"], "--spot-check", str(small)])
    assert result.exit_code == 0, result.output
    bad = CliRunner().invoke(cli.app, ["dossier", "--business", biz["domain"], "--spot-check", str(tmp_path / "nope.yaml")])
    assert bad.exit_code == 1 and "not found" in bad.output


# ---- table headers ------------------------------------------------------------------------------------------------------------------------------

def test_every_table_has_real_header_cells(biz, tmp_path):
    filled(biz, tmp_path)
    res = dossier(biz, tmp_path, questions=write_questions(tmp_path / "s_v1.yaml", FULL))
    seen = 0
    for key, blk in all_cells(res):
        seen += 1
        assert blk["headers"] and all(h.strip() for h in blk["headers"]), (key, blk["headers"])
        assert all(len(row) == len(blk["headers"]) for row in blk["rows"]), key
    assert seen >= 8
    page = res.html_path.read_text()
    assert page.count("<th") >= seen * 2 and "<th scope='col'></th>" not in page and "<th></th>" not in page
    assert all(h.strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", page))


def test_section_four_columns_are_named_for_the_client(biz, tmp_path):
    set_engagement(biz, "web_dev_aeo")
    filled(biz, tmp_path)
    head = next(blk["headers"] for key, blk in all_cells(dossier(biz, tmp_path)) if key == "how_ai_sees")
    cols = section(dossier(biz, tmp_path), "how_ai_sees")["data"]["columns"]
    assert head[1] == f"Old site · before · {cols[0]['date']}" and head[2] == f"New site · after_site · {cols[1]['date']}"
    assert re.fullmatch(r"Old site · before · \d{4}-\d{2}-\d{2}", head[1])


def test_columns_for_a_client_whose_site_is_not_replaced(biz, tmp_path):
    set_engagement(biz, "aeo_only")
    add_diagnosis(biz, "before", [])
    head = next(blk["headers"] for key, blk in all_cells(dossier(biz, tmp_path)) if key == "how_ai_sees")
    assert head[1].startswith("Before our work · before · ")


# ---- markdown and entities in the HTML ----------------------------------------------------------------------------------------------------

def test_bold_and_backticks_render_as_formatting_in_the_html(biz, tmp_path):
    filled(biz, tmp_path)
    page = dossier(biz, tmp_path, questions=write_questions(tmp_path / "s_v1.yaml", FULL)).html_path.read_text()
    assert "**" not in page and "`" not in page
    assert "<strong>draft</strong>" in page and "<code>aeo recommend</code>" in page
    assert "<b>" not in page.replace("<body>", "")


def test_the_inline_formatter_escapes_before_it_formats():
    from dossier.render import inline
    assert inline("a **bold** and `code` & <i>x</i>") == "a <strong>bold</strong> and <code>code</code> &amp; &lt;i&gt;x&lt;/i&gt;"
    assert inline("2 * 3 = 6, 4 ** 5") == "2 * 3 = 6, 4 ** 5"        # stars with no closing pair stay as they are


def test_notes_stay_verbatim_even_if_they_contain_markdown(biz, tmp_path):
    first = dossier(biz, tmp_path)
    first.notes_path.write_text(first.notes_path.read_text().replace("## Stage\n", "## Stage\nthey said **no** twice\n"))
    assert "they said **no** twice" in dossier(biz, tmp_path).html_path.read_text()


def test_entities_in_the_saved_page_are_decoded(biz, tmp_path):
    folder = tmp_path / "clients" / biz["domain"] / "evidence"
    folder.mkdir(parents=True)
    (folder / "2026-10-13_raw_page_run1.html").write_text(
        "<html><head><title>O&#x27;land Stations &amp; Co</title></head><body><h1>Welcome to O&#x27;land</h1></body></html>")
    page = section(dossier(biz, tmp_path), "snapshot")["data"]["page"]
    assert page["title"] == "O'land Stations & Co" and page["main_heading"] == "Welcome to O'land"
    assert "&#x27;" not in (tmp_path / "clients" / biz["domain"] / "generated").glob("*dossier_v1.md").__next__().read_text()


# ---- findings in plain words --------------------------------------------------------------------------------------------------------------

def test_each_finding_is_a_plain_sentence_then_the_code_in_brackets(biz, tmp_path):
    from tests.test_honest_closed import oland
    oland(biz)
    lines = next(s["lines"] for s in section(dossier(biz, tmp_path), "how_ai_sees")["data"]["steps"])
    assert "Fixed: RENDER_PHONE_MISSING what [RENDER_PHONE_MISSING]" in lines
    assert "New: SCHEMA_NONE_FOUND what [SCHEMA_NONE_FOUND]" in lines
    assert all(re.search(r"\[[A-Z_]+\]", l) and not l.split(": ", 1)[1].startswith("[") for l in lines)


# ---- wording ----------------------------------------------------------------------------------------------------------------------------------------

def stage_rows(res):
    return {i["item"]: i for i in section(res, "stage")["data"]["items"]}


def test_with_no_facts_the_stage_row_says_no_truth_document_yet(biz, tmp_path):
    row = stage_rows(dossier(biz, tmp_path))["Facts confirmed"]
    assert row["detail"] == "no truth document yet" and "0 of 0" not in json.dumps(section(dossier(biz, tmp_path), "stage"))
    confirm_facts(biz, Phone="514-555-0100")
    assert stage_rows(dossier(biz, tmp_path))["Facts confirmed"]["detail"] == "1 of 1"


def test_a_replaced_site_with_no_move_evidence_says_so(biz, tmp_path):
    set_engagement(biz, "web_dev_aeo")
    assert stage_rows(dossier(biz, tmp_path))["Site-move evidence logged"]["detail"] == "the site is being replaced; nothing logged"
    add_diagnosis(biz, "before", [], platform="wix")
    add_diagnosis(biz, "after_site", [], platform="nextjs")
    row = stage_rows(dossier(biz, tmp_path))["Site-move evidence logged"]
    assert row["detail"] == "site replaced; move not finished (no site_move evidence)" and row["status"] == "not yet"
    add_evidence_file(biz, tmp_path, "redirects.txt", "site_move", checkpoint="after_site")
    assert stage_rows(dossier(biz, tmp_path))["Site-move evidence logged"]["status"] == "done"


def test_finish_the_site_move_is_also_a_problem_of_kind_site(biz, tmp_path):
    set_engagement(biz, "web_dev_aeo")
    add_diagnosis(biz, "before", [], platform="wix")
    add_diagnosis(biz, "after_site", [], platform="nextjs")
    items = section(dossier(biz, tmp_path), "problems")["data"]["items"]
    move = [i for i in items if i["text"].startswith("Finish the site move")]
    assert len(move) == 1 and move[0]["kind"] == "site" and "already replaced" in move[0]["text"]
    add_evidence_file(biz, tmp_path, "redirects.txt", "site_move", checkpoint="after_site")
    assert not any(i["text"].startswith("Finish the site move") for i in section(dossier(biz, tmp_path), "problems")["data"]["items"])


def test_an_aeo_only_client_is_never_asked_to_finish_a_move(biz, tmp_path):
    set_engagement(biz, "aeo_only")
    assert not any(i["text"].startswith("Finish the site move") for i in section(dossier(biz, tmp_path), "problems")["data"]["items"])


# ---- print layout ----------------------------------------------------------------------------------------------------------------------------------

def css_of(page: str) -> str:
    return re.sub(r"\s+", "", re.search(r"<style>(.*?)</style>", page, re.S).group(1))


def test_headings_stay_with_their_first_table_and_long_tables_may_break(biz, tmp_path):
    filled(biz, tmp_path)
    res = dossier(biz, tmp_path, questions=write_questions(tmp_path / "s_v1.yaml", FULL))
    css = css_of(res.html_path.read_text())
    printed = css[css.index("@mediaprint"):]
    assert re.search(r"h2,h3\{[^}]*break-after:avoid", printed) and re.search(r"\.answer\{[^}]*break-after:avoid", css)
    # no table is forced to stay whole by default: the last word for a plain table is "auto"
    assert printed.rindex("table{break-inside:auto") > printed.index("table,ul")        # declared after the general avoid list
    assert "table.keep{break-inside:avoid" in printed and "tr{break-inside:avoid" in printed
    assert "break-before:page" not in css and "page-break-before:always" not in css


def test_only_short_tables_are_marked_to_stay_together(biz):
    short = {"business": {"name": "x", "domain": "x.example", "status": "prospect"}, "version": 1, "generated_on": "2026-10-25", "notes_warnings": [],
             "sections": [{"key": "a", "title": "A", "answer": "a", "data": {}, "notes": "",
                           "blocks": [{"t": "table", "headers": ["H"], "rows": [["r"]] * 15}, {"t": "table", "headers": ["H"], "rows": [["r"]] * 16}]}]}
    page = render_html(short)
    assert page.count("<table class='keep'>") == 1 and page.count("<table>") == 1


def test_notes_boxes_do_not_split_across_pages(biz, tmp_path):
    css = css_of(dossier(biz, tmp_path).html_path.read_text())
    assert re.search(r"@mediaprint\{.*\.notes\{break-inside:avoid", css)
