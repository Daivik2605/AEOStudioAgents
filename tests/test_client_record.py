"""Business lookup, engagement, evidence, the work log, events and the timeline (PLAN.md sections 2 and 10)."""

from __future__ import annotations

import getpass
import uuid
from datetime import date, datetime, timezone

import psycopg
import pytest
from typer.testing import CliRunner

from aeo import cli
from audit import store
from core import client_files, evidence as ev, operator as operator_module, timeline as tl
from core.businesses import (BusinessError, get_engagement, resolve_business, set_engagement)
from core.checkpoints import CheckpointError
from core.db import get_connection
from core.operator import operator_name


# ---- helpers ------------------------------------------------------------------------------------------------------

def make_business(name: str | None = None) -> dict:
    conn = get_connection()
    try:
        domain = f"rec-{uuid.uuid4().hex[:12]}.example"
        bid = conn.execute("INSERT INTO businesses (name, domain, website_url) VALUES (%s, %s, %s) RETURNING id",
                           (name, domain, f"https://{domain}")).fetchone()[0]
        return {"id": str(bid), "name": name, "domain": domain}
    finally:
        conn.close()


@pytest.fixture
def biz():
    return make_business(f"Acme {uuid.uuid4().hex[:8]}")


@pytest.fixture
def conn():
    c = get_connection()
    yield c
    c.close()


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def a_file(tmp_path, name="report.pdf", content=b"%PDF-1.4 fake"):
    p = tmp_path / "incoming" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(content)
    return p


def add(conn, biz, file, **kw):
    kw.setdefault("checkpoint", "before")
    kw.setdefault("note", "what we understood")
    kw.setdefault("operator", "Sam")
    return ev.add_evidence(conn, business=biz, file=file, **kw)


# ---- finding the business -------------------------------------------------------------------------------------------

def test_a_business_is_found_by_id_by_domain_and_by_name(conn, biz):
    for ref in (biz["id"], biz["domain"], f"https://www.{biz['domain']}/about", biz["name"], biz["name"].upper()):
        assert resolve_business(conn, ref)["id"] == biz["id"]


def test_two_businesses_with_the_same_name_are_refused_not_guessed(conn):
    name = f"Twin {uuid.uuid4().hex[:8]}"
    a, b = make_business(name), make_business(name)
    with pytest.raises(BusinessError, match="more than one business") as caught:
        resolve_business(conn, name)
    assert a["id"] in str(caught.value) and b["id"] in str(caught.value)
    assert resolve_business(conn, a["id"])["id"] == a["id"]            # the id still picks one
    assert resolve_business(conn, b["domain"])["id"] == b["id"]        # and so does the domain


def test_a_name_that_matches_one_business_and_a_domain_that_matches_another_is_refused(conn):
    other = make_business()
    clash = make_business(other["domain"])                            # named exactly like the other one's domain
    with pytest.raises(BusinessError, match="more than one"):
        resolve_business(conn, other["domain"])
    assert clash["id"] != other["id"]


@pytest.mark.parametrize("ref", ["", "   ", "no-such-business-anywhere", "00000000-0000-0000-0000-000000000000"])
def test_nothing_found_is_an_error(conn, ref):
    with pytest.raises(BusinessError):
        resolve_business(conn, ref)


# ---- engagement ---------------------------------------------------------------------------------------------------------

def test_a_new_business_has_no_engagement_recorded(conn, biz):
    assert get_engagement(conn, biz["id"]) == {"engagement_type": None, "access_level": None}


def test_every_engagement_type_and_access_level_can_be_set(conn):
    for t in ("aeo_only", "web_dev_aeo", "web_dev_only", "migration_pitch", "prospect"):
        for a in ("none", "cms", "hosting", "full"):
            b = make_business()
            assert set_engagement(conn, b["id"], engagement_type=t, access_level=a, actor="Sam")["changed"]
            assert get_engagement(conn, b["id"]) == {"engagement_type": t, "access_level": a}


