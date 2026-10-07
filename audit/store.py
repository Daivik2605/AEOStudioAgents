"""Database reads and writes for `aeo audit`. The raw answer is saved the moment
it is typed in; everything derived from it is filled in afterwards."""

from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from core.journal import write_journal

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
               location_context: str | None, question_set_version: str,
               sources_cited: list[dict] | None = None, repeat_number: int | None = None) -> tuple[UUID, int]:
    """Saves one raw answer. Terminal mode lets the database count the repeat; a fill-in file
    states it (its block says "run 2 of 3"), so the caller passes it."""
    repeat = repeat_number or next_repeat_number(conn, probe_run_id, question, engine)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO probe_results (probe_run_id, query_text, location_context, engine, repeat_number, "
            "raw_response_text, retrieval_activated, engine_version, logged_in_state, question_set_version, "
            "sources_cited) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (probe_run_id, question, location_context, engine, repeat, raw_answer,
             retrieval_activated, engine_version, logged_in_state, question_set_version,
             Jsonb(sources_cited) if sources_cited is not None else None))
        return cur.fetchone()[0], repeat


def results_to_extract(conn: psycopg.Connection, probe_run_id, only_ids=None) -> list[dict]:
    """Answers that have not been through extraction yet (businesses_named is still NULL).
    `only_ids` limits it to rows just saved, so an import never re-reads older rows."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, query_text, raw_response_text FROM probe_results "
                    "WHERE probe_run_id = %s AND businesses_named IS NULL ORDER BY created_at, id", (probe_run_id,))
        rows = [{"id": r[0], "question": r[1], "answer": r[2]} for r in cur.fetchall()]
    if only_ids is not None:
        wanted = set(only_ids)
        rows = [r for r in rows if r["id"] in wanted]
    return rows


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



def named_businesses_by_answer(conn: psycopg.Connection, probe_run_id) -> list[list[dict]]:
    """For each answer that has been through extraction: the businesses it named."""
    with conn.cursor() as cur:
        cur.execute("SELECT businesses_named FROM probe_results WHERE probe_run_id = %s "
                    "AND businesses_named IS NOT NULL ORDER BY created_at, id", (probe_run_id,))
        return [row[0] for row in cur.fetchall()]


ACTOR = "aeo audit"


def start_run(conn: psycopg.Connection, *, business: dict, qs, checkpoint: str | None, engines: list[str],
              answers_expected: int, how: str, extra: dict | None = None) -> UUID:
    """Creates the probe_run and journals probe_started. Both capture routes (terminal and fill-in file) use this.
    The journal entry holds the question-set fingerprint that the hash lock checks against."""
    run_id = create_run(conn, business=business, checkpoint=checkpoint)
    write_journal(
        business_id=business["id"], entry_type="probe_started", actor=ACTOR,
        description=f"Manual audit started ({how}): {qs.version}, {answers_expected} answers expected",
        related_table="probe_runs", related_id=run_id, conn=conn,
        details={"probe_run_id": str(run_id), "question_set_version": qs.version,
                 "question_set_sha256": qs.fingerprint, "checkpoint": checkpoint, "engines": engines,
                 "answers_expected": answers_expected, "capture": how, **(extra or {})})
    return run_id


def complete_run(conn: psycopg.Connection, *, business_id: str, run_id, answers: int) -> None:
    finish_run(conn, run_id, complete=True)
    write_journal(business_id=business_id, entry_type="probe_completed", actor=ACTOR,
                  description=f"Manual audit complete: {answers} answers collected",
                  related_table="probe_runs", related_id=run_id, conn=conn,
                  details={"probe_run_id": str(run_id), "answers": answers})


def get_run(conn: psycopg.Connection, run_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, business_id, status, checkpoint, run_type, mode FROM probe_runs WHERE id = %s", (run_id,))
        r = cur.fetchone()
    return {"id": str(r[0]), "business_id": str(r[1]), "status": r[2], "checkpoint": r[3],
            "run_type": r[4], "mode": r[5]} if r else None


def started_details(conn: psycopg.Connection, run_id) -> dict | None:
    """The probe_started journal details for this run (question set version and fingerprint, repeats...)."""
    with conn.cursor() as cur:
        cur.execute("SELECT details FROM client_journal WHERE entry_type = 'probe_started' "
                    "AND details->>'probe_run_id' = %s ORDER BY created_at LIMIT 1", (str(run_id),))
        r = cur.fetchone()
    return r[0] if r else None


def saved_triples(conn: psycopg.Connection, probe_run_id) -> set[tuple[str, str, int]]:
    """(question, engine, repeat_number) of every answer already saved in this run. This is what
    a fill-in block is matched against, so importing a file twice can never duplicate a row."""
    with conn.cursor() as cur:
        cur.execute("SELECT query_text, engine, repeat_number FROM probe_results WHERE probe_run_id = %s", (probe_run_id,))
        return {(q, e, n) for q, e, n in cur.fetchall()}
