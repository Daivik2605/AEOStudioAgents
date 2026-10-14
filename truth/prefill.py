"""Pre-filling the truth document from the latest diagnosis (PLAN.md section 16). Plain code, no AI.

What comes out is only ever a guess for the client to check: every value is marked unconfirmed
by the caller. We read the raw JSON-LD the diagnosis stored; we never change it.
"""

from __future__ import annotations

import json
import re

from diagnose.structured_data import ORG_TYPES, _LEGAL_SUFFIX, _NUMBERED_COMPANY, _types, _walk

# A finding in the diagnosis becomes a direct question under the fact it is about (PLAN.md section 16).
# {site_name} is filled in when the site's structured data gave one.
FINDING_QUESTIONS: dict[str, tuple[str, str]] = {
    "SCHEMA_NAME_IS_LEGAL_ENTITY": ("name", "Your site's data names the business '{site_name}'. What name should AI use?"),
    "RENDER_BUSINESS_NAME_MISSING": ("name", "Your business name is not in the page text that AI reads. What name should it find there?"),
    "SCHEMA_LOCALBUSINESS_NO_AREASERVED": ("service_area", "Your site's data does not say where you work. Where do you actually serve?"),
    "SCHEMA_OPENING_HOURS_EMPTY": ("loc1_hours", "Opening hours are blank in your site's data. When are you open, including winter or other seasons?"),
    "RENDER_PHONE_MISSING": ("loc1_phone", "Your phone number is not in the page text that AI reads. Which number should customers use?"),
    "RENDER_ADDRESS_MISSING": ("loc1_address", "Your address is not in the page text that AI reads. What is it?"),
}


SITE = "your website's structured data"


def _text(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _text(value.get("name"))
    return None


def _join(values, sep: str) -> str | None:
    items = values if isinstance(values, list) else [values]
    parts = [t for t in (_text(v) for v in items) if t]
    return sep.join(parts) or None


def _address(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    country = value.get("addressCountry")
    parts = [value.get("streetAddress"), value.get("addressLocality"), value.get("addressRegion"),
             value.get("postalCode"), _text(country)]
    return ", ".join(str(p).strip() for p in parts if isinstance(p, str) and p.strip()) or None


def _hours(node: dict) -> str | None:
    plain = node.get("openingHours")
    if plain:
        items = plain if isinstance(plain, list) else [plain]
        text = "; ".join(i.strip() for i in items if isinstance(i, str) and i.strip())
        if text:
            return text
    specs = node.get("openingHoursSpecification")
    if isinstance(specs, dict):
        specs = [specs]
    rows = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        days = spec.get("dayOfWeek")
        days = ", ".join(d.rsplit("/", 1)[-1] for d in ([days] if isinstance(days, str) else days or []) if isinstance(d, str))
        opens, closes = spec.get("opens"), spec.get("closes")
        if days or opens or closes:
            rows.append(" ".join(x for x in [days, f"{opens}-{closes}" if opens and closes else (opens or closes)] if x))
    return "; ".join(rows) or None


def _org_node(raw_blocks: list[str]) -> dict | None:
    nodes = []
    for raw in raw_blocks or []:
        try:
            data = json.loads(raw)
        except ValueError:
            continue                                   # a broken block is a finding, not something to read here
        nodes += [n for n in _walk(data) if set(_types(n)) & ORG_TYPES and not set(n) <= {"@id", "@type"}]
    return next((n for n in nodes if _text(n.get("name"))), nodes[0] if nodes else None)


def looks_like_legal_entity(name: str) -> bool:
    return bool(_NUMBERED_COMPANY.match(name.strip()) or _LEGAL_SUFFIX.search(name))


def from_diagnosis(diagnosis: dict | None, business: dict) -> tuple[dict[str, str], dict[str, list[str]], set[str]]:
    """(values, notes, from_site): `values` maps a slot key to a guessed value; `notes` maps a slot key to
    plain-language lines to show under it (where the guess came from, and any question the diagnosis raised);
    `from_site` is the keys whose guess came from the site's structured data (the rest came from `aeo add`)."""
    values: dict[str, str] = {}
    notes: dict[str, list[str]] = {}
    from_site: set[str] = set()

    def put(key: str, value: str | None, source: str) -> None:
        if value and key not in values:
            values[key] = value
            if source == SITE:
                from_site.add(key)
            notes.setdefault(key, []).append(f"Pre-filled from {source}. This is our guess: check it.")

    node = _org_node(diagnosis["existing_jsonld"]) if diagnosis else None
    site = SITE
    site_name = _text(node.get("name")) if node else None
    if node:
        if site_name and looks_like_legal_entity(site_name):
            put("legal_name", site_name, site)
        elif site_name:
            put("name", site_name, site)
        put("legal_name", _text(node.get("legalName")), site)
        put("name", _text(node.get("alternateName")), site)
        founded = re.match(r"\d{4}", str(node.get("foundingDate") or ""))
        put("founded", founded.group(0) if founded else None, site)
        put("website", _text(node.get("url")), site)
        put("email", _text(node.get("email")), site)
        put("loc1_address", _address(node.get("address")), site)
        put("loc1_phone", _text(node.get("telephone")), site)
        put("loc1_hours", _hours(node), site)
        put("service_area", _join(node.get("areaServed"), "; "), site)
        put("languages", _join(node.get("knowsLanguage") or node.get("availableLanguage"), "; "), site)
        put("price_ranges", _text(node.get("priceRange")), site)
        same_as = node.get("sameAs")
        put("social", _join(same_as, "\n") if same_as else None, site)

    # What we typed ourselves when the business was added. Still unconfirmed.
    put("name", business.get("name"), "the name entered when the business was added")
    put("website", business.get("website_url"), "the address entered when the business was added")

    for finding in (diagnosis or {}).get("findings") or []:
        hit = FINDING_QUESTIONS.get(finding.get("code"))
        if hit:
            key, question = hit
            notes.setdefault(key, []).append("Question: " + question.format(site_name=site_name or "the name on your site"))
    if diagnosis and diagnosis.get("render"):
        h1 = [t for t in diagnosis["render"].get("h1_texts") or [] if t]
        if h1:
            notes.setdefault("name", []).append(f"The main heading on your page says: \"{h1[0]}\".")
    return values, notes, from_site
