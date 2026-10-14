"""`aeo files migrate`: dry run by default, --apply copies, and nothing is ever deleted or overwritten."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aeo import cli
from core import files_migrate as fm

SLUG = "acme.example"


def stamp(path: Path, day: str) -> None:
    t = datetime.fromisoformat(f"{day}T12:00:00").timestamp()
    os.utime(path, (t, t))


def put(path: Path, content: str | bytes, day: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    if day:
        stamp(path, day)
    return path


@pytest.fixture
def reports(tmp_path):
    """A small copy of O'land's old layout: two checkpoint folders, a timestamp-style file, a 'latest' copy that
    differs from its dated twin (a newer run), 'latest' copies that are identical, and a hand-made snapshot."""
    r = tmp_path / "reports" / SLUG
    old, new = r / "old_site", r / "new_site_no_aeo"
    put(old / "audit_manual_snapshot.md", "snapshot", "2026-10-04")
    put(old / "diagnosis.20261004T204235.md", "run A md", "2026-10-04")
    put(old / "diagnosis.20261004T204235.json", "run A json", "2026-10-04")
    put(old / "diagnosis.md", "run B md (newer, only exists as latest)", "2026-10-05")
    put(old / "diagnosis.json", "run B json", "2026-10-05")
    put(new / "diagnosis_run1_2026-10-13.md", "run C md", "2026-10-13")
    put(new / "diagnosis_run1_2026-10-13.json", "run C json", "2026-10-13")
    put(new / "raw_page_run1_2026-10-13.html", b"<p>caf\xc3\xa9</p>", "2026-10-13")
    put(new / "diagnosis.md", "run C md", "2026-10-13")          # identical to the dated copy
    put(new / "diagnosis.json", "run C json", "2026-10-13")
    put(new / "raw_page.html", b"<p>caf\xc3\xa9</p>", "2026-10-13")
    return tmp_path / "reports"


def tree(root: Path) -> dict[str, tuple[bytes, float]]:
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime) for p in sorted(root.rglob("*")) if p.is_file()}


def by_dst(plan, clients):
    return {str(i.dst.relative_to(clients)): i.src.name for i in plan.to_copy}


def test_the_plan_puts_everything_in_the_new_layout_in_date_order(reports, tmp_path):
    clients = tmp_path / "clients"
    plan = fm.plan_migration(reports, clients)
    assert by_dst(plan, clients) == {
        f"{SLUG}/evidence/2026-10-04_audit_manual_snapshot.md": "audit_manual_snapshot.md",   # counts as evidence
        f"{SLUG}/generated/2026-10-04_diagnosis_run1.md": "diagnosis.20261004T204235.md",
        f"{SLUG}/generated/2026-10-04_diagnosis_run1.json": "diagnosis.20261004T204235.json",
        f"{SLUG}/generated/2026-10-05_diagnosis_run2.md": "diagnosis.md",                      # a real, newer run
        f"{SLUG}/generated/2026-10-05_diagnosis_run2.json": "diagnosis.json",
        f"{SLUG}/generated/2026-10-13_diagnosis_run3.md": "diagnosis_run1_2026-10-13.md",
        f"{SLUG}/generated/2026-10-13_diagnosis_run3.json": "diagnosis_run1_2026-10-13.json",
        f"{SLUG}/evidence/2026-10-13_raw_page_run3.html": "raw_page_run1_2026-10-13.html",     # same run as its diagnosis
    }


def test_latest_copies_identical_to_a_dated_copy_are_skipped_not_copied(reports, tmp_path):
    plan = fm.plan_migration(reports, tmp_path / "clients")
    skipped = {i.src.relative_to(reports).as_posix(): i.note for i in plan.items if i.action == "skip"}
    assert set(skipped) == {f"{SLUG}/new_site_no_aeo/diagnosis.md", f"{SLUG}/new_site_no_aeo/diagnosis.json",
                            f"{SLUG}/new_site_no_aeo/raw_page.html"}
    assert all("same bytes as" in note for note in skipped.values())


def test_a_dry_run_changes_nothing(reports, tmp_path):
    clients = tmp_path / "clients"
    before = tree(reports)
    result = CliRunner().invoke(cli.app, ["files", "migrate"])
    assert result.exit_code == 0, result.output
    assert "Dry run: 8 file(s) would be copied. Nothing was changed." in result.output
    assert "->" in result.output and not clients.exists() and tree(reports) == before


@pytest.fixture
def point_cli_at(reports, tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "REPORTS_ROOT", reports)
    monkeypatch.setattr("core.client_files.CLIENTS_ROOT", tmp_path / "clients")
    return tmp_path / "clients"


def test_the_cli_dry_run_prints_old_path_arrow_new_path(point_cli_at):
    result = CliRunner().invoke(cli.app, ["files", "migrate"])
    assert result.exit_code == 0, result.output
    line = next(l for l in result.output.splitlines() if "audit_manual_snapshot.md" in l)
    assert "reports" in line.split("->")[0] and "2026-10-04_audit_manual_snapshot.md" in line.split("->")[1]


def test_apply_copies_the_files_and_never_touches_reports(reports, point_cli_at):
    clients = point_cli_at
    before = tree(reports)
    result = CliRunner().invoke(cli.app, ["files", "migrate", "--apply"])
    assert result.exit_code == 0, result.output
    assert "Copied 8 file(s)." in result.output and "delete reports/ by hand" in result.output
    assert tree(reports) == before                               # every original: still there, same bytes, same mtime
    assert (clients / SLUG / "generated" / "2026-10-04_diagnosis_run1.md").read_text() == "run A md"
    assert (clients / SLUG / "generated" / "2026-10-05_diagnosis_run2.md").read_text() == "run B md (newer, only exists as latest)"
    assert (clients / SLUG / "evidence" / "2026-10-13_raw_page_run3.html").read_bytes() == b"<p>caf\xc3\xa9</p>"
    assert (clients / SLUG / "evidence" / "2026-10-04_audit_manual_snapshot.md").read_text() == "snapshot"


def test_the_migration_code_has_no_way_to_delete():
    source = Path(fm.__file__).read_text()
    for word in ("unlink", "rmtree", "os.remove", ".rmdir", "shutil.move", ".rename(", ".replace("):
        assert word not in source, word


def test_applying_twice_adds_nothing_the_second_time(reports, point_cli_at):
    CliRunner().invoke(cli.app, ["files", "migrate", "--apply"])
    first = tree(point_cli_at)
    again = CliRunner().invoke(cli.app, ["files", "migrate", "--apply"])
    assert again.exit_code == 0 and "Nothing to copy." in again.output
    assert tree(point_cli_at) == first                           # no _2 duplicates


def test_a_taken_name_with_different_content_is_never_overwritten(reports, tmp_path):
    clients = tmp_path / "clients"
    taken = put(clients / SLUG / "generated" / "2026-10-04_diagnosis_run1.md", "something else entirely")
    plan = fm.plan_migration(reports, clients)
    dst = next(i.dst for i in plan.to_copy if i.src.name == "diagnosis.20261004T204235.md")
    assert dst.name == "2026-10-04_diagnosis_run1_2.md"
    fm.apply_plan(plan)
    assert taken.read_text() == "something else entirely" and dst.read_text() == "run A md"


def test_apply_stops_rather_than_overwrite_if_a_file_appeared_after_the_plan(reports, tmp_path):
    clients = tmp_path / "clients"
    plan = fm.plan_migration(reports, clients)
    victim = plan.to_copy[0].dst
    put(victim, "appeared later")
    with pytest.raises(FileExistsError):
        fm.apply_plan(plan)
    assert victim.read_text() == "appeared later"


def test_an_old_folder_that_is_not_a_checkpoint_is_copied_and_named_in_a_note(tmp_path):
    reports = tmp_path / "reports"
    put(reports / SLUG / "manual_check" / "diagnosis_run1_2026-10-01.md", "x", "2026-10-01")
    plan = fm.plan_migration(reports, tmp_path / "clients")
    assert len(plan.to_copy) == 1
    assert any(f"{SLUG}/manual_check" in n for n in plan.notes)


def test_no_reports_folder_means_nothing_to_do(tmp_path):
    plan = fm.plan_migration(tmp_path / "nope", tmp_path / "clients")
    assert plan.items == [] and "nothing to migrate" in plan.notes[0]


def test_a_second_client_is_kept_apart(reports, tmp_path):
    put(reports / "other.example" / "old_site" / "diagnosis_run1_2026-10-01.md", "other", "2026-10-01")
    clients = tmp_path / "clients"
    plan = fm.plan_migration(reports, clients)
    other = [i for i in plan.to_copy if "other.example" in str(i.dst)]
    assert [i.dst.name for i in other] == ["2026-10-01_diagnosis_run1.md"]       # its own numbering, starts at 1
