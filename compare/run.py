"""`aeo compare`: two checkpoints side by side (PLAN.md step 20). Reads the database, writes

    clients/<domain>/generated/<date>_compare_<a>_vs_<b>_run<N>.md     the comparison
    clients/<domain>/generated/<date>_compare_<a>_vs_<b>_run<N>.json   exactly the data it was made from

No AI. Nothing is written to the database. The diagnosis comparison and the audit comparison each work on their own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from compare.audit import DETECTION_FLOOR_POINTS, compare_audits, load_side
from compare.between import between, load_entries
from compare.diagnosis import compare_diagnoses, pick_diagnosis
from compare.errors import CompareError
from compare.render import render_markdown
from core import client_files
from core.checkpoints import check_checkpoint
from core.db import get_connection

__all__ = ["CompareError", "CompareResult", "make_comparison"]


@dataclass
class CompareResult:
    md_path: Path
    json_path: Path
    data: dict


def _day(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).date().isoformat()


def _diag_summary(d: dict) -> dict:
    return {"id": d["id"], "checkpoint": d["checkpoint"], "date": _day(d["checked_at"]), "platform": d["platform"],
            "plan": d["plan"], "verdict": d["verdict"], "findings": len(d["findings"])}


def make_comparison(business: dict, label_a: str, label_b: str, *, a_run: str | None = None, b_run: str | None = None,
                    a_diagnosis: str | None = None, b_diagnosis: str | None = None,
                    clients_root: Path | None = None, today: date | None = None) -> CompareResult:
    check_checkpoint(label_a, required=True)
    check_checkpoint(label_b, required=True)
    conn = get_connection()
    try:
        da = pick_diagnosis(conn, business["id"], label_a, a_diagnosis)
        db = pick_diagnosis(conn, business["id"], label_b, b_diagnosis)
        sa, why_a = load_side(conn, business["id"], label_a, a_run)
        sb, why_b = load_side(conn, business["id"], label_b, b_run)
        entries = load_entries(conn, business["id"])
    finally:
        conn.close()

    if da and db and da["id"] == db["id"]:
        raise CompareError("A and B are the same diagnosis. Give two different labels, or choose with --a-diagnosis / --b-diagnosis.")
    if sa and sb and sa["run"]["id"] == sb["run"]["id"]:
        raise CompareError("A and B are the same audit run. Give two different labels, or choose with --a-run / --b-run.")

    # --- what can be compared -------------------------------------------------------------------------------
    work = [e for e in entries if e["kind"] == "work"]
    audit = {"available": False, "reason": None}
    diagnosis = {"available": False, "reason": None}
    missing = []
    if da and db:
        until = max([_day(db["checked_at"])] + ([_day(sb["run"]["completed_at"] or sb["run"]["created_at"])] if sb else []))
        diagnosis = {"available": True, "reason": None, "a": _diag_summary(da), "b": _diag_summary(db),
                     **compare_diagnoses(da, db, work, until)}
    else:
        who = " and ".join(f"{l}" for l, d in ((label_a, da), (label_b, db)) if not d)
        diagnosis["reason"] = f"no diagnosis at checkpoint {who}"
        missing.append(diagnosis["reason"])
    if sa and sb:
        audit = compare_audits(sa, sb, business["domain"], label_a, label_b)
    else:
        audit["reason"] = "; ".join(r for r in (why_a, why_b) if r)
        missing.append(audit["reason"])
    if not diagnosis["available"] and not audit["available"]:
        raise CompareError("There is nothing to compare: " + "; ".join(missing) + ". Run `aeo diagnose` and/or `aeo audit` "
                           "for both checkpoints first (or choose runs with --a-run, --b-run, --a-diagnosis, --b-diagnosis).")

    # --- what happened in between ----------------------------------------------------------------------------
    a_dates = ([_day(da["checked_at"])] if da and db else []) + ([_day(sa["run"]["completed_at"] or sa["run"]["created_at"])] if sa and sb else [])
    b_dates = ([_day(db["checked_at"])] if da and db else []) + ([_day(sb["run"]["started_at"] or sb["run"]["created_at"])] if sa and sb else [])
    start, end = max(a_dates), min(b_dates)
    window = between(entries, start, end) if start <= end else []
    between_data = {"from": start, "to": end, "entries": window,
                    "events": sum(1 for e in window if e["outside_our_control"]),
                    "note": (f"B is dated before A ({end} is before {start}), so nothing is listed." if start > end else None)}

    data = {
        "tool": {"name": "aeo compare"},
        "generated_on": (today or date.today()).isoformat(),
        "business": {"id": business["id"], "name": business["name"], "domain": business["domain"]},
        "labels": {"a": label_a, "b": label_b},
        "detection_floor": {"points": DETECTION_FLOOR_POINTS,
                            "text": f"This design detects roughly a {DETECTION_FLOOR_POINTS}-point change, and not less (PLAN.md section 8)."},
        "diagnosis": diagnosis, "audit": audit, "between": between_data,
    }
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    data = json.loads(text)            # the markdown is made from exactly what the .json holds
    kind = f"compare_{label_a}_vs_{label_b}"
    _, paths = client_files.write_generated(
        client_files.generated_dir(business["domain"], clients_root), kind=kind, anchor_ext="md",
        day=today or date.today(), files={"md": render_markdown(data), "json": text})
    return CompareResult(paths["md"], paths["json"], data)
