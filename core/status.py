"""A business's relationship status, and the one function that changes it (PLAN.md section 7).

    prospect   we have not been hired
    client     hired (the deposit is paid)
    delivered  the work is finished
    declined   WE said no
    rejected   THEY said no

businesses.status holds the current value. status_history (migration 020) holds every value it has had, who set it
and why; a database trigger writes that history, so a change made any other way (the decline flow, raw SQL) is
recorded too. This file reads it, and `set_status` is how a person changes it.

Nothing here deletes anything. Declining or rejecting a business that has audit data keeps all of that data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from core.journal import write_journal

STATUSES = ("prospect", "client", "delivered", "rejected", "declined")

# A "no" is a decision, so it always needs its reason written down. Moving to client or prospect does not.
REASON_REQUIRED = ("declined", "rejected")

ACTOR = "aeo status"


class StatusError(Exception):
    """The change cannot be made. The message says what to do."""


@dataclass
class StatusChange:
    changed: bool                 # False when the business already had this status
    old: str
    new: str
    warnings: list[str] = field(default_factory=list)


def _audit_warning(conn: psycopg.Connection, business_id: str, current: str) -> str | None:
    """Says what data exists, when someone is saying no to a business we have already worked for."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), count(*) FILTER (WHERE status = 'in_progress') FROM probe_runs WHERE business_id = %s",
                    (business_id,))
        runs, in_progress = cur.fetchone()
    paid = current in ("client", "delivered")
    if not runs and not paid:
        return None
    parts = []
    if paid:
        parts.append(f"this business is a {current} (paid work)")
    if runs:
        parts.append(f"it has {runs} audit run(s)" + (f", {in_progress} still in progress" if in_progress else ""))
    return ("Data exists: " + " and ".join(parts) + ". Nothing is deleted; all of it is kept, "
            "and the audit can still be read.")


def set_status(conn: psycopg.Connection, business_id: str, new_status: str, *, reason: str | None,
               operator: str) -> StatusChange:
    """Changes the status. The one place that does so on purpose.

    - An unknown status, or declined/rejected with no reason, is refused before anything is touched.
    - Setting the status the business already has changes nothing and adds no history row.
    - The trigger from migration 013 writes the status_change journal row and the trigger from migration 020 writes the
      history row. This passes them the person and the reason, then adds the reason as a `note` linked to the
      status_change row, the way the decline flow does.
    """
    if new_status not in STATUSES:
        raise StatusError(f"'{new_status}' is not a status. Use one of: {', '.join(STATUSES)}.")
    reason = (reason or "").strip() or None
    if new_status in REASON_REQUIRED and not reason:
        raise StatusError(f"A reason is required to set a business to {new_status}. "
                          f"Add --reason \"...\" saying why ({'we turned them down' if new_status == 'declined' else 'they turned us down'}).")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM businesses WHERE id = %s FOR UPDATE", (business_id,))
            row = cur.fetchone()
            if row is None:
                raise StatusError(f"no business with id {business_id}")
            current = row[0]
            if current == new_status:
                return StatusChange(changed=False, old=current, new=new_status)
            warnings = []
            if new_status in REASON_REQUIRED:
                warning = _audit_warning(conn, business_id, current)
                if warning:
                    warnings.append(warning)
            # Transaction-local: they vanish when this transaction ends, so they can never leak into another change.
            cur.execute("SELECT set_config('aeo.status_set_by', %s, true), set_config('aeo.status_reason', %s, true)",
                        (operator, reason or ""))
            cur.execute("UPDATE businesses SET status = %s WHERE id = %s", (new_status, business_id))
            cur.execute("SELECT id FROM client_journal WHERE business_id = %s AND entry_type = 'status_change' "
                        "AND details->>'to' = %s ORDER BY created_at DESC, id DESC LIMIT 1", (business_id, new_status))
            status_change_id = cur.fetchone()[0]
        if reason:
            write_journal(business_id=business_id, entry_type="note", actor=operator,
                          description=f"{new_status.capitalize()}: {reason}",
                          related_table="client_journal", related_id=status_change_id,
                          details={"reason_for": new_status, "reason": reason}, conn=conn)
    return StatusChange(changed=True, old=current, new=new_status, warnings=warnings)


def get_status(conn: psycopg.Connection, business_id: str) -> dict:
    """The current status, why and when it was set, and every change, newest first."""
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM businesses WHERE id = %s", (business_id,))
        row = cur.fetchone()
        if row is None:
            raise StatusError(f"no business with id {business_id}")
        cur.execute("SELECT status, reason, set_by, set_at FROM status_history WHERE business_id = %s "
                    "ORDER BY seq DESC", (business_id,))
        history = [{"status": s, "reason": r, "set_by": by, "set_at": at} for s, r, by, at in cur.fetchall()]
    latest = history[0] if history else None
    # The newest history row is the current status. If they ever disagree, the businesses row is the truth
    # (the database enforces it), and the reason shown is only the one that belongs to it.
    matches = latest is not None and latest["status"] == row[0]
    return {"status": row[0], "reason": latest["reason"] if matches else None,
            "set_at": latest["set_at"] if matches else None, "set_by": latest["set_by"] if matches else None,
            "history": history}


def list_statuses(conn: psycopg.Connection, status: str | None = None) -> list[dict]:
    """Every business: domain, status, engagement type and when the status last changed."""
    if status is not None and status not in STATUSES:
        raise StatusError(f"'{status}' is not a status. Use one of: {', '.join(STATUSES)}.")
    sql = ("SELECT b.domain, b.status, b.engagement_type, "
           "COALESCE((SELECT h.set_at FROM status_history h WHERE h.business_id = b.id ORDER BY h.seq DESC LIMIT 1), "
           "b.created_at) FROM businesses b")
    args: list = []
    if status:
        sql += " WHERE b.status = %s"
        args.append(status)
    with conn.cursor() as cur:
        cur.execute(sql + " ORDER BY b.domain", args)
        return [{"domain": d, "status": s, "engagement_type": e, "last_changed": at} for d, s, e, at in cur.fetchall()]
