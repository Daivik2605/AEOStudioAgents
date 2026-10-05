"""Creating a business. Used by `aeo add`.

This is deliberately bare (PLAN.md section 15 defers the questionnaire and the
profiler): a businesses row, and an empty version-1 profile so that anything
that needs a profile (like `aeo audit`) has a real row to point at.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import psycopg

from core.db import get_connection
from core.journal import write_journal


class BusinessError(Exception):
    """A problem the user can fix (bad address, domain already added)."""


def parse_site(raw: str) -> tuple[str, str]:
    """Returns (domain, website_url) for whatever the user typed.

    'www.' is dropped from the domain so www.example.com and example.com
    cannot be added as two different businesses.
    """
    url = raw.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BusinessError(f"'{raw}' is not a web address (need something like https://example.com)")
    domain = parts.hostname.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain, url


def add_business(raw_url: str, name: str | None = None) -> dict:
    """Creates the business (status 'prospect') and its empty profile v1.

    Raises BusinessError if the domain is already there. Nothing is changed in that case.
    """
    domain, website_url = parse_site(raw_url)
    conn = get_connection()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO businesses (name, domain, website_url) VALUES (%s, %s, %s) RETURNING id",
                        (name, domain, website_url),
                    )
                except psycopg.errors.UniqueViolation:
                    raise BusinessError(f"{domain} is already added. Nothing was changed.") from None
                business_id = cur.fetchone()[0]
                # source='client' because the profile is meant to hold client-confirmed facts.
                # Every fact column stays empty until a real questionnaire fills it.
                cur.execute(
                    "INSERT INTO business_profiles (business_id, version, source) VALUES (%s, 1, 'client') RETURNING id",
                    (business_id,),
                )
                profile_id = cur.fetchone()[0]
            write_journal(business_id=business_id, entry_type="business_added",
                          description=f"Added {domain}", actor="aeo add",
                          related_table="businesses", related_id=business_id,
                          details={"domain": domain, "name": name}, conn=conn)
            write_journal(business_id=business_id, entry_type="profile_created",
                          description="Empty profile v1 created (no facts yet)", actor="aeo add",
                          related_table="business_profiles", related_id=profile_id,
                          details={"version": 1}, conn=conn)
    finally:
        conn.close()
    return {"id": str(business_id), "domain": domain, "website_url": website_url,
            "name": name, "profile_id": str(profile_id)}
