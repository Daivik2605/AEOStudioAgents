"""Evidence, the work log and events (PLAN.md section 10).

  evidence  a file that came IN (a CDN report, a screenshot, a client email). Copied into
            clients/<domain>/evidence/ and recorded in the `evidence` table.
  work      something WE did for the client. A journal row of type `work`.
  event     something outside our control that can move the numbers. A journal row of type `event`.

All journal writes go through core/journal.write_journal.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from core import client_files
from core.checkpoints import check_checkpoint
from core.journal import write_journal


class EvidenceError(Exception):
    """A problem the person can fix (bad date, unknown finding code, missing file)."""


SOURCES = ("us", "client")

# The kinds of evidence we know by name (PLAN.md section 10). The type is free text, so another name still works; this list
# is for the help text and for the dossier, which looks for these names to see what has arrived.
EVIDENCE_TYPES = ("cdn_report", "ai_answer_screenshot", "client_email", "work_file", "search_console", "analytics",
                  "bing_wmt", "gbp", "site_move")


def check_finding_code(code: str | None) -> str | None:
    """Finding codes live in diagnose/codes.py (the registry that diagnose/findings.py builds from)."""
    if code is None:
        return None
    from diagnose.codes import REGISTRY       # imported here: core/ should not need diagnose/ just to load
    if code not in REGISTRY:
        raise EvidenceError(f"'{code}' is not a finding code. Codes are listed in diagnose/codes.py "
                            f"(for example {', '.join(sorted(REGISTRY)[:3])}).")
    return code


def parse_day(text: str | None, what: str) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise EvidenceError(f"{what} must be a date like 2026-10-14, not '{text}'") from None


def add_evidence(conn, *, business: dict, checkpoint: str, file: Path, note: str | None,
                 evidence_type: str | None = None, source: str | None = None, captured: str | None = None,
                 period_start: str | None = None, period_end: str | None = None, finding: str | None = None,
                 internal: bool = False, operator: str, clients_root: Path | None = None,
                 today: date | None = None) -> dict:
    """Checks everything, copies the file into evidence/, then records it. Nothing is copied if any check fails."""
    check_checkpoint(checkpoint, required=True)
    check_finding_code(finding)
    if source is not None and source not in SOURCES:
        raise EvidenceError(f"--from must be one of: {', '.join(SOURCES)}")
    captured_on = parse_day(captured, "--captured")
    start, end = parse_day(period_start, "--period-start"), parse_day(period_end, "--period-end")
    if (start is None) != (end is None):
        raise EvidenceError("give --period-start and --period-end together, or neither")
    if start and end and end < start:
        raise EvidenceError("--period-end is before --period-start")
    file = Path(file)
    if not file.is_file():
        raise EvidenceError(f"file not found: {file}")

    # The date in the name is when the evidence is FROM (the captured date) if we were told, else today.
    name = f"{(captured_on or today or date.today()).isoformat()}_{client_files.safe_name(file.name)}"
    folder = client_files.evidence_dir(business["domain"], clients_root)
    stored = client_files.copy_new(file, folder, name)
    relative = f"evidence/{stored.name}"

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO evidence (business_id, file_path, evidence_type, source, captured_on, period_start, "
            "period_end, checkpoint, note, finding_code, client_visible, operator) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at",
            (business["id"], relative, evidence_type, source, captured_on, start, end, checkpoint, note,
             finding, not internal, operator))
        evidence_id, created_at = cur.fetchone()
    return {"id": str(evidence_id), "file_path": relative, "stored_at": stored, "created_at": created_at}


def list_evidence(conn, business_id: str, checkpoint: str | None = None) -> list[dict]:
    if checkpoint is not None:
        check_checkpoint(checkpoint)
    sql = ("SELECT id, file_path, evidence_type, source, captured_on, period_start, period_end, checkpoint, note, "
           "finding_code, client_visible, operator, created_at FROM evidence WHERE business_id = %s")
    args: list = [business_id]
    if checkpoint is not None:
        sql += " AND checkpoint = %s"
        args.append(checkpoint)
    with conn.cursor() as cur:
        cur.execute(sql + " ORDER BY created_at, id", args)
        cols = ["id", "file_path", "evidence_type", "source", "captured_on", "period_start", "period_end",
                "checkpoint", "note", "finding_code", "client_visible", "operator", "created_at"]
        return [dict(zip(cols, r)) | {"id": str(r[0])} for r in cur.fetchall()]


def add_work(conn, *, business: dict, text: str, operator: str, finding: str | None = None,
             checkpoint: str | None = None, file: Path | None = None, clients_root: Path | None = None,
             today: date | None = None) -> dict:
    """Records one piece of work. With `file`, the file is stored as evidence (which needs a checkpoint)."""
    if not text.strip():
        raise EvidenceError("the work entry is empty")
    check_checkpoint(checkpoint)
    check_finding_code(finding)
    if file is not None and checkpoint is None:
        raise EvidenceError("--file is stored as evidence, and evidence needs a checkpoint: add --checkpoint "
                            "(before, after_site, after_aeo or check)")
    stored = None
    if file is not None:
        stored = add_evidence(conn, business=business, checkpoint=checkpoint, file=file, note=text,
                              evidence_type="work_file", source="us", finding=finding, operator=operator,
                              clients_root=clients_root, today=today)
    entry_id = write_journal(
        business_id=business["id"], entry_type="work", actor=operator, description=text.strip(), conn=conn,
        related_table="evidence" if stored else None, related_id=stored["id"] if stored else None,
        details={"finding": finding, "checkpoint": checkpoint, "file": stored["file_path"] if stored else None})
    return {"id": str(entry_id), "evidence": stored}


def add_event(conn, *, business: dict, text: str, operator: str) -> dict:
    if not text.strip():
        raise EvidenceError("the event is empty")
    entry_id = write_journal(business_id=business["id"], entry_type="event", actor=operator,
                             description=text.strip(), conn=conn)
    return {"id": str(entry_id)}
