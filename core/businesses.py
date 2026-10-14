"""Creating a business. Used by `aeo add`.

This is deliberately bare (PLAN.md section 15 defers the questionnaire and the
profiler): a businesses row, and an empty version-1 profile so that anything
that needs a profile (like `aeo audit`) has a real row to point at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from uuid import UUID
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


# ---- finding a business from whatever the person typed ------------------------------------------

def resolve_business(conn, ref: str) -> dict:
    """`--business` accepts an id, a domain or a name. Returns {id, name, domain}.

    All three are tried and every business that matches is collected. Exactly one match is the
    answer; none or several is an error that says what was found, so a wrong business is never
    picked silently.
    """
    ref = (ref or "").strip()
    if not ref:
        raise BusinessError("no business given (use an id, a domain or a name)")

    found: dict[str, dict] = {}
    try:
        UUID(ref)
    except ValueError:
        pass
    else:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, domain FROM businesses WHERE id = %s", (ref,))
            for r in cur.fetchall():
                found[str(r[0])] = {"id": str(r[0]), "name": r[1], "domain": r[2]}
    try:
        domain, _ = parse_site(ref)
    except BusinessError:
        domain = None
    if domain:
        match = find_by_domain(conn, domain)
        if match:
            found[match["id"]] = match
    for match in find_by_name(conn, ref):
        found[match["id"]] = match

    if not found:
        raise BusinessError(f"no business matches '{ref}' (tried it as an id, a domain and a name)")
    if len(found) > 1:
        raise BusinessError(f"'{ref}' matches more than one business: " + "; ".join(_describe(m) for m in found.values())
                            + ". Use the id of the one you mean.")
    return next(iter(found.values()))


# ---- engagement type and access level (PLAN.md section 2) -----------------------------------------

ENGAGEMENT_TYPES = ("aeo_only", "web_dev_aeo", "web_dev_only", "migration_pitch", "prospect")
ACCESS_LEVELS = ("none", "cms", "hosting", "full")


def set_engagement(conn, business_id: str, *, engagement_type: str | None, access_level: str | None,
                   actor: str) -> dict:
    """Sets either or both. Returns {changed, engagement_type, access_level}. A change writes a journal note.

    Setting a value to what it already is changes nothing and writes nothing.
    """
    if engagement_type is not None and engagement_type not in ENGAGEMENT_TYPES:
        raise BusinessError(f"'{engagement_type}' is not an engagement type. Valid: {', '.join(ENGAGEMENT_TYPES)}")
    if access_level is not None and access_level not in ACCESS_LEVELS:
        raise BusinessError(f"'{access_level}' is not an access level. Valid: {', '.join(ACCESS_LEVELS)}")
    if engagement_type is None and access_level is None:
        raise BusinessError("nothing to set: give --type, --access or both")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT engagement_type, access_level FROM businesses WHERE id = %s FOR UPDATE", (business_id,))
            row = cur.fetchone()
            if row is None:
                raise BusinessError(f"no business with id {business_id}")
            old_type, old_access = row
            new_type = old_type if engagement_type is None else engagement_type
            new_access = old_access if access_level is None else access_level
            if (new_type, new_access) == (old_type, old_access):
                return {"changed": False, "engagement_type": new_type, "access_level": new_access}
            cur.execute("UPDATE businesses SET engagement_type = %s, access_level = %s WHERE id = %s",
                        (new_type, new_access, business_id))
        write_journal(
            business_id=business_id, entry_type="note", actor=actor,
            description=f"Engagement set: type {new_type or 'not set'} (was {old_type or 'not set'}), "
                        f"access {new_access or 'not set'} (was {old_access or 'not set'})",
            related_table="businesses", related_id=business_id, conn=conn,
            details={"engagement_type": {"from": old_type, "to": new_type},
                     "access_level": {"from": old_access, "to": new_access}})
    return {"changed": True, "engagement_type": new_type, "access_level": new_access}


def get_engagement(conn, business_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT engagement_type, access_level FROM businesses WHERE id = %s", (business_id,))
        r = cur.fetchone()
    return {"engagement_type": r[0], "access_level": r[1]}
