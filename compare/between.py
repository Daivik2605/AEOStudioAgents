"""Work-log entries and events from the journal, for the comparison (PLAN.md section 10, "Work log and events").
Events are things outside our control that can move the numbers (an engine update, the client editing their site)."""

from __future__ import annotations

from datetime import timezone


def load_entries(conn, business_id: str) -> list[dict]:
    """Every 'work' and 'event' entry, oldest first. `date` is the UTC day, as an ISO string."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, created_at, entry_type, description, actor, details FROM client_journal "
                    "WHERE business_id = %s AND entry_type IN ('work', 'event') ORDER BY created_at, id", (business_id,))
        rows = cur.fetchall()
    return [{"id": str(r[0]), "date": r[1].astimezone(timezone.utc).date().isoformat(), "kind": r[2],
             "text": r[3], "operator": r[4], "finding": (r[5] or {}).get("finding"),
             "checkpoint": (r[5] or {}).get("checkpoint"), "outside_our_control": r[2] == "event"} for r in rows]


def between(entries: list[dict], start: str, end: str) -> list[dict]:
    """Entries dated from `start` to `end`, both days included."""
    return [e for e in entries if start <= e["date"] <= end]
