"""The audit fill-in file: generate -> a person fills it in -> import.

Real test database, fake Anthropic client. The "person" is the `fill()` helper, which edits the
sheet the way a human would: it types under the label lines and touches nothing else.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from typer.testing import CliRunner

from aeo.cli import app
from audit import fill_in as fi
from audit.fill_in import ImportRefused, generate_fill_in, import_fill_in, parse_sheet
from audit.manual import AuditError
from conftest import FakeAnthropicClient
from tests.test_audit import biz, extraction, qfile, query  # noqa: F401  (fixtures and helpers)

Q1 = "who supplies water refill stations for events?"
Q2 = "best refill station rental in Canada"
DAY = date(2026, 10, 7)


# ---- helpers ------------------------------------------------------------------

def generate(biz, qfile, tmp_path, *, questions=(Q1, Q2), repeats=1, checkpoint="old_site", day=DAY):
    path = qfile(questions=questions)
    made = generate_fill_in(biz["id"], str(path), checkpoint=checkpoint, repeats=repeats,
                            reports_root=tmp_path / "reports", today=day)
    return path, made


def _insert_after(lines, label, content, *, last=False, start=0, end=None):
    end = len(lines) if end is None else end
    hits = [i for i in range(start, end) if lines[i].rstrip() == label]
    i = hits[-1] if last else hits[0]
    lines[i + 1:i + 1] = content.split("\n") if content else []


def fill(sheet_path, *, header=None, **blocks):
    """header: (operator, logged, location). blocks: id -> dict(mode, searched, answer, links)."""
    lines = sheet_path.read_text(encoding="utf-8").split("\n")
    if header:
        op, logged, loc = header
        end = next(i for i, l in enumerate(lines) if fi.HEADING_RE.match(l))
        for label, value in ((fi.LOCATION_LABEL, loc), (fi.LOGGED_LABEL, logged), (fi.OPERATOR_LABEL, op)):
            if value:
                _insert_after(lines, label, value, end=end)
    for block_id, v in blocks.items():
        start = next(i for i, l in enumerate(lines) if fi.HEADING_RE.match(l) and f"[{block_id.replace('_', '-')}]" in l)
        end = next((i for i in range(start + 1, len(lines)) if fi.HEADING_RE.match(lines[i])), len(lines))
        # Bottom-up so earlier insertions do not shift the later positions.
        for label, key, last in ((fi.LINKS_LABEL, "links", True), (fi.ANSWER_LABEL, "answer", False),
                                 (fi.SEARCH_LABEL, "searched", False), (fi.MODE_LABEL, "mode", False)):
            if v.get(key):
                _insert_after(lines, label, v[key], last=last, start=start, end=end)
                end += len(v[key].split("\n"))
    sheet_path.write_text("\n".join(lines), encoding="utf-8")


def done(answer="an answer", searched="y", mode="gpt-5-thinking", links=""):
    return {"answer": answer, "searched": searched, "mode": mode, "links": links}


HEADER = ("Sam", "out", "Montreal")


def ids(question_count=2, repeats=1):
    return [fi.block_id(n, e, r) for n in range(1, question_count + 1) for e in ("chatgpt", "perplexity")
            for r in range(1, repeats + 1)]


def all_done(sheet, question_count=2, repeats=1, **kw):
    fill(sheet, header=HEADER, **{i.replace("-", "_"): done(answer=f"answer for {i}", **kw)
                                  for i in ids(question_count, repeats)})


def rows_of(run_id):
    return query("SELECT query_text, engine, repeat_number, raw_response_text, retrieval_activated, engine_version, "
                 "logged_in_state, location_context, question_set_version, sources_cited FROM probe_results "
                 "WHERE probe_run_id = %s ORDER BY query_text, engine, repeat_number", (run_id,))


# ---- generating ---------------------------------------------------------------

def test_the_sheet_is_a_numbered_file_with_no_latest_copy_and_a_run_in_progress(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    folder = tmp_path / "reports" / biz["domain"] / "old_site"
    assert made.path == folder / "audit_fill_in_run1_2026-10-07.md"
    assert [p.name for p in folder.iterdir()] == ["audit_fill_in_run1_2026-10-07.md"]
    _, second = generate(biz, qfile, tmp_path, day=date(2026, 10, 9))
    assert second.path.name == "audit_fill_in_run2_2026-10-09.md"
    assert made.path.exists()                                        # the first sheet is untouched
    run = query("SELECT status, checkpoint, mode, run_type, gate_check_id FROM probe_runs WHERE id = %s",
                (made.probe_run_id,))[0]
    assert run == ("in_progress", "old_site", "manual", "visibility", None)
    assert query("SELECT details->>'repeats', details->>'capture' FROM client_journal "
                 "WHERE entry_type = 'probe_started' AND details->>'probe_run_id' = %s", (made.probe_run_id,))[0] \
        == ("1", "fill_in_file")


def test_the_header_and_one_block_per_question_engine_and_repeat(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path, repeats=3)
    text = made.path.read_text(encoding="utf-8")
    for needle in (fi.SHEET_MAGIC, f"BUSINESS: {biz['name']}", f"BUSINESS ID: {biz['id']}",
                   f"QUESTION SET: {path.stem}", "CHECKPOINT: old_site", "DATE: 2026-10-07",
                   f"PROBE RUN: {made.probe_run_id}", "REPEATS: 3", fi.OPERATOR_LABEL, fi.LOGGED_LABEL, fi.LOCATION_LABEL):
        assert needle in text
    sheet = parse_sheet(text)
    assert made.blocks == len(sheet.blocks) == 2 * 2 * 3
    assert [b.id for b in sheet.blocks][:4] == ["Q1-chatgpt-r1", "Q1-chatgpt-r2", "Q1-chatgpt-r3", "Q1-perplexity-r1"]
    assert "## Q2 · Perplexity · run 3 of 3 · [Q2-perplexity-r3]" in text
    assert len({b.id for b in sheet.blocks}) == len(sheet.blocks)


def test_a_block_has_exactly_the_layout_in_the_plan():
    assert fi.render_block(3, "chatgpt", 1, 3, "eco-friendly hydration station suppliers for weddings") == (
        "## Q3 · ChatGPT · run 1 of 3 · [Q3-chatgpt-r1]\n\n"
        "QUESTION (copy this):\neco-friendly hydration station suppliers for weddings\n\n"
        "MODE (e.g. free-Instant, Thinking):\n\n\n"
        "SEARCHED THE WEB (y / n / unclear):\n\n\n"
        "ANSWER (paste the full answer below this line):\n\n\n"
        "LINKS IT SHOWED (one per line):\n\n\n"
        "---\n")


def test_generate_refuses_bad_input(biz, qfile, tmp_path):
    with pytest.raises(AuditError, match="no business"):
        generate_fill_in(str(uuid.uuid4()), str(qfile()), checkpoint=None, reports_root=tmp_path)
    with pytest.raises(AuditError, match="1 or more"):
        generate_fill_in(biz["id"], str(qfile()), checkpoint=None, repeats=0, reports_root=tmp_path)
    assert query("SELECT count(*) FROM probe_runs WHERE business_id = %s", (biz["id"],))[0][0] == 0


# ---- the round trip -----------------------------------------------------------

def test_round_trip_every_field_lands_in_the_same_columns_as_terminal_mode(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    fill(made.path, header=("Sam", "OUT", "Montreal"), **{
        "Q1_chatgpt_r1": done("Acme is great. See https://news.example/story today.", "y", "gpt-5-thinking",
                              "https://www.Acme.example/refill\nhttps://third.example/p"),
        "Q1_perplexity_r1": done("perplexity one", "n", ""),
        "Q2_chatgpt_r1": done("chatgpt two", "unclear", "gpt-5-instant"),
        "Q2_perplexity_r1": done("perplexity two", "yes", "sonar", "https://a.example/x"),
    })
    client = FakeAnthropicClient([extraction(("Acme Refill", True, True)), extraction(("Rival", False, False)),
                                  extraction(), extraction(("Acme Refill", True, False))])
    res = import_fill_in(made.path, client=client)

    qv = path.stem
    by_question = lambda rows: sorted(rows, key=lambda r: (r[0], r[1], r[2]))
    assert by_question(rows_of(made.probe_run_id)) == by_question([
        (Q1, "chatgpt", 1, "Acme is great. See https://news.example/story today.", True, "gpt-5-thinking",
         "logged_out", "Montreal", qv,
         [{"url": "https://www.Acme.example/refill", "domain": "acme.example", "origin": "pasted"},
          {"url": "https://third.example/p", "domain": "third.example", "origin": "pasted"},
          {"url": "https://news.example/story", "domain": "news.example", "origin": "in_answer"}]),
        (Q1, "perplexity", 1, "perplexity one", False, None, "logged_out", "Montreal", qv, []),
        (Q2, "chatgpt", 1, "chatgpt two", None, "gpt-5-instant", "logged_out", "Montreal", qv, []),     # unclear -> NULL
        (Q2, "perplexity", 1, "perplexity two", True, "sonar", "logged_out", "Montreal", qv,
         [{"url": "https://a.example/x", "domain": "a.example", "origin": "pasted"}]),
    ])
    assert res.saved == 4 and res.complete and res.already_saved == 0
    assert query("SELECT status, completed_at IS NOT NULL FROM probe_runs WHERE id = %s", (made.probe_run_id,))[0] \
        == ("complete", True)
    types = sorted(r[0] for r in query("SELECT entry_type FROM client_journal WHERE business_id = %s AND "
                                       "details->>'probe_run_id' = %s", (biz["id"], made.probe_run_id)))
    assert types == ["note", "probe_completed", "probe_started"]
    note = query("SELECT details FROM client_journal WHERE entry_type = 'note' AND details->>'probe_run_id' = %s",
                 (made.probe_run_id,))[0][0]
    assert note["operator"] == "Sam" and len(note["blocks_saved"]) == 4
    # Extraction ran, with the same derived columns as terminal mode.
    assert res.counts == {"answers": 4, "named_business": 2, "recommended_business": 1, "not_analysed": 0}
    assert res.summary["client_named"] == 2 and len(client.messages.calls) == 4


def test_a_pasted_answer_with_blank_lines_markdown_rules_and_label_words_comes_through_intact(biz, qfile, tmp_path):
    tricky = "\n".join([
        "QUESTION: here is the word QUESTION and the word ANSWER in capitals.",
        "",
        "",
        "## Top picks",
        "---",
        "| name | city |",
        "|------|------|",
        "- **Acme**   (trailing spaces below)   ",
        "    indented code",
        "",
        fi.Q_LABEL,                      # even an exact copy of a label line
        fi.ANSWER_LABEL,
        fi.LINKS_LABEL,
        "## Q9 · not a real heading",
        "---",
        "final line",
    ])
    path, made = generate(biz, qfile, tmp_path, questions=(Q1,))
    fill(made.path, header=HEADER, Q1_chatgpt_r1=done(tricky, links="https://a.example/x"),
         Q1_perplexity_r1=done("plain"))
    import_fill_in(made.path, client=FakeAnthropicClient([extraction(), extraction()]))
    stored = query("SELECT raw_response_text, sources_cited FROM probe_results WHERE probe_run_id = %s AND "
                   "engine = 'chatgpt'", (made.probe_run_id,))[0]
    assert stored[0] == tricky
    assert [s["url"] for s in stored[1]] == ["https://a.example/x"]      # the links box was not confused either


def test_answers_are_matched_to_blocks_by_id_not_by_position(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path, questions=(Q1,))
    fill(made.path, header=HEADER, Q1_chatgpt_r1=done("for chatgpt"), Q1_perplexity_r1=done("for perplexity"))
    text = made.path.read_text(encoding="utf-8")
    first, second = text.index("## Q1 · ChatGPT"), text.index("## Q1 · Perplexity")
    head, chat, perp = text[:first], text[first:second], text[second:]
    made.path.write_text(head + perp.rstrip("\n") + "\n\n" + chat, encoding="utf-8")        # swapped on disk
    import_fill_in(made.path, client=FakeAnthropicClient([extraction(), extraction()]))
    assert [(r[1], r[3]) for r in rows_of(made.probe_run_id)] == [("chatgpt", "for chatgpt"),
                                                                   ("perplexity", "for perplexity")]


# ---- all or nothing, and what is missing --------------------------------------

def test_default_import_lists_every_missing_item_and_saves_nothing(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    fill(made.path, header=("", "sideways", ""), **{
        "Q1_chatgpt_r1": done("fine"),
        "Q1_perplexity_r1": {"answer": "", "searched": "y", "mode": "", "links": ""},                 # no answer
        "Q2_chatgpt_r1": {"answer": "an answer", "searched": "", "mode": "", "links": ""},             # not said
        "Q2_perplexity_r1": {"answer": "x", "searched": "maybe", "mode": "", "links": "ftp://bad.example/x"},
    })
    with pytest.raises(ImportRefused) as err:
        import_fill_in(made.path, client=FakeAnthropicClient([]))
    problems = "\n".join(err.value.problems)
    assert f"header: {fi.OPERATOR_LABEL} is empty" in problems
    assert "must be 'out' or 'in' (got 'sideways')" in problems
    assert "Q1-perplexity-r1: ANSWER is empty" in problems
    assert "Q2-chatgpt-r1: SEARCHED THE WEB is empty" in problems
    assert "Q2-perplexity-r1: SEARCHED THE WEB must be y, n or unclear (got 'maybe')" in problems
    assert "Q2-perplexity-r1: LINKS:" in problems and "ftp://bad.example/x" in problems
    assert "Q1-chatgpt-r1" not in problems                                  # the finished block is not blamed
    assert rows_of(made.probe_run_id) == []
    assert query("SELECT status FROM probe_runs WHERE id = %s", (made.probe_run_id,))[0][0] == "in_progress"


def test_a_fully_blank_sheet_lists_every_block(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    with pytest.raises(ImportRefused) as err:
        import_fill_in(made.path)
    text = "\n".join(err.value.problems)
    assert all(f"{i}: ANSWER is empty" in text and f"{i}: SEARCHED THE WEB is empty" in text for i in ids())


def test_even_with_partial_a_missing_header_stops_everything(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    fill(made.path, Q1_chatgpt_r1=done())                                  # no header at all
    with pytest.raises(ImportRefused, match="problem"):
        import_fill_in(made.path, partial=True)
    assert rows_of(made.probe_run_id) == []


def test_a_damaged_block_is_reported_not_guessed_at(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path, questions=(Q1,))
    fill(made.path, header=HEADER, Q1_chatgpt_r1=done(), Q1_perplexity_r1=done())
    text = made.path.read_text(encoding="utf-8").replace(fi.SEARCH_LABEL + "\ny\n", "", 1)
    made.path.write_text(text, encoding="utf-8")
    with pytest.raises(ImportRefused) as err:
        import_fill_in(made.path)
    assert any(p.startswith("Q1-chatgpt-r1") and "label" in p for p in err.value.problems)


# ---- partial, resume, split ----------------------------------------------------

def test_partial_saves_only_complete_blocks_and_a_later_import_saves_the_rest_without_duplicates(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    fill(made.path, header=HEADER, Q1_chatgpt_r1=done("one"), Q1_perplexity_r1=done("two"),
         Q2_chatgpt_r1={"answer": "half", "searched": "", "mode": "", "links": ""})        # unfinished
    client = FakeAnthropicClient([extraction(), extraction(), extraction(), extraction()])

    first = import_fill_in(made.path, partial=True, client=client)
    assert (first.saved, first.complete, first.saved_total, first.expected_total) == (2, False, 2, 4)
    assert any("Q2-chatgpt-r1: SEARCHED THE WEB is empty" in p for p in first.unfinished)
    assert any("Q2-perplexity-r1: ANSWER is empty" in p for p in first.unfinished)
    assert query("SELECT status FROM probe_runs WHERE id = %s", (made.probe_run_id,))[0][0] == "in_progress"
    assert len(client.messages.calls) == 2

    fill(made.path, Q2_chatgpt_r1={"searched": "n"}, Q2_perplexity_r1=done("four"))      # finish the same file
    second = import_fill_in(made.path, client=client)                                    # default mode now
    assert (second.saved, second.already_saved, second.complete) == (2, 2, True)
    assert len(rows_of(made.probe_run_id)) == 4                                          # no duplicates
    assert len(client.messages.calls) == 4                                               # only the 2 new rows were read
    assert query("SELECT status FROM probe_runs WHERE id = %s", (made.probe_run_id,))[0][0] == "complete"

    third = import_fill_in(made.path, client=client)                                     # importing again changes nothing
    assert (third.saved, third.already_saved) == (0, 4) and len(rows_of(made.probe_run_id)) == 4
    assert len(client.messages.calls) == 4
    assert query("SELECT count(*) FROM client_journal WHERE entry_type = 'probe_completed' AND "
                 "details->>'probe_run_id' = %s", (made.probe_run_id,))[0][0] == 1


def test_two_people_can_each_import_their_own_copy(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    copy_a, copy_b = made.path.with_name("a.md"), made.path.with_name("b.md")
    for c in (copy_a, copy_b):
        c.write_text(made.path.read_text(encoding="utf-8"), encoding="utf-8")
    fill(copy_a, header=("Ann", "out", "Montreal"), Q1_chatgpt_r1=done("a1"), Q1_perplexity_r1=done("a2"))
    fill(copy_b, header=("Bob", "in", ""), Q2_chatgpt_r1=done("b1"), Q2_perplexity_r1=done("b2"))
    client = FakeAnthropicClient([extraction()] * 4)
    import_fill_in(copy_a, partial=True, client=client)
    res = import_fill_in(copy_b, partial=True, client=client)
    assert res.complete and res.saved == 4 - 2
    got = {(r[3], r[6], r[7]) for r in rows_of(made.probe_run_id)}
    assert got == {("a1", "logged_out", "Montreal"), ("a2", "logged_out", "Montreal"),
                   ("b1", "logged_in", None), ("b2", "logged_in", None)}      # each row keeps its own file's header
    operators = {r[0] for r in query("SELECT details->>'operator' FROM client_journal WHERE entry_type = 'note' "
                                     "AND details->>'probe_run_id' = %s", (made.probe_run_id,))}
    assert operators == {"Ann", "Bob"}


def test_the_default_import_of_a_second_copy_ignores_blocks_already_saved(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path, questions=(Q1,))
    copy_b = made.path.with_name("b.md")
    copy_b.write_text(made.path.read_text(encoding="utf-8"), encoding="utf-8")
    fill(made.path, header=HEADER, Q1_chatgpt_r1=done("a"))
    import_fill_in(made.path, partial=True, client=FakeAnthropicClient([extraction()]))
    fill(copy_b, header=("Bob", "out", ""), Q1_perplexity_r1=done("b"))     # its chatgpt box is empty: already saved elsewhere
    res = import_fill_in(copy_b, client=FakeAnthropicClient([extraction()]))
    assert res.complete and res.saved == 1 and res.already_saved == 1


# ---- the frozen question set --------------------------------------------------

def test_an_edited_question_in_the_sheet_is_refused_in_both_modes_and_nothing_is_saved(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    all_done(made.path)
    made.path.write_text(made.path.read_text(encoding="utf-8").replace(f"\n{Q2}\n", f"\n{Q2} in Ontario\n", 1),
                         encoding="utf-8")
    for partial in (False, True):
        with pytest.raises(ImportRefused) as err:
            import_fill_in(made.path, partial=partial)
        assert any("question text was changed" in p and Q2 in p for p in err.value.problems)
    assert rows_of(made.probe_run_id) == []


def test_a_question_set_edited_after_the_sheet_was_made_is_refused(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    all_done(made.path)
    path.write_text(f'questions:\n  - "{Q1}"\n  - "{Q2} (reworded)"\n')
    with pytest.raises(AuditError, match="no longer matches"):
        import_fill_in(made.path)
    assert rows_of(made.probe_run_id) == []


def test_a_block_that_is_not_part_of_this_audit_is_refused(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path)
    all_done(made.path)
    made.path.write_text(made.path.read_text(encoding="utf-8").replace(
        "· [Q2-perplexity-r1]", "· [Q7-perplexity-r1]"), encoding="utf-8")
    with pytest.raises(ImportRefused) as err:
        import_fill_in(made.path)
    assert any("Q7-perplexity-r1" in p and "not part of this audit" in p for p in err.value.problems)


def test_a_file_from_another_database_or_a_random_file_is_refused(tmp_path):
    random = tmp_path / "x.md"
    random.write_text("hello")
    with pytest.raises(AuditError, match="not an audit fill-in sheet"):
        import_fill_in(random)
    with pytest.raises(AuditError, match="not found"):
        import_fill_in(tmp_path / "nope.md")


# ---- repeats ------------------------------------------------------------------

def test_repeat_numbers_come_from_the_blocks_even_when_filled_out_of_order(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path, questions=(Q1,), repeats=3)
    fill(made.path, header=HEADER, Q1_chatgpt_r2=done("second"))
    client = FakeAnthropicClient([extraction()] * 6)
    import_fill_in(made.path, partial=True, client=client)
    assert [(r[1], r[2], r[3]) for r in rows_of(made.probe_run_id)] == [("chatgpt", 2, "second")]   # 2, not 1
    fill(made.path, **{i.replace("-", "_"): done(f"ans {i}") for i in ids(1, 3) if i != "Q1-chatgpt-r2"})
    res = import_fill_in(made.path, client=client)
    assert res.complete and res.saved == 5
    got = [(r[1], r[2]) for r in rows_of(made.probe_run_id)]
    assert got == [("chatgpt", 1), ("chatgpt", 2), ("chatgpt", 3), ("perplexity", 1), ("perplexity", 2), ("perplexity", 3)]


def test_the_run_is_not_complete_until_every_repeat_is_saved(biz, qfile, tmp_path):
    path, made = generate(biz, qfile, tmp_path, questions=(Q1,), repeats=2)
    fill(made.path, header=HEADER, **{i.replace("-", "_"): done() for i in ids(1, 2)[:3]})
    res = import_fill_in(made.path, partial=True, client=FakeAnthropicClient([extraction()] * 3))
    assert not res.complete and (res.saved_total, res.expected_total) == (3, 4)


# ---- terminal mode still works, and the CLI -----------------------------------

def test_terminal_mode_still_counts_repeats_itself(biz, qfile):
    from audit import store
    from core.db import get_connection
    conn = get_connection()
    try:
        profile = store.load_business_and_profile(conn, biz["id"])
        run_id = store.create_run(conn, business=profile, checkpoint=None)
        kw = dict(probe_run_id=run_id, question=Q1, engine="chatgpt", retrieval_activated=None, engine_version=None,
                  logged_in_state="logged_out", location_context=None, question_set_version="x_v1")
        assert [store.add_result(conn, raw_answer="a", **kw)[1], store.add_result(conn, raw_answer="b", **kw)[1]] == [1, 2]
    finally:
        conn.close()


def test_cli_generate_then_import_partial_then_import(biz, qfile, tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "REPORTS_ROOT", tmp_path / "reports")
    path = qfile(questions=(Q1,))
    made = CliRunner().invoke(app, ["audit", "--business", biz["id"], "--question-set", str(path),
                                    "--fill-in", "--checkpoint", "old_site", "--repeats", "2"])
    assert made.exit_code == 0, made.output
    sheet = next((tmp_path / "reports").rglob("audit_fill_in_run1_*.md"))
    assert "Fill-in file written (4 blocks)" in made.output and f"aeo audit import {sheet}" in made.output

    fake = FakeAnthropicClient([extraction(("Acme Refill", True, True)), extraction(("Rival", False, False)),
                                extraction(), extraction()])
    monkeypatch.setattr("core.llm.anthropic.Anthropic", lambda: fake)

    refused = CliRunner().invoke(app, ["audit", "import", str(sheet)])
    assert refused.exit_code == 1 and "Nothing was saved" in refused.output
    assert "header:" in refused.output and fake.messages.calls == []

    fill(sheet, header=HEADER, Q1_chatgpt_r1=done("one"), Q1_chatgpt_r2=done("two"))
    part = CliRunner().invoke(app, ["audit", "import", str(sheet), "--partial"])
    assert part.exit_code == 0, part.output
    assert "Saved 2 new answer(s)." in part.output and "Audit not complete yet: 2 of 4" in part.output
    assert "Answers collected: 2" in part.output and "Named once, usually 1st" in part.output

    fill(sheet, Q1_perplexity_r1=done("three"), Q1_perplexity_r2=done("four"))
    full = CliRunner().invoke(app, ["audit", "import", str(sheet)])
    assert full.exit_code == 0, full.output
    assert "Saved 2 new answer(s). 2 block(s) were already saved" in full.output
    assert "Audit complete: every answer is saved." in full.output and "Answers collected: 4" in full.output


def test_cli_repeats_needs_fill_in_and_the_required_options(biz, qfile):
    r = CliRunner().invoke(app, ["audit", "--business", biz["id"], "--question-set", str(qfile()), "--repeats", "3"])
    assert r.exit_code == 1 and "--fill-in" in r.output
    r = CliRunner().invoke(app, ["audit", "--business", biz["id"]])
    assert r.exit_code == 1 and "--question-set" in r.output
