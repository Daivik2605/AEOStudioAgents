"""Gathers everything a client report says, as one JSON-safe dictionary (PLAN.md step 22).

Every number here comes from the function behind the command that owns it:
    aeo audit report  -> audit.report_data.build_report
    aeo compare       -> compare.run.build_comparison
    aeo recommend     -> the `recommendations` table that command fills
    aeo timeline      -> core.timeline.collect_work
    aeo evidence      -> core.evidence.list_evidence
Nothing is re-parsed from markdown and nothing is recomputed in a different way.

The result is written to the .json file, and `render_html` needs nothing else, so the HTML can be rebuilt from that
file alone. That includes the screenshots (embedded as data in the file), the AI summaries, and the dates.
"""

from __future__ import annotations

import base64
import mimetypes
from datetime import date, timezone
from pathlib import Path

from audit.report_data import ReportError, build_report, select_run
from compare.between import load_entries
from compare.diagnosis import compare_diagnoses
from compare.run import CompareError, build_comparison
from core import client_files
from core.checkpoints import CHECKPOINTS
from core.db import get_connection
from core.businesses import get_engagement
from core.evidence import list_evidence
from core.timeline import collect_work
from scoring import scoring

STAGES = ("starting_point", "work_done", "results", "check")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_EMBED_BYTES = 5_000_000
DEFAULT_RESULTS = ("before", "after_aeo")


class ClientReportError(Exception):
    """The report cannot be made. The message says what to do."""


# ---- small readers ------------------------------------------------------------------------------------------------------

def _diagnoses(conn, business_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, checkpoint, checked_at, platform, platform_plan, verdict, findings FROM diagnoses "
                    "WHERE business_id = %s ORDER BY checked_at, created_at, id", (business_id,))
        rows = cur.fetchall()
    return [{"id": str(r[0]), "checkpoint": r[1], "checked_at": r[2], "platform": r[3], "plan": r[4], "verdict": r[5],
             "findings": r[6] or []} for r in rows]


def _diag_view(d: dict) -> dict:
    return {"id": d["id"], "checkpoint": d["checkpoint"], "date": d["checked_at"].astimezone(timezone.utc).date().isoformat(),
            "platform": d["platform"], "verdict": d["verdict"],
            "findings": [{"code": f["code"], "severity": f["severity"], "what": f["what"], "evidence": f.get("evidence")}
                         for f in d["findings"]]}


def _complete_runs(conn, business_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, checkpoint, COALESCE(completed_at, created_at) FROM probe_runs WHERE business_id = %s "
                    "AND run_type = 'visibility' AND status = 'complete' ORDER BY COALESCE(completed_at, created_at), id",
                    (business_id,))
        return [{"id": str(r[0]), "checkpoint": r[1], "when": r[2]} for r in cur.fetchall()]


def _first_two_checkpoints(diagnoses: list[dict]) -> tuple[str, str] | None:
    present = [c for c in CHECKPOINTS if any(d["checkpoint"] == c for d in diagnoses)]
    return (present[0], present[1]) if len(present) >= 2 else None


def _latest_by_checkpoint(diagnoses: list[dict], label: str) -> dict | None:
    matching = [d for d in diagnoses if d["checkpoint"] == label]
    return matching[-1] if matching else None


def _enrich_comparison(cmp: dict, by_id: dict[str, dict]) -> dict:
    """Adds what the client wording needs to each closed / still open / new finding of a diagnosis comparison:
    the finding's own evidence text (for values like the name the website gave), the platform it was found on, and,
    for a closed finding, whether the website platform changed between A and B ("Fixed by the new website").
    `aeo compare` is not touched: this works on a copy."""
    da, db = by_id.get(cmp["a"]["id"]), by_id.get(cmp["b"]["id"])
    if not (da and db):
        return cmp
    ev_a = {f["code"]: f.get("evidence") for f in da["findings"]}
    ev_b = {f["code"]: f.get("evidence") for f in db["findings"]}
    # A change of platform counts only when BOTH are known: "unknown -> Wix" does not show a site was replaced.
    replaced = bool(da["platform"] and db["platform"] and da["platform"] != db["platform"])
    out = dict(cmp)
    # `fixed` (and `closed`, the same list): only findings whose subject still exists. A platform change counts for these.
    out["closed"] = [{**f, "evidence": ev_a.get(f["code"]), "platform": da["platform"], "site_replaced": replaced}
                     for f in cmp.get("fixed", cmp["closed"])]
    out["fixed"] = out["closed"]
    # `no longer applies`: gone because what they were about was removed. Each says what replaced it (a finding in B).
    out["no_longer_applies"] = [
        {**f, "evidence": ev_a.get(f["code"]), "platform": da["platform"],
         "replaced_by": [{**r, "evidence": ev_b.get(r["code"]), "platform": db["platform"]} for r in f["replaced_by"]]}
        for f in cmp.get("no_longer_applies", [])]
    out["still_open"] = [{**f, "evidence": ev_b.get(f["code"]), "platform": db["platform"]} for f in cmp["still_open"]]
    out["new"] = [{**f, "evidence": ev_b.get(f["code"]), "platform": db["platform"]} for f in cmp["new"]]
    out["still_count"], out["new_count"] = len(cmp["still_open"]), len(cmp["new"])
    out["fixed_count"], out["no_longer_applies_count"] = len(out["fixed"]), len(out["no_longer_applies"])
    return out


