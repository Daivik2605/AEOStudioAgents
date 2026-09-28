"""The only place that writes to client_journal.

Anything in the system that needs to record "this happened to this business"
calls write_journal() instead of writing its own INSERT, so the journal stays
consistent and every entry_type is one of the values PLAN.md defines.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from core.db import get_connection

# Mirrors the CHECK constraint on client_journal.entry_type (PLAN.md section 10).
ENTRY_TYPES = {
    "business_added",
    "status_change",
    "profile_created",
    "questionnaire_received",
    "gate_checked",
    "gate_decision",
    "probe_started",
    "probe_completed",
    "score_computed",
    "audit_generated",
    "audit_sent",
    "recommendations_generated",
    "schema_deployed",
    "content_published",
    "note",
    "error",
}


def write_journal(
    *,
    business_id: str | UUID,
    entry_type: str,
    description: str,
    actor: str,
    related_table: str | None = None,
    related_id: str | UUID | None = None,
    details: dict[str, Any] | None = None,
    conn: psycopg.Connection | None = None,
) -> UUID:
    """Writes one row to client_journal and returns its id.

    entry_type is checked against the same fixed list the database enforces,
    so a typo shows up as a clear Python error instead of a raw Postgres
    constraint violation.

    Pass an existing `conn` to make this write part of a transaction the
    caller already holds open; otherwise this opens and closes its own.
    """
    if entry_type not in ENTRY_TYPES:
        raise ValueError(f"'{entry_type}' is not a valid client_journal entry_type")

    owns_connection = conn is None
    conn = conn or get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO client_journal
                    (business_id, entry_type, description, actor, related_table, related_id, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    business_id,
                    entry_type,
                    description,
                    actor,
                    related_table,
                    related_id,
                    Jsonb(details) if details is not None else None,
                ),
            )
            row = cur.fetchone()
            return row[0]
    finally:
        if owns_connection:
            conn.close()
