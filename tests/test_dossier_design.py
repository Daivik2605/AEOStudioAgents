"""Step 22f: the dossier design pass. Presentation only: every number on the web page comes from the section data."""

from __future__ import annotations

import re
from html import unescape

from typer.testing import CliRunner

from aeo import cli
from dossier import render as R
from dossier.run import rebuild_from_json
from tests.test_audit import biz, confirm_facts  # noqa: F401  (fixtures and helpers)
from tests.test_compare import RIVAL, add_run, make_qs
from tests.test_dossier import dossier, filled, section, set_engagement, write_questions
from tests.test_dossier_followup import FULL
from tests.test_honest_closed import oland


def page_of(res) -> str:
    return res.html_path.read_text(encoding="utf-8")


def text_of(fragment: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def rich(biz, tmp_path):
    filled(biz, tmp_path)
    set_engagement(biz, "web_dev_aeo")
    return dossier(biz, tmp_path, questions=write_questions(tmp_path / "s_v1.yaml", FULL))


# ---- the index ----------------------------------------------------------------------------------------------------------------------

def test_every_link_to_an_anchor_points_at_one_that_exists(biz, tmp_path):
    page = page_of(rich(biz, tmp_path))
    ids = re.findall(r"\bid='([^']+)'", page) + re.findall(r'\bid="([^"]+)"', page)
    assert len(ids) == len(set(ids)), "an id is used twice"
    links = re.findall(r"href='#([^']+)'", page)
    assert len(links) > 40
    assert [l for l in links if l not in ids] == []


def test_the_index_lists_every_section_and_sub_section_with_a_link(biz, tmp_path):
    res = rich(biz, tmp_path)
    page = page_of(res)
    index = re.search(r"<nav id='index'.*?</nav>", page, re.S).group(0)
    for s in res.data["sections"]:
        assert f"href='#s-{s['key']}'" in index and s["title"] in text_of(index)
        for blk in s["blocks"]:
            if blk["t"] == "h":
                assert blk["text"] in text_of(index)
        assert f"href='#s-{s['key']}-0'" in index                                    # "Our notes"
    sub_links = re.findall(r"href='#(s-[a-z_]+-\d+)'", index)
    sub_headings = sum(1 for s in res.data["sections"] for b in s["blocks"] if b["t"] == "h")
    assert len(sub_links) == sub_headings + len(res.data["sections"])
    assert re.search(r"<h3 id='s-questions-1'>", page)                               # a sub-section really carries its anchor


def test_each_section_heading_has_a_back_to_index_link(biz, tmp_path):
    res = rich(biz, tmp_path)
    page = page_of(res)
    assert page.count("class='back' href='#index'") == len(res.data["sections"]) == 11
    assert "<nav id='index'" in page and page.index("id='cover'") < page.index("id='index'") < page.index("id='s-how_to_use'")


# ---- the cover -------------------------------------------------------------------------------------------------------------------------

def tiles_in(page: str) -> dict:
    return {m.group(1): m.group(0) for m in re.finditer(r"<a class='tile' data-tile='([a-z_]+)'.*?</a>", page, re.S)}


def test_the_cover_has_the_client_facts_and_six_tiles(biz, tmp_path):
    res = rich(biz, tmp_path)
    page = page_of(res)
    cover = re.search(r"<section class='cover'.*?</section>", page, re.S).group(0)
    for fragment in (biz["name"], biz["domain"], "status", "web_dev_aeo", "v1", res.data["generated_on"]):
        assert fragment in text_of(cover)
    assert list(tiles_in(page)) == ["website_health", "crawler", "spot_check", "facts", "question_set", "audits"]


def test_the_tile_numbers_equal_the_section_data(biz, tmp_path):
    oland(biz)
    res = dossier(biz, tmp_path)
    tiles = tiles_in(page_of(res))
    step = section(res, "how_ai_sees")["data"]["steps"][-1]["counts"]
    assert (step["fixed_count"], step["no_longer_applies_count"], step["still_open_count"], step["new_count"]) == (2, 6, 1, 1)
    assert "2 fixed · 6 no longer apply · 1 open · 1 new" in text_of(tiles["website_health"])
    for label in ("fixed", "no longer applies", "open", "new"):
        assert f"<span class='chip" in tiles["website_health"] and label in text_of(tiles["website_health"])


def test_every_other_tile_is_the_stage_and_section_data(biz, tmp_path):
    res = rich(biz, tmp_path)
    tiles = {k: text_of(v) for k, v in tiles_in(page_of(res)).items()}
    stage = {i["item"]: i for i in section(res, "stage")["data"]["items"]}
    snap = section(res, "snapshot")["data"]
    assert f"{snap['confirmed']} of {snap['total']} confirmed" in tiles["facts"]
    assert stage["Spot check run"]["detail"] in tiles["spot_check"] and stage["Spot check run"]["status"] in tiles["spot_check"]
    qset = section(res, "questions")["data"]["set"]
    assert qset["version"] in tiles["question_set"] and qset["state"] in tiles["question_set"]
    done = sum(1 for i in stage.values() if i["item"].startswith("Audit: ") and i["status"] == "done")
    assert f"{done} done" in tiles["audits"]
    crawler = section(res, "crawler")["data"]["reports"]
    rows = section(res, "how_ai_sees")["data"]["columns"][-1]["rows"]
    bots = [v for k, v in rows.items() if k.endswith("bot)")]
    assert f"{len(crawler)} crawler report(s) on file" in tiles["crawler"]
    assert (f"{sum(1 for v in bots if v != 'allowed')} of {len(bots)} AI bots blocked" in tiles["crawler"]) or f"all {len(bots)} AI bots allowed" in tiles["crawler"]


def test_empty_tiles_say_so_plainly(biz, tmp_path):
    tiles = {k: text_of(v) for k, v in tiles_in(page_of(dossier(biz, tmp_path))).items()}
    assert "no diagnosis yet" in tiles["website_health"] and "not tested" in tiles["crawler"]
    assert "not yet" in tiles["spot_check"] and "no truth document yet" in tiles["facts"]
    assert "no question set yet" in tiles["question_set"] and "none yet" in tiles["audits"]


def test_top_three_problems_and_next_step_come_from_sections_seven_and_eleven(biz, tmp_path):
    res = rich(biz, tmp_path)
    page = page_of(res)
    card = re.search(r"Top 3 problems.*?</ol>", page, re.S).group(0)
    items = section(res, "problems")["data"]["items"][:3]
    assert len(items) == 3
    for i in items:
        assert i["text"].replace("`", "") in text_of(card) and i["severity"] in text_of(card)
    flat = text_of(card)
    assert flat.index(items[0]["text"].replace("`", "")[:20]) < flat.index(items[2]["text"].replace("`", "")[:20])
    step = section(res, "next")["data"]["steps"][0]
    nxt = re.search(r"Next step.*?</div>", page, re.S).group(0)
    assert step["step"] in text_of(nxt) and step["why"].replace("`", "")[:30] in text_of(nxt)


def test_a_complete_baseline_says_nothing_is_blocking(biz, tmp_path):
    res = dossier(biz, tmp_path)
    assert "Nothing is blocking" not in page_of(res)            # an empty client still has next steps
    page = R.cover_html({**res.data, "sections": [dict(s, data={**s["data"], "steps": []}) if s["key"] == "next" else s for s in res.data["sections"]]})
    assert "Nothing is blocking." in page


# ---- chips ---------------------------------------------------------------------------------------------------------------------------------

def test_every_chip_has_a_text_label_and_one_colour_per_label(biz, tmp_path):
    page = page_of(rich(biz, tmp_path))
    chips = re.findall(r"<span class='chip c-([a-z]+)'>(.*?)</span>", page)
    assert len(chips) > 40
    seen = {}
    for colour, inner in chips:
        label = re.sub(r"^[^\w]+", "", unescape(inner)).strip()
        assert re.search(r"[A-Za-z]{2,}", label), f"a chip with no text label: {inner!r}"
        if colour != "plain":       # plain chips are labels (type, length, kind), not statuses: "medium" length is not "medium" importance
            assert seen.setdefault(label, colour) == colour, f"{label} has two colours"
    assert page.count("<span class='chip") == len(chips)                      # none was written in some other form
    labels = {re.sub(r"^[^\w]+", "", unescape(i)).strip() for _, i in chips}
    assert {"done", "not yet", "fixed", "no longer applies", "new"} <= labels


def test_chip_kinds_cover_the_listed_statuses_with_distinct_looks():
    for label in ("done", "not yet", "draft", "frozen", "high", "medium", "low", "fixed", "no longer applies", "open", "new"):
        assert label in R.CHIP_KINDS
        assert re.search(r"[A-Za-z]", R.chip(label))
    # colour is never the only signal: the glyph or the border style differs between the main groups
    assert len({R.CHIP_KINDS[k][1] for k in ("done", "not yet", "high", "medium", "no longer applies", "new")}) == 6
    css = R.CSS
    assert "border-style:dashed" in css and "border-style:dotted" in css and "border-width:2.5px" in css


def test_status_and_importance_columns_become_chips(biz, tmp_path):
    res = rich(biz, tmp_path)
    page = page_of(res)
    stage = re.search(r"<section class='sec' id='s-stage'>.*?</section>", page, re.S).group(0)
    assert "<span class='chip c-good'>✓ done</span>" in stage and "<span class='chip c-wait'>○ not yet</span>" in stage
    problems = re.search(r"<section class='sec' id='s-problems'>.*?</section>", page, re.S).group(0)
    assert re.search(r"<td><span class='chip c-(bad|mid|quiet)'>", problems)


# ---- readability --------------------------------------------------------------------------------------------------------------------------

def test_the_findings_become_a_table_of_outcome_sentence_and_code(biz, tmp_path):
    oland(biz)
    res = dossier(biz, tmp_path)
    page = page_of(res)
    sec4 = re.search(r"<section class='sec' id='s-how_ai_sees'>.*?</section>", page, re.S).group(0)
    assert "<th scope='col'>Outcome</th><th scope='col'>What it says</th><th scope='col'>Code</th>" in sec4
    rows = re.findall(r"<tr><td class='outcome'>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>", sec4, re.S)
    assert len(rows) == 10                                                              # 2 fixed + 6 removed + 1 open + 1 new
    assert sorted(text_of(r[0]).split(" ", 1)[1] for r in rows).count("no longer applies") == 6
    code_cells = [text_of(r[2]) for r in rows]
    assert "SCHEMA_NONE_FOUND" in code_cells and all(re.fullmatch(r"[A-Z_]+", c) for c in code_cells)
    assert all("class='code'" in r[2] for r in rows)
    assert "replaced by: SCHEMA_NONE_FOUND what" in text_of(sec4)
    assert "[SCHEMA_NONE_FOUND]" not in sec4                                               # in the table the code has its own column


def test_the_markdown_keeps_the_plain_lines(biz, tmp_path):
    oland(biz)
    md = dossier(biz, tmp_path).md_path.read_text()
    assert "- Fixed: RENDER_PHONE_MISSING what [RENDER_PHONE_MISSING]" in md


def test_the_questions_table_uses_chips_for_type_length_and_target(biz, tmp_path):
    page = page_of(rich(biz, tmp_path))
    sec = re.search(r"<section class='sec' id='s-questions'>.*?</section>", page, re.S).group(0)
    row = re.search(r"<tr><td>refill station rental</td>(.*?)</tr>", sec, re.S).group(1)
    assert "<span class='chip c-plain'>discovery</span>" in row and "<span class='chip c-plain'>short</span>" in row
    assert "<span class='chip c-plain'>target</span>" in row
    other = re.search(r"<tr><td>What is the brand\?</td>(.*?)</tr>", sec, re.S).group(1)
    assert "<span class='muted'>no</span>" in other and "spot check</span>" in other


def test_numbers_are_right_aligned_and_rows_are_striped(biz, tmp_path):
    table = R.table_html({"headers": ["Name", "Count", "Share"], "rows": [["a", "12", "40%"], ["b", "3", "7.5%"]]})
    assert table.count("<td class=\"num\">") == 4 and "<th scope='col' class=\"num\">Count</th>" in table and "<th scope='col'>Name</th>" in table
    mixed = R.table_html({"headers": ["Count"], "rows": [["12"], ["none"]]})
    assert "num" not in mixed                                                                # one non-number: the column stays left
    assert "tbody tr:nth-child(even)" in R.CSS and "td.num,th.num{text-align:right" in R.CSS


def test_our_notes_look_like_an_annotation_not_data(biz, tmp_path):
    first = dossier(biz, tmp_path)
    first.notes_path.write_text(first.notes_path.read_text().replace("## Stage\n", "## Stage\nwe think the move is late\n"))
    page = page_of(dossier(biz, tmp_path))
    box = re.search(r"<aside class='notes' id='s-stage-0'>.*?</aside>", page, re.S).group(0)
    assert "Our notes" in box and "written by us, not data" in box and "we think the move is late" in box
    assert page.count("<aside class='notes'") == 11
    css = re.search(r"\.notes\{([^}]*)\}", R.CSS).group(1)
    assert "border-left:6px" in css and "background:var(--note)" in css


def test_a_consistent_type_scale_one_accent_and_numbered_sections(biz, tmp_path):
    css = R.CSS
    for var in ("--t-title", "--t-section", "--t-sub", "--t-body", "--t-small", "--accent:"):
        assert var in css
    assert css.count("--accent:") == 1
    page = page_of(rich(biz, tmp_path))
    assert re.search(r"<h2><span class='num'>1</span>How to use", page) and re.search(r"<h2><span class='num'>11</span>What happens next", page)
    assert page.count("<p class='answer'>") == 11            # the answer card of each section


# ---- print --------------------------------------------------------------------------------------------------------------------------------

def test_print_has_a_header_a_footer_and_page_numbers(biz, tmp_path):
    res = dossier(biz, tmp_path)
    css = re.sub(r"\s+", "", re.search(r"<style>(.*?)</style>", page_of(res), re.S).group(1))
    page_rule = re.search(r"@page\{.*?\}\}", css).group(0)
    assert "@top-left" in page_rule and f"{biz['name']}·dossier".replace(" ", "") in page_rule
    assert "@bottom-left" in page_rule and "INTERNAL:notfortheclient·v1".replace(" ", "") in page_rule.replace(" ", "")
    assert 'counter(page)' in page_rule and 'counter(pages)' in page_rule
    assert "break-before:page" not in css and "page-break-before:always" not in css


def test_the_page_header_text_is_safe_inside_css(biz):
    data = {"business": {"name": 'Bad "name" \\ x', "domain": "x.example"}, "version": 2, "generated_on": "2026-10-25"}
    rule = R.page_rule(data)
    assert '\\"' not in rule and "\\ " not in rule.replace("\\\"Page", "") and rule.count('"') % 2 == 0


# ---- self-contained -------------------------------------------------------------------------------------------------------------------

def test_still_one_self_contained_file(biz, tmp_path):
    page = page_of(rich(biz, tmp_path))
    assert page.count("<style>") == 1 and page.startswith("<!doctype html>")
    for banned in ("<script", "<link", "<iframe", "<img", "<svg", "@import", "url(", "xmlns=", "@font-face", "fonts.googleapis"):
        assert banned not in page, banned
    assert not re.search(r"""(?i)\b(?:src|href|action)\s*=\s*["']?\s*(?:https?:)?//""", page)
    assert not re.search(r"https?://", re.sub(r"<(?:p|td|li|div|span)[^>]*>.*?</(?:p|td|li|div|span)>", "", page, flags=re.S))
    for href in re.findall(r"href='([^']*)'", page):
        assert href.startswith("#")
    assert "font-family" not in page or "system-ui" in page


def test_the_client_report_was_not_touched_by_this_pass():
    from client_report import render as client
    assert ".tile" not in client.CSS and "--accent" not in client.CSS and "break-before:page" not in client.CSS


# ---- --from-json ---------------------------------------------------------------------------------------------------------------------------

def test_from_json_rebuilds_identically(biz, tmp_path):
    res = rich(biz, tmp_path)
    again = rebuild_from_json(biz, res.json_path, clients_root=tmp_path / "clients")
    assert again.identical == {"md": True, "html": True}
    assert again.html_path == res.html_path and again.md_path == res.md_path


def test_from_json_recreates_a_missing_file_and_never_overwrites_a_different_one(biz, tmp_path):
    res = rich(biz, tmp_path)
    original = res.html_path.read_bytes()
    res.html_path.unlink()
    again = rebuild_from_json(biz, res.json_path, clients_root=tmp_path / "clients")
    assert again.identical["html"] is None and again.html_path.read_bytes() == original
    res.md_path.write_text("hand-edited")
    third = rebuild_from_json(biz, res.json_path, clients_root=tmp_path / "clients")
    assert third.identical["md"] is False and res.md_path.read_text() == "hand-edited" and third.md_path != res.md_path
    assert third.md_path.read_text() == R.render_md(__import__("json").loads(res.json_path.read_text()))


def test_from_json_needs_no_database_rows(biz, tmp_path):
    res = rich(biz, tmp_path)
    data = __import__("json").loads(res.json_path.read_text())
    assert R.render_html(data).encode() == res.html_path.read_bytes() and R.render_md(data).encode() == res.md_path.read_bytes()


def test_the_command_rebuilds_from_json(biz):
    runner = CliRunner()
    made = runner.invoke(cli.app, ["dossier", "--business", biz["domain"]])
    assert made.exit_code == 0, made.output
    json_file = re.search(r"Data:\s+(\S+\.json)", made.output).group(1)
    again = runner.invoke(cli.app, ["dossier", "--business", biz["domain"], "--from-json", json_file])
    assert again.exit_code == 0 and again.output.count("identical to the original") == 2
    clash = runner.invoke(cli.app, ["dossier", "--business", biz["domain"], "--from-json", json_file, "--questions", "x.yaml"])
    assert clash.exit_code == 1 and "do not combine" in clash.output
    bad = runner.invoke(cli.app, ["dossier", "--business", biz["domain"], "--from-json", "nope.json"])
    assert bad.exit_code == 1 and "cannot read" in bad.output