def test_setting_one_leaves_the_other_alone(conn, biz):
    set_engagement(conn, biz["id"], engagement_type="aeo_only", access_level=None, actor="Sam")
    set_engagement(conn, biz["id"], engagement_type=None, access_level="cms", actor="Sam")
    assert get_engagement(conn, biz["id"]) == {"engagement_type": "aeo_only", "access_level": "cms"}


def test_bad_values_are_refused_by_the_code_and_by_the_database(conn, biz):
    with pytest.raises(BusinessError, match="not an engagement type"):
        set_engagement(conn, biz["id"], engagement_type="F", access_level=None, actor="Sam")
    with pytest.raises(BusinessError, match="not an access level"):
        set_engagement(conn, biz["id"], engagement_type=None, access_level="root", actor="Sam")
    with pytest.raises(BusinessError, match="nothing to set"):
        set_engagement(conn, biz["id"], engagement_type=None, access_level=None, actor="Sam")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE businesses SET engagement_type = 'type_f' WHERE id = %s", (biz["id"],))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE businesses SET access_level = 'admin' WHERE id = %s", (biz["id"],))
    assert get_engagement(conn, biz["id"]) == {"engagement_type": None, "access_level": None}


def test_each_change_writes_a_journal_entry_and_no_change_writes_none(conn, biz):
    def notes():
        return rows(conn, "SELECT description, actor, details FROM client_journal "
                          "WHERE business_id = %s AND entry_type = 'note' ORDER BY created_at", biz["id"])
    set_engagement(conn, biz["id"], engagement_type="web_dev_aeo", access_level="full", actor="Sam")
    ((description, actor, details),) = notes()
    assert actor == "Sam" and "type web_dev_aeo (was not set)" in description and "access full (was not set)" in description
    assert details["engagement_type"] == {"from": None, "to": "web_dev_aeo"}
    again = set_engagement(conn, biz["id"], engagement_type="web_dev_aeo", access_level="full", actor="Sam")
    assert again["changed"] is False and len(notes()) == 1
    set_engagement(conn, biz["id"], engagement_type="aeo_only", access_level=None, actor="Sam")
    assert len(notes()) == 2 and notes()[1][2]["engagement_type"] == {"from": "web_dev_aeo", "to": "aeo_only"}


# ---- who is doing the work ------------------------------------------------------------------------------------------------

def test_the_operator_is_aeo_operator_or_the_system_user(monkeypatch):
    monkeypatch.setattr(operator_module, "_load_env_file", lambda path: None)
    monkeypatch.setenv("AEO_OPERATOR", "Anikait")
    assert operator_name() == "Anikait"
    monkeypatch.setenv("AEO_OPERATOR", "   ")
    assert operator_name() == getpass.getuser()
    monkeypatch.delenv("AEO_OPERATOR")
    assert operator_name() == getpass.getuser()


# ---- evidence -----------------------------------------------------------------------------------------------------------------

def test_evidence_is_copied_into_the_client_folder_and_every_field_is_recorded(conn, biz, tmp_path):
    src = a_file(tmp_path, "Cloudflare AI report.pdf")
    done = add(conn, biz, src, checkpoint="after_aeo", note="GPTBot crawled 40 times", evidence_type="cdn_report",
               source="client", captured="2026-10-01", period_start="2026-09-01", period_end="2026-09-30",
               finding="CRAWLER_BLOCKED_GPTBOT", operator="Anikait", today=date(2026, 10, 14))
    stored = client_files.CLIENTS_ROOT / biz["domain"] / "evidence" / "2026-10-01_Cloudflare-AI-report.pdf"
    assert done["stored_at"] == stored and stored.read_bytes() == src.read_bytes()
    assert src.exists()                                              # the original is left where it was
    (row,) = rows(conn, "SELECT file_path, evidence_type, source, captured_on, period_start, period_end, checkpoint, "
                        "note, finding_code, client_visible, operator, created_at FROM evidence WHERE business_id = %s",
                  biz["id"])
    assert row[:11] == ("evidence/2026-10-01_Cloudflare-AI-report.pdf", "cdn_report", "client", date(2026, 10, 1),
                        date(2026, 9, 1), date(2026, 9, 30), "after_aeo", "GPTBot crawled 40 times",
                        "CRAWLER_BLOCKED_GPTBOT", True, "Anikait")
    assert row[11] is not None


