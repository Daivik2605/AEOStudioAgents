"""`aeo audit report`: read one audit run and write the report (PLAN.md section 2 step 5, sections 8 and 10).

  clients/<domain>/generated/<date>_audit_report_run<N>.md     the report
  clients/<domain>/generated/<date>_audit_report_run<N>.json   exactly the data the report was made from

and, for a finished run, the scores go into `visibility_scores` with their formula_version.
No AI is used. Re-running gives a new numbered file with identical content (nothing is overwritten).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from audit.report_data import ReportError, build_report, select_run
from audit.report_render import render_markdown
from core import client_files
from core.db import get_connection
from core.journal import write_journal
from core.operator import operator_name

__all__ = ["ReportError", "ReportResult", "make_report"]


@dataclass
class ReportResult:
    md_path: Path
    json_path: Path
    run_id: str
    data: dict
    scores_saved: bool
    other_runs: int


def save_scores(conn, run_id: str, data: dict) -> None:
    """One row per (run, formula version). Re-running the report on the same data rewrites the same numbers."""
    m = data["headline"]["measures"]
    accuracy = m.get("accuracy", {}).get("value")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO visibility_scores (probe_run_id, formula_version, visibility_score, gap_score, accuracy_score, tier) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (probe_run_id, formula_version) DO UPDATE SET visibility_score = EXCLUDED.visibility_score, "
            "gap_score = EXCLUDED.gap_score, accuracy_score = EXCLUDED.accuracy_score, tier = EXCLUDED.tier, "
            "computed_at = now()",
            (run_id, data["tool"]["formula_version"], m["visibility"]["value"], m["gap"]["value"], accuracy,
             data["headline"]["tier"]))


def make_report(business: dict, *, run: str | None = None, checkpoint: str | None = None, partial: bool = False,
                clients_root: Path | None = None, today: date | None = None) -> ReportResult:
    conn = get_connection()
    try:
        chosen, others = select_run(conn, business["id"], run=run, checkpoint=checkpoint)
        data = build_report(conn, business, chosen, partial=partial)
        # Through JSON and back, so the markdown is made from exactly what the .json file will hold.
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        data = json.loads(text)
        md = render_markdown(data)

        _, paths = client_files.write_generated(
            client_files.generated_dir(business["domain"], clients_root), kind="audit_report", anchor_ext="md",
            day=today or date.today(), files={"md": md, "json": text})

        # A report on an unfinished run is not a measurement, so its numbers are not saved as one.
        scores_saved = data["incomplete"] is None
        if scores_saved:
            save_scores(conn, chosen["id"], data)
        write_journal(
            business_id=business["id"], entry_type="audit_generated", actor=operator_name(),
            description=f"Audit report written for run {chosen['id']} ({data['design']['label']}"
                        + ("" if scores_saved else ", INCOMPLETE") + ")",
            related_table="probe_runs", related_id=chosen["id"], conn=conn,
            details={"probe_run_id": chosen["id"], "design": data["design"]["label"], "partial": not scores_saved,
                     "formula_version": data["tool"]["formula_version"], "scores_saved": scores_saved,
                     "report": f"generated/{paths['md'].name}", "data": f"generated/{paths['json'].name}"})
    finally:
        conn.close()
    return ReportResult(paths["md"], paths["json"], chosen["id"], data, scores_saved, others)
