"""What `aeo recommend` reads from a finished audit run: the answers, and the question tags recorded when it started."""

from __future__ import annotations

from audit import store
from audit.report_data import ReportError, load_answers, select_run


class NoFinishedAudit(Exception):
    """There is no finished audit to prioritise by."""


def load_finished_audit(conn, business_id: str, run: str | None) -> dict:
    """The run asked for, or the latest FINISHED visibility run. An unfinished run is refused: a fix list
    ordered by half an audit would look as sure as one ordered by a whole one."""
    try:
        chosen, _ = select_run(conn, business_id, run=run)
    except ReportError as exc:
        raise NoFinishedAudit(str(exc)) from exc
    if chosen["status"] != "complete":
        raise NoFinishedAudit(f"audit run {chosen['id']} is not finished (status {chosen['status']}).")
    if chosen["run_type"] != "visibility":
        raise NoFinishedAudit(f"run {chosen['id']} is a {chosen['run_type']} run; the fix list is built from a visibility run.")
    started = store.started_details(conn, chosen["id"]) or {}
    answers = [a for a in load_answers(conn, chosen["id"]) if a["named"] is not None]
    if not answers:
        raise NoFinishedAudit(f"audit run {chosen['id']} has no answers the model has read yet.")
    profile = store.load_business_and_profile(conn, business_id, chosen["profile_id"])
    planned = started.get("questions") or []
    return {"run": chosen, "started": started, "answers": answers,
            "targets": [q["text"] for q in planned if q.get("is_target")],
            "tags_recorded": bool(planned),
            "run_profile_version": profile["profile_version"] if profile else None,
            "run_facts": profile["facts"] if profile else []}