def test_dates_default_sensibly_and_unknowns_stay_empty(conn, biz, tmp_path):
    done = add(conn, biz, a_file(tmp_path, "shot.png"), today=date(2026, 10, 14))
    assert done["file_path"] == "evidence/2026-10-14_shot.png"        # no captured date: today names the file
    (row,) = rows(conn, "SELECT evidence_type, source, captured_on, period_start, period_end, finding_code, "
                        "client_visible FROM evidence WHERE business_id = %s", biz["id"])
    assert row == (None, None, None, None, None, None, True)           # nothing is invented; visible by default


def test_internal_evidence_is_not_client_visible(conn, biz, tmp_path):
    add(conn, biz, a_file(tmp_path, "a.png"), internal=True)
    add(conn, biz, a_file(tmp_path, "b.png"))
    assert rows(conn, "SELECT client_visible FROM evidence WHERE business_id = %s ORDER BY created_at, id",
                biz["id"]) == [(False,), (True,)]


def test_the_same_file_added_twice_is_kept_twice_not_overwritten(conn, biz, tmp_path):
    src = a_file(tmp_path, "shot.png", b"first")
    one = add(conn, biz, src, today=date(2026, 10, 14))
    src.write_bytes(b"second")
    two = add(conn, biz, src, today=date(2026, 10, 14))
    assert (one["file_path"], two["file_path"]) == ("evidence/2026-10-14_shot.png", "evidence/2026-10-14_shot_2.png")
    assert one["stored_at"].read_bytes() == b"first" and two["stored_at"].read_bytes() == b"second"


@pytest.mark.parametrize("bad", ["old_site", "q1", "BEFORE", ""])
def test_a_bad_checkpoint_is_refused_and_nothing_is_copied(conn, biz, tmp_path, bad):
    with pytest.raises(CheckpointError, match="Valid labels"):
        add(conn, biz, a_file(tmp_path), checkpoint=bad)
    assert not (client_files.CLIENTS_ROOT / biz["domain"]).exists()
    assert rows(conn, "SELECT count(*) FROM evidence WHERE business_id = %s", biz["id"]) == [(0,)]


def test_a_checkpoint_is_required_for_evidence(conn, biz, tmp_path):
    with pytest.raises(CheckpointError, match="required"):
        add(conn, biz, a_file(tmp_path), checkpoint=None)


def test_finding_codes_are_checked_against_the_registry(conn, biz, tmp_path):
    from diagnose.codes import REGISTRY
    with pytest.raises(ev.EvidenceError, match="not a finding code"):
        add(conn, biz, a_file(tmp_path), finding="MADE_UP_CODE")
    with pytest.raises(ev.EvidenceError, match="not a finding code"):
        add(conn, biz, a_file(tmp_path), finding="llms_txt_missing")        # codes are exact
    assert not (client_files.CLIENTS_ROOT / biz["domain"]).exists()          # refused before copying
    for code in ("LLMS_TXT_MISSING", "RENDER_H1_MISSING", next(iter(REGISTRY))):
        assert add(conn, biz, a_file(tmp_path), finding=code)["id"]


@pytest.mark.parametrize("kw, message", [
    ({"source": "nobody"}, "--from must be one of: us, client"),
    ({"captured": "14/10/2026"}, "--captured must be a date"),
    ({"period_start": "2026-09-01"}, "together"),
    ({"period_end": "2026-09-30"}, "together"),
    ({"period_start": "2026-09-30", "period_end": "2026-09-01"}, "before"),
])
def test_bad_fields_are_refused_before_anything_is_copied(conn, biz, tmp_path, kw, message):
    with pytest.raises(ev.EvidenceError, match=message):
        add(conn, biz, a_file(tmp_path), **kw)
    assert not (client_files.CLIENTS_ROOT / biz["domain"]).exists()