# ---- sections ---------------------------------------------------------------------------------------------------------------

def _health(conn, business: dict, diagnoses: list[dict], a: str | None, b: str | None, work: list[dict]) -> dict:
    if not diagnoses:
        return {"available": False}
    latest = diagnoses[-1]
    out = {"available": True, "latest": _diag_view(latest), "comparison": None}
    pair = (a, b) if (a and b) else _first_two_checkpoints(diagnoses)
    if pair:
        da, db = _latest_by_checkpoint(diagnoses, pair[0]), _latest_by_checkpoint(diagnoses, pair[1])
        if da and db and da["id"] != db["id"]:
            until = db["checked_at"].astimezone(timezone.utc).date().isoformat()
            out["comparison"] = _enrich_comparison(
                {"a": _diag_view(da), "b": _diag_view(db), "labels": list(pair), **compare_diagnoses(da, db, work, until)},
                {d["id"]: d for d in diagnoses})
    return out


def _audit_today(conn, business: dict) -> dict:
    """The headline of the latest FINISHED audit run, from the same function `aeo audit report` uses."""
    try:
        run, _ = select_run(conn, business["id"])
    except ReportError:
        return {"available": False}
    if run["status"] != "complete":
        return {"available": False}
    data = build_report(conn, business, run, partial=False)
    return {
        "available": True, "design": data["design"], "header": {
            k: data["header"][k] for k in ("run_id", "checkpoint", "question_set_version", "first_answer_on",
                                           "last_answer_on", "profile_version", "confirmed_facts")},
        "engines": [e["label"] for e in data["header"]["engines"]],
        "extraction": {"answers": data["extraction"]["answers"], "scored": data["extraction"]["scored"],
                       "failed": len(data["extraction"]["failed"])},
        "headline": data["headline"], "targets_vs_control": data["targets_vs_control"],
        "share_of_voice": data["share_of_voice"], "wrong_facts": data["wrong_facts"],
        "tags_recorded": data["tags_recorded"],
        "questions": [{"number": q["number"], "text": q["question"], "is_target": q["is_target"]} for q in data["appendix"]],
        "formula_version": data["tool"]["formula_version"],
    }


def _recommendations(conn, business_id: str, platform: str | None = None) -> dict:
    """The titles of the fixes in the latest `aeo recommend`, from the table it fills."""
    with conn.cursor() as cur:
        cur.execute("SELECT created_at, priority_fixes, fact_fixes, prioritised_by_audit FROM recommendations "
                    "WHERE business_id = %s ORDER BY created_at DESC, id LIMIT 1", (business_id,))
        r = cur.fetchone()
    if r is None:
        return {"available": False}
    fixes = list(r[2] or []) + list(r[1] or [])
    order = ["access", "wrong_fact", "target_question", "finding", "hygiene"]
    fixes.sort(key=lambda f: (order.index(f["section"]), int(f["id"][1:])))
    return {"available": True, "date": r[0].astimezone(timezone.utc).date().isoformat(), "prioritised_by_audit": r[3],
            "platform": platform,
            "fixes": [{"id": f["id"], "section": f["section"], "title": f["title"], "cannot_do": bool(f["cannot_do"]),
                       "finding_code": f.get("finding_code"), "file": f.get("file"), "quote": f.get("quote"),
                       "question": f.get("question")} for f in fixes]}


