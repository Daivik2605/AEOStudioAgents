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

__all__ = ["DossierError", "DossierResult", "make_dossier", "rebuild_from_json"]


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


@dataclass
class RebuildResult:
    md_path: Path
    html_path: Path
    identical: dict          # {"md": True/False/None, "html": ...}: same as the original, different, or the original was missing


def rebuild_from_json(business: dict, json_file: Path, *, clients_root: Path | None = None) -> RebuildResult:
    """Makes the .md and .html again from a saved .json and nothing else. A file that is already there with the same bytes is
    left alone; one that differs is never overwritten (the new one is written beside it under the next free name)."""
    try:
        data = json.loads(Path(json_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DossierError(f"cannot read {json_file}: {exc}") from exc
    if not isinstance(data, dict) or data.get("tool", {}).get("name") != "aeo dossier" or "sections" not in data:
        raise DossierError(f"{json_file} is not a dossier data file")
    if data["business"]["id"] != business["id"]:
        raise DossierError("that data file belongs to a different business")
    folder = client_files.generated_dir(business["domain"], clients_root)
    paths, same = {}, {}
    for ext, text in (("md", render_md(data)), ("html", render_html(data))):
        target = folder / (Path(json_file).stem + f".{ext}")
        raw = text.encode("utf-8")
        if target.exists() and target.read_bytes() == raw:
            paths[ext], same[ext] = target, True
        else:
            existed = target.exists()          # asked BEFORE writing: write_new creates the file
            paths[ext] = client_files.write_new(folder, target.name, raw)
            same[ext] = False if existed else None
    return RebuildResult(paths["md"], paths["html"], same)
