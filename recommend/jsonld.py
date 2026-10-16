"""schema.json: JSON-LD built from CONFIRMED facts only (PLAN.md step 19).

  * Unconfirmed facts never go in. The result lists what was left out and why.
  * LocalBusiness, or a more specific type when the truth document has a confirmed "Business type" fact
    that names a schema.org type we know (recommend/schema_types.yaml).
  * One entry per location (a location is a confirmed "Location N address").
  * Checked before it is returned: it parses, has the required fields, and its name, address, phone and hours
    are word-for-word the confirmed facts. If any check fails, there is no document and the errors say why.

The address and hours are the confirmed text exactly as the client wrote it. Splitting an address into street,
city and postcode, or turning "Mon-Fri 9-5" into the structured hours format, would mean guessing, and the
rule is that the markup says exactly what the client confirmed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from recommend.llms_file import LABEL, fact, location_numbers

TYPES_FILE = Path(__file__).parent / "schema_types.yaml"
BUSINESS_TYPE_LABEL = "Business type"
CONTEXT = "https://schema.org"


@lru_cache(maxsize=1)
def _types() -> dict:
    return yaml.safe_load(TYPES_FILE.read_text())


def known_types() -> set[str]:
    return set(_types()["types"])


def _pascal(text: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[\s_-]+", text.strip()) if w)


def choose_type(business_type: str | None) -> tuple[str, str | None]:
    """(schema.org type, a note if the confirmed type was not usable)."""
    default = _types()["default"]
    if not business_type:
        return default, None
    candidate = _pascal(business_type)
    for t in known_types():
        if t.casefold() == candidate.casefold():
            return t, None
    return default, f"The confirmed business type '{business_type}' is not a schema.org LocalBusiness type this tool knows, so LocalBusiness was used."


@dataclass
class JsonLdResult:
    document: dict | None
    text: str | None
    errors: list[str] = field(default_factory=list)
    left_out: list[dict] = field(default_factory=list)      # [{label, why}]
    business_type: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "valid" if self.document is not None else "invalid"


def _split(text: str, pattern: str = r"[;\n]+") -> list[str]:
    return [p.strip() for p in re.split(pattern, text) if p.strip()]


def left_out_facts(all_facts: list[dict]) -> list[dict]:
    """Every fact in the profile that is NOT confirmed, with the reason it is not in the document."""
    out = []
    for f in all_facts or []:
        if f.get("status") == "confirmed" and (f.get("value") or "").strip():
            continue
        label = (f.get("label") or "").strip()
        if not label:
            continue
        has_value = bool((f.get("value") or "").strip())
        out.append({"label": label,
                    "why": "a value is written down but nobody has confirmed it" if has_value
                    else "empty: nothing was written down or confirmed"})
    return out


def build_jsonld(confirmed: list[dict], all_facts: list[dict]) -> JsonLdResult:
    """`confirmed` is core.profiles.confirmed_facts(...); `all_facts` is the raw list, only used to say what is missing."""
    result = JsonLdResult(document=None, text=None, left_out=left_out_facts(all_facts))
    name = fact(confirmed, LABEL["name"])
    type_name, type_note = choose_type(fact(confirmed, BUSINESS_TYPE_LABEL))
    result.business_type = type_name
    if type_note:
        result.notes.append(type_note)
    if not name:
        result.errors.append(f"'{LABEL['name']}' is not a confirmed fact, so the markup has no name.")

    website = fact(confirmed, LABEL["website"])
    shared: dict = {}
    for key, prop in (("legal_name", "legalName"), ("website", "url"), ("email", "email"),
                      ("founded", "foundingDate"), ("price_ranges", "priceRange")):
        v = fact(confirmed, LABEL[key])
        if v:
            shared[prop] = v
    area = fact(confirmed, LABEL["service_area"])
    if area:
        parts = _split(area, r"[;,\n]+")
        shared["areaServed"] = parts if len(parts) > 1 else parts[0]
    social = fact(confirmed, LABEL["social"])
    if social:
        links = [p for p in re.split(r"[\s,;]+", social) if re.match(r"^https?://", p)]
        if links:
            shared["sameAs"] = links

    nodes = []
    for n in location_numbers(confirmed):
        address = fact(confirmed, f"Location {n} address")
        if not address:
            continue
        node: dict = {"@type": type_name}
        if website:
            node["@id"] = f"{website.rstrip('/')}/#location-{n}"
        node["name"] = name
        node.update({k: v for k, v in shared.items() if k != "url"})
        if "url" in shared:
            node["url"] = shared["url"]
        node["address"] = address
        phone = fact(confirmed, f"Location {n} phone")
        if phone:
            node["telephone"] = phone
        hours = fact(confirmed, f"Location {n} hours")
        if hours:
            node["openingHours"] = hours
        nodes.append(node)
    if not nodes:
        result.errors.append("No location address is confirmed, so there is no location to describe. "
                             "Confirm at least 'Location 1 address' in the truth document.")
    if result.errors:
        return result

    document = {"@context": CONTEXT, "@graph": nodes}
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    errors = validate_jsonld(text, confirmed)
    if errors:
        result.errors = errors
        return result
    result.document, result.text = document, text
    return result


def validate_jsonld(text: str, confirmed: list[dict]) -> list[str]:
    """Checks a schema.json text against the confirmed facts. Returns every problem found (empty = valid).
    Does not trust the builder: it reads only the text and the facts."""
    try:
        doc = json.loads(text)
    except (ValueError, TypeError) as exc:
        return [f"It is not valid JSON: {exc}"]
    errors: list[str] = []
    if not isinstance(doc, dict) or doc.get("@context") != CONTEXT:
        errors.append(f"@context must be {CONTEXT}.")
    graph = doc.get("@graph") if isinstance(doc, dict) else None
    if not isinstance(graph, list) or not graph:
        return errors + ["@graph must be a list with at least one location."]

    confirmed_name = fact(confirmed, LABEL["name"])
    numbers = location_numbers(confirmed)
    allowed = {
        "address": {v for n in numbers if (v := fact(confirmed, f"Location {n} address"))},
        "telephone": {v for n in numbers if (v := fact(confirmed, f"Location {n} phone"))},
        "openingHours": {v for n in numbers if (v := fact(confirmed, f"Location {n} hours"))},
        "legalName": {v for v in [fact(confirmed, LABEL["legal_name"])] if v},
        "url": {v for v in [fact(confirmed, LABEL["website"])] if v},
        "email": {v for v in [fact(confirmed, LABEL["email"])] if v},
        "foundingDate": {v for v in [fact(confirmed, LABEL["founded"])] if v},
        "priceRange": {v for v in [fact(confirmed, LABEL["price_ranges"])] if v},
    }
    types = known_types()
    for i, node in enumerate(graph, start=1):
        where = f"location {i}"
        if not isinstance(node, dict):
            errors.append(f"{where}: is not an object.")
            continue
        if node.get("@type") not in types:
            errors.append(f"{where}: @type '{node.get('@type')}' is not a LocalBusiness type.")
        if not node.get("name"):
            errors.append(f"{where}: name is missing.")
        elif node["name"] != confirmed_name:
            errors.append(f"{where}: name '{node['name']}' is not the confirmed name"
                          + (f" '{confirmed_name}'." if confirmed_name else " (none is confirmed)."))
        if not node.get("address"):
            errors.append(f"{where}: address is missing.")
        for prop, ok_values in allowed.items():
            value = node.get(prop)
            if value is not None and value not in ok_values:
                errors.append(f"{where}: {prop} '{value}' is not a confirmed fact.")
    wanted = len([n for n in numbers if fact(confirmed, f"Location {n} address")])
    if len(graph) != wanted:
        errors.append(f"There are {len(graph)} entries but {wanted} confirmed location address(es): one entry per location.")
    return errors