def _evidence(conn, business: dict, clients_root: Path | None) -> list[dict]:
    """Only evidence marked client-visible. Screenshots are embedded (as data, so the HTML needs no other file);
    everything else is listed with its date, source and note."""
    out = []
    base = client_files.client_dir(business["domain"], clients_root)
    for e in list_evidence(conn, business["id"]):
        if not e["client_visible"]:
            continue
        path = base / e["file_path"]
        suffix = Path(e["file_path"]).suffix.lower()
        item = {"id": e["id"], "file": Path(e["file_path"]).name, "type": e["evidence_type"], "source": e["source"],
                "captured_on": (e["captured_on"] or e["created_at"].astimezone(timezone.utc).date()).isoformat(),
                "period_start": e["period_start"].isoformat() if e["period_start"] else None,
                "period_end": e["period_end"].isoformat() if e["period_end"] else None,
                "checkpoint": e["checkpoint"], "note": e["note"], "finding_code": e["finding_code"],
                "embedded": False, "data_uri": None, "kind": "listed"}
        if suffix in IMAGE_TYPES:
            item["kind"] = "image"
            try:
                raw = path.read_bytes()
            except OSError:
                raw = None
            if raw is not None and len(raw) <= MAX_EMBED_BYTES:
                item["embedded"] = True
                item["data_uri"] = f"data:{IMAGE_TYPES[suffix]};base64," + base64.b64encode(raw).decode("ascii")
        out.append(item)
    out.sort(key=lambda i: (i["captured_on"], i["file"], i["id"]))
    return out


def _work(conn, business: dict, diagnoses: list[dict]) -> list[dict]:
    what_for: dict[str, dict] = {}
    for d in diagnoses:                       # the newest wording of each finding wins
        for f in d["findings"]:
            what_for[f["code"]] = {"what": f["what"], "evidence": f.get("evidence"), "platform": d["platform"]}
    out = []
    for w in collect_work(conn, business["id"]):
        code = w["details"].get("finding")
        out.append({"id": w["id"], "date": w["when"].astimezone(timezone.utc).date().isoformat(), "operator": w["operator"],
                    "text": w["text"], "finding_code": code, "finding": what_for[code]["what"] if code in what_for else None,
                    "finding_evidence": what_for[code]["evidence"] if code in what_for else None,
                    "finding_platform": what_for[code]["platform"] if code in what_for else None,
                    "checkpoint": w["details"].get("checkpoint")})
    return out


# ---- choosing what to compare for `check` --------------------------------------------------------------------------------------

def _check_pair(conn, business_id: str, diagnoses: list[dict]) -> dict:
    """The latest 'check' and the measurement just before it, for the diagnosis and for the audit."""
    kw: dict = {"use_diagnosis": False, "use_audit": False}
    label_a = label_b = "check"
    checks = [i for i, d in enumerate(diagnoses) if d["checkpoint"] == "check"]
    if checks and checks[-1] > 0:
        b, a = diagnoses[checks[-1]], diagnoses[checks[-1] - 1]
        kw.update(use_diagnosis=True, a_diagnosis=a["id"], b_diagnosis=b["id"])
        label_a = a["checkpoint"] or "check"
    runs = _complete_runs(conn, business_id)
    run_checks = [i for i, r in enumerate(runs) if r["checkpoint"] == "check"]
    if run_checks and run_checks[-1] > 0:
        rb, ra = runs[run_checks[-1]], runs[run_checks[-1] - 1]
        kw.update(use_audit=True, a_run=ra["id"], b_run=rb["id"])
        if not kw["use_diagnosis"]:
            label_a = ra["checkpoint"] or "check"
    if not (kw["use_diagnosis"] or kw["use_audit"]):
        raise ClientReportError("There is no 'check' to report on, or nothing before it to compare it with. Run `aeo diagnose` "
                                "and/or `aeo audit` with --checkpoint check first (and have an earlier measurement).")
    return {"label_a": label_a, "label_b": "check", **kw}


# ---- the whole report ---------------------------------------------------------------------------------------------------------------

