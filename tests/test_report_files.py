"""The numbered run history shared by diagnose (now) and audit (later)."""

from __future__ import annotations

from datetime import date

import pytest

from core.report_files import next_run_number, write_run_files

D1, D2, D3 = date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 9)


def write(folder, day, text="x"):
    return write_run_files(folder, {"diagnosis.md": text, "diagnosis.json": "{}", "raw_page.html": b"<p>x</p>"},
                           anchor="diagnosis.md", day=day)


def test_first_run_is_1_and_each_run_is_one_more(tmp_path):
    assert [write(tmp_path, D1), write(tmp_path, D2), write(tmp_path, D3)] == [1, 2, 3]
    assert (tmp_path / "diagnosis_run2_2026-10-05.md").exists()
    assert (tmp_path / "raw_page_run3_2026-10-09.html").read_bytes() == b"<p>x</p>"


def test_latest_copy_follows_the_newest_run_and_numbered_copies_never_change(tmp_path):
    write(tmp_path, D1, "first")
    write(tmp_path, D2, "second")
    assert (tmp_path / "diagnosis.md").read_text() == "second"
    assert (tmp_path / "diagnosis_run1_2026-10-02.md").read_text() == "first"
    assert (tmp_path / "diagnosis_run2_2026-10-05.md").read_text() == "second"


def test_two_runs_on_the_same_day_do_not_collide(tmp_path):
    assert (write(tmp_path, D1), write(tmp_path, D1)) == (1, 2)
    assert (tmp_path / "diagnosis_run1_2026-10-02.md").exists() and (tmp_path / "diagnosis_run2_2026-10-02.md").exists()


def test_numbering_comes_from_the_disk_even_after_a_gap(tmp_path):
    write(tmp_path, D1)
    write(tmp_path, D2)
    write(tmp_path, D3)
    # Someone deletes run 2 by hand. Counting files would say "2 exist, next is 3" and collide with run 3.
    for f in tmp_path.glob("*_run2_*"):
        f.unlink()
    assert next_run_number(tmp_path, "diagnosis.md") == 4
    assert write(tmp_path, D3) == 4
    assert (tmp_path / "diagnosis_run3_2026-10-09.md").exists()      # run 3 untouched


def test_other_files_in_the_folder_do_not_confuse_the_count(tmp_path):
    (tmp_path / "notes_run9_2026-01-01.md").write_text("not ours")
    (tmp_path / "diagnosis_run5.md").write_text("no date, not ours")
    (tmp_path / "diagnosis.2026-10-02T101010.md").write_text("old timestamp style")
    assert write(tmp_path, D1) == 1


def test_a_numbered_file_is_never_overwritten(tmp_path):
    write(tmp_path, D1, "first")
    (tmp_path / "diagnosis.md").unlink()     # even if "latest" vanishes, history stays
    write(tmp_path, D2, "second")
    assert (tmp_path / "diagnosis_run1_2026-10-02.md").read_text() == "first"
    with pytest.raises(FileExistsError):     # the safety net itself
        import core.report_files as rf
        with open(tmp_path / rf.numbered_name("diagnosis.md", 1, D1), "xb"):
            pass


def test_a_report_from_before_numbering_becomes_run_1_instead_of_being_overwritten(tmp_path):
    (tmp_path / "diagnosis.md").write_text("written by the old version")
    (tmp_path / "diagnosis.json").write_text("{}")
    assert write(tmp_path, D2, "new") == 2
    kept = [f for f in tmp_path.glob("diagnosis_run1_*.md")]
    assert len(kept) == 1 and kept[0].read_text() == "written by the old version"
    assert (tmp_path / "diagnosis.md").read_text() == "new"