def test_a_missing_file_is_refused(conn, biz, tmp_path):
    with pytest.raises(ev.EvidenceError, match="file not found"):
        add(conn, biz, tmp_path / "nope.pdf")


def test_the_database_refuses_a_bad_checkpoint_or_source_even_if_the_code_is_bypassed(conn, biz):
    insert = ("INSERT INTO evidence (business_id, file_path, checkpoint, source, operator) "
              "VALUES (%s, 'evidence/x', %s, %s, 'Sam')")
    conn.execute(insert, (biz["id"], "before", "us"))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(insert, (biz["id"], "old_site", "us"))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(insert, (biz["id"], "before", "somebody"))


def test_evidence_list_filters_by_checkpoint(conn, biz, tmp_path):
    add(conn, biz, a_file(tmp_path, "a.png"), checkpoint="before")
    add(conn, biz, a_file(tmp_path, "b.png"), checkpoint="after_aeo")
    assert [r["checkpoint"] for r in ev.list_evidence(conn, biz["id"])] == ["before", "after_aeo"]
    assert [r["checkpoint"] for r in ev.list_evidence(conn, biz["id"], "after_aeo")] == ["after_aeo"]
    with pytest.raises(CheckpointError):
        ev.list_evidence(conn, biz["id"], "q1")


# ---- work log and events -------------------------------------------------------------------------------------------------

def journal(conn, biz, entry_type):
    return rows(conn, "SELECT description, actor, details, related_table, related_id FROM client_journal "
                      "WHERE business_id = %s AND entry_type = %s ORDER BY created_at", biz["id"], entry_type)


def test_a_work_entry_is_a_journal_row_of_type_work(conn, biz):
    ev.add_work(conn, business=biz, text="  Rewrote the homepage H1  ", operator="Anikait",
                finding="RENDER_H1_MISSING", checkpoint="after_aeo")
    ((description, actor, details, table, related),) = journal(conn, biz, "work")
    assert (description, actor) == ("Rewrote the homepage H1", "Anikait")
    assert details == {"finding": "RENDER_H1_MISSING", "checkpoint": "after_aeo", "file": None} and table is None


def test_an_event_is_a_journal_row_of_type_event_and_not_work(conn, biz):
    ev.add_event(conn, business=biz, text="Client switched hosting provider", operator="Sam")
    ((description, actor, *_),) = journal(conn, biz, "event")
    assert (description, actor) == ("Client switched hosting provider", "Sam")
    assert journal(conn, biz, "work") == []


def test_work_and_events_need_some_text_and_a_valid_code_and_label(conn, biz):
    for call in (lambda: ev.add_work(conn, business=biz, text="  ", operator="Sam"),
                 lambda: ev.add_event(conn, business=biz, text="", operator="Sam")):
        with pytest.raises(ev.EvidenceError, match="empty"):
            call()
    with pytest.raises(ev.EvidenceError, match="not a finding code"):
        ev.add_work(conn, business=biz, text="x", operator="Sam", finding="NOPE")
    with pytest.raises(CheckpointError):
        ev.add_work(conn, business=biz, text="x", operator="Sam", checkpoint="q1")
    assert journal(conn, biz, "work") == []


