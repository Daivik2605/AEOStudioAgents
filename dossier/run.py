"""`aeo dossier`: writes the baseline dossier (.md, .html, .json) into clients/<domain>/generated/.

Versions count per client (one more than the highest on disk, any date). Files are never overwritten. The notes file is
created once on the first run and never overwritten.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from core import client_files
from core.db import get_connection
from dossier import notes as notes_mod
from dossier.data import DossierError, collect
from dossier.render import render_html, render_md

__all__ = ["DossierError", "DossierResult", "make_dossier"]


@dataclass
class DossierResult:
    md_path: Path
    html_path: Path
    json_path: Path
    notes_path: Path
    notes_created: bool
    warnings: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def next_version(folder: Path) -> int:
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}_dossier_v(\d+)(?:_\d+)?\.(?:md|html|json)$")
    numbers = [int(m.group(1)) for f in folder.glob("*") if (m := pattern.match(f.name))]
    return max(numbers, default=0) + 1


def make_dossier(business: dict, *, questions: Path | None = None, spot_check: Path | None = None, clients_root: Path | None = None, today: date | None = None,
                 question_dir: Path | None = None) -> DossierResult:
    day = today or date.today()
    notes_path, created = notes_mod.ensure_notes(business["name"] or business["domain"], business["domain"], clients_root)
    notes, warnings = notes_mod.read_notes(notes_path)
    folder = client_files.generated_dir(business["domain"], clients_root)
    version = next_version(folder)
    conn = get_connection()
    try:
        data = collect(conn, business, version=version, today=day, notes=notes, notes_warnings=warnings,
                       questions_path=questions, clients_root=clients_root, question_dir=question_dir,
                       spot_check_path=spot_check)
    finally:
        conn.close()
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    data = json.loads(text)                  # the .md and .html are made from exactly what the .json holds
    stem = f"{day.isoformat()}_dossier_v{version}"
    md = client_files.write_new(folder, f"{stem}.md", render_md(data).encode("utf-8"))
    html = client_files.write_new(folder, f"{stem}.html", render_html(data).encode("utf-8"))
    js = client_files.write_new(folder, f"{stem}.json", text.encode("utf-8"))
    return DossierResult(md, html, js, notes_path, created, warnings, data)
