"""Database reads and writes for `aeo audit`. The raw answer is saved the moment
it is typed in; everything derived from it is filled in afterwards."""

from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

# Profile columns that hold facts a client can confirm. Left out on purpose:
# apparent_competitors (our guess, not their fact), schema_type (technical),
# buyer_description, questions and questionnaire_answers (free-form working notes).
FACT_FIELDS = [
    "industry", "customer_type", "reach", "offering", "services",
    "location_city", "location_region", "location_country", "service_area",
    "address", "phone", "email", "hours", "social_profiles", "languages",
    "alternate_names", "proof_points",
]


def load_business_and_profile(conn: psycopg.Connection, business_id: str) -> dict | None:
    """The business plus its newest profile (id and confirmed facts), or None if there is no such business."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, domain FROM businesses WHERE id = %s", (business_id,))
        row = cur.fetchone()
        if row is None:
            return None
        business = {"id": str(row[0]), "name": row[1], "domain": row[2], "profile_id": None, "facts": []}
        cur.execute(f"SELECT id, {', '.join(FACT_FIELDS)} FROM business_profiles "
                    "WHERE business_id = %s ORDER BY version DESC LIMIT 1", (business_id,))
        profile = cur.fetchone()
    if profile:
        business["profile_id"] = str(profile[0])
        # An empty list or an empty string is not a fact, any more than NULL is.
        business["facts"] = [{"field": f, "value": v} for f, v in zip(FACT_FIELDS, profile[1:])
                             if v not in (None, "", [], {})]
    return business


def create_run(conn: psycopg.Connection, *, business: dict, checkpoint: str | None) -> UUID:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO probe_runs (business_id, business_profile_id, run_type, mode, status, started_at, checkpoint) "
            "VALUES (%s, %s, 'visibility', 'manual', 'in_progress', now(), %s) RETURNING id",
            (business["id"], business["profile_id"], checkpoint))
        return cur.fetchone()[0]


def next_repeat_number(conn: psycopg.Connection, probe_run_id, question: str, engine: str) -> int:
    """1 for the first answer to this question on this engine in this run, then 2, 3 ... Never overwrites."""
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(repeat_number), 0) + 1 FROM probe_results "
                    "WHERE probe_run_id = %s AND query_text = %s AND engine = %s",
                    (probe_run_id, question, engine))
        return cur.fetchone()[0]


def add_result(conn: psycopg.Connection, *, probe_run_id, question: str, engine: str, raw_answer: str,
               retrieval_activated: bool | None, engine_version: str | None, logged_in_state: str,
               location_context: str | None, question_set_version: str) -> tuple[UUID, int]:
    repeat = next_repeat_number(conn, probe_run_id, question, engine)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO probe_results (probe_run_id, query_text, location_context, engine, repeat_number, "
            "raw_response_text, retrieval_activated, engine_version, logged_in_state, question_set_version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (probe_run_id, question, location_context, engine, repeat, raw_answer,
             retrieval_activated, engine_version, logged_in_state, question_set_version))
        return cur.fetchone()[0], repeat


def results_to_extract(conn: psycopg.Connection, probe_run_id) -> list[dict]:
    """Answers that have not been through extraction yet (businesses_named is still NULL)."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, query_text, raw_response_text FROM probe_results "
                    "WHERE probe_run_id = %s AND businesses_named IS NULL ORDER BY created_at, id", (probe_run_id,))
        return [{"id": r[0], "question": r[1], "answer": r[2]} for r in cur.fetchall()]


def save_extraction(conn: psycopg.Connection, result_id, *, businesses: list[dict], named_any: bool,
                    recognized: bool, recommended: bool) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE probe_results SET businesses_named = %s, named_any_business = %s, "
                    "recognized = %s, recommended = %s WHERE id = %s",
                    (Jsonb(businesses), named_any, recognized, recommended, result_id))


def save_claims(conn: psycopg.Connection, result_id, claims: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE probe_results SET claims_checked = %s WHERE id = %s", (Jsonb(claims), result_id))


def finish_run(conn: psycopg.Connection, probe_run_id, *, complete: bool) -> None:
    with conn.cursor() as cur:
        if complete:
            cur.execute("UPDATE probe_runs SET status = 'complete', completed_at = now() WHERE id = %s", (probe_run_id,))
        else:
            cur.execute("UPDATE probe_runs SET status = 'failed' WHERE id = %s", (probe_run_id,))


def summary_counts(conn: psycopg.Connection, probe_run_id) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(*) FILTER (WHERE recognized), count(*) FILTER (WHERE recommended), "
            "count(*) FILTER (WHERE businesses_named IS NULL) FROM probe_results WHERE probe_run_id = %s",
            (probe_run_id,))
        total, named, recommended, not_analysed = cur.fetchone()
    return {"answers": total, "named_business": named, "recommended_business": recommended,
            "not_analysed": not_analysed}