def test_a_file_on_a_work_entry_is_stored_as_evidence(conn, biz, tmp_path):
    done = ev.add_work(conn, business=biz, text="Uploaded the new robots.txt", operator="Sam",
                       checkpoint="after_aeo", finding="ROBOTS_DISALLOWS_OAI_SEARCHBOT",
                       file=a_file(tmp_path, "robots.txt", b"User-agent: *"), today=date(2026, 10, 14))
    stored = client_files.CLIENTS_ROOT / biz["domain"] / "evidence" / "2026-10-14_robots.txt"
    assert stored.read_bytes() == b"User-agent: *" and done["evidence"]["file_path"] == "evidence/2026-10-14_robots.txt"
    assert rows(conn, "SELECT note FROM evidence WHERE business_id = %s", biz["id"]) == [("Uploaded the new robots.txt",)]
    ((_, _, details, table, related),) = journal(conn, biz, "work")
    assert details["file"] == "evidence/2026-10-14_robots.txt" and table == "evidence" and str(related) == done["evidence"]["id"]


def test_a_file_without_a_checkpoint_is_refused_and_nothing_is_written(conn, biz, tmp_path):
    with pytest.raises(ev.EvidenceError, match="needs a checkpoint"):
        ev.add_work(conn, business=biz, text="x", operator="Sam", file=a_file(tmp_path))
    assert journal(conn, biz, "work") == [] and not (client_files.CLIENTS_ROOT / biz["domain"]).exists()


# ---- the timeline -------------------------------------------------------------------------------------------------------------

def at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=timezone.utc)


@pytest.fixture
def story(conn, tmp_path):
    """One business with one of everything, at known times that are deliberately NOT in the order they were created."""
    biz = make_business(f"Story {uuid.uuid4().hex[:8]}")
    conn.execute("INSERT INTO business_profiles (business_id, version, source) VALUES (%s, 1, 'client')", (biz["id"],))
    profile = store.load_business_and_profile(conn, biz["id"])

    work = ev.add_work(conn, business=biz, text="Fixed the H1", operator="Anikait", finding="RENDER_H1_MISSING",
                       checkpoint="after_aeo")
    event = ev.add_event(conn, business=biz, text="Client edited their own homepage", operator="Sam")
    evidence = add(conn, biz, a_file(tmp_path, "cdn.pdf"), checkpoint="before", note="AI crawler report",
                   evidence_type="cdn_report", source="client", operator="Sam")
    internal = add(conn, biz, a_file(tmp_path, "memo.txt"), checkpoint="before", note="our private memo", internal=True)
    conn.execute("UPDATE businesses SET status = 'client' WHERE id = %s", (biz["id"],))        # the trigger logs it
    run = store.create_run(conn, business=profile, checkpoint="before")
    diag = conn.execute("INSERT INTO diagnoses (business_id, checkpoint, url, verdict, platform, findings) "
                        "VALUES (%s, 'before', 'https://x.example', 'conditional', 'wordpress', '[{}, {}]'::jsonb) "
                        "RETURNING id", (biz["id"],)).fetchone()[0]
    status_row = conn.execute("SELECT id FROM client_journal WHERE business_id = %s AND entry_type = 'status_change'",
                              (biz["id"],)).fetchone()[0]

    # Fix every time, so the expected order is known: diagnosis, audit, evidence, internal evidence, status, work, event.
    conn.execute("UPDATE diagnoses SET checked_at = %s WHERE id = %s", (at(1), diag))
    conn.execute("UPDATE probe_runs SET started_at = %s WHERE id = %s", (at(2), run))
    conn.execute("UPDATE evidence SET created_at = %s WHERE id = %s", (at(3), evidence["id"]))
    conn.execute("UPDATE evidence SET created_at = %s WHERE id = %s", (at(4), internal["id"]))
    conn.execute("UPDATE client_journal SET created_at = %s WHERE id = %s", (at(5), status_row))
    conn.execute("UPDATE client_journal SET created_at = %s WHERE id = %s", (at(6), work["id"]))
    conn.execute("UPDATE client_journal SET created_at = %s WHERE id = %s", (at(7), event["id"]))
    return biz


