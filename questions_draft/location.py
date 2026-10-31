"""Where the business is, and whether a drafted question carries a place (PLAN.md step 25e). Plain code: no AI, no I/O.

A customer choosing a local business types a place ("emergency dentist Toronto", "near me"). A discovery question with
no place is not what a customer types, so the draft rejects it. This module does two jobs:

  1. find_place: the place to use. The operator's --location wins; else the address in the page's structured data; else
     an address in the page's text. It never guesses: no place found means None, and the caller stops and asks.
  2. anchor: does one question carry a place. The place counts if it is the location (accents and capitals ignored),
     another place the site names, a proper-noun place the site's own text contains, or "near me" and its kin.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

# "Near me" in the languages we have met. A question in another language can still name the place itself.
NEAR_ME = (
    "near me", "nearby", "close by", "around me", "closest to me", "nearest to me", "near my location",   # en
    "pres de moi", "a proximite", "autour de moi", "pres de chez moi", "pas loin",                       # fr (folded)
    "cerca de mi", "cerca de aqui", "por aqui",                                                            # es
    "in der nahe", "in meiner nahe",                                                                       # de
)

_CA_PROVINCES = "AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT"
_NAME = r"[A-ZÀ-ÝÄÖÜ][\w'’.\-]*(?: [A-ZÀ-ÝÄÖÜ][\w'’.\-]*){0,2}"
# "…, Toronto, ON M5V 2T6" and "…, Austin, TX 78701". The comma before the city keeps the street out of the name.
_ADDRESS = (
    re.compile(rf",\s*({_NAME}),?\s+(?:{_CA_PROVINCES})\s+[A-Z]\d[A-Z]\s?\d[A-Z]\d"),
    re.compile(rf",\s*({_NAME}),\s*[A-Z]{{2}}\s+\d{{5}}(?:-\d{{4}})?\b"),
)


def fold(text: str) -> str:
    """Lower case, no accents, hyphens and apostrophes as spaces, one space between words."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    return " ".join(re.sub(r"[-'’]", " ", t).split())


def _has(haystack_folded: str, needle: str) -> bool:
    n = fold(needle)
    return bool(n) and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", haystack_folded) is not None


@dataclass
class Place:
    location: str | None = None                          # the one place to use, or None
    source: str = ""                                     # in words, for the operator
    known: list[str] = field(default_factory=list)       # other places the site names (areas served, region, other towns)
    by_language: dict[str, str] = field(default_factory=dict)   # language -> how the site writes the location in it


@dataclass
class PlaceRule:
    """What a question must carry. Built once per run and handed to the checks."""
    required: bool
    location: str | None
    known: list[str] = field(default_factory=list)
    by_language: dict[str, str] = field(default_factory=dict)
    corpus: str = ""                                     # the site's own text, as written (for the proper-noun check)


# ---- reading the site --------------------------------------------------------------------------------------------------------

def _strings(value, language: str | None = None) -> list[tuple[str, str | None]]:
    """Every (text, language) a JSON-LD value holds. A language-tagged value is {"@language": "fr", "@value": "Montréal"}."""
    if isinstance(value, str):
        return [(value.strip(), language)] if value.strip() else []
    if isinstance(value, dict):
        if "@value" in value:
            return _strings(value["@value"], value.get("@language") or language)
        return _strings(value.get("name"), language)
    if isinstance(value, list):
        return [pair for v in value for pair in _strings(v, language)]
    return []


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _from_structured_data(blocks: list) -> tuple[list[tuple[str, str | None]], list[str]]:
    """(localities as (name, language), other places: regions and areas served), in the order the page gives them."""
    localities: list[tuple[str, str | None]] = []
    others: list[str] = []
    for raw in blocks or []:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        for node in _walk(data):
            address = node.get("address")
            for a in (address if isinstance(address, list) else [address]):
                if isinstance(a, dict):
                    localities += _strings(a.get("addressLocality"))
                    others += [t for t, _ in _strings(a.get("addressRegion")) if len(t) > 2]
            if "areaServed" in node:
                others += [t for t, _ in _strings(node["areaServed"])]
    return localities, others


def _from_page_text(text: str) -> str | None:
    for pattern in _ADDRESS:
        m = pattern.search(text or "")
        if m:
            return m.group(1).strip(" .,")
    return None


def find_place(blocks: list, page_text: str, operator: str | None) -> Place:
    """The place to use, with where it came from. Never invents one."""
    localities, others = _from_structured_data(blocks)
    by_language: dict[str, str] = {}
    for name, lang in localities:
        if lang:
            by_language.setdefault(lang.lower().split("-")[0], name)
    plain = [n for n, _ in localities]
    known: list[str] = []
    for n in [*plain, *others]:
        if n and not any(fold(n) == fold(k) for k in known):
            known.append(n)

    if operator and operator.strip():
        location, source = operator.strip(), "given with --location"
    elif plain:
        location, source = plain[0], "the address in the page's structured data"
    else:
        location = _from_page_text(page_text)
        source = "an address written in the page's text" if location else ""
    known = [k for k in known if location is None or fold(k) != fold(location)]
    return Place(location=location, source=source, known=known, by_language=by_language)


# ---- checking a question --------------------------------------------------------------------------------------------------------

def _is_proper_noun_of_site(place: str, corpus: str) -> bool:
    """A place word the model says it took from the site: the site's text must hold it, and always as a name (a word that
    the site also writes in lower case, such as "dentist", is a common word and not a place)."""
    n = fold(place)
    if not n or not corpus:
        return False
    found = False
    for m in re.finditer(rf"(?<!\w){re.escape(place.strip())}(?!\w)", corpus, flags=re.I):
        found = True
        if m.group(0)[0].islower():
            return False
    if found:
        return True
    return _has(fold(corpus), place)        # the same name with other accents or hyphens (the caller checks the capital)


def anchor(rule: PlaceRule, text: str, claimed: str | None = None) -> str | None:
    """How the question carries a place, in words, or None if it carries none.
    `claimed` is the place word the model says it used; it counts only if it is in the question and the site names it."""
    t = fold(text)
    if rule.location and _has(t, rule.location):
        return "the location"
    for name in rule.by_language.values():
        if _has(t, name):
            return "the location"
    for k in rule.known:
        if _has(t, k):
            return f"a place the site names ({k})"
    if isinstance(claimed, str) and claimed.strip() and _has(t, claimed) and claimed.strip()[:1].isupper() \
            and _is_proper_noun_of_site(claimed, rule.corpus):
        return f"a place from the site's text ({claimed.strip()})"
    if any(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", t) for p in NEAR_ME):
        return "near me"
    return None


def language_issue(rule: PlaceRule, text: str, language: str | None) -> str | None:
    """The site writes the place differently in this question's language, and the question does not use that form."""
    name = rule.by_language.get((language or "").lower())
    if not name:
        return None
    t = fold(text)
    if _has(t, name):
        return None
    others = [rule.location, *rule.known]
    used = next((o for o in others if o and _has(t, o)), None)
    if used and fold(used) != fold(name):
        return f"it uses the place as \"{used}\" but the site writes it \"{name}\" in {language}"
    return None
