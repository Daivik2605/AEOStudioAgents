"""Database reads and writes for `aeo diagnose`.

Journal rows go through core/journal.write_journal (the only function allowed
to write client_journal). The decline flow uses the same method `aeo status`
will use (PLAN.md section 7): update businesses.status, let the existing
trigger write the status_change row, then add a `note` linked to it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from core.journal import write_journal

ACTOR = "diagnose"


def load_business(conn: psycopg.Connection, business_id: str) -> dict | None:
    """The business plus the facts we can check the page against, or None if it does not exist."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, domain, website_url, status FROM businesses WHERE id = %s", (business_id,))
        row = cur.fetchone()
        if row is None:
            return None
        business = {"id": str(row[0]), "name": row[1], "domain": row[2], "website_url": row[3], "status": row[4],
                    "alternate_names": [], "phone": None, "address": None}
        # The newest profile version has the best-known phone, address and other names.
        cur.execute(
            "SELECT alternate_names, phone, address FROM business_profiles "
            "WHERE business_id = %s ORDER BY version DESC LIMIT 1", (business_id,))
        profile = cur.fetchone()
        if profile:
            business["alternate_names"] = list(profile[0] or [])
            business["phone"], business["address"] = profile[1], profile[2]
    return business


def insert_diagnosis(conn: psycopg.Connection, *, business_id: str | None, checkpoint: str | None, url: str,
                     checked_at: Any, platform: str | None, platform_version: str | None,
                     platform_plan: str | None, robots_txt: str | None, existing_jsonld: list[str],
                     crawler_access: dict, render: dict | None, findings: list[dict], verdict: str,
                     verdict_reason: str, readiness_score: float | None) -> UUID:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO diagnoses
                (business_id, checkpoint, url, checked_at, platform, platform_version, platform_plan,
                 robots_txt, existing_jsonld, crawler_access, render, findings, verdict,
                 verdict_reason, readiness_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (business_id, checkpoint, url, checked_at, platform, platform_version, platform_plan,
             robots_txt, Jsonb(existing_jsonld), Jsonb(crawler_access),
             Jsonb(render) if render is not None else None, Jsonb(findings), verdict,
             verdict_reason, readiness_score),
        )
        return cur.fetchone()[0]


def journal_diagnosed(conn: psycopg.Connection, *, business_id: str, diagnosis_id: UUID, url: str,
                      verdict: str, platform: str | None, finding_count: int) -> UUID:
    return write_journal(
        business_id=business_id, entry_type="diagnosed", actor=ACTOR,
        description=f"Diagnosed {url}: verdict {verdict}" + (f" on {platform}" if platform else ""),
        related_table="diagnoses", related_id=diagnosis_id,
        details={"verdict": verdict, "platform": platform, "finding_count": finding_count}, conn=conn)


def apply_decline(conn: psycopg.Connection, *, business_id: str, diagnosis_id: UUID, reason: str) -> str:
    """Records a decline. Returns what happened: 'declined', 'already_declined' or 'not_a_prospect'.

    Only a prospect is moved automatically. Declining someone who is already a
    client, delivered or rejected is a person's decision (PLAN.md section 12),
    so for those we change nothing and say so.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM businesses WHERE id = %s FOR UPDATE", (business_id,))
            status = cur.fetchone()[0]
            if status == "declined":
                return "already_declined"
            if status != "prospect":
                return "not_a_prospect"
            # The trigger from migration 013 writes the status_change row for us.
            cur.execute("UPDATE businesses SET status = 'declined' WHERE id = %s", (business_id,))
            cur.execute(
                "SELECT id FROM client_journal WHERE business_id = %s AND entry_type = 'status_change' "
                "AND details->>'to' = 'declined' ORDER BY created_at DESC, id LIMIT 1", (business_id,))
            status_change_id = cur.fetchone()[0]
        # PLAN.md section 7: the reason is a `note` linked to the status_change row.
        write_journal(
            business_id=business_id, entry_type="note", actor=ACTOR,
            description=f"Declined: {reason}",
            related_table="client_journal", related_id=status_change_id,
            details={"reason_for": "declined", "reason": reason}, conn=conn)
    return "declined"


def journal_decline_recommended(conn: psycopg.Connection, *, business_id: str, diagnosis_id: UUID, reason: str) -> UUID:
    return write_journal(
        business_id=business_id, entry_type="decline_recommended", actor=ACTOR,
        description="Decline recommended. A migration pitch is pending a person's approval; none has been written.",
        related_table="diagnoses", related_id=diagnosis_id,
        details={"reason": reason, "pitch_status": "pending_human_approval"}, conn=conn)