def test_the_timeline_lists_everything_in_date_order_with_its_label(conn, story):
    items = tl.collect_timeline(conn, story["id"])
    assert [(i["kind"], i["label"]) for i in items] == [
        ("diagnosis", "before"), ("audit", "before"), ("evidence", "before"), ("evidence", "before"),
        ("status change", ""), ("work", "after_aeo"), ("event", "")]
    assert [i["when"] for i in items] == sorted(i["when"] for i in items)
    texts = [i["text"] for i in items]
    assert "verdict conditional on wordpress, 2 finding(s)" in texts[0]
    assert "Status changed from prospect to client" in texts[4]
    assert "Fixed the H1 [RENDER_H1_MISSING] (Anikait)" in texts[5]
    assert "Client edited their own homepage (Sam)" in texts[6]
    assert "internal only" in texts[3] and "internal only" not in texts[2]


def test_a_business_with_nothing_has_an_empty_timeline(conn, biz):
    assert tl.collect_timeline(conn, biz["id"]) == []
    assert "Nothing recorded yet." in tl.render_timeline(biz, [])


def test_other_businesses_never_appear(conn, story):
    stranger = make_business()
    ev.add_event(conn, business=stranger, text="not yours", operator="Sam")
    assert all("not yours" not in i["text"] for i in tl.collect_timeline(conn, story["id"]))


def test_the_markdown_files_are_rebuilt_from_the_database_every_time(conn, story):
    items, timeline_path, work_path = tl.regenerate(conn, story)
    folder = client_files.CLIENTS_ROOT / story["domain"]
    assert (timeline_path, work_path) == (folder / "timeline.md", folder / "work_log.md")

    lines = [l for l in timeline_path.read_text().splitlines() if l.startswith("- ")]
    assert len(lines) == len(items) == 7
    for line, item in zip(lines, items):
        assert tl._when(item["when"]) in line and item["kind"] in line and item["text"] in line
    assert "- **2026-10-01 09:00 UTC** · diagnosis · `before` —" in lines[0]
    assert "- **2026-10-06 09:00 UTC** · work · `after_aeo` —" in lines[5]

    work_lines = [l for l in work_path.read_text().splitlines() if l.startswith("- ")]
    assert work_lines == ["- **2026-10-06 09:00 UTC** · Anikait — Fixed the H1 (`after_aeo`; closes `RENDER_H1_MISSING`)"]
    assert "Client edited" not in work_path.read_text()               # an event is not work

    # Same database -> byte-for-byte the same files (no "generated at" stamp).
    first = (timeline_path.read_bytes(), work_path.read_bytes())
    tl.regenerate(conn, story)
    assert (timeline_path.read_bytes(), work_path.read_bytes()) == first

    # Change the database and the files follow; hand edits are not kept.
    timeline_path.write_text("someone typed here")
    ev.add_work(conn, business=story, text="Added the llms.txt", operator="Sam")
    items2, *_ = tl.regenerate(conn, story)
    assert len(items2) == 8 and "someone typed here" not in timeline_path.read_text()
    assert "Added the llms.txt" in timeline_path.read_text() and "Added the llms.txt" in work_path.read_text()


# ---- the commands -------------------------------------------------------------------------------------------------------------

def run(*args, **kw):
    return CliRunner().invoke(cli.app, list(args), **kw)


def test_cli_engagement_sets_shows_and_refuses(conn, biz):
    ok = run("engagement", "--business", biz["domain"], "--type", "aeo_only", "--access", "cms")
    assert ok.exit_code == 0 and "type aeo_only, access cms" in ok.output
    assert "type aeo_only, access cms" in run("engagement", "--business", biz["name"]).output       # no options: show
    assert "nothing changed" in run("engagement", "--business", biz["id"], "--type", "aeo_only").output
    bad = run("engagement", "--business", biz["id"], "--type", "F")
    assert bad.exit_code == 1 and "not an engagement type" in bad.output


def test_cli_an_ambiguous_name_is_refused(conn):
    name = f"Dup {uuid.uuid4().hex[:8]}"
    make_business(name), make_business(name)
    result = run("engagement", "--business", name, "--type", "aeo_only")
    assert result.exit_code == 1 and "more than one business" in result.output


