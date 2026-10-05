"""Creating a business. Used by `aeo add`.

This is deliberately bare (PLAN.md section 15 defers the questionnaire and the
profiler): a businesses row, and an empty version-1 profile so that anything
that needs a profile (like `aeo audit`) has a real row to point at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
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


@dataclass
class AddResult:
    created: bool        # False when the person confirmed it is a business we already have
    business: dict


def _describe(row: dict) -> str:
    return f"{row['name'] or 'no name'} (id {row['id']}, domain {row['domain']})"


def find_by_domain(conn, domain: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, domain FROM businesses WHERE domain = %s", (domain,))
        r = cur.fetchone()
    return {"id": str(r[0]), "name": r[1], "domain": r[2]} if r else None


def find_by_name(conn, name: str) -> list[dict]:
    """Businesses whose name is the same, ignoring case and surrounding spaces. Oldest first."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, domain FROM businesses WHERE lower(btrim(name)) = lower(btrim(%s)) "
                    "ORDER BY created_at", (name,))
        return [{"id": str(r[0]), "name": r[1], "domain": r[2]} for r in cur.fetchall()]


def add_with_checks(raw_url: str, name: str | None = None, *, same_business: str | None = None,
                    force_new: bool = False, interactive: bool,
                    confirm: Callable[[str], bool]) -> AddResult:
    """`add_business`, after three checks (all before anything is inserted):

    a) The domain already exists: always refused, naming the business that has it. Nothing overrides this.
    b) Another business has the same name but a different domain: it might be the same business at a new
       address, so a person must say. Interactively we ask (`confirm`, default no). Not interactively we
       refuse, unless --same-business <id> (it is that one) or --force-new (it is a different one) was given.
    c) Neither: add it.

    The flags do nothing when no business has the same name.
    """
    if same_business and force_new:
        raise BusinessError("--same-business and --force-new contradict each other; use one.")
    domain, _ = parse_site(raw_url)

    conn = get_connection()
    try:
        existing = find_by_domain(conn, domain)
        if existing:
            raise BusinessError(f"{domain} is already added, as {_describe(existing)}. Nothing was changed.")
        matches = find_by_name(conn, name) if name else []
    finally:
        conn.close()

    if matches:
        if same_business:
            chosen = next((m for m in matches if m["id"] == same_business), None)
            if chosen is None:
                raise BusinessError(f"--same-business {same_business} is not one of the businesses named '{name}': "
                                    + "; ".join(_describe(m) for m in matches) + ". Nothing was changed.")
            return AddResult(created=False, business=chosen)
        if not force_new:
            if not interactive:
                raise BusinessError(
                    f"A business named '{name}' already exists: " + "; ".join(_describe(m) for m in matches)
                    + ". A person has to say whether this is the same business at a different web address. "
                    "Run again with --same-business <id> if it is, or --force-new if it is a different business. "
                    "Nothing was changed.")
            for m in matches:
                if confirm(f"A business named '{name}' already exists (id {m['id']}, domain {m['domain']}). "
                           "Is this the same business at a different web address?"):
                    return AddResult(created=False, business=m)
    return AddResult(created=True, business=add_business(raw_url, name))


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
                    # Only reachable if two adds race each other; add_with_checks looks first.
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
