"""Business profile versions that came from a truth document (PLAN.md section 16).

A version is never edited or deleted (the database refuses, migration 018): a correction is a new version.
Claim checking reads facts ONLY through `confirmed_facts`, so an unconfirmed fact can never be used to
mark an answer wrong, and the story is not reachable from there at all.
"""

from __future__ import annotations

from psycopg.types.json import Jsonb

CONFIRMED, UNCONFIRMED = "confirmed", "unconfirmed"
STORY_FIELDS = ("description", "goals", "customers", "customer_questions")


def confirmed_facts(facts: list[dict] | None) -> list[dict]:
    """The shape claim checking wants: [{"field": label, "value": text}], confirmed facts only."""
    return [{"field": f["label"], "value": f["value"]} for f in (facts or [])
            if f.get("status") == CONFIRMED and f.get("value")]


def latest_profile(conn, business_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, version, facts, story FROM business_profiles WHERE business_id = %s "
                    "ORDER BY version DESC LIMIT 1", (business_id,))
        r = cur.fetchone()
    return {"id": str(r[0]), "version": r[1], "facts": r[2] or [], "story": r[3] or {}} if r else None


def save_new_version(conn, business_id: str, *, facts: list[dict], story: dict) -> tuple[str, int]:
    """Adds the next version (source 'client') and returns (profile id, version). Older rows are not touched."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO business_profiles (business_id, version, source, facts, story) "
            "SELECT %s, COALESCE(MAX(version), 0) + 1, 'client', %s, %s FROM business_profiles WHERE business_id = %s "
            "RETURNING id, version", (business_id, Jsonb(facts), Jsonb(story), business_id))
        profile_id, version = cur.fetchone()
    return str(profile_id), version
