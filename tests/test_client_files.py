"""The clients/ layout and naming, the four checkpoint labels, and the checkpoint row migration (PLAN.md section 10)."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest

from core import client_files as cf
from core.checkpoints import CHECKPOINTS, CheckpointError, check_checkpoint
from core.db import get_connection
from core.files_migrate import odd_checkpoints

D1, D2 = date(2026, 10, 2), date(2026, 10, 5)


# ---- layout and naming -----------------------------------------------------------------------------------------

def test_a_client_folder_is_named_after_the_domain_with_evidence_and_generated_inside(tmp_path):
    assert cf.client_dir("https://www.Example.com/x", tmp_path) == tmp_path / "example.com"
    assert cf.generated_dir("example.com", tmp_path) == tmp_path / "example.com" / "generated"
    assert cf.evidence_dir("example.com", tmp_path) == tmp_path / "example.com" / "evidence"


def test_names_are_date_first_with_an_optional_run_number():
    assert cf.dated_name(D1, "diagnosis", "md", 2) == "2026-10-02_diagnosis_run2.md"
    assert cf.dated_name(D1, "cdn_report", "pdf") == "2026-10-02_cdn_report.pdf"


def test_a_hostile_name_cannot_escape_the_folder():
    assert cf.safe_name("../../etc/passwd") == "etc-passwd"
    assert cf.safe_name("my report (final).pdf") == "my-report-final-.pdf"
    assert cf.safe_name("...") == "file" and cf.safe_name("") == "file"


def write(folder, day, text="x"):
    return cf.write_generated(folder, kind="diagnosis", anchor_ext="md", day=day,
                              files={"md": text, "json": "{}"})


def test_runs_are_numbered_one_more_than_the_highest_and_there_is_no_latest_copy(tmp_path):
    assert [write(tmp_path, D1)[0], write(tmp_path, D2)[0], write(tmp_path, D2)[0]] == [1, 2, 3]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "2026-10-02_diagnosis_run1.json", "2026-10-02_diagnosis_run1.md",
        "2026-10-05_diagnosis_run2.json", "2026-10-05_diagnosis_run2.md",
        "2026-10-05_diagnosis_run3.json", "2026-10-05_diagnosis_run3.md"]


def test_numbering_comes_from_the_folder_even_after_a_gap(tmp_path):
    for day in (D1, D2, D2):
        write(tmp_path, day)
    for f in tmp_path.glob("*_run2.*"):
        f.unlink()                                   # someone deletes run 2 by hand
    assert cf.next_run_number(tmp_path, "diagnosis", "md") == 4     # not 3: that would collide with run 3
    assert write(tmp_path, D2)[0] == 4


def test_other_files_do_not_confuse_the_count(tmp_path):
    (tmp_path / "2026-10-01_notes_run9.md").write_text("another kind")
    (tmp_path / "diagnosis_run5.md").write_text("no date")
    (tmp_path / "2026-10-01_diagnosis_run7.json").write_text("{}")    # same kind, other extension
    assert write(tmp_path, D1)[0] == 1


def test_a_name_that_is_taken_gets_2_then_3_and_nothing_is_overwritten(tmp_path):
    first = cf.write_new(tmp_path, "2026-10-02_cdn_report.pdf", b"one")
    second = cf.write_new(tmp_path, "2026-10-02_cdn_report.pdf", b"two")
    third = cf.write_new(tmp_path, "2026-10-02_cdn_report.pdf", b"three")
    assert [p.name for p in (first, second, third)] == [
        "2026-10-02_cdn_report.pdf", "2026-10-02_cdn_report_2.pdf", "2026-10-02_cdn_report_3.pdf"]
    assert [p.read_bytes() for p in (first, second, third)] == [b"one", b"two", b"three"]


def test_copy_new_leaves_the_original_alone_and_never_overwrites(tmp_path):
    original = tmp_path / "in" / "shot.png"
    original.parent.mkdir()
    original.write_bytes(b"\x89PNG")
    a = cf.copy_new(original, tmp_path / "evidence", "2026-10-02_shot.png")
    b = cf.copy_new(original, tmp_path / "evidence", "2026-10-02_shot.png")
    assert (a.name, b.name) == ("2026-10-02_shot.png", "2026-10-02_shot_2.png")
    assert original.read_bytes() == b"\x89PNG"


def test_a_clash_copy_still_counts_as_its_run(tmp_path):
    (tmp_path / "2026-10-02_diagnosis_run4_2.md").write_text("x")
    assert cf.next_run_number(tmp_path, "diagnosis", "md") == 5


# ---- checkpoint labels -------------------------------------------------------------------------------------------

def test_only_the_four_labels_are_accepted():
    assert CHECKPOINTS == ("before", "after_site", "after_aeo", "check")
    for label in CHECKPOINTS:
        assert check_checkpoint(label) == label
    assert check_checkpoint(None) is None


@pytest.mark.parametrize("bad", ["", "Before", "q1", "post_aeo", "quarterly_2027_Q1", "../../etc", "before "])
def test_anything_else_is_refused_with_the_list_of_valid_labels(bad):
    with pytest.raises(CheckpointError, match="Valid labels: before, after_site, after_aeo, check"):
        check_checkpoint(bad)


def test_the_two_old_names_say_what_replaced_them():
    with pytest.raises(CheckpointError, match="'old_site' is not a checkpoint label \\('old_site' is now called 'before'\\)"):
        check_checkpoint("old_site")
    with pytest.raises(CheckpointError, match="now called 'after_site'"):
        check_checkpoint("new_site_no_aeo")


def test_a_required_checkpoint_cannot_be_missing():
    with pytest.raises(CheckpointError, match="required"):
        check_checkpoint(None, required=True)


# ---- the database enforces the same four, and the row migration ----------------------------------------------------

MIGRATION = Path(__file__).parent.parent / "db" / "migrations" / "017_engagement_evidence_checkpoints.sql"


class _Rollback(Exception):
    pass


def _migration_updates() -> list[str]:
    """The real UPDATE statements out of migration 017, so the test runs exactly what the migration runs."""
    spec = importlib.util.spec_from_file_location("migrate_script", Path(__file__).parent.parent / "db" / "migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    for raw in module.split_statements(MIGRATION.read_text()):
        # The splitter keeps the comment lines above a statement attached to it; drop them.
        lines = [l for l in raw.splitlines() if l.strip() and not l.strip().startswith("--")]
        if lines and lines[0].upper().startswith("UPDATE"):
            statements.append("\n".join(lines))
    return statements


def _insert_diagnosis(conn, business_id, checkpoint):
    return conn.execute(
        "INSERT INTO diagnoses (business_id, checkpoint, url, verdict) VALUES (%s, %s, 'https://x.example', 'serve') "
        "RETURNING id", (business_id, checkpoint)).fetchone()[0]


def test_the_database_refuses_a_checkpoint_that_is_not_one_of_the_four(business_id):
    conn = get_connection()
    try:
        for good in (*CHECKPOINTS, None):
            _insert_diagnosis(conn, business_id, good)
        for bad in ("old_site", "q1", "Before"):
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_diagnosis(conn, business_id, bad)
    finally:
        conn.close()


def test_old_rows_are_renamed_and_any_other_value_is_left_alone_and_listed(business_id):
    """Pre-migration rows are simulated inside a transaction that is rolled back, so nothing stays behind."""
    odd = f"odd_{uuid.uuid4().hex[:8]}"
    conn = get_connection()
    try:
        with pytest.raises(_Rollback):
            with conn.transaction():
                conn.execute("ALTER TABLE diagnoses DROP CONSTRAINT diagnoses_checkpoint_check")
                ids = {name: _insert_diagnosis(conn, business_id, name)
                       for name in ("old_site", "new_site_no_aeo", odd, "before", None)}
                for statement in _migration_updates():
                    conn.execute(statement)
                after = {name: conn.execute("SELECT checkpoint FROM diagnoses WHERE id = %s", (i,)).fetchone()[0]
                         for name, i in ids.items()}
                assert after == {"old_site": "before", "new_site_no_aeo": "after_site", odd: odd,
                                 "before": "before", None: None}
                assert ("diagnoses", odd, 1) in odd_checkpoints(conn)       # listed, not guessed
                raise _Rollback
    finally:
        conn.close()
    conn = get_connection()
    try:
        assert ("diagnoses", odd, 1) not in odd_checkpoints(conn)           # the rollback left nothing behind
    finally:
        conn.close()


def test_the_migration_updates_cover_both_tables_and_exactly_the_two_known_names():
    text = " ".join(_migration_updates())
    assert text.count("old_site") == 2 and text.count("new_site_no_aeo") == 2
    assert "diagnoses" in text and "probe_runs" in text
