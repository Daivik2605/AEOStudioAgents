"""`aeo client-report`: builds one client report (PLAN.md step 22).

    clients/<domain>/generated/<date>_client_report_<stage>_v<N>.html   the report, one self-contained file
    clients/<domain>/generated/<date>_client_report_<stage>_v<N>.json   every piece of data it used

`--from-json` rebuilds the HTML from that file alone and produces the same bytes. The command never sends anything:
sending is a person's job (PLAN.md section 12).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from client_report.data import STAGES, ClientReportError, collect
from client_report.render import render_html
from client_report.summaries import add_summaries
from core import client_files

__all__ = ["ClientReportError", "ReportResult", "make_client_report", "rebuild_from_json"]


@dataclass
class ReportResult:
    html_path: Path
    json_path: Path | None
    data: dict
    identical_to_original: bool | None = None     # only for --from-json


def next_version(folder: Path, stage: str) -> int:
    """One more than the highest version of this stage's report in the folder (any date)."""
    pattern = re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}_client_report_{re.escape(stage)}_v(\d+)(?:_\d+)?\.(?:html|json)$")
    numbers = [int(m.group(1)) for f in folder.glob("*") if (m := pattern.match(f.name))]
    return max(numbers, default=0) + 1


def make_client_report(business: dict, stage: str, *, a: str | None = None, b: str | None = None, no_ai: bool = False,
                       ask: Callable[[str], str] = input, say: Callable[[str], None] = print, client=None,
                       clients_root: Path | None = None, today: date | None = None) -> ReportResult:
    if stage not in STAGES:
        raise ClientReportError(f"--stage must be one of: {', '.join(STAGES)}")
    folder = client_files.generated_dir(business["domain"], clients_root)
    version = next_version(folder, stage)
    day = today or date.today()
    data = collect(business, stage, version=version, a=a, b=b, today=day, clients_root=clients_root)
    if not no_ai:
        add_summaries(data, ask=ask, say=say, client=client)       # raises CostGateAborted before anything is written
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    data = json.loads(text)             # the HTML is made from exactly what the .json file holds
    html = render_html(data)
    stem = f"{day.isoformat()}_client_report_{stage}_v{version}"
    html_path = client_files.write_new(folder, f"{stem}.html", html.encode("utf-8"))
    json_path = client_files.write_new(folder, f"{stem}.json", text.encode("utf-8"))
    return ReportResult(html_path, json_path, data)


def rebuild_from_json(business: dict, json_file: Path, *, clients_root: Path | None = None) -> ReportResult:
    """Renders the HTML again from a saved .json. If the original .html is still there and the bytes match, nothing is
    written; otherwise the file is written beside it under the same name (or the next free one), never over it."""
    try:
        data = json.loads(Path(json_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClientReportError(f"cannot read {json_file}: {exc}") from exc
    if not isinstance(data, dict) or data.get("tool", {}).get("name") != "aeo client-report" or "sections" not in data:
        raise ClientReportError(f"{json_file} is not a client-report data file")
    if data["business"]["id"] != business["id"]:
        raise ClientReportError("that data file belongs to a different business")
    html = render_html(data).encode("utf-8")
    folder = client_files.generated_dir(business["domain"], clients_root)
    target = folder / (Path(json_file).stem + ".html")
    if target.exists():
        if target.read_bytes() == html:
            return ReportResult(target, Path(json_file), data, identical_to_original=True)
        written = client_files.write_new(folder, target.name, html)
        return ReportResult(written, Path(json_file), data, identical_to_original=False)
    written = client_files.write_new(folder, target.name, html)
    return ReportResult(written, Path(json_file), data, identical_to_original=None)
