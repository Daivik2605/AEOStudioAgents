"""Where a client's files live, and what they are called (PLAN.md section 10).

    clients/<domain>/
        evidence/     <YYYY-MM-DD>_<name>.<ext>             things that came IN. Kept forever, never overwritten.
        generated/    <YYYY-MM-DD>_<kind>[_runN].<ext>      things the tool MADE. Rebuildable.
        work_log.md   timeline.md                           regenerated from the database

Rules that hold everywhere in this file:
  * Nothing is overwritten. A name that is taken gets _2, _3 ... added.
  * There are no "latest" copies. Sorting by name is sorting by date.
  * A run number is worked out from the files already in the folder (one more than the highest), so
    it cannot drift from what is really there. Highest, not "how many", so a file deleted by hand
    can never make the next number collide with a later one.
  * The checkpoint label is NOT in a file name. The database holds it; the folder is just a dated history.
"""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

CLIENTS_ROOT = Path(__file__).parent.parent / "clients"


def clients_root(root: Path | None = None) -> Path:
    """`root` if one was given (tests), else the real clients/ folder. Read at call time so a test can swap it."""
    return Path(root) if root is not None else CLIENTS_ROOT


def slugify_domain(value: str) -> str:
    """'https://www.Example.com/x' -> 'example.com'"""
    host = urlsplit(value if "//" in value else f"//{value}").hostname or value
    host = host.lower().removeprefix("www.")
    return re.sub(r"[^a-z0-9.-]+", "-", host).strip("-.") or "unknown-site"


def client_dir(domain: str, root: Path | None = None) -> Path:
    return clients_root(root) / slugify_domain(domain)


def generated_dir(domain: str, root: Path | None = None) -> Path:
    return client_dir(domain, root) / "generated"


def evidence_dir(domain: str, root: Path | None = None) -> Path:
    return client_dir(domain, root) / "evidence"


def safe_name(name: str) -> str:
    """A file name that cannot contain a slash or start with a dot, so nothing can escape the folder."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.")
    return cleaned or "file"


def dated_name(day: date, kind: str, ext: str, run: int | None = None) -> str:
    """'2026-10-13_diagnosis_run2.md'. `ext` has no dot."""
    return f"{day.isoformat()}_{kind}" + (f"_run{run}" if run is not None else "") + f".{ext}"


def _with_suffix(path: Path, n: int) -> Path:
    return path.with_name(f"{path.stem}_{n}{path.suffix}")


def free_path(folder: Path, filename: str) -> Path:
    """`folder/filename`, or `filename` with _2, _3 ... before the extension if that name is taken."""
    path = folder / filename
    n = 2
    while path.exists():
        path = _with_suffix(folder / filename, n)
        n += 1
    return path


def next_run_number(folder: Path, kind: str, ext: str) -> int:
    """One more than the highest `<date>_<kind>_run<N>.<ext>` in the folder (a _2 clash copy counts as its run)."""
    pattern = re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}_{re.escape(kind)}_run(\d+)(?:_\d+)?\.{re.escape(ext)}$")
    numbers = [int(m.group(1)) for f in folder.glob("*") if (m := pattern.match(f.name))]
    return max(numbers, default=0) + 1


def write_new(folder: Path, filename: str, data: bytes) -> Path:
    """Writes a file that does not exist yet and returns where it went. Never overwrites: "xb" refuses
    to open an existing file, and if another process took the name a moment ago the next free one is used."""
    folder.mkdir(parents=True, exist_ok=True)
    while True:
        path = free_path(folder, filename)
        try:
            with open(path, "xb") as f:
                f.write(data)
            return path
        except FileExistsError:
            continue          # lost a race for that name: look for the next free one


def _bytes(content: str | bytes) -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


def write_generated(folder: Path, *, kind: str, files: dict[str, str | bytes], anchor_ext: str,
                    day: date) -> tuple[int, dict[str, Path]]:
    """Writes one run of tool output: `files` maps an extension ('md', 'json') to its content.
    All of them share one run number (worked out from `anchor_ext`). Returns (run, {ext: path})."""
    folder.mkdir(parents=True, exist_ok=True)
    run = next_run_number(folder, kind, anchor_ext)
    paths = {ext: write_new(folder, dated_name(day, kind, ext, run), _bytes(content)) for ext, content in files.items()}
    return run, paths


def copy_new(source: Path, folder: Path, filename: str) -> Path:
    """Copies a file in without overwriting anything (and without touching the original)."""
    return write_new(folder, filename, Path(source).read_bytes())
