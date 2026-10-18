"""The notes file: clients/<domain>/dossier_notes.md.

A person writes here; the tool never does after the first run. The file is created once from a template with one
heading per dossier section, and is never overwritten. The text under each heading is copied word for word into that
section of the dossier. If a heading is renamed or added, the run says so and does not guess where its text belongs.
"""

from __future__ import annotations

from pathlib import Path

from core import client_files

# (key, heading). The heading text is the contract with the notes file: it must match exactly.
SECTIONS = [
    ("how_to_use", "How to use"),
    ("snapshot", "Business snapshot"),
    ("stage", "Stage"),
    ("how_ai_sees", "How AI sees the site"),
    ("crawler", "AI crawler activity"),
    ("ai_says", "What AI says today"),
    ("problems", "Problems, ranked"),
    ("questions", "Proposed question set"),
    ("signals", "AI visibility signals"),
    ("client_needs", "What we need from the client"),
    ("next", "What happens next"),
]
TITLES = dict(SECTIONS)
NOTES_FILE = "dossier_notes.md"


def notes_path(domain: str, root: Path | None = None) -> Path:
    return client_files.client_dir(domain, root) / NOTES_FILE


def template(name: str) -> str:
    lines = [f"# Dossier notes: {name}", "",
             "Write your notes under the headings below. The text under each heading is copied word for word into that",
             "section of the dossier, under \"Our notes\". Do not rename or add headings: a heading that is not on this",
             "list is not copied, and the run warns you. This file is never overwritten by the tool.", ""]
    for _, heading in SECTIONS:
        lines += [f"## {heading}", "", ""]
    return "\n".join(lines)


def ensure_notes(name: str, domain: str, root: Path | None = None) -> tuple[Path, bool]:
    """Creates the notes file from the template if it does not exist. Never touches an existing one."""
    path = notes_path(domain, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as f:       # "x": fail rather than overwrite
            f.write(template(name))
        return path, True
    except FileExistsError:
        return path, False


def read_notes(path: Path) -> tuple[dict[str, str], list[str]]:
    """({section key: the text under its heading, verbatim}, warnings). Only exact "## Heading" lines count."""
    by_heading = {h: k for k, h in SECTIONS}
    notes: dict[str, str] = {k: "" for k, _ in SECTIONS}
    warnings: list[str] = []
    seen: list[str] = []
    current: str | None = None          # a section key, or "?" for text under an unknown heading, or None before any heading
    buf: list[str] = []

    def flush() -> None:
        if current in notes:
            notes[current] = "\n".join(buf).strip("\n")

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            flush()
            buf = []
            heading = line[3:].strip()
            if heading in by_heading:
                current = by_heading[heading]
                seen.append(current)
            else:
                current = "?"
                warnings.append(f"The notes file has a heading that is not a dossier section: \"{heading}\". "
                                "Its text was NOT copied anywhere. Rename it to one of the section names, or move the text.")
        else:
            buf.append(line)
    flush()
    for key, heading in SECTIONS:
        if key not in seen:
            warnings.append(f"The notes file has no heading \"{heading}\" (renamed or removed), so there are no notes for that section.")
    return notes, warnings
