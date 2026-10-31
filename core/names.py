"""The names a business is known by (PLAN.md step 25h). Plain code: no AI.

A business has one canonical name, any number of aliases, and its domain (always an alias). The canonical name is what
reports show and what most brand questions use. Every name, and the domain, counts as the same business when an AI answer
is read. Changing the canonical name or adding an alias is a new row in `business_names` (migration 023); nothing is
overwritten or deleted. `businesses.name` always holds the current canonical name.

Matching is strict on purpose. Two names are the same business only when, after the writing is tidied (case, accents,
apostrophes, "the", a closing "Inc" or "Ltd"), they are the same words. "Maxim's Family Dental" is NOT "Maxim's Family
Dentist": one different word is a different name, and a wrong match would credit another business's answer to our client.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import psycopg

from core.businesses import BusinessError, parse_site
from core.journal import write_journal

ACTOR = "aeo names"

# Closing words that are not part of what a business is called. Only these; anything else is a different name.
_LEGAL_ENDINGS = {"inc", "incorporated", "ltd", "limited", "llc", "corp", "corporation", "ltee", "gmbh", "plc"}
MIN_LETTERS = 4                      # a shorter name or domain word is too easy to hit by accident


def name_key(text: str | None) -> str:
    """A name in a form two writings of the same name share: lower case, no accents, no apostrophes, words only, without a
    leading "the" or a closing legal ending. "Maxim’s Dentistry, Inc." and "maxims dentistry" give the same key."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"['‘’ʼ`]", "", text)             # Maxim's -> maxims, not "maxim s"
    text = text.replace("&", " and ")
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in _LEGAL_ENDINGS:
        words = words[:-1]
    return " ".join(words)


def host_of(text: str | None) -> str | None:
    """The web host in an address or a bare domain ("https://www.Example.com/about" -> "example.com"), or None."""
    if not text or not text.strip():
        return None
    try:
        domain, _ = parse_site(text.strip())
    except BusinessError:
        return None
    return domain if "." in domain and re.fullmatch(r"[a-z0-9.-]+", domain) else None


@dataclass
class Identity:
    """Everything the business may be called, for matching an answer to it."""
    canonical: str | None
    aliases: list[str] = field(default_factory=list)       # aliases only (not the canonical name, not the domain)
    domain: str | None = None
    former: list[str] = field(default_factory=list)        # earlier canonical names (still recognised in answers)

    def names(self) -> list[str]:
        """Canonical first, then aliases, then former canonical names; each once."""
        out: list[str] = []
        for n in [self.canonical, *self.aliases, *self.former]:
            if n and name_key(n) not in {name_key(o) for o in out}:
                out.append(n)
        return out

    def _keys(self) -> dict[str, str]:
        """key -> the name it came from, for every name and the domain's own forms."""
        keys: dict[str, str] = {}
        for n in self.names():
            k = name_key(n)
            if k:
                keys.setdefault(k, n)
        if self.domain:
            host = self.domain.lower().removeprefix("www.")
            for form in (host, host.split(".")[0]):
                k = name_key(form)
                if len(k.replace(" ", "")) >= MIN_LETTERS:
                    keys.setdefault(k, self.domain)
        return keys

    def match(self, name: str | None, domain: str | None = None) -> str | None:
        """Which of this business's names (or its domain) the answer's business is, or None if it is another business.

        Same words only. The domain also matches when the answer gives it as a web address, with or without www. A name
        that merely contains ours, or ours with one word different, does not match."""
        if self.domain:
            ours = self.domain.lower().removeprefix("www.")
            for given in (domain, name):
                if host_of(given) == ours:
                    return self.domain
        key = name_key(name)
        if key:
            hit = self._keys().get(key)
            if hit:
                return hit
        # The compact form catches "MaximsDentistry" for "Maxim's Dentistry", and nothing looser than that.
        compact = key.replace(" ", "")
        if len(compact) >= MIN_LETTERS:
            for k, source in self._keys().items():
                if k.replace(" ", "") == compact:
                    return source
        return None


def other_names(business: dict) -> list[str]:
    """Every name besides the canonical one that a loaded business dict carries: its aliases, then earlier canonical names."""
    return list(dict.fromkeys([*(business.get("aliases") or []), *(business.get("former_names") or [])]))


def names_in(text: str, names: list[str]) -> list[str]:
    """Which of `names` the text uses, as whole words. When one name sits inside a longer one that is also used, only the
    longer is counted ("Maxim's Dentistry" is not 'used' by a question that says "Maxim's Dentistry Plus")."""
    words = name_key(text).split()
    padded = f" {' '.join(words)} "
    spans: list[tuple[int, int, str]] = []
    for n in names:
        key = name_key(n)
        if not key:
            continue
        for m in re.finditer(rf"(?<= ){re.escape(key)}(?= )", padded):
            spans.append((m.start(), m.end(), n))
    spans = [s for s in spans if not any((o[0] <= s[0] and s[1] <= o[1]) and (o[1] - o[0]) > (s[1] - s[0]) for o in spans)]
    return list(dict.fromkeys(s[2] for s in spans))


# ---- the database --------------------------------------------------------------------------------------------------------------

def get_identity(conn: psycopg.Connection, business_id: str) -> Identity:
    """Reads the current canonical name, the aliases (oldest first) and the earlier canonical names."""
    with conn.cursor() as cur:
        cur.execute("SELECT name, domain FROM businesses WHERE id = %s", (business_id,))
        row = cur.fetchone()
        if row is None:
            raise BusinessError(f"no business with id {business_id}")
        canonical, domain = row
        cur.execute("SELECT name, role FROM business_names WHERE business_id = %s ORDER BY seq", (business_id,))
        rows = cur.fetchall()
    aliases = [n for n, role in rows if role == "alias"]
    former = [n for n, role in rows if role == "canonical"]
    keep = name_key(canonical)
    aliases = [n for n in dict.fromkeys(aliases) if name_key(n) != keep]
    seen = {name_key(n) for n in aliases} | {keep}
    former = [n for n in dict.fromkeys(former) if name_key(n) not in seen]
    return Identity(canonical=canonical, aliases=aliases, domain=domain, former=former)


def history(conn: psycopg.Connection, business_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT name, role, set_by, created_at FROM business_names WHERE business_id = %s ORDER BY seq",
                    (business_id,))
        return [{"name": r[0], "role": r[1], "set_by": r[2], "when": r[3]} for r in cur.fetchall()]


def _clean(name: str | None) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise BusinessError("a name cannot be empty")
    if len(name_key(cleaned).replace(" ", "")) < 2:
        raise BusinessError(f"'{cleaned}' is not a usable name")
    return cleaned


def set_names(conn: psycopg.Connection, business_id: str, *, canonical: str | None = None, aliases: list[str] | None = None,
              operator: str) -> dict:
    """Sets the canonical name and/or adds aliases, in one step. Returns what changed.

    - A new canonical name is written as a new row and into businesses.name; the earlier name stays in the history, and is
      still recognised in answers.
    - An alias that is already known (the canonical name, an alias, or the same words written differently) adds nothing.
    - An alias that is the domain is not stored: the domain is always an alias.
    - Nothing is deleted. Every change is noted in the client journal.
    """
    aliases = aliases or []
    if canonical is None and not aliases:
        raise BusinessError("nothing to set: give --canonical, --alias, or both")
    canonical = _clean(canonical) if canonical is not None else None
    aliases = [_clean(a) for a in aliases]

    result = {"canonical": {"changed": False, "from": None, "to": None}, "aliases_added": [], "aliases_already_known": []}
    with conn.transaction():
        conn.execute("SELECT set_config('aeo.name_set_by', %s, true)", (operator,))
        row = conn.execute("SELECT name, domain FROM businesses WHERE id = %s FOR UPDATE", (business_id,)).fetchone()
        if row is None:
            raise BusinessError(f"no business with id {business_id}")
        old, domain = row
        result["canonical"].update({"from": old, "to": old})
        if canonical is not None and canonical != old:
            # The trigger from migration 023 writes the canonical row.
            conn.execute("UPDATE businesses SET name = %s WHERE id = %s", (canonical, business_id))
            result["canonical"].update({"changed": True, "to": canonical})
            write_journal(business_id=business_id, entry_type="note", actor=ACTOR, conn=conn,
                          description=f"Canonical name set to '{canonical}' (was '{old or 'not set'}')",
                          related_table="businesses", related_id=business_id,
                          details={"canonical_name": {"from": old, "to": canonical}})
        for alias in aliases:
            # Known means the canonical name, an alias or the domain. An earlier canonical name does not count: a person who
            # makes the old name an alias on purpose (the usual case) means it to be listed, and used, as one.
            now = get_identity(conn, business_id)
            already = Identity(now.canonical, now.aliases, now.domain).match(alias) is not None
            if already:
                result["aliases_already_known"].append(alias)
                continue
            conn.execute("INSERT INTO business_names (business_id, name, role, set_by) VALUES (%s, %s, 'alias', %s)",
                         (business_id, alias, operator))
            result["aliases_added"].append(alias)
            write_journal(business_id=business_id, entry_type="note", actor=ACTOR, conn=conn,
                          description=f"Alias added: '{alias}'", related_table="businesses", related_id=business_id,
                          details={"alias_added": alias})
    return result