def collect(business: dict, stage: str, *, version: int, a: str | None = None, b: str | None = None,
            today: date | None = None, clients_root: Path | None = None, language: str = "en") -> dict:
    if stage not in STAGES:
        raise ClientReportError(f"--stage must be one of: {', '.join(STAGES)}")
    for label in (a, b):
        if label is not None and label not in CHECKPOINTS:
            raise ClientReportError(f"'{label}' is not a checkpoint label. Valid labels: {', '.join(CHECKPOINTS)}")
    if bool(a) != bool(b):
        raise ClientReportError("give --a and --b together, or neither")
    if a and stage not in ("starting_point", "results"):
        raise ClientReportError(f"--a and --b choose what to compare in the 'starting_point' and 'results' reports, not '{stage}'")

    conn = get_connection()
    try:
        diagnoses = _diagnoses(conn, business["id"])
        work = _work(conn, business, diagnoses)
        entries = [{"date": w["date"], "operator": w["operator"], "text": w["text"], "finding": w["finding_code"]} for w in work]
        sections: list[dict] = []
        label_dates: dict = {}
        engagement = get_engagement(conn, business["id"])["engagement_type"]

        if stage == "starting_point":
            audit = _audit_today(conn, business)
            if audit["available"]:
                ai_sections = [
                    {"key": "ai_today", "data": audit},
                    {"key": "questions", "data": {"available": True, "design": audit["design"]["label"], "items": audit["questions"],
                                                  "tags_recorded": audit["tags_recorded"], "version": audit["header"]["question_set_version"]}}]
            else:       # no finished audit: ONE short section saying the measurement is next, not two empty ones
                ai_sections = [{"key": "ai_next", "data": {}}]
            sections = [
                {"key": "health", "data": _health(conn, business, diagnoses, a, b, entries)},
                *ai_sections,
                {"key": "recommend", "data": _recommendations(conn, business["id"], diagnoses[-1]["platform"] if diagnoses else None)},
                {"key": "evidence", "data": {"items": _evidence(conn, business, clients_root)}},
                # `audit` decides whether the sentence "what AI says" may be used (PLAN.md section 6): only with a finished audit.
                {"key": "promise", "data": {"audit": audit["available"]}},
            ]
            labels = {"a": a or "", "b": b or ""}
        elif stage == "work_done":
            sections = [{"key": "work", "data": {"items": work}},
                        {"key": "evidence", "data": {"items": _evidence(conn, business, clients_root)}}]
            labels = {"a": "", "b": ""}
        else:
            if stage == "results":
                label_a, label_b = (a, b) if (a and b) else DEFAULT_RESULTS
                pair = {"label_a": label_a, "label_b": label_b}
            else:
                pair = _check_pair(conn, business["id"], diagnoses)
                label_a, label_b = pair["label_a"], pair["label_b"]
            cmp_kw = {k: v for k, v in pair.items() if k not in ("label_a", "label_b")}
            try:
                comparison = build_comparison(business, label_a, label_b, **cmp_kw)
            except CompareError as exc:
                raise ClientReportError(str(exc)) from exc
            sections = _result_sections(comparison, diagnoses)
            labels = {"a": label_a, "b": label_b}
            label_dates = _label_dates(comparison)
    finally:
        conn.close()

    return {
        "tool": {"name": "aeo client-report", "language": language},
        "stage": stage, "version": version, "generated_on": (today or date.today()).isoformat(),
        "business": {"id": business["id"], "name": business["name"], "domain": business["domain"]},
        "labels": labels, "label_dates": label_dates, "engagement": engagement,
        "formula_version": scoring.formula_version(),
        "sections": [{"key": s["key"], "data": s["data"], "summary": None} for s in sections],
        "ai": {"used": False, "model": None, "calls": 0, "rejected": []},
    }


def _label_dates(c: dict) -> dict:
    """The day of side A and side B: the diagnosis date, or the audit's finish date when there is no diagnosis.
    The client report needs it for "quarterly check on <date>"."""
    out = {}
    for side in ("a", "b"):
        if c["diagnosis"]["available"]:
            out[side] = c["diagnosis"][side]["date"]
        elif c["audit"]["available"]:
            out[side] = (c["audit"][side]["completed_at"] or "")[:10] or None
    return {k: v for k, v in out.items() if v}


def _result_sections(c: dict, diagnoses: list[dict]) -> list[dict]:
    """The sections of `results` and `check`, taken from the comparison's own data."""
    audit = c["audit"]
    diag = _enrich_comparison(c["diagnosis"], {d["id"]: d for d in diagnoses}) if c["diagnosis"]["available"] else c["diagnosis"]
    out = [{"key": "r_health", "data": diag}]
    out.append({"key": "r_ai", "data": {**audit, "detection_floor": c["detection_floor"]}})
    out.append({"key": "r_targets", "data": audit["targets_vs_control"] if audit["available"] else {"available": False, "reason": audit["reason"]}})
    out.append({"key": "r_questions", "data": ({"available": True, "design": audit["design"]["label"], **audit["questions"],
                                                 "items": audit["questions"]["items"], "version": audit["question_set"]["version"]}
                                                if audit["available"] else {"available": False})})
    out.append({"key": "r_voice", "data": ({"available": True, **audit["share_of_voice"]} if audit["available"] else {"available": False})})
    out.append({"key": "r_facts", "data": audit["wrong_facts"] if audit["available"] else {"available": False, "reason": audit["reason"]}})
    out.append({"key": "r_sources", "data": ({"available": True, **audit["sources"]} if audit["available"] else {"available": False})})
    out.append({"key": "r_between", "data": c["between"]})
    return out