def test_cli_evidence_add_and_list(biz, tmp_path, monkeypatch):
    monkeypatch.setenv("AEO_OPERATOR", "Anikait")
    src = a_file(tmp_path, "email.txt", b"approved")
    added = run("evidence", "add", str(src), "--business", biz["domain"], "--checkpoint", "after_site",
                "--note", "Client approved the draft", "--type", "client_email", "--from", "client",
                "--captured", "2026-10-10", "--finding", "LLMS_TXT_MISSING", "--internal")
    assert added.exit_code == 0, added.output
    assert "2026-10-10_email.txt" in added.output and "internal" in added.output
    listed = run("evidence", "list", "--business", biz["id"])
    assert "evidence/2026-10-10_email.txt" in listed.output and "INTERNAL" in listed.output
    assert "after_site" in listed.output and "Client approved the draft" in listed.output
    assert "added by Anikait" in listed.output
    assert "No evidence recorded." in run("evidence", "list", "--business", biz["id"], "--checkpoint", "before").output


def test_cli_evidence_add_refuses_a_bad_label_and_a_bad_code(biz, tmp_path):
    src = a_file(tmp_path)
    label = run("evidence", "add", str(src), "--business", biz["id"], "--checkpoint", "old_site", "--note", "n")
    assert label.exit_code == 1 and "Valid labels: before, after_site, after_aeo, check" in label.output
    code = run("evidence", "add", str(src), "--business", biz["id"], "--checkpoint", "before", "--note", "n",
               "--finding", "NOPE")
    assert code.exit_code == 1 and "not a finding code" in code.output
    missing = run("evidence", "add", str(src), "--business", biz["id"], "--note", "n")
    assert missing.exit_code == 1 and "required" in missing.output


def test_cli_log_event_and_timeline_end_to_end(conn, biz, tmp_path):
    assert run("log", "Rewrote the title tag", "--business", biz["domain"], "--finding", "RENDER_H1_MISSING",
               "--checkpoint", "after_aeo").exit_code == 0
    with_file = run("log", "Sent robots.txt", "--business", biz["domain"], "--checkpoint", "after_aeo",
                    "--file", str(a_file(tmp_path, "robots.txt", b"x")))
    assert with_file.exit_code == 0 and "File kept as evidence" in with_file.output
    no_label = run("log", "x", "--business", biz["domain"], "--file", str(a_file(tmp_path, "y.txt")))
    assert no_label.exit_code == 1 and "needs a checkpoint" in no_label.output
    assert run("event", "Client launched a sale", "--business", biz["domain"]).exit_code == 0

    shown = run("timeline", "--business", biz["domain"])
    assert shown.exit_code == 0, shown.output
    out = shown.output
    assert out.index("Rewrote the title tag") < out.index("Sent robots.txt") < out.index("Client launched a sale")
    assert "Written:" in out
    folder = client_files.CLIENTS_ROOT / biz["domain"]
    assert (folder / "timeline.md").read_text().count("\n- ") == 4          # work, work, its evidence file, event
    assert (folder / "work_log.md").read_text().count("\n- ") == 2


def test_cli_diagnose_and_audit_take_a_domain_or_name_and_refuse_a_bad_label(biz, tmp_path):
    bad = run("diagnose", "https://acme.example", "--business", biz["domain"], "--checkpoint", "old_site")
    assert bad.exit_code == 1 and "now called 'before'" in bad.output
    unknown = run("diagnose", "https://acme.example", "--business", "no-such-business-here")
    assert unknown.exit_code == 1 and "no business matches" in unknown.output
    bad_audit = run("audit", "--business", biz["name"], "--question-set", str(tmp_path / "x_v1.yaml"),
                    "--checkpoint", "q1")
    assert bad_audit.exit_code == 1 and "Valid labels" in bad_audit.output
