"""Numbered report history, shared by `aeo diagnose` and (later) `aeo audit`.

Every run writes permanent, numbered, dated copies next to each other:

    diagnosis_run1_2026-10-02.md      diagnosis_run1_2026-10-02.json     raw_page_run1_2026-10-02.html
    diagnosis_run2_2026-10-05.md      ...

and also overwrites a "latest" copy with no run number (diagnosis.md, diagnosis.json,
raw_page.html), so there is always one obvious place to look.

The run number is worked out from the files already in the folder, never stored
anywhere else, so it cannot drift from what is really there: it is one more than the
highest number found. (Highest, not "how many files", so a deleted middle run can
never make the next number collide with a later one.) Numbered files are never
overwritten or deleted. Only the "latest" copies are overwritten.
"""

from __future__ import annotations

import re
import shutil
from datetime import date, datetime
from pathlib import Path


def _run_pattern(anchor: str) -> re.Pattern:
    stem, dot, suffix = anchor.rpartition(".")
    return re.compile(rf"^{re.escape(stem)}_run(\d+)_\d{{4}}-\d{{2}}-\d{{2}}\.{re.escape(suffix)}$")


def next_run_number(folder: Path, anchor: str) -> int:
    """One more than the highest `<stem>_run<N>_<date>.<ext>` found for `anchor` (e.g. 'diagnosis.md')."""
    pattern = _run_pattern(anchor)
    numbers = [int(m.group(1)) for f in folder.glob("*") if (m := pattern.match(f.name))]
    return max(numbers, default=0) + 1


def numbered_name(latest_name: str, run: int, day: date) -> str:
    stem, dot, suffix = latest_name.rpartition(".")
    return f"{stem}_run{run}_{day.isoformat()}.{suffix}"


def write_run_files(folder: Path, files: dict[str, str | bytes], *, anchor: str, day: date) -> int:
    """Writes each file as a numbered copy plus a 'latest' copy. Returns the run number.

    `files` maps the latest name ("diagnosis.md") to its content (text or bytes).
    `anchor` is the file whose numbered copies define the run number.
    """
    folder.mkdir(parents=True, exist_ok=True)
    _adopt_unnumbered_latest(folder, files, anchor)
    run = next_run_number(folder, anchor)
    for latest_name, content in files.items():
        data = content.encode("utf-8") if isinstance(content, str) else content
        # "xb" refuses to open an existing file, so a numbered file can never be overwritten.
        with open(folder / numbered_name(latest_name, run, day), "xb") as f:
            f.write(data)
        (folder / latest_name).write_bytes(data)
    return run


def _adopt_unnumbered_latest(folder: Path, files: dict, anchor: str) -> None:
    """Reports written before numbering existed have only a 'latest' file. Give that one run 1,
    dated from the file's own modified time, so overwriting 'latest' below does not lose it."""
    if next_run_number(folder, anchor) != 1 or not (folder / anchor).exists():
        return
    day = datetime.fromtimestamp((folder / anchor).stat().st_mtime).date()
    for latest_name in files:
        old = folder / latest_name
        if old.exists():
            shutil.copy2(old, folder / numbered_name(latest_name, 1, day))
