"""`aeo status` (PLAN.md step 12, section 7): the status of a business, its history, and the migration behind it.
Real Postgres (aeo_test). The migration tests build their own throwaway databases, never touching aeo_test's data."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from typer.testing import CliRunner

from aeo import cli
from audit import store
from core import status as st, timeline as tl
from core.db import get_connection
from tests.test_audit import biz  # noqa: F401  (a real business made through `aeo add`'s own function)

MIGRATIONS = Path(__file__).parent.parent / "db" / "migrations"


@pytest.fixture(autouse=True)
def operator(monkeypatch):
    monkeypatch.setenv("AEO_OPERATOR", "sam")


@pytest.fixture
def conn():
    c = get_connection()
    yield c
    c.close()


def run(*args):
    return CliRunner().invoke(cli.app, ["status", *args])


def history_count(conn, business_id) -> int:
    return conn.execute("SELECT count(*) FROM status_history WHERE business_id = %s", (business_id,)).fetchone()[0]


# ---- default, set and show ------------------------------------------------------------------------------------------

def test_a_new_business_is_a_prospect_with_one_history_row(biz, conn):
    info = st.get_status(conn, biz["id"])
    assert info["status"] == "prospect" and len(info["history"]) == 1
    assert info["history"][0]["set_by"] == "system"
    result = run(biz["domain"])
    assert result.exit_code == 0 and f"{biz['domain']}: prospect" in result.output


def test_set_then_show_the_status_reason_date_and_who(biz, conn):
    result = run(biz["domain"], "--set", "client", "--reason", "deposit paid")
    assert result.exit_code == 0 and "prospect -> client" in result.output
    shown = run(biz["domain"]).output
    assert f"{biz['domain']}: client" in shown and "Reason: deposit paid" in shown and "by sam" in shown and "UTC" in shown
    info = st.get_status(conn, biz["id"])
    assert (info["status"], info["reason"], info["set_by"]) == ("client", "deposit paid", "sam")


def test_the_reason_is_optional_for_client_and_prospect(biz, conn):
    assert run(biz["domain"], "--set", "client").exit_code == 0
    assert run(biz["domain"], "--set", "prospect").exit_code == 0
    assert st.get_status(conn, biz["id"])["reason"] is None


def test_the_reason_is_also_kept_as_a_journal_note_linked_to_the_status_change(biz, conn):
    run(biz["domain"], "--set", "rejected", "--reason", "they chose another agency")
    change, note = conn.execute(
        "SELECT c.id, n.details FROM client_journal c JOIN client_journal n ON n.related_id = c.id "
        "WHERE c.business_id = %s AND c.entry_type = 'status_change' AND c.details->>'to' = 'rejected' "
        "AND n.entry_type = 'note'", (biz["id"],)).fetchone()
    assert note == {"reason_for": "rejected", "reason": "they chose another agency"}


# ---- reasons -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["declined", "rejected"])
def test_a_reason_is_required_for_declined_and_rejected(biz, conn, status):
    for args in ([biz["domain"], "--set", status], [biz["domain"], "--set", status, "--reason", "   "]):
        result = run(*args)
        assert result.exit_code != 0 and "reason is required" in result.output.lower() + (result.stderr or "")
    assert st.get_status(conn, biz["id"])["status"] == "prospect"      # nothing changed
    assert history_count(conn, biz["id"]) == 1


def test_an_unknown_status_is_refused(biz):
    result = run(biz["domain"], "--set", "maybe")
    assert result.exit_code != 0


# ---- same status ---------------------------------------------------------------------------------------------------------

def test_setting_the_same_status_again_is_a_no_op(biz, conn):
    run(biz["domain"], "--set", "client", "--reason", "deposit")
    before = history_count(conn, biz["id"])
    journal_before = conn.execute("SELECT count(*) FROM client_journal WHERE business_id = %s", (biz["id"],)).fetchone()[0]
    result = run(biz["domain"], "--set", "client", "--reason", "again")
    assert result.exit_code == 0 and "already client" in result.output
    assert history_count(conn, biz["id"]) == before
    assert conn.execute("SELECT count(*) FROM client_journal WHERE business_id = %s", (biz["id"],)).fetchone()[0] == journal_before


# ---- history -------------------------------------------------------------------------------------------------------------

def test_history_is_newest_first_and_never_overwritten(biz, conn):
    for status, reason in [("client", "deposit"), ("delivered", None), ("rejected", "ended the contract")]:
        assert run(biz["domain"], "--set", status, *(["--reason", reason] if reason else [])).exit_code == 0
    history = st.get_status(conn, biz["id"])["history"]
    assert [h["status"] for h in history] == ["rejected", "delivered", "client", "prospect"]
    assert history[0]["reason"] == "ended the contract"
    shown = run(biz["domain"]).output
    assert shown.index("rejected") < shown.index("delivered") < shown.rindex("client") < shown.rindex("prospect")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE status_history SET reason = 'edited' WHERE business_id = %s", (biz["id"],))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM status_history WHERE business_id = %s", (biz["id"],))


def test_a_change_made_outside_aeo_status_is_recorded_too(biz, conn):
    conn.execute("UPDATE businesses SET status = 'client' WHERE id = %s", (biz["id"],))     # what the decline flow does
    info = st.get_status(conn, biz["id"])
    assert (info["status"], info["set_by"], info["reason"]) == ("client", "system", None)


def test_the_operator_is_not_remembered_between_changes(biz, conn, monkeypatch):
    run(biz["domain"], "--set", "client", "--reason", "deposit")
    conn.execute("UPDATE businesses SET status = 'delivered' WHERE id = %s", (biz["id"],))   # a later change by someone else
    info = st.get_status(conn, biz["id"])
    assert (info["set_by"], info["reason"]) == ("system", None)


# ---- declining a business that has data -------------------------------------------------------------------------------------

def test_declining_a_business_with_an_audit_warns_and_deletes_nothing(biz, conn):
    profile = store.load_business_and_profile(conn, biz["id"])
    store.create_run(conn, business=profile, checkpoint="before")
    runs_before = conn.execute("SELECT count(*) FROM probe_runs WHERE business_id = %s", (biz["id"],)).fetchone()[0]
    result = run(biz["domain"], "--set", "declined", "--reason", "we cannot serve this platform")
    assert result.exit_code == 0 and "Data exists" in result.output and "1 audit run" in result.output
    assert "still in progress" in result.output
    assert conn.execute("SELECT count(*) FROM probe_runs WHERE business_id = %s", (biz["id"],)).fetchone()[0] == runs_before
    assert st.get_status(conn, biz["id"])["status"] == "declined"


def test_declining_a_plain_prospect_prints_no_warning(biz):
    result = run(biz["domain"], "--set", "declined", "--reason", "no fit")
    assert result.exit_code == 0 and "Data exists" not in result.output


# ---- --all and --filter ----------------------------------------------------------------------------------------------------

def test_all_lists_every_business_and_filter_narrows_it(biz, conn):
    run(biz["domain"], "--set", "declined", "--reason", "no fit")
    everything = run("--all")
    assert everything.exit_code == 0 and "Domain" in everything.output and "Last changed" in everything.output
    assert any(l.startswith(biz["domain"]) and "declined" in l for l in everything.output.splitlines())
    declined = run("--all", "--filter", "declined")
    assert biz["domain"] in declined.output
    assert all(" client " not in f" {l} " for l in declined.output.splitlines()[1:])
    assert biz["domain"] not in run("--all", "--filter", "client").output


def test_all_with_an_unknown_filter_or_a_misused_flag_is_an_error(biz):
    assert run("--all", "--filter", "maybe").exit_code != 0
    assert run("--filter", "declined").exit_code != 0                 # --filter needs --all
    assert run("--all", biz["domain"]).exit_code != 0
    assert run().exit_code != 0                                         # nothing to show
    assert run(biz["domain"], "--reason", "why").exit_code != 0         # a reason with nothing to set


def test_list_statuses_gives_domain_status_engagement_and_date(biz, conn):
    row = next(r for r in st.list_statuses(conn) if r["domain"] == biz["domain"])
    assert row["status"] == "prospect" and row["last_changed"] is not None and "engagement_type" in row


# ---- unknown domain ----------------------------------------------------------------------------------------------------------

def test_an_unknown_domain_is_a_clear_error_not_a_crash():
    domain = f"nobody-{uuid.uuid4().hex[:10]}.example"
    for args in ([domain], [domain, "--set", "client"]):
        result = run(*args)
        assert result.exit_code == 1 and "no business matches" in result.output.lower()
        assert isinstance(result.exception, SystemExit)         # a clean exit, not a traceback


# ---- the timeline --------------------------------------------------------------------------------------------------------------

def test_the_timeline_line_carries_the_reason_and_who(biz, conn):
    run(biz["domain"], "--set", "declined", "--reason", "we cannot serve this platform")
    lines = [i["text"] for i in tl.collect_timeline(conn, biz["id"]) if i["kind"] == "status change"]
    assert "Status changed from prospect to declined: we cannot serve this platform (sam)" in lines


# ---- the decline flow --------------------------------------------------------------------------------------------------------------

def test_the_decline_flow_points_at_a_command_that_exists():
    source = (Path(__file__).parent.parent / "aeo" / "cli.py").read_text()
    assert "aeo status --business" not in source             # the old, never-built syntax
    assert 'aeo status {business or \'<domain>\'} --set declined --reason' in source


# ---- the migration --------------------------------------------------------------------------------------------------------------

def _migrate_module():
    spec = importlib.util.spec_from_file_location("migrate_script", Path(__file__).parent.parent / "db" / "migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scratch_database():
    """An empty database made for one test and dropped after it. Never aeo or aeo_test."""
    admin_url = os.environ["TEST_DATABASE_URL"]
    name = f"aeo_migtest_{uuid.uuid4().hex[:10]}"
    admin = psycopg.connect(admin_url, autocommit=True)
    admin.execute(f'CREATE DATABASE "{name}"')
    url = make_conninfo(admin_url, dbname=name)
    try:
        yield url
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def apply_migrations(module, url, directory, monkeypatch):
    monkeypatch.setattr(module, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(sys, "argv", ["migrate.py", url])
    module.main()


def test_the_migration_applies_on_a_fresh_database(scratch_database, monkeypatch):
    apply_migrations(_migrate_module(), scratch_database, MIGRATIONS, monkeypatch)
    with psycopg.connect(scratch_database, autocommit=True) as c:
        bid = c.execute("INSERT INTO businesses (domain, website_url) VALUES ('x.example', 'https://x.example') RETURNING id").fetchone()[0]
        assert c.execute("SELECT status, set_by FROM status_history WHERE business_id = %s", (bid,)).fetchall() == [("prospect", "system")]
        c.execute("UPDATE businesses SET status = 'client' WHERE id = %s", (bid,))
        assert [r[0] for r in c.execute("SELECT status FROM status_history WHERE business_id = %s ORDER BY seq", (bid,))] == ["prospect", "client"]
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO status_history (business_id, status, set_by) VALUES (%s, 'maybe', 'x')", (bid,))


def test_the_migration_applies_on_top_of_the_existing_schema_and_rebuilds_history(scratch_database, tmp_path, monkeypatch):
    module = _migrate_module()
    before = tmp_path / "before"
    before.mkdir()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.stem < "020":
            shutil.copy(path, before / path.name)
    apply_migrations(module, scratch_database, before, monkeypatch)              # the schema as it was before step 12

    with psycopg.connect(scratch_database, autocommit=True) as c:
        plain = c.execute("INSERT INTO businesses (domain, website_url) VALUES ('plain.example', 'https://plain.example') RETURNING id").fetchone()[0]
        gone = c.execute("INSERT INTO businesses (domain, website_url) VALUES ('gone.example', 'https://gone.example') RETURNING id").fetchone()[0]
        c.execute("UPDATE businesses SET status = 'declined' WHERE id = %s", (gone,))      # migration 013 journals it
        change = c.execute("SELECT id FROM client_journal WHERE business_id = %s AND entry_type = 'status_change'", (gone,)).fetchone()[0]
        c.execute("INSERT INTO client_journal (business_id, entry_type, description, actor, related_table, related_id, details) "
                  "VALUES (%s, 'note', 'Declined: no fit', 'aeo diagnose', 'client_journal', %s, %s)",
                  (gone, change, psycopg.types.json.Jsonb({"reason_for": "declined", "reason": "no fit"})))

    apply_migrations(module, scratch_database, MIGRATIONS, monkeypatch)          # now step 12 goes on top

    with psycopg.connect(scratch_database) as c:
        rows = lambda b: c.execute("SELECT status, reason, set_by FROM status_history WHERE business_id = %s ORDER BY seq", (b,)).fetchall()
        assert rows(plain) == [("prospect", None, "system (backfilled)")]
        assert rows(gone) == [("prospect", None, "system (backfilled)"), ("declined", "no fit", "system (backfilled)")]
        assert c.execute("SELECT count(*) FROM schema_migrations WHERE version = '020_status_history'").fetchone()[0] == 1
